import csv
import os
import sqlite3
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import customtkinter as ctk

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

DB_EXTENSIONS = ".db .sqlite .sqlite3 .db3 .s3db .sl3 .sqlite2 .sql"
ROW_LIMITS = ["50", "100", "500", "1000", "5000", "Tümü"]


class DatabaseViewer(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("DB Viewer")
        self.geometry("1200x760")
        self.minsize(900, 560)

        self.conn = None
        self.db_path = None
        self.current_table = None
        self.current_columns = []
        self.current_rows = []

        self._build_toolbar()
        self._build_body()
        self._build_statusbar()

    # ---------------------------------------------------------------- UI
    def _build_toolbar(self):
        bar = ctk.CTkFrame(self, corner_radius=0, height=52)
        bar.pack(side="top", fill="x")
        bar.pack_propagate(False)

        ctk.CTkButton(bar, text="Veritabanı Aç", width=140, command=self.open_database).pack(
            side="left", padx=(12, 8), pady=10
        )
        ctk.CTkButton(
            bar, text="Yenile", width=80, fg_color="gray30", hover_color="gray40",
            command=self.refresh_table,
        ).pack(side="left", padx=4, pady=10)

        ctk.CTkLabel(bar, text="Satır limiti:").pack(side="left", padx=(20, 6))
        self.row_limit = ctk.StringVar(value="500")
        ctk.CTkOptionMenu(bar, values=ROW_LIMITS, variable=self.row_limit, width=100).pack(
            side="left", pady=10
        )

        ctk.CTkButton(
            bar, text="CSV Dışa Aktar", width=130, fg_color="#2f9e44", hover_color="#2b8a3e",
            command=self.export_csv,
        ).pack(side="right", padx=12, pady=10)

        self.db_label = ctk.CTkLabel(bar, text="Açık veritabanı yok", text_color="gray70")
        self.db_label.pack(side="right", padx=12)

    def _build_body(self):
        body = ctk.CTkFrame(self, corner_radius=0, fg_color="transparent")
        body.pack(side="top", fill="both", expand=True)
        body.grid_rowconfigure(0, weight=1)
        body.grid_columnconfigure(1, weight=1)

        # --- sidebar: tables -------------------------------------------------
        side = ctk.CTkFrame(body, width=230, corner_radius=0)
        side.grid(row=0, column=0, sticky="nsw")
        side.grid_propagate(False)

        ctk.CTkLabel(side, text="Tablolar / Görünümler", font=ctk.CTkFont(weight="bold")).pack(
            anchor="w", padx=12, pady=(12, 6)
        )
        self.table_list = ctk.CTkScrollableFrame(side, label_text="")
        self.table_list.pack(fill="both", expand=True, padx=8, pady=(0, 12))

        # --- content ---------------------------------------------------------
        self.tabs = ctk.CTkTabview(body)
        self.tabs.grid(row=0, column=1, sticky="nsew", padx=(6, 10), pady=10)

        self.data_tab = self.tabs.add("Veri")
        self.query_tab = self.tabs.add("SQL Sorgu")

        self._build_data_tab()
        self._build_query_tab()

    def _build_data_tab(self):
        frame = self.data_tab
        frame.grid_rowconfigure(1, weight=1)
        frame.grid_columnconfigure(0, weight=1)

        self.data_info = ctk.CTkLabel(frame, text="Bir tablo seçin", anchor="w")
        self.data_info.grid(row=0, column=0, sticky="ew", padx=4, pady=(0, 4))

        wrap = tk.Frame(frame)
        wrap.grid(row=1, column=0, sticky="nsew")
        wrap.grid_rowconfigure(0, weight=1)
        wrap.grid_columnconfigure(0, weight=1)

        self.grid_tree = ttk.Treeview(wrap, show="headings")
        ysb = ttk.Scrollbar(wrap, orient="vertical", command=self.grid_tree.yview)
        xsb = ttk.Scrollbar(wrap, orient="horizontal", command=self.grid_tree.xview)
        self.grid_tree.configure(yscrollcommand=ysb.set, xscrollcommand=xsb.set)
        self.grid_tree.grid(row=0, column=0, sticky="nsew")
        ysb.grid(row=0, column=1, sticky="ns")
        xsb.grid(row=1, column=0, sticky="ew")

    def _build_query_tab(self):
        frame = self.query_tab
        frame.grid_rowconfigure(2, weight=1)
        frame.grid_columnconfigure(0, weight=1)

        self.query_box = ctk.CTkTextbox(frame, height=120, font=("Consolas", 13))
        self.query_box.grid(row=0, column=0, sticky="ew", padx=2, pady=(4, 6))
        self.query_box.insert("1.0", "SELECT * FROM table_name LIMIT 100;")

        ctk.CTkButton(frame, text="Çalıştır", width=120, command=self.run_query).grid(
            row=1, column=0, sticky="w", padx=2, pady=(0, 6)
        )

        wrap = tk.Frame(frame)
        wrap.grid(row=2, column=0, sticky="nsew")
        wrap.grid_rowconfigure(0, weight=1)
        wrap.grid_columnconfigure(0, weight=1)

        self.query_tree = ttk.Treeview(wrap, show="headings")
        ysb = ttk.Scrollbar(wrap, orient="vertical", command=self.query_tree.yview)
        xsb = ttk.Scrollbar(wrap, orient="horizontal", command=self.query_tree.xview)
        self.query_tree.configure(yscrollcommand=ysb.set, xscrollcommand=xsb.set)
        self.query_tree.grid(row=0, column=0, sticky="nsew")
        ysb.grid(row=0, column=1, sticky="ns")
        xsb.grid(row=1, column=0, sticky="ew")

    def _build_statusbar(self):
        self.status = ctk.CTkLabel(self, text="Hazır", anchor="w", height=26, text_color="gray70")
        self.status.pack(side="bottom", fill="x", padx=12)

    # ------------------------------------------------------------ actions
    def open_database(self):
        path = filedialog.askopenfilename(
            title="SQLite veritabanı seç",
            filetypes=[("SQLite / DB", "*.db *.sqlite *.sqlite3 *.db3 *.s3db *.sl3 *.sqlite2 *.sql"),
                       ("Tüm dosyalar", "*.*")],
        )
        if not path:
            return
        try:
            if self.conn:
                self.conn.close()
            self.conn = sqlite3.connect(path)
            self.conn.row_factory = sqlite3.Row
            self.db_path = path
            self.db_label.configure(text=os.path.basename(path))
            self.current_table = None
            self._clear_tree(self.grid_tree)
            self.data_info.configure(text="Bir tablo seçin")
            self.load_schema()
            self.set_status(f"Açıldı: {path}")
        except Exception as exc:
            messagebox.showerror("Hata", f"Veritabanı açılamadı:\n{exc}")

    def load_schema(self):
        for child in self.table_list.winfo_children():
            child.destroy()

        rows = self.conn.execute(
            "SELECT name, type FROM sqlite_master "
            "WHERE type IN ('table','view') AND name NOT LIKE 'sqlite_%' ORDER BY type, name"
        ).fetchall()

        if not rows:
            ctk.CTkLabel(self.table_list, text="Tablo bulunamadı", text_color="gray60").pack(pady=8)
            return

        for row in rows:
            name, kind = row["name"], row["type"]
            label = f"{'▤' if kind == 'table' else '◧'}  {name}"
            btn = ctk.CTkButton(
                self.table_list, text=label, anchor="w", height=30,
                fg_color="transparent", hover_color="gray30",
                text_color=("gray10", "gray90"),
                command=lambda n=name: self.load_table(n),
            )
            btn.pack(fill="x", pady=1)

    def load_table(self, name):
        if not self.conn:
            return
        self.current_table = name
        limit = self._row_limit_value()
        sql = f'SELECT * FROM "{name}"' + (f" LIMIT {limit}" if limit else "")
        try:
            cur = self.conn.execute(sql)
            columns = [d[0] for d in cur.description]
            rows = cur.fetchall()
        except Exception as exc:
            messagebox.showerror("Hata", f"Tablo okunamadı:\n{exc}")
            return
        self.current_columns = columns
        self.current_rows = rows
        self._fill_tree(self.grid_tree, columns, rows)
        self.data_info.configure(text=f"{name}  •  {len(rows)} satır  •  {len(columns)} sütun")
        self.set_status(f"{name} yüklendi ({len(rows)} satır)")
        self.tabs.set("Veri")

    def refresh_table(self):
        if self.current_table:
            self.load_table(self.current_table)
        elif self.conn:
            self.load_schema()

    def run_query(self):
        if not self.conn:
            messagebox.showwarning("Uyarı", "Önce bir veritabanı açın.")
            return
        sql = self.query_box.get("1.0", "end").strip()
        if not sql:
            return
        try:
            cur = self.conn.execute(sql)
            if cur.description:
                columns = [d[0] for d in cur.description]
                rows = cur.fetchall()
                self._fill_tree(self.query_tree, columns, rows)
                self.set_status(f"Sorgu tamam: {len(rows)} satır")
            else:
                self.conn.commit()
                self._fill_tree(self.query_tree, ["Bilgi"], [("İşlem tamamlandı",)])
                self.set_status("Sorgu çalıştırıldı (sonuç kümesi yok)")
                self.load_schema()
        except Exception as exc:
            messagebox.showerror("SQL Hatası", str(exc))

    def export_csv(self):
        if not self.current_rows:
            messagebox.showwarning("Uyarı", "Dışa aktarılacak veri yok.")
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".csv", filetypes=[("CSV", "*.csv")],
            initialfile=f"{self.current_table or 'export'}.csv",
        )
        if not path:
            return
        try:
            with open(path, "w", newline="", encoding="utf-8-sig") as fh:
                writer = csv.writer(fh)
                writer.writerow(self.current_columns)
                for row in self.current_rows:
                    writer.writerow(list(row))
            self.set_status(f"CSV kaydedildi: {path}")
        except Exception as exc:
            messagebox.showerror("Hata", f"CSV yazılamadı:\n{exc}")

    # ------------------------------------------------------------ helpers
    def _row_limit_value(self):
        val = self.row_limit.get()
        return "" if val == "Tümü" else val

    def _fill_tree(self, tree, columns, rows):
        self._clear_tree(tree)
        tree["columns"] = columns
        for col in columns:
            tree.heading(col, text=col)
            tree.column(col, width=140, anchor="w", stretch=False)
        for i, row in enumerate(rows):
            values = ["NULL" if v is None else str(v) for v in row]
            tag = "even" if i % 2 == 0 else "odd"
            tree.insert("", "end", values=values, tags=(tag,))
        tree.tag_configure("even", background="#2b2b2b")
        tree.tag_configure("odd", background="#333333")

    def _clear_tree(self, tree):
        tree.delete(*tree.get_children())
        tree["columns"] = ()

    def set_status(self, text):
        self.status.configure(text=text)


if __name__ == "__main__":
    app = DatabaseViewer()
    app.mainloop()
