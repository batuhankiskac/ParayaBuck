# ParayaBuck

Python trading bot for **US stocks** via **Alpaca** API. Backtest, paper trade, and (eventually) live trade — all from the CLI.

---

## Quick Start (No API Key Required)

```bash
# 1. Install
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# 2. Generate synthetic data (5000 bars of 5m data)
python scripts/generate_sample_data.py --symbol SPY --timeframe 5m --bars 5000

# 3. Run backtest on synthetic data (CSV source)
parayabuck backtest --source csv
```

---

## Installation

| Step | Command |
|------|---------|
| Clone | `git clone <repo> && cd ParayaBuck` |
| Venv | `python3 -m venv .venv && source .venv/bin/activate` |
| Install | `pip install -e ".[dev]"` |
| Env | `cp .env.example .env` ve [Alpaca](https://app.alpaca.markets) anahtarlarını gir |

---

## Configuration

### 1. `.env` — API credentials

```ini
ALPACA_API_KEY=your_key
ALPACA_SECRET_KEY=your_secret
```

### 2. `config/settings.yaml` — bot settings

| Bölüm | Ne işe yarar |
|-------|-------------|
| `account.starting_balance` | Backtest/paper'da başlangıç bakiyesi |
| `risk.*` | Maksimum pozisyon, günlük kayıp limiti, drawdown limiti, komisyon |
| `portfolio.*` | Portföy dağılım kuralları (sektör, pozisyon başı limit) |
| `symbols` | İşlem yapılacak hisseler ve kontrat özellikleri |
| `strategy` | Varsayılan strateji ve parametreleri |
| `backtest` | Varsayılan sembol, timeframe, tarih aralığı |
| `alpaca` | Paper/real modu, gecikmeli data |

---

## CLI Komutları

### `parayabuck backtest`

Geçmiş veride strateji test et.

```
Options:
  -s, --strategy TEXT     Strateji adı (config'deki varsayılanı ezer)
  -y, --symbol TEXT       Sembol (örn: SPY, AAPL)
  -t, --timeframe TEXT    Periyod (1m, 5m, 15m, 30m, 1h, 1d)
  --start YYYY-MM-DD      Başlangıç tarihi
  --end YYYY-MM-DD        Bitiş tarihi
  --source [alpaca|csv]   Veri kaynağı (varsayılan: alpaca)
  --metrics / --no-metrics Detaylı metrikler (Sharpe, drawdown vs.)
  --plot / --no-plot      Equity eğrisi grafiği çiz (output/ klasörüne kaydeder)
  --config, -c PATH       Config dosyası (varsayılan: config/settings.yaml)
  --log-level LEVEL       DEBUG/INFO/WARNING/ERROR (varsayılan: INFO)
```

Örnekler:

```bash
# CSV data ile backtest (Alpaca hesabı gerekmez)
parayabuck backtest --source csv

# Farklı strateji
parayabuck backtest -s ema_crossover -y AAPL -t 1d --start 2024-01-01 --end 2024-06-30

# Grafik çiz
parayabuck backtest --plot
```

### `parayabuck strategies`

Kullanılabilir stratejileri listele.

```
parayabuck strategies
```

| Strateji | Açıklama | Kullanım |
|----------|----------|----------|
| `ema_crossover` | EMA 9/21 cross | Trend takibi, basit |
| `mean_reversion` | Bollinger + RSI | Düşük volatilite |
| `pairs_trading` | İstatistiksel arbitraj | Piyasa nötr |
| `multi_factor` | Multi-faktor momentum | Trend piyasası |
| `vwap_reversion` | VWAP intraday reversion | Gün içi |

### `parayabuck paper`

Alpaca paper hesabı ile canlı data üzerinde kağıt ticareti.

```
Options:
  -s, --strategy TEXT   Strateji adı
  -y, --symbol TEXT     Sembol
  -t, --timeframe TEXT  Periyod
```

```bash
# Alpaca paper hesabına bağlan, canlı data ile çalıştır
parayabuck paper -s mean_reversion -y AAPL -t 15m
```

### `parayabuck run`

Fire-and-forget mod — otomatik strateji rotasyonu, risk yönetimi, çoklu sembol.

```
Options:
  -t, --timeframe TEXT     Bar periyodu (varsayılan: 15m)
  --poll INTEGER           Poll aralığı saniye (varsayılan: 60)
  --max-positions INTEGER  Maksimum eşzamanlı pozisyon (varsayılan: 8)
  -y, --symbols TEXT       Virgülle ayrılmış semboller (config'dekini ezer)
  --risk-pct FLOAT         Trade başına risk (equity oranı, varsayılan: 0.02)
  --stop-loss FLOAT        Hard stop-loss % (varsayılan: 0.04)
  --take-profit FLOAT      Take-profit % (varsayılan: 0.06)
  --trail FLOAT            Trailing stop % (varsayılan: 0.03)
  --paper / --real         Paper veya real hesap (varsayılan: paper)
```

```bash
# Tam otomatik: sembollere rejim analizi yap, strateji seç, çalıştır
parayabuck run --symbols AAPL,MSFT,NVDA --timeframe 5m
```

### `parayabuck auto`

Tam otomatik backtest — sembol seçimi, strateji rotasyonu, portföy backtest'i.

```
Options:
  -t, --timeframe TEXT    Periyod (varsayılan: 15m)
  --start YYYY-MM-DD      Başlangıç (varsayılan: 2025-01-01)
  --end YYYY-MM-DD        Bitiş (varsayılan: 2025-06-30)
  --max-symbols INTEGER   Maksimum sembol sayısı (varsayılan: 8)
  --plot / --no-plot      Grafik çiz
```

```bash
parayabuck auto --timeframe 1d --start 2024-01-01 --end 2024-12-31 --plot
```

### `parayabuck account`

Alpaca hesap bilgilerini göster (bakiye, pozisyonlar, PnL).

```bash
parayabuck account
```

### `parayabuck diversify`

Sembol evreninde çeşitlendirme analizi (sektör + korelasyon).

```bash
parayabuck diversify
```

---

## Adım Adım: İlk Backtest

```bash
# 1. Synthetic data oluştur
python scripts/generate_sample_data.py --symbol SPY --timeframe 15m --bars 5000

# 2. Farklı stratejileri dene
parayabuck backtest -s mean_reversion --source csv --metrics
parayabuck backtest -s ema_crossover --source csv --metrics
parayabuck backtest -s vwap_reversion --source csv --metrics

# 3. Grafiklerle karşılaştır
parayabuck backtest -s mean_reversion --source csv --plot
```

---

## Strateji Yazma

`parayabuck.strategy.base.Strategy`'den türet ve `on_bar()` metotunu implement et:

```python
from parayabuck.strategy.base import Strategy
from parayabuck.models import Bar, Order, Position, Side

class MyStrategy(Strategy):
    name = "my_strategy"

    def on_bar(self, bar: Bar, position: Position) -> Order | None:
        if position.is_flat and bar.close > bar.sma(20):
            return Order(symbol=bar.symbol, side=Side.BUY, qty=10)
        return None
```

Stratejiyi `cli.py` içindeki `_load_strategy()` fonksiyonuna ekle.

---

## Disclaimer

This is for educational purposes. Trading stocks involves substantial risk of loss. Always test with paper money before risking real capital.
