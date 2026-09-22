# Equity lifecycle interpretation

**Decision: MIXED — recurring giveback is present, but these diagnostics do not demonstrate a defective exit policy or that a new exit would improve the strategy.** Modest transient gains frequently disappear; substantial winners usually remain profitable and supply an essential right tail. Further research is defensible as a question, not as approval for a rule, parameter, or promotion. Crypto remains **INCONCLUSIVE** because historical replay parity failed; no crypto economic conclusion should be imported from the equity results.

Evidence sources: `EQUITY_EPISODE_DIAGNOSTICS.json`, `EQUITY_SUMMARY.json`, `INDEPENDENT_VERIFICATION.json`. The independent verifier checked all 908 episodes with fee-capitalized average-cost accounting and reported PASS, no failures. These are offline reconstruction findings, not runtime evidence.

## Uncensored equity evidence

The analysis below excludes three horizon-censored episodes forcibly liquidated at the final bar, leaving 905 actual closures, including two lifecycle-terminal closures. Their final net P&L is $96,363.00; horizon-censored episodes contribute $1,189.94 after final liquidation costs to the $97,552.94 total account gain.

- 781 closures had positive observed MFE; 279 subsequently closed at a loss: **35.72%**. This statistic includes arbitrarily small positive excursions and therefore is not a measure of large winners squandered.
- Of 258 episodes reaching +5%, 36 exited nonpositive (**13.95%**). Of 103 reaching +10%, ten exited nonpositive (**9.71%**). All 17 reaching +20% exited profitably; the +30% and +40% samples contain only nine and five observations. These are nested retrospective descriptive bins, not proposed exit levels.
- Median retention among positive-MFE episodes is **51.50%**. For the +5%, +10% and +20% groups it is **77.78%, 78.82%, and 90.17%** respectively. Larger observed winners generally retained more of their peak, although the high-level samples are small.
- Final episode return median is **0.53%**, versus observed MFE median **2.63%**; these are separate marginal medians and must not be subtracted as if they describe one median trade. Median holding time is 13 days; median peak-to-exit interval is six days. Median dollar giveback is $211.23, p90 $1,205.71, under the supplied fixed-total-buy-outlay normalization.
- The top ten episodes contribute **55.47%** of net closed-episode P&L; the top 91 (ceil of 10% of 905) contribute **185.17%**. The latter exceeds 100% because other episodes offset some gains. This is strong right-tail dependence, not a feasible return obtainable by selecting those winners ex ante.

## Development versus validation

Cohorts are fully contained: development exits through 2021-12-31 and starts within the replay; validation entries on/after 2022-01-03 with actual exit by replay end. Six episodes crossing the boundary are excluded from both rows. Three censored episodes are excluded throughout. This avoids assigning pre-validation entry information to a fully-contained validation cohort; it does not make this retrospective analysis a fresh untouched holdout.

| Cohort | Closures | Positive MFE then loss | Median retention, positive MFE | +5% then nonpositive | +10% then nonpositive | Net closed P&L |
|---|---:|---:|---:|---:|---:|---:|
| Development | 445 | 127/384 (33.07%) | 61.33% | 16/129 (12.40%) | 7/52 (13.46%) | $54,982.49 |
| Validation | 454 | 147/391 (37.60%) | 39.12% | 17/125 (13.60%) | 3/50 (6.00%) | $42,667.95 |

Broad positive-MFE erosion persists and is worse in validation, while the +10% loss fraction improves. That disagreement argues against a universal large-profit giveback diagnosis. Dollar cohort totals also reflect changing capital and different durations; they are not directly comparable return estimates.

## Exit-year cohorts

Each full episode is assigned to its final exit year, including its earlier-year P&L. These are episode cohorts, **not calendar-year portfolio returns**. The 2026 cohort ends July 30 and is partial.

| Exit year | Closures | Positive MFE then loss | Median retention, positive MFE | Net closed P&L |
|---|---:|---:|---:|---:|
| 2017 | 100 | 26/83 (31.33%) | 62.53% | $11,835.12 |
| 2018 | 83 | 23/71 (32.39%) | 52.94% | -$234.07 |
| 2019 | 91 | 26/81 (32.10%) | 74.40% | $14,374.82 |
| 2020 | 85 | 28/77 (36.36%) | 53.05% | $19,878.72 |
| 2021 | 86 | 24/72 (33.33%) | 59.19% | $9,127.91 |
| 2022 | 98 | 35/76 (46.05%) | 14.41% | -$16,000.33 |
| 2023 | 102 | 33/91 (36.26%) | 45.13% | $10,275.65 |
| 2024 | 90 | 23/82 (28.05%) | 63.23% | $22,750.36 |
| 2025 | 107 | 35/93 (37.63%) | 42.14% | $12,438.33 |
| 2026 partial | 63 | 26/55 (47.27%) | 10.78% | $11,916.50 |

Low median retention can coexist with positive cohort profit, particularly in 2026, because a few large winners outweigh frequent small reversals. The erosion is recurring across years rather than one anomalous episode, but its severity is regime-sensitive.

## Interpretation limits

1. MFE is the maximum of the observed marked episode path, including its terminal fill. It is an ex-post upper envelope, not an executable exit opportunity. Daily marks do not identify intraday highs, crossing order, or a fill obtainable at the peak.
2. Capital normalization uses total episode buy outlay, known only after the lifecycle. Partial sells, additions and distributions mean these are episode economics, not simply entry-to-exit price returns or a causal live trigger series.
3. Mean retention is unstable when MFE is tiny; the supplied negative mean should not be read as the typical winner losing multiple times its peak. Medians and positive-MFE denominators are more interpretable.
4. Giveback sums across episodes combine non-simultaneous peaks and cannot be recovered as portfolio P&L. Likewise, a decline in open unrealized P&L can reflect realizing winners and must be checked against realized plus unrealized P&L.
5. The recorded closure reasons are 673 alpha-no-longer-selected, 230 risk-overlay and two lifecycle-terminal. These are attribution categories derived from observed decisions, not a counterfactual proof that those components caused avoidable losses.
6. No alternative exit, re-entry behavior, additional trading costs, changed capital availability, or right-tail loss has been simulated. The evidence cannot choose among those tradeoffs. Canonical drawdown shadow behavior also prevents treating this curve as a literal continuously tradable production account path.

**Research vocabulary:** recurring modest-gain erosion; regime-sensitive retention; substantial-winner persistence; right-tail-dependent returns; mixed diagnostic evidence. Avoid claiming systematic large-winner destruction, executable peak recovery, a validated profit-lock benefit, or readiness to change PAPER/LIVE.
