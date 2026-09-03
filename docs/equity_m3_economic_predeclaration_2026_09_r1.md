# M3 Economic Mission Predeclaration R1

## Predeclaration status

- Mission: M3_ECONOMIC_MISSION_PREDECLARATION_R1
- Verdict: M3_ECONOMIC_PREDECLARATION_BLOCKED
- Blocked reasons: ["remote_ci:NOT_RUN_BLOCKED"]
- Economic contract frozen: true
- Economic execution run: false
- Economic results present: false
- Real execution authorized: false
- Next required mission: M3_ECONOMIC_MISSION_EXECUTION

This is a before-results contract. It does not execute the real M3 or
V3 economic backtest and contains no economic result.

## Accepted upstream gates

- R5 data gate: M3_DATA_GATE_PASS, result 3c2f67d31772f2fba118137ad262bd8ffdb737d8, receipt digest 41afb58aca2fb38f4f0d301fc169938809b1cb9afcfa5c9855d97e292916abeb, 38/38 gates PASS.
- Candidate OCP R2: M3_CANDIDATE_OCP_ATTESTATION_R2_PASS, receipt digest 8a576e650060c27e7f4a0c6f0fd8a532bc2509df26fd58c0587656ad47f766b6, corrective CI 33678608824.
- The superseded unversioned OCP receipt is rejected and is not an economic prerequisite.

## Exact data identities

{
  "canonical_equity_v5": {
    "crlf_normalized_sha256": "df42733b640e6dea17e1ed11115846933a86d2c525810dd3f473acc32e6d0db3",
    "manifest_git_blob": "a366944ec26d6f9edff82e9e892fe397f3aed112",
    "raw_sha256": "bb7aaae86bc1909c6b0a1094a59430f5ed9900bb3ec63891bbdcda8cda055139"
  },
  "composite_semantic": {
    "sha256": "5bb66b7ff15d1f9ef3e9ce7caa4ef2314fcc670cf80b9696ee576e1b9cac4eda"
  },
  "corporate_actions": {
    "raw_sha256": "a0a60755a3f6adfc06a5ef0e6ed82852e1584c20fdb62cefed92b765dc9adc23"
  },
  "iwm_normalized_sidecar": {
    "raw_sha256": "8f8122e33d5ef2b194e22c7e53a15198920476690997535bbc5c05d522463dc2",
    "semantic_row_sha256": "cc1855c607ee394fae9e5ede594d1420f0c4d03cb8a5af08e14e6a656cd7d837"
  },
  "iwm_raw_bundle": {
    "raw_sha256": "9590e35d2f5b80a4cd7f5c2c755acfcc088a6f7696d6eb86039d702ee6832258"
  },
  "iwm_source_receipt": {
    "raw_sha256": "2dbeeebc688dab111013a114e9c08b44c116e23c48a42968099f6c8671ea4003"
  },
  "lifetime": {
    "raw_sha256": "ed675d9b6ba7f173b65ae2b5272ffb62e09d6a007ed36ea22d3aba7a36774596"
  },
  "r5_evaluation": {
    "candidate_decision_eligibility_digest_sha256": "ab5ee2d5c1bc58f711f1961dff505563adf67c02d67917c1d067431136514a32",
    "candidate_universe_digest_sha256": "2a0c5b987d40cefed46c4f2ded595e1759e13c8744e2bc003f2da4befac2106e",
    "matrix_merkle_root": "99187e5e0d94348b87e6c89aae3612d4bbd40e3795e6844289daf740d18073d9",
    "pit_raw_sha256": "657f2d00716ca33dd4142f7bb6166fcd72f2344fad20678456c731b292fd77ac",
    "projection_digest_sha256": "7e1a5ee098e23397d8f4d77e7d73e79c8491c28b089854f9174a8c0049a7b240"
  }
}

## Exact observation identities

{
  "candidate_X": {
    "boundary": "X|X",
    "market": "equity",
    "ordered_observation_merkle_root": "cee257f5053570c2b5957adf5b6b4b8b2c0943273790b20135a24ed5ffbe568f",
    "semantic_observation_universe_hash": "eb9b3df623a2fdcb5b02c9c174fbf629a355f4faf018df9989de8703f8907f6f",
    "strategy": "equity_proxy_multifactor_residual_momentum_v1",
    "strategy_parameter_digest_sha256": "d25c225a0acc4a1e379da74e50b160d93a18de68ef9684205b84eceae93f98aa",
    "timeframe": "1d",
    "version": "1.0.0"
  },
  "control_C": {
    "boundary": "C|C",
    "market": "equity",
    "ordered_observation_merkle_root": "d6ec0d61b100ab527ee2978ddcd6210c09f866b03cc62637525c9ac4f2d6509a",
    "semantic_observation_universe_hash": "e1f0e2ee7420a9d43a8fe69e2048e4a5a3e135f3d3ffc5d2bb8b425fdb487eb1",
    "strategy": "equity_residual_momentum_v3",
    "strategy_parameter_digest_sha256": "d25c225a0acc4a1e379da74e50b160d93a18de68ef9684205b84eceae93f98aa",
    "timeframe": "1d",
    "version": "3.0.0"
  }
}

