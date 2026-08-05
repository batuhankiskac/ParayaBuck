# Strateji Mimari Dönüşümü — 2026-08-04

Bu doküman, tekil sembol ve indikatör tabanlı alpha katmanının neden ve nasıl
kaldırıldığını, yerine hangi portföy düzeyinde mimarinin kurulduğunu açıklar.

## 1. Neden kaldırıldı

Eski dört strateji (5m/15m/1h/1d üzerinde) kapsamlı şekilde tarandı; 496 aday
konfigürasyon hiçbir timeframe'de güvenilir parametre platosu üretmedi. Bulgular
`docs/multitimeframe_validation_2026-08-04.md` dosyasında audit kaydı olarak
korunur:

- `stock_trend_pullback`: timeframe'ler boyunca ana zarar kaynaklarından biri.
- `stock_mean_reversion`: yalnız 1h'de sınırlı umut; işlem sayısı yetersiz.
- `crypto_trend_breakout`: 1h'de PF ~1,09 — maliyetlere dayanıksız.
- `crypto_range_reversion`: pozitif ve kararlı OOS expectancy üretmedi.

Aynı veri üzerinde daha fazla parametre taraması ciddi overfitting riski
taşıyordu. Bu stratejiler ayrıca bar başına `Signal` üreten, tek sembol ve
`minimum_signal_score` eşiğine dayalı bir yapıya gömülüydü; portföy düzeyinde
sıralama ve hedef ağırlık üreten yeni araştırma yönüyle uyumsuzdu.

## 2. Kaldırılan bileşenler

