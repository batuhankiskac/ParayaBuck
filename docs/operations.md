# Operations

How the running PAPER bot behaves and how to watch it. Deployment is in
`docs/deployment.md`; symptom playbooks are in
`docs/operator_runbook.md`. All commands run on the VM, in the checkout.

## Daily cycle

| When (ET) | What happens |
|---|---|
| Regular session | 5m IEX bars stream for SPY, QQQ, QLD; the account is polled every 15 s and reconciled every 60 s |
| ≥ 16:00 | The session's daily bar is flushed; VT35 decides on completed bars only (E2 vote on QQQ: close vs 200-session SMA, 252-session return > 0, close vs 126-session channel midpoint; R2 early re-entry at half weight while the vote is off: QQQ vs SMA50, its 20-session vol vs its 252-session median, 20-session return > 0; QLD 20-session vol). A daily bar for a session still trading is dropped from the signal history (`Ignored unfinished equity daily bar` in the log) |
| Right after the decision | Decision persisted; SELLs, then BUYs, submitted as DAY market orders sized against a fresh account snapshot; one `daily_summary` alert. A decision blocked by a recoverable gate is re-queued instead (`decision_deferred`) and re-decided every 60 s until 40 min after the close |
| Next open (09:30) | Alpaca fills the queued orders |
| Close + 45 min / next open + 30 min | Intent watchdog checks that a decision exists and that the target is held (below) |

No order is placed when the target is within the 0.5% rebalance band or the
order is under $10 (`execution.rebalance_band_pct`,
`execution.minimum_order_notional`), and — with `execution.downsize_gain_band`
(A1u) — a downward resize between two positive weights is held while the drift
stays under 0.5 / 3 / 8 pp of the embedded USD FIFO gain fraction of the units
it would sell (`docs/research/gain_band_integration_v1.md`); the daily summary
then says `gain band held (g 7.2%, band 8pp)`. With
`execution.december_downsize_deferral_sessions` (H2, shipped 10) a downsize
between two positive weights whose FILL session is one of the last 10 NYSE
sessions of December is postponed whatever its size and re-planned at every
later decision, so it executes at the first January open; the summary says
`december deferral held (88.0% -> 90.0%, 10 sessions)`, the decision summary
reports `DECEMBER_DEFERRAL_HOLD`, and the intent watchdog does not read the
deliberately held weight as a failed order (an exit to zero still alarms).
Exits, entries, up-resizes and any reduction the risk layer produced are never
deferred, and an unavailable NYSE calendar falls back to the ordinary rule. A fresh deployment makes one
startup catch-up decision on the latest completed bars; a restart never
duplicates a decision. A first start or a restart that missed a close during
regular market hours sends that catch-up as a DAY market order, which fills
intraday rather than at the next open (see `docs/live_readiness_v1.md`, Timing).


## One runner per account

The PAPER Alpaca account is shared between the VM and every other machine
that has the credentials. Trading therefore needs an explicit per-machine
designation: `PARAYABUCK_TRADING_HOST=1`, set only by the trading-host drop-in on
the VM (`deploy/systemd/vm-dropin/trading-host.conf`, installed under
`/etc/systemd/system/parayabuck.service.d/`). The template unit does not set it.
`.env` must never carry it: `doctor` refuses it there.

Without it `parayabuck web`, a bare `parayabuck` and `docker compose up` still
serve the console, settings and backtests, but the engine refuses to start —
auto-start logs the refusal and skips, a manual start returns an HTTP 400
explaining that the machine is not the designated trading host. The state
stays `stopped`; nothing is submitted to Alpaca. Only `1`, `true`, `yes` and
`on` count as the designation — any other value (including `0` or `no`) keeps
trading refused.

Verify on the VM with:

```bash
systemctl show parayabuck -p Environment | grep -o 'PARAYABUCK_TRADING_HOST=[^ ]*'
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

Every alert is written to the service log and to the operational event
journal. The shipped config sends nothing off the VM; the owner can opt in to
e-mail delivery of the important kinds (`alerting.email`, see "E-mail alerts"
in `docs/deployment.md`). Read the local copy on the VM:

```bash
parayabuck ops events --since 24h [--kind K] [--severity WARNING]   # the journal
parayabuck ops status                       # health checks + emitted/throttled alert counters
sudo journalctl -u parayabuck -g 'ALERT ' --since today
```

or on the dashboard through the SSH tunnel. Repeats of the same kind (per
incident/market/order, and per `reason`/`state`, where relevant) are throttled
for 60 s; a throttled repeat is logged at INFO with its running count. An
incident that carries an `episode_key` (every incident record does: its category
on one account, strategy and market) is throttled by that episode, so the
per-record uuid never splits one incident into many. A CRITICAL incident logs at
ERROR. Secrets are redacted from the log line and the journal row. Emission never
blocks or fails trading, and no health check depends on alert delivery.

| Kind | Fires when | Severity |
|---|---|---|
| `runtime_started` | Engine start completed | info |
| `live_started` | A LIVE engine runtime started, by hand or by the standing LIVE authorization (e-mailed) | info |
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
| `risk_epoch_reset` | Operator reset the drawdown baseline (e-mailed) | info |
| `safety_lock_reset` | Operator cleared a halt with `reset_safety_lock` after a verified reconcile; the message names any halts still in force (e-mailed) | info |
| `circuit_breaker` | Breaker engaged (ownership drift, worker restart limit, queue overflow, persisted incident restored) | warning |
| `data_provider_unavailable` | Warning-level data-provider outage (formerly `scanner_refresh_failure`) | warning |
| `signal_adjustment_failure` / `signal_adjustment_recovery` | Split-basis verification failed (e-mailed) / recovered (equity entries halted meanwhile) | warning / info |
| `paper_incident` | Specific incidents (e.g. stuck deferred BUY, unknown order canceled) | warning |
| `signal_source_mismatch` | IEX and SIP daily bars disagree on the trend (position held), their closes diverge, or the SIP bar is missing | warning |
| `decision_deferred` | A session-close decision was re-queued because a recoverable gate (reconcile breaker, stale account snapshot, invalidated snapshot lease, transient broker fetch) was active; one per session, plus one when the retry window expires without a decision | warning |
| `market_data_degraded` | The effective historical feed stopped being the configured one (SIP entitlement downgrade to IEX), and again when it recovers | warning |
| `session_close_bar_incomplete` | A session closed with stream minutes missing: the incomplete daily bar was withheld and the SIP daily bar is fetched for the decision | warning |
| `exit_sell_rejected` | The broker rejected a SELL (an exit or a reduction of QLD). The engine retries it **once** at the next safe account poll (running, not warming up, no breaker or entry halt, no open order in the symbol) with the client id `<original>-retry1`, re-sized to the managed quantity still held; a rejected retry is alerted again (`state=retry_rejected`) and never retried | warning |
| `live_rearm_required` | LIVE is configured but did not start by itself (no valid standing LIVE authorization, or a LIVE gate is closed) | warning |
| `live_authorization_expiring` / `live_authorization_expired` | The standing LIVE authorization ends within 3 days (one per day) / has ended | warning |
| `alert_delivery_failed` | An alert e-mail failed or was dropped (rate limit, full queue); local only, never e-mailed | warning |

E-mail (opt-in) forwards `runtime_stopped_unexpected`, `runtime_start_failed`,
`circuit_breaker`, `drawdown_halt`, `risk_incident`, `ambiguous_order`,
`paper_incident`, `order_rejected`, `exit_sell_rejected`, `intent_alarm`,
`intent_recovered`, `reconciliation_failure`, the three LIVE notices,
`live_started`, `risk_epoch_reset`, `safety_lock_reset`,
`signal_adjustment_failure`, `market_data_degraded`, `data_provider_unavailable`,
`backup_failed` and, when `alerting.email.daily_summary` is true, `daily_summary`.
Delivery is asynchronous and bounded:

- the same incident is mailed at most every 30 min (its episode key, not the
  per-record uuid);
- at most `alerting.email.max_per_hour` (20) mails leave per hour, but a
  non-critical kind may use only the cap minus a quarter of it (15 of 20). The quarter is reserved for
  `drawdown_halt`, `circuit_breaker`, `runtime_stopped_unexpected`,
  `runtime_start_failed`, `risk_incident`, `ambiguous_order`,
  `exit_sell_rejected`, `live_authorization_expired` and any CRITICAL incident;
- a failed send is retried in memory, four attempts in all with waits of 5, 15 and
  45 s. A mail that still fails is journaled as `alert_delivery_failed` (keyed by
  the failed kind and the reason) and is **not** sent again. There is no on-disk
  outbox: mails queued when the process stops are lost, and an SMTP outage longer
  than about a minute drops the mails that arrive during it. Read those rows with
  `parayabuck ops events --kind alert_delivery_failed`;
- the engine snapshot carries the e-mail counters under `alerts.email` (`sent`,
  `failed`, `dropped`, `queued`, `last_success_age_seconds`, which is null until the
  first successful send). A growing age is the signal that mail has stopped.

It never blocks or fails trading. The alert channel has no quiet hours: every
mailed kind is sent at any hour.

`alerting.enabled`, `alerting.webhook_url` and `alerting.events` (the webhook
removed on 2026-10-06) are rejected on load with a pointer to this section;
the alerting keys are `alerting.daily_summary` and the `alerting.email` block.

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

### Stop and restart

A stop from the dashboard is durable. It writes `data/runtime/OPERATOR_STOPPED`
next to the operator halt marker, and auto-start refuses while that file exists.
`sudo systemctl restart parayabuck` (every deploy) therefore leaves the engine
stopped. The owner starts it again with the dashboard Başlat button, which removes
the file. Shutdown, restarts and settings saves that stop the engine do not write
the file, so they keep auto-start. For a hard stop that also refuses manual starts,
set the operator halt first (`parayabuck halt`, see `docs/operator_runbook.md`,
"Operator halt").

A settings save never restarts a running engine by itself. The save request
carries `restart_engine`, which defaults to `false` on the API: while the
engine runs, a save that changes runtime settings is refused with "Stop the
bot before saving settings without a restart" unless the request sets
`restart_engine` to `true`. The console sends `true` only after the operator
confirms the restart in its dialog.

### Daily summary

One line per equity decision (format from `monitoring/daily_summary.py`; values illustrative):

```text
equity_leveraged_trend_v1 2026-09-29: QQQ 550.00 vs SMA200 500.00 (+10.00%) uptrend | QLD target 80.0% held 69.3% | pos QLD 700 | equity $101,000.00 (+1,000.00, +1.00% d/d) | submitted orders 1/1
```

Weights are fractions of account equity; "held" is the position at decision
time, before that decision's orders fill. When the decision has orders, the
order segment reads per side, for example `submitted orders sell 2/2 buy 0/3`:
submitted over planned for each side, so BUYs that the rebalance deferred show
as not submitted instead of being netted against the SELLs. With no orders the
segment keeps the combined `submitted orders 0/0` count. Disable with
`alerting.daily_summary: false`.

The summary ends with a YTD tax segment (`tax YTD <year>: net gain ..., est.
tax ...`). When the USD/TRY or Yİ-ÜFE inputs are missing it reads `tax: rates
missing`; if the figures cannot be computed at all it reads `tax: unavailable`.
The summary is still sent in either case.

### Trading state and the stale-account sell-only option

Each market's `markets.<market>.trading_state` in the runtime health payload is
`active` (entries and exits allowed), `reducing` (no new entries: a
reconciliation latch or a per-market entry latch or halt) or `halted` (no
orders: a circuit breaker or warm-up). It is derived from the existing flags
and is observability only; the flags keep their own recovery paths.

`runtime.stale_account_sell_only` (default `false`) changes one case only. When
the account snapshot is older than `maximum_account_age_for_entry_seconds` and
that is the sole readiness blocker, the decision runs sell-only over the last
known snapshot: BUYs stay held, and each long position is capped at the bot's
own owned quantity from the portfolio repository. While it is off, a stale
snapshot holds the whole rebalance, sells included. Buys held this way are
expected to wait for the next session; that follows the existing entry-latch
path and is not separately tested.

### Intent watchdog

Catches a runtime that is alive but not trading (2026-09-25..29: four sessions
without a decision while every probe was green). Observability only — it never
gates or changes trading. Evaluated on the NYSE calendar (holidays and early
closes included).

| Code | Condition |
|---|---|
| `equity_decision_missing` | No decision persisted for the last NYSE session by ITS close (16:00 ET, 13:00 ET on an early close) + `equity_decision_grace_minutes` |
| `equity_position_under_target` | Latest decision targets a positive weight, but after the next open + `equity_fill_grace_minutes` the account holds less than `minimum_held_fraction` of it |
| `equity_position_over_target` | The account still holds more than the decision targeted after the fill window — the everyday case is a cash target whose DAY SELL never filled. The tolerance is the larger of 1 % of equity and 10 % of the target, plus the gain-band allowance when the latest decision deliberately held the position above target |
| `equity_signal_source_hold` | The decision deliberately held the position because the IEX stream bar and the SIP daily bar disagree on the trend (see `signal_cross_check:`) |

`intent_watchdog` keys: `enabled` (true), `equity_decision_grace_minutes`
(45), `equity_fill_grace_minutes` (30), `minimum_held_fraction` (0.5),
`fail_readiness` (true: active alarms make `/readyz` 503),
`evaluation_interval_seconds` (60).

### systemd watchdog and the reconcile-pending latch

The template unit is `Type=notify` with `WatchdogSec=300`. The web process (`parayabuck
web`) sends `READY=1` from its lifespan once the app is serving, whether or not
the engine starts. A pinger thread sends `WATCHDOG=1` every 30 s while the
engine gate allows it: no engine running (stopped, halted, never started), or
a running engine whose account loop completed any pass within the last
120 s and whose decision worker (`pb-decisions`) beat within the last 120 s (a
missing worker record is not counted). A pass that raises still counts, so an
outage does not restart-loop the unit. What stops the pings is an account loop
that stops passing altogether, or a decision worker that stops beating. A hung
loop therefore stops the pings and systemd restarts the unit after
`WatchdogSec`. Outside systemd `NOTIFY_SOCKET` is unset and the sends are no-ops.

**VM state (as of 2026-10-27).** The VM's unit is `Type=simple` with no
`WatchdogSec`, so a wedged account loop is not restarted by systemd. Its deadman
heartbeat install is not recorded in `docs/OPERATIONS_LOG.md`. The fix is the
drop-in `deploy/systemd/vm-dropin/watchdog.conf` (`Type=notify`, `NotifyAccess=main`,
`WatchdogSec=300`), which is not installed by default: the orchestrator decides
when the VM installs it. Check which state is in force with
`systemctl show parayabuck -p Type,WatchdogUSec`; `Type=simple` and
`WatchdogUSec=0` (or `infinity`) mean the VM runs without the watchdog. Installing the drop-in
needs a restart, so do it in an approved window and check `systemctl status
parayabuck` afterwards: with `Type=notify` the unit stays `activating` until
`READY=1` arrives.

A premarket probe runs once per NYSE session, 30 to 5 minutes before the open
(broker clock, calendar and open-order parity against the local non-terminal
orders). A failure raises a `reconciliation_failure` alert with
`reason=premarket_probe_failed` and sets the reconcile-pending latch below;
exits are never blocked.

Alpaca does not replay trade updates, so a fill can be missed while the
stream is down. Three events set the reconcile-pending latch
(`reason` in the runtime state):

| Reason | Set when |
|---|---|
| `trade_updates_restart` | the `pb-trade-updates` worker failed or returned |
| `trade_feed_stale` | open orders exist, the regular session is open, and no trade update arrived for 300 s |
| `premarket_probe_failed` | the premarket probe found clock, calendar or open-order disagreement |
| `account_fetch_failures` (breaker, not latch) | 5 account fetches in a row failed; the halt clears only through the operator reset, which reconciles first |

While the latch is set, new BUYs wait and rejected-SELL retries wait, the same
gate as `reconciliation_failed`. The session decision first waits and retries
for a clean reconciliation, like a recoverable gate. If the latch is still set two
retry intervals before the retry window closes, the decision runs anyway: its
planned SELLs (exits and rebalance sells) go out, and each BUY is held by the submit
gate before it is sent, whether it comes from the decision or a deferred BUY release.
The startup catch-up is not run while the latch is set: it stays `blocked:` as
before and the operator reset retries it. A deferred BUY side of a mixed rebalance stays deferred
and is released by a later advance once the latch clears. A BUY-only decision that
is held is not sent; its rows are closed as canceled by the next broker lookup, and
the session is not decided again. A hard halt (`CIRCUIT_BREAKER`) is
different: it holds exits too, see `docs/operator_runbook.md`. The latch clears only after a reconcile that started
after the latch was set completes with no drift. The periodic 60 s reconcile retries
it, and it is not a halt: no operator action is needed.

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
parayabuck ops status [--json]            # overall health, trading state, per-check lines; exit 0 OK, 1 BLOCKED/DEGRADED/UNKNOWN, 2 CRITICAL
parayabuck ops doctor [--json] [--journal PATH]   # exit 0 ok, 1 degraded, 2 critical
parayabuck ops events --since 2h [--kind K] [--severity DEBUG|INFO|WARNING|CRITICAL] [--decision ID] [--order ID]
    # --since / --until take ISO timestamps; a timestamp without an offset is read as UTC, not New York time
parayabuck ops timeline --decision ID | --order ID
parayabuck ops explain [latest|DECISION_ID]   # why a decision did or did not trade (latest = current account)
parayabuck ops bundle [--output PATH]     # sanitized diagnostic zip (no keys or secrets; account ids masked)
parayabuck ops rebase-ownership SYMBOL --broker-qty QTY [--account-id ID] [--note TEXT]
    # adopt the broker's share count for one symbol; dry run unless --confirm
```