C uses C|C with a fresh V3 execution instance. X uses X|X with a fresh
M3 execution instance. X|C is diagnostic-only and cannot enter an
economic comparison. Both runs share one clean commit, byte-identical
inputs, sessions, PIT scanner pool, timestamps, lifetime data,
corporate actions, terminal map, benchmark, capital, risk, allocation,
planner, costs, fees, and terminal policy.

## Frozen experiment contract

- Reference/pin contract: {"effective_candidate_observation_capacity": 24, "full_pin_order": ["SPY", "IWM", "QQQ", "SH", "PSQ", "DOG"], "maximum_stream_symbols": 30, "observation_only_references": ["SPY", "IWM", "QQQ"], "reference_bars_available_to_m3": true, "references_outside_candidate_membership": true, "references_outside_executable_symbols": true, "references_outside_targets": true}
- Evaluation windows: {"FULL": {"inclusive_end": "2026-07-30", "metric_start": "2017-02-06", "warmup_source_start": "2016-01-04"}, "RECENT": {"inclusive_end": "2026-07-30", "metric_start": "2022-01-03", "separate_backtest_from_full": true, "warmup_before_metric_start_allowed": true}}
- Robustness periods: [{"end": "2019-12-31", "start": "2017-02-06"}, {"end": "2021-12-31", "start": "2020-01-02"}, {"end": "2023-12-29", "start": "2022-01-03"}, {"end": "2026-07-30", "start": "2024-01-02"}]
- Timing contract: {"completed_bar_signal_timing": "The completed regular-session bar is ingested before the decision for that session timestamp.", "corporate_action_ordering": "corporate actions apply before fills in the session cycle", "decision_timestamp": "the first bar timestamp of the completed session cycle", "execution": "next tradable bar only", "final_valuation": "last included evaluation session after terminal handling and explicit end liquidation", "near_window_end": "an order created on 2026-07-30 has no eligible next execution bar, is not silently filled, and is excluded from economics", "order_creation_timestamp": "the decision timestamp", "pit_snapshot": "latest snapshot with effective_at <= exact decision timestamp", "post_window_data": "bars after 2026-07-30 cannot enter the reported window", "same_bar_fill": "forbidden; pending orders are filled before the next decision cycle", "terminal_map": "T1 terminal lifecycle is applied after the final decision and identically for C and X"}
- Baseline configuration: {"backtest": {"commission": 0, "equity_cat_per_share_fallback": 3e-06, "equity_sec_fee_rate_fallback": 2.06e-05, "equity_taf_cap_fallback": 9.79, "equity_taf_per_share_fallback": 0.000195, "equity_use_effective_dated_fees": true, "initial_cash": 100000, "slippage_bps": 2, "spread_bps": 2}, "equity_sleeve": {"maximum_allocation": 0.55}, "execution": {"minimum_order_notional": 10, "rebalance_band_pct": 0.005}, "research_execution": {"annualization_sessions": 252, "benchmark_symbol": "SPY", "enforce_drawdown_halt": false, "risk_free_rate": 0, "terminal_policy": "T1"}, "risk": {"cash_buffer_pct": 0.4, "daily_loss_limit_pct": 0.005, "max_drawdown_pct": 0.05, "max_gross_exposure_pct": 0.6, "max_position_pct": 0.1, "max_positions": 6}}
- Cost scenarios: {"BASELINE_1X": {"hard_acceptance": true, "multiplier": 1, "slippage_bps": 2, "spread_bps": 2}, "STRESS_2X": {"hard_acceptance": true, "multiplier": 2, "slippage_bps": 4, "spread_bps": 4}, "STRESS_3X": {"hard_acceptance": false, "multiplier": 3, "slippage_bps": 6, "spread_bps": 6}, "STRESS_5X": {"hard_acceptance": false, "multiplier": 5, "slippage_bps": 10, "spread_bps": 10}}
- Bootstrap: {"block_length_weeks": 4, "diagnostic_only": true, "method": "moving-block bootstrap on weekly active returns", "reported": ["bootstrap median", "5th percentile", "95th percentile", "probability mean active return > 0"], "resamples": 10000, "seed": 20260902, "statistic": "mean weekly X-minus-C return"}

