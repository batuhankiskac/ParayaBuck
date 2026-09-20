# Focused literature review

The literature supports a real reference-price construct, but support for a
concentrated, long-only, liquid-U.S., post-publication implementation is
conditional rather than strong. No ParayaBuck outcome was inspected.

## Evidence table

| Source | Sample and construction | Main evidence | Limitation and relevance |
|---|---|---|---|
| [George & Hwang (2004), *The 52-Week High and Momentum Investing*](https://doi.org/10.1111/j.1540-6261.2004.00695.x) | All CRSP stocks, July 1963-December 2001; month-end price divided by the highest price in the 12 months ending that same month-end; monthly formation; equal-weight top/bottom 30%; six-month overlapping holds | The ratio retains predictive content after individual-return and industry-momentum controls and dominates them in the original U.S. tests | Long-short evidence is stronger than the long leg; the short leg contributes more to the controlled spread; no executable cost study; all-CRSP equal weighting exposes the result to smaller names |
| [Sturm (2008), *The 52-Week High Strategy: Momentum and Overreaction in Large Firm Stocks*](https://doi.org/10.3905/joi.2008.707218) | 90 large CRSP stocks, 1987-2005; month-end close divided by highest close; monthly equal-weight portfolios | Finds little continuation evidence among large stocks and some overreaction evidence | Direct warning for a liquid-stock implementation; fixed cohort creates selection concerns |
| [Marshall & Cahan (2005), *Is the 52-Week High Momentum Strategy Profitable Outside the US?*](https://doi.org/10.1080/09603100500386008) | Australian short-sale-eligible stocks; George-Hwang-style monthly 12-month ratio and six-month hold | Out-of-sample gross long-short support across size/liquidity groups | Different market; does not establish a U.S. long-only effect |
| [Du (2008), *The 52-Week High and Momentum Investing in International Stock Indexes*](https://doi.org/10.1016/j.qref.2007.02.001) | 18 developed-market MSCI price indexes, 1969-2004 | Reference-price signal contains a component distinct from ordinary index momentum | Long-run reversals differ from the original paper; costs remove a large part of risk-adjusted profit; index evidence is not stock-level long-only evidence |
| [Liu, Liu & Ma (2011), *The 52-Week High Momentum Strategy in International Stock Markets*](https://doi.org/10.1016/j.jimonfin.2010.08.004) | Individual stocks in 20 markets; monthly George-Hwang construction | Positive spreads in 18 markets, significant in 10; survives conventional-momentum controls | Returns are highly correlated with momentum; transaction costs remove significance in most markets |
| [Bettman, Sault & von Reibnitz (2010), *The Impact of Liquidity and Transaction Costs on the 52-Week High Momentum Strategy in Australia*](https://doi.org/10.1177/0312896210385282) | Australian equities, 1996-2008; liquid/illiquid partitions; bid/ask and short-sale constraints | Raw evidence is positive in liquid stocks and negative in illiquid stocks | After costs and constraints, the liquid strategy has no significant dollar profit |
| [Lobao & Fernandes (2017), *The 52-Week High and Momentum Investing: Implications for Asset Pricing Models*](https://aefweb.net/AefArticles/aef180206Lobao.pdf) | U.S. exchanges, 1980-2014; one-month-skipped 12-month price-to-high factor | Full-period spread is small and insignificant; 2001-2014 is negative while conventional WML is stronger | Important post-publication U.S. non-persistence warning |
| [Huddart, Lang & Yetman (2009), *Volume and Price Patterns Around a Stock's 52-Week Highs and Lows*](https://doi.org/10.1287/mnsc.1080.0920) | U.S. trading around prior-range crossings | Volume reactions remain after past-return and news controls; prior highs are salient | Mechanism evidence, not net alpha; effects are stronger for smaller, ambiguous firms |
| [Baker, Pan & Wurgler (2012), *The Effect of Reference Point Prices on Mergers and Acquisitions*](https://doi.org/10.1016/j.jfineco.2012.04.010) | U.S. M&A offer and acceptance decisions around recent peaks | Offers cluster around 52-week highs and acceptance changes around the reference point after return controls | Strong reference-point evidence in a different decision setting, not portfolio alpha proof |
| [Barroso, Edelen & Karehnke (2025), *Empirical Determinants of Momentum: A Perspective Using International Data*](https://doi.org/10.1093/rof/rfae025) | Developed ex-U.S. and emerging stocks; momentum-explanation horse race | The 52-week-high coefficient is insignificant and ordinary momentum barely changes when it is included; evidence favors gradual-information proxies | Recent, high-quality evidence against treating anchoring as a universal standalone explanation |

## Distinctness from conventional momentum

Documented evidence: the ratio is a price-level/path statistic, not a
cumulative-return statistic. Two stocks can have identical 12-1 returns but
different ratios because their intrayear maxima differ. The original U.S.
tests retain signal content after conventional momentum controls. International
work nevertheless reports high correlation, and recent evidence finds that
adding the ratio may leave ordinary momentum essentially unchanged.

Interpretation: the annual maximum makes the hypothesis structurally different
from V3's residual 6/9/12 return consensus, so one controlled experiment is
scientifically defensible. It is not proven independent or complementary, and
the candidate must be called **reference-price momentum**, not a new orthogonal
factor.

## Behavioral mechanism

Documented evidence supports the salience of prior highs, trading responses at
crossings, expectation errors, and use of highs in M&A bargaining. The proposed
story is that investors anchor on the high and under-adjust to information near
that reference point. Risk compensation, generic underreaction, and the ratio's
compression of recent return history remain viable alternatives.

Interpretation: anchoring is a plausible mechanism, not established causality.
The future test can evaluate a frozen signal; it cannot identify investor
psychology from returns alone.

## Construction conclusion

The literature-faithful, data-robust choice is highest **daily closing price**.
The original studies use observed price/closing-price series, and the large-firm
study is explicit about highest close. Highest intraday high would create a
microstructure-sensitive one-print extremum not required by the core
hypothesis; the local split audit also found three extreme intraday highs near
reverse-split dates while their closes remained continuous.

Use a completed month-end close divided by the maximum of 252 completed,
split-adjusted daily closes ending on that same session. Include the decision
bar, rank higher ratios first, and rebalance monthly. This is a deterministic
trading-session translation of the paper's 12-month month-end definition.

The literature's overlapping long-short vintages are not copied. ParayaBuck's
candidate is a scarce-slot long-only alternative; that is a material external-
validity deviation and is why strict displacement, validation, cost, turnover,
and concentration gates are required.
