# Round 6+ raw findings (rolling per-track pipeline) — unverified agent output

## F6 — broker order-status edge cases
- F6-1 HIGH runtime_service.py:291,2046-2050, runtime_models.py:34-44 — a partially filled SELL that ends done_for_day is terminal but not in RESIDUAL_SELL_STATUSES {canceled, cancelled, expired} → no residual, sell phase "complete", BUYs go out with shares still held. Fix: add done_for_day to the residual set (and settle D-5).
- F6-2 medium (part of the P-1/R-2 cluster) — an unfilled DAY SELL canceled/expired at the close completes the sell phase silently: re-plan drops the SELL; with no BUYs the rebalance goes COMPLETE with no alert; only the intent watchdog notices at next open + grace. NOTE: this is the normal Alpaca end of an unfilled DAY order, so the P-1 cluster is reachable without any crash.
- F6-3 medium alpaca_reconciliation.py:48-60, runtime_models.py:34-44 — replaced/suspended/held/stopped/pending_review are "working" forever; a SELL stuck there keeps the rebalance open, every decision returns rebalance_in_progress (bot stops deciding), no stuck alert in the sell phase. Fix: follow replaced successor; age-based stuck alert for sell_pending.
- F6-4 low runtime_service.py:3068-3080 — _submit_orders skip-set is a hand-written list missing stopped/suspended/held/replaced/pending_review → re-entry resends the same client_order_id. Derive from the shared sets.
- F6-5 medium alpaca.py:431-438 — stale-ownership check treats every non-DEAD status (incl. calculated, replaced) as "still working" → overstated ownership kept.
- Confirmed: nothing emits order_expired (DD5-5).

## P6 — exception paths along decision → order
- P6-1 medium runtime_repository.py:1660-1664 vs alpaca_reconciliation.py:425-449 — owned_cost_bases swallows LotBookInconsistency and returns (), so reconciliation's fail-closed handler is dead; an unreplayable lot book passes reconcile silently.
- P6-2 medium runtime_service.py:1697-1703,2104,2624, alpaca.py:212 — "Asset is not tradable" ValueError means "reject one order" on submit but HALT THE RUNTIME on planning/residual paths (a held symbol turning untradable halts all exits); conversely parser bugs in asset_trading_rules are booked as clean rejections. Fix: typed UntradableAsset exception; skip+alert. (Wider than C-1.)
- P6-3 medium runtime.py:2766-2778 — trade-update advance swallows every exception with a log line: no halt (even for OwnershipDriftError), no alert, unbounded retries.
- P6-4 medium alpaca.py:716-734, runtime_service.py:3115-3128 — a submit error proven "never accepted" by the lookup is re-raised raw → caller marks reconciliation_required and CANCELS the untried SELL legs (which then feed the P-1 cluster).
- P6-5 low alpaca.py:458-564 — dead broker order with partial fill but null avg price → recorded "submitted", fill dropped; unguarded fromisoformat.
- P6-6 low runtime_service.py:2662-2690 — clock gate's broad except also hides parser bugs (gate dormant anyway, DC-1).
- P6-7 low — equity ≤ 0 / duplicate broker rows raise different exception types per path (= C-5).

## Q6 — test hygiene (AGENTS.md rules)
- Q6-1 medium tests/conftest.py:585-592 — the "engine threads still alive" teardown check filters out daemon threads, but every pb- thread is a daemon → the check can never fire; a test that forgets stop() leaks loops into later tests (order-dependent failures). Restore fixtures also `except: pass`.
- Q6-2 medium tests/conftest.py:380-391,560-574,39-43 — the production-path guard RECORDS violations but still performs the open/connect; failure only at teardown, after the write. config/, data/backups, data/research_runs aren't protected. Fix: raise in the guards; extend the list.
- Q6-4 medium tests/test_web_security_invariants.py:301-315 — ticket TTL 10 ms around immediate consume → flaky on a loaded runner.
- Q6-5 medium tests/test_runtime_feed_safety_2026_10_27.py:286-322 — recv() on a Unix socket with no timeout → a regression hangs the whole suite (no timeout plugin).
- Q6-3 low conftest.py:59 — CREDENTIALS_DIRECTORY not stripped; tests run on the VM shell could read real credential files.
- Q6-6..12 low — timing-bound waits and a vacuous assertion (test_web_backtest_exclusion.py); tests pinned to shipped settings.yaml values; 0.2 s absence window vs async writer; subprocess with cwd=repo; unfrozen now().date() across midnight; /tmp dir leak per test; racy global state-tree diff.

## V6 — config validation (all need an operator to set an unwise value; severity is the impact if they do)
- V6-1 high-impact config.py:309-318, execution/portfolio.py:1060 — execution.minimum_order_notional has no ceiling and silently drops SELLs/exits below it (no log, no hold record). Fix: exempt exits/risk reductions; log drops; cap the knob. (Extends F-3/H-1.)
- V6-2 high-impact config.py:385-390, web/schemas/settings.py:28-29 — drawdown halt and daily-loss limit accept up to 0.999 (web up to 99.9 %) → halt can never trip for a 2× ETF. Cap at a usable maximum in both places.
- V6-3 medium equity_leveraged_trend.py:159-160,273-274 — sma_bars: 0 loads in production = permanent buy-and-hold, no warning.
- V6-4 medium config.py:400 vs engine.py:62 — account_poll_interval_seconds unbounded vs the hard-coded 120 s watchdog gate; poll > ~300 s → systemd restart loop (each losing in-memory halts, R-1).
- V6-5 medium equity_leveraged_trend.py:90,162 — vol_target up to 1.0 effectively disables vol targeting; the code comment says so.
- V6-6..12 low — max_age vs poll interval not enforced; gain-band tiers bypass rebalance_band_pct and band has no ceiling (percent typo 0.5 = 50 %); max_positions accepts float → TypeError every decision; `alerting.email: true` silently disables e-mail and crashes LIVE doctor; december deferral sessions unbounded; retry window unbounded when watchdog off; max_position_pct below maximum_allocation caps silently.

## C6 — async blocking inventory (orchestrator spot-check: C6-1 confirmed, safety.py:118 unwrapped)

