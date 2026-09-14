from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

import scripts.research.r46_treasury_auction_unwind_tlt_v3_recovery_2026_09_14 as runner


def _write_state(tmp_path: Path) -> Path:
    payload: dict[str, object] = {
        "schema_version": "r46-recovery-one-shot-state-v3",
        "research_id": runner.V3_RESEARCH_ID,
        "candidate_id": "R46",
        "recovery_generation": 1,
        "status": "SEALED_UNUSED",
        "integrity": {"artifact_sha256": ""},
    }
    payload["integrity"]["artifact_sha256"] = runner._canonical_hash(payload, "artifact_sha256")  # type: ignore[index]
    path = tmp_path / "state.json"
    path.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _pins() -> dict[str, str]:
    return {
        "preflight_receipt_sha256": "a" * 64,
        "authorization_sha256": "b" * 64,
        "v3_governance_sha256": "c" * 64,
        "v3_execution_contract_sha256": "d" * 64,
        "parent_v2_governance_sha256": "e" * 64,
        "parent_v2_package_sha256": "f" * 64,
        "v2_reservation_physical_sha256": "1" * 64,
        "v2_reservation_transition_sha256": "2" * 64,
    }


def _valid_authorization(root: Path) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": runner.AUTHORIZATION_SCHEMA,
        "candidate_id": "R46",
        "research_id": runner.V3_RESEARCH_ID,
        "recovery_generation": 1,
        "authorized": True,
        "economic_execution_authorized": True,
        "preflight_receipt_sha256": "a" * 64,
        "execution_contract_sha256": "b" * 64,
        "source_commit": "1" * 40,
        "freeze_commit": "2" * 40,
        "v3_state_canonical_sha256": runner.V3_STATE_CANONICAL_SHA256,
        "v2_reservation_physical_sha256": runner.V2_RESERVED_SHA256,
        "authorization_nonce": "synthetic-only-nonce",
        "issued_for_output_directory": str(root / runner.V3_OUTPUT_DIR_REL),
        "rerun_authorized": False,
        "parameter_change_authorized": False,
        "promotion_authorized": False,
        "integrity": {"authorization_sha256": ""},
    }
    payload["integrity"]["authorization_sha256"] = runner._canonical_hash(  # type: ignore[index]
        payload, "authorization_sha256"
    )
    return payload


def test_pristine_state_reports_unused(tmp_path: Path) -> None:
    state = _write_state(tmp_path)
    assert runner.recovery_one_shot_status(state, require_crosslinks=False) == "SEALED_UNUSED"


def test_reservation_is_exclusive_and_binds_every_parent(tmp_path: Path) -> None:
    state = _write_state(tmp_path)
    result = runner.reserve_recovery_one_shot(state, **_pins())
    assert result["status"] == "RESERVED"
    sidecar = state.with_name(f"{state.name}.reserved.json")
    payload = json.loads(sidecar.read_text(encoding="utf-8"))
    assert payload["schema_version"] == runner.TRANSITION_SCHEMA
    assert payload["status"] == "RESERVED"
    assert set(_pins()).issubset(payload)
    assert payload["integrity"]["transition_sha256"] == runner._canonical_hash(payload, "transition_sha256")
    with pytest.raises(RuntimeError, match="ONE_SHOT_ALREADY_RESERVED"):
        runner.reserve_recovery_one_shot(state, **_pins())


def test_consumption_is_irreversible_and_does_not_rewrite_base_state(tmp_path: Path) -> None:
    state = _write_state(tmp_path)
    before = state.read_bytes()
    runner.reserve_recovery_one_shot(state, **_pins())
    runner.consume_recovery_one_shot(
        state,
        output_path=tmp_path / "output",
        receipt_path=tmp_path / "output" / "run_receipt.json",
        economic_output_bundle_sha256="9" * 64,
    )
    assert state.read_bytes() == before
    assert runner.recovery_one_shot_status(state) == "CONSUMED"
    with pytest.raises(RuntimeError, match="ONE_SHOT_ALREADY_CONSUMED"):
        runner.reserve_recovery_one_shot(state, **_pins())
    with pytest.raises(RuntimeError, match="ONE_SHOT_ALREADY_CONSUMED"):
        runner.consume_recovery_one_shot(
            state,
            output_path=tmp_path / "output-2",
            receipt_path=tmp_path / "output-2" / "run_receipt.json",
            economic_output_bundle_sha256="8" * 64,
        )


