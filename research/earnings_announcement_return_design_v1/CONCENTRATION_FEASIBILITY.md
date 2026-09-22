# Concentration feasibility

## Required verdicts

`EAR_PREMIUM_REQUIRES_DIVERSIFICATION`

`ONE_SLOT_EAR_ALPHA_DENSITY_UNSUPPORTED`

## Evidence that does exist

The January 22, 2008 BKSV version's full-sample Table 2 shows a strongly
ordered positive end, though not strict monotonicity across every middle
quintile.  At 60 trading days the characteristic-adjusted returns are
`−1.28, −0.21, −0.16, 0.76, 1.99%` from EAR1 through EAR5, with a 3.27% spread;
at 120 days they are `−1.45, −0.42, −0.15, 0.61, 2.62%`, with a 4.07% spread.
The highest quintile is reported significantly positive, so the historical
effect is not solely the short leg.  Table 6's calendar-time implementation
reports `−0.16, 0.04, 0.10, 0.24, 0.53%` across EAR1–EAR5 and a 0.69% spread,
reported significant at 1%.

The paper's top-1,000-size robustness is useful for liquidity. Table 7 reports
cumulative post-announcement returns for independent EAR×SUE cells. At 60 days
the large-stock EAR5 cells are positive and significant across all SUE
quintiles, but results become weaker and nonuniform at longer horizons; for
example, the 240-day SUE1/EAR5 and SUE2/EAR5 cells are not significantly
positive. This supports a diversified large-stock gradient more clearly than
an unconditional concentrated long leg. Source: [January 22, 2008 BKSV author PDF](https://www.anderson.ucla.edu/documents/areas/fac/finance/santa_clara_surprises.pdf),
SHA-256 `d55a0f2cc116437957f282345e31699a0e50598256e53fca12d1d3fc5b9e5b10`,
Tables 2, 6 and 7.

Version reconciliation: the older June 2007 `ear.pdf` reports 1.66% and 2.21%
for EAR5 at 60 and 120 days and 0.49%/0.56% for the Table 6 EAR5/spread.  Those
values are not mixed into this campaign; the corresponding later-version
values are 1.99%, 2.62%, 0.53%, and 0.69%.

HXZ's independent replication uses deciles and shows significant high-minus-
low market-adjusted announcement-return spreads with NYSE breakpoints and
value weighting through 2016.  That supports an extreme-tail ordering at a
broad portfolio level, though it is a different `[-2,+1]` market-adjusted
signal and does not isolate the long leg.  Source: [HXZ published article](https://academic.oup.com/rfs/article/33/5/2019/5236964), Table 3 and Appendix A.1.2.

## Why top six is not established

The evidence units are diversified quintiles or deciles.  Neither BKSV nor HXZ
reports the expected continuation of the six largest contemporaneous EARs,
the order statistics inside the top portfolio, or the contribution of each
issuer.  BKSV do not estimate a continuous post-return slope that demonstrates
ever-higher expected continuation within EAR5.  Monotonic portfolio means
support ranking into broad buckets; they do not license the inference
`top quintile -> top six`.

The primary mechanism also matters.  BKSV find that much of the EAR strategy's
subsequent return appears around later quarterly earnings announcements, and
they document persistence across four later announcements.  A six-name book
would expose the result to announcement timing, issuer, sector, and single-
event idiosyncrasy that the published diversified portfolios average away.
No modern U.S. study located here validates that concentration.

## Why one slot is not established

EAR is issuer-specific and continuous, which makes it operationally rankable.
Operational rankability is not evidence of alpha density.  The literature does
not show that the single maximum positive EAR has higher expected continuation
than the rest of the upper tail after realistic liquidity and causal entry.
Nor does it bound the influence of concurrent M&A, financing, bankruptcy,
split, or other firm news that can create a very large event-window return.

Accordingly, the literature supports historical diversified upper-tail
exposure but cannot justify a six-name or one-name expression.  No local
forward outcomes, return buckets, hit rates, or parameter comparisons were
calculated to reach this conclusion.
