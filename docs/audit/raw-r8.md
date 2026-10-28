# Audit wave 3 — raw findings (main 8f9fa637, 2026-10-28)

Read-only, 8 tracks × 5 pipelined rounds (P decision→order, F fill→books, R restart/persistence, C concurrency, T strategy/time/data, Q tests, D docs/config/observability, S security). Lenses: see each section header. Each section carries an orchestrator note on what was spot-checked. Ids are `<track>3-<round>-<n>`.

## R3-1 — halt/reset after D1/D2/E1
_Orchestrator: R3-1-1 and R3-1-2 confirmed by reading runtime.py (3955-3961 has no `_note_halt_reason`; the leftover set is computed in one `_state_lock` block and released in a later one)._
- **R3-1-1** (medium, medium) runtime.py:6632-6668 — INCOMPLETE of R2-1-2: the re-raise guard and the release run in two `_state_lock` blocks with `_clear_reconciliation_incidents` between them; a rearmed reason (e.g. `trade_update_callback_failure`) raised in that window is in `leftover_rearmed` and is released, and its row deleted. Fix: recompute the leftover set and re-check the seq inside the release block.
- **R3-1-2** (medium, high) runtime.py:3955-3961 — INCOMPLETE of R2-1-2: the decision worker's `OwnershipDriftError` branch sets the breaker without `_note_halt_reason` or an incident write (the fourth site; the other three were fixed). A drift raised there during a drift reset is lifted, and a crash before the next account pass loses the halt. Fix: `_note_halt_reason` + `_save_halt_incident_best_effort`.
- **R3-1-3** (medium, medium) runtime.py:6821-6886, 3777 — INCOMPLETE of C2-10-2: the reset's stream restart clears `last_error`, then judges delivery from the stale pre-restart `last_event_at`, so a restart-limit market halt is retained; the first live bar cannot auto-clear it because `last_error` is None. Entries stay blocked until a second reset. Fix: require an event newer than the respawn, or let recovery clear a matching restart-limit halt.
- **R3-1-4** (low, high) runtime.py:6711-6718 — retained market halts (from `_verify_market_halts` / `_confirm_stream_restarts`) are missing from the `safety_lock_reset` alert's `retained` field, against the docstring.
- **R3-1-5** (low, high) runtime.py:6686-6694 — a reset that fails after the release re-halts with `portfolio_runtime_failure` but emits no `circuit_breaker` alert or metric, unlike the catch-up retry path.

## F3-1 — tax changes J2/K1/K2/X3
_Orchestrator: F3-1-5 matches what the J2 merge already knew (stream fills carry no commission), so the note is expected to fire for most symbols; kept as a UX finding. F3-1-1 checked: `export.py` masks `account_id` but returns `str(exc)` unmasked._
- **F3-1-1** (low, high) tax/ledger.py:170-173, export.py:348-349 — INCOMPLETE of S2-2-1: `ytd_status` masks `account_id` but its `error` text carries the full account id from `assert_no_ownership_adjustments`; the dashboard shows it.
- **F3-1-2** (low, medium) tax/cli.py:242-270 — INCOMPLETE of F2-3-9 on the CLI: `tax export` without `--account-id` can report for the `"unknown"` placeholder with no warning.
- **F3-1-3** (low, high) tax/rates.py:5, 355-359 — `rates-fetch` rewrites usdtry.csv with only `date,usdtry_forex_buying`, dropping the documented optional selling column.
- **F3-1-4** (low, medium) tax/dividends.py:278-303 — a 1 January import marks 31 December covered, against its own comment that the run day may be incomplete; a late-posting 31 Dec dividend is missed while the status reads "complete".
- **F3-1-5** (low, medium) execution/alpaca_updates.py:184-189, tax/export.py:215-222 — stream fills carry no commission, so the J2 unobserved-fee note fires for nearly every symbol every year; real gaps are indistinguishable from the zero-commission norm.
- **F3-1-6** (low, medium) tax/cli.py:186-193, docs/operations.md:433-434 — a missing ledger in `tax import-activities` exits 1, but the exit-code table promises 3.

## Q3-1 — tests re-pinned in the wave-2 merges
_Orchestrator: no assertion was weakened to hide a regression; all items are test-strength gaps. Q3-1-2 checked: `record_order` writes `datetime.now(UTC)` and the `_force_order_created_at` helper is gone._
- **Q3-1-1** (low, high) tests/test_web_settings.py:78-86 — the parametrized engine-refusal test matches only "Motor zaten", not the per-state label.
- **Q3-1-2** (low, high) tests/test_ownership_rebase.py:208-270 — ownership-consumption tests now order by wall-clock reads, not the timestamps they name (`record_order` stamps `now()`); a clock tie can flip `<=`.
- **Q3-1-3** (low, high) tests/test_submit_gate_shadow_tl_2026_10_27.py:189-205 — the clock-read-failure warning branch (non-DAY tif) lost its only test.
- **Q3-1-4** (low, high) tests/test_observability_mission5.py:1162-1168 — exact JSON payload check relaxed to a key-subset check.
- **Q3-1-5** (low, high) tests/test_audit_fix_W06.py:203 — superseded-SELL test checks the phase only, not the status.
- **Q3-1-6** (low, medium) tests/test_december_deferral_h2.py:534/610, test_gain_band_a1u.py:917/954 — the December and gain-band verdict tests clear the carried cadence watermark; the carry × December boundary combination is untested.
- **Q3-1-7** (low, medium) tests/test_ownership_rebase.py:381-398, 520-557 — `_with_plan_digest` injects a zero digest when none is printed, so a refusal test can pass on the digest check instead of the open-orders check.

## C3-1 — lock ordering after D1/E1/G1/X1
_Orchestrator: C3-1-1 confirmed (runtime.py:4682 still passes a bare `self.broker.reconcile()` lambda; 4620 uses the locked helper). No lock-order cycle found._
- **C3-1-1** (medium, high) runtime.py:4682 — INCOMPLETE of C2-1-3: the generic decision-failure `PENDING_ORDER_UNKNOWN` recovery reconciles without `_reconcile_lock`, so it can interleave with pb-reconcile or an operator reset. Fix: `reconcile_action=self._reconcile_for_incident_recovery`.
- **C3-1-2** (low, high) execution/risk_peak.py:401-419, alpaca_account.py:699-716 — INCOMPLETE of C2-3-5: the `peak_bootstrap` alert is still emitted inside `_peak_lock` (once per bootstrap).
- **C3-1-3** (low, medium) portfolio/runtime_repository.py:2250-2413 — the surplus-alert dedupe check/add now runs after `store.lock` (G1 after-release callback); a concurrent MATCH resolve can leave a stale key that suppresses a later real surplus alert, or two callbacks both emit.
- **C3-1-4** (low, low) execution/alpaca_account.py:755-757 — `_snapshot_cache` is stamped at completion, so a slower older fetch can overwrite a newer one as "fresh".
- Not re-reported: R2-1-6 (pre-reset snapshot re-latch) is unchanged and self-heals within 15 s.

## S3-1 — L2/M3 security changes
_Orchestrator: S3-1-1 applies only to the container/compose path (PARAYABUCK_CONTAINER=YES); the VM runs the home-dir systemd install, so it is not live there._
- **S3-1-1** (medium, medium) web/security.py:76-81, 169-171 — container mode trusts any RFC1918 peer; another container on the Docker bridge can fetch the control token with a spoofed `Host: localhost` and call mutating routes. Fix: trust only the bridge gateway or an explicit CIDR.
- **S3-1-2** (low, medium) runtime.py:802-1861 — a manual LIVE approval is recorded as consumed only after the order-submitting startup steps, so a start that fails after submitting leaves the approval reusable (single-use gap).
- **S3-1-3** (low, high) web/managers/engine.py:1870-1918 — the LIVE readiness checklist shows the approval green without the host-binding and single-use checks the runtime enforces, against its own docstring.
- **S3-1-4** (low, medium) live_safety.py:31, 43 — the 10-distinct-character floor admits structured low-entropy approval keys.

## P3-1 — wave-2 rebalance changes, regression review

*Orchestrator note: I read the code for P3-1-1 (runtime.py:7007-7022 vs runtime_service.py:2381) and P3-1-3 (runtime_service.py:3306-3313) and both hold. P3-1-1 is an INCOMPLETE of P2-2-4/A2.*

- **P3-1-1** (medium, high) runtime.py:7007-7022, portfolio/runtime_service.py:2380-2381 — `"residual_sell_submitted"` is missing from `_HELD_SESSION_STATUSES`. When a decision pass submits a residual SELL, the session is not parked and is never re-run, so the target for that session is lost; only the `equity_decision_missing` watchdog notices. The A2 coverage test (test_audit2_fix_A2.py:386-395) does not walk `_residual_sell_result`. Fix: add the status, and walk every `PortfolioRuntimeResult` return in the coverage test.
- **P3-1-2** (medium, medium) runtime.py:1112-1138, 3285, 4537 — `_maybe_readvance_held_buy_side` takes `_portfolio_lock` from the account loop on each BUY_READY pass. While a decision holds that lock across broker submits, the 15 s snapshot poll stalls. This contradicts the comment at ~1159-1165 that the account loop never queues. Fix: `acquire(blocking=False)` and skip the pass, or move the readvance off the account loop.
- **P3-1-3** (medium, high) portfolio/runtime_service.py:3306-3313 — when the submit gate blocks partway through a BUY batch after some BUYs went out, the unsent BUYs are dropped. The only record is a log.warning; there is no `_alert_buy_side_held` and no stuck-BUY row. Fix: send the alert before returning `_enter_buy_pending`.
- **P3-1-4** (low, medium) runtime.py:3321-3331 — the B1 entry-halt latch is lifted on a no-op advance (submit_deferred_buys False returns None with no broker call), which proves nothing about the broker. Fix: lift only when the advance actually ran.
- **P3-1-5** (low, high) runtime.py:1112-1138, runtime_service.py:2202-2238 — the readvance fires for every BUY_READY state, not only the two statuses its docstring names, and writes `_log_deferred_buy_withheld` at WARNING every 60 s during a long halt or latch. Fix: skip when trading_state is not ACTIVE.

## T3-1 — split continuity and basis after H2/X2

*Orchestrator note: I read adjustment.py:130-158 for T3-1-1/T3-1-2. The detector compares only the stored tail with the provider's newest bar, and any prior that differs by more than 1e-6 returns False. A stream-built prior (not the SIP close) therefore silences it, which makes T3-1-1's scenario more likely to fire, not less. The impact path goes through QLD realized-vol sizing.*

