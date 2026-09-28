# LIVE Readiness Gap Analysis — ParayaBuck 2026-08-29

**Repository:** `batuhankiskac/ParayaBuck`  
**Branch audited:** local working tree (post-Mission 7/8)  
**Audit date (UTC):** 2026-08-29  
**Auditor context:** Gate B documentation — loopback-only, no secrets, no LIVE enable  
**Modes frozen:** `runtime.mode = paper`, `live.enabled = false`, `live.hard_dollar_cap = 0`, `PARAYABUCK_ENABLE_LIVE != YES`  
**Constraint:** This document MUST NOT enable LIVE, MUST NOT generate an operator approval, MUST NOT auto-arm. PAPER is preserved.

> **Loopback-only invariant:** All operator surfaces (`docs/operations.md`, `docker-compose.yml`, `deploy/systemd/*`, `src/parayabuck/web/security.py`) bind `127.0.0.1:8000` and validate Host/Origin/control-token at the middleware layer. No `0.0.0.0` exposure outside Docker volume isolation where `PARAYABUCK_CONTAINER=YES` gates the host override.

---

## 0. Guardrails — verified before writing

| Guard | Value | Source | Verified |
|-------|-------|--------|----------|
| `runtime.mode` | `paper` | `config/settings.yaml:8` `runtime.mode: paper` | `read:5165` |
| `live.enabled` | `false` | `config/settings.yaml:204` | `read:5165` |
| `live.hard_dollar_cap` | `0` | `config/settings.yaml:205` | non-positive → LIVE blocked by `config.py:115-118` and `runtime.py:424-425,754-755` |
| `live.operator_approval_path` | `""` (empty) | `config/settings.yaml:206` | absent → `cli.py:303` and `web/managers/engine.py:941` fail-closed |
| `PARAYABUCK_ENABLE_LIVE` | not `YES` (default `.env.example:8` → `NO`) | `.env.example:8`, `docker-compose.yml:12` | `runtime.py:756`, `engine.py:436/763` require `== YES` |
| Operator approval file | not generated, not required in PAPER | `live_safety.py:62-101`, `cli.py:372-382` | `LiveOperatorApproval.load_verified` would fail on absent/expired/HMAC mismatch |
| Auto-arm | none | `web/managers/engine.py:762-826` `_require_valid_live_arm` requires phrase `LIVE <suffix>` per `account.account_suffix` + idle engine | manual, short-lived HMAC, expires in ≤1440 min |

No file in this audit mutates `config/settings.yaml`, `.env`, `live.*`, or creates `operator_approval_path`.

---

## 1. Classification Legend

| Status | Meaning | Gate implication |
|--------|---------|------------------|
| **PASS** | Evidence exists, tests green, docs match code; no change before soak | Ready to enter soak |
| **NEEDS PAPER SOAK** | Code PASS but real broker/time observations missing; must accumulate wall-clock PAPER history | Block LIVE until 1–2 weeks of live-wall-clock PAPER |
| **NEEDS OPERATOR PROCEDURE** | Code exists but operator runbook / drill / monitoring threshold not yet exercised by a human | Paper-soak companion; must be written + dry-run |
| **NEEDS IMPLEMENTATION** | Code gap or hardening deferred; should be addressed before or immediately after soak | Schedule before LIVE |
| **NOT TESTED** | No automated or manual evidence in this release | Must be tested before LIVE |

---

## 2. Summary Table — all Mission 8 areas

