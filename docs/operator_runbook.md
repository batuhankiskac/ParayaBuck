# Operator runbook

Symptom-by-symptom playbooks for the PAPER bot on the VM. Diagnostics are
read-only. **Do not bypass risk controls, edit the DB by hand, or start a
second engine.** When unsure, collect a bundle first.

On the VM the engine runs because the unit sets
`Environment=PARAYABUCK_TRADING_HOST=1`; on any other machine that variable is
absent and every engine start is refused. If a start fails with *"this
machine is not designated as the trading host"*, the console and backtests
still work — restore the unit line (`systemctl cat parayabuck | grep
PARAYABUCK_TRADING_HOST`) and restart the service. Never add the variable to a
laptop `.env` to "make it work": that is what lets a second runner onto the
shared account.

```bash
parayabuck ops status
parayabuck ops doctor                 # exit 0 ok, 1 degraded, 2 critical
parayabuck ops events --since 2h
parayabuck ops bundle --output /tmp/pb_bundle.zip   # sanitized; share status.json + recent_events.jsonl first
curl -s http://127.0.0.1:8000/readyz | python3 -m json.tool
sudo journalctl -u parayabuck --since "1 hour ago" --no-pager | tail -n 100
```

Alerts are local only: nothing is sent off the VM. Every alert is in the
journal (`ops events`, filter with `--kind`/`--severity`) and in the service
log (`journalctl -u parayabuck -g 'ALERT '`); `ops status` prints how many the
engine emitted and how many repeats it throttled.

Safety resets are operator actions in the dashboard (or
`POST /api/engine/reset-safety-lock`, `POST /api/safety/risk-epoch`). A reset
succeeds only after the queue drains, data gaps clear and reconciliation
passes, and it clears the reason it verified (plus the halt rows this start
already re-armed, see "Circuit breaker active").

## Intent alarm (`/readyz` 503 with `degraded_reasons`)

An active alarm is now on every operator surface, not only `/readyz`:
`ops status` (`Watchdog ALARM`), `ops doctor` (exit 2 for
`equity_decision_missing`, exit 1 otherwise), the dashboard `Sağlık
kontrolleri` badge (which lists the codes instead of `Sağlıklı`) and
`/api/ops/status`. `equity_decision_missing` alone is CRITICAL; the rest are
DEGRADED. None of them gate trading.


- `equity_decision_missing` — no decision for the last NYSE session by ITS
  close + 45 min. The close is 16:00 ET, or 13:00 ET on an early close
  (2026-11-27, 2026-12-24); a holiday is not a session, so nothing is owed
  over one. Check `ops explain latest`, `ops status` (Workers, Adjustment,
  Market halts) and the journal for `Ignored unfinished equity daily bar`,
  warmup, stream or signal-adjustment errors.
- `equity_position_under_target` — the target is positive but QLD held is
  below half of it 30 min after the next NYSE session open. Check open
  orders/fills
  (`ops timeline --decision <id>`), `order_rejected` / `ambiguous_order`
  events, entry latches and the broker account.
- `equity_position_over_target` — 30 min after the next NYSE session open the
  account still holds more than the decision targeted, beyond
  max(1 % of equity, 10 % of the target) — the slack covers exit dust and the
  ordinary price drift of a held position between decisions. In practice a cash target whose DAY SELL
  never filled, leaving the previous position; check the order in
  `ops timeline --decision <id>` (expired/cancelled?), `order_rejected` /
  `order_expired` events and the broker account. The window is the next
  *session* open, so over a weekend/holiday or an early close it does not
  open early.
- `equity_signal_source_hold` — the position was deliberately held because the
  IEX stream bar and the SIP daily bar disagree on the trend. Not a fault;
  confirm the `Cross-check` line in `ops status` before acting.

Alarms clear by themselves (`intent_recovered`) once reality matches intent.

## Bot is not trading

`ops explain latest` names the reason:

| Reason | Meaning | Action |
|---|---|---|
| `NO_TARGET_CHANGE` | Target unchanged | Normal |
| `UNKNOWN` | No evidence names a cause (summary event pruned, or an unrecognised reason) | Not an all-clear: check `ops timeline --decision <id>`, `ops status` and the service log |
| `REBALANCE_BAND` / `MIN_NOTIONAL` | Drift under 0.5% or order under $10 | Normal; do not lower the thresholds to force trades |
| `ENTRY_LATCH` | Daily-loss or drawdown latch, or `signal_adjustment_unverified` | Wait for recovery; drawdown needs a risk-epoch reset after review |
| `ACCOUNT_STALE` | Account snapshot > 30 s old | See below |
| `SELL_PHASE_PENDING` | Previous rebalance still selling | `ops timeline --decision <id>` |
| `AMBIGUOUS_ORDER` / `REBALANCE_BLOCKED` | Broker outcome unknown | Manual, see below |

## Engine did not start (`runtime_start_failed`, `/readyz` 503 with `engine_state=error` or `starting`)

The console is up (`/healthz` 200) but nothing is trading, and systemd did
not restart anything because the process never died. `curl -s
127.0.0.1:8000/readyz` gives `engine_state` and `engine_error`; the alert
fields give `reason` and `attempts`.

- `credentials_missing` / `startup_rejected` — a startup guard refused the
  start (missing keys, tracked `.env` in git, live not armed, config error).
  Fix the cause; do not bypass the guard. `Auto-start blocked` is deliberate.
- `startup_error` — permanent startup error, e.g. Alpaca 401 after a key
  rotation, a SQLite error in `begin_session`, a corrupt peak sidecar, a
  catch-up exception. Fix it, then start the engine from the dashboard.
- `transient_no_retry` / `transient_retrying` — network-class failure. The
  auto-start loop retries with 5/15/30/60 s backoff and the state stays
  `starting`; `start_retry_attempt` in `/readyz` shows how long it has been
  going. Fix connectivity, or stop the engine and start it manually.

`ops status` says nothing about a start failure on its own: every alarm it
aggregates lives in the runtime that failed to start. Use `/readyz` first.

## Runtime appears dead

`ops status` shows heartbeat stale (> 90 s) or `UNKNOWN`.

- Process gone: `systemctl status parayabuck`; systemd restarts on failure.
  `sudo systemctl restart parayabuck` if needed.
- Process alive, snapshot stale: the snapshot writer failed; check the
  `observability` check and the journal.
- `stop_failed`: workers did not stop; a process restart is required.

- Stop pressed while the engine was still starting: startup aborts before the
  restart-rebalance resume and the startup catch-up, so no order is submitted
  and the state settles at `stopped`. A start request reporting that the
  startup was cancelled is that outcome, not a failure to retry.

Do not delete `health_snapshot.json` to "fix" status.

## Account stale

Snapshot older than `engine.maximum_account_age_for_entry_seconds` (30 s):
the **whole** session-close decision is paused — BUYs *and* target SELLs — because
position quantity, cash and exposure can no longer size any order safely.
Broker-native protective exits (resting stops) keep working. Events
`account_became_stale` → `account_recovered`. The paused decision is re-queued
and retried every 60 s (see "Decision deferred"), so a short outage costs
latency, not the session. If stale for > 30 min with `reconciliation_failure`,
check Alpaca reachability (`parayabuck doctor` without `--offline`), then
restart.

## Decision deferred (`decision_deferred`)

A session-close decision blocked by a *recoverable* gate — a reconciliation
breaker, a stale account snapshot, a snapshot lease invalidated by a
concurrent fill, or a transient broker timeout — is **not** dropped. It is
re-queued and re-decided every `engine.decision_retry_interval_seconds` (60 s)
until `engine.decision_retry_window_minutes` (40) after the **session's own
close**, which is inside the intent watchdog's 45-minute decision grace. The
close comes from the NYSE calendar: 16:00 ET on a full session, 13:00 ET on an
early close, so an early-close day gives up at 13:40 ET instead of retrying
three hours past the close. One alert is emitted per deferred session, never
one per retry; the log line is `Decision deferred for retry`.

