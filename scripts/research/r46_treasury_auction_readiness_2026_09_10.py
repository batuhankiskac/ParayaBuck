"""R46 Treasury auction/TLT data and PIT readiness gates.

This module deliberately stops before any economic calculation.  It validates
the source lineage, point-in-time timestamps, TLT market-data accounting, and
the shared-capital/holdout contract needed before an independent verifier may
run R46.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections import defaultdict
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from scripts.research.acquire_r46_treasury_xml_v2_2026_09_10 import (
    RESULT_AVAILABILITY_POLICY,
    RESULT_SIGNAL_ELIGIBILITY,
)

CUTOFF_UTC = "2026-07-31T23:59:59Z"
AUCTION_CUTOFF_LOCAL = "16:15 America/New_York"
AUCTION_CUTOFF_TIME = time(16, 15)
NEW_YORK = ZoneInfo("America/New_York")
GOVERNANCE_SCHEMA = "r46-governance-manifest-v1"
DATA_PACKAGE_SCHEMA = "r46-treasury-auction-unwind-data-package-v1"
NYSE_CALENDAR_SCHEMA = "r46-nyse-regular-calendar-v1"
R46_SCHEMA = "r46-treasury-auction-unwind-readiness-v1"
AUCTION_SCHEMA = "r46-treasury-auction-ledger-v1"
TLT_BARS_SCHEMA = "r46-tlt-split-adjusted-bars-v1"
TLT_DIVIDENDS_SCHEMA = "r46-tlt-separate-dividends-v1"
TLT_ELIGIBILITY_SCHEMA = "r46-tlt-pit-eligibility-v1"
HEX64_RE = re.compile(r"^[0-9a-f]{64}$")
CUSIP_RE = re.compile(r"^[A-Z0-9]{9}$")
OFFICIAL_DOCUMENT_HOSTS = {
    "treasurydirect.gov",
    "www.treasurydirect.gov",
    "fiscaldata.treasury.gov",
    "home.treasury.gov",
}
ELIGIBILITY_SOURCE_HOSTS = OFFICIAL_DOCUMENT_HOSTS | {
    "data.alpaca.markets",
    "www.ishares.com",
}
ALLOWED_IDENTITY_BASES = {
    "historical_provider_asset_map",
    "exchange_archive",
    "issuer_historical_filing",
}
ALLOWED_PUBLICATION_SOURCES = {
    "treasury_document_header",
    "official_archive_metadata",
    "official_release_timestamp",
    "official_XML_release_time_missing",
}


def sha256(path: Path) -> str:
    """Return the hash used by every R46 first-class artifact."""

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _parse_aware(value: Any, field: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} publication_at missing" if field == "publication_at" else f"{field} missing")
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field} timestamp invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field} timezone required")
    return parsed.astimezone(UTC)


def _parse_date(value: Any, field: str) -> date:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} missing")
    try:
        return date.fromisoformat(value.strip())
    except ValueError as exc:
        raise ValueError(f"{field} invalid") from exc


def _official_url(value: Any, *, hosts: set[str]) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ValueError("source_url invalid")
    parsed = urlparse(value)
    if parsed.scheme != "https" or parsed.username or parsed.password or not parsed.netloc:
        raise ValueError("source_url must be HTTPS without credentials")
    if parsed.hostname not in hosts:
        raise ValueError("source_url is not an allowed official source")
    return value


def _safe_relative_file(root: Path, value: Any, field: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} missing")
    relative = Path(value)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError(f"{field} unsafe path")
    root_resolved = root.resolve()
    path = root / relative
    try:
        resolved = path.resolve(strict=True)
    except (OSError, ValueError) as exc:
        raise ValueError(f"{field} missing file") from exc
    if root_resolved not in resolved.parents or not resolved.is_file():
        raise ValueError(f"{field} missing file")
    return resolved


def _cutoff_for_session(auction_date: date) -> datetime:
    return datetime.combine(auction_date, AUCTION_CUTOFF_TIME, tzinfo=NEW_YORK).astimezone(UTC)


def _result_available_at(auction_date: date) -> datetime:
    """Return the policy boundary using civil-day arithmetic in New York."""

    return datetime.combine(auction_date + timedelta(days=1), time.min, tzinfo=NEW_YORK)


def _validate_document(document: dict[str, Any], *, repo_root: Path) -> dict[str, Any]:
    required = {
        "document_id",
        "document_kind",
        "canonical_event_id",
        "source_url",
        "raw_path",
        "sha256",
        "security_type",
        "instrument_type",
        "issuer_identifier",
        "original_tenor",
        "issue_or_reopening",
        "security_identifier",
        "auction_session_date",
        "published_at",
        "available_at",
        "source_timezone",
        "publication_at_source",
        "retrieved_at",
        "revision_id",
        "supersedes_document_id",
        "correction_or_amendment_chain",
    }
    if not isinstance(document, dict) or not required.issubset(document):
        raise ValueError("auction document schema missing required field")
    kind = document["document_kind"]
    if kind not in {"announcement", "result"}:
        raise ValueError("document_kind invalid")
    document_id = document["document_id"]
    event_id = document["canonical_event_id"]
    if not isinstance(document_id, str) or not document_id.strip() or not isinstance(event_id, str) or not event_id.strip():
        raise ValueError("document identity missing")
    _official_url(document["source_url"], hosts=OFFICIAL_DOCUMENT_HOSTS)
    raw_path = _safe_relative_file(repo_root, document["raw_path"], "raw_path")
    digest = document["sha256"]
    if not isinstance(digest, str) or not HEX64_RE.fullmatch(digest):
        raise ValueError("document sha256 invalid")
    if sha256(raw_path) != digest:
        raise ValueError(f"document hash mismatch:{document_id}")
    if document["security_type"] != "nominal" or not isinstance(document["instrument_type"], str) or not document["instrument_type"].strip():
        raise ValueError("R46 requires nominal security")
    if not isinstance(document["issuer_identifier"], str) or not document["issuer_identifier"].strip():
        raise ValueError("issuer_identifier missing")
    if isinstance(document["original_tenor"], bool) or float(document["original_tenor"]) != 30.0:
        raise ValueError("R46 requires 30Y")
    if document["issue_or_reopening"] not in {"original", "reopening"}:
        raise ValueError("auction_type invalid")
    security_identifier = document["security_identifier"]
    if not isinstance(security_identifier, str) or not CUSIP_RE.fullmatch(security_identifier):
        raise ValueError("security_identifier invalid")
    auction_date = _parse_date(document["auction_session_date"], "auction_session_date")
    cutoff_date = _parse_aware(CUTOFF_UTC, "historical_cutoff").date()
    if auction_date > cutoff_date:
        raise ValueError("auction_date post_cutoff")
    if not isinstance(document["source_timezone"], str) or not document["source_timezone"].strip():
        raise ValueError("source_timezone missing")
    if kind == "result":
        if document.get("official_publication_date") != auction_date.isoformat():
            raise ValueError("result official_publication_date mismatch")
        if document.get("availability_derivation") != RESULT_AVAILABILITY_POLICY:
            raise ValueError("result availability policy mismatch")
        if document.get("result_signal_eligibility") != RESULT_SIGNAL_ELIGIBILITY:
            raise ValueError("result signal eligibility mismatch")
        if document.get("same_day_signal_allowed") is not False:
            raise ValueError("result same-day signal policy mismatch")
        if document["source_timezone"] != "America/New_York":
            raise ValueError("result source_timezone policy mismatch")
        available_at = _parse_aware(document["available_at"], "available_at")
        expected_available_at = _result_available_at(auction_date).astimezone(UTC)
        if available_at != expected_available_at:
            raise ValueError("result available_at policy mismatch")
        publication_time_proven = document.get("publication_time_proven")
        if not isinstance(publication_time_proven, bool):
            raise ValueError("result publication_time_proven missing")
        release_time = document.get("release_time")
        if publication_time_proven:
            published_at = _parse_aware(document["published_at"], "published_at")
            if not isinstance(release_time, str) or not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d(?::[0-5]\d)?", release_time):
                raise ValueError("result release_time evidence missing")
            if document["publication_at_source"] != "official_release_timestamp":
                raise ValueError("result publication_at_source mismatch")
            parts = [int(part) for part in release_time.split(":")]
            release_local = datetime.combine(
                auction_date,
                time(parts[0], parts[1], parts[2] if len(parts) == 3 else 0),
                tzinfo=NEW_YORK,
            )
            if release_local.time() > AUCTION_CUTOFF_TIME:
                raise ValueError("result release_time after auction cutoff")
            if published_at != release_local.astimezone(UTC):
                raise ValueError("result published_at release_time mismatch")
        else:
            if document["published_at"] is not None or release_time not in (None, ""):
                raise ValueError("result published_at missing-release mismatch")
            published_at = None
            if document["publication_at_source"] != "official_XML_release_time_missing":
                raise ValueError("result publication_at_source mismatch")
    else:
        published_at = _parse_aware(document["published_at"], "published_at")
        available_at = _parse_aware(document["available_at"], "available_at")
        if available_at < published_at:
            raise ValueError("available_at precedes published_at")
        session_cutoff = _cutoff_for_session(auction_date)
        if available_at > session_cutoff:
            raise ValueError("auction_cutoff available_at after 16:15 America/New_York")
    retrieved_at = _parse_aware(document["retrieved_at"], "retrieved_at")
    if document["publication_at_source"] not in ALLOWED_PUBLICATION_SOURCES:
        raise ValueError("publication_at_source missing")
    revision_id = document["revision_id"]
    if not isinstance(revision_id, str) or not revision_id.strip():
        raise ValueError("revision_id missing")
    supersedes = document["supersedes_document_id"]
    if supersedes is not None and (not isinstance(supersedes, str) or not supersedes.strip()):
        raise ValueError("supersedes_document_id invalid")
    chain = document["correction_or_amendment_chain"]
    if not isinstance(chain, list):
        raise ValueError("correction_or_amendment_chain invalid")
    result_status = document.get("result_status")
    if kind == "result" and result_status not in {"COMPLETED", "COMPLETED_WITH_AMENDMENT"}:
        raise ValueError("result status is not completed")
    if kind == "announcement" and result_status is not None:
        raise ValueError("announcement has result status")
    return {
        **document,
        "document_id": document_id,
        "canonical_event_id": event_id,
        "raw_path": str(raw_path),
        "auction_date_value": auction_date,
        "published_at_value": published_at,
        "available_at_value": available_at,
        "retrieved_at_value": retrieved_at,
    }


def normalize_auction_documents(documents: list[dict[str, Any]], *, repo_root: Path) -> dict[str, Any]:
    """Pair and admit original/reopening 30Y Treasury documents.

    A revision is admitted only when it explicitly points to its predecessor.
    The latest revision available by the frozen cutoff is used, so an
    untracked duplicate or a late correction cannot silently alter the event.
    """

    if not isinstance(documents, list) or not documents:
        raise ValueError("auction document archive is empty")
    validated = [_validate_document(document, repo_root=repo_root) for document in documents]
    by_event_kind: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    by_document_id: dict[str, dict[str, Any]] = {}
    for document in validated:
        document_id = document["document_id"]
        if document_id in by_document_id:
            raise ValueError("duplicate document id")
        by_document_id[document_id] = document
        by_event_kind[(document["canonical_event_id"], document["document_kind"])].append(document)

    selected: dict[tuple[str, str], dict[str, Any]] = {}
    for key, candidates in by_event_kind.items():
        revisions = [str(item["revision_id"]) for item in candidates]
        if len(revisions) != len(set(revisions)):
            raise ValueError("duplicate revision")
        candidates.sort(key=lambda item: item["available_at_value"])
        for index, candidate in enumerate(candidates):
            predecessor = candidate["supersedes_document_id"]
            if index == 0:
                if predecessor is not None or candidate["revision_id"] != "original":
                    raise ValueError("revision chain starts with supersedes_document_id")
            elif predecessor != candidates[index - 1]["document_id"]:
                raise ValueError("revision chain invalid")
        selected[key] = candidates[-1]

    event_ids = sorted({document["canonical_event_id"] for document in validated})
    events: list[dict[str, Any]] = []
    for event_id in event_ids:
        announcement = selected.get((event_id, "announcement"))
        result = selected.get((event_id, "result"))
        if announcement is None or result is None:
            raise ValueError(f"auction event missing announcement/result:{event_id}")
        identity_fields = ("security_identifier", "auction_session_date", "security_type", "original_tenor", "issue_or_reopening", "issuer_identifier", "instrument_type", "source_timezone")
        if any(announcement[field] != result[field] for field in identity_fields):
            raise ValueError(f"auction document identity mismatch:{event_id}")
        available_at = result["available_at_value"]
        published_at = result["published_at_value"]
        chain = []
        for candidate in by_event_kind[(event_id, "announcement")] + by_event_kind[(event_id, "result")]:
            chain.append(
                {
                    "document_id": candidate["document_id"],
                    "document_hash": candidate["sha256"],
                    "document_kind": candidate["document_kind"],
                    "revision_id": candidate["revision_id"],
                    "supersedes_document_id": candidate["supersedes_document_id"],
                }
            )
        events.append(
            {
                "schema": AUCTION_SCHEMA,
                "canonical_event_id": event_id,
                "security_identifier": announcement["security_identifier"],
                "issuer_identifier": announcement["issuer_identifier"],
                "instrument_type": announcement["instrument_type"],
                "original_tenor": int(announcement["original_tenor"]),
                "issue_or_reopening": announcement["issue_or_reopening"],
                "auction_session_date": announcement["auction_session_date"],
                "announcement_document_id": announcement["document_id"],
                "result_document_id": result["document_id"],
                "announcement_document_hash": announcement["sha256"],
                "result_document_hash": result["sha256"],
                "announcement_revision_id": announcement["revision_id"],
                "result_revision_id": result["revision_id"],
                "published_at": published_at.isoformat().replace("+00:00", "Z") if published_at is not None else None,
                "available_at": available_at.isoformat().replace("+00:00", "Z"),
                "source_timezone": announcement["source_timezone"],
                "retrieved_at": max(announcement["retrieved_at_value"], result["retrieved_at_value"]).isoformat().replace("+00:00", "Z"),
                "correction_or_amendment_chain": chain,
                "duplicate_event_key": f"{event_id}:{announcement['sha256']}:{result['sha256']}",
                "announcement_available_at": announcement["available_at_value"].isoformat().replace("+00:00", "Z"),
                "result_available_at": result["available_at_value"].isoformat().replace("+00:00", "Z"),
                "entry_available_at": available_at.isoformat().replace("+00:00", "Z"),
                "availability_derivation": result["availability_derivation"],
                "result_signal_eligibility": result["result_signal_eligibility"],
                "same_day_signal_allowed": result["same_day_signal_allowed"],
                "result_status": result["result_status"],
            }
        )
    return {
        "schema": AUCTION_SCHEMA,
        "status": "COMPLETE",
        "historical_cutoff": CUTOFF_UTC,
        "auction_cutoff_local": AUCTION_CUTOFF_LOCAL,
        "admitted_event_count": len(events),
        "events": events,
        "economic_signal_rows": 0,
    }


def _validate_available_at(value: Any, field: str = "available_at") -> str:
    parsed = _parse_aware(value, field)
    if parsed > _parse_aware(CUTOFF_UTC, "historical_cutoff"):
        raise ValueError(f"{field} post_cutoff")
    return parsed.isoformat().replace("+00:00", "Z")


def validate_tlt_bars(rows: list[dict[str, Any]], *, available_at: str | None) -> dict[str, Any]:
    """Validate execution-grade, split-adjusted TLT bars without dividends."""

    source_time = _validate_available_at(available_at)
    required = {
        "permanent_security_identifier",
        "listing_and_tradability_interval",
        "session_date",
        "regular_open_timestamp",
        "source_vintage",
        "available_at",
        "adjustment",
        "open",
        "high",
        "low",
        "close",
        "volume",
    }
    if not isinstance(rows, list) or not rows:
        raise ValueError("TLT bars empty")
    keys: set[tuple[str, str]] = set()
    dates: list[date] = []
    records: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("TLT bar schema invalid")
        if "adjclose" in row:
            raise ValueError("TLT bars must not use all-adjusted adjclose")
        if set(row) != required:
            raise ValueError("TLT bar schema invalid")
        if not isinstance(row["permanent_security_identifier"], str) or not row["permanent_security_identifier"].strip():
            raise ValueError("TLT bar identity invalid")
        if not isinstance(row["listing_and_tradability_interval"], str) or not row["listing_and_tradability_interval"].strip():
            raise ValueError("TLT bar eligibility interval missing")
        if not isinstance(row["source_vintage"], str) or not row["source_vintage"].strip():
            raise ValueError("TLT bar source_vintage missing")
        if row["adjustment"] != "split":
            raise ValueError("TLT bars must be split-adjusted")
        row_date = _parse_date(row["session_date"], "bar session_date")
        timestamp = _parse_aware(row["regular_open_timestamp"], "regular_open_timestamp")
        row_available_at = _parse_aware(row["available_at"], "bar available_at")
        if row_available_at > _parse_aware(CUTOFF_UTC, "historical_cutoff"):
            raise ValueError("bar available_at post_cutoff")
        if row_available_at > _parse_aware(source_time, "bars artifact available_at"):
            raise ValueError("bar available_at after artifact available_at")
        if row_date > _parse_aware(CUTOFF_UTC, "historical_cutoff").date():
            raise ValueError("bar session_date post_cutoff")
        if timestamp.astimezone(NEW_YORK).date() != row_date:
            raise ValueError("bar session_date/regular_open_timestamp mismatch")
        try:
            values = [float(row[field]) for field in ("open", "high", "low", "close", "volume")]
        except (TypeError, ValueError) as exc:
            raise ValueError("TLT bar values invalid") from exc
        if not all(math.isfinite(value) for value in values) or any(value <= 0 for value in values[:4]) or values[4] < 0:
            raise ValueError("TLT bar values invalid")
        if not values[2] <= values[0] <= values[1] or not values[2] <= values[3] <= values[1]:
            raise ValueError("TLT bar OHLC invalid")
        key = (row["session_date"], row["regular_open_timestamp"])
        if key in keys or row_date in dates:
            raise ValueError("TLT bar duplicate")
        keys.add(key)
        dates.append(row_date)
        records.append(dict(row))
    if dates != sorted(dates):
        raise ValueError("TLT bar dates not ordered")
    return {
        "schema": TLT_BARS_SCHEMA,
        "status": "COMPLETE",
        "permanent_security_identifier": sorted({str(row["permanent_security_identifier"]) for row in records}),
        "adjustment": "split",
        "accounting": "separate_cash_dividends_required",
        "available_at": source_time,
        "rows": len(records),
        "first": dates[0].isoformat(),
        "last": dates[-1].isoformat(),
        "records": records,
    }


def validate_nyse_calendar(rows: list[dict[str, Any]], *, available_at: str | None) -> dict[str, Any]:
    """Validate a pinned NYSE session calendar used for next/fifth opens."""

    source_time = _validate_available_at(available_at, "calendar available_at")
    required = {
        "regular_session_date",
        "regular_open_timestamp",
        "regular_close_timestamp",
        "holiday_or_closure_reason",
    }
    if not isinstance(rows, list) or not rows:
        raise ValueError("NYSE calendar empty")
    records: list[dict[str, Any]] = []
    dates: list[date] = []
    for row in rows:
        if not isinstance(row, dict) or set(row) != required:
            raise ValueError("NYSE calendar schema invalid")
        session_date = _parse_date(row["regular_session_date"], "calendar regular_session_date")
        if session_date.weekday() >= 5:
            raise ValueError("NYSE calendar weekend session")
        reason = row["holiday_or_closure_reason"]
        is_closed = reason not in (None, "", "NONE")
        if is_closed:
            if row["regular_open_timestamp"] is not None or row["regular_close_timestamp"] is not None:
                raise ValueError("closed NYSE session has regular timestamps")
        else:
            open_at = _parse_aware(row["regular_open_timestamp"], "calendar regular_open_timestamp")
            close_at = _parse_aware(row["regular_close_timestamp"], "calendar regular_close_timestamp")
            local_open = open_at.astimezone(NEW_YORK)
            local_close = close_at.astimezone(NEW_YORK)
            if local_open.date() != session_date or local_close.date() != session_date:
                raise ValueError("NYSE calendar timestamp/date mismatch")
            if local_open.time() != time(9, 30):
                raise ValueError("NYSE regular open must be 09:30 America/New_York")
            if close_at <= open_at:
                raise ValueError("NYSE regular close precedes open")
        if session_date in dates:
            raise ValueError("NYSE calendar duplicate session")
        dates.append(session_date)
        records.append(dict(row))
    if dates != sorted(dates):
        raise ValueError("NYSE calendar dates not ordered")
    return {
        "schema": NYSE_CALENDAR_SCHEMA,
        "status": "COMPLETE",
        "available_at": source_time,
        "rows": len(records),
        "records": records,
    }


def validate_tlt_dividends(rows: list[dict[str, Any]], *, available_at: str | None) -> dict[str, Any]:
    """Validate the separate TLT cash-distribution ledger."""

    source_time = _validate_available_at(available_at)
    required = {
        "permanent_security_identifier",
        "action_type",
        "effective_date",
        "cash_or_ratio",
        "source_document_hash",
        "duplicate_event_key",
        "event_timestamp",
        "currency",
        "available_at",
    }
    if not isinstance(rows, list):
        raise ValueError("TLT dividends schema invalid")
    keys: set[tuple[str, str]] = set()
    records: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict) or set(row) != required:
            raise ValueError("TLT dividends schema invalid")
        if not isinstance(row["permanent_security_identifier"], str) or not row["permanent_security_identifier"].strip() or row["action_type"] != "cash_dividend" or row["currency"] != "USD":
            raise ValueError("TLT dividend identity/currency invalid")
        if not isinstance(row["source_document_hash"], str) or not HEX64_RE.fullmatch(row["source_document_hash"]):
            raise ValueError("dividend source_document_hash invalid")
        if not isinstance(row["duplicate_event_key"], str) or not row["duplicate_event_key"].strip():
            raise ValueError("dividend duplicate_event_key missing")
        ex_date = _parse_date(row["effective_date"], "dividend effective_date")
        timestamp = _parse_aware(row["event_timestamp"], "dividend event_timestamp")
        if timestamp.astimezone(NEW_YORK).date() != ex_date:
            raise ValueError("dividend effective_date/event_timestamp mismatch")
        row_available_at = _parse_aware(row["available_at"], "dividend available_at")
        if row_available_at > _parse_aware(CUTOFF_UTC, "historical_cutoff"):
            raise ValueError("dividend available_at post_cutoff")
        if row_available_at > _parse_aware(source_time, "dividends artifact available_at"):
            raise ValueError("dividend available_at after artifact available_at")
        if ex_date > _parse_aware(CUTOFF_UTC, "historical_cutoff").date():
            raise ValueError("dividend effective_date post_cutoff")
        try:
            amount = float(row["cash_or_ratio"])
        except (TypeError, ValueError) as exc:
            raise ValueError("dividend cash_or_ratio invalid") from exc
        if not math.isfinite(amount) or amount <= 0:
            raise ValueError("dividend cash_or_ratio must be positive")
        key = (row["permanent_security_identifier"], row["event_timestamp"])
        if key in keys:
            raise ValueError("duplicate dividend event")
        keys.add(key)
        records.append(dict(row))
    records.sort(key=lambda row: row["event_timestamp"])
    return {
        "schema": TLT_DIVIDENDS_SCHEMA,
        "status": "COMPLETE",
        "permanent_security_identifier": sorted({str(row["permanent_security_identifier"]) for row in records}),
        "accounting": "separate_cash_dividends",
        "available_at": source_time,
        "rows": len(records),
        "records": records,
    }


def validate_tlt_eligibility(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Validate historical TLT identity/tradability intervals for PIT use."""

    required = {
        "canonical_symbol",
        "historical_symbol",
        "permanent_security_identifier",
        "provider_asset_id",
        "security_identifier",
        "venue",
        "listing_and_tradability_interval",
        "valid_from",
        "valid_to",
        "first_observed_bar",
        "last_observed_bar",
        "identity_basis",
        "source_url",
        "available_at",
    }
    if not isinstance(rows, list) or not rows:
        raise ValueError("TLT eligibility empty")
    intervals: list[tuple[date, date | None, dict[str, Any]]] = []
    for row in rows:
        if not isinstance(row, dict) or set(row) != required:
            raise ValueError("TLT eligibility schema invalid")
        if row["canonical_symbol"] != "TLT" or row["historical_symbol"] != "TLT":
            raise ValueError("TLT eligibility identity invalid")
        if not isinstance(row["permanent_security_identifier"], str) or not row["permanent_security_identifier"].strip() or not isinstance(row["provider_asset_id"], str) or not row["provider_asset_id"].strip() or not isinstance(row["venue"], str) or not row["venue"].strip():
            raise ValueError("TLT eligibility provider identity missing")
        if not isinstance(row["security_identifier"], str) or not CUSIP_RE.fullmatch(row["security_identifier"]):
            raise ValueError("TLT eligibility security_identifier invalid")
        if row["identity_basis"] not in ALLOWED_IDENTITY_BASES:
            raise ValueError("historical identity basis required")
        if not isinstance(row["listing_and_tradability_interval"], str) or not row["listing_and_tradability_interval"].strip():
            raise ValueError("listing_and_tradability_interval missing")
        _official_url(row["source_url"], hosts=ELIGIBILITY_SOURCE_HOSTS)
        _validate_available_at(row["available_at"])
        valid_from = _parse_date(row["valid_from"], "eligibility valid_from")
        valid_to = None if row["valid_to"] is None else _parse_date(row["valid_to"], "eligibility valid_to")
        first_observed = _parse_date(row["first_observed_bar"], "eligibility first_observed_bar")
        last_observed = _parse_date(row["last_observed_bar"], "eligibility last_observed_bar")
        if valid_to is not None and valid_to < valid_from:
            raise ValueError("eligibility interval reversed")
        if last_observed < first_observed:
            raise ValueError("eligibility observed interval reversed")
        intervals.append((valid_from, valid_to, dict(row)))
    intervals.sort(key=lambda item: item[0])
    for previous, current in zip(intervals, intervals[1:]):
        previous_end = previous[1]
        if previous_end is None or previous_end > current[0]:
            raise ValueError("overlapping TLT eligibility intervals")
    return {
        "schema": TLT_ELIGIBILITY_SCHEMA,
        "status": "COMPLETE",
        "canonical_symbol": "TLT",
        "rows": len(intervals),
        "historical_identity": True,
        "records": [item[2] for item in intervals],
    }


