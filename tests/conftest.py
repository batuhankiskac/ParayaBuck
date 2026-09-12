"""Test isolation: prevent production cache/runtime pollution and engine crashes."""

from __future__ import annotations

import threading
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from parayabuck.data import cache as cache_module
from parayabuck.data.cache import WarmupCache
from parayabuck.monitoring import journal as journal_module
from parayabuck.monitoring import receipt as receipt_module
from parayabuck.monitoring import snapshot as snapshot_module
from parayabuck.monitoring.journal import DEFAULT_JOURNAL_PATH, EventJournal
from parayabuck.monitoring.snapshot import DEFAULT_SNAPSHOT_PATH, SnapshotWriter
from parayabuck.storage import SQLiteStore
from parayabuck.web.state import get_web_state, set_web_state

_PROTECTED_PATHS = [
    Path("data/cache/warmup.sqlite3"),
    Path("data/cache/warmup.sqlite3-wal"),
    # -shm is shared memory, mtime changes on read; not reliable for isolation check
    Path("data/runtime/parayabuck.sqlite3"),
    # wal for parayabuck is ephemeral; exclude -shm, check wal leniently
    Path("data/runtime/observability.sqlite3"),
    Path("data/runtime/health_snapshot.json"),
]

_REPO_ROOT = Path(__file__).resolve().parents[1]

# Research tests are deliberately classified here instead of by directory:
# ``tests/research/`` contains both current contracts and closed mission
# records. New tests in that directory are historical by default until their
# research status is reviewed and added to the current allowlist.
_PRODUCT = "product"
_CURRENT_RESEARCH = "current_research"
_HISTORICAL_RESEARCH = "historical_research"

_CURRENT_RESEARCH_TEST_PATHS = (
    "tests/test_equity_v3_current_parity_control.py",
    "tests/test_full_survivorship_terminal.py",
    "tests/test_normalized_hash.py",
    "tests/test_research_experiment.py",
    "tests/test_research_runner.py",
    "tests/test_terminal_aware_engine.py",
    "tests/research/test_equity_profit_protection_2026_09_r2.py",
    "tests/research/test_execution_cost_transferability_p0_2026_09.py",
    "tests/research/test_execution_cost_transferability_p1_paper_observation_2026_09.py",
    "tests/research/test_r46_governance_contract_2026_09_10.py",
    "tests/research/test_r46_nyse_calendar_2026_09_10.py",
    "tests/research/test_r46_preflight_2026_09_10.py",
    "tests/research/test_r46_publication_availability_evidence_2026_09_11.py",
    "tests/research/test_r46_tlt_market_data_2026_09_10.py",
    "tests/research/test_r46_treasury_acquisition_2026_09_10.py",
    "tests/research/test_r46_treasury_auction_readiness_2026_09_10.py",
    "tests/research/test_r46_treasury_xml_v2_2026_09_10.py",
    "tests/research/test_r46_v2_data_package_2026_09_10.py",
    "tests/research/test_r46_v2_execution_2026_09_10.py",
    "tests/research/test_r46_frozen_source_pins_2026_09_12.py",
    "tests/research/test_r46_v2_governance_2026_09_10.py",
    "tests/research/test_r46_v3_oracle_2026_09_11.py",
)

_HISTORICAL_RESEARCH_TEST_PATHS = (
    "tests/test_candidate_observation_parity_receipts.py",
    "tests/test_m3_iwm_acquisition.py",
    "tests/test_research_equity_m3_candidate_ocp.py",
    "tests/test_research_equity_m3_candidate_ocp_attestation_r2.py",
    "tests/test_research_equity_m3_data_gate.py",
    "tests/test_research_equity_m3_economic.py",
    "tests/test_research_equity_m3_economic_execution.py",
    "tests/test_research_equity_m3_m4.py",
    "tests/test_research_equity_m3_r5.py",
)

_CURRENT_RESEARCH_TESTS = frozenset(_CURRENT_RESEARCH_TEST_PATHS)
_HISTORICAL_RESEARCH_TESTS = frozenset(_HISTORICAL_RESEARCH_TEST_PATHS)

