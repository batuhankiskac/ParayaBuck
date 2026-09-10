from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[2] / "scripts/research/normalize_equity_h06_form4_2026_09_09.py"
SPEC = importlib.util.spec_from_file_location("h06_local", SCRIPT)
assert SPEC and SPEC.loader
h06 = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(h06)


def _first_metadata() -> dict:
    rows = json.loads(h06.BATCH_RECEIPT.read_text(encoding="utf-8"))["rows"]
    return next(row for row in rows if row.get("form") == "4" and row.get("status_code") == 200 and row.get("body_path"))


def test_timezone_aware_acceptance_is_normalized_to_utc() -> None:
    assert h06.accepted_utc("2026-06-29T20:03:17.000+03:00") == "2026-06-29T17:03:17.000Z"
    with pytest.raises(ValueError, match="timezone_aware"):
        h06.accepted_utc("2026-06-29T20:03:17")


def test_error_body_is_rejected_even_when_hash_is_self_consistent(tmp_path: Path) -> None:
    body = tmp_path / "error.html"
    body.write_text("<html><title>SEC.gov | File Unavailable</title>File Unavailable</html>", encoding="utf-8")
    metadata = {**_first_metadata(), "body_path": "temporary/error.html", "body_sha256": hashlib.sha256(body.read_bytes()).hexdigest()}
    with pytest.raises(ValueError, match="content_invalid"):
        h06.parse_body(body, metadata, "0" * 64)


def test_mutated_receipt_bound_body_fails_hash_validation(tmp_path: Path) -> None:
    metadata = _first_metadata()
    source = h06.REPO / metadata["body_path"]
    body = tmp_path / "mutated.xml"
    body.write_bytes(source.read_bytes() + b" mutation")
    metadata = {**metadata, "body_path": "temporary/mutated.xml"}
    with pytest.raises(ValueError, match="body_sha256_mismatch"):
        h06.parse_body(body, metadata, "0" * 64)


def test_identity_and_numeric_fields_are_typed_and_identity_mismatch_fails() -> None:
    metadata = _first_metadata()
    source = h06.REPO / metadata["body_path"]
    bad_metadata = {**metadata, "cik": "0000000000"}
    with pytest.raises(ValueError, match="issuer_cik_metadata_mismatch"):
        h06.parse_body(source, bad_metadata, "0" * 64)
    rows = json.loads(h06.ROWS.read_text(encoding="utf-8"))["rows"]
    candidates = [row for row in rows if row["disposition"] == "CANDIDATE_OPEN_MARKET_PURCHASE"]
    assert candidates and all(isinstance(row["shares_or_derivative_units"], float) for row in candidates)
    assert all(len(row["source_body_sha256"]) == 64 and len(row["source_receipt_sha256"]) == 64 for row in candidates)


def test_frozen_package_counts_and_manifest_are_fail_closed() -> None:
    result = h06.verify_manifest()
    assert result["status"] == "H06_LOCAL_NORMALIZATION_MANIFEST_VERIFIED"
    report = json.loads(h06.REPORT.read_text(encoding="utf-8"))
    assert report["input_documents"]["receipt_valid_bodies"] == 3993
    assert report["input_documents"]["invalid_or_unbound_bodies"] == 1
    assert report["parsed_transaction_rows"] == 10314
    assert report["candidate_rows_for_future_gate"] == 274
    assert report["admitted_economic_rows"] == 0
    assert report["routine_history"]["classification"] == "descriptive_only_no_horizon_or_threshold"
    assert report["routine_history"]["rows_with_prior_package_observation"] > 0
    lineage = report["amendment_lineage"]
    row_level = lineage["row_level_unmatched_or_non_unique"]
    documents = lineage["document_level"]
    assert lineage["amendment_documents"] == 62
    assert lineage["linked_amendments"] == 26
    assert row_level["row_count"] == 149
    assert row_level["unique_accession_count"] == 41
    assert len(row_level["accession_refs"]) == row_level["row_count"]
    assert len(row_level["unique_accessions"]) == row_level["unique_accession_count"]
    assert documents["total_documents"] == 62
    assert documents["uniquely_linked_documents"] == 26
    assert documents["linked_only_documents"] == 21
    assert documents["mixed_documents"] == 5
    assert documents["entirely_unmatched_documents"] == 36
    assert documents["accounting_check"] is True
    assert set(documents["mixed_accessions"]) == set(documents["uniquely_linked_accessions"]) & set(row_level["unique_accessions"])
    assert set(documents["entirely_unmatched_accessions"]) == set(row_level["unique_accessions"]) - set(documents["uniquely_linked_accessions"])
    assert report["row_accounting"] == {
        "parsed": 10314,
        "candidate_for_future_gate": 274,
        "admitted_economic": 0,
        "explicitly_excluded": 8418,
        "non_transaction_holding": 1574,
        "unknown": 48,
    }
    rows = json.loads(h06.ROWS.read_text(encoding="utf-8"))["rows"]
    assert any(row["transaction_code"] == "P" and row["disposition"] == "CANDIDATE_OPEN_MARKET_PURCHASE" for row in rows)
    assert all(row["derivative"] for row in rows if row["disposition"] == "EXCLUDED_DERIVATIVE_SECURITY")
    assert all(row["transaction_type"] == ("derivative_security" if row["derivative"] else "non_derivative_security") for row in rows)
    amendment_rows = [row for row in rows if row["form"] == "4/A"]
    assert len(amendment_rows) == 208
    assert all("replaced_accession" in row["amendment_lineage"] for row in amendment_rows)
