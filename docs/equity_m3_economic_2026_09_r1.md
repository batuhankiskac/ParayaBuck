# M3 Economic Mission Execution R1

## Mission result

- Mission: M3_ECONOMIC_MISSION_EXECUTION_R1
- Compact phase: D30
- Compact verdict: M3_ECONOMIC_GATE_FAIL
- Raw economic verdict: M3_ECONOMIC_GATE_FAIL
- Blocked reasons: []
- Economic execution run: true
- Candidate economics run: true
- Economic metrics present: true
- Economic mission authorized: true
- Research only: true; automatic promotion: false; human promotion only: true.
- Next required mission: M3_FEED_TRANSFERABILITY_AND_PAPER_READINESS_GATE

The raw result is an ignored artifact under the exact predeclared output
directory. This tracked receipt contains the compact provenance binding
and economic summary; it does not rerun any strategy or data gate.

## Authorization and upstream closure

- A30 implementation: 6d39fb81dda1bacaad291a39fbee103d7ff0f303
- B30 authorization manifest: 6110150e2069fd9f1dd16c4ecc2f208c099dade6
- C30 result commit: ee97c4e2a24685372ed1901f9a831ecade6a1b3e
- Authorization ID: M3_ECONOMIC_EXECUTION_R1_2026_09_03; operator: human_operator.
- Predeclaration closure: {"closure_commit": "960f0eaa42e300b95d1de2e8d3cd34626fb6d2a7", "final_commit": "b487d25ae1abf4140f536462463a8e83b97090bb", "implementation_commit": "db1ce1c5abc316a75d5972fb9138ffd77da1ca6a", "manifest_commit": "7f0bd86cce6d9ccbadef5bbc1e49e6bb9c18d052", "receipt_path": "data/research_receipts/equity_m3_economic_predeclaration_2026_09_r1.json", "receipt_raw_sha256": "d631b387ad581b5386514e60db3950e838ec3910b9c2a00200c21c6484bbc6fb", "receipt_self_digest_sha256": "a1e05cee72de803adf78b4eb8c51abefcbf85651a445d4d96c9c3e805cbd226f", "remote_ci_head": "a005dcd5ae3afd1ed90985836a9b61fc43d19d4b", "remote_ci_run": "33711180326", "result_commit": "a005dcd5ae3afd1ed90985836a9b61fc43d19d4b", "starting_head": "06aaf78a7ea75aa3d97404916d5107194534fe6c"}
- R5/R2 upstream: {"candidate_ocp": "M3_CANDIDATE_OCP_ATTESTATION_R2_PASS", "candidate_ocp_receipt_digest": "8a576e650060c27e7f4a0c6f0fd8a532bc2509df26fd58c0587656ad47f766b6", "data_gate": "M3_DATA_GATE_PASS", "data_gate_result_commit": "3c2f67d31772f2fba118137ad262bd8ffdb737d8"}
- Raw result identity: {"path": "data/research/generated/equity_m3_economic_2026_09_r1/mission_result.json", "raw_mission_digest": "318d26bb6856d2b464be3ba2bcc46924f5c6f4bdbcc6c3857cbc9f05b4d57ca7", "raw_sha256": "6b44942f4788abca0818b526b554a538e093c58c6447ea5d76d824178c9535fc", "semantic_result_digest": "06e02b3590962b1e4fa1841cc703036a7370d121d242216b695c13abd45a61a4"}

## Remote CI claim

- {"conclusion": "success", "head_sha": "ee97c4e2a24685372ed1901f9a831ecade6a1b3e", "python_matrix": {"3.12": "PASS", "3.13": "PASS", "3.14": "PASS"}, "required_stages": {"build": "PASS", "dependency_audit": "PASS", "focused_acquisition_pit_tests": "PASS", "focused_economic_execution_tests": "PASS", "focused_economic_predeclaration_tests": "PASS", "focused_m3_data_gate_tests": "PASS", "focused_ocp_tests": "PASS", "focused_r2_attestation_tests": "PASS", "full_pytest": "PASS", "mypy": "PASS", "production_isolation": "PASS", "ruff_format": "PASS", "ruff_lint": "PASS", "secret_scan": "PASS", "wheel_smoke": "PASS"}, "run_id": "33752455337", "status": "PASS"}

## Baseline economic summary

| Window | Active total return | CAGR difference | Sharpe difference | Drawdown difference | Turnover ratio |
| --- | ---: | ---: | ---: | ---: | ---: |
| FULL | -0.115713644557 | -0.0138441176108 | -0.131808234844 | -0.0321989043421 | 0.964315697197 |
| RECENT | -0.0592496944841 | -0.0138368413524 | -0.134495521046 | -0.0283250257953 | 0.965997596018 |