Sharing a bundle: the bundle masks account ids to `***` plus their last four
characters (`expected_account_id`, `account_id`, `account_number` in the
sanitized config and the journal fields), and redacts the alert e-mail
routing (addresses and the SMTP host) with the same policy the event journal
uses (`redact_fields`). Keys and tokens are redacted as before. Masking does
not hide positions, order ids or decision history, so read the zip before it
leaves the VM and send only what the question needs.

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
so there is no delivery to verify. `ops doctor` adds a `journal_file` check on
the file given by `--journal` (else the configured path): its presence and
last-write age, `UNKNOWN` when absent. `ops bundle` ships the same persisted
copies as `alert_stats.json` / `journal_stats.json` (`source:
engine_snapshot`), or `unavailable` without a snapshot. `--severity` is
case-insensitive and rejects values outside the vocabulary.

`ops doctor` also has a `circuit_breaker` check: CRITICAL while a breaker is
open (with its reason), UNKNOWN when the snapshot carries no runtime state.

`parayabuck doctor [--strict] [--paper] [--offline]` is the deployment
preflight (config, mode, live off, credentials, writability, SQLite
integrity, tracked `.env`, config digest; online it also checks Alpaca
PAPER reachability). The SQLite `journal_mode` row is informational only:
a restored backup opens in DELETE mode until the engine sets WAL on its
next start, so that row never fails `--strict`. `--strict` does fail when
the tracked-file scan cannot run (`git ls-files` fails, for example under a
service user with dubious ownership), because a scan that cannot run is not
a clean scan.

