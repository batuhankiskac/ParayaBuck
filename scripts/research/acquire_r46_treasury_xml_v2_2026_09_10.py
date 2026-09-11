"""Acquire execution-grade Treasury auction XML evidence for R46 v2.

The legacy Treasury inventory is identity-only and must be hash-pinned before
any XML is fetched.  This module retains official XML/XSD bytes, derives only
publication metadata and identity fields, and deliberately never copies
auction economics into normalized JSONL.  Network use is opt-in through an
injected transport or the CLI; tests use only mocked transports.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import tempfile
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, date, datetime, time, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urlparse
from xml.etree import ElementTree
from zoneinfo import ZoneInfo

import requests

REPO = Path(__file__).resolve().parents[2]
CUTOFF_UTC = "2026-07-31T23:59:59Z"
TREASURY_XML_BASE_URL = "https://www.treasurydirect.gov/xml/"
TREASURY_XSD_BASE_URL = "https://www.treasurydirect.gov/xsd/"
TREASURY_HOSTS = frozenset({"treasurydirect.gov", "www.treasurydirect.gov"})
XML_SCHEMA_INSTANCE = "http://www.w3.org/2001/XMLSchema-instance"
XML_NAMESPACE = "http://www.treasurydirect.gov/"
XML_ACQUISITION_SCHEMA = "r46-treasury-xml-acquisition-v2"
DOCUMENT_SCHEMA = "r46-treasury-auction-documents-v2"
INVENTORY_SCHEMA = "r46-treasury-acquisition-inventory-v1"
RESEARCH_ID = "r46_treasury_auction_unwind_tlt_v2_2026_09_10"
CANDIDATE_ID = "R46"
NEW_YORK = ZoneInfo("America/New_York")
RESULT_AVAILABILITY_POLICY = "official_XML_AuctionDate_plus_one_civil_day_midnight_America_New_York_for_all_results"
RESULT_SIGNAL_ELIGIBILITY = "FIRST_REGULAR_OPEN_STRICTLY_AFTER_AVAILABLE_AT"
MAX_XML_BYTES = 16 * 1024 * 1024
MAX_XSD_BYTES = 16 * 1024 * 1024
XML_SCHEMA_VALIDATOR = Path("/usr/bin/xmllint")
HEX64 = set("0123456789abcdef")
HTTP_PROVENANCE_HEADERS = frozenset(
    {
        "age",
        "cache-control",
        "content-length",
        "content-type",
        "date",
        "etag",
        "expires",
        "last-modified",
        "location",
        "server",
    }
)
FORBIDDEN_ECONOMIC_FIELDS = frozenset(
    {
        "bid_to_cover",
        "bidtocover",
        "high_yield",
        "highyield",
        "low_yield",
        "lowyield",
        "yield",
        "tail",
        "price",
        "offer_amount",
        "offering_amount",
        "bid_amount",
        "competitive_bids",
        "noncompetitive_bids",
        "awarded_amount",
        "stopout_rate",
    }
)


class TreasuryXMLContractError(ValueError):
    """Raised when official XML evidence cannot prove the v2 contract."""


def _canonical_hash(payload: Mapping[str, object], field: str) -> str:
    value = json.loads(json.dumps(dict(payload), ensure_ascii=False))
    integrity = value.get("integrity")
    if not isinstance(integrity, dict):
        raise TreasuryXMLContractError("integrity object missing")
    integrity.pop(field, None)
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _canonical_top_hash(payload: Mapping[str, object], field: str) -> str:
    value = json.loads(json.dumps(dict(payload), ensure_ascii=False))
    value.pop(field, None)
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def canonical_event_id_digest(values: Sequence[object]) -> str:
    """Hash a sorted, unique event-ID list using the receipt canonical form."""

    if isinstance(values, (str, bytes, bytearray)):
        raise TreasuryXMLContractError("event id list required")
    normalized: list[str] = []
    for value in values:
        if not isinstance(value, str) or not value.strip():
            raise TreasuryXMLContractError("event id invalid")
        normalized.append(value.strip())
    if len(set(normalized)) != len(normalized):
        raise TreasuryXMLContractError("event id list duplicate")
    encoded = json.dumps(sorted(normalized), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _canonical_event_ids(values: Sequence[object], field: str) -> list[str]:
    if isinstance(values, (str, bytes, bytearray)):
        raise TreasuryXMLContractError(f"{field} must be a list")
    normalized: list[str] = []
    for value in values:
        if not isinstance(value, str) or not value.strip():
            raise TreasuryXMLContractError(f"{field} contains invalid event id")
        normalized.append(value.strip())
    if len(set(normalized)) != len(normalized):
        raise TreasuryXMLContractError(f"{field} contains duplicate event id")
    return sorted(normalized)


def _id_set_audit(values: Sequence[object], field: str) -> dict[str, object]:
    ids = _canonical_event_ids(values, field)
    return {
        "ids": ids,
        "count": len(ids),
        "canonical_digest_sha256": canonical_event_id_digest(ids),
    }


def _parse_aware(value: object, field: str, *, cutoff: bool = True) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise TreasuryXMLContractError(f"{field} missing")
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise TreasuryXMLContractError(f"{field} invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise TreasuryXMLContractError(f"{field} timezone required")
    parsed = parsed.astimezone(UTC)
    if cutoff and parsed > datetime.fromisoformat(CUTOFF_UTC.replace("Z", "+00:00")):
        raise TreasuryXMLContractError(f"{field} post_cutoff")
    return parsed


def _safe_url(value: object, *, suffix: str | None = None) -> str:
    if not isinstance(value, str) or value != value.strip() or not value:
        raise TreasuryXMLContractError("source URL invalid")
    parsed = urlparse(value)
    if parsed.scheme != "https" or parsed.username or parsed.password or parsed.hostname not in TREASURY_HOSTS:
        raise TreasuryXMLContractError("source URL is not official HTTPS")
    try:
        port = parsed.port
    except ValueError as exc:
        raise TreasuryXMLContractError("source URL port invalid") from exc
    if port not in (None, 443):
        raise TreasuryXMLContractError("source URL port invalid")
    if parsed.query or parsed.fragment:
        raise TreasuryXMLContractError("source URL query/fragment forbidden")
    if suffix is not None and not parsed.path.lower().endswith(suffix.lower()):
        raise TreasuryXMLContractError("source URL extension invalid")
    return value


def _xml_local_name(tag: object) -> str:
    return str(tag).rsplit("}", 1)[-1]


def _xml_values(root: ElementTree.Element, name: str) -> list[str]:
    return [child.text.strip() for child in root.iter() if _xml_local_name(child.tag) == name and child.text and child.text.strip()]


def _xml_one(root: ElementTree.Element, name: str, *, required: bool = True) -> str | None:
    values = _xml_values(root, name)
    if len(values) > 1:
        raise TreasuryXMLContractError(f"XML duplicate {name}")
    if not values and required:
        raise TreasuryXMLContractError(f"XML missing {name}")
    return values[0] if values else None


def _guard_bytes(content: object, *, kind: str, limit: int) -> bytes:
    if isinstance(content, bytearray):
        content = bytes(content)
    if not isinstance(content, bytes) or not content:
        raise TreasuryXMLContractError(f"{kind} body empty")
    if len(content) > limit:
        raise TreasuryXMLContractError(f"{kind} body too large")
    upper = content.upper()
    if b"<!DOCTYPE" in upper or b"<!ENTITY" in upper:
        raise TreasuryXMLContractError(f"{kind} DOCTYPE/ENTITY forbidden")
    return content


def _inventory_rows(path: Path) -> tuple[list[dict[str, object]], str, dict[str, object]]:
    """Load the canonical inventory and return its raw file SHA for receipts."""

    if path.is_symlink() or not path.is_file():
        raise TreasuryXMLContractError("legacy inventory path invalid")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        raise TreasuryXMLContractError("legacy inventory JSON invalid") from exc
    if not isinstance(payload, dict) or payload.get("schema") not in {INVENTORY_SCHEMA, "r46-treasury-public-source-evidence-receipt-v1"}:
        raise TreasuryXMLContractError("legacy inventory schema invalid")
    integrity = payload.get("integrity")
    declared = integrity.get("inventory_sha256") if isinstance(integrity, Mapping) else None
    pin_field = "inventory_sha256"
    if declared is None:
        declared = payload.get("artifact_sha256")
        pin_field = "artifact_sha256"
    if declared is None:
        declared = payload.get("inventory_sha256")
        pin_field = "inventory_sha256"
    if not isinstance(declared, str) or len(declared) != 64 or any(char not in HEX64 for char in declared):
        raise TreasuryXMLContractError("legacy inventory must be hash-pinned")
    computed = _canonical_hash(payload, pin_field) if isinstance(integrity, Mapping) else _canonical_top_hash(payload, pin_field)
    if declared != computed:
        raise TreasuryXMLContractError("legacy inventory pin mismatch")
    events = payload.get("events")
    if not isinstance(events, list) or not events:
        raise TreasuryXMLContractError("legacy inventory events missing")
    rows: list[dict[str, object]] = []
    for event in events:
        if not isinstance(event, dict):
            raise TreasuryXMLContractError("legacy inventory event invalid")
        rows.append(dict(event))
    return rows, hashlib.sha256(path.read_bytes()).hexdigest(), payload


def _event_id(event: Mapping[str, object]) -> str:
    explicit = event.get("canonical_event_id")
    if isinstance(explicit, str) and explicit.strip():
        return explicit.strip()
    cusip = event.get("cusip")
    auction_date = event.get("auction_date", event.get("auctionDate"))
    if not isinstance(cusip, str) or not isinstance(auction_date, str):
        raise TreasuryXMLContractError("legacy event identity missing")
    return f"{auction_date}:{cusip}"


def _event_date(event: Mapping[str, object]) -> date:
    value = event.get("auction_date", event.get("auctionDate"))
    if not isinstance(value, str):
        raise TreasuryXMLContractError("legacy auction date missing")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise TreasuryXMLContractError("legacy auction date invalid")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise TreasuryXMLContractError("legacy auction date invalid") from exc
    if parsed > date.fromisoformat(CUTOFF_UTC[:10]):
        raise TreasuryXMLContractError("legacy auction date post_cutoff")
    return parsed


def _event_cusip(event: Mapping[str, object]) -> str:
    value = event.get("cusip", event.get("security_identifier"))
    if not isinstance(value, str) or not re.fullmatch(r"[A-Z0-9]{9}", value):
        raise TreasuryXMLContractError("legacy CUSIP invalid")
    return value


def _event_document_refs(event: Mapping[str, object], kind: str) -> list[dict[str, object]]:
    value = event.get(kind)
    if isinstance(value, Mapping):
        return [dict(value)]
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        refs = [dict(item) for item in value if isinstance(item, Mapping)]
        if len(refs) != len(value):
            raise TreasuryXMLContractError(f"legacy {kind} references invalid")
        return refs
    raise TreasuryXMLContractError(f"legacy {kind} reference missing")


def _reference_url(ref: Mapping[str, object], kind: str) -> str:
    value = ref.get("xml_url") or ref.get("xmlUrl")
    if not isinstance(value, str):
        filename_key = "xmlFilenameAnnouncement" if kind == "announcement" else "xmlFilenameCompetitiveResults"
        value = ref.get(filename_key)
    if not isinstance(value, str):
        raise TreasuryXMLContractError(f"legacy {kind} XML URL missing")
    if "://" in value and not value.startswith("https://"):
        raise TreasuryXMLContractError(f"legacy {kind} XML URL must be HTTPS")
    if "://" not in value:
        value = f"{TREASURY_XML_BASE_URL}{Path(value).name}"
    url = _safe_url(value, suffix=".xml")
    parsed_url = urlparse(url)
    if parsed_url.path.count("/") != 2 or not parsed_url.path.startswith("/xml/"):
        raise TreasuryXMLContractError(f"legacy {kind} XML path invalid")
    filename = Path(parsed_url.path).name
    prefix = "A_" if kind == "announcement" else "R_"
    if not filename.upper().startswith(prefix) or not re.fullmatch(r"[AR]_\d{8}_\d+\.xml", filename, flags=re.IGNORECASE):
        raise TreasuryXMLContractError(f"legacy {kind} XML basename invalid")
    return url


def _schema_provenance(root: ElementTree.Element) -> tuple[str, str, str]:
    schema_location = root.attrib.get(f"{{{XML_SCHEMA_INSTANCE}}}schemaLocation")
    if not isinstance(schema_location, str) or not schema_location.strip():
        raise TreasuryXMLContractError("XML schemaLocation missing")
    parts = schema_location.split()
    if len(parts) != 2 or parts[0] != XML_NAMESPACE:
        raise TreasuryXMLContractError("XML schemaLocation invalid")
    schema_reference = parts[1]
    # Treasury's legacy XML commonly embeds an HTTP XSD URL even though the
    # official XSD route is available over HTTPS.  Preserve the raw
    # schemaLocation above, but canonicalize only that exact scheme here so
    # acquisition remains HTTPS-only and schema validation can proceed.
    if schema_reference.startswith("http://"):
        schema_reference = "https://" + schema_reference[len("http://") :]
    schema_url = _safe_url(schema_reference, suffix=".xsd")
    if not urlparse(schema_url).path.startswith("/xsd/"):
        raise TreasuryXMLContractError("XML schemaLocation path invalid")
    match = re.fullmatch(r"Auction_(v\d+_\d+_\d+)\.xsd", Path(urlparse(schema_url).path).name, flags=re.IGNORECASE)
    if match is None:
        raise TreasuryXMLContractError("XML schema version invalid")
    return schema_location, schema_url, match.group(1)


def _parse_xml(
    content: bytes,
    source_url: str,
    *,
    kind: str,
    event: Mapping[str, object],
    reference: Mapping[str, object] | None = None,
    retrieved_at: str,
) -> tuple[dict[str, object], str, str]:
    content = _guard_bytes(content, kind=f"{kind} XML", limit=MAX_XML_BYTES)
    source_url = _reference_url({"xml_url": source_url}, kind)
    try:
        root = ElementTree.fromstring(content)
    except ElementTree.ParseError as exc:
        raise TreasuryXMLContractError(f"{kind} XML malformed") from exc
    schema_location, schema_url, schema_version = _schema_provenance(root)
    cusip = _xml_one(root, "CUSIP")
    auction_date_text = _xml_one(root, "AuctionDate")
    if cusip != _event_cusip(event):
        raise TreasuryXMLContractError(f"{kind} XML CUSIP identity mismatch")
    event_date = _event_date(event)
    if auction_date_text != event_date.isoformat():
        raise TreasuryXMLContractError(f"{kind} XML AuctionDate identity mismatch")
    release_time = _xml_one(root, "ReleaseTime", required=False)
    result_release_time_missing = False
    availability: datetime | None = None
    if kind == "result":
        availability = datetime.combine(event_date + timedelta(days=1), time.min, tzinfo=NEW_YORK)
        if release_time is None:
            # Older official result XMLs contain an empty ReleaseTime element.
            # Preserve the document, but do not invent a publication instant.
            # The runner must apply the explicit next-session-open policy.
            result_release_time_missing = True
            publication = None
            publication_source = "official_XML_release_time_missing"
        elif not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d(?::[0-5]\d)?", release_time):
            raise TreasuryXMLContractError("result XML ReleaseTime invalid")
        else:
            parts = [int(part) for part in release_time.split(":")]
            release = datetime.combine(event_date, time(parts[0], parts[1], parts[2] if len(parts) == 3 else 0), tzinfo=NEW_YORK)
            if release.time() > time(16, 15):
                raise TreasuryXMLContractError("result ReleaseTime after 16:15 America/New_York")
            publication = release
            publication_source = "official_release_timestamp"
        result_pdf = _xml_one(root, "ResultsPDFName")
        if result_pdf is None or not re.fullmatch(r"R_\d{8}_\d+\.pdf", result_pdf, flags=re.IGNORECASE):
            raise TreasuryXMLContractError("result XML ResultsPDFName invalid")
        result_name_date = Path(urlparse(source_url).path).stem.split("_")[1]
        if result_name_date != event_date.strftime("%Y%m%d"):
            raise TreasuryXMLContractError("result XML filename/date identity mismatch")
    else:
        # Inventory publication is deliberately not treated as a timestamp.
        # The only conservative claim is end-of-day on the calendar day before
        # the auction, which is necessarily before the auction session.
        publication = datetime.combine(event_date - timedelta(days=1), time(23, 59, 59), tzinfo=NEW_YORK)
        publication_source = "official_archive_metadata"
        announcement_name_date = Path(urlparse(source_url).path).stem.split("_")[1]
        if announcement_name_date > event_date.strftime("%Y%m%d"):
            raise TreasuryXMLContractError("announcement XML filename after auction day")
    parsed_retrieved = _parse_aware(retrieved_at, "acquisition_time", cutoff=False)
    current_ref = dict(reference) if reference is not None else _event_document_refs(event, kind)[0]
    revision_id = current_ref.get("revision_id", "original")
    supersedes = current_ref.get("supersedes_document_id")
    if not isinstance(revision_id, str) or not revision_id.strip():
        raise TreasuryXMLContractError(f"{kind} revision_id missing")
    if supersedes is not None and (not isinstance(supersedes, str) or not supersedes.strip()):
        raise TreasuryXMLContractError(f"{kind} supersedes_document_id invalid")
    chain = current_ref.get("correction_or_amendment_chain", [])
    if not isinstance(chain, list):
        raise TreasuryXMLContractError(f"{kind} correction lineage invalid")
    issue_or_reopening = event.get("issue_or_reopening", "original")
    if issue_or_reopening not in {"original", "reopening"}:
        raise TreasuryXMLContractError("legacy issue_or_reopening invalid")
    document_id = f"{kind}-{Path(urlparse(source_url).path).stem}-{revision_id.strip()}"
    record: dict[str, object] = {
        "schema": DOCUMENT_SCHEMA,
        "document_id": document_id,
        "document_kind": kind,
        "canonical_event_id": _event_id(event),
        "source_url": source_url,
        "raw_sha256": hashlib.sha256(content).hexdigest(),
        "sha256": hashlib.sha256(content).hexdigest(),
        "security_type": "nominal",
        "instrument_type": "US_TREASURY_SECURITY",
        "issuer_identifier": "US_TREASURY",
        "original_tenor": 30,
        "issue_or_reopening": issue_or_reopening,
        "security_identifier": str(cusip),
        "auction_session_date": event_date.isoformat(),
        "published_at": publication.isoformat() if publication is not None else None,
        "available_at": (availability if kind == "result" else publication).isoformat(),  # type: ignore[union-attr]
        "source_timezone": "America/New_York",
        "publication_at_source": publication_source,
        "retrieved_at": parsed_retrieved.isoformat().replace("+00:00", "Z"),
        "revision_id": revision_id.strip(),
        "supersedes_document_id": supersedes,
        "correction_or_amendment_chain": chain,
        "schema_location": schema_location,
        "schema_version": schema_version,
        "xsd_source_url": schema_url,
        "release_time": release_time,
        "publication_time_proven": not result_release_time_missing if kind == "result" else True,
        "economic_fields_filtered": True,
    }
    if kind == "result":
        record["official_publication_date"] = event_date.isoformat()
        record["availability_derivation"] = RESULT_AVAILABILITY_POLICY
        record["result_signal_eligibility"] = RESULT_SIGNAL_ELIGIBILITY
        record["same_day_signal_allowed"] = False
        record["published_at_evidence_only"] = True
        record["eligibility_anchor_date"] = event_date.isoformat()
        record["result_status"] = current_ref.get("result_status", "COMPLETED")
        if record["result_status"] not in {"COMPLETED", "COMPLETED_WITH_AMENDMENT"}:
            raise TreasuryXMLContractError("result status is not completed")
    if any(key.casefold() in FORBIDDEN_ECONOMIC_FIELDS for key in record):
        raise TreasuryXMLContractError("normalized document contains forbidden economic field")
    return record, schema_url, schema_version


def normalize_treasury_xml_v2(
    content: bytes,
    source_url: str,
    *,
    kind: str,
    event: Mapping[str, object],
    acquisition_time: str,
    reference: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Normalize one official XML response without writing or fetching it."""

    record, _, _ = _parse_xml(
        content,
        source_url,
        kind=kind,
        event=event,
        reference=reference,
        retrieved_at=acquisition_time,
    )
    return record


