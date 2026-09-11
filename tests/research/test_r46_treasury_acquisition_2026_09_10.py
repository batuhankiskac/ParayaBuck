from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from scripts.research import acquire_r46_treasury_documents_2026_09_10 as acquisition

build_document_record = acquisition.build_document_record
build_tlt_alpaca_query = acquisition.build_tlt_alpaca_query
fetch_official_document = acquisition.fetch_official_document
official_source_routes = acquisition.official_source_routes
sanitized_blocked_receipt = acquisition.sanitized_blocked_receipt


REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts/research/acquire_r46_treasury_documents_2026_09_10.py"
CAPABILITY_RECEIPT = REPO / "data/research_receipts/r46_treasury_source_capability_2026_09_10.json"
RESEARCH_ID = "r46_treasury_auction_unwind_tlt_v1_2026_09_10"


def _source_body(*, published_at: str = "2024-01-31T08:30:00-05:00", available_at: str = "2024-01-31T08:30:00-05:00") -> bytes:
    return (
        b"%PDF-1.7\n"
        + f"Published At: {published_at}\n".encode()
        + f"Available At: {available_at}\n".encode()
        + b"Auction Date: February 08, 2024\n"
        + b"Original Issue Date: February 15, 2024\n"
        + b"Security Term: 30-Year Bond\n"
        + b"CUSIP Number: 912810TX6\n"
    )


def _metadata(kind: str, identifier: str, *, result_status: str | None = None) -> dict[str, object]:
    return {
        "document_id": identifier,
        "document_kind": kind,
        "canonical_event_id": "2024-02-08-30Y-ORIGINAL",
        "source_timezone": "America/New_York",
        "security_type": "nominal",
        "instrument_type": "US_TREASURY_SECURITY",
        "issuer_identifier": "US_TREASURY",
        "original_tenor": 30,
        "issue_or_reopening": "original",
        "security_identifier": "912810TX6",
        "auction_session_date": "2024-02-08",
        "correction_or_amendment_chain": [],
        "revision_id": "original",
        "supersedes_document_id": None,
        "result_status": result_status,
    }


def test_official_source_routes_are_https_and_cover_archive_query_and_documents() -> None:
    routes = official_source_routes()

    assert routes["archive"] == "https://treasurydirect.gov/auctions/announcements-data-results/announcement-results-press-releases/"
    assert routes["auction_query"] == "https://treasurydirect.gov/auctions/auction-query/"
    assert routes["fiscal_documents"] == "https://fiscaldata.treasury.gov/static-data/published-reports/auctions-query/"
    assert routes["legacy_json_search"].startswith("https://www.treasurydirect.gov/TA_WS/securities/search?")
    assert "format=json" in routes["legacy_json_search"]
    assert routes["announced_rss"] == "https://www.treasurydirect.gov/TA_WS/securities/announced/rss"
    assert routes["auctioned_result_rss"] == "https://www.treasurydirect.gov/TA_WS/securities/auctioned/rss"
    assert routes["xml_base"] == "https://www.treasurydirect.gov/xml/"
    assert routes["xml_xsd"].endswith("/xsd/Auction_v6_0_0.xsd")
    assert all(value.startswith("https://") for value in routes.values())
    assert all("@" not in value and "key=" not in value and "secret=" not in value for value in routes.values())


def _legacy_30y_row(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "cusip": "912810TX6",
        "auctionDate": "2024-02-08T00:00:00",
        "issueDate": "2024-02-15T00:00:00",
        "securityType": "Bond",
        "originalIssueDate": "",
        "originalCusip": "",
        "securityTerm": "29-Year 11-Month",
        "originalSecurityTerm": "30-Year",
        "reopening": "No",
        "tips": "No",
        "pdfFilenameAnnouncement": "A_20240131_2.pdf",
        "xmlFilenameAnnouncement": "A_20240131_2.xml",
        "pdfFilenameCompetitiveResults": "R_20240208_3.pdf",
        "xmlFilenameCompetitiveResults": "R_20240208_3.xml",
        "updatedTimestamp": "2026-09-10T12:34:56Z",
        "offeringAmount": "10000000000",
        "highYield": "4.25",
    }
    row.update(overrides)
    return row


def test_normalize_legacy_json_array_keeps_only_identity_and_dataset_update() -> None:
    events = acquisition.normalize_legacy_json_array(
        json.dumps([_legacy_30y_row()]).encode("utf-8"),
        source_base_url="https://www.treasurydirect.gov/xml/",
    )

    assert events == [
        {
            "cusip": "912810TX6",
            "auction_date": "2024-02-08",
            "issue_date": "2024-02-15",
            "original_issue_date": None,
            "original_cusip": None,
            "issue_or_reopening": "original",
            "announcement": {
                "xml_name": "A_20240131_2.xml",
                "xml_url": "https://www.treasurydirect.gov/xml/A_20240131_2.xml",
                "pdf_name": "A_20240131_2.pdf",
                "pdf_url": "https://www.treasurydirect.gov/instit/annceresult/press/preanre/2024/A_20240131_2.pdf",
            },
            "result": {
                "xml_name": "R_20240208_3.xml",
                "xml_url": "https://www.treasurydirect.gov/xml/R_20240208_3.xml",
                "pdf_name": "R_20240208_3.pdf",
                "pdf_url": "https://www.treasurydirect.gov/instit/annceresult/press/preanre/2024/R_20240208_3.pdf",
            },
            "dataset_update": "2026-09-10T12:34:56Z",
        }
    ]
    serialized = json.dumps(events, sort_keys=True)
    assert "offeringAmount" not in serialized and "highYield" not in serialized
    assert "published_at" not in serialized and "available_at" not in serialized


def test_normalize_legacy_json_array_filters_non_candidates_and_rejects_candidate_duplicates() -> None:
    events = acquisition.normalize_legacy_json_array(
        json.dumps([_legacy_30y_row(originalSecurityTerm="20-Year"), _legacy_30y_row()]).encode()
    )
    assert len(events) == 1 and events[0]["cusip"] == "912810TX6"
    events = acquisition.normalize_legacy_json_array(
        json.dumps([_legacy_30y_row(tips="Yes"), _legacy_30y_row()]).encode()
    )
    assert len(events) == 1 and events[0]["cusip"] == "912810TX6"
    with pytest.raises(ValueError, match="no eligible"):
        acquisition.normalize_legacy_json_array(json.dumps([_legacy_30y_row(originalSecurityTerm="20-Year")]).encode())
    with pytest.raises(ValueError, match="duplicate"):
        acquisition.normalize_legacy_json_array(json.dumps([_legacy_30y_row(), _legacy_30y_row()]).encode())


def test_normalize_legacy_json_array_fails_closed_for_missing_selected_identity() -> None:
    with pytest.raises(ValueError, match="CUSIP"):
        acquisition.normalize_legacy_json_array(json.dumps([_legacy_30y_row(cusip="")]).encode())
    with pytest.raises(ValueError, match="tips"):
        acquisition.normalize_legacy_json_array(json.dumps([_legacy_30y_row(tips=None)]).encode())


