"""Fail-closed checks for the isolated official CME X02 retry bundle."""

from __future__ import annotations

import copy
import json
import shutil
from pathlib import Path

import pytest

from scripts.research.crossasset_preflight_2026_09_08 import x02_cme_retry
from scripts.research.crossasset_receipt_verifier_2026_09_08 import (
    canonical_hash,
    sha256,
    verify_source_matrix,
    verify_x02_retry_manifest,
)

RETRY = Path("data/research/external/crossasset_public_2026_09_10/x02_cme_retry/retry_manifest.json")


def test_x02_retry_is_receipt_pinned_and_data_blocked() -> None:
    result = verify_x02_retry_manifest(RETRY)
    assert result["probe_count"] == 24
    assert result["admitted_rows"] == 0
    manifest = json.loads(RETRY.read_text(encoding="utf-8"))
    assert manifest["conclusion"] == "NO_ADMISSIBLE_CME_SETTLEMENT_ROWS"
    assert {row["status"] for row in manifest["records"]} == {"HTTP_ERROR", "REQUEST_ERROR"}
    assert sum(row["status"] == "HTTP_ERROR" for row in manifest["records"]) == 14
    assert sum(row["status"] == "REQUEST_ERROR" for row in manifest["records"]) == 10
    assert x02_cme_retry()["retry_settlement_rows"] == 0
    matrix = json.loads(Path("data/research_manifests/crossasset_x02_x06_source_matrix_2026_09_08.json").read_text(encoding="utf-8"))
    report = json.loads(Path("data/research_runs/crossasset_x02_x06_2026_09_08/gate_report.json").read_text(encoding="utf-8"))
    assert matrix["x02_cme_retry"]["manifest_sha256"] == sha256(RETRY)
    assert report["X02"]["cme_retry"] == matrix["x02_cme_retry"]
    preflight = json.loads(Path("data/research/generated/crossasset_preflight_2026_09_08/results.json").read_text(encoding="utf-8"))
    assert preflight["cards"]["X02"]["retry_manifest_sha256"] == sha256(RETRY)


def test_current_matrix_classifies_cme_routes_as_run_terminal_without_absence_claim() -> None:
    matrix = json.loads(Path("data/research_manifests/crossasset_x02_x06_source_matrix_2026_09_08.json").read_text(encoding="utf-8"))
    current = {row["source_id"]: row for row in matrix["records"] if row.get("source_id") in {"cme_settlements", "cme_treasury_futures_settlements"}}
    assert set(current) == {"cme_settlements", "cme_treasury_futures_settlements"}
    for row in current.values():
        assert row["status"] == "PUBLIC_ROUTE_ATTEMPTS_EXHAUSTED_FOR_THIS_RUN"
        assert row["error_class"] == "READ_TIMEOUT"
        assert row["attempt_count"] == 4
        assert row["source_absence_claim"] is False
        assert row["further_public_retry_required"] is False
        assert row["external_data_blocker"] is True
        assert row["admitted_rows"] == row["economic_rows"] == 0
        assert row["retry_manifest_sha256"] == sha256(RETRY)
    report = json.loads(Path("data/research_runs/crossasset_x02_x06_2026_09_08/gate_report.json").read_text(encoding="utf-8"))
    assert report["X02"]["status"] == "PUBLIC_ROUTE_ATTEMPTS_EXHAUSTED_FOR_THIS_RUN"
    assert report["X02"]["source_absence_claim"] is False


def test_x02_retry_manifest_status_mutation_fails_closed(tmp_path: Path) -> None:
    mutated = copy.deepcopy(json.loads(RETRY.read_text(encoding="utf-8")))
    mutated["records"][0]["status"] = "HTTP_SUCCESS"
    mutated["manifest_sha256"] = canonical_hash(mutated, "manifest_sha256")
    target = tmp_path / "retry_manifest.json"
    target.write_text(json.dumps(mutated), encoding="utf-8")
    with pytest.raises(AssertionError, match="status binding"):
        verify_x02_retry_manifest(target)


def test_x02_retry_body_and_receipt_coherent_mutation_without_record_pin_fails(tmp_path: Path) -> None:
    source_dir = RETRY.parent
    target_dir = tmp_path / "data/research/external/crossasset_public_2026_09_10/x02_cme_retry"
    shutil.copytree(source_dir, target_dir)
    manifest_path = target_dir / "retry_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    record = manifest["records"][0]
    body_path = tmp_path / record["body_path"]
    receipt_path = tmp_path / record["receipt_path"]
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    body = body_path.read_bytes() + b"mutation"
    body_path.write_bytes(body)
    receipt["body_sha256"] = __import__("hashlib").sha256(body).hexdigest()
    receipt["bytes"] = len(body)
    receipt_path.write_text(json.dumps(receipt, sort_keys=True), encoding="utf-8")
    record["receipt_sha256"] = sha256(receipt_path)
    manifest["manifest_sha256"] = canonical_hash(manifest, "manifest_sha256")
    manifest_path.write_text(json.dumps(manifest, sort_keys=True), encoding="utf-8")
    with pytest.raises(AssertionError, match="receipt binding mismatch"):
        verify_x02_retry_manifest(manifest_path, repo_root=tmp_path)


def test_x02_matrix_supplemental_pin_mutation_fails_closed(tmp_path: Path) -> None:
    matrix_path = Path("data/research_manifests/crossasset_x02_x06_source_matrix_2026_09_08.json")
    matrix = copy.deepcopy(json.loads(matrix_path.read_text(encoding="utf-8")))
    matrix["x02_cme_retry"]["manifest_sha256"] = "0" * 64
    matrix["matrix_sha256"] = canonical_hash(matrix, "matrix_sha256")
    target = tmp_path / "matrix.json"
    target.write_text(json.dumps(matrix), encoding="utf-8")
    with pytest.raises(AssertionError, match="X02 retry manifest pin mismatch"):
        verify_source_matrix(target)
