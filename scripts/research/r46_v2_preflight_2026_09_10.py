"""Fail-closed, outcome-free preflight for the R46 v2 one-shot bundle.

This module validates governance and provenance only.  It never imports a
portfolio worker, reads an economic result, computes a return, or changes
PAPER/LIVE/registry state.  A zero process exit code only means that the
machine-readable result was emitted; callers must gate on ``status``.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import locale
import os
import platform
import re
import subprocess
import sys
import tempfile
from collections.abc import Mapping
from copy import deepcopy
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any, cast
from zoneinfo import ZoneInfo

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scripts.research.build_r46_v2_governance_2026_09_10 import (
    EXECUTION_COMMAND as GOVERNANCE_EXECUTION_COMMAND,
)
from scripts.research.build_r46_v2_governance_2026_09_10 import (
    FINAL_TREASURY_DOCUMENTS_BYTE_SIZE,
    FINAL_TREASURY_DOCUMENTS_RAW_SHA256,
    FINAL_TREASURY_DOCUMENTS_RELATIVE,
    FINAL_TREASURY_RECEIPT_BYTE_SIZE,
    FINAL_TREASURY_RECEIPT_RAW_SHA256,
    FINAL_TREASURY_RECEIPT_RELATIVE,
    PUBLICATION_AVAILABILITY_EVIDENCE_BYTE_SIZE,
    PUBLICATION_AVAILABILITY_EVIDENCE_RAW_SHA256,
    PUBLICATION_AVAILABILITY_EVIDENCE_RELATIVE,
    PUBLICATION_AVAILABILITY_EVIDENCE_SCHEMA,
)
from scripts.research.r46_treasury_auction_readiness_2026_09_10 import (
    _cross_validate_tlt_data,
    normalize_auction_documents,
    validate_nyse_calendar,
    validate_tlt_bars,
    validate_tlt_dividends,
    validate_tlt_eligibility,
)

V1_MANIFEST_RELATIVE = Path("data/research_manifests/r46_treasury_auction_unwind_tlt_v1_2026_09_10.json")
V1_SCHEMA_RELATIVE = Path("data/research_manifests/r46_treasury_auction_unwind_tlt_v1_2026_09_10.schema.json")
V1_SOURCE_MATRIX_RELATIVE = Path("data/research_manifests/r46_treasury_auction_unwind_tlt_v1_2026_09_10_source_matrix.json")
V1_HOLDOUT_CONTRACT_RELATIVE = Path(
    "data/research_manifests/r46_treasury_auction_unwind_tlt_v1_2026_09_10_holdout_custody_contract.json"
)
PUBLIC_EVIDENCE_RECEIPT_RELATIVE = Path(
    "data/research_receipts/r46_treasury_public_source_evidence_2026_09_10_v1.json"
)
V2_SCHEMA_VERSION = "r46-governance-manifest-v2"
V2_RESEARCH_ID = "r46_treasury_auction_unwind_tlt_v2_2026_09_10"
V2_STATISTICS_SCHEMA = "r46-statistics-plan-v2"
V2_FAMILY_SCHEMA = "r46-multiplicity-family-v2"
V2_ONE_SHOT_SCHEMA = "r46-one-shot-state-v2"
V2_DATA_PACKAGE_SCHEMA = "r46-treasury-auction-unwind-data-package-v2"
NATIVE_ATTESTATION_SCHEMA = "r46-native-ledger-attestation-v2"
PREFLIGHT_RECEIPT_SCHEMA = "r46-v2-preflight-receipt-v2"
PIT_SOURCE_SCHEMAS = {
    "r46-tlt-market-data-acquisition-v2",
    "r46-treasury-acquisition-receipt-v2",
    "r46-treasury-xml-acquisition-v2",
}
CUTOFF_UTC = "2026-07-31T23:59:59Z"
EXPECTED_V1_MANIFEST_SHA256 = "f0897f8aaeb56ca285b9ac626b95fb3131105b8653ea137b9d8ad621a44ec509"
RESULT_AVAILABILITY_POLICY = (
    "official_XML_AuctionDate_plus_one_civil_day_midnight_America_New_York_for_all_results"
)
RESULT_SIGNAL_ELIGIBILITY = "FIRST_REGULAR_OPEN_STRICTLY_AFTER_AVAILABLE_AT"
ACTUAL_RELEASE_TIME_ROLE = "evidence_only"
EXPECTED_EVENT_COUNT = 127
EXPECTED_MISSING_RELEASE_COUNT = 15
EVENT_IDS_CANONICALIZATION = "sorted unique event IDs encoded as compact UTF-8 JSON array"
EXPECTED_EVENT_IDS_SHA256 = "f726f27aaece6532de1581b5830e38cceba67c75ae02dfa79fedaffc0e9d4b0b"
PUBLIC_EVIDENCE_RECEIPT_RAW_SHA256 = "c24cffa145391d139c442e64237aae7225d14cf76ce7f0ce2ade095ba3eec960"
XML_V2_ACQUIRER_RELATIVE = "scripts/research/acquire_r46_treasury_xml_v2_2026_09_10.py"
NEW_YORK = ZoneInfo("America/New_York")
PLACEHOLDER = "UNSET_UNTIL_FINAL_CLEAN_COMMIT"
HEX64_RE = re.compile(r"^[0-9a-f]{64}$")
HEX40_RE = re.compile(r"^[0-9a-f]{40}$")
FORBIDDEN_OUTCOME_KEYS = {
    "returns",
    "return",
    "nav",
    "pnl",
    "p&l",
    "turnover",
    "drawdown",
    "cvar",
    "sharpe",
    "p_values",
    "p_value",
    "pvalue",
    "p_l",
    "sharpe_ratio",
    "sortino",
    "sortino_ratio",
    "profit_loss",
    "bid_to_cover",
    "yield",
    "tail",
    "fills",
}
REQUIRED_ARTIFACTS = {"documents", "bars", "dividends", "eligibility", "calendar"}
REQUIRED_SOURCE_ROLES = (
    "builder",
    "publication_evidence",
    "preflight",
    "runner",
    "verifier",
    "v3_oracle",
    "custody",
    "data_acquisition",
    "calendar",
    "package",
    "native_attestation",
    "readiness",
    "tests",
)
EXPECTED_FAMILY_IDS = {
    "R46_PRIMARY_EVENT_EPISODE_EXCESS_VS_EXPOSURE_MATCHED_NULL",
    "R46_INCUMBENT_COMPARISON",
    "R46_TIMING_PLACEBO",
    "R46_COST_2X",
    "R46_COST_3X",
    "R46_CAPACITY_SUPPRESSION",
    "R46_MISSINGNESS",
}
EXPECTED_FAMILY_METHOD = "holm_step_down_for_descriptive_adjusted_p_values"
EXPECTED_PRIMARY_ESTIMAND = "initial_equity_portfolio_contribution"
EXPECTED_PERMUTATION_METHOD = "paired_sign_flip_randomization_under_exchangeability"
EXPECTED_FAMILY_ORDERING = "lexicographic_hypothesis_id"
EXPECTED_REPORTED_OUTPUT_COUNT = 7
EXPECTED_INFERENTIAL_IDS = (
    "R46_PRIMARY_EVENT_EPISODE_EXCESS_VS_EXPOSURE_MATCHED_NULL",
    "R46_INCUMBENT_COMPARISON",
    "R46_COST_2X",
    "R46_COST_3X",
)
EXPECTED_FAMILY_ROLES = {
    "R46_PRIMARY_EVENT_EPISODE_EXCESS_VS_EXPOSURE_MATCHED_NULL": "primary",
    "R46_INCUMBENT_COMPARISON": "secondary",
    "R46_TIMING_PLACEBO": "secondary",
    "R46_COST_2X": "secondary",
    "R46_COST_3X": "secondary",
    "R46_CAPACITY_SUPPRESSION": "coverage",
    "R46_MISSINGNESS": "coverage",
}
V5_INCUMBENT_PATHS = {
    "bars": "data/historical/equity_pit_symmetric_2016_2026_v5.csv",
    "lifetimes": "data/historical/equity_entity_lifetimes_full_v5.csv",
    "corporate_actions": "data/historical/equity_survivorship_full_corporate_actions_v5.csv",
    "bars_manifest": "data/historical/equity_pit_symmetric_2016_2026_v5.manifest.json",
    "corporate_actions_manifest": "data/historical/equity_survivorship_full_corporate_actions_v5.manifest.json",
    "seam": "scripts/run_symmetric_v5_validation.py",
}
V5_EXTERNAL_CONTENT_KEYS = frozenset({"bars", "corporate_actions", "bars_manifest", "corporate_actions_manifest"})
V5_BASE_REQUIRED_CONTENT_KEYS = frozenset({"lifetimes"})
V5_SOURCE_REQUIRED_CONTENT_KEYS = frozenset({"lifetimes", "seam"})
V5_EXTERNAL_CONTENT_PATHS = frozenset(V5_INCUMBENT_PATHS[key] for key in V5_EXTERNAL_CONTENT_KEYS)
# C_src may be followed by a freeze-only commit containing immutable,
# outcome-free evidence and governance artifacts.  Runtime source/config/lock
# changes are never permitted in that interval; all outcome-relevant source is
# required to be present and blob-pinned in C_src itself.
ALLOWED_SOURCE_TO_FREEZE_PREFIXES = (
    "data/research_manifests/",
    "data/research_receipts/",
    "data/research/",
    "docs/plans/",
    "docs/research/",
)
EXECUTION_COMMAND = GOVERNANCE_EXECUTION_COMMAND
_PREFLIGHT_PROOF = object()


class VerifiedPreflightResult(dict[str, Any]):
    """JSON-serializable result carrying an in-process-only persistence proof."""

    __slots__ = ("proof", "payload_digest")

    def __init__(self, value: Mapping[str, Any]) -> None:
        super().__init__(value)
        self.proof: object | None = None
        self.payload_digest: str | None = None


def _canonical_bytes(payload: Mapping[str, Any]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def canonical_hash(payload: Mapping[str, Any], *, self_field: str) -> str:
    value = deepcopy(dict(payload))
    integrity = value.get("integrity")
    if not isinstance(integrity, dict):
        raise ValueError("integrity_object_required")
    integrity.pop(self_field, None)
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("json_object_required")
    return value


def _canonical_event_ids_digest(event_ids: list[str]) -> str:
    canonical = sorted(set(event_ids))
    encoded = json.dumps(canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _public_receipt_event_id(event: Mapping[str, Any]) -> str:
    explicit = event.get("canonical_event_id")
    if isinstance(explicit, str) and explicit.strip():
        return explicit.strip()
    auction_date = event.get("auction_date", event.get("auctionDate"))
    cusip = event.get("cusip", event.get("security_identifier"))
    if not isinstance(auction_date, str) or not auction_date.strip() or not isinstance(cusip, str) or not cusip.strip():
        raise ValueError("public_evidence_event_identity_missing")
    return f"{auction_date.strip()}:{cusip.strip()}"


def _trusted_public_evidence_policy(root: Path) -> tuple[dict[str, Any] | None, list[str]]:
    """Recompute the policy from the fixed public receipt and frozen evidence."""

    reasons: list[str] = []
    try:
        public_path = _safe_path(root, PUBLIC_EVIDENCE_RECEIPT_RELATIVE.as_posix())
        public_receipt = _load_json(public_path)
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
        return None, ["PUBLIC_EVIDENCE_RECEIPT_UNREADABLE"]
    if public_receipt.get("schema") != "r46-treasury-public-source-evidence-receipt-v1":
        reasons.append("PUBLIC_EVIDENCE_RECEIPT_SCHEMA")
    events = public_receipt.get("events")
    if not isinstance(events, list) or any(not isinstance(event, Mapping) for event in events):
        return None, sorted(set(reasons + ["PUBLIC_EVIDENCE_EVENTS_INVALID"]))
    try:
        event_ids = sorted({_public_receipt_event_id(cast(Mapping[str, Any], event)) for event in events})
    except ValueError:
        return None, sorted(set(reasons + ["PUBLIC_EVIDENCE_EVENT_ID_INVALID"]))
    digest = _canonical_event_ids_digest(event_ids)
    if digest != EXPECTED_EVENT_IDS_SHA256:
        reasons.append("PUBLIC_EVIDENCE_EVENT_IDS_DIGEST_PIN")
    if len(events) != EXPECTED_EVENT_COUNT or len(event_ids) != len(events):
        reasons.append("PUBLIC_EVIDENCE_EXPECTED_EVENT_COUNT_INVALID")
    try:
        public_receipt_raw_sha256 = sha256_file(public_path)
    except OSError:
        return None, sorted(set(reasons + ["PUBLIC_EVIDENCE_RECEIPT_UNREADABLE"]))
    if public_receipt_raw_sha256 != PUBLIC_EVIDENCE_RECEIPT_RAW_SHA256:
        reasons.append("PUBLIC_EVIDENCE_RECEIPT_HASH_PIN")

    try:
        evidence_path = _safe_path(root, PUBLICATION_AVAILABILITY_EVIDENCE_RELATIVE.as_posix())
        evidence = _load_json(evidence_path)
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
        return None, sorted(set(reasons + ["PUBLICATION_AVAILABILITY_EVIDENCE_UNREADABLE"]))
    evidence_integrity = evidence.get("integrity")
    if (
        evidence.get("schema") != PUBLICATION_AVAILABILITY_EVIDENCE_SCHEMA
        or evidence.get("status") != "COMPLETE"
        or evidence.get("research_id") != V2_RESEARCH_ID
        or evidence.get("candidate_id") != "R46"
    ):
        reasons.append("PUBLICATION_AVAILABILITY_EVIDENCE_SCHEMA")
    evidence_self_hash: str | None = None
    if isinstance(evidence_integrity, Mapping):
        try:
            evidence_self_hash = canonical_hash(evidence, self_field="evidence_sha256")
        except (TypeError, ValueError, KeyError):
            evidence_self_hash = None
    if not isinstance(evidence_integrity, Mapping) or evidence_integrity.get("evidence_sha256") != evidence_self_hash:
        reasons.append("PUBLICATION_AVAILABILITY_EVIDENCE_SELF_HASH")
    if sha256_file(evidence_path) != PUBLICATION_AVAILABILITY_EVIDENCE_RAW_SHA256:
        reasons.append("PUBLICATION_AVAILABILITY_EVIDENCE_RAW_HASH")
    if evidence.get("economic_execution") is not False or evidence.get("economic_outcomes_accessed") is not False:
        reasons.append("PUBLICATION_AVAILABILITY_EVIDENCE_ECONOMIC_BOUNDARY")
    if (
        evidence.get("result_availability_policy") != RESULT_AVAILABILITY_POLICY
        or evidence.get("result_signal_eligibility") != RESULT_SIGNAL_ELIGIBILITY
        or evidence.get("same_day_signal_allowed") is not False
        or evidence.get("actual_release_time_role") != ACTUAL_RELEASE_TIME_ROLE
        or evidence.get("server_last_modified_is_publication_proof") is not False
    ):
        reasons.append("PUBLICATION_AVAILABILITY_EVIDENCE_POLICY")

    evidence_ids = evidence.get("expected_event_ids")
    if (
        not isinstance(evidence_ids, list)
        or any(not isinstance(value, str) or not value.strip() for value in evidence_ids)
        or evidence_ids != sorted(set(evidence_ids))
        or len(evidence_ids) != EXPECTED_EVENT_COUNT
    ):
        reasons.append("PUBLICATION_AVAILABILITY_EVIDENCE_EVENT_IDS")
        evidence_ids = []
    evidence_digest = _canonical_event_ids_digest([str(value) for value in evidence_ids])
    if evidence_ids != event_ids or evidence_digest != EXPECTED_EVENT_IDS_SHA256:
        reasons.append("PUBLICATION_AVAILABILITY_EVIDENCE_EVENT_SET")
    if (
        evidence.get("expected_event_count") != EXPECTED_EVENT_COUNT
        or evidence.get("expected_event_ids_sha256") != evidence_digest
        or evidence.get("expected_event_ids_digest") != evidence_digest
    ):
        reasons.append("PUBLICATION_AVAILABILITY_EVIDENCE_EVENT_DIGEST")
    missing = evidence.get("missing_release")
    missing_ids = missing.get("expected_event_ids") if isinstance(missing, Mapping) else None
    if (
        not isinstance(missing_ids, list)
        or any(not isinstance(value, str) or not value.strip() for value in missing_ids)
        or missing_ids != sorted(set(missing_ids))
        or len(missing_ids) != EXPECTED_MISSING_RELEASE_COUNT
        or not set(missing_ids).issubset(set(event_ids))
    ):
        reasons.append("PUBLICATION_AVAILABILITY_EVIDENCE_MISSING_RELEASE")
        missing_ids = []
    missing_digest = _canonical_event_ids_digest([str(value) for value in missing_ids])
    if (
        not isinstance(missing, Mapping)
        or missing.get("expected_count") != EXPECTED_MISSING_RELEASE_COUNT
        or missing.get("expected_event_ids_sha256") != missing_digest
        or missing.get("expected_event_ids_digest") != missing_digest
        or missing.get("derivation") != "normalized_result_rows_published_at_null"
        or missing.get("source_field") != "result.published_at"
    ):
        reasons.append("PUBLICATION_AVAILABILITY_EVIDENCE_MISSING_RELEASE_DIGEST")

    sources = evidence.get("sources")
    expected_sources = {
        "receipt": (
            FINAL_TREASURY_RECEIPT_RELATIVE.as_posix(),
            "r46-treasury-xml-acquisition-v2",
            FINAL_TREASURY_RECEIPT_RAW_SHA256,
            FINAL_TREASURY_RECEIPT_BYTE_SIZE,
        ),
        "documents": (
            FINAL_TREASURY_DOCUMENTS_RELATIVE.as_posix(),
            "r46-treasury-auction-documents-v2",
            FINAL_TREASURY_DOCUMENTS_RAW_SHA256,
            FINAL_TREASURY_DOCUMENTS_BYTE_SIZE,
        ),
    }
    if not isinstance(sources, Mapping) or set(sources) != set(expected_sources):
        reasons.append("PUBLICATION_AVAILABILITY_EVIDENCE_SOURCES")
    else:
        for name, expected in expected_sources.items():
            source = sources.get(name)
            if (
                not isinstance(source, Mapping)
                or set(source) != {"path", "schema", "raw_sha256", "byte_size"}
                or tuple(source.get(field) for field in ("path", "schema", "raw_sha256", "byte_size")) != expected
            ):
                reasons.append(f"PUBLICATION_AVAILABILITY_EVIDENCE_SOURCE:{name}")

    return {
        "expected_event_count": EXPECTED_EVENT_COUNT,
        "expected_event_ids": event_ids,
        "expected_event_ids_sha256": digest,
        "expected_event_ids_digest": digest,
        "missing_release": {
            "expected_count": EXPECTED_MISSING_RELEASE_COUNT,
            "expected_event_ids": missing_ids,
            "expected_event_ids_sha256": missing_digest,
            "expected_event_ids_digest": missing_digest,
        },
        "source_receipt": {
            "path": PUBLIC_EVIDENCE_RECEIPT_RELATIVE.as_posix(),
            "schema": public_receipt.get("schema"),
            "raw_sha256": public_receipt_raw_sha256,
        },
        "source_evidence": {
            "path": PUBLICATION_AVAILABILITY_EVIDENCE_RELATIVE.as_posix(),
            "schema": PUBLICATION_AVAILABILITY_EVIDENCE_SCHEMA,
            "raw_sha256": PUBLICATION_AVAILABILITY_EVIDENCE_RAW_SHA256,
            "byte_size": PUBLICATION_AVAILABILITY_EVIDENCE_BYTE_SIZE,
        },
    }, sorted(set(reasons))


def _validate_publication_policy(
    policy: Any,
    root: Path,
    *,
    trusted: Mapping[str, Any] | None = None,
    label: str = "governance",
) -> list[str]:
    """Validate an exact policy block against independently read public evidence."""

    prefix = "PUBLICATION_POLICY" if label == "governance" else f"PUBLICATION_POLICY_{label.upper()}"
    if not isinstance(policy, Mapping):
        return [f"{prefix}_REQUIRED"]
    reasons: list[str] = []
    policy_fields = {
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
    extra_fields = set(policy) - policy_fields
    if extra_fields:
        reasons.append(f"{prefix}_EXTRA_FIELDS")
    for field, expected in (
        ("result_availability_policy", RESULT_AVAILABILITY_POLICY),
        ("result_signal_eligibility", RESULT_SIGNAL_ELIGIBILITY),
        ("same_day_signal_allowed", False),
        ("actual_release_time_role", ACTUAL_RELEASE_TIME_ROLE),
        ("server_last_modified_is_publication_proof", False),
        ("expected_event_count", EXPECTED_EVENT_COUNT),
        ("canonicalization", EVENT_IDS_CANONICALIZATION),
    ):
        if policy.get(field) != expected:
            reasons.append(f"{prefix}_MISMATCH:{field}")
    ids = policy.get("expected_event_ids")
    if (
        not isinstance(ids, list)
        or any(not isinstance(value, str) or not value.strip() for value in ids)
        or len(ids) != len(set(ids))
        or sorted(ids) != ids
        or len(ids) != EXPECTED_EVENT_COUNT
    ):
        reasons.append(f"{prefix}_EXPECTED_EVENT_IDS_INVALID")
    else:
        digest = _canonical_event_ids_digest([str(value) for value in ids])
        if policy.get("expected_event_ids_sha256") != digest or policy.get("expected_event_ids_digest") != digest:
            reasons.append(f"{prefix}_EXPECTED_EVENT_IDS_DIGEST_MISMATCH")
        if digest != EXPECTED_EVENT_IDS_SHA256:
            reasons.append(f"{prefix}_EXPECTED_EVENT_IDS_FREEZE_DIGEST_MISMATCH")
        if isinstance(trusted, Mapping) and ids != trusted.get("expected_event_ids"):
            reasons.append(f"{prefix}_EXPECTED_EVENT_IDS_MISMATCH")
    source_receipt = policy.get("source_receipt")
    if not isinstance(source_receipt, Mapping):
        reasons.append(f"{prefix}_SOURCE_RECEIPT_REQUIRED")
    else:
        if set(source_receipt) != {"path", "schema", "raw_sha256"}:
            reasons.append(f"{prefix}_SOURCE_RECEIPT_FIELDS_INVALID")
        if source_receipt.get("path") != PUBLIC_EVIDENCE_RECEIPT_RELATIVE.as_posix():
            reasons.append(f"{prefix}_SOURCE_RECEIPT_PATH_MISMATCH")
        if source_receipt.get("schema") != "r46-treasury-public-source-evidence-receipt-v1":
            reasons.append(f"{prefix}_SOURCE_RECEIPT_SCHEMA_MISMATCH")
        try:
            receipt_path = _safe_path(root, source_receipt.get("path"))
            receipt_hash = sha256_file(receipt_path)
            if receipt_hash != PUBLIC_EVIDENCE_RECEIPT_RAW_SHA256:
                reasons.append(f"{prefix}_SOURCE_RECEIPT_FREEZE_HASH_MISMATCH")
            if source_receipt.get("raw_sha256") != receipt_hash:
                reasons.append(f"{prefix}_SOURCE_RECEIPT_HASH_MISMATCH")
        except (OSError, ValueError):
            reasons.append(f"{prefix}_SOURCE_RECEIPT_UNREADABLE")
    source_evidence = policy.get("source_evidence")
    if not isinstance(source_evidence, Mapping):
        reasons.append(f"{prefix}_SOURCE_EVIDENCE_REQUIRED")
    else:
        if set(source_evidence) != {"path", "schema", "raw_sha256", "byte_size"}:
            reasons.append(f"{prefix}_SOURCE_EVIDENCE_FIELDS_INVALID")
        if source_evidence.get("path") != PUBLICATION_AVAILABILITY_EVIDENCE_RELATIVE.as_posix():
            reasons.append(f"{prefix}_SOURCE_EVIDENCE_PATH_MISMATCH")
        if source_evidence.get("schema") != PUBLICATION_AVAILABILITY_EVIDENCE_SCHEMA:
            reasons.append(f"{prefix}_SOURCE_EVIDENCE_SCHEMA_MISMATCH")
        if source_evidence.get("raw_sha256") != PUBLICATION_AVAILABILITY_EVIDENCE_RAW_SHA256:
            reasons.append(f"{prefix}_SOURCE_EVIDENCE_HASH_MISMATCH")
        if source_evidence.get("byte_size") != PUBLICATION_AVAILABILITY_EVIDENCE_BYTE_SIZE:
            reasons.append(f"{prefix}_SOURCE_EVIDENCE_SIZE_MISMATCH")
        try:
            evidence_path = _safe_path(root, source_evidence.get("path"))
            if sha256_file(evidence_path) != source_evidence.get("raw_sha256"):
                reasons.append(f"{prefix}_SOURCE_EVIDENCE_CURRENT_HASH_MISMATCH")
        except (OSError, ValueError):
            reasons.append(f"{prefix}_SOURCE_EVIDENCE_UNREADABLE")
        if isinstance(trusted, Mapping) and source_evidence != trusted.get("source_evidence"):
            reasons.append(f"{prefix}_SOURCE_EVIDENCE_CROSSLINK")
    missing = policy.get("missing_release")
    if not isinstance(missing, Mapping):
        reasons.append(f"{prefix}_MISSING_RELEASE_REQUIRED")
    else:
        if set(missing) != {
            "expected_count",
            "expected_event_ids",
            "expected_event_ids_sha256",
            "expected_event_ids_digest",
            "derivation",
            "source_field",
        }:
            reasons.append(f"{prefix}_MISSING_RELEASE_FIELDS_INVALID")
        if missing.get("expected_count") != EXPECTED_MISSING_RELEASE_COUNT:
            reasons.append(f"{prefix}_MISSING_RELEASE_COUNT_MISMATCH")
        missing_ids = missing.get("expected_event_ids")
        missing_sha = missing.get("expected_event_ids_sha256")
        missing_digest = missing.get("expected_event_ids_digest")
        if missing_ids is None:
            reasons.append(f"{prefix}_MISSING_RELEASE_IDS_REQUIRED")
        elif (
            not isinstance(missing_ids, list)
            or any(not isinstance(value, str) or not value.strip() for value in missing_ids)
            or len(missing_ids) != EXPECTED_MISSING_RELEASE_COUNT
            or len(missing_ids) != len(set(missing_ids))
            or sorted(missing_ids) != missing_ids
        ):
            reasons.append(f"{prefix}_MISSING_RELEASE_IDS_INVALID")
        else:
            digest = _canonical_event_ids_digest([str(value) for value in missing_ids])
            if missing_sha != digest or missing_digest != digest:
                reasons.append(f"{prefix}_MISSING_RELEASE_DIGEST_MISMATCH")
            if missing.get("derivation") != "normalized_result_rows_published_at_null":
                reasons.append(f"{prefix}_MISSING_RELEASE_DERIVATION_INVALID")
            if missing.get("source_field") != "result.published_at":
                reasons.append(f"{prefix}_MISSING_RELEASE_SOURCE_FIELD_INVALID")
            if isinstance(trusted, Mapping):
                trusted_missing = trusted.get("missing_release", {}).get("expected_event_ids")
                if not isinstance(trusted_missing, list) or missing_ids != trusted_missing:
                    reasons.append(f"{prefix}_MISSING_RELEASE_IDS_MISMATCH")
    return sorted(set(reasons))


def _validate_v1_contract_preservation(manifest: Mapping[str, Any], root: Path) -> list[str]:
    preservation = manifest.get("v1_contract_preservation")
    if not isinstance(preservation, Mapping):
        return ["V1_CONTRACT_PRESERVATION_REQUIRED"]
    reasons: list[str] = []
    preservation_fields = {
        "manifest_path",
        "manifest_raw_sha256",
        "manifest_byte_size",
        "schema_path",
        "schema_raw_sha256",
        "schema_byte_size",
        "source_matrix_path",
        "source_matrix_raw_sha256",
        "source_matrix_byte_size",
        "holdout_contract_path",
        "holdout_contract_raw_sha256",
        "holdout_contract_byte_size",
        "event_contract_canonical_sha256",
        "allowed_v2_overlay",
    }
    if set(preservation) != preservation_fields:
        reasons.append("V1_CONTRACT_FIELDS_INVALID")
    expected = {
        "manifest": V1_MANIFEST_RELATIVE.as_posix(),
        "schema": V1_SCHEMA_RELATIVE.as_posix(),
        "source_matrix": V1_SOURCE_MATRIX_RELATIVE.as_posix(),
        "holdout_contract": V1_HOLDOUT_CONTRACT_RELATIVE.as_posix(),
    }
    for key, relative in expected.items():
        path_field = f"{key}_path"
        hash_field = f"{key}_raw_sha256"
        size_field = f"{key}_byte_size"
        if preservation.get(path_field) != relative:
            reasons.append(f"V1_CONTRACT_PATH_MISMATCH:{key}")
            continue
        try:
            path = _safe_path(root, relative)
        except ValueError:
            reasons.append(f"V1_CONTRACT_ARTIFACT_MISSING:{key}")
            continue
        if preservation.get(hash_field) != sha256_file(path):
            reasons.append(f"V1_CONTRACT_RAW_HASH_MISMATCH:{key}")
        if preservation.get(size_field) != path.stat().st_size:
            reasons.append(f"V1_CONTRACT_RAW_SIZE_MISMATCH:{key}")
    if preservation.get("allowed_v2_overlay") != ["publication_availability_policy"]:
        reasons.append("V1_CONTRACT_OVERLAY_INVALID")
    try:
        parent_path = _safe_path(root, V1_MANIFEST_RELATIVE.as_posix())
        parent = _load_json(parent_path)
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
        return sorted(set(reasons + ["V1_CONTRACT_PARENT_UNREADABLE"]))
    event_contract = parent.get("event_contract")
    if not isinstance(event_contract, Mapping):
        reasons.append("V1_CONTRACT_EVENT_CONTRACT_MISSING")
    else:
        encoded = json.dumps(event_contract, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        if preservation.get("event_contract_canonical_sha256") != hashlib.sha256(encoded).hexdigest():
            reasons.append("V1_CONTRACT_EVENT_CONTRACT_HASH_MISMATCH")
        if manifest.get("event_contract") != event_contract:
            reasons.append("V1_CONTRACT_EVENT_CONTRACT_DRIFT")
    data_contract = manifest.get("data_contract")
    if isinstance(data_contract, Mapping):
        if data_contract.get("source_matrix_path") != preservation.get("source_matrix_path"):
            reasons.append("V1_CONTRACT_SOURCE_MATRIX_PATH_CROSSLINK")
        if data_contract.get("source_matrix_sha256") != preservation.get("source_matrix_raw_sha256"):
            reasons.append("V1_CONTRACT_SOURCE_MATRIX_HASH_CROSSLINK")
    else:
        reasons.append("V1_CONTRACT_DATA_CONTRACT_MISSING")
    return sorted(set(reasons))


def _parse_aware_timestamp(value: Any, field: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field}_missing")
    parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field}_timezone_required")
    return parsed


def _validate_normalized_policy_and_first_open(
    documents: Mapping[str, Any],
    calendar: Mapping[str, Any],
    *,
    trusted: Mapping[str, Any] | None,
) -> list[str]:
    """Recompute result availability and the strict first regular open."""

    reasons: list[str] = []
    events = documents.get("events")
    calendar_rows = calendar.get("records")
    if not isinstance(events, list) or not isinstance(calendar_rows, list):
        return ["PUBLICATION_POLICY_NORMALIZED_INPUT_INVALID"]
    open_timestamps: list[datetime] = []
    for row in calendar_rows:
        if not isinstance(row, Mapping):
            continue
        reason = row.get("holiday_or_closure_reason")
        if reason not in (None, "", "NONE"):
            continue
        try:
            open_timestamps.append(_parse_aware_timestamp(row.get("regular_open_timestamp"), "calendar_open"))
        except (TypeError, ValueError):
            reasons.append("PUBLICATION_POLICY_CALENDAR_OPEN_INVALID")
    open_timestamps.sort()
    for event in events:
        if not isinstance(event, Mapping):
            reasons.append("PUBLICATION_POLICY_EVENT_INVALID")
            continue
        event_id = str(event.get("canonical_event_id", ""))
        try:
            auction_date = date.fromisoformat(str(event.get("auction_session_date")))
            result_available_at = _parse_aware_timestamp(event.get("available_at"), "event_available_at")
            available_at = _parse_aware_timestamp(
                event.get("entry_available_at", event.get("available_at")), "event_entry_available_at"
            )
            expected_available = datetime.combine(auction_date + timedelta(days=1), time.min, tzinfo=NEW_YORK)
            if result_available_at != expected_available.astimezone(result_available_at.tzinfo):
                reasons.append(f"PUBLICATION_POLICY_AVAILABLE_AT_MISMATCH:{event_id}")
            if available_at != result_available_at:
                reasons.append(f"PUBLICATION_POLICY_ENTRY_AVAILABLE_AT_MISMATCH:{event_id}")
            first_open = next((timestamp for timestamp in open_timestamps if timestamp > available_at), None)
            if first_open is None:
                reasons.append(f"PUBLICATION_POLICY_FIRST_OPEN_MISSING:{event_id}")
            optional_open = next(
                (
                    event.get(field)
                    for field in (
                        "first_regular_open_after_available_at",
                        "entry_regular_open_timestamp",
                        "entry_open_timestamp",
                    )
                    if field in event
                ),
                None,
            )
            if optional_open is not None and first_open is not None:
                if _parse_aware_timestamp(optional_open, "event_first_open") != first_open:
                    reasons.append(f"PUBLICATION_POLICY_FIRST_OPEN_MISMATCH:{event_id}")
            if event.get("result_signal_eligibility") not in (None, RESULT_SIGNAL_ELIGIBILITY):
                reasons.append(f"PUBLICATION_POLICY_MISMATCH:result_signal_eligibility:{event_id}")
            if event.get("same_day_signal_allowed") not in (None, False):
                reasons.append(f"PUBLICATION_POLICY_MISMATCH:same_day_signal_allowed:{event_id}")
            if event.get("availability_derivation") not in (None, RESULT_AVAILABILITY_POLICY):
                reasons.append(f"PUBLICATION_POLICY_MISMATCH:availability_derivation:{event_id}")
        except (TypeError, ValueError):
            reasons.append(f"PUBLICATION_POLICY_NORMALIZED_EVENT_INVALID:{event_id}")
    if isinstance(trusted, Mapping):
        actual_ids = sorted(
            str(event.get("canonical_event_id"))
            for event in events
            if isinstance(event, Mapping) and isinstance(event.get("canonical_event_id"), str)
        )
        if actual_ids != trusted.get("expected_event_ids"):
            reasons.append("PUBLICATION_POLICY_NORMALIZED_EVENT_UNIVERSE_MISMATCH")
    return sorted(set(reasons))


def _validate_declared_schedule_linkage(
    declared: Any,
    documents: Mapping[str, Any],
    calendar: Mapping[str, Any],
) -> list[str]:
    """Recompute package schedule linkage from normalized events and calendar."""

    if declared is None:
        return []
    if not isinstance(declared, Mapping):
        return ["PUBLICATION_POLICY_SCHEDULE_LINKAGE_INVALID"]
    reasons: list[str] = []
    if set(declared) != {"policy", "strict_comparator", "event_count", "events", "canonical_digest_sha256"}:
        reasons.append("PUBLICATION_POLICY_SCHEDULE_LINKAGE_FIELDS_INVALID")
    if declared.get("policy") != RESULT_SIGNAL_ELIGIBILITY:
        reasons.append("PUBLICATION_POLICY_SCHEDULE_POLICY_MISMATCH")
    if declared.get("strict_comparator") != ">":
        reasons.append("PUBLICATION_POLICY_SCHEDULE_COMPARATOR_MISMATCH")
    rows = declared.get("events")
    events = documents.get("events")
    calendar_rows = calendar.get("records")
    if not isinstance(rows, list) or not isinstance(events, list) or not isinstance(calendar_rows, list):
        return sorted(set(reasons + ["PUBLICATION_POLICY_SCHEDULE_LINKAGE_INVALID"]))
    opens: list[tuple[datetime, str, str]] = []
    seen_sessions: set[str] = set()
    for calendar_row in calendar_rows:
        if not isinstance(calendar_row, Mapping) or calendar_row.get("holiday_or_closure_reason") not in (None, "", "NONE"):
            continue
        session = calendar_row.get("regular_session_date")
        if not isinstance(session, str) or not session.strip() or session in seen_sessions:
            reasons.append("PUBLICATION_POLICY_CALENDAR_OPEN_INVALID")
            continue
        try:
            opening = _parse_aware_timestamp(calendar_row.get("regular_open_timestamp"), "calendar_open")
        except (TypeError, ValueError):
            reasons.append("PUBLICATION_POLICY_CALENDAR_OPEN_INVALID")
            continue
        seen_sessions.add(session)
        opens.append((opening, session, opening.isoformat().replace("+00:00", "Z")))
    opens.sort(key=lambda value: value[0])
    computed: list[dict[str, Any]] = []
    for event in events:
        if not isinstance(event, Mapping):
            reasons.append("PUBLICATION_POLICY_SCHEDULE_EVENT_INVALID")
            continue
        event_id = event.get("canonical_event_id")
        if not isinstance(event_id, str) or not event_id.strip():
            reasons.append("PUBLICATION_POLICY_SCHEDULE_EVENT_INVALID")
            continue
        try:
            available = _parse_aware_timestamp(
                event.get("entry_available_at", event.get("available_at")), "event_available_at"
            )
        except (TypeError, ValueError):
            reasons.append(f"PUBLICATION_POLICY_SCHEDULE_EVENT_INVALID:{event_id}")
            continue
        future = [item for item in opens if item[0] > available]
        if len(future) < 6:
            reasons.append(f"PUBLICATION_POLICY_FIRST_OPEN_MISSING:{event_id}")
            continue
        computed.append(
            {
                "canonical_event_id": event_id,
                "available_at": available.isoformat().replace("+00:00", "Z"),
                "entry_session_date": future[0][1],
                "entry_open_timestamp": future[0][2],
                "future_open_timestamps": [item[2] for item in future[:6]],
                "holding_intervals": 5,
            }
        )
    if declared.get("event_count") != len(computed) or rows != computed:
        reasons.append("PUBLICATION_POLICY_SCHEDULE_LINKAGE_MISMATCH")
    digest = hashlib.sha256(
        json.dumps(computed, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    if declared.get("canonical_digest_sha256") != digest:
        reasons.append("PUBLICATION_POLICY_SCHEDULE_DIGEST_MISMATCH")
    return sorted(set(reasons))


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(f"jsonl_object_required:{line_number}")
        rows.append(value)
    return rows


def _load_csv(path: Path) -> list[dict[str, Any]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def _top_level_canonical_hash(payload: Mapping[str, Any], self_field: str) -> str:
    value = deepcopy(dict(payload))
    value.pop(self_field, None)
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _safe_path(root: Path, value: Any, *, must_exist: bool = True) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("path_missing")
    relative = Path(value)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("path_unsafe")
    root = root.resolve()
    resolved = (root / relative).resolve()
    if root != resolved and root not in resolved.parents:
        raise ValueError("path_outside_repo")
    if must_exist and (not resolved.is_file()):
        raise ValueError("path_missing")
    return resolved


def _relative_to_root(root: Path, path: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def _resolve_existing_argument(root: Path, value: Path) -> Path:
    """Resolve a CLI/API path relative to the declared repository root."""

    candidate = Path(value)
    if not candidate.is_absolute():
        candidate = root / candidate
    resolved = candidate.resolve()
    return _safe_path(root, _relative_to_root(root, resolved))


def _blocked(*blockers: str, checked_artifacts: int = 0) -> dict[str, Any]:
    unique = sorted({item for item in blockers if item})
    return {
        "status": "PRE_RUN_OK" if not unique else "BLOCKED",
        "blockers": unique,
        "checked_artifacts": checked_artifacts,
        "research_only": True,
        "read_only": True,
        "economic_execution": False,
        "economic_run": False,
        "economic_outcomes_accessed": False,
        "outcome_artifacts_present": False,
        "promotion": False,
        "integration_candidate": False,
        "paper_live_untouched": True,
        "paper_or_live_activation": False,
        "auto_promotion": False,
        "no_auto_promotion": True,
    }


def _result_digest(result: Mapping[str, Any]) -> str:
    value = {key: deepcopy(item) for key, item in result.items() if key != "preflight_receipt"}
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def build_preflight_receipt(result: Mapping[str, Any]) -> dict[str, Any]:
    """Build the single runner-compatible, outcome-free preflight receipt."""

    receipt: dict[str, Any] = {
        "schema_version": PREFLIGHT_RECEIPT_SCHEMA,
        "status": result.get("status"),
        "research_id": result.get("research_id", V2_RESEARCH_ID),
        "candidate_id": "R46",
        "research_only": True,
        "read_only": True,
        "economic_execution": False,
        "economic_run": False,
        "economic_outcomes_accessed": False,
        "outcome_artifacts_present": False,
        "paper_live_untouched": True,
        "paper_or_live_activation": False,
        "integration_candidate": False,
        "promotion": False,
        "auto_promotion": False,
        "no_auto_promotion": True,
        "manifest_path": result.get("governance_manifest_path"),
        "governance_manifest_path": result.get("governance_manifest_path"),
        "package_path": result.get("data_package_manifest_path"),
        "data_package_manifest_path": result.get("data_package_manifest_path"),
        "independent_software_review_path": result.get("independent_software_review_path"),
        "native_attestation_path": result.get("native_attestation_path"),
        "pit_source_attestation_path": result.get("pit_source_attestation_path"),
        "one_shot_state_path": result.get("one_shot_state_path"),
        "manifest_sha256": result.get("governance_manifest_sha256"),
        "governance_manifest_sha256": result.get("governance_manifest_sha256"),
        "manifest_file_sha256": result.get("governance_manifest_file_sha256"),
        "package_sha256": result.get("data_package_manifest_sha256"),
        "data_package_manifest_sha256": result.get("data_package_manifest_sha256"),
        "package_file_sha256": result.get("data_package_manifest_file_sha256"),
        "review_sha256": result.get("independent_software_review_sha256"),
        "review_file_sha256": result.get("independent_software_review_file_sha256"),
        "native_attestation_sha256": result.get("native_attestation_sha256"),
        "native_attestation_file_sha256": result.get("native_attestation_file_sha256"),
        "pit_source_attestation_sha256": result.get("pit_source_attestation_sha256"),
        "pit_source_attestation_file_sha256": result.get("pit_source_attestation_file_sha256"),
        "one_shot_state_sha256": result.get("one_shot_state_sha256"),
        "one_shot_state_canonical_sha256": result.get("one_shot_state_sha256"),
        "one_shot_state_file_sha256": result.get("one_shot_state_file_sha256"),
        "source_commit": result.get("source_commit"),
        "freeze_commit": result.get("freeze_commit"),
        "checked_artifacts": result.get("checked_artifacts", 0),
        "checks": deepcopy(result.get("checks", {})),
        "blockers": list(result.get("blockers", [])),
        "integrity": {
            "canonicalization": "UTF-8 JSON, sorted object keys, compact separators, ensure_ascii=false; omit integrity.receipt_sha256 before hashing",
            "receipt_sha256": "",
        },
    }
    receipt["integrity"]["receipt_sha256"] = canonical_hash(receipt, self_field="receipt_sha256")
    return receipt


def persist_preflight_receipt(result: Mapping[str, Any], path: Path) -> Path:
    """Atomically create a receipt file; never replace a prior receipt."""

    if not isinstance(result, VerifiedPreflightResult) or result.proof is not _PREFLIGHT_PROOF:
        raise ValueError("preflight_result_not_verified")
    if result.get("status") != "PRE_RUN_OK" or result.payload_digest != _result_digest(result):
        raise ValueError("preflight_result_not_verified")
    receipt = result.get("preflight_receipt")
    expected = build_preflight_receipt(result)
    if not isinstance(receipt, Mapping) or dict(receipt) != expected or not _self_hash_valid(receipt, "receipt_sha256"):
        raise ValueError("preflight_result_not_verified")
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary: str | None = None
    encoded = (json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", dir=target.parent, prefix=f".{target.name}.", delete=False
        ) as handle:
            temporary = handle.name
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, target)
        except FileExistsError as exc:
            raise FileExistsError(f"no_overwrite:{target}") from exc
        directory_descriptor = os.open(target.parent, os.O_RDONLY)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    finally:
        if temporary is not None:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
    return target


def _self_hash_valid(payload: Mapping[str, Any], field: str) -> bool:
    integrity = payload.get("integrity")
    declared = integrity.get(field) if isinstance(integrity, Mapping) else None
    return isinstance(declared, str) and declared == canonical_hash(payload, self_field=field)


def _validate_statistics(path: Path, manifest: Mapping[str, Any], root: Path) -> list[str]:
    reasons: list[str] = []
    try:
        plan = _load_json(path)
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
        return ["STATISTICS_PLAN_UNREADABLE"]
    expected_hash = (
        manifest.get("statistics", {}).get("plan_sha256") if isinstance(manifest.get("statistics"), Mapping) else None
    )
    if not isinstance(expected_hash, str) or expected_hash != plan.get("integrity", {}).get("artifact_sha256"):
        reasons.append("STATISTICS_PLAN_HASH_CROSSLINK")
    if not _self_hash_valid(plan, "artifact_sha256"):
        reasons.append("STATISTICS_PLAN_HASH_INVALID")
    if (
        plan.get("schema_version") != V2_STATISTICS_SCHEMA
        or plan.get("research_id") != V2_RESEARCH_ID
        or plan.get("candidate_id") != "R46"
    ):
        reasons.append("STATISTICS_PLAN_IDENTITY")
    if plan.get("claim_policy") != "retrospective_descriptive_only_no_confirmatory_claim":
        reasons.append("STATISTICS_CONFIRMATORY_CLAIM_FORBIDDEN")
    if plan.get("coverage_claim_policy") != "minimums_are_operational_gates_not_power_claims":
        reasons.append("STATISTICS_COVERAGE_POLICY_INVALID")
    if plan.get("sample_unit") != "event_episode":
        reasons.append("STATISTICS_SAMPLE_UNIT_INVALID")
    inferential = plan.get("inferential_family")
    if plan.get("reported_output_count") != EXPECTED_REPORTED_OUTPUT_COUNT:
        reasons.append("STATISTICS_REPORTED_OUTPUT_COUNT_INVALID")
    if (
        not isinstance(inferential, Mapping)
        or inferential.get("size") != len(EXPECTED_INFERENTIAL_IDS)
        or tuple(inferential.get("ids", ())) != EXPECTED_INFERENTIAL_IDS
        or inferential.get("method") != EXPECTED_FAMILY_METHOD
    ):
        reasons.append("STATISTICS_INFERENTIAL_FAMILY_INVALID")
    primary = plan.get("primary_estimand")
    if (
        not isinstance(primary, Mapping)
        or primary.get("estimand") != EXPECTED_PRIMARY_ESTIMAND
        or primary.get("horizon") != "five_open_to_open_intervals"
        or not isinstance(primary.get("id"), str)
        or primary.get("confirmatory_threshold") is not None
    ):
        reasons.append("STATISTICS_PRIMARY_ESTIMAND_INVALID")
    secondary = plan.get("secondary_diagnostics")
    if not isinstance(secondary, list) or not secondary:
        reasons.append("STATISTICS_SECONDARY_DIAGNOSTICS_MISSING")
    elif isinstance(primary, Mapping):
        secondary_ids = [item.get("id") for item in secondary if isinstance(item, Mapping)]
        primary_id = primary.get("id")
        expected_secondary = EXPECTED_FAMILY_IDS - {primary_id} if isinstance(primary_id, str) else set()
        if (
            any(not isinstance(item, str) for item in secondary_ids)
            or set(secondary_ids) != expected_secondary
            or len(secondary_ids) != len(expected_secondary)
        ):
            reasons.append("STATISTICS_SECONDARY_DIAGNOSTICS_INVALID")
    resampling = plan.get("resampling")
    if not isinstance(resampling, Mapping):
        reasons.append("STATISTICS_RESAMPLING_MISSING")
    else:
        seeds = [resampling.get(key) for key in ("bootstrap_seed", "permutation_seed", "placebo_seed")]
        if any(not isinstance(seed, int) or isinstance(seed, bool) for seed in seeds) or len(set(seeds)) != len(seeds):
            reasons.append("STATISTICS_RESAMPLING_SEEDS_INVALID")
        for key in ("bootstrap_repetitions", "permutation_repetitions", "block_length"):
            if not isinstance(resampling.get(key), int) or resampling[key] <= 0:
                reasons.append(f"STATISTICS_{key.upper()}_INVALID")
        if resampling.get("permutation_method") != EXPECTED_PERMUTATION_METHOD:
            reasons.append("STATISTICS_PERMUTATION_METHOD_INVALID")
    coverage = plan.get("coverage_gates")
    if not isinstance(coverage, Mapping):
        reasons.append("STATISTICS_COVERAGE_GATES_MISSING")
    else:
        for key in ("min_event_episodes", "min_holding_intervals"):
            if not isinstance(coverage.get(key), int) or coverage[key] <= 0:
                reasons.append(f"STATISTICS_{key.upper()}_INVALID")
        for key in ("max_missing_rate", "max_capacity_suppression_rate"):
            if (
                not isinstance(coverage.get(key), (int, float))
                or isinstance(coverage[key], bool)
                or not 0 <= coverage[key] <= 1
            ):
                reasons.append(f"STATISTICS_{key.upper()}_INVALID")
    return reasons


def _validate_family(path: Path, manifest: Mapping[str, Any]) -> list[str]:
    reasons: list[str] = []
    try:
        family = _load_json(path)
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
        return ["FAMILY_REGISTRY_UNREADABLE"]
    expected_hash = (
        manifest.get("multiplicity", {}).get("family_registry_sha256")
        if isinstance(manifest.get("multiplicity"), Mapping)
        else None
    )
    if not isinstance(expected_hash, str) or expected_hash != family.get("integrity", {}).get("artifact_sha256"):
        reasons.append("FAMILY_REGISTRY_HASH_CROSSLINK")
    if not _self_hash_valid(family, "artifact_sha256"):
        reasons.append("FAMILY_REGISTRY_HASH_INVALID")
    if (
        family.get("schema_version") != V2_FAMILY_SCHEMA
        or family.get("research_id") != V2_RESEARCH_ID
        or family.get("candidate_id") != "R46"
    ):
        reasons.append("FAMILY_REGISTRY_IDENTITY")
    if family.get("scope") != "r46_v2_reported_outputs_only":
        reasons.append("FAMILY_REGISTRY_SCOPE_INVALID")
    if (
        family.get("global_prior_search_family_complete") is not False
        or family.get("global_prior_search_family_status") != "INCOMPLETE_DISCLOSED"
    ):
        reasons.append("FAMILY_REGISTRY_GLOBAL_COMPLETENESS_INVALID")
    if family.get("adjusted_p_values_interpretation") != "descriptive_only":
        reasons.append("FAMILY_REGISTRY_ADJUSTED_P_VALUES_POLICY_INVALID")
    hypotheses = family.get("hypotheses")
    ids = [item.get("id") for item in hypotheses if isinstance(item, Mapping)] if isinstance(hypotheses, list) else []
    if (
        not isinstance(hypotheses, list)
        or not ids
        or any(not isinstance(item, str) or not item.strip() for item in ids)
        or len(ids) != len(set(ids))
    ):
        reasons.append("FAMILY_REGISTRY_HYPOTHESES_INVALID")
    elif set(ids) != EXPECTED_FAMILY_IDS:
        reasons.append("FAMILY_REGISTRY_REPORTED_OUTPUTS_INCOMPLETE")
    if (
        family.get("family_size") != len(ids)
        or family.get("reported_output_count") != EXPECTED_REPORTED_OUTPUT_COUNT
        or family.get("inferential_family_size") != len(EXPECTED_INFERENTIAL_IDS)
        or tuple(family.get("inferential_ids", ())) != EXPECTED_INFERENTIAL_IDS
        or not isinstance(family.get("alpha"), (int, float))
    ):
        reasons.append("FAMILY_REGISTRY_DECLARATION_INVALID")
    if family.get("method") != EXPECTED_FAMILY_METHOD:
        reasons.append("FAMILY_REGISTRY_METHOD_INVALID")
    if family.get("ordering") != EXPECTED_FAMILY_ORDERING:
        reasons.append("FAMILY_REGISTRY_ORDERING_INVALID")
    if family.get("claim_policy") != "retrospective_descriptive_only_no_confirmatory_claim":
        reasons.append("FAMILY_REGISTRY_CLAIM_POLICY_INVALID")
    hypothesis_items = hypotheses if isinstance(hypotheses, list) else []
    roles = {
        item.get("id"): item.get("role")
        for item in hypothesis_items
        if isinstance(item, Mapping) and isinstance(item.get("id"), str)
    }
    if roles != EXPECTED_FAMILY_ROLES:
        reasons.append("FAMILY_REGISTRY_ROLES_INVALID")
    if family.get("variant_inventory_complete") is not True:
        reasons.append("FAMILY_REGISTRY_INCOMPLETE")
    return reasons


def _git_commit_exists(root: Path, commit: Any) -> bool:
    if not isinstance(commit, str) or not HEX40_RE.fullmatch(commit):
        return False
    result = subprocess.run(
        ["git", "cat-file", "-e", f"{commit}^{{commit}}"],
        cwd=root,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return result.returncode == 0


def _git_blob_sha256(root: Path, commit: Any, relative: Any) -> str | None:
    if not isinstance(commit, str) or not HEX40_RE.fullmatch(commit) or not isinstance(relative, str):
        return None
    path = Path(relative)
    if path.is_absolute() or ".." in path.parts or not relative:
        return None
    result = subprocess.run(
        ["git", "show", f"{commit}:{path.as_posix()}"],
        cwd=root,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if result.returncode != 0:
        return None
    return hashlib.sha256(result.stdout).hexdigest()


def _git_worktree_clean(root: Path, *, allowed_paths: tuple[str, ...] = ()) -> bool:
    result = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=all"],
        cwd=root,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if result.returncode != 0:
        return False
    for line in result.stdout.decode("utf-8", errors="replace").splitlines():
        path = line[3:] if len(line) >= 4 else ""
        if " -> " in path:
            path = path.rsplit(" -> ", 1)[-1]
        if path not in allowed_paths:
            return False
    return True


def _git_head(root: Path) -> str | None:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=root,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    value = result.stdout.decode("ascii", errors="ignore").strip()
    return value if result.returncode == 0 and HEX40_RE.fullmatch(value) else None


def _git_is_ancestor(root: Path, ancestor: Any, descendant: Any) -> bool:
    if not isinstance(ancestor, str) or not isinstance(descendant, str):
        return False
    result = subprocess.run(
        ["git", "merge-base", "--is-ancestor", ancestor, descendant],
        cwd=root,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return result.returncode == 0


def _git_diff_paths(root: Path, source_commit: str, freeze_commit: str) -> list[str]:
    result = subprocess.run(
        ["git", "diff", "--name-only", "--diff-filter=ACMRTUXB", f"{source_commit}..{freeze_commit}"],
        cwd=root,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if result.returncode != 0:
        raise ValueError("SOURCE_FREEZE_DIFF_UNREADABLE")
    return [line for line in result.stdout.decode("utf-8", errors="strict").splitlines() if line]


def _validate_source_to_freeze_diff(root: Path, source_commit: str, freeze_commit: str) -> list[str]:
    try:
        paths = _git_diff_paths(root, source_commit, freeze_commit)
    except (OSError, UnicodeError, ValueError):
        return ["SOURCE_FREEZE_DIFF_UNREADABLE"]
    return [
        f"SOURCE_FREEZE_DIFF_FORBIDDEN:{path}"
        for path in paths
        if not any(path.startswith(prefix) for prefix in ALLOWED_SOURCE_TO_FREEZE_PREFIXES)
    ]


def _runtime_environment() -> dict[str, str]:
    return {
        "python": platform.python_version(),
        "platform": f"{platform.system()} {platform.machine()}",
        "timezone": os.environ.get("TZ") or "Europe/Istanbul",
        "locale": os.environ.get("LC_ALL") or os.environ.get("LANG") or locale.setlocale(locale.LC_ALL, None),
        "command": EXECUTION_COMMAND,
    }


def _validate_declared_source_file(
    source: Mapping[str, Any],
    root: Path,
    *,
    name: str,
    spec: Mapping[str, Any],
    commit: Any,
    label: str,
    reasons: list[str],
) -> None:
    try:
        path = _safe_path(root, name)
    except ValueError:
        reasons.append(f"SOURCE_FILE_MISSING:{label}")
        return
    declared = spec.get("sha256")
    if not isinstance(declared, str) or not HEX64_RE.fullmatch(declared):
        reasons.append(f"SOURCE_PIN_PLACEHOLDER:{label}")
    elif sha256_file(path) != declared:
        reasons.append(f"SOURCE_HASH_MISMATCH:{label}")
    if name in V5_EXTERNAL_CONTENT_PATHS:
        if (
            spec.get("required_in_source_commit") is not False
            or spec.get("required_in_base_commit") is not False
        ):
            reasons.append(f"SOURCE_EXTERNAL_COMMIT_REQUIREMENT_INVALID:{label}")
        if spec.get("git_blob_sha256") is not None:
            reasons.append(f"SOURCE_EXTERNAL_BLOB_FORBIDDEN:{label}")
        return
    blob_declared = spec.get("git_blob_sha256")
    if not isinstance(blob_declared, str) or not HEX64_RE.fullmatch(blob_declared):
        reasons.append(f"SOURCE_GIT_BLOB_PIN_REQUIRED:{label}")
    elif isinstance(commit, str):
        blob_actual = _git_blob_sha256(root, commit, name)
        if blob_actual is None:
            reasons.append(f"SOURCE_FILE_NOT_IN_COMMIT:{label}")
        elif blob_actual != blob_declared:
            reasons.append(f"SOURCE_GIT_BLOB_HASH_MISMATCH:{label}")
        elif sha256_file(path) != blob_actual:
            reasons.append(f"SOURCE_CURRENT_BYTES_NOT_SOURCE_COMMIT:{label}")


V5_INCUMBENT_DEPENDENCY_KEYS = (
    "bars",
    "lifetimes",
    "corporate_actions",
    "bars_manifest",
    "corporate_actions_manifest",
    "seam",
)


def _v5_spec_policy_reasons(key: str, spec: Mapping[str, Any]) -> list[str]:
    expected_path = V5_INCUMBENT_PATHS[key]
    reasons: list[str] = []
    if spec.get("path") != expected_path:
        reasons.append(f"INCUMBENT_DATA_PATH_MISMATCH:{key}")
    if spec.get("required_in_base_commit") is not (key in V5_BASE_REQUIRED_CONTENT_KEYS):
        reasons.append(f"INCUMBENT_DATA_BASE_COMMIT_REQUIREMENT:{key}")
    if spec.get("required_in_source_commit") is not (key in V5_SOURCE_REQUIRED_CONTENT_KEYS):
        reasons.append(f"INCUMBENT_DATA_SOURCE_COMMIT_REQUIREMENT:{key}")
    blob = spec.get("git_blob_sha256")
    if key in V5_EXTERNAL_CONTENT_KEYS:
        if blob is not None:
            reasons.append(f"INCUMBENT_DATA_EXTERNAL_BLOB_FORBIDDEN:{key}")
    elif not isinstance(blob, str) or not HEX64_RE.fullmatch(blob):
        reasons.append(f"INCUMBENT_DATA_GIT_BLOB_REQUIRED:{key}")
    return reasons


def _validate_v5_incumbent_policy(source: Any) -> list[str]:
    """Reject manifest-declared V5 paths and commit requirements that drift from code."""

    if not isinstance(source, Mapping):
        return ["INCUMBENT_DATA_CONTRACT_REQUIRED"]
    source_block = source.get("incumbent_dependencies")
    if not isinstance(source_block, Mapping):
        return ["INCUMBENT_DATA_CONTRACT_REQUIRED"]
    source_files = source.get("source_files")
    if not isinstance(source_files, Mapping):
        source_files = {}

    reasons: list[str] = []
    crosslink_fields = (
        "path",
        "sha256",
        "byte_size",
        "git_blob_sha256",
        "required_in_base_commit",
        "required_in_source_commit",
        "status",
    )
    for key in V5_INCUMBENT_DEPENDENCY_KEYS:
        spec = source_block.get(key)
        if not isinstance(spec, Mapping):
            reasons.append(f"INCUMBENT_DATA_SPEC:{key}")
            continue
        reasons.extend(_v5_spec_policy_reasons(key, spec))
        source_spec = source_files.get(V5_INCUMBENT_PATHS[key])
        if not isinstance(source_spec, Mapping):
            if key not in V5_EXTERNAL_CONTENT_KEYS:
                reasons.append(f"INCUMBENT_DATA_SOURCE_PIN:{key}")
            continue
        reasons.extend(_v5_spec_policy_reasons(key, source_spec))
        for field in crosslink_fields:
            if spec.get(field) != source_spec.get(field):
                reasons.append(f"INCUMBENT_DATA_SOURCE_CROSSLINK:{key}:{field}")
    return sorted(set(reasons))


def _validate_incumbent_data_contract(manifest: Mapping[str, Any], root: Path) -> list[str]:
    """Validate static V5 PIT dependencies and their manifest cross-links."""

    source = manifest.get("source_provenance")
    data_contract = manifest.get("data_contract")
    source_block = source.get("incumbent_dependencies") if isinstance(source, Mapping) else None
    data_block = data_contract.get("incumbent_data_contract") if isinstance(data_contract, Mapping) else None
    reasons: list[str] = []
    if not isinstance(source_block, Mapping) or not isinstance(data_block, Mapping):
        return ["INCUMBENT_DATA_CONTRACT_REQUIRED"]
    if dict(source_block) != dict(data_block):
        reasons.append("INCUMBENT_DATA_CONTRACT_CROSSLINK")
    if source_block.get("schema_version") != "r46-incumbent-data-contract-v1":
        reasons.append("INCUMBENT_DATA_CONTRACT_SCHEMA")
    if source_block.get("historical_cutoff") != CUTOFF_UTC:
        reasons.append("INCUMBENT_DATA_CUTOFF")
    source_files = source.get("source_files") if isinstance(source, Mapping) else None
    if not isinstance(source_files, Mapping):
        reasons.append("INCUMBENT_DATA_SOURCE_FILES_REQUIRED")
        source_files = {}
    reasons.extend(_validate_v5_incumbent_policy(source))
    paths: dict[str, Path] = {}
    external_content_keys = V5_EXTERNAL_CONTENT_KEYS
    for key in V5_INCUMBENT_DEPENDENCY_KEYS:
        spec = source_block.get(key)
        if not isinstance(spec, Mapping):
            reasons.append(f"INCUMBENT_DATA_SPEC:{key}")
            continue
        expected_relative = V5_INCUMBENT_PATHS[key]
        relative = spec.get("path")
        if not isinstance(relative, str) or not relative:
            reasons.append(f"INCUMBENT_DATA_PATH:{key}")
            continue
        source_spec = source_files.get(expected_relative)
        if not isinstance(source_spec, Mapping) and key not in external_content_keys:
            reasons.append(f"INCUMBENT_DATA_SOURCE_PIN:{key}")
        elif isinstance(source_spec, Mapping):
            for field in (
                "sha256",
                "byte_size",
                "git_blob_sha256",
                "required_in_base_commit",
                "required_in_source_commit",
                "status",
            ):
                if spec.get(field) != source_spec.get(field):
                    reasons.append(f"INCUMBENT_DATA_SOURCE_CROSSLINK:{key}:{field}")
        elif (
            spec.get("required_in_base_commit") is not False
            or spec.get("required_in_source_commit") is not False
            or spec.get("git_blob_sha256") is not None
        ):
            reasons.append(f"INCUMBENT_DATA_EXTERNAL_PIN_INVALID:{key}")
        try:
            resolved = _safe_path(root, relative)
        except ValueError:
            reasons.append(f"INCUMBENT_DATA_MISSING:{key}")
            continue
        declared_hash = spec.get("sha256")
        if not isinstance(declared_hash, str) or not HEX64_RE.fullmatch(declared_hash):
            reasons.append(f"INCUMBENT_DATA_HASH:{key}")
        elif sha256_file(resolved) != declared_hash:
            reasons.append(f"INCUMBENT_DATA_HASH:{key}")
        declared_size = spec.get("byte_size")
        if not isinstance(declared_size, int) or isinstance(declared_size, bool) or declared_size != resolved.stat().st_size:
            reasons.append(f"INCUMBENT_DATA_SIZE:{key}")
        paths[key] = resolved
        blob_hash = spec.get("git_blob_sha256")
        if key not in external_content_keys and (not isinstance(blob_hash, str) or not HEX64_RE.fullmatch(blob_hash)):
            reasons.append(f"INCUMBENT_DATA_GIT_BLOB:{key}")

    for key, declared in (
        ("bars_manifest_sha256", source_block.get("bars_manifest_sha256")),
        ("corporate_actions_manifest_sha256", source_block.get("corporate_actions_manifest_sha256")),
    ):
        expected_key = "bars_manifest" if key.startswith("bars_") else "corporate_actions_manifest"
        if declared != (source_block.get(expected_key, {}) or {}).get("sha256"):
            reasons.append(f"INCUMBENT_DATA_MANIFEST_HASH_ALIAS:{key}")

    try:
        bars_manifest = _load_json(paths["bars_manifest"])
        corporate_actions_manifest = _load_json(paths["corporate_actions_manifest"])
    except (KeyError, OSError, UnicodeError, ValueError, json.JSONDecodeError):
        return sorted(set(reasons + ["INCUMBENT_DATA_MANIFEST_UNREADABLE"]))

    def manifest_path(value: Any) -> Path | None:
        if not isinstance(value, str) or not value:
            return None
        candidate = Path(value)
        if candidate.is_absolute():
            return candidate.resolve()
        try:
            return _safe_path(root, value)
        except ValueError:
            return None

    bars_dataset = manifest_path(bars_manifest.get("dataset"))
    ca_dataset = manifest_path(corporate_actions_manifest.get("dataset"))
    if bars_dataset != paths.get("bars"):
        reasons.append("INCUMBENT_DATA_BARS_MANIFEST_PATH")
    if ca_dataset != paths.get("corporate_actions"):
        reasons.append("INCUMBENT_DATA_CA_MANIFEST_PATH")
    for payload, key in ((bars_manifest, "bars"), (corporate_actions_manifest, "corporate_actions")):
        if payload.get("sha256") != source_block.get(key, {}).get("sha256"):
            reasons.append(f"INCUMBENT_DATA_MANIFEST_DATA_HASH:{key}")
        if payload.get("byte_size") != paths[key].stat().st_size:
            reasons.append(f"INCUMBENT_DATA_MANIFEST_SIZE:{key}")
    lifetime_path = manifest_path(bars_manifest.get("lifetime_table"))
    if lifetime_path != paths.get("lifetimes") or bars_manifest.get("lifetime_table_hash") != source_block.get(
        "lifetimes", {}
    ).get("sha256"):
        reasons.append("INCUMBENT_DATA_LIFETIMES_MANIFEST_LINK")
    last_date = bars_manifest.get("last_date")
    if not isinstance(last_date, str) or last_date[:10] > CUTOFF_UTC[:10]:
        reasons.append("INCUMBENT_DATA_BARS_POST_CUTOFF")
    return sorted(set(reasons))


def _validate_source_provenance(
    source: Any,
    root: Path,
    *,
    manifest_sha256: Any = None,
    research_id: Any = None,
    freeze_commit: Any = None,
    post_freeze_paths: tuple[str, ...] = (),
) -> list[str]:
    """Validate source C_src, current freeze HEAD, bytes, and runtime pins."""

    del manifest_sha256, research_id
    reasons: list[str] = []
    if not isinstance(source, Mapping):
        return ["SOURCE_PROVENANCE_MISSING"]
    commits: dict[str, Any] = {}
    for field in ("base_commit", "source_commit"):
        value = source.get(field)
        commits[field] = value
        if value == PLACEHOLDER:
            reasons.append("SOURCE_COMMIT_PLACEHOLDER")
        elif not isinstance(value, str) or not HEX40_RE.fullmatch(value):
            reasons.append("SOURCE_COMMIT_PIN_INVALID")
        elif not _git_commit_exists(root, value):
            reasons.append(f"SOURCE_COMMIT_NOT_FOUND:{field}")

    source_commit = commits.get("source_commit")
    base_commit = commits.get("base_commit")
    head = _git_head(root)
    expected_freeze = freeze_commit if freeze_commit is not None else head
    if not isinstance(expected_freeze, str) or not HEX40_RE.fullmatch(expected_freeze):
        reasons.append("FREEZE_COMMIT_PIN_INVALID")
    elif head != expected_freeze:
        reasons.append("FREEZE_COMMIT_NOT_HEAD")
    if (
        isinstance(source_commit, str)
        and isinstance(expected_freeze, str)
        and not _git_is_ancestor(root, source_commit, expected_freeze)
    ):
        reasons.append("SOURCE_COMMIT_NOT_ANCESTOR_OF_FREEZE")
    if (
        isinstance(base_commit, str)
        and HEX40_RE.fullmatch(base_commit)
        and isinstance(source_commit, str)
        and HEX40_RE.fullmatch(source_commit)
        and _git_commit_exists(root, base_commit)
        and _git_commit_exists(root, source_commit)
        and not _git_is_ancestor(root, base_commit, source_commit)
    ):
        reasons.append("BASE_COMMIT_NOT_ANCESTOR_OF_SOURCE")
    if (
        isinstance(source_commit, str)
        and HEX40_RE.fullmatch(source_commit)
        and isinstance(expected_freeze, str)
        and HEX40_RE.fullmatch(expected_freeze)
        and _git_commit_exists(root, source_commit)
        and _git_commit_exists(root, expected_freeze)
    ):
        reasons.extend(_validate_source_to_freeze_diff(root, source_commit, expected_freeze))

    if source.get("clean_tree_required") is not True or source.get("clean_tree_observed") is not True:
        reasons.append("SOURCE_CLEAN_TREE_REQUIRED")
    else:
        tree_clean = (
            _git_worktree_clean(root, allowed_paths=post_freeze_paths)
            if post_freeze_paths
            else _git_worktree_clean(root)
        )
        if not tree_clean:
            reasons.append("SOURCE_CLEAN_TREE_DIRTY")

    source_files = source.get("source_files")
    checked_base_paths: set[str] = set()

    def validate_base_blob(name: str, spec: Mapping[str, Any]) -> None:
        if (
            not isinstance(base_commit, str)
            or not HEX40_RE.fullmatch(base_commit)
            or spec.get("required_in_base_commit") is not True
            or name in V5_EXTERNAL_CONTENT_PATHS
            or name in checked_base_paths
        ):
            return
        checked_base_paths.add(name)
        declared_blob = spec.get("git_blob_sha256")
        actual_blob = _git_blob_sha256(root, base_commit, name)
        if actual_blob is None:
            reasons.append(f"SOURCE_FILE_NOT_IN_BASE_COMMIT:{name}")
        elif declared_blob != actual_blob:
            reasons.append(f"SOURCE_BASE_GIT_BLOB_HASH_MISMATCH:{name}")

    if not isinstance(source_files, Mapping) or not source_files:
        reasons.append("SOURCE_FILES_UNPINNED")
    else:
        for name, spec in source_files.items():
            if not isinstance(name, str) or not isinstance(spec, Mapping):
                reasons.append("SOURCE_FILE_DECLARATION_INVALID")
                continue
            _validate_declared_source_file(
                source, root, name=name, spec=spec, commit=source_commit, label=name, reasons=reasons
            )
            validate_base_blob(name, spec)

    for role in REQUIRED_SOURCE_ROLES:
        spec = source.get(role)
        if not isinstance(spec, Mapping):
            reasons.append(f"SOURCE_ROLE_MISSING:{role}")
            continue
        path_value = spec.get("path")
        if not isinstance(path_value, str):
            reasons.append(f"SOURCE_ROLE_FILE_MISSING:{role}")
            continue
        _validate_declared_source_file(
            source, root, name=path_value, spec=spec, commit=source_commit, label=role, reasons=reasons
        )
        validate_base_blob(path_value, spec)

    environment = source.get("environment")
    if not isinstance(environment, Mapping) or environment.get("network") not in {"disabled", False}:
        reasons.append("ENVIRONMENT_NETWORK_NOT_DISABLED")
    else:
        lock_path_value = environment.get("dependency_lock_path")
        lock_hash = environment.get("dependency_lock_sha256")
        lock_blob_hash = environment.get("dependency_lock_git_blob_sha256")
        try:
            lock_path = _safe_path(root, lock_path_value)
        except ValueError:
            lock_path = None
            reasons.append("ENVIRONMENT_DEPENDENCY_LOCK_REQUIRED")
        if not isinstance(lock_hash, str) or not HEX64_RE.fullmatch(lock_hash):
            reasons.append("ENVIRONMENT_DEPENDENCY_LOCK_REQUIRED")
        elif lock_path is not None and sha256_file(lock_path) != lock_hash:
            reasons.append("ENVIRONMENT_DEPENDENCY_LOCK_HASH_MISMATCH")
        if not isinstance(lock_blob_hash, str) or not HEX64_RE.fullmatch(lock_blob_hash):
            reasons.append("ENVIRONMENT_DEPENDENCY_LOCK_GIT_BLOB_REQUIRED")
        elif lock_path_value and isinstance(source_commit, str):
            actual_lock_blob = _git_blob_sha256(root, source_commit, lock_path_value)
            if actual_lock_blob is None:
                reasons.append("ENVIRONMENT_DEPENDENCY_LOCK_NOT_IN_COMMIT")
            elif actual_lock_blob != lock_blob_hash:
                reasons.append("ENVIRONMENT_DEPENDENCY_LOCK_GIT_BLOB_MISMATCH")
            elif lock_path is not None and sha256_file(lock_path) != actual_lock_blob:
                reasons.append("ENVIRONMENT_DEPENDENCY_LOCK_CURRENT_BYTES_MISMATCH")
        actual_environment = _runtime_environment()
        for field, actual in actual_environment.items():
            declared = environment.get(field)
            if not isinstance(declared, str) or not declared.strip() or declared == PLACEHOLDER:
                reasons.append(f"ENVIRONMENT_{field.upper()}_REQUIRED")
            elif declared != actual:
                reasons.append(f"ENVIRONMENT_{field.upper()}_MISMATCH")

    # The old v1 capability receipt is intentionally not a v2 readiness source.
    # V2 readiness is proven by the package's content-level acquisition evidence.
    return reasons


def _validate_independent_review(
    path: Path, manifest: Mapping[str, Any], source: Mapping[str, Any], *, root: Path | None = None
) -> tuple[list[str], dict[str, Any]]:
    reasons: list[str] = []
    try:
        payload = _load_json(path)
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
        return ["INDEPENDENT_SOFTWARE_REVIEW_UNREADABLE"], {}
    if payload.get("schema_version") != "r46-independent-software-review-v1" or payload.get("status") not in {
        "REVIEWED",
        "VALIDATED",
    }:
        reasons.append("INDEPENDENT_SOFTWARE_REVIEW_SCHEMA")
    allowed_keys = {
        "schema_version",
        "status",
        "research_id",
        "candidate_id",
        "reviewed_commit",
        "governance_manifest_sha256",
        "independence",
        "reviewed_paths",
        "integrity",
    }
    if set(payload) - allowed_keys:
        reasons.append("INDEPENDENT_SOFTWARE_REVIEW_SCHEMA_KEYS")
    if _artifact_contains_forbidden(path):
        reasons.append("INDEPENDENT_SOFTWARE_REVIEW_OUTCOME_FIELDS")
    if payload.get("research_id") != manifest.get("research_id") or payload.get("candidate_id") != "R46":
        reasons.append("INDEPENDENT_SOFTWARE_REVIEW_CROSSLINK")
    if payload.get("reviewed_commit") != source.get("source_commit") or payload.get(
        "governance_manifest_sha256"
    ) != manifest.get("integrity", {}).get("manifest_sha256"):
        reasons.append("INDEPENDENT_SOFTWARE_REVIEW_CROSSLINK")
    if (
        payload.get("independence") != "independent_software_review"
        or not isinstance(payload.get("reviewed_paths"), list)
        or not payload.get("reviewed_paths")
    ):
        reasons.append("INDEPENDENT_SOFTWARE_REVIEW_INDEPENDENCE_REQUIRED")
    reviewed_paths = payload.get("reviewed_paths")
    allowed_paths: set[str] = set()
    source_files = source.get("source_files")
    if isinstance(source_files, Mapping):
        allowed_paths.update(name for name in source_files if isinstance(name, str))
    for role in REQUIRED_SOURCE_ROLES:
        spec = source.get(role)
        if isinstance(spec, Mapping) and isinstance(spec.get("path"), str):
            allowed_paths.add(spec["path"])
    if isinstance(reviewed_paths, list):
        for reviewed_path in reviewed_paths:
            path_invalid = False
            if root is not None and isinstance(reviewed_path, str):
                try:
                    _safe_path(root, reviewed_path, must_exist=False)
                except ValueError:
                    path_invalid = True
            if (
                not isinstance(reviewed_path, str)
                or not reviewed_path.strip()
                or Path(reviewed_path).is_absolute()
                or ".." in Path(reviewed_path).parts
                or reviewed_path not in allowed_paths
                or path_invalid
            ):
                reasons.append("INDEPENDENT_SOFTWARE_REVIEW_PATH_INVALID")
                break
    review_hash = (
        payload.get("integrity", {}).get("review_sha256") if isinstance(payload.get("integrity"), Mapping) else None
    )
    if not isinstance(review_hash, str) or review_hash != canonical_hash(payload, self_field="review_sha256"):
        reasons.append("INDEPENDENT_SOFTWARE_REVIEW_SELF_HASH")
    return sorted(set(reasons)), payload


def _validate_one_shot(path: Path, manifest: Mapping[str, Any], root: Path) -> list[str]:
    try:
        state = _load_json(path)
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
        return ["ONE_SHOT_STATE_UNREADABLE"]
    expected = (
        manifest.get("evaluation_contract", {}).get("one_shot_state_sha256")
        if isinstance(manifest.get("evaluation_contract"), Mapping)
        else None
    )
    if not isinstance(expected, str) or expected != state.get("integrity", {}).get("artifact_sha256"):
        return ["ONE_SHOT_STATE_HASH_CROSSLINK"]
    reasons: list[str] = []
    if _artifact_contains_forbidden(path):
        reasons.append("ONE_SHOT_OUTCOME_FIELDS")
    if not _self_hash_valid(state, "artifact_sha256"):
        reasons.append("ONE_SHOT_STATE_HASH_INVALID")
    if (
        state.get("schema_version") != V2_ONE_SHOT_SCHEMA
        or state.get("research_id") != V2_RESEARCH_ID
        or state.get("candidate_id") != "R46"
    ):
        reasons.append("ONE_SHOT_STATE_IDENTITY")
    if state.get("status") != "SEALED_UNUSED":
        reasons.append("ONE_SHOT_STATE_INVALID")
    if state.get("prior_access_acknowledged") is not True or state.get("contamination_acknowledged") is not True:
        reasons.append("ONE_SHOT_DISCLOSURE_REQUIRED")
    for field in ("confirmatory_claim_forbidden", "tuning_forbidden", "rerun_forbidden"):
        if state.get(field) is not True:
            reasons.append(f"ONE_SHOT_{field.upper()}_REQUIRED")
    if state.get("run_count") != 0 or state.get("release_count") != 0:
        reasons.append("ONE_SHOT_ALREADY_CONSUMED")
    if state.get("outcome_artifacts_present") is not False:
        reasons.append("ONE_SHOT_OUTCOME_ACCESS_INVALID")
    if state.get("reservation_owner") != "execution" or state.get("reservation_state") != "UNRESERVED":
        reasons.append("ONE_SHOT_RESERVATION_OWNER_INVALID")
    return reasons


def _json_contains_forbidden(value: Any, *, allow_fills: bool = False) -> bool:
    if isinstance(value, Mapping):
        for key, child in value.items():
            normalized_key = _normalize_outcome_key(key)
            if (
                normalized_key in FORBIDDEN_OUTCOME_KEYS
                and not (allow_fills and normalized_key == "fills")
            ):
                return True
            if _json_contains_forbidden(child, allow_fills=allow_fills):
                return True
    elif isinstance(value, list):
        return any(_json_contains_forbidden(child, allow_fills=allow_fills) for child in value)
    return False


def _normalize_outcome_key(key: object) -> str:
    if not isinstance(key, str):
        return ""
    return re.sub(r"[^a-z0-9]+", "_", key.casefold()).strip("_")


def _contains_key(value: Any, names: set[str]) -> bool:
    if isinstance(value, Mapping):
        return any(
            (isinstance(key, str) and key in names) or _contains_key(child, names) for key, child in value.items()
        )
    if isinstance(value, list):
        return any(_contains_key(child, names) for child in value)
    return False


def _contains_non_null_key(value: Any, names: set[str]) -> bool:
    if isinstance(value, Mapping):
        return any(
            (isinstance(key, str) and key in names and child is not None) or _contains_non_null_key(child, names)
            for key, child in value.items()
        )
    if isinstance(value, list):
        return any(_contains_non_null_key(child, names) for child in value)
    return False


def _artifact_contains_forbidden(path: Path, *, allow_native_fills: bool = False) -> bool:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return False
    lines = [line for line in text.splitlines() if line.strip()]
    parsed_any = False
    for line in lines:
        try:
            value = json.loads(line)
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        parsed_any = True
        if _json_contains_forbidden(value, allow_fills=allow_native_fills):
            return True
    if parsed_any:
        return False
    try:
        value = json.loads(text)
    except (TypeError, ValueError, json.JSONDecodeError):
        value = None
    if value is not None and _json_contains_forbidden(value, allow_fills=allow_native_fills):
        return True
    if len(lines) > 0:
        try:
            rows = list(csv.reader(lines))
        except (csv.Error, UnicodeError):
            rows = []
        if rows and len(rows[0]) > 1:
            return any(_normalize_outcome_key(field) in FORBIDDEN_OUTCOME_KEYS for field in rows[0])
    return False


def _validate_native_attestation(
    receipt: Mapping[str, Any],
    manifest: Mapping[str, Any],
    *,
    package_sha256: Any = None,
    repo_root: Path | None = None,
) -> list[str]:
    reasons: list[str] = []
    required = {
        "schema_version",
        "status",
        "research_id",
        "candidate_id",
        "synthetic_prices",
        "native_adapter_verified",
        "native_engine",
        "native_one_ledger",
        "shared_account",
        "research_only",
        "economic_execution",
        "economic_run",
        "outcome_artifacts_present",
        "paper_live_untouched",
        "paper_or_live_activation",
        "promotion",
        "integration_candidate",
        "auto_promotion",
        "no_auto_promotion",
        "separate_sleeve_blend",
        "runner_path",
        "runner_sha256",
        "verifier_path",
        "verifier_sha256",
        "source_path",
        "source_sha256",
        "governance_canonical_sha256",
        "package_canonical_sha256",
        "package_sha256",
        "required_fields",
        "fills",
        "ledger",
        "manifest_sha256",
        "attestation_sha256",
    }
    if not required.issubset(receipt):
        reasons.append("NATIVE_ATTESTATION_SCHEMA")
    if receipt.get("schema_version") != NATIVE_ATTESTATION_SCHEMA or receipt.get("status") != "READY_NATIVE_LEDGER":
        reasons.append("NATIVE_ATTESTATION_SCHEMA")
    if receipt.get("research_id") != manifest.get("research_id") or receipt.get("candidate_id") != "R46":
        reasons.append("NATIVE_ATTESTATION_IDENTITY")
    if (
        receipt.get("synthetic_prices") is not True
        or receipt.get("native_adapter_verified") is not True
        or receipt.get("native_engine") != "PortfolioBacktestEngine"
        or receipt.get("native_one_ledger") is not True
        or receipt.get("shared_account") is not True
    ):
        reasons.append("NATIVE_ATTESTATION_ONE_LEDGER_REQUIRED")
    for field, expected in {
        "research_only": True,
        "economic_execution": False,
        "economic_run": False,
        "outcome_artifacts_present": False,
        "paper_live_untouched": True,
        "paper_or_live_activation": False,
        "promotion": False,
        "integration_candidate": False,
        "auto_promotion": False,
        "no_auto_promotion": True,
        "separate_sleeve_blend": False,
    }.items():
        if receipt.get(field) is not expected:
            reasons.append(f"NATIVE_ATTESTATION_BOUNDARY:{field}")
    expected_manifest_hash = manifest.get("integrity", {}).get("manifest_sha256")
    if (
        receipt.get("manifest_sha256") != expected_manifest_hash
        or receipt.get("governance_canonical_sha256") != expected_manifest_hash
    ):
        reasons.append("NATIVE_ATTESTATION_MANIFEST_CROSSLINK")
    if package_sha256 is not None and (
        receipt.get("package_sha256") != package_sha256 or receipt.get("package_canonical_sha256") != package_sha256
    ):
        reasons.append("NATIVE_ATTESTATION_PACKAGE_CROSSLINK")
    runner = (
        manifest.get("source_provenance", {}).get("runner")
        if isinstance(manifest.get("source_provenance"), Mapping)
        else None
    )
    if isinstance(runner, Mapping) and receipt.get("runner_path") != runner.get("path"):
        reasons.append("NATIVE_ATTESTATION_RUNNER_CROSSLINK")
    for role, path_field, hash_field in (
        ("runner", "runner_path", "runner_sha256"),
        ("verifier", "verifier_path", "verifier_sha256"),
    ):
        declared = manifest.get("source_provenance", {}).get(role) if isinstance(manifest.get("source_provenance"), Mapping) else None
        if isinstance(declared, Mapping):
            if receipt.get(path_field) != declared.get("path"):
                reasons.append(f"NATIVE_ATTESTATION_{role.upper()}_PATH_CROSSLINK")
            if isinstance(declared.get("sha256"), str) and receipt.get(hash_field) != declared.get("sha256"):
                reasons.append(f"NATIVE_ATTESTATION_{role.upper()}_HASH_CROSSLINK")
    source_files = manifest.get("source_provenance", {}).get("source_files") if isinstance(manifest.get("source_provenance"), Mapping) else None
    source_spec = source_files.get(receipt.get("source_path")) if isinstance(source_files, Mapping) else None
    if isinstance(source_spec, Mapping):
        if receipt.get("source_sha256") != source_spec.get("sha256"):
            reasons.append("NATIVE_ATTESTATION_SOURCE_HASH_CROSSLINK")
    else:
        reasons.append("NATIVE_ATTESTATION_SOURCE_PATH_CROSSLINK")
    required_fields = receipt.get("required_fields")
    if not isinstance(required_fields, list) or set(required_fields) != {
        "decision_timestamp",
        "signal_asof_timestamp",
        "target_weights",
        "realized_holdings",
        "fills",
        "fees",
        "cash",
        "suppression_reason",
        "ownership_attribution",
    }:
        reasons.append("NATIVE_ATTESTATION_LEDGER_FIELDS")
    declared_hash = receipt.get("attestation_sha256")
    if (
        not isinstance(declared_hash, str)
        or not HEX64_RE.fullmatch(declared_hash)
        or declared_hash != _top_level_canonical_hash(receipt, "attestation_sha256")
    ):
        reasons.append("NATIVE_ATTESTATION_SELF_HASH")
    ledger = receipt.get("ledger")
    if not isinstance(ledger, Mapping):
        reasons.append("NATIVE_ATTESTATION_LEDGER_INVALID")
    else:
        try:
            from scripts.research.verify_r46_treasury_auction_unwind_tlt_v2_2026_09_10 import verify_attestation

            verified = verify_attestation(receipt, repo_root=repo_root or REPO)
        except (ImportError, TypeError, ValueError, KeyError, AttributeError):
            reasons.append("NATIVE_ATTESTATION_INDEPENDENT_VERIFIER_FAILED")
        else:
            if not isinstance(verified, Mapping) or verified.get("status") != "VERIFIED_NATIVE_LEDGER":
                reasons.append("NATIVE_ATTESTATION_INDEPENDENT_VERIFIER_FAILED")
    if not isinstance(ledger, Mapping) or receipt.get("fills") != ledger.get("fills"):
        reasons.append("NATIVE_ATTESTATION_FILL_CROSSLINK")
    return reasons


def _resolve_nested_path(root: Path, base: Path, value: Any) -> Path:
    try:
        return _safe_path(root, value)
    except ValueError:
        if not isinstance(value, str) or Path(value).is_absolute() or ".." in Path(value).parts:
            raise
        return _safe_path(root, _relative_to_root(root, base.parent / value))


def _bar_key_set(path: Path) -> set[tuple[str, str, str]]:
    rows = _load_csv(path)
    keys: set[tuple[str, str, str]] = set()
    for row in rows:
        permanent = row.get("permanent_security_identifier")
        session_date = row.get("session_date")
        timestamp = row.get("regular_open_timestamp") or row.get("timestamp")
        if not all(isinstance(value, str) and value for value in (permanent, session_date, timestamp)):
            raise ValueError("raw_split_key_invalid")
        keys.add((cast(str, permanent), cast(str, session_date), cast(str, timestamp)))
    if not keys:
        raise ValueError("raw_split_empty")
    return keys


def _validate_acquisition_receipt(path: Path, package: Mapping[str, Any], root: Path) -> list[str]:
    """Validate the v2 Alpaca receipt and recompute raw/split key parity."""

    reasons: list[str] = []
    try:
        receipt = _load_json(path)
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
        return ["PIT_SOURCE_ACQUISITION_UNREADABLE"]
    if receipt.get("schema") != "r46-tlt-market-data-acquisition-v2":
        reasons.append("PIT_SOURCE_ACQUISITION_SCHEMA")
    if (
        receipt.get("status") != "COMPLETE"
        or receipt.get("acquisition_mode") != "retrospective"
        or receipt.get("retrospective_acquisition") is not True
    ):
        reasons.append("PIT_SOURCE_ACQUISITION_NOT_COMPLETE")
    if receipt.get("historical_cutoff") != CUTOFF_UTC or receipt.get("economic_execution") is not False:
        reasons.append("PIT_SOURCE_ACQUISITION_BOUNDARY")
    declared_self = (
        receipt.get("integrity", {}).get("receipt_sha256") if isinstance(receipt.get("integrity"), Mapping) else None
    )
    if not isinstance(declared_self, str) or declared_self != canonical_hash(receipt, self_field="receipt_sha256"):
        reasons.append("PIT_SOURCE_ACQUISITION_SELF_HASH")
    acquisition = package.get("acquisition")
    if not isinstance(acquisition, Mapping):
        return sorted(set(reasons + ["PIT_SOURCE_ACQUISITION_REFERENCE_REQUIRED"]))
    if acquisition.get("receipt_path") != _relative_to_root(root, path) or acquisition.get(
        "receipt_sha256"
    ) != sha256_file(path):
        reasons.append("PIT_SOURCE_ACQUISITION_CROSSLINK")
    if acquisition.get("raw_split_key_parity") is not True or receipt.get("raw_split_key_parity") is not True:
        reasons.append("PIT_SOURCE_RAW_SPLIT_PARITY_REQUIRED")
    artifacts = receipt.get("artifacts")
    if not isinstance(artifacts, Mapping):
        return sorted(set(reasons + ["PIT_SOURCE_ACQUISITION_ARTIFACTS_REQUIRED"]))
    try:
        raw_spec = artifacts.get("raw_bars")
        split_spec = artifacts.get("split_bars")
        if not isinstance(raw_spec, Mapping) or not isinstance(split_spec, Mapping):
            raise ValueError("raw_split_artifact_missing")
        raw_path = _resolve_nested_path(root, path, raw_spec.get("path"))
        split_path = _resolve_nested_path(root, path, split_spec.get("path"))
        if not isinstance(raw_spec.get("sha256"), str) or sha256_file(raw_path) != raw_spec.get("sha256"):
            reasons.append("PIT_SOURCE_RAW_BARS_HASH")
        if not isinstance(split_spec.get("sha256"), str) or sha256_file(split_path) != split_spec.get("sha256"):
            reasons.append("PIT_SOURCE_SPLIT_BARS_HASH")
        if _bar_key_set(raw_path) != _bar_key_set(split_path):
            reasons.append("PIT_SOURCE_RAW_SPLIT_KEY_PARITY")
    except (OSError, UnicodeError, ValueError, csv.Error):
        reasons.append("PIT_SOURCE_RAW_SPLIT_UNREADABLE")
    return sorted(set(reasons))


def _validate_receipt_reference(
    root: Path,
    reference: Any,
    *,
    expected_path: Any,
    kind: str,
    manifest: Mapping[str, Any],
    package: Mapping[str, Any] | None = None,
) -> list[str]:
    if not isinstance(reference, Mapping) or reference.get("path") != expected_path:
        return [f"{kind}_ATTESTATION_CROSSLINK"]
    try:
        path = _safe_path(root, reference.get("path"))
    except ValueError:
        return [f"{kind}_ATTESTATION_MISSING"]
    declared = reference.get("sha256")
    if not isinstance(declared, str) or not HEX64_RE.fullmatch(declared) or sha256_file(path) != declared:
        return [f"{kind}_ATTESTATION_HASH"]
    try:
        receipt = _load_json(path)
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
        return [f"{kind}_ATTESTATION_UNREADABLE"]
    if kind == "PIT_SOURCE" and isinstance(package, Mapping):
        acquisition = package.get("acquisition")
        if isinstance(acquisition, Mapping) and reference.get("path") != acquisition.get("receipt_path"):
            return ["PIT_SOURCE_ATTESTATION_CROSSLINK"]
    if kind == "NATIVE_LEDGER":
        package_hash = (
            package.get("integrity", {}).get("manifest_sha256")
            if isinstance(package, Mapping) and isinstance(package.get("integrity"), Mapping)
            else None
        )
        reasons = _validate_native_attestation(receipt, manifest, package_sha256=package_hash, repo_root=root)
        if (
            reference.get("schema_version") != NATIVE_ATTESTATION_SCHEMA
            or reference.get("self_hash_field") != "attestation_sha256"
            or reference.get("self_hash") != receipt.get("attestation_sha256")
        ):
            reasons.append("NATIVE_ATTESTATION_REFERENCE_SCHEMA")
        if _artifact_contains_forbidden(path, allow_native_fills=True):
            reasons.append("NATIVE_ATTESTATION_OUTCOME_FIELDS")
        return sorted(set(reasons))

    if not isinstance(reference.get("schema_version"), str):
        return [f"{kind}_ATTESTATION_REFERENCE_SCHEMA"]
    if reference.get("schema_version") not in PIT_SOURCE_SCHEMAS:
        return [f"{kind}_ATTESTATION_SCHEMA"]
    if receipt.get("schema_version") != reference.get("schema_version") and receipt.get("schema") != reference.get(
        "schema_version"
    ):
        return [f"{kind}_ATTESTATION_SCHEMA"]
    if receipt.get("candidate_id") != "R46" or receipt.get("research_id") not in {None, manifest.get("research_id")}:
        return [f"{kind}_ATTESTATION_IDENTITY"]
    if reference.get("self_hash_field") != "receipt_sha256" or reference.get("self_hash") != receipt.get(
        "integrity", {}
    ).get("receipt_sha256"):
        return [f"{kind}_ATTESTATION_SELF_HASH"]
    if not _self_hash_valid(receipt, "receipt_sha256"):
        return [f"{kind}_ATTESTATION_SELF_HASH"]
    if receipt.get("historical_cutoff") != CUTOFF_UTC or receipt.get("status") not in {"COMPLETE", "VALIDATED"}:
        return [f"{kind}_ATTESTATION_NOT_READY"]
    if receipt.get("schema") == "r46-tlt-market-data-acquisition-v2" and isinstance(package, Mapping):
        reasons = _validate_acquisition_receipt(path, package, root)
        if reasons:
            return sorted(set(reasons))
    if _artifact_contains_forbidden(path):
        return [f"{kind}_ATTESTATION_OUTCOME_FIELDS"]
    return []


def _validate_data_package(
    path: Path, manifest: Mapping[str, Any], manifest_path: Path, root: Path
) -> tuple[list[str], int]:
    try:
        package = _load_json(path)
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
        return ["data_package_manifest_unreadable"], 0
    reasons: list[str] = []
    trusted_policy, trusted_policy_reasons = _trusted_public_evidence_policy(root)
    reasons.extend(trusted_policy_reasons)
    reasons.extend(
        _validate_publication_policy(
            package.get("publication_availability_policy"),
            root,
            trusted=trusted_policy,
            label="package",
        )
    )
    if package.get("publication_availability_policy") != manifest.get("publication_availability_policy"):
        reasons.append("PUBLICATION_POLICY_PACKAGE_GOVERNANCE_CROSSLINK")
    schema = package.get("schema") or package.get("schema_version")
    if schema != V2_DATA_PACKAGE_SCHEMA:
        reasons.append("data_package_schema")
    if package.get("status") != "DATA_PACKAGE_READY":
        reasons.append("data_package_status")
    for field, expected in {
        "economic_execution": False,
        "economic_run": False,
        "no_auto_promotion": True,
    }.items():
        if package.get(field) is not expected:
            reasons.append(f"data_package_boundary:{field}")
    if "research_only" in package and package.get("research_only") is not True:
        reasons.append("data_package_boundary:research_only")
    if not _self_hash_valid(package, "manifest_sha256"):
        reasons.append("data_package_manifest_hash")
    if _artifact_contains_forbidden(path):
        reasons.append("data_package_outcome_fields")
    try:
        from scripts.research.build_r46_v2_data_package_2026_09_10 import (
            _documents_for_validator,
            _eligibility_rows_for_validator,
            consume_r46_v2_data_package,
        )

        consume_r46_v2_data_package(path, repo_root=root)
    except (ImportError, OSError, UnicodeError, TypeError, ValueError, KeyError, csv.Error, json.JSONDecodeError):
        reasons.append("data_package_validator")
    declared_package_hash = (
        manifest.get("data_contract", {}).get("data_package_manifest_sha256")
        if isinstance(manifest.get("data_contract"), Mapping)
        else None
    )
    if declared_package_hash is not None and declared_package_hash != package.get("integrity", {}).get(
        "manifest_sha256"
    ):
        reasons.append("data_package_manifest_crosslink")
    try:
        expected_governance_path = _relative_to_root(root, manifest_path)
    except ValueError:
        expected_governance_path = ""
    if package.get("governance_manifest_path") != expected_governance_path or package.get(
        "governance_manifest_sha256"
    ) != manifest.get("integrity", {}).get("manifest_sha256"):
        reasons.append("governance_data_package_crosslink")
    if package.get("research_id") != manifest.get("research_id") or package.get("candidate_id") != "R46":
        reasons.append("data_package_identity")
    governance_contract = (
        manifest.get("data_contract", {}).get("incumbent_data_contract")
        if isinstance(manifest.get("data_contract"), Mapping)
        else None
    )
    if isinstance(governance_contract, Mapping):
        if package.get("incumbent_dependencies") != governance_contract:
            reasons.append("incumbent_data_package_crosslink")
    elif "incumbent_dependencies" in package:
        reasons.append("incumbent_data_package_unexpected")
    if package.get("historical_cutoff") != CUTOFF_UTC:
        reasons.append("historical_cutoff")
    artifacts = package.get("artifacts")
    artifact_paths: dict[str, Path] = {}
    if not isinstance(artifacts, Mapping) or set(artifacts) != REQUIRED_ARTIFACTS:
        reasons.append("artifact_declarations")
    else:
        for name in sorted(REQUIRED_ARTIFACTS):
            spec = artifacts.get(name)
            if (
                not isinstance(spec, Mapping)
                or not isinstance(spec.get("path"), str)
                or not isinstance(spec.get("sha256"), str)
                or not HEX64_RE.fullmatch(spec["sha256"])
            ):
                reasons.append(f"artifact_declaration:{name}")
                continue
            try:
                artifact_path = _safe_path(root, spec["path"])
            except ValueError:
                reasons.append(f"artifact_missing:{name}")
                continue
            artifact_paths[name] = artifact_path
            if sha256_file(artifact_path) != spec["sha256"]:
                reasons.append(f"artifact_hash:{name}")
            elif _artifact_contains_forbidden(artifact_path):
                reasons.append(f"artifact_outcome_fields:{name}")
    market_data = package.get("market_data")
    if not isinstance(market_data, Mapping):
        reasons.append("market_data_contract")
    else:
        for name in ("bars", "dividends", "calendar"):
            metadata = market_data.get(name)
            if (
                not isinstance(metadata, Mapping)
                or not isinstance(metadata.get("available_at"), str)
                or not metadata.get("available_at", "").strip()
                or not isinstance(metadata.get("source_vintage"), str)
                or metadata.get("source_vintage", "").strip().casefold()
                in {"current", "latest", "unknown", "unset", "none"}
            ):
                reasons.append(f"{name}_availability")

    validated: dict[str, Any] = {}
    if set(artifact_paths) == REQUIRED_ARTIFACTS and isinstance(market_data, Mapping):
        try:
            document_rows = _load_jsonl(artifact_paths["documents"])
            validated["documents"] = normalize_auction_documents(
                _documents_for_validator(
                    document_rows,
                    documents_path=artifact_paths["documents"],
                    root=root,
                ),
                repo_root=root,
            )
            validated["bars"] = validate_tlt_bars(
                _load_csv(artifact_paths["bars"]),
                available_at=market_data["bars"]["available_at"],
            )
            validated["dividends"] = validate_tlt_dividends(
                _load_csv(artifact_paths["dividends"]),
                available_at=market_data["dividends"]["available_at"],
            )
            eligibility_rows = _load_jsonl(artifact_paths["eligibility"])
            validated["eligibility"] = validate_tlt_eligibility(
                _eligibility_rows_for_validator(eligibility_rows)
            )
            validated["eligibility"]["records"] = eligibility_rows
            validated["calendar"] = validate_nyse_calendar(
                _load_jsonl(artifact_paths["calendar"]),
                available_at=market_data["calendar"]["available_at"],
            )
            cross_blockers = _cross_validate_tlt_data(
                bars=validated["bars"],
                dividends=validated["dividends"],
                eligibility=validated["eligibility"],
                calendar=validated["calendar"],
            )
            if cross_blockers:
                reasons.append("data_package_cross_validation")
            reasons.extend(
                _validate_normalized_policy_and_first_open(
                    validated["documents"],
                    validated["calendar"],
                    trusted=trusted_policy,
                )
            )
            coverage = package.get("coverage")
            if isinstance(coverage, Mapping) and "schedule_linkage" in coverage:
                reasons.extend(
                    _validate_declared_schedule_linkage(
                        coverage.get("schedule_linkage"),
                        validated["documents"],
                        validated["calendar"],
                    )
                )
        except (OSError, UnicodeError, TypeError, ValueError, KeyError, csv.Error, json.JSONDecodeError):
            reasons.append("data_package_validator")
    expected_count = package.get("expected_event_count")
    expected_ids = package.get("expected_event_ids")
    if (
        not isinstance(expected_count, int)
        or isinstance(expected_count, bool)
        or not isinstance(expected_ids, list)
        or expected_count != len(expected_ids)
        or any(not isinstance(item, str) or not item.strip() for item in expected_ids)
        or len(expected_ids) != len({item for item in expected_ids if isinstance(item, str)})
    ):
        reasons.append("auction_archive_completeness")
    elif isinstance(validated.get("documents"), Mapping):
        actual_ids = sorted(
            str(event.get("canonical_event_id"))
            for event in validated["documents"].get("events", [])
            if isinstance(event, Mapping) and isinstance(event.get("canonical_event_id"), str)
        )
        if expected_count != len(actual_ids) or sorted(expected_ids) != actual_ids:
            reasons.append("auction_archive_completeness")
    coverage = package.get("coverage")
    stats = manifest.get("statistics")
    if not isinstance(coverage, Mapping) or not isinstance(stats, Mapping):
        reasons.append("coverage_gates_missing")
    else:
        for key in ("event_episodes", "holding_intervals", "missing_rate", "fifth_open_exit"):
            if key not in coverage:
                reasons.append(f"coverage_field_missing:{key}")
        plan_path_value = stats.get("plan_path")
        try:
            plan = _load_json(_safe_path(root, plan_path_value))
            gates = plan.get("coverage_gates", {})
        except (
            AttributeError,
            KeyError,
            TypeError,
            ValueError,
            OSError,
            UnicodeError,
            csv.Error,
            json.JSONDecodeError,
        ):
            gates = {}
        if not isinstance(gates, Mapping):
            reasons.append("coverage_gates_missing")
        else:
            minimum_events_raw = gates.get("min_event_episodes")
            minimum_intervals_raw = gates.get("min_holding_intervals")
            maximum_missing_raw = gates.get("max_missing_rate")
            maximum_suppression_raw = gates.get("max_capacity_suppression_rate")
            minimum_events: int = (
                minimum_events_raw
                if isinstance(minimum_events_raw, int) and not isinstance(minimum_events_raw, bool)
                else 1
            )
            minimum_intervals: int = (
                minimum_intervals_raw
                if isinstance(minimum_intervals_raw, int) and not isinstance(minimum_intervals_raw, bool)
                else 1
            )
            maximum_missing: float = (
                float(maximum_missing_raw)
                if isinstance(maximum_missing_raw, (int, float)) and not isinstance(maximum_missing_raw, bool)
                else 0.0
            )
            maximum_suppression: float = (
                float(maximum_suppression_raw)
                if isinstance(maximum_suppression_raw, (int, float)) and not isinstance(maximum_suppression_raw, bool)
                else 0.0
            )
            event_episodes = coverage.get("event_episodes")
            holding_intervals = coverage.get("holding_intervals")
            fifth_open_exit = coverage.get("fifth_open_exit")
            missing_rate = coverage.get("missing_rate")
            capacity_suppression_rate = coverage.get("capacity_suppression_rate", 0)
            capacity = coverage.get("capacity")
            capacity_status = coverage.get("capacity_status")
            if (
                not isinstance(event_episodes, int)
                or isinstance(event_episodes, bool)
                or not isinstance(holding_intervals, int)
                or isinstance(holding_intervals, bool)
            ):
                reasons.append("coverage_counts_invalid")
            elif event_episodes < minimum_events or holding_intervals < minimum_intervals:
                reasons.append("BLOCKED_INSUFFICIENT_EVENT_RATE")
            elif (
                isinstance(expected_count, int)
                and not isinstance(expected_count, bool)
                and (event_episodes != expected_count or holding_intervals != expected_count * 5)
            ):
                reasons.append("coverage_counts_disagree")
            if not isinstance(fifth_open_exit, int) or isinstance(fifth_open_exit, bool) or fifth_open_exit < 0:
                reasons.append("coverage_fifth_open_exit")
            elif (
                isinstance(expected_count, int)
                and not isinstance(expected_count, bool)
                and fifth_open_exit != expected_count
            ):
                reasons.append("coverage_fifth_open_exit")
            if (
                not isinstance(missing_rate, (int, float))
                or isinstance(missing_rate, bool)
                or not 0 <= missing_rate <= 1
            ):
                reasons.append("coverage_missing_rate")
            elif missing_rate > maximum_missing:
                reasons.append("coverage_missing_rate")
            if (
                not isinstance(capacity_suppression_rate, (int, float))
                or isinstance(capacity_suppression_rate, bool)
                or not 0 <= capacity_suppression_rate <= 1
            ):
                reasons.append("coverage_capacity_suppression_rate")
            elif capacity_suppression_rate > maximum_suppression:
                reasons.append("coverage_capacity_suppression_rate")
            if capacity is None:
                if capacity_status != "PRE_RUN_CAPACITY_PENDING":
                    reasons.append("coverage_capacity_status")
            elif not isinstance(capacity, (int, float)) or isinstance(capacity, bool) or capacity < 0:
                reasons.append("coverage_capacity_status")
    acquisition = package.get("acquisition")
    if not isinstance(acquisition, Mapping):
        reasons.append("PIT_SOURCE_ACQUISITION_REQUIRED")
    else:
        receipt_value = acquisition.get("receipt_path")
        try:
            receipt_path = _resolve_nested_path(root, path, receipt_value)
            reasons.extend(_validate_acquisition_receipt(receipt_path, package, root))
        except (OSError, UnicodeError, ValueError, csv.Error):
            reasons.append("PIT_SOURCE_ACQUISITION_UNREADABLE")
    if any(
        key in package
        for key in (
            "holdout_attestation",
            "pit_source_attestation",
            "native_ledger_attestation",
            "native_ledger_receipt",
            "pit_source_receipt",
        )
    ):
        reasons.append("embedded_attestation_forbidden")
    return sorted(set(reasons)), 5 if not reasons else 0


def _manifest_blockers(
    manifest: Mapping[str, Any],
    manifest_path: Path,
    root: Path,
    *,
    freeze_commit: Any = None,
    preflight_receipt_path: Path | None = None,
) -> list[str]:
    reasons: list[str] = []
    if _artifact_contains_forbidden(manifest_path):
        reasons.append("GOVERNANCE_OUTCOME_FIELDS")
    if manifest.get("schema_version") != V2_SCHEMA_VERSION:
        reasons.append("manifest_schema")
    if manifest.get("research_id") != V2_RESEARCH_ID or manifest.get("candidate_id") != "R46":
        reasons.append("manifest_identity")
    if manifest.get("status") != "RETROSPECTIVE_ONE_SHOT_DISCLOSED":
        reasons.append("manifest_status")
    trusted_policy, trusted_policy_reasons = _trusted_public_evidence_policy(root)
    reasons.extend(trusted_policy_reasons)
    reasons.extend(
        _validate_publication_policy(
            manifest.get("publication_availability_policy"),
            root,
            trusted=trusted_policy,
            label="governance",
        )
    )
    reasons.extend(_validate_v1_contract_preservation(manifest, root))
    for field, expected in {
        "declared_before_outcomes": True,
        "economic_execution": False,
        "economic_run": False,
        "economic_outcomes_accessed": False,
        "outcome_artifacts_present": False,
        "research_only": True,
        "promotion": False,
        "integration_candidate": False,
        "paper_live_untouched": True,
        "paper_or_live_activation": False,
        "auto_promotion": False,
        "no_auto_promotion": True,
    }.items():
        if manifest.get(field) is not expected:
            reasons.append(f"manifest_boundary:{field}")
    contamination = manifest.get("contamination")
    if (
        not isinstance(contamination, Mapping)
        or contamination.get("acknowledged") is not True
        or contamination.get("confirmatory_claim_forbidden") is not True
    ):
        reasons.append("CONTAMINATION_DISCLOSURE_REQUIRED")
    evaluation = manifest.get("evaluation_contract")
    if (
        not isinstance(evaluation, Mapping)
        or evaluation.get("mode") != "retrospective_one_shot"
        or evaluation.get("tuning_forbidden") is not True
        or evaluation.get("rerun_forbidden") is not True
    ):
        reasons.append("ONE_SHOT_POLICY_INVALID")
    if not _self_hash_valid(manifest, "manifest_sha256"):
        reasons.append("manifest_hash_invalid")
    integrity = manifest.get("integrity")
    if not isinstance(integrity, Mapping):
        reasons.append("MANIFEST_INTEGRITY_REQUIRED")
    else:
        schema_value = integrity.get("schema_path")
        schema_hash = integrity.get("schema_sha256")
        try:
            schema_path = _safe_path(root, schema_value)
            schema_payload = _load_json(schema_path)
            if (
                not isinstance(schema_hash, str)
                or not HEX64_RE.fullmatch(schema_hash)
                or sha256_file(schema_path) != schema_hash
            ):
                reasons.append("MANIFEST_SCHEMA_HASH_INVALID")
            if (
                schema_payload.get("$schema") != "https://json-schema.org/draft/2020-12/schema"
                or schema_payload.get("type") != "object"
            ):
                reasons.append("MANIFEST_SCHEMA_INVALID")
        except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
            reasons.append("MANIFEST_SCHEMA_UNREADABLE")
    if _contains_non_null_key(
        manifest,
        {
            "data_package_manifest_sha256",
            "pit_source_attestation_sha256",
            "native_ledger_attestation_sha256",
            "independent_software_review_sha256",
            "review_sha256",
        },
    ):
        reasons.append("POST_SEAL_HASH_IN_STATIC_GOVERNANCE")
    evidence = manifest.get("evidence_contract")
    if (
        not isinstance(evidence, Mapping)
        or evidence.get("governance_contract") != "immutable_static_manifest_sha256"
        or evidence.get("post_seal_hashes_excluded") is not True
        or not isinstance(evidence.get("package_path"), str)
        or not isinstance(evidence.get("native_attestation_path"), str)
        or not isinstance(evidence.get("independent_software_review_path"), str)
    ):
        reasons.append("EVIDENCE_CONTRACT_INVALID")
    data_contract = manifest.get("data_contract")
    if isinstance(evidence, Mapping) and isinstance(data_contract, Mapping):
        if evidence.get("package_path") != data_contract.get("data_package_manifest_path"):
            reasons.append("EVIDENCE_PACKAGE_PATH_CROSSLINK")
    source_review = (
        manifest.get("source_provenance", {}).get("independent_software_review")
        if isinstance(manifest.get("source_provenance"), Mapping)
        else None
    )
    if (
        isinstance(evidence, Mapping)
        and isinstance(source_review, Mapping)
        and evidence.get("independent_software_review_path") != source_review.get("path")
    ):
        reasons.append("EVIDENCE_REVIEW_PATH_CROSSLINK")
    operator_authorization = manifest.get("operator_authorization")
    if (
        not isinstance(operator_authorization, Mapping)
        or operator_authorization.get("kind") != "user_scope_authorization"
        or operator_authorization.get("authorized") is not True
        or operator_authorization.get("cryptographic") is not False
        or operator_authorization.get("confirmatory_claim") is not False
        or not isinstance(operator_authorization.get("scope"), str)
        or not operator_authorization["scope"].strip()
    ):
        reasons.append("OPERATOR_SCOPE_AUTHORIZATION_INVALID")

    reasons.extend(_validate_incumbent_data_contract(manifest, root))

    try:
        parent_path = _safe_path(root, manifest.get("parent_manifest_path"))
        parent = _load_json(parent_path)
    except (ValueError, OSError, UnicodeError, json.JSONDecodeError):
        parent = None
        reasons.append("PARENT_MANIFEST_UNREADABLE")
    if isinstance(parent, Mapping):
        parent_hash = canonical_hash(parent, self_field="manifest_sha256")
        if (
            parent_hash != EXPECTED_V1_MANIFEST_SHA256
            or manifest.get("parent_manifest_sha256") != parent_hash
            or parent.get("integrity", {}).get("manifest_sha256") != parent_hash
        ):
            reasons.append("PARENT_MANIFEST_HASH_INVALID")
        for key in (
            "candidate",
            "event_contract",
            "instrument_contract",
            "cost_contract",
            "controls",
            "execution_contract",
        ):
            if manifest.get(key) != parent.get(key):
                reasons.append(f"V2_PROTOCOL_DRIFT:{key}")
        if parent.get("schema_version") != "r46-governance-manifest-v1" or parent.get("candidate_id") != "R46":
            reasons.append("PARENT_MANIFEST_IDENTITY")

    holdout = manifest.get("holdout")
    if (
        not isinstance(holdout, Mapping)
        or holdout.get("status") != "RETROSPECTIVE_ONE_SHOT_DISCLOSED"
        or holdout.get("historical_holdout_accepted") is not False
        or holdout.get("prior_access_acknowledged") is not True
    ):
        reasons.append("RETROSPECTIVE_DISCLOSURE_INVALID")

    stats = manifest.get("statistics")
    if not isinstance(stats, Mapping) or stats.get("status") != "READY_STATISTICS":
        reasons.append("BLOCKED_STATISTICS")
    else:
        try:
            stats_path = _safe_path(root, stats.get("plan_path"))
            if _artifact_contains_forbidden(stats_path):
                reasons.append("STATISTICS_OUTCOME_FIELDS")
            reasons.extend(_validate_statistics(stats_path, manifest, root))
        except ValueError:
            reasons.append("STATISTICS_PLAN_MISSING")
    multiplicity = manifest.get("multiplicity")
    if not isinstance(multiplicity, Mapping) or multiplicity.get("status") != "READY_MULTIPLICITY":
        reasons.append("BLOCKED_STATISTICS")
    else:
        try:
            family_path = _safe_path(root, multiplicity.get("family_registry_path"))
            if _artifact_contains_forbidden(family_path):
                reasons.append("FAMILY_REGISTRY_OUTCOME_FIELDS")
            reasons.extend(_validate_family(family_path, manifest))
        except ValueError:
            reasons.append("FAMILY_REGISTRY_MISSING")

    source_provenance = manifest.get("source_provenance")
    data_acquisition = source_provenance.get("data_acquisition") if isinstance(source_provenance, Mapping) else None
    if not isinstance(data_acquisition, Mapping) or data_acquisition.get("path") != XML_V2_ACQUIRER_RELATIVE:
        reasons.append("SOURCE_ROLE_DATA_ACQUISITION_XML_V2_REQUIRED")
    reasons.extend(
        _validate_source_provenance(
            source_provenance,
            root,
            manifest_sha256=manifest.get("integrity", {}).get("manifest_sha256"),
            research_id=manifest.get("research_id"),
            freeze_commit=freeze_commit,
            post_freeze_paths=(
                (_relative_to_root(root, preflight_receipt_path),)
                if (
                    preflight_receipt_path is not None
                    and preflight_receipt_path.exists()
                    and root.resolve() in preflight_receipt_path.resolve().parents
                )
                else ()
            ),
        )
    )
    try:
        one_shot_path = _safe_path(
            root, evaluation.get("one_shot_state_path") if isinstance(evaluation, Mapping) else None
        )
        reasons.extend(_validate_one_shot(one_shot_path, manifest, root))
    except ValueError:
        reasons.append("ONE_SHOT_STATE_MISSING")

    return sorted(set(reasons))


def _evidence_reference(root: Path, path: Path, *, self_field: str) -> dict[str, Any]:
    payload = _load_json(path)
    integrity = payload.get("integrity")
    declared = integrity.get(self_field) if isinstance(integrity, Mapping) else payload.get(self_field)
    schema = payload.get("schema_version") or payload.get("schema")
    return {
        "path": _relative_to_root(root, path),
        "sha256": sha256_file(path),
        "schema_version": schema,
        "self_hash_field": self_field,
        "self_hash": declared,
    }


def _finish_result(result: VerifiedPreflightResult) -> VerifiedPreflightResult:
    result["preflight_receipt"] = build_preflight_receipt(result)
    if result.get("status") == "PRE_RUN_OK":
        result.proof = _PREFLIGHT_PROOF
        result.payload_digest = _result_digest(result)
    return result


def verify_v2_manifest(
    manifest_path: Path,
    *,
    data_package_manifest_path: Path | None = None,
    pit_source_attestation_path: Path | None = None,
    native_ledger_attestation_path: Path | None = None,
    independent_software_review_path: Path | None = None,
    freeze_commit: str | None = None,
    repo_root: Path | None = None,
    preflight_receipt_path: Path | None = None,
) -> VerifiedPreflightResult:
    """Return a machine-readable v2 gate without emitting economic values."""

    root = (repo_root or REPO).resolve()
    try:
        resolved_manifest = _resolve_existing_argument(root, manifest_path)
        manifest = _load_json(resolved_manifest)
    except (ValueError, OSError, UnicodeError, json.JSONDecodeError):
        return _finish_result(
            VerifiedPreflightResult({**_blocked("manifest_unreadable"), "research_id": V2_RESEARCH_ID})
        )

    reasons = _manifest_blockers(
        manifest,
        resolved_manifest,
        root,
        freeze_commit=freeze_commit,
        preflight_receipt_path=preflight_receipt_path,
    )
    evidence_contract: Mapping[str, Any] = (
        cast(Mapping[str, Any], manifest.get("evidence_contract"))
        if isinstance(manifest.get("evidence_contract"), Mapping)
        else {}
    )
    source: Mapping[str, Any] = (
        cast(Mapping[str, Any], manifest.get("source_provenance"))
        if isinstance(manifest.get("source_provenance"), Mapping)
        else {}
    )
    review_spec: Mapping[str, Any] = (
        cast(Mapping[str, Any], source.get("independent_software_review"))
        if isinstance(source.get("independent_software_review"), Mapping)
        else {}
    )
    package_candidate = data_package_manifest_path
    if package_candidate is None:
        declared = (
            manifest.get("data_contract", {}).get("data_package_manifest_path")
            if isinstance(manifest.get("data_contract"), Mapping)
            else None
        )
        if not isinstance(declared, str) and isinstance(evidence_contract.get("package_path"), str):
            declared = evidence_contract["package_path"]
        package_candidate = (root / declared) if isinstance(declared, str) else None
    package_path: Path | None = None
    package_payload: Mapping[str, Any] | None = None
    checked = 0
    if package_candidate is None:
        reasons.append("data_package_manifest_required")
    else:
        try:
            package_path = _resolve_existing_argument(root, package_candidate)
            package_reasons, checked = _validate_data_package(package_path, manifest, resolved_manifest, root)
            reasons.extend(package_reasons)
            package_payload = _load_json(package_path)
        except (
            AttributeError,
            KeyError,
            TypeError,
            ValueError,
            OSError,
            UnicodeError,
            csv.Error,
            json.JSONDecodeError,
        ):
            reasons.append("data_package_manifest_unreadable")

    def resolve_evidence(explicit: Path | None, *fallbacks: Any, kind: str) -> Path | None:
        declared_values = [candidate for candidate in fallbacks if isinstance(candidate, str) and candidate.strip()]
        declared_path: Path | None = None
        if declared_values:
            try:
                declared_path = _safe_path(root, declared_values[0], must_exist=False)
            except ValueError:
                declared_path = None
            for other in declared_values[1:]:
                try:
                    other_path = _safe_path(root, other, must_exist=False)
                except ValueError:
                    continue
                if declared_path is not None and other_path != declared_path:
                    reasons.append(f"{kind}_MANIFEST_PATH_CROSSLINK")
        value: Any = explicit
        if value is None:
            value = declared_values[0] if declared_values else None
        if value is None:
            return None
        try:
            resolved = _resolve_existing_argument(root, Path(value))
        except (ValueError, OSError):
            return None
        if explicit is not None and declared_path is not None and resolved != declared_path:
            reasons.append(f"{kind}_EXPLICIT_PATH_MISMATCH")
        return resolved

    review_path = resolve_evidence(
        independent_software_review_path,
        evidence_contract.get("independent_software_review_path"),
        review_spec.get("path"),
        kind="INDEPENDENT_SOFTWARE_REVIEW",
    )
    review_payload: Mapping[str, Any] = {}
    native_payload: Mapping[str, Any] = {}
    pit_payload: Mapping[str, Any] = {}
    native_path = resolve_evidence(
        native_ledger_attestation_path,
        evidence_contract.get("native_attestation_path"),
        manifest.get("execution_contract", {}).get("native_ledger_attestation_path")
        if isinstance(manifest.get("execution_contract"), Mapping)
        else None,
        kind="NATIVE_LEDGER",
    )
    pit_path = resolve_evidence(
        pit_source_attestation_path,
        evidence_contract.get("pit_source_attestation_path"),
        manifest.get("data_contract", {}).get("pit_source_attestation_path")
        if isinstance(manifest.get("data_contract"), Mapping)
        else None,
        kind="PIT_SOURCE",
    )
    if pit_path is None and isinstance(package_payload, Mapping):
        acquisition = package_payload.get("acquisition")
        if isinstance(acquisition, Mapping):
            pit_path = resolve_evidence(None, acquisition.get("receipt_path"), kind="PIT_SOURCE")
    one_shot_path: Path | None = None
    evaluation = manifest.get("evaluation_contract") if isinstance(manifest.get("evaluation_contract"), Mapping) else {}
    if isinstance(evaluation, Mapping) and isinstance(evaluation.get("one_shot_state_path"), str):
        try:
            one_shot_path = _resolve_existing_argument(root, Path(evaluation["one_shot_state_path"]))
        except (ValueError, OSError):
            one_shot_path = None
    if review_path is None:
        reasons.append("INDEPENDENT_SOFTWARE_REVIEW_REQUIRED")
    else:
        review_reasons, review_payload = _validate_independent_review(review_path, manifest, source, root=root)
        reasons.extend(review_reasons)
    if native_path is None:
        reasons.append("NATIVE_LEDGER_ATTESTATION_REQUIRED")
    elif package_payload is None:
        reasons.append("NATIVE_LEDGER_PACKAGE_REQUIRED")
    else:
        try:
            native_payload = _load_json(native_path)
            native_reference = _evidence_reference(root, native_path, self_field="attestation_sha256")
            reasons.extend(
                _validate_receipt_reference(
                    root,
                    native_reference,
                    expected_path=(
                        evidence_contract.get("native_attestation_path")
                        or (
                            manifest.get("execution_contract", {}).get("native_ledger_attestation_path")
                            if isinstance(manifest.get("execution_contract"), Mapping)
                            else None
                        )
                    ),
                    kind="NATIVE_LEDGER",
                    manifest=manifest,
                    package=package_payload,
                )
            )
        except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
            reasons.append("NATIVE_LEDGER_ATTESTATION_UNREADABLE")
    if pit_path is not None:
        try:
            pit_payload = _load_json(pit_path)
            pit_reference = _evidence_reference(root, pit_path, self_field="receipt_sha256")
            reasons.extend(
                _validate_receipt_reference(
                    root,
                    pit_reference,
                    expected_path=(
                        evidence_contract.get("pit_source_attestation_path")
                        or (
                            manifest.get("data_contract", {}).get("pit_source_attestation_path")
                            if isinstance(manifest.get("data_contract"), Mapping)
                            else None
                        )
                        or (
                            package_payload.get("acquisition", {}).get("receipt_path")
                            if isinstance(package_payload, Mapping)
                            and isinstance(package_payload.get("acquisition"), Mapping)
                            else None
                        )
                    ),
                    kind="PIT_SOURCE",
                    manifest=manifest,
                    package=package_payload,
                )
            )
        except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
            reasons.append("PIT_SOURCE_ATTESTATION_UNREADABLE")

    manifest_hash = (
        manifest.get("integrity", {}).get("manifest_sha256") if isinstance(manifest.get("integrity"), Mapping) else None
    )
    result_data: dict[str, Any] = {
        **_blocked(*reasons, checked_artifacts=checked),
        "research_id": manifest.get("research_id", V2_RESEARCH_ID),
        "governance_manifest_path": _relative_to_root(root, resolved_manifest),
        "governance_manifest_sha256": manifest_hash,
        "governance_manifest_file_sha256": sha256_file(resolved_manifest),
        "data_package_manifest_path": _relative_to_root(root, package_path) if package_path is not None else None,
        "data_package_manifest_sha256": package_payload.get("integrity", {}).get("manifest_sha256")
        if isinstance(package_payload, Mapping) and isinstance(package_payload.get("integrity"), Mapping)
        else None,
        "data_package_manifest_file_sha256": sha256_file(package_path) if package_path is not None else None,
        "independent_software_review_path": _relative_to_root(root, review_path) if review_path is not None else None,
        "independent_software_review_sha256": review_payload.get("integrity", {}).get("review_sha256")
        if isinstance(review_payload, Mapping) and isinstance(review_payload.get("integrity"), Mapping)
        else None,
        "independent_software_review_file_sha256": sha256_file(review_path) if review_path is not None else None,
        "native_attestation_path": _relative_to_root(root, native_path) if native_path is not None else None,
        "native_attestation_sha256": native_payload.get("attestation_sha256") if native_path is not None else None,
        "native_attestation_file_sha256": sha256_file(native_path) if native_path is not None else None,
        "pit_source_attestation_path": _relative_to_root(root, pit_path) if pit_path is not None else None,
        "pit_source_attestation_sha256": pit_payload.get("integrity", {}).get("receipt_sha256")
        if pit_path is not None
        else None,
        "pit_source_attestation_file_sha256": sha256_file(pit_path) if pit_path is not None else None,
        "source_commit": source.get("source_commit"),
        "freeze_commit": freeze_commit or _git_head(root),
        "one_shot_state_path": _relative_to_root(root, one_shot_path) if one_shot_path is not None else None,
        "one_shot_state_sha256": evaluation.get("one_shot_state_sha256") if isinstance(evaluation, Mapping) else None,
        "one_shot_state_file_sha256": sha256_file(one_shot_path) if one_shot_path is not None else None,
        "checks": {
            "governance_static_contract": not any(
                reason.startswith("manifest") or reason.startswith("V2_PROTOCOL") for reason in reasons
            ),
            "data_package_exact_five": checked == 5,
            "external_review": review_path is not None
            and not any(reason.startswith("INDEPENDENT_SOFTWARE_REVIEW") for reason in reasons),
            "native_attestation": native_path is not None
            and not any(reason.startswith("NATIVE_LEDGER") for reason in reasons),
            "one_shot_sealed_unused": not any(reason.startswith("ONE_SHOT") for reason in reasons),
        },
    }
    return _finish_result(VerifiedPreflightResult(result_data))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("governance_manifest", type=Path)
    parser.add_argument("--data-package-manifest", type=Path)
    parser.add_argument("--pit-source-attestation", type=Path)
    parser.add_argument("--native-ledger-attestation", type=Path)
    parser.add_argument("--independent-software-review", type=Path)
    parser.add_argument("--freeze-commit")
    parser.add_argument("--repo-root", type=Path)
    parser.add_argument("--preflight-receipt", type=Path)
    args = parser.parse_args(argv)
    result = verify_v2_manifest(
        args.governance_manifest,
        data_package_manifest_path=args.data_package_manifest,
        pit_source_attestation_path=args.pit_source_attestation,
        native_ledger_attestation_path=args.native_ledger_attestation,
        independent_software_review_path=args.independent_software_review,
        freeze_commit=args.freeze_commit,
        repo_root=args.repo_root,
    )
    if args.preflight_receipt is not None:
        receipt_path = (
            args.preflight_receipt
            if args.preflight_receipt.is_absolute()
            else (args.repo_root or REPO) / args.preflight_receipt
        )
        try:
            persist_preflight_receipt(result, receipt_path)
        except (OSError, ValueError) as exc:
            blockers = list(result.get("blockers", []))
            blockers.append(f"PREFLIGHT_RECEIPT_PERSIST_FAILED:{exc}")
            result["status"] = "BLOCKED"
            result["blockers"] = sorted(set(blockers))
            result = VerifiedPreflightResult(dict(result))
            result["preflight_receipt"] = build_preflight_receipt(result)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