def _manifest_blockers(manifest: dict[str, Any]) -> list[str]:
    blockers: list[str] = []
    if not isinstance(manifest, dict) or manifest.get("schema_version") != GOVERNANCE_SCHEMA:
        blockers.append("manifest_schema")
        return blockers
    if not isinstance(manifest.get("research_id"), str) or not manifest["research_id"].strip() or manifest.get("candidate_id") != "R46":
        blockers.append("manifest_identity")
    if manifest.get("status") != "FROZEN_PRE_OUTCOME":
        blockers.append("manifest_status")
    for field in ("declared_before_outcomes", "research_only", "paper_live_untouched", "no_auto_promotion"):
        if manifest.get(field) is not True:
            blockers.append(f"manifest_boundary:{field}")
    for field in ("economic_execution", "economic_run", "economic_outcomes_accessed", "outcome_artifacts_present", "promotion", "integration_candidate", "paper_or_live_activation", "auto_promotion"):
        if manifest.get(field) is not False:
            blockers.append(f"manifest_boundary:{field}")

    event_contract = manifest.get("event_contract")
    if (
        not isinstance(event_contract, dict)
        or event_contract.get("decision_cutoff") != f"{AUCTION_CUTOFF_LOCAL} on the auction session"
        or event_contract.get("retrieval_time_substitution_forbidden") is not True
        or event_contract.get("entry") != "next regular-session TLT open after the qualifying event"
        or event_contract.get("exit") != "fifth regular-session open after entry, producing five open-to-open holding intervals"
        or event_contract.get("overlap") != "Overlapping signals do not pyramid or extend the first event's exit."
    ):
        blockers.append("r46_rule_contract")

    execution = manifest.get("execution_contract")
    caps = execution.get("caps") if isinstance(execution, dict) else None
    if (
        not isinstance(execution, dict)
        or execution.get("native_one_ledger_required") is not True
        or execution.get("long_only") is not True
        or execution.get("no_leverage") is not True
        or execution.get("no_pyramid") is not True
        or not isinstance(caps, dict)
        or caps.get("equity_gross_max") != 0.55
        or caps.get("combined_gross_max") != 0.60
        or caps.get("combined_cash_min") != 0.40
        or caps.get("total_positions_max") != 6
    ):
        blockers.append("shared_capital_contract")

    data_contract = manifest.get("data_contract")
    if not isinstance(data_contract, dict) or data_contract.get("pit_source_status") != "READY_PIT_SOURCE":
        blockers.append("BLOCKED_PIT_SOURCE")
    elif (
        not isinstance(data_contract.get("pit_source_attestation_path"), str)
        or not data_contract["pit_source_attestation_path"].strip()
        or not isinstance(data_contract.get("pit_source_attestation_sha256"), str)
        or not HEX64_RE.fullmatch(data_contract["pit_source_attestation_sha256"])
    ):
        blockers.append("BLOCKED_PIT_SOURCE")

    if not isinstance(execution, dict) or execution.get("native_ledger_status") != "READY_NATIVE_LEDGER":
        blockers.append("BLOCKED_NATIVE_LEDGER")
    elif (
        not isinstance(execution.get("native_ledger_attestation_path"), str)
        or not execution["native_ledger_attestation_path"].strip()
        or not isinstance(execution.get("native_ledger_attestation_sha256"), str)
        or not HEX64_RE.fullmatch(execution["native_ledger_attestation_sha256"])
    ):
        blockers.append("BLOCKED_NATIVE_LEDGER")

    holdout = manifest.get("holdout")
    if (
        not isinstance(holdout, dict)
        or holdout.get("status") != "SEALED_PROSPECTIVE_SINGLE_USE"
        or holdout.get("mode") != "prospective_single_use"
        or holdout.get("historical_holdout_accepted") is not False
        or not isinstance(holdout.get("contract_path"), str)
        or not holdout["contract_path"].strip()
        or not isinstance(holdout.get("contract_sha256"), str)
        or not HEX64_RE.fullmatch(holdout["contract_sha256"])
        or not isinstance(holdout.get("custody_receipt_path"), str)
        or not holdout["custody_receipt_path"].strip()
        or holdout.get("accessed") is not False
    ):
        blockers.append("holdout")
    statistics = manifest.get("statistics")
    if not isinstance(statistics, dict) or statistics.get("status") != "READY_STATISTICS":
        blockers.append("BLOCKED_STATISTICS")
    multiplicity = manifest.get("multiplicity")
    if not isinstance(multiplicity, dict) or multiplicity.get("status") != "READY_MULTIPLICITY":
        blockers.append("BLOCKED_STATISTICS")

    provenance = manifest.get("source_provenance")
    if not isinstance(provenance, dict):
        blockers.append("BLOCKED_SOURCE_PIN")
    else:
        if (
            not isinstance(provenance.get("base_commit"), str)
            or not re.fullmatch(r"[0-9a-f]{40}", provenance["base_commit"])
            or provenance.get("clean_tree_observed") is not True
        ):
            blockers.append("BLOCKED_SOURCE_PIN")
        for role in ("runner", "verifier", "preflight", "custody_validator"):
            declaration = provenance.get(role)
            if (
                not isinstance(declaration, dict)
                or not isinstance(declaration.get("path"), str)
                or not declaration["path"].strip()
                or not isinstance(declaration.get("sha256"), str)
                or not HEX64_RE.fullmatch(declaration["sha256"])
                or declaration.get("required_in_base_commit") is not True
            ):
                blockers.append(f"BLOCKED_{role.upper()}_NOT_PINNED")
        environment = provenance.get("environment")
        if (
            not isinstance(environment, dict)
            or not isinstance(environment.get("command"), str)
            or not environment["command"].strip()
            or environment["command"].startswith("UNSET_")
            or not isinstance(environment.get("dependency_lock_sha256"), str)
            or not HEX64_RE.fullmatch(environment["dependency_lock_sha256"])
            or environment.get("network") != "disabled"
        ):
            blockers.append("BLOCKED_ENVIRONMENT_NOT_PINNED")
    return sorted(set(blockers))


