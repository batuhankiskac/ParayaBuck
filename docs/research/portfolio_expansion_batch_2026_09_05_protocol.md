# Portfolio expansion batch protocol — 2026-09-05

**Status:** pre-results protocol; no portfolio expansion run is authorized by this document.

## Fixed baseline and capital contract

The live/PAPER equity identity remains `equity_residual_momentum_v3` with its
existing maximum allocation of 0.55 and parameters unchanged. This batch never
replaces, retunes, or rescales that equity strategy. The configured crypto
identity remains `crypto_low_variance_hysteresis_v1` with maximum allocation
0.05. Configuration evidence is `config/settings.yaml:137-174`.

The account starts at 100,000 in the research engine. Shared risk limits are:
maximum gross exposure 0.60, minimum cash buffer 0.40, maximum six positions,
maximum single position 0.10, maximum crypto exposure 0.10, maximum crypto
position 0.05, and maximum two crypto positions (`config/settings.yaml:175-184`).
These are ceilings, not promises of realized exposure. No candidate may add
capital, increase a ceiling, or bypass the existing risk overlay.

The first portfolio-contribution batch therefore keeps equity V3 exactly fixed
and tests C3 and C4 as fixed crypto-budget mixtures with the incumbent. A
50/50 mixture means 0.025 account weight for the incumbent and 0.025 for one
candidate, for a total crypto budget of 0.05. This is mathematically within
the crypto sleeve and does not add capital, but it requires one deterministic
combined crypto target set and shared ledger. C2 low-beta equity is retained
as a standalone `REJECT` record and is outside this batch because the equity
side is fixed.

## Candidates and controls

The baseline is one shared-ledger portfolio containing fixed equity V3 and the
current crypto incumbent at 0.05. Each mixture arm contains the incumbent at
0.025 plus exactly one candidate at 0.025; cadence, top-2 equal weights,
HYST4 retention, fees, execution, and terminal policy remain unchanged. C3
changes only the frozen MAX rank score; C4 changes only the frozen signed-risk
score. C3 standalone economic output is at
`data/research/crypto_max_return_economic_2026_09/attempt_01_canonical/economic_results.json`;
C4 standalone output is at
`data/research/crypto_signed_risk_economic_2026_09/attempt_01_canonical/economic_results.json`.
Both remain standalone evidence; neither establishes portfolio contribution.
The incumbent-only baseline is the third arm. A candidate cannot contribute
two independent top-2 crypto portfolios while the account cap is two crypto
positions: the composite must rank or merge targets deterministically and
fail closed when the union exceeds the cap.

For each arm, use full `2021-04-01T00:00:00Z` through
`2026-07-31T23:59:59Z` and recent `2024-01-01T00:00:00Z` through
`2026-07-31T23:59:59Z`, fresh engine/reset per arm-window-cost cell, and 1x/2x/3x
costs. The primary comparison is the fixed combined baseline versus the
50/50 incumbent-candidate mixture. A secondary diagnostic records C3/C4
overlap with the incumbent; it is not a gate by itself.

## Existing NAV and curve evidence

`data/research/full_survivorship_2026_08/full_v3_results_v5.json` contains
aggregate V3 NAV metrics for multiple terminal policies but no timestamped
portfolio curve. `data/research/crypto_signed_risk_economic_2026_09/attempt_01_canonical/economic_results.json`
contains 18 cell-level equity curves, timestamps, fills, fees and cash ledgers;
the MAX canonical result has the corresponding standalone cell structure.
`data/research/archive/2026_08/regime_execution_2026_08/results.json:40-45`
records an earlier synthetic sleeve frontier, explicitly marked heavy
backtest deferred. `data/research/archive/2026_08/portfolio_construction_2026_08/results.json:12-73`
contains a lightweight 2024–2026 proxy for risk-parity, sector-cap and
vol-target construction, not a synchronized current-V3 portfolio result.
These artifacts can seed diagnostics only when dates, costs, cash treatment
and terminal semantics match; otherwise the comparison is blocked.

Because the archived V3 artifact has no curve, a decision-grade mixture must
regenerate the fixed V3 leg through the existing product/backtest path with
the canonical equity bars, the same inclusive evaluation endpoints, causal
universe snapshots, next-tradable-open execution, and the declared terminal
policy. `run_product_backtest` supports explicit `start`, `end`, cost,
prepared inputs, universe, and `persist=False` research calls
(`src/parayabuck/backtest/product.py:208-307`); its output can remain isolated
from runtime persistence. The old aggregate V3 numbers cannot be silently
combined with timestamped crypto curves.

## Required measurements

Every candidate must produce one shared account ledger, not a post-hoc sum of
independent returns. Required outputs are total return, CAGR, Sharpe, maximum
drawdown, turnover, fees, fills, timestamped equity curve, cash/equity
reconciliation, actual positions, and terminal evidence. The ledger must
retain the fixed V3 sleeve and enforce the shared gross, cash, position and
crypto limits at every fill.

