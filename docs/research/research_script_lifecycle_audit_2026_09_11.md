# Research script lifecycle audit — 2026-09-11

- Source HEAD: `c3313407eb227ccd4ef2a6974454997727fdaab1` (`Remove obsolete V3 integrity tests`)
- Commit created: **none**
- Scope: every tracked Python script under `scripts/research/`, `scripts/archive/`, root `scripts/research_*.py`, and the clearly research-only root `scripts/*.py` surface.
- Machine-readable inventory: `/private/tmp/parayabuck_script_lifecycle_inventory_2026_09_11_final.json` (untracked internal audit artifact).

## Summary

The audit classified 168 scripts. It removes 28 redundant closed/intermediate scripts, retains 140 scripts, and moves none. The reduction is 446,638 bytes and 8,425 source lines. No `src/parayabuck/**`, tests, configuration, manifests, receipts, reports, datasets, results, attestations, PAPER, LIVE, or economics code was changed.

The active structure remains the accepted 1,460-test surface: 1,028 product/non-research tests and 432 current-research tests. R46, execution-cost P0/P1, equity profit-protection R2, terminal/current V3 machinery, M3 pins, and the all-34 closure remain protected.

## Classification totals

| Code | Classification | Count | Action summary |
|---|---|---:|---|
| `A` | `KEEP_ACTIVE_CURRENT` | 21 | active/current retained |
| `B` | `KEEP_CANONICAL_VERIFIER` | 33 | canonical verifier/final audit retained |
| `C` | `KEEP_PINNED_HISTORICAL` | 43 | pinned or documented historical retained |
| `D` | `KEEP_REUSABLE_INFRASTRUCTURE` | 9 | reusable infrastructure retained |
| `E` | `ARCHIVE_CLOSED` | 24 | existing August archive retained |
| `F` | `DELETE_REDUNDANT` | 28 | redundant script deleted |
| `G` | `KEEP_OPERATIONAL` | 10 | operational utility retained |

## Deleted scripts

These are the 28 `F` rows. Each had no retained current/historical test importer after the dynamic-import sweep, no exact manifest/receipt path or identity pin, and no dependency from the canonical all-34 closure. The one exception found during post-edit validation was restored before this final inventory.

- `scripts/research/acquire_crossasset_x02_cme_retry_2026_09_10.py` — 12,746 bytes, 213 lines — Closed X-route probe/diagnostic runner has no retained test, pin, importer, or canonical closure dependency; closure components remain.
- `scripts/research/acquire_crypto_max_public_routes_2026_09_09.py` — 23,507 bytes, 445 lines — Closed exploratory K-route/diagnostic runner has no retained test, pin, importer, or canonical closure dependency; evidence is retained.
- `scripts/research/build_equity_h01_h05_route_closure_2026_09_09.py` — 6,135 bytes, 104 lines — One-shot intermediate equity acquisition/receipt builder has no retained test, pin, importer, or canonical closure dependency.
- `scripts/research/build_equity_pit_universe_manifest_2026_09_07.py` — 4,946 bytes, 108 lines — One-shot intermediate equity acquisition/receipt builder has no retained test, pin, importer, or canonical closure dependency.
- `scripts/research/build_strategy_queue_completion_report_2026_09_09.py` — 29,407 bytes, 473 lines — Intermediate queue aggregation/report runner has no retained test, pin, or canonical closure dependency; frozen closure remains.
- `scripts/research/build_strategy_queue_max_testing_aggregate_2026_09_09.py` — 33,821 bytes, 447 lines — Intermediate queue aggregation/report runner has no retained test, pin, or canonical closure dependency; frozen closure remains.
- `scripts/research/build_strategy_queue_zero_wait_decision_2026_09_09.py` — 12,314 bytes, 236 lines — Intermediate zero-wait builder/verifier has no retained test, pin, or closure dependency; later final decision/verifier remains.
- `scripts/research/crossasset_bind_x03_public_samples_2026_09_09.py` — 16,405 bytes, 303 lines — Closed X-route probe/diagnostic runner has no retained test, pin, importer, or canonical closure dependency; closure components remain.
- `scripts/research/crossasset_crypto_overlay_diagnostics_2026_09_09.py` — 24,185 bytes, 405 lines — Closed X-route probe/diagnostic runner has no retained test, pin, importer, or canonical closure dependency; closure components remain.
- `scripts/research/crossasset_max_testing_verdict_matrix_2026_09_09.py` — 17,473 bytes, 309 lines — Closed X-route probe/diagnostic runner has no retained test, pin, importer, or canonical closure dependency; closure components remain.
- `scripts/research/crossasset_retrospective_diagnostics_2026_09_09.py` — 19,117 bytes, 363 lines — Closed X-route probe/diagnostic runner has no retained test, pin, importer, or canonical closure dependency; closure components remain.
- `scripts/research/crypto_capacity_aware_diagnostics_2026_09_09.py` — 15,744 bytes, 238 lines — No retained current/historical test importer, immutable path/identity pin, or canonical verifier dependency; evidence remains in Git/data records.
- `scripts/research/crypto_max_diagnostics_2026_09_09.py` — 32,237 bytes, 572 lines — Closed exploratory K-route/diagnostic runner has no retained test, pin, importer, or canonical closure dependency; evidence is retained.
- `scripts/research/crypto_max_testing_2026_09_09.py` — 16,647 bytes, 230 lines — Closed exploratory K-route/diagnostic runner has no retained test, pin, importer, or canonical closure dependency; evidence is retained.
- `scripts/research/equity_profit_protection_2026_09.py` — 10,619 bytes, 284 lines — R1 is superseded by R2; no current importer or immutable runner/path/identity pin remains; report and manifest are retained.
- `scripts/research/fetch_equity_sec_archived_submissions_2026_09_07.py` — 3,591 bytes, 87 lines — One-shot intermediate equity acquisition/receipt builder has no retained test, pin, importer, or canonical closure dependency.
- `scripts/research/freeze_crypto_reference_series_2026_09_09.py` — 7,134 bytes, 143 lines — Closed exploratory K-route/diagnostic runner has no retained test, pin, importer, or canonical closure dependency; evidence is retained.
- `scripts/research/probe_crossasset_x01_alternatives_2026_09_08.py` — 2,800 bytes, 76 lines — Closed X-route probe/diagnostic runner has no retained test, pin, importer, or canonical closure dependency; closure components remain.
- `scripts/research/refresh_h14_current_stage_semantics_2026_09_09.py` — 8,457 bytes, 171 lines — Superseded H14 stage-semantics refresh has no retained test, pin, importer, or final-verifier dependency.
- `scripts/research/regenerate_equity_h01_h05_r12_with_sec_evidence_2026_09_09.py` — 7,831 bytes, 162 lines — Per-revision regeneration runner has no retained consumer or immutable source pin; canonical filing verifier and evidence remain.
- `scripts/research/regenerate_equity_h01_h05_r13_with_sec_evidence_2026_09_09.py` — 8,520 bytes, 171 lines — Per-revision regeneration runner has no retained consumer or immutable source pin; canonical filing verifier and evidence remain.
- `scripts/research/regenerate_equity_h01_h05_r14_with_sec_evidence_2026_09_09.py` — 8,520 bytes, 171 lines — Per-revision regeneration runner has no retained consumer or immutable source pin; canonical filing verifier and evidence remain.
- `scripts/research/regenerate_equity_h01_h05_r15_with_sec_evidence_2026_09_09.py` — 9,176 bytes, 178 lines — Per-revision regeneration runner has no retained consumer or immutable source pin; canonical filing verifier and evidence remain.
- `scripts/research/regenerate_equity_h01_h05_r16_with_sec_evidence_2026_09_09.py` — 9,176 bytes, 178 lines — Per-revision regeneration runner has no retained consumer or immutable source pin; canonical filing verifier and evidence remain.
- `scripts/research/regenerate_equity_h01_h05_r17_with_sec_evidence_2026_09_09.py` — 9,176 bytes, 178 lines — Per-revision regeneration runner has no retained consumer or immutable source pin; canonical filing verifier and evidence remain.
- `scripts/research/regenerate_equity_h01_h05_r18_with_sec_evidence_2026_09_09.py` — 9,176 bytes, 178 lines — Per-revision regeneration runner has no retained consumer or immutable source pin; canonical filing verifier and evidence remain.
- `scripts/research/verify_strategy_queue_zero_wait_decision_2026_09_09.py` — 6,252 bytes, 111 lines — Intermediate zero-wait builder/verifier has no retained test, pin, or closure dependency; later final decision/verifier remains.
- `scripts/research_v3_position_risk.py` — 81,526 bytes, 1,891 lines — Byte-identical to the retained August archive copy; no current importer, manifest/receipt pin, or verifier dependency remains.

## Archived scripts

No new archive directory was created in this pass, so `scripts/archive/research_2026_09/README.md` was not required. Path-sensitive closed runners were not moved; the superseded R1 runner was deleted because its evidence has no runner identity pin and R2 is the canonical replacement. The existing 24-file August archive remains immutable and in place:

- `scripts/archive/research_2026_08/audit_survivorship.py`
- `scripts/archive/research_2026_08/build_full_survivorship_dataset.py`
- `scripts/archive/research_2026_08/build_survivorship_dataset.py`
- `scripts/archive/research_2026_08/combined_portfolio_validation.py`
- `scripts/archive/research_2026_08/corrected_v3_runner.py`
- `scripts/archive/research_2026_08/fetch_corporate_actions_v4.py`
- `scripts/archive/research_2026_08/research_crypto_cadence.py`
- `scripts/archive/research_2026_08/research_deployment_start.py`
- `scripts/archive/research_2026_08/research_equity_alpha_optimization.py`
- `scripts/archive/research_2026_08/research_equity_cadence.py`
- `scripts/archive/research_2026_08/research_equity_hardening.py`
- `scripts/archive/research_2026_08/research_equity_staleness.py`
- `scripts/archive/research_2026_08/research_helpers.py`
- `scripts/archive/research_2026_08/research_mh_raw_shadow.py`
- `scripts/archive/research_2026_08/research_mh_raw_validation.py`
- `scripts/archive/research_2026_08/research_production_policy.py`
- `scripts/archive/research_2026_08/research_robustness_audit.py`
- `scripts/archive/research_2026_08/research_v3_exit_latency.py`
- `scripts/archive/research_2026_08/research_v3_position_risk.py`
- `scripts/archive/research_2026_08/research_v3_risk_tail_loss.py`
- `scripts/archive/research_2026_08/run_corrected_v3.py`
- `scripts/archive/research_2026_08/run_v3_survivorship_comparison.py`
- `scripts/archive/research_2026_08/strategy_foundation.py`
- `scripts/archive/research_2026_08/strategy_robustness.py`

## Exact duplicates removed

