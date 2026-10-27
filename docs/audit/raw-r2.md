# Round 2 raw findings (dead code / needless complexity) — unverified agent output

## Slice N — data, tax, markets, core, utils, misc (backup/storage/rates/dividends/export only name-scanned)
- DN-1 high markets/nyse_calendar.py:45 — calendar_degraded_reason() tests-only; docstring (12-15) promises a health surface that doesn't read it. Remove or wire into health.
- DN-3/4 high data/alpaca.py:175-177,292-296,731-735 — provider_calls/symbols_requested/bars_returned are write-only counters wrapped in pointless try/except. Remove (fix runtime.py:4483 docstring; tests touch them).
- DN-5 high data/alpaca.py (~12 sites) — getattr(self, "_x", default) on attributes __init__ always sets.
- DN-6/7/8 high-medium data/alpaca.py:436-438, 330-331, 229-231 — unreachable branches (configured feed always set; empty-symbols return; minutes None).
- DN-9/10 medium data/alpaca.py:251-261, 110-115 — duplicated _last_batch_degraded assignment; local re-import + unreachable try/except.
- DN-11 medium data/alpaca.py:754-760 — min_pct param never passed.
- DN-12 medium data/alpaca.py:189-191 + runtime.py:1254 — sip_quote_fallback_active alias always equals sip_fallback_active.
- DN-13 low data/alpaca_stream.py:65-67 — getattr for subscribe_updated_bars (check pinned SDK).
- DN-14 medium data/cache.py:89,134,176,187-194 — default/None branches of WarmupCache unused in production.
- DN-15 low data/aggregator.py:34-35,78-79 — minutes==1 pass-through likely unreachable.
- DN-16 high tax/ledger.py:82-89 — LotDisposal.to_dict() has no caller.
- DN-17 high runtime_events.py:33-36 — drain_for_test() test-only seam in production class.
- DN-18 medium runtime.py:1839-1840 — hasattr guards always true.
- DN-20/21 low backup.py:364 now=, tax/rates.py:47-50 env= — params never passed in production (test seams?).
- Crypto leftovers: Market.CRYPTO, symbols.py crypto bases (TRUMP, HYPE…), is_crypto() — still LIVE for classifying foreign inventory; not removable without product decision. config.py:1051-1052 and web/managers/engine.py:568-569 still list minimum/maximum_crypto_order_notional settings — check if read anywhere.
- Estimate ~70-80 removable lines.

## Slice D — portfolio (not runtime_service), risk, universe, risk_peak
- DD-1 high runtime_repository.py:751-754 — put_drawdown_liquidation_state no production caller (a test monkeypatches it as tripwire).
- DD-2 high equity_leveraged_trend.py:120 — cadence_label never read.
- DD-3/4 high execution/risk_peak.py:170,206,216-217 — unreachable/tautological guards.
- DD-5 high portfolio/base.py:197 — requires_intermediate_risk_rebalance never overridden in production → non-scheduled decision True-side never fires (runtime_service.py:1449,1496; portfolio_engine.py:1235,1310). Retire hook or rename arg _context.
- DD-6 high portfolio/base.py:75-76 — require_product_universe(allow_static_context=True) is a no-op; calls at runtime_service.py:1489, portfolio_engine.py:1260.
- DD-7 high risk_peak.py:32-224,365-390 — try/except that only re-raises KeyboardInterrupt/SystemExit (no-op boilerplate).
- DD-8 medium portfolio/universes.py:1 vs universe/equity.py:15 — two equity universe version constants with contradicting names (keep values; fix naming).
- DD-11/12/13 low — stale docstrings: risk/__init__.py, runtime_rebalance.py:11, portfolio_engine.py:1241-1247.
- Cross-refs: getattr fallbacks for methods that always exist — runtime.py:1130,1136,1142; runtime_service.py:2857,3001.
- Estimate ~25 lines (+~10 if the hook is retired).

