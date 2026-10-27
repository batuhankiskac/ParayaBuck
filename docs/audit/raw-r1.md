# Round 1 raw findings (bugs) — unverified agent output, verify before fixing

## Slice B — runtime.py 3017–end
- B-1 medium/medium runtime.py:3602-3612 — while an operator-resettable halt (account_fetch_failures) is set, the decision batch is dropped with only a log.warning; after reset nothing re-decides that session. Fix: defer via _defer_portfolio_batch or emit a no-trade journal event and an alert.
- B-2 low/low runtime.py:3390-3396 — SIP session-close fallback cleared silently when _historical is None (no log, no alert). Fix: log.error + alert like the exhaustion path.

## Slice A — runtime.py 1–3016
- A-1 high (orchestrator-confirmed code) runtime.py:2762-2767 — `except TypeError` retries advance_rebalance without `submit_deferred_buys`; the real default is True, so any internal TypeError re-runs it with the BUY gate dropped (fail-open) and repeats side effects. Fix: decide by inspect.signature, not by catching TypeError.
- A-2 low runtime.py:712 (also 4283, 4292) — `_last_account_at` has no __init__ default (confirmed: only set at 1462/2037/5065); health() raises AttributeError after an aborted start. Fix: init to None.
- A-3 low runtime.py:1749-1752 — broker read failure in _pin_managed_symbols swallowed silently → foreign-inventory alert skipped. Fix: log warning.
- A-4 low runtime.py:2909-2922 — exception reading order side swallowed → rejected-SELL alert dropped silently. Fix: log + generic alert.

## Slice M — cli, config, live_safety, live_authorization
- M-1 medium cli.py:1605/1640 — `ops rebase-ownership` dry run constructs PortfolioStateRepository (runs migrations, creates missing DB) before the --confirm gate. Fix: open read-only/require exists for dry run.
- M-2 medium config.py:635-636 — LIVE remaps only decision_db_path; observability journal_path and snapshot_path stay shared with PAPER. Fix: remap defaults under data/runtime/live/.
- M-3 low config.py:1104,114,455 — `enabled` / `auto_start_engine` not type-checked; YAML "false" string is truthy. Fix: reject non-bool in __post_init__.
- M-4 low live_safety.py:102 — PAPER refusal keyed on "AK" key prefix rather than classifier verdict (defense in depth; documented as deliberate).
- M-5 low cli.py:255-271,547-551 — `halt`/`resume` need a loadable config; emergency halt fails when settings.yaml is broken; marker dir depends on which config loads.
- M-6 low cli.py:1040,1151,1166,1369,1530 — read-only ops commands silently fall back to PAPER defaults when an explicit --config fails.
- M-7 low cli.py:421-449 — authorize-live with no enabled strategy → uncaught ValueError traceback.
- M-8 low live_safety.py:331-343 — config fingerprint hashes all default fields; adding a defaulted field voids standing LIVE authorization after deploy.

## Slice F — execution/portfolio.py, telemetry.py
- F-1 CRITICAL (orchestrator-confirmed by experiment) execution/portfolio.py:1243-1244 — validate_order_batch grid check `abs(quantity/increment - round(...)) > 1e-6` in float. With Alpaca's fallback increment 1e-9 (alpaca.py:215, equities have no min_trade_increment) a 9-decimal quantity of 10–5000 shares fails ~39% of the time (3932/10000 in a random test) → valid orders, including SELLs, are rejected before submission. Introduced in this wave (not yet running on the VM: VM runs 21a43f7). BLOCKS the pending VM restart. Fix: Decimal check `Decimal(str(q)) % Decimal(str(inc)) == 0` + regression test with 1e-9 and q > 10.
- F-2 medium (latent, tests-only caller) telemetry.py:620-672 — forward_metrics adds all-time fill realized PnL on top of session-scoped risk realized → equity estimate overstated.
- F-3 low execution/portfolio.py:1058-1061 — target-zero SELL below min notional dropped with no hold reason/alert.
- F-4 low telemetry.py:1337 — completeness uses absolute 1e-12 tolerance (below float ulp for thousands of shares). Reporting only.
- F-5 low telemetry.py:104-110 — "no such column" swallowed into empty results.

