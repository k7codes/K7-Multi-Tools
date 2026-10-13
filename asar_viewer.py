"""ASAR viewer / extractor.

ASAR is Electron's archive format (as produced by @electron/asar). This tool
lists the contents, previews text files, and extracts single files or the whole
archive.

Format recap
------------
    offset 0 : u32  = 4                         (pickle payload size of the size picker)
    offset 4 : u32  = header_size               (length of the header pickle)
    offset 8 : header pickle
                  u32 = payload size (header_size - 4)
                  u32 = json length
                  ... json bytes ...  (+ padding to 4-byte alignment)
    data area starts at 8 + header_size; every file offset in the JSON is
    relative to that position.
"""

import json
import os
import struct
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import customtkinter as ctk

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

TEXT_PREVIEW_LIMIT = 1_000_000
HEX_PREVIEW_SIZE = 4096


class AsarArchive:
    def __init__(self, path):
        self.path = path
        with open(path, "rb") as fh:
            prefix = fh.read(8)
            if len(prefix) < 8:
                raise ValueError("Dosya çok küçük, geçerli bir ASAR değil")
            header_size = struct.unpack("<I", prefix[4:8])[0]
            header_buf = fh.read(header_size)
            if len(header_buf) < 8:
                raise ValueError("ASAR başlığı okunamadı")
            json_len = struct.unpack("<I", header_buf[4:8])[0]
            self.header = json.loads(header_buf[8:8 + json_len].decode("utf-8"))
        self.base = 8 + header_size
        self.files = {}
        self._walk(self.header.get("files", {}), "")

    def _walk(self, node, prefix):
        for name, meta in node.items():
            full = prefix + name
            if "files" in meta:
                self._walk(meta["files"], full + "/")
            else:
                self.files[full] = (int(meta.get("size", 0)), int(meta.get("offset", 0)))

    def read(self, path):
        size, offset = self.files[path]
        with open(self.path, "rb") as fh:
            fh.seek(self.base + offset)
            return fh.read(size)

    def extract_all(self, dest):
        count = 0
        with open(self.path, "rb") as fh:
            for rel, (size, offset) in self.files.items():
                target = os.path.join(dest, *rel.split("/"))
                os.makedirs(os.path.dirname(target), exist_ok=True)
                fh.seek(self.base + offset)
                with open(target, "wb") as out:
                    out.write(fh.read(size))
                count += 1
        return count


def _looks_text(data):
    if b"\x00" in data[:512]:
        return False
    try:
        data.decode("utf-8")
        return True
    except UnicodeDecodeError:
        return False


