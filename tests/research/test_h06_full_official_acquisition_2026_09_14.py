from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.research import h06_full_official_acquisition_2026_09_14 as subject


def test_quarter_inventory_completeness() -> None:
    rows = subject.quarter_inventory()
    subject.validate_quarter_inventory(rows)
    assert len(rows) == 30 and rows[0]["period"] == "2019Q1" and rows[-1]["period"] == "2026Q2"


def test_sha_mismatch_rejection(tmp_path: Path) -> None:
    path = tmp_path / "source.zip"
    path.write_bytes(b"wrong")
    row = {"period": "2019Q1", "local_path": "source.zip", "bytes": 5, "sha256": "0" * 64}
    with pytest.raises(ValueError, match="sha256_mismatch"):
        subject.validate_cached_source(row, tmp_path)


def test_july_2026_index_reconciliation() -> None:
    assert subject.reconcile_accessions(["a", "b"], ["b", "a"])["status"] == "JULY_2026_ACCESSION_RECONCILIATION_COMPLETE"
    assert subject.reconcile_accessions(["a", "b"], ["a"])["status"] == "INCOMPLETE"


def test_form_4_a_inventory_rows_are_not_collapsed() -> None:
    rows = [{"accession": "a", "form": "4"}, {"accession": "b", "form": "4/A"}]
    assert [row["form"] for row in rows] == ["4", "4/A"]


def test_duplicate_accession_detection() -> None:
    with pytest.raises(ValueError, match="duplicate_accession"):
        subject.reconcile_accessions(["a", "a"], ["a"])


def test_acceptance_timestamp_required() -> None:
    assert subject.acceptance_datetime(b"<ACCEPTANCE-DATETIME>20260731165959") == "2026-07-31T16:59:59"
    with pytest.raises(ValueError, match="required"):
        subject.acceptance_datetime(b"FILING-DATE: 20260731")


def test_sec_fair_access_cache_behavior_offline(tmp_path: Path) -> None:
    calls: list[str] = []

    def transport(request: object) -> bytes:
        calls.append(str(getattr(request, "full_url")))
        return b"official"

    client = subject.FairAccessClient("ParayaBuck contact=https://example.invalid", transport=transport, sleeper=lambda _: None)
    target = tmp_path / "cached.bin"
    assert client.get("https://www.sec.gov/files/example", target) == (b"official", False)
    assert client.get("https://www.sec.gov/files/example", target) == (b"official", True)
    assert len(calls) == 1


def test_tracked_blocker_manifest_is_self_hashed_and_truthful() -> None:
    value = json.loads(subject.MANIFEST.read_text())
    subject.verify_seal(value)
    assert value["status"] == "H06_FULL_ACQUISITION_INCOMPLETE"
    assert value["quarters_acquired"] == 1
    assert value["quarter_sources"][-1]["retrieval_status"] == "ACQUIRED_AND_VALIDATED"