## Slice K — monitoring paper_incident, health, journal, diagnostic, email_alerts
- K-1 medium email_alerts.py:277-294 — throttle slot and hourly quota consumed before queue put; queue-full drop then suppresses later same-key alerts silently. Fix: record after put succeeds.
- K-2 medium diagnostic.py:84-86,187-189,351-355 — forensics called non-strict; unreadable ledger shows "no ambiguous orders"/"sessions ok". Fix: strict=True + UNKNOWN on error.
- K-3 medium health.py:794-800 — trading_state ignores a CRITICAL intent_watchdog (missed decision) → `ops status` prints ACTIVE.
- K-4 medium email_alerts.py:356-377,414-422 — close() on settings reload abandons queued alert mails with no journal/drop count.
- K-5 low paper_incident.py:662-663 — reconcile-first treats None and a drift-bearing dict as reconciled (misleading incident record).
- K-6 low paper_incident.py:744-745,835-843 — recovery_result labels claim actions that did not run.
- K-7 low paper_incident.py:402 — inclusive lower bound vs documented exclusive (false-positive only).
- K-8 low health.py:691-695 — evaluate_health silently falls back to Config() defaults.

## Slice C — portfolio/runtime_service.py
- C-1 high (orchestrator-confirmed code) runtime_service.py:2506 (vs 1698-1702) — deferred-BUY re-plan fetches asset_trading_rules for EVERY universe symbol; one untradable universe member raises ValueError and blocks all deferred BUYs indefinitely. Fix: rules only for held ∪ targeted / planned BUY symbols.
- C-2 medium runtime_service.py:2536-2575 — re-plan drops planned SELLs when BUYs also exist; BUYs go out while an owed SELL is silently discarded (sells-before-buys broken). Fix: hold BUYs if replan_needs_sell, or log.
- C-3 medium runtime_service.py:1041-1101 (vs 1352) — startup_catch_up plans from a pre-advance account snapshot after advance returned rebalance_complete → possible duplicate BUY. Fix: return or re-fetch account, like 1352.
- C-4 low runtime_service.py:2054-2056 — one leg at residual-round limit closes the whole rebalance before other legs' residuals go out.
- C-5 low runtime_service.py:1514,1544 — unguarded division by account.equity.
- C-6 low runtime_service.py:269,459-468 — no-trade suppression dict is module-global, key lacks account_id/mode.
- C-7 low runtime_service.py:239-240,519-520 — journal/summary emit failures swallowed with no log.

## Slice I — web/managers, web/services
- I-1 medium managers/engine.py:484-495 — unknown paper mode (config missing) counts as "not paper", so save_settings to live skips the LIVE-entry gates (arm still needed at start). Fix: tri-state fail-closed.
- I-2 medium managers/engine.py:910-912,1010-1012 — stop during a transient auto-start retry leaves engine_state stuck at "stopping" until process restart.
- I-3 medium managers/engine.py:444-463,782,796 — watchdog_healthy returns True while runtime.start() hangs (self.runtime not yet published) → systemd never restarts a hung start. Fix: manager-side start timestamp + grace.
- I-4 medium managers/engine.py:1609-1616 — tracked-secret start gate fails open when git ls-files cannot run (VM deploy has .git, so likely fine; confirm).
- I-5 low services/settings.py:305-358 — running-engine check and config write not atomic vs concurrent start.
- I-6 low managers/engine.py:430-442 — status poll overwrites manager-owned "stop_failed".
- I-7 low services/trend_status.py:146-150 — exit price index wrong with only two vote thresholds (display only).
- I-8 low managers/engine.py:1070-1075 — credentials and endpoint mode read in two separate config reads.
- I-9 low managers/engine.py:1644 — dotenv quoting escapes quotes but not backslashes.
- I-10 low services/ops.py:151-154 — ops incidents not scoped by account_id (dashboard is).
- I-11 low services/status.py:107-111 — operations_snapshot failure swallowed silently.

