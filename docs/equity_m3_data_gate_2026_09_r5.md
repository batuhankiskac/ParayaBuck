# Equity M3 data gate R5

## 1. Executive verdict

`M3_DATA_GATE_PASS`

## 2. Boundary

- Corrective infrastructure: `PASS`
- Candidate OCP: `NOT_RUN_OUT_OF_SCOPE`
- Economic readiness: `False`
- Next required gate: `M3_CANDIDATE_OCP_GATE`

## 3. A10/B10/C10/D10 commits

`{"d6_base": "b0cd5546f93f63d162de1ded3871b4431b08b89e", "immutable_provenance": {"a10_abandoned_implementation_commit": "193182fa9ebf297fe623395476a7cdcd9c14c12f", "a11_abandoned_implementation_commit": "787e78bcf6070278c071a6ca9f18f6ee4dc0db6b", "a12_canonical_fix_commit": "d52b578c21176dd72ff4d01b51b39c6f4d15267f", "a13_abandoned_implementation_commit": "136cb93b35e46ba356a88727aaf0c12574f4ba74", "a13_implementation_commit": "136cb93b35e46ba356a88727aaf0c12574f4ba74", "a14_implementation_commit": "773966f3cfdb33bf584e98343dc4839c59deb224", "a14_implementation_tree": "8e01de4e9013f3b7f0e9fae388a28609eee12586", "a15_implementation_commit": "067c44b24906c86421143d65aa9f2558ff23cf2c", "a15_implementation_tree": "f37094973921eb3290896910a9c117890b5d4be9", "a16_implementation_commit": "79ffcb178845f64f3f2cc036b9e1919cc15891e7", "a16_implementation_tree": "2c5845bf52efee0734cbc61da95b17ed6fa2aed6", "a17_implementation_commit": "2f49c2bc6e6f8cec295ed0f0abd13f934a7059a6", "a17_implementation_tree": "1fae3b62eb14cd089c940d927ded86dd3484cd8b", "a18_implementation_commit": "e22421c2486a20e883286345dbb509bbcd5d2fc9", "a18_implementation_tree": "bd310b37ac3ed946be4a3c8aa94d8daf37c48909", "d6_base_commit": "b0cd5546f93f63d162de1ded3871b4431b08b89e", "r4_c6_commit": "4f1ddd970207406f4a70d278651796bccd5b8f7a"}, "implementation": "e22421c2486a20e883286345dbb509bbcd5d2fc9", "implementation_parent": "e22421c2486a20e883286345dbb509bbcd5d2fc9", "manifest": "2f6ce2aaf347b9e0257d4ae31e9fdcc90cdcafe4", "recovery_lineage": {"abandoned_implementation_commit": "2f49c2bc6e6f8cec295ed0f0abd13f934a7059a6", "abandoned_manifest_commit": "a6ceda31f95f3b50f8a5bfcf1f0e04efa36d90d4", "automatic_refetch_allowed": false, "corrective_base_commit": "459cf782754bc15d137fcaa4cdd081402b837f96", "reason": "C10 R5 result lacked explicit clean-run and complete synthetic-fixture regression coverage before test hardening", "reason_category": "pre_result_test_coverage_gap", "replay_exact_captured_bytes": true, "status": "SUPERSEDED_A17_B17"}}`

## 4. B10 manifest-only proof

`{"changed_paths": ["data/research_manifests/equity_m3_data_gate_2026_09_r5.json"], "exactly_manifest_only": true, "manifest_commit": "2f6ce2aaf347b9e0257d4ae31e9fdcc90cdcafe4", "manifest_git_blob_id": "b11aa6302e9edac11389d211f772a5b1d13dcc5d", "manifest_path": "data/research_manifests/equity_m3_data_gate_2026_09_r5.json", "manifest_raw_sha256": "83a2b7b796939423eff9c3c0a2d16000594cbbbed26e9de2df61206c5ecdb51e", "parent_commit": "e22421c2486a20e883286345dbb509bbcd5d2fc9"}`

## 5. Exact files changed

R5 implementation, tests, the pre-result manifest, and compact result artifacts are isolated to the declared append-only chain.

## 6. R4 immutable input verification

