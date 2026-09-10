from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[2] / "scripts/research/verify_all_34_closure_2026_09_09.py"
SPEC = importlib.util.spec_from_file_location("all_34_closure", SCRIPT)
assert SPEC and SPEC.loader
closure = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(closure)


EXPECTED_IDS = {*(f"H{i:02d}" for i in range(1, 17)), *(f"K{i:02d}" for i in range(1, 13)), *(f"X{i:02d}" for i in range(1, 7))}


def _clean_subprocess_env() -> dict[str, str]:
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    return env


def test_closure_inventory_is_exactly_34_and_has_machine_status_fields() -> None:
    record = closure.build_record(Path(__file__).parents[2])
    assert record["candidate_ids"] == sorted(EXPECTED_IDS)
    assert record["candidate_count"] == 34
    assert record["candidate_ids_unique"] is True
    assert record["economic_eligible_count"] == 0
    assert record["local_engineering_gap_count"] == 0
    assert record["research_only"] is True
    assert record["status"] == "ALL_34_RESEARCH_PROGRAM_CLOSED_WITH_DEFINITIONS"
    assert all(
        set(candidate) == closure.CANDIDATE_FIELDS
        and candidate["research_status"]
        and candidate["strategy_name"]
        and candidate["purpose_definition"] is None
        and candidate["purpose_definition_status"] == closure.UNSPECIFIED
        and candidate["mechanism"]
        and candidate["feasibility"]
        and candidate["evidence_summary"]
        and candidate["terminal_decision"]["detail"]
        and candidate["economic_run"] is False
        and candidate["eligible"] is False
        and candidate["admitted"] == 0
        and candidate["promotion"] is False
        for candidate in record["candidates"].values()
    )


def test_marker_scan_flags_current_and_ignores_explicit_archive(tmp_path: Path) -> None:
    current = tmp_path / "current"
    archive = tmp_path / "docs/archive/research_2026_08"
    current.mkdir(parents=True)
    archive.mkdir(parents=True)
    (current / "current.json").write_text('{"status":"PENDING"}\n', encoding="utf-8")
    (archive / "old.json").write_text('{"status":"PENDING"}\n', encoding="utf-8")
    findings = closure.scan_markers((current, archive.parent.parent))
    assert any(item["marker"] == "PENDING" and item["path"].endswith("current.json") for item in findings)
    assert not any(item["path"].endswith("old.json") for item in findings)


def test_current_retry_markers_are_closed_by_terminal_route_classification() -> None:
    issues = closure._current_retry_issues(Path(__file__).parents[2])
    assert issues == []
    matrix = json.loads(
        (
            Path(__file__).parents[2]
            / "data/research_manifests/crossasset_x02_x06_source_matrix_2026_09_08.json"
        ).read_text(encoding="utf-8")
    )
    terminal = {
        row["source_id"]: row
        for row in matrix["records"]
        if row.get("source_id") in {"cme_settlements", "cme_treasury_futures_settlements"}
    }
    assert {row["status"] for row in terminal.values()} == {"PUBLIC_ROUTE_ATTEMPTS_EXHAUSTED_FOR_THIS_RUN"}


def test_normal_cli_writes_only_record_and_summary(tmp_path: Path) -> None:
    before = {path.name for path in tmp_path.iterdir()}
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--output-dir", str(tmp_path)],
        check=True,
        capture_output=True,
        text=True,
        cwd=tmp_path,
        env=_clean_subprocess_env(),
    )
    assert json.loads(result.stdout)["status"] == "ALL_34_RESEARCH_PROGRAM_CLOSED_WITH_DEFINITIONS"
    assert {path.name for path in tmp_path.iterdir()} - before == {"closure_record.json", "closure_summary.md"}


def test_verify_mode_is_read_only_for_existing_closure(tmp_path: Path) -> None:
    repo = Path(__file__).parents[2]
    closure.write_closure(repo, tmp_path)
    before = {
        path.name: (hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_mtime_ns)
        for path in tmp_path.iterdir()
        if path.is_file() and path.name in {"closure_record.json", "closure_summary.md"}
    }
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--verify", "--output-dir", str(tmp_path)],
        check=True,
        capture_output=True,
        text=True,
        cwd=tmp_path,
        env=_clean_subprocess_env(),
    )
    assert json.loads(result.stdout)["read_only"] is True
    after = {
        path.name: (hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_mtime_ns)
        for path in tmp_path.iterdir()
        if path.is_file() and path.name in {"closure_record.json", "closure_summary.md"}
    }
    assert after == before


def test_manifest_pin_mutation_is_rejected(tmp_path: Path) -> None:
    body = tmp_path / "body.txt"
    body.write_text("original\n", encoding="utf-8")
    manifest = {"inputs": {"body.txt": {"sha256": closure.sha256(body)}}}
    body.write_text("mutated\n", encoding="utf-8")
    assert closure.verify_declared_hashes(manifest, tmp_path) == ["body.txt"]


