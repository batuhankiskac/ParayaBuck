"""Build the immutable, outcome-clean R46 v2 governance bundle.

The v2 bundle is a child of the frozen v1 contract.  It explicitly discloses
that the historical sample was previously accessed, so a later evaluation is
retrospective and descriptive rather than confirmatory.  This builder only
writes governance artifacts; it never reads market data, runs a strategy, or
creates human/statistician signatures.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import platform
import re
import subprocess
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
V1_MANIFEST_RELATIVE = Path("data/research_manifests/r46_treasury_auction_unwind_tlt_v1_2026_09_10.json")
V1_SCHEMA_RELATIVE = Path("data/research_manifests/r46_treasury_auction_unwind_tlt_v1_2026_09_10.schema.json")
V1_SOURCE_MATRIX_RELATIVE = Path("data/research_manifests/r46_treasury_auction_unwind_tlt_v1_2026_09_10_source_matrix.json")
V1_HOLDOUT_CONTRACT_RELATIVE = Path(
    "data/research_manifests/r46_treasury_auction_unwind_tlt_v1_2026_09_10_holdout_custody_contract.json"
)
PUBLIC_EVIDENCE_RECEIPT_RELATIVE = Path(
    "data/research_receipts/r46_treasury_public_source_evidence_2026_09_10_v1.json"
)
PUBLICATION_AVAILABILITY_EVIDENCE_RELATIVE = Path(
    "data/research_receipts/r46_treasury_publication_availability_evidence_2026_09_11_inventory_raw_hash_v3.json"
)
PUBLICATION_AVAILABILITY_EVIDENCE_SCHEMA = "r46-treasury-publication-availability-evidence-v2"
PUBLICATION_AVAILABILITY_EVIDENCE_BUILDER_RELATIVE = Path(
    "scripts/research/build_r46_publication_availability_evidence_2026_09_11.py"
)
FINAL_TREASURY_RUN_RELATIVE = Path(
    "data/research/runs/r46_treasury_xml_v2_2026_09_11_inventory_raw_hash_2026_09_11"
)
FINAL_TREASURY_RECEIPT_RELATIVE = FINAL_TREASURY_RUN_RELATIVE / "receipt.json"
FINAL_TREASURY_DOCUMENTS_RELATIVE = FINAL_TREASURY_RUN_RELATIVE / "documents.jsonl"
V2_MANIFEST_NAME = "r46_treasury_auction_unwind_tlt_v2_2026_09_10.json"
V2_SCHEMA_NAME = "r46_treasury_auction_unwind_tlt_v2_2026_09_10.schema.json"
V2_STATISTICS_NAME = "r46_treasury_auction_unwind_tlt_v2_2026_09_10_statistics_plan.json"
V2_FAMILY_NAME = "r46_treasury_auction_unwind_tlt_v2_2026_09_10_family_registry.json"
V2_ONE_SHOT_NAME = "r46_treasury_auction_unwind_tlt_v2_2026_09_10_one_shot_state.json"
V2_DATA_PACKAGE_NAME = "r46_treasury_auction_unwind_tlt_v2_2026_09_10_data_package.json"
V2_NATIVE_ATTESTATION_NAME = "r46_treasury_auction_unwind_tlt_v2_2026_09_10_native_attestation.json"
V2_SCHEMA_VERSION = "r46-governance-manifest-v2"
V2_STATISTICS_SCHEMA = "r46-statistics-plan-v2"
V2_FAMILY_SCHEMA = "r46-multiplicity-family-v2"
V2_ONE_SHOT_SCHEMA = "r46-one-shot-state-v2"
V2_DATA_PACKAGE_SCHEMA = "r46-treasury-auction-unwind-data-package-v2"
V2_RESEARCH_ID = "r46_treasury_auction_unwind_tlt_v2_2026_09_10"
V1_RESEARCH_ID = "r46_treasury_auction_unwind_tlt_v1_2026_09_10"
CANDIDATE_ID = "R46"
R46_HISTORICAL_CUTOFF = "2026-07-31T23:59:59Z"
V5_INCUMBENT_PATHS = {
    "bars": "data/historical/equity_pit_symmetric_2016_2026_v5.csv",
    "lifetimes": "data/historical/equity_entity_lifetimes_full_v5.csv",
    "corporate_actions": "data/historical/equity_survivorship_full_corporate_actions_v5.csv",
    "bars_manifest": "data/historical/equity_pit_symmetric_2016_2026_v5.manifest.json",
    "corporate_actions_manifest": "data/historical/equity_survivorship_full_corporate_actions_v5.manifest.json",
    "seam": "scripts/run_symmetric_v5_validation.py",
}
V5_EXTERNAL_CONTENT_KEYS = frozenset({"bars", "corporate_actions", "bars_manifest", "corporate_actions_manifest"})
V5_TRACKED_CONTENT_KEYS = frozenset({"lifetimes", "seam"})
V5_BASE_REQUIRED_CONTENT_KEYS = frozenset({"lifetimes"})
EXPECTED_V1_MANIFEST_SHA256 = "f0897f8aaeb56ca285b9ac626b95fb3131105b8653ea137b9d8ad621a44ec509"
RESULT_AVAILABILITY_POLICY = (
    "official_XML_AuctionDate_plus_one_civil_day_midnight_America_New_York_for_all_results"
)
RESULT_SIGNAL_ELIGIBILITY = "FIRST_REGULAR_OPEN_STRICTLY_AFTER_AVAILABLE_AT"
ACTUAL_RELEASE_TIME_ROLE = "evidence_only"
EXPECTED_EVENT_COUNT = 127
EXPECTED_MISSING_RELEASE_COUNT = 15
EXPECTED_EVENT_IDS_DIGEST_FIELD = "expected_event_ids_sha256"
EVENT_IDS_CANONICALIZATION = "sorted unique event IDs encoded as compact UTF-8 JSON array"
# These pins are the tracked public-receipt freeze inputs.  The builder still
# recomputes them from the receipt; the constants make the generated schema
# reject a self-consistent but different universe.
EXPECTED_EVENT_IDS_SHA256 = "f726f27aaece6532de1581b5830e38cceba67c75ae02dfa79fedaffc0e9d4b0b"
PUBLIC_EVIDENCE_RECEIPT_RAW_SHA256 = "c24cffa145391d139c442e64237aae7225d14cf76ce7f0ce2ade095ba3eec960"
PUBLICATION_AVAILABILITY_EVIDENCE_RAW_SHA256 = "f88efc293d5645f8e32514e80383f4b5f64a4bca068c7bb85382bdc004b318ef"
PUBLICATION_AVAILABILITY_EVIDENCE_BYTE_SIZE = 6374
FINAL_TREASURY_RECEIPT_RAW_SHA256 = "2b5b3b4972958295931debb71b5e580cec696ccfd2b6ba9e55103ddc52b3c684"
FINAL_TREASURY_RECEIPT_BYTE_SIZE = 257364
FINAL_TREASURY_DOCUMENTS_RAW_SHA256 = "f2ae73fe5dd443c3d2de45059cd8b7738881faf49e23cecaae7ced7e2b251576"
FINAL_TREASURY_DOCUMENTS_BYTE_SIZE = 423675
PLACEHOLDER = "UNSET_UNTIL_FINAL_CLEAN_COMMIT"
CANONICALIZATION = (
    "UTF-8 JSON, sorted object keys, compact separators, ensure_ascii=false; omit the nested self-hash before hashing"
)
EXECUTION_COMMAND = (
    "python scripts/research/r46_treasury_auction_unwind_tlt_v2_2026_09_10.py "
    "--gate <preflight_receipt> --package <data_package_manifest> "
    "--one-shot-state <one_shot_state> --output-dir <output_dir>"
)
HEX40_RE = re.compile(r"^[0-9a-f]{40}$")


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
    """Hash a JSON object after removing exactly its nested self-hash field."""

    value = copy.deepcopy(dict(payload))
    integrity = value.get("integrity")
    if not isinstance(integrity, dict):
        raise ValueError("integrity_object_required")
    integrity.pop(self_field, None)
    return sha256_bytes(_canonical_bytes(value))


def canonical_event_ids_digest(event_ids: list[str]) -> str:
    """Hash the canonical sorted unique event-ID universe independently of a caller."""

    canonical = sorted(set(event_ids))
    encoded = json.dumps(canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return sha256_bytes(encoded)


def _load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"json_object_required:{path}")
    return payload


def _public_receipt_event_id(event: Mapping[str, Any]) -> str:
    explicit = event.get("canonical_event_id")
    if isinstance(explicit, str) and explicit.strip():
        return explicit.strip()
    auction_date = event.get("auction_date", event.get("auctionDate"))
    cusip = event.get("cusip", event.get("security_identifier"))
    if not isinstance(auction_date, str) or not auction_date.strip() or not isinstance(cusip, str) or not cusip.strip():
        raise ValueError("public_evidence_event_identity_missing")
    return f"{auction_date.strip()}:{cusip.strip()}"


def _validate_publication_availability_evidence(
    repo_root: Path, public_event_ids: list[str]
) -> dict[str, Any]:
    """Validate the immutable final-run publication evidence input."""

    evidence_path = repo_root / PUBLICATION_AVAILABILITY_EVIDENCE_RELATIVE
    evidence = _load_json(evidence_path)
    if evidence.get("schema") != PUBLICATION_AVAILABILITY_EVIDENCE_SCHEMA:
        raise ValueError("publication_availability_evidence_schema_invalid")
    if evidence.get("status") != "COMPLETE":
        raise ValueError("publication_availability_evidence_incomplete")
    if evidence.get("research_id") != V2_RESEARCH_ID or evidence.get("candidate_id") != CANDIDATE_ID:
        raise ValueError("publication_availability_evidence_identity_invalid")
    if evidence.get("result_availability_policy") != RESULT_AVAILABILITY_POLICY:
        raise ValueError("publication_availability_evidence_policy_invalid")
    if evidence.get("result_signal_eligibility") != RESULT_SIGNAL_ELIGIBILITY:
        raise ValueError("publication_availability_evidence_eligibility_invalid")
    if evidence.get("same_day_signal_allowed") is not False:
        raise ValueError("publication_availability_evidence_same_day_invalid")
    if evidence.get("actual_release_time_role") != ACTUAL_RELEASE_TIME_ROLE:
        raise ValueError("publication_availability_evidence_release_role_invalid")
    if evidence.get("server_last_modified_is_publication_proof") is not False:
        raise ValueError("publication_availability_evidence_last_modified_invalid")
    if evidence.get("economic_execution") is not False or evidence.get("economic_outcomes_accessed") is not False:
        raise ValueError("publication_availability_evidence_economic_boundary_invalid")
    if sha256_file(evidence_path) != PUBLICATION_AVAILABILITY_EVIDENCE_RAW_SHA256:
        raise ValueError("publication_availability_evidence_bytes_changed")
    integrity = evidence.get("integrity")
    if not isinstance(integrity, Mapping) or integrity.get("evidence_sha256") != canonical_hash(
        evidence, self_field="evidence_sha256"
    ):
        raise ValueError("publication_availability_evidence_self_hash_invalid")

    event_ids = evidence.get("expected_event_ids")
    if (
        not isinstance(event_ids, list)
        or any(not isinstance(value, str) or not value.strip() for value in event_ids)
        or event_ids != sorted(set(event_ids))
        or len(event_ids) != EXPECTED_EVENT_COUNT
        or event_ids != public_event_ids
    ):
        raise ValueError("publication_availability_evidence_event_ids_invalid")
    event_digest = canonical_event_ids_digest([str(value) for value in event_ids])
    if (
        evidence.get("expected_event_count") != EXPECTED_EVENT_COUNT
        or evidence.get("expected_event_ids_sha256") != event_digest
        or evidence.get("expected_event_ids_digest") != event_digest
        or event_digest != EXPECTED_EVENT_IDS_SHA256
    ):
        raise ValueError("publication_availability_evidence_event_digest_invalid")

    missing = evidence.get("missing_release")
    if not isinstance(missing, Mapping):
        raise ValueError("publication_availability_evidence_missing_release_required")
    missing_ids = missing.get("expected_event_ids")
    if (
        not isinstance(missing_ids, list)
        or any(not isinstance(value, str) or not value.strip() for value in missing_ids)
        or missing_ids != sorted(set(missing_ids))
        or len(missing_ids) != EXPECTED_MISSING_RELEASE_COUNT
        or not set(missing_ids).issubset(set(event_ids))
    ):
        raise ValueError("publication_availability_evidence_missing_release_invalid")
    missing_digest = canonical_event_ids_digest([str(value) for value in missing_ids])
    if (
        missing.get("expected_count") != EXPECTED_MISSING_RELEASE_COUNT
        or missing.get("expected_event_ids_sha256") != missing_digest
        or missing.get("expected_event_ids_digest") != missing_digest
        or missing.get("derivation") != "normalized_result_rows_published_at_null"
        or missing.get("source_field") != "result.published_at"
    ):
        raise ValueError("publication_availability_evidence_missing_release_digest_invalid")

    sources = evidence.get("sources")
    if not isinstance(sources, Mapping) or set(sources) != {"receipt", "documents"}:
        raise ValueError("publication_availability_evidence_sources_invalid")
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
    for name, expected in expected_sources.items():
        source = sources.get(name)
        if not isinstance(source, Mapping) or set(source) != {"path", "schema", "raw_sha256", "byte_size"}:
            raise ValueError(f"publication_availability_evidence_source_invalid:{name}")
        if tuple(source.get(field) for field in ("path", "schema", "raw_sha256", "byte_size")) != expected:
            raise ValueError(f"publication_availability_evidence_source_mismatch:{name}")
    return evidence


def _publication_availability_policy(repo_root: Path) -> dict[str, Any]:
    """Build the uniform Treasury result-availability policy from tracked evidence.

    The tracked v1 public receipt contains the complete 127-event inventory but
    predates the normalized release-time evidence.  In that case the exact
    missing-release set remains an explicit receipt-recomputed input rather than
    being guessed from ignored run bytes.
    """

    receipt_path = repo_root / PUBLIC_EVIDENCE_RECEIPT_RELATIVE
    receipt = _load_json(receipt_path)
    events = receipt.get("events")
    if not isinstance(events, list):
        raise ValueError("public_evidence_events_required")
    if any(not isinstance(event, Mapping) for event in events):
        raise ValueError("public_evidence_event_invalid")
    event_ids = sorted({_public_receipt_event_id(event) for event in events})
    if len(events) != EXPECTED_EVENT_COUNT or len(event_ids) != len(events):
        raise ValueError("public_evidence_expected_event_count_invalid")
    digest = canonical_event_ids_digest(event_ids)
    if digest != EXPECTED_EVENT_IDS_SHA256:
        raise ValueError("public_evidence_event_ids_digest_changed")
    receipt_raw_sha256 = sha256_file(receipt_path)
    if receipt_raw_sha256 != PUBLIC_EVIDENCE_RECEIPT_RAW_SHA256:
        raise ValueError("public_evidence_receipt_bytes_changed")
    evidence = _validate_publication_availability_evidence(repo_root, event_ids)
    missing = evidence["missing_release"]
    missing_ids = list(missing["expected_event_ids"])
    missing_digest = str(missing["expected_event_ids_digest"])
    source_receipt = {
        "path": PUBLIC_EVIDENCE_RECEIPT_RELATIVE.as_posix(),
        "schema": receipt.get("schema"),
        "raw_sha256": receipt_raw_sha256,
    }
    return {
        "result_availability_policy": RESULT_AVAILABILITY_POLICY,
        "result_signal_eligibility": RESULT_SIGNAL_ELIGIBILITY,
        "same_day_signal_allowed": False,
        "actual_release_time_role": ACTUAL_RELEASE_TIME_ROLE,
        "server_last_modified_is_publication_proof": False,
        "expected_event_count": EXPECTED_EVENT_COUNT,
        "expected_event_ids": event_ids,
        "expected_event_ids_sha256": digest,
        "expected_event_ids_digest": digest,
        "missing_release": {
            "expected_count": EXPECTED_MISSING_RELEASE_COUNT,
            "expected_event_ids": missing_ids,
            "expected_event_ids_sha256": missing_digest,
            "expected_event_ids_digest": missing_digest,
            "derivation": "normalized_result_rows_published_at_null",
            "source_field": "result.published_at",
        },
        "source_receipt": source_receipt,
        "source_evidence": {
            "path": PUBLICATION_AVAILABILITY_EVIDENCE_RELATIVE.as_posix(),
            "schema": PUBLICATION_AVAILABILITY_EVIDENCE_SCHEMA,
            "raw_sha256": PUBLICATION_AVAILABILITY_EVIDENCE_RAW_SHA256,
            "byte_size": PUBLICATION_AVAILABILITY_EVIDENCE_BYTE_SIZE,
        },
        "canonicalization": EVENT_IDS_CANONICALIZATION,
    }


def _v1_contract_preservation(repo_root: Path, v1: Mapping[str, Any]) -> dict[str, Any]:
    """Pin raw v1 bytes and the unmodified base event contract."""

    paths = {
        "manifest": V1_MANIFEST_RELATIVE,
        "schema": V1_SCHEMA_RELATIVE,
        "source_matrix": V1_SOURCE_MATRIX_RELATIVE,
        "holdout_contract": V1_HOLDOUT_CONTRACT_RELATIVE,
    }
    pins: dict[str, Any] = {}
    for key, relative in paths.items():
        path = repo_root / relative
        if not path.is_file():
            raise FileNotFoundError(f"v1_contract_artifact_missing:{relative}")
        pins[f"{key}_path"] = relative.as_posix()
        pins[f"{key}_raw_sha256"] = sha256_file(path)
        pins[f"{key}_byte_size"] = path.stat().st_size
    event_contract = v1.get("event_contract")
    if not isinstance(event_contract, Mapping):
        raise ValueError("v1_event_contract_required")
    pins["event_contract_canonical_sha256"] = sha256_bytes(_canonical_bytes(event_contract))
    pins["allowed_v2_overlay"] = ["publication_availability_policy"]
    return pins


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    """Create one canonical pretty JSON file without replacing an existing file."""

    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = (json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
    temporary: str | None = None
    try:
        with tempfile.NamedTemporaryFile(mode="wb", dir=path.parent, prefix=f".{path.name}.", delete=False) as handle:
            temporary = handle.name
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            # link(2) is atomic and, unlike replace(2), never overwrites a target.
            os.link(temporary, path)
        except FileExistsError as exc:
            raise FileExistsError(f"no_overwrite:{path}") from exc
    finally:
        if temporary is not None:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass


def _relative_reference(path: Path, *, repo_root: Path) -> str:
    try:
        return path.resolve().relative_to(repo_root.resolve()).as_posix()
    except ValueError:
        return path.name


def _deep_merge(base: dict[str, Any], override: Mapping[str, Any]) -> dict[str, Any]:
    for key, value in override.items():
        if isinstance(value, Mapping) and isinstance(base.get(key), dict):
            _deep_merge(base[key], value)
        else:
            base[key] = copy.deepcopy(value)
    return base


def _git_blob_sha256(repo_root: Path, commit: str, relative: str) -> str | None:
    """Return the SHA-256 of a committed blob, not Git's object id."""

    if not HEX40_RE.fullmatch(commit) or not relative or Path(relative).is_absolute() or ".." in Path(relative).parts:
        return None
    result = subprocess.run(
        ["git", "show", f"{commit}:{Path(relative).as_posix()}"],
        cwd=repo_root,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return hashlib.sha256(result.stdout).hexdigest() if result.returncode == 0 else None


def _pin_source_files(source: dict[str, Any], *, repo_root: Path, source_commit: str | None) -> None:
    """Pin current source bytes and committed blobs without including the bundle itself."""

    commit_is_valid = isinstance(source_commit, str) and HEX40_RE.fullmatch(source_commit) is not None
    commit_value = source_commit if isinstance(source_commit, str) else ""

    declarations: list[tuple[str, dict[str, Any]]] = []
    source_files = source.get("source_files")
    if isinstance(source_files, Mapping):
        declarations.extend(
            (str(name), spec) for name, spec in source_files.items() if isinstance(name, str) and isinstance(spec, dict)
        )
    for role in (
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
        "source",
        "terminal_aware_engine",
        "regulatory_fees",
        "portfolio_engine",
        "settings",
        "readiness",
        "tests",
    ):
        spec = source.get(role)
        if isinstance(spec, dict) and isinstance(spec.get("path"), str):
            declarations.append((spec["path"], spec))

    seen: set[int] = set()
    for relative, spec in declarations:
        marker = id(spec)
        if marker in seen:
            continue
        seen.add(marker)
        path = repo_root / relative
        spec["required_in_base_commit"] = False
        spec["sha256"] = sha256_file(path) if path.is_file() else None
        spec["byte_size"] = path.stat().st_size if path.is_file() else None
        spec["git_blob_sha256"] = (
            _git_blob_sha256(repo_root, commit_value, relative)
            if commit_is_valid
            else None
        )
        if relative == PUBLICATION_AVAILABILITY_EVIDENCE_RELATIVE.as_posix():
            spec["required_in_base_commit"] = False
            spec["required_in_source_commit"] = True
        elif relative in {V5_INCUMBENT_PATHS[key] for key in V5_TRACKED_CONTENT_KEYS}:
            spec["required_in_base_commit"] = relative in {
                V5_INCUMBENT_PATHS[key] for key in V5_BASE_REQUIRED_CONTENT_KEYS
            }
            spec["required_in_source_commit"] = True
        else:
            spec.setdefault("required_in_base_commit", False)
            spec.setdefault("required_in_source_commit", False)
        spec["status"] = "PINNED" if spec["sha256"] and spec["git_blob_sha256"] else "BLOCKED_NOT_IN_SOURCE_COMMIT"

    environment = source.get("environment")
    if isinstance(environment, dict):
        lock_path_value = environment.get("dependency_lock_path")
        if isinstance(lock_path_value, str):
            lock_path = repo_root / lock_path_value
            environment["dependency_lock_sha256"] = sha256_file(lock_path) if lock_path.is_file() else None
            environment["dependency_lock_git_blob_sha256"] = (
                _git_blob_sha256(repo_root, commit_value, lock_path_value)
                if commit_is_valid
                else None
            )
        if commit_is_valid:
            environment["python"] = platform.python_version()
            environment["platform"] = f"{platform.system()} {platform.machine()}"
            environment["timezone"] = os.environ.get("TZ") or "Europe/Istanbul"
            environment["locale"] = os.environ.get("LC_ALL") or os.environ.get("LANG") or "C.UTF-8"
            environment["command"] = EXECUTION_COMMAND


def _incumbent_dependency_block(source: Mapping[str, Any], *, repo_root: Path) -> dict[str, Any]:
    """Return source-pinned V5 dependencies without any post-seal artifacts."""

    source_files = source.get("source_files")
    if not isinstance(source_files, Mapping):
        source_files = {}
    block: dict[str, Any] = {
        "schema_version": "r46-incumbent-data-contract-v1",
        "historical_cutoff": R46_HISTORICAL_CUTOFF,
    }
    for key, relative in V5_INCUMBENT_PATHS.items():
        declared = source_files.get(relative)
        spec = dict(declared) if isinstance(declared, Mapping) else {}
        spec.setdefault("path", relative)
        path = repo_root / relative
        if path.is_file():
            raw_hash = sha256_file(path)
            spec["sha256"] = raw_hash
            spec["raw_sha256"] = raw_hash
            spec["byte_size"] = path.stat().st_size
            if key in {"bars_manifest", "corporate_actions_manifest"}:
                try:
                    payload = _load_json(path)
                except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
                    payload = {}
                if isinstance(payload, Mapping):
                    spec["dataset_sha256"] = payload.get("sha256")
                    if key == "bars_manifest":
                        spec["lifetime_table_sha256"] = payload.get("lifetime_table_hash")
            spec.setdefault(
                "status",
                "EXTERNAL_CONTENT_PINNED"
                if key in {"bars", "corporate_actions", "bars_manifest", "corporate_actions_manifest"}
                else "PINNED",
            )
        else:
            spec.setdefault("sha256", None)
            spec.setdefault("raw_sha256", None)
            spec.setdefault("byte_size", None)
            spec.setdefault("status", "BLOCKED_MISSING")
        is_external = key in V5_EXTERNAL_CONTENT_KEYS
        is_tracked = key in V5_TRACKED_CONTENT_KEYS
        if is_external:
            # External market bytes are content-addressed, but deliberately
            # do not need to exist in C_src or carry a Git blob pin.
            spec["git_blob_sha256"] = None
            spec["required_in_base_commit"] = False
            spec["required_in_source_commit"] = False
        elif is_tracked:
            spec["required_in_base_commit"] = key in V5_BASE_REQUIRED_CONTENT_KEYS
            spec["required_in_source_commit"] = True
            source_commit = source.get("source_commit")
            spec["git_blob_sha256"] = _git_blob_sha256(
                repo_root,
                source_commit if isinstance(source_commit, str) else "",
                relative,
            )
        else:
            spec.setdefault("git_blob_sha256", None)
            spec.setdefault("required_in_base_commit", False)
            spec.setdefault("required_in_source_commit", False)
        block[key] = spec
    block["bars_manifest_sha256"] = block["bars_manifest"].get("sha256")
    block["corporate_actions_manifest_sha256"] = block["corporate_actions_manifest"].get("sha256")
    return block


def _statistics_plan() -> dict[str, Any]:
    inferential_ids = [
        "R46_PRIMARY_EVENT_EPISODE_EXCESS_VS_EXPOSURE_MATCHED_NULL",
        "R46_INCUMBENT_COMPARISON",
        "R46_COST_2X",
        "R46_COST_3X",
    ]
    plan: dict[str, Any] = {
        "schema_version": V2_STATISTICS_SCHEMA,
        "research_id": V2_RESEARCH_ID,
        "candidate_id": CANDIDATE_ID,
        "status": "FROZEN_PRE_RUN_UNAPPROVED",
        "claim_policy": "retrospective_descriptive_only_no_confirmatory_claim",
        "sample_unit": "event_episode",
        "coverage_claim_policy": "minimums_are_operational_gates_not_power_claims",
        "episode_definition": {
            "key": "canonical_event_id:auction_session_date",
            "same_date_events": "one exposure episode",
            "overlap": "no pyramid and no extension",
            "holding_horizon": "five_open_to_open_intervals",
            "daily_marks_are_not_independent": True,
        },
        "primary_estimand": {
            "id": "R46_PRIMARY_EVENT_EPISODE_EXCESS_VS_EXPOSURE_MATCHED_NULL",
            "estimand": "initial_equity_portfolio_contribution",
            "contrast": "candidate_minus_exposure_matched_null",
            "aggregation": "equal_weight_over_eligible_event_episodes",
            "horizon": "five_open_to_open_intervals",
            "direction": "descriptive_signed_difference",
            "confirmatory_threshold": None,
        },
        "secondary_diagnostics": [
            {"id": "R46_INCUMBENT_COMPARISON", "role": "descriptive_control"},
            {"id": "R46_TIMING_PLACEBO", "role": "descriptive_control"},
            {"id": "R46_COST_2X", "role": "cost_stress"},
            {"id": "R46_COST_3X", "role": "cost_stress"},
            {"id": "R46_CAPACITY_SUPPRESSION", "role": "coverage_only"},
            {"id": "R46_MISSINGNESS", "role": "coverage_only"},
        ],
        "reported_output_count": 7,
        "inferential_family": {
            "size": 4,
            "ids": inferential_ids,
            "method": "holm_step_down_for_descriptive_adjusted_p_values",
            "interpretation": "descriptive_only_no_threshold_or_selection",
        },
        "resampling": {
            "method": "circular_event_episode_block_bootstrap",
            "bootstrap_repetitions": 10000,
            "bootstrap_seed": 4601,
            "permutation_method": "paired_sign_flip_randomization_under_exchangeability",
            "permutation_repetitions": 10000,
            "permutation_seed": 4602,
            "placebo_seed": 4603,
            "block_unit": "event_episode",
            "block_length": 5,
        },
        "coverage_gates": {
            "min_event_episodes": 24,
            "min_holding_intervals": 120,
            "max_missing_rate": 0.0,
            "max_capacity_suppression_rate": 0.25,
            "require_fifth_open_exit": True,
            "insufficient_policy": "BLOCKED_INSUFFICIENT_EVENT_RATE_no_extension_or_relaxation",
        },
        "outcome_fields_forbidden_before_run": [
            "returns",
            "nav",
            "pnl",
            "fills",
            "turnover",
            "drawdown",
            "cvar",
            "sharpe",
            "p_values",
        ],
        "approvals": {
            "statistician": None,
            "operator": None,
            "independent_reviewer": None,
        },
        "integrity": {"canonicalization": CANONICALIZATION, "artifact_sha256": ""},
    }
    plan["integrity"]["artifact_sha256"] = canonical_hash(plan, self_field="artifact_sha256")
    return plan


def _family_registry() -> dict[str, Any]:
    hypotheses = [
        {"id": "R46_PRIMARY_EVENT_EPISODE_EXCESS_VS_EXPOSURE_MATCHED_NULL", "role": "primary"},
        {"id": "R46_INCUMBENT_COMPARISON", "role": "secondary"},
        {"id": "R46_TIMING_PLACEBO", "role": "secondary"},
        {"id": "R46_COST_2X", "role": "secondary"},
        {"id": "R46_COST_3X", "role": "secondary"},
        {"id": "R46_CAPACITY_SUPPRESSION", "role": "coverage"},
        {"id": "R46_MISSINGNESS", "role": "coverage"},
    ]
    registry: dict[str, Any] = {
        "schema_version": V2_FAMILY_SCHEMA,
        "research_id": V2_RESEARCH_ID,
        "candidate_id": CANDIDATE_ID,
        "family_id": "new_complementary_information_sources_2026_09",
        "status": "FROZEN_PRE_RUN_UNAPPROVED",
        "claim_policy": "retrospective_descriptive_only_no_confirmatory_claim",
        "scope": "r46_v2_reported_outputs_only",
        "variant_inventory_complete": True,
        "global_prior_search_family_complete": False,
        "global_prior_search_family_status": "INCOMPLETE_DISCLOSED",
        "prior_access_disclosure": "Historical artifacts were previously accessed; this family cannot support a pristine confirmatory claim.",
        "hypotheses": hypotheses,
        "family_size": len(hypotheses),
        "reported_output_count": len(hypotheses),
        "inferential_family_size": 4,
        "inferential_ids": [
            "R46_PRIMARY_EVENT_EPISODE_EXCESS_VS_EXPOSURE_MATCHED_NULL",
            "R46_INCUMBENT_COMPARISON",
            "R46_COST_2X",
            "R46_COST_3X",
        ],
        "alpha": 0.05,
        "method": "holm_step_down_for_descriptive_adjusted_p_values",
        "adjusted_p_values_interpretation": "descriptive_only",
        "ordering": "lexicographic_hypothesis_id",
        "approvals": {
            "statistician": None,
            "operator": None,
            "independent_reviewer": None,
        },
        "integrity": {"canonicalization": CANONICALIZATION, "artifact_sha256": ""},
    }
    registry["integrity"]["artifact_sha256"] = canonical_hash(registry, self_field="artifact_sha256")
    return registry


def _one_shot_state() -> dict[str, Any]:
    state: dict[str, Any] = {
        "schema_version": V2_ONE_SHOT_SCHEMA,
        "research_id": V2_RESEARCH_ID,
        "candidate_id": CANDIDATE_ID,
        "status": "SEALED_UNUSED",
        "prior_access_acknowledged": True,
        "contamination_acknowledged": True,
        "confirmatory_claim_forbidden": True,
        "tuning_forbidden": True,
        "rerun_forbidden": True,
        "release_count": 0,
        "run_count": 0,
        "outcome_artifacts_present": False,
        "reservation_owner": "execution",
        "reservation_state": "UNRESERVED",
        "integrity": {"canonicalization": CANONICALIZATION, "artifact_sha256": ""},
    }
    state["integrity"]["artifact_sha256"] = canonical_hash(state, self_field="artifact_sha256")
    return state


def _schema() -> dict[str, Any]:
    digest_schema = {"type": "string", "pattern": "^[0-9a-f]{64}$"}
    event_ids_schema = {
        "type": "array",
        "minItems": EXPECTED_EVENT_COUNT,
        "maxItems": EXPECTED_EVENT_COUNT,
        "uniqueItems": True,
        "items": {"type": "string", "minLength": 1},
    }
    missing_event_ids_schema = {
        "type": "array",
        "minItems": EXPECTED_MISSING_RELEASE_COUNT,
        "maxItems": EXPECTED_MISSING_RELEASE_COUNT,
        "uniqueItems": True,
        "items": {"type": "string", "minLength": 1},
    }
    publication_policy_schema = {
        "type": "object",
        "required": [
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
        ],
        "properties": {
            "result_availability_policy": {"const": RESULT_AVAILABILITY_POLICY},
            "result_signal_eligibility": {"const": RESULT_SIGNAL_ELIGIBILITY},
            "same_day_signal_allowed": {"const": False},
            "actual_release_time_role": {"const": ACTUAL_RELEASE_TIME_ROLE},
            "server_last_modified_is_publication_proof": {"const": False},
            "expected_event_count": {"const": EXPECTED_EVENT_COUNT},
            "expected_event_ids": event_ids_schema,
            "expected_event_ids_sha256": {"const": EXPECTED_EVENT_IDS_SHA256},
            "expected_event_ids_digest": {"const": EXPECTED_EVENT_IDS_SHA256},
            "missing_release": {
                "type": "object",
                "required": [
                    "expected_count",
                    "expected_event_ids",
                    "expected_event_ids_sha256",
                    "expected_event_ids_digest",
                    "derivation",
                    "source_field",
                ],
                "properties": {
                    "expected_count": {"const": EXPECTED_MISSING_RELEASE_COUNT},
                    "expected_event_ids": missing_event_ids_schema,
                    "expected_event_ids_sha256": digest_schema,
                    "expected_event_ids_digest": digest_schema,
                    "derivation": {
                        "const": "normalized_result_rows_published_at_null"
                    },
                    "source_field": {
                        "const": "result.published_at"
                    },
                },
                "additionalProperties": False,
            },
            "source_receipt": {
                "type": "object",
                "required": ["path", "schema", "raw_sha256"],
                "properties": {
                    "path": {"const": PUBLIC_EVIDENCE_RECEIPT_RELATIVE.as_posix()},
                    "schema": {"const": "r46-treasury-public-source-evidence-receipt-v1"},
                    "raw_sha256": {"const": PUBLIC_EVIDENCE_RECEIPT_RAW_SHA256},
                },
                "additionalProperties": False,
            },
            "source_evidence": {
                "type": "object",
                "required": ["path", "schema", "raw_sha256", "byte_size"],
                "properties": {
                    "path": {"const": PUBLICATION_AVAILABILITY_EVIDENCE_RELATIVE.as_posix()},
                    "schema": {"const": PUBLICATION_AVAILABILITY_EVIDENCE_SCHEMA},
                    "raw_sha256": {"const": PUBLICATION_AVAILABILITY_EVIDENCE_RAW_SHA256},
                    "byte_size": {"const": PUBLICATION_AVAILABILITY_EVIDENCE_BYTE_SIZE},
                },
                "additionalProperties": False,
            },
            "canonicalization": {"const": EVENT_IDS_CANONICALIZATION},
        },
        "additionalProperties": False,
    }
    v1_preservation_schema = {
        "type": "object",
        "required": [
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
        ],
        "properties": {
            "manifest_path": {"const": V1_MANIFEST_RELATIVE.as_posix()},
            "manifest_raw_sha256": digest_schema,
            "manifest_byte_size": {"type": "integer", "minimum": 1},
            "schema_path": {"const": V1_SCHEMA_RELATIVE.as_posix()},
            "schema_raw_sha256": digest_schema,
            "schema_byte_size": {"type": "integer", "minimum": 1},
            "source_matrix_path": {"const": V1_SOURCE_MATRIX_RELATIVE.as_posix()},
            "source_matrix_raw_sha256": digest_schema,
            "source_matrix_byte_size": {"type": "integer", "minimum": 1},
            "holdout_contract_path": {"const": V1_HOLDOUT_CONTRACT_RELATIVE.as_posix()},
            "holdout_contract_raw_sha256": digest_schema,
            "holdout_contract_byte_size": {"type": "integer", "minimum": 1},
            "event_contract_canonical_sha256": digest_schema,
            "allowed_v2_overlay": {"const": ["publication_availability_policy"]},
        },
        "additionalProperties": False,
    }
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "parayabuck://research/r46-treasury-auction-unwind-tlt-v2-governance",
        "title": "R46 retrospective one-shot disclosed governance manifest",
        "type": "object",
        "required": [
            "schema_version",
            "research_id",
            "candidate_id",
            "parent_manifest_path",
            "parent_manifest_sha256",
            "status",
            "contamination",
            "evaluation_contract",
            "statistics",
            "multiplicity",
            "data_contract",
            "execution_contract",
            "evidence_contract",
            "source_provenance",
            "publication_availability_policy",
            "v1_contract_preservation",
            "operator_authorization",
            "integrity",
        ],
        "properties": {
            "schema_version": {"const": V2_SCHEMA_VERSION},
            "research_id": {"const": V2_RESEARCH_ID},
            "candidate_id": {"const": CANDIDATE_ID},
            "parent_manifest_sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
            "status": {"const": "RETROSPECTIVE_ONE_SHOT_DISCLOSED"},
            "declared_before_outcomes": {"const": True},
            "economic_execution": {"const": False},
            "economic_run": {"const": False},
            "economic_outcomes_accessed": {"const": False},
            "outcome_artifacts_present": {"const": False},
            "research_only": {"const": True},
            "promotion": {"const": False},
            "integration_candidate": {"const": False},
            "paper_live_untouched": {"const": True},
            "paper_or_live_activation": {"const": False},
            "auto_promotion": {"const": False},
            "no_auto_promotion": {"const": True},
            "operator_authorization": {
                "type": "object",
                "required": ["kind", "authorized", "cryptographic", "scope", "confirmatory_claim"],
                "properties": {
                    "kind": {"const": "user_scope_authorization"},
                    "authorized": {"const": True},
                    "cryptographic": {"const": False},
                    "scope": {"type": "string", "minLength": 1},
                    "confirmatory_claim": {"const": False},
                },
            },
            "contamination": {
                "type": "object",
                "required": ["acknowledged", "confirmatory_claim_forbidden"],
                "properties": {
                    "acknowledged": {"const": True},
                    "confirmatory_claim_forbidden": {"const": True},
                },
            },
            "evaluation_contract": {
                "type": "object",
                "required": ["mode", "tuning_forbidden", "rerun_forbidden"],
                "properties": {
                    "mode": {"const": "retrospective_one_shot"},
                    "tuning_forbidden": {"const": True},
                    "rerun_forbidden": {"const": True},
                },
            },
            "evidence_contract": {
                "type": "object",
                "required": [
                    "governance_contract",
                    "post_seal_hashes_excluded",
                    "package_path",
                    "native_attestation_path",
                    "independent_software_review_path",
                ],
                "properties": {
                    "governance_contract": {"const": "immutable_static_manifest_sha256"},
                    "post_seal_hashes_excluded": {"const": True},
                    "package_path": {"type": "string", "minLength": 1},
                    "native_attestation_path": {"type": "string", "minLength": 1},
                    "independent_software_review_path": {"type": "string", "minLength": 1},
                },
            },
            "publication_availability_policy": publication_policy_schema,
            "v1_contract_preservation": v1_preservation_schema,
            "integrity": {
                "type": "object",
                "required": ["schema_path", "schema_sha256", "manifest_sha256"],
            },
        },
        "additionalProperties": True,
    }


