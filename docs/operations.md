# Operations

How the running PAPER bot behaves and how to watch it. Deployment and the
webhook setup are in `docs/deployment.md`; symptom playbooks are in
`docs/operator_runbook.md`. All commands run on the VM, in the checkout.

## Daily cycle

| When (ET) | What happens |
|---|---|
| Regular session | 5m IEX bars stream for SPY, QQQ, QLD; the account is polled every 15 s and reconciled every 60 s |
| ≥ 16:00 | The session's daily bar is flushed; VT35 decides on completed bars only (QQQ close vs 200-session SMA, QLD 20-session vol). A daily bar for a session still trading is dropped from the signal history (`Ignored unfinished equity daily bar` in the log) |
| Right after the decision | Decision persisted; SELLs, then BUYs, submitted as DAY market orders sized against a fresh account snapshot; one `daily_summary` alert |
| Next open (09:30) | Alpaca fills the queued orders |
| Close + 45 min / next open + 30 min | Intent watchdog checks that a decision exists and that the target is held (below) |

No order is placed when the target is within the 0.5% rebalance band or the
order is under $10 (`execution.rebalance_band_pct`,
`execution.minimum_order_notional`). A fresh deployment makes one startup
catch-up decision on the latest completed bars; a restart never duplicates a
decision.

## Health and readiness

| Probe | Meaning |
|---|---|
| `GET /healthz` | Process liveness only: `200 {"status":"ok"}` whenever the web process serves. Docker's healthcheck uses it |
| `GET /readyz` | `200` only if the config is valid **and** (trading is not expected **or** the runtime is healthy) **and** no intent-watchdog alarm is active (when `intent_watchdog.fail_readiness`). Otherwise `503` |

`/readyz` body: `ready`, `process_alive`, `config_valid`, `trading_expected`,
`trading_ready`, `engine_state`, `runtime_expected`, `degraded_reasons`,
`error`. `degraded_reasons` lists active watchdog codes. Trading is expected
when `app.auto_start_engine` is on in paper mode, a runtime is active, or the
engine is in error. `503` during warmup (`engine_state=warming_up`) is normal.
`/api/status` carries the full `RuntimeHealth`, including `intent_watchdog`.

## Alerts

Every alert is written to the log/journal (`journalctl -u parayabuck`,
`parayabuck ops events`). If a webhook URL is set
(`PARAYABUCK_ALERT_WEBHOOK_URL`, see `docs/deployment.md`) and the kind is in
`alerting.events`, it is also POSTed as JSON: `event_id`, `kind`, `severity`,
`message` (also as `text` and `content` for Slack/Discord), `occurred_at`,
`fields`, plus correlation ids. Delivery runs on a background queue (256
entries, 3 attempts, 0.5/1/2 s backoff); it never blocks or fails trading.
Repeats of the same kind (per incident/market/order where relevant) are
suppressed for 60 s. Secrets are redacted from logs and payloads.

| Kind | Fires when | Webhook in current config |
|---|---|---|
| `runtime_started` | Engine start completed | yes |
| `runtime_stopped_unexpected` | Stop from a halted/error state or with a breaker active | yes |
| `daily_summary` | After each persisted equity decision | yes |
| `intent_alarm` / `intent_recovered` | Watchdog code becomes active / clears | yes |
| `order_rejected` | Broker rejected a portfolio order | yes |
| `ambiguous_order` | Order outcome unknown; dependent BUYs blocked | yes |
| `reconciliation_failure` / `reconciliation_recovered` | Reconciliation failed / recovered | yes |
| `risk_incident` | Daily-loss latch (SELL-only), or crypto found in the account at startup | yes |
| `drawdown_halt` | Drawdown limit breached (or restored at startup) | yes |
| `risk_epoch_reset` | Operator reset the drawdown baseline | yes |
| `circuit_breaker` | Breaker engaged (ownership drift, worker restart limit, queue overflow, persisted incident restored) | yes |
| `scanner_refresh_failure` | Warning-level provider outage (legacy name) | yes |
| `signal_adjustment_failure` / `signal_adjustment_recovery` | Split-basis verification failed / recovered (equity entries halted meanwhile) | no |
| `paper_incident` | Specific incidents (e.g. stuck deferred BUY, unknown order canceled) | no |
| `paper_receipt` | Per-decision receipt (high volume) | no |
| `order_recovered` | Valid kind, currently never emitted | no |

To forward a "no" kind, add it to `alerting.events` (unknown kinds fail config
load).

### Daily summary

One line per equity decision (format from `monitoring/daily_summary.py`; values illustrative):

```text
equity_leveraged_trend_v1 2026-09-29: QQQ 550.00 vs SMA200 500.00 (+10.00%) uptrend | QLD target 80.0% held 69.3% | pos QLD 700 | equity $101,000.00 (+1,000.00, +1.00% d/d) | submitted orders 1/1
```

Weights are fractions of account equity; "held" is the position at decision
time, before that decision's orders fill. Disable with
`alerting.daily_summary: false`.

### Intent watchdog

Catches a runtime that is alive but not trading (2026-09-25..29: four sessions
without a decision while every probe was green). Observability only — it never
gates or changes trading. Evaluated on the NYSE calendar (holidays and early
closes included).

| Code | Condition |
|---|---|
| `equity_decision_missing` | No decision persisted for the last NYSE session by close (never before 16:00 ET) + `equity_decision_grace_minutes` |
| `equity_position_under_target` | Latest decision targets a positive weight, but after the next open + `equity_fill_grace_minutes` the account holds less than `minimum_held_fraction` of it |

