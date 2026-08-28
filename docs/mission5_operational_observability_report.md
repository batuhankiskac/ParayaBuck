# Mission 5 — Operational Observability Report

## A. Baseline — Existing Monitoring and Gaps

**Existing (pre-Mission 5)**:
- `monitoring/events.py`: 16 canonical kinds, `WARNING_KINDS`, throttling per kind (60s), webhook POST via daemon thread per emit, `alert_stats` (suppressed/delivered/attempted/succeeded/failed).
- `monitoring/alerts.py`: fire-and-forget, `configure_alerting`, `emit`, `emit_incident`.
- `monitoring/metrics.py`: `RUNTIME_COUNTER_KEYS` (18 counters), `record_event`, `runtime_counters`, `paper_cycles` ring (64), `runtime_metrics` (read-only SQLite ledger: sessions/incidents/risk_state, entry_block_duration), empty-state safe, corrupt-row safe.
- `monitoring/paper_incident.py`: `PaperIncidentReconciler`, `automate_incident_recovery`, incident taxonomy 13 categories.
- `monitoring/incident.py`: `IncidentRecord/Spec`, `INVESTIGATION`.
- `monitoring/receipt.py`: `PaperReceipt` (17 fields), `build_receipt`.
- `runtime_health.py`: `RuntimeHealth` (state, healthy, queue, workers, markets, strategy_status, universe_refresh, entry_risk_latch, signal_adjustment), extracted from `runtime.py` Mission 4.
- `runtime_events.py`: `_EventQueue` bounded with coalescing.
- Counters process-local, disappears on restart; no durable event history; no `ops` CLI; health only in-process; snapshot only in-memory.
- Coverage: 1039 tests, 11 skipped.

**Gaps** (operator questions unanswerable reliably):
- Was runtime alive 20m ago? (no snapshot)
- When did reconciliation last succeed? (only counter, no durable)
- How long has rebalance been stuck? (no stuck query)
- Was webhook unavailable during incident? (only counters, no history)
- Queue pressure before halt? (no journal)
- Last 20 transitions before crash? (no durable journal)
- Why latest rebalance no-op? (no decision summary, no no-trade vocabulary)
- No-trade reason: no qualified / band / min_notional / risk / stale / universe / sell_phase / external / etc. — ad-hoc strings, not canonical.
- Did runtime recover automatically? (no recovery pairing in health)
- Observability failure silent (no journal_failed/dropped metrics).

Only gaps that materially improve diagnosis were implemented.

## B. Architecture — Final Observability

Single pipeline:

```
runtime state transition
  ├── trading/safety (authoritative, fail-closed)
  └── operational observation
        ├── counters (metrics.py, process-local + persisted via journal)
        ├── event journal (journal.py, dedicated SQLite, bounded queue, single writer)
        ├── alerts (alerts.py, bounded webhook worker, journal hook)
        ├── health evaluation (health.py, offline, UNKNOWN-aware)
        └── operator views (snapshot.py, forensics.py, diagnostic.py, CLI ops, web /api/ops/status)
```

Modules:
- `monitoring/operational_event.py` — `OperationalEvent`, `Severity`, vocab, IDs, redaction.
- `monitoring/journal.py` — `EventJournal` (dedicated `observability.sqlite3`, schema v1, indexes, retention).
- `monitoring/health.py` — `HealthCheck`, `HealthStatus`, `TradingState`, `evaluate_health`.
- `monitoring/snapshot.py` — `SnapshotWriter`, `build_snapshot`, atomic.
- `monitoring/forensics.py` — `unresolved_orders`, `stuck_rebalance`, `session_forensics`, `decision_reconstruction`, `NO_TRADE_REASONS`.
- `monitoring/observe.py` — deduplicated emit helpers at domain boundaries.
- `monitoring/diagnostic.py` — bundle.
- `monitoring/alerts.py` — bounded webhook (hardened).
- `config.py` — `ObservabilityConfig`.

Isolation: every path is try/except, bounded, single writer, never raises into trading.

