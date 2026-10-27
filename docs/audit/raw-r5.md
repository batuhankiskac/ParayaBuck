# Round 5 raw findings (cross-cutting angles) — unverified agent output

## Angle Q — test gaps
- Q-1..Q-7: none of the known highs has a test that would catch it. A-1 (no double raises TypeError inside), F-1 (validate_order_batch never run with 1e-9; the 1e-9 tests go through the Decimal submit path), C-2 (the replan SELL test asserts only BUYs), C-4 (one-leg cap test only), G-1 (the fake returns [] on every read, so a re-read is untested), C-1 (no fake raises in asset_trading_rules), B-1 (parametrize omits account_fetch_failures). Each fix should come with the test named in the agent report.
- Q-8 NEW medium runtime.py:1316-1318, cli.py:569-574 — operator halt marker is checked only at engine START. `parayabuck halt` during a run does not stop trading (CLI says to use the console stop). Decision: should `halt` stop a running engine? Either way, pin it with a test.
- Q-9 low web/managers/engine.py:1074-1075 — same as I-8 (two mode reads); no test.
- Q-10 low tests/test_observability_mission5.py:753-764 — patches parayabuck.monitoring.journal.emit_journal, but observe.py bound the name at import → the test passes vacuously.
- Q-11 low tests/test_observability_mission5.py:767-786 — failure-isolation tests assert only "no exception"; one writes /tmp/bad/snap.json (hard-coded path, against AGENTS.md's tmp_path rule).
- Covered OK: sells-before-buys (initial phase), idempotent client_order_id, no BUY while halted/latched, PAPER/LIVE key separation, drawdown/daily-loss/drift halts, DAY orders after close, restart idempotency.

## Angle P — decision → order flow
- P-1 HIGH (orchestrator-confirmed code: alpaca.py:513-514 unknown id → "canceled"; runtime_service.py:2050-2053 zero-fill SELL → no residual) — a SELL that never reached the broker (crash after the sell-phase commit, before submit) or that ended with zero fill counts as a finished sell phase. BUY_READY → re-plan still wants the SELL → C-2 drops it → BUYs go out while the position is still held. A rejected SELL with its retry still pending can go the same way via the trade-update advance (runtime.py:2763). Same gate as C-2, different trigger: the C-2 fix must also treat a zero-fill or never-sent SELL as "exit still required".
- P-2 medium runtime_service.py:1285,1328-1346 — previous_timestamp is advanced before advance_rebalance; if that returns rebalance_complete (e.g. a missed completing trade update, or residual exhausted), the session is consumed with no decision and is never re-planned (a retry is no longer a period start). Fix: restore previous_timestamp on that early return. (Relates to C-3.)
- P-3 low runtime_service.py:2903,2945 vs execution/portfolio.py:1258 — BUY sizing uses max(reference, mark) but validation uses reference only → a sized BUY can be dropped at validation and lost for the session.

## Angle R — restart and crash recovery
- R-1 HIGH (orchestrator-confirmed: _raise_halt runtime.py:2567-2580 sets memory only; runtime.py:3886) — halts raised in memory (portfolio_runtime_failure etc.) are persisted only by the next SUCCESSFUL account poll (runtime.py:2270 → _persist_risk_state). If the broker is down, or the same DB error blocks the save, and the process is then watchdog-killed or restarted (SIGABRT skips stop(); stop() never writes an incident), the restore finds no row and trading resumes without operator verification → fail-open after restart. Fix: persist persistent-reason halts synchronously in _raise_halt / _raise_reconciliation_breaker and in stop().
- R-2 HIGH — same as P-1 (zero-fill / never-sent SELL leg treated as finished; untried legs canceled by _submit_orders 3123-3127 too). Two agents found it independently.
- R-3 medium runtime_service.py:1006-1018,1868-1881,1640 — a pure-BUY decision persisted at "submitting" with a crash before the broker call: on restart startup returns duplicate_decision before any resolver; the BUYs sit as "submitting" (count as open orders) and are dropped silently until the next session.
- R-4 medium (orchestrator-confirmed: client id has no quantity, execution/portfolio.py:1161-1163; record_order is INSERT OR IGNORE, runtime_repository.py:756-759) — a re-planned deferred BUY reuses the client_order_id of its own terminal (canceled/rejected) row; the new row is ignored, _submit_orders skips the terminal row, and the rebalance completes with no BUY and no alert. Fix: fresh suffixed id on re-plan.
- R-5 low runtime_service.py:609-700,1018-1046 — after a config change (new configuration_digest) a restart treats an already-decided session as undecided and re-decides it.
- Note: operator halt marker not stopping a running engine is documented in cli.py:556-558 (Q-8 is a design question, not a bug).

## Angle F — fill → trading books → tax books (ids prefixed BF- to avoid clashing with F-1 grid bug)
- BF-1 medium runtime_repository.py:1244-1373, runtime.py:2895 — a stream fill with null average advances cumulative quantity but books no fills row (later event books only the delta) → orders cumulative 10 vs fills 5 → next sell: trading P&L 0.0, tax LedgerError. With a zero average the event is rejected, apply returns False and the caller ignores it (no reconcile scheduled, unlike the unknown-status branch). Fix: check the bool, _schedule_reconcile() on False; never advance quantity without a booked fill.
- BF-2 medium runtime_repository.py:1292-1409, ledger.py:452-456, tax/ledger.py:128 — a fee observed after its fill never reaches portfolio_fills; tax uses fee 0 without a completeness flag (telemetry already marks fee_completeness "unknown"). In practice no production path observes fees today (alpaca-py TradeUpdate has none), so the tax export should say "fees not included / unknown".
- BF-3 medium alpaca.py:556-561, alpaca_reconciliation.py:255 — a recovered fill on a partially filled then canceled order takes updated_at (cancel time) as fill time → a Dec 31 fill recovered after New Year moves to the next TAX YEAR (FX and indexation month too). Fix: never fall back to updated_at for fill time; flag recovered timestamps.
- BF-4 low runtime_repository.py:1380-1391,1621-1650, tax/ledger.py:131,224-227 — trading realized P&L frozen at booking time and replayed in booking order (no ORDER BY), tax replays chronologically; a late-booked earlier fill makes the books disagree. Fix: ORDER BY occurred_at, activity_id; flag out-of-order bookings.
- BF-5 low alpaca_updates.py:98-118 — per-fill price derived from cumulative averages; per-execution price/qty available but unused.

## Angle C — concurrency (ids prefixed CC- to avoid clashing with runtime_service C-*)
- CC-5 medium (orchestrator-confirmed: runtime.py:1876-1892 joins every thread while holding the spawn locks; 5353 _schedule_reconcile takes the same lock; 3283 pb-decisions calls it) — stop() during a decision-loop reconcile tick: pb-decisions blocks on the spawn lock, its 5 s join times out, stop() returns stop_failed ("process restart required") although everything finished. The async trade-update loop also freezes on the same lock (2888, 3015). Fix: under the lock only set a stopping flag, then join outside it.
- CC-6 HIGH (medium confidence) runtime.py:2751-2757 — the deferred-BUY permission (trading_state + basis latch, which may do provider network I/O) is computed BEFORE _portfolio_lock and never re-checked; runtime_service's only BUY gate is that flag. A halt raised by the account loop (ownership_drift) or a decision during that window still lets the deferred BUY through. Fix: re-read the gate under the lock just before submit (pass a callable).
- CC-7 medium web/managers/engine.py:917-931 — stop_engine holds _account_lock across runtime.stop() (tens of seconds of joins) → every /api/account and websocket tick blocks a worker thread. (Pairs with P-I2.)
- CC-8 medium (low-medium conf.) runtime.py:5414-5423 vs 2254-2270 — operator risk-epoch reset isn't fenced against the account loop; a pre-reset snapshot re-raises the high-water via max() and re-latches the drawdown halt.
- CC-9 medium (low conf.) runtime.py:2895-2990 — fill booked before the account cache is invalidated; a decision can claim the pre-fill snapshot in between.
- CC-10 low runtime.py:4880-4908 — runtime.history read under _state_lock while replaced under _portfolio_lock; "dict changed size during iteration" possible.
- CC-11 low runtime.py:1985-1988,3098-3106 — a stream worker outliving stop() can set _state back to running.
- Lock order otherwise consistent; no non-reentrant lock re-entry found.

## Angle T — time, calendar, DST (DST, early closes, tax year boundaries all checked clean)
- T-1 medium (orchestrator-confirmed: execution/portfolio.py:213-228 @lru_cache over a try/except returning ()) — one calendar failure for year Y is cached for the life of the process → the December H2 deferral silently never applies for that year (sells that should wait execute in December; the watchdog sees no deferral metadata either). Fix: don't cache failures (retry like nyse_calendar's CALENDAR_RETRY_SECONDS).
- T-2 low monitoring/journal.py:396-399, cli.py:903-914 — `ops events --since/--until` with a non-UTC offset compares ISO strings unnormalised → wrong window. Fix: astimezone(UTC).
- T-3 low data/aggregator.py:33-111, runtime.py:3316 — if the decision loop stalls past NY midnight, yesterday's daily bucket is released on rollover without the completeness check / SIP fallback.
- T-4 low monitoring/premarket.py:44-63 — with the calendar degraded, a holiday counts as a session → premarket probe fails and latches reconcile-pending (cross-module effect of N-2).

