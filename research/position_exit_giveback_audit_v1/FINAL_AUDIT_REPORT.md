# Executive conclusion

Equity shows **mixed profit retention**, not a demonstrated systematic collapse of large winners. Small unrealized gains often disappear, but large winners usually stay profitable and support long-run net P&L. Crypto cannot be judged because the historical replay parity gate failed. Overall: `EXIT_AUDIT_INCONCLUSIVE`.

# Production strategy exit behavior

## V3

Weekly completed-bar decisions; positive long-horizon residual-momentum eligibility; multi-horizon consensus ranking; inverse-volatility sizing; eight raw selected names subject to the six-position account cap; turnover band/minimum notional; next-bar execution. Recent price reversal need not remove a still-eligible high-ranked name. No fixed take profit, trailing stop, profit lock, break-even stop or position-profit-dependent exit in this portfolio strategy path.

## Crypto

22-day low realized-volatility ranking, two equal-weight slots, 96-hour cadence; eligible existing holdings may stay through rank four, vacancies filled by rank. No position-profit-dependent exit. Runtime risk caps, order execution and canonical drawdown-shadow semantics are separate from signal exits. See CURRENT_STRATEGY_EXIT_AUDIT.md for exact code references and full distinctions.

# Episode methodology

Flat-to-flat inventory; resizing stays in the episode. Net cash-flow-plus-mark P&L includes canonical dividends, fees and embedded execution costs. Completed daily marks plus actual final settlement endpoints; no intraday highs. Returns divide by fixed total episode purchase outlay, including fees. This conservative ex-post normalization differs from price appreciation. All 908 equity episodes reconcile to NAV; the 3 horizon-censored final liquidations are excluded from 905 actual-exit diagnostics. See EPISODE_DEFINITION.md.

# Equity results


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

# Crypto results


`AUDIT_INCONCLUSIVE` / `AUDIT_INCONCLUSIVE_REPLAY_MISMATCH`.

The exact frozen input bytes were recovered and verified. Two fresh current-production runs (one instrumented, one uninstrumented) match exactly. However, the historical canonical universe hash and all six stored output hashes disagree. Fresh fill count 388 vs historical 454; decisions 455 vs 453. Historical ending equity $110,539.90 differs from fresh $105,481.52. These are parity evidence, not a giveback comparison or an alternative-exit backtest.

The dataset is crypto_expanded_1h.csv, aggregated into complete daily bars, with hourly PIT universe snapshots, 2021-04-01 through 2026-07-31. Frozen dataset SHA: 73425011c3e021a10a5d4d7d29c2c169957000f89b23afb9aea9d2354729067e. See CRYPTO_REPLAY_PARITY.json and crypto_replay.py for source commit and reconstruction. The present output is quarantined; episode count, holding time, MFE, MAE, retention, round trips, exit-reason proportions, +20/+30/+40% outcomes and right-tail attribution are N/A, not zero. No shifted path is interpreted as the historical production control.

Code confirms retention of eligible holdings through rank four, new vacancies filled from the best-ranked candidates, equal weights and a 96-hour cadence. It intentionally delays replacement in return for lower turnover. Vacancies do not rescue a holder that fails the rule; trade bands, minimum notional and next-bar execution can delay actual flattening. How often hysteresis extends holdings beyond eventual peaks cannot be stated on an accepted replay in this audit.

Historical half-year walk-forward folds overlap the promotion dataset; they are not untouched post-promotion validation. Repairing/explaining canonical reproducibility is necessary before an empirical crypto exit campaign can be justified. No strategy was changed to force parity.

# Profit-level diagnostics


Equity actual exits only. Overlapping descriptive cohorts, fixed total-purchases return denominator; values are decimal fractions. No exit at any level is simulated.

| Ever reached | N | Final median | Final mean | Profitable fraction | Median retention | Exit <=0 fraction | Median crossing→exit days |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 0.05 | 258 | 0.0596 | 0.0667 | 0.8605 | 0.7778 | 0.1395 | 12.0000 |
| 0.1 | 103 | 0.1091 | 0.1104 | 0.9029 | 0.7882 | 0.0971 | 15.0000 |
| 0.2 | 17 | 0.2358 | 0.2790 | 1.0000 | 0.9017 | 0.0000 | 14.0000 |
| 0.3 | 9 | 0.3752 | 0.3772 | 1.0000 | 0.9336 | 0.0000 | 21.0000 |
| 0.4 | 5 | 0.4248 | 0.4433 | 1.0000 | 0.9336 | 0.0000 | 25.0000 |

