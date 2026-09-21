# Literature Review

## Conclusion

MAX is a serious and behaviorally plausible cross-sectional characteristic,
but the evidence does not establish a six-name, long-only LOW-MAX alpha. The
canonical spread is driven substantially by the poor subsequent performance of
the highest-MAX stocks, depends on a diversified long-short construction, and
is strongly entangled with idiosyncratic volatility, reversal, skewness,
liquidity, and mispricing states. Later investability-oriented replication is
materially weaker.

## Exact seminal definition

Bali, Cakici, and Whitelaw define, for stock i in calendar month t:

`MAX(i,t) = max[d=1..D(t)] R(i,d)`

where `R(i,d)` is a daily simple stock return. Month t MAX predicts month t+1;
there is no skip month. Portfolios are rebalanced monthly. MAX1 is the primary
measure. MAX(N) averages the N largest daily returns, but the authors retain
MAX1 for simplicity even though some MAX2-MAX5 results are stronger.

The paper says it uses CRSP daily stock returns but does not identify `RET` or
`RETX` by field name. Standard CRSP return treatment points toward a
distribution-inclusive return, but that is an inference rather than an
explicit field-level statement. Consequently a split-adjusted price-only
close-to-close implementation would be an auditable repository proxy, not a
claim of exact published-field replication. Simple, not log, return is the
published formula.

