"""Freeze R46 Treasury publication/availability evidence from final XML inputs.

This builder derives the event universe and missing publication timestamps from
the final normalized Treasury documents.  It deliberately has no event-ID
argument: callers cannot substitute a caller-owned universe or missingness set.
The resulting receipt is evidence-only and never launches an economic run.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from collections import Counter
from collections.abc import Mapping
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

REPO = Path(__file__).resolve().parents[2]
FINAL_RUN_RELATIVE = Path(
    "data/research/runs/r46_treasury_xml_v2_2026_09_11_inventory_raw_hash_2026_09_11"
)
RECEIPT_RELATIVE = FINAL_RUN_RELATIVE / "receipt.json"
DOCUMENTS_RELATIVE = FINAL_RUN_RELATIVE / "documents.jsonl"
DEFAULT_OUTPUT = Path(
    "data/research_receipts/r46_treasury_publication_availability_evidence_2026_09_11_inventory_raw_hash_v3.json"
)

SCHEMA = "r46-treasury-publication-availability-evidence-v2"
RESEARCH_ID = "r46_treasury_auction_unwind_tlt_v2_2026_09_10"
CANDIDATE_ID = "R46"
RESULT_AVAILABILITY_POLICY = (
    "official_XML_AuctionDate_plus_one_civil_day_midnight_America_New_York_for_all_results"
)
RESULT_SIGNAL_ELIGIBILITY = "FIRST_REGULAR_OPEN_STRICTLY_AFTER_AVAILABLE_AT"
EXPECTED_EVENT_COUNT = 127
EXPECTED_MISSING_RELEASE_COUNT = 15
EXPECTED_EVENT_IDS_SHA256 = "f726f27aaece6532de1581b5830e38cceba67c75ae02dfa79fedaffc0e9d4b0b"
EXPECTED_MISSING_RELEASE_IDS_SHA256 = "82962914cc032f371160549757fb785419e797deef0666f41c42deafbb88ce9c"
EVENT_IDS_CANONICALIZATION = "sorted unique event IDs encoded as compact UTF-8 JSON array"
NEW_YORK = ZoneInfo("America/New_York")


class PublicationAvailabilityEvidenceError(ValueError):
    """Raised when final Treasury inputs cannot prove this evidence contract."""


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _canonical_bytes(payload: Mapping[str, Any]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def canonical_hash(payload: Mapping[str, Any], *, self_field: str) -> str:
    value = json.loads(json.dumps(dict(payload), ensure_ascii=False))
    integrity = value.get("integrity")
    if not isinstance(integrity, dict):
        raise PublicationAvailabilityEvidenceError("integrity object missing")
    integrity.pop(self_field, None)
    return sha256_bytes(_canonical_bytes(value))


def canonical_event_ids_digest(event_ids: list[str]) -> str:
    canonical = sorted(set(event_ids))
    return sha256_bytes(json.dumps(canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))


def _load_json(path: Path, field: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise PublicationAvailabilityEvidenceError(f"{field} unreadable") from exc
    if not isinstance(value, dict):
        raise PublicationAvailabilityEvidenceError(f"{field} object required")
    return value


def _safe_input(root: Path, relative: Path) -> Path:
    root_resolved = root.resolve()
    path = (root / relative).resolve()
    if path == root_resolved or root_resolved not in path.parents or not path.is_file():
        raise PublicationAvailabilityEvidenceError(f"input missing:{relative.as_posix()}")
    return path


def _relative_path(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError as exc:
        raise PublicationAvailabilityEvidenceError("source path outside repo root") from exc


def _parse_aware(value: Any, field: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise PublicationAvailabilityEvidenceError(f"{field} timestamp missing")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise PublicationAvailabilityEvidenceError(f"{field} timestamp invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise PublicationAvailabilityEvidenceError(f"{field} timestamp timezone missing")
    return parsed


def _event_ids(value: Any, field: str) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value):
        raise PublicationAvailabilityEvidenceError(f"{field} invalid")
    ids = [item.strip() for item in value]
    canonical = sorted(set(ids))
    if len(ids) != len(canonical) or ids != canonical:
        raise PublicationAvailabilityEvidenceError(f"{field} must be sorted and unique")
    return canonical


def _validate_receipt(receipt: Mapping[str, Any], receipt_path: Path, documents_path: Path) -> None:
    if receipt.get("schema") != "r46-treasury-xml-acquisition-v2":
        raise PublicationAvailabilityEvidenceError("receipt schema invalid")
    if receipt.get("research_id") != RESEARCH_ID or receipt.get("candidate_id") != CANDIDATE_ID:
        raise PublicationAvailabilityEvidenceError("receipt identity invalid")
    if receipt.get("status") != "COMPLETE":
        raise PublicationAvailabilityEvidenceError("receipt incomplete")
    if receipt.get("economic_execution") is not False or receipt.get("economic_run") is not False:
        raise PublicationAvailabilityEvidenceError("receipt economic boundary invalid")
    if receipt.get("economic_outcomes_accessed") is True:
        raise PublicationAvailabilityEvidenceError("receipt economic outcomes boundary invalid")
    if receipt.get("economic_fields_filtered") is not True:
        raise PublicationAvailabilityEvidenceError("receipt economic field filter missing")

    integrity = receipt.get("integrity")
    declared = integrity.get("receipt_sha256") if isinstance(integrity, Mapping) else None
    if not isinstance(declared, str) or declared != canonical_hash(receipt, self_field="receipt_sha256"):
        raise PublicationAvailabilityEvidenceError("receipt self-hash mismatch")

    declared_documents = receipt.get("documents_path")
    if not isinstance(declared_documents, str) or Path(declared_documents).is_absolute():
        raise PublicationAvailabilityEvidenceError("receipt documents path invalid")
    try:
        resolved_documents = (receipt_path.parent / declared_documents).resolve()
    except OSError as exc:
        raise PublicationAvailabilityEvidenceError("receipt documents path invalid") from exc
    if resolved_documents != documents_path.resolve():
        raise PublicationAvailabilityEvidenceError("receipt documents path mismatch")
    declared_hash = receipt.get("documents_sha256")
    actual_hash = sha256_file(documents_path)
    if not isinstance(declared_hash, str) or declared_hash != actual_hash:
        raise PublicationAvailabilityEvidenceError("receipt documents hash mismatch")

    declared_count = receipt.get("expected_event_count")
    if declared_count != EXPECTED_EVENT_COUNT:
        raise PublicationAvailabilityEvidenceError("receipt expected event count invalid")
    publication = receipt.get("publication_evidence")
    if not isinstance(publication, Mapping):
        raise PublicationAvailabilityEvidenceError("receipt publication evidence missing")
    if (
        publication.get("availability_derivation") != RESULT_AVAILABILITY_POLICY
        or publication.get("next_session_open_policy") != RESULT_SIGNAL_ELIGIBILITY
        or publication.get("same_day_result_signals_allowed") is not False
        or publication.get("server_last_modified_is_publication_proof") is not False
    ):
        raise PublicationAvailabilityEvidenceError("receipt publication policy invalid")
    basis = receipt.get("publication_basis")
    if isinstance(basis, Mapping) and basis.get("result") != RESULT_AVAILABILITY_POLICY:
        raise PublicationAvailabilityEvidenceError("receipt publication basis invalid")


def _validate_documents(
    documents_path: Path,
    receipt: Mapping[str, Any],
) -> tuple[list[str], list[str], dict[str, int]]:
    try:
        lines = documents_path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise PublicationAvailabilityEvidenceError("documents unreadable") from exc
    if not lines:
        raise PublicationAvailabilityEvidenceError("documents empty")

    rows: list[dict[str, Any]] = []
    for index, line in enumerate(lines, start=1):
        if not line.strip():
            raise PublicationAvailabilityEvidenceError(f"normalized row blank:{index}")
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise PublicationAvailabilityEvidenceError(f"normalized row invalid:{index}") from exc
        if not isinstance(row, dict):
            raise PublicationAvailabilityEvidenceError(f"normalized row object required:{index}")
        rows.append(row)

    kinds = Counter(row.get("document_kind") for row in rows)
    if kinds != Counter({"announcement": EXPECTED_EVENT_COUNT, "result": EXPECTED_EVENT_COUNT}):
        raise PublicationAvailabilityEvidenceError("normalized row kind/count invalid")
    if any(row.get("schema") != "r46-treasury-auction-documents-v2" for row in rows):
        raise PublicationAvailabilityEvidenceError("normalized row schema invalid")

    announcement_ids: list[str] = []
    result_ids: list[str] = []
    missing_ids: list[str] = []
    for index, row in enumerate(rows, start=1):
        event_id = row.get("canonical_event_id")
        if not isinstance(event_id, str) or not event_id.strip():
            raise PublicationAvailabilityEvidenceError(f"normalized event ID missing:{index}")
        event_id = event_id.strip()
        kind = row.get("document_kind")
        if kind == "announcement":
            announcement_ids.append(event_id)
            if row.get("publication_time_proven") is not True:
                raise PublicationAvailabilityEvidenceError("announcement publication evidence invalid")
            continue

        result_ids.append(event_id)
        if row.get("official_publication_date") != row.get("auction_session_date"):
            raise PublicationAvailabilityEvidenceError("result official publication date invalid")
        if row.get("availability_derivation") != RESULT_AVAILABILITY_POLICY:
            raise PublicationAvailabilityEvidenceError("result availability policy invalid")
        if row.get("result_signal_eligibility") != RESULT_SIGNAL_ELIGIBILITY:
            raise PublicationAvailabilityEvidenceError("result signal eligibility invalid")
        if row.get("same_day_signal_allowed") is not False:
            raise PublicationAvailabilityEvidenceError("result same-day policy invalid")
        if "published_at_evidence_only" in row and row.get("published_at_evidence_only") is not True:
            raise PublicationAvailabilityEvidenceError("result publication evidence semantics invalid")
        auction_date_value = row.get("auction_session_date")
        if not isinstance(auction_date_value, str):
            raise PublicationAvailabilityEvidenceError("result auction date invalid")
        try:
            auction_date = date.fromisoformat(auction_date_value)
        except ValueError as exc:
            raise PublicationAvailabilityEvidenceError("result auction date invalid") from exc
        expected_available = datetime.combine(auction_date + timedelta(days=1), time.min, tzinfo=NEW_YORK)
        available = _parse_aware(row.get("available_at"), "result available_at")
        if available != expected_available:
            raise PublicationAvailabilityEvidenceError("result available_at policy invalid")
        if row.get("source_timezone") != "America/New_York":
            raise PublicationAvailabilityEvidenceError("result source timezone invalid")

        published_at = row.get("published_at")
        proven = row.get("publication_time_proven")
        release_time = row.get("release_time")
        if not isinstance(proven, bool):
            raise PublicationAvailabilityEvidenceError("result publication evidence state invalid")
        if published_at is None:
            if proven is not False or release_time not in (None, ""):
                raise PublicationAvailabilityEvidenceError("result missing publication evidence invalid")
            if row.get("publication_at_source") != "official_XML_release_time_missing":
                raise PublicationAvailabilityEvidenceError("result missing publication source invalid")
            missing_ids.append(event_id)
        else:
            if proven is not True or not isinstance(published_at, str) or not published_at.strip():
                raise PublicationAvailabilityEvidenceError("result publication timestamp invalid")
            _parse_aware(published_at, "result published_at")
            if not isinstance(release_time, str) or not release_time.strip():
                raise PublicationAvailabilityEvidenceError("result release time invalid")

    announcement_ids = _event_ids(announcement_ids, "announcement event IDs")
    result_ids = _event_ids(result_ids, "result event IDs")
    missing_ids = _event_ids(missing_ids, "missing release event IDs")
    if announcement_ids != result_ids:
        raise PublicationAvailabilityEvidenceError("announcement/result event IDs disagree")
    if len(result_ids) != EXPECTED_EVENT_COUNT:
        raise PublicationAvailabilityEvidenceError("normalized event count invalid")
    if canonical_event_ids_digest(result_ids) != EXPECTED_EVENT_IDS_SHA256:
        raise PublicationAvailabilityEvidenceError("normalized event IDs digest invalid")
    receipt_ids = _event_ids(receipt.get("expected_event_ids"), "receipt expected event IDs")
    if receipt_ids != result_ids:
        raise PublicationAvailabilityEvidenceError("receipt/document event IDs disagree")
    publication = receipt.get("publication_evidence")
    receipt_missing = _event_ids(
        publication.get("result_release_time_missing_event_ids") if isinstance(publication, Mapping) else None,
        "receipt missing release event IDs",
    )
    if receipt_missing != missing_ids:
        raise PublicationAvailabilityEvidenceError("receipt/document missing release IDs disagree")
    if len(missing_ids) != EXPECTED_MISSING_RELEASE_COUNT:
        raise PublicationAvailabilityEvidenceError("missing release count invalid")
    if canonical_event_ids_digest(missing_ids) != EXPECTED_MISSING_RELEASE_IDS_SHA256:
        raise PublicationAvailabilityEvidenceError("missing release IDs digest invalid")
    return result_ids, missing_ids, {
        "row_count": len(rows),
        "announcement_count": len(announcement_ids),
        "result_count": len(result_ids),
    }


def _atomic_write(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = (json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
    temporary: str | None = None
    try:
        with tempfile.NamedTemporaryFile(mode="wb", dir=path.parent, prefix=f".{path.name}.", delete=False) as handle:
            temporary = handle.name
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError as exc:
            raise FileExistsError(f"no_overwrite:{path}") from exc
    finally:
        if temporary is not None:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass


def build_publication_availability_evidence(
    repo_root: Path = REPO,
    *,
    output_path: Path | None = None,
) -> dict[str, Any]:
    """Derive and atomically write the final normalized publication evidence."""

    root = Path(repo_root).resolve()
    receipt_path = _safe_input(root, RECEIPT_RELATIVE)
    documents_path = _safe_input(root, DOCUMENTS_RELATIVE)
    receipt = _load_json(receipt_path, "receipt")
    _validate_receipt(receipt, receipt_path, documents_path)
    event_ids, missing_ids, normalized = _validate_documents(documents_path, receipt)
    expected_digest = canonical_event_ids_digest(event_ids)
    missing_digest = canonical_event_ids_digest(missing_ids)
    receipt_raw_sha256 = sha256_file(receipt_path)
    documents_raw_sha256 = sha256_file(documents_path)
    evidence: dict[str, Any] = {
        "schema": SCHEMA,
        "status": "COMPLETE",
        "research_id": RESEARCH_ID,
        "candidate_id": CANDIDATE_ID,
        "result_availability_policy": RESULT_AVAILABILITY_POLICY,
        "result_signal_eligibility": RESULT_SIGNAL_ELIGIBILITY,
        "same_day_signal_allowed": False,
        "actual_release_time_role": "evidence_only",
        "server_last_modified_is_publication_proof": False,
        "expected_event_count": len(event_ids),
        "expected_event_ids": event_ids,
        "expected_event_ids_sha256": expected_digest,
        "expected_event_ids_digest": expected_digest,
        "missing_release": {
            "expected_count": len(missing_ids),
            "expected_event_ids": missing_ids,
            "expected_event_ids_sha256": missing_digest,
            "expected_event_ids_digest": missing_digest,
            "derivation": "normalized_result_rows_published_at_null",
            "source_field": "result.published_at",
        },
        "normalized_rows": {
            **normalized,
            "document_kind_field": "document_kind",
            "event_id_field": "canonical_event_id",
            "missing_release_predicate": "result.published_at is null",
        },
        "sources": {
            "receipt": {
                "path": _relative_path(receipt_path, root),
                "schema": receipt.get("schema"),
                "raw_sha256": receipt_raw_sha256,
                "byte_size": receipt_path.stat().st_size,
            },
            "documents": {
                "path": _relative_path(documents_path, root),
                "schema": "r46-treasury-auction-documents-v2",
                "raw_sha256": documents_raw_sha256,
                "byte_size": documents_path.stat().st_size,
            },
        },
        "economic_execution": False,
        "economic_outcomes_accessed": False,
        "integrity": {
            "canonicalization": (
                "UTF-8 JSON, sorted object keys, compact separators, ensure_ascii=false; "
                "omit integrity.evidence_sha256"
            ),
            "evidence_sha256": "",
        },
    }
    evidence["integrity"]["evidence_sha256"] = canonical_hash(evidence, self_field="evidence_sha256")
    destination = Path(DEFAULT_OUTPUT if output_path is None else output_path)
    if not destination.is_absolute():
        destination = root / destination
    _atomic_write(destination, evidence)
    return evidence


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=REPO)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)
    evidence = build_publication_availability_evidence(args.repo_root, output_path=args.output)
    print(json.dumps({"status": evidence["status"], "output": str(args.output)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
