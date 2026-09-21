# Final design decision

`DESIGN_REJECT_NO_DEFENSIBLE_HYPOTHESIS`

The signal is measurable, but the available evidence does not support adapting
the diversified, small/illiquid-sensitive anomaly into ParayaBuck's liquid
top-six or eventual one-slot architecture. The fixed scanner removes the core
published population, modern U.S. large/liquid long-only evidence is
insufficient, and incomplete PIT product taxonomy makes extreme ranks
ambiguous.

# Starting repository state

The branch was created from clean `main` at
`d3dea037789c16c335744b56d216289a4536096c`, equal to `origin/main` after fetch.
The starting configuration was PAPER-only: 55% V3 equity target, 5% crypto
target, 60% gross target, 40% cash target, six account-wide positions, 10%
equity-name cap, at most two crypto positions, and LIVE disabled with zero
capital.

# Primary Bali–Peng–Shen–Tang specification

The final RFS article sorts NYSE/AMEX/NASDAQ common stocks into NYSE-breakpoint
deciles each month, using current-month liquidity shock to predict month `t+1`.
It reports equal- and value-weighted portfolios over August 1963–December 2010.
At least 15 valid daily return observations are required per month.

# Exact liquidity-shock formula

For stock `i`, day `d`, month `t`:

`ILLIQ_i,t = mean_d(|R_i,d| / VOLD_i,d) * 10^6`

`LIQU_i,t = -(ILLIQ_i,t - mean(ILLIQ_i,t-12 ... ILLIQ_i,t-1))`

The final published formula is not divided by a prior standard deviation. That
standardized form belongs to an earlier working paper/robustness variant.

# Sign convention

Positive `LIQU` means current illiquidity is below its trailing 12-month mean:
liquidity improved. Higher positive scores rank first.

# Amihud implementation

The structural audit uses absolute split-adjusted close-to-close price return
divided by split-adjusted close times split-adjusted volume, averages at least
15 valid daily terms, and requires 12 consecutive prior valid months. This is a
diagnostic reconstruction. Local bars do not expose the exact CRSP total-return
basis, so exact return parity is not claimed.

# Dollar-volume / split semantics

Alpaca source receipts specify SIP bars with `split` adjustment. Reciprocal
price/share-volume adjustment preserves return, dollar volume and the Amihud
term; two-close 4-for-1 and 1-for-8 synthetic fixtures pass. The receipt also
records adjacent stored AAPL 2020 and GE 2021 rows, which demonstrate price-
basis continuity but do not independently prove the provider's transformation
algorithm. Nonpositive volume is never inserted into the Amihud formula.

# Primary long-leg evidence

The original highest-shock decile has 1.69% EW monthly return and 0.48% FF3
alpha (`t=6.88`); VW is 1.60% and 0.45% (`t=6.01`). Middle deciles are near
1.0% returns and near-zero alpha; the low decile has 0.51% EW return/-0.75%
alpha and 0.43% VW/-0.73% alpha. The long leg therefore exists independently,
but only in a diversified portfolio. About 39% of EW spread alpha is the high
leg and 61% is the low leg.

`POSITIVE_LIQUIDITY_SHOCK_LONG_LEG_SUPPORTED`

# Small/liquid-stock dependence

Original subsamples retain a significant large-stock and most-liquid spread,
but magnitudes are much larger for small and illiquid stocks. Thus the narrow
original-sample verdict is `LIQUID_STOCK_EFFECT_SUFFICIENT_FOR_TEST`; it does
not establish a modern, concentrated liquid-stock strategy.

# Modern/post-publication evidence

Later international work supports a broad liquidity-shock phenomenon, but no
clean post-publication U.S. large/liquid concentrated long-only replication was
found. Recent international evidence also finds the asymmetry is driven more
by liquidity decreases, with positive increases small or mostly insignificant.

`MODERN_LIQUIDITY_SHOCK_EVIDENCE_INSUFFICIENT`

# External replication

Japan supports positive-versus-negative shock continuation, but its mechanism
evidence differs from the original U.S. interpretation. Korea directly tests
the shock and finds an attractive high leg overall, yet its most-liquid subset
does not validate the required thesis. Global evidence is diversified and
cannot establish U.S. one-name alpha density.

# Distinctness from abnormal volume

