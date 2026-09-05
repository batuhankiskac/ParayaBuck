# Crypto signed-risk economic protocol — 2026-09-05

**Status:** immutable pre-results protocol; not a frozen run authorization.

This study compares `signed_risk` (C4 signed downside ratio), the native
`low_variance` incumbent, and the fixed `dollar_volume` comparator across three
arms, two fresh-reset windows, and 1x/2x/3x costs: exactly 18 cells. Full is
`2021-04-01T00:00:00Z..2026-07-31T23:59:59Z`; recent is
`2024-01-01T00:00:00Z..2026-07-31T23:59:59Z`. Metrics are account-level with
5% crypto allocation and 95% idle native cash at zero yield.

The candidate uses `sum(min(r,0)^2)/sum(r^2)` over 22 completed single-day
returns, ascending score then symbol, 23 consecutive completed D1 prices,
top-2 equal weights, HYST4 retention, and a fixed 96-hour UTC schedule. The
incumbent and controls retain native eligibility, observation, execution, and
risk semantics. The liquidity comparator ranks trailing 20 completed D1
`close * volume` medians descending with symbol tie-break.

Technical validity requires exactly one record for each arm/window/cost cell,
finite required metrics, matching source/input/protocol/settings hashes, causal
as-of data, and an actual fee/terminal ledger. Any technical failure is
`BLOCKED`; no fabricated zero or repaired accounting is accepted. Daily signals
use completed shifted D1 availability; execution uses the unshifted 1h feed and
first strictly later tradable open. Terminal handling is native final marking
without forced crypto liquidation.

Economic gates are evaluated only after technical validity:

- **R1:** signed-risk total return > 0 in both windows at 3x cost.
- **R2:** full-window 1x signed-risk Sharpe > incumbent Sharpe, OR signed-risk
  drawdown <= 0.90× incumbent drawdown, positive CAGR, and CAGR >=
  `max(0, 0.90× incumbent CAGR)`.
- **R3:** signed-risk drawdown <= 1.5× incumbent drawdown in both windows at 3x.
- **R4:** signed-risk total return >= 0 in both windows at 2x.
- **R5:** signed-risk total return >= liquidity-control return in both windows at 3x.

Any failed gate is `REJECT`; all five passing is
`RETAIN_FOR_RESEARCH`. Venue-PIT completeness is unsupported by the configured
pool/bar-availability proxy, so no result supports PAPER or LIVE. Fold and BTC
regime views are descriptive only and never gates.