class AsarViewer(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("ASAR Viewer")
        self.geometry("1150x720")
        self.minsize(880, 520)
        self.archive = None
        self.current_path = None

        self._build_toolbar()
        self._build_body()
        self._build_statusbar()

    def _build_toolbar(self):
        bar = ctk.CTkFrame(self, corner_radius=0, height=52)
        bar.pack(side="top", fill="x")
        bar.pack_propagate(False)

        ctk.CTkButton(bar, text="ASAR Aç", width=120, command=self.open_archive).pack(
            side="left", padx=(12, 8), pady=10)
        ctk.CTkButton(bar, text="Tek Dosya Çıkar", width=130, fg_color="gray30",
                      hover_color="gray40", command=self.extract_selected).pack(
            side="left", padx=4, pady=10)
        ctk.CTkButton(bar, text="Tümünü Çıkar", width=120, fg_color="#2f9e44",
                      hover_color="#2b8a3e", command=self.extract_all).pack(
            side="left", padx=4, pady=10)

        ctk.CTkLabel(bar, text="Filtre:").pack(side="left", padx=(18, 4))
        self.filter_var = ctk.StringVar()
        self.filter_var.trace_add("write", lambda *_: self.refresh_tree())
        ctk.CTkEntry(bar, textvariable=self.filter_var, width=180,
                     placeholder_text="isim ile ara").pack(side="left", pady=10)

        self.file_label = ctk.CTkLabel(bar, text="ASAR açık değil", text_color="gray70")
        self.file_label.pack(side="right", padx=12)

    def _build_body(self):
        body = ctk.CTkFrame(self, corner_radius=0, fg_color="transparent")
        body.pack(side="top", fill="both", expand=True)
        body.grid_rowconfigure(0, weight=1)
        body.grid_columnconfigure(1, weight=1)

        left = ctk.CTkFrame(body, width=380, corner_radius=0)
        left.grid(row=0, column=0, sticky="nsw")
        left.grid_propagate(False)
        left.grid_rowconfigure(1, weight=1)
        left.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(left, text="İçerik", font=ctk.CTkFont(weight="bold")).grid(
            row=0, column=0, sticky="w", padx=12, pady=(12, 6))

        wrap = tk.Frame(left)
        wrap.grid(row=1, column=0, sticky="nsew", padx=8, pady=(0, 10))
        wrap.grid_rowconfigure(0, weight=1)
        wrap.grid_columnconfigure(0, weight=1)
        self.tree = ttk.Treeview(wrap, show="tree")
        ysb = ttk.Scrollbar(wrap, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=ysb.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        ysb.grid(row=0, column=1, sticky="ns")
        self.tree.bind("<<TreeviewSelect>>", self.on_select)

        right = ctk.CTkFrame(body, corner_radius=0)
        right.grid(row=0, column=1, sticky="nsew", padx=(6, 10), pady=10)
        right.grid_rowconfigure(1, weight=1)
        right.grid_columnconfigure(0, weight=1)

        self.info = ctk.CTkLabel(right, text="Bir dosya seç", anchor="w")
        self.info.grid(row=0, column=0, sticky="ew", padx=8, pady=(8, 4))

        self.preview = ctk.CTkTextbox(right, font=("Consolas", 12), wrap="none")
        self.preview.grid(row=1, column=0, sticky="nsew", padx=8, pady=(0, 8))

    def _build_statusbar(self):
        self.status = ctk.CTkLabel(self, text="Hazır", anchor="w", height=26,
                                   text_color="gray70")
        self.status.pack(side="bottom", fill="x", padx=12)

    # -------------------------------------------------------------- actions
    def open_archive(self):
        path = filedialog.askopenfilename(
            title="ASAR seç", filetypes=[("ASAR arşivi", "*.asar"), ("Tüm dosyalar", "*.*")])
        if not path:
            return
        try:
            self.archive = AsarArchive(path)
            self.file_label.configure(text=os.path.basename(path))
            self.refresh_tree()
            self.status.configure(text=f"Açıldı: {path} ({len(self.archive.files)} dosya)")
        except Exception as exc:
            messagebox.showerror("Hata", f"ASAR açılamadı:\n{exc}")

    def refresh_tree(self):
        self.tree.delete(*self.tree.get_children())
        if not self.archive:
            return
        query = self.filter_var.get().strip().lower()
        paths = sorted(self.archive.files)
        if query:
            paths = [p for p in paths if query in p.lower()]

        nodes = {}
        for path in paths:
            parts = path.split("/")
            parent = ""
            for i, part in enumerate(parts):
                key = "/".join(parts[: i + 1])
                if key in nodes:
                    parent = nodes[key]
                    continue
                is_file = i == len(parts) - 1
                icon = "📄" if is_file else "📁"
                item = self.tree.insert(parent, "end", text=f"{icon} {part}",
                                        open=bool(query))
                nodes[key] = item
                parent = item
        self.status.configure(text=f"{len(paths)} giriş gösteriliyor")

    def _selected_file_path(self):
        item = self.tree.focus()
        if not item:
            return None
        parts = []
        while item:
            text = self.tree.item(item, "text")
            parts.append(text.split(" ", 1)[1])
            item = self.tree.parent(item)
        return "/".join(reversed(parts))

    def on_select(self, _event=None):
        path = self._selected_file_path()
        if not path or not self.archive or path not in self.archive.files:
            return
        self.current_path = path
        size, offset = self.archive.files[path]
        self.info.configure(text=f"{path}   •   {size:,} bayt   •   offset {offset:,}")
        try:
            data = self.archive.read(path)
        except Exception as exc:
            self.preview.delete("1.0", "end")
            self.preview.insert("1.0", f"Okunamadı: {exc}")
            return

        self.preview.delete("1.0", "end")
        if _looks_text(data[:TEXT_PREVIEW_LIMIT]):
            text = data[:TEXT_PREVIEW_LIMIT].decode("utf-8", "replace")
            self.preview.insert("1.0", text)
            if len(data) > TEXT_PREVIEW_LIMIT:
                self.preview.insert("end", "\n\n… (önizleme kısaltıldı)")
        else:
            self.preview.insert("1.0", self._hex_dump(data[:HEX_PREVIEW_SIZE]))

    def _hex_dump(self, data):
        lines = []
        for i in range(0, len(data), 16):
            chunk = data[i:i + 16]
            hex_part = " ".join(f"{b:02x}" for b in chunk).ljust(47)
            ascii_part = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
            lines.append(f"{i:08x}  {hex_part}  {ascii_part}")
        if len(data) == HEX_PREVIEW_SIZE:
            lines.append("… (önizleme kısaltıldı)")
        return "\n".join(lines)

    def extract_selected(self):
        if not self.archive or not self.current_path:
            messagebox.showwarning("Uyarı", "Önce bir dosya seçin.")
            return
        name = os.path.basename(self.current_path)
        target = filedialog.asksaveasfilename(initialfile=name)
        if not target:
            return
        try:
            with open(target, "wb") as fh:
                fh.write(self.archive.read(self.current_path))
            self.status.configure(text=f"Çıkarıldı: {target}")
        except Exception as exc:
            messagebox.showerror("Hata", f"Çıkarılamadı:\n{exc}")

    def extract_all(self):
        if not self.archive:
            messagebox.showwarning("Uyarı", "Önce bir ASAR açın.")
            return
        dest = filedialog.askdirectory(title="Çıkarma klasörü seç")
        if not dest:
            return
        try:
            count = self.archive.extract_all(dest)
            self.status.configure(text=f"{count} dosya çıkarıldı: {dest}")
            messagebox.showinfo("Tamam", f"{count} dosya çıkarıldı.")
        except Exception as exc:
            messagebox.showerror("Hata", f"Çıkarma başarısız:\n{exc}")


if __name__ == "__main__":
    AsarViewer().mainloop()
