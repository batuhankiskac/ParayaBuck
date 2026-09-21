# Primary literature review

## Published CMP classifier

Cohen, Malloy and Pomorski (2012, *Journal of Finance*) classify a trader only
after at least one purchase or sale in each of three preceding consecutive
calendar years. At the start of the next year, the trader is routine when a
trade occurred in the same calendar month in all three years; otherwise the
eligible trader is opportunistic. A person without the three-year history is
**unclassified**, not opportunistic.

In the main specification, routine is absorbing: once routine, all subsequent
trades remain routine. Opportunistic insiders are checked again each year and
become permanently routine if their prior three years later establish a
same-month pattern. The Internet Appendix's rolling-window variant, which can
reclassify in both directions and retains the last class when a three-year
window is unavailable, is a separate robustness test. The article also tests a
trade-level variant in which a routine insider's established routine-month
trades are routine and other-month trades opportunistic; that too is
robustness. `Quasi-routine` belongs to an older draft and is not part of the
final method.

The original data are Thomson Reuters Form 4 records from 1986-2007; return
tests use 1989-2007. CMP retained reported open-market purchases/sales and
excluded options exercises and private transactions. Its insider universe
included decision-making officers, directors, and greater-than-10% beneficial
owners. It does not validate a CEO/CFO-only, nonexecutive-only, or entity-only
scope.

## Purchase long leg

An issuer enters the opportunistic-buy portfolio in month `t+1` if any insider
made an opportunistic purchase in month `t`; portfolios rebalance monthly and
hold one month. Table IV reports positive equal-weight buy-leg alpha under all
models and positive value-weight alpha under most models: approximately 0.87%
CAPM, 0.64% FF3, 0.52% Carhart, and 0.72% five-factor per month, with the
Carhart value-weight estimate marginal.

The headline 0.82% value-weight abnormal return is the opportunistic
buy-minus-sell portfolio, not the purchase-only leg. Moreover, value-weight
opportunistic-minus-routine **buy** differences are positive but not
statistically significant across the reported models. The incremental CMP
classification case is therefore materially weaker for a liquid long-only
mandate than the headline suggests.

Appendix A11 finds opportunistic-buy predictability in both halves of a prior-
December market-cap split, including about 0.55% for the large half. This is a
median size split, not a $20 million ADV or institutional-liquidity test.

## Signal strength

Appendix A9 finds that `log(1 + number of opportunistic buys at the issuer in
the prior month)` predicts next-month returns. Since log is monotone, raw
transaction count has the same rank order. This is the strongest primary
support for one non-blended signal. It is transaction count, not distinct
insider count. Dollar- and shares-outstanding-weighted portfolios in Appendix
A8 are weighting tests, not evidence for a top-one dollar-size rank.

Neither the article nor appendix validates concentrating a diversified
issuer-month portfolio into one slot or a top-k rule. The roughly six-month
event-time rise is a combined four-leg spread, not proof of six-month buy-only
persistence. The clean purchase-only hold is month `t+1`.

## Lakonishok and Lee

Lakonishok and Lee (2001) find purchases more informative than sales, but much
of the cross-sectional predictability is driven by small firms. Their causal
public annual strategy ranks on a six-month net-purchase ratio and holds for a
year; it is not CMP opportunistic classification or evidence for a liquid
single-name slot. Greater-than-10% owner-only results are not a monotonic bullish
case and do not justify an entity/large-owner-only scope.

## Sources

- [CMP article and appendix combined manuscript](https://dash.harvard.edu/server/api/core/bitstreams/7312037e-2b77-6bd4-e053-0100007fdf3b/content)
- [CMP Internet Appendix](https://www.afajof.org/wp-content/uploads/files/supplements/Decoding_Inside_Information-.pdf)
- [CMP DOI](https://doi.org/10.1111/j.1540-6261.2012.01740.x)
- [Lakonishok-Lee NBER manuscript](https://www.nber.org/system/files/working_papers/w6656/w6656.pdf)