def _valid_candidate(tmp_path: Path) -> dict[str, object]:
    source = tmp_path / "source.json"
    source.write_text('{"cards":{"H01":{"status":"PIT_PENDING"}}}\n', encoding="utf-8")
    digest = closure.sha256(source)
    definition_path = tmp_path / closure.DEFINITION_SOURCE
    definition_path.parent.mkdir(parents=True, exist_ok=True)
    definition_path.write_text("# frozen definition fixture\n", encoding="utf-8")
    definition_digest = closure.sha256(definition_path)
    return {
        "candidate_id": "H01",
        "source_path": "source.json",
        "pointer": ["cards", "H01"],
        "status": "PIT_PENDING",
        "strategy_name": "Value",
        "purpose_definition": None,
        "purpose_definition_status": closure.UNSPECIFIED,
        "mechanism": "Value",
        "feasibility": "PIT value unavailable",
        "unspecified_fields": {"purpose_definition": None, "field_formula": None, "window": None, "cost_stress": None, "controls": None, "data_requirements": None, "scope": closure.UNSPECIFIED},
        "research_status": "PIT_PENDING",
        "evidence_summary": "rows=0",
        "evidence_counts": {"rows": 0},
        "pit_provenance": {"status": "PIT_PENDING", "source_path": "source.json", "source_sha256": digest, "pointer": ["cards", "H01"]},
        "terminal_decision": {"kind": "blocker", "detail": ["PIT gate remains unmet"]},
        "economic_run": False,
        "eligible": False,
        "admitted": 0,
        "promotion": False,
        "economic_violations": [],
        "definition_source": {"path": closure.DEFINITION_SOURCE, "section": "H01 — Value", "sha256": definition_digest},
        "result_source": {"path": "source.json", "sha256": digest, "pointer": ["cards", "H01"]},
        "integration_candidate": False,
    }


def test_candidate_contract_rejects_missing_null_wrong_type_extra_archive_and_string_true(tmp_path: Path) -> None:
    valid = _valid_candidate(tmp_path)
    assert closure.validate_candidate_contract("H01", valid, tmp_path) == []
    missing = dict(valid)
    missing.pop("strategy_name")
    assert closure.validate_candidate_contract("H01", missing, tmp_path)
    null_value = dict(valid)
    null_value["mechanism"] = None
    assert closure.validate_candidate_contract("H01", null_value, tmp_path)
    wrong_type = dict(valid)
    wrong_type["evidence_counts"] = []
    assert closure.validate_candidate_contract("H01", wrong_type, tmp_path)
    extra = dict(valid)
    extra["unexpected"] = False
    assert closure.validate_candidate_contract("H01", extra, tmp_path)
    archived = dict(valid)
    archived["source_path"] = "docs/archival/source.json"
    archived["pit_provenance"] = dict(valid["pit_provenance"], source_path="docs/archival/source.json")
    assert closure.validate_candidate_contract("H01", archived, tmp_path)
    string_true = dict(valid)
    string_true["economic_run"] = "true"
    assert closure.validate_candidate_contract("H01", string_true, tmp_path)


def test_candidate_inventory_rejects_missing_or_extra_ids() -> None:
    assert closure.validate_candidate_inventory({candidate: {} for candidate in closure.EXPECTED_IDS}) == []
    missing = {candidate: {} for candidate in closure.EXPECTED_IDS[:-1]}
    assert closure.validate_candidate_inventory(missing)
    extra = {candidate: {} for candidate in closure.EXPECTED_IDS} | {"H99": {}}
    assert closure.validate_candidate_inventory(extra)


def test_verify_rejects_coherently_rehashed_candidate_schema_mutation(tmp_path: Path) -> None:
    repo = Path(__file__).parents[2]
    source_record = repo / "data/research_runs/all_34_research_closure_2026_09_09/closure_record.json"
    record = json.loads(source_record.read_text(encoding="utf-8"))
    record["candidates"]["H01"]["strategy_name"] = None
    record["record_sha256"] = closure.canonical_hash({**record, "record_sha256": closure.PENDING})
    tmp_path.joinpath("closure_record.json").write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp_path.joinpath("closure_summary.md").write_text(f"Record SHA-256: `{record['record_sha256']}`\n", encoding="utf-8")
    with pytest.raises(ValueError, match="closure_candidate_schema_invalid"):
        closure.verify_closure(repo, tmp_path)


def test_h06_document_and_row_amendment_accounting_is_current() -> None:
    report = json.loads(
        (
            Path(__file__).parents[2]
            / "data/research/external/equity_h06_local_normalization_2026_09_09/report.json"
        ).read_text(encoding="utf-8")
    )
    assert closure.validate_h06_lineage(report) == []
    mutated = json.loads(json.dumps(report))
    mutated["amendment_lineage"]["document_level"]["entirely_unmatched_documents"] = 35
    assert closure.validate_h06_lineage(mutated)
