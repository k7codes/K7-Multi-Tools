"""Archive readers for .pak files with automatic format detection.

Supported formats:
  * Unreal Engine .pak  (UE4 / UE5, versions V1..V11, AES-256 encrypted index
    and/or data, Zlib/Gzip/Zstd/LZ4 compression)
  * Quake / id Software PAK ("PACK")
  * Valve Source VPK (v1 / v2, embedded + external chunk archives)
  * ZIP-based .pak (many tools just rename a zip)

The public entry point is :func:`open_pak`.
"""

from __future__ import annotations

import base64
import gzip
import io
import os
import struct
import zipfile
import zlib
from dataclasses import dataclass, field
from typing import List, Optional, Tuple


class PakError(Exception):
    """Raised for malformed or unsupported archives."""


class UnsupportedFeature(PakError):
    """Raised when an archive uses a feature we cannot handle (e.g. Oodle)."""


# ======================================================================
# shared model
# ======================================================================

@dataclass
class PakEntry:
    path: str
    size: int                      # uncompressed size
    compressed_size: int           # stored size (== size when not compressed)
    offset: int
    compression: str = "none"
    encrypted: bool = False
    extra: dict = field(default_factory=dict)

    @property
    def is_compressed(self) -> bool:
        return self.compression not in ("", "none")


class PakArchive:
    """Common interface implemented by every format reader."""

    format_name = "unknown"

    def __init__(self, path: str):
        self.path = path
        self.entries: List[PakEntry] = []
        self.mount_point: Optional[str] = None
        self.info: dict = {}

    def read(self, entry: PakEntry) -> bytes:  # pragma: no cover - interface
        raise NotImplementedError

    def close(self):  # pragma: no cover - interface
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def _b64_key(value) -> Optional[bytes]:
    """Accept a pak key as raw bytes, hex, or base64 (as found in Crypto.json)."""
    if value is None:
        return None
    if isinstance(value, (bytes, bytearray)):
        data = bytes(value)
    else:
        text = str(value).strip()
        if not text:
            return None
        data = None
        # Crypto.json keys are base64; try that first, then hex.
        try:
            data = base64.b64decode(text, validate=True)
        except Exception:
            data = None
        if data is None or len(data) not in (16, 24, 32):
            try:
                data = bytes.fromhex(text)
            except ValueError:
                if data is None:
                    raise PakError("Geçersiz AES anahtarı (base64 veya hex olmalı)")
    if len(data) not in (16, 24, 32):
        raise PakError(f"AES anahtarı 16/24/32 bayt olmalı, {len(data)} geldi")
    return data


def _aes_ecb_decrypt(key: bytes, data: bytes) -> bytes:
    try:
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    except ImportError as exc:  # pragma: no cover
        raise PakError("AES için 'cryptography' paketi gerekli") from exc
    data = data[: len(data) - (len(data) % 16)]
    decryptor = Cipher(algorithms.AES(key), modes.ECB()).decryptor()
    return decryptor.update(data) + decryptor.finalize()


def _align16(offset: int) -> int:
    return (offset + 15) & ~15


# ======================================================================
# little-endian reader helper
# ======================================================================

class _Reader:
    def __init__(self, fh):
        self.fh = fh

    def seek(self, pos, whence=0):
        return self.fh.seek(pos, whence)

    def tell(self):
        return self.fh.tell()

    def read_bytes(self, n: int) -> bytes:
        data = self.fh.read(n)
        if len(data) != n:
            raise PakError("Beklenmedik dosya sonu")
        return data

    def u8(self) -> int:
        return struct.unpack("<B", self.read_bytes(1))[0]

    def i8(self) -> int:
        return struct.unpack("<b", self.read_bytes(1))[0]

    def u16(self) -> int:
        return struct.unpack("<H", self.read_bytes(2))[0]

    def u32(self) -> int:
        return struct.unpack("<I", self.read_bytes(4))[0]

    def i32(self) -> int:
        return struct.unpack("<i", self.read_bytes(4))[0]

    def u64(self) -> int:
        return struct.unpack("<Q", self.read_bytes(8))[0]

    def bool(self) -> bool:
        value = self.u8()
        if value == 1:
            return True
        if value == 0:
            return False
        raise PakError(f"Geçersiz bool değeri: {value}")

    def fstring(self) -> str:
        length = self.i32()
        if length < 0:
            raw = self.read_bytes((-length) * 2)
            text = raw.decode("utf-16-le", "replace")
        else:
            raw = self.read_bytes(length)
            text = raw.decode("utf-8", "replace")
        return text.split("\x00", 1)[0]