`intent_watchdog` keys: `enabled` (true), `equity_decision_grace_minutes`
(45), `equity_fill_grace_minutes` (30), `minimum_held_fraction` (0.5),
`fail_readiness` (true: active alarms make `/readyz` 503),
`evaluation_interval_seconds` (60).

## ops CLI

Read-only; works while the engine runs. Reads
`data/runtime/health_snapshot.json` (written every 30 s), the decision DB and
the event journal `data/runtime/observability.sqlite3` (30 days / 50,000
events).

```bash
parayabuck ops status [--json]            # overall health, trading state, per-check lines
parayabuck ops doctor [--json]            # exit 0 ok, 1 degraded, 2 critical
parayabuck ops events --since 2h [--kind K] [--severity S] [--decision ID] [--order ID]
parayabuck ops timeline --decision ID | --order ID
parayabuck ops explain [latest|DECISION_ID]   # why a decision did or did not trade
parayabuck ops bundle [--output PATH]     # sanitized diagnostic zip (no keys, no webhook URL)
```

Health checks: `runtime_heartbeat` (snapshot ≤ 90 s old), `workers`, `queue`
(age ≤ 30 s), `account`, `reconciliation` (CRITICAL after 5 min without
success), `signal_adjustment`, `market_halts`, `risk` (active safety incident
= BLOCKED), `execution`, `alerts` (webhook failures/drops), `observability`.
`UNKNOWN` means no evidence, not OK. Overall: CRITICAL > BLOCKED > DEGRADED >
UNKNOWN > OK. Trading state is separate: `ACTIVE`, `ENTRY_BLOCKED`,
`REBALANCE_BLOCKED`, `MARKET_HALTED`, `CIRCUIT_BREAKER`, `STOPPED`, `UNKNOWN`.
The watchdog is not an `ops status` check; read it from `/readyz` or
`ops events --kind intent_alarm`. (`ops status` still prints a `Universe` line;
there is no universe check any more, so it always reads `UNKNOWN`.)

`parayabuck doctor [--strict] [--paper] [--offline]` is the deployment
preflight (config, mode, live off, credentials, writability, SQLite
integrity/WAL, tracked `.env`, config digest; online it also checks Alpaca
PAPER reachability).

## State, backup and restore

| Path | What | If lost |
|---|---|---|
| `data/runtime/parayabuck.sqlite3` | Decisions, orders, fills, rebalance phases, risk state, safety incidents | **Critical** — restore from backup; broker positions survive but decision lineage does not |
| `data/runtime/paper_equity_peak.json` | Drawdown peak + epoch | Missing → bootstraps at 0; corrupt → fail-closed |
| `config/settings.yaml`, `.env` | Config (tracked in git), secrets | Restore from git / your secret store |
| `data/runtime/observability.sqlite3`, `health_snapshot.json`, `data/cache/` | Journal, snapshot, warmup bars | Regenerated |
| `data/backups/` | Backups | — |

`parayabuck backup` takes a consistent online snapshot (sqlite3 backup API,
safe while running) to `backup.path` (`data/backups`), verifies it with
`PRAGMA integrity_check`, and prunes files older than `backup.retention_days`
(7). The timer runs it every 6 h. Check one file with
`parayabuck backup --verify-only <file>`. Copy backups off the VM
occasionally; they live on the same disk.

Restore (never overwrite in place unverified):

```bash
sudo systemctl stop parayabuck
mkdir -p /tmp/pb-bad && cp -a data/runtime/parayabuck.sqlite3* /tmp/pb-bad/   # keep evidence
.venv/bin/parayabuck backup --verify-only data/backups/<file>.sqlite3
cp data/backups/<file>.sqlite3 data/runtime/parayabuck.sqlite3
rm -f data/runtime/parayabuck.sqlite3-wal data/runtime/parayabuck.sqlite3-shm
sudo systemctl start parayabuck
.venv/bin/parayabuck ops doctor && curl -s http://127.0.0.1:8000/readyz
```

Orders and fills after the backup are missing from the restored ledger. On
start the engine reconciles it against the broker and halts on drift it cannot
explain; review the broker account before any safety reset. Never delete the DB, safety incidents or the peak file to "reset".

## Incident reconciliation

Read-only P&L reconciliation of the ledger against Alpaca activity (transfers
are fetched automatically):

```bash
.venv/bin/python scripts/reconcile_paper_incident.py \
  --database data/runtime/parayabuck.sqlite3 --after 2026-09-01T00:00:00Z \
  --starting-equity 100000 --starting-unrealized-pnl 0
```

It reports realized/unrealized P&L, fees, transfers, expected vs actual
equity and the unexplained residual. `scripts/execution_quality_report.py`
prints fill latency, slippage and fees versus the backtest cost model (report
only; it never changes behaviour).

## PAPER rules

- Never change strategy parameters because a week lost or a month won. Any
  change to what or how the bot trades is a new, separately validated
  identity (`docs/validated_strategies.md`).
- 7/30/90-day reviews are reporting windows, not approval gates. PAPER checks
  live-data parity, runtime behaviour, execution quality and reliability, not
  short-term P&L.
- A config-digest change starts a new observation window; log it in
  `docs/OPERATIONS_LOG.md`.
- Real PAPER orders outside the engine need an explicit opt-in:
  `scripts/paper_smoke.py` requires `PARAYABUCK_ALLOW_PAPER_SMOKE=YES`,
  paper mode, and live not enabled. Do not run it while the VM engine runs.
