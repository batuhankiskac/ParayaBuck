"""Fail-closed contract checks for the R46 pre-outcome governance bundle."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

from scripts.research import validate_r46_holdout_custody_2026_09_10 as custody_validator

REPO = Path(__file__).resolve().parents[2]
MANIFEST = REPO / "data/research_manifests/r46_treasury_auction_unwind_tlt_v1_2026_09_10.json"
SOURCE_MATRIX = REPO / "data/research_manifests/r46_treasury_auction_unwind_tlt_v1_2026_09_10_source_matrix.json"
HOLDOUT_CONTRACT = REPO / "data/research_manifests/r46_treasury_auction_unwind_tlt_v1_2026_09_10_holdout_custody_contract.json"
CAPABILITY_RECEIPT = REPO / "data/research_receipts/r46_treasury_source_capability_2026_09_10.json"
VALIDATOR = REPO / "scripts/research/validate_r46_holdout_custody_2026_09_10.py"


def _run_validator(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(VALIDATOR), *args],
        cwd=REPO,
        text=True,
        capture_output=True,
        check=False,
    )


def test_r46_governance_artifacts_exist() -> None:
    assert MANIFEST.is_file()
    assert SOURCE_MATRIX.is_file()
    assert HOLDOUT_CONTRACT.is_file()
    assert VALIDATOR.is_file()


def test_r46_contract_is_explicitly_non_economic_and_statistically_blocked() -> None:
    payload = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert payload["status"] == "FROZEN_PRE_OUTCOME"
    assert payload["economic_execution"] is False
    assert payload["economic_run"] is False
    assert payload["promotion"] is False
    assert payload["integration_candidate"] is False
    assert payload["paper_live_untouched"] is True
    assert payload["paper_or_live_activation"] is False
    assert payload["statistics"]["status"] == "BLOCKED_STATISTICS"
    assert payload["statistics"]["power_justified"] is False
    assert payload["statistics"]["minimums"] is None
    assert payload["multiplicity"]["status"] == "BLOCKED_STATISTICS"
    assert payload["multiplicity"]["family_size"] is None
    assert payload["multiplicity"]["alpha"] is None


def test_r46_holdout_is_prospective_single_use_and_not_historical() -> None:
    payload = json.loads(HOLDOUT_CONTRACT.read_text(encoding="utf-8"))
    assert payload["mode"] == "prospective_single_use"
    assert payload["historical_holdout_accepted"] is False
    assert payload["status"] == "BLOCKED_NO_HOLDOUT"
    assert payload["requirements"]["independent_custodian"] is True
    assert payload["requirements"]["operator_attestation"] is True
    assert payload["requirements"]["one_release_only"] is True


def test_r46_holdout_requires_append_only_access_log_and_external_signature_verifier() -> None:
    payload = json.loads(HOLDOUT_CONTRACT.read_text(encoding="utf-8"))

    assert payload["requirements"]["access_log_append_only"] is True
    verification = payload["signature_verification"]
    assert verification["required"] is True
    assert verification["mode"] == "external_cryptographic_verifier"
    assert verification["status"] == "BLOCKED_SIGNATURE_VERIFICATION"
    assert verification["embedded_signature_accepted"] is False


def test_r46_source_matrix_freezes_canonical_pit_fields_without_outcomes() -> None:
    payload = json.loads(SOURCE_MATRIX.read_text(encoding="utf-8"))
    assert payload["economic_execution"] is False
    assert payload["economic_run"] is False
    assert set(payload["source_routes"]["treasury_documents"]["required_fields"]) == {
        "canonical_event_id",
        "security_identifier",
        "original_tenor",
        "issue_or_reopening",
        "announcement_document_hash",
        "result_document_hash",
        "published_at",
        "available_at",
        "correction_or_amendment_chain",
    }
    assert {"price_return", "NAV", "P&L", "CVaR", "Sharpe"}.issubset(
        payload["record_schema"]["outcome_fields_not_exposed_to_preflight"]
    )
    assert payload["coverage"]["event_count"] is None
    assert payload["coverage"]["capacity_intersection_count"] is None


def test_r46_manifest_binds_self_hashed_capability_receipt_without_pit_readiness() -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    binding = manifest["source_provenance"]["source_capability_receipt"]
    receipt_path = REPO / binding["path"]
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))

    assert binding["sha256"] == hashlib.sha256(receipt_path.read_bytes()).hexdigest()
    assert binding["artifact_sha256"] == receipt["artifact_sha256"]
    assert binding["status"] == receipt["status"] == "CAPABILITY_OR_PAID_BLOCKED"
    assert binding["route_capability_status"] == receipt["route_capability_status"] == "PUBLIC_ROUTES_IDENTIFIED"
    assert binding["r46_admission_status"] == receipt["r46_admission_status"] == "DATA_ACQUISITION_REQUIRED"


def test_r46_validator_rejects_mutated_capability_receipt_raw_bytes(tmp_path: Path) -> None:
    for relative in (MANIFEST, SOURCE_MATRIX, HOLDOUT_CONTRACT, CAPABILITY_RECEIPT):
        destination = tmp_path / relative.relative_to(REPO)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(relative, destination)

    receipt_path = tmp_path / CAPABILITY_RECEIPT.relative_to(REPO)
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["status"] = "PUBLIC_ROUTE_CAPABLE"
    receipt_path.write_text(json.dumps(receipt, sort_keys=True), encoding="utf-8")

    reasons = custody_validator.validate_manifest(tmp_path)

    assert "BLOCKED_SOURCE_CAPABILITY_RECEIPT" in reasons


def test_r46_validator_rejects_capability_receipt_path_substitution(tmp_path: Path) -> None:
    for relative in (MANIFEST, SOURCE_MATRIX, HOLDOUT_CONTRACT, CAPABILITY_RECEIPT):
        destination = tmp_path / relative.relative_to(REPO)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(relative, destination)

    manifest_path = tmp_path / MANIFEST.relative_to(REPO)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    binding = manifest["source_provenance"]["source_capability_receipt"]
    binding["path"] = "../outside-capability.json"
    manifest["integrity"]["manifest_sha256"] = custody_validator.canonical_hash(
        manifest, "manifest_sha256", nested=True
    )
    manifest_path.write_text(json.dumps(manifest, sort_keys=True), encoding="utf-8")

    reasons = custody_validator.validate_manifest(tmp_path)

    assert "BLOCKED_SOURCE_CAPABILITY_RECEIPT" in reasons


def test_r46_governance_schema_requires_capability_receipt_binding() -> None:
    schema_path = REPO / "data/research_manifests/r46_treasury_auction_unwind_tlt_v1_2026_09_10.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    provenance = schema["properties"]["source_provenance"]
    assert "source_capability_receipt" in provenance["required"]
    capability = provenance["properties"]["source_capability_receipt"]
    assert set(capability["required"]) == {
        "path",
        "sha256",
        "artifact_sha256",
        "status",
        "route_capability_status",
        "r46_admission_status",
    }


def test_r46_manifest_and_contract_hash_pins_are_current() -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert manifest["integrity"]["manifest_sha256"] == custody_validator.canonical_hash(
        manifest, "manifest_sha256", nested=True
    )
    schema_path = REPO / manifest["integrity"]["schema_path"]
    assert manifest["integrity"]["schema_sha256"] == custody_validator.sha256(schema_path)
    source_path = REPO / manifest["data_contract"]["source_matrix_path"]
    source = json.loads(source_path.read_text(encoding="utf-8"))
    assert source["integrity"]["artifact_sha256"] == custody_validator.canonical_hash(
        source, "artifact_sha256", nested=True
    )
    assert manifest["data_contract"]["source_matrix_sha256"] == custody_validator.sha256(source_path)
    holdout_path = REPO / manifest["holdout"]["contract_path"]
    holdout = json.loads(holdout_path.read_text(encoding="utf-8"))
    assert holdout["integrity"]["artifact_sha256"] == custody_validator.canonical_hash(
        holdout, "artifact_sha256", nested=True
    )
    assert manifest["holdout"]["contract_sha256"] == custody_validator.sha256(holdout_path)


def test_r46_validator_fails_closed_before_runner_and_custody_are_ready() -> None:
    result = _run_validator("--json")
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["status"] == "BLOCKED"
    assert "BLOCKED_STATISTICS" in payload["reasons"]
    assert "BLOCKED_NO_HOLDOUT" in payload["reasons"]
    assert "BLOCKED_RUNNER_NOT_IN_BASE" in payload["reasons"]
    assert "BLOCKED_ENVIRONMENT_NOT_PINNED" in payload["reasons"]
    assert "BLOCKED_PIT_SOURCE" in payload["reasons"]
    assert "BLOCKED_NATIVE_LEDGER" in payload["reasons"]
    assert "BLOCKED_SIGNATURE_VERIFICATION" in payload["reasons"]
    assert "INVALID_HOLDOUT_CUSTODY_CONTRACT" not in payload["reasons"]


def test_r46_validator_rejects_unsigned_or_reused_custody_receipt(tmp_path: Path) -> None:
    receipt = {
        "schema_version": "r46-holdout-custody-receipt-v1",
        "research_id": "r46_treasury_auction_unwind_tlt_v1_2026_09_10",
        "holdout_id": "r46_treasury_auction_unwind_tlt_v1_2026_09_10:prospective_holdout",
        "status": "SEALED_PROSPECTIVE_SINGLE_USE",
        "manifest_sha256": "0" * 64,
        "data_root_sha256": "1" * 64,
        "access_log_sha256": "2" * 64,
        "sealed_at": "2026-09-10T00:00:00Z",
        "release_count": 2,
        "one_release_only": True,
        "outcomes_accessed_before_release": False,
        "storage_class": "WORM",
        "operator": {"id": "operator-a", "signature": ""},
        "custodian": {"id": "operator-a", "signature": ""},
    }
    path = tmp_path / "receipt.json"
    path.write_text(json.dumps(receipt), encoding="utf-8")
    result = _run_validator("--receipt", str(path), "--json")
    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["status"] == "INVALID"
    assert "CUSTODY_SIGNATURE_MISSING" in payload["reasons"]
    assert "CUSTODIAN_MUST_DIFFER_FROM_OPERATOR" in payload["reasons"]
    assert "CUSTODY_RELEASE_COUNT_INVALID" in payload["reasons"]
    assert "CUSTODY_FIELD_MISSING:holdout_start" in payload["reasons"]


def test_r46_validator_rejects_tampered_canonical_source_matrix_self_hash(tmp_path: Path) -> None:
    for relative in (MANIFEST, SOURCE_MATRIX, HOLDOUT_CONTRACT):
        destination = tmp_path / relative.relative_to(REPO)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(relative, destination)

    tampered = json.loads((tmp_path / SOURCE_MATRIX.relative_to(REPO)).read_text(encoding="utf-8"))
    tampered["integrity"]["artifact_sha256"] = "0" * 64
    (tmp_path / SOURCE_MATRIX.relative_to(REPO)).write_text(json.dumps(tampered), encoding="utf-8")

    reasons = custody_validator.validate_manifest(tmp_path)
    assert "INVALID_SOURCE_MATRIX_SELF_HASH" in reasons


def test_r46_validator_rejects_weak_holdout_custody_requirements(tmp_path: Path) -> None:
    for relative in (MANIFEST, SOURCE_MATRIX, HOLDOUT_CONTRACT):
        destination = tmp_path / relative.relative_to(REPO)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(relative, destination)

    tampered = json.loads((tmp_path / HOLDOUT_CONTRACT.relative_to(REPO)).read_text(encoding="utf-8"))
    tampered["requirements"]["independent_custodian"] = False
    (tmp_path / HOLDOUT_CONTRACT.relative_to(REPO)).write_text(json.dumps(tampered), encoding="utf-8")

    reasons = custody_validator.validate_manifest(tmp_path)
    assert "INVALID_HOLDOUT_CUSTODY_CONTRACT" in reasons


def test_r46_validator_rejects_hash_that_only_matches_worktree_not_pinned_base(tmp_path: Path) -> None:
    for relative in (MANIFEST, SOURCE_MATRIX, HOLDOUT_CONTRACT):
        destination = tmp_path / relative.relative_to(REPO)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(relative, destination)

    pinned = tmp_path / "pinned/source.py"
    pinned.parent.mkdir(parents=True, exist_ok=True)
    pinned.write_bytes(b"base blob")
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "pinned/source.py"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "-c", "user.name=R46 Test", "-c", "user.email=r46@example.invalid", "commit", "-qm", "base"],
        cwd=tmp_path,
        check=True,
    )
    pinned.write_bytes(b"worktree only")

    manifest_path = tmp_path / MANIFEST.relative_to(REPO)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    base_commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=tmp_path, text=True).strip()
    manifest["source_provenance"]["base_commit"] = base_commit
    manifest["source_provenance"]["source_commit"] = base_commit
    manifest["source_provenance"]["clean_tree_observed"] = True
    manifest["source_provenance"]["source_files"]["pinned/source.py"] = {
        "sha256": hashlib.sha256(b"worktree only").hexdigest(),
        "role": "test pin",
    }
    manifest["integrity"]["manifest_sha256"] = custody_validator.canonical_hash(
        manifest, "manifest_sha256", nested=True
    )
    manifest_path.write_text(json.dumps(manifest, sort_keys=True), encoding="utf-8")

    reasons = custody_validator.validate_manifest(tmp_path)

    assert "BLOCKED_SOURCE_BASE_HASH" in reasons


def test_r46_receipt_validation_runs_full_manifest_gate(tmp_path: Path) -> None:
    for relative in (MANIFEST, SOURCE_MATRIX, HOLDOUT_CONTRACT):
        destination = tmp_path / relative.relative_to(REPO)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(relative, destination)

    manifest_path = tmp_path / MANIFEST.relative_to(REPO)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["status"] = "INVALIDATED"
    manifest_path.write_text(json.dumps(manifest, sort_keys=True), encoding="utf-8")
    contract_path = tmp_path / manifest["holdout"]["contract_path"]
    receipt = {
        "schema_version": "r46-holdout-custody-receipt-v1",
        "research_id": manifest["research_id"],
        "holdout_id": f"{manifest['research_id']}:prospective_holdout",
        "status": "SEALED_PROSPECTIVE_SINGLE_USE",
        "manifest_sha256": custody_validator.sha256(manifest_path),
        "holdout_contract_sha256": custody_validator.sha256(contract_path),
        "data_root_sha256": "1" * 64,
        "access_log_sha256": "2" * 64,
        "sealed_at": "2026-09-10T00:00:00Z",
        "holdout_start": "2026-09-11T00:00:00Z",
        "holdout_end_exclusive": "2026-10-01T00:00:00Z",
        "storage_class": "WORM",
        "encrypted_at_rest": True,
        "access_log_append_only": True,
        "raw_holdout_hidden_until_release": True,
        "operator": {"id": "operator-a", "signature": "signature-a"},
        "custodian": {"id": "custodian-b", "signature": "signature-b"},
        "independent_reviewer": {"id": "reviewer-c", "signature": "signature-c"},
        "outcomes_accessed_before_release": False,
        "one_release_only": True,
        "release_count": 0,
    }
    receipt_path = tmp_path / "receipt.json"
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")

    result = custody_validator.validate_receipt(receipt_path, repo=tmp_path)

    assert result["status"] == "INVALID"
    assert "INVALID_MANIFEST_SCHEMA" in result["reasons"]
