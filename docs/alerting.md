# Alerting & runtime observability

The engine ships a minimal event-notification layer
(`parayabuck.monitoring.alerts`) and a runtime/risk metric aggregator
(`parayabuck.monitoring.metrics`). Alerts are **never per-bar noise**: they
fire on state transitions (scanner/reconciliation failure ↔ recovery, risk
breaches, circuit-breaker engagements, runtime lifecycle).

## Event kinds

`parayabuck.monitoring.alerts.emit(kind, *, message, **fields)` accepts
exactly these 12 kinds:

| Kind | When |
| --- | --- |
| `runtime_started` | Engine start completed (mode, symbol count). |
| `runtime_stopped_unexpected` | Stop from a halted/error state or with a circuit breaker active. |
| `scanner_refresh_failure` | A scheduled scanner refresh failed (per market, with retry attempt). |
| `scanner_recovered` | A scanner refresh succeeded. |
| `order_rejected` | A portfolio order was rejected by the broker. |
| `ambiguous_order` | Two-phase rebalance outcome ambiguous / crypto exit state ambiguous; entries halted. |
| `reconciliation_failure` | Periodic or startup reconciliation failed (incl. protection failures). |
| `reconciliation_recovered` | A failed reconciliation recovered. |
| `risk_incident` | Daily-loss limit breached; entries latched (SELL-only). |
| `drawdown_halt` | Drawdown limit breached (or restored at startup); entries halted until risk-epoch reset. |
| `risk_epoch_reset` | Operator-verified drawdown-baseline reset (`start_new_risk_epoch`). |
| `circuit_breaker` | A circuit breaker engaged (ownership drift, worker crash/restart limit, queue overflow, restored persistent incident). |

Unknown kinds are rejected with a logged error; `emit` never raises.

## Sinks

1. **Console/log sink — always on.** Every accepted event is written to the
   `parayabuck.monitoring.alerts` logger (`INFO` for lifecycle/recovery,
   `WARNING` for failures) with structured `fields`.
2. **Webhook POST — optional.** When configured, each event is POSTed as
   JSON (`kind`, `message`, `occurred_at`, `fields`) to the webhook URL.
   Delivery is fire-and-forget on a daemon thread with a short timeout;
   failures are logged, never raised.

Per-kind **throttling** (default 60 s window) coalesces repeat failures so a
broken scanner/broker cannot spam the webhook: the first occurrence is
delivered, repeats inside the window are suppressed (counted, logged at
DEBUG). `alerts.alert_stats()` exposes `suppressed`/`webhook_sent` counters.

## Configuration

```yaml
alerting:
  enabled: true
  webhook_url: ""        # or set PARAYABUCK_ALERT_WEBHOOK_URL
  events:                # kinds forwarded to the webhook
  - runtime_started
  - runtime_stopped_unexpected
  - scanner_refresh_failure
  - scanner_recovered
  - order_rejected
  - ambiguous_order
  - reconciliation_failure
  - reconciliation_recovered
  - risk_incident
  - drawdown_halt
  - risk_epoch_reset
  - circuit_breaker
```

Precedence: `configure_alerting(...)` (called by the engine from
`Config.alerting`) > `PARAYABUCK_ALERT_WEBHOOK_URL` env > the `alerting:`
block in `config/settings.yaml` (or `CONFIG_PATH`). The console/log sink
always receives every kind regardless of the `events` filter.

## Runtime metrics

`parayabuck.monitoring.metrics.runtime_metrics(database_path=None, *,
account_id=None) -> dict` aggregates, JSON-serializable and empty-state safe:

* **Persisted state** (read-only queries on the runtime SQLite ledger):
  `safety_incidents` (current active incidents), `risk_state` (latest
  per-account session row), `runtime_sessions` (session counts and derived
  `restart_count` = starts − 1).
* **Process-local counters** (`record_event`, zero-filled): scanner
  refresh failures/recoveries, reconciliation failures/recoveries, data-gap
  episodes, daily-loss/drawdown incidents, circuit-breaker engagements,
  order rejections, ambiguity events, runtime starts/unexpected stops.
* **Derived fields**: `restart_count`, `scanner_failures`,
  `reconciliation_failures`, `data_gap_events`, `daily_loss_incidents`,
  `drawdown_incidents`, `circuit_breaker_events`, `order_rejected_events`,
  `ambiguous_order_events`, and `entry_block_duration_seconds` (seconds the
  persisted drawdown halt has been blocking entries, `None` when inactive).

The engine records one `runtime_sessions` row per start
(`record_runtime_session`) and marks it ended on stop
(`end_runtime_session`); an unended row is a crash. No new tables were
added — the existing authoritative ledger records are extended.