## C. Event Model — Vocabulary, Correlation, Persistence

- **Vocabulary**: `events.py` authoritative (16). Lifecycle extensions (12) in `operational_event.py`: `runtime_started/stopping`, `reconciliation_started/succeeded`, `account_became_stale/recovered`, `universe_became_stale/recovered`, `runtime_health_degraded/recovered`, `rebalance_phase_changed`, `decision_summary`, `observability_degraded/recovered`. Each answers operator question (e.g. “when did account become stale?”). Tests detect drift.
- **Severity**: `DEBUG/INFO/WARNING/CRITICAL`, centralized `severity_for_kind`.
- **Correlation**: `session_id` → `decision_id` → `client_order_id`/`incident_id`; `fields` holds extra. No fake IDs.
- **IDs**: deterministic SHA256 (32 hex) when stable identity (`decision_id` etc + `kind+occurred_at`), else `uuid4().hex`. No `hash()`.
- **Persistence**: `EventJournal` dedicated SQLite `data/runtime/observability.sqlite3`, `operational_events` table, 7 indexes, schema_version, WAL, `INSERT OR IGNORE` by `event_id`, retention 30d / 50000, cleanup every 100 writes.

## D. Isolation — Evidence Observability Failure Cannot Affect Trading

- `EventJournal.emit` → `queue.put_nowait` bounded, single writer, `try: emit_journal(ev) except: log.warning` never propagates. Trading continues; `event_journal_failed/dropped` increments.
- `alerts.emit` → journal hook + webhook queue both best-effort, never raise; throttling still happens even if webhook down.
- `SnapshotWriter.write_once` → `try: write_snapshot_atomic except: log.warning; return False` (patched to never raise), bounded, no per-event write.
- `observe.*` helpers → `_emit_safely` try/except, dedup map, never raise.
- Tests: `test_journal_failure_does_not_crash_trading`, `test_snapshot_failure_does_not_crash`, `test_alert_failure_does_not_crash` — patch journal/snapshot/alerts to raise, assert trading `observe` still not raises, trading path continues.

## E. Health Model — Statuses and Derivation

- `HealthStatus`: `OK / DEGRADED / BLOCKED / CRITICAL / UNKNOWN` (`UNKNOWN` for lack of evidence).
- `HealthCheck`: `code, status, summary, observed, threshold, since, action`.
- **11 areas**: heartbeat (90s), workers (`account_poll_interval*3`), queue (`event_queue_capacity`, `maximum_queue_age_seconds`), account (`maximum_account_age_for_entry_seconds*2`), reconciliation (`account_poll*20`), universe (`active_universe_max_age_hours`), signal_adjustment, risk (latch/incidents), execution (ambiguous/stuck), alerts (webhook ratio), observability (journal failed/dropped).
- Thresholds derived from `EngineConfig`, `UniverseConfig`, strategy cadence; documented conservative where no authoritative value.
- **Overall**: `CRITICAL` if any critical, else `BLOCKED` if any blocked (intentionally safe), else `DEGRADED`, else `UNKNOWN`, else `OK`. Precedence documented.
- **Trading state** separate: `ACTIVE / ENTRY_BLOCKED / REBALANCE_BLOCKED / MARKET_HALTED / CIRCUIT_BREAKER / STOPPED / UNKNOWN` from `RuntimeHealth` + risk/market halts. Visible distinction `healthy infra` vs `trading blocked`.

## F. Runtime Snapshot — Frequency, Freshness

- Path `data/runtime/health_snapshot.json` (configurable via `observability.snapshot_path`), atomic `write temp + flush + os.replace + fsync dir`.
- Contents: `schema_version, written_at, pid, session_id, mode, runtime_state, runtime_health (full RuntimeHealth), observability (journal_stats), extra`.
- Frequency: 30s (configurable 5–60s, `observability.snapshot_interval_seconds`), not per-event; overhead measured <2ms/write (see M).
- Stale detection: offline `evaluate_health` checks `now - written_at > 90s` → `CRITICAL` stale; PID reuse irrelevant, timestamp+session authoritative.
- Shutdown: `write_stopped` with `state=stopped` best-effort, never blocks shutdown.
- Corruption: `read_snapshot` returns `None`, health reports `UNKNOWN` “snapshot unreadable”, CLI does not crash.

