from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from scripts.research.acquire_r46_treasury_xml_v2_2026_09_10 import (
    TreasuryXMLContractError,
    acquire_r46_treasury_xml_v2,
    normalize_treasury_xml_v2,
)

EVENT_ID = "2024-02-08-30Y-ORIGINAL"
RESULT_URL = "https://www.treasurydirect.gov/xml/R_20240208_1.xml"
ANNOUNCEMENT_URL = "https://www.treasurydirect.gov/xml/A_20240207_1.xml"
XSD_URL = "https://www.treasurydirect.gov/xsd/Auction_v6_0_0.xsd"

RESULT_XML = b'''<?xml version="1.0" encoding="UTF-8"?>
<AuctionData xmlns="http://www.treasurydirect.gov/"
 xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
 xsi:schemaLocation="http://www.treasurydirect.gov/ https://www.treasurydirect.gov/xsd/Auction_v6_0_0.xsd">
  <AuctionAnnouncement>
    <CUSIP>912810TM2</CUSIP><AuctionDate>2024-02-08</AuctionDate>
    <IssueDate>2024-02-15</IssueDate><SecurityTerm>30-Year</SecurityTerm>
    <BidToCover>2.31</BidToCover>
  </AuctionAnnouncement>
  <AuctionResults>
    <ReleaseTime>14:00</ReleaseTime><ResultsPDFName>R_20240208_1.pdf</ResultsPDFName>
    <HighYield>4.30</HighYield><Tail>1.2</Tail>
  </AuctionResults>
</AuctionData>
'''
ANNOUNCEMENT_XML = b'''<?xml version="1.0" encoding="UTF-8"?>
<AuctionData xmlns="http://www.treasurydirect.gov/"
 xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
 xsi:schemaLocation="http://www.treasurydirect.gov/ https://www.treasurydirect.gov/xsd/Auction_v6_0_0.xsd">
  <AuctionAnnouncement>
    <CUSIP>912810TM2</CUSIP><AuctionDate>2024-02-08</AuctionDate>
    <IssueDate>2024-02-15</IssueDate><SecurityTerm>30-Year</SecurityTerm>
    <OfferAmount>100000000</OfferAmount>
  </AuctionAnnouncement>
</AuctionData>
'''
XSD = b'''<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema"
 targetNamespace="http://www.treasurydirect.gov/"
 xmlns="http://www.treasurydirect.gov/"
 elementFormDefault="qualified" version="6.0.0">
  <xs:element name="AuctionData">
    <xs:complexType>
      <xs:sequence>
        <xs:any minOccurs="0" maxOccurs="unbounded" processContents="lax"/>
      </xs:sequence>
    </xs:complexType>
  </xs:element>
</xs:schema>'''

LEGACY_HTTP_RESULT_XML = RESULT_XML.replace(
    b"https://www.treasurydirect.gov/xsd/Auction_v6_0_0.xsd",
    b"http://www.treasurydirect.gov/xsd/Auction_v6_0_0.xsd",
)
LEGACY_HTTP_ANNOUNCEMENT_XML = ANNOUNCEMENT_XML.replace(
    b"https://www.treasurydirect.gov/xsd/Auction_v6_0_0.xsd",
    b"http://www.treasurydirect.gov/xsd/Auction_v6_0_0.xsd",
)
MISSING_RELEASE_RESULT_XML = RESULT_XML.replace(
    b"<ReleaseTime>14:00</ReleaseTime>",
    b"<ReleaseTime></ReleaseTime>",
)


def _canonical_hash(payload: dict[str, object], field: str) -> str:
    value = json.loads(json.dumps(payload, ensure_ascii=False))
    value["integrity"].pop(field, None)  # type: ignore[index]
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _inventory(root: Path) -> Path:
    payload: dict[str, object] = {
        "schema": "r46-treasury-acquisition-inventory-v1",
        "research_id": "r46_treasury_auction_unwind_tlt_v2_2026_09_10",
        "candidate_id": "R46",
        "events": [
            {
                "canonical_event_id": EVENT_ID,
                "cusip": "912810TM2",
                "auction_date": "2024-02-08",
                "issue_date": "2024-02-15",
                "issue_or_reopening": "original",
                "announcement": {"xml_url": ANNOUNCEMENT_URL, "revision_id": "announcement-r1", "supersedes_document_id": None},
                "result": {"xml_url": RESULT_URL, "revision_id": "result-r1", "supersedes_document_id": None},
            }
        ],
        "integrity": {"canonicalization": "omit integrity.inventory_sha256", "inventory_sha256": ""},
    }
    payload["integrity"]["inventory_sha256"] = _canonical_hash(payload, "inventory_sha256")  # type: ignore[index]
    path = root / "inventory.json"
    path.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")
    return path