# ======================================================================
# Unreal Engine pak
# ======================================================================

UE_MAGIC = 0x5A6F12E1

VERSION_ORDER = [
    "V0", "V1", "V2", "V3", "V4", "V5", "V6", "V7",
    "V8A", "V8B", "V9", "V10", "V11",
]
VERSION_INDEX = {name: i for i, name in enumerate(VERSION_ORDER)}
VERSION_MAJOR = {
    "V0": 0, "V1": 1, "V2": 2, "V3": 3, "V4": 4, "V5": 5, "V6": 6,
    "V7": 7, "V8A": 8, "V8B": 8, "V9": 9, "V10": 10, "V11": 11,
}

# version_major value -> representative human version string
MAJOR_TO_VERSION = {
    0: "V0", 1: "V1", 2: "V2", 3: "V3", 4: "V4", 5: "V5", 6: "V6",
    7: "V7", 8: "V8B", 9: "V9", 10: "V10", 11: "V11",
}

_COMP_NONE = ("none", "")


def _ue_footer_size(ver: str) -> int:
    major = VERSION_MAJOR[ver]
    size = 4 + 4 + 8 + 8 + 20
    if major >= 7:
        size += 16                       # encryption key guid
    if major >= 4:
        size += 1                        # encrypted index flag
    if major == 9:
        size += 1                        # frozen index flag
    if VERSION_INDEX[ver] >= VERSION_INDEX["V8A"]:
        size += 32 * 4                   # 4 compression method names
    if VERSION_INDEX[ver] >= VERSION_INDEX["V8B"]:
        size += 32                       # 5th compression method name
    return size


@dataclass
class _UeFooter:
    version: str
    encrypted: bool
    index_offset: int
    index_size: int
    compression: List[Optional[str]]
    guid: Optional[int] = None


@dataclass
class _UeBlock:
    start: int
    end: int


@dataclass
class _UeEntry:
    offset: int
    compressed: int
    uncompressed: int
    compression_slot: Optional[int]
    hash: Optional[bytes]
    blocks: Optional[List[_UeBlock]]
    flags: int
    compression_block_size: int

    @property
    def encrypted(self) -> bool:
        return bool(self.flags & 1)


def _ue_serialized_size(ver: str, compression_slot: Optional[int], block_count: int) -> int:
    major = VERSION_MAJOR[ver]
    size = 8 + 8 + 8                                   # offset, compressed, uncompressed
    size += 1 if ver == "V8A" else 4                   # compression method index
    if major == 1:
        size += 8                                      # timestamp
    size += 20                                          # hash
    if compression_slot is not None:
        size += 4 + (8 + 8) * block_count
    size += 1                                           # encrypted flag
    if major >= 3:
        size += 4                                       # compression block size
    return size


def _ue_read_entry(r: _Reader, ver: str) -> _UeEntry:
    major = VERSION_MAJOR[ver]
    offset = r.u64()
    compressed = r.u64()
    uncompressed = r.u64()
    if ver == "V8A":
        raw = r.u8()
    else:
        raw = r.u32()
    compression_slot = None if raw == 0 else raw - 1
    if major == 1:
        r.u64()                                        # timestamp
    h = r.read_bytes(20)
    blocks = None
    if major >= 3 and compression_slot is not None:
        count = r.u32()
        blocks = [_UeBlock(r.u64(), r.u64()) for _ in range(count)]
    flags = r.u8() if major >= 3 else 0
    block_size = r.u32() if major >= 3 else 0
    return _UeEntry(offset, compressed, uncompressed, compression_slot, h,
                    blocks, flags, block_size)


