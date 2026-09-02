from __future__ import annotations

import ast
import json
import subprocess
from pathlib import Path

import pytest

from parayabuck.backtest.observation_parity import canonical_digest
from scripts import compact_equity_m3_candidate_ocp_attestation_r2 as r2
from scripts import research_equity_m3_m4 as economic_consumer


def _raw_fixture() -> dict[str, object]:
    raw: dict[str, object] = {
        "mission": "M3_CANDIDATE_OCP_GATE",
        "verdict": "M3_CANDIDATE_OCP_GATE_PASS",
        "research_only": True,
        "candidate_economics_run": False,
        "economic_metrics_present": False,
        "economic_mission_authorized": False,
        "economic_mission_run": False,
        "blocked_reasons": [],
        "no_network_or_credentials": True,
        "input_artifacts": {
            "raw_outputs": {str(index): {"path": path} for index, path in enumerate(r2.R2_RAW_OUTPUT_PATHS)}
        },
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
        "implementation_identity": {
            "commit": r2.A23_IMPLEMENTATION_COMMIT,
            "critical_git_blobs": {},
        },
        "manifest_identity": {
            "commit": r2.B23_MANIFEST_COMMIT,
            "path": r2.C23_MANIFEST_PATH,
            "git_blob_id": "cbf9f07e9dbd5467ace4e938f5be0de5665d9a40",
            "raw_sha256": "6264e56135a95e3ebd2b244f5d19b0b54f2845808314393131fa9599209fe1e1",
        },
        "run_commit": r2.B23_MANIFEST_COMMIT,
        "observation_change_summary": {
            "C_universe_hash": r2.HASH_PARITY["C_semantic_universe_hash"],
            "C_merkle_root": r2.HASH_PARITY["C_ordered_observation_merkle_root"],
            "X_universe_hash": r2.HASH_PARITY["X_semantic_universe_hash"],
            "X_merkle_root": r2.HASH_PARITY["X_ordered_observation_merkle_root"],
            "sequence_change_count": r2.HASH_PARITY["sequence_change_count"],
            "membership_change_count": r2.HASH_PARITY["membership_change_count"],
            "scanner_metadata_mismatch_counts": {"all": 0},
        },
        "snapshot_coverage": {"count": r2.HASH_PARITY["snapshot_count"]},
        "direct_builder_parity": {
            "sample_count": r2.HASH_PARITY["direct_builder_sample_count"],
            "mismatch_count": r2.HASH_PARITY["direct_builder_mismatch_count"],
        },
        "future_bar_tests": {
            "sample_count": r2.HASH_PARITY["future_bar_sample_count"],
            "failure_count": r2.HASH_PARITY["future_bar_failure_count"],
        },
        "mandatory_gate_results": {f"gate_{index}": {"status": "PASS"} for index in range(42)},
    }
    raw["raw_digest_sha256"] = canonical_digest(raw)
    return raw


def _substantive_claim() -> dict[str, object]:
    raw = _raw_fixture()
    return r2._substantive_replay(
        raw,
        {
            "manifest_git_blob_id": "cbf9f07e9dbd5467ace4e938f5be0de5665d9a40",
            "raw_output_paths": list(r2.R2_RAW_OUTPUT_PATHS),
            "local_receipt_self_digest": r2.C23_LOCAL_RECEIPT_DIGEST,
            "mandatory_gate_count": 42,
        },
    )


def _receipt_shell() -> dict[str, object]:
    receipt: dict[str, object] = {
        "schema_version": "2.0.0",
        "mission": r2.R2_MISSION,
        "verdict": r2.R2_PASS,
        "research_only": True,
        "automatic_promotion": False,
        "human_promotion_only": True,
        "candidate_economics_run": False,
        "economic_metrics_present": False,
        "economic_mission_authorized": False,
        "blocked_reasons": [],
        "substantive_replay": _substantive_claim(),
    }
    receipt["self_digest_sha256"] = canonical_digest(receipt)
    return receipt


def test_d23_regression_old_raw_with_new_ci_is_rejected() -> None:
    raw = _raw_fixture()
    raw["implementation_identity"] = {
        "commit": r2.A22_IMPLEMENTATION_COMMIT,
        "critical_git_blobs": {},
    }
    with pytest.raises(r2.AttestationError, match="raw implementation commit"):
        r2._validate_raw_declarations(raw)


def test_ci_run_id_without_head_sha_is_rejected() -> None:
    with pytest.raises(r2.AttestationError, match="head SHA"):
        r2._corrective_ci_claim(
            status="PASS",
            run_id="123456",
            head_sha="",
            local_result_commit=r2.C23_RESULT_COMMIT,
        )


