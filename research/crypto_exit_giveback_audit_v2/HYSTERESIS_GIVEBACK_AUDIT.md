# Hysteresis giveback audit

Scope: observational attribution of the current canonical replay only. Rank is ascending qualified 22-day realized volatility. At each scheduled decision, held scanner-eligible symbols with rank <=4 are candidates for retention; retained candidates are sorted by rank and capped at two. Vacant slots are filled from the ranking. Rank 3/4 retention is therefore conditional on capacity, not an unconditional lock.

The 96-hour schedule is epoch aligned. It is not measured from position entry and has no holding-age expiry. A due decision is a possible replacement opportunity, not a mandatory exit. Order execution follows later; neither MFE nor a next decision guarantees an executable peak price.

MFE rank is the latest **observed decision** rank at or before the MFE mark, accompanied by its timestamp and age in `CRYPTO_EPISODE_DIAGNOSTICS.json`. There is no invented daily rank between decisions. `retained_only_after_peak` means at least one strictly post-MFE decision selected this holding at rank 3/4 under hysteresis; it does not mean every post-peak day or decision was retained solely by that rule.

## Natural-exit cohort association

The groups below exclude two terminal-censored episodes. Membership is retrospective and endogenous to holding duration and rank changes. Differences describe associations; they do not estimate profit causally attributable to hysteresis or predict any alternative exit policy.

| Any post-peak rank 3/4 retention | Episodes | Median MFE return | Median final return | Median retention (positive MFE) | Median giveback USD | Median peak-to-exit days |
|---|---:|---:|---:|---:|---:|---:|
| False | 30 | 9.5% | 4.3% | 75.3% | 93.02 | 4.00 |
| True | 25 | 8.8% | -6.0% | -49.1% | 581.65 | 34.00 |

Decision-state counts use strictly post-MFE actual decisions while the episode still has inventory; entry-source decisions and the MFE instant itself are excluded.

| Cohort | Top two selected | Rank 3/4 retained | Not selected/zero target pending exit | Other selected |
|---|---:|---:|---:|---:|
| Natural | 264 | 84 | 113 | 0 |
| Terminal censored | 26 | 5 | 0 | 0 |
| Natural MFE >=30% | 59 | 22 | 13 | 0 |

## All 15 large-MFE natural episodes

“Large” here means normalized MFE >=30%, matching the descriptive cohort. The three state columns count actual post-peak decisions, not days. Delay is from peak mark to the next observed decision in the replay, even if that opportunity falls after episode exit.

| Episode | MFE date | MFE return | Final return | Retention | Top two | Rank 3/4 | Pending exit | Next decision delay hours |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| ETH/USD:1 | 2021-05-11 | 66.6% | 13.1% | 19.6% | 5 | 5 | 1 | 48.0 |
| LTC/USD:1 | 2021-08-23 | 50.1% | 42.1% | 83.9% | 0 | 1 | 1 | 48.0 |
| LTC/USD:2 | 2021-10-20 | 34.9% | 26.3% | 75.3% | 0 | 0 | 1 | 96.0 |
| BTC/USD:2 | 2021-11-08 | 60.0% | 30.5% | 50.9% | 26 | 3 | 1 | 24.0 |
| LINK/USD:1 | 2022-08-12 | 38.6% | 17.4% | 45.1% | 0 | 3 | 1 | 96.0 |
| LTC/USD:4 | 2022-11-23 | 44.3% | 43.5% | 98.3% | 0 | 0 | 1 | 24.0 |
| ETH/USD:5 | 2023-02-07 | 36.3% | 34.7% | 95.5% | 0 | 0 | 1 | 24.0 |
| ETH/USD:6 | 2023-04-16 | 35.4% | 32.7% | 92.5% | 0 | 0 | 1 | 24.0 |
| ETH/USD:7 | 2024-02-19 | 55.1% | 54.8% | 99.5% | 0 | 0 | 0 | 96.0 |
| LTC/USD:5 | 2024-03-11 | 42.1% | 21.0% | 49.9% | 0 | 2 | 1 | 72.0 |
| BTC/USD:4 | 2024-03-13 | 118.5% | 103.3% | 87.1% | 19 | 6 | 1 | 24.0 |
| LTC/USD:8 | 2025-05-10 | 36.8% | 27.7% | 75.1% | 0 | 2 | 1 | 48.0 |
| BTC/USD:5 | 2025-05-22 | 73.4% | 69.8% | 95.2% | 9 | 0 | 1 | 48.0 |
| XRP/USD:3 | 2025-07-17 | 43.0% | 40.6% | 94.5% | 0 | 0 | 1 | 48.0 |
| LTC/USD:9 | 2025-07-20 | 32.3% | 31.8% | 98.4% | 0 | 0 | 0 | 72.0 |

## Timing and limits

Complete actual rank paths, first observed rank/eligibility failure, first zero target, final liquidation-intent decision, final-fill source decision, and both epoch-boundary and actual-decision delays are recorded per episode. See `EXIT_REASON_AUDIT.md` for the liquidation timing ledger.

Two terminal observations use the final available source price dated **2026-07-30**. The replay horizon/account snapshot is **2026-07-31**. These dates describe different events; terminal policy marks are not natural strategy exits. MFE is a hindsight mark-to-market statistic, not a demonstrated collectible profit. No counterfactual exit, threshold optimization, parameter change, or alternative-strategy backtest was run.

## Requested mechanism interpretation

A: Giveback can occur while still top-two ranked: large-MFE episodes contain 59 post-peak top-two decision observations. B: Rank 3/4 retention also accompanies deterioration (22 observations among large-MFE episodes); the 96-hour cadence can defer reconsideration, but the data do not identify avoidable P&L. C: A holding-age “hysteresis expiry” does not exist; after each epoch opportunity, continued rank/capacity eligibility can retain the holding again. D: Delayed execution is separately visible between zero-target intent and flat inventory; partial liquidity/participation execution and the two-name retained selection capacity must not be conflated with a risk-overlay exit. No final natural closure in this replay is classified RISK_OVERLAY_ZERO_TARGET. E: The observed mechanisms are mixed. An additive dollar allocation of total giveback to these mechanisms is not identified without a counterfactual, which this audit deliberately does not run.