def test_normalize_legacy_json_array_validates_bond_and_reopening_lineage() -> None:
    reopening = _legacy_30y_row(
        cusip="912810TX6",
        auctionDate="2024-05-09T00:00:00",
        issueDate="2024-05-16T00:00:00",
        originalIssueDate="2024-02-15T00:00:00",
        originalCusip="",
        reopening="Yes",
        pdfFilenameAnnouncement="A_20240509_2.pdf",
        xmlFilenameAnnouncement="A_20240509_2.xml",
        pdfFilenameCompetitiveResults="R_20240509_3.pdf",
        xmlFilenameCompetitiveResults="R_20240509_3.xml",
    )
    events = acquisition.normalize_legacy_json_array(json.dumps([reopening]).encode())
    assert events[0]["issue_or_reopening"] == "reopening"
    assert events[0]["original_issue_date"] == "2024-02-15"
    assert events[0]["original_cusip"] is None
    with pytest.raises(ValueError, match="securityType"):
        acquisition.normalize_legacy_json_array(json.dumps([_legacy_30y_row(securityType="Note")]).encode())
    with pytest.raises(ValueError, match="originalIssueDate"):
        acquisition.normalize_legacy_json_array(json.dumps([reopening | {"originalIssueDate": ""}]).encode())
    with pytest.raises(ValueError, match="originalIssueDate"):
        acquisition.normalize_legacy_json_array(
            json.dumps([reopening | {"originalIssueDate": "2024-05-09T00:00:00"}]).encode()
        )
    with pytest.raises(ValueError, match="originalCusip"):
        acquisition.normalize_legacy_json_array(
            json.dumps([reopening | {"originalCusip": "912810UV2"}]).encode()
        )


def test_normalize_legacy_json_array_validates_filename_dates_and_derives_pdf_year() -> None:
    prior_announcement = _legacy_30y_row(
        pdfFilenameAnnouncement="A_20231231_2.pdf",
        xmlFilenameAnnouncement="A_20231231_2.xml",
    )
    events = acquisition.normalize_legacy_json_array(json.dumps([prior_announcement]).encode())
    assert events[0]["announcement"]["pdf_url"].startswith(
        "https://www.treasurydirect.gov/instit/annceresult/press/preanre/2023/"
    )
    with pytest.raises(ValueError, match="announcement.*date"):
        acquisition.normalize_legacy_json_array(
            json.dumps(
                [
                    _legacy_30y_row(
                        pdfFilenameAnnouncement="A_20240209_2.pdf",
                        xmlFilenameAnnouncement="A_20240209_2.xml",
                    )
                ]
            ).encode()
        )
    with pytest.raises(ValueError, match="result.*date"):
        acquisition.normalize_legacy_json_array(
            json.dumps(
                [
                    _legacy_30y_row(
                        pdfFilenameCompetitiveResults="R_20240209_3.pdf",
                        xmlFilenameCompetitiveResults="R_20240209_3.xml",
                    )
                ]
            ).encode()
        )


def _result_xml(*, cusip: str = "912810TX6", auction_date: str = "2024-02-08", release_time: str = "13:00:00", pdf_name: str = "R_20240208_3.pdf", schema_location: str = "http://www.treasurydirect.gov/ https://www.treasurydirect.gov/xsd/Auction_v6_0_0.xsd") -> bytes:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<td:AuctionData xmlns:td="http://www.treasurydirect.gov/" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xsi:schemaLocation="{schema_location}">
  <AuctionAnnouncement>
    <CUSIP>{cusip}</CUSIP>
    <AuctionDate>{auction_date}</AuctionDate>
  </AuctionAnnouncement>
  <AuctionResults>
    <ReleaseTime>{release_time}</ReleaseTime>
    <ResultsPDFName>{pdf_name}</ResultsPDFName>
  </AuctionResults>
</td:AuctionData>
""".encode()


def test_parse_result_xml_is_raw_unadmitted_and_hash_pinned() -> None:
    body = _result_xml()
    parsed = acquisition.parse_result_xml(body, "https://www.treasurydirect.gov/xml/R_20240208_3.xml")

    assert parsed["cusip"] == "912810TX6"
    assert parsed["auction_date"] == "2024-02-08"
    assert parsed["release_time"] == "13:00:00"
    assert parsed["results_pdf_name"] == "R_20240208_3.pdf"
    assert parsed["raw_sha256"] == hashlib.sha256(body).hexdigest()
    assert parsed["source_timezone"] is None
    assert parsed["published_at"] is None
    assert parsed["available_at"] is None
    assert parsed["schema_location"].endswith("/xsd/Auction_v6_0_0.xsd")
    assert parsed["schema_version"] == "v6_0_0"
    assert parsed["schema_validation_status"] == "NOT_PERFORMED"
    assert "XML_SCHEMA_VALIDATION_NOT_PERFORMED" in parsed["blockers"]
    assert parsed["within_historical_window"] is True
    assert parsed["historical_admission_eligible"] is False
    assert parsed["status"] in {"RAW_UNADMITTED", "PRESENT_NOT_ADMITTED"}
    assert parsed["admitted"] is False
    assert {"SOURCE_TIMEZONE_UNPROVEN", "RESULT_VERSION_LINEAGE_UNPROVEN", "PUBLIC_AVAILABILITY_UNPROVEN"}.issubset(parsed["blockers"])


@pytest.mark.parametrize(
    "body, match",
    [
        (b"<Result>", "XML"),
        (_result_xml(cusip="BAD"), "CUSIP"),
        (_result_xml(auction_date="2024-99-99"), "date"),
        (_result_xml(release_time="not-a-time"), "time"),
        (_result_xml(pdf_name="R_20240208_3.txt"), "file"),
    ],
)
def test_parse_result_xml_rejects_identity_failures(body: bytes, match: str) -> None:
    with pytest.raises(ValueError, match=match):
        acquisition.parse_result_xml(body, "https://www.treasurydirect.gov/xml/R_20240208_3.xml")


def test_parse_result_xml_requires_exact_xml_route_and_declares_schema_without_validating() -> None:
    with pytest.raises(ValueError, match="source URL"):
        acquisition.parse_result_xml(_result_xml(), "https://www.treasurydirect.gov/private/R_20240208_3.xml")
    with pytest.raises(ValueError, match="schema"):
        acquisition.parse_result_xml(_result_xml(schema_location="http://example.invalid/schema.xsd"), "https://www.treasurydirect.gov/xml/R_20240208_3.xml")


def test_parse_result_xml_retains_v7_schema_version_without_claiming_validation() -> None:
    parsed = acquisition.parse_result_xml(
        _result_xml(schema_location="http://www.treasurydirect.gov/ https://www.treasurydirect.gov/xsd/Auction_v7_0_0.xsd"),
        "https://www.treasurydirect.gov/xml/R_20240208_3.xml",
    )
    assert parsed["schema_version"] == "v7_0_0"
    assert parsed["schema_validation_status"] == "NOT_PERFORMED"


def test_parse_result_xml_marks_post_cutoff_as_capability_only() -> None:
    body = _result_xml(auction_date="2026-09-10", pdf_name="R_20260910_3.pdf")
    parsed = acquisition.parse_result_xml(body, "https://www.treasurydirect.gov/xml/R_20260910_3.xml")
    assert parsed["within_historical_window"] is False
    assert parsed["historical_admission_eligible"] is False
    assert parsed["capability_evidence_only"] is True
    assert "POST_CUTOFF_CAPABILITY_ONLY" in parsed["blockers"]


def _result_rss() -> bytes:
    return b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <item>
    <title>30-Year Treasury Auction Results</title>
    <pubDate>Thu, 08 Feb 2024 13:05:00 GMT</pubDate>
    <description>&lt;p&gt;CUSIP: 912810TX6&lt;br/&gt;Term and Type: 30-Year Bond&lt;br/&gt;Auction Date: 02/08/2024&lt;br/&gt;
      &lt;a href="https://www.treasurydirect.gov/instit/annceresult/press/preanre/2024/R_20240208_3.pdf"&gt;PDF&lt;/a&gt;
      &lt;a href="https://www.treasurydirect.gov/xml/R_20240208_3.xml"&gt;XML&lt;/a&gt;&lt;/p&gt;</description>
    <link>https://www.treasurydirect.gov/auctions/announcements-data-results/announcement-results-press-releases/</link>
  </item>
</channel></rss>"""


