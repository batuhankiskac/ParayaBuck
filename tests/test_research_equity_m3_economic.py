from __future__ import annotations

import ast
import copy
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from scripts import research_equity_m3_economic as economic


def _implementation_commit() -> str | None:
    current = economic.git_head()
    commits = economic._git("rev-list", "--first-parent", "--reverse", f"{economic.STARTING_HEAD}..{current}")  # type: ignore[attr-defined]
    for commit in commits.splitlines():
        try:
            if economic.git_parents(commit) == [economic.STARTING_HEAD] and sorted(
                economic.changed_paths(economic.STARTING_HEAD, commit)
            ) == sorted(economic.IMPLEMENTATION_PATHS):
                return commit
        except (economic.ContractError, OSError):
            continue
    return None


def _manifest_commit() -> tuple[str, str] | None:
    implementation = _implementation_commit()
    if implementation is None:
        return None
    current = economic.git_head()
    commits = economic._git("rev-list", "--first-parent", "--reverse", f"{implementation}..{current}")  # type: ignore[attr-defined]
    for commit in commits.splitlines():
        if economic.git_parents(commit) == [implementation] and economic.changed_paths(implementation, commit) == [
            economic.MANIFEST_PATH
        ]:
            return implementation, commit
    return None


def _require_manifest() -> tuple[str, str]:
    value = _manifest_commit()
    if value is None:
        pytest.skip("the A25/B25 chain is not committed yet")
    return value


def _pair_fixture() -> tuple[dict[str, object], dict[str, object]]:
    shared = {
        "scanner_pool_digest": "pool",
        "scanner_timestamp_digest": "timestamps",
        "terminal_map_digest": "terminal",
        "corporate_action_digest": "actions",
        "cost_model": {"slippage_bps": 2, "spread_bps": 2},
        "initial_cash": 100000,
        "evaluation_sessions": ["2026-01-02", "2026-01-05"],
    }
    return (
        {"observation_boundary": "C|C", **shared},
        {"observation_boundary": "X|X", **shared},
    )


def test_starting_head_anchor_exists() -> None:
    assert len(economic.STARTING_HEAD) == 40
    assert economic.git_parents(economic.STARTING_HEAD) == [economic.E24_PARENT]


def test_upstream_r5_receipt_is_accepted() -> None:
    upstream = economic.validate_upstream()
    assert upstream["data_gate"]["verdict"] == "M3_DATA_GATE_PASS"
    assert upstream["data_gate"]["mandatory_gate_count"] == 38


def test_upstream_ocp_r2_receipt_is_accepted() -> None:
    upstream = economic.validate_upstream()
    assert upstream["ocp_r2"]["verdict"] == "M3_CANDIDATE_OCP_ATTESTATION_R2_PASS"
    assert upstream["ocp_r2"]["self_digest_sha256"] == economic.R2_RECEIPT_DIGEST


def test_old_unversioned_ocp_is_rejected() -> None:
    assert economic.old_unversioned_ocp_rejected() is True


def test_r2_altered_self_digest_is_rejected() -> None:
    payload = json.loads((economic.REPO / economic.R2_RECEIPT_PATH).read_text(encoding="utf-8"))
    payload["self_digest_sha256"] = "0" * 64
    with pytest.raises((economic.ContractError, ValueError)):
        economic._validate_r2_payload(payload)  # type: ignore[attr-defined]


def test_r2_altered_c23_head_binding_is_rejected() -> None:
    payload = json.loads((economic.REPO / economic.R2_RECEIPT_PATH).read_text(encoding="utf-8"))
    payload["substantive_replay"]["c23_ci"]["head_sha"] = economic.R2_RESULT_COMMIT
    from m3_data_gate_helpers import canonical_digest

    payload["self_digest_sha256"] = canonical_digest(
        {key: value for key, value in payload.items() if key != "self_digest_sha256"}
    )
    with pytest.raises((economic.ContractError, ValueError)):
        economic._validate_r2_payload(payload)  # type: ignore[attr-defined]


def test_r5_data_gate_identity_mismatch_blocks() -> None:
    receipt = economic._json_blob(economic.git_head(), economic.R5_RECEIPT_PATH)  # type: ignore[attr-defined]
    manifest = economic._json_blob(economic.git_head(), economic.R5_MANIFEST_PATH)  # type: ignore[attr-defined]
    altered = copy.deepcopy(receipt)
    altered["pit_population"]["candidate_universe_digest_sha256"] = "0" * 64
    with pytest.raises(economic.ContractError, match="R5 receipt self-digest"):
        economic.validate_r5_receipt_payload(altered, manifest)


