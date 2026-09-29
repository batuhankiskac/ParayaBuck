# Dashboard

The loopback web console (`/`, reach it through an SSH tunnel) answers: is the
bot running and healthy, what does it hold and why, when does it decide next,
are there open orders, and is risk blocking anything. Diagnostic tools
(backtest runner, raw ops state, live log console) are on `/tools`. The UI text
is Turkish.

All panels read existing state only: the runtime SQLite ledger (read-only
connections), `EngineRuntime.health()`, the broker account snapshot (5 s
cache) and the telemetry modules. No secrets, no writes. A panel whose source
is missing (engine stopped, no ledger) returns `{"unavailable": true, ...}`
instead of an error.

## API

| Endpoint | Contents |
|---|---|
| `GET /api/dashboard/overview` | All panels in one payload (`as_of` stamped); polled every 5 s |
| `GET /api/equity` | Strategy card |
| `GET /api/risk` | Risk panel |
| `GET /api/execution` | Open orders and recent fills |
| `GET /api/universe` | Configured symbols and managed pins |
| `GET /api/health` | System health panel |
| `GET /api/explanations` | Per-holding reasons |
| `GET /api/metrics` | Execution telemetry + runtime metrics |
| `GET /api/performance?period=1D\|1W\|1M\|3M\|1A\|ALL` | Broker equity history vs VOO (IEX), cached 60 s; display only |
| `GET /api/ops/status` | Ops health summary; polled every 10 s |
| `GET /api/status` | Engine state and full `RuntimeHealth`; polled every 2 s |

## Panels

| Panel | Source |
|---|---|
| Portfolio value + chart | `/api/performance`: portfolio vs VOO; degrades to `available: false` or `benchmark_error` |
| Mode badge | `runtime.mode` + `live.enabled`, never broker connectivity: green **PAPER**; red **LIVE** only when a live engine is trading; dimmed "LIVE (devre dışı)" otherwise |
| Account | Equity, cash, daily/realized/unrealized P&L, fees, drawdown from the account snapshot; gross exposure = sum of position values; engine state; last reconciliation |
| Strategy card — **Leveraged Index Trend v1** | Status (active / warming / halted / disabled), configured symbols, positions and allocation vs `strategy.equity.maximum_allocation` (0.98), next expected decision (last decision + `hours:24`, an estimate), startup catch-up status (`submitted`, `warming_up`, `blocked:*`, `already_invested`, `catch_up_superseded`, `duplicate_decision`, `rebalance_in_progress`), holdings with target/current weight and reason (`trend_up` when QLD is targeted) |
| Risk | Gross exposure vs `max_gross_exposure_pct`, cash vs `cash_buffer_pct`, per-position cap, max positions, daily P&L vs `daily_loss_limit_pct`, drawdown vs `max_drawdown_pct`, entry-risk latch, circuit breaker, active safety incidents, risk epoch, buy capacity. Nothing critical is hidden |
| Execution | 25 open orders and 25 recent fills with decision lineage, reference price, slippage bps, fee, expected-vs-actual fill comparison; ownership adjustments |
| Universe | `universe.equity.symbols` (static) plus runtime managed pins |
| System health | Workers (alive, heartbeat, restarts, last error), queue size/age/drops, per-market stream health and data gaps, account age vs the 30 s entry limit, reconciliation, DB path/size/WAL, restart count |
| Explanations | Per-holding reason from the persisted decision metadata matched to current positions; a holding outside the latest decision is shown as such |

## Frontend

Templates `templates/base.html`, `index.html`, `tools.html`; dependency-free,
same-origin static assets (the CSP forbids inline scripts and third-party
origins): `static/css/app.css` (light/dark tokens), `static/js/theme.js`,
`api.js` (control token), `websocket.js` (ticketed sockets), `ui.js` (shell,
status polling, engine controls, PAPER/LIVE arming, settings drawer),
`chart.js` (SVG line chart), `dashboard.js`, `tools.js`. Bump `ASSET_VERSION`
in `web/routers/pages.py` when static files change.
