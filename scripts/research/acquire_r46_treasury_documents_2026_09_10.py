"""Acquire R46 Treasury documents with explicit source provenance.

The network helper is opt-in. Inventory metadata may identify documents, but
this package has no PDF extractor and therefore never admits a document from
its bytes. A valid official response can only be retained as ``RAW_UNADMITTED``
until publication and availability evidence is independently extracted and
verified. Failed transport/identity validation never creates a raw artifact
and every blocked receipt is sanitized.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import sys
import tempfile
from datetime import UTC, date, datetime, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse
from xml.etree import ElementTree

import requests

REPO = Path(__file__).resolve().parents[2]
if __package__ in {None, ""} and str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scripts.research.r46_treasury_auction_readiness_2026_09_10 import (  # noqa: E402
    CUTOFF_UTC,
    OFFICIAL_DOCUMENT_HOSTS,
    _official_url,
    _parse_aware,
    sha256,
)

TREASURY_ARCHIVE_URL = "https://treasurydirect.gov/auctions/announcements-data-results/announcement-results-press-releases/"
TREASURY_AUCTION_QUERY_URL = "https://treasurydirect.gov/auctions/auction-query/"
TREASURY_FISCAL_DOCUMENTS_URL = "https://fiscaldata.treasury.gov/static-data/published-reports/auctions-query/"
TREASURY_LEGACY_JSON_SEARCH_URL = (
    "https://www.treasurydirect.gov/TA_WS/securities/search?"
    "startdate=2016-01-01&enddate=2026-07-31&format=json"
)
TREASURY_ANNOUNCED_RSS_URL = "https://www.treasurydirect.gov/TA_WS/securities/announced/rss"
TREASURY_AUCTIONED_RESULT_RSS_URL = "https://www.treasurydirect.gov/TA_WS/securities/auctioned/rss"
TREASURY_XML_BASE_URL = "https://www.treasurydirect.gov/xml/"
TREASURY_XML_XSD_URL = "https://www.treasurydirect.gov/xsd/Auction_v6_0_0.xsd"
TREASURY_PDF_BASE_URL = "https://www.treasurydirect.gov/instit/annceresult/press/preanre/"
TREASURY_XML_SCHEMA_NAMESPACE = "http://www.w3.org/2001/XMLSchema-instance"
ALPACA_BARS_URL = "https://data.alpaca.markets/v2/stocks/bars"
START = "2016-01-01"
END_EXCLUSIVE = "2026-08-01"
MAX_LEGACY_JSON_BYTES = 64 * 1024 * 1024
MAX_XML_RSS_BYTES = 16 * 1024 * 1024
INVENTORY_SCHEMA = "r46-treasury-acquisition-inventory-v1"
RECEIPT_SCHEMA = "r46-treasury-acquisition-receipt-v2"
CAPABILITY_SCHEMA = "r46-treasury-source-capability-v1"
CUSIP_RE = re.compile(r"^[A-Z0-9]{9}$")
PDF_SIGNATURE = b"%PDF-"
PDF_CONTENT_TYPE = "application/pdf"
RAW_UNADMITTED_STATUS = "RAW_UNADMITTED"
RAW_UNADMITTED_ERROR = "EXTRACTOR_UNAVAILABLE"
INVENTORY_PINNED_STATUS = "HASH_PINNED_OFFICIAL_EXPORT_AND_ANNUAL_COVERAGE"
INVENTORY_UNPINNED_STATUS = "UNPINNED_OFFICIAL_EXPORT"
INVENTORY_INVALID_PIN_STATUS = "INVALID_OFFICIAL_EXPORT_PIN"
CALLER_TIMESTAMP_FIELDS = {
    "published_at",
    "available_at",
    "publication_at",
    "publication_timestamp",
    "retrieved_at",
    "source_available_at",
}


def official_source_routes() -> dict[str, str]:
    return {
        "archive": TREASURY_ARCHIVE_URL,
        "auction_query": TREASURY_AUCTION_QUERY_URL,
        "fiscal_documents": TREASURY_FISCAL_DOCUMENTS_URL,
        "legacy_json_search": TREASURY_LEGACY_JSON_SEARCH_URL,
        "announced_rss": TREASURY_ANNOUNCED_RSS_URL,
        "auctioned_result_rss": TREASURY_AUCTIONED_RESULT_RSS_URL,
        "xml_base": TREASURY_XML_BASE_URL,
        "xml_xsd": TREASURY_XML_XSD_URL,
    }


def build_tlt_alpaca_query(adjustment: str) -> dict[str, Any]:
    if adjustment not in {"raw", "split"}:
        raise ValueError("TLT adjustment must be raw or split")
    return {
        "symbols": "TLT",
        "timeframe": "1Day",
        "start": START,
        "end": END_EXCLUSIVE,
        "feed": "sip",
        "adjustment": adjustment,
        "limit": 10000,
        "sort": "asc",
    }


def _safe_relative_path(root: Path, relative_path: str) -> Path:
    if not isinstance(relative_path, str) or not relative_path.strip():
        raise ValueError("output path unsafe")
    relative = Path(relative_path)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("output path unsafe")
    root_resolved = root.resolve()
    destination = (root_resolved / relative).resolve(strict=False)
    if destination == root_resolved or root_resolved not in destination.parents:
        raise ValueError("output path unsafe")
    return destination


def _atomic_write_no_overwrite(root: Path, relative_path: str, content: bytes) -> Path:
    destination = _safe_relative_path(root, relative_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary_name: str | None = None
    try:
        file_descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{destination.name}.", suffix=".tmp", dir=str(destination.parent)
        )
        with os.fdopen(file_descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary_name, destination)
        os.unlink(temporary_name)
        temporary_name = None
        directory_descriptor = os.open(destination.parent, os.O_RDONLY)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    finally:
        if temporary_name is not None:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass
    return destination


def _canonical_artifact_hash(payload: dict[str, Any], field: str) -> str:
    unsigned = dict(payload)
    unsigned.pop(field, None)
    canonical = json.dumps(unsigned, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _atomic_json(root: Path, relative_path: str, payload: dict[str, Any]) -> Path:
    content = (json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
    return _atomic_write_no_overwrite(root, relative_path, content)


def _guard_public_bytes(content: bytes, *, kind: str, limit: int) -> None:
    if not isinstance(content, bytes) or not content:
        raise ValueError(f"{kind} body empty")
    if len(content) > limit:
        raise ValueError(f"{kind} body size exceeds limit")
    upper = content.upper()
    if b"<!DOCTYPE" in upper or b"<!ENTITY" in upper:
        raise ValueError(f"{kind} DOCTYPE/ENTITY forbidden")


def _json_array_payload(payload: bytes | bytearray | str | list[dict[str, Any]]) -> list[Any]:
    if isinstance(payload, (bytes, bytearray)):
        _guard_public_bytes(bytes(payload), kind="legacy JSON", limit=MAX_LEGACY_JSON_BYTES)
        try:
            value = json.loads(bytes(payload).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("legacy JSON malformed") from exc
    elif isinstance(payload, str):
        _guard_public_bytes(payload.encode("utf-8"), kind="legacy JSON", limit=MAX_LEGACY_JSON_BYTES)
        try:
            value = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise ValueError("legacy JSON malformed") from exc
    else:
        value = payload
    if not isinstance(value, list) or not value:
        raise ValueError("legacy JSON must be a non-empty ARRAY")
    return value


def _required_text(row: dict[str, Any], field: str) -> str:
    value = row.get(field)
    if not isinstance(value, str) or not value.strip():
        display_field = "CUSIP" if field == "cusip" else field
        raise ValueError(f"legacy identity missing:{display_field}")
    return value.strip()


def _legacy_date(value: Any, field: str) -> str:
    raw = _required_text({field: value}, field)
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"legacy {field} invalid date") from exc
    if parsed.hour or parsed.minute or parsed.second or parsed.microsecond:
        raise ValueError(f"legacy {field} must be ISO midnight")
    return parsed.date().isoformat()


def _optional_legacy_date(value: Any, field: str) -> str | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    return _legacy_date(value, field)


def _document_filename(value: Any, *, kind: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"legacy identity missing:{kind} filename")
    filename = value.strip()
    if Path(filename).name != filename or filename.startswith("."):
        raise ValueError(f"legacy {kind} filename invalid")
    if kind == "announcement":
        if not filename.startswith("A_") or (
            not filename.lower().endswith(".pdf") and not filename.lower().endswith(".xml")
        ):
            raise ValueError("legacy announcement filename invalid")
    elif kind == "result":
        if not filename.startswith("R_") or (
            not filename.lower().endswith(".pdf") and not filename.lower().endswith(".xml")
        ):
            raise ValueError("legacy result filename invalid")
    return filename


def _official_legacy_url(value: str) -> str:
    _official_url(value, hosts=OFFICIAL_DOCUMENT_HOSTS)
    return value


def _require_exact_url(value: str, expected: str, *, label: str) -> str:
    if value != expected:
        raise ValueError(f"{label} source URL invalid")
    _official_legacy_url(value)
    return value


def _filename_date(filename: str, *, kind: str) -> date:
    prefix = "A" if kind == "announcement" else "R"
    match = re.fullmatch(rf"{prefix}_(\d{{8}})_\d+\.(?:pdf|xml)", filename, flags=re.IGNORECASE)
    if match is None:
        raise ValueError(f"legacy {kind} filename date invalid")
    try:
        return datetime.strptime(match.group(1), "%Y%m%d").date()
    except ValueError as exc:
        raise ValueError(f"legacy {kind} filename date invalid") from exc


def _legacy_document_urls(
    *,
    filename_pdf: Any,
    filename_xml: Any,
    kind: str,
    auction_date: str,
    source_base_url: str,
) -> dict[str, str]:
    pdf_name = _document_filename(filename_pdf, kind=kind)
    xml_name = _document_filename(filename_xml, kind=kind)
    if not pdf_name.lower().endswith(".pdf") or not xml_name.lower().endswith(".xml"):
        raise ValueError(f"legacy {kind} filename extensions invalid")
    if Path(pdf_name).stem != Path(xml_name).stem:
        raise ValueError(f"legacy {kind} filename identity mismatch")
    pdf_date = _filename_date(pdf_name, kind=kind)
    xml_date = _filename_date(xml_name, kind=kind)
    if pdf_date != xml_date:
        raise ValueError(f"legacy {kind} filename date identity mismatch")
    parsed_auction_date = date.fromisoformat(auction_date)
    if kind == "result" and pdf_date != parsed_auction_date:
        raise ValueError("legacy result filename date must equal auction date")
    if kind == "announcement" and pdf_date > parsed_auction_date:
        raise ValueError("legacy announcement filename date must not exceed auction date")
    year = str(pdf_date.year)
    pdf_url = urljoin(f"{TREASURY_PDF_BASE_URL}{year}/", pdf_name)
    xml_url = urljoin(source_base_url, xml_name)
    _official_legacy_url(pdf_url)
    _official_legacy_url(xml_url)
    return {
        "xml_name": xml_name,
        "xml_url": xml_url,
        "pdf_name": pdf_name,
        "pdf_url": pdf_url,
    }


def normalize_legacy_json_array(
    payload: bytes | bytearray | str | list[dict[str, Any]],
    *,
    source_base_url: str = TREASURY_XML_BASE_URL,
    start_date: str = START,
    end_date_exclusive: str = END_EXCLUSIVE,
) -> list[dict[str, Any]]:
    """Normalize legacy Treasury search rows into identity-only 30Y events.

    ``updatedTimestamp`` is retained only as the raw ``dataset_update`` field;
    it is deliberately never represented as publication or availability time.
    Economic auction fields are not copied.
    """

    _require_exact_url(source_base_url, TREASURY_XML_BASE_URL, label="legacy XML")
    try:
        bounded_start = date.fromisoformat(start_date)
        bounded_end = date.fromisoformat(end_date_exclusive)
    except (TypeError, ValueError) as exc:
        raise ValueError("legacy date bounds invalid") from exc
    if bounded_start >= bounded_end:
        raise ValueError("legacy date bounds invalid")
    rows = _json_array_payload(payload)
    events: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("legacy JSON row malformed")
        auction_date = _legacy_date(row.get("auctionDate"), "auctionDate")
        parsed_auction_date = date.fromisoformat(auction_date)
        if not bounded_start <= parsed_auction_date < bounded_end:
            continue
        original_term_value = row.get("originalSecurityTerm")
        tips_value = row.get("tips")
        if not isinstance(original_term_value, str) or not original_term_value.strip() or not isinstance(tips_value, str) or not tips_value.strip():
            if not isinstance(tips_value, str) or not tips_value.strip():
                if row.get("securityTerm") == "30-Year" or original_term_value == "30-Year":
                    raise ValueError("legacy selected identity missing:tips")
                continue
            if not isinstance(original_term_value, str) or not original_term_value.strip():
                if tips_value == "No" or row.get("securityTerm") == "30-Year":
                    raise ValueError("legacy selected identity missing:originalSecurityTerm")
                continue
            continue
        original_term = original_term_value.strip()
        tips = tips_value.strip()
        if tips != "No" or original_term != "30-Year":
            continue
        security_type = _required_text(row, "securityType")
        if security_type != "Bond":
            raise ValueError("legacy securityType must be Bond")
        cusip = _required_text(row, "cusip")
        if not CUSIP_RE.fullmatch(cusip):
            raise ValueError("legacy CUSIP invalid")
        issue_date = _legacy_date(row.get("issueDate"), "issueDate")
        original_issue_date = _optional_legacy_date(row.get("originalIssueDate"), "originalIssueDate")
        original_cusip_raw = row.get("originalCusip")
        original_cusip = None
        if original_cusip_raw is not None and (not isinstance(original_cusip_raw, str) or original_cusip_raw.strip()):
            original_cusip = _required_text(row, "originalCusip")
            if not CUSIP_RE.fullmatch(original_cusip):
                raise ValueError("legacy originalCusip invalid")
            if original_cusip != cusip:
                raise ValueError("legacy originalCusip lineage mismatch")
        reopening_raw = _required_text(row, "reopening").casefold()
        if reopening_raw in {"no", "n"}:
            issue_or_reopening = "original"
        elif reopening_raw in {"yes", "y"}:
            issue_or_reopening = "reopening"
        else:
            raise ValueError("legacy reopening classification invalid")
        dataset_update = _required_text(row, "updatedTimestamp")
        identity = (cusip, auction_date)
        if identity in seen:
            raise ValueError("legacy duplicate identity")
        if issue_date == auction_date:
            raise ValueError("legacy issue date must differ from auction date")
        if issue_or_reopening == "reopening":
            if original_issue_date is None:
                raise ValueError("legacy reopening originalIssueDate missing")
            if date.fromisoformat(original_issue_date) >= parsed_auction_date:
                raise ValueError("legacy reopening originalIssueDate must precede auction date")
        announcement = _legacy_document_urls(
            filename_pdf=row.get("pdfFilenameAnnouncement"),
            filename_xml=row.get("xmlFilenameAnnouncement"),
            kind="announcement",
            auction_date=auction_date,
            source_base_url=source_base_url,
        )
        result = _legacy_document_urls(
            filename_pdf=row.get("pdfFilenameCompetitiveResults"),
            filename_xml=row.get("xmlFilenameCompetitiveResults"),
            kind="result",
            auction_date=auction_date,
            source_base_url=source_base_url,
        )
        seen.add(identity)
        events.append(
            {
                "cusip": cusip,
                "auction_date": auction_date,
                "issue_date": issue_date,
                "original_issue_date": original_issue_date,
                "original_cusip": original_cusip,
                "issue_or_reopening": issue_or_reopening,
                "announcement": announcement,
                "result": result,
                "dataset_update": dataset_update,
            }
        )
    events.sort(key=lambda event: (event["auction_date"], event["cusip"], event["issue_date"], event["issue_or_reopening"]))
    if not events:
        raise ValueError("legacy no eligible 30-Year nominal events")
    return events


def _xml_local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _xml_critical_text(root: ElementTree.Element, field: str) -> str:
    matches = [child for child in root.iter() if _xml_local_name(str(child.tag)) == field]
    if len(matches) != 1:
        if len(matches) > 1:
            raise ValueError(f"result XML duplicate {field}")
        raise ValueError(f"result XML missing {field}")
    value = matches[0].text.strip() if matches[0].text else ""
    if not value:
        raise ValueError(f"result XML missing {field}")
    return value


def _validate_result_xml_time(value: str) -> str:
    if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d(?::[0-5]\d)?", value):
        raise ValueError("result XML ReleaseTime invalid time")
    return value


RESULT_XML_ROOT = "AuctionData"
RESULT_XML_NAMESPACE = "http://www.treasurydirect.gov/"


def _historical_window_fields(auction_date: str) -> dict[str, Any]:
    within = START <= auction_date < END_EXCLUSIVE
    return {
        "historical_window": {"start": START, "end_exclusive": END_EXCLUSIVE},
        "within_historical_window": within,
        "historical_admission_eligible": False,
        "capability_evidence_only": not within,
    }


def _declared_xml_schema(root: ElementTree.Element) -> tuple[str, str]:
    schema_location = root.attrib.get(f"{{{TREASURY_XML_SCHEMA_NAMESPACE}}}schemaLocation")
    if not isinstance(schema_location, str) or not schema_location.strip():
        raise ValueError("result XML schemaLocation missing")
    parts = schema_location.split()
    if len(parts) != 2 or parts[0] != RESULT_XML_NAMESPACE:
        raise ValueError("result XML schemaLocation invalid")
    schema_url = urlparse(parts[1])
    if (
        schema_url.scheme != "https"
        or schema_url.netloc != "www.treasurydirect.gov"
        or schema_url.params
        or schema_url.query
        or schema_url.fragment
        or not re.fullmatch(r"/xsd/Auction_v\d+_\d+_\d+\.xsd", schema_url.path)
    ):
        raise ValueError("result XML schemaLocation invalid")
    match = re.fullmatch(r"Auction_(v\d+_\d+_\d+)\.xsd", Path(schema_url.path).name)
    if match is None:
        raise ValueError("result XML schema version invalid")
    return schema_location, match.group(1)


def parse_result_xml(
    content: bytes,
    source_url: str,
    *,
    expected_cusip: str | None = None,
    expected_auction_date: str | None = None,
) -> dict[str, Any]:
    """Parse only identity and release metadata from one official result XML."""

    if not isinstance(content, bytes) or not content:
        raise ValueError("result XML body empty")
    _official_legacy_url(source_url)
    source_name = Path(urlparse(source_url).path).name
    if not re.fullmatch(r"R_\d{8}_\d+\.xml", source_name, flags=re.IGNORECASE):
        raise ValueError("result XML source filename invalid; official basename R_YYYYMMDD_N.xml required")
    _require_exact_url(source_url, urljoin(TREASURY_XML_BASE_URL, source_name), label="result XML")
    _guard_public_bytes(content, kind="result XML", limit=MAX_XML_RSS_BYTES)
    try:
        root = ElementTree.fromstring(content)
    except ElementTree.ParseError as exc:
        raise ValueError("result XML malformed") from exc
    if str(root.tag) != f"{{{RESULT_XML_NAMESPACE}}}{RESULT_XML_ROOT}":
        raise ValueError("result XML unexpected root")
    schema_location, schema_version = _declared_xml_schema(root)
    announcements = [child for child in list(root) if _xml_local_name(str(child.tag)) == "AuctionAnnouncement"]
    results = [child for child in list(root) if _xml_local_name(str(child.tag)) == "AuctionResults"]
    if len(announcements) != 1 or len(results) != 1:
        raise ValueError("result XML sections invalid")
    for field in ("CUSIP", "AuctionDate", "ReleaseTime", "ResultsPDFName"):
        _xml_critical_text(root, field)
    cusip = _xml_critical_text(announcements[0], "CUSIP")
    auction_date = _xml_critical_text(announcements[0], "AuctionDate")
    release_time = _xml_critical_text(results[0], "ReleaseTime")
    pdf_name = _xml_critical_text(results[0], "ResultsPDFName")
    if not CUSIP_RE.fullmatch(cusip):
        raise ValueError("result XML CUSIP invalid")
    try:
        parsed_date = date.fromisoformat(auction_date)
    except ValueError as exc:
        raise ValueError("result XML AuctionDate invalid date") from exc
    auction_date = parsed_date.isoformat()
    _validate_result_xml_time(release_time)
    if not re.fullmatch(r"R_\d{8}_\d+\.pdf", pdf_name, flags=re.IGNORECASE):
        raise ValueError("result XML ResultsPDFName invalid file")
    source_stem = Path(source_name).stem
    if Path(pdf_name).stem != source_stem:
        raise ValueError("result XML identity mismatch")
    source_date = date.fromisoformat(source_stem.split("_")[1]).isoformat()
    if source_date != auction_date:
        raise ValueError("result XML identity mismatch")
    if expected_cusip is not None and cusip != expected_cusip:
        raise ValueError("result XML identity mismatch CUSIP")
    if expected_auction_date is not None and auction_date != expected_auction_date:
        raise ValueError("result XML identity mismatch AuctionDate")
    pdf_url = urljoin(f"{TREASURY_PDF_BASE_URL}{auction_date[:4]}/", pdf_name)
    _official_legacy_url(pdf_url)
    blockers = [
        "SOURCE_TIMEZONE_UNPROVEN",
        "RESULT_VERSION_LINEAGE_UNPROVEN",
        "PUBLIC_AVAILABILITY_UNPROVEN",
        "XML_SCHEMA_VALIDATION_NOT_PERFORMED",
    ]
    historical = _historical_window_fields(auction_date)
    if historical["capability_evidence_only"]:
        blockers.append("POST_CUTOFF_CAPABILITY_ONLY")
    return {
        "schema": "r46-treasury-result-xml-evidence-v1",
        "source_url": source_url,
        "cusip": cusip,
        "auction_date": auction_date,
        "release_time": release_time,
        "results_pdf_name": pdf_name,
        "results_pdf_url": pdf_url,
        "raw_sha256": hashlib.sha256(content).hexdigest(),
        "source_timezone": None,
        "published_at": None,
        "available_at": None,
        "status": RAW_UNADMITTED_STATUS,
        "admission_status": "PRESENT_NOT_ADMITTED",
        "admitted": False,
        "schema_location": schema_location,
        "schema_version": schema_version,
        "schema_validation_status": "NOT_PERFORMED",
        "blockers": blockers,
        "credential_fields_persisted": False,
        **historical,
    }


def _rss_direct_text(item: ElementTree.Element, names: set[str]) -> list[str]:
    return [
        child.text.strip()
        for child in list(item)
        if _xml_local_name(str(child.tag)) in names and child.text and child.text.strip()
    ]


def _rss_labeled_values(text: str, label: str) -> list[str]:
    return [match.strip() for match in re.findall(rf"(?i)(?:^|[>\s]){re.escape(label)}\s*:\s*([^<;\r\n]+)", text)]


def _rss_identity_value(item: ElementTree.Element, description: str, *, label: str, direct_names: set[str]) -> str:
    values = _rss_direct_text(item, direct_names) + _rss_labeled_values(description, label)
    if len(values) != 1:
        raise ValueError(f"result RSS duplicate or missing {label}")
    return values[0]


def _rss_pub_date(value: str) -> str:
    value = value.strip()
    if not re.search(r"\b(?:GMT|UTC)\s*$", value, flags=re.IGNORECASE):
        raise ValueError("result RSS pubDate must use GMT or UTC")
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError) as exc:
        raise ValueError("result RSS pubDate invalid RFC/GMT") from exc
    if parsed.tzinfo is None:
        raise ValueError("result RSS pubDate timezone missing")
    if parsed.utcoffset() != timedelta(0):
        raise ValueError("result RSS pubDate must use zero-offset GMT or UTC")
    return parsed.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _rss_pub_date_values(value: str) -> tuple[str, str]:
    raw = value.strip()
    return raw, _rss_pub_date(raw)


def _rss_link_identities(item: ElementTree.Element, description: str, *, auction_date: str) -> tuple[str, str, str, str]:
    hrefs = re.findall(r"(?i)href\s*=\s*[\"']([^\"']+)[\"']", html.unescape(description))
    direct = _rss_direct_text(item, {"guid", "enclosure", "pdf", "xml", "pdfurl", "xmlurl"})
    links = hrefs + direct
    pdf_links = [link for link in links if urlparse(link).path.lower().endswith(".pdf")]
    xml_links = [link for link in links if urlparse(link).path.lower().endswith(".xml")]
    if len(pdf_links) != 1 or len(xml_links) != 1:
        raise ValueError("result RSS PDF/XML link identity missing or duplicate")
    pdf_url, xml_url = pdf_links[0], xml_links[0]
    _official_legacy_url(pdf_url)
    _official_legacy_url(xml_url)
    pdf_name = Path(urlparse(pdf_url).path).name
    xml_name = Path(urlparse(xml_url).path).name
    if not re.fullmatch(r"R_\d{8}_\d+\.pdf", pdf_name, flags=re.IGNORECASE) or not re.fullmatch(r"R_\d{8}_\d+\.xml", xml_name, flags=re.IGNORECASE):
        raise ValueError("result RSS linked file identity invalid")
    if Path(pdf_name).stem != Path(xml_name).stem:
        raise ValueError("result RSS link identity mismatch")
    try:
        linked_date = date.fromisoformat(Path(pdf_name).stem.split("_")[1]).isoformat()
    except ValueError as exc:
        raise ValueError("result RSS linked file identity invalid") from exc
    if linked_date != auction_date:
        raise ValueError("result RSS link identity mismatch")
    expected_pdf_url = urljoin(f"{TREASURY_PDF_BASE_URL}{linked_date[:4]}/", pdf_name)
    expected_xml_url = urljoin(TREASURY_XML_BASE_URL, xml_name)
    if pdf_url != expected_pdf_url or xml_url != expected_xml_url:
        raise ValueError("result RSS linked URL path identity mismatch")
    return pdf_name, pdf_url, xml_name, xml_url


def _rss_description_text(description: str) -> str:
    plain = re.sub(r"(?is)<br\s*/?>", "\n", html.unescape(description))
    plain = re.sub(r"(?is)<[^>]+>", " ", plain)
    return html.unescape(plain)


def _rss_term(value: str) -> str:
    match = re.match(r"^((?:\d+-(?:Year|Month|Week|Day))(?:\s+\d+-(?:Year|Month|Week|Day))?)\s+.+$", value.strip(), flags=re.IGNORECASE)
    if match is None:
        raise ValueError("result RSS term invalid")
    return match.group(1)


def parse_result_rss(content: bytes, source_url: str) -> dict[str, Any]:
    """Parse official result RSS only as a non-admitting cross-check."""

    if not isinstance(content, bytes) or not content:
        raise ValueError("result RSS body empty")
    _require_exact_url(source_url, TREASURY_AUCTIONED_RESULT_RSS_URL, label="result RSS")
    _guard_public_bytes(content, kind="result RSS", limit=MAX_XML_RSS_BYTES)
    try:
        root = ElementTree.fromstring(content)
    except ElementTree.ParseError as exc:
        raise ValueError("result RSS malformed") from exc
    if _xml_local_name(str(root.tag)) != "rss":
        raise ValueError("result RSS unexpected root")
    channels = [child for child in list(root) if _xml_local_name(str(child.tag)) == "channel"]
    if len(channels) != 1:
        raise ValueError("result RSS channel invalid")
    items = [child for child in list(channels[0]) if _xml_local_name(str(child.tag)) == "item"]
    if not items:
        raise ValueError("result RSS items missing")
    normalized: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for item in items:
        descriptions = _rss_direct_text(item, {"description"})
        if len(descriptions) != 1:
            raise ValueError("result RSS description invalid")
        description = html.unescape(descriptions[0])
        description_text = _rss_description_text(description)
        pub_dates = _rss_direct_text(item, {"pubDate"})
        if len(pub_dates) != 1:
            raise ValueError("result RSS pubDate duplicate or missing")
        cusip = _rss_identity_value(item, description_text, label="CUSIP", direct_names={"CUSIP", "cusip"})
        term_raw = _rss_identity_value(item, description_text, label="Term and Type", direct_names={"SecurityTerm", "Term", "term"})
        auction_date_raw = _rss_identity_value(item, description_text, label="Auction Date", direct_names={"AuctionDate", "auctionDate"})
        if not CUSIP_RE.fullmatch(cusip):
            raise ValueError("result RSS CUSIP invalid")
        term = _rss_term(term_raw)
        try:
            auction_date = date.fromisoformat(auction_date_raw).isoformat()
        except ValueError:
            try:
                auction_date = datetime.strptime(auction_date_raw, "%m/%d/%Y").date().isoformat()
            except ValueError as exc:
                raise ValueError("result RSS Auction Date invalid date") from exc
        pdf_name, pdf_url, xml_name, xml_url = _rss_link_identities(item, description, auction_date=auction_date)
        identity = (cusip, auction_date)
        if identity in seen:
            raise ValueError("result RSS duplicate identity")
        seen.add(identity)
        pub_date, pub_date_utc = _rss_pub_date_values(pub_dates[0])
        historical = _historical_window_fields(auction_date)
        normalized.append(
            {
                "cusip": cusip,
                "term": term,
                "auction_date": auction_date,
                "pub_date": pub_date,
                "pub_date_utc": pub_date_utc,
                "pdf_name": pdf_name,
                "pdf_url": pdf_url,
                "xml_name": xml_name,
                "xml_url": xml_url,
                "available_at": None,
                **{key: value for key, value in historical.items() if key != "historical_window"},
            }
        )
    normalized.sort(key=lambda item: (item["auction_date"], item["cusip"], item["pub_date_utc"]))
    blockers = ["RSS_CROSSCHECK_ONLY", "PUBLIC_AVAILABILITY_UNPROVEN"]
    if any(item["capability_evidence_only"] for item in normalized):
        blockers.append("POST_CUTOFF_CAPABILITY_ONLY")
    return {
        "schema": "r46-treasury-result-rss-crosscheck-v1",
        "source_url": source_url,
        "raw_sha256": hashlib.sha256(content).hexdigest(),
        "status": "CROSSCHECK_ONLY",
        "retention_complete": False,
        "inventory_source": False,
        "admission_source": False,
        "historical_window": {"start": START, "end_exclusive": END_EXCLUSIVE},
        "items": normalized,
        "blockers": blockers,
        "credentials_used": False,
    }


def _reconcile_cross_sources(
    events: list[dict[str, Any]],
    result_rss: dict[str, Any] | None,
    result_xml: list[dict[str, Any]],
) -> dict[str, Any]:
    legacy_index = {(event["cusip"], event["auction_date"]): event for event in events}
    rss_items = result_rss["items"] if isinstance(result_rss, dict) else []
    rss_index = {
        (item["cusip"], item["auction_date"], item["xml_url"]): item
        for item in rss_items
    }
    matched_rss: set[tuple[str, str, str]] = set()
    in_window_count = 0
    in_window_matched = 0
    post_cutoff_count = 0
    post_cutoff_matched = 0
    xml_identities: set[tuple[str, str]] = set()
    for record in result_xml:
        identity = (record["cusip"], record["auction_date"])
        if identity in xml_identities:
            raise ValueError("result XML duplicate identity")
        xml_identities.add(identity)
        if record["within_historical_window"]:
            in_window_count += 1
            event = legacy_index.get(identity)
            if event is None:
                raise ValueError("result XML in-window legacy event mismatch")
            if record["source_url"] != event["result"]["xml_url"]:
                raise ValueError("result XML legacy result URL identity mismatch")
            record["cross_source_match"] = "LEGACY_EVENT"
            in_window_matched += 1
            rss_key = (record["cusip"], record["auction_date"], record["source_url"])
            if rss_key in rss_index:
                rss_index[rss_key]["cross_source_match"] = "LEGACY_EVENT"
                matched_rss.add(rss_key)
        else:
            post_cutoff_count += 1
            if result_rss is None:
                raise ValueError("post-cutoff result XML requires matching RSS evidence")
            rss_key = (record["cusip"], record["auction_date"], record["source_url"])
            if rss_key not in rss_index:
                raise ValueError("post-cutoff result XML RSS identity mismatch")
            record["cross_source_match"] = "POST_CUTOFF_CAPABILITY_ONLY"
            rss_index[rss_key]["cross_source_match"] = "POST_CUTOFF_CAPABILITY_ONLY"
            matched_rss.add(rss_key)
            post_cutoff_matched += 1
    if not result_xml:
        status = "NOT_PERFORMED_NO_EXPLICIT_RESULT_XML"
    elif post_cutoff_count:
        status = "RECONCILED_CAPABILITY_ONLY"
    else:
        status = "RECONCILED"
    return {
        "status": status,
        "legacy_event_count": len(events),
        "result_xml_count": len(result_xml),
        "result_xml_in_window": in_window_count,
        "result_xml_in_window_matched": in_window_matched,
        "result_xml_post_cutoff": post_cutoff_count,
        "result_xml_post_cutoff_matched": post_cutoff_matched,
        "result_rss_item_count": len(rss_items),
        "result_rss_matched": len(matched_rss),
        "result_rss_unmatched": len(rss_items) - len(matched_rss),
    }


def _local_artifact(
    path: Path,
    *,
    kind: str,
    content: bytes | None = None,
    source_url: str,
) -> dict[str, Any]:
    if content is None:
        content = path.read_bytes()
    _official_legacy_url(source_url)
    return {
        "kind": kind,
        "path": str(path),
        "source_url": source_url,
        "bytes": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
    }


def build_local_public_evidence_receipt(
    *,
    legacy_json_path: Path,
    announced_rss_path: Path | None = None,
    result_rss_path: Path | None = None,
    result_xml_paths: list[Path] | tuple[Path, ...] = (),
    output_root: Path | None = None,
    receipt_relative_path: str | None = None,
) -> dict[str, Any]:
    """Build a local-only, non-economic public-source evidence receipt."""

    legacy_content = legacy_json_path.read_bytes()
    events = normalize_legacy_json_array(legacy_content)
    artifacts: dict[str, Any] = {
        "legacy_json": _local_artifact(
            legacy_json_path,
            kind="legacy_json",
            content=legacy_content,
            source_url=TREASURY_LEGACY_JSON_SEARCH_URL,
        )
    }
    result_rss = None
    if result_rss_path is not None:
        rss_content = result_rss_path.read_bytes()
        result_rss = parse_result_rss(rss_content, TREASURY_AUCTIONED_RESULT_RSS_URL)
        artifacts["result_rss"] = _local_artifact(
            result_rss_path,
            kind="result_rss",
            content=rss_content,
            source_url=TREASURY_AUCTIONED_RESULT_RSS_URL,
        )
    announced_rss = None
    if announced_rss_path is not None:
        announced_content = announced_rss_path.read_bytes()
        artifacts["announced_rss"] = _local_artifact(
            announced_rss_path,
            kind="announced_rss",
            content=announced_content,
            source_url=TREASURY_ANNOUNCED_RSS_URL,
        )
        announced_rss = {
            "status": "HASH_ONLY_UNVALIDATED",
            "source_url": TREASURY_ANNOUNCED_RSS_URL,
            "raw_sha256": artifacts["announced_rss"]["sha256"],
            "blockers": ["ANNOUNCEMENT_RSS_UNVALIDATED"],
        }
    result_xml: list[dict[str, Any]] = []
    xml_artifacts: list[dict[str, Any]] = []
    seen_xml_paths: set[str] = set()
    seen_xml_hashes: set[str] = set()
    seen_xml_identities: set[tuple[str, str]] = set()
    for xml_path in result_xml_paths:
        resolved_xml_path = str(xml_path.resolve(strict=False))
        if resolved_xml_path in seen_xml_paths:
            raise ValueError("result XML duplicate path")
        seen_xml_paths.add(resolved_xml_path)
        xml_content = xml_path.read_bytes()
        xml_hash = hashlib.sha256(xml_content).hexdigest()
        if xml_hash in seen_xml_hashes:
            raise ValueError("result XML duplicate hash")
        seen_xml_hashes.add(xml_hash)
        xml_name = xml_path.name
        xml_url = urljoin(TREASURY_XML_BASE_URL, xml_name)
        parsed = parse_result_xml(xml_content, xml_url)
        xml_identity = (parsed["cusip"], parsed["auction_date"])
        if xml_identity in seen_xml_identities:
            raise ValueError("result XML duplicate identity")
        seen_xml_identities.add(xml_identity)
        result_xml.append(parsed)
        xml_artifacts.append(
            _local_artifact(
                xml_path,
                kind="result_xml",
                content=xml_content,
                source_url=xml_url,
            )
        )
    if xml_artifacts:
        artifacts["result_xml"] = xml_artifacts
    cross_source_reconciliation = _reconcile_cross_sources(events, result_rss, result_xml)
    blockers = ["SOURCE_TIMEZONE_UNPROVEN", "RESULT_VERSION_LINEAGE_UNPROVEN", "PUBLIC_AVAILABILITY_UNPROVEN"]
    if result_rss is None:
        blockers.append("RESULT_RSS_NOT_PROVIDED")
    else:
        blockers.extend(result_rss["blockers"])
    if not result_xml:
        blockers.append("RESULT_XML_NOT_PROVIDED")
    else:
        for parsed in result_xml:
            blockers.extend(parsed["blockers"])
    if announced_rss is not None:
        blockers.extend(announced_rss["blockers"])
    receipt: dict[str, Any] = {
        "schema": "r46-treasury-public-source-evidence-receipt-v1",
        "research_id": "r46_treasury_auction_unwind_tlt_v1_2026_09_10",
        "candidate_id": "R46",
        "source_routes": official_source_routes(),
        "status": "SOURCE_EVIDENCE_PARTIAL",
        "admission": "DATA_ACQUISITION_REQUIRED",
        "economic_execution": False,
        "economic_run": False,
        "outcomes_accessed": False,
        "economic_outcomes_accessed": False,
        "dataset_update_semantic_class": "dataset_update",
        "network_attempted": False,
        "credentials_used": False,
        "paid_source_used": False,
        "events": events,
        "historical_window": {"start": START, "end_exclusive": END_EXCLUSIVE},
        "cross_source_reconciliation": cross_source_reconciliation,
        "announced_rss": announced_rss,
        "result_rss": result_rss,
        "result_xml": result_xml,
        "artifacts": artifacts,
        "raw_artifact_hashes": {
            key: ([entry["sha256"] for entry in value] if isinstance(value, list) else value["sha256"])
            for key, value in artifacts.items()
        },
        "blockers": sorted(set(blockers)),
    }
    if output_root is not None and receipt_relative_path is not None:
        _safe_relative_path(output_root, receipt_relative_path)
        receipt["receipt_path"] = receipt_relative_path
    receipt["artifact_sha256"] = _canonical_artifact_hash(receipt, "artifact_sha256")
    if output_root is not None and receipt_relative_path is not None:
        _atomic_json(output_root, receipt_relative_path, receipt)
    return receipt


# Descriptive aliases keep the data-only API easy to discover without changing
# the original PDF acquisition entry points.
normalize_legacy_json = normalize_legacy_json_array
parse_legacy_json_array = normalize_legacy_json_array
parse_official_result_xml = parse_result_xml
parse_official_result_rss = parse_result_rss
build_public_evidence_receipt = build_local_public_evidence_receipt


def build_document_record(
    root: Path,
    relative_path: str,
    content: bytes,
    *,
    source_url: str,
    document_id: str,
    document_kind: str,
    canonical_event_id: str,
    source_timezone: str,
    retrieved_at: str,
    security_type: str,
    instrument_type: str,
    issuer_identifier: str,
    original_tenor: int,
    issue_or_reopening: str,
    security_identifier: str,
    auction_session_date: str,
    correction_or_amendment_chain: list[dict[str, Any]],
    issue_date: str | None = None,
    revision_id: str = "original",
    supersedes_document_id: str | None = None,
    result_status: str | None = None,
) -> dict[str, Any]:
    """Write a validated raw response without admitting its event data.

    The project deliberately has no PDF extraction dependency. In particular,
    this function must not decode a PDF as UTF-8 or treat text-like fixture
    markers as source publication evidence. The caller's inventory supplies
    identity only; publication/availability fields remain null until a real,
    independently verified extractor is available.
    """

    _official_url(source_url, hosts=OFFICIAL_DOCUMENT_HOSTS)
    if not isinstance(content, bytes) or not content:
        raise ValueError("document content empty")
    if not content.startswith(PDF_SIGNATURE):
        raise ValueError("document content signature invalid")
    if issue_date is not None and auction_session_date == issue_date:
        raise ValueError("auction_session_date must be auction date")
    if not isinstance(source_timezone, str) or not source_timezone.strip():
        raise ValueError("source_timezone missing")
    _parse_aware(retrieved_at, "retrieved_at")
    if document_kind not in {"announcement", "result"}:
        raise ValueError("document_kind invalid")
    if security_type != "nominal" or float(original_tenor) != 30.0:
        raise ValueError("R46 requires nominal 30Y")
    if not CUSIP_RE.fullmatch(security_identifier):
        raise ValueError("security_identifier invalid")
    if issue_or_reopening not in {"original", "reopening"}:
        raise ValueError("issue_or_reopening invalid")
    if document_kind == "result" and result_status not in {"COMPLETED", "COMPLETED_WITH_AMENDMENT"}:
        raise ValueError("result status is not completed")
    if document_kind == "announcement" and result_status is not None:
        raise ValueError("announcement has result status")

    destination = _atomic_write_no_overwrite(root, relative_path, content)
    return {
        "document_id": document_id,
        "document_kind": document_kind,
        "canonical_event_id": canonical_event_id,
        "source_url": source_url,
        "raw_path": relative_path,
        "sha256": sha256(destination),
        "security_type": security_type,
        "instrument_type": instrument_type,
        "issuer_identifier": issuer_identifier,
        "original_tenor": int(original_tenor),
        "issue_or_reopening": issue_or_reopening,
        "security_identifier": security_identifier,
        "auction_session_date": auction_session_date,
        "issue_date": issue_date,
        "published_at": None,
        "available_at": None,
        "source_timezone": source_timezone,
        "publication_at_source": None,
        "timestamp_evidence": None,
        "source_timestamp_status": "SOURCE_TIMESTAMP_MISSING",
        "retrieved_at": retrieved_at,
        "revision_id": revision_id,
        "supersedes_document_id": supersedes_document_id,
        "correction_or_amendment_chain": correction_or_amendment_chain,
        "result_status": result_status,
        "status": RAW_UNADMITTED_STATUS,
        "admitted": False,
        "error_code": RAW_UNADMITTED_ERROR,
        "admission_blocker": "SOURCE_TIMESTAMP_MISSING;EXTRACTOR_UNAVAILABLE",
        "credential_fields_persisted": False,
    }


def sanitized_blocked_receipt(
    *,
    source_url: str,
    status_code: int | None,
    error: str | None,
    retrieved_at: str,
    error_code: str | None = None,
    network_attempted: bool = False,
) -> dict[str, Any]:
    """Return a receipt with no headers, tokens, or provider error text."""

    parsed = urlparse(source_url)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
        raise ValueError("source_url must be HTTPS without credentials")
    _parse_aware(retrieved_at, "retrieved_at")
    if status_code is not None and (isinstance(status_code, bool) or not 100 <= status_code <= 599):
        raise ValueError("status_code invalid")
    if error_code is not None:
        status = "BLOCKED_VALIDATION"
        reason = error_code
    elif status_code is not None and status_code == 401:
        status = "BLOCKED_HTTP"
        reason = "HTTP_UNAUTHORIZED"
    elif status_code is not None and not 200 <= status_code < 300:
        status = "BLOCKED_HTTP"
        reason = f"HTTP_{status_code}"
    else:
        status = "BLOCKED_REQUEST"
        reason = "REQUEST_ERROR"
    return {
        "schema": "r46-sanitized-acquisition-receipt-v1",
        "source_url": source_url,
        "retrieved_at": retrieved_at,
        "status": status,
        "status_code": status_code,
        "error_code": reason,
        "credential_fields_persisted": False,
        "raw_path": None,
        "raw_sha256": None,
        "admitted": False,
        "network_attempted": network_attempted,
        "admission_blocker": "source_unavailable_or_timestamp_missing",
    }


def _blocked_validation(source_url: str, *, retrieved_at: str, error_code: str, network_attempted: bool = True) -> dict[str, Any]:
    return sanitized_blocked_receipt(
        source_url=source_url,
        status_code=200,
        error=None,
        retrieved_at=retrieved_at,
        error_code=error_code,
        network_attempted=network_attempted,
    )


def fetch_official_document(
    *,
    root: Path,
    relative_path: str,
    source_url: str,
    metadata: dict[str, Any],
    timeout: float = 30.0,
    session: Any = requests,
) -> dict[str, Any]:
    """Fetch one identified official PDF without trusting caller timestamps."""

    _official_url(source_url, hosts=OFFICIAL_DOCUMENT_HOSTS)
    if not isinstance(metadata, dict):
        return _blocked_validation(
            source_url,
            retrieved_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            error_code="METADATA_INVALID",
            network_attempted=False,
        )
    try:
        _reject_caller_timestamps(metadata)
    except ValueError:
        return _blocked_validation(
            source_url,
            retrieved_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            error_code="CALLER_TIMESTAMP_FIELDS_FORBIDDEN",
            network_attempted=False,
        )
    retrieved_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    try:
        response = session.get(source_url, timeout=timeout)
    except requests.RequestException:
        return sanitized_blocked_receipt(
            source_url=source_url,
            status_code=None,
            error=None,
            retrieved_at=retrieved_at,
            network_attempted=True,
        )
    if response.status_code != 200:
        return sanitized_blocked_receipt(
            source_url=source_url,
            status_code=response.status_code,
            error=None,
            retrieved_at=retrieved_at,
            network_attempted=True,
        )
    final_url = getattr(response, "url", None) or source_url
    try:
        _official_url(final_url, hosts=OFFICIAL_DOCUMENT_HOSTS)
    except ValueError:
        return _blocked_validation(source_url, retrieved_at=retrieved_at, error_code="FINAL_URL_NOT_OFFICIAL")
    headers = getattr(response, "headers", {})
    content_type = str(headers.get("content-type", "")).split(";", 1)[0].strip().lower()
    if content_type != PDF_CONTENT_TYPE:
        return _blocked_validation(source_url, retrieved_at=retrieved_at, error_code="CONTENT_TYPE_INVALID")
    content = response.content
    if not isinstance(content, bytes) or not content:
        return _blocked_validation(source_url, retrieved_at=retrieved_at, error_code="EMPTY_BODY")
    if not content.startswith(PDF_SIGNATURE):
        return _blocked_validation(source_url, retrieved_at=retrieved_at, error_code="CONTENT_SIGNATURE_INVALID")
    try:
        record = build_document_record(
            root,
            relative_path,
            content,
            source_url=final_url,
            document_id=str(metadata["document_id"]),
            document_kind=str(metadata["document_kind"]),
            canonical_event_id=str(metadata["canonical_event_id"]),
            source_timezone=str(metadata["source_timezone"]),
            retrieved_at=retrieved_at,
            security_type=str(metadata["security_type"]),
            instrument_type=str(metadata["instrument_type"]),
            issuer_identifier=str(metadata["issuer_identifier"]),
            original_tenor=int(metadata["original_tenor"]),
            issue_or_reopening=str(metadata["issue_or_reopening"]),
            security_identifier=str(metadata["security_identifier"]),
            auction_session_date=str(metadata["auction_session_date"]),
            issue_date=str(metadata["issue_date"]) if metadata.get("issue_date") is not None else None,
            correction_or_amendment_chain=list(metadata["correction_or_amendment_chain"]),
            revision_id=str(metadata.get("revision_id", "original")),
            supersedes_document_id=metadata.get("supersedes_document_id"),
            result_status=metadata.get("result_status"),
        )
    except (KeyError, TypeError, ValueError, OSError) as exc:
        message = str(exc)
        if "auction date" in message:
            error_code = "AUCTION_DATE_INVALID"
        elif "content signature" in message:
            error_code = "CONTENT_SIGNATURE_INVALID"
        else:
            error_code = "DOCUMENT_VALIDATION"
        return _blocked_validation(source_url, retrieved_at=retrieved_at, error_code=error_code)
    record.update(
        {
            "status": RAW_UNADMITTED_STATUS,
            "http_status": response.status_code,
            "content_type": content_type,
            "final_url": final_url,
            "retrieved_at": retrieved_at,
            "network_attempted": True,
        }
    )
    return record


def _reject_caller_timestamps(value: Any) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if key in CALLER_TIMESTAMP_FIELDS:
                raise ValueError(f"caller timestamp field forbidden:{key}")
            _reject_caller_timestamps(child)
    elif isinstance(value, list):
        for child in value:
            _reject_caller_timestamps(child)


def _inventory_pin_status(payload: dict[str, Any], *, base_dir: Path) -> str:
    """Classify whether coverage is bound to verified local official evidence."""

    pin = payload.get("official_inventory_pin")
    if not isinstance(pin, dict):
        return INVENTORY_UNPINNED_STATUS
    try:
        _official_url(pin["export_url"], hosts=OFFICIAL_DOCUMENT_HOSTS)
        export_sha256 = pin["export_sha256"]
        if not isinstance(export_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", export_sha256):
            return INVENTORY_INVALID_PIN_STATUS
        export_path = _safe_relative_path(base_dir, pin["export_path"])
        if not export_path.is_file() or sha256(export_path) != export_sha256:
            return INVENTORY_INVALID_PIN_STATUS
        annual_coverage = pin["annual_coverage"]
        if not isinstance(annual_coverage, list) or not annual_coverage:
            return INVENTORY_INVALID_PIN_STATUS
        for coverage in annual_coverage:
            if not isinstance(coverage, dict):
                return INVENTORY_INVALID_PIN_STATUS
            _official_url(coverage["url"], hosts=OFFICIAL_DOCUMENT_HOSTS)
            coverage_sha256 = coverage["sha256"]
            if not isinstance(coverage_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", coverage_sha256):
                return INVENTORY_INVALID_PIN_STATUS
            coverage_path = _safe_relative_path(base_dir, coverage["path"])
            if not coverage_path.is_file() or sha256(coverage_path) != coverage_sha256:
                return INVENTORY_INVALID_PIN_STATUS
    except (KeyError, OSError, TypeError, ValueError):
        return INVENTORY_INVALID_PIN_STATUS
    return INVENTORY_PINNED_STATUS


def load_pinned_inventory(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("inventory unreadable") from exc
    if not isinstance(payload, dict) or payload.get("schema") != INVENTORY_SCHEMA:
        raise ValueError("inventory schema invalid")
    _reject_caller_timestamps(payload)
    if payload.get("research_id") != "r46_treasury_auction_unwind_tlt_v1_2026_09_10" or payload.get("candidate_id") != "R46":
        raise ValueError("inventory identity invalid")
    if payload.get("historical_cutoff") != CUTOFF_UTC:
        raise ValueError("inventory historical cutoff invalid")
    _official_url(payload.get("source_url"), hosts=OFFICIAL_DOCUMENT_HOSTS)
    events = payload.get("events")
    if not isinstance(events, list) or not events:
        raise ValueError("inventory events missing")
    seen_events: set[str] = set()
    seen_securities: set[tuple[str, str]] = set()
    normalized_events: list[dict[str, Any]] = []
    historical_cutoff_date = _parse_aware(CUTOFF_UTC, "historical_cutoff").date()
    required = {
        "canonical_event_id",
        "security_identifier",
        "original_tenor",
        "issue_or_reopening",
        "auction_session_date",
        "announcement_url",
        "result_url",
    }
    for event in events:
        if not isinstance(event, dict) or not required.issubset(event):
            raise ValueError("inventory event schema invalid")
        event_id = event["canonical_event_id"]
        security_identifier = event["security_identifier"]
        if not isinstance(event_id, str) or not event_id.strip() or event_id in seen_events:
            raise ValueError("inventory duplicate event")
        if not isinstance(security_identifier, str) or not CUSIP_RE.fullmatch(security_identifier):
            raise ValueError("inventory security_identifier invalid")
        if isinstance(event["original_tenor"], bool) or int(event["original_tenor"]) != 30:
            raise ValueError("inventory original tenor invalid")
        if event["issue_or_reopening"] not in {"original", "reopening"}:
            raise ValueError("inventory issue_or_reopening invalid")
        try:
            auction_date = date.fromisoformat(str(event["auction_session_date"]))
        except ValueError as exc:
            raise ValueError("inventory auction_session_date invalid") from exc
        if auction_date > historical_cutoff_date:
            raise ValueError("inventory auction_session_date post_cutoff")
        issue_date = event.get("issue_date")
        if issue_date is not None:
            try:
                parsed_issue_date = date.fromisoformat(str(issue_date))
            except ValueError as exc:
                raise ValueError("inventory issue_date invalid") from exc
            if auction_date == parsed_issue_date:
                raise ValueError("auction_session_date must be auction date")
        _official_url(event["announcement_url"], hosts=OFFICIAL_DOCUMENT_HOSTS)
        _official_url(event["result_url"], hosts=OFFICIAL_DOCUMENT_HOSTS)
        key = (security_identifier, auction_date.isoformat())
        if key in seen_securities:
            raise ValueError("inventory duplicate security/date")
        seen_events.add(event_id)
        seen_securities.add(key)
        normalized = dict(event)
        normalized["original_tenor"] = 30
        normalized["auction_session_date"] = auction_date.isoformat()
        normalized_events.append(normalized)
    normalized_payload = dict(payload)
    normalized_payload["events"] = normalized_events
    normalized_payload["inventory_pin_status"] = _inventory_pin_status(payload, base_dir=path.parent)
    normalized_payload["inventory_sha256"] = sha256(path)
    normalized_payload["inventory_path"] = str(path)
    return normalized_payload


def build_source_capability_receipt(
    *,
    checked_at: str,
    result_publication_timestamp_found: bool,
    complete_inventory_pinned: bool,
    tlt_source_status: str,
) -> dict[str, Any]:
    _parse_aware(checked_at, "checked_at")
    blockers: list[str] = []
    if not result_publication_timestamp_found:
        blockers.append("RESULT_PUBLICATION_TIMESTAMP_UNAVAILABLE")
    if not complete_inventory_pinned:
        blockers.append("COMPLETE_EVENT_INVENTORY_NOT_PINNED")
    if tlt_source_status != "READY_PIT_SOURCE":
        blockers.append(tlt_source_status)
    receipt: dict[str, Any] = {
        "schema": CAPABILITY_SCHEMA,
        "research_id": "r46_treasury_auction_unwind_tlt_v1_2026_09_10",
        "candidate_id": "R46",
        "checked_at": checked_at,
        "status": "CAPABILITY_OR_PAID_BLOCKED" if blockers else "PUBLIC_ROUTE_CAPABLE",
        "route_capability_status": "PUBLIC_ROUTES_IDENTIFIED",
        "r46_admission_status": "DATA_ACQUISITION_REQUIRED",
        "routes": official_source_routes(),
        "observations": {
            "archive": "Historical announcement/result press releases are linked by auction year; page does not expose a complete machine-readable inventory in this probe.",
            "auction_query": "Official query advertises date-range CSV/JSON/TSV/XML export and identity/document filename fields; no per-document publication clock is exposed.",
            "sample_announcement_url": "https://www.treasurydirect.gov/instit/annceresult/press/preanre/2024/A_20240131_2.pdf",
            "sample_result_url": "https://www.treasurydirect.gov/instit/annceresult/press/preanre/2024/R_20240208_3.pdf",
            "sample_auction_date": "2024-02-08",
            "sample_issue_date": "2024-02-15",
            "sample_announcement_clock": "Embargoed Until 08:30 A.M.",
            "sample_result_clock": None,
            "fiscaldata_publication_field": "record_date is DATE-only, not a source publication timestamp",
        },
        "source_evidence": [
            {
                "url": TREASURY_ARCHIVE_URL,
                "fact": "Historical announcement/result press releases are linked by auction year; complete machine-readable coverage was not proven.",
                "checked_at": checked_at,
            },
            {
                "url": TREASURY_AUCTION_QUERY_URL,
                "fact": "The official query advertises date-range export formats and document identity/filename fields, but no per-document publication clock.",
                "checked_at": checked_at,
            },
            {
                "url": "https://www.treasurydirect.gov/instit/annceresult/press/preanre/2024/A_20240131_2.pdf",
                "fact": "The sample announcement identifies CUSIP 912810TX6, nominal 30-year bond, auction date 2024-02-08, original issue date 2024-02-15, and an 08:30 A.M. embargo line.",
                "checked_at": checked_at,
            },
            {
                "url": "https://www.treasurydirect.gov/instit/annceresult/press/preanre/2024/R_20240208_3.pdf",
                "fact": "The sample result identifies the same issue and says For Immediate Release, but exposes no exact publication clock.",
                "checked_at": checked_at,
            },
            {
                "url": TREASURY_FISCAL_DOCUMENTS_URL,
                "fact": "The public fiscal route exposes record_date as date-only, which cannot prove published_at or available_at.",
                "checked_at": checked_at,
            },
        ],
        "blockers": blockers,
        "timestamp_rule": "Never use caller metadata, retrieval time, HTTP retrieval time, or date-only record_date as published_at/available_at; preserve the 16:15 America/New_York cutoff.",
        "credentials_used": False,
        "paid_source_used": False,
        "network_acquisition_attempted": False,
    }
    receipt["artifact_sha256"] = _canonical_artifact_hash(receipt, "artifact_sha256")
    return receipt


def _safe_component(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("._") or "event"


def _metadata_for_event(event: dict[str, Any], kind: str) -> dict[str, Any]:
    return {
        "document_id": f"{event['canonical_event_id']}:{kind}",
        "document_kind": kind,
        "canonical_event_id": event["canonical_event_id"],
        "source_timezone": "America/New_York",
        "security_type": "nominal",
        "instrument_type": "US_TREASURY_SECURITY",
        "issuer_identifier": "US_TREASURY",
        "original_tenor": 30,
        "issue_or_reopening": event["issue_or_reopening"],
        "security_identifier": event["security_identifier"],
        "auction_session_date": event["auction_session_date"],
        "issue_date": event.get("issue_date"),
        "correction_or_amendment_chain": [],
        "revision_id": "original",
        "supersedes_document_id": None,
        "result_status": "COMPLETED" if kind == "result" else None,
    }


def acquire_pinned_inventory(
    inventory_path: Path,
    *,
    output_root: Path,
    fetch: bool = False,
    timeout: float = 30.0,
    session: Any = requests,
) -> dict[str, Any]:
    """Ingest a pinned URL inventory and fetch only when ``fetch`` is true."""

    inventory = load_pinned_inventory(inventory_path)
    result: dict[str, Any] = {
        "schema": RECEIPT_SCHEMA,
        "research_id": inventory["research_id"],
        "candidate_id": inventory["candidate_id"],
        "inventory_sha256": inventory["inventory_sha256"],
        "inventory_source_url": inventory["source_url"],
        "inventory_pin_status": inventory["inventory_pin_status"],
        "r46_admission_status": "DATA_ACQUISITION_REQUIRED",
        "network_attempted": False,
        "credentials_used": False,
        "paid_source_used": False,
        "documents": [],
        "events": [],
        "blockers": [],
    }
    if inventory["inventory_pin_status"] != INVENTORY_PINNED_STATUS:
        result.update(
            {
                "status": "CAPABILITY_OR_PAID_BLOCKED",
                "blockers": ["COMPLETE_EVENT_INVENTORY_NOT_HASH_PINNED"],
                "admitted_document_count": 0,
                "admitted_event_count": 0,
            }
        )
        return result
    if not fetch:
        result.update({"status": "PINNED_INVENTORY_ONLY", "admitted_document_count": 0, "admitted_event_count": 0})
        return result

    result["network_attempted"] = True
    blockers: list[str] = []
    for event in inventory["events"]:
        event_id = str(event["canonical_event_id"])
        safe_event_id = _safe_component(event_id)
        for kind, url in (("announcement", event["announcement_url"]), ("result", event["result_url"])):
            document = fetch_official_document(
                root=output_root,
                relative_path=f"raw/{safe_event_id}/{kind}.pdf",
                source_url=str(url),
                metadata=_metadata_for_event(event, kind),
                timeout=timeout,
                session=session,
            )
            result["documents"].append(document)
            blocker = document.get("error_code")
            if document.get("status") != RAW_UNADMITTED_STATUS or document.get("admitted") is not False:
                blocker = RAW_UNADMITTED_ERROR
            blockers.append(f"{event_id}:{kind}:{blocker or 'BLOCKED'}")
    result["blockers"] = sorted(set(blockers))
    result["admitted_document_count"] = 0
    result["admitted_event_count"] = 0
    result["status"] = (
        "CAPABILITY_OR_PAID_BLOCKED"
        if any(code in item for item in blockers for code in ("SOURCE_TIMESTAMP_MISSING", "EXTRACTOR_UNAVAILABLE"))
        else "BLOCKED"
    )
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", type=Path, help="pinned official URL inventory JSON")
    parser.add_argument(
        "--legacy-json",
        "--public-legacy-json",
        dest="legacy_json",
        type=Path,
        help="local public Treasury legacy search JSON ARRAY (no network)",
    )
    parser.add_argument("--announced-rss", type=Path, help="optional local public announced RSS bytes (no network)")
    parser.add_argument(
        "--result-rss",
        "--auctioned-result-rss",
        dest="result_rss",
        type=Path,
        help="optional local public auction-result RSS bytes (no network)",
    )
    parser.add_argument(
        "--result-xml",
        dest="result_xml",
        type=Path,
        action="append",
        default=[],
        help="optional local public result XML bytes with official R_YYYYMMDD_*.xml basename; repeatable (no network)",
    )
    parser.add_argument("--output-root", type=Path, default=REPO / "data/research/r46_treasury_acquisition_2026_09_10")
    parser.add_argument("--receipt", help="relative receipt filename under --output-root")
    parser.add_argument("--fetch", action="store_true", help="opt in to official network fetches")
    parser.add_argument("--timeout", type=float, default=30.0)
    args = parser.parse_args(argv)
    if args.legacy_json is not None:
        try:
            receipt_relative = args.receipt or "evidence_receipt.json"
            result = build_local_public_evidence_receipt(
                legacy_json_path=args.legacy_json,
                announced_rss_path=args.announced_rss,
                result_rss_path=args.result_rss,
                result_xml_paths=args.result_xml,
                output_root=args.output_root,
                receipt_relative_path=receipt_relative,
            )
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            raise
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    if args.inventory is None:
        payload = {
            "schema": "r46-acquisition-route-contract-v2",
            "routes": official_source_routes(),
            "tlt_query": build_tlt_alpaca_query("split"),
            "network_attempted": False,
        }
        print(json.dumps(payload, sort_keys=True))
        return 0
    try:
        result = acquire_pinned_inventory(
            args.inventory,
            output_root=args.output_root,
            fetch=args.fetch,
            timeout=args.timeout,
        )
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        result = {
            "schema": RECEIPT_SCHEMA,
            "status": "BLOCKED_VALIDATION",
            "error_code": "INVENTORY_INVALID",
            "network_attempted": False,
            "credentials_used": False,
            "paid_source_used": False,
            "admitted_document_count": 0,
            "admitted_event_count": 0,
        }
    if args.fetch:
        receipt_relative = args.receipt or "acquisition_receipt.json"
        _atomic_json(args.output_root, receipt_relative, result)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result.get("status") == "PINNED_INVENTORY_ONLY" else 1


if __name__ == "__main__":
    raise SystemExit(main())
