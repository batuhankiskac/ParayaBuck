# high_proximity364 mechanism review — 2026-09-05

## Scope and decision status

This is a standalone mechanism and research-design review of the frozen
`high_proximity364` screen arm. It does not change the equity V3 logic, its
`0.55` ceiling, the crypto `0.05` sleeve, gross `0.60`, cash `0.40`,
`max2crypto`/`max6total` limits, production configuration, or PAPER/LIVE
behavior. No new backtest was run. The candidate rule is frozen; the observed
full/recent screen improvement is descriptive and does not establish a joint
portfolio benefit.

The immediate portfolio question is complementarity: can this candidate help
the incumbent's weak periods after controlling for shared cash, common crypto
risk, fees, and the actual position ledger? That question can eventually be
answered with either a second signal or a veto architecture, but the evidence
must come first. No regime switch, entry veto, or promotion rule is implied by
this document.

## Exact local rule and provenance

The executable research adapter is
[`scripts/research/portfolio_expansion_crypto_screen_2026_09.py`](../../scripts/research/portfolio_expansion_crypto_screen_2026_09.py:152).
For each in-universe, eligible symbol it obtains a strict completed-D1 window
of 364 prices, then computes

`P364_t = close_t / max(close_{t-363}, ..., close_t)`.

The current close is included in the denominator. Finite scores are sorted
descending, with symbol as the deterministic tie breaker
([lines 156–167](../../scripts/research/portfolio_expansion_crypto_screen_2026_09.py:156)).
The protocol independently records the same formula and minimum history
([lines 14–21](../../docs/research/portfolio_expansion_crypto_screen_protocol_2026_09_05.md:14),
[lines 23–31](../../docs/research/portfolio_expansion_crypto_screen_protocol_2026_09_05.md:23)).

The score is only one part of the frozen adaptation. Signals use 24-hour
availability shifting after complete 24-hour aggregation, UTC 96-hour
decisions, HYST4 retention, top-2 equal targets, next-later-hour execution,
actual fees/slippage/spread, and final mark with no forced sale
([protocol](../../docs/research/portfolio_expansion_crypto_screen_protocol_2026_09_05.md:5)).
The full window is 2022-01-01 through 2026-07-31 and the recent window is
2024-01-01 through 2026-07-31. The 2022 start is a warmup/data requirement,
not evidence that the rule existed or was selected before that date.

## What mechanism is actually being tested?

`P364` measures *state relative to a trailing peak*, not a return directly.
Values near one identify coins whose current close is near their recent maximum;
low values identify coins that remain far below that maximum. Descending
cross-sectional selection therefore chooses persistent relative strength and
usually excludes unresolved drawdowns. Because the horizon is about one year,
the signal is best described as a slow price-strength or high-water-mark
continuation signal. It is not the same as 22-day return momentum, a
time-series sign rule, or a volatility control.

The likely economic channels are hypotheses. A coin near its high may have
continuing demand, gradual information diffusion, attention/herding, or a
trend that has not yet exhausted. The peak anchor can also create a behavioral
reference point: investors may react differently to assets that approach a
salient prior high. Conversely, the score can simply select market beta and
large, liquid winners. Near a market-wide high it can become a BTC/ETH or
crypto-beta proxy; after a sharp reversal it can buy assets with the most
crowded exposure. In a thin or rapidly rotating universe, the apparent signal
can also be a data-availability or survivorship artifact.

The 364-day lookback and 96-hour rebalance imply low signal turnover relative
to short-horizon momentum, but top-2 concentration remains high. HYST4 reduces
unnecessary replacement around rank boundaries; it does not make the score
independent of the incumbent. A high score can persist through the same broad
trend that makes the low-variance incumbent profitable, so low correlation of
portfolio PnL is neither required nor sufficient for independent alpha.

## Relation to the original 52-week-high literature

