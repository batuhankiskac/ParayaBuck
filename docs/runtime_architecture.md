# Runtime architecture

> EngineRuntime and PortfolioRuntime are thin orchestration facades; persistence, decision and execution lifecycle are isolated. One equity `PortfolioRuntime` runs today.

```
EngineRuntime
  ├─ event / stream layer          runtime_events.py      (RuntimeEvent, EventQueue)
  ├─ health / snapshot assembly    runtime_health.py      (WorkerHealth, RuntimeHealth, _portfolio_catch_up_health)
  ├─ product universe              runtime_universe.py    (ActiveProductUniverse: static equity symbols + managed pins)
  ├─ intent watchdog               monitoring/intent_watchdog.py (decision/fill expectations vs reality)
  ├─ portfolio runtime (equity)
  │     ├─ decision                portfolio/runtime_models.py       (PortfolioDecisionService, PortfolioContext)
  │     ├─ risk / planning         execution/portfolio.py            (PortfolioRiskOverlay, TargetToOrderPlanner)
  │     ├─ rebalance execution     portfolio/runtime_rebalance.py    (sell_pending → buy_ready → buy_pending → complete/blocked)
  │     ├─ ownership               portfolio/runtime_ownership.py    (net_owned_quantities pure helper)
  │     └─ repository              portfolio/runtime_repository.py   (PortfolioStateRepository, SQLite, WAL)
  ├─ warmup / cache                data/cache.py                     (WarmupCache, crash-safe migration)
  └─ health + dashboard            web/server.py reads RuntimeHealth snapshots
```

## Module Responsibilities

| Module | Owns | Does NOT own |
|--------|------|--------------|
| `portfolio/runtime_models.py` | Immutable models, terminal order vocabulary (`_TERMINAL_ORDER_STATUSES`, `TERMINAL_ORDER_STATUSES_SQL`), rebalance phases, `PortfolioDecisionService`, `configuration_digest`, `_decision_id` | DB, broker, risk |
| `portfolio/runtime_repository.py` | Persistence: migrations, sessions, decisions, orders, fills, ownership adjustments, rebalance execution, high-water, query helpers | Strategy, risk, broker |
| `portfolio/runtime_rebalance.py` | Two-phase phase predicates: `is_sell_phase_complete`, `requires_ambiguous_block` (phase constants live in `runtime_models.py`) | DB SQL, broker |
| `portfolio/runtime_ownership.py` | Pure net-ownership math (`net_owned_quantities`) | DB |
| `portfolio/runtime_service.py` | Orchestration facade: `PortfolioRuntime` — builds context, applies risk, plans orders, drives two-phase submit, advances rebalance. Thin, delegates to models/repository/rebalance/ownership | Direct SQL, migration SQL |
| `runtime_events.py` | `RuntimeEvent` / `EventQueue` with coalescing, replacement, overflow, ordering, thread Condition | Business logic |
| `runtime_health.py` | `WorkerHealth`, `RuntimeHealth`, `_portfolio_catch_up_health` assembly | Threading, queue mechanics |
| `runtime_universe.py` | Immutable `ActiveProductUniverse` membership snapshot (configured symbols + observation pins) | Strategy, broker |
| `monitoring/intent_watchdog.py` | Pure evaluation of `equity_decision_missing` / `equity_position_under_target` on the NYSE calendar | Trading decisions (observability only) |
| `runtime.py` | `EngineRuntime` facade: startup, strategy init, worker lifecycle, account polling, reconciliation scheduling, warmup retry, signal-adjustment verification, intent-watchdog scheduling, daily summary alert, entry-risk latch, circuit breaking, shutdown | Direct queue/health/universe internals (delegates) |

## Dependency Direction

```
models
  ↓
repository

models
  ↓
decision / rebalance / ownership

repository + decision + rebalance (+ ownership)
  ↓
PortfolioRuntime facade

runtime_events / runtime_health / runtime_universe
  ↓
EngineRuntime facade
```

No circular imports. `repository` does not import `PortfolioRuntime`. `rebalance` does not import `EngineRuntime`. `runtime_service` circular hacks via `try/except` imports are removed (product guard now module-level import, fail-closed).

## Restart Safety