def test_config_baseline_matches_tracked_yaml() -> None:
    commit = _implementation_commit() or economic.STARTING_HEAD
    config = economic._yaml_blob(commit, economic.CONFIG_PATH)  # type: ignore[attr-defined]
    assert economic.validate_baseline_configuration(config) == economic.BASELINE_CONFIGURATION


def test_strategy_identity_is_frozen() -> None:
    config = economic._yaml_blob(economic.git_head(), economic.CONFIG_PATH)  # type: ignore[attr-defined]
    economic._validate_strategy_contract(config)  # type: ignore[attr-defined]
    assert economic.OBSERVATION_IDENTITIES["control_C"]["strategy"] == "equity_residual_momentum_v3"
    assert economic.OBSERVATION_IDENTITIES["candidate_X"]["strategy"] == (
        "equity_proxy_multifactor_residual_momentum_v1"
    )


def test_manifest_builder_is_hermetic(monkeypatch: pytest.MonkeyPatch) -> None:
    implementation, _manifest = _require_manifest()

    def explode(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("external-data resolver was called")

    monkeypatch.setattr(economic, "resolve_external_data_artifact", explode, raising=False)
    built = economic.build_predeclaration_manifest(implementation)
    assert built["no_external_data_resolution"] is True


def test_manifest_builder_does_not_call_external_data_resolver(monkeypatch: pytest.MonkeyPatch) -> None:
    implementation, _manifest = _require_manifest()
    calls: list[object] = []

    def record(*args: object, **_kwargs: object) -> None:
        calls.extend(args)

    monkeypatch.setattr(economic, "resolve_external_data_artifact", record, raising=False)
    economic.build_predeclaration_manifest(implementation)
    assert calls == []


def test_manifest_has_no_real_result_fields() -> None:
    implementation, _manifest = _require_manifest()
    manifest = economic.build_predeclaration_manifest(implementation)
    assert economic._forbidden_keys(manifest) == ()  # type: ignore[attr-defined]
    assert manifest["economic_results_present"] is False
    assert "results" not in manifest


def test_default_runner_invocation_cannot_run_economics() -> None:
    assert economic.main([]) == economic.NO_EXECUTION_EXIT


def test_real_execution_flag_without_authorization_blocks() -> None:
    assert economic.main(["--execute-predeclared-economic-mission"]) == economic.BLOCKED_EXIT


def test_authorization_without_explicit_execution_flag_does_not_run() -> None:
    assert economic.main(["--execution-authorization", "/tmp/not-created.json"]) == economic.NO_EXECUTION_EXIT


@pytest.mark.parametrize(
    "argv",
    [
        ["--validate-contract", "--parameters", "alternate"],
        ["--validate-contract", "--start", "2022-01-03"],
        ["--validate-contract", "--cost", "5x"],
        ["--validate-contract", "--strategy", "m4"],
    ],
)
def test_forbidden_cli_overrides_are_rejected(argv: list[str]) -> None:
    with pytest.raises(SystemExit):
        economic.main(argv)


def test_m4_is_not_a_supported_runner_command() -> None:
    with pytest.raises(SystemExit):
        economic.main(["--m4"])


def test_control_must_use_c_c() -> None:
    control, candidate = _pair_fixture()
    control["observation_boundary"] = "X|X"
    with pytest.raises(economic.ContractError, match="control observation boundary"):
        economic.validate_pair_contract(control, candidate)


def test_candidate_must_use_x_x() -> None:
    control, candidate = _pair_fixture()
    candidate["observation_boundary"] = "X|C"
    with pytest.raises(economic.ContractError, match="candidate observation boundary"):
        economic.validate_pair_contract(control, candidate)


def test_x_c_is_not_economic() -> None:
    control, candidate = _pair_fixture()
    candidate["diagnostic_boundary"] = "X|C"
    candidate["used_for_economics"] = True
    with pytest.raises(economic.ContractError, match="X\\|C"):
        economic.validate_pair_contract(control, candidate)


@pytest.mark.parametrize("field", ["scanner_pool_digest", "scanner_timestamp_digest", "terminal_map_digest"])
def test_same_run_identity_mismatch_blocks(field: str) -> None:
    control, candidate = _pair_fixture()
    candidate[field] = "different"
    with pytest.raises(economic.ContractError, match="same-run"):
        economic.validate_pair_contract(control, candidate)


def test_c_and_x_costs_must_match() -> None:
    control, candidate = _pair_fixture()
    candidate["cost_model"] = {"slippage_bps": 4, "spread_bps": 4}
    with pytest.raises(economic.ContractError, match="cost_model"):
        economic.validate_pair_contract(control, candidate)


def test_c_and_x_initial_cash_must_match() -> None:
    control, candidate = _pair_fixture()
    candidate["initial_cash"] = 1
    with pytest.raises(economic.ContractError, match="initial_cash"):
        economic.validate_pair_contract(control, candidate)


def test_reference_symbols_cannot_enter_membership() -> None:
    with pytest.raises(economic.ContractError, match="candidate membership"):
        economic.validate_reference_contract(["SPY"], [], [])


def test_reference_symbols_cannot_enter_targets() -> None:
    with pytest.raises(economic.ContractError, match="target symbols"):
        economic.validate_reference_contract([], ["IWM"], [])


def test_reference_symbols_cannot_enter_execution() -> None:
    with pytest.raises(economic.ContractError, match="executable symbols"):
        economic.validate_reference_contract([], [], ["QQQ"])


def test_capacity_contract_is_six_reserved_and_24_candidate() -> None:
    economic.validate_reference_contract([], [], [])
    assert economic.REFERENCE_CONTRACT["maximum_stream_symbols"] == 30
    assert economic.REFERENCE_CONTRACT["effective_candidate_observation_capacity"] == 24


def test_full_and_recent_windows_are_frozen() -> None:
    assert economic.EVALUATION_WINDOWS["FULL"] == {
        "warmup_source_start": "2016-01-04",
        "metric_start": "2017-02-06",
        "inclusive_end": "2026-07-30",
    }
    assert economic.EVALUATION_WINDOWS["RECENT"]["metric_start"] == "2022-01-03"


def test_four_robustness_periods_are_frozen() -> None:
    assert len(economic.ROBUSTNESS_PERIODS) == 4
    assert economic.ROBUSTNESS_PERIODS[0]["start"] == "2017-02-06"
    assert economic.ROBUSTNESS_PERIODS[-1]["end"] == "2026-07-30"


def test_warmup_is_separate_from_metric_window() -> None:
    assert economic.EVALUATION_WINDOWS["RECENT"]["warmup_before_metric_start_allowed"] is True
    assert economic.TIMING_CONTRACT["post_window_data"].startswith("bars after 2026-07-30")


def test_same_bar_fill_is_rejected() -> None:
    timestamp = datetime(2026, 7, 30, tzinfo=UTC)
    with pytest.raises(economic.ContractError, match="same-bar"):
        economic.validate_fill_timing(timestamp, timestamp, timestamp, next_execution_bar_exists=True)


def test_post_window_fill_is_rejected() -> None:
    order = datetime(2026, 7, 29, tzinfo=UTC)
    fill = datetime(2026, 7, 31, tzinfo=UTC)
    end = datetime(2026, 7, 30, tzinfo=UTC)
    with pytest.raises(economic.ContractError, match="post-window"):
        economic.validate_fill_timing(order, fill, end, next_execution_bar_exists=True)


def test_terminal_order_without_next_bar_is_not_silently_filled() -> None:
    timestamp = datetime(2026, 7, 30, tzinfo=UTC)
    with pytest.raises(economic.ContractError, match="valid next execution"):
        economic.validate_fill_timing(
            timestamp - timedelta(days=1),
            timestamp,
            timestamp,
            next_execution_bar_exists=False,
        )


def test_valid_next_bar_fill_is_accepted() -> None:
    order = datetime(2026, 7, 29, tzinfo=UTC)
    fill = datetime(2026, 7, 30, tzinfo=UTC)
    economic.validate_fill_timing(order, fill, fill, next_execution_bar_exists=True)


def test_event_sequence_requires_an_order_before_fill() -> None:
    timestamp = datetime(2026, 7, 30, tzinfo=UTC)
    with pytest.raises(economic.ContractError, match="no prior order"):
        economic.validate_event_sequence([{"kind": "fill", "order_id": "x", "timestamp": timestamp}], timestamp)


def test_event_sequence_accepts_next_session_fill() -> None:
    order = datetime(2026, 7, 29, tzinfo=UTC)
    fill = datetime(2026, 7, 30, tzinfo=UTC)
    economic.validate_event_sequence(
        [
            {"kind": "order", "order_id": "x", "timestamp": order, "next_execution_bar_exists": True},
            {"kind": "fill", "order_id": "x", "timestamp": fill},
        ],
        fill,
    )


def test_cost_scenarios_are_exactly_one_two_three_five_x() -> None:
    assert list(economic.COST_SCENARIOS) == ["BASELINE_1X", "STRESS_2X", "STRESS_3X", "STRESS_5X"]
    assert [economic.COST_SCENARIOS[name]["multiplier"] for name in economic.COST_SCENARIOS] == [1, 2, 3, 5]  # type: ignore[index]


def test_regulatory_fees_are_not_stressed() -> None:
    assert economic.COST_SCENARIO_CONTRACT["multiplied_only"] == ["slippage_bps", "spread_bps"]
    assert "commission" in economic.COST_SCENARIO_CONTRACT["not_multiplied"]
    assert "TAF" in economic.COST_SCENARIO_CONTRACT["not_multiplied"]


def test_metrics_and_conventions_are_frozen() -> None:
    assert len(economic.METRIC_CONTRACT["per_strategy"]) == 20
    assert len(economic.METRIC_CONTRACT["candidate_minus_control"]) == 19
    assert economic.METRIC_CONTRACT["conventions"]["annualization_factor"] == 252


def test_bootstrap_is_diagnostic_and_frozen() -> None:
    assert economic.BOOTSTRAP_CONTRACT["seed"] == 20260902
    assert economic.BOOTSTRAP_CONTRACT["block_length_weeks"] == 4
    assert economic.BOOTSTRAP_CONTRACT["resamples"] == 10000
    assert economic.BOOTSTRAP_CONTRACT["diagnostic_only"] is True


def test_acceptance_thresholds_are_frozen() -> None:
    assert economic.ACCEPTANCE_CRITERIA["thresholds_frozen_before_results"] is True
    assert len(economic.ACCEPTANCE_CRITERIA["pass_criteria"]) == 11


def test_no_optimization_contract() -> None:
    assert economic.STRATEGY_CONTRACT["shared_parameters"] == economic.FROZEN_PARAMETERS
    assert economic.ACCEPTANCE_CRITERIA["stress_diagnostics"] == ["STRESS_3X", "STRESS_5X"]


def test_feed_transferability_mismatch_is_recorded() -> None:
    assert economic.FEED_TRANSFERABILITY_BOUNDARY == {
        "historical_feed": "sip",
        "paper_live_stream_feed": "iex",
        "feed_match": False,
        "economic_pass_scope": "M3 historical economic research PASS only",
        "not_established": ["PAPER readiness", "LIVE readiness", "promotion readiness"],
        "next_required_technical_gate": "M3_FEED_TRANSFERABILITY_AND_PAPER_READINESS_GATE",
        "human_promotion_only": True,
    }


def test_execution_authorization_schema_is_fail_closed(tmp_path: Path) -> None:
    path = tmp_path / "authorization.json"
    path.write_text(
        json.dumps(
            {
                "mission": "M3_ECONOMIC_MISSION_EXECUTION",
                "real_execution_authorized": True,
                "human_approval": True,
            }
        ),
        encoding="utf-8",
    )
    assert economic.validate_execution_authorization(path)["human_approval"] is True


def test_execution_authorization_wrong_mission_blocks(tmp_path: Path) -> None:
    path = tmp_path / "authorization.json"
    path.write_text(
        json.dumps(
            {
                "mission": "M3_ECONOMIC_MISSION_PREDECLARATION_R1",
                "real_execution_authorized": True,
                "human_approval": True,
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(economic.ContractError, match="authorization mission"):
        economic.validate_execution_authorization(path)


def test_real_execution_stops_even_with_valid_authorization(tmp_path: Path) -> None:
    path = tmp_path / "authorization.json"
    path.write_text(
        json.dumps(
            {
                "mission": "M3_ECONOMIC_MISSION_EXECUTION",
                "real_execution_authorized": True,
                "human_approval": True,
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(economic.ContractError, match="reserved"):
        economic.guard_real_execution(explicit_execution_flag=True, execution_authorization=path)


def test_runner_has_no_broker_or_network_import() -> None:
    economic._validate_source_has_no_broker_path()  # type: ignore[attr-defined]
    tree = ast.parse((economic.REPO / "scripts/research_equity_m3_economic.py").read_text(encoding="utf-8"))
    assert not any(
        isinstance(node, ast.ImportFrom) and node.module and node.module.startswith(("alpaca", "requests", "broker"))
        for node in ast.walk(tree)
    )


def test_synthetic_pass_fixture() -> None:
    result = economic.run_synthetic_fixture("pass")
    assert result["verdict"] == economic.PASS_VERDICT


def test_synthetic_fail_fixture() -> None:
    result = economic.run_synthetic_fixture("fail")
    assert result["verdict"] == economic.FAIL_VERDICT


def test_synthetic_blocked_fixture() -> None:
    result = economic.run_synthetic_fixture("blocked")
    assert result["verdict"] == economic.ECONOMIC_BLOCKED_VERDICT


def test_fail_cannot_be_relabelled_pass() -> None:
    fixture = economic._synthetic_fixture("fail")  # type: ignore[attr-defined]
    fixture["verdict"] = economic.PASS_VERDICT
    assert economic.evaluate_synthetic_economic_fixture(fixture)["verdict"] == economic.FAIL_VERDICT


def test_blocked_cannot_be_relabelled_fail() -> None:
    fixture = economic._synthetic_fixture("blocked")  # type: ignore[attr-defined]
    fixture["verdict"] = economic.FAIL_VERDICT
    assert economic.evaluate_synthetic_economic_fixture(fixture)["verdict"] == economic.ECONOMIC_BLOCKED_VERDICT


def test_production_isolation_scope_is_narrow() -> None:
    implementation = _implementation_commit()
    if implementation is None:
        pytest.skip("A25 is not committed yet")
    assert sorted(economic.changed_paths(economic.STARTING_HEAD, implementation)) == sorted(
        economic.IMPLEMENTATION_PATHS
    )
    assert not any(path.startswith("src/") for path in economic.changed_paths(economic.STARTING_HEAD, implementation))
    assert "config/settings.yaml" not in economic.changed_paths(economic.STARTING_HEAD, implementation)


def test_prior_r5_and_r2_artifacts_are_immutable() -> None:
    current = economic.git_head()
    for path in (
        economic.R5_RECEIPT_PATH,
        economic.R5_MANIFEST_PATH,
        economic.R2_RECEIPT_PATH,
        economic.R2_MANIFEST_PATH,
    ):
        assert economic.tracked_blob(current, path) == economic.tracked_blob(economic.STARTING_HEAD, path)


def test_manifest_topology_and_identity() -> None:
    implementation, manifest_commit = _require_manifest()
    manifest = economic._json_blob(manifest_commit, economic.MANIFEST_PATH)  # type: ignore[attr-defined]
    economic.validate_manifest_payload(manifest, implementation, manifest_commit=manifest_commit)


def test_manifest_commit_changes_only_manifest() -> None:
    implementation, manifest_commit = _require_manifest()
    assert economic.changed_paths(implementation, manifest_commit) == [economic.MANIFEST_PATH]


def test_predeclaration_manifest_has_no_result_digest() -> None:
    implementation, _manifest = _require_manifest()
    manifest = economic.build_predeclaration_manifest(implementation)
    assert "self_digest_sha256" not in manifest
    assert "remote_ci" not in manifest


def test_manifest_has_future_output_contract_but_no_output() -> None:
    implementation, _manifest = _require_manifest()
    manifest = economic.build_predeclaration_manifest(implementation)
    output = manifest["future_raw_output_contract"]
    assert output["ignored_output_directory"] == economic.RAW_OUTPUT_DIR
    assert economic.REPO.joinpath(economic.RAW_OUTPUT_DIR).exists() is False


def test_contract_validation_is_non_economic() -> None:
    result = economic.validate_contract()
    assert result["economic_execution_run"] is False
    assert result["economic_results_present"] is False
    assert result["real_execution_authorized"] is False


def test_manifest_stores_exact_observation_boundaries() -> None:
    implementation, _manifest = _require_manifest()
    manifest = economic.build_predeclaration_manifest(implementation)
    assert manifest["exact_observation_identities"]["control_C"]["boundary"] == "C|C"  # type: ignore[index]
    assert manifest["exact_observation_identities"]["candidate_X"]["boundary"] == "X|X"  # type: ignore[index]


def test_manifest_next_mission_is_execution_only() -> None:
    implementation, _manifest = _require_manifest()
    manifest = economic.build_predeclaration_manifest(implementation)
    assert manifest["next_required_mission"] == "M3_ECONOMIC_MISSION_EXECUTION"
    assert manifest["real_execution_authorized"] is False