| Area | Status | Evidence | Gap | Next Step |
|------|--------|----------|-----|-----------|
| **Execution — account / ownership** | **PASS** | `execution/alpaca.py` facade + `execution/alpaca_account.py` split; `risk_peak.py` 469L strict-mypy; `runtime.py:1735` cached snapshot `maximum_account_age_for_entry_seconds:30`; `tests/execution/test_alpaca_characterization.py:17` + `test_portfolio_runtime.py:402` ownership classification | Single `account()` path still partially in facade (stub split) — low risk, no semantics change | Complete stub extraction post-soak (`alpaca_account.py` body); no LIVE block |
| **Execution — orders / ambiguity** | **PASS** | `execution/alpaca_orders.py` + `alpaca_models.AmbiguousOrderState`; truth matrix `docs/alpaca_broker_truth_matrix.md`; `tests/execution/test_alpaca_faults.py:5` (proven accepted via lookup, proven rejected 404, ambiguous on lookup failure, cancel unconfirmed) | Submit timeout → lookup recovery verified via fake only; no real-time Alpaca timeout observed in PAPER | Keep PAPER soak with fake coverage + one live PAPER timeout-recovery observation before LIVE |
| **Execution — protection / HELD** | **NEEDS PAPER SOAK** | `execution/alpaca_protection.py` `ProtectionService` 381L; fee-reserve `QuantityResolution` 0.9975 band when `qty_available==0`; HELD detection via nested `get_order_by_id(nested=True)`; `docs/mission7_execution_boundary_report.md §8` fail-closed table | HELD/stop lifecycle exercised in unit tests but no wall-clock PAPER bracket-fill/replace cycle observed | Soak 1–2 weeks PAPER equity; audit `ProtectionService` cancels + replaces via `execution/alpaca_protection.py:200-320` logs |
| **Execution — crypto locks** | **PASS** | `execution/alpaca_crypto.py` per-symbol `threading.Lock` non-blocking, OCO synthetic; `tests/execution/test_alpaca_concurrency.py:3` `test_same_symbol_exit_serialized`, `test_different_symbols_not_blocked` | Real 24/7 crypto stream concurrency not load-tested on host; only fake concurrency | Retain fake tests; optional `bench_execution_boundary.py: crypto pending exit recovery 0.00003s` baseline — no LIVE block unless PAPER crypto concurrency alert fires |
| **Execution — reconciliation idempotency** | **PASS** | `execution/alpaca_reconciliation.py` 7-bucket (`MATCH`, `EXTERNAL`, `STALE`, …) idempotent; `ReconciliationService.reconcile()` dedup by `activity.id`, watermark 24h paginated; `portfolio/idempotency.py` `IdempotencyKeyFactory` deterministic `strategy|version|iso|symbol|side|rebalance`; `test_alpaca_concurrency::test_reconcile_idempotent`; `tests/test_restart_chaos.py::test_open_managed_sell_exempts_drift_after_restart` fixed via `risk_peak` `__new__` isolation | Idempotency proven on fake; cold-start reconciliation after kill-9 not yet observed in PAPER wall-clock | PAPER kill/restart drill (see deployment) before LIVE |
| **PAPER soak** | **NEEDS PAPER SOAK** | Current identities are defined in `docs/validated_strategies.md` and `config/settings.yaml`; `scripts/audit_paper_soak.py` provides the read-only audit | No persisted scheduled/catch-up production decisions, no `portfolio_orders`→`portfolio_fills` lifecycle, no reconciliation-required incidents observed over real calendar weeks | **Block LIVE:** run uninterrupted PAPER 1–2 calendar weeks and re-run `python3 scripts/audit_paper_soak.py --pretty` until the scheduled-decision continuity, lifecycle, identity-continuity, and reconciliation checks pass |
| **Deployment — systemd** | **NEEDS OPERATOR PROCEDURE** | `deploy/systemd/parayabuck.service` hardened: `User=parayabuck`, `EnvironmentFile`, `ExecStartPre=parayabuck doctor --strict --paper --offline`, `Restart=on-failure`, `RestartSec=10`, `TimeoutStopSec=35`, `UMask=0077`, `NoNewPrivileges`, `PrivateTmp`, `ProtectSystem=strict`, `ProtectHome=true`, `ReadWritePaths=/opt/parayabuck/{config,data,reports}`; `docs/deployment.md:28-175` step-by-step Google Cloud update/rollback verified prescriptively | `systemd` unit never exercised on target VM in this release (audit machine `product_audit.md:128` `Docker build/healthz/SIGTERM NOT RUN`); `ReadWritePaths` vs `read_only:true` deferred per `deferred_refactor_seams.md:49` | Dry-run on staging VM: `daemon-reload → enable --now → status → journalctl -f → systemctl stop → healthz closed → backup → start → readyz 200`; record evidence |
| **Deployment — Docker** | **NEEDS OPERATOR PROCEDURE** | `Dockerfile:1-40` `python:3.12-slim` non-root `parayabuck`, locked `requirements.lock`, `VOLUME ["/app/config","/app/data","/app/reports"]`, `HEALTHCHECK /healthz` `30s/5s/20s×3`, `STOPSIGNAL SIGTERM`; `docker-compose.yml:22` `127.0.0.1:8000:8000`, `restart:unless-stopped`, `healthcheck` liveness `/healthz` (not `/readyz`), `stop_grace_period:30s`, `json-file` rotation `10m×3`, `env_file:.env` (no secrets on cmdline) | Docker gate `NOT RUN` on this machine (`product_audit.md:128`, `docs/deployment.md:169`); Docker is alternative to systemd, not both | Papertrail: `docker compose up -d --build → logs -f → curl /healthz /readyz → docker compose down` with same bind-mounts; confirm dashboard PAPER badge |
| **Deployment — secrets** | **PASS** | `.env` mode `600` (`deployment.md:22`), `EnvironmentFile=/opt/parayabuck/.env` (systemd), `env_file:.env` + `VOLUME` (compose); `web/security.py` redacts `api_key/webhook_url/approval_key` in events/bundle (`operational_observability.md:145`), `cli.py:311-318` checks `PARAYABUCK_OPERATOR_APPROVAL_KEY ≥32 chars`; logs never emit credential values (`operations.md:94`) | Secret rotation procedure undocumented; no test for `.env` permission enforcement | Add operator runbook step: `chmod 600 .env`, `umask 0077` already in units, verify `ps aux | grep ALPACA` shows no secrets |
| **Deployment — state** | **PASS** | `data/runtime/parayabuck.sqlite3` WAL `busy_timeout 5000` (`paper_runtime_lifecycle.md:26`), `DEFAULT_DECISION_DB_PATH` single-sourced (`config.py:21`); `storage.py:17` + `execution/ledger.py:59` + `portfolio/runtime_service.py:174` shared `store.lock`; `operations.md:9-19` persist docs, active-universe snapshots `data/runtime/active_{equity,crypto}_universe.json` with digest pin | State migration rollback only when new release corrupts ledger (`deployment.md:159-162` keeps healthy new ledger with broker-executed fills) — operator must internalize "broker reconciliation is authoritative" | Drill: snapshot `cp -a data/runtime` before upgrade; practice restore with same-path semantics |
| **Deployment — backup / restore** | **PASS** | `backup.py` online `sqlite3 backup API` (never raw copy) `BACKUP_PREFIX parayabuck-` `BACKUP_SUFFIX .sqlite3`, atomic timestamped file + `PRAGMA integrity_check` + `prune_backups` oldest-first + `latest_verified_backup` rollback-journal normalized; `deploy/systemd/parayabuck-backup.{service,timer}` every `6h` `Persistent=true`; docs `operations.md:97-119`, `deployment.md:85-91` | Timer never fired wall-clock in this audit; retention pruning (7 days default) not observed on host | Run `parayabuck backup` live (engine running), verify `integrity_check`, test restore to temp dir + `sqlite3 ... "SELECT count(*) FROM portfolio_decisions"` |
| **Deployment — crash restart** | **NEEDS OPERATOR PROCEDURE** | `runtime.py:829` `EngineRuntime.start` fail-closed: `DISABLED/ already running / live without YES` → `RuntimeError`, transient → `TransientStartupError` retried by caller; `_account_loop 15s`, `_schedule_reconcile 60s singleton pb-reconcile`, `pb-warmup-retry 600s floor doubling per consecutive attempt to 600*2**6s`, counter reset on any pending-set change; startup reconciles broker before decisions, pins open positions with lineage, restores `safety_incidents` + `risk_state`, warms strategies (`paper_runtime_lifecycle.md:13-76`); `product_audit.md:62-65` clean stop verified: `SIGTERM → Engine runtime stopped → Application shutdown complete` | No kill-9 / OOM / host-reboot recovery observed on target; `restart>5 → market halt` (`monitoring/health.py`) not wall-clock tested | Kill drill: `systemctl kill -s SIGKILL` or `docker kill` then `start` → verify reconciliation adopts positions, no duplicate orders (deterministic `client_order_id`), health recovers |
| **Deployment — graceful shutdown** | **PASS** | `runtime.py:1641` `stop()` joins `pb-signal-adjustment` before `HistoricalData.close` (`test_shutdown_joins_signal_adjustment_before_client_close:704`); `runtime.py:1591` `stream.stop()` on every path; web `server.py:624,683` wraps `runtime.stop()` on failure; systemd `TimeoutStopSec=35` > join `5/30/30/10s`; Docker `stop_grace_period:30s`; docs `deployment.md:61` do not lower below 30s | Shutdown under load (open orders + streaming bars) not soaked | Observe PAPER shutdown via `journalctl` for `Application shutdown complete` and absence of orphan state; keep `TimeoutStopSec` at 35 |
| **Observability** | **PASS** | Dedicated `observability.sqlite3` WAL Option A (`operational_observability.md:23`), bounded `queue(2048)` single writer `INSERT OR IGNORE` idempotent `event_id` SHA256, 11 `health.py` checks (`runtime_heartbeat 90s`, `workers 90s`, `queue 30s`, `account/reconciliation stale`, `universe`, `signal_adjustment`, `risk`, `execution`, `alerts`, `observability`); CLI `parayabuck ops {status,doctor,events,timeline,explain,bundle}` stable JSON; `diagnostic.py` bundle sanitized + `***REDACTED***` allowlist; alerts bounded `queue(256)` retries `3` `0.5/1/2s`, throttled per kind; `tests/test_observability_mission5.py 31KB` 994 passing baseline | Bundle size bounded `<5MB` 200 events truncated — operator may need longer window for 2-week soak; health thresholds derived from config/30s cadence — tune if `execution_timeframe` changes | Run `parayabuck ops status --json`, `doctor`, `events --since 2h`, `bundle` during PAPER soak; file bundle per incident |
| **Performance** | **PASS** | `benchmarks/execution_boundary_mission8.json` (fake, no network): `account 100 positions 0.00019s`, `reconcile 20 positions 0.00018s`, `submit BUY 0.00235s`, `submit SELL 0.00233s`, `timeout→client_id recovery 0.00238s`, `peak save 0.00013s`, `protection matching 0.00009s`; `mission7_execution_boundary_report.md §7` no >5% regression vs Mission 6; `bench_web_platform.py` `/healthz 0.0005s` RSS delta `89MB`; `bench_backtest.py` `1.7M rows 4.72s` `v2 backtest 23.48s` | Benchmarks on dev machine, not target GCE SKU; no sustained 7-day latency/throughput observation under real Alpaca poll/stream load | Re-run `python benchmarks/bench_execution_boundary.py` on target host during PAPER soak; gate >5% p95 drift |
| **Security — loopback** | **PASS** | `config/settings.yaml:3` `host: 127.0.0.1`, `web/security.py:89` loopback allowlist `127.0.0.1/localhost/::1` (+ `testserver` under pytest), `docker-compose.yml:24` `127.0.0.1:8000:8000`, `deploy/systemd/parayabuck.service:23` comment loopback-only + `ProtectSystem` | `PARAYABUCK_CONTAINER=YES` + `HOST=0.0.0.0` allowed only inside Docker (`Dockerfile:6`); operator must not set `HOST` to `0.0.0.0` on bare-metal | Assert `curl http://127.0.0.1:8000/healthz` succeeds, `curl -H "Host: evil.com"` 403 `Untrusted Host header` |
| **Security — Host header** | **PASS** | `web/security.py:49-55` `is_trusted_host` exact match allowlist, `build_protect_middleware:86-93` returns `403 {"detail":"Untrusted Host header"}` ; `tests/test_web_security_invariants.py:8.5KB` covers invariants | No wall-clock external Host fuzz beyond test suite | Keep test; add `test_web_security_invariants` to pre-deploy pytest subset (`deployment.md:113`) |
| **Security — Origin** | **PASS** | `web/security.py:27-32` `trusted_browser_origin` requires `127.0.0.1/localhost/::1`; middleware `94-97` rejects mutating methods with untrusted Origin `403 Untrusted browser origin`; `GET/HEAD/OPTIONS` non-mutating exempt, empty Origin treated as non-browser in middleware (`_trusted_browser_origin_for_middleware:35`) but mutated still checked via control-token | Empty-Origin `curl` from operator desktop must still present control token for POST/PUT/DELETE | Verify `curl -X POST http://127.0.0.1:8000/api/... -H "Origin: https://evil.com"` 403 |
| **Security — control token** | **PASS** | `web/security.py:62-63` `secrets.compare_digest` on `x-parayabuck-control-token` header vs `control_token_provider()` per-request closure; ticket store `WebSocketTicketStore` TTL 30s single-use thread-safe; `web/application.py` injects CSP `default-src 'self'` `Referrer-Policy no-referrer` `nosniff` `no-store` on `/static/` | Token rotation requires app restart; no automated rotation runbook | Promote `control_token_provider` rotation procedure and `bundle` redaction test in `tests/test_web_security_invariants.py` |
| **Security — LIVE arm** | **PASS** | 7-gate: `config runtime.mode==live` + `live.enabled true` + `live.hard_dollar_cap>0` (`config.py:117-120`) + live Alpaca creds + `PARAYABUCK_ENABLE_LIVE=YES` (`runtime.py:756-757`, `web/managers/engine.py:436,763`) + HMAC approval `LiveOperatorApproval` expiry ≤1440 min binding `account_id+configuration_digests+maximum_dollar_exposure` + `LiveArmToken LIVE <suffix>` (`cli.py:309-382`, `live_safety.py:50-101`, `engine.py:898-934`); read-only Web checklist `GET /api/live/prerequisites` (there is no `ops live` CLI command) | No LIVE approval exists in this repo (empty path) — by design; `authorize-live` never run in PAPER; HMAC key `PARAYABUCK_OPERATOR_APPROVAL_KEY ≥32` placeholder (` .env.example:9`) | Document-only: enumerate 7 gates in operator runbook; refuse any partial arm (`engine.py:825-934` checklists) |