## Slice K — monitoring
- DK-1 high intent_watchdog.py:120 — equity_decision_time(session, close) just returns close; inline.
- DK-2 high intent_watchdog.py:48,375 — nyse_sessions imported only for __all__.
- DK-3 medium intent_watchdog.py:153,239 — market param always EQUITY.
- DK-4 high operational_event.py:70-93 — _REDACTED_KEYS covered by _SENSITIVE_SUBSTRINGS except "authorization"; merge.
- DK-5 medium operational_event.py:58-66 vs events.py:81 — two WARNING_KINDS sets; merge.
- DK-6/7 high/medium email_alerts.py:363,379 — flush()/stats() tests-only.
- DK-8 high alerts.py:45 — configure_alerting throttle_seconds override tests-only.
- DK-9 high events.py:39 — alert kind "paper_receipt" never emitted.
- DK-10 high events.py:61,99 — "reconciliation_unexpected_fill_side" never emitted, and the documented skip-and-report behaviour wasn't found → possibly MISSING behaviour, not dead code. Check execution/.
- DK-11 high incident.py:16-161 — DUPLICATE_DECISION, DUPLICATE_ORDER_RISK, CLOCK_OR_SESSION_MISMATCH, RESTART_RECOVERY_FAILED never raised (~40 lines).
- DK-12 medium-high paper_incident.py:521-540 — SIGNAL_HISTORY_UNVERIFIED, ORDER_DRIFT, STATE_RESTORE_FAILED never raised (7 of 12 categories unused overall).
- DK-13 medium incident.py:106 — IncidentSpec.recovery_condition never read (doc-only).
- DK-14 medium incident.py:192 — IncidentRecord.to_dict no caller.
- DK-15 high paper_incident.py:548-568 — unreachable PROVIDER critical arm; DB-integrity arm duplicates else.
- DK-16 high operational_event.py:215 — from_dict tests-only.
- DK-17 keep snapshot.py:95 read_snapshot (used by scripts/repair_local_state.py).
- DK-18/19/20 low — getattr on settings fields; redundant alert_delivery_failed guard; metrics DEFAULT_DATABASE_PATH alias.
- Estimate ~150 lines.

## Slice B — runtime.py 3017–end (no unused methods; waste is guards + duplication)
- DB-1/2 high runtime.py:3338 `now`, 5649 `market` — unused params (ruff confirmed); remove with call-site args (3331; 880, 902, 5645).
- DB-3 keep runtime.py:4737,5251,5301 `now=` — test clock seams.
- DB-4/5/6/7 high-medium runtime.py:3700,3740 (else arm unreachable), 3697-3732 (hasattr market.value), 5020-5024 (reconcile_failure_threshold getattr+fallback), 3447-3451 (retry settings getattr with duplicated defaults).
- DB-8 high runtime.py:3351,4650,4790 — signal_cross_check config read via the same getattr+isinstance block 3×.
- DB-9 high runtime.py:3518-3521,4691-4694,4751 — _deferred_portfolio_batches not in __init__, created lazily in 2 places. (Ties to A-2-style missing-init bugs.)
- DB-10/11/12 high-medium — duplicated expired-retry alert block (3499-3515 vs 4764-4781); duplicated relevant-bars comprehension (3623/3646); duplicated reason expression (3616 vs 4221); duplicated real-gate check (3241, 3661).
- DB-13 medium runtime.py:3666-3670 — `except TypeError: gate(market)` re-runs the real gate on any internal TypeError (same pattern as A-1; less dangerous but same smell).
- DB-14/15/16 medium-high — guards for PortfolioRuntime methods that always exist (3997-4005, 4400-4401, 4240-4243).
- DB-17 high (big) — ~90 getattr/hasattr guards on attributes __init__ always sets; 44 test files build EngineRuntime via __new__ and depend on them. Cleanup needs a shared test factory first.
- DB-18/19/20 stale comments — 3655-3659 (says stale snapshot blocks everything, code runs sell-only), 3744-3748 orphaned fragment, 4987-4988.
- DB-21..26 low — local datetime re-imports (3689, 4183); duplicated lease-invalidated defer (3797/3816); unreachable `if not bars` (3490, 4759); 3 near-identical automate_incident_recovery calls (3881-3919); reconcile+breaker triplet repeated 3× (5040, 5392, 5487); dead initializer 4575.
- Estimate ~80-110 lines.