Crypto: N/A — AUDIT_INCONCLUSIVE_REPLAY_MISMATCH.

# Right-tail dependence


Equity actual exits; no trades removed and no strategy rerun. Top 10% uses ceil(905×0.10)=91 episodes. Net contribution shares may exceed 100% because other episodes lose money.

| Group | N | P&L $ | Share positive P&L | Share net P&L |
| --- | --- | --- | --- | --- |
| top1 | 1 | 8,659.6112 | 0.0265 | 0.0899 |
| top5 | 5 | 32,815.2099 | 0.1006 | 0.3405 |
| top10 | 10 | 53,451.4984 | 0.1638 | 0.5547 |
| top10pct | 91 | 178,439.9878 | 0.5468 | 1.8517 |

Largest ten contributors; full top-decile identities and excursions are in EQUITY_SUMMARY.json:

| Episode | Final P&L $ | MFE return | Final return |
| --- | --- | --- | --- |
| equity_residual_momentum_v3:AAPL:7 | 8,659.6112 | 0.6498 | 0.6393 |
| equity_residual_momentum_v3:APH:3 | 6,340.6361 | 0.3849 | 0.3752 |
| equity_residual_momentum_v3:GLW:4 | 6,114.2887 | 0.3322 | 0.3322 |
| equity_residual_momentum_v3:QLD:2 | 5,974.4207 | 0.4048 | 0.4048 |
| equity_residual_momentum_v3:ARKK:1 | 5,726.2532 | 0.7143 | 0.4248 |
| equity_residual_momentum_v3:META:2 | 5,670.3187 | 0.4604 | 0.4299 |
| equity_residual_momentum_v3:CAT:8 | 5,576.9025 | 0.4290 | 0.3176 |
| equity_residual_momentum_v3:RCL:4 | 3,401.9601 | 0.2069 | 0.2020 |
| equity_residual_momentum_v3:FCX:1 | 2,994.0567 | 0.2357 | 0.2129 |
| equity_residual_momentum_v3:UBER:2 | 2,993.0506 | 0.2925 | 0.2117 |

The top decile supplies 54.68% of positive P&L and 185.17% of net P&L. Long-run net economics depend materially on the right tail. Aggressive early profit taking could truncate those trends; this is a structural risk, not a tested claim about an exit rule. The +20% cohort has 17 profitable exits, median final return 23.58% and median retention 90.17%; +40% has five cases, median final 42.48%, median retention 93.36%. Some winners continued well beyond early diagnostic levels. Small tail samples prevent broad certainty. Crypto: N/A pending parity.

# Exit-reason analysis


Classification uses the actual final sell decision timestamp and captured post-overlay target plus preserved strategy metadata. An originally selected name removed by the overlay is risk_overlay; an explicit global risk_off_detail is eligibility_gate_failure; otherwise an omitted equity target is alpha_no_longer_selected. The latter cannot distinguish individual eligibility loss from rank displacement with selected-only metadata. No cause is guessed. A remaining positive target would be rebalance_sizing. Terminal policies distinguish lifetime events from horizon censoring.

| Category | N | Median MFE $ | Median MFE return | Median final $ | Median final return | Median giveback $ | Median retention | Median hold days | Median peak→exit days |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| alpha_no_longer_selected | 673 | 359.5561 | 0.0286 | 58.6297 | 0.0044 | 240.0466 | 0.4506 | 13.0000 | 6.0000 |
| lifecycle_terminal | 2 | 396.2010 | 0.0354 | -453.0115 | -0.0409 | 849.2124 | -1.1262 | 28.5000 | 16.0000 |
| risk_overlay | 230 | 280.4838 | 0.0222 | 81.3603 | 0.0070 | 141.2070 | 0.6641 | 14.0000 | 5.0208 |

Horizon-censored liquidation count: 3 (excluded above). The zero-count categories are not evidence that the code cannot produce them. Risk overlay count is not equivalent to drawdown forced sales: count caps can remove a selected name, while drawdown is shadowed in this canonical environment. Crypto proportions are N/A pending parity.

# Peak-to-exit delay

Across actual equity exits the median is six calendar days; the 90th percentile is about 22 days. “After the peak” is hindsight. Weekly decision timing, persistent eligibility/ranking and execution constraints explain why a position may remain held through reversals. Captured selected-name metadata demonstrates continued selection but does not causally allocate giveback among cadence, ranking, inverse-vol sizing and turnover bands. Portfolio-level realized/unrealized paths are persisted separately from episode giveback and NAV drawdown.

# Development vs validation stability