parse_treasury_xml_v2 = normalize_treasury_xml_v2


def _response(result: object) -> tuple[int, object, Mapping[str, str]]:
    if isinstance(result, tuple):
        if len(result) == 2:
            status, body = result
            headers: Mapping[str, str] = {}
        elif len(result) == 3:
            status, body, raw_headers = result
            headers = raw_headers if isinstance(raw_headers, Mapping) else {}
        else:
            raise TreasuryXMLContractError("transport response invalid")
        if not isinstance(status, int):
            raise TreasuryXMLContractError("transport status invalid")
        return status, body, headers
    status = getattr(result, "status_code", None)
    if not isinstance(status, int):
        raise TreasuryXMLContractError("transport status missing")
    headers = getattr(result, "headers", {})
    body = getattr(result, "content", None)
    return status, body, headers if isinstance(headers, Mapping) else {}


def _requests_transport(url: str, *, params: dict[str, object], headers: dict[str, str], timeout: float) -> tuple[int, object, Mapping[str, str]]:
    response = requests.get(url, params=params, headers=headers, timeout=timeout, allow_redirects=False)
    return response.status_code, response.content, response.headers


def _fetch_bytes(
    url: str,
    *,
    transport: Callable[..., object],
    max_redirects: int = 3,
) -> tuple[bytes, str, Mapping[str, str], tuple[str, ...]]:
    current = _safe_url(url, suffix=".xml") if url.lower().endswith(".xml") else _safe_url(url, suffix=".xsd")
    original = urlparse(current)
    redirect_chain = [current]
    for _ in range(max_redirects + 1):
        try:
            result = transport(current, params={}, headers={}, timeout=45.0)
            status, body, headers = _response(result)
        except TreasuryXMLContractError:
            raise
        except Exception as exc:
            raise TreasuryXMLContractError(f"transport {type(exc).__name__}") from exc
        if status in {301, 302, 303, 307, 308}:
            location = body.get("location") if isinstance(body, Mapping) else headers.get("Location") or headers.get("location")
            if not isinstance(location, str):
                raise TreasuryXMLContractError("redirect location missing")
            parsed = urlparse(location)
            if parsed.scheme != original.scheme or parsed.path != original.path or parsed.query or parsed.fragment or parsed.hostname not in TREASURY_HOSTS:
                raise TreasuryXMLContractError("redirect path must remain exact")
            current = _safe_url(location, suffix=Path(original.path).suffix)
            redirect_chain.append(current)
            continue
        if status != 200:
            raise TreasuryXMLContractError(f"HTTP {status}")
        return _guard_bytes(body, kind="official XML/XSD", limit=MAX_XML_BYTES), current, headers, tuple(redirect_chain)
    raise TreasuryXMLContractError("redirect limit")