- **T3-1-1** (high, medium) data/adjustment.py:132-158, runtime.py:4085 — the split detector works only on the day the provider's newest bar is the split day. Once a post-split stream bar for D becomes the stored tail, the D-1→D jump inside an unrebased provider window is never tested, and verify adopts a window with a fake ~-50% move (vol sizing collapses, spurious SELL). Fix: scan the provider window's consecutive final closes for a KNOWN-ratio jump whose stored priors match the pre-jump closes, and add a test where the stored tail is post-split.
- **T3-1-2** (medium, low) adjustment.py:149-153, 51 — one stored prior that differs from the provider by more than 1e-6 (for example a stream-built close) is read as "the provider rebased" and returns False for the whole window. Fix: decide "rebased" from the ratio (provider prior / stored prior ≈ KNOWN ratio), not from any inequality.
- **T3-1-3** (medium, high) adjustment.py:158, core/split_ratios.py:10-13 — a non-standard ratio (3:2 = 1.5), or a 2:1 split with a large split-day move, falls outside every band and passes silently. The stated design is to halt for an operator. Low reachability for SPY/QQQ/QLD. Fix: fail closed on any non-unity provider-internal jump whose priors match the pre-jump closes.
- **T3-1-4** (low, high) data/alpaca.py:668, 314-326, 721-724 — the cool-down clock starts at request start. With the 30 s timeout and 30 s base delay, the first cool-down has already expired when it is recorded (effective delays 0/30/90/210/270 s). Fix: take `now` inside `_note_provider_failure`.
- **T3-1-5** (low, high) alpaca.py:344-408, runtime.py:4068, 5541 — `get_session_daily_bars` skips the cool-down gate and failure accounting, contrary to the X2 claim. Fix: route it through the same gate.
- **T3-1-6** (low, medium) alpaca.py:327-341, 817-825 — rebase invalidation is overlap-only, so old-basis cache rows outside the answered window survive, contrary to the docstring. Small practical impact.

## D3-1 — docs vs code

*Orchestrator note: I checked D3-1-1 (operator_runbook.md:586-587 says "not sticky") and D3-1-2 (ui.js:264 shows only "Paper"/"CANLI") against the code. Both are confirmed and both are docs-only.*

- **D3-1-1** (medium, high) docs/operator_runbook.md:586-587 — the runbook says a dashboard Stop is "not sticky", but the code and operations.md:187-191 write OPERATOR_STOPPED and keep the engine stopped across restarts. An operator restarting during a deploy will think the engine failed to start.
- **D3-1-2** (medium, high) docs/dashboard.md:29 — the documented mode badge (green PAPER, red LIVE only while trading, dimmed "LIVE (devre dışı)") does not exist. ui.js:264 shows "Paper" or "CANLI" from runtime.mode alone, regardless of engine state.
- **D3-1-3** (medium, high) docs/operations.md:187-191 — the doc does not say what happens when the stop-marker write fails: the Stop still takes effect but does not survive a restart. The only signal is `runtime_start_failed` with reason=`operator_stop_unrecorded`, which is not in the alert table.
- **D3-1-4** (low, high) operations.md:442-445 — the "Known gap" note for ops events is stale. The read is now strict and a failure exits 1 (cli.py:829, 1817-1823).
- **D3-1-5** (low, high) operations.md:301-309 — the reconcile-pending latch has a fourth reason, `empty_positions_read` (runtime.py:2622-2624), which no doc mentions.
- **D3-1-6** (low, medium) operations.md:296-298, runtime.py:6255 — "exits are never blocked" contradicts the wait-then-decide semantics: while the latch is set, the SELLs in the batch wait until about 38 minutes after the close.
- **D3-1-7** (low, high) operations.md:266-285 — the watchdog section omits the 600 s startup grace.
- **D3-1-8** (low, high) operator_runbook.md — the new behaviours in §9 (provider cool-down of 30 s to 5 min; refusal on unknown top-level keys and quoted booleans) are not in any operator doc.
- **D3-1-9** (low, medium) operations.md:658-661 — the restore commands install the peak sidecars without a `[ -e ]` guard, contradicting the text at :670. The LIVE line fails with "cannot stat" on PAPER hosts.

## Q3-2 — new code paths without tests (X1–X4 and the integration commits)

*Orchestrator note: I read health.py:138-150 for Q3-2-1. `stop_failed` is grouped with a planned stop and is returned before the staleness check, so this is confirmed. It is a production bug, not only a test gap.*

- **Q3-2-1** (medium, high) monitoring/health.py:140-147, runtime.py:2205 — REGRESSION of D2-6-3. `stop_failed` is treated like a planned stop, so a hung process with workers that did not stop is never reported CRITICAL "heartbeat stale". It stays BLOCKED. Fix: take the planned branch only for `state == "stopped"`, and add a test with a 5-hour-old `stop_failed` snapshot that expects CRITICAL.
- **Q3-2-2** (medium, medium) runtime.py:2597, 1104-1138 — the account-loop readvance is tested only against a stub. No integration test shows that a held BUY actually reaches the broker, or that the readvance runs after the publish.
- **Q3-2-3** (low, high) runtime.py:6331-6341 — the trigger branch of the per-order silence clock (`max(last_seen, _trade_feed_probe_at)`) is never exercised. Every test sets `_trade_feed_probe_at=None`.
- **Q3-2-4** (low, medium) utils/http.py:10-31 — `is_network_failure`'s 429/5xx and None-status branches and its `__cause__`/`__context__` chain walk are untested. The cool-down is tested only with ConnectionError and RuntimeError.
- **Q3-2-5** (low, high) runtime.py:2186 — the 8 s vs 5 s stream join depends on the thread name. The test checks only the constant and the name literal.
- **Q3-2-6** (low, high) runtime_service.py:1956, 3214, execution/portfolio.py:1055 — the wiring of `december_calendar_fallbacks` through planner.plan is untested; only the helpers have unit tests.

## R3-2 — restart over the existing VM state

*Orchestrator note: on R3-2-5, the VM restarted cleanly on 2026-10-27 at 22:27 UTC on 85a0443f, which is the same code as main. That start ran the strict doctor, so the drop-ins and the .env permissions were in place at that point. The finding remains as a reminder for a future daemon-reload or unit re-copy. I read engine.py:524-534 for R3-2-2 and confirmed it.*

- **R3-2-1** (medium, medium) portfolio/runtime_service.py:3088-3091, 811-814 — INCOMPLETE of R2-8-2. When the config digest changes mid-session and the engine restarts, the open rebalance is closed as CLOSED_UNRESOLVED. `_session_decided`, however, checks every digest, so the D2 decision for the same session is refused as `duplicate_decision`. The new target therefore waits until the next session, and the dropped BUY is never re-planned. Fix: exclude decisions closed by a digest change from the session check.
- **R3-2-2** (low, high) web/managers/engine.py:528, 533 — INCOMPLETE of S2-6-1. `app.auto_start_engine` is still read raw from the dict, so `"false"` in quotes is truthy and the engine auto-starts. Fix: validate it with `_require_bool` and read the dataclass field.
- **R3-2-3** (low, medium) live_safety.py:116, config.py:638-642 — the `mode_credentials` merge (`{**os.environ, **file}`) is the reverse of the engine's dotenv order (override=False), and a blank file value overrides the process value. When the parsers disagree on an inline `#` comment, `doctor --strict` can block the start. Fix: use the same order as the engine, or refuse on any difference, and correct the docstring.
- **R3-2-4** (low, low) runtime.py:1857-1859 — if writing the approval nonce record fails after the start commit, the approval stays replayable (FileNotFoundError is read as "not consumed"). LIVE with a manual approval only. Fix: record it before the commit, or stop the runtime when the write fails.
- **R3-2-5** (low, medium; operational) deploy/systemd — since wave 2, a missing drop-in (trading-host, start-timeout) or a loose .env permission makes every start fail. This is not a code defect. Check the drop-ins and the .env permissions before any daemon-reload or unit copy.

## C3-2 — threads that now submit orders

*Orchestrator note: I read runtime.py:3270-3332 for C3-2-1. `failures.pop` runs on every advance that does not raise, including one where `submit_now=False`, so the finding holds. It overlaps P3-1-4 and raises it to medium: since X1 the readvance runs every 60 s, so a single no-op pass lifts the latch by itself. One mitigation: an ambiguous submit usually also sets the reconcile latch, which keeps trading_state off ACTIVE. Even so, the B1 contract is broken. C3-2-2 overlaps P3-1-2 and adds detail; fix them together.*

- **C3-2-1** (medium, high) runtime.py:3274-3331, runtime_service.py:2202-2205 — SAME AS P3-1-4, with a stronger scenario. An advance with the BUY side held by the halt does nothing and returns None. That None is read as "the advance works again", which lifts `rebalance_reconciliation_required`. The readvance repeats this automatically every 60 s. The B1 test stubs `trading_state` as ACTIVE (test_audit2_fix_B1.py:257, 292), so it cannot see the case. Fix: lift only when `submit_now` was true and the advance did not raise, and add a test that uses the real `trading_state`.
- **C3-2-2** (medium, medium) runtime.py:1134-1138, 3285, 4537 — SAME AS P3-1-2. The account loop takes `_portfolio_lock` with a blocking acquire and then makes broker calls under it. While the decision holds the lock, the drift sync, feed freshness, intent watchdog, readiness and publish all wait. A hung broker call hangs the account loop too, which feeds the web watchdog's 120 s check. Fix: non-blocking acquire, skipping the pass when the lock is busy.
- **C3-2-3** (low, medium) runtime.py:1176, 2596-2598 — the readvance sizes BUYs from the pre-lock account snapshot, even when that snapshot was invalidated in the same pass. There is no lease re-check like the one the decision path does at 4527. The error is conservative; no over-buy could be built from it.
- **C3-2-4** (low, low) runtime.py:1783-1861, 1195-1218, 5756-5770 — BUY advances from the trade-update and monitor paths are not gated on `_startup_committed`, unlike the readvance and retry paths. A narrow window, with no reproduction.

## F3-2 — apply_trade_update after I2/G1

*Orchestrator note: I read runtime.py:3463-3470 and 6305-6348 for F3-2-1 and confirmed it. The session gate returns before pruning, so stamps written after the close stay unchanged overnight. This went live on the VM with X2 (85a0443f, deployed 22:27 UTC on 10-27), and the first session open where it can show is 10-28 at 13:30 UTC, but only on a day the bot holds a DAY order queued overnight. The effect is one reconcile, one latch and possibly an alert. Entries are held until the reconcile clears, and orders already sent are not affected. Restarts empty the stamps, which hides the problem.*

