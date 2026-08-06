# Portföy stratejisi araştırma akışı

Bu doküman, emekli edilen sinyal tabanlı strateji araştırma akışının
(`--experiment-spec`, `--sweep`, `--walk-forward`) yerine geçen güncel
portföy araştırma akışını anlatır. Eski akış `scripts/run_backtest.py`
içinde artık yalnız "veri kalite / legacy fixture" modunda çalışır ve
strateji değerlendirmez (bkz. `scripts/run_backtest.py` başlığı ve
`docs/strategy_migration_2026-08-04.md`).

## Araştırma hattı

Tek giriş noktası `scripts/run_portfolio_research.py`:

```bash
# 1) Veri indirme (düzeltilmiş pipeline: canonical unique semboller,
#    dividend+split ayarlı, provider metadata + dataset kalite kapısı)
python scripts/run_portfolio_research.py --download

# 2) Baseline (yalnız kilitli holdout öncesi barlar; tam örneklem baseline yok)
python scripts/run_portfolio_research.py --baseline

# 3) Nested validation (holdout kilitli; registry preflight önce)
python scripts/run_portfolio_research.py --nested

# 4) Holdout (yalnız bir kez; registry fingerprint kilidi)
python scripts/run_portfolio_research.py --nested --unlock-holdout
```

Raporlar `reports/portfolio_research/` altındadır (baseline, cost_comparison,
nested/<experiment_id>/trial_results.json + manifest.json + gate_table.md).

## Veri katmanı

- `data/research_store.py` — versiyonlu Parquet/CSV depo. Kaydetme politikası:
  exact duplicate barlar tek bara düşer (`input_duplicate_bars` audit sayacı),
  conflicting duplicate koşuyu fail eder ve veri seti yazılmaz. `data_hash`
  canonical sembol + timeframe üzerinden deterministik hesaplanır.
- `data/providers.py` — `ResearchDataProvider` protokolü:
  `AlpacaResearchDataProvider`, `LocalResearchDataProvider`,
  `ExternalAdjustedDailyProvider` (Stooq). Harici provider'ın adjustment
  doğruluğu asla varsayılmaz; `reconcile_providers` ile Alpaca `all`
  overlap'i üzerinde doğrulanmadan veri seti kabul edilmez.
- `data/dataset_quality.py` — sembol başına kalite kapısı: kapsam, takvim
  bilinçli gap analizi (`pandas-market-calendars` NYSE — orakle testlerle
  2016-2026 gerçek bar verisine karşı doğrulanmış), geç listelenme, OHLC
  geçerliliği, timezone, adjustment politikası. Equity production araştırması
  dividend+split ayarlı (total-return) veri ister. Kapı her
  `--baseline/--rerun/--nested` koşusundan ÖNCE fail-closed çalışır ve
  `reports/portfolio_research/quality/<dataset>.json` artefaktını yazar.

## Nested validation metrikleri (continuous OOS)

- Aday, validation penceresinin TAMAMINDA tek bir continuous koşuyla
  değerlendirilir: warmup validation öncesi tüm barlar (embargo purge yok —
  stratejiler model fit etmez, gösterge ısıtması son barları da kullanmalı),
  ay sonu zorunlu liquidation yok (gerçek devamlı portföy semantiği).
- Bağımsız aylık fold metrikleri (`positive_fold_ratio`, PBO blokları,
  median_fold_*) continuous koşunun aylık bölümlenmesinden türetilir;
  ayrı fold koşularıyla sat-al-yeniden-al artefaktı üretilmez.
- `unique_oos_calendar_months`: continuous OOS gözlemlerinden unique (year, month).
- Aggregate OOS metrikleri (sharpe/sortino/calmar/max_drawdown/return)
  doğrudan continuous eğriden hesaplanır (stitching gereksizdir).
- DSR: gerçek kronolojik OOS return serisi üzerinden (skew/kurtosis seriden,
  observation sayısı gerçek gözlem sayısı, trials = dataset hash'e bağlı
  GLOBAL trial ledger sayısı — yeni experiment id bütçeyi sıfırlayamaz).