- Deleted `scripts/research_v3_position_risk.py`, raw SHA-256 `32c627d5e4021d99069233276387b966785e1a16c215f6984fec85daca26d008`, because it was byte-identical to retained `scripts/archive/research_2026_08/research_v3_position_risk.py` and had no current importer or immutable path/identity pin.
- The remaining exact duplicate groups are intentionally retained: `research_equity_alpha_optimization.py` is used by the current V3 parity test and its archive copy preserves August lineage; `research_helpers.py` is current/reusable and is needed by archived standalone scripts; `research_v3_exit_latency.py` remains because R2 explicitly records its path in the forbidden-lineage contract and the archive copy preserves historical lineage.

## Active/current scripts retained

Class `A` retains the current allowlist and direct current research contracts:

- `scripts/execution_cost_transferability_p1_report.py`
- `scripts/execution_shadow_report.py`
- `scripts/research/acquire_r46_tlt_market_data_2026_09_10.py`
- `scripts/research/acquire_r46_treasury_documents_2026_09_10.py`
- `scripts/research/acquire_r46_treasury_xml_v2_2026_09_10.py`
- `scripts/research/build_r46_native_ledger_attestation_2026_09_10.py`
- `scripts/research/build_r46_nyse_calendar_2026_09_10.py`
- `scripts/research/build_r46_publication_availability_evidence_2026_09_11.py`
- `scripts/research/build_r46_v2_data_package_2026_09_10.py`
- `scripts/research/build_r46_v2_governance_2026_09_10.py`
- `scripts/research/equity_profit_protection_2026_09_r2.py`
- `scripts/research/r46_preflight_2026_09_10.py`
- `scripts/research/r46_treasury_auction_readiness_2026_09_10.py`
- `scripts/research/r46_treasury_auction_unwind_tlt_v2_2026_09_10.py`
- `scripts/research/r46_v2_preflight_2026_09_10.py`
- `scripts/research/r46_v3_oracle_2026_09_11.py`
- `scripts/research/validate_r46_holdout_custody_2026_09_10.py`
- `scripts/research/verify_r46_treasury_auction_unwind_tlt_v2_2026_09_10.py`
- `scripts/research_crypto_candidate_tournament.py`
- `scripts/research_equity_alpha_optimization.py`
- `scripts/research_v3_current_parity_control.py`

## Reusable research infrastructure retained

Class `D` remains available for shared validation, data-contract, isolation, and parity plumbing:

- `scripts/audit_symmetric_stage_a_coverage.py`
- `scripts/build_symmetric_v5_pipeline.py`
- `scripts/check_production_isolation.py`
- `scripts/fetch_corporate_actions_v5.py`
- `scripts/m3_data_gate_helpers.py`
- `scripts/research/crossasset_source_matrix_2026_09_08.py`
- `scripts/research_helpers.py`
- `scripts/research_v3_equity_roadmap.py`
- `scripts/run_symmetric_v5_validation.py`

## Pinned historical scripts retained

Class `C` covers exact path/hash/commit-bound historical campaigns, retained test dependencies, or documented frozen research implementations:

- `scripts/acquire_m3_iwm_raw_bundle.py`
- `scripts/build_m3_pit_scanner_universe.py`
- `scripts/compact_equity_m3_economic_predeclaration.py`
- `scripts/compact_equity_m3_economic_result.py`
- `scripts/research/acquire_crossasset_x01_alpaca_2026_09_08.py`
- `scripts/research/acquire_crossasset_x01_treasury_2026_09_08.py`
- `scripts/research/acquire_crossasset_x01_yahoo_2026_09_08.py`
- `scripts/research/acquire_equity_fundamentals_pilot_2026_09_07.py`
- `scripts/research/acquire_equity_h01_h05_price_security_retry_2026_09_09.py`
- `scripts/research/acquire_equity_sec_original_documents_pilot_2026_09_07.py`
- `scripts/research/acquire_equity_secondary_candidate_batches_2026_09_08.py`
- `scripts/research/build_equity_h01_h16_max_testing_verdict_2026_09_09.py`
- `scripts/research/build_equity_h01_h16_protocol_2026_09_09.py`
- `scripts/research/crypto_max_return_economic_2026_09.py`
- `scripts/research/crypto_max_return_novelty_2026_09.py`
- `scripts/research/crypto_max_return_novelty_runner_2026_09.py`
- `scripts/research/crypto_signed_risk_economic_2026_09.py`
- `scripts/research/crypto_signed_risk_novelty_2026_09.py`
- `scripts/research/crypto_signed_risk_novelty_runner_2026_09.py`
- `scripts/research/crypto_source_gap_audit_kraken_2026_09_09.py`
- `scripts/research/equity_h01_h05_causal_input_gate_2026_09_08.py`
- `scripts/research/equity_low_beta_candidate_2026_09.py`
- `scripts/research/equity_low_beta_engine_2026_09.py`
- `scripts/research/execution_model_robustness_backtest_2026_09.py`
- `scripts/research/high_proximity364_history_fixed_2026_09_05.py`
- `scripts/research/high_proximity364_history_fixed_runner_2026_09_05.py`
- `scripts/research/immediate_candidates_economic_2026_09_09.py`
- `scripts/research/normalize_equity_sec_fundamentals_pilot_2026_09_07.py`
- `scripts/research/portfolio_expansion_crypto_diagnostic_2026_09_05.py`
- `scripts/research/portfolio_expansion_crypto_screen_2026_09.py`
- `scripts/research/run_equity_h14c_posthoc_2026_09_09.py`
- `scripts/research/run_equity_local_diagnostics_2026_09_09.py`
- `scripts/research/zero_wait_overlay_economic_2026_09_10.py`
- `scripts/research_candidate_observation_parity.py`
- `scripts/research_crypto_residual_low_variance.py`
- `scripts/research_equity_m3_candidate_ocp.py`
- `scripts/research_equity_m3_data_gate.py`
- `scripts/research_equity_m3_data_gate_r5.py`
- `scripts/research_equity_m3_economic.py`
- `scripts/research_equity_m3_m4.py`
- `scripts/research_h14_turn_of_month.py`
- `scripts/research_v3_equity_followups.py`
- `scripts/research_v3_exit_latency.py`

## Canonical final verifiers retained

Class `B` preserves the final closure components and non-economic artifact audits/receipt compactors:

- `scripts/compact_candidate_observation_parity_receipt.py`
- `scripts/compact_equity_m3_candidate_ocp_attestation_r2.py`
- `scripts/compact_equity_m3_candidate_ocp_receipt.py`
- `scripts/research/acquire_crypto_secondary_public_2026_09_08.py`
- `scripts/research/acquire_equity_secondary_public_2026_09_08.py`
- `scripts/research/audit_h14_native_batch_r4.py`
- `scripts/research/build_immediate_candidates_invalidation_2026_09_10.py`
- `scripts/research/build_strategy_queue_contract_2026_09_09.py`
- `scripts/research/build_zero_wait_final_decision_2026_09_10.py`
- `scripts/research/crossasset_preflight_2026_09_08.py`
- `scripts/research/crossasset_public_route_exhaustion_2026_09_09.py`
- `scripts/research/crossasset_receipt_verifier_2026_09_08.py`
- `scripts/research/crossasset_two_leg_ledger_2026_09_08.py`
- `scripts/research/crypto_queue_causal_adapters_2026_09_08.py`
- `scripts/research/crypto_secondary_causal_gate_2026_09_08.py`
- `scripts/research/equity_secondary_causal_gate_2026_09_08.py`
- `scripts/research/h14_v3_daily_nav_comparability_2026_09_09.py`
- `scripts/research/high_proximity364_exposure_audit_2026_09_05.py`
- `scripts/research/high_proximity364_history_fixed_audit_2026_09_05.py`
- `scripts/research/high_proximity364_trace_2026_09_05.py`
- `scripts/research/k11_signed_flow_candidate_2026_09_08.py`
- `scripts/research/normalize_equity_h06_form4_2026_09_09.py`
- `scripts/research/normalize_equity_secondary_h08_h15_2026_09_09.py`
- `scripts/research/normalize_h11_nport_snapshot_2026_09_09.py`
- `scripts/research/portfolio_expansion_crypto_screen_analysis_2026_09_05.py`
- `scripts/research/run_v3_canonical_validation.py`
- `scripts/research/v3_baseline_no_persist_verify_2026_09_08.py`
- `scripts/research/verify_all_34_closure_2026_09_09.py`
- `scripts/research/verify_equity_h01_h05_sec_filing_evidence_2026_09_09.py`
- `scripts/research/verify_immediate_candidates_economic_2026_09_09.py`
- `scripts/research/verify_immediate_candidates_invalidation_2026_09_10.py`
- `scripts/research/verify_zero_wait_final_decision_2026_09_10.py`
- `scripts/research/verify_zero_wait_overlay_economic_2026_09_10.py`

## Operational scripts left untouched

Class `G` is outside this research-lifecycle mutation and remains unchanged:

- `scripts/audit_crypto_t1_promotion_parity.py`
- `scripts/audit_crypto_universe_parity.py`
- `scripts/audit_v3_paper_soak.py`
- `scripts/execution_quality_report.py`
- `scripts/paper_smoke.py`
- `scripts/reconcile_paper_incident.py`
- `scripts/repair_local_state.py`
- `scripts/run_crypto_universe_scan.py`
- `scripts/run_paper_chaos.py`
- `scripts/scan_equity_universe.py`

## Dependency graph findings

- Corrected AST graph: 106 script edges; corrected test graph: 86 test edges; dynamic/path graph: 128 edges; parse errors: 0.
- The graph resolves package-alias imports, in-function imports, `importlib.util.spec_from_file_location` loaders, subprocess/path literals, test/CLI references, and exact manifest/receipt text references. Post-edit active-code sweep found no reference to any deleted script.
- Canonical all-34 closure verifier `scripts/research/verify_all_34_closure_2026_09_09.py` has a 16-script transitive closure:

  - `scripts/research/acquire_crypto_secondary_public_2026_09_08.py`
  - `scripts/research/acquire_equity_secondary_public_2026_09_08.py`
  - `scripts/research/build_strategy_queue_contract_2026_09_09.py`
  - `scripts/research/crossasset_preflight_2026_09_08.py`
  - `scripts/research/crossasset_public_route_exhaustion_2026_09_09.py`
  - `scripts/research/crossasset_receipt_verifier_2026_09_08.py`
  - `scripts/research/crossasset_two_leg_ledger_2026_09_08.py`
  - `scripts/research/crypto_queue_causal_adapters_2026_09_08.py`
  - `scripts/research/crypto_secondary_causal_gate_2026_09_08.py`
  - `scripts/research/equity_secondary_causal_gate_2026_09_08.py`
  - `scripts/research/k11_signed_flow_candidate_2026_09_08.py`
  - `scripts/research/normalize_equity_h06_form4_2026_09_09.py`
  - `scripts/research/normalize_equity_secondary_h08_h15_2026_09_09.py`
  - `scripts/research/normalize_h11_nport_snapshot_2026_09_09.py`
  - `scripts/research/verify_all_34_closure_2026_09_09.py`
  - `scripts/research/verify_equity_h01_h05_sec_filing_evidence_2026_09_09.py`