The score is not the closed AV hypothesis: it uses `|return|/dollar volume`, a
12-month illiquidity-level reference and no one-day return band. Local
contemporaneous decomposition shows negligible correlation with log dollar-
volume change (`0.015`) but material relationships with absolute-return change
(`-0.601`) and current illiquidity (`-0.923`).

`LIQUIDITY_SHOCK_DISTINCT_FROM_CLOSED_AV_HYPOTHESIS`

# Distinctness from V3 / IVOL

V3 is signed 12-1 residual momentum with 6/9/12 ranks, an IVOL rejection and a
weekly cadence. `LIQU` is an unsigned monthly change in price impact. Literature
controls indicate incremental diversified information, but local structural
correlation with contemporaneous volatility is `-0.600`, so V3's IVOL screen
will overlap materially. No local outcome-controlled regression was run.

# Scanner interaction

The fixed scanner requires price at least $5, trailing 20-valid-bar median
dollar volume at least $20M, 95% completeness and ATR between 0.5% and 12%,
then retains the top 300 by dollar volume. This is not a neutral investability
floor for an anomaly strongest in small/illiquid stocks.

`SCANNER_REMOVES_CORE_ANOMALY_POPULATION`

# Broad US_EQUITY limitation

The canonical history contains broad `US_EQUITY`, not a complete PIT
common-stock-only taxonomy. Within-security normalization reduces static-level
bias but cannot remove product-specific flow shocks in ETFs, ETNs, preferreds
or structured products. Current labels cannot repair historical identity.

`UNIVERSE_LIMITATION_INVALIDATES_LIQUIDITY_SHOCK_TEST`

# Structural opportunity coverage

No future returns were used. Through the canonical endpoint, 11,681,230 rows
were included and 5,602 partial-endpoint rows excluded. There are 676,349
zero-volume rows (5.79%); they remain scanner bars but are excluded from Amihud.
Lifecycle matching excluded no rows, and duplicate symbol/session data aborts
the audit. Across 115 scoreable months from
2017 onward, every month has at least six scanner-scoreable observations; the
monthly count ranges from 234 to 289 with median 280. Measurement opportunity
therefore exists, but opportunity count is not economic support.

# Six-slot feasibility

Academic evidence concerns diversified deciles, not six issuers. Extreme-tail
monotonicity and Fama–MacBeth slopes do not prove top-six robustness, especially
after a scanner that changes the tested population.

`LIQUIDITY_SHOCK_PREMIUM_REQUIRES_DIVERSIFICATION`

# One-slot feasibility

No reviewed evidence establishes reliable alpha density in the single most
extreme issuer-level shock. Event and measurement noise become undiversified.

`ONE_SLOT_ALPHA_DENSITY_UNSUPPORTED`

# Timing / causality

The faithful sequence is all daily observations in month `t`, decision only
after month end, execution at the next tradable open, and holding through month
`t+1`. The repository has an exact next-open helper, but its generic monthly
path is one session late; any future research harness would have to use the
exact causal helper and effective-dated lifetimes explicitly.

# Red-team findings

The 24-question red team found the formula and sign reconstructible and split
basis structurally defensible. It rejected the design because modern/liquid
evidence, universe integrity, six-name support and one-slot support fail. These
are evidence failures, not a reason to substitute a new formula or family.

# No-economic-peek verification

The audit calculated only contemporaneous components, coverage, missingness and
split invariance. It did not calculate candidate forward returns, P&L, NAV,
CAGR, Sharpe, Sortino, drawdown, IC, hit rate, future-return buckets, economic
parameter comparisons or candidate/V3 outcome correlation. External published
returns were used only as literature evidence.

# Production state

No strategy was registered. V3, crypto, scanner, account limits, PAPER and LIVE
are unchanged. No order was placed. The rejection authorizes no frozen
economic test, combination test or deployment.

# Files

All campaign artifacts and the reproducible structural-only audit script are in
`research/positive_liquidity_shock_design_v1/`. Because the decision is REJECT,
neither `CANDIDATE_DESIGN.md` nor `FUTURE_TEST_PROTOCOL.md` exists.

# Final research branch SHA

The commit containing this report cannot self-embed its own SHA. The final
branch SHA is recorded in the completion message after the commit is created.
