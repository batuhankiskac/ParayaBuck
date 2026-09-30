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


## One runner per account

The PAPER Alpaca account is shared between the VM and every other machine
that has the credentials. Trading therefore needs an explicit per-machine
designation: `PARAYABUCK_TRADING_HOST=1`, set only by the systemd unit on the
VM (`deploy/systemd/parayabuck.service`).

Without it `parayabuck web`, a bare `parayabuck` and `docker compose up` still
serve the console, settings and backtests, but the engine refuses to start —
auto-start logs the refusal and skips, a manual start returns an HTTP 400
explaining that the machine is not the designated trading host. The state
stays `stopped`; nothing is submitted to Alpaca. Only `1`, `true`, `yes` and
`on` count as the designation — any other value (including `0` or `no`) keeps
trading refused.

Verify on the VM with:

```bash
systemctl show parayabuck -p Environment | grep PARAYABUCK_TRADING_HOST
curl -s http://127.0.0.1:8000/api/status | python3 -m json.tool | grep engine_state
```

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
`engine_error` carries the last engine start/stop error and
`start_retry_attempt` the number of auto-start retries so far, so an external
probe can tell a dead engine from one that is still retrying.

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
| `runtime_start_failed` | Engine did not start: missing credentials, an unimportable runtime, a blocked startup (tracked-secret scan, live arming, config), a permanent startup error, a manual transient failure, N auto-start retries, or an out-of-process probe that found `/readyz` not ready | yes |
| `backup_failed` | A backup could not be written or verified, or the newest backup is older than 12 h (backup timer stopped) | yes |
| `daily_summary` | After each persisted equity decision | yes |
| `intent_alarm` / `intent_recovered` | Watchdog code becomes active / clears | yes |
| `order_rejected` | Broker rejected a portfolio order | yes |
| `ambiguous_order` | Order outcome unknown; dependent BUYs blocked | yes |
| `reconciliation_failure` / `reconciliation_recovered` | Reconciliation failed / recovered | yes |
| `risk_incident` | Daily-loss latch (SELL-only), or crypto found in the account at startup | yes |
| `drawdown_halt` | Drawdown limit breached (or restored at startup) | yes |
| `risk_epoch_reset` | Operator reset the drawdown baseline | yes |
| `circuit_breaker` | Breaker engaged (ownership drift, worker restart limit, queue overflow, persisted incident restored) | yes |
| `data_provider_unavailable` | Warning-level data-provider outage (renamed from `scanner_refresh_failure`, which config load now rejects) | yes |
| `signal_adjustment_failure` / `signal_adjustment_recovery` | Split-basis verification failed / recovered (equity entries halted meanwhile) | yes |
| `paper_incident` | Specific incidents (e.g. stuck deferred BUY, unknown order canceled) | yes |
| `signal_source_mismatch` | IEX and SIP daily bars disagree on the trend (position held), their closes diverge, or the SIP bar is missing | yes |
| `paper_receipt` | Per-decision receipt (high volume) | no |

To forward a "no" kind, add it to `alerting.events` (unknown kinds fail config
load).

### Engine start failures

A start that never completes is the one failure systemd cannot see: the web
process keeps serving, so `/healthz` stays `200` and `Restart=on-failure`
never fires, and every alarm that could report it lives in the runtime that
failed to start. `/readyz` answers `503` (the engine is in `error`, or it is
stuck at `starting` with a growing `start_retry_attempt`) and
`runtime_start_failed` is emitted from every start-failure path:

| `reason` | Meaning | Next step |
|---|---|---|
| `credentials_missing` | No API key/secret | Fix `.env`; the engine is not trading |
| `runtime_import_failed` | `parayabuck.runtime` is not importable | The deploy is broken; reinstall |
| `startup_rejected` | A startup guard refused the start (tracked-secret scan, live arming, config) | Read the message; the guard stays in place |
| `startup_error` | Permanent startup error (Alpaca 401, SQLite error, catch-up exception) | Fix the cause, then start the engine |
| `transient_no_retry` | A manual start hit a network-class failure | Retry the start |
| `transient_retrying` | The auto-start loop is retrying and the attempts are not clearing | Fix the network, or stop and start the engine manually |
| `readyz_probe` | `parayabuck ops watchdog` found the deployment not ready | Read `engine_state`/`engine_error` in the message |

Transient auto-start retries keep the state at `starting` and back off
5/15/30/60 s indefinitely; the alert fires from the third attempt and then
every tenth, with `attempts`. Run `parayabuck ops watchdog` by hand to get
the same verdict (see ops CLI below).

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
parayabuck ops rebase-ownership SYMBOL --broker-qty QTY [--account-id ID] [--note TEXT]
    # adopt the broker's share count for one symbol; dry run unless --confirm
