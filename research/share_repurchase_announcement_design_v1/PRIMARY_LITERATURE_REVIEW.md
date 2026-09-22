# Primary literature review

Review date: 2026-09-22. Research design only; no local returns, event panel, economic backtest, or production behavior was changed. Numerical results below are authors' published estimates, not reproduced results. Primary research is used even where the paper is hosted on an author/academic mirror. Access limitations are explicit.

## The precise question

The relevant estimand is a long-only outsider's incremental return after the first executable entry following public availability. An announcement-window CAR containing day −1 or day 0 is not that estimand. Neither an issuer's execution-price advantage nor an ex-post completed-program portfolio answers it. A multi-year event-study average is also not a demonstrated one-position or six-position allocator. These are methodological distinctions, not findings of a new local test.

## Foundational studies

### Ikenberry, Lakonishok and Vermaelen (1995)

*Market underreaction to open market share repurchases*, Journal of Financial Economics 39, 181–208. [Published paper, author-affiliated LSV host](https://www.lsvasset.com/pdf/research-papers/ShareRepurchases.pdf); [DOI](https://doi.org/10.1016/0304-405X(95)00826-Z).

The abstract (p.181) studies 1980–1990 announcements and reports 12.1% abnormal four-year buy-and-hold performance after the initial announcement; the value subgroup reports 45.3%, while glamour firms show no positive abnormal drift. These are cumulative four-year estimates, not annual alpha. The published PDF was located, but its text extraction was incomplete; exact portfolio-construction and weighting details beyond this verified abstract are not asserted here. The distinction between value and glamour supports conditioning on pre-event information rather than treating every authorization as equally informative. It does not prove that a modern large/liquid universe preserves the effect. No claim is made that the reported figure is net of executable next-open costs or applicable to a short holding horizon.

### Peyer and Vermaelen (2009)

*The Nature and Persistence of Buyback Anomalies*, Review of Financial Studies 22, 1693–1745. [DOI](https://doi.org/10.1093/rfs/hhn024); [paper mirror](https://www.dimfunds.com/wp-content/uploads/2015/09/08.03.27-Nature-of-Buyback-Anomalies.pdf); [authors' contemporaneous explanation](https://knowledge.insead.edu/economics-finance/buyback-investing).

The paper argues for persistence and a mechanism involving earlier market overreaction to bad news, analyst downgrades and pessimistic long-term forecasts. Its open-market announcement analysis must be kept separate from its fixed-price tender-offer arbitrage analysis. The latter's tender mechanics cannot support an open-market announcement strategy. Full-paper retrieval was unsuccessful in this review; exact table values, sample counts and weighting claims are therefore not inferred from secondary summaries. The authors' publication-era explanation is supportive historical evidence, not a modern replication or verification of this repository's entry rule. Their historical ranking is a research motivation; the ranking audit must separately establish feature availability, universe comparability and implementable selection.

### Grullon and Michaely (2004)

*The Information Content of Share Repurchase Programs*, Journal of Finance 59, 651–680. [Publisher](https://onlinelibrary.wiley.com/doi/10.1111/j.1540-6261.2004.00645.x); [full published paper](https://quantcorpfin.com/wp-content/uploads/2019/11/Info-content-of-share-repo-programs-Grullon-2004.pdf).

Section II, pp.657–659/Table I: 4,443 announcements during 1980–1997; average three-day (−1,0,+1) CAR 2.71%, benchmarked to the CRSP value-weighted index. Firms concentrate in the top three CRSP size deciles (p.658), so historical research is not exclusively about tiny firms. The abstract finds no operating-performance improvement, but reduced systematic risk/cost of capital and a stronger response among firms prone to overinvestment. This challenges a simple improved-earnings story. Selection excludes firms cutting dividends during the announcement year (pp.657–658), which would require future information for some announcement dates. That retrospective restriction cannot become an ex-ante eligibility rule. A value-weighted benchmark is not proof of a value-weighted strategy. The announcement CAR is unavailable in full to an outsider entering afterward; the sample is not post-2010 evidence.

## Translation into a testable research claim

| Distinction | Required interpretation |
|---|---|
| Announcement response versus subsequent return | Exclude price movement before the allowed entry; preserve timestamp uncertainty rather than assuming an announcement-date close was obtainable. |
| Long-only versus long-short | Benchmark subtraction and regression alpha are analytical comparisons. Do not silently require shorting the benchmark, a low-score leg, or equity issuers. |
| Equal-weighted versus value-weighted | Report both when evaluating breadth; use actual capped allocator weights for feasibility. Benchmark weights and portfolio weights are different objects. |
| Undervaluation versus guaranteed execution | An authorization conveys intent and discretion. Neither future completion nor future takeover status may select the initial universe. |
| Return explanation versus investability | A statistically significant historical average does not establish transaction-cost headroom, diversification, tail behavior or cash utilization. |

The foundations justify investigation of a conditional historical hypothesis. They do not independently clear the proposed strategy. See [modern evidence](MODERN_EVIDENCE_REVIEW.md) for direct contradictory findings, sample-era limits and the unfilled modern large/liquid next-open evidence requirement.
