# Correctness rerun v2 (düzeltilmiş altyapı) — equity_dual_momentum_v2_correctness_rerun_2

- Dataset: `equity_etf_1d_v2` (kalite kapısı her koşu öncesi fail-closed)
- Metodoloji: continuous OOS (embargo yok, ay sonu liquidation yok), aylık fold'lar continuous koşudan bölümlenir
- DSR trials sayısı: global trial ledger (dataset hash'e bağlı)
- Holdout: kilitli (açılmadı)
- Source: `alpaca` / `all`

## Frozen aday

- trial_id: `6d869b6909a7cd7d`
- parametreler: `{'absolute_filter': 'above_200dma', 'momentum_scheme': '6_1', 'volatility_target': None, 'volatility_window': 20}`
- unique OOS ay: 107
- aggregate Sharpe: 0.687
- aggregate MaxDD: 24.08%
- DSR: 52.6%
- PBO: 92.2%
- pozitif fold oranı: 62.6%
- benchmark return: 231.83%
- excess return: -133.51%
- information ratio: -0.59
- cash yield: 0.00%
- validation gate: FAILED (sharpe_below_gate, drawdown_above_gate, deflated_sharpe_below_gate, pbo_above_gate, parameter_neighborhood_not_robust)

## Karar

Frozen aday validation gate'ini GEÇMEDİ → dual momentum ailesi **kalıcı arşivlenir**. Aynı veri üzerinde yeni 6_1 varyantı taranmaz; bir sonraki araştırma programı farklı bir mekanizma (günlük time-series trend / defensif tahsis) ile preregister edilmelidir.