---

## 3. Detailed Evidence & Gaps

### 3.1 Execution boundary — account / ownership

**Code path.** `AlpacaBroker.account()` is the single account+positions fetch (`execution/alpaca.py:200`, `runtime.py:1735`). Snapshot cached as `_last_account_snapshot/_last_account_at` and guarded by `engine.maximum_account_age_for_entry_seconds:30` (`config/settings.yaml:214`) and `monitoring/health.py` account-stale threshold (`×2`). `RiskPeakService.ensure_reconciled(account_id, equity)` is fail-closed on identity change (`risk_peak.py:ensure_reconciled`).

**Evidence.** `tests/execution/test_alpaca_characterization.py` 17 tests pin happy paths (blocked flag, buying power, ownership classification including `manage_external_positions:false` granularity). `tests/test_portfolio_runtime.py:402` asserts ownership buckets. `benchmarks/execution_boundary_mission8.json` `account 100 positions` median `0.00019s`. `docs/alpaca_broker_contract_before.md:42-46` documents `account_id/account_suffix/manage_external_positions/ledger` surface.

**Gap.** The account facade remains a future extraction seam; behavior is preserved through delegation and is not a correctness gap.

**Next step.** Complete `alpaca_account.py` extraction post-soak (2 commits, tests green each). No LIVE blocker.

