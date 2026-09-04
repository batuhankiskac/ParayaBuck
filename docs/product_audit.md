# ParayaBuck Product Identity Audit

Audit date: 2026-08-11 · HEAD at audit: `199d153` · HEAD after hardening:
`<see git log>` (product hardening mini-loop: G1–G9)

Documentation only. Nothing at runtime reads this file.

> **Historical snapshot — not current retention authority.** This audit was
> written on 2026-08-11 against HEAD `199d153`; later changes mean its path and
> retention claims may no longer match the current tree. See the [current
> research index](research/README.md) for the current lifecycle. Do not treat
> this historical classification as automatic deletion authority.

## Verdict

**B — AUTOMATED TRADING PRODUCT WITH MINOR PRODUCT GAPS** (pre-hardening),
**closed by the hardening loop: product foundation is now FROZEN.**

ParayaBuck is an autonomous Alpaca trading bot. Configure, supply PAPER
credentials, start, leave running: it discovers/maintains its universe,
ingests market data, runs the enabled strategy on schedule, applies risk,
submits and reconciles orders, monitors positions, and recovers from restart
without any research command. Research/backtest tooling is optional and
cleanly separated.

**PRODUCT FOUNDATION: FROZEN — NO FURTHER PRODUCT ARCHITECTURE REDESIGN
REQUIRED.** Future work focuses on strategy selection, risk semantics, PAPER
validation, and operational polish — not research infrastructure. See
`docs/product_foundation.md`.

## Product entrypoints

| Workflow | Command | Notes |
|---|---|---|
| PAPER | `parayabuck` or `parayabuck web` | Web console (uvicorn) auto-starts the engine in-process when `runtime.mode: paper` + credentials + `app.auto_start_engine: true` (default). |
| LIVE | same command, then arm | Requires `runtime.mode: live`, `live.enabled: true`, positive `live.hard_dollar_cap`, `PARAYABUCK_ENABLE_LIVE=YES`, a signed short-lived HMAC approval (`parayabuck authorize-live`), and a fresh Web account-suffix confirmation. Never automatic. |
| BACKTEST | `parayabuck backtest [--market ...] [--data bars.csv]` | Finite offline/historical CLI workflow. |
| OPTIMIZE | `parayabuck optimize --grid ... --train-start ... --train-end ... [--oos-*]` | Finite research workflow (TRAIN/OOS grid). |
| OTHER | `parayabuck authorize-live` | Live approval signing (read-only account verification). |

Backtest is a separate CLI surface, not the runtime abstraction. Backtest and
the running engine are mutually exclusive by design (one process).

## Runtime sequence (PAPER, verified live)

`parayabuck` → uvicorn lifespan → auto-start task → `EngineRuntime.start()`
(`src/parayabuck/runtime.py:643`):

1. mode guards (disabled refused; live requires all arming gates) →
2. `broker.reconcile()` (startup reconciliation; failure → `halted`, fail-closed) →
3. pin managed/open-order symbols into the universe →
4. construct `PortfolioRuntime` per enabled market (strategy from
   `portfolio_strategy_from(config.strategy.<market>.name)` — pure factory) →
5. live approval check (live only) →
6. `_restore_persistent_state()` (risk baselines, circuit breaker) →
7. `_advance_portfolio_rebalance()` per market (resume interrupted
   two-phase rebalances exactly once) →
8. subscribe bar stream (stocks + crypto) →
9. start daemon threads: `pb-decisions`, `pb-account` (15 s poll + risk
   latch), `pb-stocks`/`pb-crypto` streams, `pb-trade-updates`,
   `pb-universe-refresh` (equity pre-market 06:00 ET, crypto every 24 h),
   60 s reconcile schedule →
10. `_warmup()` (historical bars seeded into state; no decisions) →
11. readiness; `state=running`, `/readyz` 200.

Universe preflight (`universe/preflight.py`) auto-scans when the active
universe is missing/stale; verified live: a fresh data dir auto-scanned the
equity universe (13 045 assets → 303 shortlist → 30 stream symbols) and the
crypto universe with no manual step. Clean stop verified: SIGTERM →
`Engine runtime stopped` → `Application shutdown complete`; sessions
persisted, no orphan state.

## Automation scorecard

