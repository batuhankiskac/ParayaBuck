from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from scripts.research import normalize_h11_nport_snapshot_2026_09_09 as h11


def test_snapshot_parses_structured_holdings_without_admitting_pit() -> None:
    report, rows = h11.parse_snapshot()
    assert report["parsed_holding_rows"] == 565
    assert report["flow_rows"] == 0
    assert report["admitted_rows"] == 0
    assert report["filer_cik"] == "0001100663"
    assert report["report_date"] == "2025-05-31"
    assert report["series_id"] == "S000031717"
    assert report["class_id"] == "C000098688"
    assert report["field_counts"]["name"] == 565
    assert report["field_counts"]["balance"] == 565
    assert report["field_counts"]["value_usd"] == 565
    assert report["field_counts"]["asset_category"] == 564
    assert report["provenance"]["submission_binding_status"] == "UNBOUND_ACCESSION"
    assert report["provenance"]["receipt_status"] == "ON_DISK"
    assert all(row["available_at"] is None and row["accepted_at"] is None for row in rows)


def test_snapshot_preserves_duplicate_identity_groups_and_row_occurrences() -> None:
    report, rows = h11.parse_snapshot()
    assert report["unique_security_identity_keys"] == 516
    assert report["duplicate_identity_rows"] == 49
    assert len({row["row_identity"] for row in rows}) == 565
    assert sum(row["security_identity_status"] == "IDENTIFIER_PRESENT" for row in rows) == 442


def test_current_h11_batch_is_twenty_ncsr_bodies_without_holdings_tags() -> None:
    assert h11.verify_current_h11_batch() == {
        "valid_body_count": 20,
        "forms": {"N-CSR": 10, "N-CSRS": 10},
        "nport_holding_tags": 0,
        "retryable_accessions": 0,
    }


def test_current_batch_body_hash_mutation_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    payload = json.loads(h11.BATCH.read_text(encoding="utf-8"))
    for row in payload["rows"]:
        if row.get("form") == "N-CSR" and row.get("status_code") == 200 and not row.get("content_validation_error"):
            row["body_sha256"] = "0" * 64
            break
    path = tmp_path / "nport_batch_receipt.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(h11, "BATCH", path)
    with pytest.raises(ValueError, match="current_h11_body_receipt_mismatch"):
        h11.verify_current_h11_batch()


def test_snapshot_manifest_verifies_and_cli_is_read_only() -> None:
    before = {path: (path.stat().st_mtime_ns, hashlib.sha256(path.read_bytes()).hexdigest()) for path in (h11.MANIFEST, h11.REPORT, h11.ROWS)}
    result = h11.verify()
    assert result["status"] == "H11_SNAPSHOT_MANIFEST_VERIFIED"
    after = {path: (path.stat().st_mtime_ns, hashlib.sha256(path.read_bytes()).hexdigest()) for path in (h11.MANIFEST, h11.REPORT, h11.ROWS)}
    assert after == before


def test_manifest_canonical_mutation_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    payload = json.loads(h11.MANIFEST.read_text(encoding="utf-8"))
    payload["status"] = "MUTATED"
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(h11, "MANIFEST", path)
    with pytest.raises(ValueError, match="manifest_canonical_hash_mismatch"):
        h11.verify()