## Angle D — docs / deploy / config drift (ids prefixed DD5-)
- DD5-1 medium web/routers/health.py:74-104, monitoring/deadman.py:70-106 — the dead-man pings /fail (pages the owner) for states the docs call normal: warmup after a restart, a deliberate console stop/halt, a signal-source hold. Fix: success for warming_up and operator-stopped, or separate liveness.
- DD5-2 medium docs/operations.md:217-219 vs runtime.py:2311-2314 — doc says a SUCCESSFUL account pass feeds the systemd watchdog; code stamps every pass (by my design: an outage must not cause restart loops). Fix the doc (and the dead last_successful_account_iteration_at, DA-2).
- DD5-3 medium docs/deployment.md:205-206, deadman unit:13 vs deadman.py:79-80 — with no heartbeat URL the dead-man exits 0 silently; docs promise a loud unit failure.
- DD5-4 HIGH docs/operations.md:618-619 vs cli.py:44-46 — `parayabuck web --config X` is rejected by argparse (flag is top-level only: `parayabuck --config X web`).
- DD5-5 medium docs/operator_runbook.md:63-64 — tells operators to look for an `order_expired` event that nothing emits.
- DD5-6 medium docs/operator_runbook.md:~27 — documented curl for reset-safety-lock / risk-epoch omits the required x-parayabuck-control-token header → 403 during an incident.
- DD5-7/8 low — operations.md e-mail list misses market_data_degraded, data_provider_unavailable, backup_failed; runbook + backup unit say nothing leaves the VM (dead-man ping and opt-in off-host copy exist).
- DD5-9 low deadman.py:31 — readyz URL hard-codes port 8000, ignores PORT/app.port.
- DD5-10/11 low — "the way CI does" test command differs from ci.yml; tax README points to a path that is really a git tag.
- Checked fine: ExecStartPre flags, Type=notify/WatchdogSec wiring, ReadWritePaths cover every write path, credential names, CLI flags in docs, routes, runbook constants.