| Bileşen | Dosya |
|---|---|
| `stock_trend_pullback` | `src/parayabuck/strategies/stock_trend.py` |
| `stock_mean_reversion` | `src/parayabuck/strategies/stock_mean_reversion.py` |
| `crypto_trend_breakout` | `src/parayabuck/strategies/crypto_trend.py` |
| `crypto_range_reversion` | `src/parayabuck/strategies/crypto_range.py` |
| Eski immutable parametre tipleri + `StrategySpec` | `src/parayabuck/strategies/specs.py` |
| Eski momentum yardımcıları (yalnız test kullanıyordu) | `src/parayabuck/backtest/challengers.py` |
| Eski nested deney motoru (eski spec'lere bağlı) | `src/parayabuck/backtest/research.py` |
| `minimum_signal_score` grid taraması | `src/parayabuck/backtest/sweep.py` |
| Eski deney manifestleri (5m/15m/1h/1d) | `config/experiments/strategy_*_v1.yaml` |
| Eski strateji backtest betiği | `scripts/run_strategy_backtests.py` |

Ek olarak:

- `TradingEngine`'den `VALIDATED_LIVE_STRATEGY_VERSIONS` ve canlı strateji
  sürüm gate'i kaldırıldı; `default_strategies()` geri dönüşü yok.
- `walk_forward_optimized` / `calendar_walk_forward_optimized` (skor sweep'ine
  dayalı nested optimizasyon) `validation.py`'den kaldırıldı.
- CLI'dan `--experiment-spec`, `--unlock-holdout`, `--sweep*`,
  `--walk-forward-optimize` ve eski deney/rapor yolları kaldırıldı.
- Eski stratejilere ait golden/sentetik testler yeni mimari testleriyle
  değiştirildi (`tests/test_crypto_strategies.py`,
  `tests/test_backtest_sweep.py` silindi; `test_strategy_research.py` yeniden
  yazıldı).

Korunan altyapı: BacktestEngine/BacktestBroker (fill simülasyonu, spread/
slippage/fee, parti kapasitesi), cost senaryoları, DSR/PBO, kilitli outer
holdout, ProductionGate çerçevesi, veri kapsam/hash kontrolleri, raporlama.

## 3. Yeni mimari

### Portföy katmanı — `src/parayabuck/portfolio/`

- `models.py`: `TargetWeight`, `TargetPortfolio`, `RebalancePolicy`,
  `PortfolioState` (restart koruması), `RebalanceReason`.
  Kurallar: ağırlık toplamı ≤ 1, negatif ağırlık reddi, long-only, nakit açık
  temsil, karar/effective timestamp ayrımı.
- `base.py`: `PortfolioContext` (yalnız kapanmış barlar, PIT evren, holdingler)
  ve `PortfolioStrategy` ABC.
- `momentum.py`: 12-1 / 6-1 / 3-6-12 normalize momentum, realized vol,
  ters-vol ağırlık, diyagonal vol hedefleme.
- `universes.py`: kalıcı ETF evreni (SPY/QQQ/IWM + 11 sektör SPDR), yalnız
  execution tarafında desteklenen spot çiftleri, versiyon sabitleri.
- Stratejiler: `equity_etf_dual_momentum_v1`, `crypto_cross_sectional_momentum_1h_v1`,
  `equity_time_series_momentum_1d_v1`.

### Portföy backtest motoru — `src/parayabuck/backtest/portfolio_engine.py`

`BacktestBroker`'ın maliyet modeli birebir korunarak portföy hedefleri için
yeni bir motor yazıldı (mevcut broker'a kısmi satış/alış eklemek, test edilmiş
giriş/çıkış mantığını bozma riski taşırdı):

- Kararlar kapanmış barlardan; emirler bir sonraki işlem yapılabilir barın
  açılışından dolar (slippage + yarım spread adverse, kripto taker fee).
- Aynı timestamp'te önce satış, sonra alış (nakit asla negatif olmaz).
- `turnover_band` ve `minimum_order_notional` küçük emirleri engeller.
- Sembol başına tek net delta → sell/buy churn yok.
- Yeni karar önceki dolmamış emirleri geçersiz kılar (duplicate order yok).
- `PortfolioState` JSON olarak persist edilir; restart aynı allokasyonla devam
  eder, ay ortasında plansız rebalance üretmez.
- Kısmi fill, hacim katılım sınırı, sıfır hacim güvenliği, fractional qty.

### Araştırma veri katmanı — `src/parayabuck/data/research_store.py`

Broker warmup cache'inden bağımsız, versiyonlu yerel depo:

- Sembol/timeframe partition'ları (Parquet varsa, yoksa gzip CSV).
- `DatasetManifest`: veri hash'i, feed, adjustment, indirme tarihi, eksik/
  duplicate bar sayısı, universe versiyonu.
- Point-in-time universe snapshot'ları (kripto için trailing likidite).

### Deneyler — `src/parayabuck/backtest/portfolio_experiments.py`

- `PortfolioExperimentSpec`: bütçeler (≤30 mekanizma, ≤200 parametre, ≤10
  finalist), train/validation/holdout, embargo, seed, cost senaryoları.
- `PortfolioGate` (piyasa bazlı): equity 60 OOS rebalance ayı + rejim
  çeşitliliği; crypto 100 OOS pozisyon + stress PF ≥ 1,0 + pozitif ay oranı.
- `run_portfolio_experiment`: takvim fold'ları, DSR/PBO tüm denemelerle,
  plato işaretleme, holdout yalnız `unlock_holdout` ile ve bir kez.

## 4. Canlı ve paper trading durumu

Bu dönüşüm sırasında canlı işlem kapalıdır. Yeni stratejiler production gate'i
geçmeden canlı allowlist'e eklenmez; geçse bile 30 gün shadow + 90 gün paper
şartı ve düşük riskli canary ile devreye alınır. Bugünkü araştırma sonuçları
(2026-08-04) hiçbir stratejiyi production'a taşımamaktadır; ayrıntı
`docs/research_summary_2026-08-04.md` içindedir.

## 5. Komutlar

```bash
# Veri indir + araştırma deposuna yaz
python scripts/run_portfolio_research.py --download

# Baseline'lar (base + stress maliyet karşılaştırması)
python scripts/run_portfolio_research.py --baseline

# Nested validation (holdout kilitli)
python scripts/run_portfolio_research.py --nested

# Holdout'u yalnız bir kez aç (yalnızca gate'i geçen finalistler için)
python scripts/run_portfolio_research.py --nested --unlock-holdout
```

## 6. Değiştirilen / silinen dosyaların özeti

### Silinen
- `src/parayabuck/strategies/stock_trend.py`, `stock_mean_reversion.py`,
  `crypto_trend.py`, `crypto_range.py`, `specs.py`
- `src/parayabuck/backtest/challengers.py`, `research.py`, `sweep.py`
- `config/experiments/strategy_5m_v1.yaml`, `strategy_15m_v1.yaml`,
  `strategy_1h_v1.yaml`, `strategy_1d_v1.yaml`
- `scripts/run_strategy_backtests.py`
- `tests/test_crypto_strategies.py`, `tests/test_backtest_sweep.py`

### Yeni
- `src/parayabuck/portfolio/` — modeller, context, momentum, universe,
  üç strateji, factory/registry
- `src/parayabuck/backtest/portfolio_engine.py` — portföy backtest motoru
- `src/parayabuck/backtest/portfolio_experiments.py` — deney spec + nested + gate
- `src/parayabuck/backtest/portfolio_report.py` — portföy raporları
- `src/parayabuck/data/research_store.py` — versiyonlu araştırma veri deposu
- `config/experiments/equity_dual_momentum_v1.yaml`, `equity_tsm_1d_v1.yaml`,
  `crypto_cross_sectional_1h_v1.yaml`
- `scripts/run_portfolio_research.py`
- `tests/test_portfolio_models.py`, `test_portfolio_engine.py`,
  `test_portfolio_experiments.py`, `test_research_store.py`, `test_cleanup.py`
- `docs/research_summary_2026-08-04.md`, `docs/strategy_migration_2026-08-04.md`

### Değiştirilen
- `src/parayabuck/engine.py` — eski canlı strateji sürüm gate'i ve
  `default_strategies` kaldırıldı
- `src/parayabuck/strategies/__init__.py` — yalnız genel `Strategy` ABC
- `src/parayabuck/backtest/experiments.py` — cost senaryoları, DSR/PBO, gate,
  manifest korundu; eski spec/candidate makineleri kaldırıldı
- `src/parayabuck/backtest/validation.py` — takvim fold'ları korundu;
  skor-sweep tabanlı nested optimizasyon kaldırıldı
- `src/parayabuck/backtest/cli.py` — veri indirme/kapsam/rapor aracına
  sadeleştirildi; eski strateji/deney/sweep seçenekleri kaldırıldı
- `src/parayabuck/backtest/__init__.py`, `tests/*` (eski strateji
  referansları ve anlamsızlaşan testler), `README.md`

### Korunan (audit)
- `docs/multitimeframe_validation_2026-08-04.md` (eski araştırma audit raporu)
- Eski koşulara ait `reports/backtests/` sonuçları
- `docs/strategy_research.md` (kullanıcı değişikliği)

## 7. Referanslar

- Eski araştırma audit raporu: `docs/multitimeframe_validation_2026-08-04.md`
- Yeni sonuç özeti: `docs/research_summary_2026-08-04.md`
- Raporlar: `reports/portfolio_research/`