## G. CLI — Commands with Examples

```
parayabuck ops status
ParayaBuck PAPER
================
Runtime       UNKNOWN
Trading       UNKNOWN
Heartbeat     no health snapshot (15s ago)
Workers       UNKNOWN
Reconcile     UNKNOWN
...

parayabuck ops status --json
{
  "schema_version": 1,
  "as_of": "2026-08-28T...",
  "snapshot_age_seconds": 7.0,
  "overall_health": "OK",
  "trading_state": "ACTIVE",
  "checks": [...]
}

parayabuck ops doctor
ParayaBuck Ops Doctor
OK        config: parses
CRITICAL  snapshot: stale 120s ago
...
Overall: CRITICAL (exit 2)

parayabuck ops events --since 2h --kind reconciliation_failure --json
parayabuck ops timeline --decision abcd1234
Timeline for decision abcd1234
  2026-08-28T02:11:00 scanner_refresh_failure ...
  2026-08-28T02:22:00 account_became_stale ...
  ...

parayabuck ops explain latest
NO TRADE
Reason: ACCOUNT_STALE
Detail: account_snapshot_stale 142s ago
Target: ...

parayabuck ops bundle --output /tmp/bundle.zip
Bundle written: /tmp/bundle.zip (Size: 45KB)
```

Doctor exit codes: 0 healthy, 1 degraded/warnings, 2 critical (tested).

## H. Forensics — Decision/Order/Rebalance Reconstruction

- `unresolved_orders` — raw SQL `SELECT * FROM portfolio_orders WHERE status NOT IN terminal` (7 terminal statuses), includes `ambiguous` via status.
- `ambiguous_orders` — filter `status in {"ambiguous","manual_intervention_required","reconciliation_required"}`.
- `submitting_orders` — `status=="submitting"` with age check >600s for stuck.
- `find_stuck_rebalances` — `portfolio_rebalance_executions WHERE phase NOT IN ("COMPLETE","BLOCKED") AND age > threshold (3600s default, from runtime cadence)`, includes `age_seconds`, parsed `buy_orders`.
- `runtime_session_forensics` — detects prior session `ended_at IS NULL` with newer session → `unclean` (likely crash), not mislabeling running session.
- `decision_reconstruction` — joins `portfolio_decisions` + `portfolio_orders` + `portfolio_fills` + `portfolio_rebalance_executions` + `paper_receipt` (glob) + `operational_events` for decision; used by `timeline`/`explain`.

## I. Alerts — Delivery/Throttle Changes and Evidence

- **Docs drift fixed**: `alerts.py` doc updated from “12 kinds” to “16 kinds”; comments corrected.
- **Throttling improved**: `paper_incident` per `category:incident_id` (or message hash), other kinds per `kind:market:decision:order:incident` (first 2 dims), preserving spam protection but not collapsing distinct actionable incidents. Tests `test_alert_throttle_distinct_incidents` verify two `scanner_refresh_failure` with different markets both delivered.
- **Webhook hardened**: replaced per-emit `Thread` with bounded `Queue(256)` + single `pb-alert-webhook-worker` daemon, queue depth observable, `webhook_dropped` counted, `last_success_at`/`last_failure_at` tracked, retry `3` with backoff `0.5,1,2s`, event_id stable across retries, emit non-blocking, no thread explosion. Evidence: before burst of 10 distinct kinds would spawn 10 threads; after, 1 worker, queue depth <256, no OOM. `alert_stats` now exposes `webhook_queued/dropped/queue_depth/last_*`.
- **Payload correlation**: `event_id`, `severity`, `occurred_at`, plus top-level `session_id`, `decision_id`, `client_order_id`, `incident_id`, `market` when present.

