# PAPER Runtime Lifecycle — ParayaBuck

**Mode:** `paper` (`runtime.mode paper`), **Strategy:**
`equity_residual_momentum_v3_dividend_sleeve_v1` (**Residual Momentum v3 +
dividend-growth sleeve**; unchanged V3 3.0.0 weekly core plus a static
SCHD/VIG/DGRO sleeve), **Live:** `false`. See the
[production evidence](research/dividend_sleeve_production_v1.md).

This document is the single source for the actual PAPER startup/runtime lifecycle as implemented in `src/parayabuck/runtime.py` (EngineRuntime). Every stage lists inputs, persisted state, external dependency, failure/retry, fail-open/closed, and telemetry with file:line refs. No alpha/economics change.

---

## 1. Process Start — `EngineRuntime.start()` `runtime.py:829`

| Field | Detail |
|-------|--------|
| **Inputs** | `config/settings.yaml` path, `ALPACA_API_KEY/SECRET`, `PARAYABUCK_ENABLE_LIVE`, `app.host/port` loopback guard |
| **Persisted** | None yet (first write is `runtime_sessions` at stage-end) |
| **Dep** | None (pre-network) |
| **Failure** | `RuntimeMode.DISABLED` → `RuntimeError` halt; `already running`/`stop_failed` → `RuntimeError`; `live` without `live.enabled`/`hard_dollar_cap`/`YES` → `RuntimeError` |
| **Retry** | None — caller discards on `TransientStartupError` |
| **Fail** | **closed** |
| **Telemetry** | `log.info` startup, `health.state=starting`, `alerts.emit(circuit_breaker)` on drift |

## 2. Config Load — `config.py:648`

Inputs: YAML `runtime, engine, alpaca, data, universe, strategy, risk, execution, backup, alerting, live` + env. Current equity config is `maximum_allocation: 0.55`, `core_allocation: 0.40`, and `sleeve_allocation: 0.15` (SCHD/VIG/DGRO at 0.05 each), with `universe.equity.sleeve_trade_symbols` and `risk.slot_exempt_equity_symbols`. Persisted: `Config` in-memory, digests later in `portfolio_runtime_sessions`. Failure: `FileNotFoundError`, `ValueError` on ratios, unknown alert events — fail-fast. Telemetry: `warnings.warn` deprecated keys.

## 3. DB / Storage Startup — `storage.py:17`, `execution/ledger.py:59`, `portfolio/runtime_service.py:174`

`SQLiteStore` `PRAGMA journal_mode=WAL, foreign_keys=ON, busy_timeout=5000` + `ExecutionLedger` & `PortfolioStateRepository` share `store.connection/store.lock`. Migrations `risk_state` (session), `safety_incidents` (persistent), `runtime_sessions`, `portfolio_*`. Failure: `sqlite3.Error` → abort. Retry: `busy_timeout 5000`. Fail-closed. Telemetry: `log.warning` on close, `metrics._connect_readonly mode=ro`.

## 4. Broker Account & Position Fetch — `execution/alpaca.py:200`, `runtime.py:1735`

Single `broker.account()` returns both account + positions. Cached `_last_account_snapshot/_last_account_at` (`runtime.py:446`). Failure: 429/5xx transient → `TransientStartupError`; 401/403 → permanent `ValueError`; `OwnershipDriftError` → halted. Retry: start path `_is_transient_network_error` → `TransientStartupError`; steady-state `_account_loop` 15s poll. Fail-closed on drift, defer on transient. Telemetry: `circuit_breaker ownership_drift`, `health.account_fresh` gate `maximum_account_age_for_entry_seconds=30`.

## 5. Persisted State Load & Circuit-Breaker Restore — `runtime.py:1912`

`risk_state` SESSION_SCOPED (today only), `safety_incidents` PERSISTENT_INCIDENT (ownership_drift, protection_failure, ambiguous_order_state, portfolio_drawdown_halt). Failure: missing → clean baseline; corrupt JSON → warning/skipped. Fail-closed restore — halt resurrects as `circuit_breaker_reason`. Telemetry: `alerts.emit(circuit_breaker Persisted)` .

## 6. Reconcile (Startup & Periodic) — `runtime.py:3152`, `execution/alpaca.py:450`

`broker.reconcile()` = owned quantities, open orders, ledger entries, crypto stops. On success `clear_safety_incident`; on `ProtectionReconciliationError`/`AmbiguousOrderState` → halt, `reconciliation_failed` deferred. Retry: startup transient → `TransientStartupError`; periodic `_schedule_reconcile` singleton `pb-reconcile` every 60s, `_generic_reconcile_failures≥3` → `reconciliation_bug_suspected` halt. Fail-closed on protection/drift. Telemetry: `reconciliation_failure/recovered`, `circuit_breaker`.

## 7. Historical Warmup — `runtime.py:3435`, `data/alpaca.py:94`, `data/cache.py:15`

`symbols` + `required_timeframes` + `minimum_history_bars` 252 + `stock_feed` sip/iex. Cache digest `SHA256(timeframes+strategies)` gates reuse. Failure: coverage <60% → refetch; SIP 403 → degrade to iex; timeout → `TransientStartupError`. Retry: `_WARMUP_RETRY_SECONDS=600` singleton `pb-warmup-retry`. Fail-closed via `not_ready_symbols`. Telemetry: `Warmup starting/complete`, `Historical gap remains`.

## 8. Universe Refresh — `universe/preflight.py:81`, `runtime.py:1077`

