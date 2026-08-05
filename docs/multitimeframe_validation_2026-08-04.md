# Çoklu timeframe optimizasyon doğrulaması — 2026-08-04

Bu çalışma mevcut dört stratejiyi `5m`, `15m` ve `1d` için birbirinden
bağımsız parametre uzayları, maliyet senaryoları, exit politikaları, seed'ler
ve kilitli outer holdout'larla değerlendirdi. `1h` ana araştırma manifesti
değişmeden korunmuştur.

## Koşu kapsamı

| Timeframe | Veri aralığı | Train/validation/holdout | Aday | OOS fold | Veri kapsaması |
| --- | --- | --- | ---: | ---: | ---: |
| `5m` | 2026-04-06–2026-08-03 | 2/1/1 ay | 160 | 1 | %92,8–100,0 |
| `15m` | 2026-02-05–2026-08-03 | 3/1/1 ay | 160 | 2 | %95,3–100,0 |
| `1d` | 2024-08-14–2026-08-03 | 9/1/6 ay | 176 | 9 | %95,9–100,1 |

Alpaca geçmiş veri çağrısı çalışma ortamında erişilemediği için koşular
feed-matched yerel warmup cache'e düştü. Manifestler tam veri hash'ini ve
holdout çıkarıldıktan sonraki analiz hash'ini ayrı kaydetti.

## Holdout öncesi birleşik baseline

Bu tablo aday seçimi değildir; dört varsayılan stratejinin yalnız görünür
araştırma dönemindeki birleşik backtest sonucudur.

| Timeframe | İşlem | Net getiri | PF | Sharpe |
| --- | ---: | ---: | ---: | ---: |
| `5m` | 265 | -%0,29 | 0,93 | -0,60 |
| `15m` | 98 | -%1,33 | 0,69 | -1,71 |
| `1d` | 15 | +%1,32 | 1,93 | 0,61 |

## Nested OOS aday sonuçları

Her satır, ilgili stratejide validation fold'larının medyan Calmar'ına göre
ilk sıradaki adayı gösterir. Çok az işlemli satırlardaki PF/Sharpe değerleri
edge kanıtı sayılmaz.

| Timeframe | Strateji | OOS işlem | PF | Sharpe | Expectancy | Stress expectancy | Plato |
| --- | --- | ---: | ---: | ---: | ---: | ---: | --- |
| `5m` | `stock_trend_pullback` | 26 | 0,46 | -5,77 | -34,27 | -52,85 | Hayır |
| `5m` | `stock_mean_reversion` | 0 | 0,00 | 0,00 | 0,00 | 0,00 | Hayır |
| `5m` | `crypto_trend_breakout` | 0 | 0,00 | 0,00 | 0,00 | 0,00 | Hayır |
| `5m` | `crypto_range_reversion` | 0 | 0,00 | 0,00 | 0,00 | 0,00 | Hayır |
| `15m` | `stock_trend_pullback` | 28 | 0,55 | -2,58 | -22,20 | -36,46 | Hayır |
| `15m` | `stock_mean_reversion` | 1 | tanımsız | 1,77 | +49,20 | +38,70 | Hayır |
| `15m` | `crypto_trend_breakout` | 0 | 0,00 | 0,00 | 0,00 | 0,00 | Hayır |
| `15m` | `crypto_range_reversion` | 0 | 0,00 | 0,00 | 0,00 | 0,00 | Hayır |
| `1d` | `stock_trend_pullback` | 1 | 0,00 | 0,00 | -36,70 | -48,17 | Hayır |
| `1d` | `stock_mean_reversion` | 0 | 0,00 | 0,00 | 0,00 | 0,00 | Hayır |
| `1d` | `crypto_trend_breakout` | 3 | 1,55 | 0,00 | +36,25 | +25,09 | Hayır |
| `1d` | `crypto_range_reversion` | 0 | 0,00 | 0,00 | 0,00 | 0,00 | Hayır |

## Sonuç

- Parametre ve exit-policy taramaları üç timeframe'de de eksiksiz tamamlandı.
- Hisse ve kripto adayları aynı manifest holdout sınırına bağlandı; piyasa son
  bar tarihlerinin farklı olması fold sayısını artık değiştirmiyor.
- `5m`, `15m` ve `1d` sonuçlarının hiçbirinde en az beş komşu
  konfigürasyonla desteklenen pozitif parametre platosu oluşmadı.
- Hiçbir aday 100 OOS işlem, PF, Sharpe, DSR/PBO ve stress maliyeti production
  kapılarını birlikte karşılamadı. Kilitli holdout açılmadı ve holdout registry
  oluşturulmadı.
- `5m` ve `15m` sonuçları kısa Alpaca veri pencereleri nedeniyle yalnız erken
  falsification kanıtıdır. Yeni veri geldikçe yeni fold eklenmeden bu
  timeframe'ler için production kararı verilmemelidir.

## Tekrar üretme

```bash
.venv/bin/python scripts/run_backtest.py \
  --experiment-spec config/experiments/strategy_5m_v1.yaml \
  --output-dir reports/experiments/strategy_5m_v1_validation

.venv/bin/python scripts/run_backtest.py \
  --experiment-spec config/experiments/strategy_15m_v1.yaml \
  --output-dir reports/experiments/strategy_15m_v1_validation

.venv/bin/python scripts/run_backtest.py \
  --experiment-spec config/experiments/strategy_1d_v1.yaml \
  --output-dir reports/experiments/strategy_1d_v1_validation
```

Bu komutlarda `--unlock-holdout` özellikle kullanılmamalıdır.