It also reports backup freshness: the age of the newest *automatic* snapshot in
`backup.path`. Labelled `parayabuck-keep-*` snapshots do not count: they are
never pruned, so counting one would report OK for 12 h after every manual
backup while the 6 h timer stays dead. That row is a `WARN`, never a blocker —
the service runs `doctor --strict` as `ExecStartPre`, and a stale backup must
not keep the engine from starting.

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

Decision-input parity with live: a daily CSV row on an NYSE non-session date
(holiday) is dropped with a `non-session bar (holiday); dropped` warning, so
no decision is ever taken on a session that never traded; a decision waits
until every symbol the strategy reads (`decision_symbols`, QQQ/QLD for VT35)
has `minimum_history_bars` bars, exactly like the live `warming_up` gate, and
the default evaluation window (no `--start`) opens on the first session those
symbols can decide on — a short SPY benchmark series delays neither.

Known modelling differences (accepted, not bugs):

- **Rotation timing.** The engine fills every order of a decision at the next
  bar's open, SELLs before BUYs, so a rotation's BUY leg spends the same-open
  SELL proceeds with no gap. Live submits the SELLs first and plans the BUY
  only after they are terminal, off a fresh account snapshot, normally a
  session later at a different open. VT35 holds a single symbol (QLD or
  cash), so it never co-plans both legs and is unaffected; a multi-symbol
  strategy's rotation cost/timing in a backtest is optimistic by that gap.