Primary sources: [Bali, Cakici & Whitelaw (2011)](https://doi.org/10.1016/j.jfineco.2010.08.014),
[author PDF](https://pages.stern.nyu.edu/~rwhitela/papers/max%20jfe11.pdf),
[CRSP calculation methodology](https://www.crsp.org/wp-content/uploads/guides/CRSP_Calculations_and_Index_Methodologies.pdf).

## Source review

| Authors | Publication/year | Sample and universe | MAX definition; formation/holding; construction | Controls and IVOL relationship | Cost evidence and mechanism | Limitations and ParayaBuck applicability |
|---|---|---|---|---|---|---|
| Bali, Cakici & Whitelaw | JFE, 2011 | CRSP NYSE/Amex/Nasdaq firms; principal tests Jul-1962 to Dec-2005 | Prior-calendar-month MAX1; no skip; next month; EW and VW deciles, high-minus-low | Size, B/M, momentum, reversal, liquidity, beta, IVOL, total/systematic/idiosyncratic/expected skewness and MIN. MAX-IVOL correlation about 0.75; MAX survives IVOL controls and IVOL reverses after MAX controls | No net-cost test; lottery demand/optimistic beliefs; authors flag small/illiquid high-MAX names and shorting as serious impediments | Result is concentrated in high MAX and does not prove positive alpha in the low leg or in a six-name portfolio. $5, NYSE-only, and small-stock exclusions do not fully erase the historical spread. |
| Fong & Toh | JBF, 2014 | U.S. common stocks, Jul-1965 to Dec-2007 | Prior-month MAX1; monthly VW deciles, high-minus-low | Sentiment, institutional ownership, lagged sentiment-return factor and firm controls | No net-cost test; optimism/speculation and short-sale constraints | Effect is strongest in high-sentiment periods and mostly high-MAX underperformance, not unusually high low-MAX returns. Direct warning against the proposed long-only translation. [DOI](https://doi.org/10.1016/j.jbankfin.2014.05.006) |
| Annaert, De Ceuster & Verstegen | JBF, 2013 | 7,861 firms, 13 European countries, Dec-1979 to Jun-2011 | Prior-month MAX1; MAX2-MAX5 robustness; monthly EW/VW deciles | Size, B/M, momentum, reversal, liquidity, skewness/coskewness and IVOL; IVOL weakens after MAX | No net-cost test; lottery/mispricing with arbitrage limits | Univariate MAX evidence is weak; negative relation is more evident after controls. International support is specification-sensitive. [DOI](https://doi.org/10.1016/j.jbankfin.2013.05.015) |
| Walkshäusl | JBF, 2014 | 11 euro-area markets | Prior-month MAX; monthly sorts | MAX predicts returns and absorbs IVOL; relation varies with cash-flow volatility and profitability | No decisive net-cost evidence; mispricing interpretation | Supports external validity, but market structure and implementation differ from U.S. six-name long-only use. [DOI](https://doi.org/10.1016/j.jbankfin.2014.01.020) |
| Zhong & Gray | JBF, 2016 | Australia, 1991-2013; largest-500 subset | Prior-month MAX1; next-month long low/short high | Risk adjustment and mispricing partitions; not supported as a priced-risk factor | No direct net-cost test; arbitrage asymmetry and mispricing | Survives in a large-stock subset, but is concentrated in overpriced stocks and reverses among underpriced stocks. A blunt LOW-MAX rule is not universal. [DOI](https://doi.org/10.1016/j.jbankfin.2016.01.007) |
| Bali, Brown, Murray & Tang | JFQA, 2017 | U.S. common stocks, 1963-2012; price at least $5 | MAX5: average of five largest daily returns; at least 15 valid daily observations; monthly; EW deciles | Beta, size, B/M, momentum, liquidity, IVOL, skewness, tail/funding exposures and institutional ownership; MAX1-MAX5 robust | No direct net-cost test; lottery demand and beta anomaly | Provides an operational 15-day precedent, not a reason to search N locally. MAX5 is more IVOL-like than MAX1. [DOI](https://doi.org/10.1017/S0022109017000928) |
| Hou & Loh | JFE, 2016 | CRSP, 1963-2012 | Cross-sectional decomposition of IVOL explanations | MAX can mechanically account for IVOL, but MAX-IVOL correlation is about 0.90; MAX is omitted from the joint horse race because of collinearity | Reversal, spreads, retail share, expected skewness and skewness each explain parts; no MAX-specific cost proof | Statistical subsumption does not identify a distinct causal signal. Strong warning for V3, which already rejects high IVOL. [DOI](https://doi.org/10.1016/j.jfineco.2016.02.013) |
| Egginton & Hur | JEF, 2018 | U.S. common stocks, 1965-2013; share codes 10/11 | Prior-month MAX(N); monthly deciles; $5 robustness | MAX-IVOL correlation nearly 0.90. Both can survive with $5 and overpricing controls | No net-cost test; effect concentrated in overpriced stocks | Conditional result needs capital-gains-overhang and 52-week-high inputs; these are unavailable or a closed family here. It does not justify a clean MAX-only rule. [DOI](https://doi.org/10.1016/j.jempfin.2018.03.001) |
| Hou, Xue & Zhang | RFS, 2020 | 452 U.S. anomalies using NYSE breakpoints and value weighting | Mdr1 is prior-month maximum daily return; 1/6/12-month holdings | Common replication protocol emphasizing investability; no MAX-specific IVOL horse race | No MAX-specific net-cost proof | MAX is among prominent anomalies that fail robust replication; the one-month spread is much weaker than the original. Most relevant adverse evidence for a liquid scalable universe. [DOI](https://doi.org/10.1093/rfs/hhy131) |
| Yuan, Rieger & Caliskan | MRQ, 2020 | Seven countries; U.S. subperiods through 2017 | Prior-month MAX variants | Cross-country/subperiod tests; later MAX can proxy for IVOL | No decisive cost proof | Negative MAX relation disappears or reverses in several developed markets; U.S. 1990-2017 slopes are insignificant. Important persistence warning. [DOI](https://doi.org/10.1007/s11301-019-00176-3) |
| Barberis & Huang | AER, 2008 | Equilibrium theory | Positively skewed securities under probability weighting | Theoretical, not a MAX-versus-IVOL empirical horse race | Cumulative prospect theory can generate overpricing | Supplies a behavioral prior, not identification of MAX as the unique empirical proxy. [DOI](https://doi.org/10.1257/aer.98.5.2066) |
| Kumar | JF, 2009 | Individual-investor holdings and gambling proxies | Broader lottery-stock definition: low price, high IVOL, high skew | Links lottery demand to investor clientele | Investors with stronger lottery preference underperform | Direct behavioral evidence, but the construct is broader than MAX and concentrated in retail-oriented names. [DOI](https://doi.org/10.1111/j.1540-6261.2009.01483.x) |
| Barinov | JFE, 2018 | MAX, expected-skewness and MIN portfolios | Published anomaly sorts | Aggregate-volatility-risk loadings explain substantial low returns | Volatility-insurance risk interpretation | Avoiding high MAX may discard an aggregate-volatility hedge; pure behavioral causality is not established. [DOI](https://doi.org/10.1016/j.jfineco.2018.06.007) |
| Baars & Mohrschladt | JEBO, 2021 and 2024 | Event and experimental evidence | MAX and lottery-choice settings | Overreaction and volatility conditioning challenge standalone MAX preference | Alternative mechanisms; no strategy cost validation | MAX preference may disappear after volatility control; supports redundancy concerns. [2021 DOI](https://doi.org/10.1016/j.jebo.2021.09.027), [2024 DOI](https://doi.org/10.1016/j.jebo.2024.02.004) |
| Novy-Marx & Velikov | RFS, 2016 | Broad anomaly transaction-cost study | General portfolio-cost analysis | Not MAX-specific | Strategies above roughly 50% one-way monthly turnover rarely survive; buy/hold bands help | Does not certify MAX. A six-name monthly portfolio makes each replacement large. [NBER](https://www.nber.org/papers/w20721) |

## MAX1 versus MAXN

If a later repository state clears the blocking conditions and the family is
redesigned, MAX1 is the only literature-faithful starting point: it is seminal,
simple, auditable, and less IVOL-like than MAX5. This is not a frozen candidate
in the present decision. No local variant was calculated or compared.

## Evidence synthesis

- Direction is clear in the canonical spread: higher MAX predicts lower
  subsequent return; a positive-direction trade is long low / short high.
- The low leg alone is not comparably established. Much of the effect is
  high-MAX underperformance.
- MAX retains content in several IVOL-controlled studies, but correlations of
  roughly 0.75-0.90 make mutual-control inference unstable.
- Large/liquid evidence is mixed: original robustness and an Australian
  largest-500 result are positive; investability-oriented U.S. replication and
  post-publication evidence are weak.
- Important studies do not demonstrate MAX-specific net profitability after
  realistic trading costs.
- The literature supports a hypothesis worth understanding, not the exact
  long-only top-six implementation requested here.