- **F3-2-1** (medium, high) runtime.py:3466-3470, 6315, 6336-6337 — a DAY order sent at around 16:20 ET keeps its after-hours stream stamp. At the first in-session pass at 09:30 the order looks silent for about 17 hours, so `trade_feed_stale` latches and a reconcile runs. This repeats on every morning that follows a decision. The X2 test covers only the in-session case. Fix: clamp the reference to the session open (`max(last_seen, trigger, session_open_utc)`), or drop the stamps of open orders when the session is closed.
- **F3-2-2** (low, high) runtime.py:3509-3513 — INCOMPLETE of F2-2-4. A fill the ledger refuses still only calls `_schedule_reconcile()`. If a reconcile is already running, this does nothing, and entries are not latched. The refused frame also refreshes the stamp. Fix: `_set_reconcile_pending("refused_fill_frame")`.

## S3-2 — redaction after G1

*Orchestrator note: S3-2-1 and S3-2-2 both send the full account id off the host by e-mail, so both are medium. On the VM, e-mail alerting goes to the owner's own inbox, which lowers the real exposure.*

- **S3-2-1** (medium, high) monitoring/incident.py:180-186, paper_incident.py:575-582, runtime_repository.py:2404 — INCOMPLETE of S2-2-1. `episode_key` contains the full broker account id (G1 added this for the ownership surplus). `redact_text` has no account rule, so the id reaches the journal fields, ops events, the bundle and the e-mail body. Fix: build the key from a sha256 prefix or `mask_account_number`, or have `redact_text` mask the registered account id.
- **S3-2-2** (medium, medium) execution/risk_peak.py:174, 255, 291, runtime.py:2440-2459 — exception text that contains the account id becomes the incident `error`, then the circuit_breaker alert message, and is carried into the journal, the e-mail and the bundle. Fix: mask it in the message, or mask the registered id in `redact_text`.
- **S3-2-3** (low, high) risk_peak.py:288, 335-337 — two log calls write the full account id (line 398 of the same module masks it).
- **S3-2-4** (low, high) monitoring/operational_event.py:185-188 — because of the regex order, `Authorization: Bearer <token>` leaves the token visible. I confirmed this with copies of the regexes. Latent: no current path logs a header.
- **S3-2-5** (low, medium) logger.py:54-57 — the root logger has no handler, so third-party WARNING records go to `lastResort` and skip RedactingFormatter.
- **S3-2-6** (low, medium) monitoring/email_alerts.py:133-143 — INCOMPLETE of S2-2-2/S2-2-6. `_scrub_error` does not scrub `smtp_host`.
- **S3-2-7** (low, high) operational_event.py:191-195 — the free-text scrub stops at whitespace (secrets with spaces leak), does not match "api key:" or "secret key=", and has no approval or webhook keywords.
- **S3-2-8** (low, medium) cli.py:466, 1331, 2270, tax/cli.py:200, engine.py:1661, 1929 — `register_secret_text` is used only in the runtime and one engine path. Fix: register the key and secret in `AlpacaBroker.__init__`.
- **S3-2-9** (low, medium) logger.py:20-29 — a formatter exception falls through to `handleError`, which writes the raw msg and args to stderr.

## T3-2 — provider cool-down vs decision timing

*Orchestrator note: I read runtime.py:5405-5422 and 270-275 and confirmed T3-2-1 and T3-2-2. T3-2-1 is a regression that X2 (the cool-down) introduced: the verifier's 0.5 s retry now always lands inside the 30 s cool-down. T3-2-2 is older (not wave 2), but it is reported here because the retry window was raised to 40 minutes.*

- **T3-2-1** (medium, high) runtime.py:5409-5421, data/alpaca.py:670-676, 314-321 — REGRESSION of X2. The verifier's one transient retry (0.5 s later) always hits `HistoricalProviderCooldown`, so a single fast network blip at decision time holds the whole equity book, exits included, for that session (`signal_adjustment_unverified`). The test uses a fake `_historical`, so it never reaches the cool-down gate. Fix: let the in-call retry bypass the gate, or wait until `cooldown_until`, bounded by the retry budget. Alternatively, make a transient failure a recoverable gate that defers the batch within the retry window.
- **T3-2-2** (medium, medium) runtime.py:273-274, 4025, 4101-4110 — the session-close SIP fallback stops after 4 one-minute attempts, around close+18 min, while the decision window runs to close+40 min. Empty answers also count as attempts. A short outage of about 3 minutes, or a late SIP bar, loses the session. Fix: retry until the same deadline `_defer_portfolio_batch` uses.
- **T3-2-3** (low, high) data/alpaca.py:213-234, runtime.py:1517 — the cool-down is shown only as raw fields. It does not set the degraded flag or raise an alert, and `provider_cooldown_until` is not cleared once it lapses.
- **T3-2-4** (low, medium) runtime.py:6947-6994, 5846-5884 — within one warmup-retry round, a fast error on the first batch skips every later batch through the cool-down, and the round still uses up a backoff slot.

## P3-2 — liveness (lost, stuck or doubly decided sessions)

*Orchestrator note: I read runtime.py:4553-4566 for P3-2-1. The halt that the in-lock re-read catches ends in a `continue` with no deferral, so the finding is confirmed. P3-2-1 is a coupling gap between C9-2 and R9-2/B-1. The agent's status table (park / consumed / retried / dropped) is in the "Checked but fine" section of the report. Its summary: warming_up, incomplete_strategy_batch, incomplete_managed_batch and no_eligible_bars are dropped (P3-2-4), and residual_sell_submitted is dropped (P3-1-1).*

- **P3-2-1** (medium, high) runtime.py:4337-4349 vs 4553-4566 — when a hard halt is raised during the lease fetch or the lock wait, the in-lock re-read drops the session (`continue`) without `_defer_portfolio_batch`. There is no deferred key, no alert and no retry window, so the session is not decided even if the operator resets within the window. Fix: call `_defer_portfolio_batch(relevant, market, f"halted:{live_breaker}")` before the `continue`.
- **P3-2-2** (medium, high/medium) runtime.py:5018-5025, 6049-6075, 6726-6728 — when the startup catch-up is blocked by the soft reconcile latch, which clears itself, it is never re-run. A periodic reconcile clears the latch about 60 s later, but catch-up is retried only on `reset_safety_lock`. A session that closed just before the restart is lost. Fix: run `_run_startup_catch_up(blocked_only=True)` after a clean reconcile clears the latch.
- **P3-2-3** (medium, medium) runtime_service.py:2197-2200, 2747, 3174-3186 — the 4-day stuck-leg rule (P2-7-4) does not cover BLOCKED or the `replan_order_in_flight` hold. One non-terminal leg (held, stopped and so on) freezes the market's decisions forever, and an operator reset cannot clear it. Fix: apply `_stuck_leg_ids` and the age check in both places.
- **P3-2-4** (low, medium) runtime_service.py:1460-1496, 1632-1646 — `warming_up` (a symbol below minimum history after warmup), `incomplete_strategy_batch` and `incomplete_managed_batch` are neither parked nor deferred on the decision path. The session drops with no alert. Fix: send these through `_defer_portfolio_bars`.

## D3-2 — Turkish UI and alert text consistency

*Orchestrator note: I read runtime.py:512-531 for D3-2-4. When equity is turned off, `_strategy_specs` is empty and the constructor raises, so the hint at base.html:137 is wrong and the owner's switch leaves the engine unable to start (confidence raised to high). I also read ui.js:468 for D3-2-3 and confirmed it. D3-2-1, -2, -5, -6 and -7 are all the same class: English refusal text, check codes and halt codes shown in the Turkish UI.*

- **D3-2-1** (medium, high) web/schemas/settings.py:51-58, services/settings.py:369-370, ui.js:603 — refusals on settings save and 422 errors reach the Turkish toast in English, for example "Stop the bot before saving settings without a restart".
- **D3-2-2** (medium, high) web/managers/engine.py:1815-2050, ui.js:509-516 — the "Canlıya geçiş ön koşulları" checklist is entirely English.
- **D3-2-3** (medium, high) ui.js:468, routers/safety.py:100-103 — the Paper-switch dialog says "Bot demo hesapla çalışmaya devam eder", but the server refuses the switch while the engine is running.
- **D3-2-4** (medium, high) templates/base.html:137, runtime.py:529-530 — the hint says the position keeps being monitored when "Hisse/ETF işlemleri" is turned off. In fact EngineRuntime raises `At least one enabled strategy needs an active universe` and the engine does not start. Fix: change the hint to say so, or add a monitor-only mode (needs an owner decision).
- **D3-2-5** (medium, high) ui.js:123-128, 326, 431, dashboard.js:518-532 — BREAKER_LABELS maps only 6 codes, so portfolio_runtime_failure, decision_worker_crashed, trade_update_callback_failure, final_market_event_queue_overflow, account_fetch_failures and *_restart_limit appear raw. The buy-capacity reasons are mixed into the "Mutabakat" row.
- **D3-2-6** (medium, high) runtime.py:6576-6639, engine.py:1192, 1666, routers/engine.py:87 — the reset, LIVE start, mode arming and backtest refusals are in English ("İşlem başarısız: Operator halt marker is still present…").
- **D3-2-7** (medium, high) monitoring/health.py summaries, dashboard.js:644-653 — the health-check panel shows English codes, raw OK/CRITICAL/BLOCKED badges and English summaries.
- **D3-2-8** (low, medium) health.py:453 — the market entry-halt action says to press "Kilidi doğrula", but in that state the button is hidden and the reset is refused.
- **D3-2-9** (low, high) ui.js:421-422 — the Stop toast says "Motor durduruldu" even when the server answered "stopping".
- **D3-2-10** (low, high) ui.js:594 — the restart toast mentions only address/port, but log_level and open_browser also need a restart.
- **D3-2-11** (low, high) dashboard.js (several) — raw English tokens: source, scanner state, ownership kind, trend_up/early_reentry, "Leveraged index trend v1", the lineage id and engine_error.
- **D3-2-12** (low, medium) dashboard.js:661, email_alerts.py:190-212 — the "Son olaylar" feed and the e-mails are in English. The language choice needs a decision.
- **D3-2-13** (low, high) ui.js, health.py:500, index.html, tools.js — inconsistent terms: CANLI vs LIVE (the confirmation phrase), the risk latch vs the new-entry latch, Kıyas vs Benchmark, drawdown vs Zirveden düşüş.
- **D3-2-14** (low, high) ui.js:319, api.js:68 — cosmetic issues: literal backticks in the banner, lowercase fallback messages, and the raw "Failed to fetch" text in toasts.
- **D3-2-15** (low, medium) ui.js:459 — the LIVE confirmation says "geri alınabilir". Orders already sent cannot be undone, so the wording is misleading.

## Q3-3 — conftest isolation after T3/L2 and the redirect

