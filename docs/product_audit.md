# ParayaBuck Product Identity Audit

Audit date: 2026-08-11 · HEAD: `199d153` (+ one defect fix, see "Automated fix")

Documentation only. Nothing at runtime reads this file.

## Verdict

**B — AUTOMATED TRADING PRODUCT WITH MINOR PRODUCT GAPS.**

ParayaBuck is an autonomous Alpaca trading bot. Configure, supply PAPER
credentials, start, leave running: it discovers/maintains its universe,
ingests market data, runs the enabled strategy on schedule, applies risk,
submits and reconciles orders, monitors positions, and recovers from restart
without any research command. Research/backtest tooling is optional and
cleanly separated. A small number of operational gaps remain (below); none
require research infrastructure, and none block normal autonomous operation.

**NO FURTHER PRODUCT ARCHITECTURE REDESIGN REQUIRED.** Future work should
focus on strategy selection, risk semantics, PAPER validation, and
operational polish — not research infrastructure.

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
| 8 | Automatic fill/reconciliation | PARTIAL (see gaps G1, G5, G6) |
| 9 | Restart recovery | PARTIAL (unit-tested per layer; no full-engine restart test; G2) |
| 10 | Persistent safety state | PARTIAL (risk baselines persist; G2) |
| 11 | Operator Web control | PASS (status/start/stop/positions/orders/safety/universe/config; verified) |
| 12 | PAPER/LIVE separation | PASS (mode-driven, triple-gated live) |
| 13 | LIVE disabled by default | PASS (config + env + approval; verified) |
| 14 | No research dependency for runtime | PASS (verified code paths) |
| 15 | Backtest remains supporting tool | PASS (backtest reuses production, never the reverse) |
| 16 | Strategy selection is normal config | PASS (`strategy.equity.name` + parameters in settings.yaml) |
| 17 | No experiment promotion requirement | PASS (no registry/trial/gate on any runtime path) |
| 18 | Managed positions remain monitored | PASS (pins, bracket/stop protection, reconcile) |
| 19 | Scanner refresh requires no manual action | PARTIAL (automatic; failed scan halts entries until next scheduled slot, ≤ ~24 h) |
| 20 | Long-running runtime needs no research commands | PASS |

**PASS 16 · PARTIAL 4 · FAIL 0**

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
| `pytest` | PASS 359 passed (357 before audit fix) |
| `python -m build` | PASS (wheel + sdist) |
| `python scripts/run_paper_chaos.py` | PASS (63 marked scenarios) |
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

## Known operational gaps

- **G1 (fixed this audit) — crash window between repository persistence and
  broker acknowledgement wedged two-phase rebalances.** A row left at
  `submitting` (process dies between `record_order("submitting")` and the
  ack write) was neither terminal nor ambiguous, so `advance_rebalance`
  returned `None` forever and no reconcile/reset path rewrote the row:
  the market stayed `rebalance_in_progress` and recovery required manual DB
  surgery. **Automated fix**: `PortfolioRuntime._resolve_stuck_submission_rows`
  + `AlpacaBroker.resolve_portfolio_submission` resolve `submitting` rows at
  restart via the deterministic client-order-id lookup — unknown-at-broker →
  canceled, filled-at-broker → real fill recovered through the existing
  recovery callback, working → submitted. Fail-safe (broker unreachable ⇒
  retry, no guessing). 2 regression tests added.
- **G2 — safety-state persistence is session-date-scoped.** `risk_state` is
  keyed (account_id, session_date) and restored only for today's date, so a
  halt (e.g. `ownership_drift`) persisted at 23:59 is silently cleared by a
  restart after midnight. Recovery otherwise intact.
- **G3 — auto-start is one-shot.** A transient startup failure leaves the
  engine in `error` until the operator clicks Start or restarts the process
  (observed live). No retry.
- **G4 — crypto universe scan runs even with `strategy.crypto.enabled:
  false`** (universe discovery stays enabled): full USD-pair scan every 24 h
  and at startup when stale. Not trading-affecting; avoidable load.
- **G5 — failed universe scan halts entries until the next scheduled refresh
  slot** (equity next pre-market 06:00 ET, crypto +24 h), fail-closed but
  with up to ~24 h of blocked entries; only broker-native resting stops
  reduce risk meanwhile.
- **G6 — legacy execution-layer gaps**: the old signal-engine ledger path is
  unreachable from production code but still maintained; stream trade updates
  for ledger orders are dropped (60 s reconcile + fill-activity pagination
  covers them); fill-activity recovery can silently skip older activities;
  portfolio fills persist zero PnL (slippage only).
- **G7 — Web live-arming UI** only covers the in-process account-suffix
  phrase; the approval-file prerequisite enforced by the engine is not
  surfaced, so a Web-only live arm fails at engine start with a generic
  error. (Live is unreachable by default regardless.)
- **G8 — chaos gate excludes the restart tests** (marker `paper_chaos`
  matches only 3 modules); restart recovery is not part of the gate despite
  its docstring.
- **G9 — port-bind failure orphans the engine** (observed live): the engine
  auto-starts during lifespan startup before the socket binds; if the bind
  fails the engine keeps running with no operator console.

## Dead / obsolete research code

- `core/models.py` `Signal` / `StrategyContext` / `StrategyEvaluation` and
  `markets/regime.py` `RegimeDetector`/`RegimeAssessment` — remnants of the
  retired signal engine; test-only users, no production callers.
- `config/experiments/` — empty, unreferenced scaffold.
- `data/research/` — orphaned dataset cache (gitignored, zero references).
- `data/runtime/portfolio_trial_ledger.json` /
  `portfolio_holdout_registry.json` — unreferenced research leftovers.
- SQLite tables `executions`, `runtime_sessions` — dead schema (never
  written). `execution/ledger.py` legacy machinery (`submit()`,
  `replace_protective_stop`, `protect_recovered_crypto_entry`) is
  unreachable from production but retained for recovery/crypto protection.
- None of these are required by, or misleading about, the current product
  runtime; none were deleted (kept per audit guidance — cleanup optional).

## Strategy → order trace (enabled: `equity_dynamic_trend_1d_v1`)

`AlpacaBarStream` → `EngineRuntime._on_bar` → `TimeframeAggregator` (5m→1d)
→ event queue → `_decision_loop` → `_process_portfolio_timestamp` →
readiness gate → `PortfolioRuntime.process_closed_bars` →
`PortfolioDecisionService.build_context` → `EquityDynamicTrend1dV1.decide`
→ `TargetPortfolio` → entry-latch/intermediate caps → `PortfolioRiskOverlay`
→ `TargetToOrderPlanner.plan` (SELL-first, deterministic `pbp-` client ids)
→ two-phase persisted rebalance → `_submit_orders`
(persist-before-broker) → `AlpacaBroker.submit_portfolio_order` →
TradingStream trade update → `PortfolioStateRepository.apply_trade_update`
→ ownership. No backtest-only class in this path.
