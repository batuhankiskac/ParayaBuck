# Frozen protocol: `equity_v3_volatility_managed_v1`

Protocol status: **FROZEN BEFORE ECONOMIC OUTCOMES**.

No Control A, Treatment B, or Comparator C P&L/economic metric may be
calculated until this file exists. The SHA-256 of this exact file is recorded
in `run_manifest.json` and independently verified.

## Hypothesis and non-goals

The current production V3 may have better risk-adjusted performance if its
equity exposure is reduced when broad-market realized volatility is unusually
high. The experiment changes exposure only. It adds no instrument, position
slot, alpha signal, regime classifier, leverage, or production registration.

The V3 candidate universe, PIT observation-set ranking, residualization against
SPY, positive 12-1 gate, raw 6/9/12 consensus, idiosyncratic-volatility filter,
top-name selection, alpha ranks, inverse-volatility sizing, per-position cap,
weekly cadence, next-tradable-bar execution, transaction-cost model, and risk
overlay remain those on the starting `main` commit.

## Data identity and semantics

The campaign uses the locally available symmetric v5 package:

| Role | Path | SHA-256 |
|---|---|---|
| Split-adjusted daily equity bars | `data/historical/equity_pit_symmetric_2016_2026_v5.csv` | `bb7aaae86bc1909c6b0a1094a59430f5ed9900bb3ec63891bbdcda8cda055139` |
| Entity lifetimes | `data/historical/equity_entity_lifetimes_full_v5.csv` | `ed675d9b6ba7f173b65ae2b5272ffb62e09d6a007ed36ea22d3aba7a36774596` |
| Corporate actions | `data/historical/equity_survivorship_full_corporate_actions_v5.csv` | `a0a60755a3f6adfc06a5ef0e6ed82852e1584c20fdb62cefed92b765dc9adc23` |

The price package contains two lexical encodings of the same equity-bar
schema: `equity/1d` and `EQUITY/1Day`. The research loader normalizes only those
market/timeframe labels to the current engine's `Market.EQUITY`/`1d` values;
numeric fields and rows are not altered, and canonical-symbol deduplication is
the engine's existing implementation. Prices are split-adjusted. Corporate
actions therefore contribute only `cash_dividends`; split/stock-action rows
are not applied again. Dividend timestamps use the ex-date and are processed by
the existing engine before the corresponding bar cycle.

The package's PIT methodology is the supplied symmetric v5 active/inactive
provider-entity dataset, with the current production trailing scanner and
alpha-ranked observation set rebuilt strictly from bars preceding each
snapshot. Inactive symbols use their supplied terminal lifecycle bar; active
symbols have no invented terminal event. The campaign ends at the last eligible
dataset period, not beyond the supplied bars.

## Windows and timing

- Warmup: all available 2016 bars before `2017-01-03`.
- Development: `2017-01-03` through `2021-12-31`, inclusive.
- Validation: `2022-01-03` through `2026-07-31`, inclusive.
- Full period: `2017-01-03` through `2026-07-31`, inclusive.

The V3 policy is weekly: the first observed equity bar in each ISO week is the
decision opportunity. A decision uses that completed bar. Orders use the
existing engine's next tradable bar with timestamp strictly greater than the
decision and fill at that bar's open, with current slippage, half-spread,
commission, and effective-dated equity regulatory fees. No daily allocation
changes are introduced by the overlay.

For coverage, an eligible opportunity is a weekly period-start timestamp that
has a completed SPY bar and a current PIT universe snapshot. Actual decisions
are engine rebalance records at those timestamps, including valid risk-off/cash
targets. A missing record is a skipped opportunity; the runner classifies
observable missing-universe, missing-observation-bar, insufficient-history, or
unexplained causes without inferring a P&L outcome.

## Frozen volatility signal

For each completed daily SPY bar `t`, define simple closes-to-close log returns:

`r_t = ln(close_t / close_{t-1})`.

At a weekly decision, use the most recent 21 such returns whose SPY bars are at
or before the decision timestamp. Let `std_21` be the sample standard
deviation and define:

`VOL21_t = std_21 * sqrt(252)`.

The current observation is excluded from the baseline. The baseline is the
median of the prior 252 **daily VOL21 observations** strictly before the current
SPY VOL21 observation:

`BASEVOL_t = median(VOL21_{t-252}, ..., VOL21_{t-1})`.

No future bar, VIX, external index, synthetic series, SPY trend, drawdown
classifier, volatility-of-volatility, hysteresis, correlation filter, or
alternative window is permitted.

If the current VOL21 or 252 prior observations are unavailable, the candidate
fails closed to normal production V3 semantics: `scale=1.0`, with an explicit
`insufficient_history_normal_v3` status. These fallback scale values are part
of the pre-outcome development mean used for Comparator C. If `VOL21 <= 0`
after sufficient history, the same normal-V3 fallback is used and is recorded.

## Frozen Treatment B

When `VOL21 > 0` and the prior baseline exists:

`scale_t = min(1.0, BASEVOL_t / VOL21_t)`

