# Deferred Large-Module Refactor — Safe Extraction Seams

Post-hardening audit 2026-08-27: four hotspot modules exceed 100KB. No big-bang split before deployment per mission §18. Current hotspots are stable and hardened; extraction is deferred to post-PAPER-soak with narrow, tested seams.

## Hotspots (lines / bytes)

- `src/parayabuck/runtime.py` ~4109L / 209KB — EngineRuntime, warmup, catch-up, reconciliation, health
- `src/parayabuck/web/server.py` ~3363L / 144KB — FastAPI, dashboard, API, ws, settings, credentials
- `src/parayabuck/execution/alpaca.py` ~2409L / 116KB — Alpaca broker, orders, polling, stop-limit
- `src/parayabuck/execution/ledger.py` ~1607L / 69KB — SQLite WAL, decisions/orders/fills/sessions
- `src/parayabuck/portfolio/runtime_service.py` ~2205L / 104KB — PortfolioRuntime, per-market loops (also hotspot but not in original 4)

## Safe Seams (no behavior change, extraction-ready)

### runtime.py
- `EngineRuntime.__init__` → `engine/bootstrap.py` (config validation + strategy_specs + store/ledger/broker construction)
- `EngineRuntime._run` loop + catch-up → `engine/loop.py`
- Reconciliation (`_reconcile_*`) → `engine/reconcile.py`
- Health / metrics → `engine/health.py`
Seam: `EngineRuntime` keeps public API, delegates to helpers; helpers receive `Config`, `SQLiteStore`, `AlpacaBroker` via constructor.

### web/server.py
- `EngineManager` (800L) → `web/manager.py` (state, credentials, live arming, digests)
- `SettingsPayload` + `_validated_settings_payload` + `/_handle_save_settings` → `web/settings_service.py` + `web/schemas.py`
- Routes (`/api/settings`, `/api/account`, etc.) → `web/routes/settings.py`, `routes/account.py`
- Static/templates mount → `web/app.py` (already separate)
Seam: `server.py` becomes thin FastAPI assembly; `manager` stays singleton.

### execution/alpaca.py
- `_load_peak_equity` / `_save_peak_equity` + `RiskStatePersistenceError` → `execution/peak_state.py` (now uses `atomic_write`, configurable `runtime_state_dir`)
- Order building (`_build_order_request`) → `execution/orders.py`
- Polling / fill recovery → `execution/polling.py`
Seam: `AlpacaBroker` composes `PeakState` and `OrderBuilder`; peak path derived from `runtime.decision_db_path` parent (already implemented).

### execution/ledger.py
- `ExecutionLedger` (decisions/orders/fills) → `ledger/decision_store.py` + `ledger/order_store.py`
- `PortfolioStateRepository` already separate (`portfolio/runtime_service.py`) — no split needed.
Seam: `SQLiteStore` remains shared; stores share connection/lock.

## Constraints for Extraction

- Preserve `atomic_write` durability, WAL invariants, and fail-closed peak semantics.
- Keep `product_strategy_from` gate at execution boundary; extraction must not reintroduce `portfolio_strategy_from` in runtime.
- Settings API must keep `extra="forbid"` and PATCH + digest semantics; schemas move but validation stays.
- No allocation / risk / timeframe parameter changes during extraction.
- Each seam gets a dedicated test file before moving code; integration test is `pytest -q` + `pytest -m paper_chaos -q`.

## Deferred Docker Hardening

`read_only: true` with writable `config`, `data`, `reports` and tmpfs for `/tmp` was audited but **DEFERRED**: UI config persistence via `atomic_write` requires `ReadWritePaths` and directory fsync; enabling `read_only: true` without thorough integration test would break settings saves and backup. Documented as post-soak hardening; current hardening keeps `non-root`, `restart unless-stopped`, `stop_grace_period 30s`, loopback `127.0.0.1:8000:8000`, `UMask 0077`, `NoNewPrivileges`, `PrivateTmp`, `ProtectSystem=strict`.