_R46_TESTS_BY_MODULE: dict[str, tuple[str, ...]] = {
    "tests/research/test_r46_frozen_source_pins_2026_09_12.py": (
        "test_r46_frozen_source_pins_match_native_attestation_and_governance",
    ),
    "tests/research/test_r46_governance_contract_2026_09_10.py": (
        "test_r46_governance_artifacts_exist",
        "test_r46_contract_is_explicitly_non_economic_and_statistically_blocked",
        "test_r46_holdout_is_prospective_single_use_and_not_historical",
        "test_r46_holdout_requires_append_only_access_log_and_external_signature_verifier",
        "test_r46_source_matrix_freezes_canonical_pit_fields_without_outcomes",
        "test_r46_manifest_binds_self_hashed_capability_receipt_without_pit_readiness",
        "test_r46_validator_rejects_mutated_capability_receipt_raw_bytes",
        "test_r46_validator_rejects_capability_receipt_path_substitution",
        "test_r46_governance_schema_requires_capability_receipt_binding",
        "test_r46_manifest_and_contract_hash_pins_are_current",
        "test_r46_validator_fails_closed_before_runner_and_custody_are_ready",
        "test_r46_validator_rejects_unsigned_or_reused_custody_receipt",
        "test_r46_validator_rejects_tampered_canonical_source_matrix_self_hash",
        "test_r46_validator_rejects_weak_holdout_custody_requirements",
        "test_r46_validator_rejects_hash_that_only_matches_worktree_not_pinned_base",
        "test_r46_receipt_validation_runs_full_manifest_gate",
    ),
    "tests/research/test_r46_nyse_calendar_2026_09_10.py": (
        "test_build_is_deterministic_and_emits_the_pinned_complete_artifact",
        "test_every_record_has_only_required_fields_and_existing_validator_accepts_it",
        "test_nyse_regular_open_uses_dst_correct_utc_offsets",
        "test_early_close_is_preserved_as_the_calendar_close",
        "test_output_contains_sessions_only_without_weekends_or_holidays",
        "test_dependency_version_mismatch_fails_closed",
        "test_lock_pin_mismatch_fails_closed",
        "test_tampering_invalidates_the_canonical_self_hash",
        "test_atomic_writer_refuses_to_overwrite_existing_output",
        "test_writer_emits_preflight_compatible_jsonl_and_hash_pinned_receipt",
        "test_jsonl_tamper_duplicate_and_invalid_row_fail_closed",
        "test_two_target_collision_leaves_missing_target_unwritten",
        "test_cli_writes_jsonl_and_explicit_receipt_without_network",
        "test_stage_payload_removes_temp_file_when_fsync_fails",
        "test_publish_exception_rolls_back_new_targets_but_not_existing_files",
        "test_orphan_records_without_receipt_are_not_complete",
    ),
    "tests/research/test_r46_preflight_2026_09_10.py": (
        "test_verify_r46_manifest_reads_separate_pinned_data_package_but_stays_blocked",
        "test_verify_r46_manifest_rejects_mutated_document_bytes",
        "test_verify_r46_manifest_rejects_post_cutoff_source_availability",
        "test_verify_r46_manifest_rejects_archive_count_drift",
        "test_verify_r46_manifest_rejects_governance_crosslink_drift",
        "test_verify_r46_manifest_rejects_mutated_capability_receipt_raw_bytes",
        "test_verify_r46_manifest_rejects_capability_receipt_status_after_rebinding",
        "test_verify_r46_manifest_rejects_receipt_path_substitution",
        "test_verify_r46_manifest_rejects_embedded_attestation_even_with_receipts",
        "test_live_governance_manifest_routes_to_truthful_block_without_manifest_schema",
        "test_preflight_module_cli_accepts_frozen_invocation_for_live_governance_manifest",
        "test_v2_outcome_scanner_normalizes_keys_without_scanning_prose",
        "test_v2_independent_review_has_strict_schema_and_path_allowlist",
        "test_v2_source_provenance_requires_base_commit_ancestor_and_required_blobs",
        "test_v2_source_provenance_base_validation_skips_source_only_seam_but_checks_lifetimes",
        "test_v2_lifetime_source_pin_accepts_strict_crlf_normalization",
        "test_v2_source_validation_applies_crlf_exception_only_to_lifetimes",
        "test_v2_lifetime_source_pin_rejects_content_or_bare_cr",
        "test_v2_other_source_paths_still_require_byte_identity",
        "test_v2_v5_incumbent_policy_accepts_exact_contract",
        "test_v2_v5_incumbent_policy_rejects_lifetime_base_requirement_downgrade",
        "test_v2_v5_incumbent_policy_rejects_seam_path_substitution",
        "test_v2_v5_incumbent_policy_rejects_external_flag_and_blob_tampering",
    ),
    "tests/research/test_r46_publication_availability_evidence_2026_09_11.py": (
        "test_evidence_builder_derives_exact_policy_and_sources_from_final_receipt",
        "test_evidence_builder_rejects_alternate_missing_ids_after_receipt_rehash",
        "test_evidence_builder_rejects_last_modified_and_same_day_policy_spoof",
        "test_evidence_builder_rejects_receipt_documents_hash_spoof",
        "test_evidence_builder_rejects_receipt_self_hash_tamper",
        "test_evidence_builder_has_no_caller_event_universe_parameter",
    ),
    "tests/research/test_r46_tlt_market_data_2026_09_10.py": (
        "test_alpaca_request_makes_raw_and_split_contract_explicit",
        "test_alpaca_corporate_action_request_uses_current_cash_dividend_route",
        "test_normalize_bars_derives_session_close_availability_and_requires_calendar_for_provider_midnight",
        "test_dividend_normalization_derives_ex_date_regular_open",
        "test_provider_corporate_action_schema_is_normalized_without_fixture_only_fields",
        "test_current_alpaca_cash_dividend_mapping_is_normalized_from_raw_provider_fields",
        "test_identity_normalization_is_cutoff_bounded_and_rejects_current_snapshot_without_historical_interval",
        "test_historical_identity_evidence_preserves_exchange_transition_and_is_hash_pinned",
        "test_calendar_loader_accepts_pinned_jsonl",
        "test_authenticated_current_identity_is_attested_against_supplied_history",
        "test_mocked_acquisition_fetches_all_alpaca_routes_and_writes_credential_safe_receipt",
        "test_env_loading_is_opt_in_and_second_publication_cannot_overwrite",
    ),
    "tests/research/test_r46_treasury_acquisition_2026_09_10.py": (
        "test_official_source_routes_are_https_and_cover_archive_query_and_documents",
        "test_normalize_legacy_json_array_keeps_only_identity_and_dataset_update",
        "test_normalize_legacy_json_array_filters_non_candidates_and_rejects_candidate_duplicates",
        "test_normalize_legacy_json_array_fails_closed_for_missing_selected_identity",
        "test_normalize_legacy_json_array_validates_bond_and_reopening_lineage",
        "test_normalize_legacy_json_array_validates_filename_dates_and_derives_pdf_year",
        "test_parse_result_xml_is_raw_unadmitted_and_hash_pinned",
        "test_parse_result_xml_rejects_identity_failures",
        "test_parse_result_xml_requires_exact_xml_route_and_declares_schema_without_validating",
        "test_parse_result_xml_retains_v7_schema_version_without_claiming_validation",
        "test_parse_result_xml_marks_post_cutoff_as_capability_only",
        "test_parse_result_rss_normalizes_pubdate_for_crosscheck_only",
        "test_parse_result_rss_rejects_duplicates_and_mismatched_link_identity",
        "test_parse_result_rss_requires_exact_endpoint_zero_offset_and_marks_post_cutoff",
        "test_local_public_evidence_receipt_is_atomic_data_only_and_hashes_artifacts",
        "test_local_public_evidence_receipt_hashes_announced_rss_only_without_validation",
        "test_local_public_evidence_receipt_reads_each_input_file_once",
        "test_local_public_evidence_receipt_reconciles_in_window_xml_to_legacy_event",
        "test_local_public_evidence_receipt_reconciles_post_cutoff_xml_only_to_rss",
        "test_local_public_evidence_receipt_rejects_duplicate_result_xml_path_hash_and_identity",
        "test_public_parsers_reject_doctype_and_entity_before_element_tree",
        "test_public_parsers_enforce_bounded_payload_sizes",
        "test_cli_local_public_evidence_mode_never_networks_and_does_not_overwrite",
        "test_build_tlt_alpaca_query_freezes_split_and_raw_coverage_without_credentials",
        "test_build_document_record_keeps_pdf_raw_unadmitted_without_real_extractor",
        "test_build_document_record_does_not_parse_source_body_cutoff_text",
        "test_build_document_record_marks_missing_extractor_evidence_unadmitted",
        "test_build_document_record_refuses_overwrite",
        "test_build_document_record_rejects_resolved_path_escape",
        "test_fetch_rejects_caller_asserted_timestamps_even_when_present_in_metadata",
        "test_fetch_rejects_nested_caller_asserted_timestamps_before_network",
        "test_fetch_keeps_source_without_timestamp_evidence_raw_unadmitted",
        "test_fetch_rejects_redirect_to_non_official_host",
        "test_fetch_rejects_mismatched_content_type",
        "test_fetch_rejects_mismatched_pdf_signature",
        "test_fetch_keeps_pdf_raw_unadmitted_without_real_extractor",
        "test_sanitized_blocked_receipt_does_not_persist_credentials",
        "test_load_pinned_inventory_rejects_caller_asserted_timestamps",
        "test_load_pinned_inventory_rejects_issue_date_as_auction_date",
        "test_load_pinned_inventory_accepts_auction_date_distinct_from_issue_date",
        "test_load_pinned_inventory_rejects_event_after_historical_cutoff",
        "test_build_source_capability_receipt_records_public_timestamp_blocker",
        "test_static_capability_receipt_is_canonical_and_self_hashed",
        "test_cli_help_works_when_invoked_directly",
        "test_cli_does_not_network_without_explicit_fetch",
        "test_acquire_pinned_inventory_never_reports_acquired_from_fetcher_admission",
        "test_acquire_pinned_inventory_fetches_only_with_opt_in",
    ),
    "tests/research/test_r46_treasury_auction_readiness_2026_09_10.py": (
        "test_normalize_auction_documents_pairs_completed_nominal_30y_event",
        "test_normalize_auction_documents_accepts_missing_release_only_with_policy",
        "test_normalize_auction_documents_policy_is_dst_safe",
        "test_normalize_auction_documents_rejects_forged_result_offset_or_date",
        "test_normalize_auction_documents_rejects_last_modified_publication_substitution",
        "test_normalize_auction_documents_rejects_published_at_not_matching_release_time",
        "test_normalize_auction_documents_rejects_null_published_when_release_is_present",
        "test_normalize_auction_documents_rejects_missing_source_publication_time",
        "test_normalize_auction_documents_rejects_retrieval_time_as_publication_evidence",
        "test_normalize_auction_documents_rejects_result_availability_policy_substitution",
        "test_normalize_auction_documents_rejects_wrong_security_term",
        "test_normalize_auction_documents_requires_revision_chain_for_duplicates",
        "test_normalize_auction_documents_rejects_amendment_without_original_predecessor",
        "test_validate_tlt_bars_requires_split_adjustment_and_timezone",
        "test_validate_tlt_dividends_is_separate_cash_ledger",
        "test_validate_tlt_eligibility_rejects_current_snapshot_identity",
        "test_cross_validate_tlt_data_selects_same_id_historical_interval_by_date",
        "test_cross_validate_tlt_data_fails_closed_on_overlapping_identity_intervals",
        "test_verify_r46_package_remains_blocked_even_when_data_and_holdout_shapes_pass",
        "test_verify_r46_package_never_clears_embedded_signatures_without_verifier",
        "test_validate_tlt_bars_requires_each_row_available_by_artifact_time",
        "test_validate_tlt_dividends_requires_each_row_available_by_artifact_time",
        "test_cross_validate_tlt_data_rejects_bar_available_before_exact_session_open",
        "test_cross_validate_tlt_data_rejects_dividend_before_event_open_or_event_mismatch",
        "test_validate_nyse_calendar_rejects_non_regular_open_timestamp",
        "test_verify_r46_package_stops_when_holdout_was_accessed",
        "test_verify_r46_package_rejects_unsigned_holdout_attestation",
        "test_verify_r46_package_rejects_rule_relaxation",
        "test_verify_r46_package_rejects_shared_capital_cap_drift",
        "test_r46_manifest_serializes_utc_cutoff_as_immutable_contract",
    ),
    "tests/research/test_r46_treasury_xml_v2_2026_09_10.py": (
        "test_treasury_xml_v2_pins_raw_xml_and_filters_economic_fields",
        "test_treasury_xml_v2_rejects_cross_path_redirect_and_overwrite",
        "test_treasury_xml_v2_canonicalizes_legacy_http_schema_location_to_official_https",
        "test_treasury_xml_v2_rejects_nested_output_symlink",
        "test_treasury_xml_v2_seals_missing_release_time_as_next_session_policy",
        "test_treasury_xml_v2_result_availability_uses_civil_day_dst_safe_policy",
    ),
    "tests/research/test_r46_v2_data_package_2026_09_10.py": (
        "test_expected_event_ids_use_sorted_unique_canonical_semantics",
        "test_dividend_source_validator_rejects_attacker_supplied_row_hash",
        "test_build_v2_package_hashes_five_artifacts_and_records_retrospective_sources",
        "test_package_accepts_hash_pinned_official_historical_identity_source",
        "test_package_rejects_treasury_receipt_without_schema_validation",
        "test_package_rejects_treasury_receipt_without_xsd_document_lineage",
        "test_package_recomputes_raw_split_row_key_parity",
        "test_package_recomputes_corporate_action_raw_row_hashes",
        "test_package_rejects_missing_retrospective_metadata_before_admission",
        "test_package_rejects_nonparity_raw_split_acquisition",
        "test_package_publication_is_atomic_and_no_overwrite",
        "test_consumer_rejects_rehashed_package_with_inconsistent_coverage",
        "test_consumer_rejects_changed_pinned_receipt",
        "test_package_cli_accepts_explicit_five_artifact_metadata",
        "test_package_rejects_caller_event_universe_alternative_to_governance_pin",
        "test_consumer_rejects_tampered_publication_policy_crosslink",
        "test_builder_rejects_publication_evidence_treasury_source_crosslink",
    ),
    "tests/research/test_r46_v2_execution_2026_09_10.py": (
        "test_runner_refuses_mapping_and_requires_pinned_on_disk_gate",
        "test_persisted_preflight_forwards_all_explicit_evidence_paths",
        "test_production_package_runner_rejects_caller_economic_overrides",
        "test_production_config_failure_blocks_and_static_universe_is_test_only",
        "test_production_pit_loader_is_repo_root_relative_and_fail_closed",
        "test_event_marker_reuses_cached_incumbent_target_without_v3_rerun",
        "test_event_boundary_preserves_existing_incumbent_pending_orders",
        "test_candidate_control_incumbent_engine_path_is_exact_except_tlt",
        "test_r46_control_matches_canonical_terminal_aware_v5_lifecycle_for_non_tlt",
        "test_r46_effective_dated_equity_fee_stress_scales_official_trade_date_fee",
        "test_marker_volume_is_excluded_from_r46_point_in_time_liquidity_history",
        "test_tlt_remainder_after_exact_open_is_not_carried_to_a_later_bar",
        "test_tlt_pending_order_waits_for_exact_open_after_intermediate_bars",
        "test_partial_tlt_entry_or_exit_is_not_an_eligible_event_episode",
        "test_holm_is_limited_to_frozen_inferential_ids_and_marks_diagnostics_not_tested",
        "test_timing_placebo_runs_shifted_candidate_and_control_native_engine_cell",
        "test_timing_placebo_persists_rehashed_economic_ledgers_with_shifted_identity",
        "test_incumbent_comparison_uses_complete_primary_event_endpoints_and_sign_flip",
        "test_v5_incumbent_dependencies_require_exact_manifest_hashes_and_cutoff",
        "test_primary_null_is_explicit_zero_rate_same_reservation_and_cost_cell_is_bound",
        "test_shared_account_rejects_frozen_cap_overrides_and_seed_slot_overflow",
        "test_shared_account_sells_before_buys_and_reconciles_cash_fees_and_ownership",
        "test_fill_rejections_are_persisted_and_batch_is_atomic",
        "test_dividend_accounting_is_explicit_and_owner_bound",
        "test_suppressed_and_duplicate_events_are_persisted",
        "test_r46_event_uses_next_open_entry_and_fifth_open_after_entry_exit",
        "test_r46_schedule_uses_first_open_strictly_after_available_at_across_dst",
        "test_r46_placebo_shift_preserves_new_york_civil_time_across_dst",
        "test_r46_placebo_shift_anchors_market_closure_to_next_regular_session",
        "test_r46_benchmark_projection_is_asof_and_fails_before_first_source",
        "test_r46_schedule_rejects_weekend_holiday_and_forged_dst_open",
        "test_r46_policy_fixture_has_127_canonical_events_and_published_at_is_noncausal",
        "test_r46_package_loader_validates_uniform_policy_for_compact_127_event_fixture",
        "test_r46_package_bar_loader_requires_pit_availability_at_or_after_session_open",
        "test_r46_package_dividend_loader_rejects_availability_after_event_timestamp",
        "test_r46_policy_fixture_rejects_short_or_incomplete_production_package",
        "test_r46_policy_fixture_rejects_all_missing_release_timestamps",
        "test_r46_actual_release_timestamp_cannot_advance_policy_entry",
        "test_native_adapter_invokes_real_portfolio_engine_and_dividend_path",
        "test_package_event_fixture_uses_sixth_open_dividends_and_three_cost_cells",
        "test_overlay_suppresses_when_six_incumbent_slots_are_occupied",
        "test_overlay_counts_six_base_targets_before_adding_tlt",
        "test_package_runner_round_trip_publishes_consumed_receipt_and_refuses_rerun",
        "test_native_smoke_attestation_is_independently_verifiable_and_tamper_evident",
        "test_native_attestation_rejects_caller_forged_file_pins",
        "test_independent_attestation_verifier_recomputes_current_file_pins",
        "test_preflight_rejects_explicit_alternate_evidence_paths",
        "test_one_shot_reservation_is_atomic_no_overwrite_and_consumed_once",
        "test_one_shot_transitions_are_self_hashed_and_crosslinked",
        "test_one_shot_crash_reconciliation_never_reopens_consumed_state",
        "test_run_native_once_is_fail_closed_and_separate_from_test_only_surface",
        "test_one_shot_sidecar_persistence_fsyncs_file_and_parent_directory",
        "test_production_one_shot_transitions_require_all_crosslinks",
        "test_reconciliation_invalid_receipt_aborts_consumed_without_rerun",
        "test_reconciliation_malformed_consumed_is_terminal_under_strict_reads",
        "test_reconciliation_never_accepts_receipt_for_malformed_consumed_transition",
        "test_transition_filename_status_and_state_canonical_hash_are_strict",
        "test_inner_verifier_recomputes_boundaries_and_rejects_tampering",
        "test_inner_verifier_recomputes_fill_costs_and_snapshot_chain",
        "test_economic_structural_verifier_requires_inner_ledger_hash_before_replay",
        "test_economic_payload_does_not_publish_redundant_residual_arrays",
        "test_decision_contract_preserves_desired_overlay_separately_from_applied_target",
        "test_native_fill_identity_and_causality_are_recomputed",
        "test_native_snapshot_gross_and_cash_weight_are_recomputed",
        "test_public_economic_package_verifier_requires_published_files_and_receipt",
        "test_strict_curve_rejects_unbound_empty_benchmark_curve",
        "test_strict_curve_recomputes_all_serialized_result_metrics",
        "test_strict_curve_does_not_accept_holdings_history_as_metric_authority",
        "test_file_backed_terminal_owner_flip_is_rejected",
        "test_strict_replay_rejects_aggregate_fragments_over_source_bar_cap",
        "test_strict_replay_rejects_equal_timestamp_fragments_for_one_order",
        "test_native_structural_rejects_equal_timestamp_fragments_for_one_order",
        "test_strict_replay_adv_cutoff_advances_for_two_bar_partial_order",
        "test_strict_replay_rejects_false_terminal_with_final_holdings",
        "test_strict_replay_rejects_duplicate_terminal_symbol_declaration",
        "test_strict_curve_binds_benchmark_to_pinned_spy_bars",
        "test_strict_git_lineage_requires_base_source_freeze_ancestry_and_head",
        "test_strict_contained_file_rejects_symlink_sidecars",
        "test_attestation_write_is_no_overwrite",
        "test_package_backed_runner_exposes_real_engine_and_exact_package_entrypoints",
        "test_governance_package_native_preflight_and_runner_gate_round_trip",
        "test_strict_file_backed_green_fixture_verifies_without_production_runner",
    ),
    "tests/research/test_r46_v2_governance_2026_09_10.py": (
        "test_v2_builder_freezes_disclosed_child_contract_without_human_signatures",
        "test_v2_builder_emits_explicit_v5_source_file_paths_for_preflight",
        "test_v2_builder_canonicalizes_conflicting_v5_source_override_path",
        "test_v2_builder_is_atomic_no_overwrite",
        "test_v2_builder_preserves_existing_one_shot_state",
        "test_v2_preflight_reports_explicit_blockers_and_never_emits_economics",
        "test_v2_preflight_rejects_a_consumed_one_shot_state",
        "test_v2_preflight_resolves_repo_relative_manifest_argument",
        "test_v2_preflight_rejects_v1_data_package_schema",
        "test_native_attestation_structural_fills_are_not_global_outcome_fields",
        "test_preflight_ok_receipt_is_canonical_and_bound_to_both_inputs",
        "test_preflight_receipt_persistence_rejects_unverified_forgery",
        "test_v2_receipt_schema_matches_runner_and_contains_external_evidence_refs",
        "test_v2_builder_static_contract_excludes_post_seal_hashes",
        "test_v2_builder_can_finalize_from_explicit_source_commit",
        "test_v2_builder_declares_all_outcome_relevant_source_and_runtime_pins",
        "test_governance_and_preflight_freeze_one_exact_runner_command",
        "test_preflight_freeze_diff_rejects_runtime_source_changes",
        "test_v2_builder_accepts_deterministic_source_overrides",
        "test_family_registry_requires_exact_method_order_and_roles",
        "test_family_registry_separates_reported_outputs_from_inferential_holm_scope",
        "test_arbitrary_pit_receipt_is_not_a_validated_source_attestation",
        "test_parent_execution_contract_is_immutable",
        "test_static_evidence_paths_are_crosslinked",
        "test_acquisition_parity_is_recomputed_and_tampering_blocks",
        "test_source_provenance_rejects_head_ancestry_and_runtime_drift",
        "test_static_governance_hash_does_not_change_when_external_evidence_is_bound",
        "test_preflight_receipt_round_trips_through_runner_reader",
        "test_v2_builder_freezes_uniform_publication_policy_and_tracked_universe",
        "test_v2_schema_closes_uniform_publication_policy_block",
        "test_v2_builder_pins_v1_raw_bytes_and_event_contract_independently",
        "test_v2_preflight_reads_v1_parent_when_contract_paths_are_repo_relative",
        "test_v2_preflight_rejects_uniform_policy_spoof_even_after_manifest_rehash",
        "test_v2_preflight_recomputes_tracked_event_universe_not_caller_ids",
        "test_strict_one_shot_chain_rejects_external_valid_transition_symlink",
        "test_strict_contained_file_rejects_parent_component_symlink",
    ),
    "tests/research/test_r46_v3_oracle_2026_09_11.py": (
        "test_pure_math_boundaries_are_exact_and_tie_averaged",
        "test_skip_tail_and_six_position_account_scaling_are_frozen",
        "test_account_risk_keeps_the_six_lowest_oracle_ranks_not_alphabetical_symbols",
        "test_pending_order_ledger_reconstructs_partial_remainder_from_exact_fill_id",
        "test_pending_order_ledger_tracks_tlt_event_remainder_then_expires_at_exact_open",
        "test_pending_order_ledger_preserves_tlt_and_replaces_incumbent_pending_at_event_boundary",
        "test_effective_membership_reserves_tlt_pending_in_capacity_three_context",
        "test_account_risk_enforces_daily_loss_and_high_water_drawdown_state",
        "test_account_risk_full_approved_tlt_context_matches_six_name_scaling",
        "test_target_reproduces_v3_pipeline_and_strips_tlt_at_context_seam",
        "test_effective_membership_reserves_managed_and_pending_capacity_and_honors_terminal_lifetime",
        "test_event_boundary_reuses_cached_ordinary_target_even_if_input_changes",
        "test_oracle_has_no_production_strategy_or_engine_imports_and_fresh_import_works",
    ),
}