def test_treasury_xml_v2_pins_raw_xml_and_filters_economic_fields(tmp_path: Path) -> None:
    inventory = _inventory(tmp_path)
    calls: list[str] = []

    def transport(url: str, *, params: dict[str, object], headers: dict[str, str], timeout: float) -> tuple[int, object]:
        calls.append(url)
        if url == RESULT_URL:
            return 307, {"location": RESULT_URL}
        if url == ANNOUNCEMENT_URL:
            return 200, ANNOUNCEMENT_XML
        if url == XSD_URL:
            return 200, XSD
        if url == RESULT_URL:
            return 200, RESULT_XML
        raise AssertionError(url)

    # A redirect response is followed by the same-path response in order to
    # prove the redirect guard without using a real endpoint.
    result_calls = iter([(307, {"location": RESULT_URL}), (200, RESULT_XML)])

    def redirect_transport(url: str, *, params: dict[str, object], headers: dict[str, str], timeout: float) -> tuple[int, object]:
        calls.append(url)
        if url == RESULT_URL:
            return next(result_calls)
        if url == ANNOUNCEMENT_URL:
            return 200, ANNOUNCEMENT_XML
        if url == XSD_URL:
            return 200, XSD
        raise AssertionError(url)

    receipt = acquire_r46_treasury_xml_v2(
        inventory,
        output_dir=tmp_path / "out",
        transport=redirect_transport,
        acquisition_time="2026-09-10T08:00:00Z",
    )

    assert receipt["status"] == "COMPLETE"
    assert receipt["expected_event_count"] == 1
    assert receipt["expected_event_ids"] == [EVENT_ID]
    assert receipt["inventory"]["sha256"] == hashlib.sha256(inventory.read_bytes()).hexdigest()  # type: ignore[index]
    assert receipt["integrity"]["receipt_sha256"] == _canonical_hash(receipt, "receipt_sha256")  # type: ignore[index]
    assert receipt["acquisition_audit"]["inventory_event_ids"]["ids"] == [EVENT_ID]
    assert receipt["acquisition_audit"]["announcement_event_ids"]["ids"] == [EVENT_ID]
    assert receipt["acquisition_audit"]["result_event_ids"]["ids"] == [EVENT_ID]
    assert receipt["acquisition_audit"]["missing_release_time_event_ids"]["ids"] == []
    assert receipt["acquisition_audit"]["missing_release_time_event_ids"]["count"] == 0
    assert receipt["xsd"][0]["schema_version"] == "v6_0_0"  # type: ignore[index]
    assert receipt["xsd"][0]["validation_status"] == "SCHEMA_VALIDATED"  # type: ignore[index]
    assert receipt["xsd"][0]["validation_tool"] == "/usr/bin/xmllint"  # type: ignore[index]
    docs = [json.loads(line) for line in (tmp_path / "out" / "documents.jsonl").read_text().splitlines()]
    assert len(docs) == 2
    announcement = next(row for row in docs if row["document_kind"] == "announcement")
    result = next(row for row in docs if row["document_kind"] == "result")
    assert announcement["published_at"].startswith("2024-02-07T23:59:59")
    assert announcement["published_at"] < "2024-02-08T00:00:00"
    assert result["published_at"].startswith("2024-02-08T14:00:00")
    assert result["source_url"] == RESULT_URL
    assert result["revision_id"] == "result-r1"
    assert result["supersedes_document_id"] is None
    assert result["raw_sha256"] == hashlib.sha256(RESULT_XML).hexdigest()
    assert (tmp_path / "out" / result["raw_path"]).is_file()
    forbidden = {"bid_to_cover", "high_yield", "yield", "tail", "price", "offer_amount"}
    assert not forbidden.intersection({key.casefold() for row in docs for key in row})