| # | Item | Grade |
|---|---|---|
| 1 | One-command PAPER startup | PASS (verified live) |
| 2 | Continuous runtime loop | PASS (daemon threads, health/readiness) |
| 3 | Automatic market-data ingestion | PASS (stream + aggregators + gap repair + warmup retry) |
| 4 | Automatic universe maintenance | PASS (preflight auto-scan + refresh loop + pins; verified live) |
| 5 | Automatic strategy decisions | PASS (closed-bar schedule, weekly policy, intermediate risk rebalance) |
| 6 | Automatic risk application | PASS (overlay + entry latch + buy-capacity sizing) |
| 7 | Automatic order submission | PASS (two-phase, deterministic client ids, persist-before-broker) |
| 8 | Automatic fill/reconciliation | PASS (stream fills + 60 s reconcile + startup fill-activity recovery + G1 crash-window resolution in the chaos gate) |
| 9 | Restart recovery | PASS (per-layer unit tests + 16 restart chaos scenarios incl. cross-day; full-engine lifecycle verified live) |
| 10 | Persistent safety state | PASS (safety_incidents table survives day boundary; session-scoped baselines deliberate) |
| 11 | Operator Web control | PASS (status/start/stop/positions/orders/safety/universe/config; verified) |
| 12 | PAPER/LIVE separation | PASS (mode-driven, triple-gated live) |
| 13 | LIVE disabled by default | PASS (config + env + approval; verified) |
| 14 | No research dependency for runtime | PASS (verified code paths) |
| 15 | Backtest remains supporting tool | PASS (backtest reuses production, never the reverse) |
| 16 | Strategy selection is normal config | PASS (`strategy.equity.name` + parameters in settings.yaml) |
| 17 | No experiment promotion requirement | PASS (no registry/trial/gate on any runtime path) |
| 18 | Managed positions remain monitored | PASS (pins, bracket/stop protection, reconcile) |
| 19 | Scanner refresh requires no manual action | PASS (automatic; failed scans retry bounded 5/15/30/60 min, equity gated pre-market) |
| 20 | Long-running runtime needs no research commands | PASS |

**PASS 20 · PARTIAL 0 · FAIL 0** (after hardening loop)

## Research-runtime dependency audit

ZERO mandatory research-runtime coupling. Verified in code:

- PAPER startup does not read `docs/release_validation.md` or any validation
  status (`runtime.py` imports contain no backtest/docs/research module).
- Broker submission never inspects backtest results or `optimization.json`.
- Runtime never reads `reports/`, `data/research/`, or optimizer output.
- Strategy loading is a pure name→class factory (`portfolio/__init__.py`).
- Web start button requires no research pass.
- `config/settings.yaml` has no research keys
  (`promotion_state`/`candidate_status`/`experiment_id`/`holdout_status`
  absent repo-wide).
- `config/experiments/` is an empty, unreferenced directory.
- `data/runtime/portfolio_trial_ledger.json` and
  `portfolio_holdout_registry.json` are unreferenced leftovers (no code,
  test, or script reads them).
- Import edges: production → backtest only via `cli.py:14` (module-level
  import of `backtest.product` when launching `parayabuck`; code loaded, no
  data/gates). Backtest → production is the healthy direction
  (strategies, `PortfolioExecutionPolicy`, scanners are reused by the
  simulator).

## Test gate (2026-08-11, Python 3.14, repo venv)

| Gate | Result |
|---|---|
| `ruff check .` | PASS |
| `ruff format --check .` | PASS |
| `mypy src` | PASS (64 files) |
| `pytest` | PASS 426 passed (hardening added 67) |
| `python -m build` | PASS (wheel + sdist) |
| `python scripts/run_paper_chaos.py` | PASS (90 marked scenarios incl. restart/cross-day chaos) |
| `pip-audit` | PASS (no known vulnerabilities) |
| Docker build/healthz/readyz/SIGTERM/restart | NOT RUN (no Docker daemon on this machine; all contexts point at dead sockets) |

## PAPER connectivity (verified read-only, 2026-08-11)

- Account ACTIVE, `$99 380.68` equity, 0 positions, 0 open orders.
- Assets: 14 219 active US equities (13 355 tradable), 73 tradable crypto.
- Historical bars (AAPL/MSFT 1d) and latest quote fetch OK.
- Clock: market closed (next open 2026-08-12 09:30 ET).
- Engine started with the shipped config (strategy enabled, allocation as
  configured) against a fresh temp state dir: auto-scanned both universes,
  warmed, `state=running`, `/readyz` 200, then clean SIGTERM stop. No orders
  were placed (0 orders/positions on the account before and after).
- `PARAYABUCK_ALLOW_PAPER_SMOKE=YES` is not set and was not manufactured;
  the execution-lifecycle smoke (`scripts/paper_smoke.py`) was not run.

