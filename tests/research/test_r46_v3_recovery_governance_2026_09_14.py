from __future__ import annotations

import ast
import hashlib
import json
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from scripts.research import r46_v3_recovery_preflight_2026_09_14 as validator

REPO = Path(__file__).resolve().parents[2]
INCIDENT = Path("data/research_receipts/r46_v2_reservation_incident_2026_09_14.json")
V2_STATE = validator.V2_STATE_REL
V2_RESERVED = validator.V2_RESERVED_REL
V2_PREFLIGHT = validator.V2_PREFLIGHT_REL
V3_STATE = validator.V3_STATE_REL
V3_MANIFEST = validator.V3_MANIFEST_REL
V3_SCHEMA = validator.V3_SCHEMA_REL
FIXTURE_FILES = (
    INCIDENT,
    V2_STATE,
    V2_RESERVED,
    V2_PREFLIGHT,
    validator.V2_GOVERNANCE_REL,
    validator.V2_PACKAGE_REL,
    validator.V2_STATISTICS_REL,
    validator.V2_FAMILY_REL,
    V3_STATE,
    V3_MANIFEST,
    V3_SCHEMA,
)


def _fixture(tmp_path: Path) -> Path:
    for relative in FIXTURE_FILES:
        source = REPO / relative
        destination = tmp_path / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
    return tmp_path


def _validate(root: Path) -> dict[str, Any]:
    return validator.validate_recovery(root, canonical_root=REPO)


def _rewrite_json(root: Path, relative: Path, mutate: Callable[[dict[str, Any]], None], self_field: str | None = None) -> None:
    path = root / relative
    payload = json.loads(path.read_text(encoding="utf-8"))
    mutate(payload)
    if self_field is not None:
        payload["integrity"][self_field] = validator.canonical_hash(payload, self_field)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")


def _write_scoped_output(root: Path, relative_name: str, *, alternate: bool = False) -> Path:
    parent = (
        root / "data/research_runs/r46_treasury_auction_unwind_tlt_v2_alternate"
        if alternate
        else root / validator.V2_OUTPUT_DIR_REL
    )
    parent.mkdir(parents=True, exist_ok=True)
    path = parent / relative_name
    path.write_text("synthetic sentinel\n", encoding="utf-8")
    return path


def _snapshot(root: Path) -> dict[str, tuple[int, str, int]]:
    result: dict[str, tuple[int, str, int]] = {}
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        relative = path.relative_to(root).as_posix()
        result[relative] = (path.stat().st_size, hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_mtime_ns)
    return result


def test_valid_fixture_reaches_recovery_governance_ready(tmp_path: Path) -> None:
    result = _validate(_fixture(tmp_path))
    assert result["status"] == "RECOVERY_GOVERNANCE_READY"
    assert result["blockers"] == []


def test_missing_v2_reservation_blocks(tmp_path: Path) -> None:
    root = _fixture(tmp_path)
    (root / V2_RESERVED).unlink()
    assert "V2_RESERVED_SIDECAR_MISSING" in _validate(root)["blockers"]


def test_one_byte_tampered_reservation_blocks(tmp_path: Path) -> None:
    root = _fixture(tmp_path)
    path = root / V2_RESERVED
    content = bytearray(path.read_bytes())
    content[0] ^= 1
    path.write_bytes(bytes(content))
    assert "V2_RESERVED_SIDECAR_HASH_MISMATCH" in _validate(root)["blockers"]


def test_wrong_reservation_transition_hash_blocks(tmp_path: Path) -> None:
    root = _fixture(tmp_path)
    _rewrite_json(root, V2_RESERVED, lambda payload: payload["integrity"].__setitem__("transition_sha256", "0" * 64))
    assert "V2_RESERVED_TRANSITION_HASH_INVALID" in _validate(root)["blockers"]