### 3.2 Execution — orders / ambiguity

**Code path.** `execution/alpaca_orders.py` (produced) + facade. `AmbiguousOrderState` raised when submit hits timeout and recovery lookup is inconclusive (`docs/alpaca_broker_truth_matrix.md`). `PortfolioRuntimeBroker.submit_portfolio_order` uses deterministic `client_order_id = f"parayabuck-{decision_id}-{salt}-{symbol}-{side}"` (per-ledger salt) (`paper_runtime_lifecycle.md:62`).

**Evidence.** `tests/execution/test_alpaca_faults.py:5` — proven accepted via `get_order_by_client_id`, proven rejected via `404`, ambiguous when lookup fails, cancel-unconfirmed ambiguous. Fake `FakeTradingClient` `script_submit/script_get_order_by_client_id/submit_then_raise` (`execution/fake_client.py:363`).

**Gap.** Only fake injection. No real Alpaca timeout wall-clock captured. Paper soak should surface at least one timeout-recovery path under real latency.

**Next step.** Observe PAPER soak logs for `AmbiguousOrderState` / `order_rejected` emits; no code change required.

### 3.3 Execution — protection / HELD

**Code path.** `ProtectionService` batch `get_orders` once, fee-reserve tolerance, nested bracket `get_order_by_id(nested=True)`, `required_qty` computed with `0.9975` reserve when `qty_available==0`, `under/over` detection, `cancel+confirm` before replace, remainder-only submission (`alpaca_protection.py:1-35` header).