The timing freeze is completed-bar signal ingestion, decision at the
session timestamp, PIT snapshot selection at effective_at <= decision
timestamp, order creation at that decision timestamp, no same-bar fill,
next-tradable-bar execution, corporate actions before fills, T1 terminal
handling after the final decision, and final valuation at the last
included session. An order created on 2026-07-30 has no valid next bar and
cannot be silently filled; no post-window bar enters economics.

Required strategy metrics, candidate-minus-control active metrics,
exposure diagnostics, calendar-year results, all four robustness periods,
and the diagnostic bootstrap are listed in the manifest. Negative or bad
future results must not be omitted. The frozen PASS/FAIL/BLOCKED criteria
are evaluated only by the later economic mission.

## Runner and authorization boundary

- Dedicated runner: scripts/research_equity_m3_economic.py
- No-argument invocation is non-executing and unauthorized.
- Parameter, date, cost, arbitrary-strategy, M4, broker, and PAPER/LIVE paths are rejected.
- Real execution additionally requires an explicit flag, a separate authorization artifact,
  a committed manifest, clean topology, exact identities, and a new empty output directory.
- The authorization artifact was not created in this mission.

Historical feed is sip; configured PAPER/LIVE stock streaming is
iex; feed_match=false. A future economic PASS can establish only
historical research PASS, never PAPER/LIVE or promotion readiness.

## Mandatory predeclaration gates

| Gate | Status |
| --- | --- |
| C_C_boundary_contract | PASS |
| X_C_economic_use_forbidden | PASS |
| X_X_boundary_contract | PASS |
| acceptance_criteria_frozen | PASS |
| baseline_cost_contract | PASS |
| bootstrap_contract | PASS |
| candidate_strategy_identity | PASS |
| capacity_contract | PASS |
| clean_worktree | PASS |
| control_strategy_identity | PASS |
| corporate_action_contract | PASS |
| data_identity_contract | PASS |
| default_no_execution | PASS |
| evaluation_window_contract | PASS |
| execution_authorization_required | PASS |
| execution_timing_contract | PASS |
| feed_transferability_boundary | PASS |
| metric_contract | PASS |
| no_optimization_contract | PASS |
| no_real_economic_execution | PASS |
| observation_identity_contract | PASS |
| old_unversioned_ocp_rejected | PASS |
| production_isolation | PASS |
| receipt_forbidden_real_result_fields | PASS |
| receipt_self_digest | PASS |
| reference_role_contract | PASS |
| same_run_control_contract | PASS |
| starting_head | PASS |
| stress_cost_contract | PASS |
| synthetic_blocked_fixture | PASS |
| synthetic_fail_fixture | PASS |
| synthetic_pass_fixture | PASS |
| terminal_policy_contract | PASS |
| upstream_ocp_r2_ci_binding | PASS |
| upstream_ocp_r2_receipt_identity | PASS |
| upstream_ocp_r2_verdict | PASS |
| upstream_r5_receipt_identity | PASS |
| upstream_r5_verdict | PASS |
| warmup_metric_separation | PASS |

Synthetic contract evidence is unit/integration-only: synthetic PASS
returns M3_ECONOMIC_GATE_PASS, threshold failure returns
M3_ECONOMIC_GATE_FAIL, and integrity failure returns
M3_ECONOMIC_GATE_BLOCKED. No real economic result was generated.

## Append-only publication chain

- Required starting head: 06aaf78a7ea75aa3d97404916d5107194534fe6c.
- A25 implementation commit: db1ce1c5abc316a75d5972fb9138ffd77da1ca6a.
- B25 manifest commit: 7f0bd86cce6d9ccbadef5bbc1e49e6bb9c18d052; manifest-only.
- C25 local result commit: PENDING_C25_SELF_COMMIT.
- Remote CI claim: {"head_sha": null, "run_id": null, "status": "NOT_RUN_BLOCKED"}.
- Receipt self-digest: 3a46e37d8eb76fcb6fb56a6f7ab32c584d8aef9b3a7b83966141349c34623e29.

Manifest-only proof: B25 changes exactly data/research_manifests/equity_m3_economic_2026_09_r1.json.
C25 changes exactly data/research_receipts/equity_m3_economic_predeclaration_2026_09_r1.json and docs/equity_m3_economic_predeclaration_2026_09_r1.md.
D25 changes only the receipt and report after C25 CI is fully green.
E25 changes only this report after D25 CI is fully green.

## Scope confirmations

No real economic backtest, real P&L, optimization, M4, promotion,
PAPER/LIVE change, production behavior change, market-data access,
credential access, IWM refetch, or history rewrite occurred.
Economic execution remains unauthorized.