## Angle S — security (VM single-user, dashboard loopback)
- S-1 medium .env.example:14, engine.py:1345, live_safety.py:253,391 — LIVE approval HMAC key only length-checked; the tracked placeholder "<replace_with_at_least_32_random_characters>" (42 chars) passes. Left in .env → a public key signs standing LIVE authorizations offline. Scanner/doctor don't flag it. Fix: reject placeholder/low-entropy keys; add to scanner and doctor.
- S-2 low deploy/systemd/parayabuck-backup.service:10, backup.py:273-296 — backup unit loads the whole .env; the off-host child inherits every trading secret; its stderr tail is journaled and e-mailed.
- S-3 low web/schemas/settings.py:38, engine.py:1356,1376 — approval path unvalidated (e.g. /dev/zero → OOM on settings drawer, FIFO hangs LIVE start); error text echoes file contents, contrary to docstring.
- S-4 low live_safety.py:294,432,454,458 — compare_digest on non-ASCII str raises TypeError outside LiveApprovalError → doctor (ExecStartPre) fails → unit won't start.
- S-5 low web/security.py:139-146, app.py:83-87 — loopback enforced only in app.main(); `uvicorn server:app --host 0.0.0.0` exposes read endpoints to anyone sending Host: localhost. Not the systemd path.
- S-6 low journal.py:87-93,291 — journal fields stored unredacted (redaction on read only); key list lacks token/credential/bearer (extends L-7).
- S-7 low engine.py:1642-1666 — .env writer doesn't escape backslashes (= I-9).
- S-8 low deadman.py:33 — heartbeat URL (a secret) allowed over plain http.
- S-9 low requirements.lock (--no-hashes), Dockerfile:1 mutable base tag; dev extra `httpx2` is an unusual name — check it.
- S-10 low live_safety.py:226-362 — approval nonce never recorded → replayable until expiry (design question).
- S-11 low diagnostic.py:491, tax/export.py:228, tax/rates.py:256 — CLI artifacts use the shell umask (UMask=0077 only in units).
- Clean: YAML safe_load everywhere, no shell=True/eval/pickle, SQL bound, middleware/token/ws tickets, SMTP STARTTLS, secrets absent from API responses.
