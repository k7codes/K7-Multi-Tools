"""PAK viewer / extractor with automatic format detection.

Supports (auto-detected):
  * Unreal Engine .pak  (UE4 / UE5, encrypted index & data with AES key)
  * Quake PAK
  * Source VPK
  * ZIP archives that were simply renamed to .pak
"""

import os
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import customtkinter as ctk

import pak_formats
from pak_formats import PakError, UnsupportedFeature

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

TEXT_PREVIEW_LIMIT = 1_000_000
HEX_PREVIEW_SIZE = 4096


def _looks_text(data):
    if b"\x00" in data[:512]:
        return False
    try:
        data.decode("utf-8")
        return True
    except UnicodeDecodeError:
        return False


def _human(num):
    for unit in ("B", "KB", "MB", "GB"):
        if num < 1024:
            return f"{num:.0f} {unit}" if unit == "B" else f"{num:.1f} {unit}"
        num /= 1024
    return f"{num:.1f} TB"


class PakViewer(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("PAK Viewer")
        self.geometry("1220x760")
        self.minsize(940, 560)
        self.archive = None
        self.current = None

        self._build_toolbar()
        self._build_body()
        self._build_statusbar()

    def _build_toolbar(self):
        bar = ctk.CTkFrame(self, corner_radius=0, height=100)
        bar.pack(side="top", fill="x")
        bar.pack_propagate(False)

        row1 = ctk.CTkFrame(bar, fg_color="transparent")
        row1.pack(fill="x")
        ctk.CTkButton(row1, text="PAK Aç", width=110, command=self.open_pak).pack(
            side="left", padx=(12, 8), pady=(10, 4))
        ctk.CTkButton(row1, text="Seçileni Çıkar", width=120, fg_color="gray30",
                      hover_color="gray40", command=self.extract_selected).pack(
            side="left", padx=4, pady=(10, 4))
        ctk.CTkButton(row1, text="Tümünü Çıkar", width=120, fg_color="#2f9e44",
                      hover_color="#2b8a3e", command=self.extract_all).pack(
            side="left", padx=4, pady=(10, 4))

        ctk.CTkLabel(row1, text="Filtre:").pack(side="left", padx=(18, 4))
        self.filter_var = ctk.StringVar()
        self.filter_var.trace_add("write", lambda *_: self.refresh_list())
        ctk.CTkEntry(row1, textvariable=self.filter_var, width=200,
                     placeholder_text="yol ara").pack(side="left", pady=(10, 4))

        self.file_label = ctk.CTkLabel(row1, text="PAK açık değil", text_color="gray70")
        self.file_label.pack(side="right", padx=12)

        row2 = ctk.CTkFrame(bar, fg_color="transparent")
        row2.pack(fill="x")
        ctk.CTkLabel(row2, text="AES anahtarı (base64/hex):").pack(
            side="left", padx=(12, 6), pady=(0, 10))
        self.key_var = ctk.StringVar()
        ctk.CTkEntry(row2, textvariable=self.key_var, width=460,
                     placeholder_text="şifreli paketler için gerekli").pack(
            side="left", pady=(0, 10))
        ctk.CTkButton(row2, text="Yeniden Aç", width=110, fg_color="gray30",
                      hover_color="gray40", command=self.reopen).pack(
            side="left", padx=8, pady=(0, 10))
        self.format_label = ctk.CTkLabel(row2, text="", text_color="gray70")
        self.format_label.pack(side="left", padx=8)

    def _build_body(self):
        body = ctk.CTkFrame(self, corner_radius=0, fg_color="transparent")
        body.pack(side="top", fill="both", expand=True)
        body.grid_rowconfigure(0, weight=1)
        body.grid_columnconfigure(0, weight=3)
        body.grid_columnconfigure(1, weight=2)

        left = ctk.CTkFrame(body, corner_radius=0)
        left.grid(row=0, column=0, sticky="nsew", padx=(10, 5), pady=10)
        left.grid_rowconfigure(0, weight=1)
        left.grid_columnconfigure(0, weight=1)

        columns = ("path", "size", "stored", "method", "enc")
        self.tree = ttk.Treeview(left, columns=columns, show="headings")
        self.tree.heading("path", text="Yol")
        self.tree.heading("size", text="Boyut")
        self.tree.heading("stored", text="Depolanan")
        self.tree.heading("method", text="Yöntem")
        self.tree.heading("enc", text="Şifreli")
        self.tree.column("path", width=360, anchor="w")
        self.tree.column("size", width=90, anchor="e")
        self.tree.column("stored", width=90, anchor="e")
        self.tree.column("method", width=80, anchor="center")
        self.tree.column("enc", width=60, anchor="center")
        ysb = ttk.Scrollbar(left, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=ysb.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        ysb.grid(row=0, column=1, sticky="ns")
        self.tree.bind("<<TreeviewSelect>>", self.on_select)

        right = ctk.CTkFrame(body, corner_radius=0)
        right.grid(row=0, column=1, sticky="nsew", padx=(5, 10), pady=10)
        right.grid_rowconfigure(1, weight=1)
        right.grid_columnconfigure(0, weight=1)

        self.info = ctk.CTkLabel(right, text="Bir dosya seç", anchor="w",
                                 justify="left")
        self.info.grid(row=0, column=0, sticky="ew", padx=8, pady=(8, 4))

        self.preview = ctk.CTkTextbox(right, font=("Consolas", 12), wrap="none")
        self.preview.grid(row=1, column=0, sticky="nsew", padx=8, pady=(0, 8))

    def _build_statusbar(self):
        self.status = ctk.CTkLabel(self, text="Hazır", anchor="w", height=26,
                                   text_color="gray70")
        self.status.pack(side="bottom", fill="x", padx=12)

    # -------------------------------------------------------------- actions
    def _key(self):
        text = self.key_var.get().strip()
        return text or None

    def open_pak(self):
        path = filedialog.askopenfilename(
            title="PAK seç",
            filetypes=[("PAK arşivleri", "*.pak *.vpk *.dat"), ("Tüm dosyalar", "*.*")])
        if not path:
            return
        self._load(path)

    def reopen(self):
        if self.archive:
            self._load(self.archive.path)

    def _load(self, path):
        if self.archive:
            self.archive.close()
            self.archive = None
        try:
            self.archive = pak_formats.open_pak(path, key=self._key())
        except UnsupportedFeature as exc:
            messagebox.showwarning("Şifreli / Desteklenmiyor", str(exc))
            return
        except PakError as exc:
            messagebox.showerror("Hata", f"PAK açılamadı:\n{exc}")
            return
        except Exception as exc:
            messagebox.showerror("Hata", f"PAK açılamadı:\n{exc}")
            return

        self.file_label.configure(text=os.path.basename(path))
        fmt = self.archive.format_name
        ver = self.archive.info.get("version", "")
        mount = self.archive.mount_point or ""
        self.format_label.configure(text=f"{fmt} {ver}" + (f"  •  mount: {mount}" if mount else ""))
        self.refresh_list()
        self.status.configure(text=f"Açıldı: {path} ({len(self.archive.entries)} dosya)")

    def refresh_list(self):
        self.tree.delete(*self.tree.get_children())
        if not self.archive:
            return
        query = self.filter_var.get().strip().lower()
        count = 0
        for entry in self.archive.entries:
            if query and query not in entry.path.lower():
                continue
            method = "yok"
            if entry.compression not in ("none", ""):
                method = entry.compression
            enc = "evet" if entry.encrypted else "-"
            self.tree.insert("", "end", values=(
                entry.path,
                _human(entry.size),
                _human(entry.compressed_size),
                method,
                enc,
            ))
            count += 1
        self.status.configure(text=f"{count} / {len(self.archive.entries)} dosya")

    def _selected_entry(self):
        item = self.tree.focus()
        if not item:
            return None
        path = self.tree.item(item, "values")[0]
        for entry in self.archive.entries:
            if entry.path == path:
                return entry
        return None

    def on_select(self, _event=None):
        if not self.archive:
            return
        entry = self._selected_entry()
        if entry is None:
            return
        self.current = entry
        self.info.configure(text=(
            f"{entry.path}\n"
            f"Boyut: {entry.size:,} bayt  •  Depolanan: {entry.compressed_size:,} bayt  "
            f"•  Yöntem: {entry.compression}  •  Şifreli: {'evet' if entry.encrypted else 'hayır'}"
        ))
        self.preview.delete("1.0", "end")
        try:
            data = self.archive.read(entry)
        except UnsupportedFeature as exc:
            self.preview.insert("1.0", f"⚠ {exc}")
            return
        except Exception as exc:
            self.preview.insert("1.0", f"Okunamadı: {exc}")
            return

        if _looks_text(data[:TEXT_PREVIEW_LIMIT]):
            self.preview.insert("1.0", data[:TEXT_PREVIEW_LIMIT].decode("utf-8", "replace"))
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
        if not self.archive or not self.current:
            messagebox.showwarning("Uyarı", "Önce bir dosya seçin.")
            return
        name = os.path.basename(self.current.path) or "file"
        target = filedialog.asksaveasfilename(initialfile=name)
        if not target:
            return
        try:
            with open(target, "wb") as fh:
                fh.write(self.archive.read(self.current))
            self.status.configure(text=f"Çıkarıldı: {target}")
        except UnsupportedFeature as exc:
            messagebox.showwarning("Desteklenmiyor", str(exc))
        except Exception as exc:
            messagebox.showerror("Hata", f"Çıkarılamadı:\n{exc}")

    def extract_all(self):
        if not self.archive:
            messagebox.showwarning("Uyarı", "Önce bir PAK açın.")
            return
        dest = filedialog.askdirectory(title="Çıkarma klasörü seç")
        if not dest:
            return
        done = failed = 0
        errors = []
        for entry in self.archive.entries:
            safe = entry.path.replace("\\", "/").lstrip("/")
            safe = safe.replace("..", "_")
            target = os.path.join(dest, *safe.split("/"))
            try:
                os.makedirs(os.path.dirname(target), exist_ok=True)
                with open(target, "wb") as fh:
                    fh.write(self.archive.read(entry))
                done += 1
            except Exception as exc:
                failed += 1
                if len(errors) < 5:
                    errors.append(f"{entry.path}: {exc}")
        msg = f"{done} dosya çıkarıldı."
        if failed:
            msg += f"\n{failed} dosya başarısız:\n" + "\n".join(errors)
        self.status.configure(text=msg.splitlines()[0])
        if failed:
            messagebox.showwarning("Kısmen tamam", msg)
        else:
            messagebox.showinfo("Tamam", msg)


if __name__ == "__main__":
    PakViewer().mainloop()
