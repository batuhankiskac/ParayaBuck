# Portfolio expansion crypto screen results (2026-09-05)

This is a retrospective diagnostic of the frozen 42-cell screen. Candidate arms are score adaptations of the incumbent HYST4/top2/96h policy under the same isolated 5% crypto budget; they are not archived weekly recipes.

Completed constituents: 42; fixed initial-NAV blends: 36; initial cash: `100000.00`.

Metrics use exact synchronized UTC hourly NAV series. TR/CAGR and drawdown use initial cash; hourly Sharpe annualizes 8,760 observations and daily Sharpe annualizes 365.25. Daily correlation is labeled net-PnL correlation and is undefined for constant or inactive series.

## Complementarity table

Each row is a fixed 50/50 initial-capital curve blend with the incumbent. `corr` is paired daily net-PnL correlation; `dTR` and `dDD` are blend minus incumbent.

| Window | Cost | Candidate | Corr | dTR | dDD | dTR vs half-cash |
|---|---:|---|---:|---:|---:|---:|
| full | 1x | low_max22 | 0.9729 | -0.0039 | -0.0007 | 0.0152 |
| full | 1x | signed_risk22 | 0.9306 | -0.0235 | 0.0040 | -0.0044 |
| full | 1x | downside22 | 0.9692 | 0.0113 | 0.0012 | 0.0304 |
| full | 1x | reversal70 | 0.9047 | -0.0184 | -0.0019 | 0.0007 |
| full | 1x | high_proximity364 | 0.9432 | 0.0161 | -0.0230 | 0.0352 |
| full | 1x | low_disagreement5_45 | 0.9360 | -0.0147 | 0.0067 | 0.0044 |
| full | 2x | low_max22 | 0.9727 | -0.0083 | 0.0001 | 0.0063 |
| full | 2x | signed_risk22 | 0.9304 | -0.0315 | 0.0051 | -0.0168 |
| full | 2x | downside22 | 0.9690 | 0.0115 | 0.0015 | 0.0261 |
| full | 2x | reversal70 | 0.9046 | -0.0228 | -0.0010 | -0.0082 |
| full | 2x | high_proximity364 | 0.9429 | 0.0201 | -0.0228 | 0.0347 |
| full | 2x | low_disagreement5_45 | 0.9362 | -0.0242 | 0.0084 | -0.0095 |
| full | 3x | low_max22 | 0.9727 | -0.0129 | 0.0010 | -0.0027 |
| full | 3x | signed_risk22 | 0.9305 | -0.0374 | 0.0061 | -0.0272 |
| full | 3x | downside22 | 0.9688 | 0.0116 | 0.0019 | 0.0218 |
| full | 3x | reversal70 | 0.9045 | -0.0270 | -0.0001 | -0.0169 |
| full | 3x | high_proximity364 | 0.9427 | 0.0240 | -0.0226 | 0.0342 |
| full | 3x | low_disagreement5_45 | 0.9362 | -0.0329 | 0.0111 | -0.0227 |
| recent | 1x | low_max22 | 0.9740 | -0.0060 | 0.0098 | 0.0046 |
| recent | 1x | signed_risk22 | 0.9155 | -0.0049 | 0.0140 | 0.0057 |
| recent | 1x | downside22 | 0.9656 | 0.0090 | 0.0004 | 0.0196 |
| recent | 1x | reversal70 | 0.9011 | 0.0015 | 0.0017 | 0.0121 |
| recent | 1x | high_proximity364 | 0.9472 | 0.0023 | -0.0173 | 0.0129 |
| recent | 1x | low_disagreement5_45 | 0.9180 | 0.0053 | 0.0042 | 0.0159 |
| recent | 2x | low_max22 | 0.9740 | -0.0081 | 0.0101 | -0.0006 |
| recent | 2x | signed_risk22 | 0.9142 | -0.0079 | 0.0138 | -0.0005 |
| recent | 2x | downside22 | 0.9654 | 0.0089 | 0.0002 | 0.0163 |
| recent | 2x | reversal70 | 0.9010 | -0.0010 | 0.0011 | 0.0064 |
| recent | 2x | high_proximity364 | 0.9470 | 0.0051 | -0.0185 | 0.0125 |
| recent | 2x | low_disagreement5_45 | 0.9180 | -0.0020 | 0.0060 | 0.0055 |
| recent | 3x | low_max22 | 0.9739 | -0.0101 | 0.0105 | -0.0058 |
| recent | 3x | signed_risk22 | 0.9146 | -0.0130 | 0.0162 | -0.0087 |
| recent | 3x | downside22 | 0.9651 | 0.0087 | -0.0001 | 0.0130 |
| recent | 3x | reversal70 | 0.9009 | -0.0035 | 0.0025 | 0.0008 |
| recent | 3x | high_proximity364 | 0.9468 | 0.0078 | -0.0196 | 0.0122 |
| recent | 3x | low_disagreement5_45 | 0.9179 | -0.0090 | 0.0106 | -0.0046 |

The strongest complementarity by this diagnostic is reversal70 (lowest correlation, about 0.901–0.905); the weakest is low_max22 (about 0.973–0.974). High_proximity364 has the clearest drawdown improvement versus the incumbent, about 1.7–2.3 percentage points, while keeping positive return deltas. These are descriptive results and do not imply promotion.

The JSON artifact contains every constituent and blend row, including fees, fills, terminal NAV, deltas versus the incumbent, and deltas versus the half-cash control.

Limitations: the 50/50 blend is an initial-capital curve average, not continuous rebalancing. It cannot establish shared-cash/order competition, max-two-crypto position behavior, or full V3 bot performance. Standalone all-period results do not trigger promotion or elimination.
