from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.research.normalize_equity_secondary_h08_h15_2026_09_09 import (
    _latest_receipt_rows,
    build_package,
    parse_h08_body,
    parse_h08_release_candidates,
    parse_h08_text_candidates,
    parse_h15_body,
    verify_package,
)


def test_h08_extracts_only_dimension_free_context_bound_eps() -> None:
    body = """
    <xbrli:context id="c1"><xbrli:entity><xbrli:identifier scheme="sec">0000000001</xbrli:identifier></xbrli:entity>
    <xbrli:period><xbrli:startDate>2025-01-01</xbrli:startDate><xbrli:endDate>2025-03-31</xbrli:endDate></xbrli:period></xbrli:context>
    <xbrli:context id="c2"><xbrli:entity><xbrli:identifier scheme="sec">0000000001</xbrli:identifier><xbrli:segment><xbrldi:explicitMember>ClassB</xbrldi:explicitMember></xbrli:segment></xbrli:entity>
    <xbrli:period><xbrli:startDate>2025-01-01</xbrli:startDate><xbrli:endDate>2025-03-31</xbrli:endDate></xbrli:period></xbrli:context>
    <xbrli:unit id="u1"><xbrli:measure>iso4217:USD</xbrli:measure></xbrli:unit>
    <ix:nonfraction name="us-gaap:EarningsPerShareDiluted" contextRef="c1" unitRef="u1">1.25</ix:nonfraction>
    <ix:nonfraction name="us-gaap:EarningsPerShareDiluted" contextRef="c2" unitRef="u1">9.99</ix:nonfraction>
    """
    row = {"symbol": "TEST", "cik": "1", "accn": "0000000001-25-000001", "accepted": "2025-04-01T12:00:00Z"}

    parsed = parse_h08_body(body.encode(), row, body_path="fixture.htm", body_sha256="body")

    assert len(parsed) == 1
    assert parsed[0]["value"] == 1.25
    assert parsed[0]["basis"] == "diluted"
    assert parsed[0]["fiscal_period"] == {"start": "2025-01-01", "end": "2025-03-31"}
    assert parsed[0]["release_at"] is None
    assert parsed[0]["accepted_at"] == row["accepted"]
    assert parsed[0]["source_span"]["start"] < parsed[0]["source_span"]["end"]


def test_h15_extracts_provable_dates_and_keeps_unproven_events_null() -> None:
    body = "The transaction agreement was dated as of March 12, 2024, as amended and restated by the deed dated as of May 20, 2024. The proxy is dated June 6, 2024. Completion is expected in the third calendar quarter of 2024."
    row = {"symbol": "AA", "cik": "1675149", "accn": "0001193125-24-156116", "accepted": "2024-06-07T12:00:00Z"}

    parsed = parse_h15_body(body.encode(), row, body_path="fixture.htm", body_sha256="body")

    assert parsed["target_name"] is None
    assert parsed["agreement_date"] == "2024-03-12"
    assert parsed["amendment_restated_date"] == "2024-05-20"
    assert parsed["expected_close_text"] == "third calendar quarter of 2024"
    assert parsed["announcement_at"] is None
    assert parsed["close_at"] is None
    assert parsed["date_spans"]["agreement_date"]["text"]


def test_package_verify_rejects_body_mutation(tmp_path: Path) -> None:
    out = tmp_path / "normalization"
    manifest = build_package(out=out)
    assert manifest["artifact_count"] > 0
    assert verify_package(out)["verified"] is True

    rows_path = out / "parsed" / "h08_actual_eps_rows.json"
    rows = json.loads(rows_path.read_text())
    rows.append({"mutation": True})
    rows_path.write_text(json.dumps(rows, indent=2, sort_keys=True) + "\n")

    with pytest.raises(ValueError, match="artifact_mismatch"):
        verify_package(out)