## Slice C — runtime_service.py
- DC-1 high runtime_service.py:361-382,2662-2715 — market_clock_refusal only acts on "opg", but production only sends "day" (alpaca_orders.py:100) → gate never refuses, still costs one broker.clock() call per batch. (My own earlier edit removed the DAY branch on purpose.) Owner call: remove or keep documented as dormant.
- DC-2 medium-high 608,1185 — signal_last_verified_at write-only (one test reads it).
- DC-3 medium-high 1411-1414 — orphaned comment fragment with a false claim.
- DC-4 high 486-487 — commented "Example:" line (ERA001); reword.
- DC-5 high 223-226 — elif implied by the if; unreachable.
- DC-6 medium-high 194-220 — dead dict branches + no-op fallback loop in side counting.
- DC-7 high 1490-1495 — both arms call require_product_universe(context); merge.
- DC-8 medium 1488-1489,569,589 — allow_static_universe test-only seam (16 test files).
- DC-9 medium-high ~25 sites — getattr(self.strategy, "name"/"timeframe"/"decision_symbols") on required attributes.
- DC-10 medium-high 1458-1460 — hasattr(policy, "next_boundary_after") always True.
- DC-11 medium 2329,2674,2821,2857 — broker getattr fallbacks only for fakes; declare clock/resolve_portfolio_submission on the Protocol.
- DC-12/13 medium-low — lazy-init getattr on attributes set in __init__ (1133,1173,2275,2892 duplicates the 0.03 default at 558, 141-144, 634, 2889, 3001).
- DC-14 medium-high — warming_up/duplicate_decision no-trade emission duplicated (946/1291, 1008/1331); 19 _emit_no_trade_safe sites repeat the same fields (~80-100 lines).
- DC-15 high 2545-2555 vs 2582-2592 — identical buy-phase completion block.
- DC-16 medium — repeated submit_deferred_buys gate (1944,1977,2007) and SELL_PHASE_PENDING summary (1363,1384).
- DC-17 medium — weights/marks/held-symbols comprehensions ×3 (careful: two lack the equity>0 guard = C-5 bug).
- DC-18 low-medium 2635,2685,3062 — status map built 3×.
- DC-19 low — unused params/defaults: close(status) 3232, side=None 402, embargo default 119.
- DC-20 medium 488-500 — reason_tr keys "circuit_breaker", "account_snapshot_stale" never emitted; latch strings render raw.
- DC-21 low 104 — _utcnow docstring claims test pinning, 10 direct datetime.now(UTC) calls bypass it.
- Estimate ~130 lines removable + ~120 collapsible.

## Slice F — execution (not risk_peak)
- DF-1 high telemetry.py:1095-~1298 — execution_measurement tests-only (~200 lines; already flagged in the 2026-10-11 audit as R4B4). Owner call.
- DF-2 high telemetry.py:263,535,962,1098 — `_connection` seam never passed.
- DF-3 high shadow_comparator.py:224-574 — run_shadow_comparator `as_of` historical path never used (also makes bug G-5 moot).
- DF-4 KEEP — forward_metrics, execution_metrics, cost_calibration, expected_vs_actual, dataset_metadata, run_shadow_comparator are used by scripts/execution_quality_report.py and execution_shadow_report.py (operator reports).
- DF-5..8 high-medium alpaca.py:120-158 — _peak_reconciled pair dead; _peak_account_id, _peak_state_path, _peak_epoch setter test-only.
- DF-9 high alpaca.py:31-44 — imports after code with unneeded E402 noqa (no cycle); move to top.
- DF-10 keep alpaca.py:209 market param (protocol conformity).
- DF-11 high execution/portfolio.py:203-209 — gain_band_bypass_metadata `market` unused (4 call sites).
- DF-12 high alpaca_account.py:214-224 vs alpaca_reconciliation.py:392-407 — identical _bot_tradable_symbols.
- DF-13 medium telemetry.py:819-872 vs shadow_comparator.py:368-395 — fee model written twice.
- DF-14 medium telemetry.py:174-208 vs shadow_comparator.py:410-426 — two percentile implementations; shadow one doesn't drop NaN (latent bug).
- DF-15 medium — _field/_normalized_field/_has_open_order forwarder chains across 4 files.
- DF-16 medium execution/__init__.py:1-3 — AlpacaBroker re-export unused; forces the broker stack to load on any execution import.
- DF-17 keep — getattr defaults for test fakes.
- DF-18/19/20 low — get_open_orders None default; alerting.py logger misnamed "parayabuck.execution.alpaca"; stale fee comment in alpaca_updates.py.
- Estimate ~350 lines (~200 is execution_measurement).

