from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

import scripts.research.r46_v3_execution_preflight_2026_09_14 as preflight

EXPECTED_ENVIRONMENT = {
    "os": "Darwin",
    "architecture": "arm64",
    "python": "3.13.12",
    "timezone": "Europe/Istanbul",
    "locale": "C.UTF-8",
    "dependency_lock": "uv.lock",
}


def _patch_ready_fixture(monkeypatch: pytest.MonkeyPatch) -> None:
    contract = {
        "source_commit": "1" * 40,
        "freeze_commit": "2" * 40,
        "canonical_execution_root": str(preflight.CANONICAL_EXECUTION_ROOT),
        "frozen_output_directory": str(preflight.CANONICAL_EXECUTION_ROOT / preflight.V3_OUTPUT_DIR_REL),
        "authorization": {"schema": preflight.AUTHORIZATION_SCHEMA, "present": False},
    }
    monkeypatch.setattr(preflight, "_validate_contract", lambda *args: (contract, "a" * 64))
    monkeypatch.setattr(preflight, "_validate_lineage", lambda *args: None)
    monkeypatch.setattr(
        preflight,
        "_validate_parent_and_recovery",
        lambda *args: {"status": "RECOVERY_GOVERNANCE_READY", "blockers": []},
    )
    monkeypatch.setattr(
        preflight,
        "_validate_package",
        lambda *args: {
            "manifest_sha256": preflight.V2_PACKAGE_SHA256,
            "artifact_hashes": {name: "1" * 64 for name in ("documents", "bars", "dividends", "eligibility", "calendar")},
            "expected_event_count": 127,
            "event_episodes": 127,
            "holding_intervals": 635,
            "fifth_open_exit": 127,
            "missing_rate": 0.0,
            "event_ids_sha256": preflight.EVENT_IDS_SHA256,
        },
    )
    monkeypatch.setattr(
        preflight,
        "_validate_native_attestation",
        lambda *args: {"status": "VERIFIED_NATIVE_LEDGER", "physical_sha256": "2" * 64},
    )
    monkeypatch.setattr(preflight, "_environment", lambda: dict(EXPECTED_ENVIRONMENT))


def test_ready_preflight_persists_only_a_non_economic_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_ready_fixture(monkeypatch)
    result = preflight.validate_execution_preflight(
        tmp_path, canonical_root=preflight.CANONICAL_EXECUTION_ROOT
    )
    assert result["status"] == "RECOVERY_PRE_RUN_OK"
    assert result["blockers"] == []
    assert result["economic_authorization"] is False
    assert result["runner_authorization_present"] is False
    assert result["economic_execution"] is False
    assert result["economic_run"] is False
    receipt_path = preflight.persist_preflight_receipt(result, root=tmp_path)
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert receipt["status"] == "RECOVERY_PRE_RUN_OK"
    assert receipt["integrity"]["receipt_sha256"] == preflight._canonical_hash(receipt, "receipt_sha256")
    assert not list(tmp_path.rglob("results.json"))
    assert not list(tmp_path.rglob("ledger.json"))
    assert not list(tmp_path.rglob("report.json"))
    assert not list(tmp_path.rglob("run_receipt.json"))
    assert not list(tmp_path.rglob("*.reserved.json"))
    with pytest.raises(FileExistsError):
        preflight.persist_preflight_receipt(result, root=tmp_path)


def test_recovery_governance_blocker_is_not_downgraded(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _patch_ready_fixture(monkeypatch)
    monkeypatch.setattr(
        preflight,
        "_validate_parent_and_recovery",
        lambda *args: {"status": "BLOCKED", "blockers": ["RECOVERY_MUTATED"]},
    )
    result = preflight.validate_execution_preflight(tmp_path, canonical_root=preflight.CANONICAL_EXECUTION_ROOT)
    assert result["status"] == "BLOCKED"
    assert "RECOVERY_GOVERNANCE_BLOCKED" in result["blockers"]
    assert "RECOVERY_MUTATED" in result["blockers"]


@pytest.mark.parametrize("relative", (preflight.V3_OUTPUT_DIR_REL, preflight.V3_AUTHORIZATION_REL))
def test_output_or_real_authorization_presence_blocks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, relative: Path
) -> None:
    _patch_ready_fixture(monkeypatch)
    path = tmp_path / relative
    if relative == preflight.V3_OUTPUT_DIR_REL:
        path.mkdir(parents=True)
    else:
        path.parent.mkdir(parents=True)
        path.write_text("{}\n", encoding="utf-8")
    result = preflight.validate_execution_preflight(tmp_path, canonical_root=preflight.CANONICAL_EXECUTION_ROOT)
    assert result["status"] == "BLOCKED"
    assert (
        "V3_OUTPUT_DIRECTORY_PRESENT" in result["blockers"]
        or "V3_AUTHORIZATION_PRESENT_UNEXPECTED" in result["blockers"]
    )


def test_source_pin_blocker_is_preserved(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _patch_ready_fixture(monkeypatch)

    def invalid_contract(*args: object) -> tuple[dict[str, object], str]:
        args[2].append("SOURCE_FILE_HASH_MISMATCH:runner")  # type: ignore[attr-defined]
        return {"source_commit": "1" * 40, "freeze_commit": "2" * 40}, "a" * 64

    monkeypatch.setattr(preflight, "_validate_contract", invalid_contract)
    result = preflight.validate_execution_preflight(tmp_path, canonical_root=preflight.CANONICAL_EXECUTION_ROOT)
    assert result["status"] == "BLOCKED"
    assert "SOURCE_FILE_HASH_MISMATCH:runner" in result["blockers"]


def test_blocked_result_cannot_create_a_receipt(tmp_path: Path) -> None:
    result = {"status": "BLOCKED", "blockers": ["TEST_BLOCKER"]}
    with pytest.raises(RuntimeError, match="BLOCKED_RECOVERY_PRE_RUN"):
        preflight.persist_preflight_receipt(result, root=tmp_path)
    assert not (tmp_path / preflight.V3_PREFLIGHT_REL).exists()


def test_preflight_has_no_import_edge_to_the_v3_or_v2_economic_runner() -> None:
    path = Path(__file__).resolve().parents[2] / "scripts/research/r46_v3_execution_preflight_2026_09_14.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported_modules = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    }
    assert "scripts.research.r46_treasury_auction_unwind_tlt_v2_2026_09_10" not in imported_modules
    assert "scripts.research.r46_treasury_auction_unwind_tlt_v3_recovery_2026_09_14" not in imported_modules
