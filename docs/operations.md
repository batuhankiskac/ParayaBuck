# Operations

How the running PAPER bot behaves and how to watch it. Deployment is in
`docs/deployment.md`; symptom playbooks are in
`docs/operator_runbook.md`. All commands run on the VM, in the checkout.

## Daily cycle

| When (ET) | What happens |
|---|---|
| Regular session | 5m IEX bars stream for SPY, QQQ, QLD; the account is polled every 15 s and reconciled every 60 s |
| ≥ 16:00 | The session's daily bar is flushed; VT35 decides on completed bars only (QQQ close vs 200-session SMA, QLD 20-session vol). A daily bar for a session still trading is dropped from the signal history (`Ignored unfinished equity daily bar` in the log) |
| Right after the decision | Decision persisted; SELLs, then BUYs, submitted as DAY market orders sized against a fresh account snapshot; one `daily_summary` alert. A decision blocked by a recoverable gate is re-queued instead (`decision_deferred`) and re-decided every 60 s until 40 min after the close |
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

Alerts are local only (2026-10-06: the VM sends nothing to the operator).
Every alert is written to the service log and to the operational event
journal; nothing is POSTed anywhere. Read them on the VM:

```bash
parayabuck ops events --since 24h [--kind K] [--severity WARNING]   # the journal
parayabuck ops status                       # health checks + emitted/throttled alert counters
sudo journalctl -u parayabuck -g 'ALERT ' --since today
```

or on the dashboard through the SSH tunnel. Repeats of the same kind (per
incident/market/order, and per `reason`/`state`, where relevant) are throttled
for 60 s; a throttled repeat is logged at INFO with its running count. Secrets
are redacted from the log line and the journal row. Emission never blocks or
fails trading, and no health check depends on alert delivery.

| Kind | Fires when | Severity |
|---|---|---|
| `runtime_started` | Engine start completed | info |
| `runtime_stopped_unexpected` | Stop from a halted/error state or with a breaker active | warning |
| `runtime_start_failed` | Engine did not start: missing credentials, an unimportable runtime, a blocked startup (tracked-secret scan, live arming, config), a permanent startup error, a manual transient failure, or N auto-start retries | warning |
| `backup_failed` | `parayabuck backup` could not load the configuration, or write or verify a snapshot | warning |
| `daily_summary` | After each persisted equity decision | info |
| `intent_alarm` / `intent_recovered` | Watchdog code becomes active / clears | warning / info |
| `order_rejected` | Broker rejected a portfolio order | warning |
| `ambiguous_order` | Order outcome unknown; dependent BUYs blocked | warning |
| `reconciliation_failure` / `reconciliation_recovered` | Reconciliation failed / recovered | warning / info |
| `risk_incident` | Daily-loss latch (SELL-only), or crypto found in the account at startup | warning |
| `drawdown_halt` | Drawdown limit breached (or restored at startup) | warning |
| `risk_epoch_reset` | Operator reset the drawdown baseline | info |
| `circuit_breaker` | Breaker engaged (ownership drift, worker restart limit, queue overflow, persisted incident restored) | warning |
| `data_provider_unavailable` | Warning-level data-provider outage (formerly `scanner_refresh_failure`) | warning |
| `signal_adjustment_failure` / `signal_adjustment_recovery` | Split-basis verification failed / recovered (equity entries halted meanwhile) | info |
| `paper_incident` | Specific incidents (e.g. stuck deferred BUY, unknown order canceled) | warning |
| `signal_source_mismatch` | IEX and SIP daily bars disagree on the trend (position held), their closes diverge, or the SIP bar is missing | warning |
| `decision_deferred` | A session-close decision was re-queued because a recoverable gate (reconcile breaker, stale account snapshot, invalidated snapshot lease, transient broker fetch) was active; one per session, plus one when the retry window expires without a decision | warning |
| `market_data_degraded` | The effective historical feed stopped being the configured one (SIP entitlement downgrade to IEX), and again when it recovers | warning |
| `session_close_bar_incomplete` | A session closed with stream minutes missing: the incomplete daily bar was withheld and the SIP daily bar is fetched for the decision | warning |
| `reconciliation_unexpected_fill_side` | A broker FILL activity carried a side outside Alpaca's vocabulary; it is skipped and the cursor advanced, never fatal | warning |
| `paper_receipt` | Per-decision receipt (high volume) | info |