- The closure retains the contract builder, the final verifier, equity/crypto causal gates, normalization components, cross-asset preflight/route/receipt/ledger components, and their dynamically loaded acquisition helpers. Queue completion/max-testing/zero-wait intermediate builders were not in the closure dependency direction and had no retained test/pin.
- The restored `scripts/research/acquire_equity_secondary_candidate_batches_2026_09_08.py` is now a `C` row because the retained historical secondary-causal-gate test imports it dynamically inside two test bodies.

## Immutable-evidence findings

- Parsed 141 JSON files under `data/research_manifests/` and `data/research_receipts/`; parse errors: 0.
- Exact deleted-path references in manifests/receipts after the edit: 0. Existing M3, R46, P0/P1, R2, H14, V3, and H/K/X evidence pins remain untouched.
- `git diff --name-only` contains only `docs/research/README.md` and the deleted/recovered research-script paths. No manifest, receipt, attestation, report, result, dataset, source, test, strategy, risk, PAPER, LIVE, or economics file was edited.
- Historical documentation still mentions deleted V3 root topology where it records the earlier audit; those are immutable provenance references, not executable consumers.

## Active test impact

- No test file or classifier entry changed. Current test importers were rechecked with corrected AST/dynamic test edges; no deleted script is imported by the active suite.
- `pytest --collect-only -q --strict-markers` remains 1,460 collected; expected active partitions remain 1,028 product/non-research and 432 current research. There is no unexplained active reduction.
- P0/P1, R2, all current R46 contracts, current V3 parity, terminal guards, and operational PAPER safety tests remain in the active/current validation surface.

## Historical verification

- `verify_all_34_closure_2026_09_09.py --verify`: `ALL_34_RESEARCH_PROGRAM_CLOSED_WITH_DEFINITIONS`, candidate count 34, issues `[]`, read-only, record SHA `48b3525cb52a72a540e83e34edea9367ee4265b4ddf1f7be02f970b7b8204cf6`.
- Retained M3 subset in a clean local clone with Git history and ignored research data: 439 passed, 1 deselected. The deselection is the explicitly preserved stale CI-command assertion; no M3 implementation/evidence was changed.
- Retained V3/current canonical set (`equity_residual_momentum_v3`, current parity control, strategy freeze, terminal, survivorship, and H14 V3 comparability): 55 passed.
- The old V3 integrity test filenames removed in Phase 2C are not present, so no nonexistent historical test names were claimed as executed. Root `research_v3_position_risk.py` deletion was validated against the retained archive duplicate and no test/import/pin dependency.

## Duplicate reduction

- Before: 4 exact raw-content duplicate groups across the audited Python surface. After: 3 groups remain; one redundant root/archive pair lost its root copy.
- Deleted script footprint: 446,638 bytes and 8,425 lines. The remaining duplicate groups are intentional and documented above.

## Validation commands

| Command | Observed result |
|---|---|
| `.venv/bin/pytest --collect-only -q --strict-markers` | `1460 tests collected` |
| `.venv/bin/pytest -m "not research" --strict-markers` | `1028 passed, 432 deselected, 2 warnings` (required local-socket escalation) |
| `.venv/bin/pytest -m "research and not historical_research" --strict-markers` | `432 passed, 1028 deselected` |
| `.venv/bin/python -m compileall -q scripts` | passed; all retained scripts compile |
| `.venv/bin/python scripts/research/verify_all_34_closure_2026_09_09.py --verify` | passed read-only; all 34 closed |
| clean-clone retained M3 command with stale CI assertion excluded | `439 passed, 1 deselected` |
| retained V3/current canonical command | `55 passed` |
| `git diff --check` | passed |
| post-edit deleted-script reference sweep | no active-code refs to deleted scripts |
| manifest/receipt JSON parse and exact-pin scan | 141 files parsed, 0 errors, 0 deleted-path pins |
| targeted Ruff check/format check for 11 active/reusable files | passed; 11 files already formatted |
| broad `.venv/bin/ruff check scripts/research scripts/*.py` | 117 pre-existing legacy findings; no broad historical-script rewrite made |

## Remaining cleanup candidates

- No additional deletion met the strict no-consumer/no-pin/no-verifier rule in this pass.
- The documented X01 acquisition runners, the H01-H05 base gate, shared cross-asset source matrix, final portfolio-expansion analyzer, H14 native audit, and other `C`/`B` rows remain candidates only for a future evidence-aware review because their reports, retained test imports, reusable boundaries, or final-audit role are still meaningful.
- The broad legacy Ruff findings and the retained canonical verifier’s format/import ordering are pre-existing quality debt, not part of this lifecycle cleanup and were intentionally left untouched.

## Audited research-script table

The table covers all 168 pre-edit scripts. Full raw SHA-256/Git-blob identities, corrected imports/importers, dynamic/path consumers, test importers, exact JSON/doc refs, duplicate groups, action, and rationale are in the machine-readable inventory named above.