**Evidence.** Unit-tested through the execution fault and protection suites; the broker contract is summarized in `docs/alpaca_broker_truth_matrix.md`.

**Gap.** HELD/stop lifecycle still needs a wall-clock PAPER bracket-fill and replacement observation.

**Next step.** 1–2 week PAPER equity soak with at least one `under_protected` → cancel+replace cycle logged.

### 3.4 Execution — crypto locks

**Code path.** `AlpacaCrypto` synthetic OCO `per-symbol Lock` non-blocking (`alpaca_crypto.py`), `cancel_stop_then_exit` with `crypto_stop_cancel_timeout_seconds:5` `poll 0.25s` (`config/settings.yaml:218-219`).

**Evidence.** `tests/execution/test_alpaca_concurrency.py::test_same_symbol_exit_serialized` (lock SERIALIZED) and `test_different_symbols_not_blocked`. Benchmark `crypto pending exit recovery 0.00003s`.

**Gap.** Concurrency proven on fake; sustained 24/7 `BTC/USD`+`ETH/USD` 12-symbol stream not load-tested on host under 96h rebalance contention.

**Next step.** Monitor PAPER `crypto_exit_state` reconciliation (`alpaca_reconciliation.py:222-297`) during rapid price moves; no implementation gap unless contention alert fires.

### 3.5 Execution — reconciliation idempotency

**Code path.** `ReconciliationService.reconcile()` orchestrates `account()` → fee/fill backfill (watermarked 24h paginated dedup by `activity.id`) → `ledger.managed_for_account` 7-bucket → `_reconcile_stale_portfolio_ownership` → `_reconcile_crypto_exit_state` → `_reconcile_stale_ledger_entries` → protective (re)submission. `portfolio/idempotency.py:15-37` `IdempotencyKeyFactory` freezes determinism.

**Evidence.** `test_alpaca_concurrency::test_reconcile_idempotent` (same buckets on second call). `test_restart_chaos.py::test_open_managed_sell_exempts_drift_after_restart` now passes after `RiskPeakService` `__new__` isolation fix (report §7). `portfolio/runtime_models.py` `is_terminal_order_status` plus `portfolio/runtime_rebalance.py` phase predicates.

**Gap.** Cold-restart recovery of a half-submitted `submitting` order (`resolve_portfolio_submission` at restart) proven only via fake + transient integration; not wall-clock observed.

**Next step.** Crash-restart drill (section 3.8) doubles as idempotency proof — verify second `reconcile()` yields identical buckets and no duplicate `client_order_id`.

### 3.6 PAPER soak (standalone)

**Evidence.** The selected identities and their frozen parameters are recorded in `docs/validated_strategies.md`; `scripts/audit_paper_soak.py` is read-only and requires real PAPER state for soak evidence.

