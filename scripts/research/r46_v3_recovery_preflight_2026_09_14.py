"""Outcome-free validation for the R46 v3 incident-recovery contract.

This module reads governance artifacts only.  It never imports an economic
execution module, creates a transition, writes a receipt, reads an outcome,
or authorizes a run.  A zero process exit code means that the machine-readable
result was emitted; callers must gate on ``status``.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]

V2_RESEARCH_ID = "r46_treasury_auction_unwind_tlt_v2_2026_09_10"
V3_RESEARCH_ID = "r46_treasury_auction_unwind_tlt_v3_recovery_2026_09_14"
CANDIDATE_ID = "R46"
V2_STATE_REL = Path(
    "data/research_manifests/r46_treasury_auction_unwind_tlt_v2_2026_09_10_one_shot_state.json"
)
V2_RESERVED_REL = Path(
    "data/research_manifests/r46_treasury_auction_unwind_tlt_v2_2026_09_10_one_shot_state.json.reserved.json"
)
V2_CONSUMED_REL = Path(
    "data/research_manifests/r46_treasury_auction_unwind_tlt_v2_2026_09_10_one_shot_state.json.consumed.json"
)
V2_ABORTED_REL = Path(
    "data/research_manifests/r46_treasury_auction_unwind_tlt_v2_2026_09_10_one_shot_state.json.aborted_consumed.json"
)
V2_PREFLIGHT_REL = Path("data/research_receipts/r46_v2_preflight_2026_09_14.json")
V2_GOVERNANCE_REL = Path("data/research_manifests/r46_treasury_auction_unwind_tlt_v2_2026_09_10.json")
V2_PACKAGE_REL = Path(
    "data/research_manifests/r46_treasury_auction_unwind_tlt_v2_2026_09_10_data_package.json"
)
V2_STATISTICS_REL = Path(
    "data/research_manifests/r46_treasury_auction_unwind_tlt_v2_2026_09_10_statistics_plan.json"
)
V2_FAMILY_REL = Path("data/research_manifests/r46_treasury_auction_unwind_tlt_v2_2026_09_10_family_registry.json")
V2_OUTPUT_DIR_REL = Path("data/research/runs/r46_treasury_auction_unwind_tlt_v2_final_2026_09_14")
V3_INCIDENT_REL = Path("data/research_receipts/r46_v2_reservation_incident_2026_09_14.json")
V3_STATE_REL = Path(
    "data/research_manifests/r46_treasury_auction_unwind_tlt_v3_recovery_2026_09_14_one_shot_state.json"
)
V3_MANIFEST_REL = Path("data/research_manifests/r46_treasury_auction_unwind_tlt_v3_recovery_2026_09_14.json")
V3_SCHEMA_REL = Path("data/research_manifests/r46_treasury_auction_unwind_tlt_v3_recovery_2026_09_14.schema.json")
V3_RESERVED_REL = Path(
    "data/research_manifests/r46_treasury_auction_unwind_tlt_v3_recovery_2026_09_14_one_shot_state.json.reserved.json"
)
V3_CONSUMED_REL = Path(
    "data/research_manifests/r46_treasury_auction_unwind_tlt_v3_recovery_2026_09_14_one_shot_state.json.consumed.json"
)
V3_ABORTED_REL = Path(
    "data/research_manifests/r46_treasury_auction_unwind_tlt_v3_recovery_2026_09_14_one_shot_state.json.aborted_consumed.json"
)
SEARCH_ROOTS = (Path("data/research/runs"), Path("data/research_runs"))
ECONOMIC_FILENAMES = frozenset({"results.json", "ledger.json", "report.json", "run_receipt.json"})
ECONOMIC_FILENAME_PATTERNS = ("results.json", "ledger.json", "report.json", "run_receipt.json")
V2_BUNDLE_TOKEN = "r46_treasury_auction_unwind_tlt_v2"

EXPECTED_V2_STATE_SHA256 = "c69e6bd76964131f41a02b4a58a1a308af1a2a1a038c8e339b1512b097b2f412"
EXPECTED_V2_STATE_CANONICAL_SHA256 = "b2977b5f516cd4371af2201a87cb84637beb47214e74cc3d0980bbc9eaf33fab"
EXPECTED_V2_RESERVED_SHA256 = "434b5ef71015d132516474a11078c4e5866d3c91e7d265a7f7c3b20d655141e4"
EXPECTED_V2_RESERVED_TRANSITION_SHA256 = "040d13dcba81e3d52e7d105aea9250a3c565e7b87e9b6af5c44e83b0dec28405"
EXPECTED_V2_GATE_SHA256 = "6307caa29e8a2179d36a5f2792573bb7307f22a317fa55071b348eb51ef2639c"
EXPECTED_V2_GATE_PHYSICAL_SHA256 = "b05afe121048291edce06d54ef001363470ea3cc2e16ce58eb44f35ab9d972bc"
EXPECTED_V2_GOVERNANCE_SHA256 = "92f7ecd5b7f6cdd2428ee221c3b4891f10d5d7ab6f16a5fcb2be952753557c35"
EXPECTED_V2_PACKAGE_SHA256 = "c67cfdfeebc4cbc0847d5e2e6fbdebdf6928e04b3e5103606eb8bd0c3d03f6c6"
EXPECTED_V2_STATISTICS_SHA256 = "21ebb2e453bd3587388fa800919d5ca24eb9fab4d29b21c106d8e1ce8a015331"
EXPECTED_V2_FAMILY_SHA256 = "eeda04d795d099e7571bdbeae0ccaa2dc55141ec2f15d1f8afdc507d60b9acae"
EXPECTED_INCIDENT_CLASSIFICATION = "ORIGIN_UNPROVEN"
EXPECTED_INCIDENT_TYPE = "RESERVED_WITHOUT_PERSISTED_COMPLETION"
EXPECTED_CLAIM_POLICY = "RETROSPECTIVE_DESCRIPTIVE_ONLY_ORIGIN_UNPROVEN"
HASH_RE = re.compile(r"^[0-9a-f]{64}$")


def _canonical_bytes(payload: Mapping[str, Any]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def canonical_hash(payload: Mapping[str, Any], self_field: str) -> str:
    value = copy.deepcopy(dict(payload))
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


def _load_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"json_object_required:{path}")
    return value


def _is_hash(value: object) -> bool:
    return isinstance(value, str) and HASH_RE.fullmatch(value) is not None


def _type_matches(value: object, expected: str) -> bool:
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    return True


def _schema_errors(value: object, schema: Mapping[str, Any], location: str = "$") -> list[str]:
    errors: list[str] = []
    if "const" in schema and value != schema["const"]:
        errors.append(f"{location}:const")
    expected_type = schema.get("type")
    if isinstance(expected_type, str) and not _type_matches(value, expected_type):
        errors.append(f"{location}:type")
        return errors
    if isinstance(schema.get("pattern"), str) and (
        not isinstance(value, str) or re.fullmatch(str(schema["pattern"]), value) is None
    ):
        errors.append(f"{location}:pattern")
    if isinstance(schema.get("minLength"), int) and isinstance(value, str) and len(value) < schema["minLength"]:
        errors.append(f"{location}:minLength")
    if isinstance(schema.get("required"), list) and isinstance(value, dict):
        for key in schema["required"]:
            if key not in value:
                errors.append(f"{location}:required:{key}")
    properties = schema.get("properties")
    if isinstance(properties, dict) and isinstance(value, dict):
        if schema.get("additionalProperties") is False:
            allowed = set(properties)
            errors.extend(f"{location}:additional:{key}" for key in sorted(set(value) - allowed))
        for key, child_schema in properties.items():
            if key in value and isinstance(child_schema, dict):
                errors.extend(_schema_errors(value[key], child_schema, f"{location}.{key}"))
    if isinstance(schema.get("items"), dict) and isinstance(value, list):
        for index, item in enumerate(value):
            errors.extend(_schema_errors(item, schema["items"], f"{location}[{index}]"))
    if isinstance(schema.get("minItems"), int) and isinstance(value, list) and len(value) < schema["minItems"]:
        errors.append(f"{location}:minItems")
    if isinstance(schema.get("maxItems"), int) and isinstance(value, list) and len(value) > schema["maxItems"]:
        errors.append(f"{location}:maxItems")
    if schema.get("uniqueItems") is True and isinstance(value, list):
        encoded = [json.dumps(item, sort_keys=True, ensure_ascii=False) for item in value]
        if len(encoded) != len(set(encoded)):
            errors.append(f"{location}:uniqueItems")
    return errors


def _path(root: Path, relative: Path) -> Path:
    return (root / relative).resolve()


def _add(blockers: list[str], value: str) -> None:
    if value not in blockers:
        blockers.append(value)


def _validate_incident(
    root: Path,
    canonical_root: Path,
    incident_path: Path,
    sidecar_path: Path,
    state_path: Path,
    preflight_path: Path,
    blockers: list[str],
) -> tuple[dict[str, Any] | None, str | None, str | None]:
    try:
        incident = _load_object(incident_path)
        sidecar = _load_object(sidecar_path)
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
        _add(blockers, "INCIDENT_RECEIPT_INVALID")
        return None, None, None
    try:
        incident_hash = canonical_hash(incident, "incident_sha256")
    except ValueError:
        incident_hash = None
    if incident_hash is None or incident.get("integrity", {}).get("incident_sha256") != incident_hash:
        _add(blockers, "INCIDENT_RECEIPT_INVALID")
    sidecar_sha = sha256_file(sidecar_path)
    state_sha = sha256_file(state_path) if state_path.is_file() else None
    receipt_sha = sha256_file(preflight_path) if preflight_path.is_file() else None
    try:
        incident_canonical = incident_hash
        expected_paths = {
            "reserved_sidecar_path": str(canonical_root / V2_RESERVED_REL),
            "base_state_path": str(canonical_root / V2_STATE_REL),
            "preflight_receipt_path": str(canonical_root / V2_PREFLIGHT_REL),
        }
        if any(incident.get(key) != value for key, value in expected_paths.items()):
            raise ValueError("incident_path_binding")
        if incident.get("schema_version") != "r46-v2-reservation-incident-v1":
            raise ValueError("incident_schema")
        if incident.get("candidate_id") != CANDIDATE_ID:
            raise ValueError("incident_candidate")
        if incident.get("parent_research_id") != V2_RESEARCH_ID:
            raise ValueError("incident_research")
        if incident.get("classification") != EXPECTED_INCIDENT_CLASSIFICATION:
            raise ValueError("incident_classification")
        if incident.get("incident_type") != EXPECTED_INCIDENT_TYPE:
            raise ValueError("incident_type")
        if incident.get("reservation_payload") != sidecar:
            raise ValueError("incident_payload")
        if incident.get("reserved_sidecar_physical_sha256") != sidecar_sha or sidecar_sha != EXPECTED_V2_RESERVED_SHA256:
            raise ValueError("incident_sidecar_sha")
        if incident.get("base_state_physical_sha256") != state_sha or state_sha != EXPECTED_V2_STATE_SHA256:
            raise ValueError("incident_state_sha")
        if incident.get("preflight_receipt_physical_sha256") != receipt_sha or receipt_sha != EXPECTED_V2_GATE_PHYSICAL_SHA256:
            raise ValueError("incident_receipt_sha")
        if incident.get("reserved_transition_sha256") != EXPECTED_V2_RESERVED_TRANSITION_SHA256:
            raise ValueError("incident_transition")
        if incident.get("governance_canonical_sha256") != EXPECTED_V2_GOVERNANCE_SHA256:
            raise ValueError("incident_governance")
        if incident.get("package_canonical_sha256") != EXPECTED_V2_PACKAGE_SHA256:
            raise ValueError("incident_package")
        if incident.get("preflight_receipt_canonical_sha256") != EXPECTED_V2_GATE_SHA256:
            raise ValueError("incident_gate")
        if incident.get("runner_invocation_proven") is not False:
            raise ValueError("incident_runner")
        for field in (
            "persisted_results_found",
            "persisted_ledger_found",
            "persisted_report_found",
            "persisted_run_receipt_found",
            "consumed_sidecar_found",
            "aborted_sidecar_found",
        ):
            if incident.get(field) is not False:
                raise ValueError(f"incident_{field}")
        if "economic_outcomes_accessed" in incident:
            raise ValueError("incident_absolute_outcome_claim")
        if incident.get("persisted_outcome_evidence_status") != "NONE_FOUND_IN_DECLARED_SCOPE":
            raise ValueError("incident_persisted_status")
        if incident.get("nonpersisted_outcome_exposure_status") != "UNPROVEN":
            raise ValueError("incident_nonpersisted_status")
        if incident.get("economic_outcome_claim") != "FORBIDDEN":
            raise ValueError("incident_claim")
        if incident.get("v2_reservation_must_be_preserved") is not True:
            raise ValueError("incident_preservation")
        if incident.get("v2_rerun_forbidden") is not True:
            raise ValueError("incident_rerun")
        if incident.get("recovery_requires_new_governance_generation") is not True:
            raise ValueError("incident_recovery_generation")
        if incident.get("forensic_search_roots") != [
            str(canonical_root / SEARCH_ROOTS[0]) + "/",
            str(canonical_root / SEARCH_ROOTS[1]) + "/",
        ]:
            raise ValueError("incident_search_roots")
        if incident.get("forensic_filename_patterns") != list(ECONOMIC_FILENAME_PATTERNS):
            raise ValueError("incident_search_patterns")
    except (TypeError, ValueError, KeyError):
        _add(blockers, "INCIDENT_RECEIPT_INVALID")
    return incident, incident_canonical, sidecar_sha


def _validate_v2(
    root: Path,
    canonical_root: Path,
    blockers: list[str],
) -> dict[str, Any]:
    state_path = _path(root, V2_STATE_REL)
    sidecar_path = _path(root, V2_RESERVED_REL)
    preflight_path = _path(root, V2_PREFLIGHT_REL)
    if not sidecar_path.is_file():
        _add(blockers, "V2_RESERVED_SIDECAR_MISSING")
        sidecar_payload = None
        sidecar_sha = None
    else:
        try:
            sidecar_sha = sha256_file(sidecar_path)
            sidecar_payload = _load_object(sidecar_path)
        except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
            sidecar_sha = None
            sidecar_payload = None
        if sidecar_sha != EXPECTED_V2_RESERVED_SHA256:
            _add(blockers, "V2_RESERVED_SIDECAR_HASH_MISMATCH")
        if sidecar_payload is not None:
            try:
                transition_hash = canonical_hash(sidecar_payload, "transition_sha256")
            except ValueError:
                transition_hash = None
            if (
                transition_hash != sidecar_payload.get("integrity", {}).get("transition_sha256")
                or transition_hash != EXPECTED_V2_RESERVED_TRANSITION_SHA256
                or sidecar_payload.get("status") != "RESERVED"
                or sidecar_payload.get("state_path") != str(canonical_root / V2_STATE_REL)
            ):
                _add(blockers, "V2_RESERVED_TRANSITION_HASH_INVALID")
            if sidecar_payload.get("state_sha256") != EXPECTED_V2_STATE_SHA256:
                _add(blockers, "V2_BASE_STATE_HASH_MISMATCH")
            if sidecar_payload.get("state_canonical_sha256") != EXPECTED_V2_STATE_CANONICAL_SHA256:
                _add(blockers, "V2_BASE_STATE_HASH_MISMATCH")
            if sidecar_payload.get("gate_sha256") != EXPECTED_V2_GATE_SHA256:
                _add(blockers, "V2_GATE_HASH_MISMATCH")
            if sidecar_payload.get("manifest_sha256") != EXPECTED_V2_GOVERNANCE_SHA256:
                _add(blockers, "V2_GOVERNANCE_HASH_MISMATCH")
            if sidecar_payload.get("package_sha256") != EXPECTED_V2_PACKAGE_SHA256:
                _add(blockers, "V2_PACKAGE_HASH_MISMATCH")

    if not state_path.is_file():
        _add(blockers, "V2_BASE_STATE_HASH_MISMATCH")
        state_sha = None
    else:
        state_sha = sha256_file(state_path)
        if state_sha != EXPECTED_V2_STATE_SHA256:
            _add(blockers, "V2_BASE_STATE_HASH_MISMATCH")
        try:
            state_payload = _load_object(state_path)
            state_canonical = canonical_hash(state_payload, "artifact_sha256")
        except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
            state_canonical = None
        if state_canonical != EXPECTED_V2_STATE_CANONICAL_SHA256:
            _add(blockers, "V2_BASE_STATE_HASH_MISMATCH")

    if not preflight_path.is_file():
        _add(blockers, "V2_GATE_HASH_MISMATCH")
        gate_sha = None
    else:
        gate_sha = sha256_file(preflight_path)
        try:
            gate_payload = _load_object(preflight_path)
            gate_canonical = canonical_hash(gate_payload, "receipt_sha256")
        except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
            gate_canonical = None
        if gate_sha != EXPECTED_V2_GATE_PHYSICAL_SHA256 or gate_canonical != EXPECTED_V2_GATE_SHA256:
            _add(blockers, "V2_GATE_HASH_MISMATCH")

    artifact_checks = (
        (V2_GOVERNANCE_REL, "manifest_sha256", EXPECTED_V2_GOVERNANCE_SHA256, "V2_GOVERNANCE_HASH_MISMATCH"),
        (V2_PACKAGE_REL, "manifest_sha256", EXPECTED_V2_PACKAGE_SHA256, "V2_PACKAGE_HASH_MISMATCH"),
        (V2_STATISTICS_REL, "artifact_sha256", EXPECTED_V2_STATISTICS_SHA256, "V2_STATISTICS_HASH_MISMATCH"),
        (V2_FAMILY_REL, "artifact_sha256", EXPECTED_V2_FAMILY_SHA256, "V2_FAMILY_HASH_MISMATCH"),
    )
    artifact_hashes: dict[str, str | None] = {}
    for artifact_path, field, expected, blocker in artifact_checks:
        full_path = _path(root, artifact_path)
        try:
            payload = _load_object(full_path)
            computed = canonical_hash(payload, field)
        except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
            computed = None
        artifact_hashes[artifact_path.as_posix()] = computed
        if computed != expected:
            _add(blockers, blocker)

    for transition_path, blocker in (
        (V2_CONSUMED_REL, "V2_CONSUMED_TRANSITION_PRESENT"),
        (V2_ABORTED_REL, "V2_ABORTED_TRANSITION_PRESENT"),
    ):
        if _path(root, transition_path).exists() or _path(root, transition_path).is_symlink():
            _add(blockers, blocker)

    planned_output = _path(root, V2_OUTPUT_DIR_REL)
    if planned_output.exists() or planned_output.is_symlink():
        _add(blockers, "V2_PERSISTED_ECONOMIC_OUTPUT_PRESENT")
    other_bundles: list[str] = []
    for search_root in SEARCH_ROOTS:
        directory = _path(root, search_root)
        if not directory.is_dir():
            continue
        for candidate in directory.rglob("*"):
            if not candidate.is_file() or candidate.name not in ECONOMIC_FILENAMES:
                continue
            if V2_BUNDLE_TOKEN in str(candidate):
                other_bundles.append(str(candidate))
    if other_bundles:
        _add(blockers, "V2_OTHER_ECONOMIC_BUNDLE_PRESENT")
    return {
        "state_sha256": state_sha,
        "reserved_sha256": sidecar_sha,
        "gate_physical_sha256": gate_sha,
        "artifact_canonical_sha256": artifact_hashes,
        "other_bundles": sorted(other_bundles),
    }


def _validate_v3(
    root: Path,
    canonical_root: Path,
    blockers: list[str],
) -> dict[str, Any]:
    manifest_path = _path(root, V3_MANIFEST_REL)
    schema_path = _path(root, V3_SCHEMA_REL)
    state_path = _path(root, V3_STATE_REL)
    incident_path = _path(root, V3_INCIDENT_REL)
    try:
        manifest = _load_object(manifest_path)
        schema = _load_object(schema_path)
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
        _add(blockers, "V3_MANIFEST_INVALID")
        manifest = None
        schema = None
    manifest_sha: str | None = None
    if manifest is not None and schema is not None:
        schema_errors = _schema_errors(manifest, schema)
        try:
            manifest_sha = canonical_hash(manifest, "manifest_sha256")
        except ValueError:
            manifest_sha = None
        if schema_errors or manifest_sha != manifest.get("integrity", {}).get("manifest_sha256"):
            _add(blockers, "V3_MANIFEST_INVALID")

    incident, incident_canonical, sidecar_sha = _validate_incident(
        root,
        canonical_root,
        incident_path,
        _path(root, V2_RESERVED_REL),
        _path(root, V2_STATE_REL),
        _path(root, V2_PREFLIGHT_REL),
        blockers,
    )
    state_sha: str | None = None
    state: dict[str, Any] | None
    try:
        state = _load_object(state_path)
        state_sha = sha256_file(state_path)
        state_canonical = canonical_hash(state, "artifact_sha256")
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
        state = None
        state_canonical = None
    if state is None or state_canonical != state.get("integrity", {}).get("artifact_sha256"):
        _add(blockers, "V3_ONE_SHOT_STATE_INVALID")
    if state is not None:
        if (
            state.get("status") != "SEALED_UNUSED"
            or state.get("run_count") != 0
            or state.get("release_count") != 0
            or state.get("reservation_state") != "UNRESERVED"
        ):
            _add(blockers, "V3_ONE_SHOT_ALREADY_USED")
        if (
            state.get("schema_version") != "r46-recovery-one-shot-state-v3"
            or state.get("research_id") != V3_RESEARCH_ID
            or state.get("candidate_id") != CANDIDATE_ID
            or state.get("recovery_generation") != 1
            or state.get("reservation_owner") != "execution"
            or state.get("incident_receipt_path") != V3_INCIDENT_REL.as_posix()
            or state.get("incident_receipt_physical_sha256") != (sha256_file(incident_path) if incident_path.is_file() else None)
            or state.get("incident_receipt_canonical_sha256") != incident_canonical
        ):
            _add(blockers, "V3_ONE_SHOT_STATE_INVALID")
        if any(state.get(field) is not True for field in (
            "rerun_forbidden",
            "tuning_forbidden",
            "same_sample_reuse_after_v3_result_forbidden",
            "v2_reservation_preserved",
            "v2_state_unchanged_required",
            "confirmatory_claim_forbidden",
            "retrospective_descriptive_only",
            "origin_unproven_acknowledged",
            "nonpersisted_outcome_exposure_unproven",
        )) or state.get("outcome_artifacts_present") is not False:
            _add(blockers, "V3_ONE_SHOT_STATE_INVALID")
    for transition_path in (V3_RESERVED_REL, V3_CONSUMED_REL, V3_ABORTED_REL):
        if _path(root, transition_path).exists() or _path(root, transition_path).is_symlink():
            _add(blockers, "V3_TRANSITION_SIDECAR_PRESENT")

    if manifest is not None:
        boundary_expectations: dict[str, object] = {
            "candidate_id": CANDIDATE_ID,
            "research_id": V3_RESEARCH_ID,
            "recovery_mode": "EXECUTION_INCIDENT_RECOVERY",
            "claim_policy": EXPECTED_CLAIM_POLICY,
            "status": "RECOVERY_FROZEN_PRE_OUTCOME",
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
            "parameter_search_allowed": False,
            "new_parameter_search_forbidden": True,
            "tuning_forbidden": True,
            "confirmatory_claim_forbidden": True,
            "v2_reservation_preserved": True,
            "v2_execution_reuse_forbidden": True,
        }
        if any(manifest.get(key) != value for key, value in boundary_expectations.items()):
            _add(blockers, "PRODUCTION_BOUNDARY_VIOLATION")
        expected_incident_sha = sha256_file(incident_path) if incident_path.is_file() else None
        expected_state_sha = sha256_file(state_path) if state_path.is_file() else None
        if (
            manifest.get("incident_receipt_path") != V3_INCIDENT_REL.as_posix()
            or manifest.get("incident_receipt_sha256") != expected_incident_sha
            or manifest.get("incident_receipt_canonical_sha256") != incident_canonical
            or manifest.get("one_shot_state_path") != V3_STATE_REL.as_posix()
            or manifest.get("one_shot_state_sha256") != expected_state_sha
        ):
            _add(blockers, "V3_MANIFEST_INVALID")

    return {
        "manifest_sha256": manifest_sha,
        "state_sha256": state_sha,
        "incident_sha256": incident_canonical,
    }


def validate_recovery(root: Path | str = REPO, *, canonical_root: Path | str | None = None) -> dict[str, Any]:
    """Validate only the outcome-free recovery contract rooted at ``root``."""

    filesystem_root = Path(root).resolve()
    identity_root = Path(canonical_root).resolve() if canonical_root is not None else filesystem_root
    blockers: list[str] = []
    v3_details = _validate_v3(filesystem_root, identity_root, blockers)
    v2_details = _validate_v2(filesystem_root, identity_root, blockers)
    return {
        "status": "RECOVERY_GOVERNANCE_READY" if not blockers else "BLOCKED",
        "blockers": sorted(set(blockers)),
        "research_id": V3_RESEARCH_ID,
        "candidate_id": CANDIDATE_ID,
        "recovery_mode": "EXECUTION_INCIDENT_RECOVERY",
        "economic_authorization": False,
        "claim_policy": EXPECTED_CLAIM_POLICY,
        "declared_search_scope": {
            "roots": [str(filesystem_root / value) for value in SEARCH_ROOTS],
            "filename_patterns": list(ECONOMIC_FILENAME_PATTERNS),
            "canonical_output_directory": str(filesystem_root / V2_OUTPUT_DIR_REL),
        },
        "v2": v2_details,
        "v3": v3_details,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", default=str(REPO))
    parser.add_argument("--json", action="store_true", help="emit the machine-readable result")
    args = parser.parse_args(argv)
    result = validate_recovery(args.repo_root)
    print(json.dumps(result, sort_keys=True, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
