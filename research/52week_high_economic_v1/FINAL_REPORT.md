# Frozen 52-week-high economic test

## Decision

`RESEARCH_REJECT`

The run is valid: `RESEARCH_RUN_VALID`. Candidate B was run exactly once on the frozen economic branch. Because the run is valid, the rejection is an economic frozen-gate result, not a data or validation inconclusive result. No retuning, rerun, promotion, broker order, PAPER/LIVE change, scheduler change, or next-candidate run was performed.

## Frozen identity and scope

| Item | Frozen value |
|---|---|
| Candidate | `equity_52week_high_close_v1` |
| Design SHA | `72b51a0e5c03f2ce0a8790b197cf360b26b33cd3` |
| Readiness SHA | `339d62f404a0b924ec78e3a67d8cddad7cee24c7` |
| Corrected main / origin/main | `839e92cf90e23cf83ce3cd2eb1b3ce34fefb97de` |
| Economic branch | `research/52week-high-economic-v1` |
| Date range | 2017-01-03 through 2026-07-30 |
| Development / validation split | 2017-01-03–2021-12-31 / 2022-01-03–2026-07-30 |
| Arms | Control A and Candidate B at 1x, 2x, 3x |
| Frozen construction | 252 split-adjusted closes through t; NYSE month-end; top 6; equal weights; 0.55 total; <=0.10 each |

The complete data, action, lifetime, universe, and endpoint identities are in [FROZEN_INPUT_RECEIPT.json](/Users/batuhankiskac/Desktop/ParayaBuck/research/52week_high_economic_v1/FROZEN_INPUT_RECEIPT.json). Production main is untouched.

## Run validity and parity

Fresh Control A 1x parity passed before Candidate B interpretation and execution: curve 2,406, targets 495, fills 2,054, terminal events 5, maximum metric difference `0.0`; target, fill, and terminal hashes match the expected control receipt. The independent verifier recomputed all stored period metrics and signal checks with zero failures. See [PRE_RUN_VALIDATION.json](/Users/batuhankiskac/Desktop/ParayaBuck/research/52week_high_economic_v1/PRE_RUN_VALIDATION.json) and [INDEPENDENT_VERIFICATION.json](/Users/batuhankiskac/Desktop/ParayaBuck/research/52week_high_economic_v1/INDEPENDENT_VERIFICATION.json).

## Economic results

All primary metrics for every arm, cost multiplier, and development/validation/full period are preserved in [CONTROL_A_RESULTS.json](/Users/batuhankiskac/Desktop/ParayaBuck/research/52week_high_economic_v1/CONTROL_A_RESULTS.json) and [CANDIDATE_B_RESULTS.json](/Users/batuhankiskac/Desktop/ParayaBuck/research/52week_high_economic_v1/CANDIDATE_B_RESULTS.json).

Full-period headline comparison:

| Arm | Net return | CAGR | Sharpe | Sortino | Max drawdown | Calmar | Target turnover | Costs |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Control A 1x | 97.553% | 7.369% | 0.688 | 0.884 | 20.436% | 0.361 | 17.487x | 7,529.57 |
| Candidate B 1x | 0.646% | 0.067% | 0.057 | 0.067 | 18.928% | 0.004 | 9.885x | 3,111.29 |
| Control A 2x | 87.532% | 6.787% | 0.640 | 0.822 | 21.331% | 0.318 | 17.490x | 14,638.83 |
| Candidate B 2x | -2.281% | -0.241% | 0.027 | 0.031 | 19.831% | -0.012 | 9.886x | 6,131.98 |
| Control A 3x | 78.051% | 6.210% | 0.591 | 0.761 | 22.170% | 0.280 | 17.490x | 21,353.00 |
| Candidate B 3x | -5.124% | -0.548% | -0.004 | -0.005 | 20.868% | -0.026 | 9.888x | 9,064.65 |

Candidate B 1x was positive in development (15.598% net, 2.945% CAGR, Sharpe 0.370) but negative in validation (-12.935% net, -2.979% CAGR, Sharpe -0.214) and nearly flat over the full period. Control A 1x validation was 22.720% net, 4.571% CAGR, Sharpe 0.467. Validation-year detail for both arms is in [YEARLY_VALIDATION.json](/Users/batuhankiskac/Desktop/ParayaBuck/research/52week_high_economic_v1/YEARLY_VALIDATION.json); Candidate B was negative in 2022, 2023, 2025, and 2026 YTD, with only 2024 positive.

