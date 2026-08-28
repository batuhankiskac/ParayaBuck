# Operational Observability — ParayaBuck

## Architecture

Observability is a **read-only consumption layer** over trading state.

```
runtime state transition
        │
        ├── trading/safety state  (authoritative, fail-closed)
        │
        └── operational observation
                │
                ├── counters (process-local + persisted)
                ├── event journal (durable SQLite, bounded queue)
                ├── alerts (bounded webhook worker)
                ├── health evaluation (interpretation)
                └── operator views (CLI, web, bundle)
```

**Isolation**: every emit/journals/write is try/except, bounded queue, single writer, never raises into trading. `emit_journal`, `write_snapshot_atomic`, `alerts.emit` all catch exceptions and increment `*_failed`/`*_dropped` counters instead of propagating.

**Persistence choice**: dedicated SQLite store `data/runtime/observability.sqlite3` (Option A). Rationale:
- isolation from trading DB lock contention (trading uses `parayabuck.sqlite3` WAL);
- indexed queries for `ops events`/`timeline` (time, kind, severity, correlation);
- reuse of existing `SQLiteStore` durability (WAL, synchronous=NORMAL, busy_timeout).

## Event Flow

1. Trading/runtime emits `OperationalEvent` via `monitoring/observe.py` helpers or `alerts.emit` (which also journals durably).
2. `EventJournal.emit` enqueues to bounded `queue.Queue(2048)`; single writer thread flushes batch `INSERT OR IGNORE` (idempotent by `event_id`).
3. Reader `ops events`/`timeline` queries via `EventJournal.query` with indexed filters.
4. Health evaluation reads snapshot + DB without mutating.
5. `ops status` formats derived `overall_health` / `trading_state`.

**Volume**: weekly equity strategy produces ~5–15 events/day (lifecycle transitions only, not per-bar). ~300–600 bytes/event JSON → ~0.2 MB/day → ~6 MB/30 days.

## Event Model

`monitoring/operational_event.py`:

```python
@dataclass(frozen=True)
class OperationalEvent:
    (
        event_id,
        occurred_at(UTC),
        kind,
        severity,
        message,
    )
    (
        session_id,
        account_id,
        mode,
        market,
        strategy,
    )
    (
        decision_id,
        client_order_id,
        broker_order_id,
        incident_id,
        correlation_id,
    )
    fields: Mapping[str, object]
```

- **IDs**: deterministic SHA256 (truncated 32 hex) when stable identity exists (`decision_id`/`order`/`incident`/`correlation_id`+`extra`), else `uuid4().hex`. Never `hash()`.
- **Severity**: `DEBUG/INFO/WARNING/CRITICAL`, centralized `severity_for_kind` (maps `WARNING_KINDS` + lifecycle).
- **Vocabulary**: `monitoring/events.py` is authoritative for alert kinds (16 kinds). Lifecycle extensions (`LIFECYCLE_KINDS`) add `reconciliation_started/succeeded`, `account_became_stale/recovered`, `universe_became_stale/recovered`, `runtime_health_degraded/recovered`, `rebalance_phase_changed`, `decision_summary`, `observability_degraded/recovered`. Each answers an operator question (e.g. “when did account become stale?”).
- **Correlation**: reuse domain IDs (`session_id`→`decision_id`→`client_order_id`→`incident_id`). No fake IDs.
- **Secrets**: `_REDACTED_KEYS` — `api_key`, `webhook_url`, `approval_key` etc. — redacted to `***REDACTED***` at `create_event` time; `OperationalEvent.__post_init__` rejects plain secrets.

## Retention

`EventJournal` retention:
- `event_retention_days` (default 30) — delete `occurred_at < cutoff` every ~100 writes.
- `event_max_events` (default 50000) — delete oldest excess.
- Configurable via `observability:` block; cleanup not on every event.
- Indexes: `occurred_at`, `kind`, `severity`, `session_id`, `decision_id`, `client_order_id`, `incident_id`.

Schema version 1, idempotent migration, WAL.

## Health Semantics

`monitoring/health.py`:

- `HealthStatus`: `OK / DEGRADED / BLOCKED / CRITICAL / UNKNOWN`
- `HealthCheck(code, status, summary, observed, threshold, since, action)`
- `UNKNOWN` means “no evidence” — not `OK`.
- **Areas** (11 checks): `runtime_heartbeat`, `workers`, `queue`, `account`, `reconciliation`, `universe`, `signal_adjustment`, `risk`, `execution`, `alerts`, `observability`.
- **Thresholds** derived from config/runtime cadence:
  - heartbeat: 90s (snapshot interval 30s × 3)
  - workers: `account_poll_interval * 3` (30s ×3 = 90s)
  - queue age: `engine.maximum_queue_age_seconds` (30s)
  - account/reconciliation: `maximum_account_age_for_entry_seconds *2` etc.
  - universe: `active_universe_max_age_hours *3600`
  - rebalance stuck: 3600s