def test_wrong_v2_state_hash_blocks(tmp_path: Path) -> None:
    root = _fixture(tmp_path)
    path = root / V2_STATE
    content = bytearray(path.read_bytes())
    content[-2] ^= 1
    path.write_bytes(bytes(content))
    assert "V2_BASE_STATE_HASH_MISMATCH" in _validate(root)["blockers"]


def test_wrong_gate_hash_blocks(tmp_path: Path) -> None:
    root = _fixture(tmp_path)
    _rewrite_json(root, V2_RESERVED, lambda payload: payload.__setitem__("gate_sha256", "0" * 64), "transition_sha256")
    assert "V2_GATE_HASH_MISMATCH" in _validate(root)["blockers"]


def test_wrong_governance_hash_blocks(tmp_path: Path) -> None:
    root = _fixture(tmp_path)
    _rewrite_json(root, V2_RESERVED, lambda payload: payload.__setitem__("manifest_sha256", "0" * 64), "transition_sha256")
    assert "V2_GOVERNANCE_HASH_MISMATCH" in _validate(root)["blockers"]


def test_wrong_package_hash_blocks(tmp_path: Path) -> None:
    root = _fixture(tmp_path)
    _rewrite_json(root, V2_RESERVED, lambda payload: payload.__setitem__("package_sha256", "0" * 64), "transition_sha256")
    assert "V2_PACKAGE_HASH_MISMATCH" in _validate(root)["blockers"]


def test_consumed_transition_presence_blocks(tmp_path: Path) -> None:
    root = _fixture(tmp_path)
    (root / validator.V2_CONSUMED_REL).write_text("{}\n", encoding="utf-8")
    assert "V2_CONSUMED_TRANSITION_PRESENT" in _validate(root)["blockers"]


def test_aborted_transition_presence_blocks(tmp_path: Path) -> None:
    root = _fixture(tmp_path)
    (root / validator.V2_ABORTED_REL).write_text("{}\n", encoding="utf-8")
    assert "V2_ABORTED_TRANSITION_PRESENT" in _validate(root)["blockers"]


@pytest.mark.parametrize("filename", ("results.json", "ledger.json", "report.json", "run_receipt.json"))
def test_synthetic_canonical_output_file_blocks(tmp_path: Path, filename: str) -> None:
    root = _fixture(tmp_path)
    _write_scoped_output(root, filename)
    blockers = _validate(root)["blockers"]
    assert "V2_PERSISTED_ECONOMIC_OUTPUT_PRESENT" in blockers


def test_alternate_r46_v2_economic_bundle_blocks(tmp_path: Path) -> None:
    root = _fixture(tmp_path)
    _write_scoped_output(root, "results.json", alternate=True)
    assert "V2_OTHER_ECONOMIC_BUNDLE_PRESENT" in _validate(root)["blockers"]


def _assert_v3_used_state_blocks(tmp_path: Path, mutate: Callable[[dict[str, Any]], None]) -> None:
    root = _fixture(tmp_path)
    _rewrite_json(root, V3_STATE, mutate, "artifact_sha256")
    assert "V3_ONE_SHOT_ALREADY_USED" in _validate(root)["blockers"]


def test_used_v3_state_blocks(tmp_path: Path) -> None:
    _assert_v3_used_state_blocks(tmp_path, lambda payload: payload.__setitem__("run_count", 1))


def test_reserved_v3_state_blocks(tmp_path: Path) -> None:
    _assert_v3_used_state_blocks(
        tmp_path,
        lambda payload: (payload.__setitem__("status", "RESERVED"), payload.__setitem__("reservation_state", "RESERVED")),
    )


def test_consumed_v3_state_blocks(tmp_path: Path) -> None:
    _assert_v3_used_state_blocks(
        tmp_path,
        lambda payload: (payload.__setitem__("status", "CONSUMED"), payload.__setitem__("run_count", 1)),
    )


