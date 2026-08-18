# Trading Dashboard

The Web control center (`/`, served by `src/parayabuck/web/server.py`) is an
operator dashboard that answers, in about ten seconds: is the bot running, is
it healthy, what does it hold, why, which strategy, when does it decide again,
are there open orders/fills, and is risk blocking anything.

All dashboard data comes from the **same read-only state** the existing
`/api/status` and `/api/account` endpoints expose: the runtime SQLite ledger
(`runtime.decision_db_path`, read via `mode=ro` connections), the universe
snapshot JSON files, `EngineRuntime.health()`, the broker account snapshot,
and the batch observability modules. No new ledgers, no secrets, no
mutations.

## API

| Endpoint | Contents |
| --- | --- |
| `GET /api/dashboard/overview` | Everything below in one payload (`as_of` stamped) |
| `GET /api/equity` | Equity strategy card |
| `GET /api/crypto` | Crypto strategy card |
| `GET /api/risk` | Risk panel |
| `GET /api/execution` | Execution panel |
| `GET /api/universe` | Universe panel (both markets) |
| `GET /api/health` | System health panel |
| `GET /api/explanations` | Per-holding structured reasons |
| `GET /api/metrics` | Telemetry + runtime metrics |

Every panel degrades to `{"unavailable": true, ...}` instead of a 500 when
its source is missing (engine stopped, ledger absent, or a batch module that
has not landed yet). The batch modules are imported lazily inside the
handlers: `parayabuck.execution.telemetry`
(`execution_metrics` / `forward_metrics` / `cost_calibration`,
plus `expected_vs_actual` per fill) and `parayabuck.monitoring.metrics`
(`runtime_metrics`). Mid-batch import failures surface as an explicit
`unavailable` field, never an exception.

## Panels and where each number comes from

### Account top line (metric grid)
`Equity`, `Cash`, `Daily PnL`, `Realized/Unrealized PnL`, `Fees`,
`Drawdown` — from `manager.fetch_account_snapshot()` (broker account, 5 s
cache). `Gross Exposure` — sum of open position market values. `Total PnL` —
realized + unrealized. `Engine Status` — `manager.current_engine_state()`.
`Last Reconciliation` — `RuntimeHealth.last_reconciliation_at`.

### PAPER / LIVE badge
Rendered from `config.live.enabled` + `runtime.mode` (never from broker
connectivity): a paper config always shows a green **PAPER** badge; a live
config whose engine is actually trading shows a red **LIVE** badge; a
live-configured deployment whose engine is not trading (stopped/error)
shows **LIVE (devre dışı)** — visually dimmed and struck through. The same
state is exposed as `mode.badge` / `mode.live_active` / `mode.live_disabled`
in the overview payload.

### Equity card — "Residual Momentum v2 (weekly)"
Strategy contract (display name, signal timeframe `1d`, rebalance cadence
`weekly`, minimum history) from `StrategySpec` via `_strategy_metadata`.
`Eligible pool` — shortlist count from
`universe.equity.candidate_universe_path`; `observation set` — active-universe
`selected_symbols` size; `positions` / `current allocation %` — account
positions weighted by market value over equity; `maximum allocation %` —
`strategy.equity.maximum_allocation`. Status derivation:
active / warming / halted (entry halt or circuit breaker) / scanner_issue /
disabled / engine states. `Next expected decision` — the last persisted
`portfolio_decisions` timestamp advanced by the strategy's rebalance policy
(`weekly` → next ISO-week start, `hours:N` → +N hours); it is an expected
boundary, not a guarantee. Holdings carry target weight (from the decision's
`target_json`), current weight (account), rank among the decision's
`selected_symbols`, and the per-weight `reason` + strategy `hypothesis`.

### Crypto card — "Low Realized Variance v2 (96h)"
Same shape plus: `lookback 22g` (the frozen `variance_lookback_days`),
`22g vol` per holding — the trailing 22-day realized volatility computed
with the strategy's own `realized_volatility` over cached 1d bars
(`data/cache/warmup.sqlite3`, minute-bucketed TTL, `None` when unavailable),
`rank` — the symbol's position in the decision's `selected_symbols`
(lowest-vol first), `target weight` — equal-weight from the decision,
`current weight` — account market value over equity. Rebalance cadence
`hours:96`. Each card also shows the startup catch-up status
(`submitted` / `warming_up` / `blocked:*` / `already_invested` /
`catch_up_superseded` / `duplicate_decision` / `rebalance_in_progress`).