## Slice H — backtest (cli/web/scripts pass none of the recent options; all are tests-only)
- DH-1 high backtest/lifetimes.py (362 lines) — whole module tests-only. Owner.
- DH-2 high backtest/paper_overlay.py (168 lines) — tests-only (added this wave, item from the 56-list). Owner.
- DH-3 high product.py:759-823 — _run_metrics, cost_sensitivity, FILL_TIMING_SCENARIOS, fill_timing_report tests-only (~95 lines; added this wave). Should be wired into the CLI or removed.
- DH-4..8 high portfolio_engine.py/product.py — cash_rate_series, cost_bps_per_side, fill_lag/basis/same-bar, impact, initial_positions, terminal_lifecycles: reachable only from tests (~250 lines). Owner: wire into CLI (`parayabuck backtest --cash-rate ...`) or remove.
- DH-9 high portfolio_engine.py:324,455,1352 — enforce_drawdown_halt: no caller passes True.
- DH-10/11 medium — liquidate_at_end=False and strict=False degraded paths tests-only; default_evaluation_start strict default False vs True elsewhere.
- DH-12 high/medium — result fields never read in production: benchmark_max_drawdown, ProductBacktestResult.universe; dividend_total, terminal_events, realized_pnl_events tests-only.
- DH-13/14 high/medium — getattr on decision_symbols (1263, 1954) and UniverseSnapshot fields (1714-1721).
- DH-15 high portfolio_engine.py:2101-2126 vs portfolio_report.py:374-411 — monthly/yearly bucketing written twice.
- DH-16 medium portfolio_engine.py:1678-1703 — snapshot lookup duplicated.
- DH-17..22 low — paper_overlay re-implements stdev and check; hard-coded "fill_model" string ignores options; no-op "1d" alias; unused backtest/__init__ re-exports (eager loads); universe hash shim (keep).
- Estimate ~900 lines tests-only/unreachable; ~60 removable regardless.

## Slice I — web
- DI-1 high web/application.py:85-106 — route-flatten hack rewrites the live dispatch table only for a test (= L-5); move into the test helper.
- DI-2 high application.py:16-40 — _default_state unreachable; duplicates server.py composition and creates a 3rd ticket store.
- DI-3 high managers/engine.py:1256 — _check_approval_signature 4-tuple, caller uses only `approval` (RUF059).
- DI-4 high engine.py:1419-1431 — loop over a one-element literal tuple.
- DI-5/6 medium engine.py:677, 307-308 — getattr on always-set attr; lazily created _credential_mode_warned.
- DI-7 medium managers/websocket.py:21,92 — alias + 3 ticket stores.
- DI-8 medium routers/websocket.py:33-51 — websocket=None path unreachable; comment contradicts the fallback.
- DI-9 high routers/websocket.py:83 — suppress(TimeoutError, asyncio.TimeoutError) duplicate.
- DI-10/11 high/medium static/js/ui.js:140-170, tools.js:44 — dialog.alert/alertOnly unused; always-true PBWebSocket guards.
- DI-12/13 keep — /api/ops/incidents and /api/ops/timeline have no frontend caller but are in the frozen route contract.
- DI-14 high lifespan.py:42 — docstring references removed server.py::_auto_start_paper_engine.
- DI-15 medium lifespan.py:33-83 — `manager = resolved` fallback can treat a WebState as the manager.
- DI-16 medium services/ops.py:37-90 — duplicated try blocks and hard-coded DB/journal path literals instead of the constants.
- DI-17 low — misc getattr fallbacks.
- Estimate ~60-70 lines.

