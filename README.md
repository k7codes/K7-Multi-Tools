# DB / ASAR / PAK Viewer

CustomTkinter (Python) ile yazılmış, karanlık temalı masaüstü görüntüleyici/çıkarıcı araç seti.
Üç bağımsız uygulama içerir:

| Uygulama | Dosya | Açtığı formatlar |
|----------|-------|------------------|
| **DB Viewer** | `db_viewer.py` | SQLite (`.db`, `.sqlite`, `.sqlite3`, `.db3`, `.sql` ...) |
| **ASAR Viewer** | `asar_viewer.py` | Electron `.asar` arşivleri |
| **PAK Viewer** | `pak_viewer.py` | Unreal Engine `.pak`, Quake PAK, Source VPK, ZIP |

---

## Özellikler

### DB Viewer — SQLite
- Veritabanı açma, sol panelde **tablo / view** listesi
- Tabloyu seçince veriyi ızgarada gösterme (zebra satır renkleri, `NULL` gösterimi)
- **SQL Sorgu** sekmesinde serbest sorgu çalıştırma
- Satır limiti seçimi (50 – 5000 / Tümü)
- Görüntülenen sonucu **CSV** olarak dışa aktarma

### ASAR Viewer — Electron `.asar`
- Dosya ağacı görünümü, isim ile filtreleme
- Metin dosyaları için önizleme, ikili dosyalar için **hex dump**
- Tek dosya veya **tüm arşivi** klasör yapısını koruyarak çıkarma

### PAK Viewer — otomatik format algılama
- Dosya açılışta **imzaya göre otomatik algılanır** (Unreal / Quake / VPK / ZIP)
- Tablo: yol, boyut, depolanan boyut, sıkıştırma yöntemi, şifreli mi
- Metin/hex önizleme, tek dosya veya tümünü çıkarma, filtreleme
- Şifreli paketler için **AES anahtarı** alanı (base64 veya hex) + "Yeniden Aç"

---

## PAK format desteği

| Format | Detay |
|--------|-------|
| **Unreal Engine (UE4/UE5)** | Versiyon V1–V11 · şifreli index (AES-256-ECB) · şifreli dosya verisi · Zlib / Gzip / Zstd / LZ4 açma |
| **Quake / id Software PAK** | `PACK` imzalı basit arşiv (şifreleme yok) |
| **Valve Source VPK** | v1 ve v2 · gömülü veri + harici parça dosyaları (`*_000.vpk`, ...) |
| **ZIP** | `.pak` olarak yeniden adlandırılmış zip arşivleri |

> **Oodle** sıkıştırması özel/lisanslı bir kütüphane gerektirdiğinden desteklenmez; bu dosyalar için uyarı gösterilir.

---

## Kurulum

**Gereksinim:** Python 3.9+ (Tkinter dahil)

```bash
pip install -r requirements.txt
```

İsteğe bağlı (yalnızca o özellik kullanılacaksa):

| Paket | Ne için |
|-------|---------|
| `cryptography` | Şifreli Unreal/ASAR için AES |
| `lz4` | LZ4 sıkıştırmalı Unreal paketleri |
| `zstandard` | Zstd sıkıştırmalı Unreal paketleri |

---

## Kullanım

Menüden veya doğrudan:

```bash
python db_viewer.py      # SQLite görüntüleyici
python asar_viewer.py    # ASAR görüntüleyici
python pak_viewer.py     # PAK görüntüleyici
```

Windows'ta çift tıklama için hazır başlatıcılar:

- `db_viewer.bat`
- `asar_viewer.bat`
- `pak_viewer.bat`

### Şifreli PAK açma
1. `pak_viewer.py` ile paketi açın (şifreli index varsa anahtar istenir).
2. AES anahtarını `AES anahtarı` alanına yapıştırın — hem **base64** (ör. Unreal `Crypto.json` içindeki `Key`) hem **hex** kabul edilir.
3. **Yeniden Aç**'a basın.

---

## Proje yapısı

```
db_viewer.py      # SQLite görüntüleyici
asar_viewer.py    # ASAR görüntüleyici
pak_viewer.py     # PAK görüntüleyici (arayüz)
pak_formats.py    # PAK parser'ları + format algılama (Unreal/Quake/VPK/ZIP)
requirements.txt
```

---

## Sınırlamalar

- `.pak` uzantısını paylaşan bazı özel motorlar (ör. Godot `.pck`) farklı yapıda olduğundan desteklenmez.
- Oodle sıkıştırması desteklenmez.
- Çok büyük dosyalarda önizleme ilk ~1 MB ile sınırlıdır (çıkarma tam yapılır).

---

## Lisans

MIT