*Orchestrator note: I read test_start_failure_alerts.py:73-93 and engine.py:455-492 for Q3-3-1. The test points config_path at the shipped config/settings.yaml, and start_engine() → `_clear_operator_stop_marker` unlinks the relative path `data/runtime/OPERATOR_STOPPED`, which is unguarded. The path is confirmed. The VM's deploy checklist does not run pytest on the VM (FINDINGS §7 makes no mention of it), so the live risk is the local checkout's data/runtime. Even so, this breaks the AGENTS.md rule that tests never write production paths, so it is high.*

- **Q3-3-1** (high, medium-high) web/managers/engine.py:488-491, 933; tests/test_start_failure_alerts.py:77, 93; test_auto_start_retry.py:479, 555, 610; test_audit2_fix_E2.py:29, 192, 214, 230; test_web_engine_manager_fixes.py:45 — explicit start_engine() tests delete the real `data/runtime/OPERATOR_STOPPED` marker. conftest only hooks open, os.open and sqlite3.connect, so `Path.unlink` is unguarded. Teardown catches the change only after the file is gone. Fix: point these tests at `isolated_config_path` or a tmp `_operator_stop_marker`, and add unlink, rename, replace, mkdir and rmtree hooks to conftest.
- **Q3-3-2** (medium, high) tests/conftest.py:42-57, 265-266, 299-316, 477-502 — the guard misses deletes, renames, mkdir and rmtree. The config/ tree is not in the after-the-fact signature, and the raw-string prefilter runs before resolve(), so a symlink skips it. Fix: add the hooks, add config/ to the signature, and resolve before the prefilter.
- **Q3-3-3** (low, medium) conftest.py:42-49, backtest/product.py:716 — reports/ is outside the guard, so a backtest with the default config writes into the operator's reports/ tree. Latent.
- **Q3-3-4** (low, medium) conftest.py:220-226 — CONFIG_PATH is not cleared at session start. If the developer shell exports it, every default `load_config()` reads the real file.
- **Q3-3-5** (low, high) web/app.py:108 — web main() leaves the process umask at 0o077 for the rest of the session. Fix: restore the umask in an autouse fixture.
- **Q3-3-6** (low, low) logger.py:54 — the parayabuck logger's `propagate=False` is never restored, which could make caplog tests order-dependent. The green suite argues against this, so verify it with a focused run.
- **Q3-3-7** (low, high) conftest.py:730-740 — journal teardown restores the old global, then immediately stops it and sets it to None. Latent.
- **Q3-3-8** (low, high) conftest.py:97-127 — the network guard does not block getaddrinfo (DNS) or UDP sendto/sendmsg.
- **Q3-3-9** (low, medium) tests/test_dashboard_api.py:~515, test_web_security_invariants.py:~262 — subprocess children run outside every in-process guard.

## R3-3 — backup/restore after M1/M2/O2/M3

*Orchestrator note: I read operations.md:650-662 for R3-3-1. The restore procedure hard-codes the PAPER paths, so the finding is confirmed. It is latent: the VM runs PAPER only, so the procedure is correct there today, and it becomes a blocker before LIVE. I read cli.py:1180-1190 for R3-3-4: stale backups produce only a doctor WARN row, with no emit. Confirmed.*

- **R3-3-1** (high, latent before LIVE; high) docs/operations.md:654-661, config.py:60-61, runtime.py:1577 — the LIVE-host restore installs the DB, peak and halt copy into PAPER paths (data/runtime/...). This writes the LIVE snapshot over the PAPER ledger, leaves data/runtime/live/ stale, and lets the LIVE engine start unhalted. Fix: derive every restore path from `dirname(runtime.decision_db_path)` for the active mode.
- **R3-3-2** (medium, high) operations.md:657-662, risk_peak.py:265-269, 325-349 — when the restored set has no peak sidecar, the current on-disk sidecar (a peak from a newer time) stays in place and the runtime adopts it. No `peak_bootstrap` alert fires, so the check the doc tells the operator to make catches nothing. Fix: move the current sidecar aside to $EVIDENCE.
- **R3-3-3** (medium, medium) backup.py:58-64, web/managers/engine.py:52-55, 514-521 — OPERATOR_STOPPED is neither in the backup set nor in the restore procedure. After a restore, `systemctl start` lets auto-start resume PAPER trading. Fix: add a step to the doc, or include the marker in the set.
- **R3-3-4** (medium, high) cli.py:1181-1188, monitoring/events.py:25-27 — passing the 12 h stale-backup limit raises no alert; the only signal is the WARN row in doctor. If the timer stops, or the unit fails before Python starts (for example a missing EnvironmentFile), no one is told. The events.py comment claims an alert that no code emits. Fix: emit `backup_failed(reason=stale)` from a periodic health or engine check, or carry the backup age in the dead-man ping.
- **R3-3-5** (low, high) cli.py:667-685 — a set that fails the post-publication verify stays on disk, and freshness counts it as the newest backup. Fix: delete it the same way `backup_database` does.
- **R3-3-6** (low, high) parayabuck-backup.service TimeoutStartSec=3600, backup.py:41, 443-447 — the worst-case serial off-host copy is 7 × 900 s, which exceeds the 3600 s start budget. On SIGTERM, `backup_failed` is not emitted.
- **R3-3-7** (low, high) config.py:737-738 — the 1-day retention floor is enforced only on the CLI flag. settings.yaml accepts 0.1, which leaves a single set.

## F3-3 — rebase-ownership CLI, surplus and stale clears

*Orchestrator note: I read runtime_repository.py:1350-1368 and alpaca_account.py:295-313 and 600-616. F3-3-1 and F3-3-2 are confirmed: the SQL compares timestamps only, with no status or quantity filter. Their impact is lower than the agent rated. The broker is already flat, so clearing ownership matches the broker; the real damage is that the clear is silent (no alert) and books no disposal or P&L for the tax ledger. I lowered both from high to medium. F3-3-3 is also confirmed (one order has to cover the whole shortfall), but it needs two SELLs open at the same time for one symbol. With VT35 on QLD alone, that happens only with a residual plus a replan, so I lowered it to medium.*

- **F3-3-1** (medium, high) portfolio/runtime_repository.py:1317-1322, 1350-1368 — the stale clear takes any local SELL after the last BUY as proof, whatever its quantity, and clears the whole residue. For example, after a 10-share exit the remaining 90 shares are cleared with only a log warning. Fix: require the unbooked quantity of SELLs after the last BUY to cover `owned`; otherwise refuse and alert.
- **F3-3-2** (medium, high/medium) runtime_repository.py:1350-1368, execution/alpaca.py:455-457, 467 — dead (canceled, rejected, expired) or never-submitted (404) SELLs also count as the sale that explains a flat broker. Fix: count only live SELLs or SELLs with `cumulative_filled > 0`. Do together with F3-3-1.
- **F3-3-3** (medium, medium) execution/alpaca_account.py:295-312, 610-614, runtime_repository.py:1494, 2257-2294 — a shortfall covered by the sum of several open exits is booked automatically as `FOREIGN_SELL`. When those exits fill, the same shares are subtracted again, leaving owned=0 while the broker holds 50. The bot then stops managing them. Fix: subtract the remaining quantity of all open SELLs from the shortfall, or refuse the automatic rebase while a non-terminal SELL exists.
- **F3-3-4** (medium, high) alpaca_account.py:691, alpaca_reconciliation.py:590, runtime_repository.py:2389-2391 — INCOMPLETE of F2-9-1. Three operator hints still print `--confirm` without `--plan-digest`, which the CLI refuses with exit 64. This is the copy-paste command during an incident. Fix: print the two-step flow, and add a test that parses each hint with the real argparser.

## C3-3 — web manager state machine

*Orchestrator note: I confirmed C3-3-1 (engine.py:1361-1363, an early return when the state is not "starting") and C3-3-2 (engine.py:947, get_credentials sits outside the try) by reading the code. Both wedge the state machine so that only a process restart recovers it, and the closing watchdog gate then sends systemd SIGABRT. C3-3-5 is a different consequence of R2-4-4.*

- **C3-3-1** (medium, high/low likelihood) web/managers/engine.py:1076, 1361-1363, 1256-1259, 633-634 — INCOMPLETE of C2-6-1. If a Stop lands after a transient auto-start failure, the handler sees "stopping" and returns without settling the state, so the state stays "stopping" forever. Manual Start is refused, and 150 s later the watchdog gate closes and systemd kills the unit. Fix: in the wait branch, when the state is "stopping" and runtime is None, set "stopped".
- **C3-3-2** (medium, high/low likelihood) engine.py:947, 941-943, 335 — on a manual start, `get_credentials()` runs outside the protected block. An unreadable or non-UTF-8 .env leaves the state at "starting" permanently, which ends in the same watchdog kill. Fix: move the call inside the try, or settle "starting" to "error" in a BaseException handler.
- **C3-3-3** (low, medium) engine.py:1229, 1237, 933 — Stop writes OPERATOR_STOPPED before taking `_state_lock`. A Start in between deletes the marker and is then stopped, so the result is a stopped engine with no marker, and the next reboot auto-starts it. Fix: write the marker inside the lock.
- **C3-3-4** (low, medium) engine.py:398-399, 905-938 — `request_shutdown` sets `_start_cancel` outside the lock, so a concurrent start can clear it (an R2-4-2 gap with a millisecond window).
- **C3-3-5** (medium, medium) engine.py:1260-1266, 1417, 1435, 633-634 — a Stop queued behind a slow reset or risk-epoch reconcile (for example during a broker outage) starts the stop clock first. After 150 s the watchdog gate closes and systemd sends SIGABRT while the runtime is still trading. Fix: start `_stop_started_monotonic` after the lock is acquired, or refuse reset while a stop is pending.

## T3-3 — backtest/live parity after I1

*Orchestrator note: these are parity findings, not trading bugs. None of them changes the validated VT35 numbers today (the agent compared the fill timing, limits, bands, December deferral and gain-band application point, and they match). T3-3-2 is the only medium: a PAPER BUY on a gap-up overshoots the cash buffer, while the backtest clamps it. I did not spot-check the code; I recorded the agent's findings as reported.*

