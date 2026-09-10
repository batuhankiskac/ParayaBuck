from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.research import build_strategy_queue_completion_report_2026_09_09 as builder

REPO = Path(__file__).parents[2]
LIVE_RECORD = REPO / "data/research_runs/all_34_research_closure_2026_09_09/closure_record.json"


def _write_record(path: Path, record: dict) -> None:
    record["record_sha256"] = builder.canonical_hash(record)
    path.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _fixture(tmp_path: Path) -> tuple[Path, Path, Path, dict]:
    record = json.loads(LIVE_RECORD.read_text(encoding="utf-8"))
    record_path = tmp_path / "data/research_runs/all_34_research_closure_2026_09_09/closure_record.json"
    record_path.parent.mkdir(parents=True)
    record_path.write_text(LIVE_RECORD.read_text(encoding="utf-8"), encoding="utf-8")
    for relative, pin in record["source_pins"].items():
        destination = tmp_path / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPO / relative, destination)
        os.utime(destination, ns=(pin["mtime_ns"], pin["mtime_ns"]))
    report_path = tmp_path / "docs/research/strategy_queue_completion_report_2026_09_09.md"
    output_dir = tmp_path / "data/research_runs/strategy_queue_completion_2026_09_09"
    return record_path, report_path, output_dir, record


def _rewrite_record(record_path: Path, mutate) -> dict:
    record = json.loads(record_path.read_text(encoding="utf-8"))
    mutate(record)
    _write_record(record_path, record)
    return record


def _repin_source(record_path: Path, repo: Path, relative: str) -> None:
    record = json.loads(record_path.read_text(encoding="utf-8"))
    path = repo / relative
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    record["source_pins"][relative] = {"sha256": digest, "bytes": path.stat().st_size, "mtime_ns": path.stat().st_mtime_ns}
    for candidate in record["candidates"].values():
        if candidate.get("source_path") == relative:
            candidate["result_source"]["sha256"] = digest
            candidate["pit_provenance"]["source_sha256"] = digest
    _write_record(record_path, record)


def test_not_closed_fails_fast_without_outputs(tmp_path: Path) -> None:
    record_path, report_path, output_dir, _ = _fixture(tmp_path)
    _rewrite_record(record_path, lambda record: record.update(status="NOT_CLOSED", issues=["closed_status_drift"]))
    result = subprocess.run(
        [sys.executable, str(REPO / "scripts/research/build_strategy_queue_completion_report_2026_09_09.py"), "--record", str(record_path), "--report", str(report_path), "--output-dir", str(output_dir)],
        cwd=REPO,
        check=False,
        capture_output=True,
        text=True,
    )
    payload = json.loads(result.stdout)
    assert result.returncode == 1
    assert payload["error"] == "closure_not_closed"
    assert payload["outputs_written"] is False
    assert not report_path.exists() and not output_dir.exists()


def test_definition_bound_fixture_writes_exact_34_rows_and_h14_semantics(tmp_path: Path) -> None:
    record_path, report_path, output_dir, _ = _fixture(tmp_path)
    result = builder.write_completion(tmp_path, record_path, report_path, output_dir)
    ledger = json.loads((output_dir / "closure_ledger.json").read_text(encoding="utf-8"))
    report = report_path.read_text(encoding="utf-8")
    assert result["candidate_count"] == 34
    assert ledger["candidate_ids"] == builder.EXPECTED_IDS
    assert len(ledger["rows"]) == 34
    assert ledger["integration_candidate_count"] == 0
    h14 = next(row for row in ledger["rows"] if row["candidate_id"] == "H14")
    assert h14["current_evidence"]["v3_daily_nav_rows"] == 2658
    assert h14["current_evidence"]["h14_cells"] == 18
    assert h14["current_evidence"]["fixed_weighting"] == "95% V3 + 5% H14"
    assert "Mekanizma" in report and "Dondurulmuş veri uygulanabilirliği" in report
    assert "integration candidate `0`" in report


def test_live_closure_copy_builds_deterministically(tmp_path: Path) -> None:
    record_path, report_path, output_dir, _ = _fixture(tmp_path)
    first = builder.write_completion(tmp_path, record_path, report_path, output_dir)
    first_bytes = {path.name: path.read_bytes() for path in (report_path, *sorted(output_dir.iterdir()))}
    second = builder.write_completion(tmp_path, record_path, report_path, output_dir)
    second_bytes = {path.name: path.read_bytes() for path in (report_path, *sorted(output_dir.iterdir()))}
    assert first["candidate_count"] == second["candidate_count"] == 34
    assert first_bytes == second_bytes


def test_definition_binding_drift_fails_closed_without_outputs(tmp_path: Path) -> None:
    record_path, report_path, output_dir, _ = _fixture(tmp_path)
    _rewrite_record(record_path, lambda record: record["candidates"]["H01"].update(mechanism="mutated mechanism", strategy_name="mutated mechanism"))
    with pytest.raises(ValueError, match="candidate_definition_contract_mismatch:H01|candidate_record_schema_invalid:H01"):
        builder.write_completion(tmp_path, record_path, report_path, output_dir)
    assert not report_path.exists() and not output_dir.exists()


