# Long-only evidence review

## Verdict limited to the primary historical sample

`POSITIVE_EAR_LONG_LEG_SUPPORTED`

This verdict means the highest Brandt EAR quintile itself has significantly positive subsequent abnormal returns in the 1987–2004 source sample. It does not establish modern persistence, causal SEC timestamp coverage, benchmark feasibility, top-six concentration, or a READY design.

## Brandt et al. standalone long leg

Brandt et al. (2008), [Table 2 Panel A](https://www.anderson.ucla.edu/documents/areas/fac/finance/santa_clara_surprises.pdf), reports the following `EAR5` cumulative abnormal returns. The benchmark is the firm's matched 2×3 size/book-to-market portfolio. Return accumulation is shown by equation (5) as `t+2..t+n` after the `[-1,+1]` EAR window.

Version control matters. The [June 2007 draft](https://www.anderson.ucla.edu/documents/areas/fac/finance/ear.pdf) reports smaller values, including EAR5 of 1.66% at 60 days and 2.21% at 120 days. This review uses the later January 22, 2008 author-hosted version; its cover date, 7.55% abstract result, and Table 2 are internally consistent. Cross-reviewers should not combine rows from the two versions.

| Labeled trading-day horizon | EAR5 long-only abnormal return | Reported significance | EAR5−EAR1 spread, for context only |
|---:|---:|---:|---:|
| 30 | 1.08% | 1% | 1.47% |
| 60 | 1.99% | 1% | 3.27% |
| 90 | 2.42% | 1% | 3.73% |
| 120 | 2.62% | 1% | 4.07% |
| 180 | 3.56% | 1% | 6.35% |
| 240 | 4.61% | 1% | 7.55% |

All six EAR5 observations are marked `*`, which the table defines as significant at 1%. The paper reports no t-statistics. The long-only values are taken directly from EAR5, not inferred from the spread.

The source's Table 2 does not label the portfolio as equal weighted or value weighted. Therefore these results cannot be honestly described as both, or even definitively as one of them. No value-weighted long-only counterpart is reported.

## Shape of the ranked result

The highest quintile is consistently strongest and the long leg grows with horizon, but every row is not strictly monotone from EAR1 to EAR5. For example, at 60 days EAR1 through EAR5 are −1.28%, −0.21%, −0.16%, 0.76%, and 1.99%, while at 90 days they are −1.31%, −0.34%, −0.05%, 0.78%, and 2.42%. These are strongly ordered at the positive end but do not prove that selecting the top six observations dominates owning the diversified top quintile.

Brandt Table 6 supplies a continuous-direction check: a monthly EAR rank built from announcements in months `m-2..m-4` produces standalone monthly returns of −0.16%, 0.04%, 0.10%, 0.24%, and 0.53% across EAR1–EAR5, with a 0.69% spread significant at 1%. The authors do not report a raw-EAR regression coefficient or an extreme-tail/top-six test.

## Large-stock evidence

Brandt Table 7 limits the sample to the 1,000 largest CRSP firms and reports independent EAR×SUE cells, not an unconditional EAR5 long leg. At 60 days, EAR5 returns across SUE1–SUE5 are 1.56% (5%), 1.44% (1%), 2.00% (1%), 2.02% (1%), and 2.97% (1%). At 120 days they are 1.56% (insignificant), 0.68% (insignificant), 2.48% (1%), 2.36% (5%), and 3.19% (5%). At 240 days they are −0.43% (insignificant), 1.58% (insignificant), 4.56% (5%), 4.64% (1%), and 4.44% (5%).

This is meaningful large-stock support for a diversified EAR gradient, especially at 60 days. It also shows that the large-stock long side is conditional and not uniformly significant at longer horizons. The source does not isolate large-and-liquid stocks using ParayaBuck's liquidity criteria.

## Chan et al. market-adjusted alternative

Chan, Jegadeesh, and Lakonishok (1996), [Table IV](https://www.cfrn.com.cn/uploads/fileupload/2396/paper/0908311241015993.pdf), use a four-day `[-2,+1]` announcement return relative to the equal-weighted market index and form equal-weighted deciles monthly.

The top ABR decile subsequently earns raw buy-and-hold returns of 12.2% over six months and 23.8% over one year; the bottom decile earns 6.3% and 15.5%. The individual long-decile values have no reported significance tests and are raw returns, so they are not directly comparable to Brandt's characteristic-adjusted abnormal returns. The evidence validates cross-sectional continuation from a simpler market-adjusted signal, not a long-only abnormal-return claim at a causal event-time entry.

For stocks above the NYSE median market capitalization, Chan Table VIII reports one-year raw returns of 18.3% for the top ABR decile and 14.0% for the bottom decile. Its 4.3% spread is roughly half the full-sample 8.3% spread. In large-stock monthly cross-sectional regressions, ABR's one-year slope is significant alone (3.6%, t=3.60) but not conventionally significant after joint controls (1.5%, t=1.67).

## SUE independence relevant to the long leg

Brandt Table 3's 60-day EAR5 returns rise from −0.67%, 0.62%, 1.72%, 2.14%, to 4.12% across SUE1–SUE5, while the corresponding EAR5−EAR1 spreads are 1.53%, 2.17%, 2.30%, 3.40%, and 3.97%, all marked at 1%. This shows EAR retains a gradient within every SUE quintile, although the low-SUE/high-EAR long cell is not positive at 60 days. The nonoverlap EAR hedge remains 2.01% at 60 days after removing joint extreme SUE/EAR cells. The correct inference is incremental information; it is not permission to combine EAR and SUE in the first candidate.

## Financials and utilities

The Brandt main sample excludes NAICS 52 and 22. Its only reported inclusion check states that the *joint SUE/EAR long-short strategy* earns 12.1% over 240 days when financials and utilities are included, versus 12.48% in the main specification. No EAR5-only or EAR-only sector-inclusion result is reported. That footnote cannot establish the long leg's sector robustness by itself.

## What the primary evidence does and does not support

Supported:

- the diversified highest Brandt EAR quintile was positive and significant at every reported 30–240-day horizon;
- 60-day EAR5 abnormal return was 1.99%, significant at 1%;
- an EAR gradient remained within large stocks and after conditioning on SUE or conventional price momentum;
- Chan et al. independently found continuation after a simpler market-adjusted announcement-return signal.

Not supported by these sources:

- value-weighted Brandt EAR5 results;
- top-six or one-name concentration;
- a modern/post-2010 U.S. long-only result;
- an immediate `t+2`-open fill result;
- ParayaBuck-liquidity-filtered results;
- standalone EAR financials/utilities inclusion robustness;
- choosing 60 days because it beats another horizon. The literature supplies a 60-day result, but selecting it for implementation still requires a pre-outcome rationale independent of local performance.

## Attestation

No local candidate returns or economic metrics were calculated. All numeric results above are published values from the cited primary papers.