def test_parse_result_rss_normalizes_pubdate_for_crosscheck_only() -> None:
    parsed = acquisition.parse_result_rss(_result_rss(), "https://www.treasurydirect.gov/TA_WS/securities/auctioned/rss")

    assert parsed["status"] == "CROSSCHECK_ONLY"
    assert parsed["retention_complete"] is False
    assert parsed["inventory_source"] is False
    assert parsed["admission_source"] is False
    assert parsed["items"][0]["pub_date_utc"] == "2024-02-08T13:05:00Z"
    assert parsed["items"][0]["pub_date"] == "Thu, 08 Feb 2024 13:05:00 GMT"
    assert parsed["items"][0]["cusip"] == "912810TX6"
    assert parsed["items"][0]["term"] == "30-Year"
    assert parsed["items"][0]["auction_date"] == "2024-02-08"
    assert parsed["items"][0]["pdf_url"].endswith("R_20240208_3.pdf")
    assert parsed["items"][0]["xml_url"].endswith("R_20240208_3.xml")
    assert parsed["items"][0].get("available_at") is None
    assert parsed["historical_window"] == {"start": "2016-01-01", "end_exclusive": "2026-08-01"}
    assert parsed["items"][0]["within_historical_window"] is True
    assert parsed["items"][0]["historical_admission_eligible"] is False


def test_parse_result_rss_rejects_duplicates_and_mismatched_link_identity() -> None:
    with pytest.raises(ValueError, match="CUSIP"):
        acquisition.parse_result_rss(_result_rss().replace(b"CUSIP: 912810TX6", b"CUSIP: 912810TX6; CUSIP: 912810TX6"), "https://www.treasurydirect.gov/TA_WS/securities/auctioned/rss")
    mismatch = _result_rss().replace(b"R_20240208_3.pdf", b"R_20240209_3.pdf")
    with pytest.raises(ValueError, match="identity"):
        acquisition.parse_result_rss(mismatch, "https://www.treasurydirect.gov/TA_WS/securities/auctioned/rss")


def test_parse_result_rss_requires_exact_endpoint_zero_offset_and_marks_post_cutoff() -> None:
    with pytest.raises(ValueError, match="source URL"):
        acquisition.parse_result_rss(_result_rss(), "https://www.treasurydirect.gov/TA_WS/securities/announced/rss")
    with pytest.raises(ValueError, match="GMT|UTC"):
        acquisition.parse_result_rss(
            _result_rss().replace(b"13:05:00 GMT", b"13:05:00 -0500"),
            "https://www.treasurydirect.gov/TA_WS/securities/auctioned/rss",
        )
    post_cutoff = _result_rss().replace(b"08 Feb 2024", b"10 Sep 2026").replace(b"02/08/2024", b"09/10/2026").replace(b"preanre/2024/R_20240208_3", b"preanre/2026/R_20260910_3").replace(b"/xml/R_20240208_3.xml", b"/xml/R_20260910_3.xml")
    parsed = acquisition.parse_result_rss(post_cutoff, "https://www.treasurydirect.gov/TA_WS/securities/auctioned/rss")
    assert parsed["items"][0]["within_historical_window"] is False
    assert parsed["items"][0]["historical_admission_eligible"] is False
    assert parsed["items"][0]["capability_evidence_only"] is True
    assert "POST_CUTOFF_CAPABILITY_ONLY" in parsed["blockers"]