Report:

- net PnL correlation between candidate sleeve and incumbent sleeve, using
  paired daily account or sleeve returns with an explicit valid denominator;
- marginal total return, CAGR, Sharpe and maximum drawdown versus the fixed
  combined baseline;
- contribution by sleeve and by candidate, turnover and fee delta;
- fraction of days with residual headroom and fraction of candidate signals
  suppressed by shared limits;
- full/recent results at all three costs and a no-future-data/as-of audit.

A candidate is not called complementary merely because its standalone return
is positive or its rank overlap is low. The portfolio decision requires
positive marginal value after costs, no material breach of baseline drawdown,
and stable evidence across the declared windows/costs. Exact numeric gates
must be frozen in a later execution manifest; this protocol does not invent a
post-result threshold.

## Curve blending versus shared-ledger evidence

Diagnostic curve blending multiplies or adds already-generated standalone NAV
curves after aligning timestamps and capital weights. It is useful for a cheap
correlation and rough diversification screen, but it cannot model order
competition, shared cash, crypto exposure caps, position limits, fill timing,
fees on changed quantities, or candidate suppression.

The decision-grade test is a fresh shared-ledger replay using the fixed V3
strategy plus exactly one crypto arm. It must calculate fills and fees from the
combined account and preserve the current equity sleeve. A blended curve may
be reported as diagnostic only and cannot support PAPER/LIVE or a portfolio
promotion decision.

## Smallest research-only implementation seam

`run_product_backtest` constructs one `PortfolioStrategy` and one
`PortfolioBacktestEngine` per call (`src/parayabuck/backtest/product.py:208-307`).
It cannot express two independently scored crypto strategies as separate
orders without a research-local composite dispatcher. The narrow adapter is
one new research strategy implementing the existing `PortfolioStrategy`
contract: call the incumbent and candidate scorers on the same completed-bar
context, scale each sleeve target to 0.025, merge equal-symbol targets, sort
deterministically, and apply the existing two-crypto-position rule. The
dispatcher must preserve V3 as the separate fixed equity strategy and pass the
combined crypto targets to one native engine/ledger. It must fail closed when
the merged target set exceeds the configured crypto position cap rather than
silently choosing a winner.

Minimum meaningful tests are: same-as-of paired histories; 0.025 + 0.025
crypto allocation and total 0.05 cap; duplicate-symbol merge; deterministic
tie handling; union-of-three-or-more target rejection under max-two crypto
positions; shared cash/gross/position enforcement; next-hour-open fills;
fees/turnover from actual merged orders; fresh-instance equivalence; and
terminal final-mark/no-forced-sale parity. This requires a research-only
composite/adapter and focused tests, but no production registry or config edit.

## Finite batch and stop rules

The bounded batch is: incumbent, C3 50/50 mixture, and C4 50/50 mixture; two fresh
windows and three cost tiers (18 cells). No parameter sweep, strategy hopping,
new regime selector, or extra candidate arm is allowed. Missing curves,
nonfinite metrics, mismatched source/data hashes, unavailable fill/ledger
fields, or unresolved as-of/limit semantics yield `BLOCKED` with no fabricated
zero values. Results remain research-only; PAPER/LIVE promotion is outside
scope. Existing standalone C2/C3/C4 artifacts and the fixed V3 configuration
remain immutable.

## Frozen crypto mixture diagnostic (derived curves only)

Before any shared-ledger implementation, a bounded diagnostic uses the already
completed native crypto outputs. It evaluates two fixed 50/50 mixtures: 5%
low-variance baseline with 5% MAX, and 5% low-variance baseline with 5%
signed-risk. Each standalone account NAV is normalized to its own initial
capital and the two normalized curves are averaged, so each component carries
2.5% effective account weight and the mixture retains the 5% crypto budget.
There is no weight sweep, retuning, new run, or joint fill simulation. The 0.025 + 0.025 figures are research target weights in the normalized diagnostic; they are not realized or production allocations. Likewise, the fixed 0.55 equity value elsewhere is a configured ceiling, not a guarantee of actual equity exposure.

The matrix is exactly two candidate mixtures × two declared windows × 1x/2x/3x
costs = 12 derived cells. Curves are synchronized on common UTC timestamps;
daily returns use the last available observation at each UTC day boundary.
Each mixture reports total return, CAGR, Sharpe, maximum drawdown, worst month
when derivable, paired return correlation, and delta versus the 5% low-variance
baseline. Missing, nonfinite, nonaligned, or inconsistent initial-NAV data is
BLOCKED. The calculation is a diagnostic approximation: weighted standalone
fees, turnover and terminal marks are reported from source curves, but order
competition, shared cash, max-two crypto netting, and shared risk limits are
unknown. It cannot promote or eliminate C3/C4 and is not a combined-bot or V3
result.