def _default_source_provenance(v1: Mapping[str, Any]) -> dict[str, Any]:
    source = copy.deepcopy(dict(v1.get("source_provenance", {})))
    source["base_commit"] = PLACEHOLDER
    source["source_commit"] = PLACEHOLDER
    source["result_recording_commit"] = None
    source["clean_tree_required"] = True
    source["clean_tree_observed"] = False
    source.setdefault("source_files", {})
    if isinstance(source["source_files"], dict):
        # V5 market CSVs and both manifests are external content pins.  They
        # must not acquire a Git-blob requirement because a checkout happens
        # to track a small manifest file.
        for key in ("bars", "corporate_actions", "bars_manifest", "corporate_actions_manifest"):
            source["source_files"].pop(V5_INCUMBENT_PATHS[key], None)
        for spec in source["source_files"].values():
            if isinstance(spec, dict):
                spec.setdefault("git_blob_sha256", None)
                spec["required_in_base_commit"] = False
        outcome_relevant_paths = {
            "scripts/research/acquire_r46_tlt_market_data_2026_09_10.py": "TLT market-data acquisition",
            "scripts/research/acquire_r46_treasury_xml_v2_2026_09_10.py": "Treasury XML acquisition",
            "scripts/research/build_r46_nyse_calendar_2026_09_10.py": "NYSE session-calendar construction",
            "scripts/research/build_r46_v2_data_package_2026_09_10.py": "v2 package validation and publication",
            "scripts/research/build_r46_v2_governance_2026_09_10.py": "v2 governance construction",
            PUBLICATION_AVAILABILITY_EVIDENCE_BUILDER_RELATIVE.as_posix(): "publication availability evidence construction",
            "scripts/research/r46_v2_preflight_2026_09_10.py": "v2 preflight gate",
            "scripts/research/r46_treasury_auction_unwind_tlt_v2_2026_09_10.py": "v2 runner",
            "scripts/research/verify_r46_treasury_auction_unwind_tlt_v2_2026_09_10.py": "independent v2 verifier",
            "scripts/research/r46_v3_oracle_2026_09_11.py": "independent frozen V3 target oracle",
            "scripts/research/build_r46_native_ledger_attestation_2026_09_10.py": "native-ledger attestation",
            "src/parayabuck/backtest/terminal_aware_engine.py": "terminal-aware native lifecycle engine",
            "src/parayabuck/backtest/regulatory_fees.py": "regulatory fee schedule",
            "src/parayabuck/backtest/portfolio_engine.py": "native portfolio engine",
            "config/settings.yaml": "frozen execution settings",
            "tests/research/test_r46_v2_execution_2026_09_10.py": "v2 execution contract tests",
            "tests/research/test_r46_v2_data_package_2026_09_10.py": "v2 package tests",
            "tests/research/test_r46_v2_governance_2026_09_10.py": "v2 governance tests",
            "tests/research/test_r46_tlt_market_data_2026_09_10.py": "TLT acquisition tests",
            "tests/research/test_r46_treasury_xml_v2_2026_09_10.py": "Treasury XML tests",
            "tests/research/test_r46_nyse_calendar_2026_09_10.py": "NYSE calendar tests",
            "tests/research/test_r46_treasury_acquisition_2026_09_10.py": "Treasury acquisition tests",
            "src/parayabuck/core/models.py": "native engine model types",
            "src/parayabuck/data/market_dataset.py": "native market-dataset and corporate-action types",
            "src/parayabuck/portfolio/__init__.py": "incumbent strategy factory and rebalance policy",
            "src/parayabuck/portfolio/base.py": "portfolio strategy/context seam",
            "src/parayabuck/portfolio/models.py": "target and rebalance models",
            "src/parayabuck/portfolio/equity_residual_momentum.py": "incumbent V3 base implementation",
            "src/parayabuck/portfolio/equity_residual_momentum_v3.py": "incumbent V3 implementation",
        }
        # Large market CSVs are external content pins, not C_src files.  Keep
        # only the tracked lifetime/manifest/seam bytes in the Git-pinned
        # source file set; bars and corporate-actions remain hash-pinned data.
        outcome_relevant_paths.update(
            {
                V5_INCUMBENT_PATHS[key]: f"V5 incumbent {key}"
                for key in ("lifetimes", "seam")
            }
        )
        source["source_files"].setdefault(
            PUBLICATION_AVAILABILITY_EVIDENCE_RELATIVE.as_posix(),
            {"role": "publication availability evidence"},
        )
        for path, role in outcome_relevant_paths.items():
            source["source_files"].setdefault(path, {"role": role})
    role_paths = {
        "runner": "scripts/research/r46_treasury_auction_unwind_tlt_v2_2026_09_10.py",
        "verifier": "scripts/research/verify_r46_treasury_auction_unwind_tlt_v2_2026_09_10.py",
        "v3_oracle": "scripts/research/r46_v3_oracle_2026_09_11.py",
        "preflight": "scripts/research/r46_v2_preflight_2026_09_10.py",
        "builder": "scripts/research/build_r46_v2_governance_2026_09_10.py",
        "publication_evidence": PUBLICATION_AVAILABILITY_EVIDENCE_RELATIVE.as_posix(),
        "custody": "scripts/research/validate_r46_holdout_custody_2026_09_10.py",
        "data_acquisition": "scripts/research/acquire_r46_treasury_xml_v2_2026_09_10.py",
        "calendar": "scripts/research/build_r46_nyse_calendar_2026_09_10.py",
        "package": "scripts/research/build_r46_v2_data_package_2026_09_10.py",
        "native_attestation": "scripts/research/build_r46_native_ledger_attestation_2026_09_10.py",
        "source": "src/parayabuck/backtest/portfolio_engine.py",
        "terminal_aware_engine": "src/parayabuck/backtest/terminal_aware_engine.py",
        "regulatory_fees": "src/parayabuck/backtest/regulatory_fees.py",
        "portfolio_engine": "src/parayabuck/backtest/portfolio_engine.py",
        "settings": "config/settings.yaml",
        "readiness": "scripts/research/r46_treasury_auction_readiness_2026_09_10.py",
        "tests": "tests/research/test_r46_v2_governance_2026_09_10.py",
    }
    for role, path in role_paths.items():
        source[role] = {
            "path": path,
            "sha256": None,
            "git_blob_sha256": None,
            "required_in_base_commit": False,
            "status": "BLOCKED_NOT_PINNED",
        }
    source["independent_software_review"] = {
        "schema_version": "r46-independent-software-review-v1",
        "path": "data/research_receipts/r46_v2_independent_software_review_2026_09_10.json",
        "status": "BLOCKED_NOT_PINNED",
        "required_in_base_commit": False,
    }
    environment = source.setdefault("environment", {})
    environment["network"] = "disabled"
    environment["python"] = "3.13.12"
    environment["platform"] = "Darwin arm64"
    environment["timezone"] = "Europe/Istanbul"
    environment["locale"] = "C.UTF-8"
    environment["dependency_lock_path"] = "uv.lock"
    environment["dependency_lock_sha256"] = None
    environment["dependency_lock_git_blob_sha256"] = None
    environment["command"] = PLACEHOLDER
    return source


