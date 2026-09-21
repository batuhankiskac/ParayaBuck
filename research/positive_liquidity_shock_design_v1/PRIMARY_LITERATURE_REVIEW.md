# Primary literature review

Primary authority is Bali, Peng, Shen and Tang (2014), *Liquidity Shocks and
Stock Market Reactions*, Review of Financial Studies 27, 1434–1485
([DOI](https://doi.org/10.1093/rfs/hht074)); the
[Internet Appendix](https://citeseerx.ist.psu.edu/document?doi=a131b5a08e6ac015a78833346298bab65ca6792a&repid=rep1&type=pdf)
was reviewed separately.

## Version-control finding

The published RFS formula on p.1440 is not the same as the earlier working
paper. The 2013 version standardized the demeaned shock by the prior-12-month
standard deviation. The final article deliberately uses the unstandardized
difference. Footnote 8 identifies the standardized and ARMA-GARCH versions as
earlier/robustness alternatives. The final published equation governs this
audit.

## Data and construction

- Monthly `ILLIQ` is the within-month mean of daily
  `abs(return)/dollar volume`, multiplied by `10^6`.
- At least 15 daily return observations are required in the month.
- `LIQU` is the negative difference between current `ILLIQ` and its mean over
  exactly months `t-12` through `t-1`; it is not divided by a mean, standard
  deviation or other scale.
- Positive `LIQU` means liquidity improved because current illiquidity fell
  below its own history.
- The paper applies historical NASDAQ volume divisors through 2003. Those
  predate ParayaBuck's 2016–2026 dataset and do not alter the local period.
- The predictive sample is August 1963–December 2010, using CRSP common stocks
  on NYSE, AMEX and NASDAQ.
- At each month-end stocks are sorted using NYSE breakpoints; month `t+1`
  returns are measured. Both equal- and value-weighted deciles are reported.

The article does not specify how many of the prior 12 monthly `ILLIQ` values
may be missing. An exact 12 consecutive valid-month requirement is therefore
the only fail-closed local interpretation; shortening the history is rejected.

## Alternative measures and controls

The paper also studies effective-spread shocks and a conditional `LIQCU`
variant. `LIQCU` uses an ARMA(1,1) conditional mean estimated over a rolling
60-month window with at least 24 observations. Those are robustness measures,
not candidates for an OHLCV ensemble.

The published effect survives controls for momentum, reversal, IVOL, baseline
illiquidity, liquidity volatility, turnover, abnormal dollar volume, size,
MAX, earnings news and liquidity-risk loadings. This supports conceptual
distinctness from volume and volatility alone.
