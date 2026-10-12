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
| `GET /api/performance?period=1D\|1W\|1M\|3M\|1A\|ALL` | Broker equity history vs VOO (IEX), cached 60 s per account; display only |
| `GET /api/ops/status` | Ops health summary; polled every 10 s |
| `GET /api/status` | Engine state and full `RuntimeHealth`; polled every 2 s |

## Panels

| Panel | Source |
|---|---|
| Portfolio value + chart | `/api/performance`: portfolio vs VOO; degrades to `available: false` or `benchmark_error` |
| Mode badge | `runtime.mode` + `live.enabled`, never broker connectivity: green **PAPER**; red **LIVE** only when a live engine is trading; dimmed "LIVE (devre dışı)" otherwise |
| Account | Equity, cash, daily/realized/unrealized P&L, fees, drawdown from the account snapshot; gross exposure = sum of position values; engine state |
| Strategy card — **Leveraged Index Trend v1** | Status (active / warming / halted / disabled), configured symbols, positions and allocation vs `strategy.equity.maximum_allocation` (0.98), next expected decision (the close of the next NYSE session after the last decision, early closes included; an estimate), startup catch-up status (`submitted`, `warming_up`, `blocked:*`, `already_invested`, `catch_up_superseded`, `duplicate_decision`, `rebalance_in_progress`), holdings with target/current weight and reason (`trend_up` when QLD is targeted) |
| Risk | Gross exposure vs `max_gross_exposure_pct`, cash vs `cash_buffer_pct`, daily P&L vs `daily_loss_limit_pct`, drawdown vs `max_drawdown_pct`, entry-risk latch, circuit breaker, active safety incidents, risk epoch, buy capacity. Nothing critical is hidden |
| Execution | 25 open orders and 25 recent fills with decision lineage, reference price, slippage bps, fee, expected-vs-actual fill comparison; ownership adjustments |
| Universe | `universe.equity.symbols` (static) |
| System health | Workers (alive, heartbeat, restarts, last error), per-market stream health and data gaps, account snapshot source/age, reconciliation, DB presence/size, restart count |
| Explanations | Per-holding reason from the persisted decision metadata matched to current positions; a holding outside the latest decision is shown as such |

## Frontend

Templates `templates/base.html`, `index.html`, `tools.html`; dependency-free,
same-origin static assets (the CSP forbids inline scripts and third-party
origins): `static/css/app.css` (light/dark tokens), `static/js/theme.js`,
`api.js` (control token), `websocket.js` (ticketed sockets), `ui.js` (shell,
status polling, engine controls, PAPER/LIVE arming, settings drawer),
`chart.js` (SVG line chart), `dashboard.js`, `tools.js`. Bump `ASSET_VERSION`
in `web/routers/pages.py` when static files change.

The pages embed the control token only for a peer that `/api/control-token`
itself would serve (loopback, or a private bridge peer in container mode);
`GET /api/live/prerequisites` has the same peer gate because its messages name
the approval path and key variable. WebSocket `?ticket=` values are redacted
from the uvicorn log lines. Web backtests write `reports/web-<run id>/`; a new
run deletes the oldest so that at most 20 are kept, and the run input is
bounded (64 parameters, 16 KiB of serialized values, 4096-character symbols,
128-character strategy name).