- CSCV/PBO: kombinasyon uzayı yönetilebilirse exhaustive, değilse seed'li
  dengeli uniform örnekleme; aynı seed aynı sonucu üretir.
- Parametre platosu: kendisi komşu sayılmaz; en az beş gerçek komşu
  (Hamming ≤ 1) pozitif expectancy ve PF > 1,05 sağlamalıdır.
- Zorunlu rapor alanları: benchmark return, excess return, information ratio
  ve cash yield (ortalama cash oranı × `cash_rate_annualized`, T-bill proxy).

## Production gate

`PortfolioGate.for_market(...)` sabit eşiklerle çalışır (sonuçlara göre
ayarlanmaz). Equity: unique OOS ay ≥ 60, PF ≥ 1,15, aggregate OOS Sharpe
≥ 0,75, aggregate MaxDD ≤ %10, DSR ≥ %95, PBO ≤ %20, pozitif fold oranı
≥ %60, pozitif ay oranı ≥ %60, stress PF ≥ 1,00, stress expectancy ≥ 0,
plato ve rejim çeşitliliği gerekli, tek varlık ≤ %50, tek sembol brüt kâr
≤ %30, holdout tam bir kez. Crypto ayrıca pozitif fold oranı ve rejim
çeşitliliği ister. Holdout sonucu validation metriklerine asla karışmaz
(`holdout_result`/`holdout_gate_decision` ayrıdır). Validation-only gate
holdout gereksinimlerini içermez (`include_holdout_requirements=False`).

## Holdout hijyeni

- Baseline yalnız `outer_holdout_start` öncesi barlarla çalışır; `locked_holdout_start`
  manifeste yazılır.
- `data/runtime/portfolio_holdout_registry.json`: structural fingerprint
  (market, timeframe, universe snapshot hash, holdout penceresi, adjustment,
  provider) ile kilitlenir; kontrol holdout barlarına erişimden ÖNCE yapılır.
  Aynı holdout yeni experiment_id ile tekrar açılamaz. State'ler:
  pending / complete / failed / audited. Çöken koşuların `pending` kayıtları
  `failed`'a işaretlenir ama ASLA otomatik yeniden açılmaz: crash öncesi
  holdout hesaplanmış olabilir. Tek yeniden açma yolu, `recover_holdout(...)`
  ile kayıt altına alınmış manuel operatör auditidir. Tüm yazımlar OS dosya
  kilidi (flock) altındadır.
- Holdout yalnız `frozen_candidate_trial_id` (YAML'da preregistered,
  deterministik `trial_id_for` hash'i) için ve validation gate'i TAMAMEN
  geçtiğinde açılır; sıralamadaki en iyi aday asla örtük seçilmez.
  `max_holdout_finalists: 1` zorlanır; finalist trial_id + parametreleri
  manifeste kaydedilir.
- Araştırma manifesti kaynak durumunu birebir tanımlar: kirli working tree
  ile koşu reddedilir (veya `--allow-dirty` ile dirty flag + tracked/untracked
  hash'ler manifeste yazılır).

## Universe snapshot'ları

Kripto PIT evreni deterministik `hash_point_in_time_universe` hash'i ile
`data/research/<dataset>/universe_snapshots.json` altında kalıcılaştırılır;
experiment manifestine `universe_snapshot_hash` yazılır ve koşu sırasında
sessizce yeniden hesaplanan farklı evren reddedilir.

## Correctness rerun

```bash
python scripts/run_portfolio_research.py --rerun
```

Aynı mevcut `equity_etf_1d_v1` verisi üzerinde yalnız kod doğruluğu
değişikliklerinin etkisini ölçer; `equity_dual_momentum_v1_correctness_rerun`
üretir, holdout kilitli kalır, baseline holdout öncesi kesilir. Sonuç
`docs/research_correctness_audit_2026-08-05.md` içinde yorumlanır.