def test_feasibility_is_bound_to_definition_contract(tmp_path: Path) -> None:
    record_path, report_path, output_dir, _ = _fixture(tmp_path)
    _rewrite_record(record_path, lambda record: record["candidates"]["K01"].update(feasibility="invented feasibility"))
    with pytest.raises(ValueError, match="candidate_definition_contract_mismatch:K01"):
        builder.write_completion(tmp_path, record_path, report_path, output_dir)


def test_source_target_status_mismatch_fails_closed(tmp_path: Path) -> None:
    record_path, report_path, output_dir, record = _fixture(tmp_path)
    relative = record["candidates"]["H01"]["source_path"]
    source = json.loads((tmp_path / relative).read_text(encoding="utf-8"))
    source["frozen_contracts"]["H01"]["status"] = "MUTATED_STATUS"
    (tmp_path / relative).write_text(json.dumps(source, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    _repin_source(record_path, tmp_path, relative)
    with pytest.raises(ValueError, match="candidate_status_binding_mismatch:H01"):
        builder.write_completion(tmp_path, record_path, report_path, output_dir)


def test_source_mutation_fails_without_partial_outputs(tmp_path: Path) -> None:
    record_path, report_path, output_dir, record = _fixture(tmp_path)
    relative = record["candidates"]["H01"]["source_path"]
    path = tmp_path / relative
    path.write_text(path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="closure_source_hash_mismatch|closure_source_pin_mismatch"):
        builder.write_completion(tmp_path, record_path, report_path, output_dir)
    assert not report_path.exists() and not output_dir.exists()


def test_output_receipt_binds_report_ledger_and_definition_contract(tmp_path: Path) -> None:
    record_path, report_path, output_dir, _ = _fixture(tmp_path)
    builder.write_completion(tmp_path, record_path, report_path, output_dir)
    receipt = json.loads((output_dir / "hash_receipt.json").read_text(encoding="utf-8"))
    assert receipt["report"]["sha256"] == hashlib.sha256(report_path.read_bytes()).hexdigest()
    assert receipt["ledger"]["sha256"] == hashlib.sha256((output_dir / "closure_ledger.json").read_bytes()).hexdigest()
    assert receipt["definition_contract"]["path"].endswith("candidate_contracts.json")
    assert receipt["economic_run"] is False and receipt["no_auto_promotion"] is True


def test_private_contact_in_result_source_is_rejected(tmp_path: Path) -> None:
    record_path, report_path, output_dir, record = _fixture(tmp_path)
    relative = record["candidates"]["H01"]["source_path"]
    path = tmp_path / relative
    path.write_text(path.read_text(encoding="utf-8").replace("BLOCKED_PRICE_SHARE_BASIS_UNPROVEN", "contact@example.com"), encoding="utf-8")
    _repin_source(record_path, tmp_path, relative)
    with pytest.raises(ValueError, match="private_contact_in_completion_input"):
        builder.write_completion(tmp_path, record_path, report_path, output_dir)


def test_required_definition_fields_fail_closed_without_fallbacks(tmp_path: Path) -> None:
    record_path, report_path, output_dir, _ = _fixture(tmp_path)
    _rewrite_record(record_path, lambda record: record["candidates"]["K01"].update(mechanism=""))
    with pytest.raises(ValueError, match="required_nonempty_text:mechanism:K01|candidate_record_schema_invalid:K01"):
        builder.write_completion(tmp_path, record_path, report_path, output_dir)


@pytest.mark.parametrize(
    ("field", "value", "pattern"),
    [
        ("economic_run", "false", "strict_boolean_violation:H01:economic_run|candidate_record_schema_invalid:H01"),
        ("eligible", 0, "strict_boolean_violation:H01:eligible|candidate_record_schema_invalid:H01"),
        ("promotion", 0, "strict_boolean_violation:H01:promotion|candidate_record_schema_invalid:H01"),
        ("integration_candidate", 0, "strict_boolean_violation:H01:integration_candidate|candidate_record_schema_invalid:H01"),
        ("admitted", "0", "strict_zero_integer_violation:H01:admitted|candidate_record_schema_invalid:H01"),
    ],
)
def test_economic_flags_and_counts_require_exact_types(tmp_path: Path, field: str, value: object, pattern: str) -> None:
    record_path, report_path, output_dir, _ = _fixture(tmp_path)
    _rewrite_record(record_path, lambda record: record["candidates"]["H01"].update(**{field: value}))
    with pytest.raises(ValueError, match=pattern):
        builder.write_completion(tmp_path, record_path, report_path, output_dir)


@pytest.mark.parametrize("relative", ["data/research/archives/current.json", "data/research/archival/current.json", "data/research/superseded/current.json", "data/research/lifecycle_metadata/current.json"])
def test_archival_and_lifecycle_paths_are_rejected(tmp_path: Path, relative: str) -> None:
    path = tmp_path / relative
    path.parent.mkdir(parents=True)
    path.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="archival_source_used_as_current"):
        builder._verify_source(tmp_path, relative, {"sha256": builder.sha256(path), "bytes": 2, "mtime_ns": path.stat().st_mtime_ns})


def test_candidate_map_rejects_extra_ids(tmp_path: Path) -> None:
    record_path, report_path, output_dir, _ = _fixture(tmp_path)
    _rewrite_record(record_path, lambda record: record["candidates"].update(EXTRA=dict(record["candidates"]["H01"])))
    with pytest.raises(ValueError, match="closure_candidate_bindings_invalid"):
        builder.write_completion(tmp_path, record_path, report_path, output_dir)


def test_candidate_map_rejects_missing_ids(tmp_path: Path) -> None:
    record_path, report_path, output_dir, _ = _fixture(tmp_path)
    _rewrite_record(record_path, lambda record: record["candidates"].pop("X06"))
    with pytest.raises(ValueError, match="closure_candidate_bindings_invalid"):
        builder.write_completion(tmp_path, record_path, report_path, output_dir)


def test_unspecified_fields_must_remain_null(tmp_path: Path) -> None:
    record_path, report_path, output_dir, _ = _fixture(tmp_path)
    _rewrite_record(record_path, lambda record: record["candidates"]["K02"]["unspecified_fields"].update(window="invented"))
    with pytest.raises(ValueError, match="unspecified_field_not_null:K02:window|candidate_record_schema_invalid:K02"):
        builder.write_completion(tmp_path, record_path, report_path, output_dir)


def test_generic_evidence_fallback_is_rejected(tmp_path: Path) -> None:
    record_path, report_path, output_dir, _ = _fixture(tmp_path)
    _rewrite_record(record_path, lambda record: record["candidates"]["K03"].update(evidence_summary="source-bound evidence"))
    with pytest.raises(ValueError, match="candidate_record_schema_invalid:K03"):
        builder.write_completion(tmp_path, record_path, report_path, output_dir)


def test_h14_stale_semantics_fail_closed(tmp_path: Path) -> None:
    record_path, report_path, output_dir, _ = _fixture(tmp_path)
    _rewrite_record(record_path, lambda record: record["candidates"]["H14"].update(status="PORTFOLIO_CONTRIBUTION_UNTESTED", research_status="PORTFOLIO_CONTRIBUTION_UNTESTED"))
    with pytest.raises(ValueError, match="candidate_status_binding_mismatch:H14|h14_status_invalid|candidate_record_schema_invalid:H14"):
        builder.write_completion(tmp_path, record_path, report_path, output_dir)


def test_receipt_mutation_fails_bundle_verification(tmp_path: Path) -> None:
    record_path, report_path, output_dir, _ = _fixture(tmp_path)
    builder.write_completion(tmp_path, record_path, report_path, output_dir)
    receipt_path = output_dir / "hash_receipt.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["report"]["sha256"] = "0" * 64
    receipt_path.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="completion_report_receipt_mismatch"):
        builder._verify_bundle(report_path, output_dir / "closure_ledger.json", receipt_path, record_path, tmp_path)