def _valid_holdout_attestation(attestation: dict[str, Any], manifest: dict[str, Any]) -> bool:
    """Validate the canonical sealed prospective-single-use receipt shape.

    Cross-file manifest/contract hash equality and custody storage inspection
    remain the responsibility of ``validate_r46_holdout_custody``.  This gate
    still requires every receipt field in that governance contract so a
    reduced local fixture cannot become a readiness pass.
    """

    required = {
        "schema_version",
        "research_id",
        "holdout_id",
        "status",
        "manifest_sha256",
        "holdout_contract_sha256",
        "data_root_sha256",
        "access_log_sha256",
        "sealed_at",
        "holdout_start",
        "holdout_end_exclusive",
        "storage_class",
        "encrypted_at_rest",
        "access_log_append_only",
        "raw_holdout_hidden_until_release",
        "operator",
        "custodian",
        "independent_reviewer",
        "outcomes_accessed_before_release",
        "one_release_only",
        "release_count",
    }
    if not isinstance(attestation, dict) or not required.issubset(attestation):
        return False
    if attestation["schema_version"] != "r46-holdout-custody-receipt-v1" or attestation["status"] != "SEALED_PROSPECTIVE_SINGLE_USE":
        return False
    research_id = manifest.get("research_id")
    if not isinstance(research_id, str) or attestation["research_id"] != research_id:
        return False
    if attestation["holdout_id"] != f"{research_id}:prospective_holdout":
        return False
    if "accessed" in attestation and attestation["accessed"] is not False:
        return False
    holdout = manifest.get("holdout")
    if not isinstance(holdout, dict):
        return False
    if attestation.get("_hash_pinned") is not True:
        return False
    attestation_ref = attestation.get("attestation_ref")
    if "attestation_ref" in holdout and attestation_ref != holdout.get("attestation_ref"):
        return False
    for field in ("manifest_sha256", "holdout_contract_sha256", "data_root_sha256", "access_log_sha256"):
        if not isinstance(attestation[field], str) or not HEX64_RE.fullmatch(attestation[field]):
            return False
    try:
        sealed_at = _parse_aware(attestation["sealed_at"], "sealed_at")
        holdout_start = _parse_aware(attestation["holdout_start"], "holdout_start")
        holdout_end = _parse_aware(attestation["holdout_end_exclusive"], "holdout_end_exclusive")
    except ValueError:
        return False
    if holdout_start >= holdout_end or sealed_at > holdout_start:
        return False
    if attestation["storage_class"] not in {"WORM", "IMMUTABLE_OBJECT"}:
        return False
    if any(attestation[field] is not True for field in ("encrypted_at_rest", "access_log_append_only", "raw_holdout_hidden_until_release")):
        return False
    if attestation["outcomes_accessed_before_release"] is not False or attestation["one_release_only"] is not True or attestation["release_count"] != 0:
        return False
    actors = []
    for field in ("operator", "custodian", "independent_reviewer"):
        actor = attestation[field]
        if not isinstance(actor, dict) or not isinstance(actor.get("id"), str) or not actor["id"].strip() or not isinstance(actor.get("signature"), str) or not actor["signature"].strip():
            return False
        actors.append(actor["id"])
    return len(set(actors)) == len(actors)


