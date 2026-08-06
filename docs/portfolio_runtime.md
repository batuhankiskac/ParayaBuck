# PortfolioRuntime — durum

Tarih: 2026-08-05
Statü: **TASARIM HAZIR — UYGULANMADI (research RED)**

Faz 3 (PortfolioRuntime) yalnız düzeltilmiş araştırma sonucu yeterliyse
uygulanır (§11). `equity_dual_momentum_v2` sonucu RED olduğundan runtime
uygulanmamıştır; bu doküman tasarım sözleşmesini ve bloklayıcıyı kaydeder.

## Sorumluluklar (tasarım sözleşmesi)

- Canlı kapanmış barlardan `PortfolioContext` kurmak (geçmişe bakış
  kapanmış barlarla sınırlı).
- `RebalancePolicy`'ye göre `decide` çağırmak; karar aynı kapanışta değil
  sonraki tradable session'da fill olur.
- `TargetPortfolio`'yu mevcut broker pozisyonlarıyla (tek gerçek kaynak)
  karşılaştırmak; hedef ağırlık → nominal → delta → `OrderIntent`.
- Satış/risk azaltan emirleri alışlardan önce işlemek.
- `ExecutionBroker`, `ExecutionCoordinator`, `OrderStateMachine`,
  `RiskManager`, ledger'ı yeniden kullanmak.
- Deterministik client order id (`IdempotencyKeyFactory`):
  `{strategy}-{version}-{decision_ts}-{symbol}-{side}-{rebalance_n}`.
- Duplicate rebalance engelleme; partial fill + açık order mutabakatı.
- State'i SQLite/ledger içinde atomik saklamak (`PortfolioState` v2:
  cash, holdings, avg_prices, entry_fees, realized_pnl, last_prices,
  last_target, last_rebalance, pending_target, pending_orders, watermark,
  universe_snapshot_id, schema_version).
- Restart sonrası target/order/position parity; kesintisiz ve restartlı
  koşular aynı qty/cash/equity/order id/fill üretmeli. `liquidate_at_end`
  sonrası state'ten holdings/pending temizlenir ve cash güncellenir —
  restart phantom pozisyon diriltemez. Kısmi exit sonrası restart için
  pozisyon başına realized accumulator da state'e persist edilir.
- `EngineRuntime.start()` fail-closed: `strategy_status` üretim gate'ini
  geçip açıkça allowlist'e alınmış bir stratejiyi onaylamadan
  (`approved_for_live`) runtime BAŞLAMAZ; status alanı tek başına koruma
  değildir.
- Market holiday ve first-trading-day takvimi.
## Ortak saf bileşenler (backtest + runtime)

- `PortfolioDecisionService` — planlanmadı (strateji reddi).
- `TargetToOrderPlanner` — planlanmadı.
- `WeightCalculator` — planlanmadı.
- `IdempotencyKeyFactory` — **uygulandı** (`src/parayabuck/portfolio/idempotency.py`,
  backtest'te aktif).
- `RebalanceScheduler` — planlanmadı.

## Backtest tarafındaki durum

`PortfolioBacktestEngine` şu anda: entry fee dahil realized PnL, state v2 +
restart parity, deterministik order id, rebalance raporu
(pre_trade/signed/post_fill/drift/unfilled/expected+realized turnover),
karar→fill ayrımı. Runtime adaptörü için gereken ortak saf bileşenler
henüz yok.

## Bloklayıcı

`docs/equity_dual_momentum_v2_results.md` — validation RED. Strateji
arşivlendi; pristine holdout ve yeterli araştırma kanıtı olmadan runtime'a
geçilmez.