Established equity split: development through 2021-12-31; validation begins 2022-01-03; full through 2026-07-30. Fully contained episodes only in period cohorts; six boundary-crossing episodes remain in full and in a separate cohort. No new split was chosen from outcomes. Validation here is the established canonical research period, not newly untouched data.

| Period | N | Median MFE return | Median final return | Median retention | Median giveback $ | Positive→loss fraction | Median peak→exit days |
| --- | --- | --- | --- | --- | --- | --- | --- |
| full | 905 | 0.0263 | 0.0053 | 0.5150 | 211.2288 | 0.3572 | 6.0000 |
| development | 445 | 0.0259 | 0.0085 | 0.6133 | 147.7784 | 0.3307 | 6.0000 |
| validation | 454 | 0.0260 | 0.0034 | 0.3912 | 298.9785 | 0.3760 | 6.0000 |
| cross_boundary | 6 | 0.0590 | -0.0310 | -0.5455 | 1,318.8775 | 0.8333 | 28.0000 |

All predeclared profit levels by period:

| Period | Level | N | Final median | Median retention | Nonpositive exit fraction | Median crossing→exit days |
| --- | --- | --- | --- | --- | --- | --- |
| full | 0.05 | 258 | 0.0596 | 0.7778 | 0.1395 | 12.0000 |
| full | 0.1 | 103 | 0.1091 | 0.7882 | 0.0971 | 15.0000 |
| full | 0.2 | 17 | 0.2358 | 0.9017 | 0.0000 | 14.0000 |
| full | 0.3 | 9 | 0.3752 | 0.9336 | 0.0000 | 21.0000 |
| full | 0.4 | 5 | 0.4248 | 0.9336 | 0.0000 | 25.0000 |
| development | 0.05 | 129 | 0.0595 | 0.7886 | 0.1240 | 12.0417 |
| development | 0.1 | 52 | 0.1014 | 0.7821 | 0.1346 | 18.5000 |
| development | 0.2 | 7 | 0.2896 | 0.9036 | 0.0000 | 70.9583 |
| development | 0.3 | 4 | 0.4148 | 0.9312 | 0.0000 | 80.5208 |
| development | 0.4 | 3 | 0.4248 | 0.9839 | 0.0000 | 90.0417 |
| validation | 0.05 | 125 | 0.0602 | 0.7773 | 0.1360 | 11.0000 |
| validation | 0.1 | 50 | 0.1117 | 0.8030 | 0.0600 | 10.5000 |
| validation | 0.2 | 10 | 0.2068 | 0.8210 | 0.0000 | 7.0000 |
| validation | 0.3 | 5 | 0.3322 | 0.9336 | 0.0000 | 4.0000 |
| validation | 0.4 | 2 | 0.3737 | 0.8370 | 0.0000 | 8.0000 |
| cross_boundary | 0.05 | 4 | -0.0322 | -0.4292 | 0.7500 | 59.5000 |
| cross_boundary | 0.1 | 1 | 0.0845 | 0.5973 | 0.0000 | 63.0000 |
| cross_boundary | 0.2 | 0 | N/A | N/A | N/A | N/A |
| cross_boundary | 0.3 | 0 | N/A | N/A | N/A | N/A |
| cross_boundary | 0.4 | 0 | N/A | N/A | N/A | N/A |

Annual actual-exit cohorts (2026 partial year):

| Year | N | Median holding days | Median MFE return | Median final return | Median retention | Positive→loss fraction | Median peak→exit days |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 2017 | 100 | 8.0000 | 0.0213 | 0.0063 | 0.6253 | 0.3133 | 6.0000 |
| 2018 | 83 | 13.9583 | 0.0328 | 0.0071 | 0.5294 | 0.3239 | 6.0000 |
| 2019 | 91 | 14.0000 | 0.0228 | 0.0085 | 0.7440 | 0.3210 | 5.0000 |
| 2020 | 85 | 7.0000 | 0.0247 | 0.0067 | 0.5305 | 0.3636 | 5.0000 |
| 2021 | 86 | 14.0000 | 0.0323 | 0.0124 | 0.5919 | 0.3333 | 6.0000 |
| 2022 | 98 | 8.0000 | 0.0246 | -0.0157 | 0.1441 | 0.4605 | 6.0000 |
| 2023 | 102 | 13.0000 | 0.0243 | 0.0057 | 0.4513 | 0.3626 | 6.0000 |
| 2024 | 90 | 14.0000 | 0.0298 | 0.0075 | 0.6323 | 0.2805 | 5.0000 |
| 2025 | 107 | 8.0000 | 0.0268 | 0.0045 | 0.4214 | 0.3763 | 6.0000 |
| 2026 | 63 | 13.0000 | 0.0305 | -0.0032 | 0.1078 | 0.4727 | 6.0000 |