- **T3-3-1** (low, high) portfolio/runtime_service.py:3753-3787 — LIVE's 3% gap-safety cap lands an entry at about 95% instead of 98% in calm regimes, and the gap closes over 1-2 sessions. A deliberate trade-off; it only needs documenting. LIVE is currently disabled.
- **T3-3-2** (medium, medium) runtime_service.py:3756-3764, backtest/portfolio_engine.py:1095-1168 — a PAPER BUY is sized at the close and is not clamped to the open fill. On a gap-up the 2% cash buffer is exceeded, and above about 2% gap the cash goes negative (margin). The next session then trims about 1pp. The backtest clamps, so the comment that the "validated PAPER/backtest basis" is identical is wrong. Fix: document the divergence, or model it in the backtest.
- **T3-3-3** (low, medium) execution/portfolio.py:646-650, 1037-1038 — sizing mixes the broker equity (snapshot around 15-25 min after the close) with the decision-close price. After-hours moves can push the size off by about 0.7pp. Fix: build sizing equity from cash plus qty × the decision price.
- **T3-3-4** (low, high) execution/portfolio.py:619, backtest/portfolio_engine.py:343 — the drawdown halt is enforced live but only shadowed in the backtest. The 75% DD threshold sits far above the validated window's 27.7%, so the validated results are unaffected. Stress tests are affected.
- **T3-3-5** (low, medium) backtest/portfolio_engine.py:1557-1672 vs execution/portfolio.py:950-1207 — the backtest does not call the shared planner, and its copy already differs in increments and floors. Identical for VT35 today, but this is drift risk. Fix: have the backtest call `TargetToOrderPlanner.plan`.

## S3-3 — file permissions, temp files and durability after M2

*Orchestrator note: no critical, high or medium findings in this lens. The atomic_write core, backup publication, the live_safety reads and the .env rewrite all hold. Every finding below is low. S3-3-8 is a mistaken-commit risk, made more serious by the repo being public. I did not spot-check these against the code; they are recorded as the agent reported them.*

- **S3-3-1** (low, high) web/managers/engine.py:513, live_safety.py:564 — INCOMPLETE of R2-9-3. The OPERATOR_STOPPED auto-start gate and the standing-LIVE revoke still use `os.path.lexists`, which treats any lstat error as "absent" and fails open.
- **S3-3-2** (low, high) monitoring/diagnostic.py:505-511 — the ops-bundle staging file gets no fsync before `os.replace`, and the directory gets no fsync either, so a power loss can publish a truncated bundle.
- **S3-3-3** (low, high) monitoring/receipt.py:51, 75, diagnostic.py:505 — temp files left behind by SIGKILL (receipts, bundles, state-dir atomic writes) are never swept.
- **S3-3-4** (low, medium) cli.py:1158-1161, diagnostic.py:447-449 — the doctor write probes use fixed names and `write_text`, so they follow a planted symlink.
- **S3-3-5** (low, medium) execution/risk_peak.py:31, backup.py:250 — peak-sidecar reads use no O_NOFOLLOW or O_NONBLOCK. A FIFO there would hang the engine start or the backup.
- **S3-3-6** (low, medium) live_safety.py:274, engine.py:491 — clearing the halt and stop markers does not fsync the directory, so after a power loss a marker the operator already cleared can reappear. This fails closed, but it misleads the operator.
- **S3-3-7** (low, medium) tax/export.py:311-312, tax/rates.py:355, 360 — the CSV and the summary are written as two separate atomic writes, so a failure between them leaves a mismatched pair.
- **S3-3-8** (low, medium) .gitignore:165-183 — `data/tax/` is not in .gitignore. A tax export written there by mistake can be committed (the repo is public).

## P3-3 — per-order submit gates after the merges

*Orchestrator note: I read runtime_service.py:1995-2014 and 3988-3998 for P3-3-1, and confirmed it. On the pure-SELL path the row is written as "submitting" before the gate check, which contradicts the gate comment "Held before the row is written". For P3-3-2 I read runtime.py:1196-1220: the basis check applies only to BUYs, so the mechanism is confirmed. However, the residual/retry behaviour conflicts with §8.3/§9 ("the whole position is frozen under the basis latch"), so this needs an owner decision. Note that Alpaca PAPER accounts allow shorting by default, so the oversell that P3-3-2 describes on a reverse split can open a short.*

- **P3-3-1** (medium, high/medium) portfolio/runtime_service.py:1995-2013, 3992-4003, 1149-1153, 1858 — when the decision-path gate holds a SELL from a pure-SELL decision, the order stays "submitting". There is no rebalance row, so neither the residual path nor the rejected-retry path re-sends it. After the reset, `_resolve_stale_submitting_rows` books it as canceled, and the alert says "a re-plan may send it", but no re-plan comes. The exit is lost until the next session. Fix: persist the pure-SELL decision with SELL_PENDING, or mark held rows for re-send, and correct the comment at 3995.
- **P3-3-2** (medium, high; needs an owner decision) runtime.py:1195-1220, 1241-1257, 5679-5681; runtime_service.py:2276-2300 — under the basis latch, the advance-path residual SELL and the rejected-exit retry still go out. The decision path holds the whole book, and §8.3/§9 say the freeze is deliberate. On a reverse split, a residual sized from pre-split rows can sell more than is held, which on PAPER opens a short. Fix: hold EQUITY SELLs under the cached basis latch in both gates. If the owner wants exits to flow instead, correct §8.3/§9, the docstring and test_audit2_fix_F.py:325.
- **P3-3-3** (low, high) runtime_service.py:3295-3316 — the deferred-BUY loop calls `_submit_orders` without `submit_gate`. The gate is not re-read right before the POST; the window is local I/O only. Fix: pass the gate, but resolve the pre-recorded "submitting" row first, the same problem as P3-3-1.

## Q3-4 — JS source-fragment pins vs real behaviour

*Orchestrator note: I confirmed Q3-4-1 (dashboard.js:348 `slice(19)` while the prefix is 18 characters, which is a real UI bug) and the `shell.paper: true` default in Q3-4-2 (ui.js:258). The server's LIVE arming gate still applies, so Q3-4-2(b) loses only the UI confirmation and does not open a real-money start by itself.*

- **Q3-4-1** (medium, high) web/static/js/dashboard.js:347-348 — the "recovered_session:" prefix is 18 characters but the code uses `slice(19)`. The catch-up label drops the nested status's first letter ("ubmitted") and misses the map. The N1 test does not reach this branch. Fix: use `slice("recovered_session:".length)` and add a node test.
- **Q3-4-2** (medium, high/medium) ui.js:258, 404-406, base.html:88 — the pin `"await confirmLiveStart()" in source` stays green on a mutant that ignores the result. Separately, the `shell.paper: true` default skips the CANLI dialog if Start is clicked before the first status reply. Fix: pin the full statement, initialise `shell.paper` to null and block Start while it is unknown.
- **Q3-4-3** (medium, high) ui.js:412-420 — no test covers the Stop confirmation. Deleting `if (!ok) return;` keeps the suite green, and one click then makes a persistent stop.
- **Q3-4-4** (medium, high) web/static/js/api.js:7-35 — no test pins the JS control-token header name. A rename makes every control request return 403, and the UI loses its stop path.
- **Q3-4-5** (low, high/medium) ui.js:280, 325-326, 642 — reset-button visibility is computed in two places. With breaker=reconciliation_failed and state=running the button is hidden while the message says to click it. The stream path shows it with no lock.
- **Q3-4-6** (low, high) ui.js:318-320 — nothing pins the operator-halt banner text.
- **Q3-4-7** (low, medium) base.html:215-217, ui.js:177-182, 465 — pressing Enter in the LIVE phrase field submits the first submit button, which is Cancel, so arming is cancelled silently. This fails closed.
- **Q3-4-8** (low, high) tests/test_audit2_fix_N1.py:285-297, dashboard.js:32-41 — CATCH_UP has no entry for buy_phase_submitted, replan_order_in_flight or signal_cross_check_pending, so they show as raw tokens. The test uses a hand-picked list.
- **Q3-4-9** (low, high) test_audit2_fix_N2.py:249, 339, test_audit_20261011_w4.py:348-367 — `"Bilinmiyor"` and `"risk"` also match elsewhere in the source, so the branch the test names can be deleted while the test stays green.

## D3-3 — alert and e-mail kind coverage

*Orchestrator note: D3-3-1 is the same finding as R3-3-4 and is reported from the alert-coverage side. Fix them together as one item, at the higher severity (high). The kinds that are emitted but never e-mailed are decision_deferred, signal_source_mismatch, session_close_bar_incomplete, runtime_started, reconciliation_recovered, signal_adjustment_recovery and alert_delivery_failed. The last one is excluded by design. I did not spot-check the code for the D3-3 items.*

- **D3-3-1** (high, high) monitoring/events.py:25-26, cli.py:1177-1194 — SAME AS R3-3-4. When the backup timer stops, or the unit fails before Python runs, no alert is sent. The freshness alert that the vocabulary promises does not exist.
- **D3-3-2** (medium, high) runtime.py:4175-4185, 5644-5650 — when the decision window expires and the session is "NOT decided", no e-mail is sent, because `decision_deferred` is not an e-mail kind. The only coverage is the watchdog's `equity_decision_missing`. Fix: send the expiry alert as critical, or give it its own kind.
- **D3-3-3** (medium, medium; owner decision) portfolio/signal_cross_check.py:11-14, 130-137 — a session decided on the IEX bar alone because the SIP source is missing raises `signal_source_mismatch`, which is not e-mailed. Fix: e-mail `secondary_unavailable`.
- **D3-3-4** (medium, high) runtime.py:2236-2250, forensics.py:239-273 — after an unclean exit (SIGKILL, OOM, a watchdog kill) and an automatic restart, no e-mail is sent; forensics feeds only diagnostics. Fix: e-mail a kind at startup when the previous exit was unclean.
- **D3-3-5** (low, high) cli.py:1041-1060 — a `doctor --strict` blocker in ExecStartPre emits no alert. The only channel is the dead-man.
- **D3-3-6** (low, medium) cli.py:575-578, email_alerts.py:73-74 — the backup process waits 60 s to flush e-mail, but the worst-case delivery time is about 105 s, so the e-mail can be lost silently. The comment says "three attempts" while the code makes four.
- **D3-3-7** (low, high) cli.py:338-343 — a backup with a bad config journals `backup_failed`, but the e-mail sink has not been set up yet, so no e-mail is sent.
- **D3-3-8** (low, high) events.py:34, 43, runtime.py:6051-6064 — the recovery kinds (reconciliation_recovered, signal_adjustment_recovery) are not e-mailed, and clearing the reconcile latch emits no alert.
- **D3-3-9** (low, medium) data/alpaca.py:314-324, 669-676 — the provider cool-down and decisions served from cache emit no alert. This is the alert half of T3-2-3.
- **D3-3-10** (low, high) monitoring/incident.py:16-175 — 7 incident taxonomy categories are never raised, so their runbook actions and halt flags are dead.

## T3-4 — December deferral and the gain band after I1/X3

*Orchestrator note: I confirmed T3-4-1 by reading portfolio.py:1058-1080. `deferral_case` is computed before `full_exit` and never consults it. The trigger needs a managed symbol that also holds external units. VT35/QLD holds no external units on the VM today, so in practice this is latent, but it contradicts the rule in the config comment.*