def _cross_validate_tlt_data(
    *,
    bars: dict[str, Any],
    dividends: dict[str, Any],
    eligibility: dict[str, Any],
    calendar: dict[str, Any],
) -> list[str]:
    blockers: list[str] = []
    eligibility_records = eligibility.get("records") if isinstance(eligibility, dict) else None
    bar_records = bars.get("records") if isinstance(bars, dict) else None
    dividend_records = dividends.get("records") if isinstance(dividends, dict) else None
    calendar_records = calendar.get("records") if isinstance(calendar, dict) else None
    if not isinstance(eligibility_records, list) or not eligibility_records:
        return ["tlt_identity_binding"]
    if not isinstance(bar_records, list) or not isinstance(dividend_records, list) or not isinstance(calendar_records, list):
        return ["tlt_identity_binding", "nyse_calendar"]

    eligibility_by_id: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in eligibility_records:
        if not isinstance(row, dict) or not row.get("permanent_security_identifier"):
            continue
        eligibility_by_id[str(row["permanent_security_identifier"])].append(row)

    def _eligibility_for_date(identifier: Any, observed_date: date) -> dict[str, Any] | None:
        candidates = [
            candidate
            for candidate in eligibility_by_id.get(str(identifier), [])
            if _date_in_eligibility_interval(observed_date, candidate)
        ]
        return candidates[0] if len(candidates) == 1 else None
    calendar_by_date: dict[date, dict[str, Any]] = {}
    for row in calendar_records:
        if not isinstance(row, dict):
            blockers.append("nyse_calendar")
            continue
        try:
            calendar_date = _parse_date(row.get("regular_session_date"), "calendar regular_session_date")
        except ValueError:
            blockers.append("nyse_calendar")
            continue
        calendar_by_date[calendar_date] = row

    for row in bar_records:
        if not isinstance(row, dict):
            blockers.append("tlt_identity_binding")
            continue
        identifier = row.get("permanent_security_identifier")
        try:
            session_date = _parse_date(row.get("session_date"), "bar session_date")
            row_available_at = _parse_aware(row.get("available_at"), "bar available_at")
            artifact_available_at = _parse_aware(bars.get("available_at"), "bars artifact available_at")
        except ValueError:
            blockers.append("tlt_identity_binding")
            continue
        eligibility_row = _eligibility_for_date(identifier, session_date)
        if (
            eligibility_row is None
            or row.get("listing_and_tradability_interval") != eligibility_row.get("listing_and_tradability_interval")
            or row_available_at > artifact_available_at
        ):
            blockers.append("tlt_identity_binding")
        calendar_row = calendar_by_date.get(session_date)
        if calendar_row is None or calendar_row.get("holiday_or_closure_reason") not in (None, "", "NONE"):
            blockers.append("nyse_calendar")
        else:
            try:
                bar_open = _parse_aware(row.get("regular_open_timestamp"), "bar regular_open_timestamp")
                calendar_open = _parse_aware(calendar_row.get("regular_open_timestamp"), "calendar regular_open_timestamp")
            except ValueError:
                blockers.append("nyse_session_open")
            else:
                if bar_open != calendar_open:
                    blockers.append("nyse_session_open")
                if row_available_at < bar_open:
                    blockers.append("tlt_bar_availability")

    for row in dividend_records:
        if not isinstance(row, dict):
            blockers.append("tlt_identity_binding")
            continue
        identifier = row.get("permanent_security_identifier")
        try:
            effective_date = _parse_date(row.get("effective_date"), "dividend effective_date")
            event_timestamp = _parse_aware(row.get("event_timestamp"), "dividend event_timestamp")
            row_available_at = _parse_aware(row.get("available_at"), "dividend available_at")
            artifact_available_at = _parse_aware(dividends.get("available_at"), "dividends artifact available_at")
        except ValueError:
            blockers.append("tlt_dividend_event_timestamp")
            continue
        eligibility_row = _eligibility_for_date(identifier, effective_date)
        if eligibility_row is None or row_available_at > artifact_available_at:
            blockers.append("tlt_identity_binding")
        calendar_row = calendar_by_date.get(effective_date)
        if calendar_row is None or calendar_row.get("holiday_or_closure_reason") not in (None, "", "NONE"):
            blockers.append("tlt_dividend_event_timestamp")
        else:
            try:
                calendar_open = _parse_aware(calendar_row.get("regular_open_timestamp"), "calendar regular_open_timestamp")
            except ValueError:
                blockers.append("tlt_dividend_event_timestamp")
            else:
                if event_timestamp != calendar_open:
                    blockers.append("tlt_dividend_event_timestamp")
        if row_available_at > event_timestamp:
            blockers.append("tlt_dividend_availability")
    return sorted(set(blockers))