`managed_allocation_t = 0.55 * scale_t`.

All raw V3 target weights are multiplied by the same `scale_t`; unused equity
allocation is cash. A research-only dynamic allocation cap passes
`managed_allocation_t` to the unchanged risk overlay so the post-risk managed
equity cap is `0.55 * scale_t` and can never exceed production V3. The overlay's
per-position, position-count, gross, cash, ownership, and drawdown rules remain
authoritative. No floor or upward cap beyond 1 is used.

Treatment audit rows persist decision timestamp, current-vol timestamp,
VOL21, BASEVOL, scale, status, pre-scale weights, post-scale weights,
pre-scale exposure, post-scale target exposure, post-risk target exposure,
post-fill actual exposure, and cash target.

## Control A and Comparator C

Control A is a fresh current `equity_residual_momentum_v3` run on the same
bars, PIT universe, prepared inputs, lifecycle map, execution semantics, and
cost multiplier as each other arm. It uses maximum allocation `0.55` and no
volatility overlay. Old reports are not evidence.

Before any arm's economic metrics are calculated, construct the complete
SPY-only scaler path for development eligible decision dates and freeze:

`mean_development_scale = arithmetic_mean(scale_t)`

`STATIC_ALLOCATION = 0.55 * mean_development_scale`.

This value is written to `STATIC_COMPARATOR_FREEZE.json` before Control A,
Treatment B, or Comparator C P&L is run. Comparator C is current V3 unchanged
except that its engine maximum allocation is this one constant
`STATIC_ALLOCATION`, unchanged in validation. It is not performance-selected.

## Cost stress

Each arm is run at 1x, 2x, and 3x. The multiplier applies to current equity
slippage, spread, and the effective-dated regulatory fee total. No strategy,
scaler, universe, cadence, or risk parameter changes under stress.

## Metrics and diagnostics

For A/B/C, report net total return, CAGR, annualized volatility, Sharpe,
Sortino, Calmar, maximum drawdown, longest drawdown, worst month, profit
factor when meaningful, turnover, transaction costs, average/median/minimum/
maximum equity exposure, average/maximum positions, maximum concentration, fill
count, and decision count for development, validation, and full period.

Treatment B additionally reports scale percentiles, saturation/reduction
fractions, reduced-exposure episode count and longest episode. Drawdown analysis
maps each major Control A drawdown to Treatment B using only decision timestamps.
Up/down SPY capture and exposure-normalized diagnostics are descriptive only.

## Frozen acceptance gates

All eight gates are mandatory. One valid failure yields `RESEARCH_REJECT`.
If validity is impossible because of data/implementation/validation limits, the
result is `RESEARCH_INCONCLUSIVE_DATA_OR_VALIDATION_LIMIT` and no economic
acceptance/rejection is issued.

1. **Experiment validity:** data identities, lifecycle handling, at least 90%
   weekly decision coverage in development and validation, no unexplained gap
   over 8 eligible weeks, selected-symbol identity and no-lookahead checks,
   and independent verifier all pass.
2. **Full-period risk adjustment:** B improves at least two of Sharpe, Sortino,
   Calmar versus A by at least `+0.05` absolute; none deteriorates by more than
   `0.03` absolute.
3. **Drawdown:** B maximum drawdown improves versus A by at least `2.0`
   percentage points and at least `10%` relative.
4. **Return drag:** B full-period CAGR is no more than `1.00` percentage point
   below A.
5. **Timing value:** B beats C on at least two of Sharpe, Sortino, Calmar with
   positive differences, and B CAGR is no more than `0.50` percentage points
   below C.
6. **Validation:** B validation max drawdown is lower than A, at least one of
   Sharpe/Sortino/Calmar exceeds A, CAGR drag is no worse than `1.50` points,
   and coverage validity passes.
7. **Cost robustness:** at 2x B's risk-adjusted claim does not materially
   collapse, net total return stays positive, and B is not clearly dominated by
   both A and C; at 3x net total return stays above `-20%` with no catastrophic
   cost sensitivity. For an auditable predeclared interpretation, the 2x
   risk-adjusted claim passes when all three B risk metrics are finite, every
   metric that is positive for B at 1x remains non-negative at 2x, and B is
   not below both A and C on all three risk metrics. The 3x catastrophic-cost
   check is the explicit `net_total_return > -0.20` boundary; no further
   cost-derived parameter or threshold is introduced.
8. **Deployability:** no new slots, unchanged max positions and per-position
   caps, valid gross/cash limits, no structural exposure truncation beyond the
   frozen scaler/normal V3 construction, and no treatment-specific recurring
   capacity truncation.

The only final decision strings permitted are
`RESEARCH_ACCEPT_FOR_FORWARD_PAPER_EVALUATION`, `RESEARCH_REJECT`, or
`RESEARCH_INCONCLUSIVE_DATA_OR_VALIDATION_LIMIT`. Acceptance never promotes or
enables the strategy; a future shadow/PAPER task would be separate.