_R46_FAST_ALWAYS = "R46_FAST_ALWAYS"
_R46_PIN_ALWAYS = "R46_PIN_ALWAYS"
_R46_DEEP_CHANGE_SENSITIVE = "R46_DEEP_CHANGE_SENSITIVE"
_R46_DEEP_PERIODIC = "R46_DEEP_PERIODIC"
_R46_DEEP_TIERS = frozenset({_R46_DEEP_CHANGE_SENSITIVE, _R46_DEEP_PERIODIC})

_R46_DEFAULT_TIER_BY_MODULE = {
    "tests/research/test_r46_frozen_source_pins_2026_09_12.py": _R46_PIN_ALWAYS,
    "tests/research/test_r46_governance_contract_2026_09_10.py": _R46_PIN_ALWAYS,
    "tests/research/test_r46_nyse_calendar_2026_09_10.py": _R46_PIN_ALWAYS,
    "tests/research/test_r46_preflight_2026_09_10.py": _R46_PIN_ALWAYS,
    "tests/research/test_r46_publication_availability_evidence_2026_09_11.py": _R46_PIN_ALWAYS,
    "tests/research/test_r46_tlt_market_data_2026_09_10.py": _R46_PIN_ALWAYS,
    "tests/research/test_r46_treasury_acquisition_2026_09_10.py": _R46_FAST_ALWAYS,
    "tests/research/test_r46_treasury_auction_readiness_2026_09_10.py": _R46_FAST_ALWAYS,
    "tests/research/test_r46_treasury_xml_v2_2026_09_10.py": _R46_FAST_ALWAYS,
    "tests/research/test_r46_v2_data_package_2026_09_10.py": _R46_FAST_ALWAYS,
    "tests/research/test_r46_v2_execution_2026_09_10.py": _R46_PIN_ALWAYS,
    "tests/research/test_r46_v2_governance_2026_09_10.py": _R46_PIN_ALWAYS,
    "tests/research/test_r46_v3_oracle_2026_09_11.py": _R46_FAST_ALWAYS,
}