def _response_headers(headers: Mapping[str, str]) -> dict[str, str]:
    """Retain only non-secret HTTP provenance headers from official responses."""

    retained: dict[str, str] = {}
    for key, value in headers.items():
        if not isinstance(key, str) or not isinstance(value, str):
            continue
        if key.casefold() in HTTP_PROVENANCE_HEADERS:
            retained[key] = value
    return dict(sorted(retained.items(), key=lambda item: item[0].casefold()))


def _header_value(headers: Mapping[str, str], name: str) -> str | None:
    wanted = name.casefold()
    for key, value in headers.items():
        if isinstance(key, str) and key.casefold() == wanted and isinstance(value, str):
            return value
    return None


def _http_metadata(
    *,
    source_url: str,
    final_url: str,
    redirect_chain: Sequence[str],
    body: bytes,
    headers: Mapping[str, str],
    kind: str,
    acquisition_time: str,
) -> dict[str, object]:
    """Build a hash-linked, cutoff-bounded HTTP metadata record.

    Last-Modified is retained as server-file provenance only.  It is never
    promoted to a Treasury publication timestamp because the server does not
    attest that meaning for these legacy XML routes.
    """

    raw_headers = _response_headers(headers)
    last_modified_raw = _header_value(raw_headers, "Last-Modified")
    last_modified_utc: str | None = None
    if last_modified_raw:
        try:
            parsed = parsedate_to_datetime(last_modified_raw)
            if parsed.tzinfo is not None and parsed.utcoffset() is not None:
                last_modified_utc = parsed.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
        except (TypeError, ValueError, OverflowError):
            last_modified_utc = None
    cutoff = datetime.fromisoformat(CUTOFF_UTC.replace("Z", "+00:00"))
    parsed_last_modified = datetime.fromisoformat(last_modified_utc.replace("Z", "+00:00")) if last_modified_utc else None
    base: dict[str, object] = {
        "kind": kind,
        "source_url": source_url,
        "final_url": final_url,
        "redirect_chain": list(redirect_chain),
        "http_status": 200,
        "body_sha256": hashlib.sha256(body).hexdigest(),
        "body_bytes": len(body),
        "response_headers": raw_headers,
        "last_modified_raw": last_modified_raw,
        "last_modified_utc": last_modified_utc,
        "last_modified_cutoff_bounded": parsed_last_modified is not None and parsed_last_modified <= cutoff,
        "last_modified_is_publication_proof": False,
        "last_modified_semantic": "server_file_modification_not_publication_timestamp",
        "http_date_is_publication_proof": False,
        "retrieval_time_is_publication_proof": False,
        "acquisition_time": acquisition_time,
    }
    encoded = json.dumps(base, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    base["metadata_id"] = hashlib.sha256(encoded).hexdigest()
    return base


def _atomic_publish(path: Path, content: bytes) -> None:
    cursor = path
    while cursor != Path(cursor.anchor):
        if cursor.is_symlink():
            raise TreasuryXMLContractError("publication path symlink forbidden")
        cursor = cursor.parent
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() or path.is_symlink():
        raise FileExistsError(path)
    temporary_name: str | None = None
    try:
        descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary_name, path)
        os.unlink(temporary_name)
        temporary_name = None
    finally:
        if temporary_name is not None:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass


def _validate_xml_against_xsd(xml_path: Path, xsd_path: Path) -> dict[str, object]:
    """Validate one pinned XML/XSD pair without permitting validator network I/O."""

    tool = XML_SCHEMA_VALIDATOR
    base: dict[str, object] = {"validation_tool": str(tool)}
    if not tool.is_file() or not os.access(tool, os.X_OK):
        return {**base, "validation_status": "PROVENANCE_PINNED_NOT_SCHEMA_VALIDATED", "validation_reason": "xmllint_unavailable"}
    try:
        version = subprocess.run(
            [str(tool), "--version"],
            capture_output=True,
            check=False,
            text=True,
            timeout=10,
        )
        version_output = (version.stdout or version.stderr or "").splitlines()
        if version.returncode != 0 or not version_output:
            return {**base, "validation_status": "PROVENANCE_PINNED_NOT_SCHEMA_VALIDATED", "validation_reason": "xmllint_version_unavailable"}
        validation = subprocess.run(
            [str(tool), "--nonet", "--noout", "--schema", str(xsd_path), str(xml_path)],
            capture_output=True,
            check=False,
            text=True,
            timeout=45,
        )
    except (OSError, subprocess.SubprocessError):
        return {**base, "validation_status": "PROVENANCE_PINNED_NOT_SCHEMA_VALIDATED", "validation_reason": "xmllint_execution_failed"}
    result: dict[str, object] = {
        **base,
        "validation_tool_version": version_output[0][:160],
        "validation_returncode": validation.returncode,
    }
    if validation.returncode != 0:
        result["validation_status"] = "SCHEMA_VALIDATION_FAILED"
        result["validation_reason"] = "xmllint_nonzero_exit"
        return result
    result["validation_status"] = "SCHEMA_VALIDATED"
    return result