def test_tampered_transition_is_rejected(tmp_path: Path) -> None:
    state = _write_state(tmp_path)
    runner.reserve_recovery_one_shot(state, **_pins())
    sidecar = state.with_name(f"{state.name}.reserved.json")
    payload = json.loads(sidecar.read_text(encoding="utf-8"))
    payload["authorization_sha256"] = "0" * 64
    sidecar.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="ONE_SHOT_TRANSITION_HASH_INVALID"):
        runner.recovery_one_shot_status(state)


def test_no_unreserve_reset_or_retry_api_is_exposed() -> None:
    names = set(dir(runner))
    assert not names.intersection(
        {"unreserve_recovery_one_shot", "reset_recovery_one_shot", "retry_recovery_one_shot"}
    )


def test_missing_authorization_has_exact_fail_closed_error(tmp_path: Path) -> None:
    receipt = {"integrity": {"receipt_sha256": "a" * 64}}
    contract = {"integrity": {"contract_sha256": "b" * 64}, "source_commit": "1" * 40, "freeze_commit": "2" * 40}
    with pytest.raises(RuntimeError, match="BLOCKED_V3_EXECUTION_AUTHORIZATION"):
        runner._validate_authorization(tmp_path / "missing.json", tmp_path, receipt, contract)


@pytest.mark.parametrize(
    "field",
    (
        "preflight_receipt_sha256",
        "execution_contract_sha256",
        "source_commit",
        "freeze_commit",
        "v3_state_canonical_sha256",
        "issued_for_output_directory",
        "rerun_authorized",
        "parameter_change_authorized",
        "promotion_authorized",
    ),
)
def test_authorization_contract_mutations_block(tmp_path: Path, field: str) -> None:
    payload = _valid_authorization(tmp_path)
    if field.endswith("_commit"):
        payload[field] = "0" * 40
    elif field.endswith("_authorized"):
        payload[field] = True
    elif field == "issued_for_output_directory":
        payload[field] = str(tmp_path / "different-output")
    else:
        payload[field] = "0" * 64
    path = tmp_path / "authorization.json"
    path.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")
    receipt = {"integrity": {"receipt_sha256": "a" * 64}}
    contract = {"integrity": {"contract_sha256": "b" * 64}, "source_commit": "1" * 40, "freeze_commit": "2" * 40}
    with pytest.raises(RuntimeError, match="BLOCKED_V3_EXECUTION_AUTHORIZATION"):
        runner._validate_authorization(path, tmp_path, receipt, contract)


@pytest.mark.parametrize(
    "kwargs",
    (
        {"starting_equity": 99_999.0},
        {"cost_multipliers": (1.0,)},
        {"config": object()},
        {"universe": object()},
        {"incumbent_inputs": object()},
        {"allow_marker_fallback": True},
        {"package_path": "/tmp/alternate-package.json"},
        {"one_shot_state_path": "/tmp/alternate-state.json"},
        {"output_dir": "/tmp/alternate-output"},
    ),
)
def test_production_caller_overrides_are_rejected(tmp_path: Path, kwargs: dict[str, object]) -> None:
    with pytest.raises(RuntimeError, match="BLOCKED_V3_PRODUCTION_OVERRIDE"):
        runner.run_package_backed(repo_root=tmp_path, **kwargs)


def test_test_only_runner_rejects_real_state(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="BLOCKED_V3_TEST_ONLY_STATE"):
        runner.run_recovery_test_only(
            package=object(),
            incumbent_inputs=object(),
            output_dir=tmp_path / "output",
            one_shot_state_path=runner.REPO / runner.V3_STATE_REL,
        )


def test_independent_verifier_has_no_runner_import() -> None:
    verifier_path = Path(__file__).resolve().parents[2] / (
        "scripts/research/verify_r46_treasury_auction_unwind_tlt_v3_recovery_2026_09_14.py"
    )
    tree = ast.parse(verifier_path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            assert node.module != "scripts.research.r46_treasury_auction_unwind_tlt_v3_recovery_2026_09_14"