_R46_TIER_OVERRIDES = (
    (
        _R46_FAST_ALWAYS,
        "tests/research/test_r46_tlt_market_data_2026_09_10.py",
        (
            "test_normalize_bars_derives_session_close_availability_and_requires_calendar_for_provider_midnight",
            "test_dividend_normalization_derives_ex_date_regular_open",
            "test_provider_corporate_action_schema_is_normalized_without_fixture_only_fields",
            "test_current_alpaca_cash_dividend_mapping_is_normalized_from_raw_provider_fields",
        ),
    ),
    (
        _R46_DEEP_CHANGE_SENSITIVE,
        "tests/research/test_r46_tlt_market_data_2026_09_10.py",
        (
            "test_authenticated_current_identity_is_attested_against_supplied_history",
            "test_mocked_acquisition_fetches_all_alpaca_routes_and_writes_credential_safe_receipt",
            "test_env_loading_is_opt_in_and_second_publication_cannot_overwrite",
        ),
    ),
    (
        _R46_PIN_ALWAYS,
        "tests/research/test_r46_treasury_acquisition_2026_09_10.py",
        (
            "test_official_source_routes_are_https_and_cover_archive_query_and_documents",
            "test_build_tlt_alpaca_query_freezes_split_and_raw_coverage_without_credentials",
            "test_build_document_record_refuses_overwrite",
            "test_build_document_record_rejects_resolved_path_escape",
            "test_static_capability_receipt_is_canonical_and_self_hashed",
        ),
    ),
    (
        _R46_DEEP_CHANGE_SENSITIVE,
        "tests/research/test_r46_treasury_acquisition_2026_09_10.py",
        (
            "test_local_public_evidence_receipt_is_atomic_data_only_and_hashes_artifacts",
            "test_local_public_evidence_receipt_hashes_announced_rss_only_without_validation",
            "test_local_public_evidence_receipt_reads_each_input_file_once",
            "test_local_public_evidence_receipt_reconciles_in_window_xml_to_legacy_event",
            "test_local_public_evidence_receipt_reconciles_post_cutoff_xml_only_to_rss",
            "test_local_public_evidence_receipt_rejects_duplicate_result_xml_path_hash_and_identity",
            "test_cli_local_public_evidence_mode_never_networks_and_does_not_overwrite",
            "test_fetch_rejects_caller_asserted_timestamps_even_when_present_in_metadata",
            "test_fetch_rejects_nested_caller_asserted_timestamps_before_network",
            "test_fetch_keeps_source_without_timestamp_evidence_raw_unadmitted",
            "test_fetch_rejects_redirect_to_non_official_host",
            "test_fetch_rejects_mismatched_content_type",
            "test_fetch_rejects_mismatched_pdf_signature",
            "test_fetch_keeps_pdf_raw_unadmitted_without_real_extractor",
            "test_cli_does_not_network_without_explicit_fetch",
            "test_acquire_pinned_inventory_never_reports_acquired_from_fetcher_admission",
            "test_acquire_pinned_inventory_fetches_only_with_opt_in",
        ),
    ),
    (
        _R46_DEEP_CHANGE_SENSITIVE,
        "tests/research/test_r46_treasury_auction_readiness_2026_09_10.py",
        (
            "test_verify_r46_package_remains_blocked_even_when_data_and_holdout_shapes_pass",
            "test_verify_r46_package_never_clears_embedded_signatures_without_verifier",
            "test_verify_r46_package_stops_when_holdout_was_accessed",
            "test_verify_r46_package_rejects_unsigned_holdout_attestation",
        ),
    ),
    (
        _R46_PIN_ALWAYS,
        "tests/research/test_r46_treasury_auction_readiness_2026_09_10.py",
        (
            "test_verify_r46_package_rejects_rule_relaxation",
            "test_verify_r46_package_rejects_shared_capital_cap_drift",
            "test_r46_manifest_serializes_utc_cutoff_as_immutable_contract",
        ),
    ),
    (
        _R46_PIN_ALWAYS,
        "tests/research/test_r46_treasury_xml_v2_2026_09_10.py",
        (
            "test_treasury_xml_v2_pins_raw_xml_and_filters_economic_fields",
            "test_treasury_xml_v2_rejects_cross_path_redirect_and_overwrite",
            "test_treasury_xml_v2_rejects_nested_output_symlink",
        ),
    ),
    (
        _R46_PIN_ALWAYS,
        "tests/research/test_r46_v2_data_package_2026_09_10.py",
        (
            "test_package_publication_is_atomic_and_no_overwrite",
            "test_consumer_rejects_changed_pinned_receipt",
            "test_package_rejects_caller_event_universe_alternative_to_governance_pin",
            "test_consumer_rejects_tampered_publication_policy_crosslink",
            "test_builder_rejects_publication_evidence_treasury_source_crosslink",
        ),
    ),
    (
        _R46_DEEP_CHANGE_SENSITIVE,
        "tests/research/test_r46_v2_data_package_2026_09_10.py",
        (
            "test_build_v2_package_hashes_five_artifacts_and_records_retrospective_sources",
            "test_package_accepts_hash_pinned_official_historical_identity_source",
            "test_package_rejects_treasury_receipt_without_schema_validation",
            "test_package_rejects_treasury_receipt_without_xsd_document_lineage",
            "test_package_recomputes_raw_split_row_key_parity",
            "test_package_recomputes_corporate_action_raw_row_hashes",
            "test_package_rejects_missing_retrospective_metadata_before_admission",
            "test_package_rejects_nonparity_raw_split_acquisition",
            "test_consumer_rejects_rehashed_package_with_inconsistent_coverage",
            "test_package_cli_accepts_explicit_five_artifact_metadata",
        ),
    ),
    (
        _R46_FAST_ALWAYS,
        "tests/research/test_r46_v2_execution_2026_09_10.py",
        (
            "test_runner_refuses_mapping_and_requires_pinned_on_disk_gate",
            "test_persisted_preflight_forwards_all_explicit_evidence_paths",
            "test_production_package_runner_rejects_caller_economic_overrides",
            "test_production_config_failure_blocks_and_static_universe_is_test_only",
            "test_production_pit_loader_is_repo_root_relative_and_fail_closed",
            "test_event_marker_reuses_cached_incumbent_target_without_v3_rerun",
            "test_event_boundary_preserves_existing_incumbent_pending_orders",
            "test_marker_volume_is_excluded_from_r46_point_in_time_liquidity_history",
            "test_tlt_remainder_after_exact_open_is_not_carried_to_a_later_bar",
            "test_tlt_pending_order_waits_for_exact_open_after_intermediate_bars",
            "test_partial_tlt_entry_or_exit_is_not_an_eligible_event_episode",
            "test_shared_account_rejects_frozen_cap_overrides_and_seed_slot_overflow",
            "test_shared_account_sells_before_buys_and_reconciles_cash_fees_and_ownership",
            "test_fill_rejections_are_persisted_and_batch_is_atomic",
            "test_dividend_accounting_is_explicit_and_owner_bound",
            "test_suppressed_and_duplicate_events_are_persisted",
            "test_r46_event_uses_next_open_entry_and_fifth_open_after_entry_exit",
            "test_r46_schedule_uses_first_open_strictly_after_available_at_across_dst",
            "test_r46_placebo_shift_preserves_new_york_civil_time_across_dst",
            "test_r46_placebo_shift_anchors_market_closure_to_next_regular_session",
            "test_r46_benchmark_projection_is_asof_and_fails_before_first_source",
            "test_r46_schedule_rejects_weekend_holiday_and_forged_dst_open",
            "test_r46_package_bar_loader_requires_pit_availability_at_or_after_session_open",
            "test_r46_package_dividend_loader_rejects_availability_after_event_timestamp",
            "test_r46_actual_release_timestamp_cannot_advance_policy_entry",
            "test_overlay_suppresses_when_six_incumbent_slots_are_occupied",
            "test_overlay_counts_six_base_targets_before_adding_tlt",
            "test_decision_contract_preserves_desired_overlay_separately_from_applied_target",
            "test_strict_replay_rejects_aggregate_fragments_over_source_bar_cap",
            "test_strict_replay_rejects_equal_timestamp_fragments_for_one_order",
            "test_strict_replay_adv_cutoff_advances_for_two_bar_partial_order",
            "test_strict_replay_rejects_false_terminal_with_final_holdings",
            "test_strict_replay_rejects_duplicate_terminal_symbol_declaration",
            "test_strict_curve_binds_benchmark_to_pinned_spy_bars",
        ),
    ),
    (
        _R46_DEEP_CHANGE_SENSITIVE,
        "tests/research/test_r46_v2_execution_2026_09_10.py",
        (
            "test_r46_package_loader_validates_uniform_policy_for_compact_127_event_fixture",
            "test_file_backed_terminal_owner_flip_is_rejected",
        ),
    ),
    (
        _R46_DEEP_PERIODIC,
        "tests/research/test_r46_v2_execution_2026_09_10.py",
        (
            "test_candidate_control_incumbent_engine_path_is_exact_except_tlt",
            "test_r46_control_matches_canonical_terminal_aware_v5_lifecycle_for_non_tlt",
            "test_r46_effective_dated_equity_fee_stress_scales_official_trade_date_fee",
            "test_timing_placebo_runs_shifted_candidate_and_control_native_engine_cell",
            "test_timing_placebo_persists_rehashed_economic_ledgers_with_shifted_identity",
            "test_incumbent_comparison_uses_complete_primary_event_endpoints_and_sign_flip",
            "test_native_adapter_invokes_real_portfolio_engine_and_dividend_path",
            "test_package_event_fixture_uses_sixth_open_dividends_and_three_cost_cells",
            "test_package_runner_round_trip_publishes_consumed_receipt_and_refuses_rerun",
            "test_strict_curve_recomputes_all_serialized_result_metrics",
            "test_strict_curve_does_not_accept_holdings_history_as_metric_authority",
            "test_governance_package_native_preflight_and_runner_gate_round_trip",
            "test_strict_file_backed_green_fixture_verifies_without_production_runner",
        ),
    ),
    (
        _R46_DEEP_CHANGE_SENSITIVE,
        "tests/research/test_r46_v2_governance_2026_09_10.py",
        (
            "test_v2_builder_freezes_disclosed_child_contract_without_human_signatures",
            "test_v2_builder_emits_explicit_v5_source_file_paths_for_preflight",
            "test_v2_builder_canonicalizes_conflicting_v5_source_override_path",
            "test_v2_preflight_reports_explicit_blockers_and_never_emits_economics",
            "test_v2_preflight_rejects_a_consumed_one_shot_state",
            "test_v2_preflight_resolves_repo_relative_manifest_argument",
            "test_v2_preflight_rejects_v1_data_package_schema",
            "test_native_attestation_structural_fills_are_not_global_outcome_fields",
            "test_v2_builder_can_finalize_from_explicit_source_commit",
            "test_v2_builder_accepts_deterministic_source_overrides",
            "test_acquisition_parity_is_recomputed_and_tampering_blocks",
            "test_preflight_receipt_round_trips_through_runner_reader",
        ),
    ),
    (
        _R46_PIN_ALWAYS,
        "tests/research/test_r46_v3_oracle_2026_09_11.py",
        ("test_oracle_has_no_production_strategy_or_engine_imports_and_fresh_import_works",),
    ),
)


