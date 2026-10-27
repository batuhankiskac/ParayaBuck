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
parayabuck ops doctor                 # exit 0 ok, 1 degraded, 2 critical (all exit codes: docs/operations.md, "Exit codes")
parayabuck ops events --since 2h
parayabuck ops bundle --output /tmp/pb_bundle.zip   # sanitized; share status.json + recent_events.jsonl first
curl -s http://127.0.0.1:8000/readyz | python3 -m json.tool
sudo journalctl -u parayabuck --since "1 hour ago" --no-pager | tail -n 100
```

Alerts are local unless the owner enabled e-mail (`alerting.email`, see
`docs/deployment.md`). Every alert is in the
journal (`ops events`, filter with `--kind`/`--severity`) and in the service
log (`journalctl -u parayabuck -g 'ALERT '`); `ops status` prints how many the
engine emitted and how many repeats it throttled.

Safety resets are operator actions in the dashboard (or
`POST /api/engine/reset-safety-lock`, `POST /api/safety/risk-epoch`). A reset
succeeds only after the queue drains, data gaps clear and reconciliation
passes, and it clears the reason it verified. It also clears the halt rows
this start already re-armed, but only those no longer in force in the running
process: a re-armed reason still in force keeps its halt until it is reset
itself (see "Circuit breaker active"). A reset whose halt is raised again
during its own verification is refused and changes nothing.

A risk-epoch reset returns the new `epoch` and whether it `changed`. If the
peak sidecar (`paper_equity_peak.json`, or `live_equity_peak.json` for LIVE)
is bound to another account, the engine refuses to start (fail closed). Verify
the account before moving that file aside.

From the VM shell a mutating call needs the control token header
(`x-parayabuck-control-token`); without it the request is refused with 403. The same
header applies to `POST /api/safety/risk-epoch`.

```bash
TOKEN=$(curl -s http://127.0.0.1:8000/api/control-token | python3 -c 'import json,sys; print(json.load(sys.stdin)["control_token"])')
curl -s -X POST -H "x-parayabuck-control-token: $TOKEN" http://127.0.0.1:8000/api/engine/reset-safety-lock
```

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
  max(1 % of equity, 10 % of the target), plus the gain-band allowance when the latest
  decision deliberately held the position above target — the slack covers exit dust and the
  ordinary price drift of a held position between decisions. In practice a cash target whose DAY SELL
  never filled, leaving the previous position; check the order in
  `ops timeline --decision <id>` (expired/cancelled?), `order_rejected` events and the broker account (an expired or cancelled order
  shows there too). The window is the next
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
| `ENTRY_LATCH` | Daily-loss or drawdown latch: blocks new BUYs, exits still go out | Wait for recovery; drawdown needs a risk-epoch reset after review |
| `signal_adjustment_unverified` latch | The split-basis history is not verified: the whole position is held for the session, no BUY and no SELL, exits included | Wait for the hourly `pb-signal-adjustment` check to verify (`ops status`, Adjustment); see the `SIGNAL_HISTORY_UNVERIFIED` category below |
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
  Fix the cause; do not bypass the guard.
- `operator_halt` — the operator halt marker is in force ("Engine start refused: operator
  halt is in force"). The engine stays down after a reboot until `parayabuck resume` (see
  "Operator halt"). The auto-start path emits `runtime_start_failed` with `reason=operator_halt`.
- Trading-host refusal ("Engine start refused: this machine is not designated as the trading
  host") — this machine lacks the `PARAYABUCK_TRADING_HOST=1` drop-in
  (`deploy/systemd/vm-dropin/trading-host.conf`). It is deliberate: only the designated VM
  runs the engine. The auto-start path logs it as a warning and emits no alert; a manual start
  shows the message in the console. Do not add the variable to a laptop or any other host.
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
  included. A hard halt (ownership drift, a portfolio runtime failure, a
  crashed decision worker, the `account_fetch_failures` halt, an operator
  halt) re-queues the session-close batch for the same retry window, so a
  reset inside the window still decides the session. Once the window has
  expired the session is not decided and `decision_deferred` says so. Nothing
  trades while halted — **not even sells** (a halt is a state where the
  account is not trusted).
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

`reconciliation_unexpected_fill_side` is no longer emitted. The FILL-activity mirror that
skipped activities whose side is outside Alpaca's vocabulary was removed on 2026-10-06, so
this build reports no fill side. Older journal rows of that kind can still be listed with
`parayabuck ops events --kind reconciliation_unexpected_fill_side`. An unexplained fill is
caught at the order level and fails closed, so treat a position that does not match the
ledger like any other drift: compare the broker account and fills by hand.

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
the `operations.reconciliation_anomalies` list of `/api/status` (not `ops status`; present only while the engine runtime exists) has a `split_rebase`,
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
- A **flat broker** that still shows our shares, with no local exit to explain
  it, is not auto-cleared (only dust is). The runtime halts and the alert
  `ownership_stale_uncorroborated` names the command. First check the QLD
  order and activity history on the Alpaca dashboard for the real exit; the
  rebase below then records 0 shares and books the depletion.
- Neither explains the numbers → adopt the broker's count explicitly:

```bash
.venv/bin/parayabuck ops rebase-ownership QLD --broker-qty <broker qty>   # dry run: prints "Owned now" and "Plan digest"
.venv/bin/parayabuck ops rebase-ownership QLD --broker-qty <broker qty> --confirm <owned qty> --plan-digest <digest>
```

`--confirm` takes the owned quantity the dry run printed (`Owned now`), and
`--plan-digest` takes the `Plan digest` that the dry run printed for the same flags.
Both are required: a bare `--confirm` is refused, and so is a write when the
ledger has changed since the dry run or when the flags differ (for example,
`--broker-average-cost` left out on the confirm run, which would turn a split into a
sale). A share increase is refused unless `--broker-average-cost` (a split, proven by the
broker's average entry price) or `--acquisition-unit-cost` (shares bought outside the bot)
is given; the dry run prints the broker average cost it was given. The write is
refused while the engine heartbeat is live: stop the engine first. A successful write
journals `ownership_rebased` and stores the proof (broker average cost, expected owned,
digest) in the adjustment note. When `broker.expected_account_id` holds the PA account
number, the CLI checks it against the broker account for the configured mode, so the
PA pin works; an unreachable broker refuses. See `docs/operations.md`, "Splits and foreign sells".

Never edit `portfolio_ownership_adjustments`, never flatten the position and
never delete the DB to "fix" drift. After a rebase, reconciliation is clean:
reset the safety lock from the dashboard (`docs/operations.md`, section
"Splits and foreign sells").

## Per-symbol drift lock (`runtime.per_symbol_drift_lock`, default off)

With the flag off, an ownership shortfall halts the whole runtime, as described
above. With `runtime: per_symbol_drift_lock: true`, an unexplained shortfall
(the broker holds fewer shares than the repository owns for a symbol) locks
only that symbol:

- Alert: `circuit_breaker` with `reason=symbol_ownership_drift` and the
  `symbol` field, once per newly locked symbol. A reconcile is scheduled.
- While locked, the bot refuses every BUY and SELL for that symbol locally
  (no broker call; the order row is booked `rejected`). Other symbols trade
  normally and the runtime is not halted.
- Unlock is automatic: on the first account pass where the broker again covers
  the owned quantity, the lock clears and a `reconciliation_recovered` alert
  with `reason=symbol_ownership_drift_cleared` is sent. Nothing to reset.
- Locks are in memory only. After a restart the first account snapshot
  re-detects the shortfall and re-locks the symbol (alert again).
- Short positions and duplicate broker positions still halt the whole runtime
  as before, and so does any drift with no symbol attached.

Check the broker and `ops bundle` as in the section above; fix the shortfall
(or rebase it with `ops rebase-ownership`) and the lock clears by itself.

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

**A hard halt holds exits too.** Planned SELLs, including risk-reducing exits
such as a flip-down to cash, are held along with BUYs for as long as the halt
stands ("halted = no orders"). This is deliberate: the halt means the account
state is not trusted, so no exit is sent on it either. Do not expect an exit to
go out while `CIRCUIT_BREAKER` is active; it goes out after the verified reset
and the retry window is still open, or the session is lost. The soft
reconcile-pending latch (`trade_updates_restart`, `trade_feed_stale`,
`premarket_probe_failed`) is not a halt and works differently: the decision
first waits and retries, like a recoverable gate, for a clean reconciliation.
If the latch is still set two retry intervals before the retry window closes,
the decision runs anyway: its planned SELLs go out, and only BUYs wait for a
clean reconciliation.

Review `active_incidents.json` in the bundle and the broker account, then
reset. Restarting does not clear a persisted halt; the exceptions are listed under
"Two kinds of row never come back after a restart" below. A `<worker>_restart_limit`
reason is not operator-resettable at all (the reset answers "Safety lock is not
operator-resettable"): the restart re-arms the worker, and a repeat failure halts again.

If the reset reports stopped shared workers (`pb-account` or `pb-trade-updates`),
the dashboard refuses every reset until the service restarts. Restart with
`sudo systemctl restart parayabuck` (the persisted halt rows survive it), check
`ops status`, then reset again. A stopped `pb-decisions` loop is not a reason to
restart: the reset respawns it.

Two kinds of row never come back after a restart:

- worker restart limits (`pb-decisions_restart_limit` and friends) are not
  restored — the worker is re-armed, and a repeat failure halts again by
  itself. The row stays as an audit trail;
- process-local halts (`decision_worker_crashed`,
  `trade_update_callback_failure`, `final_market_event_queue_overflow`) are
  migrated: the start re-arms the workers and the empty event queue, so the
  row is cleared and a `circuit_breaker` alert records the migration.

Every other reason (`ownership_drift`, `protection_failure`,
`reconciliation_failed`, …) is restored on restart and needs its own
verified reset. A reset verifies one reason at a time (plus the migrated set
above): after resetting, check `ops status` for remaining rows and resolve each
one.

## Drawdown liquidation (`risk.drawdown_liquidation`, default off)

A catastrophe brake for a deep drawdown from peak equity. The shipped default
is **off**, and the brake is not a scaling rule: the research governors that
scale exposure with drawdown were rejected (the drawdown-budget governor IC49
failed stage 1 in `docs/research/batch_bank_v3.md`), so the existing
`daily_loss_limit_pct` and `max_drawdown_pct` latches keep their current
meaning: they cap targets at their current weight: they block buying above it, but a target below the held weight still sells.

When enabled, a drawdown at or above `threshold_pct` (for example 0.45) sets
every managed target to zero and records the reason `drawdown_liquidation` on
the decision. External or manual units are never sold, and the managed
reductions bypass the gain band and the December deferral. The brake re-arms
once the drawdown falls below `rearm_recovery_pct` (for example 0.30) and at
least `cooldown_sessions` (for example 5) NYSE sessions have passed in cash.

```yaml
risk:
  drawdown_liquidation:
    enabled: false
    threshold_pct: 0.45
    rearm_recovery_pct: 0.30
    cooldown_sessions: 5
```

State and wiring, read before enabling:

- The RED-state is stored per account and market in the runtime database table
  `portfolio_drawdown_liquidation_states`. It is written in the same transaction
  as the decision that produced it, so a restart reloads the latched state and
  cannot re-enter a position inside the cooldown. The table is created at start
  up even while the brake is off, but it is never read or written while off.
- `PortfolioRuntime` accepts the block (`drawdown_liquidation=`) and the engine passes
  `config.risk.drawdown_liquidation` to it (`runtime.py`), so `enabled: true` takes effect at
  the next start. Enabling it is a live sell: at the threshold every managed target goes to
  zero.
- While the brake is off, the configuration digest and the loaded configuration
  fingerprint are unchanged. Enabling the block changes both, so a standing LIVE
  authorization bound to the old values must be re-approved.
- Without a persisted state the overlay is stateless: it holds RED only while
  drawdown is at or above the threshold, so a recovered account is re-admitted
  at once and there is no cooldown.
- A managed position whose gap to zero is below `execution.rebalance_band_pct`
  (0.5 % of equity by default) is not sold by the planner, so a small residual
  may stay after a liquidation.

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

Only two things leave the VM, both optional: the dead-man heartbeat
(`docs/deployment.md`, "Dead-man switch"), which pings an external service and carries no
alert content, and the opt-in e-mail channel. The webhook was removed on 2026-10-06. No
health check turns DEGRADED because alert delivery fails. Every alert
is in the event journal and the service log:

```bash
parayabuck ops events --since 24h --severity WARNING
parayabuck ops events --kind backup_failed --since 7d
sudo journalctl -u parayabuck -g 'ALERT ' --since today
```

`ops status` prints the engine's emitted/throttled counters (from
`health_snapshot.json`); a throttled repeat is logged at INFO with its count.
When e-mail is enabled, the same snapshot holds its counters under `alerts.email`:
`sent`, `failed`, `dropped`, `queued` and `last_success_age_seconds` (null until
the first successful send). A mail that failed for good is in
`parayabuck ops events --kind alert_delivery_failed`, never in your inbox, and it
is not sent again. A CRITICAL `paper_incident` (for example `DATABASE_ERROR`)
logs at ERROR.
A configuration that still carries `alerting.webhook_url`, `alerting.enabled`
or `alerting.events` fails to load (doctor/`ExecStartPre` refuses the start)
with a message naming the keys: delete them.

## Database issue

`ops doctor` reports `integrity_check` ≠ ok or foreign-key violations. Stop the
service and restore a verified backup (`docs/operations.md`, State, backup and
restore). Do not delete the DB. Install the database together with the peak
sidecars (`paper_equity_peak.json`, `live_equity_peak.json` on a LIVE host) and the
`<stem>.OPERATOR_HALT` copy of the same backup set, never a file from another set.
Before the restore, run `find config data -type l`: atomic writes refuse a symlink
that leaves its directory, so the engine cannot write through such a link.

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
with `--label`) is a manual snapshot; it is never auto-pruned, and `backup --label`
prints how many labelled snapshots exist and their size. Delete old ones by hand.

## Operator halt (`parayabuck halt` / `parayabuck resume`)

A sticky halt keeps the engine down across restarts, deploys and reboots until
an operator clears it. Use it when you want the bot to stay stopped and not
just stopped for this run.

```bash
parayabuck halt --reason "investigating fill mismatch"   # writes the marker
sudo systemctl stop parayabuck                            # or the console stop control
parayabuck resume                                         # clears the marker
sudo systemctl start parayabuck                           # or the console start control
```

- The marker is `data/runtime/OPERATOR_HALT` for the PAPER config (a LIVE
  config writes `data/runtime/live/OPERATOR_HALT`). It is JSON: `reason`,
  `created_at`, `user`. `halt` prints the mode and the marker path it wrote:
  that is the engine the marker stops, so check it matches the engine you mean.
  A broken settings file makes `halt` and `resume` change nothing (exit 1, with a
  stderr line saying so).
- While it exists, the automatic start and the console start are refused, and
  the log shows `operator halt is in force` as an ERROR. Any unreadable marker
  still counts as a halt.
- A running engine checks the marker about every 10 s from its decision loop.
  When it finds the marker, the runtime halts (circuit breaker `operator_halt`,
  persisted as a safety incident) and takes no new decision. `halt` does not
  cancel open orders or close positions, and it does not stop the process:
  stop the engine as shown above when it must go down.
- `resume` only removes the marker, and it prints whether a persisted
  `operator_halt` incident row still exists in the decision database. A restart
  after `resume` still starts halted while that row exists. Reset is refused
  while the marker exists and succeeds once it is gone and reconciliation
  passes (dashboard, or `POST /api/engine/reset-safety-lock`). Check
  `parayabuck doctor` and the dashboard before starting again.
- A stop from the dashboard is not sticky (see `docs/operations.md`, "Stop and restart"):
  `sudo systemctl restart parayabuck` starts the engine again unless this marker exists.

## `paper_incident` categories

Each `paper_incident` alert carries `category=`. The first twelve rows are the taxonomy
categories in `docs/paper_incident_taxonomy.md`. The code also emits the other `category=`
values in the second table; the taxonomy does not define them. Start with
`parayabuck ops events --kind paper_incident`, which shows the account and the error text.

| Category | Check first | Action |
|---|---|---|
| `PROVIDER_UNAVAILABLE` | `ops events --kind data_provider_unavailable`, Alpaca status, `parayabuck doctor` without `--offline` | Retries are automatic. Persistent: fix connectivity or the data plan. |
| `BROKER_ACCOUNT_STALE` | `/readyz`, account age in `ops status` | Self-clearing. Check broker reachability and the clock; no reset needed. |
| `POSITION_DRIFT` | `ops bundle` against the Alpaca positions | Halt. Follow "Ownership drift after a split or a foreign sell", then a verified reset. |
| `ORDER_DRIFT` | open orders at Alpaca against `ops timeline` | Reconciliation retries on its own. If halted, settle the unknown order by hand, then a verified reset. |
| `PENDING_ORDER_UNKNOWN` | `ops timeline --order <client_order_id>` | Wait for broker finality; the next reconcile settles it. Never resubmit. |
| `DATABASE_ERROR` | disk space, file permissions, `parayabuck backup --verify-only` on the newest backup | Fix the disk or the permissions. Restore only as in "State, backup and restore". A critical one is logged at ERROR and e-mailed. |

Six categories in the taxonomy have **no emitter in this build** and never appear in an
alert: `SIGNAL_HISTORY_UNVERIFIED`, `STATE_RESTORE_FAILED`, `DUPLICATE_DECISION`,
`DUPLICATE_ORDER_RISK`, `CLOCK_OR_SESSION_MISMATCH` and `RESTART_RECOVERY_FAILED`. Their
rows in `docs/paper_incident_taxonomy.md` describe the intended behaviour. Do not wait
for one of them in the journal: read the halt reason in `ops status` and the log instead.

Other `paper_incident` categories emitted by the code (not in the taxonomy):

| Category | Meaning | Action |
|---|---|---|
| `ownership_rebased` | A split or a foreign sell was rebased automatically (audited adjustment row) | Informational: compare with the Alpaca position (`ops bundle`). |
| `ownership_surplus_unconfirmed` | The broker holds more shares than the bot owns and no split or purchase explains it | After the Alpaca check, adopt the count with `ops rebase-ownership` plus `--broker-average-cost` (a split) or `--acquisition-unit-cost` (bought outside the bot), as in "Ownership drift after a split or a foreign sell". |
| `lot_book_inconsistency` | The lot book does not match the owned quantity; the gain band falls back to the constant band | Repair the lot book as in "Ownership drift"; the fallback is safe meanwhile. |
| `cost_basis_drift` | Reconciliation found a cost basis that differs from the broker's | Compare the lots with the broker's average entry price (`ops bundle`). Never edit the ledger by hand. |
| `invalid_fill_facts` | A fill row failed validation and was not booked | Check the order at the broker (`ops timeline --order`); reconciliation settles it. |
| `last_equity_missing` | The broker did not report `last_equity` and no baseline exists for today; entries are refused | Check Alpaca reachability. It clears when the broker reports `last_equity` again; no reset needed. |
| `peak_sidecar_write_failed` | The drawdown peak file could not be written; the snapshot is kept | Check disk space and permissions in `data/runtime`. Retried on the next poll. |
| `peak_bootstrap` | The drawdown baseline was started at current equity: no peak file and no persisted high-water | Expected on a new host. On a host that traded, find out why the peak file is missing before trusting the drawdown figure. |
| `stream_auth_failed` | The market-data stream rejected authentication | Check the keys and the data entitlement. The stream retries. |
| `undecoded_trade_update` | An Alpaca trade update could not be decoded and was dropped | Repeated events: check the SDK pin and `ops timeline`. Reconciliation re-reads the orders. |
| `unrecognized_broker_order_status` | The broker reports a status the pinned SDK does not know; the order is left unchanged | Reconciliation reports it. Persistent: check the SDK pin. |
| `unknown_order_canceled` | A portfolio order the broker never accepted was canceled | Informational: the local record is closed. |
| `residual_sell_exhausted` | A residual SELL stayed unfilled after the round limit; deferred BUYs were discarded | Check the open SELL at the broker; the next decision re-plans. |
| `rebalance_unrecoverable` | A blocked rebalance decision could not be restored; its deferred BUYs were discarded | Check `ops status` for the rebalance phase; the next decision re-plans. |
| `stuck_deferred_buy` | A deferred BUY still awaits reconciliation | Reconciliation settles it. Never resubmit. |
| `rebalance_completed_with_ambiguous_buy` | The rebalance finished while a deferred BUY awaits reconciliation | Check the BUY with `ops timeline --order`. Never resubmit. |
| `submission_resolved_canceled` | The broker lookup resolved a submitting order as canceled; it was not re-sent | Informational. |
| `stuck_submission_lookup` | A submitting order stayed unresolved after repeated lookups; escalated to reconciliation | Check the client id at the broker. Never resubmit. |
| `replan_order_exhausted` | A re-planned order ran out of fresh client ids; it is not sent | Repeated: check the symbol's order history with `ops timeline`. |
| `asset_untradable` | The broker refuses the trading rules of a symbol; it is left out of the plan | Check the symbol at Alpaca. |
| `exit_sell_untradable` | A rejected exit SELL cannot be retried: the broker refuses the symbol's trading rules | Settle the position at the broker by hand; the engine cannot retry this SELL. |

## Other alerted reasons

### `exit_sell_rejected`

The broker rejected a SELL that exits or reduces QLD. The engine retries it once at the
next safe account poll, under the client id `<original>-retry1`. A rejected retry is
alerted again with `state=retry_rejected` and is not retried. Read the rejection text in
`ops timeline --order <client_order_id>` and on the Alpaca order first. Do not sell by hand
without deciding how the ledger should see it: a manual reduction is reconciled as a
foreign sell (`docs/operations.md`, "Splits and foreign sells"). If the exit cannot wait,
record the decision in `docs/OPERATIONS_LOG.md` before acting.

### `live_rearm_required`, `live_authorization_expiring`, `live_authorization_expired`

- `live_rearm_required`: LIVE is configured but did not start by itself. Run
  `parayabuck doctor --strict --configured-mode --offline` and read the refusal. Then
  `parayabuck authorize-live --operator <name> --expires-minutes 30`, arm in the dashboard
  (`LIVE <suffix>`) and press Start. Open positions stay open meanwhile.
- `live_authorization_expiring` (within three days): renew with
  `parayabuck authorize-live --operator <name> --standing-days <n>` before it ends. A running
  engine keeps running; only the next restart needs the renewal.
- `live_authorization_expired`: the next restart waits for a manual re-arm, as above.
- To revoke at any time: `parayabuck authorize-live --revoke-standing`.
  After the credential migration, a plain shell cannot see the LIVE keys: run the
  command as the transient unit in `docs/deployment.md` ("Manual commands after the
  migration") with the LIVE credential lines added.

### `data_provider_unavailable`

A provider outage at warning level that did not halt the engine (formerly
`scanner_refresh_failure`). Check `ops events --kind data_provider_unavailable` and Alpaca
reachability (`parayabuck doctor` without `--offline`). Retries are automatic. If it lasts
for hours, entries wait on the signal path: see `PROVIDER_UNAVAILABLE` above.

### Restart-limit halts (`<worker>_restart_limit`)

A `circuit_breaker` for a worker that exceeded its restart limit. Read the worker's
last errors first (`sudo journalctl -u parayabuck --since "2 hours ago"`) and fix the
cause (usually stream connectivity or the data entitlement). Then follow the shape of the halt:

- **Shared worker** (`pb-decisions_restart_limit`, `pb-account_restart_limit`,
  `pb-trade-updates_restart_limit`): the whole engine is halted. The reset refuses these
  reasons, and restart limits are not restored on restart (see "Circuit breaker active"):
  restart the service, check `ops status`. A repeat failure halts again by itself.
- **Market stream worker** (`pb-stocks_restart_limit`): entries are halted for that market
  only. Only a verified reset clears it: the reset re-arms the stream worker and clears the
  halt when the worker delivers fresh events. Do the reset from the dashboard, then check
  `ops status`.

## Credentials from systemd (`LoadCredentialEncrypted=`)

Alpaca keys are read first from `$CREDENTIALS_DIRECTORY/<ENV_NAME>` (systemd's
private credentials directory), then from `.env`, then from the process
environment. The SMTP login uses the same order (`PARAYABUCK_SMTP_USERNAME` / `PARAYABUCK_SMTP_PASSWORD`).
Put the secret in systemd
credentials to keep it out of plain-text files:

```bash
# Encrypt with the host key. Paste the value at the prompt; it is not echoed
# and does not appear in shell history.
read -rs -p "ALPACA_API_KEY: " V && printf '%s' "$V" | \
  sudo systemd-creds encrypt --name=ALPACA_API_KEY - /etc/parayabuck/ALPACA_API_KEY.cred && unset V
```

Then add one line per secret to the unit with a drop-in
(`sudo systemctl edit parayabuck`), not by editing the shipped unit file:

```ini
[Service]
LoadCredentialEncrypted=ALPACA_API_KEY:/etc/parayabuck/ALPACA_API_KEY.cred
```

Run `sudo systemctl daemon-reload && sudo systemctl restart parayabuck`. Use
the same pattern for `ALPACA_SECRET_KEY` (and the `ALPACA_LIVE_*` pair in LIVE).
The file name in the credentials directory is the variable name, so no other
change is needed.

`parayabuck doctor --strict` (the unit's `ExecStartPre`) now FAILS when the
`.env` credential file is readable by group or other users. Fix it with
`chmod 600 .env`; a plain `doctor` only warns. Leave `.env` mode 600 on the VM
before you deploy this change, or the service will not start.

## Pin the broker account (`broker.expected_account_id`)

By default the engine trusts whichever Alpaca account the keys resolve to. To
refuse any other account, set the account number (the `PA...` value) in
`config/settings.yaml`:

```yaml
broker:
  expected_account_id: "PA0000000000"   # placeholder: use your own PA number
```

With the pin set, a mismatched account is refused with
`broker account does not match broker.expected_account_id`, and so is an
account number that cannot be read. Leave the block out to keep the previous
behaviour. The pin is checked by the `authorize-live` command and the LIVE arm
in the console, and the PAPER runtime start checks it too.