parayabuck ops watchdog [--json]       # dead-man probe: /readyz + backup freshness; exit 1 + alert on failure
```

Health checks: `runtime_heartbeat` (snapshot ≤ 90 s old), `workers`, `queue`
(age ≤ 30 s), `account`, `reconciliation` (CRITICAL after 5 min without
success), `signal_adjustment`, `market_halts`, `risk` (active safety incident
= BLOCKED), `execution`, `alerts` (webhook failures/drops), `observability`.
`UNKNOWN` means no evidence, not OK. Overall: CRITICAL > BLOCKED > DEGRADED >
UNKNOWN > OK. Trading state is separate: `ACTIVE`, `ENTRY_BLOCKED`,
`REBALANCE_BLOCKED`, `MARKET_HALTED`, `CIRCUIT_BREAKER`, `STOPPED`, `UNKNOWN`.
The watchdog and the signal cross-check are not health checks (they do not
change the overall status), but the `ops status` text view prints them from the
health snapshot: `Watchdog` (`OK`, `ALARM` with the active codes, `PENDING`
before the first pass, `DISABLED`) and `Cross-check` (last session's status,
`PENDING` while waiting for the SIP bar, `NONE` before the first session).
`/readyz` and `ops events --kind intent_alarm` carry the same information.

`parayabuck doctor [--strict] [--paper] [--offline]` is the deployment
preflight (config, mode, live off, credentials, writability, SQLite
integrity/WAL, tracked `.env`, config digest; online it also checks Alpaca
PAPER reachability).

It also reports backup freshness: the age of the newest snapshot in
`backup.path`. That row is a `WARN`, never a blocker — the service runs
`doctor --strict` as `ExecStartPre`, and a stale backup must not keep the
engine from starting.

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
`PRAGMA integrity_check`, and prunes *automatic* backups older than
`backup.retention_days` (7). The timer runs it every 6 h. Check one file with
`parayabuck backup --verify-only <file>`. Copy backups off the VM
occasionally; they live on the same disk.

Failures are never silent: `parayabuck backup` emits `backup_failed` (and
exits non-zero) when a snapshot cannot be written or verified, and
`parayabuck ops watchdog` — run every 15 min by
`parayabuck-watchdog.timer` — emits the same kind when the newest backup is
older than 12 h, i.e. when the timer itself has stopped working.

`parayabuck backup --label <name>` publishes the snapshot as
`parayabuck-keep-<name>-<stamp>.sqlite3`, outside the retention prune glob:
the pre-deploy/rollback snapshot the runbook tells you to keep is never
auto-pruned, and it stays a valid restore candidate. Use it before every
deploy and rollback; delete it yourself when the release is stable.

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

## Splits and foreign sells

Ownership is net fills, so a corporate action — or another consumer of the
shared Alpaca key — leaves the repository claiming a share count the broker
disagrees with. While the two differ every decision (exits included) is
dropped and no safety reset can succeed, because a clean reconcile is
impossible. The runtime handles the cases it can prove and halts with an
actionable incident for the rest:

- **Split** (`2`, `3`, `4`, `5`, `1/2`, `1/3`, `1/4`, `1/5`, `1/8`, `1/10`
  for-1 and their reciprocals) corroborated by the broker's average entry
  price divided by the same ratio → ownership is rebased on the broker count
  and an audited `split_rebase` adjustment is written.
- **Fewer shares at the broker** than the repository owns (a partial or full
  foreign sell) → ownership is reduced to the broker count and an audited
  `foreign_sell_reduction` adjustment is written. Adopting a smaller count can
  never over-claim a share.
- Anything else (a non-standard ratio, a share count with an unchanged cost
  basis that would mean claiming shares we never bought) → the runtime halts.
  Verify the real position on the Alpaca dashboard and adopt it explicitly:

```bash
# 1. read the position off the broker, then dry run
.venv/bin/parayabuck ops rebase-ownership QLD --broker-qty 197.6 \
  --note "QLD 2:1 split 2025-11-20 verified on the broker"
# 2. apply it (refused while any portfolio order is still open)
.venv/bin/parayabuck ops rebase-ownership QLD --broker-qty 197.6 \
  --note "QLD 2:1 split 2025-11-20 verified on the broker" --confirm
# 3. reconciliation is now clean: reset the safety lock from the dashboard
```

Every rebase writes one row to `portfolio_ownership_adjustments` and appears
as a reconciliation anomaly (`ops status` → `reconciliation_anomalies`,
dashboard → ownership). The command never flattens or cancels anything at the
broker.

This applies only to shares the bot owns. Inventory nobody claims is treated
the other way round: crypto, and equity outside the strategy universe, is
never ownership drift (`docs/operator_runbook.md`, "Foreign inventory in the
account"). On a symbol the strategy trades, an unowned QLD holding stays
`BROKER_STALE` drift and halts, because only the bot may hold or sell it.

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
  paper mode, and live not enabled. It refuses itself while the engine
  heartbeat is live and inside 15:45-16:30 ET; stop the service first
  (`docs/deployment.md`, Pre-deploy checks).