def test_manifest_allowing_promotion_blocks(tmp_path: Path) -> None:
    root = _fixture(tmp_path)
    _rewrite_json(root, V3_MANIFEST, lambda payload: payload.__setitem__("promotion", True), "manifest_sha256")
    assert "V3_MANIFEST_INVALID" in _validate(root)["blockers"]


def test_manifest_allowing_paper_live_blocks(tmp_path: Path) -> None:
    root = _fixture(tmp_path)
    _rewrite_json(root, V3_MANIFEST, lambda payload: payload.__setitem__("paper_or_live_activation", True), "manifest_sha256")
    assert "PRODUCTION_BOUNDARY_VIOLATION" in _validate(root)["blockers"]


def test_manifest_permitting_tuning_blocks(tmp_path: Path) -> None:
    root = _fixture(tmp_path)
    _rewrite_json(root, V3_MANIFEST, lambda payload: payload.__setitem__("parameter_search_allowed", True), "manifest_sha256")
    assert "V3_MANIFEST_INVALID" in _validate(root)["blockers"]


def test_v2_reservation_deletion_or_invalidation_declaration_blocks(tmp_path: Path) -> None:
    root = _fixture(tmp_path)
    _rewrite_json(
        root,
        V3_MANIFEST,
        lambda payload: payload["recovery_justification"].__setitem__("v2_reservation_deleted", True),
        "manifest_sha256",
    )
    assert "V3_MANIFEST_INVALID" in _validate(root)["blockers"]


def test_confirmatory_claim_blocks(tmp_path: Path) -> None:
    root = _fixture(tmp_path)
    _rewrite_json(
        root,
        V3_MANIFEST,
        lambda payload: payload["recovery_justification"].__setitem__("confirmatory_claim_forbidden", False),
        "manifest_sha256",
    )
    assert "V3_MANIFEST_INVALID" in _validate(root)["blockers"]


def test_origin_unproven_disclosure_is_mandatory(tmp_path: Path) -> None:
    root = _fixture(tmp_path)
    _rewrite_json(root, INCIDENT, lambda payload: payload.__setitem__("classification", "VALID_PRODUCTION_RESERVATION"), "incident_sha256")
    assert "INCIDENT_RECEIPT_INVALID" in _validate(root)["blockers"]


def test_validator_does_not_create_files(tmp_path: Path) -> None:
    root = _fixture(tmp_path)
    before = _snapshot(root)
    _validate(root)
    assert _snapshot(root) == before


def test_validator_does_not_modify_parent_reservation_timestamp_or_content(tmp_path: Path) -> None:
    root = _fixture(tmp_path)
    path = root / V2_RESERVED
    before = (path.read_bytes(), path.stat().st_mtime_ns)
    _validate(root)
    assert (path.read_bytes(), path.stat().st_mtime_ns) == before


def test_validator_does_not_import_or_call_economic_execution_functions() -> None:
    source_path = REPO / "scripts/research/r46_v3_recovery_preflight_2026_09_14.py"
    source = source_path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    forbidden_names = {
        "run_package_backed",
        "run_package_backed_test_only",
        "run_native_once_test_only",
        "reserve_one_shot",
        "consume_one_shot",
        "reconcile_one_shot",
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            assert node.func.id not in forbidden_names
        if isinstance(node, ast.ImportFrom):
            assert node.module != "scripts.research.r46_treasury_auction_unwind_tlt_v2_2026_09_10"


def test_validator_cli_emits_blocked_with_zero_exit_for_tampered_fixture(tmp_path: Path) -> None:
    root = _fixture(tmp_path)
    (root / V2_RESERVED).unlink()
    result = subprocess.run(
        [sys.executable, str(REPO / "scripts/research/r46_v3_recovery_preflight_2026_09_14.py"), "--repo-root", str(root), "--json"],
        cwd=REPO,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "BLOCKED"
    assert "V2_RESERVED_SIDECAR_MISSING" in payload["blockers"]