def _ue_read_entry_encoded(r: _Reader, ver: str) -> _UeEntry:
    bits = r.u32()
    slot = (bits >> 23) & 0x3F
    compression_slot = None if slot == 0 else slot - 1
    encrypted = bool(bits & (1 << 22))
    block_count = (bits >> 6) & 0xFFFF
    block_size = bits & 0x3F
    if block_size == 0x3F:
        block_size = r.u32()
    else:
        block_size <<= 11

    def var_int(bit):
        return r.u32() if (bits & (1 << bit)) else r.u64()

    offset = var_int(31)
    uncompressed = var_int(30)
    compressed = uncompressed if compression_slot is None else var_int(29)

    offset_base = _ue_serialized_size(ver, compression_slot, block_count)
    blocks = None
    if block_count == 1 and not encrypted:
        blocks = [_UeBlock(offset_base, offset_base + compressed)]
    elif block_count > 0:
        index = offset_base
        blocks = []
        for _ in range(block_count):
            bs = r.u32()
            blocks.append(_UeBlock(index, index + bs))
            if encrypted:
                bs = _align16(bs)
            index += bs

    return _UeEntry(offset, compressed, uncompressed, compression_slot, None,
                    blocks, 1 if encrypted else 0, block_size)


def _ue_try_footer(fh, ver: str, file_size: int) -> Optional[_UeFooter]:
    size = _ue_footer_size(ver)
    if file_size < size:
        return None
    major = VERSION_MAJOR[ver]
    try:
        fh.seek(file_size - size)
        r = _Reader(fh)
        guid = int.from_bytes(r.read_bytes(16), "little") if major >= 7 else None
        encrypted = r.bool() if major >= 4 else False
        magic = r.u32()
        ver_major = r.u32()
        index_offset = r.u64()
        index_size = r.u64()
        r.read_bytes(20)                               # index hash
        if major == 9:
            r.bool()                                   # frozen
        count = 0
        if VERSION_INDEX[ver] >= VERSION_INDEX["V8A"]:
            count = 4
        if VERSION_INDEX[ver] >= VERSION_INDEX["V8B"]:
            count = 5
        compression: List[Optional[str]] = []
        for _ in range(count):
            name = r.read_bytes(32).split(b"\x00", 1)[0].decode("ascii", "ignore")
            compression.append(name or None)
        if major < 8:
            compression += ["Zlib", "Gzip", "Oodle"]
        if magic != UE_MAGIC or ver_major != major:
            return None
        return _UeFooter(ver, encrypted, index_offset, index_size, compression, guid)
    except PakError:
        return None