`alerting.enabled`, `alerting.webhook_url` and `alerting.events` were removed
with outbound delivery and are rejected on load with a pointer to this
section; `alerting.daily_summary` is the only alerting key.

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

Transient auto-start retries keep the state at `starting` and back off
5/15/30/60 s indefinitely; the alert fires from the third attempt and then
every tenth, with `attempts`. `curl -s http://127.0.0.1:8000/readyz` gives the
same verdict by hand (`engine_state`, `engine_error`, `start_retry_attempt`).

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
| `equity_decision_missing` | No decision persisted for the last NYSE session by ITS close (16:00 ET, 13:00 ET on an early close) + `equity_decision_grace_minutes` |
| `equity_position_under_target` | Latest decision targets a positive weight, but after the next open + `equity_fill_grace_minutes` the account holds less than `minimum_held_fraction` of it |
| `equity_position_over_target` | The account still holds more than the decision targeted after the fill window — the everyday case is a cash target whose DAY SELL never filled. Residual dust under 1 % of equity is tolerated |
| `equity_signal_source_hold` | The decision deliberately held the position because the IEX stream bar and the SIP daily bar disagree on the trend (see `signal_cross_check:`) |

`intent_watchdog` keys: `enabled` (true), `equity_decision_grace_minutes`
(45), `equity_fill_grace_minutes` (30), `minimum_held_fraction` (0.5),
`fail_readiness` (true: active alarms make `/readyz` 503),
`evaluation_interval_seconds` (60).

### Decision retry (deferred session-close decisions)

A session-close decision blocked by a **recoverable** condition is re-queued,
not dropped: an active reconciliation-owned breaker, `_reconcile_failed`, the
readiness gate, an account-snapshot lease invalidated by a concurrent fill, or
a network-class account fetch failure. The batch is re-decided every
`engine.decision_retry_interval_seconds` (60) until
`engine.decision_retry_window_minutes` (40) after the **session's own close**
(16:00 ET, or 13:00 ET on an early close — the deadline follows the NYSE
calendar, so an early-close session stops retrying at 13:40 ET rather than
running three hours into the next session). The window sits inside the intent
watchdog's 45-minute decision grace, so a deferred decision either lands or
the watchdog still reports it missing, and it outlives the second-source
deferral it must survive (`sip_embargo_minutes` 15 + `retry_window_minutes`
10 = 25 min after the close). The first deferral of a session emits
`decision_deferred`; retries only log `Decision deferred for retry`.
`retry_window_ended` marks a session that was genuinely lost.

A restart does not lose the session either: if the newest completed session is
a scheduled boundary with no persisted decision, startup decides it
(`startup_catch_up_status: recovered_session:<status>`) — including while a
position is held, since a trend exit is exactly the decision that must not be
skipped. The recovered decision runs on the last **final** daily bar: a
provider bar is only eligible once its session closed more than
`signal_cross_check.sip_embargo_minutes` ago, so a restart inside the embargo
(or on an early-close day, close + 15 min) decides the *previous* session
instead. That decision also runs without the second-source cross-check, so
avoid restarting from 15:55 ET to close + 15 min — 16:30 ET on a full session,
13:30 ET on an early close (`docs/deployment.md`).

Reconciliation is on the same footing: a single Alpaca timeout during the
60 s reconcile cycle blocks entries (fail-closed) but raises no breaker and
persists no incident until `engine.reconcile_failure_threshold` (3)
consecutive transient failures. Drift, ownership and protection failures
still halt immediately. A definite broker rejection (4xx the recovery lookup
proved never reached the broker) books the row `rejected`, alerts
`order_rejected` and keeps the runtime running.

### Market data, the session calendar and the decision bar

The NYSE schedule (pandas-market-calendars) is the single source of session
truth — `parayabuck.markets.nyse_calendar` — and nothing uses weekday
arithmetic at runtime:

* **Session minutes.** Only minutes inside the real session are ingested or
  fetched. On an early close (13:00 ET) the 13:00–16:00 IEX post-market
  prints are dropped, so they can never become the decision close.
* **Session close.** The daily decision bar is flushed at the session's
  actual close plus a 30 s close grace
  (`runtime._SESSION_CLOSE_FLUSH_GRACE_SECONDS`), not at a fixed 16:00 ET.
  On an early-close day the decision is taken at 13:00 ET, and the intent
  watchdog expects it there.