`{"composite": {"adjustment_mode": "split", "calendar_identity": {"calendar": "NYSE", "name": "pandas_market_calendars", "version": "5.4.0"}, "canonical_manifest_git_blob": "a366944ec26d6f9edff82e9e892fe397f3aed112", "canonical_parent_normalized_sha256": "df42733b640e6dea17e1ed11115846933a86d2c525810dd3f473acc32e6d0db3", "canonical_parent_raw_sha256": "bb7aaae86bc1909c6b0a1094a59430f5ed9900bb3ec63891bbdcda8cda055139", "canonical_timeframe": "1d", "encoding": "m3-composite-dataset-v1", "evaluation_range": {"count": 2658, "first": "2016-01-04", "last": "2026-07-30"}, "exchange_timezone": "America/New_York", "iwm_normalized_raw_sha256": "8f8122e33d5ef2b194e22c7e53a15198920476690997535bbc5c05d522463dc2", "iwm_normalized_semantic_row_sha256": "cc1855c607ee394fae9e5ede594d1420f0c4d03cb8a5af08e14e6a656cd7d837", "iwm_raw_bundle_sha256": "9590e35d2f5b80a4cd7f5c2c755acfcc088a6f7696d6eb86039d702ee6832258", "normalizer_git_blob": "6184018564204020f05123371b2ee2ed8e91c599", "normalizer_sha256": "aaa521c3cbee4fe9bfd28adbdb2eff2b890bbf3e0806e81530f5427ea383f183", "schema_version": "m3-iwm-normalizer-v1", "semantic_digest_sha256": "5bb66b7ff15d1f9ef3e9ce7caa4ef2314fcc670cf80b9696ee576e1b9cac4eda", "source_feed": "sip", "source_range": {"end_boundary": "2026-07-31T00:00:00Z", "semantics": "[start,end)", "start": "2016-01-01T00:00:00Z"}}, "normalized_sidecar": {"normalized_row_count": 2658, "normalized_semantic_row_sha256": "cc1855c607ee394fae9e5ede594d1420f0c4d03cb8a5af08e14e6a656cd7d837", "path": "data/research/generated/m3_data_gate_r4/iwm_normalized.csv", "raw_sha256": "8f8122e33d5ef2b194e22c7e53a15198920476690997535bbc5c05d522463dc2"}, "r4_manifest": {"commit": "2f6ce2aaf347b9e0257d4ae31e9fdcc90cdcafe4", "git_blob_id": "0cc789dba475242014856b60d4913b9b3e25cc4b", "git_blob_normalized_sha256": "7cf31eef48a7a9352d689475ebc392dab336b8fc84fc62f7ac6374950fe0e55e", "git_blob_raw_sha256": "7cf31eef48a7a9352d689475ebc392dab336b8fc84fc62f7ac6374950fe0e55e", "path": "data/research_manifests/equity_m3_data_gate_2026_09_r4.json"}, "raw_bundle": {"aggregate_page_hash_digest": "39a87290b4ebafbba3c29663f5577c05c7548252e6699e5f39589849211c93eb", "page_chain_digest_sha256": "a5b5e400bb97aff3f6342bbe0b969f5d0ae5600406d5cd186ec62e0360480e1b", "path": "data/research/external/m3_iwm_raw_2026_09_r4", "raw_bundle_sha256": "9590e35d2f5b80a4cd7f5c2c755acfcc088a6f7696d6eb86039d702ee6832258"}, "raw_pit": {"byte_size": 24774560, "path": "data/research/generated/m3_data_gate_r4/pit_universe.json", "raw_sha256": "bfdf6e3f87d5c42a1923bc4009f2fa1892ade7317e11caf139d30239bac3f5ae"}, "source_receipt": {"path": "data/research/external/m3_iwm_raw_2026_09_r4/source_receipt.json", "raw_sha256": "2dbeeebc688dab111013a114e9c08b44c116e23c48a42968099f6c8671ea4003"}}`

## 7. Network statement

No network calls or credential reads occurred; only exact accepted R4 bytes were replayed.

## 8. PIT timing proof table

