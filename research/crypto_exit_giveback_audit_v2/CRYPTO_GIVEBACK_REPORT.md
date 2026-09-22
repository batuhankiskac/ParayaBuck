# Crypto giveback diagnostics

Primary behavioral population: 55 naturally closed episodes. Full-account reconciliation: 57 episodes including two terminal liquidations, which are censored for natural-exit behavior. All extrema use completed canonical bar marks; final fill P&L is distinct. P&L includes realized results from partial sales and remaining marked holdings, with canonical costs. Normalized returns divide by the episode capital denominator documented in EPISODE_DEFINITION.md.

## Natural-exit distributions

| Metric | Mean | P10 | P25 | Median | P75 | P90 |
|---|---:|---:|---:|---:|---:|---:|
| holding_days | 70.96 | 8.00 | 12.00 | 36.00 | 78.50 | 210.20 |
| time_to_mfe_days | 34.60 | 1.00 | 3.00 | 8.00 | 37.50 | 69.20 |
| mfe_to_exit_days | 36.36 | 2.00 | 3.50 | 9.00 | 38.00 | 102.00 |
| days_below_peak_after_mfe | 35.36 | 1.00 | 2.50 | 8.00 | 37.00 | 101.00 |
| days_positive_after_mfe | 12.98 | 1.00 | 2.00 | 4.00 | 8.00 | 31.80 |
| mfe | 546.74 | 13.37 | 91.48 | 320.45 | 885.77 | 1250.57 |
| mae | -376.64 | -1112.38 | -378.32 | -191.47 | -81.76 | -39.33 |
| final_pnl | 109.26 | -941.08 | -306.56 | 77.26 | 563.49 | 1057.57 |
| giveback | 460.28 | 31.93 | 76.57 | 236.69 | 610.61 | 1362.43 |
| mfe_return | 19.42% | 0.26% | 3.14% | 8.85% | 33.61% | 47.79% |
| mae_return | -10.61% | -28.08% | -13.29% | -7.17% | -2.81% | -1.41% |
| final_return | 6.73% | -18.77% | -11.21% | 2.68% | 21.74% | 38.27% |
| retention | -239.82% | -1019.35% | -170.83% | 37.27% | 83.07% | 95.22% |

Giveback and retention summaries condition on MFE > 0 (50 natural episodes). Mean retention is sensitive to tiny positive MFE and should not substitute for threshold-conditioned medians. Time distributions use all 55 natural episodes; erosion times below condition on actually reaching that reduction. Durations count completed observed days, and are not intraday time integrals.

| Profit reduction from hindsight MFE | Observed episodes | Mean days | Median days | P25 | P75 |
|---|---:|---:|---:|---:|---:|
| 25% | 35 | 2.69 | 1.00 | 1.00 | 2.50 |
| 50% | 31 | 6.10 | 2.00 | 1.00 | 4.50 |
| 75% | 25 | 6.76 | 2.00 | 1.00 | 5.00 |
| 100% | 24 | 7.75 | 3.00 | 2.00 | 6.25 |

## Round trips

20 of 50 positive-MFE natural episodes ended negative (40%); none ended exactly zero. The near-flat descriptive band is abs(final normalized return) <= 1%, not an exit rule. Two positive-MFE natural episodes ended near flat; both finished slightly positive.

| Retention band | Episodes | Share of 50 positive-MFE episodes |
|---|---:|---:|
| 25_to_50pct | 7 | 14.00% |
| 50_to_75pct | 4 | 8.00% |
| at_least_75pct | 17 | 34.00% |
| nonpositive | 20 | 40.00% |
| positive_below_25pct | 2 | 4.00% |

Band boundaries are <=0%, (0%,25%), [25%,50%), [50%,75%), and >=75%; no clipping.

| Episode | Entry | MFE | Final | Retention | Exit | Classification |
|---|---|---:|---:|---:|---|---|
| BTC/USD:1 | 2021-04-02 | 6.32% | -38.78% | -614.16% | 2021-06-24 | negative |
| ETH/USD:2 | 2021-08-30 | 18.33% | -2.52% | -13.76% | 2021-10-01 | negative |
| AAVE/USD:1 | 2021-10-25 | 7.42% | -31.02% | -418.37% | 2021-12-12 | negative |
| ETH/USD:3 | 2021-12-16 | 1.45% | -28.30% | -1957.19% | 2022-01-29 | negative |
| ETH/USD:4 | 2022-03-26 | 7.40% | -38.26% | -516.90% | 2022-07-08 | negative |
| GRT/USD:1 | 2022-07-08 | 2.92% | -11.69% | -399.78% | 2022-07-12 | negative |
| DOGE/USD:3 | 2022-09-10 | 3.00% | -2.74% | -91.26% | 2022-10-08 | negative |
| BCH/USD:2 | 2023-04-18 | 1.18% | -14.29% | -1215.84% | 2023-05-12 | negative |
| BTC/USD:3 | 2022-04-03 | 0.52% | -12.65% | -2454.24% | 2023-06-05 | negative |
| XRP/USD:1 | 2024-04-08 | 3.69% | -15.39% | -417.43% | 2024-05-14 | negative |
| DOT/USD:1 | 2024-06-03 | 0.64% | -11.22% | -1760.55% | 2024-08-29 | negative |
| DOGE/USD:4 | 2024-08-22 | 5.89% | -5.51% | -93.45% | 2024-09-01 | negative |
| XRP/USD:2 | 2024-09-15 | 0.09% | -1.74% | -1961.68% | 2024-09-27 | negative |
| ETH/USD:11 | 2024-10-17 | 26.64% | -21.02% | -78.92% | 2025-04-11 | negative |
| ETH/USD:12 | 2025-08-13 | 3.27% | -5.98% | -182.96% | 2025-08-21 | negative |
| LTC/USD:10 | 2025-12-15 | 8.52% | -11.46% | -134.45% | 2026-01-23 | negative |
| BCH/USD:5 | 2025-08-21 | 5.46% | -2.68% | -49.09% | 2026-02-09 | negative |
| LTC/USD:11 | 2026-02-09 | 2.71% | 0.71% | 26.12% | 2026-02-21 | positive; near flat |
| BTC/USD:6 | 2025-07-24 | 3.28% | -32.75% | -997.51% | 2026-03-14 | negative |
| XRP/USD:5 | 2026-03-01 | 3.52% | 0.87% | 24.73% | 2026-03-14 | positive; near flat |
| LTC/USD:12 | 2026-04-06 | 10.76% | -10.91% | -101.39% | 2026-06-04 | negative |
| AVAX/USD:1 | 2026-02-21 | 1.85% | -11.19% | -603.45% | 2026-06-07 | negative |

The +10% group has 3/27 nonpositive exits, +20% has 1/19, and +30% has 0/15. Widespread modest-profit round trips are not equivalent to typical large-winner collapse.

## Observed post-peak retention association

| Ever retained outside top two after peak | Natural episodes | Median giveback | Median retention | Median peak-to-exit days |
|---|---:|---:|---:|---:|
| False | 30 | 93.02 | 75.27% | 4.00 |
| True | 25 | 581.65 | -49.09% | 34.00 |

These groups are observational, with different exposure lengths and outcomes; they cannot measure how much loss a changed exit would avoid. The 96-hour schedule is not a holding-age lock. See HYSTERESIS_GIVEBACK_AUDIT.md for decision clocks and EXIT_REASON_AUDIT.md for verified reasons.

MFE is hindsight; the peak was not necessarily tradable. No alternative exit was tested. Current PAPER history is not represented.