- **Overall derivation** precedence: `CRITICAL` > `BLOCKED` > `DEGRADED` > `UNKNOWN` > `OK`.
- `trading_state` separate concept: `ACTIVE / ENTRY_BLOCKED / REBALANCE_BLOCKED / MARKET_HALTED / CIRCUIT_BREAKER / STOPPED / UNKNOWN`, derived from `RuntimeHealth` + risk latch/market halts. A runtime can be `healthy infrastructure` but `ENTRY_BLOCKED` by risk — distinction visible.

## CLI

```
parayabuck ops status [--json] [--snapshot PATH] [--db PATH]
parayabuck ops doctor [--json] [--snapshot PATH] [--db PATH] [--journal PATH]
parayabuck ops events [--since 2h|30m|1d|ISO] [--until ...] [--kind ...] [--severity ...] [--market ...] [--decision ID] [--order ID] [--incident ID] [--limit N] [--json]
parayabuck ops timeline --decision ID | --order ID [--json]
parayabuck ops explain [latest|DECISION_ID] [--json]   # also `ops decision`
parayabuck ops bundle [--output PATH]
```

- `status --json` stable schema: `schema_version, as_of, snapshot_age_seconds, overall_health, trading_state, checks[]`.
- `doctor` exit codes: `0` healthy, `1` degraded/warnings, `2` critical; read-only.
- `events` relative time parsing: `30m`, `2h`, `1d`, plus ISO.
- `timeline` correlation view via decision/order/incident.
- `explain` answers “why no trade?” using decision reconstruction + `decision_summary` events + no-trade vocabulary.

**Example**:

```
parayabuck ops status
parayabuck ops doctor
parayabuck ops events --since 2h
parayabuck ops timeline --decision abcd1234
parayabuck ops explain latest
parayabuck ops bundle
```

## Incident Diagnosis Flow

1. `ops status` — alive? healthy? trading blocked? why? heartbeat age, risk latch, reconciliation.
2. `ops doctor` — what requires attention (unreadable snapshot, ambiguous orders, stale universe, unclean sessions).
3. `ops events --since 2h` — what happened (ordered transitions).
4. `ops timeline --decision <id>` — how decision executed (target → risk → sells → buys → fills).
5. `ops bundle` — one sanitized zip for offline share.

## Bundle

`monitoring/diagnostic.py` `create_bundle_zip`:

- `manifest.json`, `status.json`, `doctor.json`, `recent_events.jsonl` (200 newest), `runtime_snapshot.json`, `runtime_metrics.json`, `active_incidents.json`, `unresolved_orders.json`, `stuck_rebalance.json`, `sessions.json`, `database_checks.json`, `environment.json` (git HEAD, dirty, python, platform, version), `sanitized_config.json`, `universe_status.json`, `alert_stats.json`, `journal_stats.json`.
- Size bounded: each JSON truncated 500KB, recent_events limit 200, zip <5MB.
- Security: `_redact` allowlist — `api_key`, `api_secret`, `webhook_url`, `approval_key`, `password` → `***REDACTED***`; explicit tests.

## Alert Behavior

- Vocabulary authoritative: `monitoring/events.py` (16 kinds).
- Throttling per distinct actionable incident: `paper_incident` per `category:incident_id`, `scanner_refresh_failure` per `market`, `order_*` per `order`, etc. Preserves spam protection while not suppressing distinct incidents.
- Webhook: bounded single worker `queue(256)`, single thread `pb-alert-webhook-worker`, bounded retries `3` with backoff `0.5,1,2s`, queue depth observable, dropped counted, `last_success_at`/`last_failure_at` tracked. Emit remains non-blocking; failure never raises.
- Payload correlation: `event_id`, `severity`, `occurred_at`, plus top-level `session_id`, `decision_id`, `client_order_id`, `incident_id`, `market` when present.
- Counters: `suppressed`, `delivered{}`, `webhook_queued/dropped/queue_depth/last_success_at/last_failure_at`, plus journal `event_journal_*`.

Health shows `recovered` via journal, not forever-failed.

## Storage Estimate

- Weekly strategy: ~10 events/day (transitions + weekly decision summary) → ~300/month → ~0.2 MB/month with retention 50000 cap (~30 days → <10MB).
- Snapshot: ~2KB JSON, 30s interval → negligible; atomic write via temp+replace.