def test_ci_head_must_equal_declared_result_commit() -> None:
    with pytest.raises(r2.AttestationError, match="head SHA"):
        r2._corrective_ci_claim(
            status="PASS",
            run_id="123456",
            head_sha=r2.C23_RESULT_COMMIT,
            local_result_commit=r2.B23_MANIFEST_COMMIT,
        )


def test_raw_implementation_must_equal_declared_implementation() -> None:
    raw = _raw_fixture()
    raw["implementation_identity"] = {"commit": r2.B23_MANIFEST_COMMIT, "critical_git_blobs": {}}
    with pytest.raises(r2.AttestationError, match="raw implementation commit"):
        r2._validate_raw_declarations(raw)


def test_raw_manifest_must_equal_declared_manifest() -> None:
    raw = _raw_fixture()
    raw["manifest_identity"] = {
        "commit": r2.A23_IMPLEMENTATION_COMMIT,
        "path": r2.C23_MANIFEST_PATH,
        "git_blob_id": "cbf9f07e9dbd5467ace4e938f5be0de5665d9a40",
        "raw_sha256": "6264e56135a95e3ebd2b244f5d19b0b54f2845808314393131fa9599209fe1e1",
    }
    with pytest.raises(r2.AttestationError, match="raw manifest commit"):
        r2._validate_raw_declarations(raw)


def test_raw_path_must_be_explicit_and_versioned() -> None:
    with pytest.raises(r2.AttestationError, match="explicit versioned path"):
        r2.validate_substantive_raw(
            _raw_fixture(),
            raw_path=r2.REPO / "data/research/generated/equity_m3_candidate_ocp_2026_09/ocp_run.json",
            expected_raw_sha256=r2.C23_RAW_SHA256,
            expected_raw_payload_digest=r2.C23_RAW_PAYLOAD_DIGEST,
        )


def test_default_raw_path_cannot_select_c22_artifact() -> None:
    with pytest.raises(r2.AttestationError, match="explicit versioned path"):
        r2.validate_substantive_raw(
            _raw_fixture(),
            raw_path=r2.REPO / "data/research/generated/equity_m3_candidate_ocp_2026_09/ocp_run.json",
            expected_raw_sha256=r2.C23_RAW_SHA256,
            expected_raw_payload_digest=r2.C23_RAW_PAYLOAD_DIGEST,
        )


def test_self_digest_does_not_override_provenance_mismatch() -> None:
    receipt = _receipt_shell()
    substantive = receipt["substantive_replay"]
    assert isinstance(substantive, dict)
    substantive["raw_artifact_path"] = "data/research/generated/equity_m3_candidate_ocp_2026_09/ocp_run.json"
    receipt["self_digest_sha256"] = canonical_digest(
        {key: value for key, value in receipt.items() if key != "self_digest_sha256"}
    )
    r2._validate_receipt_common(receipt)
    with pytest.raises(r2.AttestationError, match="raw_artifact_path"):
        r2._validate_substantive_receipt_claim(receipt)


def test_green_ci_does_not_override_provenance_mismatch() -> None:
    receipt = _receipt_shell()
    substantive = receipt["substantive_replay"]
    assert isinstance(substantive, dict)
    substantive["local_result_commit"] = r2.B23_MANIFEST_COMMIT
    receipt["self_digest_sha256"] = canonical_digest(
        {key: value for key, value in receipt.items() if key != "self_digest_sha256"}
    )
    r2._validate_receipt_common(receipt)
    with pytest.raises(r2.AttestationError, match="local_result_commit"):
        r2._validate_substantive_receipt_claim(receipt)


def test_c23_raw_sha_mismatch_blocks() -> None:
    with pytest.raises(r2.AttestationError, match="expected raw SHA"):
        r2.validate_substantive_raw(
            _raw_fixture(),
            raw_path=r2.REPO / r2.R2_RAW_PATH,
            expected_raw_sha256="0" * 64,
            expected_raw_payload_digest=r2.C23_RAW_PAYLOAD_DIGEST,
        )


def test_c23_raw_payload_digest_mismatch_blocks() -> None:
    with pytest.raises(r2.AttestationError, match="expected raw payload digest"):
        r2.validate_substantive_raw(
            _raw_fixture(),
            raw_path=r2.REPO / r2.R2_RAW_PATH,
            expected_raw_sha256=r2.C23_RAW_SHA256,
            expected_raw_payload_digest="0" * 64,
        )


def test_c23_local_receipt_digest_mismatch_blocks() -> None:
    claim = _substantive_claim()
    claim["local_receipt_self_digest"] = "0" * 64
    receipt = {"substantive_replay": claim}
    with pytest.raises(r2.AttestationError, match="local_receipt_self_digest"):
        r2._validate_substantive_receipt_claim(receipt)


def test_manifest_only_commit_is_required() -> None:
    with pytest.raises(r2.AttestationError, match="manifest commit must change exactly"):
        r2._validate_manifest_commit(r2.B23_MANIFEST_COMMIT, r2.A23_IMPLEMENTATION_COMMIT)