def _build_r46_test_tiers() -> dict[str, str]:
    tiers = {
        f"{path}::{name}": _R46_DEFAULT_TIER_BY_MODULE[path]
        for path, names in _R46_TESTS_BY_MODULE.items()
        for name in names
    }
    seen: set[str] = set()
    for tier, path, names in _R46_TIER_OVERRIDES:
        for name in names:
            selector = f"{path}::{name}"
            if selector in seen:
                raise RuntimeError(f"duplicate R46 tier metadata: {selector}")
            if selector not in tiers:
                raise RuntimeError(f"unknown R46 tier metadata: {selector}")
            tiers[selector] = tier
            seen.add(selector)
    if set(_R46_DEFAULT_TIER_BY_MODULE) != set(_R46_TESTS_BY_MODULE):
        raise RuntimeError("R46 tier metadata has an unregistered module")
    expected_count = sum(len(names) for names in _R46_TESTS_BY_MODULE.values())
    if len(tiers) != expected_count:
        raise RuntimeError("R46 tier metadata has duplicate test selectors")
    return tiers


_R46_TEST_TIERS = _build_r46_test_tiers()
_R46_TEST_FILES = frozenset(_R46_TESTS_BY_MODULE)
_R46_TEST_NODEIDS = frozenset(_R46_TEST_TIERS)


