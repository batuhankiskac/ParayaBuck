# Strateji araştırma akışı

Ana araştırma zaman dilimi `1h`'dir. Alpha denemelerinde işlem riski deney
manifesti tarafından `%0,25`'te sabitlenir; risk oranı strateji seçim
parametresi değildir.

## Hazır deney

Kilitli holdout'a dokunmadan aylık validation fold'larını çalıştırmak için:

```bash
python scripts/run_backtest.py \
  --experiment-spec config/experiments/strategy_1h_v1.yaml \
  --output-dir reports/experiments/strategy_1h_v1
```

Bu koşu stratejiye özel giriş parametrelerini ve sabit/break-even/Chandelier/
süre çıkışlarını markete özel base ve stress maliyetleriyle değerlendirir.
`trial_results.json` medyan Calmar sıralamasını, DSR, CSCV/PBO, stress
hassasiyetini ve parametre platosunu içerir.

Son altı ay varsayılan olarak değerlendirilmez. Önceden seçilmiş en fazla on
finalist için holdout'u bir kez açmak açık bir karar gerektirir:

```bash
python scripts/run_backtest.py \
  --experiment-spec config/experiments/strategy_1h_v1.yaml \
  --unlock-holdout \
  --output-dir reports/experiments/strategy_1h_v1_holdout
```

Bu işlem `data/runtime/holdout_registry.json` kaydı oluşturur. Aynı deney
kimliğiyle ikinci holdout değerlendirmesi reddedilir.

## Timeframe karşılaştırması ve uzun 1h koşusu

```bash
python scripts/run_strategy_backtests.py
```

Koşucu iki ayrı rapor ailesi üretir:

- `reports/backtests/common_120d/`: aynı 120 günlük 5m kaynaktan türetilen
  5m/15m/1h barların karşılaştırması.
- `reports/backtests/research_1h/`: mevcut yaklaşık 720 günlük 1h araştırması.

Borsa araştırması kalıcı geniş/sektör ETF çekirdeğini kullanır. Kripto üyeliği
her ay yalnızca geriye dönük 30 günlük likidite, ATR ve veri sürekliliğiyle
yeniden seçilir. Her strateji hem tek başına hem birleşik portföyde raporlanır.

## Çıktılar ve güvenlik

Manifest; Git revizyonu, veri hash'i, evren sürümü, seed, maliyet senaryosu,
strateji sürümü, parametre gridleri ve çıkış politikalarını kaydeder. Normal
raporlara `score_deciles.csv` ve `by_regime.csv` eklenmiştir.

Dinamik stoplar yalnızca kapanmış bardan hesaplanır ve sonraki barda etkin
olur. Alpaca sınırı stopu önce iptal etmeden atomik replace yapar; güncel stop
ve yeni broker order kimliği aynı ledger transaction'ında kalıcılaştırılır.
Broker hatasında eski stop korunur. Buna rağmen canlı güvenlik açıkken dinamik
çıkış politikaları, kapanmış-bar scheduler'ı ve profil doğrulaması tamamlanana
kadar reddedilir. `equity_1h_v1` ve `crypto_1h_v1` canlı allowlist'e
eklenmemiştir; production gate geçilmeden 1h canlı işlem açılamaz.
