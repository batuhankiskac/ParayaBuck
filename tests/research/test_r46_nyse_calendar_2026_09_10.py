from __future__ import annotations

import copy
import hashlib
import json
import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest

from scripts.research import build_r46_nyse_calendar_2026_09_10 as calendar_builder
from scripts.research.r46_preflight_2026_09_10 import _read_jsonl
from scripts.research.r46_treasury_auction_readiness_2026_09_10 import (
    NYSE_CALENDAR_SCHEMA,
    validate_nyse_calendar,
)

REPO = Path(__file__).resolve().parents[2]
REQUIRED_ROW_FIELDS = {
    "regular_session_date",
    "regular_open_timestamp",
    "regular_close_timestamp",
    "holiday_or_closure_reason",
}


def test_build_is_deterministic_and_emits_the_pinned_complete_artifact() -> None:
    first = calendar_builder.build_calendar_artifact(REPO)
    second = calendar_builder.build_calendar_artifact(REPO)

    assert first == second
    assert first["schema"] == NYSE_CALENDAR_SCHEMA
    assert first["status"] == "COMPLETE"
    assert first["calendar"] == "NYSE"
    assert first["venue"] == "XNYS"
    assert first["window"] == {"start": "2016-01-01", "end_exclusive": "2026-08-01"}
    assert first["available_at"] == "2026-05-27T02:42:06Z"
    assert first["row_count"] == len(first["records"])
    assert first["records"]
    assert first["integrity"]["artifact_sha256"] == calendar_builder.canonical_artifact_hash(first)

    dependency = first["provenance"]["dependency"]
    assert dependency["name"] == "pandas-market-calendars"
    assert dependency["import_name"] == "pandas_market_calendars"
    assert dependency["version"] == "5.4.0"
    assert dependency["bounds"] == ">=5.4.0,<6"
    assert dependency["lock"]["path"] == "uv.lock"
    assert dependency["lock"]["sha256"] == hashlib.sha256((REPO / "uv.lock").read_bytes()).hexdigest()
    assert first["provenance"]["generator"]["path"].endswith(
        "scripts/research/build_r46_nyse_calendar_2026_09_10.py"
    )
    assert "uv.lock" in first["provenance"]["available_at_basis"]
    assert "upload" in first["provenance"]["available_at_basis"]


def test_every_record_has_only_required_fields_and_existing_validator_accepts_it() -> None:
    artifact = calendar_builder.build_calendar_artifact(REPO)

    assert all(set(row) == REQUIRED_ROW_FIELDS for row in artifact["records"])
    assert all(row["holiday_or_closure_reason"] is None for row in artifact["records"])
    validated = validate_nyse_calendar(artifact["records"], available_at=artifact["available_at"])
    assert validated["schema"] == NYSE_CALENDAR_SCHEMA
    assert validated["status"] == "COMPLETE"
    assert validated["rows"] == artifact["row_count"]


def test_nyse_regular_open_uses_dst_correct_utc_offsets() -> None:
    records = {row["regular_session_date"]: row for row in calendar_builder.build_calendar_artifact(REPO)["records"]}

    assert records["2016-01-04"]["regular_open_timestamp"] == "2016-01-04T14:30:00Z"
    assert records["2024-07-01"]["regular_open_timestamp"] == "2024-07-01T13:30:00Z"
    assert records["2024-01-02"]["regular_open_timestamp"] == "2024-01-02T14:30:00Z"


def test_early_close_is_preserved_as_the_calendar_close() -> None:
    records = {row["regular_session_date"]: row for row in calendar_builder.build_calendar_artifact(REPO)["records"]}

    assert records["2024-11-29"]["regular_open_timestamp"] == "2024-11-29T14:30:00Z"
    assert records["2024-11-29"]["regular_close_timestamp"] == "2024-11-29T18:00:00Z"


def test_output_contains_sessions_only_without_weekends_or_holidays() -> None:
    session_dates = {row["regular_session_date"] for row in calendar_builder.build_calendar_artifact(REPO)["records"]}

    assert "2024-07-04" not in session_dates
    assert "2024-07-06" not in session_dates
    assert "2024-07-07" not in session_dates
    assert all(date.fromisoformat(value).weekday() < 5 for value in session_dates)


def test_dependency_version_mismatch_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(calendar_builder, "_installed_dependency_version", lambda: "5.4.1")

    with pytest.raises(ValueError, match="dependency_version_mismatch"):
        calendar_builder.build_calendar_artifact(REPO)


def test_lock_pin_mismatch_fails_closed(tmp_path: Path) -> None:
    lock_path = tmp_path / "uv.lock"
    lock_path.write_bytes((REPO / "uv.lock").read_bytes().replace(b'version = "5.4.0"', b'version = "5.4.1"', 1))

    with pytest.raises(ValueError, match="lock_dependency_version_mismatch"):
        calendar_builder.read_dependency_pin(lock_path, installed_version="5.4.0")


def test_tampering_invalidates_the_canonical_self_hash() -> None:
    artifact = calendar_builder.build_calendar_artifact(REPO)
    tampered = copy.deepcopy(artifact)
    tampered["records"][0]["regular_close_timestamp"] = "2016-01-04T21:01:00Z"

    with pytest.raises(ValueError, match="artifact_self_hash_mismatch"):
        calendar_builder.verify_artifact(tampered, REPO)


