# Primary literature review

## Scope and source status

This review reconstructs the announcement-return signal from two primary sources. It does not calculate a ParayaBuck candidate return.

1. Michael W. Brandt, Runeet Kishore, Pedro Santa-Clara, and Mohan Venkatachalam, *Earnings Announcements are Full of Surprises*, January 22, 2008, [author-hosted UCLA PDF](https://www.anderson.ucla.edu/documents/areas/fac/finance/santa_clara_surprises.pdf), also catalogued as [SSRN 909563](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=909563). The document identifies itself as a dated working-paper version, not a journal publication. The local source copy used for verification had SHA-256 `d55a0f2cc116437957f282345e31699a0e50598256e53fca12d1d3fc5b9e5b10` and 37 PDF pages.
2. Louis K. C. Chan, Narasimhan Jegadeesh, and Josef Lakonishok, “Momentum Strategies,” *Journal of Finance* 51(5), 1681–1713, 1996, [publisher record and DOI](https://doi.org/10.1111/j.1540-6261.1996.tb05222.x), with a readable [archival article copy](https://www.cfrn.com.cn/uploads/fileupload/2396/paper/0908311241015993.pdf).

The 2008 Brandt et al. manuscript is the most developed primary specification of “EAR” in the requested family. Chan et al. call their related variable `ABR`, not `EAR`, and use a materially different event window and benchmark.

An older [June 2007 author-hosted draft](https://www.anderson.ucla.edu/documents/areas/fac/finance/ear.pdf) contains different Table 2 estimates: EAR5 is 1.66% at 60 days and 2.21% at 120 days, and the 240-day spread is 6.28%. Those numbers must not be mixed with the later January 22, 2008 version reviewed here, whose corresponding values are 1.99%, 2.62%, and 7.55%. The later version also changes the abstract's annual EAR spread from 6.3% to 7.55%.

## Brandt et al. (2008): sample and signal

The sample spans January 1987 through December 2004. Eligible firms appear in both Compustat Industrial Quarterly and CRSP and must permit calculation of size and book-to-market. The authors exclude financials (two-digit NAICS 52), utilities (two-digit NAICS 22), and stocks whose CRSP daily price is below $5 on the trading day before the Compustat quarterly earnings-announcement date (`RDQE`). They do not report a firm-event count in the manuscript. See pp. 8–10.

For firm `i`, fiscal quarter `q`, and Compustat announcement date `t`, equation (4) defines:

```text
EAR(i,q) = product[j=t-1..t+1](1 + R(i,j))
           - product[j=t-1..t+1](1 + FF(i,j))
```

`FF(i,j)` is the daily return of the size/book-to-market Fama–French portfolio containing firm `i`; the authors describe six portfolios formed from two size and three book-to-market groups. Thus the published EAR is a difference between two compounded three-day returns. It is not raw return, stock return minus SPY, a market-model residual, or the sum of daily stock-minus-benchmark returns.

The paper calculates current-quarter EAR for each eligible stock but determines the five portfolio cutoffs from the distribution of EAR observations in quarter `q-1`. The stated reason is to avoid using the completed distribution of quarter `q` while forming positions as firms announce. The manuscript does not specify a minimum prior-quarter sample, tie handling, exchange-only breakpoint universe, or fallback when a cutoff cannot be estimated. It also does not describe a continuous percentile score; EAR itself is the continuous observable and the reported tests use its lagged-quarter quintile bins.

## Formation and return clock

Two nearby prose statements are imprecise: section 4.1 says portfolios are formed “the day after the earnings announcement date” and returns commence the day after portfolio formation. The methodology immediately before it is precise: the three-day signal window ends at `t+1`, and equation (5) accumulates post-announcement abnormal returns from `t+2` through `t+n`. Table 2 repeats those limits. Consequently, no causal implementation can know the paper's complete EAR before the `t+1` completed return is available.

The source uses daily close-to-close CRSP returns and never specifies an intraday release time, an opening-price trade, or special treatment of pre-market, after-close, weekend, or holiday releases. “Enter at the `t+2` open” is therefore an implementation adaptation, not an execution rule tested by the paper. The equation supports beginning the evaluated post-window return on trading day `t+2`; it does not establish how much of that day's close-to-close return remains after a `t+2` open fill. The formula's inclusive range `t+2..t+n` also contains `n-1` indexed sessions even though the table labels the horizons 30, 60, 90, 120, 180, and 240 trading days. That notation should be preserved as a source ambiguity rather than silently repaired.

## Exact primary results

Brandt et al. Table 2 Panel A reports the following cumulative abnormal returns for the standalone EAR sort. `*` denotes 1% significance and `^` denotes 5%; the table gives no t-statistics. These are portfolio-level averages, but the manuscript does not state whether Table 2 aggregates constituent observations by equal weight, value weight, or another weighting rule.

| Horizon | EAR1 | EAR2 | EAR3 | EAR4 | EAR5 | EAR5−EAR1 |
|---:|---:|---:|---:|---:|---:|---:|
| 30 | −0.39%^ | −0.16% | 0.05% | 0.33%^ | 1.08%* | 1.47%* |
| 60 | −1.28%* | −0.21% | −0.16% | 0.76%* | 1.99%* | 3.27%* |
| 90 | −1.31%* | −0.34% | −0.05% | 0.78%^ | 2.42%* | 3.73%* |
| 120 | −1.45%* | −0.42% | −0.15% | 0.61% | 2.62%* | 4.07%* |
| 180 | −2.78%* | −1.05%* | −0.20% | 0.62% | 3.56%* | 6.35%* |
| 240 | −2.94%* | −1.06% | −0.53% | 1.09% | 4.61%* | 7.55%* |

The highest EAR quintile is independently positive and significant at every reported horizon. The evidence is not limited to assigning a long-short spread to the long side. The ordering is broadly increasing but not strictly monotone at every horizon: at 60 and 90 days EAR2 is below EAR1, and at 240 days EAR3 is below EAR2.

Table 2 Panel B reports subsequent three-day announcement-window returns for EAR5 of 0.61%, 0.55%, 0.59%, and 0.30% in quarters 1–4; the first three are significant at 1%, while the fourth is also marked significant even though the EAR5−EAR1 spread is −0.02% and insignificant in quarter 4. This is evidence about recurring information arrival, not a clean instruction to refresh a 60-day holding.

## EAR versus SUE and price momentum

EAR and SUE are associated but not interchangeable. In Table 1 only 28% of top-SUE observations are also top-EAR, while 14% of bottom-SUE observations fall in top-EAR; analogous discordance exists in the opposite tail. In the 60-day independent two-way sorts of Table 3, EAR5−EAR1 is positive across every SUE quintile (1.53%, 2.17%, 2.30%, 3.40%, and 3.97%, all marked at 1%). The nonoverlap test, which removes the joint extreme cells, still reports a 2.01% 60-day EAR hedge return marked at 1%. This supports incremental EAR information without requiring SUE in an EAR-only candidate.

Table 6 performs a monthly implementation against price momentum. For month `m`, EAR ranks use announcements in months `m-2` through `m-4`; momentum uses returns from `m-13` through `m-2`; both are held during month `m`. The standalone monthly EAR quintile returns run from −0.16% to 0.53%, producing a 0.69% spread marked at 1%. Across independent momentum quintiles, the EAR5−EAR1 spread remains 0.20% to 0.70% per month. The authors also report less than 35% overlap between the highest EAR and highest momentum quintiles. This is evidence against complete subsumption by conventional 12–2 price momentum, but it is based on diversified quintiles and a lagged monthly construction.

## Financials, utilities, and large stocks

The main sample excludes financials and utilities. Footnote 2 says that including them leaves the authors' inference unchanged: the *combined SUE-and-EAR* strategy earns 12.1% over 240 trading days versus 12.48% without them. This is not an EAR-only robustness table and does not establish the long-only EAR5 return for those sectors. It supports only the narrower conclusion that their inclusion was not fatal to the paper's combined-strategy inference.

Table 7 repeats the independent EAR×SUE sorts for the top 1,000 firms in the CRSP universe. The EAR spreads remain positive in many cells, but the effect is reduced. For the large-stock EAR5 row at 60 days, the five SUE-conditioned long returns are 1.56% (5%), 1.44% (1%), 2.00% (1%), 2.02% (1%), and 2.97% (1%). At 240 days they are −0.43% (insignificant), 1.58% (insignificant), 4.56% (5%), 4.64% (1%), and 4.44% (5%). Table 7 therefore supports a diversified large-stock EAR gradient more clearly than an unconditional concentrated long-only claim.

## Chan, Jegadeesh, and Lakonishok (1996): independently supported alternative

Chan et al. study all domestic primary NYSE, AMEX, and Nasdaq stocks covered by CRSP and Compustat from January 1977 to January 1993, excluding closed-end funds, REITs, trusts, ADRs, and foreign stocks. At the beginning of each month they assign stocks to ten equal-weighted portfolios. Earnings-momentum breakpoints use only NYSE firms that announced within the prior three months, while all eligible exchanges receive ranks.

Their announcement-return variable is:

```text
ABR(i,t) = sum[j=-2..+1](R(i,j) - R(equal-weighted market,j))
```

The four-day `[-2,+1]` additive market-adjusted ABR differs from Brandt's three-day `[-1,+1]` difference in compounded stock and 2×3 characteristic-portfolio returns. Chan et al. explicitly include `+1` for delayed reaction, particularly for less frequently traded Nasdaq issues.

Table IV's top ABR decile subsequently earns raw equal-weighted buy-and-hold returns of 12.2% over six months and 23.8% over one year, versus 6.3% and 15.5% for the bottom decile. The paper does not attach significance tests to the individual long-decile raw returns. In monthly cross-sectional regressions, percentile-ranked ABR predicts six-month returns by itself (slope 3.7%, t=9.25) and with prior return, SUE, and analyst revisions (1.7%, t=4.25); corresponding one-year slopes are 5.5% (t=7.86) and 2.6% (t=3.71). This supplies independent primary evidence that a simple market-adjusted announcement-return definition predicts subsequent returns, but the monthly formation can use an announcement up to three months old and does not test an immediate `t+2`-open trade.

For firms above the NYSE median market capitalization, Table VIII reports top-decile ABR one-year raw return of 18.3% versus 14.0% for the bottom decile. The 4.3% spread is smaller than the full-sample 8.3% spread. Table IX reports a large-stock ABR percentile slope of 3.6% (t=3.60) alone, but 1.5% (t=1.67) in the full multivariate regression. Thus the large-stock evidence weakens materially under joint controls.

## Literature-faithful conclusions for the campaign

- Brandt's exact EAR requires the `[-1,+1]` stock return and the contemporaneous return of the firm's matched 2×3 size/book-to-market portfolio, then uses prior-quarter quintile breakpoints.
- The complete signal is observable only after the `t+1` bar. The primary paper evaluates post-window returns beginning at `t+2`, but it does not test an open-price execution rule.
- Historical primary evidence supports the positive long leg: `POSITIVE_EAR_LONG_LEG_SUPPORTED` for the 1987–2004 Brandt sample.
- Chan et al. independently validate a simpler market-adjusted announcement return, but their signal is `[-2,+1]`, additive, monthly ranked, and tested in 1977–1993. It is an externally supported alternative specification, not a faithful replacement that can be conflated with Brandt EAR.
- Both sources use broadly diversified decile/quintile portfolios. Neither source validates “top six” or one-name concentration.
- Neither primary source supplies post-2004 U.S. evidence.

## Research attestation

No local candidate prices, event returns, future returns, P&L, NAV, Sharpe ratio, CAGR, drawdown, information coefficient, return buckets, holding-period comparison, or V3 outcome correlation was calculated for this review. The percentages above are transcribed from the cited primary papers.
