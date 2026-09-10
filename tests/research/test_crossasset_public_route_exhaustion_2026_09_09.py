"""Offline integrity and admission checks for the frozen X01/X03 route probes."""

from __future__ import annotations

import copy
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.research.crossasset_bind_x03_public_samples_2026_09_09 import verify_gate_report_dependencies
from scripts.research.crossasset_public_route_exhaustion_2026_09_09 import verify_report
from scripts.research.crossasset_receipt_verifier_2026_09_08 import canonical_hash, sha256, verify_source_matrix

ROOT = Path("data/research/external/crossasset_public_2026_09_09/public_route_exhaustion")
REPORT = ROOT / "route_exhaustion_report.json"


def test_frozen_routes_have_body_receipts_and_terminal_statuses() -> None:
    report = json.loads(REPORT.read_text(encoding="utf-8"))
    assert report["report_sha256"] == hashlib.sha256(json.dumps({k: v for k, v in report.items() if k != "report_sha256"}, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()
    routes = report["routes"]
    assert len(routes) == 18
    assert sum(route["card"] == "X01" for route in routes) == 9
    assert sum(route["card"] == "X03" for route in routes) == 9
    assert sum(route["status"] == "HTTP_SUCCESS" for route in routes) == 17
    assert sum(route["status"] == "HTTP_ERROR" for route in routes) == 1
    assert sum(route["status"] == "REQUEST_ERROR" for route in routes) == 0
    assert verify_report(REPORT)["routes"] == 18
    for route in routes:
        assert route["endpoint"].startswith("https://")
        receipt_path = Path(route["receipt_path"])
        assert receipt_path.is_file()
        assert sha256(receipt_path) == route["receipt_sha256"]
        if route.get("body_path"):
            body = Path(route["body_path"])
            assert body.is_file()
            assert body.stat().st_size == route["bytes"]
            assert sha256(body) == route["body_sha256"]
        else:
            assert route["bytes"] == 0
            assert route["body_sha256"] is None
        assert route["credential_fields_persisted"] is False
        assert route["admissible"] is False


def test_public_sec_samples_bind_exact_identity_and_keep_rows_unadmitted() -> None:
    matrix = json.loads(Path("data/research_manifests/crossasset_x02_x06_source_matrix_2026_09_08.json").read_text(encoding="utf-8"))
    samples = {row["ticker"]: row for row in matrix["records"] if row.get("ticker") in {"EWJ", "EWZ"} and row.get("binding_status") == "IDENTITY_BOUND_SAMPLE_METADATA_ONLY"}
    assert set(samples) == {"EWJ", "EWZ"}
    assert samples["EWJ"]["cik"] == samples["EWZ"]["cik"] == "0000930667"
    assert samples["EWJ"]["series_ids"] == ["S000004249"]
    assert samples["EWJ"]["class_ids"] == ["C000011953"]
    assert samples["EWZ"]["series_ids"] == ["S000004264"]
    assert samples["EWZ"]["class_ids"] == ["C000011968"]
    assert samples["EWJ"]["parsed_holding_rows"] == 185
    assert samples["EWZ"]["parsed_holding_rows"] == 54
    assert all(row["holding_count"] == row["admitted_holding_rows"] == 0 and row["holding_rows"] == [] for row in samples.values())
    assert verify_source_matrix(Path("data/research_manifests/crossasset_x02_x06_source_matrix_2026_09_08.json"))["records"] == 29


def test_public_route_manifest_mutation_fails_closed(tmp_path: Path) -> None:
    report = json.loads(REPORT.read_text(encoding="utf-8"))
    mutated = copy.deepcopy(report)
    mutated["routes"][0]["body_sha256"] = "0" * 64
    body = Path(mutated["routes"][0]["body_path"])
    assert sha256(body) != mutated["routes"][0]["body_sha256"]
    mutated.pop("report_sha256", None)
    mutated["report_sha256"] = canonical_hash(mutated, "report_sha256")
    target = tmp_path / "route_exhaustion_report.json"
    target.write_text(json.dumps(mutated), encoding="utf-8")
    with pytest.raises(AssertionError, match="route body hash mismatch"):
        verify_report(target)


def test_verify_cli_is_read_only() -> None:
    """The CLI verify branch must not refresh receipts, bodies, or the report."""
    report = json.loads(REPORT.read_text(encoding="utf-8"))
    declared = [REPORT]
    declared.extend(Path(route["receipt_path"]) for route in report["routes"])
    declared.extend(Path(route["body_path"]) for route in report["routes"] if route.get("body_path"))
    before = {path: (path.stat().st_mtime_ns, sha256(path)) for path in declared}
    result = subprocess.run(
        [sys.executable, "scripts/research/crossasset_public_route_exhaustion_2026_09_09.py", "--verify"],
        cwd=Path.cwd(),
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"routes": 18, "report_sha256": report["report_sha256"]}
    after = {path: (path.stat().st_mtime_ns, sha256(path)) for path in declared}
    assert after == before


def test_gate_report_sec_summary_matches_canonical_x03_binding() -> None:
    matrix_path = Path("data/research_manifests/crossasset_x02_x06_source_matrix_2026_09_08.json")
    matrix = json.loads(matrix_path.read_text(encoding="utf-8"))
    matrix_result = verify_source_matrix(matrix_path)
    report_path = Path("data/research_runs/crossasset_x02_x06_2026_09_08/gate_report.json")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    sec = report["sec"]
    x03 = report["X03"]
    assert sec["binding_status"] == x03["binding_status"] == "MIXED_IDENTITY_BOUND_AND_UNBOUND_METADATA_ONLY"
    assert sec["resolved_tickers"] == x03["funds"] == {"MCHI": "0001100663", "EWJ": "0000930667", "EWZ": "0000930667"}
    assert sec["unresolved_tickers"] == []
    assert x03["unresolved_funds"] == []
    assert sec["structured_evidence_rows"] == x03["structured_evidence_rows"] == 804
    assert sec["identity_bound_structured_evidence_rows"] == x03["identity_bound_structured_evidence_rows"] == 239
    assert sec["unbound_structured_evidence_rows"] == x03["unbound_structured_evidence_rows"] == 565
    assert sec["admitted_holding_rows"] == x03["admitted_holding_rows"] == 0
    assert x03["status"] == "DATA_BLOCKED_PIT_VALUATION_FIELDS_MISSING"
    assert report["matrix_sha256"] == report["matrix_canonical_sha256"] == matrix_result["matrix_sha256"] == matrix["matrix_sha256"]
    assert report["public_route_exhaustion_report_sha256"] == sha256(Path(report["public_route_exhaustion_report"]))


def test_x03_aggregate_cannot_claim_all_rows_identity_bound() -> None:
    matrix = json.loads(Path("data/research_manifests/crossasset_x02_x06_source_matrix_2026_09_08.json").read_text(encoding="utf-8"))
    records = [row for row in matrix["records"] if row.get("family") == "X03" and row.get("parsed_holding_rows")]
    unbound_rows = sum(row["parsed_holding_rows"] for row in records if row.get("binding_status") == "METADATA_ONLY_UNBOUND_LOCAL_XML")
    bound_rows = sum(row["parsed_holding_rows"] for row in records if row.get("binding_status") == "IDENTITY_BOUND_SAMPLE_METADATA_ONLY")
    report = json.loads(Path("data/research_runs/crossasset_x02_x06_2026_09_08/gate_report.json").read_text(encoding="utf-8"))
    assert unbound_rows == 565 and bound_rows == 239
    assert report["X03"]["binding_status"] != "IDENTITY_BOUND_SAMPLES_METADATA_ONLY"
    assert report["X03"]["identity_bound_structured_evidence_rows"] == bound_rows
    assert report["X03"]["unbound_structured_evidence_rows"] == unbound_rows


def test_gate_report_preflight_dependencies_are_transitively_hash_pinned(tmp_path: Path) -> None:
    report_path = Path("data/research_runs/crossasset_x02_x06_2026_09_08/gate_report.json")
    expected = verify_gate_report_dependencies(report_path)
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["preflight_dependencies"] == expected
    mutated = copy.deepcopy(report)
    mutated["preflight_results_sha256"] = "0" * 64
    target = tmp_path / "gate_report.json"
    target.write_text(json.dumps(mutated), encoding="utf-8")
    with pytest.raises(AssertionError, match="gate report preflight results pin mismatch"):
        verify_gate_report_dependencies(target)
