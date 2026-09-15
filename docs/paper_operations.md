# Paper Operations

Operational contract for the ParayaBuck PAPER runtime (dual-market:
`equity_residual_momentum_v3` on 1d weekly cadence, `crypto_low_variance_hysteresis_v1`
on 1d 96h cadence, retention rank 4, lookback 22, top2, max allocation 0.05). v1 identities remain frozen historical baselines
This document is a companion to
`docs/operations.md` and `docs/validated_strategies.md`.

## Startup contract (Phase 4)

The PAPER runtime starts through a fixed sequence; each stage must succeed
before the next begins:

1. **Config validation** — `runtime.mode` must be `paper`; both markets
   enabled; `live.enabled` must be `false`. The engine refuses to start in
   any other combination (`EngineRuntime._validate_execution_mode`).
2. **Universe preflight** — active equity/crypto universes are validated
   (freshness, scanner digest, candidate-pool membership) with a
   deterministic configured fallback when no scanner output exists yet.
3. **Startup reconciliation** — the broker is reconciled before any
   decision; a verified reconcile clears persisted safety incidents so a
   restart never resurrects them.
4. **Strategy runtimes** — one `PortfolioRuntime` per market on a SINGLE
   shared repository/account: equity weekly (1d), crypto 96h
   (`rebalance_hours: 96`, 1d). Each receives an immutable active product
   universe snapshot: `selected_symbols` owns PIT eligibility and
   `observation_set` owns bounded stream completeness. Missing/invalid
   universe dependencies fail before a portfolio session row is opened.
5. **Warmup** — each runtime seeds completed bars until every decision-required
   symbol meets the strategy's minimum history and the latest timestamps are
   aligned (equity: beta window + 1; crypto: variance lookback + 1). One ready
   symbol is not sufficient to enter `running`.
6. **Startup catch-up** — after warmup the engine forces ONE immediate
   full-target decision per market on the latest closed bars (backtest/PAPER
   parity) unless the account already holds managed positions in that market
   (`already_invested`) or a decision already exists (restart:
   `catch_up_superseded`/`duplicate_decision`). Tagged
   `decision_origin: startup_catch_up`. Expected no-trade gates are explicit
   result statuses; unexpected invariant/persistence/provider exceptions fail
   startup instead of being converted to a blocked informational status.
7. **Account reconciliation** — a fresh broker snapshot is taken between
   phases and before any order sizing (stale account data pauses decisions
   for that market).
8. **Ready** — the runtime enters `running` when both markets have ready
   symbol history and the decision loop is live.

Offline verification of this contract lives in
`tests/test_runtime_product_universe.py`, `tests/test_dual_market_startup.py`
(real membership adapters, fake broker, no network), and in the paper-chaos
gate (`scripts/run_paper_chaos.py`).

## PAPER smoke policy

Real PAPER orders require an EXPLICIT operator opt-in: the environment
variable `PARAYABUCK_ALLOW_PAPER_SMOKE=YES` must be set, `runtime.mode`
must be `paper`, and `PARAYABUCK_ENABLE_LIVE` must not be `YES`
(`scripts/paper_smoke.py` refuses everything else). Absent that opt-in, NO
PAPER order is ever placed: the offline gates (chaos, dual-market startup,
telemetry) run entirely against fake brokers and local SQLite. This repo's
verification runs did NOT set the opt-in, so no real PAPER orders were
placed.

## Phase 11 PAPER forward rules (policy, VERBATIM)

Never change strategy parameters because:

- one week loses money;
- one month wins money;
- one coin performs badly.

Forward validation during PAPER checks **live-data parity**, **runtime
behavior**, **execution quality**, **cost assumptions**, **operational
reliability**, and **out-of-backtest behavior** — not single-period PnL.

An **economic modification** (alpha, parameters, cost model, or any
assumption that changes what or how the strategies trade) requires separate
validation and approval — it is never made reactively from PAPER results.

Reporting windows are **7 / 30 / 90 days**. These windows are **reporting
only, NOT approval gates**: a report that lands inside a window triggers
review, not an automatic parameter change.

## Execution-quality telemetry

`parayabuck.execution.telemetry` reports execution quality from the runtime
ledger (report only; it never auto-calibrates and never changes order
behavior):

- `execution_metrics` — orders / fills / rejects / fill latency / slippage
  / fees with per-market breakdowns;
- `forward_metrics` — strategy decisions, rebalance counts, target
  turnover, observation-set churn, candidate pool size, execution status,
  and portfolio PnL / drawdown;
- `cost_calibration` — configured (backtest) slippage/spread/fees vs
  observed median / p75 / p95 slippage and observed fees; the verdict is
  informational and NEVER adjusts assumptions.  Its existing reference is
  the completed decision-bar close, so a `within_model` diagnostic is not
  proof that the later-open backtest schedule is conservative or
  PAPER-transferable;
- `expected_vs_actual` / `decision_expected_vs_actual` — per-order and
  per-decision comparison of expected quantity/price vs actual fills,
  slippage bps, fees and fill delay.

`scripts/execution_quality_report.py` prints the report; it exits 0 even
against an empty or missing database.