## Slice N — data, tax, markets, core, utils, backup, storage
- N-1 medium tax/rates.py:38-44,243-291 — unconfirmed EVDS Yi-UFE series code overwrites existing months in yiufe.csv silently. Fix: refuse to overwrite differing months / candidate file.
- N-2 low markets/nyse_calendar.py:71-77 — degraded calendar treats NYSE holidays as open sessions (documented).
- N-3 low tax/export.py:259-277 — ytd_status "never raises" but ValueError/TypeError escape → dashboard tax panel 500.
- N-4 low tax/rates.py:179-183, tax/cli.py:71-94 — EVDS HTTP/JSON errors escape as tracebacks.
- N-5 low tax/rates.py:92-104 — coverage check uses previous weekday, not business day → false MissingRateError after a TR holiday.
- N-6 low data/aggregator.py:97-101 — pre-09:30 minutes clamped into the opening bucket (currently unreachable).
- N-7 low data/cache.py:100-154 — range queries compare ISO strings; relies on UTC writers (latent).

## Slice L — monitoring rest, web/*.py, web/routers
- L-1 medium routers/health.py:79-105 — /readyz returns 200 when a LIVE-configured engine was refused auto-start (expired standing authorization) → dead-man pinger stays green.
- L-2 low routers/safety.py:96-118 — toggle_mode TOCTOU: active-runtime check outside the settings lock; start_engine never takes settings_lock.
- L-3 low routers/websocket.py:57-95 — websockets get no Host/peer check (http middleware doesn't run for ws scope); ticket still required. Defense in depth.
- L-4 low web/server.py:27-38 — bind probe stops at the first bindable address; uvicorn binds all → engine may auto-start then the server fails on ::1.
- L-5 low web/application.py:88-106 — route-flatten hack drops include-level deps/prefixes inside a bare `except: pass`.
- L-6 low monitoring/metrics.py:262-272 — dict(payload) on non-object JSON escapes "never raises" → /api/status 500.
- L-7 low monitoring/operational_event.py:86 — redaction list lacks token/credential/bearer.

## Slice G — Alpaca execution layer
- G-1 high (orchestrator-confirmed code) execution/alpaca_account.py:296-306 (+ alpaca_reconciliation.py:506-579, runtime.py:490-493) — inside flat_broker_reconcile_scope the empty-positions re-read is skipped, so ONE transient empty broker read lets reconcile clear bot ownership durably (needs operator rebase). Fix: re-read once inside the scope too; accept flat only if both reads are empty.
- G-2 medium alpaca_account.py:319,362-376 — owned symbol absent from a non-empty broker position list is never a drift candidate (planner sees current=0 while ownership says 100). Fix: add missing owned symbols as broker_qty=0 candidates.
- G-3 low alpaca.py:263-325 — working BUY with empty/unparseable symbol dropped from committed capacity. Fix: unpriced entry → fail closed.
- G-4 low alpaca_account.py:263-266,461 — null last_equity → daily_pnl=0 → daily-loss gate disabled for that snapshot (documented, fails open).
- G-5 low shadow_comparator.py:572-574,339-345 — legacy-fills fallback ignores as_of.
- G-6 low alpaca.py:106-110 — failed session-timeout patch swallowed silently.
- G-7 low alpaca_client.py:200-215 / alpaca_orders.py:76-79 — get_asset 404 not mapped to DefiniteOrderRejection → reconciliation_required instead.

## Slice D — runtime_repository, ownership_rebase, signal_cross_check
- D-1 medium (orchestrator-confirmed ordering) runtime_repository.py:1380-1423,1730-1734 — a late real SELL that proves a stale ownership clear books realized P&L 0.0, because the adjustment is consumed after the FIFO P&L is computed. Fix: consume adjustments first.
- D-2 low runtime_repository.py:1352-1365 — apply_trade_update overwrites broker_order_id with "" (update_order uses COALESCE(NULLIF)).
- D-3 low signal_cross_check.py:375-389 — exception in compare_sources bypasses the mismatch hold and alert. Fix: map to secondary_unavailable.
- D-4 low ownership_rebase.py:308-309 vs alpaca_account.py:362 — tolerance mismatch (1e-6 vs 1e-8 relative) → tiny drifts classified MATCH, alert raised, halt persists.
- D-5 low runtime_repository.py:59-65,1184 — done_for_day documented resumable but treated terminal by open_orders → partially filled DAY BUY's remainder invisible to admission. (Note: DAY orders don't resume, so probably the comment is wrong; verify.)
- D-6 low runtime_repository.py:164-167 — connection leak when migration fails in the constructor.

