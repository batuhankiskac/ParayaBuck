# Overnight Reversal v1 Frozen Economic Experiment

## Decision

- Result validity: **RESEARCH_RUN_VALID**
- Economic verdict: **RESEARCH_REJECT**
- Promotion authorization: **False**. This research result does not authorize PAPER or LIVE deployment.
- Independent verification: **PASS**

## Frozen identity and scope

- Economic branch: `research/overnight-reversal-v1`
- Design branch and tip: `research/overnight-intraday-design-v1` at `7ec53efecc4d37cab17d0d96f194389b4513a69b`
- Design parent used for the economic branch: `7ec53efecc4d37cab17d0d96f194389b4513a69b`
- Candidate: `equity_overnight_reversal_v1` version `1.0.0-research`
- Control: current configured `current configured equity_residual_momentum_v3, weekly, unchanged production parameters`
- Data feed: SIP regular-session daily bars; provider open proxy, not an auction claim.
- No parameter search, post-result rescue, new data feed, registry change, PAPER change, or LIVE change.

## Candidate rule

For completed common session `d` and immediately prior common SPY session `p(d)`:

`ON_i,d = Open_i,d / Close_i,p(d) - 1`; `ON_SPY,d = Open_SPY,d / Close_SPY,p(d) - 1`; `score = -(ON_i,d - ON_SPY,d)`.

The decision is after the completed bar `d`; any order fills only on a later tradable daily-bar provider-open proxy. The candidate selects at most six long names at exactly `0.55 / 6`, leaves unused allocation in cash, excludes SPY from targets, and uses the current account/risk/scanner/lifecycle/cost policies.

## Data and observation audit

- Bars: `bb7aaae86bc1909c6b0a1094a59430f5ed9900bb3ec63891bbdcda8cda055139`; lifetimes: `ed675d9b6ba7f173b65ae2b5272ffb62e09d6a007ed36ea22d3aba7a36774596`; corporate actions: `a0a60755a3f6adfc06a5ef0e6ed82852e1584c20fdb62cefed92b765dc9adc23`.
- Frozen normalized rows and quality gates were independently re-read from the pinned files.
- Signal audit: `{'rows_seen': 761361, 'valid_pairs_seen': 757662, 'sampled_rows': 765, 'reconstructed_rows': 761361, 'decision_groups': 2657, 'boundary_decision_groups': 75, 'duplicate_keys': False, 'coverage_mode': 'full_population_plus_boundary_checks', 'score_reconstruction': 'PASS'}`.
- Candidate and control used the same eligible PIT pool; alpha-specific observation sets were persisted separately.
- Decision coverage summary: `{"A": {"actual_decisions": 495, "average_observation_set_size": 29.823861497929997, "average_selected_names": 5.869025216409485, "average_valid_eligible_names": 285.1569439217162, "first_decision": "2017-02-06", "last_decision": "2026-07-27", "longest_actual_decision_gap_days": 8, "scheduled_decisions": 500, "skipped_decisions": 5, "skipped_reasons": {"insufficient_strategy_history": 5}, "target_notional_coverage_ratio": 0.9999998239666714, "target_notional_within_two_ratio": 0.9974407011469569, "valid_overnight_cross_sections": 2657}, "B": {"actual_decisions": 2406, "average_observation_set_size": 29.823861497929997, "average_selected_names": 5.869025216409485, "average_valid_eligible_names": 285.1569439217162, "first_decision": "2017-01-03", "last_decision": "2026-07-30", "longest_actual_decision_gap_days": 4, "scheduled_decisions": 2406, "skipped_decisions": 0, "skipped_reasons": {}, "target_notional_coverage_ratio": 0.9996207162546029, "target_notional_within_two_ratio": 0.9960331885195528, "valid_overnight_cross_sections": 2657}}`. Full fill-completion records remain in `decision_coverage.json`.

## Control A — V3

A/1x development: CAGR 10.0015%, total 60.9781%, Sharpe 0.877, max DD 15.5323%, one-way target turnover 16.547x

A/1x validation: CAGR 4.5736%, total 22.7203%, Sharpe 0.467, max DD 18.9593%, one-way target turnover 18.523x

A/1x full: CAGR 7.3714%, total 97.5529%, Sharpe 0.688, max DD 20.4359%, one-way target turnover 17.492x

A/2x development: CAGR 9.4350%, total 56.8795%, Sharpe 0.833, max DD 15.5702%, one-way target turnover 16.549x

A/2x validation: CAGR 3.9754%, total 19.5391%, Sharpe 0.414, max DD 19.5233%, one-way target turnover 18.526x

A/2x full: CAGR 6.7891%, total 87.5323%, Sharpe 0.640, max DD 21.3306%, one-way target turnover 17.494x

A/3x development: CAGR 8.8754%, total 52.9135%, Sharpe 0.789, max DD 15.6081%, one-way target turnover 16.552x

A/3x validation: CAGR 3.3803%, total 16.4388%, Sharpe 0.361, max DD 20.0366%, one-way target turnover 18.525x

A/3x full: CAGR 6.2119%, total 78.0506%, Sharpe 0.592, max DD 22.1698%, one-way target turnover 17.495x

## Candidate B — overnight reversal

B/1x development: CAGR -4.0607%, total -18.7016%, Sharpe -0.212, max DD 30.3871%, one-way target turnover 175.568x