def _ensure_output_dir(path: Path) -> Path:
    cursor = path
    while cursor != Path(cursor.anchor):
        if cursor.is_symlink():
            raise TreasuryXMLContractError("output directory symlink forbidden")
        cursor = cursor.parent
    path.mkdir(parents=True, exist_ok=True)
    return path.resolve(strict=True)


def acquire_r46_treasury_xml_v2(
    inventory_path: Path,
    *,
    output_dir: Path,
    acquisition_time: str,
    transport: Callable[..., object] | None = None,
) -> dict[str, object]:
    """Fetch, validate, and atomically publish pinned Treasury XML evidence."""

    output = _ensure_output_dir(Path(output_dir))
    acquired = _parse_aware(acquisition_time, "acquisition_time", cutoff=False)
    inventory_rows, inventory_raw_hash, inventory_payload = _inventory_rows(Path(inventory_path))
    expected_ids = _canonical_event_ids([_event_id(event) for event in inventory_rows], "inventory event ids")
    fetch = transport or _requests_transport
    records: list[dict[str, object]] = []
    raw_artifacts: list[dict[str, object]] = []
    xsd_artifacts: dict[str, dict[str, object]] = {}
    fetched_urls: list[str] = []
    http_metadata_rows: list[dict[str, object]] = []
    for event in inventory_rows:
        event_id = _event_id(event)
        for kind in ("announcement", "result"):
            refs = _event_document_refs(event, kind)
            seen_revisions: set[str] = set()
            for reference in refs:
                source_url = _reference_url(reference, kind)
                body, final_url, response_headers, redirect_chain = _fetch_bytes(source_url, transport=fetch)
                fetched_urls.append(final_url)
                metadata = _http_metadata(
                    source_url=source_url,
                    final_url=final_url,
                    redirect_chain=redirect_chain,
                    body=body,
                    headers=response_headers,
                    kind=f"{kind}_xml",
                    acquisition_time=acquired.isoformat().replace("+00:00", "Z"),
                )
                http_metadata_rows.append(metadata)
                event_for_reference = dict(event)
                event_for_reference[kind] = reference
                record, schema_url, schema_version = _parse_xml(
                    body,
                    final_url,
                    kind=kind,
                    event=event_for_reference,
                    reference=reference,
                    retrieved_at=acquired.isoformat().replace("+00:00", "Z"),
                )
                if record["canonical_event_id"] != event_id:
                    raise TreasuryXMLContractError("XML canonical event identity mismatch")
                revision_id = str(record["revision_id"])
                if revision_id in seen_revisions:
                    raise TreasuryXMLContractError(f"{kind} duplicate revision")
                seen_revisions.add(revision_id)
                filename = Path(urlparse(final_url).path).name
                raw_path = output / "raw_xml" / f"{revision_id}-{filename}"
                _atomic_publish(raw_path, body)
                record["raw_path"] = str(raw_path.relative_to(output))
                record["http_metadata_id"] = metadata["metadata_id"]
                records.append(record)
                if schema_url not in xsd_artifacts:
                    xsd_body, xsd_final_url, xsd_headers, xsd_redirect_chain = _fetch_bytes(schema_url, transport=fetch)
                    xsd_metadata = _http_metadata(
                        source_url=schema_url,
                        final_url=xsd_final_url,
                        redirect_chain=xsd_redirect_chain,
                        body=xsd_body,
                        headers=xsd_headers,
                        kind="xsd",
                        acquisition_time=acquired.isoformat().replace("+00:00", "Z"),
                    )
                    http_metadata_rows.append(xsd_metadata)
                    xsd_path = output / "raw_xsd" / Path(urlparse(xsd_final_url).path).name
                    _atomic_publish(xsd_path, xsd_body)
                    version_match = re.search(r"version\s*=\s*[\"']([^\"']+)", xsd_body.decode("utf-8", errors="ignore"))
                    xsd_artifacts[schema_url] = {
                        "source_url": xsd_final_url,
                        "path": str(xsd_path.relative_to(output)),
                        "sha256": hashlib.sha256(xsd_body).hexdigest(),
                        "schema_version": schema_version,
                        "declared_version": version_match.group(1) if version_match else None,
                        "validation_status": "PROVENANCE_PINNED_NOT_SCHEMA_VALIDATED",
                        "validation_results": [],
                        "http_metadata_id": xsd_metadata["metadata_id"],
                    }
                record["xsd_source_url"] = xsd_artifacts[schema_url]["source_url"]
                record["xsd_sha256"] = xsd_artifacts[schema_url]["sha256"]
                xsd_path = output / str(xsd_artifacts[schema_url]["path"])
                validation = _validate_xml_against_xsd(raw_path, xsd_path)
                validation_results = xsd_artifacts[schema_url]["validation_results"]
                if not isinstance(validation_results, list):
                    raise TreasuryXMLContractError("XSD validation result lineage invalid")
                validation_results.append({"document_id": record["document_id"], **validation})
                statuses = [str(item.get("validation_status")) for item in validation_results if isinstance(item, Mapping)]
                if statuses and all(status == "SCHEMA_VALIDATED" for status in statuses):
                    xsd_artifacts[schema_url]["validation_status"] = "SCHEMA_VALIDATED"
                    xsd_artifacts[schema_url]["validation_tool"] = validation.get("validation_tool")
                    xsd_artifacts[schema_url]["validation_tool_version"] = validation.get("validation_tool_version")
                elif any(status == "SCHEMA_VALIDATION_FAILED" for status in statuses):
                    xsd_artifacts[schema_url]["validation_status"] = "SCHEMA_VALIDATION_FAILED"
                else:
                    xsd_artifacts[schema_url]["validation_status"] = "PROVENANCE_PINNED_NOT_SCHEMA_VALIDATED"
                raw_artifacts.append(
                    {
                        "document_id": record["document_id"],
                        "document_kind": kind,
                        "canonical_event_id": record["canonical_event_id"],
                        "path": record["raw_path"],
                        "sha256": record["raw_sha256"],
                        "source_url": final_url,
                        "schema_version": schema_version,
                        "revision_id": record["revision_id"],
                        "supersedes_document_id": record["supersedes_document_id"],
                        "http_metadata_id": metadata["metadata_id"],
                    }
                )
    records.sort(key=lambda row: (str(row["canonical_event_id"]), str(row["document_kind"]), str(row["document_id"])))
    announcement_ids = _canonical_event_ids(
        [row["canonical_event_id"] for row in records if row.get("document_kind") == "announcement"],
        "announcement event ids",
    )
    result_ids = _canonical_event_ids(
        [row["canonical_event_id"] for row in records if row.get("document_kind") == "result"],
        "result event ids",
    )
    if announcement_ids != expected_ids or result_ids != expected_ids:
        raise TreasuryXMLContractError("normalized announcement/result event IDs do not match inventory")
    missing_release_ids = _canonical_event_ids(
        [
            row["canonical_event_id"]
            for row in records
            if row.get("document_kind") == "result"
            and row.get("publication_time_proven") is False
            and row.get("published_at") is None
            and row.get("release_time") in (None, "")
        ],
        "missing release event ids",
    )
    all_result_ids = _canonical_event_ids(
        [row["canonical_event_id"] for row in records if row.get("document_kind") == "result"],
        "result event ids",
    )
    result_rows_with_missing_release = {
        str(row["canonical_event_id"])
        for row in records
        if row.get("document_kind") == "result"
        and row.get("publication_time_proven") is False
        and row.get("published_at") is None
        and row.get("release_time") in (None, "")
    }
    if not result_rows_with_missing_release.issubset(set(all_result_ids)):
        raise TreasuryXMLContractError("missing release event id audit invalid")
    documents_content = "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in records).encode("utf-8")
    _atomic_publish(output / "documents.jsonl", documents_content)
    http_metadata_rows.sort(key=lambda row: str(row["metadata_id"]))
    http_metadata_content = "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in http_metadata_rows).encode("utf-8")
    _atomic_publish(output / "http_metadata.jsonl", http_metadata_content)
    acquisition_audit = {
        "inventory_event_ids": _id_set_audit(expected_ids, "inventory event ids"),
        "announcement_event_ids": _id_set_audit(announcement_ids, "announcement event ids"),
        "result_event_ids": _id_set_audit(result_ids, "result event ids"),
        "missing_release_time_event_ids": _id_set_audit(missing_release_ids, "missing release event ids"),
        "event_id_sets_equal": announcement_ids == expected_ids == result_ids,
        "canonicalization": "sorted unique UTF-8 JSON list; SHA-256 of compact JSON list",
    }
    receipt: dict[str, object] = {
        "schema": XML_ACQUISITION_SCHEMA,
        "research_id": RESEARCH_ID,
        "candidate_id": CANDIDATE_ID,
        "status": "COMPLETE",
        "blockers": [],
        "economic_execution": False,
        "economic_run": False,
        "historical_cutoff": CUTOFF_UTC,
        "acquisition_time": acquired.isoformat().replace("+00:00", "Z"),
        "inventory": {
            "path": str(Path(inventory_path).resolve()),
            "sha256": inventory_raw_hash,
            "schema": inventory_payload["schema"],
            "pinned": True,
        },
        "expected_event_count": len(expected_ids),
        "expected_event_ids": expected_ids,
        "acquisition_audit": acquisition_audit,
        "documents_path": "documents.jsonl",
        "documents_sha256": hashlib.sha256(documents_content).hexdigest(),
        "http_metadata_path": "http_metadata.jsonl",
        "http_metadata_sha256": hashlib.sha256(http_metadata_content).hexdigest(),
        "http_metadata_count": len(http_metadata_rows),
        "raw_artifacts": raw_artifacts,
        "xsd": list(xsd_artifacts.values()),
        "source_routes": sorted(set(fetched_urls + list(xsd_artifacts))),
        "publication_basis": {
            "announcement": "conservative_end_of_day_preceding_auction_day",
            "result": RESULT_AVAILABILITY_POLICY,
        },
        "publication_evidence": {
            "result_release_time_missing_event_ids": missing_release_ids,
            "result_release_time_missing_event_count": len(missing_release_ids),
            "result_release_time_missing_event_ids_canonical_digest_sha256": canonical_event_id_digest(missing_release_ids),
            "official_publication_date_basis": "result_XML_AuctionDate",
            "availability_derivation": RESULT_AVAILABILITY_POLICY,
            "server_last_modified_is_publication_proof": False,
            "http_date_is_publication_proof": False,
            "retrieval_time_is_publication_proof": False,
            "server_last_modified_semantic": "server_file_modification_not_publication_timestamp",
            "next_session_open_policy": RESULT_SIGNAL_ELIGIBILITY,
            "same_day_result_signals_allowed": False,
            "same_day": False,
        },
        "economic_fields_filtered": True,
        "integrity": {
            "canonicalization": "UTF-8 JSON sorted object keys compact separators ensure_ascii=false; omit integrity.receipt_sha256",
            "receipt_sha256": "",
        },
    }
    receipt["integrity"]["receipt_sha256"] = _canonical_hash(receipt, "receipt_sha256")  # type: ignore[index]
    receipt_content = (json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
    _atomic_publish(output / "receipt.json", receipt_content)
    return receipt


ingest_treasury_xml_v2 = acquire_r46_treasury_xml_v2
acquire_treasury_xml_v2 = acquire_r46_treasury_xml_v2


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("inventory", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--acquisition-time", required=True)
    args = parser.parse_args(argv)
    receipt = acquire_r46_treasury_xml_v2(
        args.inventory,
        output_dir=args.output_dir,
        acquisition_time=args.acquisition_time,
    )
    print(json.dumps({"schema": receipt["schema"], "status": receipt["status"], "expected_event_count": receipt["expected_event_count"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