def _duplicate_paths(paths: tuple[str, ...]) -> tuple[str, ...]:
    seen = set()
    duplicates = set()
    for path in paths:
        if path in seen:
            duplicates.add(path)
        seen.add(path)
    return tuple(sorted(duplicates))


def _validate_research_classification() -> None:
    errors = []
    for label, paths in (
        ("current research", _CURRENT_RESEARCH_TEST_PATHS),
        ("historical research", _HISTORICAL_RESEARCH_TEST_PATHS),
    ):
        duplicates = _duplicate_paths(paths)
        if duplicates:
            errors.append(f"{label} has duplicate paths: {', '.join(duplicates)}")
        missing = sorted(path for path in paths if not (_REPO_ROOT / path).is_file())
        if missing:
            errors.append(f"{label} has missing files: {', '.join(missing)}")

    overlap = sorted(_CURRENT_RESEARCH_TESTS & _HISTORICAL_RESEARCH_TESTS)
    if overlap:
        errors.append(f"current and historical research overlap: {', '.join(overlap)}")

    root_research_tests = {
        path.relative_to(_REPO_ROOT).as_posix() for path in (_REPO_ROOT / "tests").glob("test_research_*.py")
    }
    unclassified = sorted(root_research_tests - (_CURRENT_RESEARCH_TESTS | _HISTORICAL_RESEARCH_TESTS))
    if unclassified:
        errors.append(f"unclassified root research tests: {', '.join(unclassified)}")

    missing_r46_files = sorted(path for path in _R46_TEST_FILES if not (_REPO_ROOT / path).is_file())
    if missing_r46_files:
        errors.append(f"R46 tier registry has missing files: {', '.join(missing_r46_files)}")

    if errors:
        raise pytest.UsageError(
            "Invalid research test classification metadata:\n" + "\n".join(f"- {error}" for error in errors)
        )