* **Completeness.** A session-close bar whose last expected minute never
  arrived is withheld, `session_close_bar_incomplete` fires, and the
  provider's SIP daily bar is fetched for that session from
  close + `signal_cross_check.sip_embargo_minutes` (bounded attempts). A
  session whose fallback never lands has no decision bar and places no
  order; the intent watchdog raises `equity_decision_missing`.
* **Readiness.** Stream liveness and `/readyz` follow the same calendar, so
  a holiday or a finished early close is never reported as degraded.
* **Provider embargo.** A provider daily bar is final only
  `sip_embargo_minutes` after the close (SIP history is clamped to
  now − embargo), so a deploy restart between the close and the end of the
  embargo decides on the last complete session instead of a partial bar.
  Stream-built bars are final at the real close and are not affected.
* **SIP → IEX downgrade.** Only a genuine entitlement error (401/403 or an
  explicit entitlement body, read from the response — never from the request
  URL) downgrades historical bars to IEX; a DNS blip or a timeout leaves the
  configured feed alone. The downgrade is bounded: the next request at or
  after 6 h re-probes SIP and restores it. `health.market_data` carries
  `configured`, `effective`, `quote_effective`, `sip_fallback_active`,
  `sip_fallback_since` and any pending `session_close_fallback`.
* **Outage honesty.** The split-basis verifier treats a cache-served
  response as unverified, so a provider outage fails closed instead of
  emitting a false recovery.

## ops CLI

Read-only; works while the engine runs. Reads
`data/runtime/health_snapshot.json` (written every 30 s), the decision DB and
the event journal (30 days / 50,000 events). The journal path, retention,
maximum events and queue size come from the `observability:` block in
`config/settings.yaml`; the engine applies them at start-up, so a custom
`observability.journal_path` is the file both the engine writes and
`ops events` reads. Default: `data/runtime/observability.sqlite3`.

```bash
parayabuck ops status [--json]            # overall health, trading state, per-check lines
parayabuck ops doctor [--json]            # exit 0 ok, 1 degraded, 2 critical
parayabuck ops events --since 2h [--kind K] [--severity S] [--decision ID] [--order ID]
parayabuck ops timeline --decision ID | --order ID
parayabuck ops explain [latest|DECISION_ID]   # why a decision did or did not trade
parayabuck ops bundle [--output PATH]     # sanitized diagnostic zip (no keys or secrets)
parayabuck ops rebase-ownership SYMBOL --broker-qty QTY [--account-id ID] [--note TEXT]
    # adopt the broker's share count for one symbol; dry run unless --confirm
```

Health checks: `runtime_heartbeat` (snapshot ≤ 90 s old), `workers`, `queue`
(age ≤ 30 s), `account`, `reconciliation` (CRITICAL after 5 min without
success), `signal_adjustment`, `market_halts`, `risk` (active safety incident
= BLOCKED), `execution`, `intent_watchdog` (CRITICAL on
`equity_decision_missing`, otherwise DEGRADED), `signal_cross_check`,
`observability`. `UNKNOWN` means no evidence, not OK. Overall:
CRITICAL > BLOCKED > DEGRADED > UNKNOWN > OK. Trading state is separate:
`ACTIVE`, `ENTRY_BLOCKED`, `REBALANCE_BLOCKED`, `MARKET_HALTED`,
`CIRCUIT_BREAKER`, `STOPPED`, `UNKNOWN`. An active watchdog alarm never
changes the trading state — it only colours the overall status and the doctor
exit code. `ops status` additionally prints the watchdog and the cross-check in
full text: `Watchdog` (`OK`, `ALARM` with the active codes, `PENDING`
before the first pass, `DISABLED`) and `Cross-check` (last session's status,
`PENDING` while waiting for the SIP bar, `NONE` before the first session).
`/readyz` and `ops events --kind intent_alarm` carry the same information.
The dashboard `Sağlık kontrolleri` badge shows the active alarm codes instead
of `Sağlıklı`, and `/api/ops/status` returns the same checks.

`signal_cross_check` reports the last **completed** comparison; a session still
waiting for its SIP daily bar is appended to the summary rather than making the
check UNKNOWN, so the post-close embargo window does not redden a healthy run.
Only a runtime that has never finished a comparison is UNKNOWN.

`observability` (the event journal) and the alert counters `ops status`
prints are read from the copy the **engine** persists into the health
snapshot, never from the counters of the process asking: a CLI or web process
has written nothing of its own. No persisted copy at all is `UNKNOWN`, not OK.
`observability` is DEGRADED only while the journal is failing now — its
newest write failure or drop is more recent than its last successful write. A
burst it has since written past reads `OK (recovered; …)` instead of pinning
DEGRADED until the next restart. There is no `alerts` check: alerts are local,
so there is no delivery to verify.