- Acceptance: {"criteria": {"all_integrity_gates_pass": true, "baseline_full_active_total_return_positive": false, "baseline_full_cagr_gap": false, "baseline_full_drawdown_limit": true, "baseline_full_robustness_periods": false, "baseline_full_sharpe_non_degradation": false, "baseline_full_turnover_limit": true, "baseline_recent_active_total_return_positive": false, "baseline_recent_cagr_gap": false, "baseline_recent_drawdown_limit": true, "baseline_recent_sharpe_non_degradation": false, "baseline_recent_turnover_limit": true, "immediate_repeat_exact": true, "order_independent_exact": true, "stress_2x_full_active_total_return_positive": false, "stress_2x_recent_active_total_return_positive": false}, "thresholds": {"blocked_on": ["upstream receipt or input identity failure", "C|C or X|X identity mismatch", "observation, scanner, timestamp, execution, accounting, cost, reproducibility, or isolation failure", "missing required result, incomplete scenario, or incomplete period"], "fail_when": "integrity passes but one or more economic criteria fails", "pass_criteria": ["BASELINE_1X FULL M3 CAGR minus V3 CAGR >= +0.50 percentage points", "BASELINE_1X RECENT M3 CAGR minus V3 CAGR >= +1.00 percentage points", "BASELINE_1X active total return positive in FULL and RECENT", "FULL and RECENT Sharpe difference >= -0.05", "FULL maximum-drawdown difference <= +5.00 percentage points", "RECENT maximum-drawdown difference <= +3.00 percentage points", "FULL and RECENT turnover ratio <= 1.50", "STRESS_2X active total return positive in FULL and RECENT", "BASELINE_1X active total return positive in at least three of four robustness periods", "immediate repeat and C-then-X/X-then-C results reproduce exactly", "all integrity and no-lookahead invariants pass"], "stress_diagnostics": ["STRESS_3X", "STRESS_5X"], "thresholds_frozen_before_results": true, "verdicts": ["M3_ECONOMIC_GATE_PASS", "M3_ECONOMIC_GATE_FAIL", "M3_ECONOMIC_GATE_BLOCKED"]}, "verdict": "M3_ECONOMIC_GATE_FAIL"}
- Integrity: {"accepted_upstream": true, "accounting_and_cost_ledger": true, "complete_16_cell_matrix_per_pass": true, "complete_48_execution_matrix": true, "execution_timing_contract": true, "fresh_engine_per_cell": true, "immediate_repeat_exact": true, "input_hashes": true, "no_lookahead": true, "observation_identities": true, "observation_visibility_contract": true, "order_independent_exact": true, "reference_role_contract": true, "scanner_and_pit_identity": true, "strict_prior_timing": true, "terminal_and_corporate_action_contract": true}
- Bootstrap: {"5th percentile": -0.0008493640787158686, "95th percentile": 0.0004726443639658436, "block_length_weeks": 4, "blocks_per_resample": 124, "bootstrap median": -0.00017908437574623811, "generator": "numpy.random.Generator(numpy.random.PCG64(20260902))", "method": "overlapping non-circular moving-block bootstrap", "probability mean active return > 0": 0.3287, "resamples": 10000, "seed": 20260902, "statistic": "mean weekly X-minus-C return", "weekly_observation_count": 495}

## Frozen execution contract