- **Liquidity model constants.** The participation cap
  (`max_participation_pct`, 10 % of the average daily volume) and its volume
  lookback (`liquidity_lookback_bars`, the last 20 bars with volume) are fixed
  engine constants with no config key, so they are not part of the
  configuration digest; they bind only when an order exceeds 10 % of a thin
  symbol's average volume.
- **Cost basis of the K gate.** The production backtest charges the `backtest` block
  costs (`slippage_bps`, `spread_bps`; about 3 bps per side as shipped). The K research
  gate charged 8 bps per side: a base of 4 bps multiplied by `cost_multiplier=2`. A
  production backtest of K therefore reads about 0.2 pp CAGR higher than the gate. To
  compare like with like, run `run_product_backtest` with `cost_bps_per_side=4` and
  `cost_multiplier=2` (the multiplier scales the base, so `cost_bps_per_side=8` with it
  would charge 16 bps) and state the setting in the report.

## Config flags

`risk.adjust_for_cash_flows` (default `false`) decides whether same-day
external cash moves count as trading P&L. Off, a deposit, a withdrawal or a
journal (`CSD`, `CSW`, `JNLC` activities) moves the day's P&L, the daily-loss
latch input and the drawdown peak, as it always has. On, the account snapshot
reads the day's net external cash from the broker's activities and subtracts
it from the daily P&L, from the drawdown peak (which tracks equity net of that
cash) and so from the figures the latches read. If the activities read fails,
that poll is not adjusted and a warning is logged. The runtime copies the flag
onto the broker when the engine is built (`runtime.py`); `execution/alpaca_account.py` reads it. The default keeps
today's figures; turning the flag on changes the risk inputs and is an owner
decision.

