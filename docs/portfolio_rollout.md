# Portfolio Rollout — durum

Tarih: 2026-08-05
Statü: **HAZIR DEĞİL — research RED nedeniyle rollout bağlantısı yok**

`src/parayabuck/execution/rollout.py` yalnızca değerlendirme fonksiyonu ve
state modellerini içerir; var olması rollout altyapısının hazır olduğu
anlamına gelmez. Canlı/paper/shadow brokerlarına bağlanmadı.

## Tasarım sözleşmesi (§12)

- Rollout state kalıcı saklanır; stage transition audit log'u tutulur.
- `evaluate_rollout` PortfolioRuntime giriş kapısına bağlanır.
- `halt_new_entries` yalnız yeni/artan exposure BUY emirlerini engeller;
  risk azaltan SELL ve koruyucu çıkışlar çalışmaya devam eder.
- Slippage: expected order bazında; realized broker fill − reference price;
  notional-weighted aggregate. Shadow'da counterfactual fill (next-tradable
  quote/open) kaydı.
- `expected_slippage=0` fakat fill varsa fail-closed / data-quality halt.
- Operational error shadow ve paper geçişlerini de engeller.
- `risk_per_trade_pct` yerine: exposure multiplier, maximum target
  allocation, canary portfolio scale.
- Canary %10 veya önceden tanımlı küçük exposure multiplier ile başlar;
  normal hedefe yalnız minimum canary işlem + operasyonel hatasızlık sonrası.
- Sıra: Shadow ≥30 gün → Paper ≥90 gün ve ≥50 fill → Canary ≥30
  rebalance-fill → Live.
- Kill switch: realized slippage > expected ×1,5; rolling performans sınırı;
  validated drawdown ×1,5; reconciliation problemi; duplicate order/
  idempotency; stale data; universe snapshot uyuşmazlığı; operational error.

## Mevcut rollout.py durumu

- `RolloutStage`, `RolloutPolicy`, `RolloutState`, `evaluate_rollout` var.
- Eksik: kalıcılık, audit log, PortfolioRuntime bağlantısı, broker
  entegrasyonu, slippage hesabı, canary exposure scale.
- `RolloutPolicy` hâlâ `initial_canary_risk_pct`/`normal_risk_pct` alanları
  taşıyor (risk_per_trade stili) — exposure multiplier / canary scale
  alanlarına geçiş yapılmadı.

## Bloklayıcı

Research RED (`docs/equity_dual_momentum_v2_results.md`): strateji
arşivlendi; rollout altyapısına bağlanacak onaylı strateji yok.