def test_treasury_xml_v2_rejects_cross_path_redirect_and_overwrite(tmp_path: Path) -> None:
    inventory = _inventory(tmp_path)

    def bad_transport(url: str, *, params: dict[str, object], headers: dict[str, str], timeout: float) -> tuple[int, object]:
        if url == RESULT_URL:
            return 302, {"location": ANNOUNCEMENT_URL}
        return 200, XSD if url == XSD_URL else ANNOUNCEMENT_XML

    with pytest.raises(TreasuryXMLContractError, match="redirect path"):
        acquire_r46_treasury_xml_v2(
            inventory,
            output_dir=tmp_path / "bad",
            transport=bad_transport,
            acquisition_time="2026-09-10T08:00:00Z",
        )

    def good_transport(url: str, *, params: dict[str, object], headers: dict[str, str], timeout: float) -> tuple[int, object]:
        if url == RESULT_URL:
            return 200, RESULT_XML
        if url == ANNOUNCEMENT_URL:
            return 200, ANNOUNCEMENT_XML
        return 200, XSD

    output = tmp_path / "good"
    acquire_r46_treasury_xml_v2(inventory, output_dir=output, transport=good_transport, acquisition_time="2026-09-10T08:00:00Z")
    with pytest.raises((FileExistsError, TreasuryXMLContractError)):
        acquire_r46_treasury_xml_v2(inventory, output_dir=output, transport=good_transport, acquisition_time="2026-09-10T08:00:00Z")


def test_treasury_xml_v2_canonicalizes_legacy_http_schema_location_to_official_https(tmp_path: Path) -> None:
    inventory = _inventory(tmp_path)

    def transport(url: str, *, params: dict[str, object], headers: dict[str, str], timeout: float) -> tuple[int, object]:
        if url == RESULT_URL:
            return 200, LEGACY_HTTP_RESULT_XML
        if url == ANNOUNCEMENT_URL:
            return 200, LEGACY_HTTP_ANNOUNCEMENT_XML
        if url == XSD_URL:
            return 200, XSD
        raise AssertionError(url)

    receipt = acquire_r46_treasury_xml_v2(
        inventory,
        output_dir=tmp_path / "legacy-http",
        transport=transport,
        acquisition_time="2026-09-10T08:00:00Z",
    )
    assert receipt["status"] == "COMPLETE"
    assert receipt["xsd"][0]["source_url"] == XSD_URL  # type: ignore[index]


def test_treasury_xml_v2_rejects_nested_output_symlink(tmp_path: Path) -> None:
    inventory = _inventory(tmp_path)
    output = tmp_path / "out"
    output.mkdir()
    sink = tmp_path / "sink"
    sink.mkdir()
    (output / "raw_xml").symlink_to(sink, target_is_directory=True)

    def transport(url: str, *, params: dict[str, object], headers: dict[str, str], timeout: float) -> tuple[int, object]:
        if url == RESULT_URL:
            return 200, RESULT_XML
        if url == ANNOUNCEMENT_URL:
            return 200, ANNOUNCEMENT_XML
        return 200, XSD

    with pytest.raises(TreasuryXMLContractError, match="symlink"):
        acquire_r46_treasury_xml_v2(
            inventory,
            output_dir=output,
            transport=transport,
            acquisition_time="2026-09-10T08:00:00Z",
        )