| Path | Bytes | Class | Campaign | Graph/tests/pins/docs | Action | Rationale |
|---|---:|---|---|---|---|---|
| `scripts/acquire_m3_iwm_raw_bundle.py` | 41,946 | `C` | equity M3 / candidate OCP | AST in/out 0/1; dynamic in/out 2/0; tests C/H 0/1; JSON 4; docs 0; closure no | `RETAIN` | Pinned or documented historical research implementation retained for provenance, evidence replay, or a non-redundant campaign boundary. |
| `scripts/archive/research_2026_08/audit_survivorship.py` | 12,787 | `E` | August 2026 closed archive | AST in/out 0/0; dynamic in/out 0/0; tests C/H 0/0; JSON 0; docs 0; closure no | `RETAIN_EXISTING_ARCHIVE` | Already present in the immutable August research archive; retained in place without rewrite. |
| `scripts/archive/research_2026_08/build_full_survivorship_dataset.py` | 34,924 | `E` | August 2026 closed archive | AST in/out 0/0; dynamic in/out 0/0; tests C/H 0/0; JSON 0; docs 0; closure no | `RETAIN_EXISTING_ARCHIVE` | Already present in the immutable August research archive; retained in place without rewrite. |
| `scripts/archive/research_2026_08/build_survivorship_dataset.py` | 13,923 | `E` | August 2026 closed archive | AST in/out 0/0; dynamic in/out 0/0; tests C/H 0/0; JSON 0; docs 0; closure no | `RETAIN_EXISTING_ARCHIVE` | Already present in the immutable August research archive; retained in place without rewrite. |
| `scripts/archive/research_2026_08/combined_portfolio_validation.py` | 24,308 | `E` | August 2026 closed archive | AST in/out 0/0; dynamic in/out 0/0; tests C/H 0/0; JSON 0; docs 0; closure no | `RETAIN_EXISTING_ARCHIVE` | Already present in the immutable August research archive; retained in place without rewrite. |
| `scripts/archive/research_2026_08/corrected_v3_runner.py` | 4,626 | `E` | August 2026 closed archive | AST in/out 0/0; dynamic in/out 0/0; tests C/H 0/0; JSON 0; docs 0; closure no | `RETAIN_EXISTING_ARCHIVE` | Already present in the immutable August research archive; retained in place without rewrite. |
| `scripts/archive/research_2026_08/fetch_corporate_actions_v4.py` | 10,909 | `E` | August 2026 closed archive | AST in/out 0/0; dynamic in/out 0/0; tests C/H 0/0; JSON 0; docs 0; closure no | `RETAIN_EXISTING_ARCHIVE` | Already present in the immutable August research archive; retained in place without rewrite. |
| `scripts/archive/research_2026_08/research_crypto_cadence.py` | 54,758 | `E` | August 2026 closed archive | AST in/out 0/1; dynamic in/out 0/0; tests C/H 0/0; JSON 0; docs 0; closure no | `RETAIN_EXISTING_ARCHIVE` | Already present in the immutable August research archive; retained in place without rewrite. |
| `scripts/archive/research_2026_08/research_deployment_start.py` | 18,948 | `E` | August 2026 closed archive | AST in/out 0/0; dynamic in/out 0/0; tests C/H 0/0; JSON 0; docs 0; closure no | `RETAIN_EXISTING_ARCHIVE` | Already present in the immutable August research archive; retained in place without rewrite. |
| `scripts/archive/research_2026_08/research_equity_alpha_optimization.py` | 52,645 | `E` | August 2026 closed archive | AST in/out 0/1; dynamic in/out 0/1; tests C/H 0/0; JSON 0; docs 1; closure no; duplicate group | `RETAIN_EXISTING_ARCHIVE` | Already present in the immutable August research archive; retained in place without rewrite. |
| `scripts/archive/research_2026_08/research_equity_cadence.py` | 56,577 | `E` | August 2026 closed archive | AST in/out 0/1; dynamic in/out 0/0; tests C/H 0/0; JSON 0; docs 0; closure no | `RETAIN_EXISTING_ARCHIVE` | Already present in the immutable August research archive; retained in place without rewrite. |
| `scripts/archive/research_2026_08/research_equity_hardening.py` | 26,174 | `E` | August 2026 closed archive | AST in/out 0/1; dynamic in/out 0/0; tests C/H 0/0; JSON 0; docs 0; closure no | `RETAIN_EXISTING_ARCHIVE` | Already present in the immutable August research archive; retained in place without rewrite. |
| `scripts/archive/research_2026_08/research_equity_staleness.py` | 7,400 | `E` | August 2026 closed archive | AST in/out 0/0; dynamic in/out 0/0; tests C/H 0/0; JSON 0; docs 0; closure no | `RETAIN_EXISTING_ARCHIVE` | Already present in the immutable August research archive; retained in place without rewrite. |
| `scripts/archive/research_2026_08/research_helpers.py` | 10,349 | `E` | August 2026 closed archive | AST in/out 0/0; dynamic in/out 0/0; tests C/H 0/0; JSON 0; docs 0; closure no; duplicate group | `RETAIN_EXISTING_ARCHIVE` | Already present in the immutable August research archive; retained in place without rewrite. |
| `scripts/archive/research_2026_08/research_mh_raw_shadow.py` | 10,732 | `E` | August 2026 closed archive | AST in/out 0/1; dynamic in/out 0/0; tests C/H 0/0; JSON 0; docs 0; closure no | `RETAIN_EXISTING_ARCHIVE` | Already present in the immutable August research archive; retained in place without rewrite. |
| `scripts/archive/research_2026_08/research_mh_raw_validation.py` | 50,039 | `E` | August 2026 closed archive | AST in/out 0/1; dynamic in/out 0/0; tests C/H 0/0; JSON 0; docs 0; closure no | `RETAIN_EXISTING_ARCHIVE` | Already present in the immutable August research archive; retained in place without rewrite. |
| `scripts/archive/research_2026_08/research_production_policy.py` | 12,435 | `E` | August 2026 closed archive | AST in/out 0/1; dynamic in/out 0/0; tests C/H 0/0; JSON 0; docs 0; closure no | `RETAIN_EXISTING_ARCHIVE` | Already present in the immutable August research archive; retained in place without rewrite. |
| `scripts/archive/research_2026_08/research_robustness_audit.py` | 66,044 | `E` | August 2026 closed archive | AST in/out 0/1; dynamic in/out 0/0; tests C/H 0/0; JSON 0; docs 0; closure no | `RETAIN_EXISTING_ARCHIVE` | Already present in the immutable August research archive; retained in place without rewrite. |
| `scripts/archive/research_2026_08/research_v3_exit_latency.py` | 76,279 | `E` | August 2026 closed archive | AST in/out 0/1; dynamic in/out 0/0; tests C/H 0/0; JSON 0; docs 1; closure no; duplicate group | `RETAIN_EXISTING_ARCHIVE` | Already present in the immutable August research archive; retained in place without rewrite. |
| `scripts/archive/research_2026_08/research_v3_position_risk.py` | 81,526 | `E` | August 2026 closed archive | AST in/out 0/1; dynamic in/out 0/0; tests C/H 0/0; JSON 0; docs 1; closure no; duplicate group | `RETAIN_EXISTING_ARCHIVE` | Already present in the immutable August research archive; retained in place without rewrite. |
| `scripts/archive/research_2026_08/research_v3_risk_tail_loss.py` | 77,322 | `E` | August 2026 closed archive | AST in/out 0/1; dynamic in/out 0/0; tests C/H 0/0; JSON 0; docs 1; closure no | `RETAIN_EXISTING_ARCHIVE` | Already present in the immutable August research archive; retained in place without rewrite. |
| `scripts/archive/research_2026_08/run_corrected_v3.py` | 11,343 | `E` | August 2026 closed archive | AST in/out 0/0; dynamic in/out 0/0; tests C/H 0/0; JSON 0; docs 0; closure no | `RETAIN_EXISTING_ARCHIVE` | Already present in the immutable August research archive; retained in place without rewrite. |
| `scripts/archive/research_2026_08/run_v3_survivorship_comparison.py` | 9,124 | `E` | August 2026 closed archive | AST in/out 0/0; dynamic in/out 0/0; tests C/H 0/0; JSON 0; docs 0; closure no | `RETAIN_EXISTING_ARCHIVE` | Already present in the immutable August research archive; retained in place without rewrite. |
| `scripts/archive/research_2026_08/strategy_foundation.py` | 35,807 | `E` | August 2026 closed archive | AST in/out 0/0; dynamic in/out 0/0; tests C/H 0/0; JSON 0; docs 1; closure no | `RETAIN_EXISTING_ARCHIVE` | Already present in the immutable August research archive; retained in place without rewrite. |
| `scripts/archive/research_2026_08/strategy_robustness.py` | 46,880 | `E` | August 2026 closed archive | AST in/out 0/0; dynamic in/out 0/0; tests C/H 0/0; JSON 0; docs 0; closure no | `RETAIN_EXISTING_ARCHIVE` | Already present in the immutable August research archive; retained in place without rewrite. |
| `scripts/audit_crypto_t1_promotion_parity.py` | 14,927 | `G` | repository/operational research support | AST in/out 0/1; dynamic in/out 0/0; tests C/H 0/0; JSON 0; docs 1; closure no | `RETAIN` | Operational/PAPER/reporting utility; outside research-script deletion and left untouched. |
| `scripts/audit_crypto_universe_parity.py` | 12,667 | `G` | repository/operational research support | AST in/out 0/0; dynamic in/out 0/0; tests C/H 0/0; JSON 0; docs 1; closure no | `RETAIN` | Operational/PAPER/reporting utility; outside research-script deletion and left untouched. |
| `scripts/audit_symmetric_stage_a_coverage.py` | 10,461 | `D` | repository/operational research support | AST in/out 0/0; dynamic in/out 0/0; tests C/H 0/0; JSON 0; docs 3; closure no | `RETAIN` | Reusable validation/acquisition/infrastructure boundary used by current or pinned research; retain without promotion semantics. |
| `scripts/audit_v3_paper_soak.py` | 54,516 | `G` | V3/current validation infrastructure | AST in/out 0/0; dynamic in/out 1/1; tests C/H 1/0; JSON 0; docs 4; closure no | `RETAIN` | Operational/PAPER/reporting utility; outside research-script deletion and left untouched. |
| `scripts/build_m3_pit_scanner_universe.py` | 45,032 | `C` | equity M3 / candidate OCP | AST in/out 1/1; dynamic in/out 3/1; tests C/H 1/0; JSON 7; docs 0; closure no | `RETAIN` | Pinned or documented historical research implementation retained for provenance, evidence replay, or a non-redundant campaign boundary. |
| `scripts/build_symmetric_v5_pipeline.py` | 15,085 | `D` | V3/current validation infrastructure | AST in/out 0/0; dynamic in/out 0/0; tests C/H 0/0; JSON 1; docs 2; closure no | `RETAIN` | Reusable validation/acquisition/infrastructure boundary used by current or pinned research; retain without promotion semantics. |
| `scripts/check_production_isolation.py` | 2,670 | `D` | repository/operational research support | AST in/out 1/0; dynamic in/out 1/1; tests C/H 0/0; JSON 1; docs 3; closure no | `RETAIN` | Reusable validation/acquisition/infrastructure boundary used by current or pinned research; retain without promotion semantics. |
| `scripts/compact_candidate_observation_parity_receipt.py` | 21,385 | `B` | repository/operational research support | AST in/out 0/0; dynamic in/out 1/2; tests C/H 0/1; JSON 8; docs 1; closure no | `RETAIN` | Canonical final verifier, closure component, receipt compactor, or final artifact audit; retain as the minimal historical contract. |
| `scripts/compact_equity_m3_candidate_ocp_attestation_r2.py` | 45,721 | `B` | equity M3 / candidate OCP | AST in/out 2/1; dynamic in/out 2/2; tests C/H 0/1; JSON 5; docs 1; closure no | `RETAIN` | Canonical final verifier, closure component, receipt compactor, or final artifact audit; retain as the minimal historical contract. |
| `scripts/compact_equity_m3_candidate_ocp_receipt.py` | 13,868 | `B` | equity M3 / candidate OCP | AST in/out 1/0; dynamic in/out 1/0; tests C/H 0/1; JSON 5; docs 1; closure no | `RETAIN` | Canonical final verifier, closure component, receipt compactor, or final artifact audit; retain as the minimal historical contract. |
| `scripts/compact_equity_m3_economic_predeclaration.py` | 25,987 | `C` | equity M3 / candidate OCP | AST in/out 0/2; dynamic in/out 1/1; tests C/H 0/0; JSON 3; docs 0; closure no | `RETAIN` | Pinned or documented historical research implementation retained for provenance, evidence replay, or a non-redundant campaign boundary. |
| `scripts/compact_equity_m3_economic_result.py` | 32,471 | `C` | equity M3 / candidate OCP | AST in/out 0/2; dynamic in/out 1/0; tests C/H 0/1; JSON 1; docs 0; closure no | `RETAIN` | Pinned or documented historical research implementation retained for provenance, evidence replay, or a non-redundant campaign boundary. |
| `scripts/execution_cost_transferability_p1_report.py` | 7,742 | `A` | execution-cost P0/P1 | AST in/out 0/0; dynamic in/out 0/0; tests C/H 0/0; JSON 1; docs 2; closure no | `RETAIN` | Current allowlisted research or its direct current execution/parity contract; retain active path and dependencies. |
| `scripts/execution_quality_report.py` | 6,673 | `G` | repository/operational research support | AST in/out 0/0; dynamic in/out 1/1; tests C/H 0/0; JSON 0; docs 2; closure no | `RETAIN` | Operational/PAPER/reporting utility; outside research-script deletion and left untouched. |
| `scripts/execution_shadow_report.py` | 2,682 | `A` | execution-cost P0/P1 | AST in/out 0/0; dynamic in/out 0/0; tests C/H 0/0; JSON 1; docs 0; closure no | `RETAIN` | Current allowlisted research or its direct current execution/parity contract; retain active path and dependencies. |
| `scripts/fetch_corporate_actions_v5.py` | 7,064 | `D` | V3/current validation infrastructure | AST in/out 0/0; dynamic in/out 0/0; tests C/H 0/0; JSON 1; docs 3; closure no | `RETAIN` | Reusable validation/acquisition/infrastructure boundary used by current or pinned research; retain without promotion semantics. |
| `scripts/m3_data_gate_helpers.py` | 98,219 | `D` | equity M3 / candidate OCP | AST in/out 9/0; dynamic in/out 4/0; tests C/H 1/3; JSON 9; docs 1; closure no | `RETAIN` | Reusable validation/acquisition/infrastructure boundary used by current or pinned research; retain without promotion semantics. |
| `scripts/paper_smoke.py` | 19,850 | `G` | repository/operational research support | AST in/out 0/0; dynamic in/out 0/0; tests C/H 0/0; JSON 1; docs 5; closure no | `RETAIN` | Operational/PAPER/reporting utility; outside research-script deletion and left untouched. |
| `scripts/reconcile_paper_incident.py` | 10,663 | `G` | repository/operational research support | AST in/out 0/0; dynamic in/out 0/0; tests C/H 1/0; JSON 0; docs 2; closure no | `RETAIN` | Operational/PAPER/reporting utility; outside research-script deletion and left untouched. |
| `scripts/repair_local_state.py` | 16,502 | `G` | repository/operational research support | AST in/out 0/0; dynamic in/out 0/0; tests C/H 0/0; JSON 0; docs 1; closure no | `RETAIN` | Operational/PAPER/reporting utility; outside research-script deletion and left untouched. |
| `scripts/research/acquire_crossasset_x01_alpaca_2026_09_08.py` | 17,539 | `C` | cross-asset X01-X06 | AST in/out 0/0; dynamic in/out 0/0; tests C/H 0/0; JSON 0; docs 1; closure no | `RETAIN` | Pinned or documented historical research implementation retained for provenance, evidence replay, or a non-redundant campaign boundary. |
| `scripts/research/acquire_crossasset_x01_treasury_2026_09_08.py` | 13,506 | `C` | cross-asset X01-X06 | AST in/out 0/0; dynamic in/out 0/0; tests C/H 0/0; JSON 0; docs 1; closure no | `RETAIN` | Pinned or documented historical research implementation retained for provenance, evidence replay, or a non-redundant campaign boundary. |
| `scripts/research/acquire_crossasset_x01_yahoo_2026_09_08.py` | 9,997 | `C` | cross-asset X01-X06 | AST in/out 0/0; dynamic in/out 0/0; tests C/H 0/0; JSON 1; docs 0; closure no | `RETAIN` | Pinned or documented historical research implementation retained for provenance, evidence replay, or a non-redundant campaign boundary. |
| `scripts/research/acquire_crossasset_x02_cme_retry_2026_09_10.py` | 12,746 | `F` | cross-asset X01-X06 | AST in/out 0/0; dynamic in/out 0/0; tests C/H 0/0; JSON 0; docs 0; closure no | `DELETE_REDUNDANT` | Closed X-route probe/diagnostic runner has no retained test, pin, importer, or canonical closure dependency; closure components remain. |
| `scripts/research/acquire_crypto_max_public_routes_2026_09_09.py` | 23,507 | `F` | repository/operational research support | AST in/out 0/0; dynamic in/out 0/0; tests C/H 0/0; JSON 0; docs 0; closure no | `DELETE_REDUNDANT` | Closed exploratory K-route/diagnostic runner has no retained test, pin, importer, or canonical closure dependency; evidence is retained. |
| `scripts/research/acquire_crypto_secondary_public_2026_09_08.py` | 28,932 | `B` | repository/operational research support | AST in/out 0/0; dynamic in/out 1/0; tests C/H 0/0; JSON 1; docs 0; closure yes | `RETAIN` | Canonical final verifier, closure component, receipt compactor, or final artifact audit; retain as the minimal historical contract. |
| `scripts/research/acquire_equity_fundamentals_pilot_2026_09_07.py` | 8,801 | `C` | repository/operational research support | AST in/out 0/0; dynamic in/out 0/0; tests C/H 0/0; JSON 2; docs 0; closure no | `RETAIN` | Pinned or documented historical research implementation retained for provenance, evidence replay, or a non-redundant campaign boundary. |
| `scripts/research/acquire_equity_h01_h05_price_security_retry_2026_09_09.py` | 16,721 | `C` | equity H01-H16 causal/data gate | AST in/out 0/0; dynamic in/out 0/0; tests C/H 0/0; JSON 3; docs 1; closure no | `RETAIN` | Pinned or documented historical research implementation retained for provenance, evidence replay, or a non-redundant campaign boundary. |
| `scripts/research/acquire_equity_sec_original_documents_pilot_2026_09_07.py` | 5,436 | `C` | repository/operational research support | AST in/out 0/0; dynamic in/out 0/0; tests C/H 0/0; JSON 1; docs 0; closure no | `RETAIN` | Pinned or documented historical research implementation retained for provenance, evidence replay, or a non-redundant campaign boundary. |
| `scripts/research/acquire_equity_secondary_candidate_batches_2026_09_08.py` | 47,599 | `C` | equity secondary causal/data gate | AST in/out 0/1; dynamic in/out 0/0; tests C/H 0/1; JSON 0; docs 0; closure no | `RETAIN` | Retained because the retained historical equity-secondary causal-gate test dynamically imports it; bounded public acquisition helper remains available. |
| `scripts/research/acquire_equity_secondary_public_2026_09_08.py` | 9,850 | `B` | repository/operational research support | AST in/out 0/0; dynamic in/out 1/0; tests C/H 0/0; JSON 1; docs 0; closure yes | `RETAIN` | Canonical final verifier, closure component, receipt compactor, or final artifact audit; retain as the minimal historical contract. |
| `scripts/research/acquire_r46_tlt_market_data_2026_09_10.py` | 62,674 | `A` | R46 treasury/market-data current research | AST in/out 0/0; dynamic in/out 1/0; tests C/H 3/0; JSON 1; docs 0; closure no | `RETAIN` | Current allowlisted research or its direct current execution/parity contract; retain active path and dependencies. |
| `scripts/research/acquire_r46_treasury_documents_2026_09_10.py` | 68,442 | `A` | R46 treasury/market-data current research | AST in/out 0/1; dynamic in/out 0/0; tests C/H 2/0; JSON 1; docs 0; closure no | `RETAIN` | Current allowlisted research or its direct current execution/parity contract; retain active path and dependencies. |
| `scripts/research/acquire_r46_treasury_xml_v2_2026_09_10.py` | 43,965 | `A` | R46 treasury/market-data current research | AST in/out 2/0; dynamic in/out 2/0; tests C/H 3/0; JSON 1; docs 0; closure no | `RETAIN` | Current allowlisted research or its direct current execution/parity contract; retain active path and dependencies. |
| `scripts/research/audit_h14_native_batch_r4.py` | 18,961 | `B` | equity H14 | AST in/out 0/0; dynamic in/out 0/0; tests C/H 0/0; JSON 0; docs 0; closure no | `RETAIN` | Canonical final verifier, closure component, receipt compactor, or final artifact audit; retain as the minimal historical contract. |
| `scripts/research/build_equity_h01_h05_route_closure_2026_09_09.py` | 6,135 | `F` | equity H01-H16 causal/data gate | AST in/out 0/0; dynamic in/out 0/0; tests C/H 0/0; JSON 0; docs 0; closure no | `DELETE_REDUNDANT` | One-shot intermediate equity acquisition/receipt builder has no retained test, pin, importer, or canonical closure dependency. |
| `scripts/research/build_equity_h01_h16_max_testing_verdict_2026_09_09.py` | 12,152 | `C` | equity H01-H16 causal/data gate | AST in/out 0/0; dynamic in/out 1/1; tests C/H 0/1; JSON 0; docs 0; closure no | `RETAIN` | Pinned or documented historical research implementation retained for provenance, evidence replay, or a non-redundant campaign boundary. |
| `scripts/research/build_equity_h01_h16_protocol_2026_09_09.py` | 29,553 | `C` | equity H01-H16 causal/data gate | AST in/out 0/0; dynamic in/out 1/1; tests C/H 0/1; JSON 1; docs 0; closure no | `RETAIN` | Pinned or documented historical research implementation retained for provenance, evidence replay, or a non-redundant campaign boundary. |
| `scripts/research/build_equity_pit_universe_manifest_2026_09_07.py` | 4,946 | `F` | repository/operational research support | AST in/out 0/0; dynamic in/out 0/0; tests C/H 0/0; JSON 0; docs 0; closure no | `DELETE_REDUNDANT` | One-shot intermediate equity acquisition/receipt builder has no retained test, pin, importer, or canonical closure dependency. |
| `scripts/research/build_immediate_candidates_invalidation_2026_09_10.py` | 11,095 | `B` | H/K/X strategy queue closure | AST in/out 1/0; dynamic in/out 0/2; tests C/H 0/1; JSON 0; docs 0; closure no | `RETAIN` | Canonical final verifier, closure component, receipt compactor, or final artifact audit; retain as the minimal historical contract. |
| `scripts/research/build_r46_native_ledger_attestation_2026_09_10.py` | 10,699 | `A` | R46 treasury/market-data current research | AST in/out 0/2; dynamic in/out 2/2; tests C/H 2/0; JSON 2; docs 0; closure no | `RETAIN` | Current allowlisted research or its direct current execution/parity contract; retain active path and dependencies. |
| `scripts/research/build_r46_nyse_calendar_2026_09_10.py` | 31,867 | `A` | R46 treasury/market-data current research | AST in/out 1/1; dynamic in/out 1/0; tests C/H 4/0; JSON 4; docs 0; closure no | `RETAIN` | Current allowlisted research or its direct current execution/parity contract; retain active path and dependencies. |
| `scripts/research/build_r46_publication_availability_evidence_2026_09_11.py` | 19,814 | `A` | R46 treasury/market-data current research | AST in/out 0/0; dynamic in/out 1/0; tests C/H 2/0; JSON 1; docs 0; closure no | `RETAIN` | Current allowlisted research or its direct current execution/parity contract; retain active path and dependencies. |
| `scripts/research/build_r46_v2_data_package_2026_09_10.py` | 105,128 | `A` | R46 treasury/market-data current research | AST in/out 3/3; dynamic in/out 2/0; tests C/H 3/0; JSON 2; docs 0; closure no | `RETAIN` | Current allowlisted research or its direct current execution/parity contract; retain active path and dependencies. |
| `scripts/research/build_r46_v2_governance_2026_09_10.py` | 61,362 | `A` | R46 treasury/market-data current research | AST in/out 1/0; dynamic in/out 2/14; tests C/H 2/0; JSON 2; docs 0; closure no | `RETAIN` | Current allowlisted research or its direct current execution/parity contract; retain active path and dependencies. |
| `scripts/research/build_strategy_queue_completion_report_2026_09_09.py` | 29,407 | `F` | H/K/X strategy queue closure | AST in/out 0/1; dynamic in/out 0/0; tests C/H 0/0; JSON 0; docs 0; closure no | `DELETE_REDUNDANT` | Intermediate queue aggregation/report runner has no retained test, pin, or canonical closure dependency; frozen closure remains. |
| `scripts/research/build_strategy_queue_contract_2026_09_09.py` | 14,079 | `B` | H/K/X strategy queue closure | AST in/out 2/0; dynamic in/out 0/0; tests C/H 0/0; JSON 0; docs 0; closure yes | `RETAIN` | Canonical final verifier, closure component, receipt compactor, or final artifact audit; retain as the minimal historical contract. |
| `scripts/research/build_strategy_queue_max_testing_aggregate_2026_09_09.py` | 33,821 | `F` | H/K/X strategy queue closure | AST in/out 0/1; dynamic in/out 0/0; tests C/H 0/0; JSON 0; docs 0; closure no | `DELETE_REDUNDANT` | Intermediate queue aggregation/report runner has no retained test, pin, or canonical closure dependency; frozen closure remains. |
| `scripts/research/build_strategy_queue_zero_wait_decision_2026_09_09.py` | 12,314 | `F` | H/K/X strategy queue closure | AST in/out 1/0; dynamic in/out 0/0; tests C/H 0/0; JSON 0; docs 0; closure no | `DELETE_REDUNDANT` | Intermediate zero-wait builder/verifier has no retained test, pin, or closure dependency; later final decision/verifier remains. |
| `scripts/research/build_zero_wait_final_decision_2026_09_10.py` | 17,003 | `B` | H/K/X strategy queue closure | AST in/out 1/0; dynamic in/out 0/2; tests C/H 0/1; JSON 0; docs 0; closure no | `RETAIN` | Canonical final verifier, closure component, receipt compactor, or final artifact audit; retain as the minimal historical contract. |
| `scripts/research/crossasset_bind_x03_public_samples_2026_09_09.py` | 16,405 | `F` | cross-asset X01-X06 | AST in/out 0/1; dynamic in/out 0/0; tests C/H 0/0; JSON 0; docs 0; closure no | `DELETE_REDUNDANT` | Closed X-route probe/diagnostic runner has no retained test, pin, importer, or canonical closure dependency; closure components remain. |
| `scripts/research/crossasset_crypto_overlay_diagnostics_2026_09_09.py` | 24,185 | `F` | cross-asset X01-X06 | AST in/out 0/0; dynamic in/out 0/0; tests C/H 0/0; JSON 0; docs 0; closure no | `DELETE_REDUNDANT` | Closed X-route probe/diagnostic runner has no retained test, pin, importer, or canonical closure dependency; closure components remain. |
| `scripts/research/crossasset_max_testing_verdict_matrix_2026_09_09.py` | 17,473 | `F` | cross-asset X01-X06 | AST in/out 0/0; dynamic in/out 0/0; tests C/H 0/0; JSON 0; docs 0; closure no | `DELETE_REDUNDANT` | Closed X-route probe/diagnostic runner has no retained test, pin, importer, or canonical closure dependency; closure components remain. |
| `scripts/research/crossasset_preflight_2026_09_08.py` | 34,768 | `B` | cross-asset X01-X06 | AST in/out 0/1; dynamic in/out 1/0; tests C/H 0/0; JSON 0; docs 2; closure yes | `RETAIN` | Canonical final verifier, closure component, receipt compactor, or final artifact audit; retain as the minimal historical contract. |
| `scripts/research/crossasset_public_route_exhaustion_2026_09_09.py` | 12,451 | `B` | cross-asset X01-X06 | AST in/out 0/0; dynamic in/out 1/0; tests C/H 0/0; JSON 0; docs 0; closure yes | `RETAIN` | Canonical final verifier, closure component, receipt compactor, or final artifact audit; retain as the minimal historical contract. |
| `scripts/research/crossasset_receipt_verifier_2026_09_08.py` | 20,575 | `B` | cross-asset X01-X06 | AST in/out 3/0; dynamic in/out 1/0; tests C/H 0/0; JSON 0; docs 1; closure yes | `RETAIN` | Canonical final verifier, closure component, receipt compactor, or final artifact audit; retain as the minimal historical contract. |
| `scripts/research/crossasset_retrospective_diagnostics_2026_09_09.py` | 19,117 | `F` | cross-asset X01-X06 | AST in/out 0/0; dynamic in/out 0/0; tests C/H 0/0; JSON 0; docs 0; closure no | `DELETE_REDUNDANT` | Closed X-route probe/diagnostic runner has no retained test, pin, importer, or canonical closure dependency; closure components remain. |
| `scripts/research/crossasset_source_matrix_2026_09_08.py` | 44,688 | `D` | cross-asset X01-X06 | AST in/out 0/1; dynamic in/out 0/0; tests C/H 0/0; JSON 0; docs 0; closure no | `RETAIN` | Reusable validation/acquisition/infrastructure boundary used by current or pinned research; retain without promotion semantics. |
| `scripts/research/crossasset_two_leg_ledger_2026_09_08.py` | 12,053 | `B` | cross-asset X01-X06 | AST in/out 0/0; dynamic in/out 1/0; tests C/H 0/0; JSON 0; docs 0; closure yes | `RETAIN` | Canonical final verifier, closure component, receipt compactor, or final artifact audit; retain as the minimal historical contract. |
| `scripts/research/crypto_capacity_aware_diagnostics_2026_09_09.py` | 15,744 | `F` | crypto K01-K12 / crypto research | AST in/out 0/1; dynamic in/out 0/0; tests C/H 0/0; JSON 0; docs 0; closure no | `DELETE_REDUNDANT` | No retained current/historical test importer, immutable path/identity pin, or canonical verifier dependency; evidence remains in Git/data records. |
| `scripts/research/crypto_max_diagnostics_2026_09_09.py` | 32,237 | `F` | crypto K01-K12 / crypto research | AST in/out 1/0; dynamic in/out 0/0; tests C/H 0/0; JSON 0; docs 0; closure no | `DELETE_REDUNDANT` | Closed exploratory K-route/diagnostic runner has no retained test, pin, importer, or canonical closure dependency; evidence is retained. |
| `scripts/research/crypto_max_return_economic_2026_09.py` | 22,954 | `C` | crypto K01-K12 / crypto research | AST in/out 0/1; dynamic in/out 1/3; tests C/H 0/0; JSON 4; docs 0; closure no | `RETAIN` | Pinned or documented historical research implementation retained for provenance, evidence replay, or a non-redundant campaign boundary. |
| `scripts/research/crypto_max_return_novelty_2026_09.py` | 5,130 | `C` | crypto K01-K12 / crypto research | AST in/out 3/0; dynamic in/out 3/0; tests C/H 0/1; JSON 26; docs 0; closure no | `RETAIN` | Pinned or documented historical research implementation retained for provenance, evidence replay, or a non-redundant campaign boundary. |
| `scripts/research/crypto_max_return_novelty_runner_2026_09.py` | 17,336 | `C` | crypto K01-K12 / crypto research | AST in/out 0/1; dynamic in/out 1/0; tests C/H 0/1; JSON 14; docs 0; closure no | `RETAIN` | Pinned or documented historical research implementation retained for provenance, evidence replay, or a non-redundant campaign boundary. |
| `scripts/research/crypto_max_testing_2026_09_09.py` | 16,647 | `F` | crypto K01-K12 / crypto research | AST in/out 0/0; dynamic in/out 0/0; tests C/H 0/0; JSON 0; docs 0; closure no | `DELETE_REDUNDANT` | Closed exploratory K-route/diagnostic runner has no retained test, pin, importer, or canonical closure dependency; evidence is retained. |
| `scripts/research/crypto_queue_causal_adapters_2026_09_08.py` | 33,455 | `B` | crypto K01-K12 / crypto research | AST in/out 1/1; dynamic in/out 1/1; tests C/H 0/1; JSON 1; docs 0; closure yes | `RETAIN` | Canonical final verifier, closure component, receipt compactor, or final artifact audit; retain as the minimal historical contract. |
| `scripts/research/crypto_secondary_causal_gate_2026_09_08.py` | 62,809 | `B` | crypto K01-K12 / crypto research | AST in/out 0/0; dynamic in/out 1/1; tests C/H 0/1; JSON 1; docs 0; closure yes | `RETAIN` | Canonical final verifier, closure component, receipt compactor, or final artifact audit; retain as the minimal historical contract. |
| `scripts/research/crypto_signed_risk_economic_2026_09.py` | 24,864 | `C` | crypto K01-K12 / crypto research | AST in/out 0/1; dynamic in/out 1/3; tests C/H 0/0; JSON 3; docs 0; closure no | `RETAIN` | Pinned or documented historical research implementation retained for provenance, evidence replay, or a non-redundant campaign boundary. |
| `scripts/research/crypto_signed_risk_novelty_2026_09.py` | 5,553 | `C` | crypto K01-K12 / crypto research | AST in/out 3/0; dynamic in/out 3/0; tests C/H 0/0; JSON 15; docs 0; closure no | `RETAIN` | Pinned or documented historical research implementation retained for provenance, evidence replay, or a non-redundant campaign boundary. |
| `scripts/research/crypto_signed_risk_novelty_runner_2026_09.py` | 27,354 | `C` | crypto K01-K12 / crypto research | AST in/out 0/1; dynamic in/out 2/3; tests C/H 0/0; JSON 12; docs 0; closure no | `RETAIN` | Pinned or documented historical research implementation retained for provenance, evidence replay, or a non-redundant campaign boundary. |
| `scripts/research/crypto_source_gap_audit_kraken_2026_09_09.py` | 18,008 | `C` | crypto K01-K12 / crypto research | AST in/out 0/0; dynamic in/out 0/0; tests C/H 0/0; JSON 0; docs 1; closure no | `RETAIN` | Pinned or documented historical research implementation retained for provenance, evidence replay, or a non-redundant campaign boundary. |
| `scripts/research/equity_h01_h05_causal_input_gate_2026_09_08.py` | 47,707 | `C` | equity H01-H16 causal/data gate | AST in/out 0/0; dynamic in/out 7/0; tests C/H 0/0; JSON 0; docs 1; closure no | `RETAIN` | Pinned or documented historical research implementation retained for provenance, evidence replay, or a non-redundant campaign boundary. |
| `scripts/research/equity_low_beta_candidate_2026_09.py` | 24,935 | `C` | repository/operational research support | AST in/out 1/2; dynamic in/out 1/2; tests C/H 0/1; JSON 5; docs 0; closure no | `RETAIN` | Pinned or documented historical research implementation retained for provenance, evidence replay, or a non-redundant campaign boundary. |
| `scripts/research/equity_low_beta_engine_2026_09.py` | 20,262 | `C` | repository/operational research support | AST in/out 1/1; dynamic in/out 1/0; tests C/H 0/1; JSON 5; docs 0; closure no | `RETAIN` | Pinned or documented historical research implementation retained for provenance, evidence replay, or a non-redundant campaign boundary. |
| `scripts/research/equity_profit_protection_2026_09.py` | 10,619 | `F` | equity profit-protection R1/R2 | AST in/out 0/1; dynamic in/out 0/0; tests C/H 0/0; JSON 0; docs 1; closure no | `DELETE_REDUNDANT` | R1 is superseded by R2; no current importer or immutable runner/path/identity pin remains; report and manifest are retained. |
| `scripts/research/equity_profit_protection_2026_09_r2.py` | 63,185 | `A` | equity profit-protection R1/R2 | AST in/out 0/2; dynamic in/out 0/0; tests C/H 1/0; JSON 1; docs 1; closure no | `RETAIN` | Current allowlisted research or its direct current execution/parity contract; retain active path and dependencies. |
| `scripts/research/equity_secondary_causal_gate_2026_09_08.py` | 64,080 | `B` | repository/operational research support | AST in/out 1/0; dynamic in/out 1/4; tests C/H 0/1; JSON 1; docs 0; closure yes | `RETAIN` | Canonical final verifier, closure component, receipt compactor, or final artifact audit; retain as the minimal historical contract. |
| `scripts/research/execution_model_robustness_backtest_2026_09.py` | 22,573 | `C` | repository/operational research support | AST in/out 0/1; dynamic in/out 0/0; tests C/H 0/1; JSON 1; docs 0; closure no | `RETAIN` | Pinned or documented historical research implementation retained for provenance, evidence replay, or a non-redundant campaign boundary. |
| `scripts/research/fetch_equity_sec_archived_submissions_2026_09_07.py` | 3,591 | `F` | repository/operational research support | AST in/out 0/0; dynamic in/out 0/0; tests C/H 0/0; JSON 0; docs 0; closure no | `DELETE_REDUNDANT` | One-shot intermediate equity acquisition/receipt builder has no retained test, pin, importer, or canonical closure dependency. |
| `scripts/research/freeze_crypto_reference_series_2026_09_09.py` | 7,134 | `F` | repository/operational research support | AST in/out 0/0; dynamic in/out 0/0; tests C/H 0/0; JSON 0; docs 0; closure no | `DELETE_REDUNDANT` | Closed exploratory K-route/diagnostic runner has no retained test, pin, importer, or canonical closure dependency; evidence is retained. |
| `scripts/research/h14_v3_daily_nav_comparability_2026_09_09.py` | 19,041 | `B` | equity H14 | AST in/out 0/2; dynamic in/out 0/2; tests C/H 0/1; JSON 0; docs 1; closure no | `RETAIN` | Canonical final verifier, closure component, receipt compactor, or final artifact audit; retain as the minimal historical contract. |
| `scripts/research/high_proximity364_exposure_audit_2026_09_05.py` | 16,406 | `B` | crypto portfolio-expansion screen | AST in/out 0/0; dynamic in/out 0/0; tests C/H 0/0; JSON 0; docs 1; closure no | `RETAIN` | Canonical final verifier, closure component, receipt compactor, or final artifact audit; retain as the minimal historical contract. |
| `scripts/research/high_proximity364_history_fixed_2026_09_05.py` | 2,344 | `C` | crypto portfolio-expansion screen | AST in/out 1/1; dynamic in/out 1/0; tests C/H 0/2; JSON 1; docs 3; closure no | `RETAIN` | Pinned or documented historical research implementation retained for provenance, evidence replay, or a non-redundant campaign boundary. |
| `scripts/research/high_proximity364_history_fixed_audit_2026_09_05.py` | 10,283 | `B` | crypto portfolio-expansion screen | AST in/out 0/0; dynamic in/out 0/0; tests C/H 0/0; JSON 0; docs 0; closure no | `RETAIN` | Canonical final verifier, closure component, receipt compactor, or final artifact audit; retain as the minimal historical contract. |
| `scripts/research/high_proximity364_history_fixed_runner_2026_09_05.py` | 7,875 | `C` | crypto portfolio-expansion screen | AST in/out 0/2; dynamic in/out 1/3; tests C/H 0/1; JSON 1; docs 0; closure no | `RETAIN` | Pinned or documented historical research implementation retained for provenance, evidence replay, or a non-redundant campaign boundary. |
| `scripts/research/high_proximity364_trace_2026_09_05.py` | 17,010 | `B` | crypto portfolio-expansion screen | AST in/out 0/1; dynamic in/out 0/1; tests C/H 0/1; JSON 0; docs 1; closure no | `RETAIN` | Canonical final verifier, closure component, receipt compactor, or final artifact audit; retain as the minimal historical contract. |
| `scripts/research/immediate_candidates_economic_2026_09_09.py` | 36,976 | `C` | H/K/X strategy queue closure | AST in/out 0/0; dynamic in/out 1/0; tests C/H 0/0; JSON 0; docs 0; closure no | `RETAIN` | Pinned or documented historical research implementation retained for provenance, evidence replay, or a non-redundant campaign boundary. |
| `scripts/research/k11_signed_flow_candidate_2026_09_08.py` | 17,325 | `B` | crypto K01-K12 / crypto research | AST in/out 1/1; dynamic in/out 1/0; tests C/H 0/2; JSON 1; docs 0; closure yes | `RETAIN` | Canonical final verifier, closure component, receipt compactor, or final artifact audit; retain as the minimal historical contract. |
| `scripts/research/normalize_equity_h06_form4_2026_09_09.py` | 28,215 | `B` | repository/operational research support | AST in/out 0/0; dynamic in/out 2/0; tests C/H 0/1; JSON 2; docs 0; closure yes | `RETAIN` | Canonical final verifier, closure component, receipt compactor, or final artifact audit; retain as the minimal historical contract. |
| `scripts/research/normalize_equity_sec_fundamentals_pilot_2026_09_07.py` | 12,591 | `C` | repository/operational research support | AST in/out 0/0; dynamic in/out 0/0; tests C/H 0/0; JSON 2; docs 0; closure no | `RETAIN` | Pinned or documented historical research implementation retained for provenance, evidence replay, or a non-redundant campaign boundary. |
| `scripts/research/normalize_equity_secondary_h08_h15_2026_09_09.py` | 37,371 | `B` | repository/operational research support | AST in/out 0/0; dynamic in/out 2/0; tests C/H 0/1; JSON 2; docs 0; closure yes | `RETAIN` | Canonical final verifier, closure component, receipt compactor, or final artifact audit; retain as the minimal historical contract. |
| `scripts/research/normalize_h11_nport_snapshot_2026_09_09.py` | 16,250 | `B` | repository/operational research support | AST in/out 0/0; dynamic in/out 3/1; tests C/H 0/1; JSON 1; docs 0; closure yes | `RETAIN` | Canonical final verifier, closure component, receipt compactor, or final artifact audit; retain as the minimal historical contract. |
| `scripts/research/portfolio_expansion_crypto_diagnostic_2026_09_05.py` | 7,722 | `C` | crypto portfolio-expansion screen | AST in/out 0/0; dynamic in/out 0/0; tests C/H 0/0; JSON 4; docs 1; closure no | `RETAIN` | Pinned or documented historical research implementation retained for provenance, evidence replay, or a non-redundant campaign boundary. |
| `scripts/research/portfolio_expansion_crypto_screen_2026_09.py` | 20,126 | `C` | crypto portfolio-expansion screen | AST in/out 3/2; dynamic in/out 3/3; tests C/H 0/3; JSON 7; docs 5; closure no | `RETAIN` | Pinned or documented historical research implementation retained for provenance, evidence replay, or a non-redundant campaign boundary. |
| `scripts/research/portfolio_expansion_crypto_screen_analysis_2026_09_05.py` | 11,756 | `B` | crypto portfolio-expansion screen | AST in/out 0/0; dynamic in/out 0/0; tests C/H 0/0; JSON 0; docs 0; closure no | `RETAIN` | Canonical final verifier, closure component, receipt compactor, or final artifact audit; retain as the minimal historical contract. |
| `scripts/research/probe_crossasset_x01_alternatives_2026_09_08.py` | 2,800 | `F` | cross-asset X01-X06 | AST in/out 0/0; dynamic in/out 0/0; tests C/H 0/0; JSON 0; docs 0; closure no | `DELETE_REDUNDANT` | Closed X-route probe/diagnostic runner has no retained test, pin, importer, or canonical closure dependency; closure components remain. |
| `scripts/research/r46_preflight_2026_09_10.py` | 24,311 | `A` | R46 treasury/market-data current research | AST in/out 0/1; dynamic in/out 0/0; tests C/H 2/0; JSON 1; docs 0; closure no | `RETAIN` | Current allowlisted research or its direct current execution/parity contract; retain active path and dependencies. |
| `scripts/research/r46_treasury_auction_readiness_2026_09_10.py` | 50,316 | `A` | R46 treasury/market-data current research | AST in/out 6/1; dynamic in/out 1/0; tests C/H 4/0; JSON 1; docs 0; closure no | `RETAIN` | Current allowlisted research or its direct current execution/parity contract; retain active path and dependencies. |
| `scripts/research/r46_treasury_auction_unwind_tlt_v2_2026_09_10.py` | 264,680 | `A` | R46 treasury/market-data current research | AST in/out 2/5; dynamic in/out 3/2; tests C/H 2/0; JSON 3; docs 1; closure no | `RETAIN` | Current allowlisted research or its direct current execution/parity contract; retain active path and dependencies. |
| `scripts/research/r46_v2_preflight_2026_09_10.py` | 135,343 | `A` | R46 treasury/market-data current research | AST in/out 2/4; dynamic in/out 2/2; tests C/H 3/0; JSON 2; docs 1; closure no | `RETAIN` | Current allowlisted research or its direct current execution/parity contract; retain active path and dependencies. |
| `scripts/research/r46_v3_oracle_2026_09_11.py` | 37,288 | `A` | R46 treasury/market-data current research | AST in/out 1/0; dynamic in/out 2/0; tests C/H 2/0; JSON 2; docs 0; closure no | `RETAIN` | Current allowlisted research or its direct current execution/parity contract; retain active path and dependencies. |
| `scripts/research/refresh_h14_current_stage_semantics_2026_09_09.py` | 8,457 | `F` | equity H14 | AST in/out 0/0; dynamic in/out 0/0; tests C/H 0/0; JSON 0; docs 0; closure no | `DELETE_REDUNDANT` | Superseded H14 stage-semantics refresh has no retained test, pin, importer, or final-verifier dependency. |
| `scripts/research/regenerate_equity_h01_h05_r12_with_sec_evidence_2026_09_09.py` | 7,831 | `F` | equity H01-H16 causal/data gate | AST in/out 0/0; dynamic in/out 0/2; tests C/H 0/0; JSON 0; docs 0; closure no | `DELETE_REDUNDANT` | Per-revision regeneration runner has no retained consumer or immutable source pin; canonical filing verifier and evidence remain. |
| `scripts/research/regenerate_equity_h01_h05_r13_with_sec_evidence_2026_09_09.py` | 8,520 | `F` | equity H01-H16 causal/data gate | AST in/out 0/0; dynamic in/out 0/2; tests C/H 0/0; JSON 0; docs 0; closure no | `DELETE_REDUNDANT` | Per-revision regeneration runner has no retained consumer or immutable source pin; canonical filing verifier and evidence remain. |
| `scripts/research/regenerate_equity_h01_h05_r14_with_sec_evidence_2026_09_09.py` | 8,520 | `F` | equity H01-H16 causal/data gate | AST in/out 0/0; dynamic in/out 0/2; tests C/H 0/0; JSON 0; docs 0; closure no | `DELETE_REDUNDANT` | Per-revision regeneration runner has no retained consumer or immutable source pin; canonical filing verifier and evidence remain. |
| `scripts/research/regenerate_equity_h01_h05_r15_with_sec_evidence_2026_09_09.py` | 9,176 | `F` | equity H01-H16 causal/data gate | AST in/out 0/0; dynamic in/out 0/2; tests C/H 0/0; JSON 0; docs 0; closure no | `DELETE_REDUNDANT` | Per-revision regeneration runner has no retained consumer or immutable source pin; canonical filing verifier and evidence remain. |
| `scripts/research/regenerate_equity_h01_h05_r16_with_sec_evidence_2026_09_09.py` | 9,176 | `F` | equity H01-H16 causal/data gate | AST in/out 0/0; dynamic in/out 0/2; tests C/H 0/0; JSON 0; docs 0; closure no | `DELETE_REDUNDANT` | Per-revision regeneration runner has no retained consumer or immutable source pin; canonical filing verifier and evidence remain. |
| `scripts/research/regenerate_equity_h01_h05_r17_with_sec_evidence_2026_09_09.py` | 9,176 | `F` | equity H01-H16 causal/data gate | AST in/out 0/0; dynamic in/out 0/2; tests C/H 0/0; JSON 0; docs 0; closure no | `DELETE_REDUNDANT` | Per-revision regeneration runner has no retained consumer or immutable source pin; canonical filing verifier and evidence remain. |
| `scripts/research/regenerate_equity_h01_h05_r18_with_sec_evidence_2026_09_09.py` | 9,176 | `F` | equity H01-H16 causal/data gate | AST in/out 0/0; dynamic in/out 0/2; tests C/H 0/0; JSON 0; docs 0; closure no | `DELETE_REDUNDANT` | Per-revision regeneration runner has no retained consumer or immutable source pin; canonical filing verifier and evidence remain. |
| `scripts/research/run_equity_h14c_posthoc_2026_09_09.py` | 21,131 | `C` | equity H14 | AST in/out 1/1; dynamic in/out 1/1; tests C/H 0/1; JSON 0; docs 0; closure no | `RETAIN` | Pinned or documented historical research implementation retained for provenance, evidence replay, or a non-redundant campaign boundary. |
| `scripts/research/run_equity_local_diagnostics_2026_09_09.py` | 34,382 | `C` | repository/operational research support | AST in/out 0/1; dynamic in/out 1/1; tests C/H 0/2; JSON 0; docs 0; closure no | `RETAIN` | Pinned or documented historical research implementation retained for provenance, evidence replay, or a non-redundant campaign boundary. |
| `scripts/research/run_v3_canonical_validation.py` | 1,372 | `B` | V3/current validation infrastructure | AST in/out 0/0; dynamic in/out 1/2; tests C/H 0/0; JSON 0; docs 3; closure no | `RETAIN` | Canonical final verifier, closure component, receipt compactor, or final artifact audit; retain as the minimal historical contract. |
| `scripts/research/v3_baseline_no_persist_verify_2026_09_08.py` | 1,475 | `B` | V3/current validation infrastructure | AST in/out 0/0; dynamic in/out 0/0; tests C/H 0/0; JSON 0; docs 1; closure no | `RETAIN` | Canonical final verifier, closure component, receipt compactor, or final artifact audit; retain as the minimal historical contract. |
| `scripts/research/validate_r46_holdout_custody_2026_09_10.py` | 26,872 | `A` | R46 treasury/market-data current research | AST in/out 0/0; dynamic in/out 1/0; tests C/H 1/0; JSON 2; docs 1; closure no | `RETAIN` | Current allowlisted research or its direct current execution/parity contract; retain active path and dependencies. |
| `scripts/research/verify_all_34_closure_2026_09_09.py` | 41,157 | `B` | repository/operational research support | AST in/out 1/1; dynamic in/out 0/11; tests C/H 0/1; JSON 0; docs 1; closure yes | `RETAIN` | Canonical final verifier, closure component, receipt compactor, or final artifact audit; retain as the minimal historical contract. |
| `scripts/research/verify_equity_h01_h05_sec_filing_evidence_2026_09_09.py` | 22,642 | `B` | equity H01-H16 causal/data gate | AST in/out 0/0; dynamic in/out 8/0; tests C/H 0/0; JSON 14; docs 7; closure yes | `RETAIN` | Canonical final verifier, closure component, receipt compactor, or final artifact audit; retain as the minimal historical contract. |
| `scripts/research/verify_immediate_candidates_economic_2026_09_09.py` | 4,438 | `B` | H/K/X strategy queue closure | AST in/out 0/0; dynamic in/out 1/0; tests C/H 0/1; JSON 0; docs 0; closure no | `RETAIN` | Canonical final verifier, closure component, receipt compactor, or final artifact audit; retain as the minimal historical contract. |
| `scripts/research/verify_immediate_candidates_invalidation_2026_09_10.py` | 4,763 | `B` | H/K/X strategy queue closure | AST in/out 0/1; dynamic in/out 0/0; tests C/H 0/1; JSON 0; docs 0; closure no | `RETAIN` | Canonical final verifier, closure component, receipt compactor, or final artifact audit; retain as the minimal historical contract. |
| `scripts/research/verify_r46_treasury_auction_unwind_tlt_v2_2026_09_10.py` | 267,968 | `A` | R46 treasury/market-data current research | AST in/out 4/5; dynamic in/out 4/7; tests C/H 2/0; JSON 3; docs 1; closure no | `RETAIN` | Current allowlisted research or its direct current execution/parity contract; retain active path and dependencies. |
| `scripts/research/verify_strategy_queue_zero_wait_decision_2026_09_09.py` | 6,252 | `F` | H/K/X strategy queue closure | AST in/out 0/1; dynamic in/out 0/0; tests C/H 0/0; JSON 0; docs 0; closure no | `DELETE_REDUNDANT` | Intermediate zero-wait builder/verifier has no retained test, pin, or closure dependency; later final decision/verifier remains. |
| `scripts/research/verify_zero_wait_final_decision_2026_09_10.py` | 5,687 | `B` | H/K/X strategy queue closure | AST in/out 0/1; dynamic in/out 0/0; tests C/H 0/1; JSON 0; docs 0; closure no | `RETAIN` | Canonical final verifier, closure component, receipt compactor, or final artifact audit; retain as the minimal historical contract. |
| `scripts/research/verify_zero_wait_overlay_economic_2026_09_10.py` | 6,998 | `B` | H/K/X strategy queue closure | AST in/out 0/0; dynamic in/out 1/0; tests C/H 0/0; JSON 0; docs 0; closure no | `RETAIN` | Canonical final verifier, closure component, receipt compactor, or final artifact audit; retain as the minimal historical contract. |
| `scripts/research/zero_wait_overlay_economic_2026_09_10.py` | 20,576 | `C` | H/K/X strategy queue closure | AST in/out 0/2; dynamic in/out 1/0; tests C/H 0/0; JSON 0; docs 0; closure no | `RETAIN` | Pinned or documented historical research implementation retained for provenance, evidence replay, or a non-redundant campaign boundary. |
| `scripts/research_candidate_observation_parity.py` | 28,099 | `C` | repository/operational research support | AST in/out 0/1; dynamic in/out 1/0; tests C/H 0/0; JSON 4; docs 1; closure no | `RETAIN` | Pinned or documented historical research implementation retained for provenance, evidence replay, or a non-redundant campaign boundary. |
| `scripts/research_crypto_candidate_tournament.py` | 68,446 | `A` | crypto K01-K12 / crypto research | AST in/out 1/1; dynamic in/out 0/0; tests C/H 1/0; JSON 10; docs 1; closure no | `RETAIN` | Current allowlisted research or its direct current execution/parity contract; retain active path and dependencies. |
| `scripts/research_crypto_residual_low_variance.py` | 69,964 | `C` | crypto K01-K12 / crypto research | AST in/out 0/1; dynamic in/out 1/1; tests C/H 0/0; JSON 1; docs 2; closure no | `RETAIN` | Pinned or documented historical research implementation retained for provenance, evidence replay, or a non-redundant campaign boundary. |
| `scripts/research_equity_alpha_optimization.py` | 52,645 | `A` | repository/operational research support | AST in/out 0/1; dynamic in/out 2/1; tests C/H 1/0; JSON 1; docs 2; closure no; duplicate group | `RETAIN` | Current allowlisted research or its direct current execution/parity contract; retain active path and dependencies. |
| `scripts/research_equity_m3_candidate_ocp.py` | 112,644 | `C` | equity M3 / candidate OCP | AST in/out 0/6; dynamic in/out 2/3; tests C/H 0/2; JSON 7; docs 1; closure no | `RETAIN` | Pinned or documented historical research implementation retained for provenance, evidence replay, or a non-redundant campaign boundary. |
| `scripts/research_equity_m3_data_gate.py` | 235,736 | `C` | equity M3 / candidate OCP | AST in/out 2/1; dynamic in/out 2/5; tests C/H 0/1; JSON 7; docs 2; closure no | `RETAIN` | Pinned or documented historical research implementation retained for provenance, evidence replay, or a non-redundant campaign boundary. |
| `scripts/research_equity_m3_data_gate_r5.py` | 115,200 | `C` | equity M3 / candidate OCP | AST in/out 1/3; dynamic in/out 2/4; tests C/H 0/1; JSON 5; docs 1; closure no | `RETAIN` | Pinned or documented historical research implementation retained for provenance, evidence replay, or a non-redundant campaign boundary. |
| `scripts/research_equity_m3_economic.py` | 173,984 | `C` | equity M3 / candidate OCP | AST in/out 2/3; dynamic in/out 2/8; tests C/H 0/2; JSON 4; docs 1; closure no | `RETAIN` | Pinned or documented historical research implementation retained for provenance, evidence replay, or a non-redundant campaign boundary. |
| `scripts/research_equity_m3_m4.py` | 69,206 | `C` | equity M3 / candidate OCP | AST in/out 3/4; dynamic in/out 6/5; tests C/H 0/4; JSON 15; docs 1; closure no | `RETAIN` | Pinned or documented historical research implementation retained for provenance, evidence replay, or a non-redundant campaign boundary. |
| `scripts/research_h14_turn_of_month.py` | 27,476 | `C` | equity H14 | AST in/out 1/0; dynamic in/out 0/0; tests C/H 0/0; JSON 6; docs 2; closure no | `RETAIN` | Pinned or documented historical research implementation retained for provenance, evidence replay, or a non-redundant campaign boundary. |
| `scripts/research_helpers.py` | 10,349 | `D` | repository/operational research support | AST in/out 19/0; dynamic in/out 0/0; tests C/H 0/0; JSON 0; docs 4; closure no; duplicate group | `RETAIN` | Reusable validation/acquisition/infrastructure boundary used by current or pinned research; retain without promotion semantics. |
| `scripts/research_v3_current_parity_control.py` | 6,370 | `A` | V3/current validation infrastructure | AST in/out 2/1; dynamic in/out 1/0; tests C/H 1/1; JSON 2; docs 4; closure no | `RETAIN` | Current allowlisted research or its direct current execution/parity contract; retain active path and dependencies. |
| `scripts/research_v3_equity_followups.py` | 31,333 | `C` | V3/current validation infrastructure | AST in/out 0/1; dynamic in/out 0/1; tests C/H 0/0; JSON 1; docs 2; closure no | `RETAIN` | Pinned or documented historical research implementation retained for provenance, evidence replay, or a non-redundant campaign boundary. |
| `scripts/research_v3_equity_roadmap.py` | 65,781 | `D` | V3/current validation infrastructure | AST in/out 7/2; dynamic in/out 4/1; tests C/H 1/1; JSON 7; docs 5; closure no | `RETAIN` | Reusable validation/acquisition/infrastructure boundary used by current or pinned research; retain without promotion semantics. |
| `scripts/research_v3_exit_latency.py` | 76,279 | `C` | V3/current validation infrastructure | AST in/out 1/1; dynamic in/out 0/0; tests C/H 0/0; JSON 1; docs 2; closure no; duplicate group | `RETAIN` | Pinned or documented historical research implementation retained for provenance, evidence replay, or a non-redundant campaign boundary. |
| `scripts/research_v3_position_risk.py` | 81,526 | `F` | V3/current validation infrastructure | AST in/out 0/1; dynamic in/out 0/0; tests C/H 0/0; JSON 0; docs 2; closure no; duplicate group | `DELETE_REDUNDANT` | Byte-identical to the retained August archive copy; no current importer, manifest/receipt pin, or verifier dependency remains. |
| `scripts/run_crypto_universe_scan.py` | 1,465 | `G` | repository/operational research support | AST in/out 0/0; dynamic in/out 0/0; tests C/H 0/0; JSON 0; docs 1; closure no | `RETAIN` | Operational/PAPER/reporting utility; outside research-script deletion and left untouched. |
| `scripts/run_paper_chaos.py` | 1,450 | `G` | repository/operational research support | AST in/out 0/0; dynamic in/out 0/0; tests C/H 1/0; JSON 0; docs 4; closure no | `RETAIN` | Operational/PAPER/reporting utility; outside research-script deletion and left untouched. |
| `scripts/run_symmetric_v5_validation.py` | 36,429 | `D` | V3/current validation infrastructure | AST in/out 4/0; dynamic in/out 4/0; tests C/H 2/1; JSON 4; docs 3; closure no | `RETAIN` | Reusable validation/acquisition/infrastructure boundary used by current or pinned research; retain without promotion semantics. |
| `scripts/scan_equity_universe.py` | 1,323 | `G` | repository/operational research support | AST in/out 0/0; dynamic in/out 0/0; tests C/H 0/0; JSON 0; docs 1; closure no | `RETAIN` | Operational/PAPER/reporting utility; outside research-script deletion and left untouched. |