| session | daily timestamp | scanner input | PIT effective | membership boundary | M3 matrix session | decision timestamp | consumed |
|---|---|---|---|---|---|---|---|
| 2016-01-04 | 2016-01-04T05:00:00.000000Z | 2016-01-03T05:00:00.000000Z | 2016-01-04T05:00:00.000000Z | 2016-01-04T05:00:00.000000Z | 2016-01-04 | 2016-01-04T05:00:00.000000Z | True |
| 2026-07-29 | 2026-07-29T04:00:00.000000Z | 2026-07-28T04:00:00.000000Z | 2026-07-29T04:00:00.000000Z | 2026-07-29T04:00:00.000000Z | 2026-07-29 | 2026-07-29T04:00:00.000000Z | True |
| 2026-07-30 | 2026-07-30T04:00:00.000000Z | 2026-07-29T04:00:00.000000Z | 2026-07-30T04:00:00.000000Z | 2026-07-30T04:00:00.000000Z | 2026-07-30 | 2026-07-30T04:00:00.000000Z | True |
| 2026-07-31 | 2026-07-31T04:00:00.000000Z | 2026-07-30T04:00:00.000000Z | 2026-07-31T04:00:00.000000Z | 2026-07-31T04:00:00.000000Z | NOT_IN_EVALUATION | NOT_RUN_OUT_OF_SCOPE | False |

## 9. Terminal snapshot decision

`{"evaluation_snapshot_count": 2658, "excluded_post_evaluation_snapshot_count": 1, "excluded_post_evaluation_snapshot_digest_sha256": "567ca7e950ed62efe5bbe82abf4eea6643e0dda6712fd24f968c76acd766d879", "interpretation": "evaluation projection excludes source snapshots effective after the exact 2026-07-30 decision cutoff", "projection_digest_sha256": "7e1a5ee098e23397d8f4d77e7d73e79c8491c28b089854f9174a8c0049a7b240", "source_snapshot_count": 2659, "terminal_observation_boundary_required": false}`

## 10. Clean-run proof

`{"after": [], "before": [], "clean_after_external_outputs": true, "clean_worktree_at_start": true}`

## 11. PIT population

`{"candidate_decision_eligibility_digest_sha256": "ab5ee2d5c1bc58f711f1961dff505563adf67c02d67917c1d067431136514a32", "candidate_universe_digest_sha256": "2a0c5b987d40cefed46c4f2ded595e1759e13c8744e2bc003f2da4befac2106e", "explicit_empty_snapshot_count": 1, "full_timestamp_snapshot_selection": {"comparison": "effective_at <= cutoff_timestamp", "evaluation_decision_cutoff_timestamp": "2026-07-30T04:00:00.000000Z", "status": "PASS"}, "maximum_eligible_count": 299, "median_eligible_count": 289.0, "minimum_eligible_count": 0, "reference_boundary": {"candidate_reference_overlap": [], "evidence_level": "PIT_UNIVERSE_VERIFIED", "executable_reference_overlap": [], "reference_symbols": ["IWM", "QQQ", "SPY"], "status": "PIT_UNIVERSE_VERIFIED", "target_reference_overlap": []}, "reference_exclusion": {"candidate_reference_overlap": [], "evidence_level": "PIT_UNIVERSE_VERIFIED", "executable_reference_overlap": [], "reference_symbols": ["IWM", "QQQ", "SPY"], "status": "PIT_UNIVERSE_VERIFIED", "target_reference_overlap": []}, "scanner_identity": {"scanner_config_digest": "d8e06f54cd1a2fe0a201317fb6e3f4265470b2a10fe6e119ad81554128b33392", "scanner_version": "equity-scanner-v3"}, "snapshot_count": 2658, "strict_prior_result": {"effective_at_is_strictly_after_latest_input": true, "status": "PASS", "violating_snapshots": []}, "unique_candidate_count": 889}`

## 12. M3 history eligibility

`{"first_full_candidate_feasible_decision": "2017-02-06", "m3_not_warm_pair_count": 18253, "minimum_recent_eligible_depth": 271, "scanner_candidate_pair_count": 157970, "total_eligible_matrix_count": 139717}`

## 13. Numerical feasibility

`{"candidate_symbol_count": 889, "candidate_symbol_digest_sha256": "bd9d01e213c092c1a011913d0b8f97a9d5c3714fba5ea9261fe2b72d5e188fe4", "failure_category_counts": {}, "first_full_candidate_feasible_decision": "2017-02-06", "full_candidate_depth_pass": true, "full_decision_count": 552, "full_decision_sessions_digest_sha256": "7b0ae1cca6bdeb8c2dcf6b21b9d415ff8209691d95f0f2babf389bfa62ea1e50", "full_decisions_are_subset": true, "full_initial_candidate_warmup_decision_count": 57, "full_post_feasibility_failure_count": 0, "m3_not_warm_pair_count": 18253, "matrix_digest_merkle_root": "99187e5e0d94348b87e6c89aae3612d4bbd40e3795e6844289daf740d18073d9", "matrix_examples": [], "minimum_recent_eligible_depth": 271, "no_economic_scores": true, "numerical_fail_count": 0, "numerical_pass_count": 139717, "recent_candidate_depth_failure_count": 0, "recent_candidate_depth_pass": true, "recent_decision_count": 239, "recent_decision_sessions_digest_sha256": "45bd347fee11bbf728701d2f4ccaeca23807a226b378b42deadd3696bd3bd38d", "scanner_candidate_pair_count": 157970, "total_eligible_matrix_count": 139717}`