`parayabuck doctor [--strict] [--paper] [--offline]` is the deployment
preflight (config, mode, live off, credentials, writability, SQLite
integrity/WAL, tracked `.env`, config digest; online it also checks Alpaca
PAPER reachability).

It also reports backup freshness: the age of the newest snapshot in
`backup.path`. That row is a `WARN`, never a blocker — the service runs
`doctor --strict` as `ExecStartPre`, and a stale backup must not keep the
engine from starting.

## Offline backtests (research, never runtime)

`parayabuck backtest` and `parayabuck optimize` are offline research runs.
They write their report, CSVs and universe snapshot under
`backtest.output_dir` (`reports/`) and **never** touch
`data/runtime/parayabuck.sqlite3`: a research row must not appear in the live
decision ledger or in its backups (`run_product_backtest` has no
persistence path: callers that need rows use `report.write_csvs`/artifacts).

`report.summary["data_feed"]` states the feed the bars actually came from: the
provider's effective feed for a download (`iex` when a configured SIP request
hit an entitlement downgrade) and `unknown` for `--data <csv>`, with a
`data_feed_note` caveat. A CSV run can never claim the SIP tape.

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

Failures are visible on the VM: `parayabuck backup` logs an ERROR, records
`backup_failed` in the journal and exits non-zero (the unit shows `failed` in
`systemctl status parayabuck-backup`) when the configuration does not load or
a snapshot cannot be written or verified; `parayabuck doctor` prints a
`backup stale` row when the newest backup is older than 12 h, i.e. when the
timer itself has stopped working.

`parayabuck backup --label <name>` publishes the snapshot as
`parayabuck-keep-<name>-<stamp>.sqlite3`, outside the retention prune glob:
the pre-deploy/rollback snapshot the runbook tells you to keep is never
auto-pruned, and it stays a valid restore candidate. Use it before every
deploy and rollback; delete it yourself when the release is stable.

Restore (never overwrite in place unverified). The units run with
`UMask=0077`, so the ledger and the backups are mode `0600` and owned by the
service user: every step runs as that user, and the restored file is installed
with the same owner and mode, or the engine cannot open it.

```bash
sudo systemctl stop parayabuck
PB_USER=$(systemctl show -p User --value parayabuck)     # parayabuck on a template install
EVIDENCE=/var/tmp/pb-bad-$(date -u +%Y%m%dT%H%M%SZ)
sudo install -d -o "$PB_USER" -g "$PB_USER" -m 700 "$EVIDENCE"
sudo -u "$PB_USER" cp -a data/runtime/parayabuck.sqlite3* "$EVIDENCE"/   # keep evidence
sudo -u "$PB_USER" .venv/bin/parayabuck backup --verify-only data/backups/<file>.sqlite3
sudo install -o "$PB_USER" -g "$PB_USER" -m 600 data/backups/<file>.sqlite3 data/runtime/parayabuck.sqlite3
sudo -u "$PB_USER" rm -f data/runtime/parayabuck.sqlite3-wal data/runtime/parayabuck.sqlite3-shm
sudo systemctl start parayabuck
sudo -u "$PB_USER" .venv/bin/parayabuck ops doctor; curl -s http://127.0.0.1:8000/readyz
```

Orders and fills after the backup are missing from the restored ledger. On
start the engine reconciles it against the broker and halts on drift it cannot
explain; review the broker account before any safety reset. Never delete the DB, safety incidents or the peak file to "reset".

## Incident reconciliation

Read-only P&L reconciliation of the ledger against Alpaca activity. Deposits,
withdrawals, dividends (`DIV`/`JNLS`) and broker cash charges (`FEE`/`CFEE`)
are all fetched, because every one of them moves equity:

```bash
.venv/bin/python scripts/reconcile_paper_incident.py \
  --database data/runtime/parayabuck.sqlite3 --after 2026-09-01T00:00:00Z \
  --starting-equity 100000 --starting-unrealized-pnl 0
```

`--after` is the window. The broker history starts there, so the reconciler
seeds the opening position and its cost basis from the local `portfolio_fills`
at or before that instant: a normal QLD exit is measured against the entry
that funded it, not against zero. Everything is read from the current
`portfolio_orders`/`portfolio_fills` tables, so the bot's own orders, fills and
held symbols are recognised as its own; what is left over is genuine foreign
activity (`external_position_symbols` for holdings, `broker_orders_missing_locally`
for orders). Local orders older than the window are not reported as missing at
the broker — the download simply does not cover them.