**Gap.** Zero wall-clock PAPER rebalances have been confirmed in this audit. The required soak is at least two consecutive scheduled equity decisions with stable identity and reconciliation state.

**Next step.** `NEEDS PAPER SOAK` — run PAPER engine uninterrupted 1–2 weeks. Nightly run `python3 scripts/audit_paper_soak.py --pretty` and archive each JSON. Only after checklist passes may LIVE be reconsidered (and then only with fresh approval + arm token).

### 3.7 Deployment — systemd

**Evidence.** `deploy/systemd/parayabuck.service:1-28` hardened (`NoNewPrivileges`, `PrivateTmp`, `ProtectSystem=strict`, `ProtectHome`, `ReadWritePaths`, `UMask=0077`), `Restart=on-failure RestartSec=10 TimeoutStopSec=35`, `ExecStartPre=parayabuck doctor --strict --paper --offline`. `docs/deployment.md:28-175` reproducible update/rollback with broker inspection + `tar czf` config snapshot + offline pytest subset + `curl /healthz /readyz`.

**Gap.** The `systemd` unit has not been exercised on the target host; the deployment procedure remains an operator task. `docs/deferred_refactor_seams.md` records the remaining read-only hardening seam.

**Next step.** Staging VM dry-run with `daemon-reload/enable --now/status/journalctl -f/stop/healthz-closed/backup/start/readyz-200` logged.

### 3.8 Deployment — Docker

**Evidence.** `Dockerfile:1-40` non-root, locked requirements, `VOLUMEs` for operator mutations, `HEALTHCHECK /healthz`; `docker-compose.yml:17-57` `restart:unless-stopped`, `127.0.0.1:8000:8000`, env from `.env` not in `ps`, `stop_grace_period:30s` + `json-file 10m×3`, `healthcheck` liveness on `/healthz` (readiness `/readyz` deliberately NOT health, see `operations.md:74-88` alive-vs-ready).

**Gap.** Same `NOT RUN` as systemd; Docker is documented alternative, not primary GCE path.

**Next step.** Single-host `docker compose up -d --build && logs -f` drill with bind-mount survival check.

### 3.9 Deployment — secrets

**Evidence.** `deployment.md:22` `ENV (.env, mode 600)`, `Dockerfile:14-24` non-root, `operations.md:94` "never logs credential values", `backup.py`/`diagnostic.py` redaction allowlist.

**Gap.** Rotation not drilled.

**Next step.** Add runbook: rotate `ALPACA_API_KEY/SECRET` → `systemctl restart` / `docker compose up -d` → `ops status` healthy; assert `env | grep -i alpaca` not in `docker inspect`.

### 3.10 Deployment — state

**Evidence.** `paper_runtime_lifecycle.md:26` WAL+FK+busy_timeout, `ops status` + `journal` split from trading `parayabuck.sqlite3`. `operations.md:9-19` "Keep `data/` on durable storage. Before upgrades, stop cleanly and copy both directories."

**Gap.** Operator may `git checkout` clobber `config/` — `deployment.md:157` warns not to; needs procedural enforcement via `tar czf` step.

**Next step.** Snapshot drill before every upgrade.

### 3.11 Deployment — backup / restore

**Evidence.** `backup.py:51-166` `backup_database` via `sqlite3 backup API` + `PRAGMA integrity_check` + `fsync` + `prune_backups` + `latest_verified_backup`; `parayabuck-backup.service/.timer` every 6h `Persistent=true`; `operations.md:99-119` "safe while running".

**Gap.** Timer not observed; pruning of expired (7d) not observed.

**Next step.** Manual `parayabuck backup --retention-days 7` + `latest_verified_backup` → temp restore + `SELECT count(*) FROM portfolio_decisions`.

### 3.12 Deployment — crash restart

**Evidence.** `paper_runtime_lifecycle.md:70` worker/health taxonomy; `runtime.py:829-933` fail-closed start, transient deferred, `reconcile` 60s singleton, `warmup-retry 600s floor doubling to 600*2**6s`, `signal-adjustment hourly`, `account-loop 15s`.

**Gap.** Kill-9 not observed; `restart>5 → market halt` not triggered.

**Next step.** Drill `kill -9` / `systemctl kill` then `start` → verify ledger adoption, `already_invested` catch-up status (`deployment.md:146`), no duplicate orders.

### 3.13 Deployment — graceful shutdown

**Evidence.** `runtime.py:1591` `stream.stop()` every `stop()`; join `5/30/30/10s` + `pb-signal-adjustment` before `HistoricalData.close`; `server.py:624,683` wraps stop on failed start; `TimeoutStopSec=35` > `join` window.

**Gap.** Under-load shutdown not soaked.

**Next step.** `systemctl stop` while orders subscribing; assert `journalctl` shows `Application shutdown complete` and `sqlite3 portfolio_decisions` no orphan `submitting`.