## Slice M — cli, config, live_safety, live_authorization
- DM-1 high config.py:504-506,731,1128 — DataConfig / Config.data read nowhere. Removing changes config_fingerprint → voids standing LIVE auth (see M-8 bug).
- DM-2/3 high/medium config.py:452-464 — AppConfig.open_browser/host/port/log_level validated but never used; web/app.py re-reads raw YAML, so validation doesn't gate the served values. Fix: web uses Config.app.
- DM-4 medium config.py:1073-1086 — legacy top-level `symbols:` (test fixture only).
- DM-5 keep config.py:940-1065 — fail-loud removed-key guards (~60 lines).
- DM-6 high cli.py:412-439 vs live_authorization.py:37-60 — authorize-live recomputes LIVE digests inline (one-element loop). Use live_configuration_digests (also fixes M-7 bug).
- DM-7/8/9 high/medium — unreachable: cli.py:340-343 cap/path checks, 1295-1339 dict fallbacks in _ops_timeline, 949-950 unknown-ops fall-through.
- DM-10/11 high — six ops handlers repeat config/path resolution with hard-coded path literals; try/except around dataclass reads (= M-6 bug's root).
- DM-12 medium cli.py:157-166 — `ops decision` duplicates `ops explain` parser; use aliases.
- DM-13/14 — redundant local imports (1066,1377,1438,1487); stale comments (1139,1057,1190,1441).
- DM-15..18 low — always-pass doctor rows; getattr on argparse options; --market only "equity"; config path resolved 3× in _run_doctor and a mkdir in a read-only command (707-708).
- DM-19 high live_safety.py:80-116 — verify_account_environment(account_id=) never passed.
- DM-20/21 high/medium live_safety.py:241-434 + web/managers/engine.py:1355-1372 — HMAC sign implemented twice; approval-file parse+verify implemented 3×.
- DM-22/23 low — standing path wrapper duplicate; trading_host param test-only.
- DM-26/27 low — runtime.py getattr on EngineConfig fields; settings.py hard-codes AppConfig defaults.
- Estimate ~110-130 lines.

## Slice A — runtime.py 1–3016 (no unused defs; 126 EngineRuntime.__new__ test shells in 40 files)
- DA-1 high runtime.py:2758-2767 — TypeError fallback dead for production and all doubles, and unsafe (= bug A-1). Remove.
- DA-2 high runtime.py:409-411,2241,2272,2311 — last_successful_account_iteration_at written, never read; comment says the watchdog reads it (the real reader is last_account_loop_pass_at). Added this wave.
- DA-3 medium-high 2116-2117 — comment contradicts 444-449.
- DA-4 high 1331-1334 — start() repeats live.enabled/cap checks from _validate_execution_mode.
- DA-5 medium 1324-1325 — DISABLED mode check unreachable for constructed runtimes.
- DA-6 high 2510 — _persist_safety_state reason always "ownership_drift".
- DA-7/8/9 high/medium 2644-2686 vs 5453-5464 — dead members in persistent_reasons; hand-copied reason sets already out of sync; `preferred or restorable` simplifies.
- DA-10/11 high/medium — lease predicate duplicated (2178/2197); _raise_reconciliation_breaker vs _raise_halt (2552/2567).
- DA-12 medium 81-88,565-575,… — try/except import shims for observe/snapshot modules that always exist.
- DA-14..17 medium — callable/getattr guards on ledger/repository/runtime methods that always exist.
- DA-18 medium — _last_account_at not in __init__ (= bug A-2) and two freshness timestamps for one event.
- DA-19 medium — _strategy_enabled[EQUITY] always True for constructed runtimes.
- DA-20 high — 11× getattr(self, "_state_lock", threading.RLock()) etc.: the default builds a throwaway lock (no exclusion if ever used).
- DA-21/22/23 medium — ~150 getattr(self, …) on always-set attributes; config getattr chains.
- DA-24..27 low — one-line wrapper; test-only params/returns; always-same param; crypto comment at 3004.
- Estimate ~70-90 lines; plus shell migration enables the getattr cleanup.

## Cross-cutting theme
- The single biggest dead-code driver: tests build EngineRuntime / PortfolioRuntime with __new__ (126 sites) and fakes, so production code carries ~250+ getattr/hasattr fallbacks across runtime.py and runtime_service.py. Fix order: one shared test factory → then strip fallbacks.