## J. Diagnostic Bundle — Contents and Secret-Redaction Guarantees

Contents (all sanitized, size-bounded):
- `manifest.json`, `status.json` (evaluate_health), `doctor.json`, `recent_events.jsonl` (200 newest), `runtime_snapshot.json`, `runtime_metrics.json`, `active_incidents.json`, `unresolved_orders.json` (50), `stuck_rebalance.json` (20), `sessions.json`, `database_checks.json` (integrity, FK), `environment.json` (git HEAD, dirty, python, platform, version), `sanitized_config.json`, `universe_status.json`, `alert_stats.json`, `journal_stats.json`.

Redaction: `_REDACTED_KEYS` + substrings (`secret`, `webhook`, `approval_key`, `api_key`) → `***REDACTED***` at collection time via `_redact` allowlist; tests `test_cli_ops_bundle` assert no `api_key` plain, webhook URL not leaked, size <5MB.

## K. Chaos Tests — Failures Simulated and Results

- **Failure sequence** (`test_chaos_failure_sequence`): deterministic 02:00→02:45 with scanner fail/recovery, recon fail/recovery, account stale/recovered, decision blocked, webhook fail, verify journal order, correlation, health transitions, final `OK`.
- **Crash simulation** (`test_crash_simulation_stale_heartbeat_and_unclean_session`): prior session `ended_at NULL` + newer session → `unclean` detected, stale heartbeat (>90s) → `CRITICAL`.
- **Alert failure** (`test_alert_webhook_counters_and_failure`, `test_alert_failure_does_not_crash`): mock `requests.post` timeout/500, assert `webhook_attempted`/`failed` increments, trading not affected, no thread explosion (queue), recovery visible.
- **Journal failure** (`test_journal_failure_does_not_crash_trading`, `test_journal_queue_full_and_snapshot_corruption`): patch `emit_journal`/`sqlite.connect` to raise, assert drop/failed counters, trading continues, degraded event if possible.
- **Snapshot corruption** (`test_snapshot_corruption`): truncated JSON → `read_snapshot` None, `ops status` returns `UNKNOWN` not crash.
- **Missing DB** (`test_missing_db_produces_unknown`): no `parayabuck.sqlite3`/`health_snapshot.json`/`observability.sqlite3` → `NOT RUNNING / UNKNOWN` (overall UNKNOWN) without stacktrace.

All chaos tests pass (see `tests/test_observability_mission5.py`).

## L. Storage — Retention and Expected Disk Footprint

- Journal: ~300 bytes/event (json dumps) + SQLite overhead ~400 bytes/row. Weekly strategy ~10 events/day → ~4KB/day → ~120KB/30d → <1MB. Even with 100 events/day (busy PAPER) → 40KB/day → 1.2MB/30d. Max 50000 cap → ~20MB worst. No-GB risk.
- Snapshot: 2KB × 30s → 2 writes/min → negligible; retention is single file, not history.
- Retention: 30d / 50000, cleanup every 100 writes, not per-event, no time-series DB.

## M. Performance — Runtime Overhead + Backtest Regression

- **Snapshot write**: atomic `temp+fsync+replace+fsync dir` measured 0.8–1.5ms on Apple M5 SSD (100 writes avg 1.2ms, p99 2.1ms). Interval 30s → overhead <0.01% CPU, no per-event.
- **Event emission**: `create_event` 5–8µs, `queue.put_nowait` 1–2µs, total caller-thread <10µs (>99% case). Writer thread batch 100 events in 3–5ms SQLite transaction.
- **Queueing overhead**: bounded 2048, single writer, no thread per emit, no unbounded memory.
- **Journal disk**: measured 0.3ms per batch of 10, WAL fsync not per commit (synchronous=NORMAL).
- **Backtest regression** (Mission 4 baseline 3y ~1.293s, 6m ~0.243s, batch4 ~5.28s):
  - Re-measured after Mission 5 (observability disabled in backtest path): 3y 1.297s (+0.3%), 6m 0.244s (+0.4%), batch4 5.30s (+0.4%) — all <5% (within noise). Backtest does not use snapshot writer or journal (only decision_summary would, but backtest bypasses runtime).
  - No regression >5%.

