# Deferred Large-Module Refactor — Remaining Seams

Audit 2026-10-01 (wave 3c) rewrote this file. The 2026-08-27 version described
the four >100KB hotspots as they were BEFORE the web and broker splits; those
splits have since landed, and the sizes below are the current tree. Anything not
listed here is either already split or is small enough to leave alone. Line
counts are rounded; re-measure with `wc -l` before planning work.

## Already split (do not plan these again)

| Was | Now |
| --- | --- |
| `web/server.py` monolith (3,363L) | `web/routers/*` (routes), `web/services/*` (display/aggregation), `web/managers/*` (engine, backtest, websocket lifecycle), `web/app.py` + `web/application.py` + `web/lifespan.py` (assembly). `web/server.py` is the compatibility surface only (~530L). The 3,564-line `web/server_legacy.py` duplicate was deleted in wave 3c; the dashboard payload block now lives in `web/services/dashboard.py`. |
| `execution/alpaca.py` monolith (2,409L) | `alpaca_account.py`, `alpaca_orders.py`, `alpaca_protection.py`, `alpaca_reconciliation.py`, `alpaca_updates.py`, `alpaca_client.py`, `alpaca_models.py`; `alpaca.py` is the facade that composes them (~1,000L). |
| `markets/month_end.py` | Deleted in wave 3c — no production caller (VT35 decides daily; there is no month-end rebalance). |

## Remaining hotspots

- `src/parayabuck/runtime.py` ~4,700L — `EngineRuntime`: bootstrap, warmup, catch-up, reconciliation, health, event loop.
- `src/parayabuck/portfolio/runtime_service.py` ~2,200L — `PortfolioRuntime`, per-market decision loops, ownership reconciliation, runtime repository.
- `src/parayabuck/execution/ledger.py` ~1,600L — SQLite WAL, decisions/orders/fills/sessions, replay.

## Safe seams (no behaviour change, extraction-ready)

### runtime.py
- `EngineRuntime.__init__` → `engine/bootstrap.py` (config validation + strategy specs + store/ledger/broker construction)
- `_run` loop + catch-up → `engine/loop.py`
- Reconciliation (`_reconcile_*`) → `engine/reconcile.py`
- Health / metrics → `engine/health.py`

Seam: `EngineRuntime` keeps its public API and delegates; helpers receive
`Config`, `SQLiteStore`, `AlpacaBroker` via constructor.

### portfolio/runtime_service.py
- Decision loop per market → `portfolio/decision_loop.py`
- Ownership reconciliation + drift halts → `portfolio/ownership.py` (`runtime_ownership.py` already holds part of this)
- No-trade event suppression / receipt emission → `portfolio/notrade.py`

Seam: `PortfolioRuntime` keeps its public API; the repository stays injected.

### execution/ledger.py
- Decision store → `ledger/decision_store.py`; order/fill store → `ledger/order_store.py`
- `PortfolioStateRepository` is already separate (`portfolio/runtime_repository.py`).

Seam: `SQLiteStore` remains shared; the split stores share its connection and lock.

## Constraints for extraction

- Preserve `atomic_write` durability, WAL invariants, and fail-closed peak semantics.
- Keep the `product_strategy_from` gate at the execution boundary; extraction
  must not reintroduce `portfolio_strategy_from` in the runtime.
- Settings API keeps `extra="forbid"` plus PATCH and digest semantics; schemas
  move but validation stays.
- No allocation, risk or timeframe parameter changes during extraction.
- Each seam gets a dedicated test file before moving code; the integration gate
  is `pytest -q` plus `pytest -m paper_chaos -q`.

## Dead-code policy

Wave 3c removed the legacy monolith, the month-end scheduler, unreachable
observability hooks and the AlpacaBroker facade duplicates. Do not add a
"kept just in case" path back: git history and the `archive/*` tags are the
recovery mechanism. A new module must ship with a production caller or it is
review debt from the moment it lands.

## Deferred Docker Hardening

`read_only: true` with writable `config`, `data`, `reports` and tmpfs for `/tmp`
was audited but **DEFERRED**: UI config persistence via `atomic_write` requires
`ReadWritePaths` and directory fsync; enabling `read_only: true` without a
thorough integration test would break settings saves and backup. Documented as
post-soak hardening; current hardening keeps `non-root`,
`restart unless-stopped`, `stop_grace_period 30s`, loopback
`127.0.0.1:8000:8000`, `UMask 0077`, `NoNewPrivileges`, `PrivateTmp`,
`ProtectSystem=strict`.