George and Hwang, “The 52-Week High and Momentum Investing,” *Journal of
Finance* 59 (2004), 2145–2176, study nearness to a stock's 52-week high and
report that it explains a large part of conventional momentum's profits; their
abstract says the high-nearness forecast does not reverse in the long run
([Wiley, primary publisher](https://doi.org/10.1111/j.1540-6261.2004.00695.x)).
That supports a continuation/anchoring hypothesis for the *shape* of this
feature. It does not validate this implementation: their setting is equities,
monthly portfolio formation, broad long-short cross-sectional portfolios, and
stock-history conventions. This project is long-only, top-2, 22-coin-like
configured crypto selection with a 5% sleeve, 96-hour decisions, cash and
execution constraints. A factor long-short result must not be relabeled as
evidence for this bot.

International evidence is also not a direct transfer. Han, Kang, and Ryu,
“Time-Series and Cross-Sectional Momentum in the Cryptocurrency Market: A
Comprehensive Analysis under Realistic Assumptions” (working paper, 2024),
explicitly report stronger time-series than cross-sectional evidence after
transaction-cost and liquidation considerations, with cross-sectional effects
concentrated among large winners and loser rebounds
([authors' paper](https://acfr.aut.ac.nz/__data/assets/pdf_file/0009/918729/Time_Series_and_Cross_Sectional_Momentum_in_the_Cryptocurrency_Market_with_IA.pdf)).
That is directly relevant caution: a winner-only long leg can look good while
being narrow and regime dependent.

## Primary crypto evidence and interpretation

Liu, Tsyvinski, and Goldstein, “Risks and Returns of Cryptocurrency,” *Review
of Financial Studies* 34 (2021), 2689–2727, find strong crypto time-series
momentum and crypto-specific network/attention predictors
([Oxford Academic](https://doi.org/10.1093/rfs/hhaa113)). This supports the
plausibility of continuation in crypto, but their time-series result is not a
364-day cross-sectional high-water-mark test and does not show incremental
alpha against this incumbent.

Liu, Tsyvinski, and Wu, “Common Risk Factors in Cryptocurrency,” *Journal of
Finance* 77 (2022), 1877–1919, identify market, size, and momentum factors that
capture cross-sectional expected crypto returns
([Wiley, primary publisher](https://doi.org/10.1111/jofi.13119)). Thus the
candidate's observed contribution must be decomposed against market, size,
momentum, and concentration exposures before being called a second eye.

Zaremba, et al., “Cryptocurrency factor momentum,” *Quantitative Finance* 23
(2023), 1853–1869, analyze more than 3,900 coins and report that factor
momentum is principally price momentum, with strong skew in crypto market
capitalization ([Taylor & Francis, primary publisher](https://doi.org/10.1080/14697688.2023.2269999)).
This reinforces the need for dated universe membership, BTC/ETH concentration,
and market-cap/illiquidity controls. Begušić and Kostanjčar, “Momentum and
liquidity in cryptocurrencies” (2019), report stronger momentum among liquid
cryptocurrencies and long-only liquid-winner behavior
([arXiv author manuscript](https://arxiv.org/abs/1904.00890)); it is useful
mechanism evidence but remains a different portfolio design and must not be
treated as proof for this bot.

Overall classification: `high_proximity364` is presently a *momentum-family
price-strength proxy with possible crypto-beta, size, liquidity, and
concentration exposures*. It is not established independent alpha. Its
portfolio value could still be incremental if it changes which coins are held
or when risk is avoided during incumbent weakness after common-ledger and
cash-control adjustments. The present curve blend cannot tell those cases
apart.

## What would falsify the complementarity case?

The following finite gates should be pre-registered before any deep study.
They are falsifiers and diagnostics, not invitations to retune the rule.

1. **Coverage/cash artifact.** Compare eligible-symbol counts, time in cash,
   candidate allocation, and incumbent allocation at every decision. If the
   apparent gain is explained by missing-history cash, unequal opportunity to
   trade, or a different terminal mark, the complementarity claim fails.

2. **BTC/ETH and concentration.** Report holdings, contribution, and returns
   with BTC and ETH separately identified; compute leave-one-symbol-out and
   top-two concentration views using the frozen rule. If gains disappear when
   one dominant coin is removed, classify the result as concentration
   dependence, not broad second-signal evidence.

3. **Dated-pool survivorship.** Reconstruct the eligible pool as-of each
   decision, including delisted/dead assets where the data contract permits.
   If a contemporaneous pool materially weakens or reverses the result, the
   configured-pool screen is not portable evidence.

4. **Common-risk/exposure versus cash control.** Use one shared ledger at the
   same 5% crypto ceiling and compare incumbent-only, candidate-only,
   fixed-capital half-cash, and combined candidate/incumbent outcomes. Attribute
   differences to crypto beta, size, momentum, and common holdings where
   possible. If the candidate does not improve incumbent weak periods after
   this control, the second-eye case fails.

5. **Cost robustness.** Preserve the frozen rule and test the already specified
   1x/2x/3x cost cases plus the actual ledger fees. If the contribution exists
   only at 1x or disappears under plausible realized costs, classify it as
   cost-fragile. No new cost or parameter sweep is authorized by this review.

6. **Regime heterogeneity without cherry-picking.** Predeclare calendar and
   market-state partitions, report every partition, and use an aggregate rule
   for interpretation. If gains occur only in one selected period, or if weak
   periods are not improved across the predeclared partitions, reject a
   universal all-regimes claim. Do not choose periods after inspecting results.

## Recommended comparison protocol (design only; no run)

Freeze the current candidate and incumbent definitions and run one causal
shared-ledger replay over the screen's full and recent windows, with the same
24-hour completed-D1 shift, 96-hour cadence, HYST4/top-2/equal policy, next
available execution, fees, slippage, spread, cash, and terminal mark. Keep
equity V3 and all account caps fixed. Produce synchronized hourly and daily
returns, actual fills, fee totals, cash, marked positions, eligibility counts,
time-in-cash, exposure/holdings, and every predeclared regime partition.

The minimum fixed comparison set is: incumbent-only at the 5% crypto cap;
candidate-only at the same cap; a half-cash diagnostic using initial capital;
and a combined shared-ledger allocation that splits the unchanged 5% crypto
budget equally. The combined ledger must enforce max two crypto positions and
max six total positions, so it cannot be inferred from a 50/50 NAV curve
average. Report marginal return, drawdown, drawdown overlap, downside,
turnover, fees, and contribution specifically during incumbent drawdowns or
weak predeclared partitions. Use no parameter/weight search, no
good-period-only selection, no automatic promotion, and no regime switch.

Interpretation should be staged: first establish accounting and coverage;
then determine whether combined results beat the fixed-capital and cash
controls; then test whether the improvement survives concentration, dated-pool,
common-risk, cost, and regime gates. Only if those gates pass should the team
decide whether the implementation is best treated as an independently
allocated second signal or a narrowly evidenced entry filter. The current
evidence does not decide between those architectures.

## Current evidence and confidence

The completed screen is a valid retrospective research artifact pinned to the
attempt-03 manifest and source hashes. In the independent audit, the full
1x/3x high-proximity versus low-variance 50/50 curve blend returned 5.435% /
4.440%, versus 1.913% / 1.019% for the half-cash control; recent returned
2.348% / 1.651%, versus 1.060% / 0.433%. Drawdown was better than the incumbent
blend by roughly 1.7–2.3 percentage points, but the blend is initial-capital
curve averaging and is not a shared account. The audit itself says these are
diagnostic comparisons and do not prove shared fills, order competition,
netting, or full-bot output.

Confidence is **high** in the local formula, cadence, data-shift, and scope
description; **moderate** that the feature belongs to a continuation/momentum
family based on the cited literature; and **low** for independent alpha or
joint portfolio benefit until the finite gates above are run. No production,
PAPER, LIVE, frozen-manifest, or prior literature/master report was edited.