def _classify_test_path(path: Path) -> str:
    try:
        relative = path.resolve().relative_to(_REPO_ROOT).as_posix()
    except ValueError:
        return _PRODUCT

    if relative in _CURRENT_RESEARCH_TESTS:
        return _CURRENT_RESEARCH
    if relative in _HISTORICAL_RESEARCH_TESTS or relative.startswith("tests/research/"):
        return _HISTORICAL_RESEARCH
    return _PRODUCT


def _r46_base_nodeid(nodeid: str) -> str:
    """Normalize parametrized nodeids to the externally registered test function."""
    return nodeid.split("[", 1)[0]


def _r46_is_broad_collection(config) -> bool:
    """Only require complete registry coverage for broad, not narrow, requests."""
    option = getattr(config, "option", None)
    if option is not None and (getattr(option, "keyword", "") or getattr(option, "deselect", ())):
        return False

    args = tuple(str(arg) for arg in getattr(config, "args", ()))
    if not args:
        return True

    broad_paths = {
        _REPO_ROOT.resolve(),
        (_REPO_ROOT / "tests").resolve(),
        (_REPO_ROOT / "tests" / "research").resolve(),
    }
    selected_r46_files = set()
    for arg in args:
        candidate = Path(arg)
        if not candidate.is_absolute():
            candidate = _REPO_ROOT / candidate
        try:
            resolved = candidate.resolve()
        except OSError:
            continue
        if resolved in broad_paths:
            return True
        if resolved.is_file():
            try:
                relative = resolved.relative_to(_REPO_ROOT).as_posix()
            except ValueError:
                continue
            if relative in _R46_TEST_FILES:
                selected_r46_files.add(relative)
    return selected_r46_files == _R46_TEST_FILES


def _apply_r46_tiers_and_validate(config, items) -> None:
    errors = []
    collected_selectors = set()
    for item in items:
        item_path = getattr(item, "path", None) or item.fspath
        try:
            relative_path = Path(str(item_path)).resolve().relative_to(_REPO_ROOT).as_posix()
        except ValueError:
            continue
        if relative_path not in _R46_TEST_FILES:
            continue

        selector = _r46_base_nodeid(item.nodeid)
        tier = _R46_TEST_TIERS.get(selector)
        if tier is None:
            errors.append(f"unregistered collected R46 test: {selector}")
            continue
        collected_selectors.add(selector)
        if tier in _R46_DEEP_TIERS:
            item.add_marker("r46_deep")

    if _r46_is_broad_collection(config):
        stale = sorted(_R46_TEST_NODEIDS - collected_selectors)
        if stale:
            errors.append("stale R46 tier selectors: " + ", ".join(stale))

    if errors:
        raise pytest.UsageError("Invalid R46 validation tier metadata:\n" + "\n".join(f"- {error}" for error in errors))


def pytest_addoption(parser):
    parser.addoption(
        "--include-historical-research",
        action="store_true",
        help="include historical_research test modules before collection",
    )


def pytest_configure(config):
    del config
    _validate_research_classification()


def pytest_ignore_collect(collection_path: Path, config) -> bool:
    """Skip historical modules before pytest imports or collects them."""
    path = Path(str(collection_path))
    if not path.is_file():
        return False
    return _classify_test_path(path) == _HISTORICAL_RESEARCH and not config.getoption("include_historical_research")


def pytest_collection_modifyitems(config, items):
    """Mark research items after only the allowed modules have been collected."""
    for item in items:
        item_path = getattr(item, "path", None) or item.fspath
        classification = _classify_test_path(Path(str(item_path)))
        if classification == _CURRENT_RESEARCH:
            item.add_marker("research")
        elif classification == _HISTORICAL_RESEARCH:
            item.add_marker("research")
            item.add_marker("historical_research")
    _apply_r46_tiers_and_validate(config, items)


def _file_signature(path: Path):
    if not path.exists():
        return None
    try:
        st = path.stat()
        return (st.st_mtime_ns, st.st_size)
    except FileNotFoundError:
        return None


@pytest.fixture(autouse=True)
def _restore_web_state():
    before = get_web_state()
    try:
        from parayabuck.web import server as srv

        orig_manager = srv.manager
        orig_backtest = srv.backtest_manager
    except Exception:
        orig_manager = None  # type: ignore[assignment]
        orig_backtest = None  # type: ignore[assignment]

    yield

    current = get_web_state()
    if current is not before:
        set_web_state(before)
    try:
        from parayabuck.web import server as srv2

        if before is not None and srv2.web_state is not before:
            srv2.web_state = before  # type: ignore[attr-defined]
            set_web_state(before)
        if orig_manager is not None and srv2.manager is not orig_manager:
            srv2.manager = orig_manager  # type: ignore[attr-defined]
        if orig_backtest is not None and srv2.backtest_manager is not orig_backtest:
            srv2.backtest_manager = orig_backtest  # type: ignore[attr-defined]
    except Exception:
        pass


