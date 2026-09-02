from __future__ import annotations

import ast
import copy
import json
import socket
import subprocess
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from parayabuck.backtest.observation_parity import canonical_digest, strategy_identity
from parayabuck.config import Config
from parayabuck.core.models import Bar, Market
from parayabuck.portfolio.equity_residual_momentum_v3 import EquityResidualMomentum1dV3
from scripts import compact_equity_m3_candidate_ocp_receipt as compactor
from scripts import research_equity_m3_candidate_ocp as ocp

PARAMETERS: dict[str, object] = {
    "formation_bars": 60,
    "skip_bars": 5,
    "beta_window_bars": 65,
    "residual_vol_window": 20,
    "top_n": 2,
    "max_single_weight": 0.10,
    "ivol_reject_quantile": 0.70,
    "exclude_symbols": ["SPY"],
    "rebalance_frequency": "weekly",
}


class FixtureRanker(EquityResidualMomentum1dV3):
    name = "equity_m3_fixture_ranker_v1"
    version = "1.0.0"

    @property
    def minimum_history_bars(self) -> int:
        return 3

    def rank_candidates(self, context):  # type: ignore[no-untyped-def]
        return tuple(symbol for symbol in ("AAA", "BBB") if symbol in context.history)


def _bar(symbol: str, timestamp: datetime, close: float) -> Bar:
    return Bar(
        timestamp=timestamp,
        symbol=symbol,
        timeframe="1d",
        open=close * 0.99,
        high=close * 1.01,
        low=close * 0.98,
        close=close,
        volume=1_000_000.0,
        market=Market.EQUITY,
    )


def _input(*, future: bool = False) -> ocp.SnapshotInput:
    start = datetime(2025, 1, 1, tzinfo=UTC)
    effective = start + timedelta(days=4)
    history = {
        symbol: tuple(_bar(symbol, start + timedelta(days=index), 100 + index + offset) for index in range(3))
        for symbol, offset in (("SPY", 0), ("IWM", 2), ("QQQ", 4), ("AAA", 6), ("BBB", 8))
    }
    if future:
        history["AAA"] = (*history["AAA"], _bar("AAA", effective + timedelta(days=1), 9_999))
    return ocp.SnapshotInput(
        effective_at=effective,
        latest_input_timestamp=start + timedelta(days=3),
        candidate_count=2,
        eligible_candidates=("AAA", "BBB"),
        scanner_fallback=("BBB", "AAA"),
        history=history,
        scanner_version=ocp.R5_SCANNER_VERSION,
        scanner_config_digest=ocp.R5_SCANNER_CONFIG_DIGEST,
    )


def _config() -> Config:
    base = Config()
    return replace(
        base,
        universe=replace(
            base.universe,
            equity=replace(
                base.universe.equity,
                maximum_stream_symbols=5,
                inverse_trade_symbols=(),
                fallback_trade_symbols=(),
            ),
        ),
    )


def _fixture_basis(strategy: FixtureRanker) -> ocp.ObservationBasis:
    config = _config().universe.equity
    pins = ocp._configured_pins(config)
    item = ocp._basis_snapshot(strategy, _input(), pins=pins, maximum_stream_symbols=5)
    universe = ocp._to_universe(strategy, (item,))
    return ocp.ObservationBasis(
        strategy_identity=strategy_identity(strategy),
        snapshots=(item,),
        universe=universe,
        raw_payload={"snapshots": [ocp._record_snapshot(item)]},
        semantic_hash=ocp.hash_point_in_time_universe(universe),
        observation_merkle_root=ocp._ordered_merkle_root([item.leaf_digest]),
        fallback_snapshot_count=int(item.source_classification == "scanner_quality_fallback"),
        warmup_snapshot_count=int(item.source_classification == "warmup/pins-only"),
        full_capacity_snapshot_count=int(len(item.observation_set) == 5),
    )


def _fake_tracked_identity(commit: str, path: str) -> dict[str, object]:
    return {"path": path, "commit": commit, "git_blob_id": "0" * 40, "raw_sha256": "1" * 64}