The report gives realized/unrealized P&L, fill fees, cash fees, dividends,
transfers, expected vs actual equity and the unexplained residual. A residual
is a real finding only after those separate terms are read: a distribution or a
charge that was folded into "transfers" used to look like unexplained loss.

`scripts/execution_quality_report.py` prints fill latency, the decision-close
gap and fees against the modeled fees (report only; it never changes
behaviour). Its reference is `portfolio_orders.reference_price`, which is the
**decision close**, while market orders fill at the next session open: the
distribution it reports is an overnight gap, not execution slippage, and the
report deliberately publishes no `within_model`/`above_model`/`below_model`
verdict. The backtest-aligned comparison — next-open fills against the next
open, with modeled adverse cost and fees — is
`scripts/execution_shadow_report.py` (`paper_vs_zero_cost_open_bps`).

## PAPER soak audit

`scripts/audit_paper_soak.py` is the read-only, fail-closed gate set for a
soak window. It runs against the production identity: equity-only VT35, a
decision on **every NYSE session close** (early closes and holidays from the
exchange calendar, not a fixed weekday heuristic), the configured 0.98 gross
cap and 0.02 cash buffer for BUY admission headroom, and one startup catch-up
decision **only when the deployment is genuinely cold**.

```bash
.venv/bin/python scripts/audit_paper_soak.py \
  --db data/runtime/parayabuck.sqlite3 --journal data/runtime/operational.sqlite3 \
  --start 2026-09-03T12:00:00Z
```

Decisions are labelled 00:00 ET but decided at that session's NYSE close, so
`--start`/`--end` bound the **decision moment**. Every order, fill, admission,
rebalance, receipt and decision summary is then linked to its decision by id,
never by wall clock: a `--start` inside a session (the usual
`--start <restart UTC>`) no longer splits a decision from its own orders.
`counts.startup_catch_up_required` and `counts.expected_nyse_sessions` say
whether S9 had anything to require and how many sessions were expected; a
resolved reconciliation failure (a later `reconciliation_succeeded`) does not
keep failing S8.

It also writes an anonymised replay fixture for offline reproduction. Both
outputs go to the git-ignored `reports/paper_incident/` tree by default
(`--report`, `--fixture`); keep broker-linked history out of `tests/`, where a
later `git add -A` would commit it.

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

## Tests and the isolation gate

The test suite must never touch the trading account or the operator's state,
and the session enforces that rather than trusting every test:

- `tests/conftest.py` strips `ALPACA_*`/`PARAYABUCK_*` from the environment,
  points `ENV_PATH` at a file that does not exist, refuses to load any dotenv
  file inside the repository (so the repo `.env` — the shared PAPER key — is
  never read), and blocks every non-loopback socket for the whole session.
  A test therefore cannot log in to Alpaca even if it builds a real broker.
- The same fixture fails a test that opens or changes a file under
  `data/runtime/`, `data/cache/` or `logs/`, and redirects the peak sidecar,
  journal, snapshot, decision DB and receipt defaults into `tmp_path`.
- `scripts/check_production_isolation.py` is the CI gate around the suite: it
  records every file under `data/runtime`, `data/cache`, `data/backups` and
  `logs` before `pytest -q` and verifies the tree is byte-identical (and gained
  no files) afterwards.
- Process singletons (the alert throttle state, the Web backtest manager) and
  the process environment (`CONFIG_PATH`, which `parayabuck web --config`
  exports for the process it serves) are restored around every test, so the
  suite passes in any order.

Run it the way CI does: `PYTHONPATH=src ./.venv/bin/python -m pytest -q -o pythonpath=src`.

## PAPER rules

- Never change strategy parameters because a week lost or a month won. Any
  change to what or how the bot trades is a new, separately validated
  identity (`docs/validated_strategies.md`).
- 7/30/90-day reviews are reporting windows, not approval gates. PAPER checks
  live-data parity, runtime behaviour, execution quality and reliability, not
  short-term P&L.
- A config-digest change starts a new observation window; log it in
  `docs/OPERATIONS_LOG.md`.
- Nothing places PAPER orders outside the engine: the old AAPL smoke script
  (`scripts/paper_smoke.py`) was deleted on 2026-10-06 because it traded the
  shared account beside the VM engine.