## Drawdown risk policy (2026-08-12)

Drawdown is measured against a monotonic peak (`AccountSnapshot.drawdown_pct`,
peak persisted in `data/runtime/{paper,live}_equity_peak.json` + repository
high-water), so a >`risk.max_drawdown_pct` breach cannot recover on its own
while entries stay blocked — without an explicit recovery it was a permanent
kill-switch. Policy now:

1. A drawdown breach is an **operational incident** (`portfolio_drawdown_halt`
   mirrored into `safety_incidents`): entries blocked (latch + risk overlay,
   SELL-only decisions still run), state visible across restarts.
2. Recovery is an **explicit verified risk-epoch reset**
   (`POST /api/safety/risk-epoch`, Web "Yeni risk dönemi" button): requires a
   running engine, no active circuit breaker, a verified broker
   reconciliation and a fresh account snapshot; then the peak baseline
   (broker JSON + repository high-water) is rebased to the current equity,
   the incident is cleared and the latch re-evaluates. Other incidents are
   never touched.
3. The latch also clears if the breach genuinely recovers (e.g. an external
   deposit) — the incident mirrors the breach.
4. **Backtest**: the drawdown breach is a **shadow event** (recorded per
   decision and aggregated as `drawdown_halt_shadow_events` in the run
   record/report: count + first/last date) and the alpha curve keeps trading,
   so a monotonic peak can never freeze a research run. Daily-loss halts stay
   enforced (session-scoped, they self-recover). A production-policy
   simulation can be layered on separately later.

## Operational gaps — status after hardening loop (2026-08-12)

- **G1 — crash-window rebalance wedge: CLOSED** (previous audit). A row left
  at `submitting` (crash between repository persistence and broker ack) is
  resolved at restart via the deterministic client-order-id lookup
  (`AlpacaBroker.resolve_portfolio_submission` +
  `PortfolioRuntime._resolve_stuck_submission_rows`): unknown-at-broker →
  canceled, filled-at-broker → real fill recovered, working → submitted.
  Fail-safe; now part of the chaos gate (test_restart_chaos.py).
- **G2 — cross-day safety state: CLOSED.** New `safety_incidents` table
  (account-scoped, date-independent) is the authoritative
  PERSISTENT_INCIDENT store: `ownership_drift`, `protection_failure`,
  `ambiguous_order_state`, `reconciliation_failed` and friends survive
  midnight/restart/reboot until their verified recovery (startup or periodic
  reconcile success, or a granular operator `reset_safety_lock`). Daily-loss
  /drawdown baselines stay SESSION_SCOPED in the session-keyed `risk_state`
  (a new healthy session may clear them). Migration backfills the latest
  historical halt fail-safe; an operator-cleared incident is never
  resurrected. Startup reconcile success clears incidents (same recovery
  semantics as the periodic path); the restart rebalance-resume is deferred
  while a restored breaker is active.
- **G3 — bounded autonomous startup recovery: CLOSED.** `TransientStartupError`
  classification (network-class failures: connect/read timeouts, connection
  errors, HTTP 5xx/429 — never recorded as permanent incidents) with a
  bounded auto-start retry loop in the Web manager ([5, 15, 30, 60]s cap;
  one warning per attempt; stop/shutdown cancel; manual start refused while a
  retry is pending; success resets counters). Permanent/config/safety failures
  stay fail-closed with an actionable error. Also covers the final startup
  account fetch.
- **G4 — disabled crypto scanner load: DEFERRED** (optional optimization,
  not a release blocker). Crypto scanning continues on its cadence even with
  the crypto strategy disabled; the G5 retry work keeps crypto failures
  isolated from equity.
- **G5 — scanner refresh self-healing: CLOSED.** Per-market bounded retries
  (300/900/1800/3600 s cap) after a failed refresh instead of waiting for the
  next schedule slot; fail-closed preserved (membership unchanged,
  `invalid_dynamic_universe` halt, digest validation, pins untouched).
  Equity retries are capped at the next pre-market slot when the backoff
  would land mid-session (a partial daily bar must never change the
  universe). Retry state surfaces in `/api/status` (`scanner.retry_at`,
  `retry_attempt`); shutdown cancels pending retries.
