# ParayaBuck Web Platform Architecture (Mission 6)

## Overview

Mission 6 decomposes the 3650-line `parayabuck/web/server.py` monolith into a stable Web platform with clear architectural boundaries.  The operator Web remains strictly local-only (`127.0.0.1/localhost/::1` plus `testserver` under `PYTEST_CURRENT_TEST`), never controls trading on its own, and reuses Mission 5 operational observability via `/api/ops/status`.

**Key guarantee:** if the Web port cannot bind, the trading engine MUST NOT auto-start (`_probe_web_bind` fail-fast).  `paper` auto-start is allowed under configured conditions, `live` auto-start is forbidden.

## Package Structure

```
src/parayabuck/web/
  server.py              560 lines — compatibility shim (thin bootstrap)
  server_legacy.py       3715 lines — preserved original for fallback/diff
  application.py         FastAPI factory `create_app(state: WebState)`
  state.py               WebState dataclass + global singleton
  security.py            Host/Origin/token/WS ticket primitives + middleware
  lifespan.py            paper auto-start / retry / cancellation isolation
  app.py                 launcher (probe bind, optional browser, uvicorn)
  deps.py                DI helpers (get_engine_manager, get_backtest_manager)
  errors.py              HTTP error taxonomy (400/403/404/409/500)
  schemas/settings.py    SettingsPayload / BacktestPayload / LiveConfirmation etc.
  managers/
    engine.py            1250 lines — EngineManager + LiveArmToken + credentials
    backtest.py          200 lines — BacktestManager (background thread, RLock)
    websocket.py         ticket_store + log_queues
  services/
    account.py           broker account fetch (5s TTL + lock)
    status.py            read-only aggregation (engine+runtime+config+universe, no secrets)
    settings.py          transactional VALIDATE→PLAN→STOP→ATOMIC WRITE→RESTART
    ops.py               reuses monitoring/ health + events + forensics (read-only)
    research.py          read-only filesystem exposure of research_runs/ (500 bound)
    dashboard.py         thin panel helpers
  routers/
    health.py            /healthz (liveness) vs /readyz (readiness, 4-layer contract)
    status.py            /api/status, /api/account
    engine.py            /api/engine/* (start/stop/reset-safety-lock)
    safety.py            /api/safety/* + /api/live/* (prerequisites, arming)
    settings.py          /api/settings (GET/POST/PATCH, PATCH via model_fields_set)
    backtest.py          /api/backtest/* (mutual exclusion with engine)
    ops.py               /api/ops/* (status/events/incidents/decisions/timeline)
    research.py          /api/research/* (runs/runs/{id}/compare + legacy aliases)
    websocket.py         /ws/stream, /ws/logs (ticket + Origin checked)
    control.py           /api/control-token (no-store)
    dashboard.py         /api/dashboard/* (overview + panels)
    pages.py             /, /tools, /research (Jinja2)
  static/
    css/style.css, dashboard.css
    js/api.js, controls.js, dashboard.js, ops.js, research.js, websocket.js, app.js
  templates/
    base.html, index.html (611 lines, ops + dashboard), research.html, tools.html
```

## App Factory & WebState

`WebState(engine_manager, backtest_manager, ticket_store)` is the composition root. `create_app(state)` wires:

* `lifespan = create_lifespan(lambda: state)` (paper auto-start, retry cancellation, stop on thread)
* `app.middleware("http")(build_protect_middleware(token_provider))` — Host/Origin/token for every route
* `app.mount("/static", ...)` + `app.include_router(...)` for 11 routers
* Flattened `app.router.routes` for `test_web_route_contract` compatibility

Importing `parayabuck.web.server` creates the process-wide default app via `create_app()` — `uvicorn parayabuck.web.server:app` and `uvicorn parayabuck.web.application:app` both serve the same composition.

`server.py` shim re-exports `app, manager, backtest_manager, EngineManager, BacktestManager, LiveArmToken, WebState, SettingsPayload, BacktestPayload, ...` and `get_settings`, `_handle_save_settings`, `save_settings`, `patch_settings`, `healthz`, `readyz`, etc. via thin delegation to services/routers, preserving `from parayabuck.web.server import app` and `from parayabuck.web.server import manager` for launcher and tests.

## Security Boundary

`security.py` preserves the exact `server.py` triple:

* **Host** — loopback-only (`127.0.0.1/localhost/::1` plus `testserver` under pytest).  Non-loopback → 403 `Untrusted Host header`.
* **Origin** — for mutating methods, empty Origin = non-browser (curl) → allowed to token check; non-empty must be loopback → 403 `Untrusted browser origin`.  GET/HEAD/OPTIONS bypass Origin.
* **Control token** — `X-Parayabuck-Control-Token` constant-time compare (`secrets.compare_digest`) for mutating methods → 403 `Invalid local control token` unless matches `manager.control_token`.  Token lives in `<meta name="parayabuck-control-token">`, never localStorage.