def test_treasury_xml_v2_seals_missing_release_time_as_next_session_policy(tmp_path: Path) -> None:
    inventory = _inventory(tmp_path)

    def transport(url: str, *, params: dict[str, object], headers: dict[str, str], timeout: float) -> tuple[int, object, dict[str, str]]:
        response_headers = {
            "Date": "Thu, 10 Sep 2026 22:00:00 GMT",
            "Last-Modified": "Fri, 22 May 2026 15:11:25 GMT",
            "ETag": '"fixture-result"',
        }
        if url == RESULT_URL:
            return 200, MISSING_RELEASE_RESULT_XML, response_headers
        if url == ANNOUNCEMENT_URL:
            return 200, ANNOUNCEMENT_XML, response_headers
        if url == XSD_URL:
            return 200, XSD, response_headers
        raise AssertionError(url)

    receipt = acquire_r46_treasury_xml_v2(
        inventory,
        output_dir=tmp_path / "missing-release",
        transport=transport,
        acquisition_time="2026-09-10T08:00:00Z",
    )

    assert receipt["status"] == "COMPLETE"
    assert receipt["blockers"] == []
    assert receipt["publication_basis"]["result"] == "official_XML_AuctionDate_plus_one_civil_day_midnight_America_New_York_for_all_results"
    assert receipt["publication_evidence"]["availability_derivation"] == "official_XML_AuctionDate_plus_one_civil_day_midnight_America_New_York_for_all_results"
    docs = [json.loads(line) for line in (tmp_path / "missing-release" / "documents.jsonl").read_text().splitlines()]
    result = next(row for row in docs if row["document_kind"] == "result")
    assert result["published_at"] is None
    assert result["available_at"] == "2024-02-09T00:00:00-05:00"
    assert result["publication_at_source"] == "official_XML_release_time_missing"
    assert result["official_publication_date"] == "2024-02-08"
    assert result["availability_derivation"] == "official_XML_AuctionDate_plus_one_civil_day_midnight_America_New_York_for_all_results"
    assert result["result_signal_eligibility"] == "FIRST_REGULAR_OPEN_STRICTLY_AFTER_AVAILABLE_AT"
    assert result["same_day_signal_allowed"] is False
    assert receipt["publication_evidence"]["same_day"] is False
    assert receipt["publication_evidence"]["next_session_open_policy"] == "FIRST_REGULAR_OPEN_STRICTLY_AFTER_AVAILABLE_AT"
    assert receipt["publication_evidence"]["official_publication_date_basis"] == "result_XML_AuctionDate"
    assert receipt["publication_evidence"]["http_date_is_publication_proof"] is False
    assert receipt["publication_evidence"]["retrieval_time_is_publication_proof"] is False
    metadata = [json.loads(line) for line in (tmp_path / "missing-release" / "http_metadata.jsonl").read_text().splitlines()]
    result_metadata = next(row for row in metadata if row["source_url"] == RESULT_URL)
    assert result_metadata["body_sha256"] == hashlib.sha256(MISSING_RELEASE_RESULT_XML).hexdigest()
    assert result_metadata["last_modified_cutoff_bounded"] is True
    assert result_metadata["last_modified_is_publication_proof"] is False


@pytest.mark.parametrize(
    ("auction_date", "expected_available_at", "expected_published_at"),
    (
        ("2024-03-09", "2024-03-10T00:00:00-05:00", "2024-03-09T14:00:00-05:00"),
        ("2024-11-02", "2024-11-03T00:00:00-04:00", "2024-11-02T14:00:00-04:00"),
        ("2024-11-03", "2024-11-04T00:00:00-05:00", "2024-11-03T14:00:00-05:00"),
    ),
)
def test_treasury_xml_v2_result_availability_uses_civil_day_dst_safe_policy(
    auction_date: str,
    expected_available_at: str,
    expected_published_at: str,
) -> None:
    xml = RESULT_XML.replace(b"2024-02-08", auction_date.encode()).replace(b"R_20240208_1", f"R_{auction_date.replace('-', '')}_1".encode())
    event = {
        "canonical_event_id": f"{auction_date}-30Y-ORIGINAL",
        "cusip": "912810TM2",
        "auction_date": auction_date,
        "issue_or_reopening": "original",
        "result": {"xml_url": f"https://www.treasurydirect.gov/xml/R_{auction_date.replace('-', '')}_1.xml", "revision_id": "result-r1", "supersedes_document_id": None},
    }

    result = normalize_treasury_xml_v2(
        xml,
        event["result"]["xml_url"],  # type: ignore[index]
        kind="result",
        event=event,
        acquisition_time="2026-09-10T08:00:00Z",
        reference=event["result"],  # type: ignore[arg-type]
    )

    assert result["available_at"] == expected_available_at
    assert result["availability_derivation"] == "official_XML_AuctionDate_plus_one_civil_day_midnight_America_New_York_for_all_results"
    assert result["same_day_signal_allowed"] is False
    assert result["result_signal_eligibility"] == "FIRST_REGULAR_OPEN_STRICTLY_AFTER_AVAILABLE_AT"
    assert result["published_at"] == expected_published_at