- **G6 — accounting/legacy classification: RESOLVED (2026-08-17).** The
  legacy ledger path (`AlpacaBroker.submit()` era: `submit`,
  `replace_protective_stop`, `protect_recovered_crypto_entry`,
  `_validated_values`, `_validate_pre_submit`, `_record_entry_terminal`,
  `_submit_crypto_stop`, the `ExecutionBroker` protocol, and
  `pending_entry_orders`) was deleted together with its tests; recovery and
  crypto protection live in `reconcile()` / `submit_portfolio_order()`.
  Still open (reporting-only): fill-activity recovery can skip very old
  activities — intentional bounded
  pagination, RECONCILIATION-CRITICAL but only for ages beyond the Alpaca
  activity window; (c) portfolio fills persist `realized_pnl=0` —
  REPORTING-ONLY: authoritative equity is the broker account snapshot
  (account loop + reconcile), daily-loss state derives from
  `account.daily_pnl`, not stored fill PnL; risk does not consume stored
  fill PnL; reconciliation uses quantities/activity ids, not stored PnL; the
  Web operations snapshot shows slippage and broker-computed PnL. Zero-PnL
  is a display incompleteness, not a correctness issue. No code change.
- **G7 — live-arming prerequisites: CLOSED.** `/api/live/prerequisites`
  computes an 11-item checklist mirroring the real gates (config live
  enabled/mode/cap, `PARAYABUCK_ENABLE_LIVE=YES`, approval file presence,
  HMAC key length, signature+expiry, account binding via read-only live
  probe, configuration-digest match, Web arm-token phrase, credentials,
  engine idle) WITHOUT secrets; the settings drawer renders
  satisfied/missing per item. No requirement weakened; live remains
  impossible by default.
- **G8 — restart chaos gate: CLOSED.** New `tests/test_restart_chaos.py`
  (marked `paper_chaos`) adds 16 deterministic restart scenarios — flat,
  managed position, open order, SELL phase, SELL-filled-before-BUY, partial
  fill, duplicate trade update, G1 submitting-window (both outcomes),
  persistent-incident restart, cross-day incident restart, universe
  persisted/pins, symbol outside scanner universe — executed by
  `scripts/run_paper_chaos.py` (now 90 marked scenarios).
- **G9 — Web bind failure must not orphan the engine: CLOSED.** Traced:
  uvicorn starts the lifespan (which auto-starts the engine) BEFORE binding
  the socket, so a lost port race launched a full engine before the bind
  error. Pre-bind probe in `web/app.py` (`_probe_web_bind`) fails fast
  (exit 1, clear console error, engine never started). Verified live:
  second instance on the same port exits rc=1 with zero engine activity;
  SIGTERM on the running instance shuts down cleanly with no orphans.

## Dead / obsolete research code

- `core/models.py` `Signal` / `StrategyContext` / `StrategyEvaluation` and
  `markets/regime.py` `RegimeDetector`/`RegimeAssessment` — remnants of the
  retired signal engine; test-only users, no production callers.
- `config/experiments/` — empty, unreferenced scaffold.
- `data/research/` — orphaned dataset cache (gitignored, zero references).
- `data/runtime/portfolio_trial_ledger.json` /
  `portfolio_holdout_registry.json` — unreferenced research leftovers.
- SQLite tables `executions`, `runtime_sessions` — dead schema (never
  written). The formerly retained `execution/ledger.py` legacy machinery
  (`submit()`, `replace_protective_stop`, `protect_recovered_crypto_entry`)
  was deleted on 2026-08-17 (see G6).
- None of these are required by, or misleading about, the current product
  runtime.

## Strategy → order trace (enabled: `equity_residual_momentum_v3` / `crypto_low_variance_v2`)

`AlpacaBarStream` → `EngineRuntime._on_bar` → `TimeframeAggregator` (5m→1d)
→ event queue → `_decision_loop` → `_process_portfolio_timestamp` →
readiness gate → `PortfolioRuntime.process_closed_bars` →
`PortfolioDecisionService.build_context` →
`EquityResidualMomentum1dV3.decide` / `CryptoLowVariance1dV2.decide`
→ `TargetPortfolio` → entry-latch/intermediate caps → `PortfolioRiskOverlay`
→ `TargetToOrderPlanner.plan` (SELL-first, deterministic `pbp-` client ids)
→ two-phase persisted rebalance → `_submit_orders`
(persist-before-broker) → `AlpacaBroker.submit_portfolio_order` →
TradingStream trade update → `PortfolioStateRepository.apply_trade_update`
→ ownership. Startup catch-up (`decision_origin: startup_catch_up`) runs the
same path once on the latest warmup bars after a fresh start. No
backtest-only class in this path.