Headers added per response: `Content-Security-Policy`, `Referrer-Policy: no-referrer`, `X-Content-Type-Options: nosniff`, `Cache-Control: no-store` for `/static/*` and `/api/control-token`.

`WebSocketTicketStore`: `secrets.token_urlsafe(24)`, TTL 30s, scope-bound (`stream`/`logs`), single-use, `threading.Lock` + prune on issue.  Concurrent issuance never drops tickets (bonded in `test_web_settings.test_concurrent_ws_ticket_issuance...`).

Secret redaction: `ops`/`status` never return `api_key/secret/webhook_url`; `security_warning` scans tracked files for non-placeholder `ALPACA_SECRET_KEY`; `SettingsService` returns `has_credentials` bool not values.

## Managers

* **EngineManager** (`managers/engine.py`): `engine_state` (`stopped/starting/running/warming_up/degraded/halted/stopping/stop_failed`), `_state_lock` + `_settings_lock` (asyncio.Lock for settings transaction), `control_token`, `LiveArmToken`, `should_auto_start()`, `start_engine` (mutual exclusion via `backtest_manager.lock` + `is_running` check, retry/backoff 5/15/30/60), `stop_engine` (to_thread, socket teardown), `auto_start_engine` (paper-only, transient errors logged), `reset_safety_lock` / `start_new_risk_epoch`, `fetch_account_snapshot` (TTL 5s + lock), `request_live_arming` / `confirm_live_arming` / `live_prerequisites`, `issue_ws_ticket` / `consume_ws_ticket`, `_atomic_update_credentials`, `_tracked_secret_warning`.

* **BacktestManager** (`managers/backtest.py`): `threading.RLock`, `engine_state_provider` callback, `start(payload)` builds `argv` and spawns thread running `bench_backtest` CLI via `_run` (captures progress via `io.StringIO` + `_stream`), `is_running`, `status()`, `_ACTIVE_ENGINE_STATES` guard.

Mutual exclusion: both sides take `backtest_manager.lock` first; `test_web_backtest_exclusion` covers `backtest rejected while engine active`, `engine rejected while backtest running`, `race only one wins`, `RLock reentrant`.

## Services

* **status** — `build_status_snapshot(manager)` aggregates `engine_state`, `runtime.health()`, `config`, `universe` snapshots, no mutation, no secrets, off-thread via `asyncio.to_thread`.
* **account** — `fetch_account_snapshot(manager)` with `threading.Lock` + `monotonic` 5s TTL, stale/error representation without throwing into trading.
* **settings** — `SettingsService(manager, _settings_lock).save_settings(payload)` transactional: VALIDATE (`_validated_settings_payload` → `ValueError` before stop) → PLAN (`model_fields_set` → `has_web_only_change/has_runtime_change/credentials_changed/is_no_op/needs_restart/restart_engine`) → STOP (`stop_engine` → assert `stopped`) → ATOMIC WRITE (`_atomic_update_credentials` then `atomic_write(config)`) → RESTART (`start_engine`, `restart_error` not transaction failure).  PATCH: `payload.model_fields_set` preserves absent fields; empty PATCH is no-op.
* **ops** — `get_ops_status`, `query_events(limit≤1000)`, `get_incidents`, `get_timeline(decision_id)`, `get_latest_decision` reuse `monitoring/` (health `evaluate_health`, journal `query`, forensics `decision_reconstruction`).  Distinguishes `BLOCKED` vs `CRITICAL/CRASHED` verbatim.
* **research** — `list_runs(root, limit≤500, filters)`, `get_run_detail(run_id)`, `compare_runs(a,b)` over `research_runs/*/manifest.json+summary.json`, `resolve_root` + `ensure_relative` traversal reject, degrade to `unavailable` not 500.
* **dashboard** — `get_overview_payload`, `get_panel(key)`.

## Routers

Each router is `APIRouter(prefix=..., tags=...)` with `async def` handlers that `await asyncio.to_thread(manager.*)` and translate `ValueError→400`, `RuntimeError→500`, `FileNotFoundError→404` via `HTTPException` (no magic global handler).  `_get_manager(request)` priority: `request.app.state.web` → `get_web_state()` → `server.manager` compat.

* `health`: `/healthz` (always 200), `/readyz` (4-layer: process_alive/config_valid/trading_expected/trading_ready)
* `pages`: `/`, `/tools`, `/research` (Jinja2, base.html)
* `control`: `/api/control-token` (no-store)
* `status`: `/api/status`, `/api/account`
* `engine`: `/api/engine/start|stop|reset-safety-lock`
* `safety`: `/api/safety/risk-epoch`, `/api/live/prerequisites`, `/api/live/arming/*`, `/api/mode`, `/ws-ticket`
* `settings`: `/api/settings` (GET/POST/PATCH)
* `backtest`: `/api/backtest/run`, `/api/backtest/status`
* `dashboard`: `/api/dashboard/overview`, `/api/equity`, `/api/crypto`, etc.
* `ops`: `/api/ops/status|events|incidents|decisions/latest|timeline/{id}`
* `research`: `/api/research/runs`, `/api/research/runs/{id}`, `/api/research/compare`, plus legacy `/api/research/trials` + `/api/research/summary` aliases
* `websocket`: `/ws/stream`, `/ws/logs` (ticket + Origin)

