# Audit wave 2 — raw findings (main 641f08ee, after the W01–W23 fix wave)

Read-only auditors, 8 tracks × up to 10 rounds, pipelined (a track's next lens starts as soon as its previous one returns). Ids: <track>2-<round>-<n>. Orchestrator notes in brackets say what was confirmed by reading the code.

## R2-1 — W07 halt persistence (orchestrator: R2-1-1 confirmed at runtime.py:6014-6016 — every reset releases all three rearmed reasons from memory, live or not)
- R2-1-1 medium — runtime.py:6014-6018 reset_safety_lock releases decision_worker_crashed / final_market_event_queue_overflow / trade_update_callback_failure even when live; a reset of another breaker resumes trading over a live trade-update failure. Fix: release a rearmed reason only when it is not live (or == reason).
- R2-1-2 medium — runtime.py:5948/6006 breaker release by reason string; a same-reason halt (or `parayabuck halt` marker) raised during the reset's broker calls is lifted. Fix: halt generation counter; re-check the marker under the state lock at release.
- R2-1-3 medium — runtime.py:6018-6046 a reset that raises after releasing leaves state halted with breaker None; no reset can clear it until restart (fail-safe wedge). Fix: re-derive _state in a finally.
- R2-1-4 low — runtime.py:5841 INCOMPLETE of C9-6: epoch path clears recon incidents without _risk_state_lock.
- R2-1-5 low — runtime.py:5815 epoch reset checks the breaker only before its reconcile.
- R2-1-6 low — a pre-reset account snapshot can re-save the drawdown incident after the epoch clear (self-heals in 15 s).

## T2-1 — Bar price>0 validation consequences (orchestrator: T2-1-2 and T2-1-3 confirmed — data/cache.py:156-169 and data/alpaca.py:307-324 build Bar per row with no guard; VM warmup cache checked read-only 2026-10-27: 41 525 rows, 0 non-positive, so not live today)
- T2-1-1 high — REGRESSION/INCOMPLETE of T9-1: runtime.py:4964-4990 split verifier adopts a holey provider window (stored=[] → adequate_coverage passes) for a symbol warmup refused to seed (last_incomplete_symbols is read only by warmup), so the symbol becomes ready with a missing session. Fix: treat last_incomplete_symbols as inadequate in verify, or require _window_is_complete when stored is empty.
- T2-1-2 high-if-present — data/cache.py:156-169 WarmupCache.get raises on one pre-W17 non-positive row before the provider call; the batch fails every time and cannot self-heal. Fix: drop/log invalid rows in get, or purge at startup.
- T2-1-3 medium — data/alpaca.py:307-324 get_session_daily_bars: one bad SIP row loses the session-close fallback (no decision that session) and the cross-check secondary. Fix: per-row skip like get_bars_batch.
- T2-1-4 medium — INCOMPLETE of T9-1: alpaca.py:553-564 _fallback serves a cache window with holes (60 % coverage test) without setting last_incomplete_symbols.
- T2-1-5 low — backtest/product.py:145-160 load_csv_bars: one bad row aborts the file without a row number.
- T2-1-6 low — aggregator.py:77-91 / runtime.py:3458-3466: 1m aggregator logs every correction as "dropped"; older-bucket corrections still drop silently.

## Q2-1 — pinned tests the fix wave changed (orchestrator: Q2-1-4 confirmed — `assert after.orders` removed in test_portfolio_runtime.py latched check during the W05/W06 conflict resolution)
- Q2-1-1 medium — test_runtime_decision_retry.py:238-266: hard-halt batches (ownership_drift, portfolio_runtime_failure, decision_worker_crashed) now re-queue for 40 min (R9-2/B-1 by design); no test pins that the reset's reconcile runs before the re-queued decision.
- Q2-1-2 medium — tax/rates.py:53/105-112: FX lookup now accepts up to 10 calendar days back (F7-2 by design); a one-business-day feed gap is priced silently. Fix: report note when the rate is older than the previous business day.
- Q2-1-3 medium/low-confidence — done_for_day finality (W12 D-5) rests on an unverified Alpaca claim; cite docs or a payload.
- Q2-1-4 low — test_portfolio_runtime.py:2186: the latched-decision check can now pass vacuously on an empty result.
- Q2-1-5 low — LIVE standing authorization no longer covers alerting/observability; two LIVE start tests stub _live_doctor_refusal, no test drives a real refusal through start_engine/auto-start.
- Q2-1-6 low — BUY admission "final" observation keeps the first (P7-5); docstring says final; W3 decision-path test lost its exact submission count.

## P2-1 — two-phase flow after W05+W06 merge (orchestrator: P2-1-1 mechanism confirmed — the session-staleness check (_stale_rebalance_decision) runs only in _process_closed_bars and startup_catch_up; _resume_startup_rebalances (runtime.py:3046), reset resume and trade-update advance go straight to advance_rebalance, whose only guard compares latest_decision_id; and on restart _resume_startup_rebalances (runtime.py:1655) runs BEFORE _run_startup_catch_up (1666), so the stale check comes too late — P2-1-1 confirmed)
- P2-1-1 high — REGRESSION/INCOMPLETE of P9-1: a superseded rebalance's BUY side can be released by the restart resume, reset resume or trade-update advance (latest_decision_id guard cannot fire while the rebalance is open, since no newer decision is recorded). Fix: apply the session-staleness test inside advance_rebalance/_submit_deferred_buys and close the superseded rebalance there.
- P2-1-2 medium — runtime_service.py:2821-2855: a re-plan SELL dropped by _replan_orders_with_fresh_ids (rejected leg / exhausted ids) does not hold the BUY side; with no BUYs the rebalance completes clean, with BUYs they go out while the exit is owed. Fix: when replan_needs_sell and the SELL was dropped, hold the leg and close CLOSED_UNRESOLVED before any BUY.
- P2-1-3 high/low-confidence — runtime_service.py:3628-3690: the R3 rejected-SELL retry is sized from the caller's account snapshot with no ledger check; after a re-plan SELL filled, a stale poll can send a second SELL for the same shares. Fix: size retries from owned_quantities and apply _snapshot_ahead_of_owned to retries; skip legs that already have a successor.

## S2-1 — W09/W10 web security (orchestrator: S2-1-1 mechanism confirmed — the save rejects only \r and \n while the rewrite splits with str.splitlines(); requires local access to the control token, and LIVE still needs keys, arm phrase, cap, HMAC approval and e-mail)
- S2-1-1 medium — web/managers/engine.py:639-640 / 1967-1983: a credential containing U+2028 (or \v \f \x1c-\x1e \x85) survives one save and becomes its own .env line on the next rewrite, e.g. PARAYABUCK_ENABLE_LIVE=YES. Fix: reject all control/non-ASCII chars in credentials; split .env with re.split(r"\r\n|\n|\r").
- S2-1-2 low — web/services/settings.py:446 restart_error and web/services/performance.py:141/151 return raw exception text, bypassing redact_secrets.
- S2-1-3 low — web/security.py:146-148: body cap trusts Content-Length only (chunked bodies unbounded); isdigit() accepts "²" → int() raises → 500 before the token check.
- S2-1-4 low — engine.py:302-320: a systemd credential file shadows .env keys, so a UI key rotation reports success but the engine keeps the old key (latent; VM uses EnvironmentFile).
- S2-1-5 low — engine.py:337-342 redact_secrets fails open when the .env read raises.
- S2-1-6 low — web/routers/websocket.py:57-95: WS routes skip host/peer checks (ticket + Origin only).

## F2-1 — W11 ownership/split replay (orchestrator: F2-1-1 not live — the VM DB was queried read-only on 2026-10-27 and holds no sign>0 adjustment rows, only 5 stale/dust clears)
- F2-1-1 high-if-present — ownership_rebase.py:193-200: a pre-W11 operator_rebase +1 row raises LotBookInconsistency in every replay of that symbol; sells then book 0.0 P&L, cost bases go empty (P6-1 still swallows it), and split corroboration fails. Fix: explicit disposition for legacy positive rows (doctor refusal + repair path).
- F2-1-2 medium — ownership_rebase.py:427-461: an uncorroborated exact reverse-split reduction is auto-booked as FOREIGN_SELL, so FIFO strips the wrong lots' cost. INCOMPLETE of F10-4. Fix: halt instead of booking.
- F2-1-3 low — runtime_repository.py:2072-2079: the split boundary search returns the latest corroborating boundary; a coincidental later fill can be picked.
- F2-1-4 low — runtime_repository.py:1795-1840: same-instant fills have no tie-break (SELL before BUY raises). Fix: ORDER BY occurred_at, buys first, rowid.
- F2-1-5 low — realized P&L fixed at booking; a recovered older BUY is never re-costed.
- F2-1-6 low — surplus alert keys clear only on a mismatch reconcile; a self-reconciling symbol keeps its key for the process lifetime.
- F2-1-7 low — pre-W11 split_rebase rows carry detection time; the replay now treats it as the split instant.

## C2-1 — lock ordering after the fix wave (no cycle found; lock graph acyclic in runtime, broker, store and web manager)
- C2-1-1 medium — runtime.py:2985-2997 / 1092-1110: the under-lock re-check and the orchestrator's submit_gate omit the equity signal-basis latch (_signal_basis_latched is never read by trading_state), so a deferred BUY can submit after the basis became unverified. Fix: re-read the latch under the lock and in _advance_submit_block_reason for BUYs.
- C2-1-2 low — duplicate of R2-1-4 (epoch reset clears incidents without _risk_state_lock).
- C2-1-3 low — runtime.py:4195-4262: decision-thread incident recovery calls broker.reconcile() without _reconcile_lock.
- C2-1-4 low — lock-coverage comments (_warmup_symbols, _refresh_readiness) do not match the locks taken.

## D2-1 — docs vs code after W18/W23 (orchestrator: D2-1-1 consistent with the 2026-10-27 deploy, which built the bundle from `..main`, not `..origin/main`; D2-1-2 not live — e-mail is disabled in the shipped config)
- D2-1-1 high — docs/deployment.md:417/429: bundle built from `..origin/main` has no refs/heads/main, so the VM fetch `main:refs/remotes/bundle/main` fails. Regression from W18. Fix: bundle `main` (after checking it equals origin/main).
- D2-1-2 high — the backup CLI never installs the e-mail sink (configure_email_alerts only in runtime.py:635 / web engine.py:390), so backup_failed is never mailed although deployment.md says it is. Fix: configure the sink in the backup path, or correct the docs.
- D2-1-3 high — operator_runbook.md:263 / operations.md:658: documented `--confirm` (bare) is refused since W11 (needs the owned quantity).
- D2-1-4 high — operations.md:655-659: the 2:1 split example and the "adopt the broker count" path are refused without --broker-average-cost / --acquisition-unit-cost, which no doc mentions.
- D2-1-5 medium — operator_runbook.md:318-325/572-580: restart-limit halts are not operator-resettable; the runbook says reset (shared) and contradicts itself; market-scope clears only on a verified reconnect; the pb-decisions example is wrong (it is respawned).
- D2-1-6 medium — operator_runbook.md:522: "one of the twelve" paper_incident categories; ≥13 others are emitted (ownership_rebased, last_equity_missing, peak_sidecar_write_failed, stream_auth_failed, undecoded_trade_update, unrecognized_broker_order_status, unknown_order_canceled, cost_basis_drift, invalid_fill_facts, lot_book_inconsistency, ownership_surplus_unconfirmed, peak_bootstrap, …) with no runbook row.
- D2-1-7 medium — deployment.md update section never installs the changed units or creates .env.backup; re-copying the backup unit first makes the timer fail with no alert and off-host copies stop silently.
- D2-1-8 low — restore procedure ignores the peak sidecars now carried by each backup set.
- D2-1-9 low — watchdog doc omits the pb-decisions heartbeat gate (W09 R7-4).
- D2-1-10 low — reconciliation_anomalies lives at operations.reconciliation_anomalies and only while a runtime exists.
- D2-1-11 low — "Engine did not start" bullets map operator-halt and trading-host refusals to the wrong alerts.
- D2-1-12 low — adjust_for_cash_flows doc names the wrong file for the copy (runtime.py, not alpaca_account.py).

## Q2-2 — new fix-wave paths without tests (coverage mostly good; no high items)
- Q2-2-1 low — runtime.py:6124 _clear_market_halt_if_unchanged compares the halt by string: a same-reason re-raise during the unlocked verification is erased (ABA); only a different-value change is tested. Fix: episode token.
- Q2-2-2 low — runtime_service.py:2966-2987: a re-plan SELL held by submit_gate is not recorded, yet the phase moves to SELL_PENDING and the result says replan_sell_submitted. Fix: held status when nothing was sent; test.
- Q2-2-3 low — replan_sell_dropped branch (runtime_service.py:2964-2967) has no test.
- Q2-2-4 low — execution/alpaca.py:566-568: a recovered fill with neither filled_at nor submitted_at is dated now(); untested and wrong for tax/FIFO. Fix: treat as unreadable fill facts.

## R2-2 — schema migrations (orchestrator: R2-2-1 not live on the VM any more — the engine restarted on the fix build at 09:57 UTC and migrated the DB; it bites after restoring a pre-fix backup)
- R2-2-1 medium — REGRESSION (W11 × M-1): cli.py:1816 dry run opens read-only (no _migrate), and _owned_events selects unit_cost, so on an unmigrated DB `ops rebase-ownership` (no --confirm) crashes with "no such column: unit_cost". Fix: select NULL when the column is absent, or refuse with "start the engine once".
- R2-2-2 medium — INCOMPLETE of BK-2: rollback to a pre-wave build reads no user_version; W11 positive split/acquisition rows then raise LotBookInconsistency in the old replay. deployment.md implies a loud schema error. Fix: doc — restore the --label snapshot when such rows exist.
- R2-2-3 low — ledger.py:175 stamps user_version before the repository migrations run; stamp once after both.

## F2-2 — apply_trade_update verdicts and callers
- F2-2-1 medium — runtime_repository.py:1319-1341 open_orders (recovery provider) reads only non-terminal rows; a fill reported after the row went terminal (cancel raced a fill, late event lost or refused) is never booked by recovery → owned < broker, fail-closed mismatch, no lot/P&L. Fix: recovery also loads terminal rows whose broker cumulative exceeds the booked one.
- F2-2-2 medium — INCOMPLETE of BF-5: execution_price parsed (alpaca_updates.py:48/178) but not passed; per-fill price derived from rounded cumulative averages (error up to ~C·e per share) feeds FIFO P&L and the tax ledger. Fix: use the exact execution price when its quantity matches the delta.
- F2-2-3 low — runtime_repository.py:~77-80 non-regression SQL lets a terminal event relabel a final (filled) row.
- F2-2-4 low — runtime.py:3134 a refused fill only schedules a reconcile (skipped if one is running) and latches nothing; use _set_reconcile_pending.

## T2-2 — live/backtest parity (named fix-wave changes are parity-consistent; no order-existence divergence)
- T2-2-1 low — INCOMPLETE of D9-1: PortfolioBacktestEngine(risk_limits=None) disables overlay and cash buffer while live substitutes defaults (product path passes limits).
- T2-2-2 low — INCOMPLETE of T8-2: an initial holding with no first-day bar is marked at a future close.
- T2-2-3 low — planner records December/gain-band holds before the min-notional floor, engine after; diagnostics differ.
- T2-2-4 low — P-3 stores max(close, mark) as BUY reference while telemetry labels it decision_close_reference.
- T2-2-5 low — execution/portfolio.py:88-91 policy docstring still says exits to zero keep the band (contradicts T7-2).
- T2-2-6 low — T7-2 exemption misses a mixed position whose target keeps an external floor (live only).
- T2-2-7 low — live-only gap-safe cap and pro-rata BUY scaling (documented R1 deviation; backtest slightly optimistic on rotations). DH-9 still open.

## P2-2 — liveness of the new holds (orchestrator: P2-2-1 confirmed by reading — the account loop never calls advance_rebalance; the hold was added by the orchestrator's snapshot-lag guard. VT35 trades one symbol, so a sell-then-buy re-plan is rare in practice)
- P2-2-1 medium — runtime_service.py:2845/2879: replan_snapshot_stale and rebalance_waiting_for_slot leave BUY_READY with nothing in flight; only trade updates re-advance in-session, same-session bars return duplicate_decision first, so the BUY side waits for the next session, which discards it with a log line only. Fix: re-advance from the account loop when phase is BUY_READY/SELL_PENDING and nothing is in flight (rate-limited); alert when a BUY_READY side is discarded.
- P2-2-2 medium — runtime_service.py:1417-1421/1124-1127: bar-path and startup-catch-up advances pass no submit_gate, so CC-6's per-order re-check covers only the trade-update path. Fix: pass the gate through process_closed_bars/startup_catch_up.
- P2-2-3 medium — duplicate of C2-1-1 (signal-basis latch not re-checked under the lock).
- P2-2-4 medium — INCOMPLETE of P-2: restoring previous_timestamp does not park the batch; a session held by an open or superseded rebalance (residual SELL still working) is never decided unless another batch of the same session arrives. Backstop: equity_decision_missing. Fix: _defer_portfolio_batch on those returns.
- P2-2-5 low — INCOMPLETE of C-1: runtime_service.py:2790-2801 an untradable TARGET symbol closes the whole BUY side under an "exit cannot be sent" alert; compute it from held positions only.

## D2-2 — W20 config bounds vs real configs (shipped settings.yaml passes every bound; web and config caps consistent)
- D2-2-1 medium — config.py:348-349 + live_readiness_v1.md:151: the documented remedy "raise the band to about 1 pp" fails to load (default gain-band tier 0.005 < base band) → next restart's ExecStartPre doctor fails. Fix: name the fix in the error text and the doc.
- D2-2-2 low — settings.yaml:99-101 "Uncomment to enable" for a block whose body is enabled: false.
- D2-2-3 low — config.py:218-221 `alerting.email: false` refused ("must be a mapping"); accept False as disabled.

## S2-2 — secrets and identifier redaction (orchestrator: S2-2-1 confirmed — operational_event.py:244 to_dict emits the top-level account_id raw; masking reaches only `fields`)
- S2-2-1 medium — INCOMPLETE of S6-2: top-level OperationalEvent.account_id is never masked → journal column, ops events/explain/timeline JSON, /api/ops/events, /api/ops/incidents and the dashboard tax-ytd payload carry the full account id. Fix: mask in to_dict/column and in tax status/errors.
- S2-2-2 medium — email_alerts.py:113-118/363-365: SMTP refusal text (recipients/sender) is scrubbed only of user/password; addresses reach journal fields.error, journald and the bundle.
- S2-2-3 low — free-text alert messages are never redacted; off-host command stderr (could hold a token/signed URL) is e-mailed and journaled raw.
- S2-2-4 low — config.py:202 invalid-address error text carries the address into doctor.json / sanitized_config.json of the bundle.
- S2-2-5 low — key matcher misses apiKey / APCA-API-KEY-ID / private_key / access_key / passphrase / ticket (latent; no current caller).
- S2-2-6 low — doctor prints the SMTP host on every ExecStartPre; INFO log of the host reaches the /ws/logs stream.

## C2-2 — to_thread offloads (stream dispatch is serial, so trade updates for one order never overlap)
- C2-2-1 medium — web/services/settings.py:367→396: the settings write window opens after the has_active_runtime await; a start in the gap leaves a save writing config under a running engine with no restart and no warning. Fix: open the window before the check, or check-and-open under _state_lock.
- C2-2-2 medium — REGRESSION of W09 perf 3: engine.py:1158-1179 reset_broker / start swap do not take _account_refresh_lock; an in-flight refresh republishes the dropped (e.g. LIVE) broker's account as fresh cache. Fix: take the refresh lock or tag snapshots by broker identity.
- C2-2-3 low — INCOMPLETE of CC-9: runtime.py:3141/3199-3207 the post-fill risk persist reuses the pre-booking broker snapshot (fence captured after the bump; _last_account_at not cleared).
- C2-2-4 low — settings.py:419-438: cancelling the save mid-write closes the window and skips the .env rollback while the worker keeps writing.
- C2-2-5 low — routers/safety.py:111-118: LIVE mode toggle checks the arm before the settings lock, not at write time (start re-checks, so fail-closed).
- C2-2-6 low — runtime.py:3123: a 'calculated' done_for_day computed before booking can relabel a filled row reconcile wrote in between (quantity correct).
- C2-2-7 low — engine.py:1502-1524 confirm_live_arming check-then-set without a lock can re-arm after a concurrent stop/consume invalidated it.

## R2-3 — backup/restore with peak sidecars (orchestrator: R2-3-1 confirmed — backup.py:179-180 raises on an invalid sidecar and the failure cleanup at 310-316 deletes the verified DB snapshot too)

- **R2-3-1** (medium, high) backup.py:179-189, 307-316 — one invalid peak sidecar fails every backup set, DB included, until the file is hand-repaired. Fix: keep the DB set, quarantine/omit the sidecar, alert `sidecar_invalid`.
- **R2-3-2** (low, high) backup.py:297 vs 307-309 — DB is published before its sidecars; a SIGKILL in between leaves a DB-only set that verifies and counts as newest. Fix: write sidecars first, DB link last as commit point.
- **R2-3-3** (low, high) backup.py:157-158, risk_peak.py:56-57 — validators disagree; `epoch: 1e400` raises uncaught OverflowError in both; backup never checks epoch>=0/account_id type. Fix: one shared validator.
- **R2-3-4** (low, high) scripts/repair_local_state.py:373-414 — resolution jsonl appended before the DELETE commits; a later exception leaves a false "resolved" record. Fix: append after commit.
- **R2-3-5** (low, medium) repair_local_state.py:367-374 — `decision_worker_crashed` DELETE is unscoped; one account's clean verdict clears every account's incidents. Fix: scope to the reconciled account.
- **R2-3-6** (low, medium) utils/io.py:81-97 — atomic_write now follows symlinks (realpath) so a planted link redirects the write outside the state dir (needs prior dir write access). Fix: replace on the original path or refuse links leaving the parent.
- **R2-3-7** (low, high) backup.py:46, 51, 52, 182 — orphan atomic_write temps for sidecar copies match neither the stage sweep nor prune globs; they accumulate after kills.
- **R2-3-8** (low, medium) backup.py:327-347 — failed off-host member never retried; remote set can stay DB-without-sidecar permanently.
- **R2-3-9** (low, high) backup.py:399-421, cli.py:526-535, operations.md:539 — `--verify-only` restore gate never checks the set's sidecars. Distinct from D2-1-8.
- **R2-3-10** (low, high) backup.py:459-461 — prune's -wal/-shm unlink unguarded; a concurrent delete aborts the prune loop silently.
- **R2-3-11** (low, medium) repair_local_state.py:545-562 — engine-liveness checked once; Restart=on-failure can bring the engine back mid-repair. Fix: shared flock or re-check.
- **R2-3-12** (low, high) backup.py:67-72, 236-237; cli.py:544-553 — `BackupSourceMissing` "skip without alert" contract is unreachable (no caller catches it); fix the doc or the caller.

Checked fine: keep-label sets never pruned; newest set never pruned; off-host argv quoting; stage sweep age floor; WAL via online backup API; exception-path cleanup; verify_backup mode=ro + magic bytes; repair script refuses on live engine and backs up the ledger.

## Q2-3 — mutation-style survey of fix-wave conditions (orchestrator: Q2-3-1 confirmed — runtime.py:1104-1108 gate is correct but tests only use HALTED/ACTIVE; downgraded to medium as a pure test gap; Q2-3-4 confirmed from backup.py:176-190 ordering)

- **Q2-3-1** (medium, high; auditor said high) runtime.py:1104-1108; tests/test_audit_fix_followups.py:40-52 — BUY gate never tested in REDUCING; `is not ACTIVE` → `is HALTED` survives. Fix: parametrize ACTIVE/REDUCING/HALTED with real latch flags.
- **Q2-3-2** (high, high) web/managers/engine.py:1078-1098; tests/test_audit_fix_W09.py:443-476 — most `_live_doctor_refusal` clauses untested (secret collision, empty approval path, PAPER DB path, `if problems`, credential file mode, 31-char key, negative cap). Fix: table-driven test, one case per clause.
- **Q2-3-3** (medium, high) runtime.py:5842-5877; W14 tests — runtime no-op path of `start_new_risk_epoch` untested; `expected_epoch` captured after `broker.account()`, so a reset during the broker call is absorbed (comment 5843-5844 wrong). Fix: capture epoch first; add test.
- **Q2-3-4** (medium, high) backup.py:178, 183, 189, 314-315; W19 test — only the first sidecar is ever invalidated, so cleanup of earlier copies, read-back failure and the unreadable branch are unpinned.
- **Q2-3-5** (medium, high) config.py:301, 438, 868, 876; W20 tests — `sma_bars=1`, `max_positions: true`, `vol_target=0.5`, gain tier 0.2 boundaries untested; `sma_bars: 1` and `max_positions: true` mutants survive.
- **Q2-3-6** (low, medium) runtime.py:2725-2727; W07 tests — breaker choice in `_release_halt_reason` untested with a mixed remaining set.
- **Q2-3-7** (medium, high) alpaca_reconciliation.py:310-312, runtime.py:3066 — done_for_day tolerance and missing-quantity fallback unpinned (99.5/100 case, no-quantity zero-fill).
- **Q2-3-8** (medium, high) alpaca_account.py:164-168; test_portfolio_runtime.py:1409-1423 — flat-read scope reset never asserted after exit; a leak would re-open G-1/D8-1.
- **Q2-3-9** (low, medium) alpaca_account.py:62, 80 — `QUANTITY_MATCH_FLOOR` can be raised to 1e-7 without a failure.
- **Q2-3-10** (low, medium) runtime_service.py:2942 — snapshot-ahead `+1e-9` untested below one share.
- **Q2-3-11** (low, medium) runtime_service.py:291, 2173, 2177 — `"cancelled"` spelling and the fully-filled `remaining` guard untested.

Checked fine (mutant killed): validate_order_batch grid/min-size; `_snapshot_ahead_of_owned` comparisons; advance-gate BUY/running/breaker; two-empty-reads; T7-2 parity; done_for_day `>=`; W14 reset_peak_equity comparisons; W09 32-char/cap/paper-key; W20 exact ceilings; `_health_exit_code` labels; W05 residual statuses; W19 paper sidecar; W07 remaining filter; W06 session compare.

## F2-3 — Turkish tax ledger after W13 (orchestrator: F2-3-1 confirmed — export.py:166 credits min(withholding, dividend_tax); the 20% cap is used only in the note at 171-172. Legal reading is unverified, but the code does credit excess withholding)

- **F2-3-1** (medium; high mechanism, medium legal) tax/export.py:166 — foreign tax credit not capped at the 20% treaty rate; 30% withholding without W-8BEN understates TL tax. Fix: `min(withholding, 0.20*gross, dividend_tax)`, report the excess separately.
- **F2-3-2** (medium, medium) tax/rates.py:44-46, 309-319 — Yİ-ÜFE fetch writes new months from a series the code calls unconfirmed, with no overlap check when the file has no overlapping months. Fix: require an agreeing overlap month or an explicit confirmed-series flag.
- **F2-3-3** (low, medium) export.py:162, 199, 203 — `dividend_tax_due_tl` shown as due even when `income_declaration_required` is false.
- **F2-3-4** (low, medium) tax/cli.py:98-111 — a PAPER config pointing at the LIVE default ledger is not refused for a PAPER import.
- **F2-3-5** (low, low) tax/dividends.py:27, 31, 107-125 — only DIV/DIVNRA/INT fetched; other withholding/fee types neither imported nor listed in notes.
- **F2-3-6** (low, high) tax/ledger.py:140-150 — ownership-adjustment guard ignores created_at, so a later-year adjustment blocks an earlier year's report.
- **F2-3-7** (low, high) tax/rules.py:22-54 — no 2025 bracket/threshold table; `tax export --year 2025` refuses.
- **F2-3-8** (low, low) runtime_repository.py:1540, 1557; ledger.py:118-120 — unobserved fees become 0; ledger never checks `fee_observed`.
- **F2-3-9** (low, medium) runtime.py:4544-4552; export.py:275-306 — daily tax line can read "ok" with zeros for account "unknown".

Checked fine: progressive_tax boundaries; indexation (prior month, 10% threshold); dividend/interest stacking; in-year loss netting (doc "Open points" still lists carry-forward — doc inconsistency); FX day-before rule; fee pro-rating; FIFO partial lots and oversell fail-closed; DIVNRA sign; year scoping; account-id join; --live remap; worked example reproduces 9,700 TL gain. Legal inputs (2026 brackets, 22,000 TL threshold, treaty cap, Yİ-ÜFE boundary) unverified offline.

## T2-3 — calendar, DST, holidays (orchestrator: T2-3-1 confirmed — alpaca.py:229 resets and :705 sets `last_incomplete_symbols` on the shared client; runtime.py:6254 reads it after the fetch, and the hourly verifier uses the same `_historical`)

- **T2-3-1** (medium, medium) data/alpaca.py:229, 705; runtime.py:4854, 4945, 5360-5365, 6244-6254 — W17 `last_incomplete_symbols` is shared instance state across the warmup retry and the hourly verifier threads; an interleaved call can clear the marker and let a holey window seed. Fix: return the incomplete set per call.
- **T2-3-2** (low, medium) signal_cross_check.py:351-373; runtime.py:3764-3786, 5124-5131; config.py:892-896 — engine deadline (close + 40 min) can expire a parked batch while the cross-check's own retry window is still open (late gate clear, early close).
- **T2-3-3** (low, high) cli.py:147-148, 1074-1079; operations.md:355 — `ops events --since/--until` read naive ISO as UTC, undocumented.
- **T2-3-4** (low, low) nyse_calendar.py:131-137; timeframes.py:59-61; signal_cross_check.py:258-263; runtime.py:3758-3765 — the 16:00 ET fallback fires for non-session dates as well as degraded calendar.

Checked fine: stale-decision NY keys; Q-8 operator-halt poll (monotonic); W15 premarket probe; W07 batch deadline incl. early close/holidays; early-close aggregator flush; DST switch 2026-11-01 outside sessions; label mix handled by `_align_session_tails`; intent watchdog deadline; W13 FX TURKEY_TIME; W01 December window; daily-summary year rollover; shadow comparator fee date.

## S2-3 — supply chain (orchestrator: S2-3-2 checked on the VM — `packaging` 26.2 is present in the venv although it is not in requirements.lock, and doctor reports `PASS locked pins: 36 installed versions match the lock`. Not live today; latent on a clean venv, so downgraded to low)

- **S2-3-1** (medium, high) pyproject.toml:3; deployment.md:335, 432, 556; ci.yml:49, 69; Dockerfile:24 — INCOMPLETE of S7-5: build backend (setuptools/wheel) pinned but unhashed; it runs as the deploy user during `pip install -e .` on the VM checkout. Fix: hashed build-tools file + `--no-build-isolation`.
- **S2-3-2** (low after VM check; auditor said medium) cli.py:667-705; requirements.lock:153 — the marker skip needs `packaging`, which is absent from the runtime lock; on a clean venv `_marker_applies` returns True and colorama WARNs every start. Fix: evaluate markers without packaging or lock it; add a test hiding packaging.
- **S2-3-3** (medium, high) deployment.md:430-435; no CODEOWNERS — deploy review shows only commit subjects; nothing detects a lock change. Fix: `git diff --stat` of lock/pyproject in the deploy step + approval.
- **S2-3-4** (medium, high) ci.yml:21, 31, 47, 50, 78 — CI audit/build tooling (pip-audit, build, pip) unpinned and unhashed; build backend never audited; the "single dependency source" comment is wrong.
- **S2-3-5** (low, medium) ci.yml:40 — checkout persists GITHUB_TOKEN while unhashed build code runs. Fix: `persist-credentials: false`.
- **S2-3-6** (low, high) cli.py:677-705, 918-933 — doctor pin PASS checks versions only, ignores unlocked extras, reads the lock relative to cwd, and its WARN never blocks strict mode.
- **S2-3-7** (low, medium) pyproject.toml / uv.lock — no release-age gate (`exclude-newer`); certifi was locked 4 h after publication.
- **S2-3-8** (low, high) deployment.md:334, 435; Dockerfile:23; ci.yml:48 — INCOMPLETE of S7-1: no `--only-binary :all:` on hashed installs.
- **S2-3-9** (low, high) Dockerfile:22-27 — unhashed pip upgrade/build env, no CI image build, chown makes /app/src writable by the service user.
- **S2-3-10** (low, medium) deployment.md:330-331 — first-install clones an unverified bundle from shared /var/tmp (update path has verify + checksum).

Checked fine: every lock pin hashed; all packages from pypi.org; `uv lock --check` + export diff; CI permissions contents:read; httpx2 provenance; update-path bundle verify/checksum/CI-green check; uv pinned; Dockerfile non-root + `--require-hashes`.

## C2-3 — W15 synchronous safety journal writes (orchestrator: C2-3-7 confirmed — alerts.py:116 records the throttle key before the write and :145 discards `emit_journal`'s result; C2-3-4 confirmed — runtime_repository.py:1477 emits inside the open `store.lock` + connection block. Common theme: W15 made safety kinds synchronous, and about 8 call sites hold a lock or the event loop at the time)

- **C2-3-1** (medium, high) runtime.py:3112-3246; runtime_service.py:320-345 — `order_rejected`, `exit_sell_rejected`, `data_provider_unavailable` written synchronously on the asyncio trade-update loop. Fix: `asyncio.to_thread(alerts.emit, ...)`.
- **C2-3-2** (medium, high) runtime.py:1065-1076, 2992-3007, 4117-4170; runtime_service.py:398-420, 2549-2565, 3437-3481 — decision/rebalance/exit-retry paths emit safety alerts under `_portfolio_lock`. Fix: collect events, emit after release.
- **C2-3-3** (medium, high) runtime.py:2463-2521, 5374-5381 — `risk_incident`/`drawdown_halt` emitted under `_risk_state_lock`; a stall stops the account loop heartbeat.
- **C2-3-4** (medium, high) runtime_repository.py:1415-1477, 1977-2115 — `store.lock` held across safety writes; at 1477 the trading-DB write transaction is also open.
- **C2-3-5** (medium, medium) execution/alpaca_account.py:620-639 — `paper_incident` (peak_sidecar_write_failed) emitted under the shared `_peak_lock`.
- **C2-3-6** (medium, medium) monitoring/journal.py:162-169, 272-311 — sync path has no deadline on filesystem I/O (busy_timeout only).
- **C2-3-7** (medium, high) monitoring/alerts.py:103-116, 145; journal.py:272-363 — a failed sync safety write is dropped and its repeats throttled for 60 s. Fix: clear the throttle key on False or spool.
- **C2-3-8** (low, high; e-mail disabled) email_alerts.py:266-386; runtime.py:2055-2060 — `EmailAlertSink.close()` never called on shutdown; notify/close race strands an item.
- **C2-3-9** (low, high) journal.py:315-346 — retention cleanup runs on whichever (trading) thread flushes the 100th row; unlocked counter.

Checked fine: per-call connections, no shared handle or lock nesting in the journal; get_journal under `_global_lock`; stop() leaves sync emits working; alerts releases `_lock` before I/O; 56 runtime emit sites scanned, only `_reconcile_safely` emits under a lock; EMAIL_ALERT_KINDS == SAFETY_JOURNAL_KINDS (adding an e-mail kind makes it synchronous — noted).

## P2-3 — broker side-effect idempotency (orchestrator: P2-3-2 confirmed — alpaca.py:735-742 raises DefiniteOrderRejection on a 404 lookup for any submit exception, while the terminal-status branch at 721 checks `_is_definite_refusal`; whether Alpaca's by-client-id lookup can lag after an accepted POST is unverified offline)

- **P2-3-1** (medium; high mechanism, medium impact) runtime_service.py:2737, 2989-3020, 3537-3539, 3594; runtime.py:3013; alpaca.py:756 — an ambiguous (reconciliation_required) or crash-left re-plan row is superseded by `-replan{N+1}` instead of held, so the same economic BUY can go out twice if the first fills before the snapshot catches up; replan SELL `submitting` rows are never resolved in buy_ready. Fix: hold while any row of the planned order is non-terminal/ambiguous.
- **P2-3-2** (medium, medium) alpaca.py:721-742; runtime_service.py:3569-3578, 3611; runtime.py:2388 — a transport failure (timeout) whose immediate lookup 404s is booked `rejected`, letting `retry_rejected_exit_sells` send `-retry1` sized from pre-fill ownership. Fix: only `_is_definite_refusal` errors become definite on a 404.
- **P2-3-3** (low, medium) runtime_service.py:2832-2843, 2915-2918; runtime.py:3148 — a mid-batch gate hold returns `submit_blocked` with phase still buy_ready after a BUY was sent; a later pass can re-plan a filled BUY (no BUY-side ledger-ahead check).
- **P2-3-4** (low, medium) alpaca_reconciliation.py:84-85, 207, 314-340; runtime_repository.py:77-80, 1161-1170 — reconcile cancel of a >600 s in-flight POST is permanent; a late acceptance cannot regress the row and a residual SELL follows. Fix: skip when `submit_call_started_at` set and `submit_call_returned_at` null.
- **P2-3-5** (low, medium) runtime.py:574; web/managers/engine.py:813-910 — no cross-process guard (flock/pidfile) on the broker-write path; relies on single-instance deploy.

Checked fine: single broker write site (`submit_order`), no cancel/replace/close calls anywhere; every submit path holds `_portfolio_lock`; deterministic client ids + `_session_decided`; `_has_open_order` and drift gate pre-send; rejected SELLs never replanned; residual round accounting; monotonic fill booking + non-regressing status SQL; ownership rebase CLI no broker calls; LIVE arming probes read-only. External assumptions: Alpaca rejects duplicate client ids; by-client-id lookup is read-after-write consistent.

## Q2-4 — time-dependence and flakiness (orchestrator: Q2-4-1 confirmed — test_portfolio_runtime.py:1430 seeds by `datetime.now(UTC).date()` while runtime.py:2634 loads by the New York date)

- **Q2-4-1** (medium, high) tests/test_portfolio_runtime.py:1430, 1504-1506; tests/test_safety_incidents.py:75, 95-97, 255-256; runtime.py:2624-2637, 5380 — risk-state tests key the row by UTC date, the runtime by NY date; they fail 00:00–04:00 UTC (05:00 after 2026-11-01). Fix: use the NY date in the tests or inject the clock.
- **Q2-4-2** (low, medium) tests/test_audit_fix_W09.py:607-624 — cancel-before-release ordering depends on a 50 ms Timer; flaky under load.
- **Q2-4-3** (low, high) tests/test_audit_fix_W09.py:139-146; tests/test_observability_wiring.py:53-68, 205, 211 — negative timing checks pass vacuously (0.1 s sleep, 1 s absence window).
- **Q2-4-4** (low, medium) runtime_service.py:267, 470-472 — module-global `_no_trade_last` dedupe leaks across tests; only one test resets it. Fix: autouse reset fixture.

Checked fine: seeded RNG; no naive local time/locale/TZ; runtime clocks injected in fix-wave tests; 2025/fixed 2026 bar dates; thread joins with timeouts; polling loops with deadlines; socket timeouts; wide mtime margins; W14 explicit now; list (not set) equality; mkdtemp AF_UNIX paths; lifespan shutdown bound margin.

## T2-4 — numeric precision (orchestrator: T2-4-1 confirmed by reading — alpaca_account.py:625 nets only same-day flows, so a deposit enters the peak the next day. The flag defaults to False (config.py:411) and config/ does not set it, so this is latent until someone enables it)

- **T2-4-1** (medium; latent, flag off) alpaca_account.py:138, 192-207, 423-427, 625-651; runtime.py:537-540; config.py:411 — INCOMPLETE of F9-2: with `risk.adjust_for_cash_flows` on, a deposit is absorbed into the persisted peak on day D+1 (and on day D if the activities read fails, :205 returns 0.0), so the drawdown halt trips early. Fix: a cumulative cash-adjusted index; never raise/save the peak on unknown flows.
- **T2-4-2** (low, medium) runtime_repository.py:1490, 1945-1950; ownership_rebase.py:221; tax/ledger.py:35, 224, 234 — lot-book/realized-P&L tolerance (relative 1e-6 band) is looser than the tax FIFO's absolute 1e-9; excess shares get booked at full price with zero cost while the tax export refuses.
- **T2-4-3** (low, medium) alpaca_account.py:62, 73-80; ownership_rebase.py:412; runtime_repository.py:2166-2169 — three absolute floors (1e-8, 1e-12, 1e-9) for "same share count" despite the docstring claiming one shared band; dust positions can raise `ownership_surplus_unconfirmed`.
- **T2-4-4** (low, low) runtime_ownership.py:60; alpaca_account.py:449-452; alpaca_reconciliation.py:549-552 — "flat" is absolute 1e-12 on float-summed fills; only very large holdings risk residue.
- **T2-4-5** (low, medium) ownership_rebase.py:201-215 — `_replay_lots` silently skips BUY fills with price <= 0 (NaN not checked) while ACQUIRE raises; upstream guards make it unlikely.
- **T2-4-6** (low, high; unreachable) alpaca_orders.py:87 — submit-time notional guard passes NaN/inf and skips the policy floor; defense in depth only.
- **T2-4-7** (low, medium) telemetry.py:1337 — fill "complete" uses 1e-12 vs the broker/runtime band max(1e-9, 1e-8·q); metrics only.
- **T2-4-8** (low, medium) backtest/portfolio_engine.py:1238, 1605, 1660 — backtest never floors to the asset increment or applies min size/notional; negligible for fractionable QLD.

Checked fine: floor_increment Decimal path and F-1 grid check; floors only lower quantities; non-finite input guards (Bar, PositionSnapshot, broker price, equity/cash, planner); equity/drawdown denominators; fill/over-fill bands consistent; split classifier gating; format_quantity; backtest drawdown/dividend/FIFO guards; weight-sum tolerance; F9-2 default-off behaviour already known.

## D2-3 — observability of the new fix-wave statuses (orchestrator: D2-3-1 confirmed — runtime_service.py:1485-1491 returns a hard-coded "rebalance_in_progress" with target None, dropping advance_result.status; D2-3-3 confirmed — `_raise_operator_alert_once` keys on (leg, reason) for the process lifetime)

- **D2-3-1** (medium, high) runtime_service.py:1142-1158, 1430-1491; runtime.py:4316-4361, 4490; dashboard.js:25-31, 301-304 — a scheduled decision on a session with an open rebalance reports "rebalance_in_progress" for every hold reason, and the daily summary and PAPER receipt are skipped on exactly the held days; the catch-up label shows raw tokens.
- **D2-3-2** (medium, high) runtime_service.py:2689-2920 (returns at 2845, 2879, 2901, 2915); runtime.py:3017-3019; health.py:555-560 — the four BUY-hold statuses raise no alert, e-mail, journal decision event or dashboard field; only a reason-less "stuck rebalance >1h" DEGRADED after an hour. `closed_unresolved` is invisible to ops/doctor. Observability half of P2-2-1.
- **D2-3-3** (medium, high) runtime_service.py:678, 2293-2299, 2489, 2543-2568 — operator alert dedupe for held/dropped SELL legs is per process lifetime and lacks decision_id; a recurring episode is never e-mailed again.
- **D2-3-4** (medium, high) dashboard.js:229, 246-248; runtime.py:765-772; health.py:783-800 — dashboard headline and risk story stay green while the intent watchdog reports a missed decision (K-3) or the cross-check holds.
- **D2-3-5** (medium, high) events.py:22-24; cli.py:817-830; backup.py:59; parayabuck-backup.service — the documented backup-freshness alert is not implemented; a stopped timer is visible only as a doctor WARN at engine start.
- **D2-3-6** (medium; flag off by default) runtime.py:403-410, 2600-2620; runtime_service.py:413-436, 3569-3588 — per-symbol drift-lock local refusals are reported as broker rejections and escalate as `exit_sell_rejected` e-mails; locked symbols are on no surface.
- **D2-3-7** (low, high) web/routers/engine.py:71-77; runtime.py:6046-6049 — operator reset reports success while other halts remain (WARNING log only).
- **D2-3-8** (low, high) cli.py:1623-1655; forensics.py:471-483 — `ops explain` NO TRADE branch omits the rebalance phase, says "decision_summary (none)" when one was read, and maps the new tokens to UNKNOWN.
- **D2-3-9** (low, medium) alpaca_account.py:386-387; runtime.py:311, 2446-2460 — a non-ACTIVE account status alerts only after 5 polls under the generic `account_fetch_failures`; halt not persistent.

Checked fine: ownership-drift circuit_breaker; per-symbol lock set/clear alerts; reconcile latch alerts; stale-account dashboard flag; rearmed halt migration alert; operator-halt marker alert; undecoded/auth stream paper_incident; W19 backup refusals journaled; W13 tax soft status.

## F2-4 — FIFO realized P&L and lot integrity (orchestrator: no high/critical; F2-4-1 duplicates T2-4-5 and F2-4-5 overlaps T2-4-3/T2-4-4 — kept for cross-reference, count once)

- **F2-4-1** (low, medium; dup of T2-4-5) ownership_rebase.py:213; runtime_repository.py:1687-1697, 1855-1949 — non-positive/NULL-price BUY dropped from lot replay; later sells book 0.0 P&L.
- **F2-4-2** (low, medium) runtime_repository.py:1507-1573 — `INSERT OR IGNORE INTO portfolio_fills` has no rowcount check while the order cumulative and stale-clear consumption still advance; an activity_id collision desyncs ownership from lots silently.
- **F2-4-3** (low, medium) alpaca.py:566-580; runtime_repository.py:1532-1538 — recovered fills can be dated at submit time; a late-dated SELL is costed against end-of-history lots (sell-side sibling of F2-1-5).
- **F2-4-4** (low, high; metadata only) runtime_service.py:761-770 — TL shadow buy-date pairing ignores ACQUIRE lots.
- **F2-4-5** (low, high; overlaps T2-4-3/4) runtime_ownership.py:60 vs ownership_rebase.py:235, 275, 294 — 1e-12 vs 1e-9 dust floors; owned but no lots.
- **F2-4-6** (low, low) alpaca_updates.py:150-152; runtime_repository.py:1520-1571 — an order-level cumulative commission would be booked in full on every partial fill (speculative).

Checked fine: cumulative-guard replay dedup; `_execution_price` order independence; stale-clear consumption vs replay removal; sign conventions incl. SPLIT_ADJUST; P&L formula; average-cost vs FIFO under partial sells; RLock nesting; legacy backfill; recent-fills reader. raw-r1 F-2 (all-time realized added to session risk) still present, not re-reported.

## R2-4 — systemd lifecycle + W09 shutdown/lifespan (orchestrator: R2-4-1 confirmed in the pinned uvicorn — server.py:331-337 restores the original handlers (SIG_DFL for SIGTERM) and re-raises the captured signal inside serve(), so app.py:123-126 is never reached on a signal stop)

- **R2-4-1** (medium, high) web/app.py:122-126; uvicorn server.py:76-79, 331-337; tests/test_audit_fix_W09.py:670-706 — INCOMPLETE of R7-3: the non-zero exit on `engine_stop_failed` is unreachable on SIGTERM/SIGINT; systemd sees a clean stop. The test fakes `uvicorn.run`. Fix: exit explicitly from the lifespan path or a Server subclass that does not re-raise; test against the real Server.
- **R2-4-2** (medium, medium) web/lifespan.py:171-183; uvicorn server.py:282-299, 339-343; runtime.py:1652, 1664 — INCOMPLETE of R7-2: `request_shutdown` runs only after uvicorn's HTTP drain (up to 30 s), so an in-flight start or auto-start can pass its cancel checkpoints and submit orders during the drain.
- **R2-4-3** (medium, medium) parayabuck.service:11, 32; parayabuck-backup.service:6, 16; cli.py:851; backup.py:413 — full `PRAGMA integrity_check` runs inside the default 90 s start timeout of both units (no TimeoutStartSec); a growing ledger eventually fails starts and the backup with no alert. Extends R7-5.
- **R2-4-4** (low, medium) runtime.py:1999-2029; web/managers/engine.py:1126, 1140; alpaca stream.py:202-205; parayabuck.service:37-39 — INCOMPLETE of R7-3: the 180 s stop budget is not a hard bound (unbounded lock waits and SDK stream stop); order-placing pb-decisions gets only a 5 s join.
- **R2-4-5** (low, high) web/lifespan.py:86-100; engine.py:1118-1120 — a start attempt still running past the 30 s bound is killed at exit; its documented self-cleanup never runs.
- **R2-4-6** (low, medium) alpaca_updates.py:182-190; alpaca_stream.py:215-224; runtime.py:1584-1620 — stream `stop()` is a no-op before the SDK loop exists, but the worker then calls `run()` and never exits. Fix: a `_stop_requested` flag checked by run().

Checked fine: unit ReadWritePaths cover all writes; sd_notify READY after bind probe; watchdog healthy during stop by design; respawn sites check `_running` under the stop snapshot lock; storage never closed under a live writer; ledger writes transactional; Restart=on-failure does not restart after SIGTERM.

## C2-4 — broker _peak_lock and risk_peak service (orchestrator: C2-4-1 confirmed — alpaca_account.py:390 reads equity before taking `_peak_lock` at 620, so a pre-reset read can raise the peak after the reset commits; the runtime.py:5858-5859 comment overstates the protection. C2-4-6 confirmed at risk_peak.py:254-258. Lock order checked: no cycle)

- **C2-4-1** (medium, medium) alpaca_account.py:390, 427, 620-625; runtime.py:5842-5874; risk_peak.py:431-440 — a pre-reset REST equity read is applied under `_peak_lock` after the epoch reset, re-raising and persisting the old peak; drawdown halt re-latches although the operator saw success. Fix: capture the epoch before the REST reads and skip the raise if it changed.
- **C2-4-2** (medium, medium) runtime.py:2340-2385, 5874-5880; runtime_repository.py:1618-1668; runtime_service.py:1558-1563 — DB high-water is max-merged from a pre-reset snapshot after `reset_high_water`; decisions keep measuring from the old peak. Fix: bump the snapshot generation on reset or store the epoch on the high-water row.
- **C2-4-3** (medium, medium) alpaca_account.py:334-341, 662; runtime.py:2362, 3197-3212; engine.py:1284 — overlapping `account()` calls publish `_snapshot_cache` unfenced with a publish-time timestamp; a pre-fill snapshot can be served as fresh to the fill path. Mechanism behind P2-1-3.
- **C2-4-4** (low, medium) runtime.py:5862-5888; risk_peak.py:462-475 — if the rollback sidecar write fails, a restart adopts the reset that was reported as failed.
- **C2-4-5** (low, high) runtime.py:5395-5401; risk_peak.py:199-214 — `_persist_risk_state` stamps the live `_peak_epoch` on a snapshot-derived peak; a restart in the 15 s window can restore the pre-reset peak.
- **C2-4-6** (low, medium) risk_peak.py:254-258 — exists()/read TOCTOU; `assert data is not None` escapes inside `_peak_lock` (TypeError under -O).
- **C2-4-7** (low, high; latent, flag off) runtime.py:5847; risk_peak.py:437; alpaca_account.py:625, 651 — with `adjust_for_cash_flows` on, the reset baseline is raw equity while the running peak is net of cash; restarts inflate the peak. Companion to T2-4-1.

Checked fine: all sidecar save/reconcile/restore/reset calls under `_peak_lock`; `_peak_save_pending` inside the lock; reset ordering sidecar → incident → DB high-water resolves on crash by epoch; high-water RMW under store.lock; `query_persisted_peak` fails closed; engine refresh single-flight (but see C2-4-3).

## S2-4 — file permissions, umask, atomic_write (orchestrator: all low; no medium in this lens. The systemd units set UMask=0077, so the gaps apply to manual runs)

- **S2-4-1** (low, high) scripts/repair_local_state.py:176, 212, 273, 306-307; scripts/reconcile_paper_incident.py:305; monitoring/paper_incident.py:285 — repair/reconcile scripts never set umask; the full ledger copy in data/quarantine and the reports are 0644 under umask 022, outside backup prune.
- **S2-4-2** (low, high) __main__.py; web/app.py:52-64; cli.py:275 — `os.umask(0o077)` only in cli.main; `python -m parayabuck`, a direct uvicorn launch and scripts write state under the invoker's umask.
- **S2-4-3** (low, medium) web/managers/engine.py:1971-1990; utils/io.py:83; .gitignore:165 — an orphaned `.env.XXXX` temp holds the full credential set, is never swept and is not gitignored.
- **S2-4-4** (low, high) deployment.md:107-116; parayabuck-backup.service:14-16 — `.env.backup` (SMTP password, off-host command) documented 0600 but no code checks its mode.
- **S2-4-5** (low, high) config.py:622-626; engine.py:394-397; deployment.md:88-90 vs cli.py:710-778, parayabuck.service:32 — comments/docs say a loose .env never stops a start; `doctor --strict` in ExecStartPre does block it (fail-closed, doc wrong).
- **S2-4-6** (low, medium) monitoring/diagnostic.py:502, 514 — diagnostic bundle staging uses a predictable name and `ZipFile(path, "w")`, following an existing symlink. Same class as R2-3-6.

Checked fine: atomic_write mkstemp 0600 + fchmod before write; every atomic_write caller ends 0600; backup stage/link/sidecars; receipts via mkstemp+link; all units UMask=0077; .env outside ReadWritePaths; halt marker O_NOFOLLOW + S_ISREG; no file log sink; LIVE refuses loose .env; restore installs -m 600.

## F2-5 — fees (orchestrator: F2-5-1 mechanism consistent with F2-3-8 — the only activity fetch is DIV/DIVNRA/INT (tax/dividends.py:27) and FILL-activity commission backfill was removed in d88e6c91; Alpaca fee activity type names unverified offline. Count with F2-3-8 as one root cause: live books never ingest regulatory fees. PAPER charges none, so no PAPER impact)

- **F2-5-1** (medium, high mechanism; LIVE-only) alpaca_updates.py:147-157; runtime_repository.py:1432-1433, 1540, 1557, 1893, 1950; tax/dividends.py:27, 32; telemetry.py:10, 145-155, 1004-1012 — live never ingests SEC/TAF/CAT fee debits; realized P&L, lot costs and the tax FIFO omit them while the backtest charges them. Fix: import fee activities daily and reconcile to broker cash.
- **F2-5-2** (low, medium) backtest/regulatory_fees.py:50-111 — no horizon guard on the effective-dated table; 2027+ dates silently get 2026 rates.
- **F2-5-3** (low, high) portfolio_engine.py:244-261; telemetry.py:819-871; shadow_comparator.py:362-390; live_readiness_v1.md:70 — Alpaca's daily round-up to $0.01 is documented but not modelled.
- **F2-5-4** (low, high) shadow_comparator.py:203, 247, 320, 351, 458-512, 591 — no path compares modelled vs observed fees; comparator is PAPER-only with `fee_known=False`.

Checked fine: SEC/TAF sell-only, CAT both sides; schedule matches docstring; inclusive boundaries tested; one flag/table across backtest, product, telemetry, shadow; trade-date alignment for regular session; fee in proceeds/P&L; affordable-buy exact; TAF cap per fill immaterial; cost multiplier scope; recovery path keeps fee None.

## T2-5 — warmup and history seeding (orchestrator: T2-5-1 append-only rule confirmed at runtime_service.py:922-924 and 1197-1211; the empty-first-warmup + live-close ordering is from the auditor's trace, not re-traced. T2-5-3 merges with T2-3-1)

- **T2-5-1** (medium, medium) runtime_service.py:922-924, 1197-1211, 1333-1376; runtime.py:5288-5325, 6254-6277 — `seed()` is append-only; if the first warmup seeded nothing and a live close was appended, the retry can never backfill and the symbol stays warming_up until restart with no alert. Fix: merge older provider bars by timestamp, or do not append live bars before seeding.
- **T2-5-2** (medium, medium) runtime.py:1890-1917, 5262-5298, 5971-5972; data/alpaca.py:641-643 — a managed equity pin with no provider history keeps `_not_ready_symbols` non-empty forever, blocking `reset_safety_lock` with a misleading message and pinning state at warming_up.
- **T2-5-3** (low, low; merge with T2-3-1) data/alpaca.py:251, 260; runtime.py:4945-4959 — `_last_batch_degraded` is shared client state; a concurrent retry can clear the verifier's degraded flag.
- **T2-5-4** (low, high; latent, warmup does not chunk) alpaca.py:238-239, 425-432, 610-615 — an all-empty chunk leaves a hole not marked incomplete.
- **T2-5-5** (low, medium) alpaca.py:618, 709-718, 799-843; timeframes.py:65-79 — an in-session partial daily bar can be cached as a complete window and later cache-seeded as final; decisions held by the basis latch, so impact bounded.

Checked fine: NYSE-aware warm window sizing with margin; minimum_history_bars vs vote windows (R2 397); partial provider bars dropped in seed; window completeness rules; degraded answers fail closed; warmup-retry scheduling; session batches re-queued while warming; cache keys include feed/adjustment, UTC timestamps; no crypto path; adjustment-mode separation via verifier.

## P2-4 — client-order-id lineage and collisions (orchestrator: P2-4-2 tail-only owed quantity confirmed at runtime_service.py:2172-2176; P2-4-1 needs a restored ledger copy running against the same account — operational precondition, not a normal-path bug)

- **P2-4-1** (medium, medium) runtime_repository.py:485-494; alpaca.py:680-734; alpaca_reconciliation.py:209-219 — the ledger salt travels with backups and is never re-minted, and submit recovery adopts a same-id broker order without comparing quantity; a restored copy re-deciding a decision can record qty 12 against a broker order of 10. Fix: re-mint the salt for a ledger opened from a foreign file; require symbol/side/qty match on recovery.
- **P2-4-2** (medium, medium) runtime_service.py:2172-2176, 3017, 3674-3690 — a `-retry1` sized below the rejected quantity (qty_available cap) leaves the shortfall unowed: the residual gate reads only the tail row, the phase moves to BUY_READY, and the replan of the remaining shares is dropped because its id is rejected. Fix: owed = base quantity − cumulative fills of the whole leg.
- **P2-4-3** (low, high) runtime.py:1125-1146; runtime_service.py:3628-3662 — the account-loop retry candidate check does not mirror the retry's skip rules; a skipped rejected SELL takes `_portfolio_lock` and an OPEN-orders GET every 15 s (~5,700/day).

Checked fine: suffix grammar round-trips (`_sell_leg_root`, `_sell_chain_position`); worst stacked id ~85 < 128 chars; decision_id hashes account|digest|timestamp|market; client_order_id is the PK; separate PAPER/LIVE DBs; replan free-slot search scoped and alerts on exhaustion; residual numbering monotonic; retry-of-retry blocked; superseded decisions held; uuid5 observation ids; reconciliation by exact id; legacy salt form deliberate. Out of lens: `is_terminal_order_status` omits stopped/suspended/replaced (unverified whether Alpaca emits them).

## Q2-5 — test isolation and production-path guards (orchestrator: Q2-5-4 mechanism confirmed at web/managers/backtest.py:118-124 (`shutil.rmtree` of old reports/web-*); no web-* entries exist in reports/ today, so no data was lost)

- **Q2-5-1** (medium, high) tests/conftest.py:419-437, 611-625 — the production-path guard is bypassed by sqlite `file:` URI connects (resolved as `<cwd>/file:...`); read-only opens of the LIVE DB or backups pass; absolute redirected URIs become malformed.
- **Q2-5-2** (medium latent, high) config.py:13, 583-587; conftest.py:150-199 — the dotenv guard never covers `config.load_dotenv` (bound before pytest_configure); only ENV_PATH pointing at a missing file protects the repo .env.
- **Q2-5-3** (medium, high) conftest.py:82-105, 686 — blocked network calls raise a RuntimeError that production broad excepts swallow, and they are not recorded for teardown; `127.`-prefix loopback test admits hostnames like 127.example.com.
- **Q2-5-4** (medium, high mechanism) web/managers/backtest.py:31, 52, 97, 118-124; tests/test_backtest_web.py:38; tests/test_web_backtest_exclusion.py:55 — tests call `start()` with the default `reports/` root, which prunes repo reports/web-* via rmtree; reports/ unguarded.
- **Q2-5-5** (low, high) conftest.py:647 — a leaked-thread `pytest.fail` returns before the journal restore and production-state checks.
- **Q2-5-6** (low, high) monitoring/journal.py:501-551; conftest.py:638 — journal globals not reset per test; isolated writer threads leak and are excluded from the leak check by name.
- **Q2-5-7** (low, medium) conftest.py:38-50; tax/rates.py:39-40, 293-306; runtime_service.py:389-395, 772-800 — data/tax unguarded; TL shadow reads cwd-relative rate files, machine-dependent tests.
- **Q2-5-8** (low, high) tests/test_trading_host_guard.py:131-136; web/app.py:107-117 — a test opens a real browser tab on the developer machine.
- **Q2-5-9** (low, medium) web/security.py:52, 69 — production trust checks relax on `PYTEST_CURRENT_TEST`.
- **Q2-5-10** (low, medium) web/services/dashboard.py:541-549, 838-858 — id()-keyed dashboard caches survive across tests.
- **Q2-5-11** (low, high) conftest.py:419-437, 611-625; tests/test_production_state_isolation.py — open/connect hooks have no tests; the redirect is dead for plain paths.

Checked fine: credential env stripping incl. CREDENTIALS_DIRECTORY; env mutations via monkeypatch; redirects for warmup cache, journal, snapshot, peak sidecar, receipts, stores, Alpaca data client; plain-path writes into protected dirs refused; socket guard coverage; global singletons restored; fakes for SMTP/HTTP; lru_caches pure/keyed.

## D2-4 — dashboard reflects engine state (orchestrator: D2-4-1 confirmed for the story line — dashboard.js:246-248 prints "Risk limitleri içinde" with no latch check. The strategy-card part is uncertain: dashboard.py:353 does check `entry_halt_reason`, and whether that carries the daily-loss/drawdown latch was not re-traced. D2-4-1 and D2-4-2 downgraded to medium because they are display-only; the trading gates themselves hold)

- **D2-4-1** (medium; auditor said high) dashboard.js:246-248, 441-443; dashboard.py:340-359, 579; runtime.py:2463-2498 — the headline and risk badge read "within limits" while the daily-loss/drawdown entry latch blocks new entries.
- **D2-4-2** (medium; auditor said high) engine.py ~1441-1454; dashboard.py:411-456, 553-584, 943-948; dashboard.js:241-248, 288-289, 428-431 — with no account snapshot ever fetched, zero placeholders render as real zeros ("pozisyon yok", "Risk limitleri içinde", 0% exposure). Only the hero is nulled.
- **D2-4-3** (medium, high) dashboard.py:480-502, 591-616; dashboard.js:459, 484 — failed ledger reads render as a green "Aktif güvenlik olayı yok" / "Bekleyen emir yok".
- **D2-4-4** (medium, high) dashboard.py:787-790, 820-824; dashboard.js:523-524; runtime.py:2535-2539, 2700-2731 — reconciliation tile uses only the primary breaker reason, from an incomplete set; a reconciliation halt behind ownership_drift is shown on no surface.
- **D2-4-5** (medium, high) dashboard.js:511-516; dashboard.py:815 — "Piyasa verisi: Güncel" green when no runtime exists (`stream_healthy !== false`).
- **D2-4-6** (medium, high) ui.js:115-121; dashboard.js:555-556; health.py:32-39, 785-795 — ops trading-state badge renders CIRCUIT_BREAKER, ENTRY_BLOCKED, MARKET_HALTED, REBALANCE_BLOCKED and STOPPED as neutral grey raw English.
- **D2-4-7** (medium, high) dashboard.py:349-359; dashboard.js:20-23, 231, 282-283 — strategy card shows "Aktif" when the engine is degraded or warming up.
- **D2-4-8** (medium, high) ui.js:314-323; dashboard.js:227-233, 589-590, 653-658; dashboard.py:1067-1072 — a failed status poll leaves the pill "Çalışıyor" and the story green; an unavailable overview keeps old values marked "canlı".
- **D2-4-9** (medium, high/medium) dashboard.js:258-677; api.js:65-75; tax/export.py:282-294 — partial renders keep old numbers beside a new error label; fetchJson has no timeout, so a hung request freezes that poll forever via the busy flags.
- **D2-4-10** (low, high) ui.js:299; dashboard.js:228-518, 489 — raw backend tokens as labels; "stopping" shown as a red "Motor sorunlu: stopping."
- **D2-4-11** (low, medium) equity_leveraged_trend.py:446-447; trend_status.py:58-72; dashboard.js:339-342 — trend card shows a green "Yukarı" for `hold_symbol_unavailable` (in cash).
- **D2-4-12** (low, high) web/services/ops.py:204, 220; dashboard.js:583, 667-673 — a failed latest-decision read (200 + unavailable) shows "Karar açıklaması yok", contradicting the page's own comment.
- **D2-4-13** (low, high) dashboard.py:459-462; dashboard.js:240, 295 — "Sıradaki kontrol 1 gün önce" when the engine was down through the boundary.

Checked fine: all nine engine states labelled; percent/fraction and USD/TL units; slippage/fee formats; hero nulling; ownership, capacity and trend tokens mapped; UTC-aware timestamps; PAPER/LIVE chip (mode switch refused while running); events 503 handled; tax cache only stores ok; drawdown headline matches the halt basis.

## C2-5 — W04 stream supervisor, backoff, stop (orchestrator: C2-5-1/C2-5-2 confirmed in pinned alpaca-py 0.43.5 — trading/stream.py:164-187 catches WebSocketException, closes and reconnects in-loop after `sleep(0.01)`, never returning to the runtime; data/alpaca_stream.py:107-108 resets `failures` right after connect+auth. The trigger (server dropping an authenticated session repeatedly) is unverified offline)

- **C2-5-1** (high mechanism; trigger unverified) data/alpaca_stream.py:73-110; alpaca trading/stream.py:153-187; alpaca data/live/websocket.py:315-361 — zero-delay reconnect loop: backoff resets after connect+auth, and the SDK reconnects a dropped session after 10 ms with no runtime visibility, no reconnect latch, no jitter. Fix: reset failures only after first frame / N seconds alive; on-reconnect hook sets reconcile-pending.
- **C2-5-2** (medium, high) runtime.py:3288-3340, 3580-3583, 5749-5759; trading/stream.py:50-54, 164-168, 182-185 — the pb-trade-updates supervisor (restart latch, counter, halt) is dead code because `TradingStream.run()` never returns or raises while running; gaps are covered only by the 60 s periodic reconcile; the 300 s stale check is global, not per open order.
- **C2-5-3** (low, medium) execution/alpaca_updates.py:75; data/alpaca_stream.py:131-146; runtime.py:2000-2019 — trade stream keeps websockets' 10 s close timeout (bar stream uses 2 s); the 5 s stop join can return with the old consumer still booking fills.
- **C2-5-4** (low, medium) data/alpaca_stream.py:194-197; alpaca websocket.py:162-164, 358-359 — an exception in the bar handler drops the rest of that frame's bars with only an SDK log.
- **C2-5-5** (low, high) alpaca_updates.py:87-100 — a trade update failing `_parse` is dropped with no reconcile scheduled, contradicting its docstring. Fix: route through the undecoded path.
- **C2-5-6** (low, medium) data/alpaca_stream.py:214-220; alpaca_updates.py:185-190; runtime.py:3288-3291, 6127-6155 — after one run() ends `_loop` stays set to a closed loop, so a stop/respawn race skips the stop and the next run() consumes forever. Variant of R2-4-6.

Checked fine: trade handler contains all exceptions and halts; undecoded frames schedule reconcile; lock-retry sentinel; single in-process consumer via alive flag; pb-decisions respawn checks `_running`; stop sentinels honoured; backoff caps; auth streak alert once at 3; replay dedup via cumulative guard; silent-feed check never raises; submit gate checks `_running`.

## R2-5 — crash mid-persist windows (orchestrator: R2-5-1 confirmed — alpaca_reconciliation.py:337-339 skips rows younger than 600 s and runtime.py:1457-1464 aborts startup on any drift; whether the web manager then leaves the engine in "error" with no retry is from the auditor's reading of engine.py:1018-1020)

- **R2-5-1** (medium, high) runtime.py:1455-1464, 1584-1614; alpaca_reconciliation.py:84, 336-339, 598-601; runtime_repository.py:1353; web/managers/engine.py:1018-1020 — a crash-left `submitting` row younger than 600 s makes the startup reconcile report MISSING_BROKER_ORDER and abort; the web-hosted engine stays in "error" with no auto-retry until a manual start after the row ages. The age gate is unnecessary at startup (no live submit can exist).
- **R2-5-2** (medium, medium) runtime_service.py:2057-2065, 2737-2742, 2852-2855, 2967-2970, 3217-3228 — a crash between the re-plan SELL POST and the SELL_PENDING flip lets the restart complete the rebalance (single symbol) or send other symbols' BUYs before the SELL settles. Fix: flip the phase before submitting.
- **R2-5-3** (low, high) runtime_service.py:1063-1073, 1868-1887, 2629-2630 — after a crash between commit and POST of a pure SELL/BUY decision, restart cancels the rows and never re-sends this session (two-phase path does). Delays a risk-reducing exit by up to a session.
- **R2-5-4** (low, medium) runtime.py:5874-5880; runtime_repository.py:1618-1667; runtime_service.py:1558-1563 — SIGKILL inside the epoch reset leaves the DB high-water at the old peak (never lowered except by reset). Same end state as C2-4-2 via a different trigger.

Checked fine: submit intent rows committed before the POST; decision+orders+phase one transaction; apply_trade_update one transaction; residual SELLs change no phase; BUY loop crash-safe; dropped submitting rows marked rejected; peak sidecar sidecar-first; incidents before risk_state; ownership rebase single transaction; settings/.env atomic writes.

## F2-6 — partial fills, cancels, late-fill races (orchestrator: F2-6-2 shares its root cause with P2-4-2 — residual sized from the tail row only; fix both together)

- **F2-6-1** (medium; high mechanism, rare trigger) runtime.py:1655, 1746-1776, 3147-3158, 3271, 3279, 5451-5520, 6029; runtime_service.py:1118-1124, 1418 — a fill or cancel booked by reconcile (including a refused stream event recovered through reconcile) never advances the two-phase rebalance; the residual and the BUYs wait for the next trade update or closed-bar decision.
- **F2-6-2** (low, medium; same root as P2-4-2) runtime_service.py:291, 2170-2177; runtime_repository.py:74-80, 1519-1547 — a late fill on an earlier/terminal SELL row does not shrink the residual already sent; leg can oversell (likely surfaces as ownership drift).

Checked fine: SQL parameter order; non-regression; late partial on canceled row booked; cancel cum lower/higher than last partial; stream vs reconcile double booking deduped by cumulative delta; post-submit status write blocked on final rows; residual chain parsing; RESIDUAL_SELL_STATUSES scope; same tolerance in reconcile and stream status; `_fill_from_broker_order` refuses qty/avg <= 0.

## T2-6 — R2 replay and W16 caches (orchestrator: no high/medium; W16 lru caches are pure and keyed by full value tuples. Shipped sma_bars 200 is unaffected by T2-6-1)

- **T2-6-1** (low, latent) equity_leveraged_trend.py:175-176, 292-293, 396; portfolio_engine.py:1471, 1502-1503; runtime_models.py:166 — R2 replay's SMA vote has no short-prefix guard; with sma_bars > 272 the replay uses a biased average and backtest/live diverge.
- **T2-6-2** (low, high) runtime_service.py:470-472, 522-524; journal.py:565-571 — no-trade dedupe marks the day before the write and ignores the result; one failed write suppresses the day's no-trade rows. Same class as C2-3-7.
- **T2-6-3** (low, low) equity_leveraged_trend.py:390, 415, 421 — `above_sma50` metadata uses a fresh sum while the state used the running sum; can disagree at exact equality (metadata only).

Checked fine: `_window_volatility`/`_median_of_volatilities` lru caches pure, bounded, immutable, thread-safe; 397 bound exact; warmup/readiness/decision gates consistent; trimmed backtest and live identical for sma ≤ 272; R2 state machine order; stateless replay; canonical_symbol, nyse_sessions, December-season and calendar-days caches pure; no memoised strategy/spec.

## S2-5 — LIVE gates (orchestrator: no medium or higher; every LIVE start path re-checks arm/standing, doctor refusal, approval, account binding, digest and cap. S2-5-1 confirmed at runtime.py:501 (`paper = mode == PAPER`, so DISABLED builds a live-endpoint broker; start refuses before any call). S2-5-3 overlaps F2-3-4)

- **S2-5-1** (low, high) runtime.py:501, 508-511, 543, 1435-1436; engine.py:903-917 — `runtime.mode=disabled` constructs broker and stream on the LIVE endpoint with PAPER keys (construction only). Fix: `paper = mode != LIVE`.
- **S2-5-2** (low, high) engine.py:1090-1091; cli.py:1011-1014; config.py:50-53 — "LIVE ledger ≠ PAPER ledger" uses normpath string compare; an absolute or symlinked PAPER path passes all three checks.
- **S2-5-3** (low, high; overlaps F2-3-4) runtime.py:1431-1447; cli.py:760-800; config.py:50-53 — nothing refuses a PAPER engine whose decision_db_path is the LIVE ledger.
- **S2-5-4** (low, medium) engine.py:1328-1331, 1369-1380 — account display poll reuses the held broker without checking its endpoint against the current mode; pin only applied on the LIVE branch (read-only).
- **S2-5-5** (low, medium) engine.py:1571-1573, 1707-1716; config.py:819-822 — live prerequisites probe keys off raw YAML `live.enabled`, not the parsed mode (needs a broken config).
- **S2-5-6** (low, medium) live_safety.py:279-340, 509-510; runtime.py:728-733 — interactive operator approval is not bound to the trading host and not single-use (arm still required).
- **S2-5-7** (low, medium; may be intended) web/routers/safety.py:39-52; engine.py:1263-1272; runtime.py:5795+ — a LIVE drawdown-halt reset needs only the control token and rebases to current equity, so repeated resets leave cumulative loss bounded only by the cap.

Checked fine: config invariants (mode↔live.enabled, cap, approval path, legacy paper=false refused); /api/mode and settings-save arm gates; arm token binding and lifetime; start order of checks; runtime re-verification; cap = min(config, approval); endpoint chosen only by the paper flag; live cap in sizing; deadman/backup never touch the broker; reset refuses on open breaker/drift.

## P2-5 — exception paths in advance/submit (orchestrator: P2-5-1 confirmed at runtime.py:3008-3015 (log-only `except Exception`); P2-5-4 confirmed at runtime_service.py:1794-1798 (unwrapped `asset_trading_rules` on the decision path, while `_trading_rules_for` catches on the deferred path))

- **P2-5-1** (medium, high) runtime.py:2985-3015; runtime_service.py:2389-2423, 2903-2918, 3311 — advance failures on the trade-update/startup/reset paths are swallowed with a log line; if no further trade update arrives, the next session closes the rebalance as superseded and the BUYs are silently discarded. Fix: count failures per rebalance and alert/latch.
- **P2-5-2** (medium, high) runtime_service.py:1855-1857, 2425-2465, 3594-3601 — SELLs never sent because an earlier SELL in the batch raised are marked canceled; no path resends or alerts; the blocked-close warning only mentions BUYs. Exit delayed up to a session.
- **P2-5-3** (medium, high) runtime_service.py:3591-3601; alpaca_orders.py:57, 91; alpaca.py:217-221; runtime.py:4331-4346 — pre-send broker reads (`_has_open_order` GET) inside submit_portfolio_order escape as raw errors and are booked as post-POST ambiguity (reconciliation_required), blocking the rebalance or hard-halting a decision batch although nothing was sent.
- **P2-5-4** (medium; high mechanism) runtime_service.py:1794-1798 vs 2796-2802, 3033-3051; alpaca.py:221 — a held symbol the broker calls untradable makes every decision raise ValueError → `portfolio_runtime_failure` halt, blocking exits of all symbols; W05 fixed only the deferred path.
- **P2-5-5** (low, high) runtime.py:4321-4349; runtime_service.py:1417-1420, 2246-2290 — deferral invariant "no decision row ⇒ nothing sent" is false (advance can send residuals first); requeue only accidentally safe.
- **P2-5-6** (low, high) runtime_service.py:2627-2632, 2903-2918, 3578, 3607 — post-POST status writes unguarded; a pre-POST raise yields a critical "not re-sent" alert for an order that is re-sent as -replan1.
- **P2-5-7** (low, medium) runtime.py:1057-1087 — a partially sent exit-retry batch skips the snapshot invalidation.
- **P2-5-8** (low, medium) runtime_service.py:2425-2456, 2706-2722; runtime.py:6104-6105, 6184-6201 — a BLOCKED rebalance with no order rows can never reconcile; reset cannot clear its halt; every restart re-halts.
- **P2-5-9** (low, medium) runtime.py:3015-3024, 6104-6105; runtime_service.py:2014-2016, 2425-2433 — after reset clears the rebalance halt, a late trade event re-raises it because the phase is still BLOCKED and the only closer is the halted decision path.

Checked fine: BaseException restore before `_decision_recorded`; `_submit_orders` idempotency and shape check; resolver never guesses; residual id grammar and row columns; residual/replan drops alerted; deferred-buy malformed rows → BLOCKED; sizing raises before any write; clock gate fails open by design; operator alert key recorded after emit; retry one-shot; startup catch-up failure handling; AmbiguousOrderState never deferred.

## C2-6 — web engine manager state and settings write window (orchestrator: C2-6-1 handler shape confirmed — auto_start_engine catches only TransientStartupError around the retried start_engine; the wedge relies on the auditor's reading of the halt refusal at 788-791 and stop_engine at 1112-1120)

- **C2-6-1** (medium, high) web/managers/engine.py:125, 556-561, 788-791, 813-814, 1112-1120, 1216-1239; web/lifespan.py:72-81 — an auto-start retry refused with a non-transient ValueError (e.g. operator halt set during the backoff sleep, or a stop in the dispatch gap) leaves engine_state wedged at "starting"/"stopping"; manual start, stop, settings save and mode toggle are all refused and the watchdog keeps pinging. Only a process restart recovers.
- **C2-6-2** (low, medium) engine.py:840-857, 928, 962, 1002-1019 — a generic startup failure overlapping a stop overwrites "stopping" with "error" (uses a stale local flag; the transient handler re-reads live state).
- **C2-6-3** (low, medium) engine.py:828, 1107-1113; web/services/settings.py:400-444 — a Stop issued while the state reads "stopped" during a settings-save restart is recorded only in `_start_cancel`, which the restart's start_engine clears; the engine starts anyway.
- **C2-6-4** (low, low) engine.py:532-544, 1121-1137 — the status probe writes runtime health over manager-owned "stopping"/"stop_failed", erasing the restart-required marker.

Checked fine: every engine_state write under `_state_lock`; consistent lock order (ops → refresh → account → state); publish-vs-stop atomic; transient and StartupCancelled handlers re-read state; concurrent starts refused; reset/epoch refused with no runtime; retry cancellation via call_soon_threadsafe; settings window refuses starts; write_mode_if_idle atomic.

## R2-6 — risk-epoch reset persistence and restart (orchestrator: R2-6-1 confirmed — risk_peak.py:271-275 logs "account mismatch ... ignoring sidecar" and continues instead of failing closed, contrary to docs/execution_architecture.md:33)

- **R2-6-1** (medium; high mechanism, medium likelihood) risk_peak.py:90-91, 240-244, 271-275, 301-309, 355-393; alpaca.py:71-72; docs/execution_architecture.md:33 — on restart with a different account id, the sidecar is silently ignored, the drawdown baseline re-bases to the new account's equity and the old account's sidecar is overwritten. The bootstrap incident text claims no sidecar existed. Fix: fail closed on mismatch; allow only via explicit operator action.
- **R2-6-2** (low, high) runtime.py:5874-5914; risk_peak.py:342-347; engine.py:1271-1272 — epoch changes are not auditable: no epoch fields in `risk_epoch_reset` or the API response; rollback, no-op and crash paths emit nothing.
- **R2-6-3** (low, medium) risk_peak.py:225-229, 427-430; runtime_repository.py:1626-1665; runtime.py:5874-5880 — two independent epoch counters (sidecar vs portfolio_high_water); after a crash between commits one epoch number can cover two baselines. Fix: one counter.
- **R2-6-4** (low, medium) risk_peak.py:318-341 — for a legacy (unbound) sidecar the epoch follows the higher peak, not the higher epoch; a regressed epoch tags the run's peaks and the next restart discards in-run growth.

Checked fine: offline reset refused (no CLI path writes the sidecar); higher epoch wins on DB-only/sidecar-only restore; query_persisted_peak fails closed; max-epoch risk_state query; incident clear scoped; drawdown halt not persistent and self-heals; PAPER/LIVE sidecars, ledgers and state dirs separate; account bound before reconcile; rollback restores sidecar and memory; update_high_water never lowers.

## Q2-6 — fixture realism (orchestrator: Q2-6-1 confirmed — `_PRE_VT35_LIMITS` pinned in test_audit_fix_W05.py; Q2-6-2 confirmed — fake_client.py returns None to "simulate 404" while the SDK raises APIError(404))

- **Q2-6-1** (medium, high) tests/test_audit_fix_W05.py:33-92; test_two_phase_rebalance.py:122-151; test_restart_chaos.py:57-196; test_rebalance_block_recovery.py:37-296; execution/portfolio.py:636-661 — fix-wave two-phase/restart/block-recovery tests use pre-VT35 limits (10% per asset, 50% allocation), so every sell is an overlay trim; the shipped single-QLD strategy exit/re-entry shape is never driven through the two-phase sequence.
- **Q2-6-2** (medium latent, high) tests/fake_client.py:267-285; alpaca.py:735-759; alpaca common/rest.py:207 — the default fake returns None for an unknown id while the SDK raises APIError(404); a refused submit goes down the ambiguous branch in tests but the definite-rejection branch in production.
- **Q2-6-3** (medium, high) tests/fake_client.py:94-103, 298-302; tests/execution/test_alpaca_characterization.py:365-381 — scripted submits never bump `submitted_market_orders`, so the "not duplicated" assertion cannot detect a duplicate submit.
- **Q2-6-4** (low, high) fake_client.py:255-293, 368-380; alpaca.py:566-567 — the default fake cannot represent held shares (qty_available = qty), filled orders by id, or order timestamps.
- **Q2-6-5** (low, high) tests/test_audit_fix_W16.py:36-42 and accounting/telemetry/product-backtest fixtures — effective-dated fees disabled and zero fees, while the shipped config enables them.
- **Q2-6-6** (low, high) fake_client.py FILL-activities branch; tests/execution/test_alpaca_chaos.py:10-36 — fixture models an endpoint production never calls; "no duplicate fills" test asserts nothing about fills.

Checked fine: VT35 golden uses real bars and the shipped config; SDK-shaped bar frames with 00:00 ET labels; enum vs string normalisation; nanosecond timestamps; non-fractionable assets; unknown status raw-row fallback; non-ACTIVE refusal; execution policy tested against shipped config; 10.0 notional floor in retry fixtures; position parsing guards.

## D2-5 — runbook procedures executable as written (orchestrator VM check, read-only: `git status --porcelain` on the VM shows config/settings.yaml clean (D2-5-3 not live today) and `?? .env.backup` — the backup env file is untracked and not gitignored, which makes S2-4-3's `.env.*` gitignore gap concrete on the VM. .env and .env.backup are owned by the service user, mode 600 (D2-5-4 not live))

- **D2-5-1** (medium, high) deployment.md:207-212; monitoring/deadman.py:51, 128-186 — the doc says any non-200 readiness pings /fail; the code pings success for a stopped or warming-up engine with no reasons, so a stopped engine never reaches the external alarm.
- **D2-5-2** (medium, high) deployment.md:308-341, 436, 518-519; operator_runbook.md:606-612; cli.py:402-405, 779-791; live_safety.py:63-71; config.py:593-618 — after the systemd-creds migration, the documented manual doctor, authorize-live and test-email cannot see the keys (they read only $CREDENTIALS_DIRECTORY or env) and fail.
- **D2-5-3** (medium mechanism; not live) web/services/settings.py:157-160; deployment.md:431, 555-556 — dashboard settings saves rewrite the tracked config/settings.yaml (comments dropped); `merge --ff-only` then refuses and `reset --hard` rollback silently discards saved settings. No git-status gate.
- **D2-5-4** (medium, medium; not live) deployment.md:279-280 vs 335-336; config.py:583-587 — key-rotation step uses `sudo install -m 0600` with no owner, making .env root-owned and unreadable by the service.
- **D2-5-5** (low, high) deployment.md:444-449, 555-556 — rollback reinstalls with `--no-deps` only, which the update section itself says leaves new pins in place.
- **D2-5-6** (low, high) operations.md:56; status.py:131; health.py:114 — `/api/status | grep engine_state` prints nothing (key is `state`; engine_state is on /readyz).
- **D2-5-7** (low, high) operations.md:617-621; scripts/audit_paper_soak.py:523-543 — `--report/--fixture` belong to reconcile_paper_incident.py, not audit_paper_soak.
- **D2-5-8** (low, high) operations.md:697-700; ci.yml:48 — "run it the way CI does" omits the requirements-dev.lock install; pytest missing.
- **D2-5-9** (low, medium) deployment.md:355-363 — historical watchdog-removal block copies the template unit over the hand-edited one; re-apply is only a comment.

Checked fine: CLI commands/flags; exit codes; output strings; config keys; env names; routes; file names; scripts; DB tables; alert kinds; intent codes; systemd unit values; restore and update sequence order (sidecar gap is D2-1-8); tax README.

## F2-7 — reports and exports (orchestrator: F2-7-1 confirmed at dashboard.py:928-933 (total = today's realized + open unrealized). F2-7-2 overlaps D2-4-2 (same zero-placeholder root), F2-7-6 overlaps F2-3-8, F2-7-8 duplicates F2-3-9 — count once. F2-7-3/4 affect only scripts/execution_quality_report.py)

- **F2-7-1** (medium, high) web/services/dashboard.py:928-933; index.html:69-71; dashboard.js:77-79; alpaca_account.py:645-655 — "Toplam kâr/zarar" KPI = today's realized + open unrealized; prior realized P&L is dropped and a deposit day shows as a gain.
- **F2-7-2** (medium; overlaps D2-4-2) dashboard.py:36-54, 943-969, 1034-1048; dashboard.js:399-438 — with no account snapshot the risk panel prints zeros and a red cash-buffer meter.
- **F2-7-3** (medium, medium; report-only) telemetry.py:373-376, 456-461, 777-787; scripts/execution_quality_report.py:154-155 — execution "slippage" is measured against the decision close with no basis label, so the overnight gap is reported as slippage.
- **F2-7-4** (medium, high; report-only) telemetry.py:613-670, 747-763; runtime.py:5394-5398 — forward_metrics mixes periods (day-start equity + session realized + all-time unrealized − all-time fees).
- **F2-7-5** (low, medium) monitoring/receipt.py:54-60; runtime.py:4468-4469 — receipt conflict check compares six identity keys only; a different-content rewrite is dropped while "written" is logged.
- **F2-7-6** (low; overlaps F2-3-8) dashboard.py:673-674; runtime_repository.py:1540, 1557 — unknown fee stored and shown as 0.00.
- **F2-7-7** (low, high) tax/export.py:95-125, 199, 295-300 — export JSON has no income-import status; a year exported before import shows zero dividends and no declaration as a verified result.
- **F2-7-8** (low; duplicate of F2-3-9) runtime.py:4544-4549; daily_summary.py:102-122 — tax segment prints zeros for account "unknown".
- **F2-7-9** (low, medium) tax/export.py:208-251; backtest/portfolio_report.py:81-122 — CSV exports do not neutralise cells starting with = + - @ (broker-supplied activity_id, symbol).
- **F2-7-10** (low, medium) monitoring/alerts.py:136-146; daily_summary.py:240-264; ops.py:99-136 — alert journal rows carry mode=None and usually account_id=None; PAPER and LIVE events cannot be separated.
- **F2-7-11** (low, medium) cli.py:1222-1237 — ops status header shows the configured mode as fact when the snapshot is missing.
- **F2-7-12** (low, medium) monitoring/receipt.py:18-20; cli.py:598-599 — receipt dir is cwd-relative, not derived from the configured runtime dir.
- **F2-7-13** (low, medium) backtest/portfolio_report.py:31-35 — benchmark first-year return anchors on the first bar; invalid anchor fabricates 0.0.

Checked fine: daily-summary NY session label and n/a for unknowns; per-side order segment; receipt validation and atomic write; account-scoped recent fills; daily-loss meter baseline; tax cache and Turkey-time year; performance points filtered and cache keyed by account; ops incidents scoped; telemetry read-only opens and fee dedup; tax CSV None → empty cell.

## T2-7 — data provider failure handling (orchestrator: T2-7-1 confirmed — runtime.py:2985-2987 evaluates the basis latch on every advance; the inline comment says verification "may hit the provider". T2-7-2 confirmed — alpaca.py:544 reads the feed label once and :572 reads `self._stock_feed` again for the request)

- **T2-7-1** (medium; high mechanism) runtime.py:2986, 3271, 4774-4797, 4944-4965 — the equity basis latch can call the provider synchronously on the trade-update path after every terminal SELL fill, even with no BUY pending; failures are not cached, so an outage delays serial fill processing by up to ~60 s per call.
- **T2-7-2** (medium, medium) data/alpaca.py:544-572, 585-597, 711-718 — feed label read before the request, feed object re-read for the request; a concurrent SIP→IEX downgrade stores IEX rows under 'sip' (replace_window deletes real SIP rows), later served as feed-matched cache.
- **T2-7-3** (low, medium) runtime.py:3698-3716, 5050-5051; signal_cross_check.py:109-110, 334-379 — on a session-close SIP fallback the cross-check compares SIP with SIP and still records `primary_source: stream_iex_1m`.
- **T2-7-4** (low, high) data/alpaca.py:141; alpaca common/rest.py:85-135, 368-408; runtime.py:4944-4955 — no total deadline, no failure cool-down, no page cap; 429 retried immediately.
- **T2-7-5** (low, high) data/alpaca.py:501-506, 585-597 — any 401 on SIP is classified as an entitlement error, so bad credentials trigger the 6 h IEX downgrade with the wrong warning.
- **T2-7-6** (low, medium; design question) runtime.py:2986, 4959-4965, 6244-6276; signal_cross_check.py:369-379 — degraded warmup history is seeded unchecked and only entries are latched, so a SELL can be decided on cache/IEX history.
- **T2-7-7** (low, medium) runtime.py:4736-4753, 6279-6290; deadman.py:51, 130-141 — outage alert fires once at onset and never escalates; warmup failures log-only.

Checked fine: cross-check uses explicit `feed="sip"` with bounded retries; verifier refuses degraded answers; verifier exception latches entries; empty responses degraded and not cached; entitlement regex; session-close SIP fallback armed after embargo; adoption filters partial bars; verifier fetch outside `_portfolio_lock`; premarket clock fails closed; local NYSE calendar; APIError transient classification.

## S2-6 — input validation at trust boundaries (orchestrator: nothing above low; no SQL injection, eval/exec/pickle, or request path traversal found)

- **S2-6-1** (low, high) config.py:113-131, 381-382, 819-822; runtime.py:670-671, 1442-1443; engine.py:1076-1077, 1600 — `LiveConfig.enabled` (and other `enabled` fields) has no bool type check; a quoted "false" reads as enabled. Other gates still apply.
- **S2-6-2** (low, high) config.py:1081-1112, 1214-1234 — misspelled top-level YAML blocks (e.g. `alertng:`) are ignored silently and defaults apply.
- **S2-6-3** (low, medium) web/managers/engine.py:1502-1513; routers/safety.py:152-162 — a lone-surrogate arm phrase raises before the wrong-phrase invalidation runs.
- **S2-6-4** (low, high) web/managers/backtest.py:86-104, 249-255 — a rejected backtest request overwrites the previous run's published status before validation.
- **S2-6-5** (low, high) web/managers/backtest.py:165-171; cli.py:2054, 2064 — JSON float notation like `5e-05` is read by yaml.safe_load as a string; the run fails in the background thread.
- **S2-6-6** (low, medium) monitoring/forensics.py:325-341; routers/ops.py:81-83; services/ops.py:169-172 — unvalidated decision_id hits a V1 substring fallback that can attach an unrelated legacy receipt (up to 2000 json loads per miss).
- **S2-6-7** (low, high) cli.py:163, 1054-1071, 1395; journal.py:397-400 — CLI `--limit` unbounded; huge relative `--since` raises OverflowError.

Checked fine: all request-reachable SQL parameterised; no eval/exec/pickle/shell; yaml.safe_load everywhere; backtest payload extra=forbid with name regex and size caps; settings schema bounds; mode/safety/ws-ticket schemas; ops event filters bound; trade-update parse wrapped and non-finite rejected; Bar invariants; backup label and receipt filename restrictions; dashboard JS uses textContent.

## P2-6 — startup catch-up vs scheduled decision vs superseded rebalance (orchestrator: P2-6-1 confirmed — runtime_service.py:1163-1173 calls process_closed_bars with no `signal_cross_check`; P2-6-3 confirmed — `prior_decision` at 1076-1078 is digest-scoped)

- **P2-6-1** (medium, high) runtime_service.py:1163-1173, 1674-1679; runtime.py:4067-4079, 4160-4170, 4612-4614 — startup catch-up decides a recovered session without the SIP cross-check; the scheduled path then sees duplicate_decision, so the check never runs for that session and a primary/secondary mismatch can trade.
- **P2-6-2** (medium, high) runtime_service.py:119-135, 920-921, 1061-1075; timeframes.py:65-79; runtime.py:3472-3559, 5219-5235, 6264-6307 — a restart inside the SIP embargo (close → close+15 min) loses that session's decision: seed drops the partial bar, catch-up sees the prior session decided, and nothing re-queues the session (only the watchdog notices).
- **P2-6-3** (medium, conditional) runtime_service.py:742-751, 982-987, 1076-1119; runtime_repository.py:653-664 — after a configuration-digest change, `prior_decision` is None, so a due session with a held position is refused as `already_invested`; the session's trend exit is skipped, contrary to the docstring.

Checked fine: stale-rebalance check before advance in both paths; superseded SELLs gate the new decision; blocked superseded rebalance closes only when terminal; DB-backed duplicate check across restarts and label variants; mid-session restart resumes BUY side; weekend/holiday restarts; early-close finality; entry latch evaluated before resume; reset path consistent.

## R2-7 — fresh-host / DR bootstrap (orchestrator: R2-7-1 confirmed — alpaca_reconciliation.py:566-573 calls `portfolio_flat_reconciled` for every owned symbol absent at the broker without a local SELL row, and runtime_repository.py:1189-1196 writes `stale_ownership_clear` with a note claiming an activity-history check that no code performs)

- **R2-7-1** (medium; high mechanism, medium reachability) alpaca_reconciliation.py:566-573; alpaca.py:405-440; runtime_repository.py:1172-1203; alpaca_account.py:457; runtime.py:519-522, 1457 — a restored ledger older than the broker (exit SELL filled after the backup) makes the startup reconcile durably zero owned QLD with no sale booked; lots, proceeds and realized P&L for that exit vanish and the adjustment note is false. INCOMPLETE of G-1/D8-1 for the DR case. Fix: halt for an explicit operator rebase instead of auto-clearing when no local order explains the change.
- **R2-7-2** (low, high) operations.md:493, 520-541; execution_architecture.md:33, 63; risk_peak.py:303-305, 385-393; alpaca_account.py:626-628 — docs say a missing peak bootstraps at 0 (code uses first observed equity); `peak_bootstrap` alert undocumented; restore steps omit the sidecar copies (related to D2-1-8).
- **R2-7-3** (low, high) operations.md:31-33; live_readiness_v1.md:35, 135; runtime_service.py:365-391, 1100-1135, 3109-3131 — a first-start or missed-close catch-up during regular hours sends DAY market orders that fill intraday; only the live-readiness doc says so.

Checked fine: schema creation on a fresh DB; salt minted on a fresh ledger (no collision with the old host); pre-existing broker QLD → BROKER_STALE halt and rebase works on an empty ledger; old-host open orders → UNKNOWN_ORDER halt, no auto-cancel; peak falls back to DB high-water; backup timer on a fresh host; reconcile before streams; trading-host gate documented.

## C2-7 — resource leaks over a long-running process (orchestrator: no medium or higher; memory, threads, connections and disk growth are bounded for the VT35 PAPER profile)

- **C2-7-1** (low, medium) monitoring/journal.py:245-247, 365-366, 548-562 — `configure_global_journal` replaces a started default journal without stopping it; the orphaned writer polls for the process lifetime and, with a custom journal_path, its queued events go to the default file.
- **C2-7-2** (low, low) web/services/performance.py:23, 60-62, 121-153 — a new StockHistoricalDataClient (requests.Session) per cache miss, never closed (≤ ~1,440/day; GC-dependent).

Growth estimate: memory flat (only `_premarket_probed_sessions` grows, ~250/yr); bounded throttle/queue maps; per-call SQLite connections closed; journal capped 50k rows/30 d; receipts ~365 small files/yr, pruned by backup CLI; warmup cache negligible.

Checked fine: all SQLite connections closed in finally/closing; raw fds closed; runtime threads joined with timeouts and respawns guarded; snapshot writer per runtime; email sink reconfigure closes; broker/stream lifecycle and probe broker closes; asyncio tasks referenced; module caches bounded; no unbounded queues.

## Q2-7 — LIVE path coverage (orchestrator: Q2-7-1 confirmed — no test references `authorized_live_cap` or "configuration changed during startup"; runtime.py:1557-1563 is unpinned. Severity kept high because the line is the only clamp of LIVE exposure to the approved amount)

- **Q2-7-1** (high, high) runtime.py:1552-1563; live_safety.py ~318-340; tests/test_safety_reliability_fixes.py:162-205; tests/test_live_credentials_and_standing.py:409-440 — `EngineRuntime.start()` never runs past the proof gate in LIVE in any test; the approval-cap clamp (`min(config cap, approval cap)`) and the start-time digest-drift refusal could be deleted with the suite green.
- **Q2-7-2** (medium, high) web/managers/engine.py:1456-1500; routers/safety.py:139-148 — `request_live_arming` (paper-account refusal, env/creds checks, token issue) has no behavioural test.
- **Q2-7-3** (medium, high) routers/safety.py:107-111; tests/test_web_round7_fixes.py:134-150 — the /api/mode arm guard for paper:false is stubbed out in the only test that posts it.
- **Q2-7-4** (medium, high) engine.py:893-908; tests/test_live_credentials_and_standing.py:442-449 — manual LIVE start success path untested (proof from token, single-use arm).
- **Q2-7-5** (medium, high) engine.py:1502-1516 — confirm_live_arming expiry and config-digest clauses untested.
- **Q2-7-6** (medium, high) tax/cli.py:129-195 — `tax --live` import/export wiring never driven; only the pure resolver is tested.
- **Q2-7-7** (low, high) alpaca_client.py:89; alpaca_updates.py:75; risk_peak.py:18-24; alpaca.py:70-72 — endpoint and peak-sidecar selection never asserted for paper=False.
- **Q2-7-8** (low, high) engine.py:479-531 — only the no-standing reason of the LIVE auto-start gate is tested.
- **Q2-7-9** (low, high) runtime.py:665-673, 1443-1445 — EngineRuntime LIVE cap refusals untested at engine level (config rejects earlier).

Checked fine: proof gate order tests; standing re-verification, binding and voiding; approval binding and signature; arm token credential binding and phrase mismatch; LIVE→PAPER heal; LIVE sizing caps; config remaps; authorize-live CLI; account-environment classification.

## D2-6 — consumers of exit codes and --json output (orchestrator: D2-6-1 confirmed — logger.py:23 `StreamHandler(sys.stdout)`; D2-6-3 confirmed — health.py checks staleness (line ~132) before the stopped/stop_failed branch (~142))

- **D2-6-1** (medium; high mechanism) logger.py:23; cli.py:39, 1207-1209, 1380-1398, 1643-1644; journal.py:478; health.py:442; metrics.py:265; forensics.py:89-228 — parayabuck WARNING logs go to stdout, so `--json` output is unparseable on exactly the paths that warn (unreadable DB, journal query failure).
- **D2-6-2** (medium, high) journal.py:477-480; cli.py:1397-1410; diagnostic.py:153 — a failed journal read returns [] and `ops events --json` exits 0; the documented `ops events --kind backup_failed` check reads a false all-clear; bundle recent_events has no failure marker.
- **D2-6-3** (medium, high) health.py:132-142; diagnostic.py:336-338; snapshot.py:185-191; runtime.py:1949; cli.py:1296-1306 — an intentionally stopped engine reports CRITICAL (exit 2) once its last snapshot is older than the tolerance; planned maintenance stops page a monitor keyed on 2.
- **D2-6-4** (medium, high) cli.py:282, 531, 556, 1102-1103, 1302-1306, 1341-1343, 1353-1354, 1768-1803; operations.md:353-354 — exit 2 means CRITICAL health, argparse usage error, missing backup file and internal ops-doctor failure.
- **D2-6-5** (low, high) cli.py:1081, 1296-1306, 2052-2066; tax/cli.py:73-77, 122, 141 — exit 1 shared by degraded health, bad time/param arguments and uncaught tax tracebacks.
- **D2-6-6** (low, medium) cli.py:1509-1515, 1566-1645 — `ops explain` exits 0 for an unknown decision id; the empty JSON shape drops the normal keys.
- **D2-6-7** (low, high) cli.py:1195-1198, 1352-1358; health.py:286-293, 480, 519; diagnostic.py:306, 336-338; operator_runbook.md:18 — ops status and ops doctor classify the same fault differently (corrupt ledger 1 vs 2; tolerance configured vs literal 90 s); broken settings silently fall back to defaults; UNKNOWN undocumented.

Checked fine: ExecStartPre doctor only blocks on blocker/strict FAIL rows; deadman exit codes; backup exit codes match docs; halt/resume; `_health_exit_code` pinned and StrEnum safe; --json failure messages on stderr; argparse errors before stdout; CI does not consume parayabuck exit codes; repair script fails closed; rebase-ownership codes consistent.

## S2-7 — command injection and subprocess use (orchestrator: no shell=True / os.system / shell expansion anywhere; all argv lists. Auditor corrected the brief: the web backtest runs product_cli.main in-process, not as a subprocess. S2-7-1 is latent on the VM (no off-host command configured) and extends R2-4-3)

- **S2-7-1** (medium, medium; latent) backup.py:37, 327-331, 368-369; parayabuck-backup.service:6, 16 — the 900 s off-host copy budget (sequential per artifact) cannot be honoured under the unit's default 90 s start timeout; systemd SIGTERMs mid-upload with no backup_failed alert.
- **S2-7-2** (low, high; container-only) web/managers/engine.py:867-869, 1922-1934; Dockerfile — the in-process tracked-secret start gate fails open when git is unavailable (slim image has no git, .git excluded).
- **S2-7-3** (low, medium) scripts/repair_local_state.py:95-125, 195-200 — the live-engine guard treats a missing `systemctl` on PATH as "not running" and trusts a "stopped" snapshot.
- **S2-7-4** (low, high) backup.py:363-369 — the off-host child inherits the whole backup environment (SMTP password included).
- **S2-7-5** (low, medium) backup.py:368-376; cli.py:559-575 — a non-UTF-8 byte from the copy tool raises UnicodeDecodeError, aborting remaining sidecar copies and alerting "backup could not be written" for a verified local set.
- **S2-7-6** (low, medium) cli.py:892-895; diagnostic.py:55, 60 — git calls run with no cwd; from a subdirectory the tracked-.env check inspects only that subtree.
- **S2-7-7** (low, medium) live_safety.py:63-72; cli.py:397-402, 779-782, 1007, 1923-1925 vs engine.py:302-322 — CLI resolves credentials env-first, engine file-first; a stale exported key gives contradictory doctor/authorization results (fails closed).

Checked fine: list argv everywhere; ExecStart lines invoke binaries directly; off-host quote/split round trip; no Alpaca keys reach the off-host child; subprocess timeouts; git children run no external programs; systemctl output fails closed; webbrowser argv; SMTP starttls + cert verification; web backtest argv validation; unit hardening.

## F2-8 — corporate actions in the books (orchestrator: F2-8-2 downgraded to medium — the auditor rated it high but with low confidence, since it depends on how Alpaca sets avg_entry_price after cash-in-lieu; the 2% corroboration band at ownership_rebase.py:377-378 is confirmed. QLD forward splits are handled correctly)

- **F2-8-2** (medium; auditor said high, low confidence) ownership_rebase.py:219-234, 357-380, 420-461; runtime_repository.py:1246-1262, 1984-2015; tests/test_audit_fix_W11.py:235-247 — a reverse split with a cashed-out fraction can fail the 2% cost corroboration (when the broker removes the cashed cost) and be auto-booked as FOREIGN_SELL, leaving survivors at pre-split unit cost (~10× overstated later P&L for small post-split counts).
- **F2-8-1** (medium, medium) tax/ledger.py:127-156; alpaca_account.py:527-540; alpaca_reconciliation.py:438-477, 527-531 — a cost-only corporate action (spin-off, return of capital) writes no ownership row; tax guard never fires and lots keep the old cost; cost_basis_drift is read-only.
- **F2-8-3** (low, high) cli.py:1769-1840; runtime_repository.py:2192-2210 — `ops rebase-ownership` can book a forward split as an acquisition; the CLI never shows the broker average cost.
- **F2-8-4** (low, medium) runtime_repository.py:2215-2220; ownership_rebase.py:412-413; alpaca_reconciliation.py:527-531 — a ticker rename has no booking path; the only route writes off the lots with no P&L or transfer.
- **F2-8-5** (low, medium) backtest/product.py:232-233; alpaca_account.py:527-540; alpaca_reconciliation.py:545-548 — a stock dividend halts live (fail-closed) but is ignored by the backtest.

Checked fine: forward split classification and boundary search; SPLIT_ADJUST keeps total cost; stale-clear consumption never touches split rows; unconfirmed surplus halts; planner sells capped at managed qty; split-day signal basis gated by the watermark latch; ex-date sizing price consistent; cash dividends credited to cash (performance); rebase CLI dry-run and BEGIN IMMEDIATE checks; corroborated reverse split handled as SPLIT; adjustment-row signs consistent.

## Q2-8 — property-test opportunities (orchestrator: hypothesis is not in any lock or extras and no @given exists; only seeded sweeps. Q2-8-2 conflicts with F2-6's "replan ids form their own leg (cosmetic)" — whether a replan and a residual can both be sent for one leg in the same decision is untraced; treat as unverified)

- **Q2-8-1** (medium, medium) runtime_repository.py:83-104, ~1490-1530; tests/test_audit2_20261011_w3.py:424-470 — apply_trade_update is not order-independent for lot prices: (12@101 then stale 10@102) books one 12@101 lot, the reverse order books 10@102 + 2@96; later FIFO cost and the tax ledger differ. Permutation property proposed.
- **Q2-8-2** (medium, low; unverified) runtime_service.py:298-306, 2160-2177, 3019; tests/test_replan_sell_2026_10_27.py:138-142 — `_sell_leg_root` does not strip `-replanN`, so a replanned SELL is its own leg and the original leg's residual cannot see its fills; potential oversell if both run. State-machine property proposed.
- **Q2-8-3** (low, low) tax/ledger.py:207-218 vs runtime FIFO replay ordering — different same-instant tie-breaks between tax FIFO and the runtime lot book; parity property missing (relates F2-1-4).
- **Q2-8-4** (low, medium) execution/portfolio.py:572-574, 786-792, 949-967 — planner sizes at `prices[symbol]` while the overlay values at `position.current_price`; post-fill weight can exceed the allocation cap by the price gap.
- **Q2-8-5** (low, medium) execution/portfolio.py:1145-1158, 1224-1270; tests/test_audit_fix_W01.py:57-64 — grid tests never feed floor_increment output to the validator; negative input rounds toward zero unguarded (no caller today).
- **Q2-8-6** (coverage) — the owned == Σ open FIFO lots invariant has no test (relates T2-4-2, T2-4-3, F2-4-1, F2-4-5).

Checked fine: id round-trip parsing for base/residual/retry; identical replay books nothing; monotonic cumulative and non-regressing status; floor_increment never rounds up for positive input; exact Decimal grid check; in-epoch peak max() under lock; tax slice conservation; planner cap composition.

## S2-8 — operator approval key handling (orchestrator: nothing medium or higher; HMAC-SHA256 over canonical sorted JSON, constant-time compare; a read-only copy of the approval file cannot be forged or extended unless the key is weak)

- **S2-8-1** (low, high) config.py:117; live_authorization.py:69; runtime.py:727; cli.py:403; web/managers/engine.py:1082, 1639-1640 — `operator_approval_key_env` is an unvalidated name, so any env var (e.g. PATH) can become the HMAC key; the checklist and start path resolve an empty value differently.
- **S2-8-2** (low, high) live_safety.py:26-33 — key strength is only length ≥ 32 plus a placeholder prefix; "k"*32 passes.
- **S2-8-3** (low, high) live_authorization.py:69; runtime.py:703-745, 1150-1199, 727; engine.py:300-345, 1082, 1640; cli.py:399 — approval-key rotation takes effect only after a restart; a running LIVE engine is never re-verified after rotation or standing expiry.
- **S2-8-4** (low, high) cli.py:369-380; live_safety.py:457-485 — `--revoke-standing` is a bare unlink with no revocation record; a restored copy is valid again until expiry.
- **S2-8-5** (low, high) live_safety.py:310, 460; runtime.py:727-733 vs engine.py:1641-1668 — the start path reads approval/standing files without the regular-file check or size cap the display path has (FIFO blocks start).
- **S2-8-6** (low, high) cli.py:406-411, 445-446, 493 — authorize-live with the .env.example placeholder key fails with a traceback after the LIVE probe (fail closed, but the comment promises a clean exit before the probe).
- **S2-8-7** (low, medium) trading_host.py:47-54 — host identity silently falls back to hostname alone when no machine-id is readable.
- **S2-8-8** (low, high; latent) engine.py:337-345; operational_event.py:70-120 — the approval key value is not in the value-redaction set.

Checked fine: injective canonical HMAC encoding; approval vs standing not cross-verifiable; constant-time compares; expiry and 30-day cap signed; key used only for LIVE; binding checks; no key/signature in logs, journal, bundles or API; files 0600; rotation fails closed at next start.

## T2-8 — split handling, data and signal side (orchestrator: T2-8-3 verified. runtime_service.py:1674-1678 holds the whole book on purpose, while the docstring at runtime.py:5168-5177 and the operator alert at 5189-5196 still say "risk-reducing exits keep flowing". The code is intentional and the text is stale, so this is the operator-misleading kind and is kept at medium. T2-8-1 depends on unverifiable provider rebase timing, so it is downgraded to medium.)

- **T2-8-1** (medium after orchestrator review; auditor high/low) runtime.py:4964-5009; data/adjustment.py:70-169; signal_cross_check.py:97-99, 161-166. Basis adoption has no split-continuity check. If the provider serves a post-split partial D bar before it rebases the prior sessions, verification passes and the post-split stream close at D meets pre-split SMA history, so a held position gets a spurious SELL. The cross-check cannot catch it because both sources share the history. KNOWN_SPLIT_RATIOS exists only on the books side (ownership_rebase.py).
- **T2-8-2** (medium, medium) runtime.py:4786-4804, 4977-4979, 5199-5211, 5242-5244; runtime_service.py:1237-1241. The decision-time basis check reuses any success up to about 65 minutes old. The watermark counts partial provider bars that adoption dropped, so a decision can run on a 15:31 history without the final SIP D.
- **T2-8-3** (medium, high; verified) runtime.py:4063-4066, 5170-5177, 5189-5196 vs runtime_service.py:1673-1725. The basis latch holds the whole book, SELLs included. The docstring, comment and operator alert say exits keep flowing, which misleads the operator during a latch window.
- **T2-8-4** (low, medium) data/alpaca.py:553-562, 698-718, 744-757, 809-830. A provider hole during a split rewrite turns the write into an upsert that keeps a pre-split cached row. Cache freshness checks only the tail, and nothing invalidates the cache on a basis change, so warmup can seed mixed-basis data (same root as T2-7-6).
- **T2-8-5** (low, high) adjustment.py:124-150; runtime_service.py:1213-1257. Live signal history is never trimmed, and stored bars older than the provider window are kept verbatim (pre-split). The decision is safe today because the reads are windowed to about 397 bars; a future full-history read would mix bases.

Checked fine: NY-session matching in the merge; partial/embargo finality consistent (close+15m); trend and R2 windows bounded; degraded cache refused by the verifier; dedup key includes required_through; product.py split/dividend math; backtest refuses SPLIT actions.

## R2-8 — configuration change across a restart (orchestrator: checked R2-8-1 in the code. The manual approval check at live_safety.py:333 compares only the digests, while standing auth also compares config_fingerprint at line 505, so the two LIVE paths disagree. Checked R2-8-3: the incident is cleared against the current threshold at runtime.py:2484-2491.)

- **R2-8-1** (medium LIVE, high; verified) live_safety.py:333 vs 503-505; signal_cross_check.py:339-341; runtime_service.py:3339. A manual LIVE approval covers only the strategy digests. Changing any of these and restarting keeps the approval valid:
  - signal_cross_check.enabled=false (the cross-check is skipped)
  - live.buy_gap_margin_pct
  - risk.adjust_for_cash_flows
  - runtime.stale_account_sell_only

  The same edit voids a standing authorization. Fix: bind config_fingerprint into the manual approval, and refuse LIVE when the cross-check is disabled.
- **R2-8-2** (medium LIVE / low PAPER, medium) runtime_service.py:2355-2368, 2704-2775; runtime.py:3035-3046; runtime_repository.py:980-984. An open rebalance decided under a superseded digest is resumed, and its BUY leg is re-planned under the new universe and limits. The new config's own decision is blocked that session by _session_decided. The stale check compares sessions only; the same fix point as P2-1-1 applies.
- **R2-8-3** (medium LIVE / low PAPER, medium; verified) runtime.py:2484-2491, 2921-2927. Raising risk.max_drawdown_pct before a restart silently clears a persisted portfolio_drawdown_halt without a risk-epoch reset, which contradicts the docstrings at execution/portfolio.py:557-558.
- **R2-8-4** (low, medium) runtime_service.py:1276-1282; execution/portfolio.py:498-510. The drawdown brake's RED state survives a disable/re-enable cycle and inherits the old episode clock, so the cooldown can be already satisfied on re-admission.
- **R2-8-5** (low, low) runtime_service.py:667-669, 928-937. After a digest change, the restored cadence watermark is None and is reseeded to the newest bar. A session missed at restart then depends only on startup catch-up.

Checked fine:
- R-5 holds for the same session.
- decision_id and UNIQUE include the digest.
- The digest is computed identically at all 4 sites.
- Warmup cache keys carry no strategy parameters.
- Halts are restored independently of config.
- A symbol removed from the universe stays pinned and is sold.
- expected_config_digest fails closed.

## C2-8 — runtime caches and staleness (orchestrator: checked C2-8-1 in the code. alpaca_client.py:271-276 caches the asset object without checking whether it is tradable. Most dashboard and status caches were reviewed and are fine.)

- **C2-8-1** (medium, medium; mechanism verified) execution/alpaca_client.py:271-276; execution/alpaca.py:219-221; runtime_service.py:2795-2802, 3042-3044, 3678-3680. A tradable=False asset read is cached for 24 h. A held symbol stays classed as untradable after the broker re-enables it. The owed exit SELL is held, the rebalance is closed UNRESOLVED, and the SELL retry is refused for up to 24 h. This compounds P2-5-4. Fix: use a short TTL for non-tradable entries.
- **C2-8-2** (low, latent: adjust_for_cash_flows is off by default) execution/alpaca_account.py:192-207, 616-651. With the flag on, a cached or failed same-day flow read (0.0) is folded into the monotone peak. The reported peak becomes E+2D, so a deposit can trip the drawdown halt. This is the same-day companion to T2-4-1.
- **C2-8-3** (low, display) web/services/dashboard.py:532-550. The capacity cache lasts 10 s, is keyed by id(runtime) (reusable after a restart), caches probe failures, and has no single-flight.
- **C2-8-4** (low, high) web/managers/engine.py:215, 1910-1937. The tracked-secret warning is a process-lifetime memo, and a git failure is cached as "no warning". The auto-start pre-check reads the memo; explicit start rescans.
- **C2-8-5** (low, medium) web/managers/engine.py:235-280; web/services/performance.py:112-121. The PAPER/LIVE chip and the performance cache key follow the settings file, not the running runtime's mode. A manual VM edit shows CANLI while the engine is PAPER.

Checked fine:
- status, dashboard config and payload caches (stamp-before-load, failures not cached)
- the tax YTD cache
- warmup cache keys and locking
- _snapshot_cache scope
- the 5 s account cache cleared on stop and reset
- the nyse lru_cache, which keeps only successful schedules

## F2-9 — CLI ops writers (orchestrator: checked F2-9-2 in the code. cli.py:1825-1826 compares the ledger account UUID with the pin, which the runbook documents as the PA number. Compare live_safety.py:130-141, which accepts either form.)

- **F2-9-1** (medium, high) cli.py:1787-1879; runtime_repository.py:2168-2257; ownership_rebase.py:424-433. `ops rebase-ownership --confirm` binds only the owned quantity. If the confirm run drops --broker-average-cost (the hint does not show it), the split rescale the dry run showed becomes a sale-like FIFO depletion: cost basis is lost and P&L is realized. Fix: bind a plan digest to --confirm.
- **F2-9-2** (medium, high; verified) cli.py:1825-1831 vs live_safety.py:130-145; docs/operator_runbook.md:614-626. With the documented PA-number pin set, every rebase run is refused (UUID ≠ PA). This blocks the split-recovery tool and invites removing the pin. With no pin, a PAPER config pointed at the LIVE ledger can rebase LIVE books.
- **F2-9-3** (low, low) cli.py:1747-1895. rebase-ownership has no engine-liveness or flock guard, only the open-order check, while the runbook has the engine up.
- **F2-9-4** (low, high) cli.py:1883-1891; runtime_repository.py:2261-2281. An operator rebase emits no journal event or alert, and the stored note omits the proof used (broker average cost, expected owned).
- **F2-9-5** (low, high) cli.py:300-315, 615-649. halt/resume need a loadable settings file, so a corrupt config means `halt` writes no marker. resume deletes the only record of the halt, with no journal event.
- **F2-9-6** (low, high) tax/cli.py:129-153. import-activities creates a missing ledger (mkdir + rwc connect), which can create an empty ledger that a later start treats as existing. rebase and export refuse or open read-only.
- **F2-9-7** (low, medium) cli.py:372-381. --revoke-standing is silent and does not say that a running LIVE engine keeps trading (overlaps S2-8-3/4 on the UX side).
- **F2-9-8** (low, medium) tax/rates.py:39-40, 307-308. rates-fetch silently overwrites stored USD/TRY rates, defaults to cwd-relative paths, and does not report which files it wrote.

Checked fine:
- rebase dry run is read-only and confirm runs in BEGIN IMMEDIATE
- the halt marker is atomic and fails closed
- backup labels
- authorize-live pre-broker checks
- tax import idempotent per mode
- a Yİ-ÜFE conflict aborts before any write
- repair_local_state refuses on a live engine

## D2-7 — deploy docs and unit templates vs the home-dir VM install (orchestrator: checked the template in the repo. It has Type=notify, WatchdogSec=300 and an inline `Environment=PARAYABUCK_TRADING_HOST=1`. OPERATIONS_LOG 2026-10-27 09:57 records the VM unit as `Type=simple`, and deployment.md:172 has the inline-comment SMTP example. Whether the VM's .env actually contains an inline comment was not checked, because values are never read.)

- **D2-7-1** (medium, high) deploy/systemd/parayabuck.service:19-49; docs/deployment.md:63-73, 230, 342.
  - The VM's paths, user and sandbox overrides are hand edits to full /etc copies of the units. Nothing renders or checks the installed unit.
  - Re-copying the template per the docs reverts them. The failure appears only at the next daemon-reload or reboot (CHDIR, USER or NAMESPACE errors, then `failed`).
  - Fix: move the overrides into a `systemctl edit` drop-in, or add a render script, and check with `systemctl show -p User,WorkingDirectory,ProtectHome,Type,WatchdogUSec`.
- **D2-7-2** (medium, high; verified from the log) parayabuck.service:11-18; operations.md:234-241; OPERATIONS_LOG.md:9.
  - The docs promise that a hung loop stops the watchdog pings and systemd restarts the unit after 300 s. The VM unit is Type=simple with no watchdog, so a wedged account loop (C8-1) is never restarted.
  - The deadman install on the VM is not recorded.
  - Fix: install a Type=notify drop-in, or document that the VM runs without the watchdog.
- **D2-7-3** (medium, high) parayabuck.service:23-26; deployment.md:342, 451-468.
  - The template sets PARAYABUCK_TRADING_HOST=1 inline, although its own comment says other machines must not set it, and the docs say the line is added by hand in a drop-in.
  - Any host built per First install is designated the trading runner, which risks two runners on the shared PAPER key (the 09-25 incident class).
- **D2-7-4** (medium, medium; the VM's .env was not checked) deployment.md:172; config.py:583-587.
  - The documented SMTP line carries an inline `# comment`. systemd EnvironmentFile keeps it in the value, and load_dotenv(override=False) does not replace it.
  - The shell test mail passes while the engine's mails fail with 535.
  - Fix: move the comment to its own line, have doctor warn on `#` in credential values, and on the VM check for the `#` without printing the value.
- **D2-7-5** (low, high) parayabuck.service:47-49; cli.py:729-805. The `-`-prefixed reports ReadWritePaths only moves the failure: doctor's mkdir under ProtectSystem=strict is a start blocker.
- **D2-7-6** (low, medium) deployment.md:39-40, 63-65, 254-257.
  - The VM runs ProtectHome=read-only, while the docs claim ProtectHome=true and say the off-host copy cannot read the deploy user's home.
  - On the VM the engine and backup can read ~/.ssh and ~/.config/gcloud, so a bare gsutil off-host command silently uses the user's gcloud credentials.
- **D2-7-7** (low, high) deployment.md:33, 107, 119, 244, 255-257, 279-280, 325-341; .env.example:29. The docs hardcode /opt/parayabuck and the `parayabuck` user, so VM commands fail loudly or edit a nonexistent file.

Checked fine:
- Backup writes stay under data/.
- ENV_PATH keeps .env out of the backup process.
- The deadman writes nothing.
- There are no $HOME dependencies at runtime.
- _ensure_writable holds.
- The restore docs use `systemctl show -p User`.

## Q2-9 — integration gaps (orchestrator: these are test gaps, each tied to a verified production finding. The severity is the risk that the linked bug regresses unseen, not a runtime bug of its own.)

- **Q2-9-1** (high, high) tests/test_audit_fix_W08.py:80-100; test_portfolio_runtime.py:805-870; runtime.py:2959-3024, 3271.
  - No test chains a booked SELL fill through `_handle_trade_update` into a real `PortfolioRuntime.advance_rebalance`; every advance test injects a stub or an empty runtime list.
  - P2-1-1 (a superseded BUY submitted) and P2-5-1 (an exception swallowed, the BUY dropped) are therefore invisible to the suite.
- **Q2-9-2** (medium, high) test_release_v2_integration.py:134-160; test_tax_ledger_2026_10_27.py:103-160.
  - Fills reach the books only through a direct apply_trade_update with exact prices or through raw SQL.
  - Tax FIFO is never tested on runtime-written partial fills with execution_price or fee_observed=False (F2-2-2, F2-3-8).
- **Q2-9-3** (medium, high) W07:90; engine_core_audit:237-251; two_phase:362-426.
  - `_resume_startup_rebalances` is stubbed everywhere, so the resume-before-catch-up ordering (P2-1-1 at restart) and the breaker gate at runtime.py:3040 are never run.
- **Q2-9-4** (medium, high) test_paper_receipt_v2.py:207-230. Catch-up is tested with a canned result. No test runs catch-up and then the scheduled decision with a cross-check mismatch (P2-6-1).
- **Q2-9-5** (medium, high) W09:611-662; web_round9:44-47.
  - Lifespan shutdown is tested only against a fake manager, never a real uvicorn Server with a signal.
  - R2-4-1 and R2-4-2 are therefore untested.
- **Q2-9-6** (medium, high) test_backup.py:40-70. Backup is tested on a synthetic table, never on a real ledger restored, reconciled and resumed (R2-7-1, P2-4-1, R2-2-2).
- **Q2-9-7** (medium, high) test_restart_chaos.py:735-1120. The path from a stream drop or undecodable frame to reconcile to rebalance advance is never tested end to end; the stream workers are stubbed (F2-6-1, C2-5-2, C2-5-5).
- **Q2-9-8** (low, high) test_daily_summary.py:97-125. The summary and receipt are never tested on a held-rebalance day (D2-3-1).

Checked fine:
- The two-phase lifecycle at PortfolioRuntime level.
- Booking happens before the snapshot.
- Gate re-reads under the lock.
- Startup and reset ordering in the code is consistent.

## P2-7 — multi-day rebalance lifecycle across sessions and holidays (orchestrator: checked P2-7-1 in the code. runtime_service.py:1429-1431 returns early, and the comment says "the session is planned on a later cycle". Nothing re-queues the batch for this status, so whether a later cycle for the same session exists is the open question. Severity kept at medium/medium.)

- **P2-7-1** (medium, medium) runtime_service.py:1417-1431; runtime.py:4271-4319, 5104-5150.
  - A closed-bar batch whose advance completes the rebalance returns `rebalance_complete`, restores the cadence and is not parked.
  - The X+1 session therefore gets no decision; X+2 decides.
  - The trigger is a completion the trade-update path missed (P2-5-1, F2-6-1, a stream gap) or `_close_unsettled_sell_legs`. Same root as P2-2-4, through a different branch.
  - The W06 test passes only because it re-feeds the bars.
- **P2-7-2** (low, high) runtime_service.py:1417-1423, 2389-2423, 2053-2054.
  - Superseding closes a sell_pending rebalance while `-residual1` is still working.
  - If that residual expires, nothing advances: no residual2 and no exhausted/dropped alert. The exit slips about one session with only a log line.
- **P2-7-3** (low, medium-high) execution/portfolio.py:973-979; runtime_service.py:2805-2828.
  - The December deferral window is evaluated on the original decision's timestamp during a later re-plan, so a re-plan SELL settling on Dec 17 or later is not deferred.
  - The reverse also happens: a January re-plan can be deferred.
- **P2-7-4** (medium, low) runtime_models.py:34-44; alpaca_reconciliation.py:52-68; runtime_service.py:2370-2387, 1493-1494.
  - A leg whose broker status stays held, stopped, suspended, replaced or pending_review never becomes terminal locally.
  - Every later batch and every restart then returns rebalance_in_progress, freezing all decisions (exits included) for the market.
  - There is no age-based escalation. Whether Alpaca reports these statuses for a DAY SELL is unverified.

Cross-references, no new id:
- P2-1-1 has extra skip paths: the missing_symbols, warming_up and no_eligible_bars returns at runtime_service.py:1382 come before the stale check.
- Residual-vs-duplicate SELL protection relies on done_for_day counting as open (Q2-1-3).

Checked fine:
- NY-session keying and UTC-normalised ordering.
- The December window at the year boundary.
- The gain band has no date dependence.
- Holidays and early closes.
- Residual rounds are capped at 3.
- Advance on a COMPLETE rebalance is a no-op.
- The superseded-SELL hold.

## S2-9 — logging and telemetry leaks (orchestrator: nothing medium or higher. The control token, WS ticket, approval key and healthcheck URL never reach logs. Journal and diagnostic bundle redaction holds.)

- **S2-9-1** (low, latent, medium) web/security.py:82-114; alpaca trading/stream.py:64-75; websockets legacy/protocol.py:222, 1165-1168; frames.py:148-190.
  - If the websockets.* loggers are ever set to DEBUG, the Alpaca auth frame is logged, and its elided tail includes secret_key.
  - The only scrubber rewrites `ticket=`. Nothing sets those loggers today (logger.py:38 sets only "parayabuck").
  - Fix: pin websockets.* to WARNING in setup_logging.
- **S2-9-2** (low, latent, medium) web/routers/engine.py:43-47; web/managers/engine.py:856-860, 1003-1037; web/services/settings.py:176-177, 426-447.
  - redact_secrets scrubs the stored and returned text, but the log copies are raw: log.exception tracebacks and `%s` of exc reach journald and /ws/logs.
  - The redaction set omits the running engine's in-memory key after a rotation.
  - Fix: scrub at the sink with a formatter.
- **S2-9-3** (low, high) runtime_repository.py:2111; execution/risk_peak.py:370; monitoring/metrics.py:265. Three log lines print the full Alpaca account UUID, while the journal masks it to the last 4 characters.

Checked fine:
- control token header only
- uvicorn ticket filter applied before formatting
- WS log stream sends message text only
- approval key and signatures never logged
- deadman prints exception type names only
- 422 bodies drop input
- no show_locals
- journal and bundle key-based redaction
- trade-update %r carries no account or credentials
- lifespan redacts before logging
- no root DEBUG and no file sink

## R2-9 — operator halt marker and persisted safety-incident lifecycle (orchestrator: checked three claims in the code. backup.py has no reference to the halt marker. storage.py:80 sets synchronous=NORMAL. live_safety.py:161 uses os.path.lexists. The marker write is atomic and the marker read fails closed.)

- **R2-9-1** (medium, medium; verified) backup.py:47-49, 307-309; live_safety.py:150-156; cli.py:629-643; docs/operations.md:490-494. A `parayabuck halt` issued while the engine is stopped exists only as the marker file, and the marker is not in the backup set or the documented state table. A DR restore therefore starts unhalted and trades after reconcile.
- **R2-9-2** (medium, medium) storage.py:24-30, 79-80; runtime.py:2689-2701; ledger.py:576-588. Halt incident rows commit under WAL synchronous=NORMAL, so an OS or power crash before a checkpoint can roll back the newest halts (ambiguous_order_state, protection_failure). The "crash cannot lose it" claim in R-1 holds only for process crashes.
- **R2-9-3** (low, medium; verified) live_safety.py:147-161. operator_halt_active uses os.path.lexists, which returns False when lstat raises (for example EACCES on the parent). That breaks the documented fail-closed rule.
- **R2-9-4** (low, high) runtime.py:2758-2786, 3505-3528, 1092-1105. A marker written while the engine runs is read only by the decision loop. The trade-update advance and submit gate never read it, so a BUY can be submitted after a halt while the decision thread is busy.
- **R2-9-5** (low, high) runtime.py:2661-2664, 2732-2747. A failed halt persist is only logged at critical level, with no alert and no retry, so a restart drops the halt.
- **R2-9-6** (low, high) cli.py:118, 642-648; web/managers/engine.py:431-432. The resume help and the start refusal say resume clears the halt, but a persisted operator_halt row still starts the engine halted. The runbook is correct; the CLI says nothing about the row.
- **R2-9-7** (low, high) live_safety.py:206-212. resume on a marker that is a directory raises an uncaught IsADirectoryError. This fails closed, but the tool gives no remedy.
- **R2-9-8** (low, medium; latent) cli.py:608-643. halt writes the marker for whichever config was loaded, with no engine or mode guard, so a PAPER-config halt does not stop a LIVE engine.
- **R2-9-9** (low, high) web/ and monitoring/ have no reference to the marker. A stopped engine held by the marker looks like an ordinary stop on the dashboard and in status.
- **R2-9-10** (low, low) runtime.py:2700-2747, 5994-6018. A halt save racing a reset can resurrect a released incident row (an over-halt, narrow window).

Checked fine:
- marker atomic write with fsync
- marker read fails closed (symlink, FIFO, dir, empty, invalid JSON, oversize)
- start honors the marker and the row
- reset is refused while the marker exists
- clear and save are serialized under _risk_state_lock
- corrupt incident rows are kept as halts
- the daily-loss latch is recomputed
- there is no kill-switch mechanism

## F2-10 — dividend/interest import and FX/tax conversion (orchestrator: F track complete at round 10. Dividends never touch FIFO lots or cost basis. FX selection is fail-closed.)

- **F2-10-1** (medium, high) tax/export.py:166-172, 295-300; tax/dividends.py:166-206; tax/cli.py:141-163. The import window is never recorded. A partial or late import (`--since 2026-06-01`, or no import after October) reads as a complete year: status "ok", understated dividend tax, and the declaration shown as "aşılmadı". DIVNRA withholding is credited even when no matching gross is imported, so net dividend goes negative. Extends F2-7-7.
- **F2-10-2** (low, medium) tax/dividends.py:119 vs execution/alpaca_account.py:127-128. `after=since` is exclusive on the date, while the repo elsewhere passes since-1 day, so activities dated on --since itself are dropped.
- **F2-10-3** (low, high mechanism) tax/dividends.py:37-38, 188-206. With `INSERT OR IGNORE` keyed on activity_id alone, restated amounts or a type collision under the same id are silently kept as the old row and reported as already_present.
- **F2-10-4** (low, high; PAPER diagnostic) scripts/reconcile_paper_incident.py:130; monitoring/paper_incident.py:36, 194-200. The PAPER equity bridge never fetches DIVNRA or INT and would book them under net_transfers, which leaves an unexplained residual.

Checked fine:
- FX rate is the previous published date, strictly before the trade date; a gap over 10 days raises.
- DIVNRA sign is flipped; pagination and NaN are guarded.
- Turkish UTC+3 dates; January rollover of the prior month.
- Dashboard and export agree.
- Indexation uses ≥ 10% with a one-month lag.
- Progressive brackets checked by hand.
- Interest is kept out of the withholding credit.
- The EVDS key goes in a header.

## T2-9 — VT35 vol-target sizing, live vs backtest parity (orchestrator: the formula is shared through EquityLeveragedTrendV1.decide(); only the inputs can diverge. T2-9-1 caveat: per T2-8's timeline, a fresh decision-time verify adopts the final SIP D bar over the IEX stream bar, so the IEX close survives only when the basis check is skipped (T2-8-2). Severity medium, confidence lowered to low-medium.)

- **T2-9-1** (medium, low-medium) signal_cross_check.py:228-229; runtime_service.py:1177-1196; execution/portfolio.py:956-959.
  - When the decision tail is the IEX stream bar, the target weight uses the IEX QLD close, while the backtest uses SIP.
  - The cross-check compares only trend_state, not weight. A 50 bp close gap moves the weight by about 0.51 pp, which equals the 0.5 pp band and creates an order the backtest never places.
- **T2-9-2** (low, low; extends Q2-8-4) execution/portfolio.py:956-959; runtime_service.py:1509, 1807-1811.
  - target_qty and current_weight divide by broker equity (marked at the broker's current price) but multiply by the decision close.
  - A post-close mark move of x≈±1% gives a spurious trade of about 0.96% of equity, above the band.
- **T2-9-3** (low, medium; design note, parity holds) execution/portfolio.py:930-1032, 216-287. Vol-driven trims are subject to the gain band (up to 8 pp) and the December deferral, so realized exposure can sit above the 35% target (example: 0.98 × 38% = 37.2%). The backtest behaves identically. Document this.

Checked fine:
- Vol is 20 simple returns, ddof=1, sqrt(252), identical in the R2 path.
- Split-adjusted closes on both sides.
- Clamps hold (≤ 1, overlay 0.98).
- NaN is unreachable because Bar rejects it.
- Short history gives flat.
- All settings.yaml strategy and execution keys reach both planners.
- Band and hold ordering matches, apart from the known T2-2-3 and T2-4-8.
- research/fast_engine_v1 is not on main, so it was not compared.

## Q2-10 — suite performance and hygiene (orchestrator: the auditor ran the normal suite once with `.venv/bin/python -m pytest -q -p no:cacheprovider --durations=40 -x`: **3043 passed, 1 skipped, 0 failed, 1 warning, 140 s**. git status afterwards was clean apart from raw-r7.md. Q track complete at round 10.)

- **Q2-10-1** (medium, medium) web/managers/engine.py:1907-1919; web/services/dashboard.py:528-549; tests/conftest.py:190-203.
  - The suite is not xdist-safe: `_SECRET_WARNING_CACHE` stores a stubbed None per root, and nothing resets it.
  - The capacity cache is keyed by a recyclable id().
  - Q2-5-4 (the shared reports/ root) also blocks xdist, and xdist is not in the lock.
- **Q2-10-2** (medium perf, medium) tests/test_historical_data.py:108-229. Two chunking tests take 28.5 s (about 20% of the suite) because they build 2 years of hourly bars, including off-hours, across 20 symbols to exceed 10k rows. Fix: monkeypatch `_MAX_ROWS_PER_REQUEST` and use a 60-day window.
- **Q2-10-3** (low, high) tests/test_email_alerts.py:94-103, 253-265; email_alerts.py:62, 336. The failing-SMTP test sleeps through the real 1 s + 4 s retry backoff (5.03 s). Fix: inject a no-op sleep.
- **Q2-10-4** (medium, low) tests/test_signal_adjustment_parity.py:513-515. The fixture anchors the decision bar to `datetime.now(UTC)` at 20:00 over calendar days. It may depend on the time of day or weekday, and on DST, where 20:00 UTC becomes 15:00 ET after 11-01.
- **Q2-10-5** (low, high) tests/test_observability_wiring.py:75-92; conftest.py:618-652. The global journal writer thread leaks, and the `pb-journal-writer` exemption in the leak check hides it.
- **Q2-10-6** (low, high) pyproject.toml:73-77; ci.yml:61-63. The only marker, paper_chaos, selects nothing. There is no slow or integration marker and no `--strict-markers`.
- **Q2-10-7** (low, high) test_observability_wiring.py:39-69, 197-212. The dedupe test uses two fixed 1 s real-time absence windows.
- **Q2-10-8** (low, high) test_reliability_hardening.py:70; test_audit_20261011_f1.py:253; test_audit_20261011_w2.py:421. Three unbounded `thread.join()` calls, with no pytest-timeout, would hang CI on a deadlock.
- **Q2-10-9** (low, high) test_intent_watchdog.py:25; test_warmup_anchoring.py:16. Module-level importorskip of a declared dependency turns a broken install into skips.
- **Q2-10-10** (low, high) test_dashboard_api.py:415. A module-level `tempfile.mkdtemp()` is never removed.
- **Q2-10-11** (low, medium) test_vt35_golden.py:263-312. Seven golden backtests reload the same fixture (about 4 s).

Checked fine:
- causality fixtures are module-scoped
- real sleeps are ≤ 0.1 s apart from SMTP
- other joins have timeouts
- no fixed ports
- credential env is stripped and restored
- fixed date anchors elsewhere
- the December/vol/tax module caches are reset by tests

## C2-9 — stop and shutdown races (orchestrator: checked C2-9-1 in the code. engine.py:1118-1120 marks "stopping" and returns while runtime is None, and the router then answers "Bot engine stopped." Note: C2-9-5 relies on the template watchdog, which the VM does not run (D2-7-2).)

- **C2-9-1** (medium, high mechanism; verified) web/managers/engine.py:1112-1121; web/routers/engine.py:56-63; runtime.py:1652-1666, 1100-1101, 4614.
  - A Stop pressed during startup returns at once, and the UI reports "stopped".
  - Startup resume and catch-up then still submit orders. The last cancel checkpoint is at runtime.py:1664, and the submit gate checks only `_running`, which is True during startup.
  - The orders the operator meant to stop go live.
- **C2-9-2** (low, high) runtime.py:3512-3519, 4131-4170, 2000, 2018-2022; runtime_service.py:1283-1292, 3548.
  - Stop does not interrupt the decision batch in progress: there is no submit_gate on the decision path, and `_running` is checked only between queue items.
  - A batch longer than 5 s also makes the join expire, which leaves stop_failed while the batch still submits.
- **C2-9-3** (low, high path / low likelihood) runtime.py:1609-1637, 1999-2008. If a thread start fails mid-startup, joining the unstarted Thread raises RuntimeError. stop() then aborts before its cleanup and ends in stop_failed.
- **C2-9-4** (low, medium) runtime.py:1939-1952; monitoring/snapshot.py:132-191.
  - The final "stopped" snapshot is written after only a 1 s writer join. A slow periodic write can land after it and leave the file at "stopping".
  - The snapshot can also say "stopped" even when stop then fails.
- **C2-9-5** (low, high; template only) web/managers/engine.py:557-561; runtime.py:1926-1931. watchdog_healthy returns True once `_running` is False, so a hung stop or stop_failed is never caught by the watchdog; only the deadman pages.

Checked fine:
- every runtime join is time-bounded
- spawn sites serialize against stop
- start after stop is refused
- cleanup is ordered after the joins
- SQLite writes are transactional
- mid-POST crashes are covered by the submitting row and reconcile
- no self-join
- atomic_write leaves the old file intact
- the data-stream stop is bounded
- stop is idempotent

## S2-10 — systemd sandbox and process hardening (orchestrator: S track complete at round 10. The core hardening set is present on all three services: NoNewPrivileges, Protect*, an empty CapabilityBoundingSet, SystemCallFilter=@system-service and UMask=0077. Nothing was run with systemd-analyze. The items needing a VM check are marked.)

- **S2-10-1** (medium, high facts / needs a compromised dependency) parayabuck.service:22, 41-63; runtime.py:726. The engine unit has no egress containment. The LIVE keys and the approval HMAC key sit in the process env, so any in-process code can exfiltrate them over AF_INET and forge approvals. ReadWritePaths also lets it rewrite config/. Fix: a per-UID egress allowlist, and asymmetric approval signatures so the engine holds only a public key.
- **S2-10-2** (low, medium-high) parayabuck.service:22-26; web/app.py:72-92. EnvironmentFile= overrides Environment=, so a .env line can unset the inline TRADING_HOST designation. Together with PARAYABUCK_CONTAINER=YES and HOST=0.0.0.0 in .env, the console binds all interfaces. Fix: have doctor refuse these keys in .env.
- **S2-10-3** (low, medium; VM check) all units. With no LimitCORE=0, a watchdog SIGABRT or crash core holds the env (LIVE keys). Also missing: ProtectClock, ProtectHostname, RestrictRealtime, KeyringMode=private and RemoveIPC.
- **S2-10-4** (low, medium) parayabuck.service:64. MemoryDenyWriteExecute is off with a "CPython/numpy may need W+X" rationale, but the runtime lock has no JIT and src has no ctypes or numba. Enable it after one VM trial run.
- **S2-10-5** (low, medium; VM check) parayabuck.service:49; settings.yaml:176. The engine's ReadWritePaths covers data/backups, so the trading process can delete its own restore points. Fix: ReadOnlyPaths for data/backups.
- **S2-10-6** (low, medium) web/managers/backtest.py:68-98. Web backtests run in the engine's process and cgroup with no MemoryHigh, so an OOM takes down the console and engine together and can exhaust StartLimitBurst.

Checked fine:
- the core hardening set
- socket families
- the ProtectSystem=strict write set
- UMask 0077 gives 0600 files
- the backup child inherits the backup env, not .env, and close_fds
- deadman uses LoadCredential
- ExecStart runs without a shell

## P2-8 — LIVE dollar cap end to end (orchestrator: the single BUY gate `_size_buys_to_safe_capacity` covers every BUY path, and exposure is measured in total, including open BUYs, failing closed when an open BUY is unpriced. The remaining gap is the price basis.)

- **P2-8-1** (medium LIVE, high mechanism) runtime_service.py:3329-3372; execution/portfolio.py:1084-1086; alpaca_orders.py:96-101.
  - For a flat-entry BUY, the dollar-cap term is priced at the prior close, and the order is a qty-only market order.
  - A gap-up open fills above the cap. Example: cap 98,000 at close 100 gives 970 shares; filled at 103 that is 99,910, about 2% over.
  - Nothing checks the fill price afterwards, and the unfilled remainder of a partial fill is never re-checked either.
  - Fix: price the cap term at reference × (1 + gap margin), and/or re-check at fill time and cancel the remainder.
- **P2-8-2** (low, high arithmetic / low reach) runtime_service.py:3338-3340. gap_headroom subtracts the committed open-BUY notional without the gap factor, so a +3% open on both the committed and the new BUY can borrow about committed × margin. Fix: `(spendable - committed*(1+m))/(1+m)`.

Checked fine:
- residual, replan and retry SELLs are uncapped as intended
- exposure counts managed positions plus repository and broker open BUYs, deduplicated by client id
- the cap is a total across symbols, not per symbol
- PAPER cap is forced to 0 and every cap term is LIVE-gated
- the cap only changes through a restart, which re-verifies it
- caps below one share result in no trade
- there are no notional orders
- admission runs under _portfolio_lock

## D2-8 — alert and e-mail kinds (orchestrator: checked the D2-8-1 cap code in email_alerts.py:279-290; it applies one 20/hour cap with no priority. Checked D2-8-3: four sites share reason "reconciliation_failed". The auditor's alert-kind table is summarised under checked-fine.)

- **D2-8-1** (high, high; verified) monitoring/email_alerts.py:279-299; alerts.py:63-115; config.py:168.
  - A single 20/hour e-mail cap applies first come first served, with no severity priority.
  - A storm of retry mails can consume it before a drawdown_halt arrives, so the halt mail is dropped.
  - alert_delivery_failed is keyed by reason only, so the dropped kind is not named, and repeats within 60 s are suppressed.
  - Edge-triggered kinds are never re-sent.
- **D2-8-2** (medium, high mechanism) paper_incident.py:571-581; incident.py:196; runtime.py:3746-3747.
  - Incident-record alerts put a fresh uuid4 into the throttle key, so the 60 s and 30 min dedup never applies.
  - A 40-minute account-read outage sends about 40 circuit_breaker mails, which feeds D2-8-1.
  - The same happens for undecoded trade updates (activity_id) and ownership_surplus (broker_quantity).
- **D2-8-3** (high, high; verified) runtime.py:5467-5560; email_alerts.py:266-278, 352-354.
  - Four distinct reconciliation incidents (drift, protection check deferred, and others) share the key `reconciliation_failure:reason=reconciliation_failed`.
  - After one mail, the others are dropped silently for 30 min, with no journal row inside 60 s.
- **D2-8-4** (high, high) runtime.py:5917-6049, 5907-5914, 1718-1724; web/managers/engine.py:499-501.
  - reset_safety_lock (halt clear) emits nothing to the journal or e-mail; it only logs.
  - risk_epoch_reset and runtime_started are INFO and not mailed.
  - LIVE auto-start under a standing authorization is logged only.
- **D2-8-5** (high, high) email_alerts.py:58-62, 319-407; snapshot.py:55.
  - The e-mail retry budget is about 5 s (3 attempts), and there is no outbox, so mails are lost on restart.
  - The sink's sent/failed/dropped stats() has no caller.
  - alert_delivery_failed is visible only through `ops events`; no health, status or dashboard surface reads it.
- **D2-8-6** (medium, high) runtime_repository.py:1172-1205. Stale-ownership clearing (reconcile zeroes owned) only logs: no alert, journal row or mail. Companion to R2-7-1.
- **D2-8-7** (medium, high) runtime.py:4745-4753; events.py:80-139; operations.md:111. signal_adjustment_failure (equity entries fail closed) is INFO and never mailed.
- **D2-8-8** (medium, high) paper_incident.py:554-581; incident.py:104-112. A CRITICAL DATABASE_ERROR (halting) is mailed as `reconciliation_failure` at WARNING, which misleads the operator.
- **D2-8-9** (medium, high) operator_runbook.md:533-539; paper_incident_taxonomy.md:18-20. The runbook documents six incident categories with no emitter (DUPLICATE_ORDER_RISK, CLOCK_OR_SESSION_MISMATCH, RESTART_RECOVERY_FAILED, DUPLICATE_DECISION, SIGNAL_HISTORY_UNVERIFIED, STATE_RESTORE_FAILED).
- **D2-8-10** (low, high) operations.md:117-118; events.py:39, 61. The documented paper_receipt alert is never emitted; broker_local_drift and reconciliation_unexpected_fill_side are dead names.
- **D2-8-11** (low, medium) email_alerts.py:259-264. There are no quiet hours, and intent_recovered is never mailed, so the owner is not told an alarm cleared.
- **D2-8-12** (low, medium) email_alerts.py:121-153. The subject is cut at 117 characters (the daily-summary status fields are lost), and dict/list fields are printed as Python reprs.
- **D2-8-13** (low, medium) runtime_service.py:343-352; runtime.py:2509, 1838, 1570, 2905. Coarse keys merge distinct risk_incident, circuit_breaker and exit_sell_rejected incidents.

Checked fine:
- every halt set is followed by an emit
- all 28 kinds are in ALERT_KINDS
- EMAIL_ALERT_KINDS matches the docs
- [PAPER]/[LIVE] appears in the subject
- alert_delivery_failed is not mailed, so there is no loop
- SMTP uses STARTTLS, scrubbed errors and no retry on a login failure
- stream-down alerts are mailed
- intent_alarm is edge-triggered
- all mails are English

## R2-10 — long-uptime state growth (orchestrator: R track complete at round 10. At VT35 volume, nothing medium or higher grows without bound; every growing structure stays small for years.)

- **R2-10-1** (low, medium) web/services/dashboard.py:248-256; runtime_repository.py:532-533. The latest-decision-per-market query uses a correlated MAX subquery over an index without market, so each rebuild costs O(N²). It starts to matter at about 4 or more years of rows. Fix: add an (account_id, market, decision_timestamp) index or a window function.
- **R2-10-2** (low, high) runtime_service.py:647, 918-925, 1248-1250. The in-memory bar history is append-only and never trimmed, so per-batch set and dict work grows linearly. A restart resets it. Fix this together with T2-8-5.
- **R2-10-3** (low, medium) execution/ledger.py:207-212; monitoring/metrics.py:240-258; monitoring/forensics.py:135-160; health.py:555. The ledger tables have no retention. `ops metrics` reads the whole risk_state table, and the health path runs `SELECT *` on rebalance_executions on every evaluation.
- **R2-10-4** (low, high) storage.py:79-82; journal.py:165-167; data/cache.py:54-55. No journal_size_limit or TRUNCATE checkpoint is set, so the -wal file stays at its peak size after a burst.
- **R2-10-5** (low, medium) data/cache.py:17-240. The warmup cache is never pruned outside a replaced window. The VM's 41,525 rows suggest a larger universe or timeframe than daily QLD; to be checked read-only.
- **R2-10-6** (low, medium) runtime_repository.py:1286-1309, 1706-1808; dashboard.py:706-708. Ownership-adjustment consumption adds floats and tests exhaustion by exact `<` comparison, so a sub-ulp residue keeps the row open forever.
- **R2-10-7** (low, high) cli.py:1699-1716; backup.py:470-478. Operator ops bundles and labelled backups have no retention.

Checked fine:
- Throttle and e-mail maps are bounded.
- Metrics use a fixed vocabulary.
- Journal retention.
- Deferred batches drain.
- Cross-check pending entries are pruned.
- The snapshot lease is paired.
- WS subscribers and tickets are pruned.
- Backtest runs are capped at 20.
- Caches are TTL-bounded.
- Bounded queues.
- Stream restarts are capped at 5.
- No threading.Timer.
- No float P&L accumulation on the engine path.
- No long-lived readers blocking checkpoints.

## T2-10 — December deferral and gain band, the K layer (orchestrator: T track complete at round 10. T2-10-1 matches the pre-registered H2 spec, which is sign-blind. Changing it would alter the deployed, validated K composition, which AGENTS.md says to preserve without an explicit strategy-change request. Decision: keep the behaviour, document it, and leave a sign-aware deferral as a research candidate to judge after Turkish tax, per [[paper-identity-vt35]].)

- **T2-10-1** (medium, high; design per spec) execution/portfolio.py:974-1004; backtest/portfolio_engine.py:1588-1618; tests/test_december_deferral_h2.py:356. The December deferral ignores the sign of the gain, so a loss-realizing trim is postponed to January. The loss is then booked in the next tax year and cannot offset the current year's gains.
- **T2-10-2** (low, high mechanism / opt-in) runtime.py:4085-4089; runtime_service.py:1602-1636. With stale_account_sell_only=true, the stale-account sell-only latch writes gain-band bypass metadata for every symbol. Ordinary strategy trims then skip both the band and the December deferral.
- **T2-10-3** (low, medium; documented design) execution/portfolio.py:288-315; tax/ledger.py:159-176. The band's USD gain fraction ignores FX and Yİ-ÜFE indexation, and its lot source (ownership events) differs from the tax ledger's (fills).
- **T2-10-4** (low, medium) execution/portfolio.py:225-239, 1016-1022; runtime_service.py:813-832. The fail-open fallbacks leave no decision metadata and raise no alert. A lot mismatch falls back to the constant band, logged at INFO; a calendar failure turns the deferral off, logged once at WARNING.

Checked fine:
- 2026, 2027 and 2028 windows worked by hand: 2027 Dec 24 is closed and 2027 Dec 31 is open.
- Half days count as sessions.
- Season edges.
- No hardcoded year table.
- FIFO partial lots and the tolerance check.
- Exemptions for full exit, halt, liquidation and exposure scaling.
- Backtest parity apart from T2-2-3.
- The January path re-derives trims from the current target.
- No wash-sale logic is needed.

## D2-9 — metrics and performance numbers (orchestrator: division, NaN and timezone guards hold. All findings are display-only; the trading gates use the same snapshot.)

- **D2-9-1** (medium, high) web/static/js/dashboard.js:145-180; web/services/performance.py:143-151. The chart plots equity % including deposits against price-only VOO % on one axis, with a side-by-side legend. A deposit therefore reads as outperformance, even though the code comment says the two are not compared.
- **D2-9-2** (medium, medium) performance.py:74-80; backtest/portfolio_engine.py:689-707. The live benchmark is raw (dividend-unadjusted) VOO, while the backtest benchmark is total-return. The live chart understates VOO by about 1.2-1.5% a year.
- **D2-9-3** (low, medium) performance.py:22 vs portfolio_engine.py:333. The dashboard benchmark is hard-coded VOO, while the backtest and config use SPY.
- **D2-9-4** (low, low) performance.py:138-148; alpaca.py:188-207; dashboard.js:136-166. Each series is rebased to its own first point, so the windows can be misaligned. The 1D figure measures from the first intraday point instead of the prior close used by daily_pnl.
- **D2-9-5** (low, high) web/services/dashboard.py:924-942; dashboard.js:86. The REST overview account_view omits fees, so "Ödenen ücretler" stays "—" until a websocket message arrives.
- **D2-9-6** (low, medium; extends F2-7-1) alpaca_account.py:645-655. Realized P&L is today versus the prior close (a residual that includes fees and dividends), while unrealized is versus cost. Their sum has no clear meaning.
- **D2-9-7** (low, medium) engine.py:1409; dashboard.py:411-587. The risk-meter gross exposure counts external positions, but the strategy card counts only the managed share, so the two exposure figures disagree.

Checked fine:
- division guards
- non-finite values refused
- drawdown units
- daily-loss denominator consistent
- summary and dashboard read one snapshot
- stale marking
- TR time zone for tax
- cache keys
- backtest Sharpe/Sortino with labeled rf=0
- tax netting

## P2-9 — market-clock gating of order submission (orchestrator: every order is a DAY market order and is queued to the next open by design. The clock gate is inert. P2-9-1 shares its price-basis root with P2-8-1, so fix them together.)

- **P2-9-1** (medium LIVE, medium) runtime_service.py:2776, 2918, 3262, 3340-3350; data/aggregator.py:103-111; runtime.py:2936-3007.
  - A BUY whose release is triggered by a fill during the session (for example a residual SELL filling at 11:00) is priced at the prior session close.
  - The only guard is the 3% gap margin, and that applies only in LIVE.
  - If the price has moved more than 3% intraday, the fill exceeds cash.
- **P2-9-3** (medium, low; broker behaviour unverified) runtime.py:2936-3007; runtime_service.py:379-381.
  - Nothing checks the session or a trading halt at submit time.
  - A deferred BUY goes out seconds after an opening SELL fill, and also during an LULD halt.
  - clock.is_open and next_close are never read.
- **P2-9-2** (low, high) runtime_service.py:361-386, 3109-3162; alpaca_orders.py:100.
  - The clock gate can never refuse an order, because its only refusal branch is OPG and RUNTIME_ORDER_TIME_IN_FORCE is a separate "day" constant that can drift from the value actually sent.
  - It fails open when the clock call fails.
  - It costs one REST clock call per deferred BUY while holding _portfolio_lock.
- **P2-9-4** (low, medium) runtime.py:5687-5723; monitoring/premarket.py:35-59. The premarket skew is measured from a timestamp taken before the DB read and the REST call, so a slow call over 2 s reads as clock skew. That raises a false alert and latches entries.
- **P2-9-5** (low, high) monitoring/premarket.py:6-10. The module docstring says the probe is "not wired into the runtime", but it runs on every decision-loop pass from 09:00 to 09:25 ET and can latch entries.

Checked fine:
- DST handling uses zoneinfo, with no fixed-UTC gating; the 11-01 and 03-14 transitions are safe.
- Holidays and early closes.
- DAY is the only time-in-force; CLS and OPG never reach the broker.
- Calendar vs broker next_open/next_close is cross-checked.
- The embargo is respected when provider bars are adopted.
- Committed BUYs prevent double spending overnight.

## C2-10 — deadlock and lock-order search (orchestrator: C track complete at round 10. **No lock-order cycle.** The inventory covers about 30 locks across runtime, store, broker, monitoring, data and web. There are no untimed waits, and every bounded queue uses put_nowait. Upper locks (ingestion, the spawn locks, reconcile, risk, portfolio) are never taken while state or a leaf lock is held.)

- **C2-10-1** (low, medium) web/routers/health.py:46-93; web/managers/engine.py:379-382, 532-536. The async `readyz` route calls `load_config`, `current_engine_state` and `has_active_runtime` directly on the event loop. Only `current_engine_state` waits on engine `_state_lock`, which the mode-toggle path holds across a config write with fsync. The whole loop stalls for that time. Fix: wrap the calls in `asyncio.to_thread`, as routers/status.py does.
- **C2-10-2** (low, medium) runtime.py:6105-6128, 6099, 5973-6081. `_restart_stream_worker` spawns a thread without checking `_running`, which breaks the spawn discipline the comment at 1963-1967 describes. It then polls for up to 5 s while holding `_reconcile_lock` inside `reset_safety_lock`.

Lock-order graph (summary):
- BacktestManager → engine state
- engine ops → account_refresh → account → engine state → runtime state
- runtime spawn locks → threads
- portfolio → state, snapshot and store
- state → store, journal and alerts
- risk → state and store
- reconcile → risk, state, peak and threads
- peak → store and journal
- Leaves (no nested acquisition): the journal, alerts, e-mail, ticket, log-queue and snapshot locks

## D2-10 — operator UX for the Turkish non-expert owner (orchestrator: D track complete at round 10. Checked D2-10-2 in the code: ui.js:482-503 sends the save whatever the dialog returns, so "Vazgeç" means "save without a restart". Checked the D2-10-1 dialog text at ui.js:357. Severities kept: D2-10-1..4 can lead the owner to a wrong action.)

- **D2-10-1** (high, high) ui.js:357; operations.md:162-166; engine.py:435-479; settings.yaml:10 (auto_start_engine: true).
  - The Stop dialog implies a lasting stop, but a stop is not persisted. Any restart (deploy, crash, reboot) auto-starts trading again, and LIVE does too under a standing authorization.
  - Fix: add a durable stop (write the halt marker from the UI, or persist a "stopped by operator" flag that should_auto_start honours), and say this in the dialog.
- **D2-10-2** (high, high; verified) ui.js:482-503; base.html:213-214; services/settings.py:370-416.
  - The restart prompt has no cancel path: "Vazgeç", Escape and X still save with restart=false, and that includes a switch to LIVE mode.
  - "Tamam" stops and restarts a running engine without saying that trading pauses.
- **D2-10-3** (high, high) ui.js:373-377; routers/safety.py:39-44; engine.py:1263-1290.
  - "Yeni risk dönemi" is the action that clears a drawdown halt, but its prompt is neutral and its confirm button says "Başlat", the same label as bot start.
  - It never says the loss is accepted and buying resumes at full size.
- **D2-10-4** (high, high) dashboard.js:232; ui.js:352-356; engine.py:424-436, 788-791; status.py:131.
  - An operator halt is invisible: the story says "Başlat ile çalıştırabilirsiniz".
  - Start is then refused with an English message that tells the owner to run `parayabuck resume`. Companion to R2-9-9.
- **D2-10-5** (medium, high) ui.js:352-390; engine.py:651-722. In CANLI mode, Başlat sends real orders with no confirmation. The arming text calls the switch "geri alınabilir" and never shows the dollar cap.
- **D2-10-6** (medium, high) ui.js:303; engine.py:1956. The SECURITY BLOCK message is shown in the neutral style, in English, with git-history instructions.
- **D2-10-7** (medium, high) health.py:119-536; dashboard.js:566, 580; incident.py:57-156. The "What to do" text on health checks and incidents is developer English (table and function names). The events list has no severity and no Turkish message.
- **D2-10-8** (medium, high; latent, e-mail is off) email_alerts.py:153-172. Alert e-mails are English only, with no severity and no owner action, and they point to a VM shell command.
- **D2-10-9** (medium, high) many sites (engine.py:381, 617, 621, 1260, 1270, 1460, 1516; settings.py:257, 370; trading_host.py:24-29; backtest.py:80-83; routers/engine.py:48).
  - Most refusals reach the owner in English, with env-var, CLI or log instructions.
  - A 409 "Configuration has changed" repeats forever, because the digest is never reloaded.
- **D2-10-10** (medium, high) tools.html:24-29; backtest.py:150-151; tools.js:81-106. The backtest's default Gün=90 silently overrides Yıl, and the result never shows its period. The owner reads 90-day CAGR as multi-year.
- **D2-10-11** (medium, high) base.html:136; alpaca_orders.py:96. The live warning talks about stop-limit orders the bot never uses, which implies a stop-loss that does not exist. It omits the real risk: market orders can fill far from the displayed price at a gap.
- **D2-10-12** (medium, high) dashboard.js:94, 533; ui.js:247, 390; tools.html:63. "canlı" means both live data and LIVE money, so a PAPER dashboard shows "canlı" next to data and workers.
- **D2-10-13** (medium, high) operator_runbook.md:31-35, 312-326, 517-518, 555-558.
  - The runbook is English only and names buttons and states that are not on screen (Start, reset, CIRCUIT_BREAKER, arm).
  - The LIVE procedure omits the 2-minute arming-phrase expiry.
  - It relies on CLI commands the owner cannot run.
- **D2-10-14** (low, high) ui.js:263, 365-369; engine.py:1260. "Kilidi doğrula" is shown on a degraded engine that has no lock, and pressing it gives an English refusal.
- **D2-10-15** (low, high) base.html:123-136; ui.js:458-461. Turning the "Paper trading" switch OFF means real money, with no inline warning next to the switch.
- **D2-10-16** (low, medium) index.html:74-76; dashboard.js chart note; alpaca_account.py. The daily-change tile says both "gerçekleşen" and "yatırma/çekmeler dahil", while the code nets out same-day flows and the chart note says the opposite.
- **D2-10-17** (low, high) Mixed English and Turkish, and one halt with five names: Kesici, Güvenlik kilidi, Kilidi doğrula, Güvenlik durdurdu, CIRCUIT_BREAKER.
- **D2-10-18** (low, medium) index.html:197, 232, 254; dashboard.js:520. Bare numbers with no unit or time window (slot counts, restart count, bps).
- **D2-10-19** (low, medium) index.html:176-229; dashboard.js:399-424; ui.js:548-553. Meter severity is colour-only and the 70%/100% thresholds are not stated, tabs have no ARIA roles, and error toasts vanish after 7 s.
- **D2-10-20** (low, medium) dashboard.js:641-646. When the tax status is missing, the card falls back to "aşılmadı" (contrary to its own comment), and "beyan gerekir" gives no deadline or next step.
- **D2-10-21** (low, medium) base.html:132-135; engine.py:731-732. The equity toggle does not say what happens to a held QLD position.

Checked fine:
- Stop, lock reset, epoch and live-switch dialogs return early on cancel.
- The arming phrase is constant-time and bound to the account.
- Missing loss limits are refused.
- The token is not persisted.
- Backtests are refused while the engine runs.
- Units are consistent in the settings drawer.
- Every engine state has a Turkish label.

## P2-10 — risk-off exit and QLD re-entry trace (orchestrator: P track complete at round 10. Decisions taken under the owner's delegation:
- **P2-10-1:** let planned SELLs through while the soft reconcile-pending latch is set. Code then matches the docs and the REDUCING state.
- **P2-10-3:** hard halts keep holding exits, as the docs say ("halted = no orders"). Make the runbook state this; no code change.)

- **P2-10-1** (medium, high mechanism) runtime.py:3922-3933, 4138-4152, ~5653-5675; docs/operations.md.
  - While the reconcile-pending latch is set (trade_feed_stale, trade_updates_restart, premarket_probe_failed), the whole session batch is parked, exits included.
  - If no clean reconcile lands within the 40-minute retry window, the flip-down exit is never sent. The docs and the REDUCING state promise exits go out.
- **P2-10-2** (medium, medium) runtime_service.py:1881-1987, 3302-3311; runtime.py:1092-1109, 4138-4152.
  - The decision path submits BUYs with no per-order submit_gate.
  - A drawdown or daily-loss latch, or an operator halt, raised after the pre-lock check (during the broker REST calls) does not stop the re-entry BUY. Startup catch-up has the same gap.
- **P2-10-3** (low, medium; policy) runtime.py:3922-3930; operations.md; operator_runbook.md:92.
  - Any non-reconciliation hard halt also holds the flip-down exit until an operator reset.
  - This matches "halted = no orders", but the runbook's ENTRY_LATCH and basis-latch texts suggest exits flow.
- **P2-10-4** (low, low) runtime_service.py:3316-3341; alpaca_account.py:395-438. PAPER re-entry sizing uses raw cash with no settlement term; non_marginable_buying_power is used only in LIVE.

Checked fine:
- Risk-off target is empty on a downtrend.
- Warming gates run before decide.
- The overlay caps only BUYs.
- Full exit is exempt from the band, gain band and deferral.
- The December window.
- The R2 early half-weight is by design.
- Entry, basis and cross-check holds give no BUY on a flat account.
- The drawdown latch is not a breaker.
- The trade-update BUY gate.
- FIFO P&L is booked before the fill insert.
- Vol-target re-entry sizing.
- Cash ≥ 2% and gross ≤ 98%.