- **T3-4-1** (medium, high/medium) execution/portfolio.py:1060-1078, 1088-1099, 770 — a strategy exit from a mixed position (managed plus external units) is deferred to January. The external floor keeps the target above 0, so `deferral_case` is true, while settings.yaml:167-170 says "exits to zero are never deferred". The watchdog raises no alarm either. Fix: compute `full_exit` first, and add `and not full_exit` to `deferral_case`.
- **T3-4-2** (low, high) portfolio.py:1067-1079, 1124-1154 — the gain band can still hold the managed full exit of a mixed position, because the verdict hold has no `full_exit` check. The T7-2 exemption is incomplete. Fix: `if gap < verdict.band and not full_exit:`.
- **T3-4-3** (low, high) runtime_service.py:2851-2858, portfolio.py:252-285 — the calendar-fallback alert says "weekday fallback", but the code skips the deferral entirely and applies the ordinary rule. It also alerts on any trim in Nov/Dec, even when the gap is below the band. Fix: correct the wording, and check the band before recording.

## R3-4 — systemd lifecycle after E1

*Orchestrator note: deployment.md:136 and 145 say the VM runs `Type=simple` with no watchdog, and that vm-dropin/watchdog.conf is installed only after an orchestrator decision. Every finding that depends on the watchdog is therefore **latent on the VM today**: R3-4-1, R3-4-2, R3-4-6, and also the SIGABRT parts of C3-3-1, C3-3-2 and C3-3-5. Those C3-3 items still wedge the state machine; only systemd will not kill the unit. Before watchdog.conf is installed, R3-4-1 and R3-4-2 must be resolved. I read runtime.py:370-382 for R3-4-1 and confirmed that "decision_worker_crashed" is in `_RESTART_REARMED_REASONS`.*

- **R3-4-1** (medium, high; latent until watchdog.conf; owner decision) web/managers/engine.py:654-661, runtime.py:375-381, 3175-3188, 3960-3964 — when the decision worker crashes, its heartbeat goes stale, the watchdog kills the process, and the restart clears the halt as a re-armed reason. Trading then resumes with no operator reset. Fix: keep the gate open while halted, or drop decision_worker_crashed from the restart re-arm set.
- **R3-4-2** (medium, medium; latent until watchdog.conf) runtime.py:3881-3896, 5529-5552 — the decision worker beats only at the top of its loop. A brownout that stalls one batch past 120 s gets SIGABRT mid-rebalance. The account loop beats only at the end of a pass (2637), the same shape. Fix: beat inside the batch, before each blocking call.
- **R3-4-3** (medium, high/low-medium) web/lifespan.py:83-109, engine.py:1358 — the E1 docstring says an in-flight start attempt is "abandoned", but the default executor thread is joined at interpreter exit with no timeout. A shutdown can run past TimeoutStopSec=180 and be SIGKILLed mid-cleanup, and a stop_failed set after that point exits 0. Fix: run the attempt on a dedicated daemon thread, or give it a bounded wait, and correct the docstring.
- **R3-4-4** (low, high) runtime.py:1241-1255 — `_exit_sell_retry_blocked` does not check `_startup_cancel`. Rejected-SELL retries can still be submitted during the 30-60 s shutdown drain, which contradicts the docstring at 1198-1199.
- **R3-4-5** (low, high) web/lifespan.py:172-178 — READY=1 is sent before the socket is bound, which contradicts the unit comment.
- **R3-4-6** (low, medium; latent until watchdog.conf) parayabuck.service:5-8 — under a deterministic wedge, each watchdog cycle takes about 7-8 minutes, so StartLimitBurst=5 within 600 s is never reached and the unit restarts forever.
- **R3-4-7** (low, medium) parayabuck-backup.service — no TimeoutStopSec and no SIGTERM handler. A stop during an off-host copy leaves a partial copy with no `backup_failed` alert.

## Q3-5 — flaky and time-dependent tests

*Orchestrator note: I ran `pytest tests/test_signal_adjustment_parity.py` on 2026-10-28 at 08:11 UTC, before the close. Result: `test_decision_requires_verified_through_decision_timestamp` FAILED, 1 failed and 32 passed. This **confirms** Q3-5-1. Every CI run or local suite run before close plus 15 minutes on a session day goes red. The green CI runs on 10-27 fell after the close by chance. This is the last round of track Q.*

- **Q3-5-1** (medium, high — CONFIRMED BY RUN) tests/test_signal_adjustment_parity.py:514-528, runtime.py:5329, 5440-5446 — the test anchors decision_ts to today at 20:00 UTC from the wall clock. The verifier uses `end = datetime.now(UTC)`, so before close plus the SIP embargo the decision bar is not final and the test fails. The "lagging" half passes for the wrong reason. Fix: anchor to a fixed past NYSE session and inject the verifier clock (a `_utcnow` seam).
- **Q3-5-2** (low, low) tests/test_historical_data.py:452-453 — `_today()` uses UTC midnight. Between 00:00 and 04:00 UTC that is the previous NY day. The effect on the assertions is unverified.
- **Q3-5-3** (low, medium) tests/test_audit_fix_W09.py:619-641 — the `< 1.2 s` bound also includes the time the start thread takes to enter, so it can fail on a slow CI.
- **Q3-5-4** (low, medium) tests/test_audit2_fix_X2.py:250-297 — the stop-vs-run race is not forced with an Event, so under load the race goes untested. The 0.5 s wall-clock bound can also fail.
- **Q3-5-5** (low, medium) tests/test_email_alerts.py:274-282, test_observability_wiring.py:53-70 — the wall-clock budgets and the absence windows depend on load.

## F3-4 — FIFO lot integrity after I2

*Orchestrator note: no critical or high findings. All three are low, and none produces a silent tax figure, because the tax ledger refuses any symbol with an ownership adjustment. A doc correction is also needed: the "same-instant fills are ordered by arrival" wording in §9 Q2-8-3 does not match the code, which puts BUY first at the same instant.*

- **F3-4-1** (low, medium) portfolio/runtime_repository.py:2082-2100, 2195-2205 — when a stale clear partly covers a sale, `fifo_realized_pnl` matches a late fill to the wrong lots. Only the displayed realized_pnl is wrong; the final lots are correct and the tax ledger is blocked.
- **F3-4-2** (low, high) runtime_repository.py:2213-2218, runtime_ownership.py:55-57 — the lot band uses a relative 1e-6 tolerance while the net guard uses an absolute 1e-9, so the band never absorbs noise. A residue between the two tolerances raises `OwnershipInconsistency` and halts loudly. Fix: use one tolerance for both checks.
- **F3-4-3** (low, medium) execution/alpaca_reconciliation.py:282, alpaca.py:588-590 — the recovered remainder of a canceled partial BUY is dated at `submitted_at`. When the stream has already booked a fill for the same order, FIFO order is inverted, which changes the lot cost. Fix: clamp the recovered time to no earlier than the last booked fill.

## S3-4 — systemd sandbox with the new drop-ins

*Orchestrator note: S3-4-1, S3-4-3 and S3-4-4 all put the same risk on the backup and dead-man units: the repo templates use /opt paths, the VM uses the home-dir layout, and the shipped vm-dropin set covers only the engine. That combines with R3-3-4/D3-3-1 (no alert on stale backups), so a backup failure is **completely silent**. On the next VM access, check `systemctl status parayabuck-backup.timer parayabuck-backup` and the age of the newest file in data/backups, read-only. I did not run that check in this audit. S3-4-6 ("never run on the VM") may be stale relative to the 10-27 22:27 restart, which went through ExecStartPre doctor --strict; which drop-ins the VM actually has needs checking.*

- **S3-4-1** (high, medium) docs/deployment.md:633-635 vs 546-548, parayabuck-backup.service:9, 14, 20, 33, parayabuck-deadman.service:10, 15 — the update checklist says to copy over the backup unit, while another section of the same document says not to. The /opt-path template fails on the home-dir VM before Python starts, so no alert, no backup and no off-host copy. Fix: change the update step to "diff, never cp", and ship VM drop-ins for backup and dead-man.
- **S3-4-2** (medium, high) vm-dropin/vm-paths.conf:21-30 — `ProtectHome=read-only` exposes the deploy user's whole home (~/.config/gcloud, ~/.ssh) to the engine process that holds the LIVE key. The stated reason (gcloud reads) does not apply to the engine. Fix: `ProtectHome=tmpfs` plus `BindReadOnlyPaths=<checkout>`.
- **S3-4-3** (medium, high) vm-paths.conf:1-2, deployment.md:179-187, 401-402 — the home-dir drop-in set covers only parayabuck.service, so the backup and dead-man units cannot be rebuilt from the repo. Fix: add vm-paths-backup.conf and vm-paths-deadman.conf.
- **S3-4-4** (medium, low) parayabuck-backup.service:27-30, backup.py:483-491 — the off-host copy runs with HOME on a read-only path, so gcloud may fail to write its state. The documented manual check runs outside the sandbox and does not prove the copy works. Fix: set `CLOUDSDK_CONFIG` to a writable path, and test with `systemd-run` using the unit's properties.
- **S3-4-5** (low, medium) deploy/systemd — cheap hardening that is missing: ProtectProc=invisible, ProcSubset=pid, SocketBindAllow on the engine, and MemoryDenyWriteExecute and SystemCallFilter on the dead-man.
- **S3-4-6** (low, high) deployment.md:41, 52 — the doc says the sandbox "has not run on the VM yet", and there is no pre-restart dry run (systemd-run + doctor). "Both services" should read "all three units".

## C3-4 — stream supervisor after F/X2

*Orchestrator note: I read alpaca_stream.py:120-128 for C3-4-1 and confirmed that the `await start()` call has no timeout. In case (a), the "systemd restarts it" consequence is latent, because the VM has no watchdog (R3-4 note). stop_failed still happens. Case (b), auth that never returns, applies today: the stream hangs silently and only the 180 s bar staleness check catches it.*

- **C3-4-1** (medium, high/medium) data/alpaca_stream.py:125-126; SDK websocket.py:103, 123, trading/stream.py:76 — the connect and auth steps of a reconnect have no timeout. (a) A stop during a reconnect does not interrupt the connect, so the 8 s join expires and the result is stop_failed. (b) If the server never sends the auth reply, `start()` never returns: no backoff, no alert, no reconcile latch, and worker.alive stays True. Fix: `asyncio.wait_for(start(), 30)`.
- **C3-4-2** (low, high) alpaca_stream.py:32-41, 278-300 — INCOMPLETE of R2-4-6. The SDK never clears `_loop`, so from the second run() on `wait_for_sdk_loop` does nothing, and a stop racing a restart can be lost. Fix: set `stream._loop = None` before each `asyncio.run`.
- **C3-4-3** (low, medium) SDK trading/stream.py:202-205, alpaca_updates.py:229-237 — `TradingStream.stop()` calls `.result()` with no timeout, unlike DataStream's 5 s. An engine stop can block for as long as the loop thread is busy. Fix: a bounded wait.
- **C3-4-4** (low, medium) alpaca_stream.py:156, 161 — when the stream comes up for the first time after failed attempts, `on_reconnect` does not fire, so no `trade_updates_restart` latch is set for fills that happened while the stream was down.

