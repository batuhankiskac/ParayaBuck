# ParayaBuck

Python trading bot for **US stocks** via **Alpaca** API. Backtest, paper trade, and live trade — all from a **Modern Glassmorphic Web Terminal** (FastAPI + Glassmorphic Web UI).

---

## Quick Start

```bash
# 1. Install
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# 2. Launch the Web UI
parayabuck
```

Veya doğrudan python ile:
```bash
python -m parayabuck
```

Uygulama çalıştırıldığında Web UI sunucusu başlar ve tarayıcınız otomatik olarak **`http://127.0.0.1:8000`** adresinde açılır.

İlk açılışta bot, Alpaca kimlik bilgileri doluysa otomatik başlar. Kimlik yoksa Ayarlar sekmesinden (.env ve settings.yaml) kolayca girip kaydedebilirsiniz.

---

## Installation

| Step | Command |
|------|---------|
| Clone | `git clone <repo> && cd ParayaBuck` |
| Venv | `python3 -m venv .venv && source .venv/bin/activate` |
| Install | `pip install -e ".[dev]"` |
| Run | `parayabuck` |

Alpaca anahtarlarınızı Web UI açıldıktan sonra **Ayarlar** butonuna basarak girip kaydedin.

---

## Web UI Terminal Özellikleri

- **Üst Header Bar**: Logo, canlı işlem modu rozeti (`📜 PAPER DEMO` / `⚠️ REAL TRADING`), motor durumu ve hızlı **▶ Başlat / ■ Durdur** kontrolleri.
- **Bakiye & İstatistik Kartları**: Net Portföy (Equity), Nakit, Günlük PnL, Gerçekleşen PnL, Açık Pozisyon PnL ve Drawdown kartları.
- **Portföy Grafik Analitiği**: Chart.js ile canlı equity büyüme eğrisi.
- **Açık Pozisyonlar Tablosu**: LONG/SHORT badge rozetleri ve canlı kâr/zarar durumları.
- **Terminal Konsol**: Real-time WebSocket üzerinden yayınlanan canlı sistem logları.
- **Ayarlar Modalı**: Alpaca API anahtarları, risk limitleri ve mod ayarları.