Broad positive-MFE retention weakens from development to validation, but meaningful winners do not show the same collapse: +10% nonpositive exit frequency is 13.46% development versus 6.00% validation; median retention is 78.21% versus 80.30%. Both periods retain profitable outcomes for every +20% case, with only 7 and 10 cases. Dollar giveback comparisons also reflect account growth and changing notional sizing. Crypto periods: N/A pending replay parity; historical walk-forward folds are overlapping diagnostics.

# Equity conclusion

`MIXED_PROFIT_RETENTION_BEHAVIOR`.

The evidence does not currently justify a dedicated V3 profit-protection optimization campaign: large-winner retention remains strong in validation, while the right tail contributes more than total net profits. Modest-gain erosion is real and persistent; it is not proof that protecting those gains improves the strategy.

# Crypto conclusion

`AUDIT_INCONCLUSIVE` with `AUDIT_INCONCLUSIVE_REPLAY_MISMATCH`. Reproducibility must be resolved before interpreting profit retention.

# Overall decision

`EXIT_AUDIT_INCONCLUSIVE`. The apparent issue cannot be assigned mainly to equity, crypto, both or neither while crypto is unverified. Equity alone does not substantiate a general large-winner round-trip defect.

# Interpretation

The equity anecdote is partially representative: 10 of 103 normalized +10% episodes exit nonpositive, but the typical episode in that cohort retains 78.82% of peak economic P&L. All 17 +20% cases exit profitably. An individual price-return anecdote is not directly comparable to a resized episode's capital-normalized economic return. The crypto anecdote remains unclassified. CURRENT_PAPER_TRADE_HISTORY_NOT_AUDITED; broker/account state was not accessed.

# What this audit does NOT prove

MFE is hindsight. Peak profit was not necessarily capturable. No alternative exit was tested. This audit establishes neither optimality of existing exits nor improvement from any profit-protection rule. Descriptive thresholds were supplied in advance, not fitted. Small extreme cohorts, normalization sensitivity, repeated symbols and shared market regimes mean these observations are not independent statistical proof. Canonical offline drawdown is shadowed; these results are not a claim of identical PAPER account behavior.

# Potential future research categories

No exit-design campaign is recommended from this audit. The next necessary crypto work is canonical-replay reconciliation, not an exit change.

# Independent verification

All 908 equity episodes independently reconstructed with fee-capitalized average-cost accounting against the primary cash-flow method; includes every extreme, every +10% case, resizing, dividends, splits and final costs. All persisted path points, final P&L, MFE/MAE timestamps, giveback, retention and exit reasons checked. Account reconciliation residual is below $0.000001. See INDEPENDENT_VERIFICATION.json and VALIDATION.json for exact executed checks. Crypto economic verification is not applicable after its replay gate failure.

# Production state

Source base, local main and freshly fetched origin/main: 754f4060b1eb38593201b2a269e9593d083eb534. Research branch created directly from clean main; schedule-13d branch was not merged or used as a base. Production strategy, configuration, risk, PAPER/LIVE, persistence and broker orders unchanged. Scripts write only this research directory, tests use synthetic fixtures. No alternative-exit result files.

# Files

All required artifacts reside in research/position_exit_giveback_audit_v1/. EQUITY_EPISODE_DIAGNOSTICS.json contains complete fills, decisions, actions, paths and metrics. EQUITY_SUMMARY.json contains full-period, split-period, yearly, exit-reason and tail distributions. Gzipped raw observations permit independent reconstruction. CRYPTO_EPISODE_DIAGNOSTICS.json records unavailable metrics as null, never fabricated zero. Replay scripts and parity receipts retain reproducible provenance. EXTREME_EPISODES.md provides audit examples.

Reproduce: `.venv/bin/python research/position_exit_giveback_audit_v1/equity_replay.py`; `.venv/bin/python -m research.position_exit_giveback_audit_v1.crypto_replay`; `.venv/bin/python -m research.position_exit_giveback_audit_v1.build_reports`. Historical CSVs are existing local canonical inputs, identified by receipt SHA256; they are not copied into this branch.

# Final research branch SHA

The final committed research SHA is supplied in the delivery message and obtainable with `git rev-parse research/position-exit-giveback-audit-v1`. A commit cannot contain its own resulting SHA; the source/base SHA above is not presented as the deliverable SHA.