- **Decision idempotency**: `(account_id, configuration_digest, timestamp, market)` → `decision_id` via `hash(... )[:24]`. `INSERT OR IGNORE` guarantees duplicate bar batch does not double-persist.
- **Atomic decision + orders + rebalance marker**: `record_decision_with_orders(..., rebalance_phase=..., buy_orders_json=...)` writes all rows in one `with connection:` transaction (WAL `busy_timeout=5000` + `store.lock`). Crash cannot leave half-decision.
- **Two-phase sells → buys**:
  1. Calculate rebalance, plan sells+buys
  2. Persist decision + sells + `sell_pending` + `buy_orders_json`
  3. Submit sells (`_submit_orders` idempotent via broker dedup)
  4. `advance_rebalance` polls until sells terminal (resolves `submitting` via `resolve_portfolio_submission` if broker reachable)
  5. `BLOCKED` on ambiguous (`reconciliation_required`) — buys never submitted
  6. On terminal sells, fresh `broker.account()` → re-apply risk → re-plan buys against safe capacity → submit deferred buys → `buy_pending` → `complete` when buys terminal
- **Restart between any step** resumption:
  - decision persisted + sells submitted → `current_rebalance_execution` returns `sell_pending`; `advance_rebalance` re-checks sells
  - partial sell → still `sell_pending` until all terminal
  - sell completed → transitions to `buy_ready` → `_submit_deferred_buys` re-plans from fresh account
  - `buy_ready` persisted → next start calls `advance_rebalance` and submits buys exactly once
  - `buy_pending` → checks buys terminal → `complete`
  - Stuck `submitting` rows: `_resolve_stuck_submission_rows` uses broker `resolve_portfolio_submission` to prove whether order reached Alpaca; unknown id is treated as never-filled (safe to cancel), unreachable broker leaves for retry — no guessing. The same lookup runs in `buy_ready` before re-planning, and before each new decision for rows of earlier decisions still at `submitting` (e.g. a crash before the broker call). When a submit raises, the batch's later, never-sent rows are canceled at once. Reconciliation cancels a `submitting`/`reconciliation_required` row the broker does not know (404) once its last write is 10 min old.
- **High-water / epoch**: persisted in `portfolio_high_water` with `epoch` column; restart restores max peak only from rows of current epoch.

## PAPER / LIVE Shared Path

- `PortfolioRuntime` is mode-agnostic; `mode` (`paper` vs `live`) selects `maximum_allocation` vs `min(allocation, live_dollar_cap/equity)` and the `broker.paper` flag. Rebalance, ownership, reconciliation, risk latch and repository are identical.
- `EngineRuntime` constructs `AlpacaBroker(paper=self.paper)` and passes `mode` through. No separate PAPER/LIVE branches.

## Where State Is Persisted

| State | Table / File | Key |
|-------|--------------|-----|
| Sessions | `portfolio_runtime_sessions` | `session_id` PK |
| Decisions | `portfolio_decisions` | `decision_id` PK, `UNIQUE(account_id, configuration_digest, decision_timestamp, market)` |
| Orders | `portfolio_orders` | `client_order_id` PK, FK `decision_id` |
| Fills | `portfolio_fills` | `activity_id` PK, FK `client_order_id`, includes `fill_delay_seconds` |
| Rebalance | `portfolio_rebalance_executions` | `decision_id` PK, `phase` + `status` + `buy_orders_json` |
| Ownership adjustments | `portfolio_ownership_adjustments` | `adjustment_id` PK |
| Ledger identity | `portfolio_ledger_identity` | `singleton` PK (`client_order_salt`, created once) |
| High-water | `portfolio_high_water` | `account_id` PK (`equity`, `epoch`, `updated_at`) |
| Warmup bars | `data/cache/warmup.sqlite3` (`bars`, `scope_meta`) | PK `(feed, symbol, market, timeframe, adjustment, timestamp)` |
| Execution ledger | `ExecutionLedger` (`orders`, `fills`, `managed_positions`, `risk_state`) | SQLite WAL, `busy_timeout=5000` |

All SQLite DBs use WAL + `busy_timeout=5000`; repository uses `store.lock` + `with connection:` for atomic multi-table writes. `WarmupCache._migrate_legacy_schema` handles crash recovery (see below).