## Slice E — strategy, models, risk, risk_peak, universe
- E-1 medium execution/alpaca_account.py:444-458 — failed peak-sidecar write never retried (old_peak already updated in memory); comment promises retry. Fix: dirty flag until a successful save.
- E-2 low portfolio/equity_leveraged_trend.py:381 — R2 replay SMA vote uses a truncated window when prefix < sma_bars (only sma_bars > ~272; production 200 unaffected).
- E-3 low equity_leveraged_trend.py:441-442 — zero realized vol (flat/stale closes) → weight 1.0 (max allocation). Fix: treat as insufficient history or hold.
- E-4 low execution/risk_peak.py:371-388 — reset_peak_equity checks only <= 0; NaN/inf set in memory before save rejects it (currently unreachable).
- E-5 low risk_peak.py:55-57,194-201 — corrupt newest-epoch rows silently fall back to older peak; Infinity epoch raises OverflowError.
- E-6 low risk_peak.py:350-354 — ledger-less probe brokers can lower the on-disk peak sidecar (short window).

## Slice H — backtest
- H-1 medium (design question, live parity) backtest/portfolio_engine.py:1564-1574 + execution/portfolio.py:1038,1060 — full exits (target 0) below the turnover band or $10 notional floor are never executed; residuals persist. Owner decision; same as F-3.
- H-2 low portfolio_engine.py:1922-1924 — gross_return adds costs back multiplicatively (approximation).
- H-3 low portfolio_engine.py:605-609,821,1061 — on a split day qty rescaled before _last_price refresh → cash buffer uses overstated equity (product runs reject splits; latent).
- H-4 low portfolio_engine.py:441-443,528-559 — run() doesn't reset the drawdown-liquidation latch (latent; one engine per run).
- H-5 low product.py/portfolio_engine.py/portfolio_report.py — fee dates and month/year buckets use bar UTC date, not NY session date (latent with Alpaca stamps).
- H-6 low portfolio_engine.py:656 — benchmark series matches raw symbol, elsewhere canonicalised (latent).

## Static tools (orchestrator)
- mypy: 10 errors — portfolio_engine.py:1354 `**dict[str, object]` into PortfolioRiskOverlay.apply (type-only); runtime_service.py:2062 indexing Optional dict; runtime_service.py:2391 missing annotation; runtime.py:377 Any return; runtime.py:501/507 attributes attached to AlpacaBroker at runtime (works via getattr; declare them on the class); runtime.py:3630 Optional str passed as str to _defer_portfolio_batch.
- ruff (default rules): clean. Extended: B023 runtime.py:3855 is a false positive (lambda called synchronously); RUF059 web/managers/engine.py:1256 unused approval_error/hmac_key/approval_path; 8 unused noqa directives; PLW2901 loop var overwritten at portfolio_engine.py:833, product.py:1102; B904 cli.py:928.
