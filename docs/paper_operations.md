# Paper Operations

Operational contract for the ParayaBuck PAPER runtime (dual-market:
`equity_residual_momentum_v3_dividend_sleeve_v1` / **Residual Momentum v3 +
dividend-growth sleeve**, implemented by
`src/parayabuck/portfolio/dividend_sleeve.py`, with unchanged V3 3.0.0 weekly
core at `core_allocation: 0.40` and static SCHD/VIG/DGRO
`sleeve_allocation: 0.15` at 0.05 each inside the 0.55 equity maximum;
`crypto_low_variance_hysteresis_v1` remains unchanged on 1d 96h cadence,
retention rank 4, lookback 22, top2, allocation 0.05). v1 identities remain
frozen historical baselines. See the
[dividend-sleeve production evidence](research/dividend_sleeve_production_v1.md).
This document is a companion to `docs/operations.md` and
`docs/validated_strategies.md`.

## Startup contract (Phase 4)

The PAPER runtime starts through a fixed sequence; each stage must succeed
before the next begins:

1. **Config validation** — `runtime.mode` must be `paper`; both markets
   enabled; `live.enabled` must be `false`. The engine refuses to start in
   any other combination (`EngineRuntime._validate_execution_mode`).
2. **Universe preflight** — active equity/crypto universes are validated
   (freshness, scanner digest, candidate-pool membership) with a
   deterministic configured fallback when no scanner output exists yet. The
   equity stream pins `universe.equity.sleeve_trade_symbols`; those symbols are
   hidden from V3 scoring, which ranks 23 core slots instead of 26.
3. **Startup reconciliation** — the broker is reconciled before any
   decision; a verified reconcile clears persisted safety incidents so a
   restart never resurrects them.
4. **Strategy runtimes** — one `PortfolioRuntime` per market on a SINGLE
   shared repository/account: equity core weekly (1d), with the static sleeve
   selected on the first weekly decision of each calendar month or while empty
   and otherwise held; crypto 96h (`rebalance_hours: 96`, 1d). Each receives
   an immutable active product universe snapshot: `selected_symbols` owns PIT
   eligibility and `observation_set` owns bounded stream completeness. Missing
   or invalid universe dependencies fail before a portfolio session row is opened.
5. **Warmup** — each runtime seeds completed bars until every decision-required
   symbol meets the strategy's minimum history and the latest timestamps are
   aligned (equity: beta window + 1; crypto: variance lookback + 1). One ready
   symbol is not sufficient to enter `running`.
6. **Startup catch-up** — after warmup the engine forces ONE immediate
   full-target decision per market on the latest closed bars (backtest/PAPER
   parity) unless the account already holds managed positions in that market
   (`already_invested`) or a decision already exists (restart:
   `catch_up_superseded`/`duplicate_decision`). Tagged
   `decision_origin: startup_catch_up`; it applies the normal sleeve rule:
   select on the first weekly decision of the calendar month or while empty,
   otherwise hold. Expected no-trade gates are explicit result statuses;
   unexpected invariant/persistence/provider exceptions fail startup instead of
   being converted to a blocked informational status.
7. **Account reconciliation** — a fresh broker snapshot is taken between
   phases and before any order sizing (stale account data pauses decisions
   for that market).
8. **Ready** — the runtime enters `running` when both markets have ready
   symbol history and the decision loop is live.

The sleeve symbols in `risk.slot_exempt_equity_symbols` never count toward the
8-position cap. Daily-loss, drawdown, entry-latch, and intermediate-reduction
latches never grow the sleeve.

Offline verification of this contract lives in
`tests/test_runtime_product_universe.py`, `tests/test_dual_market_startup.py`
(real membership adapters, fake broker, no network), and in the paper-chaos
gate (`scripts/run_paper_chaos.py`).

## Two-market forward observation (V2)

After deploying the V2 PAPER decision writer, record its exact deployment
timestamp. Keep both active universes fresh with provider-backed validation
before any operator PAPER start; `doctor --offline` can report
`REFRESH_NEEDED` without refreshing them. Freeze a 14-calendar-day window with
the equity V3 and crypto hysteresis identities, cap 8, crypto cap 2, receipt
schema V2, and S1-S12 gates in
`research/paper_observability_repair_v1/FORWARD_OBSERVATION_PROTOCOL_V2.md`.

```sh
PYTHONPATH=src python scripts/audit_paper_soak.py \
  --db data/runtime/parayabuck.sqlite3 \
  --receipts data/runtime/paper_receipts \
  --journal data/runtime/observability.sqlite3 \
  --config config/settings.yaml \
  --start DEPLOYMENT_TIMESTAMP_WITH_UTC_OFFSET \
  --end OBSERVATION_END_WITH_UTC_OFFSET \
  --output report.json
```

The auditor only reads the configured databases, receipt files and config.
V1 equity receipts remain historical; V2 intent and SQLite lifecycle are the
new observation authority. A missing V2 receipt or final BUY admission
observation invalidates the window. P&L is not a gate.

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