class UnrealPakArchive(PakArchive):
    format_name = "Unreal Engine PAK"

    def __init__(self, path: str, key=None):
        super().__init__(path)
        self._key = _b64_key(key)
        self._fh = open(path, "rb")
        file_size = os.fstat(self._fh.fileno()).st_size

        footer = None
        for ver in reversed(VERSION_ORDER):
            footer = _ue_try_footer(self._fh, ver, file_size)
            if footer is not None:
                break
        if footer is None:
            self._fh.close()
            raise PakError("Unreal pak imzası bulunamadı")

        self._footer = footer
        self.version = footer.version
        self.compression = footer.compression
        self.info = {
            "version": footer.version,
            "version_major": VERSION_MAJOR[footer.version],
            "encrypted_index": footer.encrypted,
            "index_offset": footer.index_offset,
            "index_size": footer.index_size,
            "encryption_guid": footer.guid,
        }
        self._entries_by_path = {}
        self._load_index()

    # ---------------------------------------------------------------- index
    def _load_index(self):
        self._fh.seek(self._footer.index_offset)
        index = self._fh.read(self._footer.index_size)
        if self._footer.encrypted:
            if self._key is None:
                raise UnsupportedFeature(
                    "Bu paket şifreli index kullanıyor; AES anahtarı gerekli")
            index = _aes_ecb_decrypt(self._key, index)

        try:
            r = _Reader(io.BytesIO(index))
            self.mount_point = r.fstring()
            count = r.u32()
            major = VERSION_MAJOR[self.version]

            if major >= 10:                            # PathHashIndex
                self._load_index_v10(r, count, major)
            else:
                for _ in range(count):
                    name = r.fstring()
                    self._entries_by_path[name] = _ue_read_entry(r, self.version)
        except (PakError, struct.error) as exc:
            if self._footer.encrypted:
                raise PakError(
                    "Şifreli index çözülemedi — AES anahtarı yanlış olabilir") from exc
            raise

        for name, ue in self._entries_by_path.items():
            comp = None
            if ue.compression_slot is not None and ue.compression_slot < len(self.compression):
                comp = self.compression[ue.compression_slot]
            self.entries.append(PakEntry(
                path=name,
                size=ue.uncompressed,
                compressed_size=ue.compressed,
                offset=ue.offset,
                compression=comp or "none",
                encrypted=ue.encrypted,
            ))
        self.entries.sort(key=lambda e: e.path)

    def _load_index_v10(self, r: _Reader, count: int, major: int):
        r.u64()                                        # path hash seed
        has_phi = r.u32()
        if has_phi != 0:
            r.u64(); r.u64(); r.read_bytes(20)         # path hash index location
        has_fdi = r.u32()
        fdi_offset = fdi_size = None
        if has_fdi != 0:
            fdi_offset = r.u64()
            fdi_size = r.u64()
            r.read_bytes(20)
        enc_size = r.u32()
        encoded_entries = r.read_bytes(enc_size)
        non_encoded_count = r.u32()
        non_encoded = [_ue_read_entry(r, self.version) for _ in range(non_encoded_count)]

        if has_fdi != 0:
            self._fh.seek(fdi_offset)
            fdi_buf = self._fh.read(fdi_size)
            if self._footer.encrypted:
                fdi_buf = _aes_ecb_decrypt(self._key, fdi_buf)
            fr = _Reader(io.BytesIO(fdi_buf))
            dir_count = fr.u32()
            records = []
            for _ in range(dir_count):
                dir_name = fr.fstring()
                file_count = fr.u32()
                for _ in range(file_count):
                    file_name = fr.fstring()
                    encoded_offset = fr.i32()
                    records.append((dir_name, file_name, encoded_offset))
            for dir_name, file_name, encoded_offset in records:
                if encoded_offset == -(2 ** 31):        # deleted / pruned sentinel
                    continue
                if encoded_offset >= 0:
                    er = _Reader(io.BytesIO(encoded_entries))
                    er.seek(encoded_offset)
                    entry = _ue_read_entry_encoded(er, self.version)
                else:
                    index = (-encoded_offset) - 1
                    if index >= len(non_encoded):
                        continue
                    entry = non_encoded[index]
                path = (dir_name[1:] if dir_name.startswith("/") else dir_name) + file_name
                self._entries_by_path[path] = entry

    # ---------------------------------------------------------------- read
    def read(self, entry: PakEntry) -> bytes:
        ue = self._entries_by_path.get(entry.path)
        if ue is None:
            raise PakError(f"Kayıt bulunamadı: {entry.path}")
        major = VERSION_MAJOR[self.version]

        self._fh.seek(ue.offset)
        r = _Reader(self._fh)
        _ue_read_entry(r, self.version)                # advance past stored header
        data_offset = self._fh.tell()

        raw_len = _align16(ue.compressed) if ue.encrypted else ue.compressed
        data = self._fh.read(raw_len)

        if ue.encrypted:
            if self._key is None:
                raise UnsupportedFeature(
                    "Bu dosya şifreli; AES anahtarı gerekli")
            data = _aes_ecb_decrypt(self._key, data)[: ue.compressed]

        if ue.compression_slot is None:
            return data

        comp = None
        if ue.compression_slot < len(self.compression):
            comp = self.compression[ue.compression_slot]
        header_size = data_offset - ue.offset
        ranges: List[Tuple[int, int]] = []
        if ue.blocks:
            for block in ue.blocks:
                if major >= 5:                          # relative chunk offsets
                    ranges.append((block.start - header_size, block.end - header_size))
                else:
                    ranges.append((block.start - data_offset, block.end - data_offset))
        else:
            ranges = [(0, len(data))]

        multi = len(ranges) > 1
        chunk_size = ue.compression_block_size
        expected = ue.uncompressed
        out = bytearray()
        for start, end in ranges:
            out += _decompress(comp, data[start:end],
                               expected if not multi else chunk_size)
        return bytes(out)

    def close(self):
        try:
            self._fh.close()
        except Exception:
            pass