## D3-4 — deploy docs vs the VM state

*Orchestrator note: OPERATIONS_LOG.md (09:57 row, 10-27) records that the VM's backup unit ran OK under the read-only mount, and that the unit stays `Type=simple` with no watchdog. So backups were working on the VM as of 10-27. The S3-4-1/D3-4-1 risk applies to future updates, not to today's state. D3-4-1 is the same finding as S3-4-1 (fix them together). D3-4-2 confirms that the log's drop-in names (start-timeout.conf, backups-readonly.conf) differ from the repo's (start-timeout-engine.conf, start-timeout-backup.conf, vm-paths.conf), so the VM state cannot be rebuilt from the repo.*

- **D3-4-1** (high, high) docs/deployment.md:632-635 vs 546-548 — SAME AS S3-4-1. The update step copies the template units over the VM's hand-edited units. The engine then fails to start with 217/USER or 200/CHDIR, which stops trading, and the backup stops silently.
- **D3-4-2** (medium, high) docs/OPERATIONS_LOG.md:9, deploy/systemd/vm-dropin/ — the drop-in names in the 10-27 22:27 record do not match the repo files. vm-paths.conf is not installed (the ExecStartPre still runs `--paper`), and it is unclear whether the backup/dead-man hardening is in force. Fix: record the `ls /etc/systemd/system/*.service.d/` and `systemctl show` output in the log, and align the names.
- **D3-4-3** (medium, high) deployment.md:632-633, .github/workflows/ci.yml — the update procedure never installs changed drop-ins, and CI validates no unit or drop-in file (no `systemd-analyze verify`).
- **D3-4-4** (medium, medium) parayabuck.service:7-8, 39, FINDINGS:200 — StartLimit* and TimeoutStopSec=180 have no drop-in. A drop-in-only rebuild falls back to the defaults (stop 90 s, no restart limit). Fix: add vm-dropin/unit-limits.conf.
- **D3-4-5** (medium, high) deployment.md:52, 166-208, web/routers/health.py:94-150 — the verification steps do not show which build is running (no SHA, no start time), and no `systemd-analyze security` step backs the claim that they "confirm" the sandbox.
- **D3-4-6** (low, high) deployment.md:370-378 — the dead-man install copies the template unit, which names a user and paths the VM does not have.
- **D3-4-7** (low, high) operations.md:442-444 — SAME AS D3-1-4 (stale known-gap paragraph).
- **D3-4-8** (low, low) deployment.md:~624-645 — between the merge/pip step and the restart, the old process can lazily import new code (205 function-level imports). Fix: halt or stop first.
- **D3-4-9** (low, high) parayabuck.service:26, 81 — the unit comments point to headings that do not exist, and the doc says "both services" for three units.
- **D3-4-10** (low, medium) deployment.md:253, 523, 526, 634 — the .env.backup commands hard-code /opt/parayabuck and the parayabuck user.
- **D3-4-11** (low, medium) deployment.md:761-775 — rollback does not restore the units or drop-ins, and says "restart" right after a stop.

## P3-4 — idempotency and client-order-id lineage after B2/I2

*Orchestrator note: I read alpaca.py:538-544 for P3-4-1 and confirmed that a 404 returns "canceled" directly, with no in-flight or age guard. For P3-4-2 I read runtime_service.py:4138-4145 and confirmed that "a different id of the same symbol" also matches the leg's own predecessor rows. P3-4-1 is a coupling gap: B2 protected the reconcile path but not the restart resolver.*

- **P3-4-1** (medium, high/medium) execution/alpaca.py:493-543, portfolio/runtime_service.py:2903-2952 — after a restart, `_resolve_submitting_rows` books a single 404 as "canceled" without the B2 in-flight or age guard. If the process died inside the POST and Alpaca accepted the order late, a residual SELL or a replan BUY is sent for the same intent, which can double the order. `_has_open_order` and the broker quantity check mitigate this but do not fully close it. Fix: when `submit_call_started_at` is set and `returned_at` is NULL, do not cancel; use reconciliation_required instead.
- **P3-4-2** (low, high) runtime_service.py:4141-4144, runtime.py:1288-1294 — INCOMPLETE of P2-1-3. The successor test also counts the leg's own predecessor rows, so a rejected residual or replan row is never retried. The alert text "one retry follows" is wrong. Fix: count only rows of the same leg with a higher chain position, or a replan row.
- **P3-4-3** (low, medium) runtime_service.py:3173-3186 — `replan_order_in_flight` also holds the risk-reducing SELLs of other symbols, and the alert calls it a BUY-side hold. Fix: hold only the in-flight legs.
- **P3-4-4** (low, high) runtime_service.py:2333, 2419, 3411-3446 — a residual or replan row that never reached the broker, once the resolver cancels it, still uses up a residual round or a replan id. Three crashes in a row exhaust the leg. Fix: book a row that was never sent as "rejected", or count only rows with a `submit_call_started_at`.
- **P3-4-5** (low, high) cli.py:1834 — `ops events` truncates the order id to 8 characters, which shows "parayabu" for every bot order, so events cannot be correlated.

## R3-5 — crash windows in the new persistence

*Orchestrator note: no critical or high findings; all three are low. Track R is complete (5 rounds). The "Checked but fine" section of the agent's report covers transaction boundaries for every multi-step change and found them sound.*

- **R3-5-1** (low, high) runtime.py:6427, 6467-6494, risk_peak.py:445-455 — if the process crashes between the sidecar commit and the incident clear, restart does not heal it, although the comment says it does: `expected_epoch` is re-read on each call. The operator has to run a second reset, which rebases the peak again to N+2 and silently replaces the baseline verified in the first attempt. Fix: finish the reset tail at startup when the sidecar epoch is newer than the high-water epoch, or correct the comment.
- **R3-5-2** (low, medium) runtime.py:6489-6490, 5914-5931, 2706-2710 — an account snapshot taken before the reset can re-save the drawdown incident after the reset cleared it. In-process the next poll clears it, but a crash inside that window leaves the halt wedged. Fix: take `_risk_state_lock` in the reset, and ignore a snapshot whose peak_epoch is behind.
- **R3-5-3** (low, high) backup.py:409-424 — if `stage.unlink()` fails after the publication commit point, the outer except deletes the snapshot that was just published, along with its members. Fix: make `unlink` best-effort and keep the destination.

## S3-5 — supply chain after the msgpack bump

*Orchestrator note: the runtime hash chain, the lock consistency, the bundle integrity checks and the dashboard asset policy all hold. msgpack 1.2.4 is the same in all three locks. The main gap is S3-5-1: there is no scheduled audit. Track S is complete (5 rounds).*

- **S3-5-1** (medium, high) .github/workflows/ci.yml:3-5 — pip-audit runs only on push/PR. There is no `schedule:` and no Dependabot, so a CVE published after the last push is never reported. Fix: add a weekly cron to run the locks job, or enable Dependabot security alerts.
- **S3-5-2** (low, high) ci.yml:19-20 — INCOMPLETE of S2-3-5. The locks job's checkout is missing `persist-credentials: false`, while the quality job has it.
- **S3-5-3** (low, medium) docs/deployment.md:514-515, cli.py:920 — the VM venv's pip is not pinned. The host's ensurepip version is used and it is outside every check, even though pip is the tool that enforces `--require-hashes`. Fix: install `pip==26.2.1`, as Dockerfile:26 does.
- **S3-5-4** (low, low; verify) requirements.lock:571 — the starlette 1.6.0 advisory check raised in raw-r6 S7-7 has no recorded closure. Fix: read the CI pip-audit log and close it in FINDINGS.
- **S3-5-5** (low, low; verify) pyproject.toml:33, requirements-dev.lock:234, 238 — the dev extra locks httpx2/httpcore2, non-canonical names the code never imports. Confirm the publisher, or drop them.

## F3-5 — CLI ops commands and exit codes

*Orchestrator note: I read the code for F3-5-1 (cli.py:1865-1875, the error is printed but the command exits 0) and F3-5-11 (`__main__.py` starts the web console), and both are confirmed. F3-5-11 is a low-reach footgun: `python -m parayabuck halt` is not documented, but an operator who tries it would believe a halt was written. Track F is complete (5 rounds).*

- **F3-5-1** (medium, high) cli.py:1865-1875, 1891, 1933 — `ops timeline` turns a failed journal read into "no events" and exits 0. This is the same gap that was fixed for `ops events`, missed here. Fix: query with `strict=True` and return EXIT_FAILURE.
- **F3-5-2** (medium, high) cli.py:1787-1795, 1845-1855, 1967-1979, 2159-2175, 1741-1759 — when settings fail to load, the read-only ops commands fall back to relative default paths without a word. A broken config then looks like "fresh install, no events" and exits 0. Fix: write a line to stderr and return EXIT_FAILURE or EXIT_MISSING, as `ops status` does.
- **F3-5-3** (low, high) cli.py:333-355, 738-777, tax/cli.py:185-193, 256-259 — exit codes for a missing config or a missing input are inconsistent: a missing config gives 1 where the doc says 3, a missing tax ledger gives 1, and `resume` with a missing directory gives 0 while `halt` gives 3.
- **F3-5-4** (medium, medium) cli.py:755-760, 790-795, 2480-2497, journal.py:53-87, 402-408 — INCOMPLETE of F2-9-4. The operator audit rows (halt set/cleared, ownership_rebased) are not safety kinds, so they are queued for the daemon writer and can be lost when the process exits. Fix: add them to SAFETY_JOURNAL_KINDS, or call `stop()`.
- **F3-5-5** (low, high) cli.py:1766-1769, 1805, 1819-1822, 2019-2021 — when the command fails, `--json` writes nothing to stdout. `ops events` calls `open_readonly` outside the try, so a bad journal ends in a traceback.
- **F3-5-6** (low, high) cli.py:432-445 — `authorize-live` usage errors exit 1 instead of 64, because it raises `SystemExit(str)`.
- **F3-5-7** (low, high) cli.py:363-381, 2678-2696 — `backtest`/`optimize` give a traceback with exit 1 on a malformed date. `--grid` is checked only after the bars have been fetched from Alpaca.
- **F3-5-8** (low, high) cli.py:660-677, backup.py:112-118 — an invalid `backup --label` raises a `backup_failed` alert and a traceback, although it is a usage error.
- **F3-5-9** (low, high) tax/cli.py:157-198 — the tax argument checks run after the environment checks, so a bad date exits 1 instead of 64.
- **F3-5-10** (low, medium) cli.py:250-254, 2384-2389 — if `rebase-ownership --account-id` is given the PA pin, as the help text says, the pin check is skipped and the plan runs against a key the ledger does not use.
- **F3-5-11** (low, high) src/parayabuck/__main__.py — `python -m parayabuck <subcommand>` ignores its arguments and starts the web console, so `python -m parayabuck halt` writes no halt. Fix: call `parayabuck.cli.main()`.