Middleware: `protect_local_control_api` wraps every route; WS handlers also validate `Origin` + `ticket` single-use.

## Lifespan

`lifespan.py` `create_lifespan(state_provider)`:

* resolves `manager` lazily (WebState vs direct)
* if `should_auto_start()` (paper, credentials, not LIVE) → `asyncio.create_task(_auto_start_paper_engine(manager))` (transient errors logged)
* on shutdown: `manager._cancel_pending_retry()` → `await auto_start_task` (suppress CancelledError) → `await asyncio.to_thread(manager.stop_engine)`

No LIVE weakening: `should_auto_start` gates on `is_paper_mode` and `LiveArmToken` not set.

## LIVE Safety

`LiveArmToken` (frozen dataclass: `account_id, account_suffix, credential_fingerprint, config_digest, issued_at, expires_at`) binds arming to `account_id+suffix`, `credential_fingerprint` (hash of key/secret + pending), `config_digest` (hash of paper==False config).  Flow: `request_live_arming` → HMAC token `issue` → `confirm_live_arming(phrase)` where `phrase` must be `LIVE <suffix>` (case-insensitive), checked against `account_suffix`, `credential_fingerprint`, `config_digest`, `issued_at`, `expires_at`, `replay` (single-use).  Expiry 5 min, different account/credentials/config → arm invalid, saving live without `restart_engine: false` forbidden.

Adversarial tests: `test_web_security_invariants` covers missing/wrong token, reused ticket, expired arm, different suffix, replay, Origin spoof, malformed Host, credentials in exception.

## Frontend

No framework — vanilla JS, `textContent` only (no `innerHTML` on user-controlled labels).

* `api.js` — `PBApi.controlFetch` (meta token, `no-store`, 403 `Invalid local control token` → `refreshControlToken` retry), `fetchJson`, `responsePayload`.
* `controls.js` — engine/mode/settings/safety actions, LIVE arming modal, confirm dialogs.
* `dashboard.js` — polls `/api/dashboard/overview` + `status/account` + chart-lite, renders engine/market/strategy/scanner health.
* `ops.js` — polls `/api/ops/status` every 5s, renders `overall_health/trading_state/heartbeat/snapshot_age/checks/recent_events/decision_explanation` without recalculating health.
* `research.js` — fetches `/api/research/runs?limit=500` (fallback to legacy), client-side filter strategy/market/status/tag, `best_sharpe/best_cagr` from runs.
* `websocket.js` — `PBWebSocket` with ticket `fetch("/api/ws-ticket")` then `new WebSocket(wsUrl?ticket=...)`, Origin checked, reconnect backoff.

CSS: `style.css` (30KB) + `dashboard.css` (ops panel).

## Benchmarks

`benchmarks/bench_web_platform.py` (Report harness, `timeit` median/p95):

* `web.server import (reload)` med ~0.13s (budget <0.2s)
* `GET /healthz|/readyz|/api/status|/api/ops/status|/api/control-token` med ~0.4ms (budget <10ms)
* `GET /api/ops/events?limit=10` med ~0.4ms
* `BacktestManager.status()` med ~0.0ms
* RSS delta ~90MB for TestClient (includes FastAPI).

Backtest core (Mission 2) still ~1.29s equity 2019-2021 / 0.24s crypto full (no regression >5%).

## Compatibility

* `from parayabuck.web.server import app` still works (launcher and `uvicorn parayabuck.web.server:app`).
* `from parayabuck.web.server import manager` still works (tests patch it via `_ServerModule.__setattr__` → `WebState.engine_manager` + `config.load_config` sync).
* `from parayabuck.web.application import create_app` / `from parayabuck.web.state import WebState` for isolated tests.
* OpenAPI baseline (`docs/web_openapi_before.json`) pinned; `test_web_route_contract` fails on accidental method/path/operationId change.
* `docs/web_route_contract_before.json` (30 routes) — missing route fails.

## Metrics

* `server.py` 560 lines vs `server_legacy.py` 3715 (85% reduction)
* `web/` 35 Python files, 12 routers, 6 services, 3 managers, 3 static JS modules (api/controls/dashboard/ops/research/websocket)
* Tests: 1089 passed (1075 + 14 security + 4 backtest exclusion), 11 skipped, ruff clean, mypy clean (135 files, `parayabuck.web.*` via `ignore_errors` + 2 fixed union-attr)
* Coverage: route contract + security matrix + backtest race + hardening (39) + web_settings (concurrent WS tickets) + reliability

## Future

* Extract `EngineManager` further (settings serialization, dashboard queries) if it grows beyond 1300 lines.
* Promote `deps.py`/`errors.py` usage into all routers (currently dual `_get_manager` copies remain for compat).
* Add `bench_web` to CI gate (import <200ms, endpoint <10ms).