def test_atomic_writer_refuses_to_overwrite_existing_output(tmp_path: Path) -> None:
    output = tmp_path / "r46_nyse_calendar.jsonl"
    receipt = tmp_path / "r46_nyse_calendar.receipt.json"
    calendar_builder.write_artifact(output, REPO, receipt=receipt)
    original = output.read_bytes()

    with pytest.raises(FileExistsError, match="overwrite"):
        calendar_builder.write_artifact(output, REPO, receipt=receipt)

    assert output.read_bytes() == original


def test_writer_emits_preflight_compatible_jsonl_and_hash_pinned_receipt(tmp_path: Path) -> None:
    output = tmp_path / "r46_nyse_calendar.jsonl"
    receipt = tmp_path / "r46_nyse_calendar.receipt.json"

    result = calendar_builder.write_artifact(output, REPO, receipt=receipt)
    records = _read_jsonl(output)
    envelope = json.loads(receipt.read_text(encoding="utf-8"))

    assert result["path"] == str(output)
    assert result["receipt_path"] == str(receipt)
    assert records
    assert envelope["schema"] == "r46-nyse-regular-calendar-receipt-v1"
    assert envelope["status"] == "COMPLETE"
    assert envelope["records"]["path"] == str(output)
    assert envelope["records"]["sha256"] == hashlib.sha256(output.read_bytes()).hexdigest()
    assert envelope["records"]["row_count"] == len(records)
    assert envelope["publication"] == calendar_builder.RECEIPT_PUBLICATION_SEMANTICS
    assert envelope["integrity"]["receipt_sha256"] == calendar_builder.canonical_receipt_hash(envelope)
    assert calendar_builder.verify_artifact_file(output, REPO, receipt=receipt)["rows"] == len(records)


def test_jsonl_tamper_duplicate_and_invalid_row_fail_closed(tmp_path: Path) -> None:
    output = tmp_path / "r46_nyse_calendar.jsonl"
    receipt = tmp_path / "r46_nyse_calendar.receipt.json"
    calendar_builder.write_artifact(output, REPO, receipt=receipt)
    records = _read_jsonl(output)

    output.write_text(
        "".join(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n" for row in [records[0], records[0]]),
        encoding="utf-8",
    )
    with pytest.raises(ValueError):
        calendar_builder.verify_artifact_file(output, REPO, receipt=receipt)

    output.write_text(
        json.dumps({"regular_session_date": "2016-01-04"}, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError):
        calendar_builder.verify_artifact_file(output, REPO, receipt=receipt)


@pytest.mark.parametrize("existing_target", ["records", "receipt"])
def test_two_target_collision_leaves_missing_target_unwritten(tmp_path: Path, existing_target: str) -> None:
    output = tmp_path / "r46_nyse_calendar.jsonl"
    receipt = tmp_path / "r46_nyse_calendar.receipt.json"
    existing = output if existing_target == "records" else receipt
    existing.write_bytes(b"sentinel\n")

    with pytest.raises(FileExistsError, match="overwrite"):
        calendar_builder.write_artifact(output, REPO, receipt=receipt)

    assert output.exists() is (existing_target == "records")
    assert receipt.exists() is (existing_target == "receipt")
    assert existing.read_bytes() == b"sentinel\n"


def test_cli_writes_jsonl_and_explicit_receipt_without_network(tmp_path: Path) -> None:
    output = tmp_path / "calendar.jsonl"
    receipt = tmp_path / "calendar.receipt.json"
    completed = subprocess.run(
        [
            sys.executable,
            str(calendar_builder.SCRIPT_PATH),
            "--repo",
            str(REPO),
            "--output",
            str(output),
            "--receipt",
            str(receipt),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    summary = json.loads(completed.stdout)
    assert summary["status"] == "COMPLETE"
    assert summary["rows"] == len(_read_jsonl(output))
    assert calendar_builder.verify_artifact_file(output, REPO, receipt=receipt)["status"] == "COMPLETE"


def test_stage_payload_removes_temp_file_when_fsync_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "records.jsonl"

    def fail_fsync(_descriptor: int) -> None:
        raise OSError("injected fsync failure")

    monkeypatch.setattr(calendar_builder.os, "fsync", fail_fsync)

    with pytest.raises(OSError, match="injected fsync failure"):
        calendar_builder._stage_payload(target, b"payload\n")

    assert list(tmp_path.glob(".records.jsonl.*.tmp")) == []


def test_publish_exception_rolls_back_new_targets_but_not_existing_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "records.jsonl"
    receipt = tmp_path / "records.receipt.json"
    existing = tmp_path / "existing.txt"
    existing.write_bytes(b"keep")
    calls = 0

    def fail_directory_fsync(_directory: Path) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("injected directory fsync failure")

    monkeypatch.setattr(calendar_builder, "_fsync_directory", fail_directory_fsync)

    with pytest.raises(OSError, match="injected directory fsync failure"):
        calendar_builder._write_pair_atomically_no_overwrite(
            {output: b"records\n", receipt: b"receipt\n"}
        )

    assert not output.exists()
    assert not receipt.exists()
    assert existing.read_bytes() == b"keep"
    assert list(tmp_path.glob(".*.tmp")) == []


def test_orphan_records_without_receipt_are_not_complete(tmp_path: Path) -> None:
    output = tmp_path / "records.jsonl"
    receipt = tmp_path / "records.receipt.json"
    records = calendar_builder.build_calendar_artifact(REPO)["records"]
    output.write_bytes(calendar_builder.canonical_jsonl_bytes(records))

    with pytest.raises(ValueError):
        calendar_builder.verify_artifact_file(output, REPO, receipt=receipt)
    with pytest.raises(ValueError):
        calendar_builder.verify_receipt_file(receipt, records_path=output, repo=REPO)