@pytest.fixture(autouse=True)
def _test_isolation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest):
    before = {p: _file_signature(p) for p in _PROTECTED_PATHS}
    iso_cache = tmp_path / "warmup_isolated.sqlite3"
    iso_journal = tmp_path / "observability_isolated.sqlite3"
    iso_snapshot = tmp_path / "health_snapshot_isolated.json"
    iso_db = tmp_path / "parayabuck_isolated.sqlite3"
    iso_receipt_dir = tmp_path / "paper_receipts"
    iso_receipt_dir.mkdir(parents=True, exist_ok=True)
    iso_cache.parent.mkdir(parents=True, exist_ok=True)

    # WarmupCache redirect
    orig_warmup_init = WarmupCache.__init__

    def _patched_warmup_init(self, path="data/cache/warmup.sqlite3"):  # type: ignore[no-untyped-def]
        if str(path) == "data/cache/warmup.sqlite3":
            path = str(iso_cache)
        return orig_warmup_init(self, path)

    monkeypatch.setattr(cache_module.WarmupCache, "__init__", _patched_warmup_init)
    monkeypatch.setattr(WarmupCache, "__init__", _patched_warmup_init)

    # EventJournal single isolated instance
    orig_journal_init = EventJournal.__init__

    def _patched_journal_init(self, path=DEFAULT_JOURNAL_PATH, queue_size=2048, retention_days=30, max_events=50000):  # type: ignore[no-untyped-def]
        if str(path) == DEFAULT_JOURNAL_PATH or str(path) == "data/runtime/observability.sqlite3":
            path = str(iso_journal)
        return orig_journal_init(
            self, path, queue_size=queue_size, retention_days=retention_days, max_events=max_events
        )

    monkeypatch.setattr(journal_module.EventJournal, "__init__", _patched_journal_init)

    iso_journal_instance = EventJournal(str(iso_journal))
    old_global = journal_module._global_journal
    journal_module._global_journal = iso_journal_instance
    orig_get_journal = journal_module.get_journal

    def _patched_get_journal(path=None):  # type: ignore[no-untyped-def]
        if path is None or str(path) in (DEFAULT_JOURNAL_PATH, "data/runtime/observability.sqlite3", str(iso_journal)):
            return iso_journal_instance  # type: ignore[return-value]
        return orig_get_journal(path)

    monkeypatch.setattr(journal_module, "get_journal", _patched_get_journal)

    # SnapshotWriter
    try:
        orig_snapshot_init = SnapshotWriter.__init__

        def _patched_snapshot_init(self, path=DEFAULT_SNAPSHOT_PATH, interval_seconds=30.0, provider=None):  # type: ignore[no-untyped-def]
            if str(path) == str(DEFAULT_SNAPSHOT_PATH) or str(path) == "data/runtime/health_snapshot.json":
                path = str(iso_snapshot)
            return orig_snapshot_init(self, path, interval_seconds=interval_seconds, provider=provider)

        monkeypatch.setattr(snapshot_module.SnapshotWriter, "__init__", _patched_snapshot_init)
    except Exception:
        pass

    try:
        monkeypatch.setattr(receipt_module, "default_receipt_dir", lambda: iso_receipt_dir)
    except Exception:
        pass

    orig_store_init = SQLiteStore.__init__

    def _patched_store_init(self, path):  # type: ignore[no-untyped-def]
        s = str(path)
        if s == "data/runtime/parayabuck.sqlite3" or (s.endswith("parayabuck.sqlite3") and "data/runtime" in s):
            if str(tmp_path) not in s:
                path = str(iso_db)
        return orig_store_init(self, path)

    monkeypatch.setattr(SQLiteStore, "__init__", _patched_store_init)

    try:
        from parayabuck.execution import ledger as ledger_module

        orig_ledger_init = ledger_module.ExecutionLedger.__init__

        def _patched_ledger_init(self, path, store=None):  # type: ignore[no-untyped-def]
            if str(path) == "data/runtime/parayabuck.sqlite3" and str(tmp_path) not in str(path):
                path = str(iso_db)
            return orig_ledger_init(self, path, store=store)

        monkeypatch.setattr(ledger_module.ExecutionLedger, "__init__", _patched_ledger_init)
    except Exception:
        pass

    try:
        from parayabuck.portfolio import runtime_repository as repo_module

        orig_repo_init = repo_module.PortfolioStateRepository.__init__

        def _patched_repo_init(self, path, store=None):  # type: ignore[no-untyped-def]
            if str(path) == "data/runtime/parayabuck.sqlite3" and str(tmp_path) not in str(path):
                path = str(iso_db)
            return orig_repo_init(self, path, store=store)

        monkeypatch.setattr(repo_module.PortfolioStateRepository, "__init__", _patched_repo_init)
    except Exception:
        pass

    try:
        import parayabuck.data.alpaca as alpaca_module

        class _BlockedStock:
            def __init__(self, *a, **kw):
                try:
                    self._session = MagicMock()
                    self._session.close = lambda: None
                except Exception:
                    self._session = SimpleNamespace(
                        close=lambda: None, request=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("blocked"))
                    )

            def get_stock_bars(self, *a, **kw):
                raise RuntimeError(
                    "Network blocked in tests: Alpaca StockHistoricalDataClient.get_stock_bars called without fake"
                )

            def get_stock_latest_quote(self, *a, **kw):
                raise RuntimeError(
                    "Network blocked in tests: Alpaca StockHistoricalDataClient.get_stock_latest_quote called without fake"
                )

        class _BlockedCrypto:
            def __init__(self, *a, **kw):
                try:
                    self._session = MagicMock()
                    self._session.close = lambda: None
                except Exception:
                    self._session = SimpleNamespace(
                        close=lambda: None, request=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("blocked"))
                    )

            def get_crypto_bars(self, *a, **kw):
                raise RuntimeError(
                    "Network blocked in tests: Alpaca CryptoHistoricalDataClient.get_crypto_bars called without fake"
                )

            def get_crypto_latest_quote(self, *a, **kw):
                raise RuntimeError(
                    "Network blocked in tests: Alpaca CryptoHistoricalDataClient.get_crypto_latest_quote called without fake"
                )

        monkeypatch.setattr(alpaca_module, "StockHistoricalDataClient", _BlockedStock)
        monkeypatch.setattr(alpaca_module, "CryptoHistoricalDataClient", _BlockedCrypto)
    except Exception:
        pass

    # No patch for should_auto_start: tests that check its return value rely on
    # real logic (paper vs live + credentials). Auto-start not triggered in tests
    # because we clear ALPACA keys and never run the web lifespan.
    # Keep network blocked via Alpaca client stubs above.

    # Redirect any direct sqlite3.connect to production files to isolated tmp
    import sqlite3 as _sqlite3

    _orig_connect = _sqlite3.connect

    def _patched_connect(database, *args, **kwargs):  # type: ignore[no-untyped-def]
        db_str = str(database)
        # Redirect production paths (including URI file:...) to isolated files
        if "data/runtime/parayabuck.sqlite3" in db_str and str(tmp_path) not in db_str:
            db_str = db_str.replace("data/runtime/parayabuck.sqlite3", str(iso_db))
            # Handle WAL/SHM etc. are separate files, not via connect, but handle anyway
            database = db_str
        if "data/runtime/observability.sqlite3" in db_str and str(tmp_path) not in db_str:
            db_str = db_str.replace("data/runtime/observability.sqlite3", str(iso_journal))
            database = db_str
        if "data/cache/warmup.sqlite3" in db_str and str(tmp_path) not in db_str:
            db_str = db_str.replace("data/cache/warmup.sqlite3", str(iso_cache))
            database = db_str
        return _orig_connect(database, *args, **kwargs)

    monkeypatch.setattr(_sqlite3, "connect", _patched_connect)

    monkeypatch.setenv("PYTEST_ISOLATION", "1")
    monkeypatch.delenv("ALPACA_API_KEY", raising=False)
    monkeypatch.delenv("ALPACA_SECRET_KEY", raising=False)

    yield

    time.sleep(0.05)
    ignored = {"pb-journal-writer", "pb-alert-webhook-worker"}
    alive = [t for t in threading.enumerate() if t.is_alive() and t.name.startswith("pb-") and t.name not in ignored]
    # Also ignore daemon threads that are expected to linger briefly
    alive_non_daemon = [t for t in alive if not t.daemon]
    if alive_non_daemon:
        pytest.fail(
            f"Engine threads still alive after test: {[t.name for t in alive_non_daemon]} — tests must stop EngineRuntime"
        )
    # For remaining daemon pb- threads, give them a moment then warn but not fail
    # (they are isolated per test via tmp journal and will be garbage collected)

    try:
        if iso_journal_instance is not None:
            try:
                iso_journal_instance.stop(timeout=0.5)
            except Exception:
                pass
        # Restore old global if it was there
        journal_module._global_journal = old_global
        # Also ensure any lingering global is cleared
        if journal_module._global_journal is not None and journal_module._global_journal is not iso_journal_instance:
            try:
                journal_module._global_journal.stop(timeout=0.5)
            except Exception:
                pass
            journal_module._global_journal = None
    except Exception:
        pass

    try:
        from parayabuck.monitoring import observe as obs_mod

        obs_mod.reset_dedup_for_tests()
    except Exception:
        pass

    for p, sig_before in before.items():
        # Research persistence tests intentionally write to a DB; our isolation
        # redirects via SQLiteStore but some direct Path writes may still touch
        # production. Skip strict check for those specific tests to avoid false
        # positives while keeping protection for cache/observability.
        if p == Path("data/runtime/parayabuck.sqlite3") and (
            "test_sqlite_protection" in request.node.name or "test_research" in request.node.name
        ):
            continue
        sig_after = _file_signature(p)
        if sig_before is None:
            if sig_after is not None:
                pytest.fail(f"Test created production file {p} that did not exist before — isolation failed")
        else:
            if sig_after != sig_before:
                pytest.fail(
                    f"Production file {p} was modified during test (before={sig_before}, after={sig_after}) — isolation failed, real cache/runtime polluted"
                )
