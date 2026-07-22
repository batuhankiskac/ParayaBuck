# ParayaBuck

ParayaBuck, Alpaca API üzerinden ABD hisseleri ve kripto varlıkları için çalışan olay
tabanlı bir işlem motoru ve yerel Web terminalidir. Stratejiler puanlanmış ve açıklanabilir
sinyaller üretir; risk yöneticisi ve broker sınırı onaylanmayan sinyallerin emre dönüşmesini
engeller.

## Güvenlik

- Varsayılan işlem modu Alpaca paper hesabıdır.
- Gerçek para modu hem Web UI üzerinden seçilmeli hem de uygulama
  `PARAYABUCK_ENABLE_LIVE=YES` ile başlatılmalıdır.
- Hisse girişleri broker üzerinde yaşayan bracket emirleri kullanır.
- Kripto girişleri koruyucu stop oluşturulamazsa güvenli biçimde kapatılır.
- Kararlar, reddedilen sinyaller, emir geçişleri ve hesap anlık görüntüleri
  `data/runtime/parayabuck.sqlite3` içinde tutulur.
- Web kontrol yüzeyi yalnızca loopback adreslerine bağlanabilir.

## Kurulum ve Web UI

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
parayabuck
```

Aynı uygulama doğrudan Python ile de başlatılabilir:

```bash
python -m parayabuck
```

Varsayılan adres `http://127.0.0.1:8000` olur. Alpaca anahtarları, paper/live modu,
risk yüzdeleri ve motor periyodu Web UI içindeki Ayarlar penceresinden yönetilir.

Gerçek para modu:

```bash
PARAYABUCK_ENABLE_LIVE=YES parayabuck
```

## Yapılandırma

Tek yapılandırma kaynağı `config/settings.yaml` dosyasıdır:

- `app`: Web sunucusu ve log ayarları
- `alpaca`: paper modu ve veri feed seçimi
- `symbols`: motorun takip edeceği semboller
- `engine`: periyot, sinyal eşiği, warmup, karar günlüğü ve yüzde bazlı risk limitleri

Ücretsiz Alpaca hesaplarında gerçek zamanlı hisse verisi için `stock_feed: iex`, uygun
veri aboneliğinde `stock_feed: sip` kullanılabilir.

## Offline backtest

```bash
python scripts/run_backtest.py data/historical/MES_5m_2024.csv \
  --symbol MES --market equity --timeframe 5m
```

Backtest aynı motor, strateji ve risk katmanını kullanır; yalnızca broker uygulaması
simülasyon brokerı ile değiştirilir.

## Geliştirme

```bash
.venv/bin/python -m pytest -q
.venv/bin/ruff check .
```

`scripts/generate_sample_data.py`, test ve backtest geliştirmesi için sentetik OHLCV
verisi üretir.