def _decompress(name: Optional[str], chunk: bytes, expected_size: int) -> bytes:
    if not name:
        return chunk
    name = name.lower()
    if name == "zlib":
        return zlib.decompress(chunk)
    if name == "gzip":
        return gzip.decompress(chunk)
    if name == "zstd":
        try:
            import zstandard
        except ImportError as exc:
            raise UnsupportedFeature("Zstd açmak için 'zstandard' paketi gerekli") from exc
        return zstandard.ZstdDecompressor().decompress(chunk)
    if name == "lz4":
        try:
            import lz4.block
        except ImportError as exc:
            raise UnsupportedFeature("LZ4 açmak için 'lz4' paketi gerekli") from exc
        return lz4.block.decompress(chunk, uncompressed_size=expected_size)
    if name == "oodle":
        raise UnsupportedFeature(
            "Oodle sıkıştırması özel bir kütüphane gerektirir, desteklenmiyor")
    raise UnsupportedFeature(f"Bilinmeyen sıkıştırma: {name}")


# ======================================================================
# Quake / id Software PAK
# ======================================================================

class QuakePakArchive(PakArchive):
    format_name = "Quake PAK"

    def __init__(self, path: str):
        super().__init__(path)
        self._fh = open(path, "rb")
        header = self._fh.read(12)
        if header[:4] != b"PACK":
            self._fh.close()
            raise PakError("Quake PACK imzası yok")
        dir_offset, dir_length = struct.unpack("<II", header[4:12])
        self._fh.seek(dir_offset)
        directory = self._fh.read(dir_length)
        for i in range(0, len(directory), 64):
            record = directory[i:i + 64]
            if len(record) < 64:
                break
            name = record[:56].split(b"\x00", 1)[0].decode("latin-1")
            offset, length = struct.unpack("<II", record[56:64])
            self.entries.append(PakEntry(
                path=name, size=length, compressed_size=length,
                offset=offset, compression="none"))
        self.info = {"entries": len(self.entries)}

    def read(self, entry: PakEntry) -> bytes:
        self._fh.seek(entry.offset)
        return self._fh.read(entry.compressed_size)

    def close(self):
        try:
            self._fh.close()
        except Exception:
            pass


# ======================================================================
# Valve Source VPK
# ======================================================================

VPK_MAGIC = 0x55AA1234