- C6-1 medium/high — web/routers/safety.py:118 `manager.reset_broker()` runs on the uvicorn loop; reset_broker takes engine.py:956 `_account_lock`, which is held across broker REST (engine.py:1057/1081) and runtime.stop() (917). Slow broker or in-progress stop freezes the whole dashboard (tens of seconds). Fix: `await asyncio.to_thread(manager.reset_broker)` (same class as P-I4).
- C6-4 medium/medium — runtime.py:3007/2957: TradingStream dispatch is serial (alpaca stream `_consume` awaits `_dispatch`), so each trade update's to_thread advance (waits on _portfolio_lock with no timeout; runtime_service.py:1416 broker.account under it) serializes all later fills. Fills are booked before the await (no loss), but booking/advance latency stacks; ~30 s per fill in a broker outage when the cache is stale. Fix: single-flight worker for advance, or bounded account refresh.
- C6-2 low — services/settings.py:354-358, engine.py:636-642/1648-1667: settings save write phase (three fsync'd atomic writes + load_config re-parse) runs on the loop. Fix with P-I4 by wrapping the whole body in to_thread.
- C6-3 low — safety.py:154 confirm_live_arming (and toggle_mode / get_settings .env+YAML reads) not offloaded, unlike sibling routes. ms-scale.
- Note: P-B2 seed() also holds _ingestion_lock, so pb-warmup-retry stalls the stock stream loop's _on_bar (stream-loop consequence not in raw-r3).

## N6 — numeric precision

- N6-1 medium/high — runtime_repository.py:1827/1830/1840, cli.py:1634-1637: `:g` formatting (6 sig. digits) in the surplus alert's copy-paste `ops rebase-ownership --broker-qty` command, incident_id and CLI prints. 1034.567891 → "1034.57"; following the alert books a wrong rebase, then a second auto FOREIGN_SELL adjustment; adjustment rows block the TL tax year; dry-run hides the gap. Fix: never `:g` quantities; use repr/`.9f` stripped.
- N6-2 low/high — alpaca_account.py:362 drift 1e-8 relative vs ownership_rebase.py:50/308 MATCH band 1e-6: a shortfall between them (e.g. 5e-4 of 1000 shares) raises OwnershipDriftError every snapshot while the resolver returns "matches"/declined → halt cleared only by manual rebase. Fix: one tolerance.
- N6-3 low/high — runtime_service.py:2948 (BUY sizing) and 3204 (exit retry) filter with rule.minimum_order_notional ($1 default) while validate_order_batch (portfolio.py:1258) uses max(policy $10, rule): a $7 scaled BUY survives sizing then is dropped as order_rejected (spurious row+alert, wrong admission reason, feeds R-4). Fix: same max() in both filters.
- N6-4 low/high — services/settings.py:222-248: percent round-trip `100*raw` vs payload float equality; retyping 1.7 (stored 0.017 → 1.7000000000000002) flags a runtime change and the save is refused while running. Fix: compare rounded fractions; round displayed value.
- Latent: full-exit float residual >1e-12 appears at ≥3000 shares (phantom adjustment row) — not reached at current ~1000-share size.

## R6 — backup, restore, migrations (orchestrator spot-check: BK-1 confirmed — risk_peak.py:381 mutates in-memory epoch/peak before save_peak_equity at 388; an OSError propagates with memory already rebased)

- BK-1 medium/high — risk_peak.py:381-388, runtime.py:5414-5418/2375-2377: failed epoch reset still applied in memory → next poll drawdown≈0, halt incident and entry latch clear although the operator saw an error; risk_state rows carry epoch N+1 so restart keeps it; DB high-water never rebased and re-imported on decision path. Fix: commit sidecar first (or roll back fields on error); DB rebase + incident clear only after success.
- BK-2 medium/high (conditional) — runtime_repository.py:1542 signed SUM vs pre-58716d01 unsigned subtraction: rollback past 2026-10-01 silently misreads split-rebase (+1) rows; no schema version gate; deployment.md:494-498 promises loud errors. Fix: max-schema-version refusal + doc caveat.
- BK-3 medium/medium — scripts/repair_local_state.py:88 trusts runtime_state=="stopped" before freshness; runtime.py starts the snapshot writer only after warmup/startup reconcile (1585), so during a restarted engine's startup the repair script runs against a live engine (closes sessions, clears incidents/breaker). Fix: "starting" heartbeat before _warmup or a lifetime fcntl lock.
- BK-4 medium/medium — alpaca_account.py:255 account_id falls back to account_number; risk_peak.py:264 mismatch only warns → peak 0 → current equity, sidecar overwritten under new key; drawdown halt can clear. Fix: fail closed when `id` missing; never overwrite a differently-keyed sidecar.
- BK-5 low — backup.py/cli.py:496-502 back up only the .sqlite3; peak sidecar not in backup; no restore command/manifest/set check.
- BK-6 low — backup.py:230 prune outside try, before copy_offhost (234); a racing stat FileNotFoundError skips off-host copy and CLI alerts "could not be written" for a verified backup.
- BK-7 low — runtime_repository.py:309-359 ALTER (autocommit) and backfill UPDATE separate, gated on column presence → crash between leaves backfill skipped forever (cumulative_filled_quantity 0).
- BK-8 low — repair_local_state.py:237-239 falls back to copy2 of main file only (WAL frames missing) and still calls it a backup.
- BK-9 low latent — risk_peak.py:193 `p.get("peak_equity", 0)` vs ledger.py:506 strict key: payload rename → silent peak 0.
- BK-10 low — backup.py:162 staging .parayabuck-stage-*.tmp left on SIGKILL/SIGTERM, not matched by prune → disk fill.
- BK-11 low — ledger.py:353-357 v6 comment describes a watermark that is never persisted.

## F7 — tax ledger vs docs/tax/turkish_tax_rules.md

- F7-1 medium/high — tax/export.py:155-157,163,189: US withholding credit cap = Turkish tax on dividends PLUS interest, not on the dividend income (doc rule 5). Test fixture tests/test_tax_ledger_2026_10_27.py:565-567 pins it: credit 80 instead of 66.30, total due understated by 13.70 TL. Fix: cap with progressive(base+div)-progressive(base); report interest tax separately; update test.
- F7-2 low/high — tax/rates.py:98-109: FX lookup checks only the file's end date; an interior gap (missing EVDS day, rates.py:220-223 skips empties) silently uses an older rate. Fix: bounded lookback or holiday list.
- F7-3 low/medium — tax/dividends.py:25: import fetches only DIV/DIVNRA/INT; capital-gain distributions / return of capital types never fetched, no completeness note (ROC would also need a cost-basis adjustment).
- F7-4 low/high — dividends.py:8/232 docstrings say payment-date rate; code (241) uses previous business day; decision record has no income FX rule.
- F7-5 low/high — export.py:272-275 ytd_status maps a missing bracket table to "rates_missing"; OSError from CSV readers escapes despite "never raises".

## Q7 — mutation-style test check (orchestrator: Q7-1 confirmed — no test references is_sell_phase_complete; code itself is correct, this is a coverage gap)

- Q7-1 medium(test gap)/medium — portfolio/runtime_rebalance.py:33 `all(...)`→`any(...)` survives: every sell-phase test uses a single SELL leg. Mutant would submit BUYs while a second SELL is still working. Add a unit test (["filled","new"] → False) and a two-SELL-leg runtime test.
- Q7-2 medium/medium — runtime_repository.py:66-68 second guard (final→terminal) removable undetected; no test applies a late fill onto a canceled/expired row (intended outcome must be decided first; ties to F6-2/P-1).
- Q7-3 low — execution/portfolio.py:563-566 daily-loss denominator and `>=` boundary survive (test comment admits either convention trips).
- Q7-4 low — portfolio.py:568 drawdown `>=`→`>` survives (no exact-5% test).
- Q7-5 low — portfolio.py:972 rebalance band `<`→`<=` survives (boundary test asserts parity only).
- Q7-6 low — portfolio.py:1007 gain-band `gap < band` boundary untested.
- Q7-7 low — live_safety.py:102 PAPER over-refusal path (PA account + AK key) untested.
- Caught cleanly: submit_buys gate, December window, 2-of-3 vote, vol-target cap, LIVE refusal, EmptyPositionsReadError guard.

## S6 — logging and error-text leaks (orchestrator: S6-1 mechanism confirmed — engine.py:281-282 reads .env values unstripped, unlike config.read_credential)

- S6-1 medium/medium — web/managers/engine.py:277-283 unstripped credential (whitespace/newline in a quoted .env value) → requests InvalidHeader text contains the secret repr → "Engine start failed: {exc}" alert (engine.py:842/890) logged raw (alerts.py:132), journaled (journal.py:280), e-mailed (runtime_start_failed in EMAIL_ALERT_KINDS), kept as last_error and in ops bundle; doctor strips so reports fine. Fix: use the stripping reader; reject whitespace; scrub key/secret from start-failure text.
- S6-2 medium/high — monitoring/diagnostic.py:218/262 "sanitized" bundle keeps broker.expected_account_id (live account number), alert e-mail addresses, SMTP host; filter (operational_event.py:86) matches only secret-ish names; docs tell operators to share it. Fix: deny-list/mask; fix docs.
- S6-3 low — web/managers/backtest.py:182-183 process-wide redirect_stderr: live-engine stderr leaks into backtest progress API.
- S6-4 low latent — websockets DEBUG would log ?ticket=; redaction filter covers only uvicorn loggers.
- S6-5 low — 422 responses echo `input` (an unknown key could carry a secret back to the token holder).

## P7 — idempotency of side effects (orchestrator: P7-3 confirmed — email_alerts.py:278 sets the throttle before enqueue at 288; queue.Full / SMTP failure still suppresses the key)

Note: the only broker write is submit_order (alpaca.py:659); there is no broker cancel/replace anywhere.
- P7-1 medium latent/medium — runtime_service.py:3066-3085: _submit_orders does not skip a row at "submitting"; it re-records (INSERT OR IGNORE keeps old qty) and re-submits the same client id with the new quantity. Protected today only by caller ordering and Alpaca's duplicate-id refusal. Fix: treat "submitting" as maybe-at-broker; assert side/symbol/qty equality on reuse.
- P7-2 medium/medium-high — runtime.py:5368-5423: epoch reset touches sidecar, high-water, incident, risk_state non-atomically with two independent epoch counters; partial failure → 500 → operator retry re-baselines at current (possibly lower) equity, hiding a newer drawdown. Fix: expected-epoch intent, one epoch number, one lock; no-op if already advanced. (Pairs with BK-1.)
- P7-3 medium/high — email_alerts.py:277-288 throttle consumed at enqueue; journal.py:354 returns True on queue put; both in memory → failed send suppresses repeats for the window, crash loses "queued" journal events, restart re-mails unchanged conditions. Fix: throttle after success; persist; synchronous write for safety events.
- P7-4 low/medium — cli.py:1570-1655 / runtime_repository.py:1879-1881: rebase CLI is another process; store.lock is per-process and no BEGIN IMMEDIATE → engine fill can land between owned read and adjustment insert.
- P7-5 low — runtime_repository.py:983 BUY admission observations keyed by uuid4, written before submit → duplicate/phantom telemetry rows.
- P7-6 low — monitoring/receipt.py:46-51 receipt first-write-wins; differing later payload silently discarded.
- P7-7 low/medium — settings save writes .env then settings.yaml; failure after the first leaves the rotated key without rollback.

## T7 — strategy math parity (live vs backtest vs archived research engine; orchestrator: T7-2 confirmed — portfolio.py:972 band skip has no target_weight==0 exemption)

Note: research/fast_engine and band_h2_combo_v1 exist only in tag archive/research/fast-engine-v1-2026-10 (ed616dc6), by design (research-artifacts-docs-only).
- T7-1 medium/high — backtest/product.py:579-596 + settings.yaml:107-108 charge ~3 bps/side; the K research gate charged 8 bps/side (4 bps × m=2). Production backtests of K look ~0.2 pp CAGR rosier than the gate. Fix: compare at cost_bps_per_side=8 / cost_multiplier=2 and state it.
- T7-2 low/medium — execution/portfolio.py:966-973: a risk-off exit to 0 is skipped when the residual is under the 0.5 pp band; research always fills exits to zero. Dust stays invested through the downtrend (latent; needs a residual). Fix: exempt target_weight==0 from the band.
- T7-3 medium-when-binding — runtime_service.py:1637-1639/2474-2476 LIVE dollar cap and gap-safe BUY cap absent from research/backtest. Intended deviation; document and surface the binding cap in the daily summary.
- T7-4 low — band/H2/gain-tier tests evaluated at the decision close in live vs fill open in research (~0.04 pp weight shift on a 2% gap).
- T7-5 low — docs/research/fast_engine_v1.md rerun command fails on main (harness only in the archive tag); document the required checkout.
- T7-6 low — live min-notional floor not in research (overlaps V6-1).
- T7-7 low/low — research QQQ input adjustment basis (split-only vs dividend-adjusted) unverified.
- Matched: trend vote, vol estimator, vol target, cap 0.98, R2, band and gain tiers, December window, decide-close/fill-open timing.

## S7 — dependency and supply chain (orchestrator: S7-1 and S7-3 confirmed — lock exported with --no-hashes; local .venv has urllib3 2.7.0 vs lock 2.8.0)

- S7-1 medium/high — requirements.lock (uv export --no-hashes), deployment.md:291/379, Dockerfile: runtime install verifies no hashes although uv.lock has sha256 for every artifact. Fix: hashed export + `pip install --require-hashes --only-binary :all:`.
- S7-2 medium/high — deployment.md:379 "skip when the lock is unchanged"; doctor never compares installed versions with the lock → a missed pin bump restarts on old deps with doctor green. Fix: doctor/ExecStartPre version check or unconditional hashed install.
- S7-4 medium/medium — deployment.md:365-375: bundle built from local main, no check that commits passed CI, no `git bundle verify`/checksum. Fix: require tip on origin/main with green CI; verify bundle.
- S7-3 low/high — local .venv drift (urllib3 2.7.0 vs 2.8.0 with GHSA-vxq7-64xx-v4gw fixed in 2.8.0; certifi, pygments, packaging, librt) plus unlocked extras; deployment.md:386-387 claim false locally. Fix: `uv sync --locked`.
- S7-5 low — pyproject.toml:2 build backend `setuptools>=68.0` unpinned, fetched at install.
- S7-6 low — Dockerfile FROM and GitHub Actions pinned by mutable tags; pip-audit unpinned in CI.
- S7-7 low/low — starlette 1.6.0 vs CVE-2026-54283/54282/48818 ranges unconfirmed; check CI pip-audit.
- Fine: lock agrees with pyproject ranges and uv.lock; only pypi sources; no CDN assets; dev deps not in runtime lock; ProtectSystem=strict.

## F8 — operator CLI behavior (orchestrator: F8-1 confirmed — tax/cli.py:120 `_database_path(args.config)` ignores --live)

- F8-1 medium/high — tax/cli.py:49-51,99-120: `tax import-activities --live` writes LIVE-account activities into the config's ledger (the PAPER ledger on the shipped config); later LIVE export omits them; reverse also possible. Fix: derive DB from credential mode or refuse on mismatch.
- F8-2 medium/high — cli.py:1631-1650 / runtime_repository.py:1645: rebase dry run doesn't show the booking kind (split rescale vs sale-like depletion); `--broker-average-cost` switches it and is undocumented; confirm re-derives from fresh state. Fix: show kind in dry run; confirm takes expected owned+kind.
- F8-3 medium/medium — cli.py:1622-1650: no engine liveness check; open-order check, owned read and insert are separate steps with a per-process lock (same root as P7-4). Fix: refuse --confirm unless engine stopped, or one BEGIN IMMEDIATE.
- F8-4 low — cli.py:50-56 `backup --retention-days` unvalidated: negative → false backup_failed alert; 0.0001 prunes every automatic backup.
- F8-5 low — relative default paths: write commands run outside the checkout create a fresh DB / halt marker and exit 0 (`parayabuck halt` from $HOME halts nothing).
- F8-6 low/medium — rebase-ownership accepts any ticker and any --account-id; a typo with zero owned adopts the symbol.
- F8-7 low — cli.py:1056/1140 `ops status` exits 0 even on CRITICAL.
- F8-8 low — `backup --verify-only` on a missing path reports CORRUPT and alerts.

## D7 — observability completeness (orchestrator: D7-1 confirmed — runtime_service.py:223-226 defaults to NO_TARGET_CHANGE; no `no_trade` reader under src/parayabuck/web)

47 stop/hold/drop reasons traced to alert(+e-mail)/journal/dashboard/ops status/runbook; full table in the agent report (kept in task output). Gaps:
- D7-1 medium/high — no-trade decisions caused by entry latch, band or min-notional suppression are reported NO_TARGET_CHANGE ("Normal" per runbook:80); portfolio_no_trade has no reader despite a comment claiming a dashboard banner.
- D7-2 medium/high — runtime_service.py:2117-2128/2074-2075 residual SELL dropped (dust/untradable) → log.warning only, sell phase marked complete, BUYs proceed.
- D7-3 medium/high — runtime.py:4800-4827 signal_adjustment_unverified latch holds ALL orders incl. exits, no transition alert, runbook calls it an entry latch.
- D7-4 medium/high — runtime.py:2470-2490 per-symbol drift lock invisible: health.py:800 reports ACTIVE while QLD is locked.
- D7-5 medium/high — runtime.py:5246-5249 four reconcile-pending latches (trade_feed_stale, premarket_probe_failed, trade_updates_restart, empty_positions_read) gate decisions with no alert/dashboard/ops/runbook surface.
- D7-6 medium/high — runtime.py:4280-4288 event_queue_stale blocks decisions; only ops status shows it.
- D7-7 low — gain-band / December holds not surfaced (intent watchdog already excuses them).
- D7-8 low — decision_deferred not e-mailed.
- D7-9 low — web/services/dashboard.py:745 reconciliation.failed equality misses reconciliation_bug_suspected / protection_failure.
- D7-10 low — engine.py:344-347 auto-start refused by halt marker → log.error only.
- D7-11 low — runbook lacks actions for paper_incident categories, exit_sell_rejected, live_rearm/authorization, data_provider_unavailable, market-scope restart-limit halt.

## C7 — long-uptime resource leaks (no material memory/handle leak found; orchestrator: C7-1 confirmed — runtime.py:3060-3095 market workers retry at 16 s forever by design (auto-recovery), alert only at count 6)

- C7-1 low-medium/high — runtime.py:3022-3094: a persistently failing market stream retries every 16 s indefinitely (~5,400 websocket handshakes and WARNING lines/day) with one alert at threshold, nothing after. Self-healing is intentional; fix the churn: grow delay to minutes after the limit and add one periodic "still failing" alert.
- C7-2 low — alpaca_reconciliation.py:162/463-465 cost-basis drift alert set never cleared → recurrence silent for the process life.
- C7-3 low — runtime_repository.py:1823/1849 `_surplus_alerts` never discarded → "once per episode" is once per process.
- C7-4 low latent — journal.py:494-498 get_journal(path≠global) builds a new journal + never-stopped writer thread per call (no current caller).
- C7-5 low — email_alerts.py:371-424 replacing the sink discards up to 64 queued alerts with no dropped count.
- Minor: performance.py:60-69 StockHistoricalDataClient per cache miss never closed.

## R7 — systemd lifecycle (orchestrator: R7-1 CONFIRMED by reading — backup.py:181 writes DELETE journal; doctor opens mode=ro (cli.py:715) so it cannot switch to WAL; cli.py:730 non-blocker FAIL sets warning; cli.py:822-824 `warning and --strict` → exit 1; unit ExecStartPre uses --strict; Restart=on-failure 10 s)

- R7-1 HIGH/high — documented restore (operations.md:472-474) installs a DELETE-journal DB; ExecStartPre `doctor --strict` fails on "journal_mode WAL" forever; the engine (which would set WAL at storage.py:79) never opens → endless restart loop after every restore. Fix: journal-mode row informational, or set WAL in the restore step/backup restore command; add a test with a backup copy.
- R7-2 medium/high — web/lifespan.py:121-140: SIGTERM during startup waits for the auto-start task without setting `_start_cancel` (engine.py:899 is the only setter) → resume and catch-up (runtime.py:1554) can submit orders during shutdown; unbounded wait.
- R7-3 medium/high — bounded stop budget ≈98 s but uvicorn has no timeout_graceful_shutdown, plus the startup wait and _account_lock (CC-7) are unbounded → SIGKILL at 120 s loses journal queue/session end; stop_failed is swallowed so the process exits as clean and Restart=on-failure doesn't fire.
- R7-4 medium/high-medium — engine.py:444-463 watchdog gate reads only the account loop; a dead/wedged pb-decisions or stream worker keeps WATCHDOG=1 (unit comment overstates).
- R7-5 low/medium — no StartLimit*, endless 10 s loop; full integrity_check in every ExecStartPre (quick_check suggested); OnFailure alert unit missing.
- R7-6 low — PARAYABUCK_ENABLE_LIVE read from sticky process env (load_dotenv override=False); removing it from .env doesn't disarm until restart.
- R7-7 low/low — tracked-secret scan fails open when git can't run as the service user (dubious ownership); doctor treats as skip.

## Q8 — web dashboard front end vs API (display-only; orchestrator: Q8-1 and Q8-3 confirmed — dashboard.js:591 renders overview account without the source guard used at :652/:653; ui.js never sends restart_engine, schema default True)

- Q8-1 medium/high — dashboard.js:71/591/653 + engine.py:1133-1143: broker-unavailable fallback (equity 0, cash 0, no positions, source "unavailable") rendered as real values; risk meter reads safe; Object.assign overwrites good values. Fix: skip when unavailable / send null.
- Q8-2 medium/high — dashboard.js:446 risk-epoch "taban" shows day_start_equity, not peak_equity the reset rebased to; contradicts the reset toast.
- Q8-3 medium/high — ui.js:475-491 save payload omits restart_engine; schemas/settings.py:34 default True → saving any runtime setting stops+restarts a trading engine with no confirm; drawer LIVE toggle always fails with a raw English error (engine.py:496-497).
- Q8-4 medium/high — dashboard.js:428/589/605/632: panel errors/unavailable silently keep last values with no stale marker.
- Q8-5 medium/medium — positions table refreshes only from the WebSocket; overview account lacks positions → freezes while WS down, disagrees with holdings cards.
- Q8-6 low — ops events/decision read failures shown as "Son olay yok" (ops.py:130-132 returns []).
- Q8-7 low/medium — 2 s status poll re-enables Start/Stop mid-request (double submit); mode chip and backtest run button same.
- Q8-8 low — donut "Nakit" = 100 − equity exposure (foreign crypto shown as cash) while the cash meter uses cash_pct.
- Q8-9 low/low — dashboard.py:725 heartbeat str(datetime) (space separator; Safari parse; string max).
- Q8-10 low — dashboard.js:330 checks `available === false`; failure payload carries `unavailable`.
- Q8-11 low — log console ignores server `time`.
- Q8-12 low — tax card says "bekliyor" for ledger/db errors.
- Q8-13 low — chart.js:104 one-argument sort comparator.

## F9 — numbers the owner reads (orchestrator: F9-1 and F9-2 confirmed — alpaca_account.py:461/468 realized = daily_pnl − intraday unrealized; no deposit/withdrawal handling anywhere in src/)

- F9-1 medium(display)/high — alpaca_account.py:461-469, dashboard.py:839-842, index.html:74-75: "Gerçekleşen" KPI is today's equity change minus intraday unrealized (includes cash flows), "Toplam" = that + lifetime unrealized. Example: sell 10 @125 bought @100 (yesterday close 120) → KPI +50 while FIFO fills/tax show +250. Fix: FIFO-realized per period from fills; label today's change separately.
- F9-2 MEDIUM-HIGH/high — alpaca_account.py:461, runtime.py:2368-2387, engine.py:1109: deposits/withdrawals are counted as P&L, daily loss and drawdown; peak never rebased. A same-day 25% withdrawal trips daily_loss_limit (0.20) and latches entries; any withdrawal inflates drawdown toward the halt until prices recover. Relevant before LIVE. Fix: subtract net external flows (activities CSD/CSW/JNLC) from day-start equity and peak.
- F9-3 medium/high — dashboard.py:392 strategy card counts external/manual positions as strategy allocation; e-mail counts only the hold symbol.
- F9-4 medium/high — tax/export.py:161/278-297: ytd_status says ok, 0 dividends, threshold "aşılmadı" when activities were never imported (the note exists only in the export).
- F9-5 medium/high — dashboard.js:134-148 period return is simple first-to-last equity → cash flows shown as performance and as "better than VOO".
- F9-6 medium/medium — daily summary "orders 2/5" mixes submitted SELLs with deferred BUYs; buy phase completion is never summarized.
- F9-7 low — buy fills store unrealized_pnl 0.0 (shown as flat).
- F9-8 low latent — weekly/monthly next-check boundary in UTC (current cadence hours:24 uses NYSE calendar).
- F9-9 low — trend card has no as-of date; stale when engine stopped.
- F9-10 low/low-medium — ops explain raw status as reason; canonicalizer maps several known reasons to UNKNOWN (overlaps D7-1).
- F9-11 low — ops incidents not account-scoped (dashboard is).

## T8 — backtest engine internals

- T8-1 medium/high — backtest/portfolio_report.py:71/394-411: yearly `return_pct`/`benchmark_pct` hold fractions (0.25 for +25%); latent (no consumer).
- T8-2 medium(initial-holdings only)/high — portfolio_engine.py:694/722 vs 619/657: initial holdings valued at first OPEN, curve and benchmark start at first CLOSE → first-day move leaks into total_return/excess_return (~3 pp on a +3% day); paper_overlay disagrees. Large for short PAPER-comparison windows.
- T8-3 medium/high — portfolio_engine.py:1116 vs 1061: the 2% cash buffer is applied only when a BUY is trimmed → fills non-monotonic in order size (9,500 fills leaving 500 cash; 10,100 trimmed leaving 2,000); live enforces the buffer always (runtime_service.py:2871).
- T8-4 low latent — pre-start dividends credited to initial holdings (portfolio_engine.py:575/604-606/806-810).
- T8-5 low/medium — dividends credited gross (no withholding); default runs price-only.
- T8-6 low latent — raw-bar SPLIT path adjusts quantity but not price/volume history (product runs reject SPLIT).
- T8-7 low — last-bar decision recorded as an unfilled rebalance; rebalance_count +1.
- T8-8 low — dividends excluded from realized PnL / win rate / attribution.
- T8-9 low/medium — paper_overlay tracking error ignores date gaps (√gap inflation).
- T8-10 low — turnover two-sided unlabeled; Sharpe rf=0 while cash earns interest.

## P8 — dead-code removal list, re-verified (batch-fix ready)

- DEAD, no test change (~114 lines): P8-1 runtime.py last_successful_account_iteration_at (409-411, 2241, 2272, 2311-2312); P8-2 data/alpaca.py provider_* write-only counters; P8-4 equity_leveraged_trend.py:120 cadence_label; P8-5 web/application.py _default_state + state=None arm; P8-6 email_alerts.py:363-371 flush(); P8-7 tax/ledger.py:82-90 LotDisposal.to_dict; P8-8 incident.py:192-199 IncidentRecord.to_dict (medium); P8-14 require_product_universe(allow_static_context=True) no-op calls; P8-19 shadow_comparator as_of path; P8-20 ui.js dialog.alert; P8-21 sip_quote_fallback_active alias; P8-22 execution/__init__ AlpacaBroker re-export; P8-23 intent_watchdog nyse_sessions re-export; P8-24 engine.py _check_approval_signature unused tuple values; P8-25 alpaca.py:330-331; P8-26 _cache_covers_window min_pct; P8-27 runtime_service.py:225-226 elif; P8-28 paper_incident.py:564-565; P8-29 IncidentSpec.recovery_condition.
- DUPLICATES to merge (~32): P8-30 _bot_tradable_symbols (alpaca_account/alpaca_reconciliation); P8-31 runtime_service.py:2545-2554 vs 2582-2591; P8-32 live_safety.py:252-256 vs 390-394; P8-33 engine.py:1367-1374 vs live_safety.py:294-303. P8-34 alpaca.py E402 import move.
- DEAD but needs test edits (~69): P8-3 signal_last_verified_at; P8-10 OperationalEvent.from_dict; P8-11 drain_for_test; P8-12 put_drawdown_liquidation_state (tripwire test); P8-13 calendar_degraded_reason (DN-1 decision open); P8-15/16/17 unused params; P8-18 verify_account_environment(account_id=); P8-41 configure_alerting throttle_seconds.
- NOT dead (earlier claims wrong): P8-35 risk_peak _peak_* fields (DF-5..8); P8-36 close(status); P8-37 record_rejection(side); P8-40 never-emitted but documented alert kinds (missing behavior, not dead).
- Owner decision: P8-9 execution_measurement (209 + its test file), P8-38 requires_intermediate_risk_rebalance hook, P8-39 market-clock OPG branch, lifetimes.py.
- Total verified safe ≈ 215 lines (§4 estimate of ~300 not fully reproduced).

## R8 — fresh-host / disaster-recovery bootstrap (orchestrator: R8-1 premise confirmed — unit ReadWritePaths lists /opt/parayabuck/reports, nothing tracked creates it; R8-7 confirmed at runtime.py:3598-3601)

- R8-1 medium/medium — deploy/systemd/parayabuck.service:42 ReadWritePaths includes reports/ which no install step creates; systemd fails namespace setup (226/NAMESPACE) before ExecStartPre → loop on a fresh host. Fix: `install -d` in First install or `-` prefix. Verify with systemd-analyze on the VM.
- R8-2 medium/medium-high — deployment.md:286-288 `useradd --create-home` fills /opt/parayabuck from skel → `git clone` into non-empty dir fails; ~/pb.bundle unreadable by the service user.
- R8-3 medium/high — backup timer enabled before any DB exists → first run backup_failed alert + failed unit.
- R8-4 medium — encrypted .cred files are host-bound; DR docs don't say to re-encrypt from the secret store; rollback assumes plaintext keys still in .env.
- R8-5 medium/high — risk_peak ensure_reconciled: with neither sidecar nor high-water the baseline silently becomes first observed equity (post-loss), no alert/epoch record; operations.md:435 says "bootstraps at 0".
- R8-6 low — deployment.md:293-294 runs doctor right after creating an empty .env.
- R8-7 low/medium — runtime.py:3598-3601 session-close batch during warmup dropped (return True, debug log); only startup catch-up can recover.

## Q9 — web endpoint server contract (30 routes inventoried; every non-GET is token+peer+Origin gated)

- Q9-1 medium/medium — routers/safety.py:99/115-118: POST /api/mode checks no-active-runtime outside the lock; a concurrent start (engine.py:700/739, never takes settings_lock) can load the old mode → LIVE runtime while UI shows PAPER (or reverse). Fix: re-check under lock; refuse while starting/stopping.
- Q9-2 low-medium/medium (design) — engine.py:1046-1054: risk-epoch reset not gated on a drawdown incident (unlike reset-safety-lock); any token holder can rebase the peak mid-drawdown and extend the real loss before the halt. Documented as routine after cash flows (live_readiness_v1.md:141); at least log pre-reset peak/drawdown, or require a reason. (Agent example used 10%; shipped limit is 0.75.)
- Q9-4 medium/high — web/services/ops.py:130-132/158-160 events and incidents swallow exceptions → 200 empty lists ("no incidents" while one is active). (UI side is Q8-6.)
- Q9-5 = P-I4 (services/settings.py:359 reset_broker on loop) — duplicate; fix with C6-1.
- Q9-3 low — GET /api/live/prerequisites does a LIVE broker account call on each drawer open and reflects probe exception text (S6-1 surface).
- Q9-6 low — internal failures return 200 with raw str(exc) (ops status, dashboard panels).
- Q9-7 low — /api/engine/start maps every failure to 400 with raw text.
- Q9-8 low — non-ASCII arming phrase → compare_digest TypeError → plain 500.
- Q9-9 low — unbounded str fields (api_key, secret, phrase, ops filters), live_dollar_cap no upper bound, no body-size limit.
- Q9-10 low/low — reset-safety-lock / risk-epoch use the runtime handle outside the lock (race with stop); manual start admitted inside the settings stop→write window.

## S8 — PAPER→LIVE guard chain (10 paths enumerated; one EngineRuntime construction engine.py:775, one order submit alpaca.py:659; orchestrator: S8-1 confirmed — engine.py:818/861 discard runtime.stop()'s False)

- S8-1 medium/medium — web/managers/engine.py:818/861/876-879 + runtime.py:1900-1903: a failed/interrupted start whose stop() returns False (a worker still blocked in a broker call) drops the runtime reference; state "error"/"stopped" → mode flip, saves and a second start allowed while the first runtime's workers (and storage) are alive and can still reach _submit_orders. Fix: on False keep self.runtime and set stop_failed.
- S8-2 medium/medium — same race as Q9-1 (start reads config before a concurrent PAPER write) — merge with Q9-1/L-2.
- S8-3 low/high — engine.py:1068-1075: dashboard/status polls build a LIVE-endpoint client with no arm/host/environment/pin check (read-only exposure).
- S8-4 low — runtime.py:1345-1391: PAPER pin/environment check runs after reconcile has already bound and written ledger state for the (possibly foreign) account; LIVE path checks first.
- S8-5 low — operator halt marker is per config directory; a PAPER halt doesn't hold a LIVE start after a mode switch (documented split).
- S8-6 low — doctor-only LIVE gates (off-host e-mail required, owner-only credential file) skipped on manual/retry start paths.
- S8-7 low — standing-authorization fingerprint over the whole config → unrelated app/alerting edit voids unattended LIVE start (extends M-8).

## D8 — broker/data response robustness

- D8-1 = G-1 (duplicate, adds consequence): alpaca_reconciliation.py:506-507/572-579 — under flat_broker_reconcile_scope one empty positions read reaches flat_reconciler → durable stale_ownership_clear of QLD → next decision buys the full target again (~double exposure); also at startup reconcile. Confirmed by reading.
- D8-3 = BF-1 (duplicate; consequence: missing lot → LotBookInconsistency swallowed by P6-1).
- D8-2 high-impact/low-likelihood, high — alpaca_client.py:24/159 strict `TypeAdapter(list[Order])` + SDK strict enums: one unknown order status/enum value makes every open-order and per-order read raise (fill events dropped inside SDK `_cast` before our handler; reconcile fails; pre-submit guard alpaca_orders.py:91 raises) → rebalance open and entries latched until SDK upgrade, no specific alert; our "unknown status" branches are unreachable. Fix: parse raw dicts, map unknown → explicit unknown; catch stream cast failures → reconcile + alert.
- D8-4 medium/high — SDK websocket loops retry auth/connect failures with ~10 ms (trading) / sleep(0) (data) and leak sockets; no project supervisor/backoff. Fix: supervise with backoff; halt+alert after N auth failures.
- D8-5 medium/high — alpaca_account.py:264-266 missing last_equity → previous = equity → daily_pnl 0 → daily-loss breaker can never trip. Fix: persisted day-start equity or refuse entries.
- D8-6 low/medium — account `status` never read (only block flags) → DISABLED/ACTION_REQUIRED account yields repeated rejections, no halt.
- D8-7 low — one malformed stream bar aborts the rest of its frame (SDK logs only).
- D8-8 low — alpaca.py:673-725 recovered FILLED order after a submit error returned without booking the fill (reconcile repairs later).
- D8-9 low — alpaca.py:221 reads `min_order_notional`, a field the pinned Asset model doesn't have → always $1 (root of N6-3).
- D8-10 low/medium — foreign position with missing mark valued 0 in BUY gross headroom.
- D8-11 low latent — pagination end by short page, no cursor check; row timestamp parse outside per-row try.
- D8-12 low — one unparseable field on a foreign position refuses the whole snapshot → halt after N polls blocks QLD exits.
- D8-13 low — tax/dividends.py `_number` accepts "nan"/"inf".

## C8 — lock ordering (NO lock cycle found; one global order: reconcile → risk → state → store; ingestion → portfolio; portfolio → state/store/peak/account_snapshot. Orchestrator: C8-1 confirmed — runtime.py:1017 takes _portfolio_lock every pass when the retry hook exists, without a pending-retry pre-check)

- C8-1 medium/medium — runtime.py:1016-1020/2271/2314 + engine.py:62/457: account loop blocks on _portfolio_lock each pass; a decision/advance holding it across slow broker REST >120 s stops last_account_loop_pass_at → watchdog pings stop → systemd kill at 300 s, possibly between submits. Fix: lock-free pending check first, or timed acquire; keep the watchdog stamp independent.
- C8-2 medium/medium — runtime.py:5742-5743 `with self._ingestion_lock, self._portfolio_lock:` in warmup → stock stream _on_bar blocked for the full portfolio hold (extends P-B2 note). Fix: drop ingestion lock there or swap order.
- C8-3 low — runtime.py:748-749 capacity_health takes _portfolio_lock for one attribute despite the comment; dashboard/receipt stall.
- C8-4 low/medium — storage.py:82/123 store.lock held across 5 s SQLite busy waits; an external writer (CLI rebase/backup/repair) serializes all in-process DB users; trade-update loop stalls on has_order.
- C8-5 low/medium — web /api/account poll writes the broker-level _snapshot_cache that the fill path reuses by age only (outside the generation fence).

## Verification B (independent refutation pass on raw-r6 claims)

- CONFIRMED: C6-4 (latency only), T8-2 (backtest reporting), T8-3 (backtest only), R7-2 (top real-world item: orders can be submitted during shutdown), R7-3 (low likelihood), R7-4 (unit comment does say "account loop" — my "overstates" note was wrong).
- PARTIAL:
  - D7-3 — exit hold is intentional; docs stale (runbook ENTRY_LATCH, taxonomy "sells flow") → doc fix.
  - D7-4 — per_symbol_drift_lock default False and commented out in settings.yaml → unreachable shipped; alert does fire at lock.
  - D7-5 — premarket_probe_failed does alert (runtime.py:5290-5297); other three latch silently; ops shows REDUCING.
  - D7-6 — batch deferred/re-queued, not dropped; health check DEGRADED + portfolio_no_trade exist.
  - P7-1 — latent; stale submitting rows resolved first, fail-closed.
  - P7-2 — needs storage failure + retry.
  - Q9-1 — narrow race.
  - S6-2 — shipped config has expected_account_id null, e-mail empty → nothing leaks until enabled.
  - R8-5 — documented, needs sidecar and DB peak both lost.

## T9 — live market-data handling before the strategy

- T9-1 medium/high-medium — data/alpaca.py:644-736 + runtime.py:5735-5746: warmup seeds provider histories with missing sessions or dropped invalid rows (completeness only picks replace vs upsert); the decision-time verifier can't see a hole that was never stored → SMA/TSM/Donchian/vol read one session further back for ~274 sessions; trend flips near SMA possible. Fix: don't seed incomplete windows; check consecutive NYSE sessions before readiness.
- T9-2 = D8-7 (duplicate; consequence: losing the 15:59 minute delays the decision via SIP fallback; mid-session loss silently wrong high/low/volume).
- T9-3 low — core/models.py:34-42 allows zero/negative prices; a non-positive last close reads as downtrend → full exit if the cross-check is unavailable.
- T9-4 low/medium — runtime.py:3163-3170 a minute correction after the daily bucket emitted is dropped silently although the decision hasn't run (comment wrong).
- T9-5 low (documented design) — decision close is the IEX stream bar; SIP holds only on trend mismatch; a cross-check exception falls back to IEX with a warning log only.
- T9-6 low/medium — during SIP→IEX downgrade the hourly verifier adopts IEX daily closes as authoritative for the whole window.

## Verification A (independent refutation pass on raw-r5/r6 claims)

- CONFIRMED:
  - CC-6 — narrow; concurrent ownership-drift halt.
  - P6-2 — wider than stated and the same root as FINDINGS C-1: runtime_service.py:2506 fetches rules for the whole universe → one untradable universe symbol stalls the BUY phase → persistent portfolio_runtime_failure halt.
  - P6-4 — a 404 lookup re-raises the raw submit error → untried legs canceled → feeds P-1.
  - BK-3 — narrow.
- PARTIAL:
  - P-1 — real and also hit by a normal DAY SELL canceled/expired at the close; harm is BUY-before-SELL and a one-session exit delay; exposure bounded because BUY sizing counts the held position.
  - R-1 — window ≈15 s on a healthy broker; needs a second fault plus a restart; stop() writes no incident.
  - F6-1 — code fact true; whether Alpaca emits done_for_day for a partial DAY SELL is unverified.
  - F6-3 — not silent (health "stuck rebalance >1h"); decision freeze, not fail-open.
  - V6-1, V6-2 — not reachable with shipped values (10 / 0.20 / 0.75).
  - BK-2 — conditional on a rollback past 2026-10-01 with +1 rows.
  - BK-4 — the DB peak under the new key is still used; sidecar not overwritten when both are missing.
- Suggested fix order: C-1/P6-2 → P-1/P6-4/C-2 cluster → R-1 → BK-3 → CC-6.

## F10 — corporate actions end to end (orchestrator: F10-2/F10-3/F10-4 mechanics confirmed at ownership_rebase.py:134-138 and 317-341)

- F10-1 HIGH/high-medium — ownership_rebase.py:307: auto split rebase keyed on the total broker/owned ratio; any fill booked between the split and the first account pass (e.g. DAY BUY 100 at 09:30:02 → 500 vs 900, ratio 1.8) disables it → surplus alert → QUANTITY_MISMATCH → reconciliation_failed halt; post-split SELL books realized 0. Fix: compute the ratio against pre-split owned (exclude fills at/after the split time).
- F10-2 HIGH/high — ownership_rebase.py:134-138: every positive ADJUST (incl. operator_rebase) rescales all open lots as a split → adopting a foreign buy (400@100 + 200@60) books 600@66.67 (true cost 52,000 vs 40,000); with F10-1 the post-split lot is rescaled too. The documented recovery corrupts cost basis. Fix: operator chooses split vs acquisition (with unit cost); never rescale lots after the split time.
- F10-3 HIGH/high — ownership_rebase.py:134-136 `if quantity <= 1e-12: continue`: zero-owned adoption (documented CLI recovery) adds nothing to the lot book → later sells book realized 0 (LotBookInconsistency swallowed), cost basis unknown, replay disagrees forever. Fix: seed a lot with operator unit cost or refuse.
- F10-4 HIGH/high-medium — ownership_rebase.py:317-341: any reduction without an exact standard ratio + cost corroboration (e.g. reverse split with cash-in-lieu, 197.6 → 98) is auto-booked as FOREIGN_SELL (rebasable) → lots depleted pro rata, remaining shares keep ~half cost, next sale doubles the gain; no halt though runbook:240 promises one. Fix: fractional-share tolerance on ratio match; halt near-standard-but-inexact reductions.
- F10-5 medium/high — tax/ledger.py:135-155: any ownership adjustment row blocks the tax report for ALL years (incl. pre-split); no documented procedure to clear.
- F10-6 low — split/foreign rows only in /api/status JSON; docs say dashboard shows them.
- F10-7 low/low — owned_quantities raises RuntimeError on negative net (broker-resized pre-split SELL), blocking every recovery path.
- F10-8 low/low — ticker rename not modelled (old ticker stale-cleared, new one foreign; halts first).
- Happy path (no fill before the first pass) works automatically except the tax block (F10-5) and dashboard visibility (F10-6).

## P9 — two-phase rebalance state machine (full transition table in agent report; orchestrator: P9-1 confirmed — runtime_service.py:1345-1410 returns rebalance_in_progress before any planning, and the withheld BUY later uses the old decision's risk_json at 2436)

- P9-1 HIGH/high-medium — while a rebalance is open (SP/BR/BP), every next-session decision is dropped before planning (exits included) and previous_timestamp consumed; a BUY side withheld by an entry latch/REDUCING is later submitted with the OLD target when the latch clears (or by operator reset runtime.py:5537, possibly days late). Also: reconciliation-booked SELL fills never call advance. Fix: at a session boundary close the older rebalance's unsubmitted BUY side as closed_unresolved and plan the new target; never submit BUYs from a decision older than the current session; advance on reconcile-driven terminal transitions; restore previous_timestamp (extends P-2).
- P9-2 medium/medium-low — BLOCKED rebalance whose target can't be parsed never closes → rebalance_blocked + entry halt every session, survives restart; one alert per process. Fix: close as closed_unresolved after alert; operator close command.
- P9-3 low latent — rebalance_waiting_for_slot never exits when the re-plan needs a SELL (SELL filtered out; part of C-2).
- P9-4 low defensive — update_rebalance_execution has no from-phase guard / rowcount check.

## S9 — filesystem safety (all low; atomic_write core is sound: same-dir O_EXCL 0600 temp, fsync, replace, dir fsync)

- S9-1 low/high — utils/io.py:35-58: atomic_write can raise AFTER os.replace (chmod / dir fsync EIO) → /api/mode returns 500 though settings.yaml already says live; same for settings save. Fix: rename is the commit point; log post-rename failures.
- S9-2 low — receipt.py:46 os.link without directory fsync → receipt lost on power loss (audit only).
- S9-3 low — tax/export.py:225-229, tax/rates.py:254-260 fixed .tmp names, follow symlinks, no fsync/cleanup → use atomic_write.
- S9-4 low — backtest report/CSVs, paper incident fixture, diagnostic zip written in place → partial files look valid.
- S9-5 low/medium — os.replace replaces a symlinked managed file (settings.yaml/.env) with a regular file → silent divergence from the managed copy.
- S9-6 low/low — halt marker / sidecar read without regular-file/size check; forensics scans up to 2000 receipts per lookup.
- S9-7 low — orphan temp files (.name.XXXX, fixed .settings.yaml.validate) after SIGKILL; no sweep.
- S9-8 low/low — CLI-created DB takes caller umask/owner; runbook rebase command lacks `sudo -u parayabuck`.

## R9 — runbook procedures vs code (orchestrator: R9-1 confirmed — no breaker/runtime_state check in monitoring/diagnostic.py; R9-3 confirmed at runtime.py:2567-2580; R9-7 confirmed — runtime.py:1436 passes drawdown_liquidation)

- R9-1 HIGH/high — diagnostic.py:345-467 / cli.py:1186-1194: `ops doctor` (documented "exit 0 ok, 1 degraded, 2 critical") has no circuit-breaker/runtime-state check → "Overall: OK", exit 0 while halted.
- R9-2 HIGH/high — runtime.py:3602-3613 (extends B-1): any hard halt drops the close decision without re-queue, but runbook RB:154-157/228-230/303-305 promises "a reset inside the window still lands the session"; reset catch-up re-runs only startup-blocked runtimes → wanted exit never submitted; only a process restart recovers.
- R9-3 HIGH/medium — runtime.py:2567-2580/4991/5516-5549: a second halt while another is active is neither kept nor persisted; resetting the first sets state running → decisions resume with the second fault unverified.
- R9-4 medium/high — runtime.py:5062-5064 drift reconcile refreshes last_reconciliation_at → reconciliation health OK while the drift halt persists.
- R9-5 medium/high — `ops explain latest` shows the previous session when today's decision was never persisted (cannot diagnose equity_decision_missing; NO_TARGET_CHANGE "Normal").
- R9-6 medium/medium — runtime.py:5500-5505 refused reset already cleared the reconciliation incident rows and breaker.
- R9-7 medium/high — runbook RB:355-358 says drawdown_liquidation is not wired; it is (runtime.py:1436) → enabling it sells all managed targets at the threshold.
- R9-8 medium/medium — LIVE→PAPER switch has no managed-LIVE-position check; LIVE QLD left unmanaged.
- R9-9 medium/medium — after a drift halt in pb-decisions the dashboard reset is refused until restart (dead shared worker); docs say reset from dashboard.
- R9-10 low — doc mismatches: ops status lacks reconciliation_anomalies; "Auto-start blocked" string absent; SMTP credential source; e-mail kinds list; over-target tolerance; dead-man statement.
- R9-11 low — dashboard engine stop isn't sticky: `systemctl restart` (deploy step) auto-starts it.

## D9 — config defaults vs shipped drift (no shipped value contradicts a documented safety rule)

- D9-1 medium/high latent — execution/portfolio.py:410-416/516 PortfolioRiskLimits defaults are pre-VT35 (0.5% daily latch, 60% gross, 6 positions) vs shipped 0.20/0.98/8; any caller omitting limits gets them and a different digest. No production path omits today. Fix: derive from RiskLimits() or make required.
- D9-2 = R9-7 (drawdown brake is wired; runbook and settings.yaml comment say pending).
- D9-3 medium/high — runbook:537-538 says PAPER start isn't account-pinned; it is (runtime.py:1388-1395).
- D9-4 low latent — PortfolioExecutionPolicy default drops the A1u gain band and H2 December deferral.
- D9-5 low — PortfolioBacktestEngine fee defaults 0 (direct construction only).
- D9-6 low — health.py:100 heartbeat tolerance 90 s fixed; snapshot_interval_seconds 120 accepted → permanent CRITICAL.
- D9-7 low latent — web schema/ui.js fallbacks 1.5%/10% vs shipped 20%/75%; a save with a missing key would tighten the latches.
- D9-8 low — ops doctor/bundle ignore CONFIG_PATH.
- D9-9 low — runbook:332 says latch "never sells" (it does sell below current).
- D9-10 low — mirror constants (60 s reconcile, heartbeat TTL, SIP embargo, watchdog, journal limits) currently equal.
- D9-11 low — README config section list and live_readiness skip rule incomplete.
- D9-12 low — data_finalization_grace_seconds 2 never used below the hardcoded 30 s close-flush floor; taxonomy quotes 2 s.

## C9 — unlocked shared mutable state (most shared state is properly locked; gaps are gate-before-lock races, same family as CC-6)

- C9-1 medium/medium — runtime.py:1006-1018 + runtime_service.py:3137: rejected-SELL retry checks breaker/warmup before _portfolio_lock and never re-checks → a SELL can go out after an ownership-drift halt raised while it waited. Fix: re-check under the lock (with CC-6).
- C9-2 medium/medium — runtime.py:3749-3829: fresh-decision entry latch and breaker read before the lease fetch and lock; lease validates only the snapshot → BUYs sized with a latch/halt raised in between. Fix: read gates under the lock or add a gate generation to the lease.
- C9-3 medium/low — runtime.py:5593-5609 _verify_market_halts clears with an unconditional write after unlocked verification → a new restart-limit halt raised in between is erased. Fix: compare-and-clear.
- C9-4 low/medium — alpaca_account.py:432-433 drift_locked_symbols is a broker-wide side attribute written by every account() caller (only with per_symbol_drift_lock on, default off).
- C9-5 low/low — warmup-retry backoff counter written under two locks.
- C9-6 low/medium — periodic reconcile clears incident rows outside _risk_state_lock → row resurrected by a concurrent _persist_risk_state (fail-closed: restart halts).
