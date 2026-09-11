"""Build the R46 v2 five-artifact, retrospective data-package manifest.

This builder is data-only.  It validates Treasury documents, split-adjusted
TLT bars, separate cash distributions, historical TLT identity, and the NYSE
calendar using the existing R46 readiness validators.  It writes one
hash-cross-linked package manifest and never runs an economic engine.
"""

from __future__ import annotations

import argparse
import copy
import csv
import hashlib
import json
import os
import tempfile
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from scripts.research.acquire_r46_treasury_xml_v2_2026_09_10 import (
    RESULT_AVAILABILITY_POLICY,
    RESULT_SIGNAL_ELIGIBILITY,
    _event_id,
)
from scripts.research.build_r46_nyse_calendar_2026_09_10 import verify_receipt_file
from scripts.research.r46_treasury_auction_readiness_2026_09_10 import (
    CUTOFF_UTC,
    _cross_validate_tlt_data,
    normalize_auction_documents,
    validate_nyse_calendar,
    validate_tlt_bars,
    validate_tlt_dividends,
    validate_tlt_eligibility,
)

V2_DATA_PACKAGE_SCHEMA = "r46-treasury-auction-unwind-data-package-v2"
ACQUISITION_SCHEMA = "r46-tlt-market-data-acquisition-v2"
ARTIFACT_NAMES = ("documents", "bars", "dividends", "eligibility", "calendar")
RESEARCH_ID = "r46_treasury_auction_unwind_tlt_v2_2026_09_10"
REPO = Path(__file__).resolve().parents[2]
CAPACITY_PENDING = "PRE_RUN_CAPACITY_PENDING"
TREASURY_XML_RECEIPT_SCHEMA = "r46-treasury-xml-acquisition-v2"
TREASURY_XML_AUDIT_SCHEMA = "r46-treasury-xml-acquisition-audit-v1"
PUBLIC_SOURCE_RECEIPT_SCHEMA = "r46-treasury-public-source-evidence-receipt-v1"
TREASURY_DOCUMENTS_SCHEMA = "r46-treasury-auction-documents-v2"
CALENDAR_RECEIPT_SCHEMA = "r46-nyse-regular-calendar-receipt-v1"
CALENDAR_ARTIFACT_SCHEMA = "r46-nyse-regular-calendar-v1"
IDENTITY_SOURCE_SCHEMA = "r46-tlt-historical-identity-evidence-v1"
PUBLICATION_AVAILABILITY_EVIDENCE_SCHEMA = "r46-treasury-publication-availability-evidence-v2"
PUBLICATION_POLICY_CANONICALIZATION = "sorted unique event IDs encoded as compact UTF-8 JSON array"
PUBLICATION_POLICY_FIELDS = frozenset(
    {
        "result_availability_policy",
        "result_signal_eligibility",
        "same_day_signal_allowed",
        "actual_release_time_role",
        "server_last_modified_is_publication_proof",
        "expected_event_count",
        "expected_event_ids",
        "expected_event_ids_sha256",
        "expected_event_ids_digest",
        "missing_release",
        "source_receipt",
        "source_evidence",
        "canonicalization",
    }
)
SCHEMA_VALIDATED = "SCHEMA_VALIDATED"
HISTORICAL_IDENTITY_COMPATIBILITY_HOSTS = frozenset(
    {
        "paper-api.alpaca.markets",
        "sec.gov",
        "www.sec.gov",
        "nasdaqtrader.com",
        "www.nasdaqtrader.com",
        "ir.nasdaq.com",
    }
)