def test_frozen_package_counts_and_external_consensus_boundary(tmp_path: Path) -> None:
    out = tmp_path / "normalization"
    build_package(out=out)
    report = json.loads((out / "normalization_report.json").read_text())

    assert report["H08"]["receipt_valid_bodies"] == 991
    assert report["H08"]["actual_eps_rows"] == 0
    assert report["H08"]["text_eps_candidate_rows"] == 7
    assert report["H08"]["numeric_proximity_bodies"] == 39
    assert report["H08"]["release_date_prose_candidate_bodies"] == 94
    assert report["H08"]["teleconference_time_candidate_bodies"] == 19
    assert report["H08"]["consensus_vintage_rows"] == 0
    assert report["H08"]["surprise_rows"] == 0
    assert report["H15"]["receipt_valid_bodies"] == 1
    assert report["H15"]["announcement_date_candidate_count"] == 2
    assert report["H15"]["economic_admissible_rows"] == 0


def test_h15_preserves_currency_ambiguity_and_source_spans(tmp_path: Path) -> None:
    out = tmp_path / "normalization"
    build_package(out=out)
    rows = json.loads((out / "parsed" / "h15_lifecycle_metadata_rows.json").read_text())
    value = rows[0]["consideration_value"]
    assert value["currency"] is None
    assert value["currency_status"] == "AMBIGUOUS"
    assert value["currency_symbol"] == "$"
    assert "$3.3" in rows[0]["raw_consideration_text"]
    assert "billion" in rows[0]["raw_consideration_text"]
    assert rows[0]["party_spans"]["Alumina Limited"]["text"] == "Alumina Limited"


def test_h08_preserves_prior_valid_body_conflicts(tmp_path: Path) -> None:
    out = tmp_path / "normalization"
    build_package(out=out)
    dispositions = json.loads((out / "parsed" / "h08_accession_dispositions.json").read_text())
    assert len(dispositions) == 1407
    assert sum(row["any_valid_body"] for row in dispositions) == 999
    assert sum(row["latest_attempt_valid"] for row in dispositions) == 991
    conflicts = [row for row in dispositions if row["disposition"] == "PRIOR_VALID_BODY_PRESERVED_LATEST_CONFLICT"]
    assert len(conflicts) == 8
    assert all(row["preserved_body"]["body_path"] for row in conflicts)


def test_h08_accounts_numeric_text_candidates_and_unresolved_release_prose() -> None:
    receipt = json.loads(Path("data/research/external/equity_secondary_public_2026_09_08/sec_batch_receipt.json").read_text())
    latest = {row["accn"]: row for row in receipt["rows"] if row.get("accn")}
    numeric = []
    releases = []
    for accession in (
        "0001292814-26-003970", "0001292814-26-002777", "0001292814-24-001911",
        "0001292814-22-002112", "0001292814-22-000611",
    ):
        row = latest[accession]
        body = Path(row["body_path"]).read_bytes()
        numeric.extend(parse_h08_text_candidates(body, row, body_path=row["body_path"], body_sha256=row["body_sha256"]))
    for row in latest.values():
        if row.get("form") in {"8-K", "8-K/A", "6-K", "6-K/A"} and row.get("status_code") == 200 and row.get("body_path") and not row.get("content_validation_error"):
            body = Path(row["body_path"]).read_bytes()
            releases.extend(parse_h08_release_candidates(body, row, body_path=row["body_path"], body_sha256=row["body_sha256"]))

    assert len(numeric) == 7
    assert all(item["admitted"] is False and item["unit_basis"] for item in numeric)
    assert len(releases) >= 94
    assert all(item["release_at"] is None and item["admitted"] is False for item in releases)


def test_duplicate_accession_prefers_receipt_valid_body() -> None:
    rows = _latest_receipt_rows(
        {
            "rows": [
                {"accn": "1", "form": "DEFM14A", "status_code": 200, "body_path": "x", "content_validation_error": ""},
                {"accn": "1", "form": "DEFM14A", "status_code": 200, "body_path": "x", "content_validation_error": "sec_document_issuer_cik_missing"},
            ]
        },
        {"DEFM14A"},
        prefer_valid=True,
    )
    assert len(rows) == 1
    assert rows[0]["content_validation_error"] == ""