## 14. Mandatory gate table

| gate | status |
|---|---|
| commit_topology | PASS |
| implementation_blob_identity | PASS |
| manifest_only_commit | PASS |
| clean_worktree_at_start | PASS |
| configuration_identity | PASS |
| dependency_identity | PASS |
| r4_acquisition_identity | PASS |
| r4_raw_bundle_replay_identity | PASS |
| canonical_artifact_identity | PASS |
| lifetime_identity | PASS |
| corporate_action_identity | PASS |
| iwm_raw_bundle | PASS |
| iwm_provider_contract | PASS |
| iwm_pagination_chain | PASS |
| iwm_normalization | PASS |
| request_boundary_conformance | PASS |
| required_reference_session_completeness | PASS |
| reference_calendar | PASS |
| reference_row_quality | PASS |
| reference_common_sessions | PASS |
| full_weekly_post_warmup_coverage | PASS |
| recent_weekly_coverage | PASS |
| composite_dataset_identity | PASS |
| pit_source_artifact_identity | PASS |
| pit_evaluation_projection_identity | PASS |
| pit_manifest_identity | PASS |
| pit_reference_boundary | PASS |
| pit_snapshot_cadence | PASS |
| pit_terminal_boundary | PASS |
| strict_prior_population | PASS |
| full_timestamp_snapshot_selection | PASS |
| deterministic_numerical_population | PASS |
| m3_history_eligibility_parity | PASS |
| full_candidate_depth_feasibility | PASS |
| recent_candidate_depth_feasibility | PASS |
| design_matrix_feasibility | PASS |
| reference_role_contract | PASS |
| receipt_forbidden_fields | PASS |

## 15. Commands actually executed

`["scripts/research_equity_m3_data_gate_r5.py", "--contract", "data/research_manifests/equity_m3_data_gate_2026_09_r5.json", "--canonical-bars", "data/historical/equity_pit_symmetric_2016_2026_v5.csv", "--canonical-manifest", "data/historical/equity_pit_symmetric_2016_2026_v5.manifest.json", "--lifetimes", "data/historical/equity_entity_lifetimes_full_v5.csv", "--corporate-actions", "data/historical/equity_survivorship_full_corporate_actions_v5.csv", "--corporate-actions-manifest", "data/historical/equity_survivorship_full_corporate_actions_v5.manifest.json", "--iwm-bundle", "data/research/external/m3_iwm_raw_2026_09_r4", "--iwm-sidecar", "data/research/generated/m3_data_gate_r4/iwm_normalized.csv", "--composite-manifest", "data/research_manifests/equity_m3_composite_dataset_2026_09_r4.json", "--r4-pit-universe", "data/research/generated/m3_data_gate_r4/pit_universe.json", "--r5-pit-manifest-source", "data/research_manifests/equity_m3_pit_population_2026_09_r4.json", "--projection-out", "/tmp/m3-r5-b18-pit.json", "--pit-manifest-out", "/tmp/m3-r5-b18-pit.manifest.json", "--receipt-out", "/tmp/m3-r5-b18-receipt.json", "--report-out", "/tmp/m3-r5-b18-report.md"]`

## 16. Local and remote CI

`{}`

## 17. Tracked versus ignored artifacts

Compact C10 artifacts are tracked; raw R4 pages, the R4 sidecar, the R4 full PIT artifact, and the R5 full projection remain ignored external/derived inputs.

## 18. Remaining blockers

`[]`

## 19. Explicit confirmations

No refetch, fabricated/substituted/filled IWM, credential access, economic P&L, alpha ranking, optimization, candidate OCP, promotion, PAPER/LIVE change, V3 re-freeze, or history rewrite occurred.

M3_DATA_GATE_PASS
