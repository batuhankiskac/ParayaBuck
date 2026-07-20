# ParayaBuck

Python trading bot for **US stocks** via **Alpaca** API. Backtest, paper trade, and live trade — all from a **desktop GUI** (PySide6/Qt).

---

## Quick Start

```bash
# 1. Install
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# 2. Launch the GUI
parayabuck
```

İlk açılışta bot, Alpaca kimlik bilgileri doluysa otomatik başlar. Kimlik yoksa Ayarlar sekmesine yönlendirilirsiniz.

---

## Installation

| Step | Command |
|------|---------|
| Clone | `git clone <repo> && cd ParayaBuck` |
| Venv | `python3 -m venv .venv && source .venv/bin/activate` |
| Install | `pip install -e ".[dev]"` |
| Run | `parayabuck` |

Alpaca anahtarlarınızı GUI açıldıktan sonra **Ayarlar** sekmesinden girip kaydedin.

---

## GUI Genel Bakış

Üç sekme:

### 1. Bot

- **Başlat / Durdur** butonları
- Equity / Cash / Günlük PnL / Pozisyon sayısı (LCD göstergeler)
- Yenileme aralığı ayarı (saniye)
- Açık pozisyonlar tablosu
- Log konsolu (seviye filtresi, otomatik kaydır, temizle)

Bot açılışta otomatik başlar (kimlik bilgileri mevcutsa). Sembol listesi, timeframe ve risk parametreleri `config/settings.yaml`'den okunur. Regim tespiti → strateji seçimi → otomatik alım/satım tamamen arka planda çalışır.

### 2. Hesap

- **Paper / REAL** mod geçişi (REAL moda geçerken onay dialogu)
- Equity, Cash, Realized/Unrealized PnL, Günlük PnL, Drawdown
- Açık pozisyonlar tablosu
- Yenile butonu (anlık snapshot)

> Mod geçişi, bot duruyorken yapılabilir. Bot çalışıyken değiştirmek için önce **Durdur**'a basın. Seçim `config/settings.yaml`'in `alpaca.paper` alanına yazılır.

### 3. Ayarlar

Form tabanlı editör — ayar ezberlemeye gerek yok:

- **.env** — Alpaca API Key / Secret Key (gizli alan), log seviyesi
- **Account** — başlangıç bakiyesi, para birimi
- **Risk** — maks. pozisyon, günlük kayıp, drawdown, trade başına risk, komisyon, slippage
- **Portföy Riski** — pozisyon/sector %, maks. eşzamanlı pozisyon, min. nakit %
- **Strateji** — varsayılan strateji + parametre tablosu (key/value)
- **Backtest varsayılanları** — sembol, timeframe, tarih aralığı
- **Alpaca** — paper modu, gecikmeli veri
- **Semboller** — checkbox listesi ile aktif/pasif
- **Kaydet / Yeniden Yükle / raw YAML** (ileridekiler için ham düzenleme)

Kaydet'e bastığınızda hem `.env` hem `settings.yaml` yazılır. Bot çalışıyorsa "yeni ayarlarla yeniden başlat" dialogu açılır.

---

## Configuration

### `.env` — API credentials

```ini
ALPACA_API_KEY=your_key
ALPACA_SECRET_KEY=your_secret
LOG_LEVEL=INFO
```

### `config/settings.yaml` — bot settings

| Bölüm | Ne işe yarar |
|-------|-------------|
| `account.starting_balance` | Backtest/paper'da başlangıç bakiyesi |
| `risk.*` | Maksimum pozisyon, günlük kayıp limiti, drawdown limiti, komisyon |
| `portfolio.*` | Portföy dağılım kuralları (sektör, pozisyon başı limit) |
| `symbols` | İşlem yapılacak hisseler ve kontrat özellikleri |
| `strategy` | Varsayılan strateji ve parametreleri |
| `backtest` | Varsayılan sembol, timeframe, tarih aralığı |
| `alpaca` | Paper/real modu, gecikmeli data |

Tümünü GUI → Ayarlar sekmesinden düzenleyebilirsiniz.

---

## Bot Çalışma Davranışı

Açılışta:

1. `.env` ve `config/settings.yaml` okunur
2. Alpaca'ya bağlanılır (paper veya real — `alpaca.paper`'a göre)
3. Tüm semboller için warmup verisi çekilir
4. Sembol başına **regim tespiti** yapılır (trend / ranging / kriz / volatilite)
5. Regime'e göre en uygun strateji atanır (örn. trend → `ema_crossover`, ranging → `mean_reversion`)
6. Poll döngüsü başlar (her `poll_interval` saniyede bir):
   - Yeni bar çek
   - Strateji sinyali varsa pozisyon aç/kapat
   - ATR tabanlı stop-loss / take-profit / trailing stop kontrolü
   - Kill-switch: günlük kayıp/drawdown limiti aşılırsa **yeni entry durur**, mevcut pozisyonlar yönetilmeye devam eder
7. Her 30 dakikada regime re-check → gerekirse strateji rotate edilir

> **Timeframe uyarısı:** Varsayılan `15m`. `1m`/`5m` gibi düşük timeframe'lerde ATR çok küçük olur ve stop floor (~%4) TP'ten (~%0.1) çok uzaklaşır → risk:ödül ters döner. Düşük timeframe'te mutlaka stop-loss ve take-profit'i uyumlu ayarlayın (Ayarlar sekmesinden veya `config/settings.yaml`'den).

---

## Stratejiler

| Strateji | Açıklama | Kullanım |
|----------|----------|----------|
| `ema_crossover` | EMA 9/21 cross | Trend takibi, basit |
| `mean_reversion` | Bollinger + RSI | Düşük volatilite |
| `pairs_trading` | İstatistiksel arbitraj | Piyasa nötr |
| `multi_factor` | Multi-faktor momentum | Trend piyasası |
| `vwap_reversion` | VWAP intraday reversion | Gün içi |

Bot, sembol başına regime tespiti yaparak uygun stratejiyi otomatik seçer. Manuel seçim Ayarlar → Strateji dropdown'ından.

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

Stratejiyi `parayabuck/gui/widgets/settings_panel.py`'deki `KNOWN_STRATEGIES` listesine ekleyin.

---

## Geliştirme

```bash
# Lint
ruff check src/parayabuck

# Typecheck
mypy src/parayabuck/gui

# GUI smoke test (headless)
QT_QPA_PLATFORM=offscreen python -c "
import sys
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QTimer
from pathlib import Path
from parayabuck.gui.main_window import MainWindow, _apply_dark_palette
MainWindow._auto_start = lambda self: None
app = QApplication(sys.argv)
_apply_dark_palette(app)
w = MainWindow(Path('config/settings.yaml'), Path('.env.example'))
w.show()
QTimer.singleShot(500, app.quit)
sys.exit(app.exec())
"
```

---

## Disclaimer

This is for educational purposes. Trading stocks involves substantial risk of loss. Always test with paper money before risking real capital.