- A later `decision_deferred` with `retry_window_ended` means the window
  expired and the session was **not** decided. `equity_decision_missing` at the
  session close + 45 min (16:45 ET; 13:45 ET on an early close) is the
  authoritative alarm; treat it as a real missed session.
- A halted runtime (`CIRCUIT_BREAKER`) blocks every decision, retry
  included: the deferred batch is retried until the deadline, so a reset
  inside the window still lands the session. Nothing trades while halted —
  **not even sells** (a halt is a state where the account is not trusted).
- `engine.reconcile_failure_threshold` (default 3) consecutive *transient*
  reconciliation failures are required before the reconciliation breaker and
  its persisted incident are raised. One Alpaca timeout only blocks entries
  (fail-closed) and is retried on the next 60 s reconcile; a clean reconcile
  clears it with no operator action. Drift, ownership and protection failures
  still halt immediately.

## Reconciliation failing

`reconcile` check CRITICAL after 5 min without success. A single
`reconciliation_failure` alert from a broker timeout is expected and
self-clearing (see above); a persisted incident and a `circuit_breaker`
(`reconciliation_failed`) mean the threshold was reached or reconciliation
reported drift. Entries are blocked from the first failure. Compare broker
positions/orders (app.alpaca.markets/paper) with `ops status` and the bundle,
then reset from the dashboard once a clean reconcile has run. Never clear
`safety_incidents` by hand, and never flatten broker positions to "fix"
drift.

`reconciliation_unexpected_fill_side` is NOT this failure: the reconciler
skipped broker FILL activities whose side is outside Alpaca's vocabulary
(`buy`, `sell`, `buy_minus`, `sell_plus`, `sell_short`,
`sell_short_exempt`) and advanced its cursor. Check
`parayabuck ops events --kind reconciliation_unexpected_fill_side` for the
activity ids and symbols. A repeated alert for the same activity means
Alpaca reports a side this build does not know; the position still has to be
reconciled by hand, but reconciliation itself is not blocked.

## Market data degraded (`market_data_degraded`)

`state=degraded` means historical bars are served from a different feed than
the configured one — in practice a SIP entitlement downgrade to IEX, so
SMA/vol history comes from one exchange's prints, not the consolidated tape.
`ops status` and `health.market_data` show `configured` / `effective` /
`sip_fallback_since`; the downgrade is bounded and the next request at or
after 6 h re-probes SIP on its own.

- Confirm the cause: `parayabuck ops events --kind market_data_degraded` and
  the log line `SIP historical entitlement unavailable`.
- If the account is entitled to SIP history, the data plan needs fixing
  (Alpaca answers 401/403 on SIP) — the downgrade clears itself afterwards.
- A transport error (DNS, timeout) no longer downgrades. If the feed flipped
  anyway, capture the log line and the exception and report it: only a
  401/403 or an explicit entitlement body is accepted as an entitlement
  error.

## Incomplete session-close bar (`session_close_bar_incomplete`)

The session closed with stream minutes missing (a stall, or a final minute
more than the 30 s close grace late), so the daily bar built from IEX 1m
bars was withheld instead of deciding on a stale close. The runtime fetches
the provider's SIP daily bar for that session from
close + `signal_cross_check.sip_embargo_minutes` and decides on it.

- Check the log for `Session-close bucket incomplete` with the newest minute
  actually received, and `ops status` / `health.market_data` for
  `session_close_fallback`.
- If the fallback landed, the decision is normal — nothing to do.
- If it did not, the session has no decision and places no order, and
  `equity_decision_missing` fires. Check Alpaca reachability
  (`parayabuck doctor` without `--offline`) and the pb-stocks worker in
  `ops status`; a stream that dies near the close repeats this.

## Ownership drift after a split or a foreign sell