def test_local_public_evidence_receipt_is_atomic_data_only_and_hashes_artifacts(tmp_path: Path) -> None:
    legacy_path = tmp_path / "legacy.json"
    rss_path = tmp_path / "result.rss"
    xml_path = tmp_path / "R_20240208_3.xml"
    legacy_path.write_bytes(json.dumps([_legacy_30y_row()]).encode("utf-8"))
    rss_path.write_bytes(_result_rss())
    xml_path.write_bytes(_result_xml())

    receipt = acquisition.build_local_public_evidence_receipt(
        legacy_json_path=legacy_path,
        result_rss_path=rss_path,
        result_xml_paths=[xml_path],
        output_root=tmp_path / "out",
        receipt_relative_path="receipt.json",
    )

    assert receipt["status"] == "SOURCE_EVIDENCE_PARTIAL"
    assert receipt["admission"] == "DATA_ACQUISITION_REQUIRED"
    assert receipt["economic_execution"] is False
    assert receipt["economic_run"] is False
    assert receipt["outcomes_accessed"] is False
    assert receipt["economic_outcomes_accessed"] is False
    assert receipt["dataset_update_semantic_class"] == "dataset_update"
    assert receipt["credentials_used"] is False
    assert receipt["artifacts"]["legacy_json"]["source_url"] == acquisition.TREASURY_LEGACY_JSON_SEARCH_URL
    assert receipt["artifacts"]["result_rss"]["source_url"] == acquisition.TREASURY_AUCTIONED_RESULT_RSS_URL
    assert receipt["artifacts"]["result_xml"][0]["source_url"].endswith("/xml/R_20240208_3.xml")
    assert receipt["artifact_sha256"] == hashlib.sha256(
        json.dumps({key: value for key, value in receipt.items() if key != "artifact_sha256"}, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    assert json.loads((tmp_path / "out/receipt.json").read_text()) == receipt
    assert receipt["events"]
    assert "PUBLIC_AVAILABILITY_UNPROVEN" in receipt["blockers"]
    reconciliation = receipt["cross_source_reconciliation"]
    assert reconciliation["status"] == "RECONCILED"
    assert reconciliation["legacy_event_count"] == 1
    assert reconciliation["result_xml_in_window_matched"] == 1
    assert reconciliation["result_rss_matched"] == 1
    assert receipt["result_rss"]["items"][0]["cross_source_match"] == "LEGACY_EVENT"


def test_local_public_evidence_receipt_hashes_announced_rss_only_without_validation(tmp_path: Path) -> None:
    legacy_path = tmp_path / "legacy.json"
    announced_path = tmp_path / "announced.rss"
    legacy_bytes = json.dumps([_legacy_30y_row()]).encode()
    announced_bytes = b"not validated RSS evidence"
    legacy_path.write_bytes(legacy_bytes)
    announced_path.write_bytes(announced_bytes)

    receipt = acquisition.build_local_public_evidence_receipt(
        legacy_json_path=legacy_path,
        announced_rss_path=announced_path,
    )

    assert receipt["announced_rss"]["status"] == "HASH_ONLY_UNVALIDATED"
    assert receipt["announced_rss"]["source_url"] == acquisition.TREASURY_ANNOUNCED_RSS_URL
    assert receipt["announced_rss"]["raw_sha256"] == hashlib.sha256(announced_bytes).hexdigest()
    assert "ANNOUNCEMENT_RSS_UNVALIDATED" in receipt["blockers"]


def test_local_public_evidence_receipt_reads_each_input_file_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    legacy_path = tmp_path / "legacy.json"
    rss_path = tmp_path / "result.rss"
    xml_path = tmp_path / "R_20240208_3.xml"
    legacy_path.write_bytes(json.dumps([_legacy_30y_row()]).encode())
    rss_path.write_bytes(_result_rss())
    xml_path.write_bytes(_result_xml())
    original_read_bytes = Path.read_bytes
    reads: dict[Path, int] = {}

    def read_once(path: Path) -> bytes:
        reads[path] = reads.get(path, 0) + 1
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", read_once)
    acquisition.build_local_public_evidence_receipt(
        legacy_json_path=legacy_path,
        result_rss_path=rss_path,
        result_xml_paths=[xml_path],
    )
    assert reads == {legacy_path: 1, rss_path: 1, xml_path: 1}


def test_local_public_evidence_receipt_reconciles_in_window_xml_to_legacy_event(tmp_path: Path) -> None:
    legacy_path = tmp_path / "legacy.json"
    xml_path = tmp_path / "R_20240208_3.xml"
    legacy_path.write_bytes(json.dumps([_legacy_30y_row()]).encode())
    xml_path.write_bytes(_result_xml(cusip="912810UV2"))

    with pytest.raises(ValueError, match="legacy"):
        acquisition.build_local_public_evidence_receipt(
            legacy_json_path=legacy_path,
            result_xml_paths=[xml_path],
        )


def test_local_public_evidence_receipt_reconciles_post_cutoff_xml_only_to_rss(tmp_path: Path) -> None:
    legacy_path = tmp_path / "legacy.json"
    rss_path = tmp_path / "result.rss"
    xml_path = tmp_path / "R_20260910_3.xml"
    post_cutoff_rss = _result_rss().replace(b"08 Feb 2024", b"10 Sep 2026").replace(b"02/08/2024", b"09/10/2026").replace(b"preanre/2024/R_20240208_3", b"preanre/2026/R_20260910_3").replace(b"/xml/R_20240208_3.xml", b"/xml/R_20260910_3.xml")
    legacy_path.write_bytes(json.dumps([_legacy_30y_row()]).encode())
    rss_path.write_bytes(post_cutoff_rss)
    xml_path.write_bytes(_result_xml(auction_date="2026-09-10", pdf_name="R_20260910_3.pdf"))

    receipt = acquisition.build_local_public_evidence_receipt(
        legacy_json_path=legacy_path,
        result_rss_path=rss_path,
        result_xml_paths=[xml_path],
    )
    reconciliation = receipt["cross_source_reconciliation"]
    assert reconciliation["status"] == "RECONCILED_CAPABILITY_ONLY"
    assert reconciliation["result_xml_post_cutoff_matched"] == 1
    assert reconciliation["result_rss_matched"] == 1
    assert receipt["result_xml"][0]["capability_evidence_only"] is True
    assert receipt["result_rss"]["items"][0]["cross_source_match"] == "POST_CUTOFF_CAPABILITY_ONLY"
    with pytest.raises(ValueError, match="RSS"):
        acquisition.build_local_public_evidence_receipt(
            legacy_json_path=legacy_path,
            result_xml_paths=[xml_path],
        )


def test_local_public_evidence_receipt_rejects_duplicate_result_xml_path_hash_and_identity(tmp_path: Path) -> None:
    legacy_path = tmp_path / "legacy.json"
    xml_path = tmp_path / "R_20240208_3.xml"
    legacy_path.write_bytes(json.dumps([_legacy_30y_row()]).encode())
    xml_path.write_bytes(_result_xml())
    with pytest.raises(ValueError, match="duplicate"):
        acquisition.build_local_public_evidence_receipt(legacy_json_path=legacy_path, result_xml_paths=[xml_path, xml_path])

    same_hash_path = tmp_path / "R_20240208_4.xml"
    same_hash_path.write_bytes(xml_path.read_bytes())
    with pytest.raises(ValueError, match="hash"):
        acquisition.build_local_public_evidence_receipt(legacy_json_path=legacy_path, result_xml_paths=[xml_path, same_hash_path])

    same_identity_path = tmp_path / "R_20240208_4.xml"
    same_identity_path.write_bytes(_result_xml(pdf_name="R_20240208_4.pdf"))
    with pytest.raises(ValueError, match="identity"):
        acquisition.build_local_public_evidence_receipt(legacy_json_path=legacy_path, result_xml_paths=[xml_path, same_identity_path])


def test_public_parsers_reject_doctype_and_entity_before_element_tree() -> None:
    xml_attack = b'<!DOCTYPE AuctionData [<!ENTITY xxe "blocked">]><AuctionData />'
    rss_attack = b'<!DOCTYPE rss [<!ENTITY xxe "blocked">]><rss />'
    with pytest.raises(ValueError, match="DOCTYPE|ENTITY"):
        acquisition.parse_result_xml(xml_attack, "https://www.treasurydirect.gov/xml/R_20240208_3.xml")
    with pytest.raises(ValueError, match="DOCTYPE|ENTITY"):
        acquisition.parse_result_rss(rss_attack, acquisition.TREASURY_AUCTIONED_RESULT_RSS_URL)


def test_public_parsers_enforce_bounded_payload_sizes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(acquisition, "MAX_LEGACY_JSON_BYTES", 8)
    with pytest.raises(ValueError, match="size"):
        acquisition.normalize_legacy_json_array(b"[123456789]")
    monkeypatch.setattr(acquisition, "MAX_XML_RSS_BYTES", 8)
    with pytest.raises(ValueError, match="size"):
        acquisition.parse_result_xml(b"<AuctionData />", "https://www.treasurydirect.gov/xml/R_20240208_3.xml")
    with pytest.raises(ValueError, match="size"):
        acquisition.parse_result_rss(b"<rss x />", acquisition.TREASURY_AUCTIONED_RESULT_RSS_URL)


def test_cli_local_public_evidence_mode_never_networks_and_does_not_overwrite(tmp_path: Path) -> None:
    legacy_path = tmp_path / "legacy.json"
    legacy_path.write_bytes(json.dumps([_legacy_30y_row()]).encode("utf-8"))
    output_root = tmp_path / "run"

    args = [
        sys.executable,
        str(SCRIPT),
        "--legacy-json",
        str(legacy_path),
        "--output-root",
        str(output_root),
        "--receipt",
        "evidence.json",
    ]
    first = subprocess.run(args, cwd=REPO, text=True, capture_output=True, check=False)
    assert first.returncode == 0, first.stderr
    payload = json.loads(first.stdout)
    assert payload["status"] == "SOURCE_EVIDENCE_PARTIAL"
    assert payload["network_attempted"] is False
    assert payload["credentials_used"] is False
    assert payload["receipt_path"] == "evidence.json"
    assert (output_root / "evidence.json").exists()
    assert json.loads((output_root / "evidence.json").read_text()) == payload

    second = subprocess.run(args, cwd=REPO, text=True, capture_output=True, check=False)
    assert second.returncode != 0
    assert "FileExistsError" in second.stderr or "already exists" in second.stderr

    bad_xml_path = tmp_path / "arbitrary-result.xml"
    bad_xml_path.write_bytes(_result_xml())
    bad = subprocess.run(args + ["--result-xml", str(bad_xml_path)], cwd=REPO, text=True, capture_output=True, check=False)
    assert bad.returncode != 0
    assert "official basename" in bad.stderr
    help_result = subprocess.run([sys.executable, str(SCRIPT), "--help"], cwd=REPO, text=True, capture_output=True, check=False)
    assert help_result.returncode == 0
    assert "official" in help_result.stdout and "R_YYYYMMDD_*.xml basename" in help_result.stdout


def test_build_tlt_alpaca_query_freezes_split_and_raw_coverage_without_credentials() -> None:
    query = build_tlt_alpaca_query("split")

    assert query == {
        "symbols": "TLT",
        "timeframe": "1Day",
        "start": "2016-01-01",
        "end": "2026-08-01",
        "feed": "sip",
        "adjustment": "split",
        "limit": 10000,
        "sort": "asc",
    }
    assert "key" not in query and "secret" not in query


def test_build_document_record_keeps_pdf_raw_unadmitted_without_real_extractor(tmp_path: Path) -> None:
    content = _source_body()
    record = build_document_record(
        tmp_path,
        "raw/announcement.pdf",
        content,
        source_url="https://treasurydirect.gov/instit/annceresult/press/preanre/2024/A_20240131_2.pdf",
        retrieved_at="2026-09-10T08:00:00Z",
        **_metadata("announcement", "announcement-20240131"),
    )

    assert record["status"] == "RAW_UNADMITTED"
    assert record["admitted"] is False
    assert record["error_code"] == "EXTRACTOR_UNAVAILABLE"
    assert record["raw_path"] == "raw/announcement.pdf"
    assert record["sha256"] == hashlib.sha256(content).hexdigest()
    assert record["published_at"] is None
    assert record["available_at"] is None
    assert record["timestamp_evidence"] is None
    assert record["auction_session_date"] == "2024-02-08"
    assert record["credential_fields_persisted"] is False


def test_build_document_record_does_not_parse_source_body_cutoff_text(tmp_path: Path) -> None:
    record = build_document_record(
        tmp_path,
        "raw/announcement.pdf",
        _source_body(
            published_at="2024-02-08T16:16:00-05:00",
            available_at="2024-02-08T16:16:00-05:00",
        ),
        source_url="https://treasurydirect.gov/instit/annceresult/press/preanre/2024/A_20240131_2.pdf",
        retrieved_at="2026-09-10T08:00:00Z",
        **_metadata("announcement", "announcement-20240131"),
    )

    assert record["status"] == "RAW_UNADMITTED"
    assert record["admitted"] is False
    assert record["available_at"] is None


def test_build_document_record_marks_missing_extractor_evidence_unadmitted(tmp_path: Path) -> None:
    record = build_document_record(
        tmp_path,
        "raw/result.pdf",
        b"%PDF-1.7\nFor Immediate Release February 08, 2024\n",
        source_url="https://treasurydirect.gov/instit/annceresult/press/preanre/2024/R_20240208_3.pdf",
        retrieved_at="2026-09-10T08:00:00Z",
        **_metadata("result", "result-20240208", result_status="COMPLETED"),
    )

    assert record["status"] == "RAW_UNADMITTED"
    assert record["admitted"] is False
    assert record["error_code"] == "EXTRACTOR_UNAVAILABLE"
    assert record["published_at"] is None
    assert record["available_at"] is None


def test_build_document_record_refuses_overwrite(tmp_path: Path) -> None:
    path = tmp_path / "raw/result.pdf"
    path.parent.mkdir()
    path.write_bytes(b"original")

    with pytest.raises(FileExistsError):
        build_document_record(
            tmp_path,
            "raw/result.pdf",
            _source_body(),
            source_url="https://treasurydirect.gov/instit/annceresult/press/preanre/2024/R_20240208_3.pdf",
            retrieved_at="2026-09-10T08:00:00Z",
            **_metadata("result", "result-20240208", result_status="COMPLETED"),
        )

    assert path.read_bytes() == b"original"


def test_build_document_record_rejects_resolved_path_escape(tmp_path: Path) -> None:
    root = tmp_path / "root"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    os.symlink(outside, root / "raw")

    with pytest.raises(ValueError, match="unsafe"):
        build_document_record(
            root,
            "raw/result.pdf",
            _source_body(),
            source_url="https://treasurydirect.gov/instit/annceresult/press/preanre/2024/R_20240208_3.pdf",
            retrieved_at="2026-09-10T08:00:00Z",
            **_metadata("result", "result-20240208", result_status="COMPLETED"),
        )


class _Response:
    def __init__(self, content: bytes, *, url: str, content_type: str = "application/pdf", status_code: int = 200) -> None:
        self.status_code = status_code
        self.content = content
        self.url = url
        self.headers = {"content-type": content_type}


class _Session:
    def __init__(self, response: _Response) -> None:
        self.response = response

    def get(self, _url: str, *, timeout: float) -> _Response:
        assert timeout == 30.0
        return self.response


def test_fetch_rejects_caller_asserted_timestamps_even_when_present_in_metadata(tmp_path: Path) -> None:
    metadata = _metadata("result", "result-20240208", result_status="COMPLETED")
    metadata.update({"published_at": "2024-02-08T13:00:00-05:00", "available_at": "2024-02-08T13:00:00-05:00"})

    result = fetch_official_document(
        root=tmp_path,
        relative_path="raw/result.pdf",
        source_url="https://treasurydirect.gov/instit/annceresult/press/preanre/2024/R_20240208_3.pdf",
        metadata=metadata,
        session=_Session(_Response(_source_body(), url="https://treasurydirect.gov/instit/annceresult/press/preanre/2024/R_20240208_3.pdf")),
    )

    assert result["status"] == "BLOCKED_VALIDATION"
    assert result["error_code"] == "CALLER_TIMESTAMP_FIELDS_FORBIDDEN"
    assert not (tmp_path / "raw/result.pdf").exists()


def test_fetch_rejects_nested_caller_asserted_timestamps_before_network(tmp_path: Path) -> None:
    metadata = _metadata("result", "result-20240208", result_status="COMPLETED")
    metadata["provenance"] = {"published_at": "2024-02-08T13:00:00-05:00"}

    result = fetch_official_document(
        root=tmp_path,
        relative_path="raw/result.pdf",
        source_url="https://treasurydirect.gov/instit/annceresult/press/preanre/2024/R_20240208_3.pdf",
        metadata=metadata,
        session=_Session(_Response(_source_body(), url="https://treasurydirect.gov/instit/annceresult/press/preanre/2024/R_20240208_3.pdf")),
    )

    assert result["status"] == "BLOCKED_VALIDATION"
    assert result["error_code"] == "CALLER_TIMESTAMP_FIELDS_FORBIDDEN"
    assert result["network_attempted"] is False
    assert not (tmp_path / "raw/result.pdf").exists()


def test_fetch_keeps_source_without_timestamp_evidence_raw_unadmitted(tmp_path: Path) -> None:
    metadata = _metadata("result", "result-20240208", result_status="COMPLETED")

    result = fetch_official_document(
        root=tmp_path,
        relative_path="raw/result.pdf",
        source_url="https://treasurydirect.gov/instit/annceresult/press/preanre/2024/R_20240208_3.pdf",
        metadata=metadata,
        session=_Session(
            _Response(
                b"%PDF-1.7\nFor Immediate Release February 08, 2024\n",
                url="https://treasurydirect.gov/instit/annceresult/press/preanre/2024/R_20240208_3.pdf",
            )
        ),
    )

    assert result["status"] == "RAW_UNADMITTED"
    assert result["error_code"] == "EXTRACTOR_UNAVAILABLE"
    assert result["admitted"] is False
    assert result["network_attempted"] is True
    assert (tmp_path / "raw/result.pdf").exists()


def test_fetch_rejects_redirect_to_non_official_host(tmp_path: Path) -> None:
    result = fetch_official_document(
        root=tmp_path,
        relative_path="raw/result.pdf",
        source_url="https://treasurydirect.gov/instit/annceresult/press/preanre/2024/R_20240208_3.pdf",
        metadata=_metadata("result", "result-20240208", result_status="COMPLETED"),
        session=_Session(_Response(_source_body(), url="https://evil.example/result.pdf")),
    )

    assert result["status"] == "BLOCKED_VALIDATION"
    assert result["error_code"] == "FINAL_URL_NOT_OFFICIAL"
    assert not (tmp_path / "raw/result.pdf").exists()


def test_fetch_rejects_mismatched_content_type(tmp_path: Path) -> None:
    result = fetch_official_document(
        root=tmp_path,
        relative_path="raw/result.pdf",
        source_url="https://treasurydirect.gov/instit/annceresult/press/preanre/2024/R_20240208_3.pdf",
        metadata=_metadata("result", "result-20240208", result_status="COMPLETED"),
        session=_Session(_Response(_source_body(), url="https://treasurydirect.gov/instit/annceresult/press/preanre/2024/R_20240208_3.pdf", content_type="text/html")),
    )

    assert result["status"] == "BLOCKED_VALIDATION"
    assert result["error_code"] == "CONTENT_TYPE_INVALID"


def test_fetch_rejects_mismatched_pdf_signature(tmp_path: Path) -> None:
    result = fetch_official_document(
        root=tmp_path,
        relative_path="raw/result.pdf",
        source_url="https://treasurydirect.gov/instit/annceresult/press/preanre/2024/R_20240208_3.pdf",
        metadata=_metadata("result", "result-20240208", result_status="COMPLETED"),
        session=_Session(
            _Response(
                b"not a pdf",
                url="https://treasurydirect.gov/instit/annceresult/press/preanre/2024/R_20240208_3.pdf",
            )
        ),
    )

    assert result["status"] == "BLOCKED_VALIDATION"
    assert result["error_code"] == "CONTENT_SIGNATURE_INVALID"


def test_fetch_keeps_pdf_raw_unadmitted_without_real_extractor(tmp_path: Path) -> None:
    result = fetch_official_document(
        root=tmp_path,
        relative_path="raw/result.pdf",
        source_url="https://treasurydirect.gov/instit/annceresult/press/preanre/2024/R_20240208_3.pdf",
        metadata=_metadata("result", "result-20240208", result_status="COMPLETED"),
        session=_Session(_Response(_source_body(), url="https://www.treasurydirect.gov/instit/annceresult/press/preanre/2024/R_20240208_3.pdf")),
    )

    assert result["status"] == "RAW_UNADMITTED"
    assert result["admitted"] is False
    assert result["error_code"] == "EXTRACTOR_UNAVAILABLE"
    assert result["source_url"].startswith("https://www.treasurydirect.gov/")
    assert result["published_at"] is None
    assert result["available_at"] is None
    assert (tmp_path / "raw/result.pdf").read_bytes() == _source_body()


def test_sanitized_blocked_receipt_does_not_persist_credentials() -> None:
    receipt = sanitized_blocked_receipt(
        source_url="https://data.alpaca.markets/v2/stocks/bars",
        status_code=401,
        error="provider rejected credentials",
        retrieved_at="2026-09-10T08:00:00Z",
    )

    serialized = json.dumps(receipt, sort_keys=True)
    assert receipt["status"] == "BLOCKED_HTTP"
    assert receipt["credential_fields_persisted"] is False
    assert "ALPACA_API_KEY" not in serialized
    assert "ALPACA_SECRET_KEY" not in serialized
    assert "provider rejected credentials" not in serialized


def test_load_pinned_inventory_rejects_caller_asserted_timestamps(tmp_path: Path) -> None:
    inventory = {
        "schema": "r46-treasury-acquisition-inventory-v1",
        "research_id": RESEARCH_ID,
        "candidate_id": "R46",
        "historical_cutoff": "2026-07-31T23:59:59Z",
        "source_url": "https://treasurydirect.gov/auctions/auction-query/",
        "events": [
            {
                "canonical_event_id": "2024-02-08-30Y-ORIGINAL",
                "security_identifier": "912810TX6",
                "original_tenor": 30,
                "issue_or_reopening": "original",
                "auction_session_date": "2024-02-08",
                "issue_date": "2024-02-15",
                "announcement_url": "https://treasurydirect.gov/instit/annceresult/press/preanre/2024/A_20240131_2.pdf",
                "result_url": "https://treasurydirect.gov/instit/annceresult/press/preanre/2024/R_20240208_3.pdf",
                "published_at": "2024-02-08T13:00:00-05:00",
            }
        ],
    }
    path = tmp_path / "inventory.json"
    path.write_text(json.dumps(inventory), encoding="utf-8")

    with pytest.raises(ValueError, match="caller timestamp"):
        acquisition.load_pinned_inventory(path)


def test_load_pinned_inventory_rejects_issue_date_as_auction_date(tmp_path: Path) -> None:
    inventory = {
        "schema": "r46-treasury-acquisition-inventory-v1",
        "research_id": RESEARCH_ID,
        "candidate_id": "R46",
        "historical_cutoff": "2026-07-31T23:59:59Z",
        "source_url": "https://treasurydirect.gov/auctions/auction-query/",
        "events": [
            {
                "canonical_event_id": "2024-02-15-30Y-ORIGINAL",
                "security_identifier": "912810TX6",
                "original_tenor": 30,
                "issue_or_reopening": "original",
                "auction_session_date": "2024-02-15",
                "issue_date": "2024-02-15",
                "announcement_url": "https://treasurydirect.gov/instit/annceresult/press/preanre/2024/A_20240131_2.pdf",
                "result_url": "https://treasurydirect.gov/instit/annceresult/press/preanre/2024/R_20240208_3.pdf",
            }
        ],
    }
    path = tmp_path / "inventory.json"
    path.write_text(json.dumps(inventory), encoding="utf-8")

    with pytest.raises(ValueError, match="auction_session_date must be auction date"):
        acquisition.load_pinned_inventory(path)


def test_load_pinned_inventory_accepts_auction_date_distinct_from_issue_date(tmp_path: Path) -> None:
    inventory = {
        "schema": "r46-treasury-acquisition-inventory-v1",
        "research_id": RESEARCH_ID,
        "candidate_id": "R46",
        "historical_cutoff": "2026-07-31T23:59:59Z",
        "source_url": "https://treasurydirect.gov/auctions/auction-query/",
        "events": [
            {
                "canonical_event_id": "2024-02-08-30Y-ORIGINAL",
                "security_identifier": "912810TX6",
                "original_tenor": 30,
                "issue_or_reopening": "original",
                "auction_session_date": "2024-02-08",
                "issue_date": "2024-02-15",
                "announcement_url": "https://treasurydirect.gov/instit/annceresult/press/preanre/2024/A_20240131_2.pdf",
                "result_url": "https://treasurydirect.gov/instit/annceresult/press/preanre/2024/R_20240208_3.pdf",
            }
        ],
    }
    path = tmp_path / "inventory.json"
    path.write_text(json.dumps(inventory), encoding="utf-8")

    loaded = acquisition.load_pinned_inventory(path)

    assert loaded["events"][0]["auction_session_date"] == "2024-02-08"
    assert loaded["inventory_sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()


def test_load_pinned_inventory_rejects_event_after_historical_cutoff(tmp_path: Path) -> None:
    inventory = {
        "schema": "r46-treasury-acquisition-inventory-v1",
        "research_id": RESEARCH_ID,
        "candidate_id": "R46",
        "historical_cutoff": "2026-07-31T23:59:59Z",
        "source_url": "https://treasurydirect.gov/auctions/auction-query/",
        "events": [
            {
                "canonical_event_id": "2026-08-01-30Y-ORIGINAL",
                "security_identifier": "912810TX6",
                "original_tenor": 30,
                "issue_or_reopening": "original",
                "auction_session_date": "2026-08-01",
                "issue_date": "2026-08-08",
                "announcement_url": "https://treasurydirect.gov/instit/annceresult/press/preanre/2026/A_20260801_1.pdf",
                "result_url": "https://treasurydirect.gov/instit/annceresult/press/preanre/2026/R_20260801_1.pdf",
            }
        ],
    }
    path = tmp_path / "inventory.json"
    path.write_text(json.dumps(inventory), encoding="utf-8")

    with pytest.raises(ValueError, match="post_cutoff"):
        acquisition.load_pinned_inventory(path)


def test_build_source_capability_receipt_records_public_timestamp_blocker() -> None:
    receipt = acquisition.build_source_capability_receipt(
        checked_at="2026-09-10T08:00:00Z",
        result_publication_timestamp_found=False,
        complete_inventory_pinned=False,
        tlt_source_status="BLOCKED_NO_AUTHORIZED_CREDENTIALS",
    )

    assert receipt["schema"] == "r46-treasury-source-capability-v1"
    assert receipt["status"] == "CAPABILITY_OR_PAID_BLOCKED"
    assert receipt["r46_admission_status"] == "DATA_ACQUISITION_REQUIRED"
    assert "RESULT_PUBLICATION_TIMESTAMP_UNAVAILABLE" in receipt["blockers"]
    assert "COMPLETE_EVENT_INVENTORY_NOT_PINNED" in receipt["blockers"]
    assert receipt["credentials_used"] is False
    assert receipt["paid_source_used"] is False
    assert receipt["route_capability_status"] == "PUBLIC_ROUTES_IDENTIFIED"
    assert receipt["source_evidence"][0]["checked_at"] == "2026-09-10T08:00:00Z"
    assert receipt["source_evidence"][0]["url"] == acquisition.TREASURY_ARCHIVE_URL
    artifact_hash = receipt["artifact_sha256"]
    unsigned = dict(receipt)
    unsigned.pop("artifact_sha256")
    expected_hash = hashlib.sha256(
        json.dumps(unsigned, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    assert artifact_hash == expected_hash


def test_static_capability_receipt_is_canonical_and_self_hashed() -> None:
    receipt = json.loads(CAPABILITY_RECEIPT.read_text(encoding="utf-8"))
    unsigned = dict(receipt)
    actual_hash = unsigned.pop("artifact_sha256")
    expected_hash = hashlib.sha256(
        json.dumps(unsigned, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()

    assert actual_hash == expected_hash
    assert receipt["route_capability_status"] == "PUBLIC_ROUTES_IDENTIFIED"
    assert receipt["r46_admission_status"] == "DATA_ACQUISITION_REQUIRED"
    assert receipt["status"] == "CAPABILITY_OR_PAID_BLOCKED"


def test_cli_help_works_when_invoked_directly() -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--help"],
        cwd=REPO,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "--inventory" in result.stdout
    assert "--fetch" in result.stdout


def test_cli_does_not_network_without_explicit_fetch(tmp_path: Path) -> None:
    inventory = {
        "schema": "r46-treasury-acquisition-inventory-v1",
        "research_id": RESEARCH_ID,
        "candidate_id": "R46",
        "historical_cutoff": "2026-07-31T23:59:59Z",
        "source_url": "https://treasurydirect.gov/auctions/auction-query/",
        "events": [
            {
                "canonical_event_id": "2024-02-08-30Y-ORIGINAL",
                "security_identifier": "912810TX6",
                "original_tenor": 30,
                "issue_or_reopening": "original",
                "auction_session_date": "2024-02-08",
                "issue_date": "2024-02-15",
                "announcement_url": "https://treasurydirect.gov/instit/annceresult/press/preanre/2024/A_20240131_2.pdf",
                "result_url": "https://treasurydirect.gov/instit/annceresult/press/preanre/2024/R_20240208_3.pdf",
            }
        ],
    }
    inventory_path = tmp_path / "inventory.json"
    inventory_path.write_text(json.dumps(inventory), encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--inventory", str(inventory_path), "--output-root", str(tmp_path / "run")],
        cwd=REPO,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["status"] == "CAPABILITY_OR_PAID_BLOCKED"
    assert payload["inventory_pin_status"] == "UNPINNED_OFFICIAL_EXPORT"
    assert "COMPLETE_EVENT_INVENTORY_NOT_HASH_PINNED" in payload["blockers"]
    assert payload["network_attempted"] is False


class _InventorySession:
    def get(self, url: str, *, timeout: float) -> _Response:
        assert timeout == 30.0
        identifier = "A_20240131_2" if "/A_" in url else "R_20240208_3"
        return _Response(
            _source_body(),
            url=f"https://treasurydirect.gov/instit/annceresult/press/preanre/2024/{identifier}.pdf",
        )


def test_acquire_pinned_inventory_never_reports_acquired_from_fetcher_admission(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    export_path = tmp_path / "official-export.csv"
    coverage_path = tmp_path / "annual-coverage-2024.html"
    export_path.write_bytes(b"fixture export bound to an official URL\n")
    coverage_path.write_bytes(b"fixture annual coverage bound to an official URL\n")
    inventory = {
        "schema": "r46-treasury-acquisition-inventory-v1",
        "research_id": RESEARCH_ID,
        "candidate_id": "R46",
        "historical_cutoff": "2026-07-31T23:59:59Z",
        "source_url": "https://treasurydirect.gov/auctions/auction-query/",
        "official_inventory_pin": {
            "export_url": "https://fiscaldata.treasury.gov/static-data/published-reports/auctions-query/",
            "export_path": export_path.name,
            "export_sha256": hashlib.sha256(export_path.read_bytes()).hexdigest(),
            "annual_coverage": [
                {
                    "year": 2024,
                    "url": "https://treasurydirect.gov/auctions/announcements-data-results/announcement-results-press-releases/",
                    "path": coverage_path.name,
                    "sha256": hashlib.sha256(coverage_path.read_bytes()).hexdigest(),
                }
            ],
        },
        "events": [
            {
                "canonical_event_id": "2024-02-08-30Y-ORIGINAL",
                "security_identifier": "912810TX6",
                "original_tenor": 30,
                "issue_or_reopening": "original",
                "auction_session_date": "2024-02-08",
                "issue_date": "2024-02-15",
                "announcement_url": "https://treasurydirect.gov/instit/annceresult/press/preanre/2024/A_20240131_2.pdf",
                "result_url": "https://treasurydirect.gov/instit/annceresult/press/preanre/2024/R_20240208_3.pdf",
            }
        ],
    }
    inventory_path = tmp_path / "inventory.json"
    inventory_path.write_text(json.dumps(inventory), encoding="utf-8")

    def _admitted_fetcher(**_: Any) -> dict[str, Any]:
        return {"document_id": "synthetic", "status": "ADMITTED", "admitted": True}

    monkeypatch.setattr(acquisition, "fetch_official_document", _admitted_fetcher)
    receipt = acquisition.acquire_pinned_inventory(
        inventory_path,
        output_root=tmp_path / "run",
        fetch=True,
    )

    assert receipt["status"] == "CAPABILITY_OR_PAID_BLOCKED"
    assert receipt["admitted_document_count"] == 0
    assert receipt["admitted_event_count"] == 0
    assert receipt["events"] == []


def test_acquire_pinned_inventory_fetches_only_with_opt_in(tmp_path: Path) -> None:
    export_path = tmp_path / "official-export.csv"
    coverage_path = tmp_path / "annual-coverage-2024.html"
    export_path.write_bytes(b"fixture export bound to an official URL\n")
    coverage_path.write_bytes(b"fixture annual coverage bound to an official URL\n")
    inventory = {
        "schema": "r46-treasury-acquisition-inventory-v1",
        "research_id": RESEARCH_ID,
        "candidate_id": "R46",
        "historical_cutoff": "2026-07-31T23:59:59Z",
        "source_url": "https://treasurydirect.gov/auctions/auction-query/",
        "official_inventory_pin": {
            "export_url": "https://fiscaldata.treasury.gov/static-data/published-reports/auctions-query/",
            "export_path": export_path.name,
            "export_sha256": "a" * 64,
            "annual_coverage": [
                {
                    "year": 2024,
                    "url": "https://treasurydirect.gov/auctions/announcements-data-results/announcement-results-press-releases/",
                    "path": coverage_path.name,
                    "sha256": "b" * 64,
                }
            ],
        },
        "events": [
            {
                "canonical_event_id": "2024-02-08-30Y-ORIGINAL",
                "security_identifier": "912810TX6",
                "original_tenor": 30,
                "issue_or_reopening": "original",
                "auction_session_date": "2024-02-08",
                "issue_date": "2024-02-15",
                "announcement_url": "https://treasurydirect.gov/instit/annceresult/press/preanre/2024/A_20240131_2.pdf",
                "result_url": "https://treasurydirect.gov/instit/annceresult/press/preanre/2024/R_20240208_3.pdf",
            }
        ],
    }
    inventory["official_inventory_pin"]["export_sha256"] = hashlib.sha256(export_path.read_bytes()).hexdigest()
    inventory["official_inventory_pin"]["annual_coverage"][0]["sha256"] = hashlib.sha256(coverage_path.read_bytes()).hexdigest()
    inventory_path = tmp_path / "inventory.json"
    inventory_path.write_text(json.dumps(inventory), encoding="utf-8")

    receipt = acquisition.acquire_pinned_inventory(
        inventory_path,
        output_root=tmp_path / "run",
        fetch=True,
        session=_InventorySession(),
    )

    assert receipt["status"] == "CAPABILITY_OR_PAID_BLOCKED"
    assert receipt["network_attempted"] is True
    assert receipt["admitted_document_count"] == 0
    assert receipt["admitted_event_count"] == 0
    assert all(document["status"] == "RAW_UNADMITTED" for document in receipt["documents"])
    assert all(document["error_code"] == "EXTRACTOR_UNAVAILABLE" for document in receipt["documents"])
