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
import json
import os
import re
import sys
import tempfile
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

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
ALPACA_BARS_URL = "https://data.alpaca.markets/v2/stocks/bars"
START = "2016-01-01"
END_EXCLUSIVE = "2026-08-01"
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
    parser.add_argument("--output-root", type=Path, default=REPO / "data/research/r46_treasury_acquisition_2026_09_10")
    parser.add_argument("--receipt", help="relative receipt filename under --output-root when fetching")
    parser.add_argument("--fetch", action="store_true", help="opt in to official network fetches")
    parser.add_argument("--timeout", type=float, default=30.0)
    args = parser.parse_args(argv)
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