## Frozen gate evaluation

| Gate | Result | Evidence / reason |
|---|---|---|
| G1 | PASS | Fresh parity, data identity, causality, signal, ledger, and independent metric checks passed. |
| G2 | FAIL | Candidate B 1x validation net return, CAGR, Sharpe, Sortino, and Calmar are negative. |
| G3 | FAIL | Full-period displacement versus Control A 1x is Sharpe -0.631, Sortino -0.817, Calmar -0.357. |
| G4 | FAIL | Validation is negative and risk-adjusted displacement is below the frozen thresholds. |
| G5 | FAIL | Candidate Calmar is far below Control A despite slightly lower full-period drawdown. |
| G6 | FAIL | Candidate B 2x and 3x full-period net return and CAGR are negative; 3x Sharpe is negative. |
| G7 | FAIL | Annualized target turnover is 9.885x (>6x) and scheduled-turnover p95 is 1.109x (>1x). Unfilled/rejected notional itself is 0.316% overall and below the yearly cap. |
| G8 | FAIL | At least five ordinary target names occurs on 85.09% of decisions (<95%); positive-P&L concentration itself is only 3.07%. |
| G9 | PASS | Shared scanner, exclusions, capital, slots, engine, costs, and production boundaries were preserved. |

The machine-readable frozen table is [GATE_TABLE.json](/Users/batuhankiskac/Desktop/ParayaBuck/research/52week_high_economic_v1/GATE_TABLE.json), and the exact decision is [RUN_DECISION.json](/Users/batuhankiskac/Desktop/ParayaBuck/research/52week_high_economic_v1/RUN_DECISION.json).

## Implementation, cadence, and concentration diagnostics

Candidate B produced 114 scheduled and 114 actual decisions, with no skipped decisions. Decisions ran on NYSE month-end sessions from `2017-01-31T05:00:00+00:00` through `2026-06-30T04:00:00+00:00`; all executions followed their decision timestamp. The bounded observation capacity was 30, all persisted audit rows were causal and rank-prefix-consistent, and fill completion was 99.684%. See [CADENCE_AUDIT.json](/Users/batuhankiskac/Desktop/ParayaBuck/research/52week_high_economic_v1/CADENCE_AUDIT.json), [CANDIDATE_B_SIGNAL_RANK_AUDIT.json](/Users/batuhankiskac/Desktop/ParayaBuck/research/52week_high_economic_v1/CANDIDATE_B_SIGNAL_RANK_AUDIT.json), and [CANDIDATE_B_DECISION_COVERAGE.json](/Users/batuhankiskac/Desktop/ParayaBuck/research/52week_high_economic_v1/CANDIDATE_B_DECISION_COVERAGE.json).

There were 234 unique filled symbols. Sector/category context is unavailable in the frozen canonical input and was not inferred. Maximum realized concentration was 12.936%; the largest positive realized-P&L contributor was AMD at 3.065%. The attribution-only diagnostic was not used to rerun or re-optimize the strategy. See [CONCENTRATION_DIAGNOSTICS.json](/Users/batuhankiskac/Desktop/ParayaBuck/research/52week_high_economic_v1/CONCENTRATION_DIAGNOSTICS.json).

Cost and implementation details, including partial fills, rejected orders, yearly unfilled fractions, and 1x/2x/3x cost stress, are in [COST_STRESS.json](/Users/batuhankiskac/Desktop/ParayaBuck/research/52week_high_economic_v1/COST_STRESS.json) and [TURNOVER_DIAGNOSTICS.json](/Users/batuhankiskac/Desktop/ParayaBuck/research/52week_high_economic_v1/TURNOVER_DIAGNOSTICS.json).

## Production boundary and handoff

No production strategy registration, allocation, risk limit, crypto path, LIVE/PAPER setting, scheduler, or broker integration was modified. The candidate remains research-only. This economic branch contains the runner, independent verifier, compact artifacts, and this report; it is not a promotion or forward-paper approval. The branch was created from readiness SHA `339d62f404a0b924ec78e3a67d8cddad7cee24c7`; the final local branch commit SHA is reported in the handoff alongside the committed artifact tree.

There is no next candidate.