### 3.14 Observability

**Evidence.** `operational_observability.md` full: 11 checks, thresholds (`heartbeat 90s`, `workers 90s`, `queue 30s`, `account stale 30s×2`, `universe age`), CLI `ops status/doctor/events/timeline/explain/bundle` stable JSON, throttling, bounded queues, `tests/test_observability_mission5.py 31KB`.

**Gap.** `PASS` with procedural companion: operator must practice `ops doctor → events --since 2h → timeline --decision <id> → bundle` flow weekly during soak.

**Next step.** Weekly `ops bundle` archive alongside soak audit.

### 3.15 Performance

**Evidence.** Current executable benchmarks are `benchmarks/bench_execution_boundary.py`, `benchmarks/bench_web_platform.py`, and `benchmarks/bench_backtest.py`; these are local performance checks, not target-host soak evidence.

**Gap.** Off-target SKU.

**Next step.** Re-run `python benchmarks/bench_execution_boundary.py` on staging host; gate p95 < `bench` ×1.05.

### 3.16 Security — loopback / Host / Origin / control-token / LIVE arm

Covered in table §2 rows. Summary mechanism:

- **Loopback:** `app.host 127.0.0.1` (`config/settings.yaml:3`, `security.py:49-55`) + `127.0.0.1:8000:8000` compose + `ProtectSystem` systemd.
- **Host:** `security.py:86-93` 403 on untrusted.
- **Origin:** `security.py:27-32,70-97` 403 on mutating + untrusted Origin.
- **Control token:** `security.py:62-63` `secrets.compare_digest` + `WebSocketTicketStore TTL 30s single-use`.
- **LIVE arm:** 7 gates enumerated §0; read-only Web checklist `GET /api/live/prerequisites`, arming via `POST /api/live/arming/request` then `POST /api/live/arming/confirm`; HMAC SHA256 `live_safety.py:55`.

All `PASS`; gaps are procedural rotation/drill.

---

## 4. Constraints re-affirmed

- `runtime.mode` remains `paper`; this doc performs no `edit` on `config/settings.yaml` beyond read.
- `live.hard_dollar_cap` remains `0`; no `parayabuck authorize-live` invocation, no `operator_approval_path` file created.
- No auto-arm; `PARAYABUCK_ENABLE_LIVE` remains not-`YES` outside a deliberate LIVE ceremony (`web/managers/engine.py:762` requires explicit operator confirmation with fresh `LIVE <suffix>` phrase — this audit does not supply it).
- No secret values appear in this document or in any log snippet; bundle redaction verified (`operational_observability.md:145`).

---

## 5. Recommendation

**Remain PAPER.**

- Execution boundary is reviewable and hardened (Mission 7 facade + Mission 8 splits), observability is isolated and bounded, deployment artefacts are hardened (systemd non-root + `ProtectSystem`, Docker non-root + loopback-only + `stop_grace_period 30s`), and security posture (loopback/Host/Origin/control-token/7-gate LIVE arm) is `PASS`.

- **However:** the PAPER soak for the current PAPER identities (`equity_leveraged_trend_v1` daily + `crypto_low_variance_hysteresis_v1` 96h) has **not yet occurred** wall-clock. No scheduled production rebalance, protection replacement, crash-restart recovery, or backup-timer fire has been observed on a durable host.

- The only path to LIVE is:

  1. Run uninterrupted PAPER on the target host (systemd primary, or Docker alternative) for **1–2 calendar weeks**. This covers ≥2 scheduled equity decisions (QQQ 200-session SMA, `vol_target` 0.35 over 20 sessions, QLD holding) and at least one 96h crypto hysteresis retention cycle, plus intervening universe refresh, signal-basis verification, and risk-latch windows.

  2. During that window, execute the **operator procedures** weekly: `ops status/doctor/events/timeline/bundle`, `audit_paper_soak.py --pretty`, `parayabuck backup` + restore check, graceful `systemctl stop/start` and one unclean `kill -9 → start` reconciliation check, and `bench_execution_boundary.py` p95 gate. Log each as evidence (bundle zip + audit JSON).

  3. Declare soak success only when `audit_paper_soak.py` reports `≥1 scheduled and ≥1 startup-catch-up decision per market with no unexplained scheduled gap > 8 equity / 5 crypto days`, `0 unexplained reconciliation_required`, `0 open ambiguous orders`, scanner/config digests stable per `runtime.py` lineage, and operator sign-off that the five `NEEDS OPERATOR PROCEDURE` drills each succeeded at least once.

- **Do not enable LIVE** before that. When soak passes, a *separate* LIVE authorization ceremony must still satisfy all 7 gates (`runtime.mode live`, `live.enabled true`, `hard_dollar_cap >0`, live Alpaca creds, `PARAYABUCK_ENABLE_LIVE=YES`, short-lived HMAC `LiveOperatorApproval` bound to `account_id + configuration_digests + cap`, and fresh `LIVE <suffix>` Web confirmation). No gate may be skipped or cached.