def _explode_external(name: str):  # type: ignore[no-untyped-def]
    def explode(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        raise AssertionError(f"{name} must not be called while building the manifest")

    return explode


def _build_hermetic_manifest(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, object]:
    missing = tmp_path / "absent-external-input"
    for name in (
        "R4_PIT_PATH",
        "R4_SIDECAR_PATH",
        "R4_COMPOSITE_PATH",
        "R4_BUNDLE_PATH",
        "CANONICAL_PATH",
        "LIFETIMES_PATH",
        "CORPORATE_ACTIONS_PATH",
        "DEFAULT_R5_PROJECTION",
        "STALE_R5_PROJECTION",
        "DEFAULT_RAW_DIR",
    ):
        monkeypatch.setattr(ocp, name, missing / name)
    monkeypatch.setattr(ocp, "_tracked_identity", _fake_tracked_identity)
    for name in ("resolve_r5_projection", "regenerate_r5_projection", "validate_iwm_bundle", "stage_file"):
        monkeypatch.setattr(ocp, name, _explode_external(name), raising=False)
    monkeypatch.setattr(socket, "socket", _explode_external("socket.socket"))
    return ocp.build_manifest()


def _patch_tracked_r5_blob(monkeypatch: pytest.MonkeyPatch, mutate) -> None:  # type: ignore[no-untyped-def]
    real_blob = ocp._git_blob
    real_sha256 = ocp._sha256_bytes
    original = real_blob(ocp.R5_RESULT_COMMIT, ocp.R5_PIT_MANIFEST_PATH)
    payload = json.loads(original)
    mutate(payload)
    mutated = json.dumps(payload, separators=(",", ":")).encode("utf-8")

    def fake_blob(commit: str, path: str) -> bytes:
        if (commit, path) == (ocp.R5_RESULT_COMMIT, ocp.R5_PIT_MANIFEST_PATH):
            return mutated
        return real_blob(commit, path)

    def fake_sha256(value: bytes) -> str:
        if value == mutated:
            return ocp.R5_PIT_MANIFEST_RAW_SHA256
        return real_sha256(value)

    monkeypatch.setattr(ocp, "_git_blob", fake_blob)
    monkeypatch.setattr(ocp, "_sha256_bytes", fake_sha256)


def _compactor_fixture(*, verdict: str = compactor.PASS) -> dict[str, object]:
    raw: dict[str, object] = {
        "mission": ocp.MISSION,
        "verdict": verdict,
        "research_only": True,
        "candidate_economics_run": False,
        "economic_metrics_present": False,
        "economic_mission_authorized": False,
        "economic_mission_run": False,
        "input_artifacts": {"raw_outputs": {}},
        "C_control": {},
        "X_candidate": {},
        "strategy_identities": {
            "object_instance_separation": {
                "C_observer_1_is_C_execution": False,
                "X_observer_1_is_X_execution": False,
            }
        },
        "C|C": {"identities_equal": True},
        "X|C": {"identities_intentionally_different": True, "diagnostic_only": True},
        "X|X": {"identities_equal": True},
        "blocked_reasons": [] if verdict == compactor.PASS else ["fixture_blocked"],
        "mandatory_gate_results": {name: {"status": "PASS"} for name in ocp.OCP_MANDATORY_GATES},
    }
    raw["raw_digest_sha256"] = canonical_digest(raw)
    return raw


def test_strategy_identity_and_fresh_instance_boundary() -> None:
    first = FixtureRanker(PARAMETERS)
    second = FixtureRanker(PARAMETERS)
    assert first is not second
    assert strategy_identity(first) == strategy_identity(second)
    with pytest.raises(ValueError, match="fresh instances"):
        ocp.assert_strategy_identity(first, first)


def test_references_are_pins_not_candidates_and_configured_pins_reduce_capacity() -> None:
    config = _config().universe.equity
    pins = ocp._configured_pins(config)
    assert pins == ocp.REFERENCE_SYMBOLS
    assert not set(("SPY", "IWM", "QQQ")) & {"AAA", "BBB"}
    assert config.maximum_stream_symbols - len(pins) == 2
    basis = _fixture_basis(FixtureRanker(PARAMETERS))
    observation = basis.snapshots[0].observation_set
    assert observation[:3] == ocp.REFERENCE_SYMBOLS
    assert len(observation) <= 5
    assert len(observation) == len(set(observation))
    assert set(observation[3:]) <= {"AAA", "BBB"}


def test_direct_builder_matches_full_fixture_path_and_fallback_is_real() -> None:
    strategy = FixtureRanker(PARAMETERS)
    item = _input()
    config = _config().universe.equity
    basis = _fixture_basis(strategy)
    direct = ocp._direct_builder_result(strategy, item, ocp._configured_pins(config), 5)
    assert direct == basis.snapshots[0].observation_set
    assert basis.snapshots[0].source_classification == "strategy_alpha"

    no_rank = FixtureRanker(PARAMETERS)
    no_rank.rank_candidates = lambda _context: ()  # type: ignore[method-assign]
    fallback_item = ocp._basis_snapshot(no_rank, item, pins=ocp._configured_pins(config), maximum_stream_symbols=5)
    assert fallback_item.source_classification == "scanner_quality_fallback"


def test_strict_history_discards_future_mutation() -> None:
    item = _input(future=True)
    strict = ocp._strict_history(item.history, item.latest_input_timestamp)
    assert all(bar.timestamp <= item.latest_input_timestamp for bars in strict.values() for bar in bars)
    strategy = FixtureRanker(PARAMETERS)
    clean = ocp._direct_builder_result(strategy, _input(), ocp.REFERENCE_SYMBOLS, 5)
    mutated = ocp._direct_builder_result(strategy, item, ocp.REFERENCE_SYMBOLS, 5)
    assert mutated == clean


def test_r5_projection_identity_rejects_altered_hash(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    projection = tmp_path / "projection.json"
    projection.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(ocp, "R5_PROJECTION_RAW_SHA256", "0" * 64)
    with pytest.raises(ocp.OcpFailure, match="size mismatch|raw SHA"):
        ocp._validate_projection(projection, {})


def test_r5_manifest_erratum_is_not_accepted_from_display_alias() -> None:
    upstream = ocp._validate_r5_upstream()
    erratum = upstream["upstream_erratum"]
    assert erratum["recorded_value"] == ocp.R5_IMPLEMENTATION
    assert erratum["authoritative_value"] == ocp.R5_IMPLEMENTATION_PARENT
    assert erratum["recorded_value"] != erratum["authoritative_value"]
    assert upstream["actual_implementation_parent"] == ocp._git("rev-parse", f"{ocp.R5_IMPLEMENTATION}^")


def test_r5_receipt_for_another_manifest_is_rejected(tmp_path: Path) -> None:
    other_manifest = tmp_path / "other-manifest.json"
    other_manifest.write_bytes((ocp._REPO / ocp.R4_PIT_MANIFEST_PATH).read_bytes())
    with pytest.raises(ocp.OcpFailure, match="R5 manifest"):
        ocp._validate_r5_upstream(other_manifest)


def test_r5_compact_pit_manifest_is_bound_to_c11_result_tree() -> None:
    identity = ocp._tracked_identity(ocp.R5_RESULT_COMMIT, ocp.R5_PIT_MANIFEST_PATH)
    assert identity["git_blob_id"] == ocp.R5_PIT_MANIFEST_BLOB


def test_r5_receipt_self_digest_and_all_mandatory_gates_are_pass() -> None:
    upstream = ocp._validate_r5_upstream()
    receipt = upstream["receipt"]
    body = dict(receipt)
    digest = body.pop("self_digest_sha256")
    assert digest == ocp.R5_RECEIPT_SELF_DIGEST
    assert canonical_digest(body) == digest
    assert all(value["status"] == "PASS" for value in receipt["mandatory_gate_results"].values())  # type: ignore[index]


def test_historical_generic_ocp_attestation_is_read_only() -> None:
    result = ocp._e1_attestation()
    assert result["status"] == "PASS"
    assert result["github_actions_run"] == "33439399546"


def test_compactor_rejects_raw_scores_and_economic_metrics() -> None:
    raw = {"mission": ocp.MISSION, "raw_digest_sha256": ""}
    raw["raw_digest_sha256"] = canonical_digest({"mission": ocp.MISSION})
    with pytest.raises(ValueError, match="research-only|verdict"):
        compactor.validate_raw(raw)

    forbidden = {"return": 0.1}
    assert compactor._forbidden_keys(forbidden) == ("return",)


def test_no_engine_or_network_execution_is_present_in_ocp_source() -> None:
    source = Path(ocp.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported = {alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
    assert "requests" not in imported
    assert "socket" not in imported
    assert "PortfolioBacktestEngine.run" not in source
    assert "run_product_backtest" not in source


@pytest.mark.parametrize("status", ["UNKNOWN", "NOT_RUN_BLOCKED"])
def test_unknown_and_not_run_gate_states_are_blocked_by_compactor(status: str, tmp_path: Path) -> None:
    raw = _compactor_fixture(verdict=compactor.BLOCKED)
    gates = raw["mandatory_gate_results"]
    assert isinstance(gates, dict)
    gates["C_control_self_parity"] = {"status": status}
    raw["raw_digest_sha256"] = canonical_digest(
        {key: value for key, value in raw.items() if key != "raw_digest_sha256"}
    )
    compactor.validate_raw(raw)
    raw_path = tmp_path / "blocked-raw.json"
    output = tmp_path / "blocked-receipt.json"
    raw_path.write_text(json.dumps(raw), encoding="utf-8")
    receipt = compactor.compact(raw_path, output, remote_ci_status="PASS")
    assert receipt["verdict"] == compactor.BLOCKED
    assert receipt["mandatory_gate_results"]["C_control_self_parity"]["status"] == status  # type: ignore[index]


def test_complete_synthetic_ocp_fixture_compacts_to_pass(tmp_path: Path) -> None:
    raw = _compactor_fixture()
    raw_path = tmp_path / "raw.json"
    output = tmp_path / "receipt.json"
    raw_path.write_text(json.dumps(raw), encoding="utf-8")
    receipt = compactor.compact(raw_path, output, remote_ci_status="PASS")
    assert receipt["verdict"] == compactor.PASS
    assert receipt["candidate_economics_run"] is False
    assert receipt["economic_mission_authorized"] is False


def test_ignored_gate_outputs_do_not_retroactively_fail_cleanliness() -> None:
    probe = ocp.DEFAULT_RAW_DIR / "cleanliness-probe.json"
    result = subprocess.run(["git", "check-ignore", "-q", str(probe)], cwd=ocp._REPO)
    assert result.returncode == 0
    before = ocp._dirty_paths()
    probe.parent.mkdir(parents=True, exist_ok=True)
    probe.write_text("{}", encoding="utf-8")
    try:
        assert ocp._dirty_paths() == before
    finally:
        probe.unlink(missing_ok=True)


def test_manifest_build_has_frozen_contract_and_no_result_fields(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    manifest = _build_hermetic_manifest(monkeypatch, tmp_path)
    assert manifest["declared_before_results"] is True
    assert manifest["accepted_upstream_errata"]["non_load_bearing"] is True  # type: ignore[index]
    assert "verdict" not in manifest
    assert manifest["evaluation_snapshot_contract"]["count"] == ocp.R5_SNAPSHOT_COUNT  # type: ignore[index]
    assert (  # type: ignore[index]
        manifest["evaluation_snapshot_contract"]["effective_at_sequence_digest_sha256"]
        == ocp.R5_EFFECTIVE_AT_SEQUENCE_DIGEST
    )
    assert manifest["accepted_data_identities"]["r5_pit_manifest_git_blob"] == ocp.R5_PIT_MANIFEST_BLOB  # type: ignore[index]
    assert manifest["accepted_data_identities"]["r5_projection_digest"] == ocp.R5_PROJECTION_DIGEST  # type: ignore[index]
    assert manifest["critical_git_blobs"]  # type: ignore[truthy-bool]
    assert set(ocp.REFERENCE_SYMBOLS) == {"SPY", "IWM", "QQQ"}


def test_build_manifest_requires_no_untracked_r4_pit(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    manifest = _build_hermetic_manifest(monkeypatch, tmp_path)
    assert manifest["declared_before_results"] is True
    assert not (tmp_path / "absent-external-input" / "R4_PIT_PATH").exists()


def test_build_manifest_requires_no_r5_projection_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    manifest = _build_hermetic_manifest(monkeypatch, tmp_path)
    assert manifest["accepted_data_identities"]["r5_projection_raw_sha256"] == ocp.R5_PROJECTION_RAW_SHA256  # type: ignore[index]
    assert not (tmp_path / "absent-external-input" / "DEFAULT_R5_PROJECTION").exists()


def test_build_manifest_does_not_call_projection_resolver(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    manifest = _build_hermetic_manifest(monkeypatch, tmp_path)
    assert manifest["mission"] == ocp.MISSION


def test_build_manifest_does_not_stage_market_data(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    manifest = _build_hermetic_manifest(monkeypatch, tmp_path)
    assert manifest["no_network_or_credentials"] is True


def test_build_manifest_uses_tracked_r5_pit_blob(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, str]] = []
    real_blob = ocp._git_blob

    def spy_blob(commit: str, path: str) -> bytes:
        calls.append((commit, path))
        return real_blob(commit, path)

    monkeypatch.setattr(ocp, "_tracked_identity", _fake_tracked_identity)
    monkeypatch.setattr(ocp, "_git_blob", spy_blob)
    manifest = ocp.build_manifest()
    assert (ocp.R5_RESULT_COMMIT, ocp.R5_PIT_MANIFEST_PATH) in calls
    assert manifest["accepted_upstream_r5"]["pit_manifest_git_blob_id"] == ocp.R5_PIT_MANIFEST_BLOB  # type: ignore[index]


def test_build_manifest_rejects_wrong_r5_pit_blob_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ocp, "R5_PIT_MANIFEST_BLOB", "0" * 40)
    with pytest.raises(ocp.OcpFailure, match="Git blob changed"):
        ocp._tracked_r5_snapshot_contract()


def test_build_manifest_rejects_changed_r5_snapshot_digest(monkeypatch: pytest.MonkeyPatch) -> None:
    def mutate(payload: dict[str, object]) -> None:
        boundary = payload["evaluation_boundary"]
        assert isinstance(boundary, dict)
        timestamps = boundary["evaluation_effective_at_timestamps"]
        assert isinstance(timestamps, list)
        timestamps[100] = timestamps[99]

    _patch_tracked_r5_blob(monkeypatch, mutate)
    with pytest.raises(ocp.OcpFailure, match="sequence digest changed"):
        ocp._tracked_r5_snapshot_contract()


def test_build_manifest_rejects_changed_r5_candidate_universe_digest(monkeypatch: pytest.MonkeyPatch) -> None:
    def mutate(payload: dict[str, object]) -> None:
        payload["candidate_universe_digest_sha256"] = "0" * 64
        boundary = payload["evaluation_boundary"]
        assert isinstance(boundary, dict)
        boundary["candidate_population_digest_sha256"] = "0" * 64

    _patch_tracked_r5_blob(monkeypatch, mutate)
    with pytest.raises(ocp.OcpFailure, match="candidate_universe_digest_sha256"):
        ocp._tracked_r5_snapshot_contract()


def test_build_manifest_rejects_changed_r5_projection_digest(monkeypatch: pytest.MonkeyPatch) -> None:
    def mutate(payload: dict[str, object]) -> None:
        payload["projection_digest_sha256"] = "0" * 64
        boundary = payload["evaluation_boundary"]
        assert isinstance(boundary, dict)
        boundary["projection_digest_sha256"] = "0" * 64

    _patch_tracked_r5_blob(monkeypatch, mutate)
    with pytest.raises(ocp.OcpFailure, match="projection_digest_sha256"):
        ocp._tracked_r5_snapshot_contract()


def test_build_manifest_succeeds_when_generated_and_external_directories_are_absent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    manifest = _build_hermetic_manifest(monkeypatch, tmp_path)
    assert manifest["declared_before_results"] is True
    assert not (tmp_path / "absent-external-input").exists()


def test_real_ocp_run_still_blocks_when_r4_pit_is_missing(tmp_path: Path) -> None:
    missing = tmp_path / "m3_data_gate_r4" / "pit_universe.json"
    with pytest.raises(ocp.OcpFailure, match="required artifact is missing"):
        ocp._canonical_identity(missing, expected_raw=ocp.R4_RAW_PIT_SHA256)


def test_real_ocp_run_still_blocks_when_r5_projection_hash_mismatches(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    projection = tmp_path / "projection.json"
    projection.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(ocp, "R5_PROJECTION_RAW_SHA256", "0" * 64)
    with pytest.raises(ocp.OcpFailure, match="raw SHA mismatch"):
        ocp._validate_projection(projection, {})


def test_real_ocp_run_still_blocks_when_iwm_sidecar_is_missing(tmp_path: Path) -> None:
    missing = tmp_path / "m3_data_gate_r4" / "iwm_normalized.csv"
    with pytest.raises(ocp.OcpFailure, match="required artifact is missing"):
        ocp._canonical_identity(missing, expected_raw=ocp.R4_SIDECAR_RAW_SHA256)


def test_manifest_creation_performs_no_network_or_credential_access(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    commands: list[tuple[str, ...]] = []
    real_run = ocp.subprocess.run

    def guarded_run(command, *args, **kwargs):  # type: ignore[no-untyped-def]
        commands.append(tuple(str(item) for item in command))
        if not command or command[0] != "git":
            raise AssertionError(f"non-Git transport or credential command: {command}")
        return real_run(command, *args, **kwargs)

    monkeypatch.setattr(ocp, "_tracked_identity", _fake_tracked_identity)
    monkeypatch.setattr(ocp.subprocess, "run", guarded_run)
    monkeypatch.setattr(socket, "socket", _explode_external("socket.socket"))
    manifest = ocp.build_manifest()
    assert manifest["no_network_or_credentials"] is True
    assert commands and all(command[0] == "git" for command in commands)


def test_manifest_creation_is_deterministic(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    first = _build_hermetic_manifest(monkeypatch, tmp_path)
    second = ocp.build_manifest()
    assert first == second


def test_manifest_creation_is_independent_of_working_copy_external_files(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    external = tmp_path / "working-copy-external"
    external.mkdir()
    (external / "pit_universe.json").write_text('{"snapshots": []}', encoding="utf-8")
    manifest = _build_hermetic_manifest(monkeypatch, tmp_path)
    assert manifest["accepted_data_identities"]["r5_projection_digest"] == ocp.R5_PROJECTION_DIGEST  # type: ignore[index]


def test_manifest_contains_no_raw_alpha_scores(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    manifest = _build_hermetic_manifest(monkeypatch, tmp_path)
    body = {key: value for key, value in manifest.items() if key != "forbidden_economic_fields"}
    text = json.dumps(body, sort_keys=True).lower()
    assert "alpha_score" not in text
    assert "candidate_ranking" not in text


def test_manifest_contains_no_economic_fields(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    manifest = _build_hermetic_manifest(monkeypatch, tmp_path)
    forbidden = set(ocp.FORBIDDEN_ECONOMIC_FIELDS)

    def keys(value: object):  # type: ignore[no-untyped-def]
        if isinstance(value, dict):
            for key, child in value.items():
                yield str(key)
                yield from keys(child)
        elif isinstance(value, list):
            for child in value:
                yield from keys(child)

    checked = {
        key for key in keys({key: value for key, value in manifest.items() if key != "forbidden_economic_fields"})
    }
    assert not checked.intersection(forbidden)


def test_ci_focused_command_passes_without_external_artifacts(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    workflow = (ocp._REPO / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert (
        "pytest -q tests/test_candidate_observation_parity.py tests/test_research_equity_m3_candidate_ocp.py tests/test_normalized_hash.py"
        in workflow
    )
    assert _build_hermetic_manifest(monkeypatch, tmp_path)["declared_before_results"] is True


def test_prior_C22_result_remains_immutable_in_git_history() -> None:
    assert ocp._git("rev-parse", f"{ocp.PRIOR_C22_COMMIT}^") == ocp.PRIOR_B22_COMMIT
    assert ocp._git("cat-file", "-t", ocp.PRIOR_C22_COMMIT) == "commit"
    assert ocp._git("diff-tree", "--no-commit-id", "--name-only", "-r", ocp.PRIOR_C22_COMMIT).splitlines() == [
        "data/research_receipts/equity_m3_candidate_ocp_2026_09.json",
        "docs/equity_m3_candidate_ocp_2026_09.md",
    ]


def test_identity_parameter_digest_rejects_one_field_change() -> None:
    first = FixtureRanker(PARAMETERS)
    altered = copy.deepcopy(PARAMETERS)
    altered["top_n"] = 3
    second = FixtureRanker(altered)
    assert strategy_identity(first)["parameter_digest_sha256"] != strategy_identity(second)["parameter_digest_sha256"]