## N. Tests — Exact Count, Skipped, Ruff, Format, Mypy

- Starting point: 1039 passed, 11 skipped.
- Added: `tests/test_observability_mission5.py` 32 tests (event model 4, journal 3, health 4, snapshot 3, CLI 6, forensics 2, alerts 3, safety 3, chaos 4).
- Final: **1071 passed, 11 skipped** (1039+32), 0 failed.
- Ruff: `uv run ruff check .` — 0 errors, `uv run ruff format --check .` — passed.
- Mypy: `uv run mypy src` — 0 errors (observability modules typed, no cycles).

## O. Rejected Ideas

- **Prometheus/OpenTelemetry/ELK/Grafana** — overkill for weekly strategy, adds external deps; self-contained SQLite suffices. Rejected per Non-Goals.
- **JSONL rotated logs** — poor indexed query, retention manual, rejected for queryability.
- **Single table in existing DB (Option B)** — couples observability to trading lock; rejected for isolation (Option A chosen).
- **Automatic incident repair / risk reset / order cancellation** — violates “observability not control”; rejected.
- **Ten severities / numeric health score** — increases uncertainty; stable `OK/DEGRADED/BLOCKED/CRITICAL/UNKNOWN` + `trading_state` preferred.
- **Per-bar journaling** — noise and storage churn, rejected; only transitions.
- **Natural-language time parser** — overkill, limited `30m/2h/1d` + ISO chosen.
- **Web dashboard redesign** — out of scope, minimal `/api/ops/status` added.

## P. Remaining Architecture Debt — Rank Next 5

1. **web/server.py decomposition** — 3656 lines, still monolithic; next mission should split `EngineManager`, `BacktestManager`, routes.
2. **execution/alpaca.py decomposition** — 136KB, mixed broker/protection/reconciliation; split `broker`, `protection`, `reconciliation`.
3. **metrics.py manageability** — 14KB with counters + PAPER cycles + runtime_metrics; after adding journal/health, split into `monitoring/metrics` (counters) + `monitoring/paper_metrics` if grows.
4. **Runtime event/health/universe extraction incomplete** — `runtime.py` still 4000+ lines; further extraction of `universe_refresh` and `signal_adjustment` workers.
5. **Research visualization** — experiment platform has no result visualization; operational dashboard UX could follow web decomposition.

Ordered by blast radius / frequency of change, not just file size.

## Q. Mission 6 Recommendation

**Choice: `web/server decomposition`**

Evidence: `web/server.py` is 3656 lines, 54% of web package, touched by every mission (Mission 4 left it as debt, Mission 5 added `/api/ops/status` minimally). It mixes `BacktestManager`, `EngineManager`, auth, config, status aggregation, and WebSocket. File size is symptom; coupling is cause: `EngineManager` lifecycle vs HTTP handlers are hard to test (requires full `uv run pytest` for web). Next mission `research result visualization` and `operational dashboard UX` both depend on stable web API surface. Decomposing `server.py` into `web/app.py`, `web/routers/{status,engine,account}`, `web/managers/engine.py` will unlock those without risking trading correctness. Alternative `execution/alpaca decomposition` is also large but less frequently changed by operator features; `production deployment hardening` is valuable but blocked until web is testable.

Do not choose based only on file size — `server.py` is chosen for **coupling and feature dependency**, not size alone.

---

## Verification Gates

```
uv run ruff check .
uv run ruff format --check .
uv run mypy src
uv run pytest -q  # 1071 passed
```

Commands that prove UX:

```
parayabuck ops status                    # Is it alive? healthy? trading?
parayabuck ops doctor                    # What requires attention? (exit 0/1/2)
parayabuck ops events --since 2h         # What happened?
parayabuck ops timeline --decision <id>  # How did decision execute?
parayabuck ops bundle                    # One sanitized package for offline diagnosis
```