def test_critical_blob_drift_blocks() -> None:
    path = "scripts/research_equity_m3_candidate_ocp.py"
    identity = r2._git_blob_identity(r2.A23_IMPLEMENTATION_COMMIT, path)
    identity["raw_sha256"] = "0" * 64
    with pytest.raises(r2.AttestationError, match="critical blob SHA"):
        r2._validate_critical_blobs({path: identity}, (r2.A23_IMPLEMENTATION_COMMIT,))


def test_remote_ci_for_another_commit_blocks() -> None:
    with pytest.raises(r2.AttestationError, match="head SHA"):
        r2._corrective_ci_claim(
            status="PASS",
            run_id=r2.C23_CI_RUN,
            head_sha=r2.C23_RESULT_COMMIT,
            local_result_commit=r2.B23_MANIFEST_COMMIT,
        )


def test_correct_a23_b23_c23_chain_is_accepted() -> None:
    assert r2._parents(r2.B23_MANIFEST_COMMIT) == [r2.A23_IMPLEMENTATION_COMMIT]
    assert r2._parents(r2.C23_RESULT_COMMIT) == [r2.B23_MANIFEST_COMMIT]
    assert r2._changed_paths(r2.A23_IMPLEMENTATION_COMMIT, r2.B23_MANIFEST_COMMIT) == [r2.C23_MANIFEST_PATH]
    r2._validate_historical_topology()


def test_old_unversioned_receipt_is_rejected_by_economic_consumer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert economic_consumer.M3_CANDIDATE_OCP_RECEIPT.name == "equity_m3_candidate_ocp_2026_09_r2.json"
    old_receipt = r2.REPO / "data/research_receipts/equity_m3_candidate_ocp_2026_09.json"
    monkeypatch.setattr(economic_consumer, "M3_CANDIDATE_OCP_RECEIPT", old_receipt)
    assert not economic_consumer._valid_m3_candidate_ocp_receipt()


def test_new_r2_receipt_is_accepted_by_economic_consumer(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    receipt_path = tmp_path / r2.R2_RECEIPT_PATH
    receipt_path.parent.mkdir(parents=True)
    receipt_path.write_text(json.dumps(_receipt_shell()), encoding="utf-8")
    seen: dict[str, object] = {}

    def accept(payload: object, *, receipt_path: Path | None = None) -> None:
        seen["payload"] = payload
        seen["path"] = receipt_path

    monkeypatch.setattr(economic_consumer, "M3_CANDIDATE_OCP_RECEIPT", receipt_path)
    monkeypatch.setattr(economic_consumer, "validate_r2_receipt", accept)
    assert economic_consumer._valid_m3_candidate_ocp_receipt()
    assert seen["path"] == receipt_path


def test_final_r2_receipt_is_provenance_valid_when_present() -> None:
    receipt_path = r2.REPO / r2.R2_RECEIPT_PATH
    if not receipt_path.is_file():
        pytest.skip("the R2 result receipt is created after A24")
    payload = json.loads(receipt_path.read_text(encoding="utf-8"))
    if payload.get("verdict") != r2.R2_PASS:
        pytest.skip("the R2 receipt is still pre-CI blocked")
    assert isinstance(payload, dict)
    r2.validate_r2_receipt(payload, receipt_path=receipt_path)


def test_no_economic_fields_in_r2_receipt() -> None:
    receipt = _receipt_shell()
    assert not r2._forbidden_keys(receipt)
    assert receipt["candidate_economics_run"] is False
    assert receipt["economic_metrics_present"] is False
    assert receipt["economic_mission_authorized"] is False


def test_no_network_market_data_or_credentials() -> None:
    source = Path(r2.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imports = {
        alias.name.split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names
    }
    assert not imports.intersection({"requests", "socket", "urllib", "alpaca", "yfinance"})
    assert "os.environ" not in source
    assert "keyring" not in source.lower()


def test_prior_receipts_and_reports_remain_immutable_in_git_history() -> None:
    old_receipt = subprocess.check_output(
        ["git", "show", f"{r2.C23_RESULT_COMMIT}:{r2.C23_LOCAL_RECEIPT_PATH}"],
        cwd=r2.REPO,
        text=True,
    )
    payload = json.loads(old_receipt)
    assert payload["self_digest_sha256"] == r2.C23_LOCAL_RECEIPT_DIGEST
    assert r2._parents(r2.D23_COMMIT) == [r2.C23_RESULT_COMMIT]
    assert r2._parents(r2.E23_COMMIT) == [r2.D23_COMMIT]
    assert r2._changed_paths(r2.D23_COMMIT, r2.E23_COMMIT) == ["docs/equity_m3_candidate_ocp_2026_09.md"]