- Data identities: {"canonical_equity_v5": {"crlf_normalized_sha256": "df42733b640e6dea17e1ed11115846933a86d2c525810dd3f473acc32e6d0db3", "manifest_git_blob": "a366944ec26d6f9edff82e9e892fe397f3aed112", "raw_sha256": "bb7aaae86bc1909c6b0a1094a59430f5ed9900bb3ec63891bbdcda8cda055139"}, "composite_semantic": {"sha256": "5bb66b7ff15d1f9ef3e9ce7caa4ef2314fcc670cf80b9696ee576e1b9cac4eda"}, "corporate_actions": {"raw_sha256": "a0a60755a3f6adfc06a5ef0e6ed82852e1584c20fdb62cefed92b765dc9adc23"}, "iwm_normalized_sidecar": {"raw_sha256": "8f8122e33d5ef2b194e22c7e53a15198920476690997535bbc5c05d522463dc2", "semantic_row_sha256": "cc1855c607ee394fae9e5ede594d1420f0c4d03cb8a5af08e14e6a656cd7d837"}, "iwm_raw_bundle": {"raw_sha256": "9590e35d2f5b80a4cd7f5c2c755acfcc088a6f7696d6eb86039d702ee6832258"}, "iwm_source_receipt": {"raw_sha256": "2dbeeebc688dab111013a114e9c08b44c116e23c48a42968099f6c8671ea4003"}, "lifetime": {"raw_sha256": "ed675d9b6ba7f173b65ae2b5272ffb62e09d6a007ed36ea22d3aba7a36774596"}, "r5_evaluation": {"candidate_decision_eligibility_digest_sha256": "ab5ee2d5c1bc58f711f1961dff505563adf67c02d67917c1d067431136514a32", "candidate_universe_digest_sha256": "2a0c5b987d40cefed46c4f2ded595e1759e13c8744e2bc003f2da4befac2106e", "matrix_merkle_root": "99187e5e0d94348b87e6c89aae3612d4bbd40e3795e6844289daf740d18073d9", "pit_raw_sha256": "657f2d00716ca33dd4142f7bb6166fcd72f2344fad20678456c731b292fd77ac", "projection_digest_sha256": "7e1a5ee098e23397d8f4d77e7d73e79c8491c28b089854f9174a8c0049a7b240"}}
- Observation identities: {"candidate_X": {"boundary": "X|X", "market": "equity", "ordered_observation_merkle_root": "cee257f5053570c2b5957adf5b6b4b8b2c0943273790b20135a24ed5ffbe568f", "semantic_observation_universe_hash": "eb9b3df623a2fdcb5b02c9c174fbf629a355f4faf018df9989de8703f8907f6f", "strategy": "equity_proxy_multifactor_residual_momentum_v1", "strategy_parameter_digest_sha256": "d25c225a0acc4a1e379da74e50b160d93a18de68ef9684205b84eceae93f98aa", "timeframe": "1d", "version": "1.0.0"}, "control_C": {"boundary": "C|C", "market": "equity", "ordered_observation_merkle_root": "d6ec0d61b100ab527ee2978ddcd6210c09f866b03cc62637525c9ac4f2d6509a", "semantic_observation_universe_hash": "e1f0e2ee7420a9d43a8fe69e2048e4a5a3e135f3d3ffc5d2bb8b425fdb487eb1", "strategy": "equity_residual_momentum_v3", "strategy_parameter_digest_sha256": "d25c225a0acc4a1e379da74e50b160d93a18de68ef9684205b84eceae93f98aa", "timeframe": "1d", "version": "3.0.0"}}
- Reference roles: {"effective_candidate_observation_capacity": 24, "full_pin_order": ["SPY", "IWM", "QQQ", "SH", "PSQ", "DOG"], "maximum_stream_symbols": 30, "observation_only_references": ["SPY", "IWM", "QQQ"], "reference_bars_available_to_m3": true, "references_outside_candidate_membership": true, "references_outside_executable_symbols": true, "references_outside_targets": true}
- Pass order and count: {"A": ["C", "X"], "B": ["C", "X"], "C": ["X", "C"]}; 48 executions.
- Timing: {"completed_bar_signal_timing": "The completed regular-session bar is ingested before the decision for that session timestamp.", "corporate_action_ordering": "corporate actions apply before fills in the session cycle", "decision_timestamp": "the first bar timestamp of the completed session cycle", "execution": "next tradable bar only", "final_valuation": "last included evaluation session after terminal handling and explicit end liquidation", "near_window_end": "an order created on 2026-07-30 has no eligible next execution bar, is not silently filled, and is excluded from economics", "order_creation_timestamp": "the decision timestamp", "pit_snapshot": "latest snapshot with effective_at <= exact decision timestamp", "post_window_data": "bars after 2026-07-30 cannot enter the reported window", "same_bar_fill": "forbidden; pending orders are filled before the next decision cycle", "terminal_map": "T1 terminal lifecycle is applied after the final decision and identically for C and X"}
- Execution addendum: {"active_drawdown": "drawdown of active wealth, with initial wealth anchor 1.0", "active_wealth": "product of (1+X_daily)/(1+C_daily) over paired dates", "bootstrap": {"block_length_weeks": 4, "generator": "numpy.random.Generator(numpy.random.PCG64(20260902))", "method": "moving-block", "percentile": "numpy.percentile linear method", "resamples": 10000}, "duplicate_terminal_timestamps": "retain the last post-liquidation value", "initial_cash_anchor": "anchor initial cash immediately before the first metric session", "ratio_zero_denominator": {"negative_denominator": "BLOCKED", "non_finite": "BLOCKED", "positive_over_zero": "positive infinity; corresponding acceptance criterion fails", "zero_over_zero": 1.0}, "robustness_and_year_slices": "slice the BASELINE FULL paired curves without independent resets"}

SPY, IWM, and QQQ are observation-only references. SH, PSQ, and DOG
remain eligible tradable reserved inverse-hedge pins when the frozen
PIT scanner and strategy gates select them. C is C|C and X is X|X;
X|C is not an economic result. The historical feed is SIP and the
configured PAPER/LIVE stream is IEX, so feed_match=false and this result
does not establish PAPER/LIVE or promotion readiness.

## Scope confirmations

No market-data network access, broker path, credential access, IWM
refetch, OCP replay, M3 data-gate replay, optimization, tournament,
promotion, PAPER/LIVE change, strategy/risk/config change, production
behavior change, or history rewrite occurred during execution.

Receipt self-digest: 0e950e4e754f19d4c285d620d517fbd30fb78aebc33c862d71ada587cc306a22.

## E30 final remote closure

- C30 full SHA: ee97c4e2a24685372ed1901f9a831ecade6a1b3e
- C30 CI run: 33752455337
- C30 Python 3.12: PASS
- C30 Python 3.13: PASS
- C30 Python 3.14: PASS
- D30 full SHA: d8866e0bd5a13ce714bf9cce39ba7cf708557b7d
- D30 CI run: 33753192580
- D30 Python 3.12: PASS
- D30 Python 3.13: PASS
- D30 Python 3.14: PASS
- Final candidate-OCP economic execution remote closure: complete.
- The receipt is unchanged in E30.
