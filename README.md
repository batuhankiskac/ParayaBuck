# ParayaBuck

Trading bot for **US stocks and crypto** via the **Alpaca** API. Backtest,
paper trade and live trade are available through the V2 event-driven engine
and Web terminal.

## ParayaBuck v2

The active engine lives in `src/parayabuck_v2` and is used by the Web UI as
well as the shadow, paper/live and backtest entry points. Stocks and crypto
have separate strategies; strategies produce scored, explainable signals and
never submit orders directly.

Safety defaults:

- `shadow` is the default and never submits an Alpaca order.
- `paper` requires the explicit `--confirm-paper YES` flag.
- `live` requires both `--confirm-live YES` and the
  `PARAYABUCK_V2_ENABLE_LIVE=YES` environment variable.
- Decisions, rejected signals, order transitions, fills and account snapshots
  are persisted in `data/runtime/parayabuck_v2.sqlite3`.

Run shadow mode:

```bash
python -m parayabuck_v2 --mode shadow --symbols AAPL MSFT BTC/USD
```

Run Alpaca paper mode:

```bash
python -m parayabuck_v2 --mode paper --confirm-paper YES --symbols AAPL BTC/USD
```

Run an offline CSV backtest:

```bash
python scripts/run_v2_backtest.py data/historical/MES_5m_2024.csv \
  --symbol MES --market equity --timeframe 5m
```

V2 risk and engine settings are in `config/settings_v2.yaml`. Do not enable
live mode until out-of-sample, shadow and paper results have been reviewed.

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

Uygulama çalıştırıldığında Web UI sunucusu başlar. Varsayılan olarak tarayıcınız **`http://127.0.0.1:8000`** adresini açar; bu davranış Ayarlar içindeki gelişmiş program ayarlarından kapatılabilir.

V2 motoru güvenlik nedeniyle Web UI içinden **Motoru Başlat** düğmesine basıldığında çalışır. İlk kullanımda Alpaca kimlik bilgilerini Ayarlar sekmesinden (.env ve settings.yaml) girip kaydedebilirsiniz.

Gerçek para modu ayrıca uygulama başlatılmadan önce açıkça etkinleştirilmelidir:

```bash
PARAYABUCK_V2_ENABLE_LIVE=YES parayabuck
```

Bu değişken olmadan gerçek para motoru başlamaz. Uygulama güvenlik nedeniyle yalnızca
`127.0.0.1`, `localhost` veya `::1` adresine bağlanır. Ücretsiz Alpaca hesaplarında
gerçek zamanlı IEX verisi için `alpaca.stock_feed: iex`; uygun ücretli veri aboneliğinde
`sip` kullanılabilir.

---

## Installation

| Step | Command |
|------|---------|
| Clone | `git clone <repo> && cd ParayaBuck` |
| Venv | `python3 -m venv .venv && source .venv/bin/activate` |
| Install | `pip install -e ".[dev]"` |
| Run | `parayabuck` |

Alpaca anahtarlarınızı Web UI açıldıktan sonra **Ayarlar** butonuna basarak girip kaydedin.

`python -m parayabuck` ve `parayabuck` komutları yalnızca Web UI sunucusunu başlatır; emir, strateji veya backtest işlemleri için ayrı bir kullanıcı CLI'sı yoktur.

---

## Web UI Terminal Özellikleri

- **Üst Header Bar**: Logo, canlı işlem modu rozeti (`📜 PAPER DEMO` / `⚠️ REAL TRADING`), motor durumu ve hızlı **▶ Başlat / ■ Durdur** kontrolleri.
- **Bakiye & İstatistik Kartları**: Net Portföy (Equity), Nakit, Günlük PnL, Gerçekleşen PnL, Açık Pozisyon PnL ve Drawdown kartları.
- **Portföy Grafik Analitiği**: Chart.js ile canlı equity büyüme eğrisi.
- **Açık Pozisyonlar Tablosu**: LONG/SHORT badge rozetleri ve canlı kâr/zarar durumları.
- **Terminal Konsol**: Real-time WebSocket üzerinden yayınlanan canlı sistem logları.
- **Ayarlar Modalı**: Alpaca API anahtarları, risk limitleri ve mod ayarları.

## Geliştirici araçları

`scripts/generate_sample_data.py`, backtest/test geliştirmesi için sentetik OHLCV verisi üreten geliştirici aracıdır. Web UI'nin çalışma akışının parçası değildir.