class DataPackageContractError(ValueError):
    """Raised when an input cannot prove the R46 v2 package contract."""


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _canonical_hash(payload: Mapping[str, Any], field: str) -> str:
    copied = json.loads(json.dumps(payload, ensure_ascii=False))
    integrity = copied.get("integrity")
    if not isinstance(integrity, dict):
        raise DataPackageContractError("integrity object missing")
    integrity.pop(field, None)
    encoded = json.dumps(copied, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _safe_path(root: Path, value: Path | str, field: str) -> Path:
    path = Path(value)
    candidate = path if path.is_absolute() else root / path
    cursor = candidate
    while cursor != Path(cursor.anchor):
        if cursor.is_symlink():
            raise DataPackageContractError(f"{field} symlink forbidden")
        cursor = cursor.parent
    root_resolved = root.resolve()
    try:
        resolved = candidate.resolve(strict=True)
    except (OSError, ValueError) as exc:
        raise DataPackageContractError(f"{field} missing") from exc
    if resolved == root_resolved or root_resolved not in resolved.parents or not resolved.is_file():
        raise DataPackageContractError(f"{field} outside repo root")
    return resolved


def _resolve_receipt_reference(receipt_path: Path, value: Any, *, root: Path, field: str) -> Path:
    """Resolve a receipt-relative path without escaping the package root."""

    if not isinstance(value, str) or not value.strip():
        raise DataPackageContractError(f"{field} path missing")
    declared = Path(value)
    if declared.is_absolute():
        return _safe_path(root, declared, field)
    candidates = (receipt_path.parent / declared, root / declared)
    last_error: DataPackageContractError | None = None
    for candidate in candidates:
        try:
            return _safe_path(root, candidate, field)
        except DataPackageContractError as exc:
            last_error = exc
    raise last_error or DataPackageContractError(f"{field} missing")


def _resolve_document_raw_path(documents_path: Path, value: Any, *, root: Path, field: str) -> Path:
    """Resolve XML-acquisition output-relative raw paths or package-root paths."""

    if not isinstance(value, str) or not value.strip():
        raise DataPackageContractError(f"{field} path missing")
    declared = Path(value)
    if declared.is_absolute():
        return _safe_path(root, declared, field)
    candidates = (documents_path.parent / declared, root / declared)
    last_error: DataPackageContractError | None = None
    for candidate in candidates:
        try:
            return _safe_path(root, candidate, field)
        except DataPackageContractError as exc:
            last_error = exc
    raise last_error or DataPackageContractError(f"{field} missing")


def _safe_destination(root: Path, value: Path | str, field: str) -> Path:
    path = Path(value)
    candidate = path if path.is_absolute() else root / path
    root_resolved = root.resolve()
    existing_parent = candidate.parent
    while not existing_parent.exists() and existing_parent != Path(existing_parent.anchor):
        existing_parent = existing_parent.parent
    if existing_parent.is_symlink():
        raise DataPackageContractError(f"{field} parent symlink forbidden")
    try:
        parent = existing_parent.resolve(strict=True)
    except (OSError, ValueError) as exc:
        raise DataPackageContractError(f"{field} parent missing") from exc
    if parent != root_resolved and root_resolved not in parent.parents:
        raise DataPackageContractError(f"{field} outside repo root")
    cursor = candidate
    while cursor != Path(cursor.anchor):
        if cursor.is_symlink():
            raise DataPackageContractError(f"{field} symlink forbidden")
        cursor = cursor.parent
    if candidate.exists() or candidate.is_symlink():
        raise FileExistsError(candidate)
    return candidate


def _atomic_publish(path: Path, content: bytes) -> Path:
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
        directory_descriptor = os.open(path.parent, os.O_RDONLY)
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
    return path


def _relative(root: Path, path: Path) -> str:
    try:
        return str(path.resolve().relative_to(root.resolve()))
    except ValueError as exc:
        raise DataPackageContractError("artifact path outside repo root") from exc


def _load_json(path: Path, field: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise DataPackageContractError(f"{field} JSON invalid") from exc
    if not isinstance(value, dict):
        raise DataPackageContractError(f"{field} JSON object required")
    return value


def _load_jsonl(path: Path, field: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise DataPackageContractError(f"{field} unreadable") from exc
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except (ValueError, json.JSONDecodeError) as exc:
            raise DataPackageContractError(f"{field} JSONL invalid:{line_number}") from exc
        if not isinstance(value, dict):
            raise DataPackageContractError(f"{field} JSONL row invalid:{line_number}")
        rows.append(value)
    return rows


def _load_csv(path: Path, field: str) -> list[dict[str, Any]]:
    try:
        with path.open(newline="", encoding="utf-8") as handle:
            return [dict(row) for row in csv.DictReader(handle)]
    except (OSError, csv.Error) as exc:
        raise DataPackageContractError(f"{field} CSV invalid") from exc


def _parse_aware(value: Any, field: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise DataPackageContractError(f"{field} missing")
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise DataPackageContractError(f"{field} timestamp invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise DataPackageContractError(f"{field} timezone required")
    parsed = parsed.astimezone(UTC)
    cutoff = datetime.fromisoformat(CUTOFF_UTC.replace("Z", "+00:00"))
    if parsed > cutoff:
        raise DataPackageContractError(f"{field} post_cutoff")
    return parsed


def _source_vintage(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DataPackageContractError(f"{field} source_vintage missing")
    normalized = value.strip()
    if normalized.lower() in {"current", "latest", "unknown", "unset", "none"}:
        raise DataPackageContractError(f"{field} source_vintage is not frozen")
    return normalized


def _validate_metadata(name: str, value: Any) -> dict[str, str]:
    if not isinstance(value, Mapping):
        raise DataPackageContractError(f"{name} metadata missing")
    vintage = _source_vintage(value.get("source_vintage"), name)
    available = _parse_aware(value.get("available_at"), f"{name} available_at")
    return {
        "source_vintage": vintage,
        "available_at": available.isoformat().replace("+00:00", "Z"),
    }


def _validate_governance(path: Path, *, root: Path) -> dict[str, Any]:
    governance = _load_json(path, "governance_manifest")
    if governance.get("research_id") != RESEARCH_ID or governance.get("candidate_id") != "R46":
        raise DataPackageContractError("governance identity mismatch")
    if "historical_cutoff" in governance and governance["historical_cutoff"] != CUTOFF_UTC:
        raise DataPackageContractError("governance historical_cutoff mismatch")
    integrity = governance.get("integrity")
    declared = integrity.get("manifest_sha256") if isinstance(integrity, Mapping) else None
    if not isinstance(declared, str) or len(declared) != 64 or any(char not in "0123456789abcdef" for char in declared):
        raise DataPackageContractError("governance canonical manifest hash missing")
    if declared != _canonical_hash(governance, "manifest_sha256"):
        raise DataPackageContractError("governance canonical manifest hash mismatch")
    return governance


def _is_explicit_test_fixture(governance: Mapping[str, Any]) -> bool:
    """Allow tiny unit fixtures to omit the future governance event-universe block."""

    return governance.get("test_fixture") is True or governance.get("fixture_mode") == "test"


def _governance_event_universe_block(governance: Mapping[str, Any]) -> Mapping[str, Any] | None:
    """Locate the governance-pinned event universe without accepting caller IDs."""

    candidates: list[Any] = [
        governance.get("event_universe"),
        # The current governance builder emits this block while the dedicated
        # event-universe block is being introduced.  It is equally immutable
        # because it is covered by the governance manifest self-hash.
        governance.get("publication_availability_policy"),
    ]
    for parent in ("event_contract", "data_contract", "source_provenance"):
        nested = governance.get(parent)
        if isinstance(nested, Mapping):
            candidates.extend((nested.get("event_universe"), nested.get("pinned_event_universe")))
            if any(
                key in nested
                for key in (
                    "event_ids",
                    "expected_event_ids",
                    "canonical_event_ids",
                    "event_count",
                    "expected_event_count",
                )
            ):
                candidates.append(nested)
    for candidate in candidates:
        if isinstance(candidate, Mapping):
            return candidate
    return None


def _governance_event_universe_ids(governance: Mapping[str, Any], *, root: Path) -> list[str] | None:
    """Return the hash-pinned governance event IDs, or ``None`` for explicit fixtures.

    Production governance must provide one of ``event_universe`` or
    ``event_contract.pinned_event_universe`` (``data_contract`` is accepted for
    transition).  The block must contain IDs or a hash-pinned JSON source with
    an ``events`` list, plus a count and canonical sorted-list digest.
    """

    block = _governance_event_universe_block(governance)
    if block is None:
        if _is_explicit_test_fixture(governance):
            return None
        raise DataPackageContractError("governance event universe pin missing")

    ids_value: Any = block.get("event_ids")
    if ids_value is None:
        ids_value = block.get("expected_event_ids")
    if ids_value is None:
        ids_value = block.get("canonical_event_ids", block.get("ids"))
    source = block.get("source") if isinstance(block.get("source"), Mapping) else None
    path_value = (
        block.get("path")
        or block.get("source_path")
        or block.get("source_receipt_path")
        or (source.get("path") if source is not None else None)
    )
    declared_source_hash = (
        block.get("sha256")
        or block.get("source_sha256")
        or block.get("raw_sha256")
        or (source.get("sha256") if source is not None else None)
        or (source.get("raw_sha256") if source is not None else None)
    )
    if ids_value is None and isinstance(block.get("events"), list):
        ids_value = [item.get("canonical_event_id") if isinstance(item, Mapping) else item for item in block["events"]]
    if isinstance(path_value, str) and path_value.strip():
        source_path = _safe_path(root, path_value, "governance event universe source")
        if declared_source_hash is not None and (not isinstance(declared_source_hash, str) or sha256(source_path) != declared_source_hash):
            raise DataPackageContractError("governance event universe source hash mismatch")
        source_payload = _load_json(source_path, "governance event universe source")
        source_ids = source_payload.get("event_ids", source_payload.get("expected_event_ids"))
        events = source_payload.get("events")
        if source_ids is None and isinstance(events, list):
            source_ids = [item.get("canonical_event_id") if isinstance(item, Mapping) else item for item in events]
            if any(value is None for value in source_ids):
                try:
                    source_ids = [_event_id(item) for item in events if isinstance(item, Mapping)]
                except (DataPackageContractError, TypeError, ValueError) as exc:
                    raise DataPackageContractError("governance event universe source event IDs invalid") from exc
        if not isinstance(source_ids, list):
            raise DataPackageContractError("governance event universe source events missing")
        if ids_value is not None and canonical_event_ids(ids_value) != canonical_event_ids(source_ids):
            raise DataPackageContractError("governance event universe source disagrees with IDs")
        ids_value = source_ids
    if ids_value is None:
        raise DataPackageContractError("governance event universe IDs missing")
    ids = canonical_event_ids(ids_value)
    count = block.get("event_count", block.get("expected_event_count"))
    if count is None:
        count = block.get("count", block.get("ids_count"))
    if count is None:
        count = len(ids)
    if not isinstance(count, int) or isinstance(count, bool) or count != len(ids):
        raise DataPackageContractError("governance event universe count mismatch")
    declared_digest = (
        block.get("canonical_digest_sha256")
        or block.get("event_ids_digest_sha256")
        or block.get("expected_event_ids_sha256")
        or block.get("expected_event_ids_digest")
        or block.get("event_id_digest_sha256")
        or block.get("event_universe_digest_sha256")
        or block.get("ids_sha256")
        or block.get("digest_sha256")
    )
    if not isinstance(declared_digest, str) or declared_digest != canonical_event_id_digest(ids):
        raise DataPackageContractError("governance event universe digest mismatch")
    return ids


def _validate_publication_availability_policy(
    governance: Mapping[str, Any],
    *,
    root: Path,
    treasury_receipt_path: Path | None = None,
    documents_path: Path | None = None,
    treasury_summary: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate the immutable governance policy copied into the package."""

    raw_policy = governance.get("publication_availability_policy")
    if not isinstance(raw_policy, Mapping) or set(raw_policy) != PUBLICATION_POLICY_FIELDS:
        raise DataPackageContractError("publication availability policy fields invalid")
    policy = copy.deepcopy(dict(raw_policy))
    if (
        policy.get("result_availability_policy") != RESULT_AVAILABILITY_POLICY
        or policy.get("result_signal_eligibility") != RESULT_SIGNAL_ELIGIBILITY
        or policy.get("same_day_signal_allowed") is not False
        or policy.get("actual_release_time_role") != "evidence_only"
        or policy.get("server_last_modified_is_publication_proof") is not False
        or policy.get("canonicalization") != PUBLICATION_POLICY_CANONICALIZATION
    ):
        raise DataPackageContractError("publication availability policy values invalid")

    try:
        policy_ids = canonical_event_ids(policy.get("expected_event_ids"))
    except DataPackageContractError as exc:
        raise DataPackageContractError("publication availability policy event IDs invalid") from exc
    if policy.get("expected_event_ids") != policy_ids:
        raise DataPackageContractError("publication availability policy event IDs not sorted unique")
    if (
        not isinstance(policy.get("expected_event_count"), int)
        or isinstance(policy.get("expected_event_count"), bool)
        or policy["expected_event_count"] != len(policy_ids)
        or policy.get("expected_event_ids_sha256") != canonical_event_id_digest(policy_ids)
        or policy.get("expected_event_ids_digest") != canonical_event_id_digest(policy_ids)
    ):
        raise DataPackageContractError("publication availability policy event digest invalid")

    missing = policy.get("missing_release")
    if not isinstance(missing, Mapping) or set(missing) != {
        "expected_count",
        "expected_event_ids",
        "expected_event_ids_sha256",
        "expected_event_ids_digest",
        "derivation",
        "source_field",
    }:
        raise DataPackageContractError("publication availability policy missing-release fields invalid")
    try:
        missing_ids = canonical_event_ids(missing.get("expected_event_ids"))
    except DataPackageContractError as exc:
        raise DataPackageContractError("publication availability policy missing-release IDs invalid") from exc
    missing_digest = canonical_event_id_digest(missing_ids)
    if (
        missing.get("expected_event_ids") != missing_ids
        or not isinstance(missing.get("expected_count"), int)
        or isinstance(missing.get("expected_count"), bool)
        or missing["expected_count"] != len(missing_ids)
        or missing.get("expected_event_ids_sha256") != missing_digest
        or missing.get("expected_event_ids_digest") != missing_digest
        or not set(missing_ids).issubset(policy_ids)
        or missing.get("derivation") != "normalized_result_rows_published_at_null"
        or missing.get("source_field") != "result.published_at"
    ):
        raise DataPackageContractError("publication availability policy missing-release values invalid")

    source_receipt = policy.get("source_receipt")
    if not isinstance(source_receipt, Mapping) or set(source_receipt) != {"path", "schema", "raw_sha256"}:
        raise DataPackageContractError("publication availability policy source receipt invalid")
    source_receipt_value = source_receipt.get("path")
    if source_receipt.get("schema") != PUBLIC_SOURCE_RECEIPT_SCHEMA or not isinstance(source_receipt_value, str):
        raise DataPackageContractError("publication availability policy source receipt schema invalid")
    source_receipt_path = _safe_path(root, source_receipt_value, "publication policy source receipt")
    if source_receipt.get("raw_sha256") != sha256(source_receipt_path):
        raise DataPackageContractError("publication availability policy source receipt hash invalid")
    public_receipt = _load_json(source_receipt_path, "publication policy source receipt")
    if public_receipt.get("schema") != PUBLIC_SOURCE_RECEIPT_SCHEMA:
        raise DataPackageContractError("publication availability policy source receipt content invalid")
    public_events = public_receipt.get("events")
    if not isinstance(public_events, list) or any(not isinstance(event, Mapping) for event in public_events):
        raise DataPackageContractError("publication availability policy source receipt events invalid")
    try:
        public_ids = canonical_event_ids([_event_id(event) for event in public_events if isinstance(event, Mapping)])
    except (DataPackageContractError, TypeError, ValueError) as exc:
        raise DataPackageContractError("publication availability policy source receipt event IDs invalid") from exc
    if public_ids != policy_ids:
        raise DataPackageContractError("publication availability policy source receipt event crosslink invalid")

    source_evidence = policy.get("source_evidence")
    if not isinstance(source_evidence, Mapping) or set(source_evidence) != {"path", "schema", "raw_sha256", "byte_size"}:
        raise DataPackageContractError("publication availability policy source evidence invalid")
    source_evidence_value = source_evidence.get("path")
    if source_evidence.get("schema") != PUBLICATION_AVAILABILITY_EVIDENCE_SCHEMA or not isinstance(source_evidence_value, str):
        raise DataPackageContractError("publication availability policy source evidence schema invalid")
    source_evidence_path = _safe_path(root, source_evidence_value, "publication policy source evidence")
    if (
        source_evidence.get("raw_sha256") != sha256(source_evidence_path)
        or not isinstance(source_evidence.get("byte_size"), int)
        or isinstance(source_evidence.get("byte_size"), bool)
        or source_evidence["byte_size"] != source_evidence_path.stat().st_size
    ):
        raise DataPackageContractError("publication availability policy source evidence hash invalid")
    evidence = _load_json(source_evidence_path, "publication policy source evidence")
    if evidence.get("schema") != PUBLICATION_AVAILABILITY_EVIDENCE_SCHEMA or evidence.get("status") != "COMPLETE":
        raise DataPackageContractError("publication availability policy source evidence content invalid")
    evidence_integrity = evidence.get("integrity")
    evidence_declared = evidence_integrity.get("evidence_sha256") if isinstance(evidence_integrity, Mapping) else None
    if not isinstance(evidence_declared, str) or evidence_declared != _canonical_hash(evidence, "evidence_sha256"):
        raise DataPackageContractError("publication availability policy source evidence self-hash invalid")
    if any(
        evidence.get(field) != policy.get(field)
        for field in (
            "result_availability_policy",
            "result_signal_eligibility",
            "same_day_signal_allowed",
            "actual_release_time_role",
            "server_last_modified_is_publication_proof",
            "expected_event_count",
            "expected_event_ids",
            "expected_event_ids_sha256",
            "expected_event_ids_digest",
        )
    ):
        raise DataPackageContractError("publication availability policy source evidence event crosslink invalid")
    evidence_missing = evidence.get("missing_release")
    if not isinstance(evidence_missing, Mapping) or any(
        evidence_missing.get(field) != missing.get(field)
        for field in (
            "expected_count",
            "expected_event_ids",
            "expected_event_ids_sha256",
            "expected_event_ids_digest",
            "derivation",
            "source_field",
        )
    ):
        raise DataPackageContractError("publication availability policy source evidence missing crosslink invalid")

    evidence_sources = evidence.get("sources")
    if not isinstance(evidence_sources, Mapping) or set(evidence_sources) != {"receipt", "documents"}:
        raise DataPackageContractError("publication availability policy source evidence sources invalid")
    evidence_receipt = evidence_sources.get("receipt")
    evidence_documents = evidence_sources.get("documents")
    if (
        not isinstance(evidence_receipt, Mapping)
        or set(evidence_receipt) != {"path", "schema", "raw_sha256", "byte_size"}
        or not isinstance(evidence_documents, Mapping)
        or set(evidence_documents) != {"path", "schema", "raw_sha256", "byte_size"}
    ):
        raise DataPackageContractError("publication availability policy source evidence source fields invalid")

    def validate_evidence_source(
        source: Mapping[str, Any],
        *,
        field: str,
        expected_path: Path | None,
        expected_schema: str,
    ) -> Path:
        value = source.get("path")
        if not isinstance(value, str) or source.get("schema") != expected_schema:
            raise DataPackageContractError(f"publication availability policy {field} source invalid")
        path = _safe_path(root, value, f"publication policy {field} source")
        if expected_path is not None and path != expected_path:
            raise DataPackageContractError(f"publication availability policy {field} source crosslink invalid")
        if (
            source.get("raw_sha256") != sha256(path)
            or not isinstance(source.get("byte_size"), int)
            or isinstance(source.get("byte_size"), bool)
            or source["byte_size"] != path.stat().st_size
        ):
            raise DataPackageContractError(f"publication availability policy {field} source hash invalid")
        return path

    if documents_path is None and isinstance(treasury_summary, Mapping):
        declared_documents = treasury_summary.get("documents_path")
        if isinstance(declared_documents, str):
            documents_path = _safe_path(root, declared_documents, "publication policy documents")
    evidence_receipt_path = validate_evidence_source(
        evidence_receipt,
        field="receipt",
        expected_path=treasury_receipt_path,
        expected_schema=TREASURY_XML_RECEIPT_SCHEMA,
    )
    evidence_documents_path = validate_evidence_source(
        evidence_documents,
        field="documents",
        expected_path=documents_path,
        expected_schema=TREASURY_DOCUMENTS_SCHEMA,
    )

    if treasury_summary is not None:
        if treasury_receipt_path is None or documents_path is None:
            raise DataPackageContractError("publication availability policy Treasury source paths missing")
        if evidence_receipt_path != treasury_receipt_path or evidence_documents_path != documents_path:
            raise DataPackageContractError("publication availability policy Treasury source crosslink invalid")
        if (
            policy_ids != treasury_summary.get("expected_event_ids")
            or policy["expected_event_count"] != treasury_summary.get("expected_event_count")
        ):
            raise DataPackageContractError("publication availability policy event crosslink invalid")
        if (
            evidence_receipt.get("raw_sha256") != treasury_summary.get("sha256")
            or evidence_receipt.get("byte_size") != treasury_receipt_path.stat().st_size
            or evidence_documents.get("raw_sha256") != treasury_summary.get("documents_sha256")
            or evidence_documents.get("byte_size") != documents_path.stat().st_size
        ):
            raise DataPackageContractError("publication availability policy Treasury source hash crosslink invalid")
        receipt_evidence = treasury_summary.get("publication_evidence")
        if not isinstance(receipt_evidence, Mapping):
            raise DataPackageContractError("publication availability policy receipt evidence missing")
        try:
            receipt_missing_ids = canonical_event_ids(receipt_evidence.get("result_release_time_missing_event_ids"))
        except DataPackageContractError as exc:
            raise DataPackageContractError("publication availability policy receipt missing IDs invalid") from exc
        if missing_ids != receipt_missing_ids or missing["expected_count"] != len(receipt_missing_ids):
            raise DataPackageContractError("publication availability policy missing-release crosslink invalid")

    return policy


def _validate_acquisition_receipt(path: Path, *, root: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    receipt = _load_json(path, "acquisition_receipt")
    if receipt.get("schema") != ACQUISITION_SCHEMA:
        raise DataPackageContractError("acquisition_receipt schema invalid")
    if receipt.get("research_id") not in (None, RESEARCH_ID):
        raise DataPackageContractError("acquisition receipt research identity invalid")
    if receipt.get("status") != "COMPLETE":
        raise DataPackageContractError("acquisition_receipt not complete")
    if receipt.get("acquisition_mode") != "retrospective" or receipt.get("retrospective_acquisition") is not True:
        raise DataPackageContractError("acquisition_mode must be retrospective")
    if receipt.get("historical_cutoff") != CUTOFF_UTC:
        raise DataPackageContractError("acquisition historical_cutoff mismatch")
    if receipt.get("economic_execution") is not False:
        raise DataPackageContractError("acquisition economic boundary invalid")
    if receipt.get("raw_split_key_parity") is not True:
        raise DataPackageContractError("raw_split_key_parity is not proven")
    integrity = receipt.get("integrity")
    declared = integrity.get("receipt_sha256") if isinstance(integrity, Mapping) else None
    if not isinstance(declared, str) or len(declared) != 64 or any(char not in "0123456789abcdef" for char in declared):
        raise DataPackageContractError("acquisition receipt self-hash missing")
    if declared != _canonical_hash(receipt, "receipt_sha256"):
        raise DataPackageContractError("acquisition receipt self-hash mismatch")
    artifacts = receipt.get("artifacts")
    if not isinstance(artifacts, Mapping):
        raise DataPackageContractError("acquisition artifacts missing")
    checked: dict[str, Any] = {}

    def resolve_receipt_artifact(value: Any, field: str) -> Path:
        try:
            return _safe_path(root, str(value), field)
        except DataPackageContractError:
            relative = Path(str(value))
            if relative.is_absolute() or ".." in relative.parts:
                raise
            try:
                receipt_relative_parent = path.parent.resolve().relative_to(root.resolve())
            except ValueError as exc:
                raise DataPackageContractError(f"{field} outside repo root") from exc
            return _safe_path(root, receipt_relative_parent / relative, field)

    for name in ("raw_bars", "split_bars"):
        spec = artifacts.get(name)
        if not isinstance(spec, Mapping):
            raise DataPackageContractError(f"acquisition {name} missing")
        artifact_path = resolve_receipt_artifact(spec.get("path", ""), f"acquisition {name}")
        expected_hash = spec.get("sha256")
        if not isinstance(expected_hash, str) or sha256(artifact_path) != expected_hash:
            raise DataPackageContractError(f"acquisition {name} hash mismatch")
        checked[name] = {"path": _relative(root, artifact_path), "sha256": expected_hash}
    for name in ("dividends", "eligibility"):
        spec = artifacts.get(name)
        if not isinstance(spec, Mapping) or not spec.get("path"):
            continue
        artifact_path = resolve_receipt_artifact(spec["path"], f"acquisition {name}")
        expected_hash = spec.get("sha256")
        if not isinstance(expected_hash, str) or sha256(artifact_path) != expected_hash:
            raise DataPackageContractError(f"acquisition {name} hash mismatch")
        checked[name] = {"path": _relative(root, artifact_path), "sha256": expected_hash}
    for name in ("dividends_source", "identity_source", "identity_provider_source"):
        spec = receipt.get(name)
        if not isinstance(spec, Mapping):
            raise DataPackageContractError(f"acquisition {name} missing")
        artifact_path = resolve_receipt_artifact(spec.get("path", ""), f"acquisition {name}")
        expected_hash = spec.get("sha256")
        if not isinstance(expected_hash, str) or sha256(artifact_path) != expected_hash:
            raise DataPackageContractError(f"acquisition {name} hash mismatch")
        checked[name] = {"path": _relative(root, artifact_path), "sha256": expected_hash}
    checked["raw_split_key_parity"] = True
    return receipt, checked


def _validate_calendar_receipt(path: Path, *, calendar_path: Path, root: Path) -> dict[str, Any]:
    """Verify the calendar sidecar against the exact five-artifact calendar."""

    receipt = _load_json(path, "calendar_receipt")
    if receipt.get("schema") != CALENDAR_RECEIPT_SCHEMA or receipt.get("status") != "COMPLETE":
        raise DataPackageContractError("calendar receipt schema/status invalid")
    if receipt.get("artifact_schema") != CALENDAR_ARTIFACT_SCHEMA or receipt.get("calendar") != "NYSE" or receipt.get("venue") != "XNYS":
        raise DataPackageContractError("calendar receipt identity invalid")
    if receipt.get("economic_execution") is not False or receipt.get("economic_data") is not False or receipt.get("network_used") is not False:
        raise DataPackageContractError("calendar receipt economic/network boundary invalid")
    integrity = receipt.get("integrity")
    declared = integrity.get("receipt_sha256") if isinstance(integrity, Mapping) else None
    if not isinstance(declared, str) or len(declared) != 64 or any(char not in "0123456789abcdef" for char in declared):
        raise DataPackageContractError("calendar receipt self-hash missing")
    if declared != _canonical_hash(receipt, "receipt_sha256"):
        raise DataPackageContractError("calendar receipt self-hash mismatch")
    records = receipt.get("records")
    if not isinstance(records, Mapping):
        raise DataPackageContractError("calendar receipt records missing")
    declared_hash = records.get("sha256")
    if records.get("raw_sha256") != declared_hash or not isinstance(declared_hash, str) or len(declared_hash) != 64 or any(char not in "0123456789abcdef" for char in declared_hash):
        raise DataPackageContractError("calendar receipt records hash invalid")
    declared_count = records.get("row_count")
    if not isinstance(declared_count, int) or isinstance(declared_count, bool) or declared_count < 1:
        raise DataPackageContractError("calendar receipt row count invalid")
    declared_path = _resolve_receipt_reference(path, records.get("path"), root=root, field="calendar receipt records")
    if declared_path != calendar_path:
        raise DataPackageContractError("calendar receipt records path mismatch")
    if sha256(calendar_path) != declared_hash:
        raise DataPackageContractError("calendar receipt records hash mismatch")
    rows = _load_jsonl(calendar_path, "calendar")
    if len(rows) != declared_count:
        raise DataPackageContractError("calendar receipt row count mismatch")
    if receipt.get("records_path") != records.get("path") or receipt.get("records_sha256") != declared_hash or receipt.get("row_count") != declared_count:
        raise DataPackageContractError("calendar receipt records alias mismatch")
    try:
        verified = verify_receipt_file(path, records_path=calendar_path, repo=root)
    except (OSError, TypeError, ValueError, KeyError) as exc:
        raise DataPackageContractError(f"calendar receipt provenance invalid: {exc}") from exc
    return {
        "path": _relative(root, path),
        "sha256": sha256(path),
        "records_path": _relative(root, calendar_path),
        "records_sha256": declared_hash,
        "rows": verified["rows"],
        "available_at": receipt["available_at"],
    }


def _validate_treasury_xml_receipt(
    path: Path,
    *,
    documents_path: Path,
    root: Path,
    expected_event_count: int | None,
    expected_event_ids: Sequence[str] | None,
) -> dict[str, Any]:
    """Verify Treasury XML/XSD bytes, document lineage, and the receipt pin."""

    receipt = _load_json(path, "treasury_xml_receipt")
    if receipt.get("schema") != TREASURY_XML_RECEIPT_SCHEMA or receipt.get("status") != "COMPLETE":
        raise DataPackageContractError("treasury XML receipt schema/status invalid")
    if receipt.get("research_id") != RESEARCH_ID or receipt.get("candidate_id") != "R46":
        raise DataPackageContractError("treasury XML receipt identity invalid")
    if receipt.get("historical_cutoff") != CUTOFF_UTC or receipt.get("economic_execution") is not False or receipt.get("economic_run") is not False:
        raise DataPackageContractError("treasury XML receipt boundary invalid")
    if receipt.get("economic_fields_filtered") is not True:
        raise DataPackageContractError("treasury XML economic-field filter not proven")
    integrity = receipt.get("integrity")
    declared = integrity.get("receipt_sha256") if isinstance(integrity, Mapping) else None
    if not isinstance(declared, str) or len(declared) != 64 or any(char not in "0123456789abcdef" for char in declared):
        raise DataPackageContractError("treasury XML receipt self-hash missing")
    if declared != _canonical_hash(receipt, "receipt_sha256"):
        raise DataPackageContractError("treasury XML receipt self-hash mismatch")

    inventory = receipt.get("inventory")
    if not isinstance(inventory, Mapping) or inventory.get("pinned") is not True:
        raise DataPackageContractError("treasury XML inventory pin missing")
    inventory_path = _resolve_receipt_reference(path, inventory.get("path"), root=root, field="treasury inventory")
    inventory_hash = inventory.get("sha256")
    if not isinstance(inventory_hash, str) or len(inventory_hash) != 64 or sha256(inventory_path) != inventory_hash:
        raise DataPackageContractError("treasury inventory hash mismatch")
    inventory_payload = _load_json(inventory_path, "treasury inventory")
    inventory_events = inventory_payload.get("events")
    if not isinstance(inventory_events, list) or not inventory_events:
        raise DataPackageContractError("treasury inventory events missing")
    try:
        inventory_ids = canonical_event_ids([_event_id(event) for event in inventory_events if isinstance(event, Mapping)])
    except (DataPackageContractError, ValueError, TypeError) as exc:
        raise DataPackageContractError("treasury inventory event IDs invalid") from exc
    if len(inventory_ids) != len(inventory_events):
        raise DataPackageContractError("treasury inventory event IDs invalid")
    inventory_integrity = inventory_payload.get("integrity")
    nested_inventory_declared = inventory_integrity.get("inventory_sha256") if isinstance(inventory_integrity, Mapping) else None
    nested_inventory_pin = isinstance(nested_inventory_declared, str)
    if nested_inventory_pin:
        inventory_declared = nested_inventory_declared
        inventory_computed = _canonical_hash(inventory_payload, "inventory_sha256")
    else:
        inventory_declared = inventory_payload.get("artifact_sha256")
        inventory_computed = hashlib.sha256(
            json.dumps(
                {key: value for key, value in inventory_payload.items() if key != "artifact_sha256"},
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
    if not isinstance(inventory_declared, str) or len(inventory_declared) != 64 or inventory_declared != inventory_computed:
        raise DataPackageContractError("treasury inventory canonical pin invalid")

    declared_documents = _resolve_receipt_reference(path, receipt.get("documents_path"), root=root, field="treasury documents")
    if declared_documents != documents_path:
        raise DataPackageContractError("treasury documents path mismatch")
    documents_hash = receipt.get("documents_sha256")
    if not isinstance(documents_hash, str) or len(documents_hash) != 64 or sha256(documents_path) != documents_hash:
        raise DataPackageContractError("treasury documents hash mismatch")
    documents = _load_jsonl(documents_path, "documents")
    if not documents:
        raise DataPackageContractError("treasury documents empty")

    receipt_count = receipt.get("expected_event_count")
    receipt_ids_value = receipt.get("expected_event_ids")
    try:
        receipt_ids = canonical_event_ids(receipt_ids_value)
    except DataPackageContractError as exc:
        raise DataPackageContractError("treasury expected events invalid") from exc
    if not isinstance(receipt_count, int) or isinstance(receipt_count, bool) or receipt_count < 1 or len(receipt_ids) != receipt_count:
        raise DataPackageContractError("treasury expected events invalid")
    if expected_event_count is not None and receipt_count != expected_event_count:
        raise DataPackageContractError("treasury expected event count mismatch")
    if expected_event_ids is not None and receipt_ids != canonical_event_ids(expected_event_ids):
        raise DataPackageContractError("treasury expected event ids mismatch")
    if receipt_ids != inventory_ids:
        raise DataPackageContractError("treasury expected event IDs disagree with pinned inventory")

    announcement_ids = canonical_event_ids(
        [row.get("canonical_event_id") for row in documents if row.get("document_kind") == "announcement"],
    )
    result_ids = canonical_event_ids(
        [row.get("canonical_event_id") for row in documents if row.get("document_kind") == "result"],
    )
    if announcement_ids != inventory_ids or result_ids != inventory_ids:
        raise DataPackageContractError("treasury announcement/result event IDs do not match inventory")

    missing_release_ids = canonical_event_ids(
        [
            row.get("canonical_event_id")
            for row in documents
            if row.get("document_kind") == "result"
            and row.get("publication_time_proven") is False
            and row.get("published_at") is None
            and row.get("release_time") in (None, "")
        ]
    )
    for row in documents:
        if row.get("document_kind") != "result":
            continue
        if row.get("official_publication_date") != row.get("auction_session_date"):
            raise DataPackageContractError("treasury result official publication date basis invalid")
        if row.get("availability_derivation") != RESULT_AVAILABILITY_POLICY:
            raise DataPackageContractError("treasury result availability policy invalid")
        if row.get("result_signal_eligibility") != RESULT_SIGNAL_ELIGIBILITY:
            raise DataPackageContractError("treasury result next-session policy invalid")
        if row.get("same_day_signal_allowed") is not False:
            raise DataPackageContractError("treasury result same-day policy invalid")
        if row.get("published_at_evidence_only") is not True:
            raise DataPackageContractError("treasury result publication timestamp evidence semantics missing")
        proven = row.get("publication_time_proven")
        if not isinstance(proven, bool):
            raise DataPackageContractError("treasury result publication evidence state invalid")
        release_time = row.get("release_time")
        published_at = row.get("published_at")
        if proven is True and (not isinstance(release_time, str) or not release_time.strip() or not isinstance(published_at, str) or not published_at.strip()):
            raise DataPackageContractError("treasury result release-time evidence invalid")
        if proven is False and (release_time not in (None, "") or published_at is not None):
            raise DataPackageContractError("treasury result missing release evidence invalid")

    audit = receipt.get("acquisition_audit")
    if not isinstance(audit, Mapping):
        raise DataPackageContractError("treasury acquisition audit missing")
    audit_sets = {
        "inventory_event_ids": inventory_ids,
        "announcement_event_ids": announcement_ids,
        "result_event_ids": result_ids,
        "missing_release_time_event_ids": missing_release_ids,
    }
    for field, observed in audit_sets.items():
        declared_audit = audit.get(field)
        if not isinstance(declared_audit, Mapping):
            raise DataPackageContractError(f"treasury acquisition audit {field} missing")
        try:
            declared_ids = canonical_event_ids(declared_audit.get("ids"))
        except DataPackageContractError as exc:
            raise DataPackageContractError(f"treasury acquisition audit {field} invalid") from exc
        if declared_ids != observed or declared_audit.get("count") != len(observed) or declared_audit.get("canonical_digest_sha256") != canonical_event_id_digest(observed):
            raise DataPackageContractError(f"treasury acquisition audit {field} mismatch")
    if audit.get("event_id_sets_equal") is not True:
        raise DataPackageContractError("treasury acquisition audit event sets not equal")
    publication_evidence = receipt.get("publication_evidence")
    if not isinstance(publication_evidence, Mapping):
        raise DataPackageContractError("treasury publication evidence missing")
    receipt_missing = canonical_event_ids(publication_evidence.get("result_release_time_missing_event_ids"))
    if receipt_missing != missing_release_ids:
        raise DataPackageContractError("treasury missing release IDs do not crosslink normalized results")
    if publication_evidence.get("result_release_time_missing_event_count") != len(missing_release_ids):
        raise DataPackageContractError("treasury missing release count mismatch")
    if publication_evidence.get("result_release_time_missing_event_ids_canonical_digest_sha256") != canonical_event_id_digest(missing_release_ids):
        raise DataPackageContractError("treasury missing release digest mismatch")

    raw_artifacts = receipt.get("raw_artifacts")
    if not isinstance(raw_artifacts, list) or not raw_artifacts:
        raise DataPackageContractError("treasury raw XML artifacts missing")
    document_by_id = {row.get("document_id"): row for row in documents if isinstance(row.get("document_id"), str)}
    if len(document_by_id) != len(documents):
        raise DataPackageContractError("treasury document ids invalid")
    raw_ids: set[str] = set()
    checked_raw_artifacts: list[dict[str, Any]] = []
    for artifact in raw_artifacts:
        if not isinstance(artifact, Mapping):
            raise DataPackageContractError("treasury raw artifact invalid")
        document_id = artifact.get("document_id")
        if not isinstance(document_id, str) or document_id in raw_ids or document_id not in document_by_id:
            raise DataPackageContractError("treasury raw artifact lineage invalid")
        raw_ids.add(document_id)
        artifact_hash = artifact.get("sha256")
        if not isinstance(artifact_hash, str) or len(artifact_hash) != 64 or any(char not in "0123456789abcdef" for char in artifact_hash):
            raise DataPackageContractError("treasury raw artifact hash invalid")
        raw_path = _resolve_receipt_reference(path, artifact.get("path"), root=root, field="treasury raw XML")
        if sha256(raw_path) != artifact_hash:
            raise DataPackageContractError(f"treasury raw artifact hash mismatch:{document_id}")
        document = document_by_id[document_id]
        document_path = _resolve_document_raw_path(documents_path, document.get("raw_path"), root=root, field="treasury document raw")
        if document_path != raw_path or document.get("sha256") != artifact_hash:
            raise DataPackageContractError(f"treasury raw/document crosslink mismatch:{document_id}")
        for field in ("document_kind", "canonical_event_id", "revision_id", "supersedes_document_id"):
            if artifact.get(field) != document.get(field):
                raise DataPackageContractError(f"treasury raw/document lineage mismatch:{document_id}:{field}")
        checked_raw_artifacts.append(
            {
                "document_id": document_id,
                "document_kind": artifact["document_kind"],
                "canonical_event_id": artifact["canonical_event_id"],
                "path": _relative(root, raw_path),
                "sha256": artifact_hash,
                "revision_id": artifact["revision_id"],
                "supersedes_document_id": artifact["supersedes_document_id"],
            }
        )
    if raw_ids != set(document_by_id):
        raise DataPackageContractError("treasury raw artifact/document count mismatch")

    xsd_artifacts = receipt.get("xsd")
    if not isinstance(xsd_artifacts, list) or not xsd_artifacts:
        raise DataPackageContractError("treasury XSD artifacts missing")
    xsd_hashes: set[str] = set()
    validation_statuses: set[str] = set()
    for artifact in xsd_artifacts:
        if not isinstance(artifact, Mapping):
            raise DataPackageContractError("treasury XSD artifact invalid")
        xsd_hash = artifact.get("sha256")
        if not isinstance(xsd_hash, str) or len(xsd_hash) != 64 or any(char not in "0123456789abcdef" for char in xsd_hash):
            raise DataPackageContractError("treasury XSD hash invalid")
        xsd_path = _resolve_receipt_reference(path, artifact.get("path"), root=root, field="treasury XSD")
        if sha256(xsd_path) != xsd_hash:
            raise DataPackageContractError("treasury XSD hash mismatch")
        source_url = artifact.get("source_url")
        parsed = urlparse(source_url) if isinstance(source_url, str) else None
        if parsed is None or parsed.scheme != "https" or parsed.username or parsed.password or parsed.hostname not in {"treasurydirect.gov", "www.treasurydirect.gov"} or parsed.query or parsed.fragment or not parsed.path.startswith("/xsd/"):
            raise DataPackageContractError("treasury XSD source URL invalid")
        validation_status = artifact.get("validation_status")
        if validation_status != SCHEMA_VALIDATED:
            raise DataPackageContractError("treasury XSD schema validation not proven")
        if artifact.get("validation_tool") != "/usr/bin/xmllint" or not isinstance(artifact.get("validation_tool_version"), str) or not artifact["validation_tool_version"].strip():
            raise DataPackageContractError("treasury XSD validation provenance missing")
        validation_results = artifact.get("validation_results")
        if not isinstance(validation_results, list):
            raise DataPackageContractError("treasury XSD validation lineage missing")
        document_xsd_hashes = {str(document.get("document_id")): document.get("xsd_sha256") for document in documents}
        if any(value is not None for value in document_xsd_hashes.values()):
            if any(value is None for value in document_xsd_hashes.values()):
                raise DataPackageContractError("treasury XSD document crosslink missing")
            expected_validation_ids = {document_id for document_id, value in document_xsd_hashes.items() if value == xsd_hash}
        else:
            expected_validation_ids = set(document_xsd_hashes)
        validation_ids: set[str] = set()
        for validation_result in validation_results:
            if not isinstance(validation_result, Mapping):
                raise DataPackageContractError("treasury XSD validation lineage invalid")
            document_id = validation_result.get("document_id")
            if not isinstance(document_id, str) or document_id in validation_ids or document_id not in expected_validation_ids:
                raise DataPackageContractError("treasury XSD validation lineage invalid")
            if validation_result.get("validation_status") != SCHEMA_VALIDATED:
                raise DataPackageContractError("treasury XSD validation lineage not proven")
            validation_ids.add(document_id)
        if validation_ids != expected_validation_ids:
            raise DataPackageContractError("treasury XSD validation lineage incomplete")
        validation_statuses.add(validation_status)
        xsd_hashes.add(xsd_hash)
    for document in documents:
        document_xsd_hash = document.get("xsd_sha256")
        if document_xsd_hash is not None and document_xsd_hash not in xsd_hashes:
            raise DataPackageContractError("treasury document XSD crosslink mismatch")

    publication_basis = receipt.get("publication_basis")
    if not isinstance(publication_basis, Mapping) or publication_basis.get("announcement") != "conservative_end_of_day_preceding_auction_day" or publication_basis.get("result") != RESULT_AVAILABILITY_POLICY:
        raise DataPackageContractError("treasury publication basis invalid")
    if (
        publication_evidence.get("availability_derivation") != RESULT_AVAILABILITY_POLICY
        or publication_evidence.get("official_publication_date_basis") != "result_XML_AuctionDate"
        or publication_evidence.get("next_session_open_policy") != RESULT_SIGNAL_ELIGIBILITY
        or publication_evidence.get("same_day") is not False
        or publication_evidence.get("same_day_result_signals_allowed") is not False
        or publication_evidence.get("server_last_modified_is_publication_proof") is not False
        or publication_evidence.get("http_date_is_publication_proof") is not False
        or publication_evidence.get("retrieval_time_is_publication_proof") is not False
    ):
        raise DataPackageContractError("treasury publication evidence policy invalid")
    return {
        "path": _relative(root, path),
        "sha256": sha256(path),
        "documents_path": _relative(root, documents_path),
        "documents_sha256": documents_hash,
        "raw_artifact_count": len(raw_artifacts),
        "raw_artifacts": checked_raw_artifacts,
        "xsd": [
            {
                "source_url": artifact["source_url"],
                "sha256": artifact["sha256"],
                "schema_version": artifact.get("schema_version"),
                "validation_status": artifact["validation_status"],
                "validation_tool": artifact["validation_tool"],
                "validation_tool_version": artifact["validation_tool_version"],
                "validation_results": [dict(result) for result in artifact["validation_results"]],
            }
            for artifact in xsd_artifacts
        ],
        "validation_statuses": sorted(validation_statuses),
        "expected_event_count": receipt_count,
        "expected_event_ids": receipt_ids,
        "acquisition_audit": copy.deepcopy(dict(audit)),
        "publication_basis": {
            "announcement": publication_basis["announcement"],
            "result": publication_basis["result"],
        },
        "publication_evidence": {
            "availability_derivation": publication_evidence["availability_derivation"],
            "official_publication_date_basis": publication_evidence["official_publication_date_basis"],
            "next_session_open_policy": publication_evidence["next_session_open_policy"],
            "same_day": publication_evidence["same_day"],
            "same_day_result_signals_allowed": publication_evidence["same_day_result_signals_allowed"],
            "server_last_modified_is_publication_proof": publication_evidence["server_last_modified_is_publication_proof"],
            "http_date_is_publication_proof": publication_evidence["http_date_is_publication_proof"],
            "retrieval_time_is_publication_proof": publication_evidence["retrieval_time_is_publication_proof"],
            "result_release_time_missing_event_ids": missing_release_ids,
            "result_release_time_missing_event_count": len(missing_release_ids),
            "result_release_time_missing_event_ids_canonical_digest_sha256": canonical_event_id_digest(missing_release_ids),
        },
    }


def _validate_identity_source(receipt: Mapping[str, Any], *, receipt_path: Path, root: Path, eligibility_rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Verify the hash-pinned historical identity wrapper behind eligibility."""

    spec = receipt.get("identity_source")
    if not isinstance(spec, Mapping):
        raise DataPackageContractError("identity source receipt entry missing")
    path_value = spec.get("path")
    source_path = _resolve_receipt_reference(receipt_path, path_value, root=root, field="identity source")
    expected_hash = spec.get("sha256")
    if not isinstance(expected_hash, str) or len(expected_hash) != 64 or sha256(source_path) != expected_hash:
        raise DataPackageContractError("identity source hash mismatch")
    source = _load_json(source_path, "identity source")
    if source.get("schema") != IDENTITY_SOURCE_SCHEMA or not isinstance(source.get("records"), list) or not source["records"]:
        raise DataPackageContractError("identity source schema invalid")
    integrity = source.get("integrity")
    declared = integrity.get("artifact_sha256") if isinstance(integrity, Mapping) else None
    if not isinstance(declared, str) or declared != _canonical_hash(source, "artifact_sha256"):
        raise DataPackageContractError("identity source self-hash invalid")
    source_records = source["records"]
    if len(source_records) < 2 or list(source_records) != [dict(row) for row in eligibility_rows]:
        raise DataPackageContractError("official multi-interval identity not crosslinked")
    source_vintage = _source_vintage(source.get("source_vintage"), "identity source")

    provider_spec = receipt.get("identity_provider_source")
    if not isinstance(provider_spec, Mapping):
        raise DataPackageContractError("identity provider source receipt entry missing")
    provider_path = _resolve_receipt_reference(receipt_path, provider_spec.get("path"), root=root, field="identity provider source")
    provider_hash = provider_spec.get("sha256")
    if not isinstance(provider_hash, str) or len(provider_hash) != 64 or sha256(provider_path) != provider_hash:
        raise DataPackageContractError("identity provider source hash mismatch")
    provider = _load_json(provider_path, "identity provider source")
    provider_asset = provider.get("asset") if isinstance(provider.get("asset"), Mapping) else provider
    provider_symbol = provider_asset.get("symbol") if isinstance(provider_asset, Mapping) else None
    provider_id = provider_asset.get("id") if isinstance(provider_asset, Mapping) else None
    provider_status = provider_asset.get("status") if isinstance(provider_asset, Mapping) else None
    if provider_symbol != "TLT" or not isinstance(provider_id, str) or not provider_id.strip() or not isinstance(provider_status, str) or not provider_status.strip():
        raise DataPackageContractError("identity provider attestation invalid")
    if any(row.get("provider_asset_id") != provider_id.strip() for row in eligibility_rows):
        raise DataPackageContractError("identity provider/historical id mismatch")
    return {
        "path": _relative(root, source_path),
        "sha256": expected_hash,
        "rows": len(source_records),
        "source_vintage": source_vintage,
        "provider_asset_id": provider_id.strip(),
        "provider_source": {"path": _relative(root, provider_path), "sha256": provider_hash},
    }


def _validate_dividend_raw_hashes(
    receipt: Mapping[str, Any],
    *,
    receipt_path: Path,
    root: Path,
    dividend_rows: Sequence[Mapping[str, Any]],
    calendar_rows: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Recompute provider raw-action hashes used by normalized dividends."""

    spec = receipt.get("dividends_source")
    if not isinstance(spec, Mapping):
        raise DataPackageContractError("dividends source receipt entry missing")
    source_path = _resolve_receipt_reference(receipt_path, spec.get("path"), root=root, field="dividends source")
    expected_hash = spec.get("sha256")
    if not isinstance(expected_hash, str) or len(expected_hash) != 64 or sha256(source_path) != expected_hash:
        raise DataPackageContractError("dividends source hash mismatch")
    source = _load_json(source_path, "dividends source")
    actions = source.get("corporate_actions", source.get("dividends"))
    if not isinstance(actions, list):
        raise DataPackageContractError("dividends source actions missing")
    by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for action in actions:
        if not isinstance(action, Mapping):
            raise DataPackageContractError("dividends source action invalid")
        action_id = action.get("id") or action.get("corporate_action_id")
        ex_date = action.get("ex_date")
        if not isinstance(action_id, str) or not action_id.strip() or not isinstance(ex_date, str) or not ex_date.strip():
            raise DataPackageContractError("dividends source action identity missing")
        encoded = json.dumps(dict(action), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        key = (action_id.strip(), ex_date.strip())
        if key in by_key:
            raise DataPackageContractError("duplicate dividends source action")
        raw_symbol = action.get("symbol") or action.get("initiating_symbol") or action.get("target_symbol")
        if raw_symbol != "TLT":
            raise DataPackageContractError("dividend source symbol mismatch")
        raw_amount = action.get("amount", action.get("cash_or_ratio", action.get("cash", action.get("rate"))))
        try:
            amount = float(raw_amount)
        except (TypeError, ValueError) as exc:
            raise DataPackageContractError("dividend source amount invalid") from exc
        currency = action.get("currency")
        if currency is None:
            # The frozen acquisition receipt explicitly records that this
            # provider's raw action feed has no currency column and that the
            # package contract derives USD.  Treat that declaration as the
            # source value; any other missing/ambiguous currency remains
            # rejected below.
            contract = receipt.get("dividends_contract") or receipt.get("corporate_action_normalization_basis")
            if isinstance(contract, Mapping) and contract.get("currency") == "USD_contract_derived":
                currency = "USD"
        if currency != "USD":
            raise DataPackageContractError("dividend source currency mismatch")
        by_key[key] = {
            "hash": hashlib.sha256(encoded).hexdigest(),
            "symbol": raw_symbol,
            "amount": amount,
            "currency": currency,
            "effective_date": ex_date.strip(),
            "permanent_security_identifier": action.get("permanent_security_identifier"),
        }
    calendar_by_date: dict[str, str] = {}
    for calendar_row in calendar_rows or ():
        if not isinstance(calendar_row, Mapping):
            raise DataPackageContractError("dividend calendar row invalid")
        calendar_date = calendar_row.get("regular_session_date")
        regular_open = calendar_row.get("regular_open_timestamp")
        if isinstance(calendar_date, str) and regular_open is not None:
            calendar_by_date[calendar_date] = _parse_aware(regular_open, "dividend calendar open").isoformat()
    for row in dividend_rows:
        duplicate_key = row.get("duplicate_event_key")
        effective_date = row.get("effective_date")
        source_hash = row.get("source_document_hash")
        if not isinstance(duplicate_key, str) or not isinstance(effective_date, str) or not isinstance(source_hash, str):
            raise DataPackageContractError("dividend raw hash row identity invalid")
        if row.get("source_row_sha256") not in (None, ""):
            raise DataPackageContractError("source_row_sha256 is verifier-owned")
        key_parts = duplicate_key.split(":", 1)
        if len(key_parts) != 2:
            raise DataPackageContractError("dividend duplicate key invalid")
        action_id = key_parts[0].strip()
        raw = by_key.get((action_id, effective_date.strip()))
        if raw is None or raw["hash"] != source_hash:
            raise DataPackageContractError("dividend raw row hash mismatch")
        try:
            row_amount = float(row.get("cash_or_ratio", "nan"))
        except (TypeError, ValueError) as exc:
            raise DataPackageContractError("dividend raw amount mismatch") from exc
        if not (row_amount == row_amount) or abs(row_amount - float(raw["amount"])) > 1e-12:
            raise DataPackageContractError("dividend raw amount mismatch")
        if row.get("currency") != raw["currency"] or row.get("action_type") != "cash_dividend":
            raise DataPackageContractError("dividend raw currency_or_type mismatch")
        if raw.get("permanent_security_identifier") is not None and row.get("permanent_security_identifier") != raw.get(
            "permanent_security_identifier"
        ):
            raise DataPackageContractError("dividend raw symbol identity mismatch")
        expected_open = calendar_by_date.get(effective_date.strip())
        if expected_open is not None:
            if _parse_aware(row.get("event_timestamp"), "dividend event timestamp").isoformat() != expected_open:
                raise DataPackageContractError("dividend raw event timestamp mismatch")
            if _parse_aware(row.get("available_at"), "dividend available timestamp").isoformat() != expected_open:
                raise DataPackageContractError("dividend raw available timestamp mismatch")
    return {
        "path": _relative(root, source_path),
        "sha256": expected_hash,
        "actions": len(actions),
        "rows_checked": len(dividend_rows),
        "row_hashes": [
            {"duplicate_event_key": f"{action_id}:{ex_date}", "source_document_hash": raw["hash"]}
            for (action_id, ex_date), raw in sorted(by_key.items())
        ],
    }


def _validate_raw_split_row_key_parity(raw_path: Path, split_rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Recompute raw/split session keys instead of trusting the receipt flag."""

    raw_rows = _load_csv(raw_path, "raw_bars")
    if not raw_rows or not split_rows:
        raise DataPackageContractError("raw/split bars empty")
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
    def keys(rows: Sequence[Mapping[str, Any]], adjustment: str) -> set[tuple[str, str]]:
        output: set[tuple[str, str]] = set()
        for row in rows:
            if set(row) != required or row.get("adjustment") != adjustment:
                raise DataPackageContractError(f"{adjustment} bars schema invalid")
            session_date = row.get("session_date")
            regular_open = row.get("regular_open_timestamp")
            if not isinstance(session_date, str) or not session_date.strip() or not isinstance(regular_open, str) or not regular_open.strip():
                raise DataPackageContractError(f"{adjustment} bars row key invalid")
            key = (session_date, regular_open)
            if key in output:
                raise DataPackageContractError(f"duplicate {adjustment} bar row key")
            output.add(key)
        return output
    raw_keys = keys(raw_rows, "raw")
    split_keys = keys(split_rows, "split")
    if raw_keys != split_keys:
        raise DataPackageContractError("raw_split_key_parity recomputation failed")
    return {"raw_rows": len(raw_rows), "split_rows": len(split_rows), "row_key_count": len(raw_keys)}


def _documents_for_validator(rows: Sequence[Mapping[str, Any]], *, documents_path: Path, root: Path) -> list[dict[str, Any]]:
    """Adapt Treasury output-relative raw paths to the package-root validator."""

    normalized: list[dict[str, Any]] = []
    for row in rows:
        copied = dict(row)
        raw_path = _resolve_document_raw_path(documents_path, copied.get("raw_path"), root=root, field="treasury document raw")
        copied["raw_path"] = _relative(root, raw_path)
        normalized.append(copied)
    return normalized


def _future_open_coverage(auction: Mapping[str, Any], bars: Mapping[str, Any], calendar: Mapping[str, Any]) -> None:
    bar_dates = {
        str(row.get("session_date"))
        for row in bars.get("records", [])
        if isinstance(row, Mapping) and row.get("permanent_security_identifier")
    }
    regular_dates = [
        str(row.get("regular_session_date"))
        for row in calendar.get("records", [])
        if isinstance(row, Mapping) and row.get("holiday_or_closure_reason") in (None, "", "NONE")
    ]
    for event in auction.get("events", []):
        if not isinstance(event, Mapping):
            raise DataPackageContractError("auction event schema invalid")
        event_date = str(event.get("auction_session_date", ""))
        future = [value for value in regular_dates if value > event_date][:6]
        if len(future) < 6 or not set(future).issubset(bar_dates):
            raise DataPackageContractError(f"tlt_fifth_session_exit_missing:{event.get('canonical_event_id', '')}")


def _schedule_digest(events: Sequence[Mapping[str, Any]]) -> str:
    encoded = json.dumps(list(events), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _strict_schedule_linkage(auction: Mapping[str, Any], calendar: Mapping[str, Any]) -> dict[str, Any]:
    """Resolve the first regular open strictly after every event availability."""

    raw_records = calendar.get("records")
    events = auction.get("events")
    if not isinstance(raw_records, list) or not isinstance(events, list) or not events:
        raise DataPackageContractError("schedule linkage inputs missing")
    opens: list[tuple[datetime, str, str]] = []
    seen_sessions: set[str] = set()
    for row in raw_records:
        if not isinstance(row, Mapping) or row.get("holiday_or_closure_reason") not in (None, "", "NONE"):
            continue
        session = row.get("regular_session_date")
        if not isinstance(session, str) or not session.strip() or session in seen_sessions:
            raise DataPackageContractError("schedule calendar session invalid")
        seen_sessions.add(session)
        opening = _parse_aware(row.get("regular_open_timestamp"), "calendar regular_open_timestamp")
        opens.append((opening, session, opening.isoformat().replace("+00:00", "Z")))
    opens.sort(key=lambda item: item[0])
    if len({item[0] for item in opens}) != len(opens):
        raise DataPackageContractError("schedule calendar duplicate open")
    schedule_rows: list[dict[str, Any]] = []
    event_ids: set[str] = set()
    for event in events:
        if not isinstance(event, Mapping):
            raise DataPackageContractError("schedule event invalid")
        event_id = event.get("canonical_event_id")
        if not isinstance(event_id, str) or not event_id.strip() or event_id in event_ids:
            raise DataPackageContractError("schedule event ID invalid")
        event_ids.add(event_id)
        available = _parse_aware(event.get("entry_available_at", event.get("available_at")), "event available_at")
        future = [item for item in opens if item[0] > available]
        if len(future) < 6:
            raise DataPackageContractError(f"schedule future regular opens missing:{event_id}")
        entry = future[0]
        future_six = future[:6]
        row = {
            "canonical_event_id": event_id,
            "available_at": available.isoformat().replace("+00:00", "Z"),
            "entry_session_date": entry[1],
            "entry_open_timestamp": entry[2],
            "future_open_timestamps": [item[2] for item in future_six],
            "holding_intervals": 5,
        }
        # If a producer supplied schedule fields, they are claims to verify,
        # not an alternative source of truth.
        for field in ("entry_session_date", "entry_open_timestamp"):
            if field in event and event[field] != row[field]:
                raise DataPackageContractError(f"schedule forged {field}:{event_id}")
        schedule_rows.append(row)
    return {
        "policy": RESULT_SIGNAL_ELIGIBILITY,
        "strict_comparator": ">",
        "event_count": len(schedule_rows),
        "events": schedule_rows,
        "canonical_digest_sha256": _schedule_digest(schedule_rows),
    }


def _validate_declared_schedule_linkage(coverage: Mapping[str, Any], computed: Mapping[str, Any]) -> None:
    declared = coverage.get("schedule_linkage")
    if declared is None:
        return
    if not isinstance(declared, Mapping) or dict(declared) != dict(computed):
        raise DataPackageContractError("schedule linkage declaration mismatch")


def _eligibility_rows_for_validator(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Use the frozen validator while preserving the v2 historical source URL.

    The v1 validator predates paper-api and the official SEC/Nasdaq evidence
    hosts.  Only those explicitly allowlisted hosts receive a compatibility
    URL; unknown or malformed sources remain unchanged and fail closed in the
    mature validator.
    """

    compatibility_url = "https://data.alpaca.markets/v2/assets/TLT"
    result: list[dict[str, Any]] = []
    for row in rows:
        source_url = row.get("source_url")
        parsed = urlparse(source_url) if isinstance(source_url, str) else None
        if (
            parsed is not None
            and parsed.scheme == "https"
            and parsed.username is None
            and parsed.password is None
            and parsed.hostname in HISTORICAL_IDENTITY_COMPATIBILITY_HOSTS
        ):
            result.append({**row, "source_url": compatibility_url})
        else:
            result.append(dict(row))
    return result


def _artifact_entry(root: Path, path: Path, metadata: Mapping[str, str]) -> dict[str, str]:
    return {
        "path": _relative(root, path),
        "sha256": sha256(path),
        "source_vintage": metadata["source_vintage"],
        "available_at": metadata["available_at"],
    }


def canonical_event_ids(values: Sequence[object] | None) -> list[str]:
    """Return the canonical sorted, unique event-ID representation."""

    if values is None or isinstance(values, (str, bytes, bytearray)):
        raise DataPackageContractError("expected_event_ids required")
    normalized: list[str] = []
    for value in values:
        if not isinstance(value, str) or not value.strip():
            raise DataPackageContractError("expected_event_id invalid")
        normalized.append(value.strip())
    if len(set(normalized)) != len(normalized):
        raise DataPackageContractError("expected_event_ids duplicate")
    return sorted(normalized)


def canonical_event_id_digest(values: Sequence[object] | None) -> str:
    """Hash a sorted, unique event-ID list used by Treasury audit blocks."""

    ids = canonical_event_ids(values)
    encoded = json.dumps(ids, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _id_audit(values: Sequence[object] | None, field: str) -> dict[str, Any]:
    try:
        ids = canonical_event_ids(values)
    except DataPackageContractError as exc:
        raise DataPackageContractError(f"{field} invalid") from exc
    return {
        "ids": ids,
        "count": len(ids),
        "canonical_digest_sha256": canonical_event_id_digest(ids),
    }


def _validate_expected_events(
    auction: Mapping[str, Any],
    *,
    expected_event_count: int | None,
    expected_event_ids: Sequence[str] | None,
) -> list[str]:
    if not isinstance(expected_event_count, int) or isinstance(expected_event_count, bool) or expected_event_count < 0:
        raise DataPackageContractError("expected_event_count required")
    ids = canonical_event_ids(expected_event_ids)
    if len(ids) != expected_event_count:
        raise DataPackageContractError("expected_event_ids/count mismatch")
    observed = canonical_event_ids(
        [event.get("canonical_event_id") for event in auction.get("events", []) if isinstance(event, Mapping)]
    )
    if ids != observed:
        raise DataPackageContractError("expected_event_ids disagree with documents")
    return ids


def _validate_coverage(
    coverage: Mapping[str, Any] | None,
    *,
    expected_event_count: int,
) -> dict[str, Any]:
    if not isinstance(coverage, Mapping):
        raise DataPackageContractError("coverage declaration required")
    required = ("event_episodes", "holding_intervals", "missing_rate", "fifth_open_exit")
    if any(field not in coverage for field in required):
        raise DataPackageContractError("coverage fields missing")
    event_episodes = coverage.get("event_episodes")
    holding_intervals = coverage.get("holding_intervals")
    fifth_open_exit = coverage.get("fifth_open_exit")
    if any(not isinstance(value, int) or isinstance(value, bool) or value < 0 for value in (event_episodes, holding_intervals, fifth_open_exit)):
        raise DataPackageContractError("coverage counts invalid")
    if event_episodes != expected_event_count or holding_intervals != expected_event_count * 5 or fifth_open_exit != expected_event_count:
        raise DataPackageContractError("coverage counts disagree with expected events")
    missing_rate = coverage.get("missing_rate")
    if not isinstance(missing_rate, (int, float)) or isinstance(missing_rate, bool) or not 0 <= float(missing_rate) <= 1:
        raise DataPackageContractError("coverage missing_rate invalid")
    capacity = coverage.get("capacity")
    capacity_status = coverage.get("capacity_status", CAPACITY_PENDING if capacity is None else None)
    if capacity is None:
        if capacity_status != CAPACITY_PENDING:
            raise DataPackageContractError("capacity status required when capacity is null")
    elif not isinstance(capacity, (int, float)) or isinstance(capacity, bool) or float(capacity) < 0:
        raise DataPackageContractError("coverage capacity invalid")
    result = {
        "event_episodes": event_episodes,
        "holding_intervals": holding_intervals,
        "missing_rate": float(missing_rate),
        "fifth_open_exit": fifth_open_exit,
        "capacity": capacity,
        "capacity_status": capacity_status,
    }
    return result


def build_r46_v2_data_package(
    governance_manifest: Path,
    *,
    documents: Path,
    bars: Path,
    dividends: Path,
    eligibility: Path,
    calendar: Path,
    acquisition_receipt: Path,
    treasury_receipt: Path,
    calendar_receipt: Path,
    artifact_metadata: Mapping[str, Mapping[str, Any]] | None = None,
    expected_event_count: int | None = None,
    expected_event_ids: Sequence[str] | None = None,
    coverage: Mapping[str, Any] | None = None,
    output: Path,
    repo_root: Path | None = None,
) -> dict[str, Any]:
    """Validate and write one retrospective R46 v2 data-package manifest."""

    governance_candidate = Path(governance_manifest).resolve()
    default_root = REPO if REPO == governance_candidate or REPO in governance_candidate.parents else governance_candidate.parent
    root = Path(repo_root or default_root).resolve()
    governance_path = _safe_path(root, governance_manifest, "governance_manifest")
    governance = _validate_governance(governance_path, root=root)
    governance_event_ids = _governance_event_universe_ids(governance, root=root)
    if governance_event_ids is not None:
        if expected_event_count != len(governance_event_ids) or expected_event_ids is None or canonical_event_ids(expected_event_ids) != governance_event_ids:
            raise DataPackageContractError("governance event universe disagrees with caller event universe")
    receipt_path = _safe_path(root, acquisition_receipt, "acquisition_receipt")
    receipt, receipt_bars = _validate_acquisition_receipt(receipt_path, root=root)
    treasury_receipt_path = _safe_path(root, treasury_receipt, "treasury_xml_receipt")
    calendar_receipt_path = _safe_path(root, calendar_receipt, "calendar_receipt")
    artifact_paths = {
        "documents": _safe_path(root, documents, "documents"),
        "bars": _safe_path(root, bars, "bars"),
        "dividends": _safe_path(root, dividends, "dividends"),
        "eligibility": _safe_path(root, eligibility, "eligibility"),
        "calendar": _safe_path(root, calendar, "calendar"),
    }
    if artifact_metadata is None:
        receipt_artifacts = receipt.get("artifacts")
        artifact_metadata = receipt_artifacts if isinstance(receipt_artifacts, Mapping) else None
    if not isinstance(artifact_metadata, Mapping):
        raise DataPackageContractError("artifact_metadata missing")
    for name in ARTIFACT_NAMES:
        if name not in artifact_metadata:
            raise DataPackageContractError(f"{name} metadata missing")
    if set(artifact_metadata) != set(ARTIFACT_NAMES):
        raise DataPackageContractError("artifact_metadata must declare exactly five artifacts")
    metadata: dict[str, dict[str, str]] = {}
    for name in ARTIFACT_NAMES:
        metadata[name] = _validate_metadata(name, artifact_metadata.get(name))

    treasury_receipt_summary = _validate_treasury_xml_receipt(
        treasury_receipt_path,
        documents_path=artifact_paths["documents"],
        root=root,
        expected_event_count=expected_event_count,
        expected_event_ids=expected_event_ids,
    )
    publication_availability_policy = _validate_publication_availability_policy(
        governance,
        root=root,
        treasury_receipt_path=treasury_receipt_path,
        documents_path=artifact_paths["documents"],
        treasury_summary=treasury_receipt_summary,
    )
    calendar_receipt_summary = _validate_calendar_receipt(
        calendar_receipt_path,
        calendar_path=artifact_paths["calendar"],
        root=root,
    )
    if treasury_receipt_summary["documents_sha256"] != sha256(artifact_paths["documents"]):
        raise DataPackageContractError("treasury documents package hash mismatch")
    if calendar_receipt_summary["records_sha256"] != sha256(artifact_paths["calendar"]):
        raise DataPackageContractError("calendar package hash mismatch")
    if calendar_receipt_summary["available_at"] != metadata["calendar"]["available_at"]:
        raise DataPackageContractError("calendar receipt available_at disagrees with package metadata")

    receipt_artifacts = receipt.get("artifacts")
    if isinstance(receipt_artifacts, Mapping):
        receipt_to_package = {"split_bars": "bars", "dividends": "dividends", "eligibility": "eligibility"}
        for receipt_name, package_name in receipt_to_package.items():
            spec = receipt_artifacts.get(receipt_name)
            if not isinstance(spec, Mapping):
                continue
            if spec.get("source_vintage") is not None and metadata[package_name]["source_vintage"] != spec.get("source_vintage"):
                raise DataPackageContractError(f"{package_name} source_vintage disagrees with acquisition receipt")
            if spec.get("available_at") is not None:
                receipt_available = _parse_aware(spec.get("available_at"), f"acquisition {receipt_name} available_at")
                if metadata[package_name]["available_at"] != receipt_available.isoformat().replace("+00:00", "Z"):
                    raise DataPackageContractError(f"{package_name} available_at disagrees with acquisition receipt")
            checked = receipt_bars.get(receipt_name)
            if isinstance(checked, Mapping):
                if checked.get("path") != _relative(root, artifact_paths[package_name]) or checked.get("sha256") != sha256(artifact_paths[package_name]):
                    raise DataPackageContractError(f"{package_name} does not match acquisition receipt")

    try:
        document_rows = _load_jsonl(artifact_paths["documents"], "documents")
        auction = normalize_auction_documents(
            _documents_for_validator(document_rows, documents_path=artifact_paths["documents"], root=root),
            repo_root=root,
        )
        bars_result = validate_tlt_bars(_load_csv(artifact_paths["bars"], "bars"), available_at=metadata["bars"]["available_at"])
        dividend_rows = _load_csv(artifact_paths["dividends"], "dividends")
        dividends_result = validate_tlt_dividends(dividend_rows, available_at=metadata["dividends"]["available_at"])
        eligibility_rows = _load_jsonl(artifact_paths["eligibility"], "eligibility")
        # The frozen v1 validator predates the v2 paper-api and official
        # historical identity hosts.  Validate via a host-only compatibility
        # copy, then retain the original evidence in the package.
        eligibility_for_validator = _eligibility_rows_for_validator(eligibility_rows)
        eligibility_result = validate_tlt_eligibility(eligibility_for_validator)
        eligibility_result["records"] = eligibility_rows
        calendar_rows = _load_jsonl(artifact_paths["calendar"], "calendar")
        calendar_result = validate_nyse_calendar(calendar_rows, available_at=metadata["calendar"]["available_at"])
        parity = _validate_raw_split_row_key_parity(
            _safe_path(root, receipt_bars["raw_bars"]["path"], "raw_bars"),
            bars_result["records"],
        )
        dividend_source = _validate_dividend_raw_hashes(
            receipt,
            receipt_path=receipt_path,
            root=root,
            dividend_rows=dividend_rows,
            calendar_rows=calendar_rows,
        )
        identity_source = _validate_identity_source(
            receipt,
            receipt_path=receipt_path,
            root=root,
            eligibility_rows=eligibility_rows,
        )
        if metadata["eligibility"]["source_vintage"] != identity_source["source_vintage"]:
            raise DataPackageContractError("eligibility source_vintage disagrees with identity evidence")
    except (OSError, TypeError, ValueError, csv.Error) as exc:
        raise DataPackageContractError(f"artifact validation failed: {exc}") from exc

    cross_blockers = _cross_validate_tlt_data(
        bars=bars_result,
        dividends=dividends_result,
        eligibility=eligibility_result,
        calendar=calendar_result,
    )
    if cross_blockers:
        raise DataPackageContractError("cross_artifact_validation:" + ",".join(sorted(set(cross_blockers))))
    _future_open_coverage(auction, bars_result, calendar_result)
    schedule_linkage = _strict_schedule_linkage(auction, calendar_result)
    _validate_declared_schedule_linkage(coverage if isinstance(coverage, Mapping) else {}, schedule_linkage)
    frozen_event_ids = _validate_expected_events(
        auction,
        expected_event_count=expected_event_count,
        expected_event_ids=expected_event_ids,
    )
    declared_coverage = _validate_coverage(coverage, expected_event_count=expected_event_count or 0)
    declared_coverage["schedule_linkage"] = schedule_linkage

    output_path = _safe_destination(root, output, "package output")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    package: dict[str, Any] = {
        "schema": V2_DATA_PACKAGE_SCHEMA,
        "status": "DATA_PACKAGE_READY",
        "research_id": governance["research_id"],
        "candidate_id": governance["candidate_id"],
        "economic_execution": False,
        "economic_run": False,
        "no_auto_promotion": True,
        "governance_manifest_path": _relative(root, governance_path),
        "governance_manifest_sha256": governance["integrity"]["manifest_sha256"],
        "publication_availability_policy": publication_availability_policy,
        "historical_cutoff": CUTOFF_UTC,
        "expected_event_count": expected_event_count,
        "expected_event_ids": frozen_event_ids,
        "acquisition": {
            "mode": "retrospective",
            "retrospective_acquisition": True,
            "receipt_path": _relative(root, receipt_path),
            "receipt_sha256": sha256(receipt_path),
            "network_used": bool(receipt.get("network_used", False)),
            "credentials_used": bool(receipt.get("credentials_used", False)),
            "economic_execution": False,
            "raw_split_key_parity": receipt_bars["raw_split_key_parity"],
            "raw_bars": receipt_bars["raw_bars"],
            "split_bars": receipt_bars["split_bars"],
        },
        "receipts": {
            "alpaca_market_data": {
                "path": _relative(root, receipt_path),
                "sha256": sha256(receipt_path),
                "raw_split_row_key_parity": parity,
                "dividends_source": dividend_source,
                "identity_source": identity_source,
            },
            "treasury_xml": treasury_receipt_summary,
            "calendar": calendar_receipt_summary,
        },
        "artifacts": {name: _artifact_entry(root, artifact_paths[name], metadata[name]) for name in ARTIFACT_NAMES},
        "market_data": {
            name: {
                "source_vintage": metadata[name]["source_vintage"],
                "available_at": metadata[name]["available_at"],
                "retrospective_acquisition": True,
            }
            for name in ("bars", "dividends", "eligibility", "calendar")
        },
        "coverage": {
            **declared_coverage,
            "auction_event_count": auction["admitted_event_count"],
            "tlt_bar_rows": bars_result["rows"],
            "tlt_dividend_rows": dividends_result["rows"],
            "tlt_identity_rows": eligibility_result["rows"],
            "nyse_calendar_rows": calendar_result["rows"],
            "economic_execution": False,
        },
        "integrity": {
            "canonicalization": "UTF-8 JSON, sorted object keys, compact separators, ensure_ascii=false; omit integrity.manifest_sha256 before hashing",
            "manifest_sha256": "",
        },
    }
    incumbent_contract = (
        governance.get("data_contract", {}).get("incumbent_data_contract")
        if isinstance(governance.get("data_contract"), Mapping)
        else None
    )
    if isinstance(incumbent_contract, Mapping):
        # This is a provenance-only side block; ``artifacts`` remains exactly
        # the five economic input artifacts and no post-seal hash is added.
        package["incumbent_dependencies"] = copy.deepcopy(dict(incumbent_contract))
    package["integrity"]["manifest_sha256"] = _canonical_hash(package, "manifest_sha256")
    _atomic_publish(output_path, (json.dumps(package, indent=2, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8"))
    package["package_path"] = _relative(root, output_path)
    return package


def consume_r46_v2_data_package(package_path: Path, *, repo_root: Path) -> dict[str, Any]:
    """Load the exact five-artifact shape a downstream execution consumer sees."""

    root = Path(repo_root).resolve()
    package_file = _safe_path(root, package_path, "data package")
    package = _load_json(package_file, "data package")
    if package.get("schema") != V2_DATA_PACKAGE_SCHEMA or package.get("status") != "DATA_PACKAGE_READY":
        raise DataPackageContractError("data package schema/status invalid")
    if package.get("research_id") != RESEARCH_ID or package.get("candidate_id") != "R46":
        raise DataPackageContractError("data package identity invalid")
    integrity = package.get("integrity")
    declared = integrity.get("manifest_sha256") if isinstance(integrity, Mapping) else None
    if not isinstance(declared, str) or declared != _canonical_hash(package, "manifest_sha256"):
        raise DataPackageContractError("data package manifest hash invalid")
    if not isinstance(package.get("expected_event_count"), int) or isinstance(package.get("expected_event_count"), bool):
        raise DataPackageContractError("data package expected_event_count invalid")
    if package["expected_event_count"] < 0:
        raise DataPackageContractError("data package expected_event_count invalid")
    governance_path_value = package.get("governance_manifest_path")
    if not isinstance(governance_path_value, str):
        raise DataPackageContractError("data package governance manifest path missing")
    governance = _validate_governance(_safe_path(root, governance_path_value, "data package governance_manifest"), root=root)
    if package.get("governance_manifest_sha256") != governance["integrity"]["manifest_sha256"]:
        raise DataPackageContractError("data package governance manifest hash mismatch")
    governance_policy = _validate_publication_availability_policy(governance, root=root)
    package_policy = package.get("publication_availability_policy")
    if not isinstance(package_policy, Mapping) or dict(package_policy) != governance_policy:
        raise DataPackageContractError("publication policy governance crosslink invalid")
    governance_event_ids = _governance_event_universe_ids(governance, root=root)
    governance_contract = (
        governance.get("data_contract", {}).get("incumbent_data_contract")
        if isinstance(governance.get("data_contract"), Mapping)
        else None
    )
    if isinstance(governance_contract, Mapping):
        if package.get("incumbent_dependencies") != governance_contract:
            raise DataPackageContractError("data package incumbent dependency crosslink invalid")
    elif "incumbent_dependencies" in package:
        raise DataPackageContractError("data package incumbent dependency unexpected")
    expected_ids_value = package.get("expected_event_ids")
    try:
        expected_ids = canonical_event_ids(expected_ids_value)
    except DataPackageContractError as exc:
        raise DataPackageContractError("data package expected_event_ids invalid") from exc
    if expected_ids != expected_ids_value or len(expected_ids) != package["expected_event_count"]:
        raise DataPackageContractError("data package expected_event_ids invalid")
    if governance_event_ids is not None and expected_ids != governance_event_ids:
        raise DataPackageContractError("data package governance event universe mismatch")
    package_coverage = package.get("coverage")
    if not isinstance(package_coverage, Mapping):
        raise DataPackageContractError("data package coverage missing")
    _validate_coverage(package_coverage, expected_event_count=package["expected_event_count"])
    artifacts = package.get("artifacts")
    if not isinstance(artifacts, Mapping) or set(artifacts) != set(ARTIFACT_NAMES):
        raise DataPackageContractError("data package must contain exactly five artifacts")
    artifact_paths: dict[str, Path] = {}
    for name in ARTIFACT_NAMES:
        spec = artifacts.get(name)
        if not isinstance(spec, Mapping):
            raise DataPackageContractError(f"data package {name} invalid")
        path = _safe_path(root, str(spec.get("path", "")), f"data package {name}")
        if spec.get("sha256") != sha256(path):
            raise DataPackageContractError(f"data package {name} hash mismatch")
        _validate_metadata(name, spec)
        artifact_paths[name] = path
    receipts = package.get("receipts")
    if not isinstance(receipts, Mapping) or set(receipts) != {"alpaca_market_data", "treasury_xml", "calendar"}:
        raise DataPackageContractError("data package receipt declarations invalid")

    market_spec = receipts.get("alpaca_market_data")
    if not isinstance(market_spec, Mapping):
        raise DataPackageContractError("data package Alpaca receipt missing")
    market_receipt_path = _safe_path(root, str(market_spec.get("path", "")), "data package Alpaca receipt")
    if market_spec.get("sha256") != sha256(market_receipt_path):
        raise DataPackageContractError("data package Alpaca receipt hash mismatch")
    market_receipt, market_checked = _validate_acquisition_receipt(market_receipt_path, root=root)
    parity = _validate_raw_split_row_key_parity(
        _safe_path(root, market_checked["raw_bars"]["path"], "raw_bars"),
        _load_csv(artifact_paths["bars"], "bars"),
    )
    if market_spec.get("raw_split_row_key_parity") != parity:
        raise DataPackageContractError("data package raw/split parity crosslink invalid")
    calendar_rows = _load_jsonl(artifact_paths["calendar"], "calendar")
    dividend_source = _validate_dividend_raw_hashes(
        market_receipt,
        receipt_path=market_receipt_path,
        root=root,
        dividend_rows=_load_csv(artifact_paths["dividends"], "dividends"),
        calendar_rows=calendar_rows,
    )
    identity_source = _validate_identity_source(
        market_receipt,
        receipt_path=market_receipt_path,
        root=root,
        eligibility_rows=_load_jsonl(artifact_paths["eligibility"], "eligibility"),
    )
    if market_spec.get("dividends_source") != dividend_source or market_spec.get("identity_source") != identity_source:
        raise DataPackageContractError("data package market source crosslink invalid")

    treasury_spec = receipts.get("treasury_xml")
    if not isinstance(treasury_spec, Mapping):
        raise DataPackageContractError("data package Treasury receipt missing")
    treasury_receipt_path = _safe_path(root, str(treasury_spec.get("path", "")), "data package Treasury receipt")
    if treasury_spec.get("sha256") != sha256(treasury_receipt_path):
        raise DataPackageContractError("data package Treasury receipt hash mismatch")
    treasury_checked = _validate_treasury_xml_receipt(
        treasury_receipt_path,
        documents_path=artifact_paths["documents"],
        root=root,
        expected_event_count=package["expected_event_count"],
        expected_event_ids=package["expected_event_ids"],
    )
    _validate_publication_availability_policy(
        governance,
        root=root,
        treasury_receipt_path=treasury_receipt_path,
        documents_path=artifact_paths["documents"],
        treasury_summary=treasury_checked,
    )
    if dict(treasury_spec) != treasury_checked:
        raise DataPackageContractError("data package Treasury receipt crosslink invalid")

    calendar_spec = receipts.get("calendar")
    if not isinstance(calendar_spec, Mapping):
        raise DataPackageContractError("data package calendar receipt missing")
    calendar_receipt_path = _safe_path(root, str(calendar_spec.get("path", "")), "data package calendar receipt")
    if calendar_spec.get("sha256") != sha256(calendar_receipt_path):
        raise DataPackageContractError("data package calendar receipt hash mismatch")
    calendar_checked = _validate_calendar_receipt(
        calendar_receipt_path,
        calendar_path=artifact_paths["calendar"],
        root=root,
    )
    if dict(calendar_spec) != calendar_checked:
        raise DataPackageContractError("data package calendar receipt crosslink invalid")
    try:
        package_documents = normalize_auction_documents(
            _documents_for_validator(
                _load_jsonl(artifact_paths["documents"], "documents"),
                documents_path=artifact_paths["documents"],
                root=root,
            ),
            repo_root=root,
        )
        package_calendar = validate_nyse_calendar(
            _load_jsonl(artifact_paths["calendar"], "calendar"),
            available_at=_validate_metadata("calendar", artifacts["calendar"])["available_at"],
        )
        computed_schedule = _strict_schedule_linkage(package_documents, package_calendar)
    except (OSError, TypeError, ValueError, csv.Error) as exc:
        raise DataPackageContractError(f"data package schedule linkage invalid: {exc}") from exc
    if package_coverage.get("schedule_linkage") != computed_schedule:
        raise DataPackageContractError("data package schedule linkage crosslink invalid")
    return {**package, "package_path": _relative(root, package_file)}


load_r46_v2_data_package = consume_r46_v2_data_package
read_r46_v2_data_package = consume_r46_v2_data_package


build_data_package = build_r46_v2_data_package


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("governance_manifest", type=Path)
    parser.add_argument("--documents", type=Path, required=True)
    parser.add_argument("--bars", type=Path, required=True)
    parser.add_argument("--dividends", type=Path, required=True)
    parser.add_argument("--eligibility", type=Path, required=True)
    parser.add_argument("--calendar", type=Path, required=True)
    parser.add_argument("--acquisition-receipt", type=Path, required=True)
    parser.add_argument("--treasury-receipt", type=Path, required=True)
    parser.add_argument("--calendar-receipt", type=Path, required=True)
    parser.add_argument("--expected-event-count", type=int, required=True)
    parser.add_argument("--expected-event-id", action="append", required=True)
    parser.add_argument("--coverage-json", type=Path, required=True)
    parser.add_argument("--artifact-metadata-json", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repo-root", type=Path)
    args = parser.parse_args(argv)
    coverage = json.loads(args.coverage_json.read_text(encoding="utf-8"))
    artifact_metadata = None
    if args.artifact_metadata_json is not None:
        artifact_metadata = json.loads(args.artifact_metadata_json.read_text(encoding="utf-8"))
    result = build_r46_v2_data_package(
        args.governance_manifest,
        documents=args.documents,
        bars=args.bars,
        dividends=args.dividends,
        eligibility=args.eligibility,
        calendar=args.calendar,
        acquisition_receipt=args.acquisition_receipt,
        treasury_receipt=args.treasury_receipt,
        calendar_receipt=args.calendar_receipt,
        artifact_metadata=artifact_metadata,
        expected_event_count=args.expected_event_count,
        expected_event_ids=args.expected_event_id,
        coverage=coverage,
        output=args.output,
        repo_root=args.repo_root,
    )
    print(json.dumps({"status": result["status"], "package_path": result["package_path"], "manifest_sha256": result["integrity"]["manifest_sha256"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