## State, backup and restore

| Path | What | If lost |
|---|---|---|
| `data/runtime/parayabuck.sqlite3` | Decisions, orders, fills, rebalance phases, risk state, safety incidents | **Critical** — restore from backup; broker positions survive but decision lineage does not |
| `data/runtime/paper_equity_peak.json` | Drawdown peak + epoch | Missing → bootstraps from the persisted high-water when one exists, otherwise from the first observed equity (alert `peak_bootstrap`); corrupt → fail-closed |
| `data/runtime/OPERATOR_HALT` | Operator halt from `parayabuck halt`; refuses every engine start until `parayabuck resume` | Lost → the halt is gone and the engine may start; restore its copy from the backup set |
| `config/settings.yaml`, `.env` | Config (tracked in git), secrets | Restore from git / your secret store |
| `data/runtime/observability.sqlite3`, `health_snapshot.json`, `data/cache/` | Journal, snapshot, warmup bars | Regenerated |
| `data/backups/` | Backups | — |

`parayabuck backup` takes a consistent online snapshot (sqlite3 backup API,
safe while running) to `backup.path` (`data/backups`), verifies it with
`PRAGMA integrity_check`, and prunes *automatic* backups older than
`backup.retention_days` (7). The timer runs it every 6 h. Check one file with
`parayabuck backup --verify-only <file>`. Copy backups off the VM
occasionally; they live on the same disk.