`circuit_breaker` (`ownership_drift` or `reconciliation_failed`), and
`ops status` → `reconciliation_anomalies` lists a `split_rebase`,
`foreign_sell_reduction` or `stale_ownership_clear` row. The repository's
share count and the broker's disagree, so every decision — exits included — is
blocked and dropped, and no reset can succeed until they match. A decision
blocked by the halt at the close is re-queued for the retry window, so a reset
inside it still lands the session; after that the session is lost and
`equity_decision_missing` alarms.

Check what the bot absorbed automatically first: an audited rebase row with
the old and new share count, the ratio and the reason. That is already the
truth, and the `paper_incident` alert (`category=ownership_rebased`) names it.

Then compare `ops bundle` with the Alpaca dashboard:

- A **stock split** (QLD has split six times since 2012) explains a multiple
  of the share count with the average entry price divided by the same ratio.
  The runtime rebases these itself; a non-standard ratio still halts.
- A **foreign sell** — another consumer of the shared PAPER key sold part of
  a position the bot owns — leaves the broker holding fewer shares. The runtime
  reduces ownership to the broker count, alerts, and keeps trading the
  remainder.
- Neither explains the numbers → adopt the broker's count explicitly:

```bash
.venv/bin/parayabuck ops rebase-ownership QLD --broker-qty <broker qty>   # dry run
.venv/bin/parayabuck ops rebase-ownership QLD --broker-qty <broker qty> --confirm
```

Never edit `portfolio_ownership_adjustments`, never flatten the position and
never delete the DB to "fix" drift. After a rebase, reconciliation is clean:
reset the safety lock from the dashboard (`docs/operations.md`, section
"Splits and foreign sells").

## Ambiguous order

An order's broker outcome is unknown; dependent BUYs are blocked.
`ops timeline --order <client_order_id>`, verify on the Alpaca dashboard. The
next reconciliation usually settles it (a local order unknown to Alpaca is
canceled after 10 min). Never resubmit manually — idempotency is by
`client_order_id`.

## Rebalance stuck

A rebalance in `sell_pending` / `buy_ready` / `buy_pending` for over 1 h
(`ops doctor` → `stuck_rebalance`). Check broker order status and
reconciliation. Do not edit `portfolio_rebalance_executions`.

Rows of markets the equity runtime no longer executes (legacy crypto rows from
the deleted sleeve) are ignored by `ops status`: nothing advances them, so
they would otherwise pin `execution` at DEGRADED for good.

## Circuit breaker active

Trading state `CIRCUIT_BREAKER`, runtime `halted`, incident persisted across
restarts. **Nothing trades while halted — not even sells.** A halt means the
account state is not trusted (ownership, protection, reconciliation or a
crashed worker), so no order of any side is submitted: a decision blocked at
the close is re-queued for the retry window, and the dependent BUY phase of a
two-phase rebalance stays at `buy_ready` until a verified reset submits it.
Review `active_incidents.json` in the bundle and the broker account, then
reset. Restarting does not clear it.

Two kinds of row never come back after a restart:

- worker restart limits (`pb-decisions_restart_limit` and friends) are not
  restored — the worker is re-armed, and a repeat failure halts again by
  itself. The row stays as an audit trail;
- process-local halts (`decision_worker_crashed`,
  `trade_update_callback_failure`, `final_market_event_queue_overflow`) are
  migrated: the start re-arms the workers and the empty event queue, so the
  row is cleared and a `circuit_breaker` alert records the migration.

Every other reason (`ownership_drift`, `protection_failure`,
`post_fill_risk_violation`, …) is restored on restart and needs its own
verified reset. A reset verifies one reason at a time (plus the migrated set
above): after resetting, check `ops status` for remaining rows and resolve each
one.

## `repair_local_state.py` and a stuck halt

`scripts/repair_local_state.py` is a one-shot local repair: it quarantines the
polluted warmup/observability files and closes interrupted runtime sessions.
It is a dry run by default — `--help` prints usage and exits 0, and without
`--confirm` it only reports what it would quarantine and rewrite. Pass
`--confirm` (or `--yes`) to actually move files and write the ledger.
Paths resolve from `load_config` (`--config`/`CONFIG_PATH`, like the CLI),
so a custom `runtime.decision_db_path` / `observability.journal_path`
repairs the real files. Three guards bound it, and all are refusals, not
warnings:

- it refuses while the engine heartbeat is live (a fresh health snapshot not
  marked `stopped`, or one it cannot read), so it never rewrites state under a
  running engine;
- it clears the `decision_worker_crashed` incident and its
  `circuit_breaker_reason` **only when broker reconciliation ran and every
  drift bucket came back empty** (the broker gets the runtime's portfolio
  ownership providers, so a bot-owned QLD position is not misread as
  `BROKER_STALE`). Without credentials, with a failing reconcile or with any
  drift, the halt stays active and the script says so. Reset that halt the
  supported way — reconcile, then reset from the dashboard or `ops` — not
  with this script. The breaker clear is scoped to the reconciled account:
  other accounts sharing the session date keep their breaker.

A cleared incident row is first appended to
`<ledger-dir>/safety_incident_resolutions.jsonl` (when it was raised, when and
by what it was resolved): `safety_incidents` only holds active halts, so that
file is the audit trail.

The script also refuses to touch any `risk_state` row whose
`circuit_breaker_reason` is not `decision_worker_crashed`.

## Foreign inventory in the account

`risk_incident` at startup naming crypto symbols and/or equity outside the
strategy universe: the shared Alpaca account holds positions ParayaBuck does
not own. The bot never trades, stops, streams or pins them, and they are not
ownership drift — startup continues. They still count toward account gross and
slot limits, so close them by hand in the Alpaca dashboard if unwanted.

This is also the message for anything the account holds outside the strategy
universe — a manual position, or a leftover from an old smoke run. Activity on a symbol the
strategy *does* trade (QLD) is different: an unowned QLD holding is
`BROKER_STALE` drift and halts the engine (see "Reconciliation failing").

## Foreign order in the account

An open broker order the engine does not own (manual order, leftover crypto
order) is `UNKNOWN_ORDER` drift: the engine cannot know whether its fills are
booked, so it refuses to start or halts until the order is terminal. Cancel or
let it fill; do not delete rows to clear it.

## Where are the alerts?

Nothing is delivered off the VM any more (no webhook, no dead-man probe since
2026-10-06), and no health check turns DEGRADED for that reason. Every alert
is in the event journal and the service log:

```bash
parayabuck ops events --since 24h --severity WARNING
parayabuck ops events --kind backup_failed --since 7d
sudo journalctl -u parayabuck -g 'ALERT ' --since today
```

`ops status` prints the engine's emitted/throttled counters (from
`health_snapshot.json`); a throttled repeat is logged at INFO with its count.
A configuration that still carries `alerting.webhook_url`, `alerting.enabled`
or `alerting.events` fails to load (doctor/`ExecStartPre` refuses the start)
with a message naming the keys: delete them.

## Database issue

`ops doctor` reports `integrity_check` ≠ ok or foreign-key violations. Stop the
service and restore a verified backup (`docs/operations.md`, State, backup and
restore). Do not delete the DB.

## Backups failing or stale (`backup_failed`)

- `reason=config_invalid` — `config/settings.yaml` does not load (the error is
  logged as ERROR by `parayabuck backup`). Fix the file; `parayabuck doctor`
  shows the same error.
- `reason=write_failed` / `corrupt` — the snapshot itself failed. Check
  `journalctl -u parayabuck-backup`, disk space and permissions on
  `backup.path`, then run `parayabuck backup` by hand and confirm it prints
  `Backup written:`.
- Stale backups (no `backup_failed` event, the timer just stopped) show up as
  the `backup stale` `WARN` row of `parayabuck doctor` (newest snapshot over
  12 h old). Check `systemctl list-timers parayabuck-backup.timer` and
  `systemctl status parayabuck-backup.service`, then run the command
  manually.

Until it is fixed, any database loss restores from a stale snapshot and
loses the decisions in between. A `parayabuck-keep-*.sqlite3` file (made
with `--label`) is a manual snapshot; it is never auto-pruned.