def _date_in_eligibility_interval(value: date, row: dict[str, Any]) -> bool:
    try:
        valid_from = _parse_date(row.get("valid_from"), "eligibility valid_from")
        valid_to = None if row.get("valid_to") is None else _parse_date(row.get("valid_to"), "eligibility valid_to")
    except ValueError:
        return False
    return valid_from <= value and (valid_to is None or value < valid_to)


def verify_r46_package(
    *,
    manifest: dict[str, Any],
    auction: dict[str, Any],
    bars: dict[str, Any],
    dividends: dict[str, Any],
    eligibility: dict[str, Any],
    holdout_attestation: dict[str, Any],
    calendar: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return readiness without producing returns, trades, or backtest output."""

    manifest = manifest if isinstance(manifest, dict) else {}
    auction = auction if isinstance(auction, dict) else {}
    bars = bars if isinstance(bars, dict) else {}
    dividends = dividends if isinstance(dividends, dict) else {}
    eligibility = eligibility if isinstance(eligibility, dict) else {}
    blockers = _manifest_blockers(manifest)
    if auction.get("schema") != AUCTION_SCHEMA or auction.get("status") != "COMPLETE" or auction.get("admitted_event_count", 0) < 1:
        blockers.append("auction_lineage")
    if bars.get("schema") != TLT_BARS_SCHEMA or bars.get("status") != "COMPLETE" or bars.get("adjustment") != "split":
        blockers.append("tlt_bars")
    if dividends.get("schema") != TLT_DIVIDENDS_SCHEMA or dividends.get("status") != "COMPLETE" or dividends.get("accounting") != "separate_cash_dividends":
        blockers.append("tlt_dividends")
    if eligibility.get("schema") != TLT_ELIGIBILITY_SCHEMA or eligibility.get("status") != "COMPLETE" or eligibility.get("historical_identity") is not True:
        blockers.append("tlt_pit_eligibility")
    if not isinstance(calendar, dict) or calendar.get("schema") != NYSE_CALENDAR_SCHEMA or calendar.get("status") != "COMPLETE":
        blockers.append("nyse_calendar")
    else:
        blockers.extend(_cross_validate_tlt_data(bars=bars, dividends=dividends, eligibility=eligibility, calendar=calendar))
    if not _valid_holdout_attestation(holdout_attestation, manifest):
        blockers.append("holdout")
    blockers.append("BLOCKED_SIGNATURE_VERIFICATION")

    if isinstance(calendar, dict) and isinstance(bars.get("records"), list):
        regular_calendar_dates: list[date] = []
        for row in calendar.get("records", []):
            if not isinstance(row, dict) or row.get("holiday_or_closure_reason") not in (None, "", "NONE"):
                continue
            try:
                regular_calendar_dates.append(_parse_date(row.get("regular_session_date"), "calendar regular_session_date"))
            except ValueError:
                blockers.append("nyse_calendar")
        bar_dates: set[date] = set()
        for row in bars.get("records", []):
            if not isinstance(row, dict) or not row.get("permanent_security_identifier"):
                continue
            try:
                bar_dates.add(_parse_date(row.get("session_date"), "bar session_date"))
            except ValueError:
                blockers.append("tlt_bars")
        events = auction.get("events", [])
        for event in events if isinstance(events, list) else []:
            if not isinstance(event, dict):
                blockers.append("auction_lineage")
                continue
            try:
                auction_date = _parse_date(event.get("auction_session_date"), "auction_session_date")
            except ValueError:
                blockers.append(f"tlt_fifth_session_exit_missing:{event.get('canonical_event_id', '')}")
                continue
            future_dates = [value for value in regular_calendar_dates if value > auction_date]
            required_dates = future_dates[:6]
            if len(required_dates) < 6 or not set(required_dates).issubset(bar_dates):
                blockers.append(f"tlt_fifth_session_exit_missing:{event.get('canonical_event_id', '')}")

    unique_blockers = sorted(set(blockers))
    status = "DATA_ACQUISITION_REQUIRED"
    return {
        "schema": DATA_PACKAGE_SCHEMA,
        "status": status,
        "blockers": unique_blockers,
        "economic_execution": False,
        "no_auto_promotion": True,
        "admitted_event_count": auction.get("admitted_event_count", 0) if not unique_blockers else 0,
    }