Each backup set is the snapshot plus a verified copy of every peak sidecar
that exists (`paper_equity_peak.json`, `live_equity_peak.json`) and of the
operator halt marker `data/runtime/OPERATOR_HALT` when it exists. The copies
are published next to the snapshot as `<snapshot stem>.<name>`, for example
`<stem>.OPERATOR_HALT`. A missing member is skipped (a host that has not
traded has none, and a host without a halt has no halt copy).

A peak sidecar that is unreadable, fails the peak-record check, or whose copy
does not read back is left out of the set. The snapshot still publishes, and
the run records `backup_failed` with reason `sidecar_invalid`, so the set has
no copy of that sidecar until the file is repaired. The members are published
before the snapshot, and the snapshot's link is the commit point: an
interrupted run leaves at most orphan members, which prune ages out, and never
a snapshot published ahead of the members it was taken with.

`parayabuck backup --verify-only <file>` checks the snapshot and every member
the set carries. A member that exists must be a valid peak record or a JSON
halt record; any other state fails the check.

Failures are visible on the VM: `parayabuck backup` logs an ERROR, records
`backup_failed` in the journal and exits non-zero (the unit shows `failed` in
`systemctl status parayabuck-backup`) when the configuration does not load or
a snapshot cannot be written or verified. A missing runtime database (no
decision has been written yet) exits 2 with one `backup_failed` alert and
writes no backup directory. `parayabuck doctor` prints a
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
# Peak sidecars from the same set, when it has them (<stem> = the file name without .sqlite3).
# Without them the peak bootstraps from the first observed equity.
sudo install -o "$PB_USER" -g "$PB_USER" -m 600 data/backups/<stem>.paper_equity_peak.json data/runtime/paper_equity_peak.json
sudo install -o "$PB_USER" -g "$PB_USER" -m 600 data/backups/<stem>.live_equity_peak.json data/runtime/live_equity_peak.json   # LIVE hosts only
# The set's halt copy, when it has one, keeps the restored host halted (a DR restore must not start unhalted).
[ -e data/backups/<stem>.OPERATOR_HALT ] && sudo install -o "$PB_USER" -g "$PB_USER" -m 600 data/backups/<stem>.OPERATOR_HALT data/runtime/OPERATOR_HALT
sudo systemctl start parayabuck
sudo -u "$PB_USER" .venv/bin/parayabuck ops doctor; curl -s http://127.0.0.1:8000/readyz
```

Orders and fills after the backup are missing from the restored ledger. On
start the engine reconciles it against the broker and halts on drift it cannot
explain; review the broker account before any safety reset. Never delete the DB, safety incidents or the peak file to "reset".

Install only the peak sidecars that the chosen set holds, and never copy a peak from
another set: a peak from a different point in time misstates the drawdown. A set without
a sidecar (a host that had not traded) leaves the peak to be re-bootstrapped
(`peak_bootstrap`); check that alert before trusting the drawdown figure. Before the restore,
copy the current `data/runtime/*_equity_peak.json` files into `$EVIDENCE` too.

The halt copy records the halt as it stood at backup time. If the halt was
cleared after that backup, restoring the copy halts the engine until you review
the state and run `parayabuck resume`. A set without a halt copy leaves any
marker already on disk untouched.

A restored file is in DELETE journal mode, because the backup normalizes to
it. That is expected: `doctor --strict` reports `sqlite journal_mode` as INFO
and the `ExecStartPre` preflight does not fail on it, and the engine sets WAL
when it opens the ledger, so the next start after the restore goes through.

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
  --db data/runtime/parayabuck.sqlite3 --journal data/runtime/observability.sqlite3 \
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

Every decision also publishes one immutable JSON receipt under
`data/runtime/paper_receipts/` (`..._<decision_id>.v2.json`; ~252/yr). The
audit above replays any window from these files, so retention never drops a
receipt younger than the soak/audit horizon: `prune_receipts` refuses any
window below 800 days (two trading years plus margin). `parayabuck backup`
(the 6 h timer) prunes receipts at exactly that 800-day floor after each
verified backup; a receipt-prune failure is logged and never fails the backup. Timeline/explain
lookups hit the filename index first and only scan the directory
newest-first (bounded) when it misses.

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
- A standard reverse ratio whose broker average entry price neither
  corroborates a split nor stays at our own cost (for example a cash-in-lieu
  fraction removed from the broker cost) is **not** booked as a sale → the
  runtime halts.
- A **flat broker** with a residue of our own shares and no open exit at the
  broker: a residue of at most 1e-6 shares (dust) is cleared. A larger residue
  is cleared only when a local SELL order submitted after the latest BUY
  explains it. Otherwise nothing is cleared, the symbol stays in drift, the
  runtime halts and the alert `ownership_stale_uncorroborated` names the
  rebase command below.
- Anything else (a non-standard ratio, a share count with an unchanged cost
  basis that would mean claiming shares we never bought) → the runtime halts.
  Verify the real position on the Alpaca dashboard and adopt it explicitly:

```bash
# 1. read the position off the broker (share count and average entry price), then dry run;
#    the output prints "Owned now" (the owned quantity, here 98.8)
.venv/bin/parayabuck ops rebase-ownership QLD --broker-qty 197.6 --broker-average-cost <broker avg price> \
  --note "QLD 2:1 split 2025-11-20 verified on the broker"
# 2. apply it with the owned quantity the dry run printed (refused while any portfolio order is still open,
#    and refused if the ledger has changed since the dry run); a bare --confirm is refused
.venv/bin/parayabuck ops rebase-ownership QLD --broker-qty 197.6 --broker-average-cost <broker avg price> \
  --note "QLD 2:1 split 2025-11-20 verified on the broker" --confirm 98.8
# 3. reconciliation is now clean: reset the safety lock from the dashboard
```

`--broker-average-cost` is the proof of a split: without it a share increase is
refused. For shares the bot did not buy (a foreign purchase on the shared key), give
`--acquisition-unit-cost <unit cost>` instead; a decrease needs neither flag and is
booked as a sale.

Every rebase writes one row to `portfolio_ownership_adjustments` and appears
as a reconciliation anomaly in the `operations.reconciliation_anomalies` list of
`curl -s http://127.0.0.1:8000/api/status` (`ops status` does not print it; the key is
present only while the engine runtime exists). If the
dashboard does not show the anomaly rows, read them from `/api/status`. The command never flattens or cancels anything at the
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
  the process environment (`CONFIG_PATH`, which `parayabuck --config <file> web`
  exports for the process it serves) are restored around every test, so the
  suite passes in any order.

Run it the way CI does (the `quality` job in `.github/workflows/ci.yml`): install the
package without dependencies (`./.venv/bin/python -m pip install --no-deps .`), then run
`./.venv/bin/python -m pytest -q`.

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