B/1x validation: CAGR -8.3747%, total -32.9955%, Sharpe -0.428, max DD 51.0675%, one-way target turnover 167.225x

B/1x full: CAGR -6.1486%, total -45.5264%, Sharpe -0.323, max DD 59.0133%, one-way target turnover 171.578x

B/2x development: CAGR -8.7424%, total -36.6768%, Sharpe -0.556, max DD 41.4122%, one-way target turnover 174.819x

B/2x validation: CAGR -12.8240%, total -46.6502%, Sharpe -0.721, max DD 57.7556%, one-way target turnover 167.035x

B/2x full: CAGR -10.7177%, total -66.2172%, Sharpe -0.639, max DD 73.4664%, one-way target turnover 171.097x

B/3x development: CAGR -13.5195%, total -51.5898%, Sharpe -0.926, max DD 54.8096%, one-way target turnover 173.337x

B/3x validation: CAGR -18.2126%, total -60.1644%, Sharpe -1.103, max DD 65.6105%, one-way target turnover 166.125x

B/3x full: CAGR -15.7966%, total -80.7155%, Sharpe -1.015, max DD 84.0829%, one-way target turnover 169.888x

All three cost scenarios are present for development, validation, and full periods above; the complete metric tables are in `v3_control_results.json` and `candidate_results.json`.

## Risk-adjusted comparison with V3

- Candidate B minus Control A full-period 1x Sharpe/Sortino/Calmar deltas: `[-1.0108327526268597, -1.3361622188610467, -0.46489819465904936]`.
- Candidate B minus Control A validation 1x Sharpe/Sortino/Calmar deltas: `[-0.8947069220695059, -1.235142028555235, -0.40522772706691024]`.
- Candidate B 1x full-period maximum drawdown: `59.0133%`; validation: `51.0675%`.

Cost cases `1x`, `2x`, and `3x` scale slippage, spread, and effective-dated equity regulatory fees; commission remains the configured zero value. Full case artifacts are `case_A_1x.json.gz`, `case_A_2x.json.gz`, `case_A_3x.json.gz`, `case_B_1x.json.gz`, `case_B_2x.json.gz`, and `case_B_3x.json.gz`.

## Yearly validation

| Year | Candidate B 1x total return | Control A 1x total return |
|---|---:|---:|
| 2022 | -3.1795% | -13.8342% |
| 2023 | -14.6593% | 10.6045% |
| 2024 | 0.2392% | 11.6299% |
| 2025 | -35.4075% | 10.1030% |
| 2026_partial | 25.2451% | 4.7681% |

## Turnover, risk, and concentration

- Coverage/turnover artifact: `decision_coverage.json` and `turnover_diagnostics.json`.
- Concentration summary: `{"largest_absolute_contributor_share": 0.035238673914368156, "largest_absolute_realized_contributor": "VXX", "leave_one_dominant_symbol_validation_cagr": -0.07270753747690428, "max_sector_count_in_targets": 6, "target_max_position_weight": 0.09166666666666667, "unique_symbols": 618}`. The full symbol-level contribution map is in `concentration_diagnostics.json`.
- The persisted case curves include fills, target turnover, realized turnover, costs, holdings, cash, concentration, drawdowns, dividends, terminal events, and per-symbol P&L.

## Gate table

| Gate | Result |
|---|---|
| G1 | PASS |
| G2 | FAIL |
| G3 | FAIL |
| G4 | FAIL |
| G5 | FAIL |
| G6 | FAIL |
| G7 | FAIL |
| G8 | FAIL |
| G9 | PASS |

The gate table is conjunctive and near misses are failures. Economic acceptance requires every frozen gate; acceptance still means only `RESEARCH_ACCEPT_FOR_FORWARD_PAPER_EVALUATION`, never automatic promotion.

## Independent verification

The verifier independently checked frozen input identity, data hashes and row/quality counts, rebuilt both PIT universes, reconstructed the full persisted signal-audit population plus boundary groups, checked unique daily decisions, no same-session/future fills, target caps, SPY non-trading, every fill and terminal cost formula, the account cash/NAV ledger, all six cases and all non-warmup metric periods, and agreement with the runner gate table. Verification details, check count, and failure count are in `independent_verification.json`; the exact verdict is in `decision.json`.

## Final reasoning

The run is valid, but the frozen conjunctive economic gates failed: `G2, G3, G4, G5, G6, G7, G8`. No near miss was promoted to a pass and no replacement candidate or post-result rescue was attempted.

## Files created

The complete run record is the focused artifact set under `research/overnight_reversal_v1/`, including the pre-run receipt, implementation, tests, signal and observation audits, six compressed case files, metric/cost/coverage/concentration diagnostics, gate table, independent verification, decision, and this report.

## Economic research branch final SHA

Verification head: `cfb43847f19048bde57680244ba6fda45b89e795`. The final committed branch SHA is reported with the handoff after the artifact commit.

## Artifact index

`FROZEN_INPUT_RECEIPT.json`, `PRE_RUN_VALIDATION.json`, `universe_A.json.gz`, `universe_B.json.gz`, `observation_set_audit.jsonl.gz`, `signal_audit.csv.gz`, `signal_summary.json`, `case_*.json.gz`, `v3_control_results.json`, `candidate_results.json`, `decision_coverage.json`, `turnover_diagnostics.json`, `concentration_diagnostics.json`, `yearly_validation.json`, `gate_table.json`, `run_manifest.json`, `independent_verification.json`, and `decision.json` are the run record.