class VpkArchive(PakArchive):
    format_name = "Source VPK"

    def __init__(self, path: str):
        super().__init__(path)
        self._dir_fh = open(path, "rb")
        header = self._dir_fh.read(28)
        magic, version = struct.unpack("<II", header[:8])
        if magic != VPK_MAGIC:
            self._dir_fh.close()
            raise PakError("VPK imzası yok")
        if version == 1:
            tree_size = struct.unpack("<I", header[8:12])[0]
            header_size = 12
        elif version == 2:
            tree_size = struct.unpack("<I", header[8:12])[0]
            header_size = 28
        else:
            self._dir_fh.close()
            raise PakError(f"Desteklenmeyen VPK sürümü: {version}")

        self.version = version
        self._data_offset = header_size + tree_size
        self._chunk_fhs = {}
        self._embedded_fh = self._dir_fh

        self._dir_fh.seek(header_size)
        tree = self._dir_fh.read(tree_size)
        self._parse_tree(tree, path)
        self.info = {"version": version, "entries": len(self.entries)}

    def _parse_tree(self, tree: bytes, path: str):
        pos = 0

        def read_cstr() -> str:
            nonlocal pos
            end = tree.find(b"\x00", pos)
            if end == -1:
                end = len(tree)
            s = tree[pos:end].decode("utf-8", "replace")
            pos = end + 1
            return s

        while True:
            ext = read_cstr()
            if ext == "":
                break
            while True:
                dir_path = read_cstr()
                if dir_path == "":
                    break
                while True:
                    filename = read_cstr()
                    if filename == "":
                        break
                    crc, preload_bytes, archive_index, offset, length = struct.unpack(
                        "<IHHII", tree[pos:pos + 16])
                    pos += 16
                    terminator = struct.unpack("<H", tree[pos:pos + 2])[0]
                    pos += 2
                    preload = tree[pos:pos + preload_bytes]
                    pos += preload_bytes
                    name = f"{dir_path}/{filename}.{ext}" if dir_path not in (" ", "") \
                        else f"{filename}.{ext}"
                    name = name.lstrip("/")
                    self.entries.append(PakEntry(
                        path=name,
                        size=preload_bytes + length,
                        compressed_size=preload_bytes + length,
                        offset=offset,
                        compression="none",
                        extra={
                            "archive_index": archive_index,
                            "preload": preload,
                            "length": length,
                        },
                    ))
        self.entries.sort(key=lambda e: e.path)

    def _chunk_fh(self, index: int):
        if index in self._chunk_fhs:
            return self._chunk_fhs[index]
        base = self.path
        if base.lower().endswith("_dir.vpk"):
            base = base[:-8]
        elif base.lower().endswith(".vpk"):
            base = base[:-4]
        chunk_path = f"{base}_{index:03d}.vpk"
        if not os.path.exists(chunk_path):
            raise PakError(f"VPK parça dosyası bulunamadı: {chunk_path}")
        fh = open(chunk_path, "rb")
        self._chunk_fhs[index] = fh
        return fh

    def read(self, entry: PakEntry) -> bytes:
        preload = entry.extra.get("preload", b"")
        length = entry.extra.get("length", 0)
        archive_index = entry.extra.get("archive_index", 0x7FFF)
        if length == 0:
            return bytes(preload)
        if archive_index == 0x7FFF:
            fh = self._embedded_fh
            base = self._data_offset
        else:
            fh = self._chunk_fh(archive_index)
            base = 0
        fh.seek(base + entry.offset)
        return bytes(preload) + fh.read(length)

    def close(self):
        for fh in self._chunk_fhs.values():
            try:
                fh.close()
            except Exception:
                pass
        try:
            self._dir_fh.close()
        except Exception:
            pass


# ======================================================================
# ZIP
# ======================================================================

class ZipPakArchive(PakArchive):
    format_name = "ZIP"

    def __init__(self, path: str):
        super().__init__(path)
        self._zip = zipfile.ZipFile(path)
        for info in self._zip.infolist():
            self.entries.append(PakEntry(
                path=info.filename,
                size=info.file_size,
                compressed_size=info.compress_size,
                offset=info.header_offset,
                compression="deflate" if info.compress_type != zipfile.ZIP_STORED else "none",
                encrypted=bool(info.flag_bits & 0x1),
            ))
        self.entries.sort(key=lambda e: e.path)

    def read(self, entry: PakEntry) -> bytes:
        return self._zip.read(entry.path)

    def close(self):
        try:
            self._zip.close()
        except Exception:
            pass


# ======================================================================
# detection
# ======================================================================

def detect_format(path: str) -> str:
    with open(path, "rb") as fh:
        head = fh.read(8)
        if head[:4] == b"PACK":
            return "quake"
        if len(head) >= 4 and struct.unpack("<I", head[:4])[0] == VPK_MAGIC:
            return "vpk"
        if head[:2] == b"PK":
            return "zip"
    # Unreal has no leading magic: inspect the footer.
    with open(path, "rb") as fh:
        file_size = os.fstat(fh.fileno()).st_size
        for ver in reversed(VERSION_ORDER):
            if _ue_try_footer(fh, ver, file_size) is not None:
                return "unreal"
    return "zip"


def open_pak(path: str, key=None) -> PakArchive:
    """Open *path*, auto-detecting the container format."""
    fmt = detect_format(path)
    if fmt == "quake":
        return QuakePakArchive(path)
    if fmt == "vpk":
        return VpkArchive(path)
    if fmt == "unreal":
        return UnrealPakArchive(path, key=key)
    return ZipPakArchive(path)