`ensure_active_universes()` pre-subscription, `refresh_equity/crypto_universe` bars+quotes, `active_*_universe.json` + digest. The 30-symbol equity IEX stream pins `universe.equity.sleeve_trade_symbols`, hides them from V3 scoring, and leaves 23 ranked core slots instead of 26. Failure: `invalid_dynamic_universe` → entry halt unless `allow_static_fallback_trading`; hot-swap protects managed pins, rollback on `rollback_failed` → `subscription_state_unknown` halt. Retry: startup one-retry 1s; loop backoff `(300,900,1800,3600)` capped 60min, equity defers to next premarket 06:00 ET. Telemetry: `scanner_refresh_failure/recovered`, `health.universe_refresh`.

## 9. Signal-Basis Verification — `runtime.py:2909`, `data/adjustment.py:55`

Equity splits `1d` provider-authoritative. Watermark `verified_through_by_symbol` dict, `healthy`, `last_success_at` (3600s). Failure: incomplete coverage or `provider fetch failed` → `signal_adjustment_unverified` (equity entries halted, sells flow). Retry: hourly `pb-signal-adjustment` + per-decision `_equity_signal_basis_latch`; no intra-call retry, deduped alerts. Fail-closed for entries. Telemetry: `signal_adjustment_failure/recovery`, `health.signal_adjustment`.

## 10. Strategy Decision — `runtime.py:2294`, `portfolio/runtime_service.py:96`

Finalized `Bar` batch after `event_grace_seconds=5.0` coalescing. `portfolio_decisions` row `decision_id=SHA256(account_id+digest+timestamp+market)` dedup `UNIQUE(account_id,configuration_digest,decision_timestamp,market)`. The equity target combines the unchanged V3 core with the static sleeve; the sleeve is selected only on the first weekly decision of each calendar month or while empty and is otherwise held. Failure: missing timeframes → `ValueError`; exception → `portfolio_runtime_failure` halt. No decision retry, startup catch-up once. Fail-closed on exception. Telemetry: `Portfolio decision: ...`, `paper_observability`.

## 11. Risk Overlay — `portfolio/runtime_service.py:PortfolioRiskOverlay`, `runtime.py:1812`

`risk.*` limits, `live_dollar_cap`, `execution band`, `high_water`. `risk_state` session + `safety_incidents` for drawdown. Failure: `daily_loss`/`drawdown` → latch `entry_risk_latch[market]=reason` (sells only), drawdown persists. Daily-loss, drawdown, entry-latch, and intermediate-reduction latches never grow the static sleeve. Re-evaluated every 15s, `start_new_risk_epoch()` rebases. Fail-closed for entries. Telemetry: `risk_incident/drawdown_halt/risk_epoch_reset`.

## 12. Order Submission (Two-Phase) — `portfolio/runtime_service.py:1161`, `execution/alpaca.py:500`, `runtime.py:938`

`TargetPortfolio` → `AssetTradingRules` → `PortfolioExecutionPolicy` (band 0.5%, min $10). `portfolio_orders` (`client_order_id=parayabuck-{decision_id}-{symbol}-{side}`) + `portfolio_rebalance_executions` (`sell_pending→buy_ready→buy_pending→complete`). SELLs first, BUY deferred until SELLs terminal. Failure: `AMBIGUOUS_ORDER_STATUSES` → `REBALANCE_PHASE_BLOCKED` → `rebalance_reconciliation_required` halt; crash between persist and ack → `submitting` resolved via `resolve_portfolio_submission` at restart. Restart-safe deterministic `client_order_id`. Telemetry: `order_rejected/ambiguous_order`, `health.strategy_batch_status`.

## 13. Fill / Update Reconciliation — `execution/alpaca_updates.py:33`, `runtime.py:1960`, `execution/alpaca.py:461`

`TradingStream TradeUpdate` primary + REST `GetOrderById` fallback. `portfolio_orders`/`portfolio_fills` (`activity_id PK`) + `managed_positions` + `reconcile_watermarks`. Failure: unmanaged update → warning + reconcile; stale `submitting` no broker → `canceled`; missing `avg_price` → stays `submitted`. Stream `pb-trade-updates` with restart backoff `2^(n-1)` ≤16s. Idempotent, loss-less. Telemetry: `order_rejected/ambiguous_order`, `_persist_risk_state` after fill.

## 14. Monitoring, Health & Shutdown/Restart — `runtime.py:583`, `monitoring/alerts.py:1`, `monitoring/metrics.py:1`, `backup.py:1`, `runtime.py:1641`

`WorkerHealth` heartbeats, `EventQueue` 1024 age, `last_account_at`. `runtime_sessions` + `metrics` counters. Health unhealthy when `!running` or worker dead >45s or queue>30s or account stale>30s or breaker. Stream `restart>5` → market halt. Shutdown `stop()` joins 5/30/30/10s, closes sessions, emits `runtime_stopped_unexpected` if halted. Restart requires per-halt `reset_safety_lock` verification. Fail-closed on halt/overflow. Telemetry: 14 `alerts.emit` kinds throttled 60s, 15 `metrics.record_event`, `health` JSON.

---

**Persisted-state taxonomy:** SESSION_SCOPED `risk_state` today-only; PERSISTENT_INCIDENT `safety_incidents` survives restart; TRANSIENT_READINESS `entry_risk_latch/market_entry_halts/not_ready`.

**Bar pipeline:** `AlpacaBarStream.on_bar` → `aggregators.update/flush_until` → `_emit_finalized_bar` → `_events.put_market_event` (1024, coalesce) → `_decision_loop` batch after grace 5s → `_process_timestamp` + `_process_portfolio_timestamp`.