def build_v2_bundle(
    repo_root: Path = REPO,
    *,
    output_root: Path | None = None,
    source_overrides: Mapping[str, Any] | None = None,
    source_commit: str | None = None,
    base_commit: str | None = None,
) -> dict[str, Path]:
    """Write the v2 manifest, statistical plan, family registry, and one-shot state."""

    repo_root = Path(repo_root).resolve()
    output = (repo_root / "data/research_manifests") if output_root is None else Path(output_root).resolve()
    output.mkdir(parents=True, exist_ok=True)
    v1_path = repo_root / V1_MANIFEST_RELATIVE
    v1 = _load_json(v1_path)
    if v1.get("schema_version") != "r46-governance-manifest-v1" or v1.get("research_id") != V1_RESEARCH_ID:
        raise ValueError("v1_parent_identity_invalid")
    v1_hash = canonical_hash(v1, self_field="manifest_sha256")
    if v1_hash != EXPECTED_V1_MANIFEST_SHA256 or v1.get("integrity", {}).get("manifest_sha256") != v1_hash:
        raise ValueError("v1_parent_hash_invalid_or_changed")
    publication_policy = _publication_availability_policy(repo_root)
    v1_preservation = _v1_contract_preservation(repo_root, v1)

    schema_path = output / V2_SCHEMA_NAME
    statistics_path = output / V2_STATISTICS_NAME
    family_path = output / V2_FAMILY_NAME
    one_shot_path = output / V2_ONE_SHOT_NAME

    # Refuse a partial overwrite before creating anything.  The one-shot state
    # is the sole execution-owned exception: an existing state is loaded and
    # preserved byte-for-byte, including a consumed state that preflight must
    # subsequently reject.
    manifest_path = output / V2_MANIFEST_NAME
    immutable_targets = (schema_path, statistics_path, family_path, manifest_path)
    existing_targets = [path for path in immutable_targets if path.exists()]
    if existing_targets:
        raise FileExistsError(f"no_overwrite:{existing_targets[0]}")
    if one_shot_path.exists():
        one_shot = _load_json(one_shot_path)
        if (
            one_shot.get("schema_version") != V2_ONE_SHOT_SCHEMA
            or one_shot.get("research_id") != V2_RESEARCH_ID
            or one_shot.get("candidate_id") != CANDIDATE_ID
            or one_shot.get("integrity", {}).get("artifact_sha256")
            != canonical_hash(one_shot, self_field="artifact_sha256")
        ):
            raise ValueError("existing_one_shot_state_invalid")
    else:
        one_shot = _one_shot_state()

    schema = _schema()
    _write_json(schema_path, schema)
    statistics = _statistics_plan()
    _write_json(statistics_path, statistics)
    family = _family_registry()
    _write_json(family_path, family)
    if not one_shot_path.exists():
        _write_json(one_shot_path, one_shot)

    statistics_ref = _relative_reference(statistics_path, repo_root=repo_root)
    family_ref = _relative_reference(family_path, repo_root=repo_root)
    one_shot_ref = _relative_reference(one_shot_path, repo_root=repo_root)
    data_package_ref = _relative_reference(output / V2_DATA_PACKAGE_NAME, repo_root=repo_root)
    source = _default_source_provenance(v1)
    if source_overrides is not None:
        _deep_merge(source, source_overrides)
    if base_commit is not None:
        source["base_commit"] = base_commit
    if source_commit is not None:
        source["source_commit"] = source_commit
    _pin_source_files(source, repo_root=repo_root, source_commit=source.get("source_commit"))
    incumbent_dependencies = _incumbent_dependency_block(source, repo_root=repo_root)
    source["incumbent_dependencies"] = copy.deepcopy(incumbent_dependencies)

    data_contract = copy.deepcopy(dict(v1.get("data_contract", {})))
    data_contract.update(
        {
            "coverage_status": "DATA_ACQUISITION_REQUIRED",
            "pit_source_status": "EXTERNAL_EVIDENCE_REQUIRED",
            "data_package_manifest_path": data_package_ref,
            # This is a static, source-pinned contract.  It deliberately does
            # not contain any post-seal package/PIT/native/review hashes.
            "incumbent_data_contract": copy.deepcopy(incumbent_dependencies),
        }
    )
    execution = copy.deepcopy(dict(v1.get("execution_contract", {})))
    manifest: dict[str, Any] = {
        "schema_version": V2_SCHEMA_VERSION,
        "research_id": V2_RESEARCH_ID,
        "candidate_id": CANDIDATE_ID,
        "family_id": v1.get("family_id"),
        "parent_manifest_path": V1_MANIFEST_RELATIVE.as_posix(),
        "parent_manifest_sha256": v1_hash,
        "status": "RETROSPECTIVE_ONE_SHOT_DISCLOSED",
        "readiness": "BLOCKED_V2_PREFLIGHT",
        "frozen_at": v1.get("frozen_at"),
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
        "operator_authorization": {
            "kind": "user_scope_authorization",
            "authorized": True,
            "cryptographic": False,
            "scope": "retrospective descriptive one-shot governance/preflight only; no economics, promotion, PAPER, or LIVE",
            "confirmatory_claim": False,
        },
        "contamination": {
            "acknowledged": True,
            "basis": "The historical interval and local artifacts were previously accessed; pristine prospective custody is impossible.",
            "historical_holdout_accepted": False,
            "confirmatory_claim_forbidden": True,
        },
        "evaluation_contract": {
            "mode": "retrospective_one_shot",
            "one_shot_state_path": one_shot_ref,
            "one_shot_state_sha256": one_shot["integrity"]["artifact_sha256"],
            "tuning_forbidden": True,
            "selection_after_outcomes_forbidden": True,
            "rerun_forbidden": True,
            "same_sample_reuse_forbidden": True,
            "valid_failure": "FAILED_VALID_FROZEN_TEST",
            "valid_pass": "PASS_RESEARCH_ONLY",
        },
        "candidate": copy.deepcopy(v1.get("candidate")),
        "event_contract": copy.deepcopy(v1.get("event_contract")),
        "publication_availability_policy": publication_policy,
        "v1_contract_preservation": v1_preservation,
        "instrument_contract": copy.deepcopy(v1.get("instrument_contract")),
        "data_contract": data_contract,
        "source_provenance": source,
        "holdout": {
            "mode": "retrospective_one_shot_disclosed",
            "status": "RETROSPECTIVE_ONE_SHOT_DISCLOSED",
            "historical_holdout_accepted": False,
            "prior_access_acknowledged": True,
            "custody_receipt_path": None,
            "custody_signatures": None,
        },
        "execution_contract": execution,
        "evidence_contract": {
            "governance_contract": "immutable_static_manifest_sha256",
            "post_seal_hashes_excluded": True,
            "package": "external_exact_five_artifact_manifest",
            "package_path": data_package_ref,
            "pit_source": "external_validated_treasury_and_alpaca_acquisition_receipts",
            "native_attestation": "external_native_ledger_attestation",
            "native_attestation_path": _relative_reference(
                repo_root / "data/research_receipts" / V2_NATIVE_ATTESTATION_NAME, repo_root=repo_root
            ),
            "independent_software_review": "external_review_binding_source_commit_and_governance_hash",
            "independent_software_review_path": source["independent_software_review"]["path"],
        },
        "cost_contract": copy.deepcopy(v1.get("cost_contract")),
        "controls": copy.deepcopy(v1.get("controls")),
        "statistics": {
            "status": "READY_STATISTICS",
            "approval_status": "DESCRIPTIVE_NO_HUMAN_SIGNATURE_GATE",
            "plan_path": statistics_ref,
            "plan_sha256": statistics["integrity"]["artifact_sha256"],
            "sample_unit": "event_episode",
            "confirmatory_claim_forbidden": True,
        },
        "multiplicity": {
            "status": "READY_MULTIPLICITY",
            "approval_status": "DESCRIPTIVE_NO_HUMAN_SIGNATURE_GATE",
            "family_registry_path": family_ref,
            "family_registry_sha256": family["integrity"]["artifact_sha256"],
            "family_size": family["family_size"],
            "method": family["method"],
        },
        "allowed_pre_run_tests": [
            "manifest/schema/parent-hash/self-hash and outcome-key checks",
            "source/environment/transitive pin and clean-tree checks",
            "five-artifact package hash/cutoff/identity checks",
            "native one-ledger attestation and capacity-only checks",
            "one-shot state and no-rerun checks",
        ],
        "forbidden_pre_run_outputs": [
            "returns",
            "nav",
            "pnl",
            "fills",
            "turnover",
            "drawdown",
            "cvar",
            "sharpe",
            "p_values",
            "best_cell_selection",
            "holdout_outcome_disclosure",
        ],
        "integrity": {
            "canonicalization": CANONICALIZATION,
            "schema_path": _relative_reference(schema_path, repo_root=repo_root),
            "schema_sha256": sha256_file(schema_path),
            "statistics_plan_path": statistics_ref,
            "family_registry_path": family_ref,
            "one_shot_state_path": one_shot_ref,
            "governance_hash_scope": "static governance only; excludes package, PIT, native, review, and preflight receipt hashes",
            "manifest_sha256": "",
        },
    }
    manifest["integrity"]["manifest_sha256"] = canonical_hash(manifest, self_field="manifest_sha256")
    _write_json(manifest_path, manifest)
    return {
        "manifest": manifest_path,
        "schema": schema_path,
        "statistics": statistics_path,
        "family": family_path,
        "one_shot": one_shot_path,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=REPO)
    parser.add_argument("--output-root", type=Path)
    parser.add_argument(
        "--source-commit", help="Immutable source commit C_src; generated artifacts are kept outside that commit"
    )
    parser.add_argument("--base-commit", help="Immutable parent/base commit used for provenance")
    parser.add_argument("--source-overrides", type=Path, help="Canonical JSON source-provenance overrides")
    args = parser.parse_args(argv)
    source_overrides: Mapping[str, Any] | None = None
    if args.source_overrides is not None:
        value = json.loads(args.source_overrides.read_text(encoding="utf-8"))
        if not isinstance(value, Mapping):
            raise ValueError("source_overrides_object_required")
        source_overrides = value
    paths = build_v2_bundle(
        args.repo_root,
        output_root=args.output_root,
        source_overrides=source_overrides,
        source_commit=args.source_commit,
        base_commit=args.base_commit,
    )
    print(json.dumps({key: str(value) for key, value in paths.items()}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