### Risk panel
`Gross exposure` vs `max_gross_exposure_pct`, `cash` vs
`cash_buffer_pct`, `equity exposure`, `crypto exposure` vs
`max_crypto_exposure_pct`, `max drawdown` vs `max_drawdown_pct`, `daily PnL`
vs `daily_loss_limit_pct` — all from the account snapshot and
`config.risk`. `Entry risk latch` — `RuntimeHealth.entry_risk_latch`.
`Circuit breaker` — `RuntimeHealth.circuit_breaker_reason`. `Safety
incidents` — `safety_incidents` rows in the ledger. `Risk epoch` — latest
`risk_state` row (session date, day-start equity, peak equity, consecutive
losses). Nothing critical is hidden: the panel always renders the latch,
breaker and incidents explicitly.

### Execution panel
Open orders and recent fills (25 each) queried read-only from
`portfolio_orders` / `portfolio_fills` joined to
`portfolio_decisions`/`portfolio_runtime_sessions` for decision lineage
(strategy + decision timestamp). Fills show side, qty, fill price,
`reference price`, `slippage bps` (fill vs reference, direction-aware),
`fee`, and Worker B's `expected_vs_actual` comparison (expected vs actual
quantity, fill ratio, average fill price, price delta/adverse bps) when the
telemetry module is available. Reconciliation anomalies come from
`portfolio_ownership_adjustments`.

### Universe panel (both markets)
`Broad discovered` — candidate payload size
(`candidate_universe_path` for equity, `candidate_symbols` for crypto);
`eligible pool` — equity shortlist / crypto candidates; `observation set` —
active-universe `selected_symbols`; `last refresh` /
`data_timestamp` / `scanner status` / `scanner version` from the active
universe JSON; `next refresh` / `refresh errors` from
`RuntimeHealth.universe_refresh`; `pinned symbols` — runtime managed pins
(equity) or `universe.crypto.pinned_symbols`. Symbol lists are rendered in a
collapsible `<details>` and capped client-side (120 chips) so hundreds of
symbols are never dumped by default.

### System health panel
`Workers` (alive, last heartbeat, restart count, last error) and `queue`
(size, age, dropped/coalesced) — `RuntimeHealth`. `Data freshness` —
per-market `stream_healthy` / `data_gap`. `Account freshness` — snapshot
age/staleness vs `engine.maximum_account_age_for_entry_seconds`.
`Reconciliation` — last reconciliation time + breaker state.
`Scanner` — universe-refresh status/errors/next refresh. `Database` — ledger
path, existence, size, WAL file presence. `Last heartbeat` — newest worker
heartbeat. `Restart count` — Worker D's `runtime_metrics().restart_count`
(runtime_sessions-derived), with a same-formula fallback while that module
has not landed.

### Explanations
Per-holding structured reasons derived from the persisted decision
(`target_json.metadata`: `selected_symbols`, `qualified_symbols`,
`hypothesis`, `risk_off_detail`) matched against current account positions —
rank, reason, hypothesis, universe version, rebalance reason, risk
multiplier. Deliberately concise; no debug dumps.

## Frontend
`templates/index.html` (Jinja2) + `static/js/app.js` + `static/css/style.css`.
The dashboard polls `/api/dashboard/overview` every 5 s; the existing
2-second status/account websocket polling is unchanged.

The main dashboard is ordered for day-to-day portfolio monitoring: primary
money metrics, secondary account metrics, the full-width performance chart,
open positions, strategy cards, risk/health, execution and universe. Research
and diagnostic workflows live on `/tools`: the Web backtest runner, raw
operations snapshot and live motor console. Both pages preserve the existing
engine controls and no-store control-token behavior; `/tools` links back to the
dashboard settings drawer for configuration changes.