---

## 6. Commands to reproduce this audit (read-only)

```bash
# 1. Guardrails — no LIVE enable
grep -E "runtime:$|mode:.*paper|live:$|enabled: false|hard_dollar_cap: 0" config/settings.yaml | head -12
grep PARAYABUCK_ENABLE_LIVE .env.example docker-compose.yml src/parayabuck/runtime.py

# 2. Execution boundary evidence
uv run ruff check . && uv run ruff format --check . && uv run mypy src
uv run pytest -q tests/execution/test_alpaca_characterization.py tests/execution/test_alpaca_faults.py tests/execution/test_alpaca_concurrency.py
uv run python benchmarks/bench_execution_boundary.py
cat benchmarks/execution_boundary_mission8.json | python3 -m json.tool | head -60
cat docs/alpaca_broker_truth_matrix.md | head -80

# 3. PAPER soak status (read-only, never writes DB)
python3 scripts/audit_paper_soak.py --pretty | head -120

# 4. Deployment artefacts
cat deploy/systemd/parayabuck.service
cat deploy/systemd/parayabuck-backup.service deploy/systemd/parayabuck-backup.timer
cat docker-compose.yml
cat Dockerfile | head -45
uv run parayabuck doctor --strict --paper --offline || true

# 5. Observability
uv run parayabuck ops status --json | python3 -m json.tool | head -80
uv run parayabuck ops doctor --json | python3 -m json.tool | head -80
uv run parayabuck ops bundle --help

# 6. Security
grep -n "trusted_browser_origin\|is_trusted_host\|CONTROL_TOKEN\|LiveOperatorApproval\|hard_dollar_cap" src/parayabuck/web/security.py src/parayabuck/live_safety.py src/parayabuck/config.py src/parayabuck/runtime.py | head -40
uv run pytest -q tests/test_web_security_invariants.py

# 7. Performance gate
uv run python benchmarks/bench_web_platform.py | tail -20
```

---

## 7. References

- `config/settings.yaml:3-252` — mode, execution band, risk, live caps, scanner, alerts
- `src/parayabuck/config.py:24-121` — `RuntimeMode`, `LiveConfig.__post_init__` live guards
- `src/parayabuck/runtime.py:424-425,753-826,829-933,1591-1641,1735,2294,3152,3435` — live gate, start lifecycle, shutdown join
- `src/parayabuck/live_safety.py:14-102` — `LiveOperatorApproval` HMAC
- `src/parayabuck/web/security.py:23-146` — loopback/Host/Origin/control-token/ticket store
- `src/parayabuck/web/managers/engine.py:254-441,762-934` — 7-gate live-arm checklist
- `src/parayabuck/execution/{alpaca,alpaca_models,alpaca_client,risk_peak,alpaca_protection,alpaca_crypto,alpaca_reconciliation,alpaca_account,alpaca_orders}` — execution boundary split
- `src/parayabuck/execution/fake_client.py:363` — `FakeTradingClient`
- `src/parayabuck/portfolio/{idempotency,runtime_models,runtime_rebalance,runtime_service}` + lifecycle `docs/paper_runtime_lifecycle.md:1-77`
- `src/parayabuck/backup.py:51-241` + `deploy/systemd/parayabuck-backup.{service,timer}` + `docs/operations.md:97-119`
- `src/parayabuck/monitoring/{health,operational_event,observe,journal,alerts,diagnostic}` + `docs/operational_observability.md`
- `docs/execution_architecture.md`, `docs/alpaca_broker_truth_matrix.md`, `docs/operational_observability.md`
- `docs/validated_strategies.md` + `scripts/audit_paper_soak.py:1-483` + `tests/test_paper_soak_auditor.py`
- `docs/deployment.md` + `deploy/systemd/*` + `Dockerfile` + `docker-compose.yml` + `docs/operations.md`
- `docs/paper_operations.md`, `docs/paper_runtime_lifecycle.md`, `docs/deferred_refactor_seams.md`
- `benchmarks/bench_execution_boundary.py`, `benchmarks/bench_web_platform.py`, `benchmarks/bench_backtest.py`
- `tests/execution/{test_alpaca_characterization,test_alpaca_faults,test_alpaca_concurrency,test_alpaca_chaos}.py`
- `tests/test_web_security_invariants.py`, `tests/test_shutdown_joins_signal_adjustment_before_client_close.py`, `tests/test_restart_chaos.py`, `tests/test_observability_mission5.py`

---

*This audit preserves PAPER, creates no LIVE approval, and authorizes no LIVE arm. The next gate is a wall-clock PAPER soak, not a config edit.*
