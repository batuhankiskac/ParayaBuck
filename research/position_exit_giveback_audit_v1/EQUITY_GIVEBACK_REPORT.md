# Equity giveback report

`MIXED_PROFIT_RETENTION_BEHAVIOR`. Current evidence does not justify a dedicated exit-design campaign for V3. This does not prove the current exits are optimal.

905 actual-exit episodes; 781 reached positive economic MFE; 279 of those later lost money (35.72%). Median positive-MFE retention is 51.50%, but meaningful-winner cohorts are materially stronger: +10% cohort retains a median 78.82% and 90.29% exits profitably; every +20% cohort episode remains profitable (17 cases). The largest excursions usually remain large winners, while modest positive excursions often disappear. Thus the motivating concern is partially representative, not a description of the typical large winner.

All 908 episodes, including 3 horizon-censored liquidations, produce $97,552.94 net economic P&L; NAV profit is the same within $0.000001. Primary tables below exclude censoring. Dollar results start from the canonical $100,000 account; returns are decimal fractions of total episode purchases.

| Metric | Mean | P10 | P25 | Median | P75 | P90 |
| --- | --- | --- | --- | --- | --- | --- |
| holding_days | 21.6626 | 7.0000 | 7.0000 | 13.0000 | 22.0000 | 49.0000 |
| final_pnl | 106.4785 | -895.9120 | -307.8709 | 70.8368 | 480.2146 | 996.4921 |
| final_return | 0.0087 | -0.0603 | -0.0241 | 0.0053 | 0.0350 | 0.0757 |
| mfe | 575.5613 | -47.1489 | 103.8250 | 337.2338 | 772.8233 | 1,395.3762 |
| mae | -451.4062 | -1,219.6023 | -663.9303 | -269.3010 | -51.2219 | 69.1381 |
| mfe_return | 0.0426 | -0.0037 | 0.0076 | 0.0263 | 0.0569 | 0.1091 |
| mae_return | -0.0322 | -0.0828 | -0.0482 | -0.0205 | -0.0042 | 0.0057 |
| giveback | 471.7539 | 0.0000 | 29.4196 | 211.2288 | 668.0311 | 1,205.7105 |
| retention | -2.0522 | -3.8081 | -0.7747 | 0.5150 | 0.9391 | 1.0000 |
| time_to_mfe_days | 12.1937 | 0.0000 | 1.0000 | 6.0000 | 13.0000 | 28.0000 |
| mfe_to_exit_days | 9.4689 | 0.0000 | 1.0000 | 6.0000 | 11.0000 | 21.9833 |

Retention survival (positive-MFE denominator):

| Retains at least | Fraction |
| --- | --- |
| 0.75 | 0.3803 |
| 0.5 | 0.5006 |
| 0.25 | 0.5749 |
| 0 | 0.6428 |

331 episodes have at least one later decision still selecting the name after its eventual marked peak. This is descriptive evidence of continued signal membership, not a causal isolation of momentum, sizing, band or cadence effects.

Time above levels (days, left-mark occupancy):

| Level | Mean | Median | P90 |
| --- | --- | --- | --- |
| 0.0 | 13.3196 | 6.0000 | 31.0000 |
| 0.05 | 5.2586 | 0.0000 | 13.0000 |
| 0.1 | 2.2302 | 0.0000 | 1.0000 |
| 0.2 | 0.6134 | 0.0000 | 0.0000 |
| 0.3 | 0.2685 | 0.0000 | 0.0000 |
| 0.4 | 0.1359 | 0.0000 | 0.0000 |

Peak to first subsequent negative economic mark (where observed):

| Mean days | Median days | P90 days |
| --- | --- | --- |
| 4.5415 | 2.0000 | 10.0000 |