## Concurrency

- **Event queue**: `threading.Condition`, bounded `maxsize`, coalescing by `(kind, symbol, timeframe, timestamp)`, overflow policy: `coalesced` → `replaced` (victim non-primary) → `overflow`. Primary never replaced by non-primary.
- **EngineRuntime locks**: ` _reconcile_spawn_lock` and `_warmup_retry_spawn_lock` are class-level to protect check-then-spawn; per-instance `_ingestion_lock` shadows class-level fallback for partially constructed test instances. Worker threads: `pb-decisions`, `pb-account`, `pb-trade-updates`, `pb-stocks` (bar stream), `pb-signal-adjustment`, plus on-demand `pb-reconcile` and `pb-warmup-retry`; stop via `Event` + `join`.
- **Portfolio repository**: `threading.RLock` per `store.lock`, one connection per store, WAL.

No `asyncio.Queue` replacement; no change to lock scope/thread ownership/daemon/stop signaling.

## Startup Lifecycle (private phases)

```
validate config + execution mode (live only when every live gate agrees) → resolve static universe + managed pins (crypto found → risk_incident alert) → create strategies (product_strategy_from)
→ initialize broker/store/repository/ledger → reconcile (broker vs ledger) → warm history (WarmupCache)
→ start streams/workers (AlpacaBarStream, AlpacaTradingUpdateStream, TimeframeAggregator)
→ signal-adjustment verification (hourly batched provider splits tail)
→ startup catch-up decision (once, only if no prior decision and not already invested)
→ become healthy (RuntimeHealth.state = "running")
```

- Warmup: completed bars until every decision symbol has `minimum_history_bars` (200) and timestamps align; unfinished equity daily bars are pruned. A gap is retried by `pb-warmup-retry` from 600 s, doubling to a ~10.7 h ceiling.
- Web auto-start retries a transient startup failure after 5/15/30/60 s (capped at 60 s).
- Persisted halts (`safety_incidents`: ownership drift, protection failure, ambiguous order, drawdown halt) are restored as the circuit-breaker reason and survive restarts until a verified reconcile or operator reset.
- Shutdown joins workers, closes the runtime session row and emits `runtime_stopped_unexpected` if halted.

Transient network errors (`RequestException`, `ConnectionError`, `TimeoutError`, `__cause__/__context__` wrapped) → `TransientStartupError` (retryable). Domain safety (`OwnershipDriftError`, `ProtectionReconciliationError`, `AmbiguousOrderState`, reconciliation `reconciliation_required`) → fail-closed, permanent.

## Entry-Risk Latch vs Stale Account

- **Entry-risk latch** (`_entry_risk_latch` per market): decisions continue but `risk_overlay` target is capped to current weights (`min(weight, current_weight)`) → only SELLs emitted. Triggered by daily-loss / drawdown breach (risk state).
- **Stale account**: `account` snapshot `is_stale` (position/cash/exposure untrusted) → `PortfolioRuntime.process_closed_bars` returns `status="stale_account"` and emits no orders (buys AND target sells blocked).

Distinct, not collapsed.

## Signal Adjustment Verification

- Hourly batched verification (`_SIGNAL_ADJUSTMENT_CHECK_SECONDS=3600`, a verification stays fresh for that interval + `_SIGNAL_ADJUSTMENT_STALE_SLACK_SECONDS=300`, lookback 14 days + 10 day margin) compares raw vs split-adjusted daily tails per symbol, verifies `adequate_coverage`, replaces `history` wholesale when provider rebased. Health field `signal_adjustment` reports coverage.

## Backtest artifacts

Backtest outputs are disposable diagnostics, not runtime state. The runtime
uses the configured strategy and historical inputs directly; it never depends
on generated reports or benchmark output.

## Rejected Abstractions

- Generic `TaskManager` collapsing warmup-retry / reconciliation / signal-adjustment → rejected (independent backoff/state, domain-specific names).
- `asyncio.Queue` for `_EventQueue` → rejected (thread semantics must not change this mission).
- Mediator / DI framework / service locator → rejected (plain composition).
- Generalized migration framework → rejected (small coherent `PortfolioStateRepository._migrate` + `WarmupCache._migrate_legacy_schema` sufficient).