## D3-5 — operator UX of the new refusals

*Orchestrator note: several of these overlap earlier findings. D3-5-3 is the same as R3-2-2 and widens it with `alpaca.data_delayed`, `open_browser`, `daily_summary` and `equity_use_effective_dated_fees`. I set it to medium rather than the agent's high, because the panel writes real booleans and a quoted value can only come from a hand edit; the §9 wording ("quoted true/false stops the load") is broader than the code and also needs correcting. D3-5-1 is the same as F3-3-4. D3-5-12 is the same as F3-5-6. Track D is complete (5 rounds).*

- **D3-5-3** (medium, high) config.py:569-570, 589, 208, 257, 286; web/managers/engine.py:528-533; data/alpaca.py:174 — SAME AS R3-2-2, wider. A quoted `"false"` is read as true for `app.auto_start_engine`, `alpaca.data_delayed` and several other bool fields, so the engine auto-starts and the feed switches to delayed_sip. Fix: apply `_require_bool` to every bool field and add a test for each.
- **D3-5-1** (medium, high) — SAME AS F3-3-4 (the e-mail, halt and log hints omit `--plan-digest`).
- **D3-5-2** (medium, high) runtime.py:6575-6601, 837, 3684, 3696-3706; ui.js:280 — the reset button appears while the engine is degraded with no breaker, and the refusal reads "not operator-resettable: none" with no next step. For `*_restart_limit` the refusal does not say to Stop and then Start. Fix: show the button only when a breaker is set, and add a cause and an action to the message.
- **D3-5-4** (medium, high) live_safety.py:409-424, 662-694, engine.py:2064, 984 — the LIVE approval refusals (signature, settings, host, expiry) and the arm refusal do not name the next command. Fix: add the `authorize-live` command and the re-arm step to each refusal.
- **D3-5-6** (medium, high) cli.py:1232-1235, 1264, 1269, 1285, 1324, 1368 — `doctor --strict` BLOCKER rows give no fix command, the summary line does not name the failing rows, and after a failed start the operator is not told about `reset-failed`.
- **D3-5-7** (low, medium) runtime.py:6616, ui.js:124 — the ownership-drift reset refusal does not point to `rebase-ownership`, and the stop → rebase → start → reset order is not written anywhere.
- **D3-5-5** (low, high) web/routers/engine.py:44-48 — runtime start refusals (LiveApprovalError and other RuntimeErrors) reach the toast as a generic 500.
- **D3-5-8** (low, high) cli.py:2206-2227, 2368-2374, 2498 — `rebase --confirm` is refused while the web unit is up, even when the engine is stopped from the dashboard. The success message does not mention the start step.
- **D3-5-9** (low, medium) cli.py:781-786 — after `resume`, the message says "release it with the console reset", but the reset is refused while the engine is stopped. The message should say to start first.
- **D3-5-10** (low, high) cli.py:79, 2361, tax/cli.py:189 — messages say "pass --config", but `--config` is accepted only before the subcommand.
- **D3-5-11** (low, high) tax/cli.py:146-149, 196 — the hint "(use --live)" for PAPER activities switches to the LIVE keys and ledger, and the credentials message does not name the variables.
- **D3-5-12** (low, high) — SAME AS F3-5-6.
- **D3-5-13** (low, high) web/managers/engine.py:517-545, trading_host.py:26-31, routers/health.py:113-124 — auto-start refusals (trading host, missing keys, tracked secret) are silent: no alert, no engine_error, and /readyz reports not ready without a cause.

## T3-5 — time math

*Orchestrator note: I read runtime.py:4476-4500 and 5670-5735 for T3-5-1 and T3-5-2. `_equity_signal_basis_latch(decision_timestamp)` runs BEFORE the cross-check parking, and `_equity_signal_basis_check` calls `verify_equity_signal_basis(required_through=decision_ts)`. At the close (16:00:30 ET) the provider's decision-day bar is not yet "final" (embargo 15 min), so the check fails, and a `risk_incident` plus a `signal_adjustment_failure` (an e-mail kind) are sent. Only after that does the cross-check park the batch, and the 16:15 retry passes. The Q3-5-1 test failure is direct evidence of the same mechanism: that test's "the decision at the close passes clean" expectation is true only after close+15. **This is live on the VM**: H2 (wave 2) was deployed on 10-27 at 22:27 UTC, after the close, so the first expected occurrence is today, 10-28, at about 20:00 UTC. settings.yaml has `signal_cross_check.enabled: true`, so T3-5-1 (no orders at all when the check is off) is conditional and does not apply on the VM. T3-5-2 does apply, which is why I raised it to high. Track T is complete (5 rounds).*

- **T3-5-2** (high, high — LIVE ON THE VM) runtime.py:4484, 5442-5458, 5670-5735, 5195-5204 — with the default config, every session close sends a false "signal basis UNVERIFIED / equity book held" alert. The `risk_incident` and `signal_adjustment_failure` are e-mailed, and a recovery follows at about 16:15. This happens every trading day, so the operator learns to ignore the real alert. The trade-update BUY resume is also blocked for about 15 minutes. Fix: count the decision session's partial provider bar as coverage (the watermark stays final-only), or run the basis latch after the cross-check parking, that is, at close+embargo. Q3-5-1 should be fixed together with this.
- **T3-5-1** (high, conditional; high/medium) runtime.py:4484, 4493-4494, signal_cross_check.py:367-368, runtime_service.py:1800-1801 — REGRESSION of T2-8-2 (H2). When `signal_cross_check.enabled: false`, or there is no SIP client, the batch is never parked, so every session-close decision is held by the basis latch and no equity order is placed. The VM has it enabled, so the VM is not affected. Same fix as T3-5-2.
- **T3-5-3** (low, medium) runtime.py:5818-5838 — `_refresh_readiness` compares raw bar labels. Once the SIP bar is adopted, QQQ/QLD carry 00:00 ET labels while SPY keeps 09:30 ET, so every evening they read not-ready and `reset_safety_lock` refuses overnight. Fix: compare `signal_session_date`.
- **T3-5-4** (low, high/low) markets/nyse_calendar.py:80-91, intent_watchdog.py:125-131 — while the NYSE calendar is degraded, the watchdog treats weekday holidays as due sessions, giving a false `equity_decision_missing`, a /readyz 503 and a dead-man fail. Fix: skip the check while the calendar is degraded, as premarket does.
- **T3-5-5** (low, medium) runtime.py:826-827, 5111-5112, 5250-5251, engine.py:645-646 — account and basis freshness use the wall clock. A backward NTP step hides staleness. Fix: use `time.monotonic()`.

## P3-5 — end-to-end QLD trace (risk-off exit day and re-entry day)

*Orchestrator note: P3-5-1 independently confirms T3-5-2 (two tracks reached the same live bug), so fix them as one item. P3-5-3 is the same as P3-2-1. For P3-5-2 I read runtime_service.py:2044-2064: the rebalance row (SELL_PENDING) is written only inside the `if sells:` branch where buys exist. The pure-SELL branch, which is exactly a VT35 risk-off exit, writes no rebalance row, so the residual path never runs. Confirmed. A partial fill on a QLD market DAY order is rare, but the exit is the most critical order in the strategy. Track P is complete (5 rounds).*

- **P3-5-1** (high, high — LIVE ON THE VM) — SAME AS T3-5-2. The basis verifier runs at close+30 s, before the SIP embargo, and sends a false UNVERIFIED alert plus a "book held" alert every session.
- **P3-5-2** (medium, high/medium) runtime_service.py:1995-2044, 2045-2064, 2263, 4083-4112; runtime.py:3518-3523 — a pure-exit SELL that is not filled or only partly filled is not re-sent during the day, and no alert fires. There is no rebalance row, so `_residual_sell_result` never runs, `retry_rejected_exit_sells` handles only `rejected`, and canceled or expired orders do not alert. On a VT35 risk-off day, if a 60% fill is followed by the DAY order expiring, the remaining 40% stays held until the next decision. Fix: write SELL_PENDING for pure-SELL decisions too, or run the residual for them from the trade-update and morning paths, and alert on a canceled or expired SELL with unfilled quantity. Combine with P3-3-1 (pure-SELL gate-held rows left submitting): both stem from the pure-SELL branch writing no rebalance row.
- **P3-5-3** (medium, medium) — SAME AS P3-2-1 (a halt raised during the network steps drops the session silently).

## C3-5 — deadlock and resource-leak search

*Orchestrator note: no lock-order inversion, self-deadlock or significant unbounded growth was found; the agent's lock graph is in its "Checked but fine" section. C3-5-1 is the same as P3-1-2/C3-2-2. Track C is complete (5 rounds), so **all 40 rounds are complete**.*

- **C3-5-1** (medium, high) — SAME AS P3-1-2 / C3-2-2. The account loop waits on `_portfolio_lock` behind the decision's broker I/O, and the retry path does the same.
- **C3-5-2** (low, medium) monitoring/alerts.py:161-180 — the suppressed-alert `log.info` runs under the global `alerts._lock`, so a stalled stderr or journald pipe blocks every alert emitter, and those emitters hold runtime locks while they wait.
- **C3-5-3** (low, medium) runtime.py:538-540 — if constructing PortfolioStateRepository fails (for example a migration refusal), the ledger's store reference is never released, which leaks a connection and its fd until GC.
- **C3-5-4** (low, high) runtime_service.py:744, 2846-2849, runtime.py:6267-6272 — `_raised_operator_alerts` and `_premarket_probed_sessions` are never pruned. Growth is under 1 MB a year.
- **C3-5-5** (low, medium) data/cache.py:184-196 — `WarmupCache.prune_before` has no caller, so warmup.sqlite3 is never pruned.
- **C3-5-6** (low, high) monitoring/journal.py:597-618 — `configure_global_journal` holds `_global_lock` through a writer join of up to 2 s and sqlite I/O (startup only).
- **C3-5-7** (low, medium) runtime.py:2213-2218, journal.py:405-406 — after the engine stop, a later emit lazily starts a new daemon writer that is never joined, so its queued rows are lost at exit.
- **C3-5-8** (low, medium) web/managers/backtest.py:89-94, 215-242 — the process-wide stdout/stderr redirect in the backtest thread creates a latent lock cycle. It is unreachable today.