def test_receipt_self_hash_mutation_fails_bundle_verification(tmp_path: Path) -> None:
    record_path, report_path, output_dir, _ = _fixture(tmp_path)
    builder.write_completion(tmp_path, record_path, report_path, output_dir)
    receipt_path = output_dir / "hash_receipt.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["receipt_sha256"] = "0" * 64
    receipt_path.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="completion_receipt_self_hash_mismatch"):
        builder._verify_bundle(report_path, output_dir / "closure_ledger.json", receipt_path, record_path, tmp_path)


def test_partial_bundle_write_rolls_back(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    record_path, report_path, output_dir, _ = _fixture(tmp_path)
    original_replace = builder.os.replace
    calls = 0

    def fail_second_replace(source: Path, destination: Path) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("simulated_replace_failure")
        original_replace(source, destination)

    monkeypatch.setattr(builder.os, "replace", fail_second_replace)
    with pytest.raises(OSError, match="simulated_replace_failure"):
        builder.write_completion(tmp_path, record_path, report_path, output_dir)
    assert not report_path.exists() and not list(output_dir.glob("*"))


def test_post_write_verification_failure_rolls_back(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    record_path, report_path, output_dir, _ = _fixture(tmp_path)
    monkeypatch.setattr(builder, "_verify_bundle", lambda *args: (_ for _ in ()).throw(ValueError("forced_bundle_failure")))
    with pytest.raises(ValueError, match="forced_bundle_failure"):
        builder.write_completion(tmp_path, record_path, report_path, output_dir)
    assert not report_path.exists() and not list(output_dir.glob("*"))
