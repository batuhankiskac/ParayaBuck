#!/usr/bin/env python3
"""Fail-closed execution gate for the R46 v3 incident-recovery one-shot.

The production entrypoint is deliberately authorization-gated.  The economic
implementation is the frozen v2 module's pure package/engine choreography;
this module owns only recovery provenance, authorization, and transitions.
TEST_ONLY helpers use synthetic fixtures and are never a production fallback.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, cast

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scripts.research import r46_treasury_auction_unwind_tlt_v2_2026_09_10 as frozen_v2

V2_RESEARCH_ID = "r46_treasury_auction_unwind_tlt_v2_2026_09_10"
V3_RESEARCH_ID = "r46_treasury_auction_unwind_tlt_v3_recovery_2026_09_14"
CANDIDATE_ID = "R46"
RECOVERY_GENERATION = 1
CLAIM_POLICY = "RETROSPECTIVE_DESCRIPTIVE_ONLY_ORIGIN_UNPROVEN"
PARENT_GOVERNANCE_COMMIT = "640097e30db857a011caa5d623e08541b2793cc8"
V2_SOURCE_COMMIT = "e10816ec08cc6b9c36000634c0e10a138d30f67e"
V2_FREEZE_COMMIT = "ea8e86eabbf499977aadf8ebc81ccbb89502aab4"
V2_GOVERNANCE_SHA256 = "92f7ecd5b7f6cdd2428ee221c3b4891f10d5d7ab6f16a5fcb2be952753557c35"
V2_PACKAGE_SHA256 = "c67cfdfeebc4cbc0847d5e2e6fbdebdf6928e04b3e5103606eb8bd0c3d03f6c6"
V2_STATISTICS_SHA256 = "21ebb2e453bd3587388fa800919d5ca24eb9fab4d29b21c106d8e1ce8a015331"
V2_FAMILY_SHA256 = "eeda04d795d099e7571bdbeae0ccaa2dc55141ec2f15d1f8afdc507d60b9acae"
V2_RESERVED_SHA256 = "434b5ef71015d132516474a11078c4e5866d3c91e7d265a7f7c3b20d655141e4"
V2_RESERVED_TRANSITION_SHA256 = "040d13dcba81e3d52e7d105aea9250a3c565e7b87e9b6af5c44e83b0dec28405"
V3_STATE_SHA256 = "59148f1e32af95ed52bbcea9db802470d7b9b5156a58ba7ffe25d8de824b6ac7"
V3_STATE_CANONICAL_SHA256 = "0165ea19b68a5e913927cb5016a703ccc9d2be73ba01a7515372727b0bdb9421"
V3_GOVERNANCE_SHA256 = "bc49ab40e00e21deb8b4ef305c18400665d4154d4c1077a8a31dbc7da452c3db"
V3_INCIDENT_SHA256 = "bdd668747423960faddbb31982566e6c49fcc3b64fd32bb29c40775a621acb8b"
V2_INCIDENT_PHYSICAL_SHA256 = "7be069ba5a29a79313bdf77b3355ff5d5fdf319b624a3d5b6da30291609d74c0"
V2_GATE_PHYSICAL_SHA256 = "b05afe121048291edce06d54ef001363470ea3cc2e16ce58eb44f35ab9d972bc"
V2_GATE_CANONICAL_SHA256 = "6307caa29e8a2179d36a5f2792573bb7307f22a317fa55071b348eb51ef2639c"

V2_STATE_REL = Path("data/research_manifests/r46_treasury_auction_unwind_tlt_v2_2026_09_10_one_shot_state.json")
V2_RESERVED_REL = Path("data/research_manifests/r46_treasury_auction_unwind_tlt_v2_2026_09_10_one_shot_state.json.reserved.json")
V2_CONSUMED_REL = Path("data/research_manifests/r46_treasury_auction_unwind_tlt_v2_2026_09_10_one_shot_state.json.consumed.json")
V2_ABORTED_REL = Path("data/research_manifests/r46_treasury_auction_unwind_tlt_v2_2026_09_10_one_shot_state.json.aborted_consumed.json")
V2_GOVERNANCE_REL = Path("data/research_manifests/r46_treasury_auction_unwind_tlt_v2_2026_09_10.json")
V2_PACKAGE_REL = Path("data/research_manifests/r46_treasury_auction_unwind_tlt_v2_2026_09_10_data_package.json")
V2_STATISTICS_REL = Path("data/research_manifests/r46_treasury_auction_unwind_tlt_v2_2026_09_10_statistics_plan.json")
V2_FAMILY_REL = Path("data/research_manifests/r46_treasury_auction_unwind_tlt_v2_2026_09_10_family_registry.json")
V2_PREFLIGHT_REL = Path("data/research_receipts/r46_v2_preflight_2026_09_14.json")
V2_INCIDENT_REL = Path("data/research_receipts/r46_v2_reservation_incident_2026_09_14.json")
V2_OUTPUT_DIR_REL = Path("data/research/runs/r46_treasury_auction_unwind_tlt_v2_final_2026_09_14")
V3_STATE_REL = Path("data/research_manifests/r46_treasury_auction_unwind_tlt_v3_recovery_2026_09_14_one_shot_state.json")
V3_GOVERNANCE_REL = Path("data/research_manifests/r46_treasury_auction_unwind_tlt_v3_recovery_2026_09_14.json")
V3_SCHEMA_REL = Path("data/research_manifests/r46_treasury_auction_unwind_tlt_v3_recovery_2026_09_14.schema.json")
V3_CONTRACT_REL = Path("data/research_manifests/r46_treasury_auction_unwind_tlt_v3_recovery_2026_09_14_execution_contract.json")
V3_CONTRACT_SCHEMA_REL = Path("data/research_manifests/r46_treasury_auction_unwind_tlt_v3_recovery_2026_09_14_execution_contract.schema.json")
V3_PREFLIGHT_REL = Path("data/research_receipts/r46_v3_recovery_preflight_2026_09_14.json")
V3_AUTHORIZATION_REL = Path("data/research_receipts/r46_v3_execution_authorization_2026_09_14.json")
V3_AUTHORIZATION_SCHEMA_REL = Path("data/research_manifests/r46_v3_recovery_execution_authorization.schema.json")
V3_RUNNER_REL = Path("scripts/research/r46_treasury_auction_unwind_tlt_v3_recovery_2026_09_14.py")
V3_VERIFIER_REL = Path("scripts/research/verify_r46_treasury_auction_unwind_tlt_v3_recovery_2026_09_14.py")
V3_PREFLIGHT_IMPL_REL = Path("scripts/research/r46_v3_execution_preflight_2026_09_14.py")
V3_OUTPUT_DIR_REL = Path("data/research/runs/r46_treasury_auction_unwind_tlt_v3_recovery_final_2026_09_14")

TRANSITION_SCHEMA = "r46-recovery-one-shot-transition-v3"
AUTHORIZATION_SCHEMA = "r46-v3-recovery-execution-authorization-v1"
PREFLIGHT_SCHEMA = "r46-v3-recovery-preflight-receipt-v1"
ECONOMIC_RUN_SCHEMA = "r46-v3-recovery-economic-run-v1"
ECONOMIC_LEDGER_STATUS = "R46_V3_RECOVERY_ECONOMIC_RUN"
FREEZE_COMMIT_SENTINEL = "CURRENT_HEAD_AT_FREEZE"
ONE_SHOT_RESERVED = "RESERVED"
ONE_SHOT_CONSUMED = "CONSUMED"
ONE_SHOT_ABORTED_CONSUMED = "ABORTED_CONSUMED"
_HEX64 = set("0123456789abcdef")
_EPS = 1e-9


def _canonical_bytes(value: Mapping[str, Any]) -> bytes:
    return json.dumps(dict(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _canonical_hash(payload: Mapping[str, Any], field: str, *, nested: bool = True) -> str:
    value = copy.deepcopy(dict(payload))
    if nested:
        integrity = value.get("integrity")
        if not isinstance(integrity, dict):
            raise ValueError("integrity_object_required")
        integrity.pop(field, None)
    else:
        value.pop(field, None)
    return hashlib.sha256(_canonical_bytes(cast(Mapping[str, Any], value))).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _published_payload_hash(payload: Mapping[str, Any]) -> str:
    encoded = (json.dumps(dict(payload), indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"json_object_required:{path}")
    return value


def _valid_hex64(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and set(value) <= _HEX64


def _valid_hex40(value: object) -> bool:
    return isinstance(value, str) and len(value) == 40 and set(value) <= _HEX64


def _path(root: Path, relative: Path) -> Path:
    return (root / relative).resolve()


def _relative_or_absolute(path: Path, root: Path, *, test_only: bool) -> str:
    if test_only:
        return str(path.resolve())
    try:
        return str(path.resolve().relative_to(root.resolve()))
    except ValueError as exc:
        raise RuntimeError("BLOCKED_V3_SOURCE_PATH") from exc


def _exclusive_bytes(path: Path, encoded: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = -1
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        if descriptor != -1:
            os.close(descriptor)
    directory_descriptor = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory_descriptor)
    finally:
        os.close(directory_descriptor)


def _exclusive_json(path: Path, payload: Mapping[str, Any]) -> None:
    encoded = (json.dumps(dict(payload), sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode(
        "utf-8"
    )
    _exclusive_bytes(path, encoded)


def _state_sidecar(state_path: Path, kind: str) -> Path:
    return state_path.with_name(f"{state_path.name}.{kind}.json")


def _canonical_artifact_hash(path: Path, field: str = "artifact_sha256") -> str:
    payload = _load_json(path)
    integrity = payload.get("integrity")
    if not isinstance(integrity, Mapping) or not isinstance(integrity.get(field), str):
        raise RuntimeError("ONE_SHOT_STATE_HASH_INVALID")
    computed = _canonical_hash(payload, field, nested=True)
    if computed != integrity.get(field):
        raise RuntimeError("ONE_SHOT_STATE_HASH_INVALID")
    return computed


def _load_recovery_state(path: Path) -> dict[str, Any]:
    state = _load_json(path)
    if state.get("schema_version") != "r46-recovery-one-shot-state-v3":
        raise RuntimeError("ONE_SHOT_STATE_INVALID")
    if state.get("research_id") != V3_RESEARCH_ID or state.get("candidate_id") != CANDIDATE_ID:
        raise RuntimeError("ONE_SHOT_STATE_INVALID")
    if state.get("recovery_generation") != RECOVERY_GENERATION:
        raise RuntimeError("ONE_SHOT_STATE_INVALID")
    if _canonical_artifact_hash(path) != state.get("integrity", {}).get("artifact_sha256"):
        raise RuntimeError("ONE_SHOT_STATE_HASH_INVALID")
    if state.get("status") != "SEALED_UNUSED":
        raise RuntimeError("ONE_SHOT_STATE_INVALID")
    return state


def _load_transition(path: Path, state_path: Path, *, require_crosslinks: bool = True) -> dict[str, Any]:
    transition = _load_json(path)
    if transition.get("schema_version") != TRANSITION_SCHEMA:
        raise RuntimeError("ONE_SHOT_TRANSITION_INVALID")
    expected = {
        _state_sidecar(state_path, "reserved"): ONE_SHOT_RESERVED,
        _state_sidecar(state_path, "consumed"): ONE_SHOT_CONSUMED,
        _state_sidecar(state_path, "aborted_consumed"): ONE_SHOT_ABORTED_CONSUMED,
    }.get(path)
    if expected is None or transition.get("status") != expected:
        raise RuntimeError("ONE_SHOT_TRANSITION_STATUS_INVALID")
    if transition.get("state_path") != str(state_path):
        raise RuntimeError("ONE_SHOT_TRANSITION_CROSSLINK_INVALID")
    if transition.get("state_sha256") != _sha256_file(state_path):
        raise RuntimeError("ONE_SHOT_TRANSITION_STATE_HASH_INVALID")
    if transition.get("state_canonical_sha256") != _canonical_artifact_hash(state_path):
        raise RuntimeError("ONE_SHOT_TRANSITION_STATE_CANONICAL_HASH_INVALID")
    integrity = transition.get("integrity")
    if not isinstance(integrity, Mapping) or integrity.get("transition_sha256") != _canonical_hash(transition, "transition_sha256"):
        raise RuntimeError("ONE_SHOT_TRANSITION_HASH_INVALID")
    required = (
        "preflight_receipt_sha256",
        "authorization_sha256",
        "v3_governance_sha256",
        "v3_execution_contract_sha256",
        "parent_v2_governance_sha256",
        "parent_v2_package_sha256",
        "v2_reservation_physical_sha256",
        "v2_reservation_transition_sha256",
    )
    if require_crosslinks and expected != ONE_SHOT_ABORTED_CONSUMED and any(
        not _valid_hex64(transition.get(key)) for key in required
    ):
        raise RuntimeError("ONE_SHOT_TRANSITION_CROSSLINK_REQUIRED")
    if expected == ONE_SHOT_CONSUMED:
        if not _valid_hex64(transition.get("economic_output_bundle_sha256")) or not _valid_hex64(
            transition.get("reserved_transition_sha256")
        ):
            raise RuntimeError("ONE_SHOT_TRANSITION_CROSSLINK_REQUIRED")
        output_path = transition.get("output_path")
        receipt_path = transition.get("receipt_path")
        if (
            not isinstance(output_path, str)
            or not Path(output_path).is_absolute()
            or not isinstance(receipt_path, str)
            or not Path(receipt_path).is_absolute()
        ):
            raise RuntimeError("ONE_SHOT_TRANSITION_PATH_INVALID")
        if Path(receipt_path).parent != Path(output_path):
            raise RuntimeError("ONE_SHOT_TRANSITION_PATH_INVALID")
        reserved_path = _state_sidecar(state_path, "reserved")
        if not reserved_path.is_file():
            raise RuntimeError("ONE_SHOT_TRANSITION_CHAIN_INVALID")
        reserved = _load_transition(reserved_path, state_path, require_crosslinks=require_crosslinks)
        if transition.get("reserved_transition_sha256") != reserved.get("integrity", {}).get("transition_sha256"):
            raise RuntimeError("ONE_SHOT_TRANSITION_CHAIN_INVALID")
    return transition


def recovery_one_shot_status(state_path: Path | str, *, require_crosslinks: bool = True) -> str:
    path = Path(state_path).resolve()
    _load_recovery_state(path)
    for kind, status in (
        ("aborted_consumed", ONE_SHOT_ABORTED_CONSUMED),
        ("consumed", ONE_SHOT_CONSUMED),
        ("reserved", ONE_SHOT_RESERVED),
    ):
        sidecar = _state_sidecar(path, kind)
        if sidecar.is_file() or sidecar.is_symlink():
            transition = _load_transition(sidecar, path, require_crosslinks=require_crosslinks)
            return str(transition.get("status", status))
    return "SEALED_UNUSED"


def reserve_recovery_one_shot(
    state_path: Path | str,
    *,
    preflight_receipt_sha256: str,
    authorization_sha256: str,
    v3_governance_sha256: str,
    v3_execution_contract_sha256: str,
    parent_v2_governance_sha256: str,
    parent_v2_package_sha256: str,
    v2_reservation_physical_sha256: str,
    v2_reservation_transition_sha256: str,
) -> dict[str, object]:
    """Create the only legal v3 reservation transition, exclusively."""
    path = Path(state_path).resolve()
    _load_recovery_state(path)
    required_values = (
        preflight_receipt_sha256,
        authorization_sha256,
        v3_governance_sha256,
        v3_execution_contract_sha256,
        parent_v2_governance_sha256,
        parent_v2_package_sha256,
        v2_reservation_physical_sha256,
        v2_reservation_transition_sha256,
    )
    if any(not _valid_hex64(value) for value in required_values):
        raise RuntimeError("ONE_SHOT_TRANSITION_CROSSLINK_REQUIRED")
    if _state_sidecar(path, "consumed").exists() or _state_sidecar(path, "aborted_consumed").exists():
        raise RuntimeError("ONE_SHOT_ALREADY_CONSUMED")
    if _state_sidecar(path, "reserved").exists():
        raise RuntimeError("ONE_SHOT_ALREADY_RESERVED")
    payload: dict[str, Any] = {
        "schema_version": TRANSITION_SCHEMA,
        "state_path": str(path),
        "state_sha256": _sha256_file(path),
        "state_canonical_sha256": _canonical_artifact_hash(path),
        "status": ONE_SHOT_RESERVED,
        "preflight_receipt_sha256": preflight_receipt_sha256,
        "authorization_sha256": authorization_sha256,
        "v3_governance_sha256": v3_governance_sha256,
        "v3_execution_contract_sha256": v3_execution_contract_sha256,
        "parent_v2_governance_sha256": parent_v2_governance_sha256,
        "parent_v2_package_sha256": parent_v2_package_sha256,
        "v2_reservation_physical_sha256": v2_reservation_physical_sha256,
        "v2_reservation_transition_sha256": v2_reservation_transition_sha256,
        "integrity": {"transition_sha256": ""},
    }
    payload["integrity"]["transition_sha256"] = _canonical_hash(payload, "transition_sha256")
    try:
        _exclusive_json(_state_sidecar(path, "reserved"), payload)
    except FileExistsError:
        raise RuntimeError("ONE_SHOT_ALREADY_RESERVED") from None
    return {"status": ONE_SHOT_RESERVED, "state_path": str(path)}


def consume_recovery_one_shot(
    state_path: Path | str,
    *,
    output_path: Path | str,
    receipt_path: Path | str,
    economic_output_bundle_sha256: str,
) -> dict[str, object]:
    """Create the irreversible CONSUMED transition after output publication."""
    path = Path(state_path).resolve()
    _load_recovery_state(path)
    if not _valid_hex64(economic_output_bundle_sha256):
        raise ValueError("output_bundle_sha256_invalid")
    if _state_sidecar(path, "consumed").exists() or _state_sidecar(path, "aborted_consumed").exists():
        raise RuntimeError("ONE_SHOT_ALREADY_CONSUMED")
    reserved_path = _state_sidecar(path, "reserved")
    if not reserved_path.is_file():
        raise RuntimeError("ONE_SHOT_RESERVATION_REQUIRED")
    reserved = _load_transition(reserved_path, path)
    output = Path(output_path).resolve()
    receipt = Path(receipt_path).resolve()
    if not output.is_absolute() or not receipt.is_absolute() or receipt.parent != output:
        raise RuntimeError("ONE_SHOT_TRANSITION_PATH_INVALID")
    payload: dict[str, Any] = {
        "schema_version": TRANSITION_SCHEMA,
        "state_path": str(path),
        "state_sha256": _sha256_file(path),
        "state_canonical_sha256": _canonical_artifact_hash(path),
        "status": ONE_SHOT_CONSUMED,
        "preflight_receipt_sha256": reserved["preflight_receipt_sha256"],
        "authorization_sha256": reserved["authorization_sha256"],
        "v3_governance_sha256": reserved["v3_governance_sha256"],
        "v3_execution_contract_sha256": reserved["v3_execution_contract_sha256"],
        "parent_v2_governance_sha256": reserved["parent_v2_governance_sha256"],
        "parent_v2_package_sha256": reserved["parent_v2_package_sha256"],
        "v2_reservation_physical_sha256": reserved["v2_reservation_physical_sha256"],
        "v2_reservation_transition_sha256": reserved["v2_reservation_transition_sha256"],
        "output_path": str(output),
        "receipt_path": str(receipt),
        "economic_output_bundle_sha256": economic_output_bundle_sha256,
        "reserved_transition_sha256": reserved["integrity"]["transition_sha256"],
        "integrity": {"transition_sha256": ""},
    }
    payload["integrity"]["transition_sha256"] = _canonical_hash(payload, "transition_sha256")
    try:
        _exclusive_json(_state_sidecar(path, "consumed"), payload)
    except FileExistsError:
        raise RuntimeError("ONE_SHOT_ALREADY_CONSUMED") from None
    return {
        "status": ONE_SHOT_CONSUMED,
        "state_path": str(path),
        "output_path": str(output),
        "economic_output_bundle_sha256": economic_output_bundle_sha256,
    }


def _resolve_file(root: Path, value: object, field: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise RuntimeError(f"BLOCKED_V3_{field.upper()}_PATH")
    candidate = Path(value)
    resolved = (candidate if candidate.is_absolute() else root / candidate).resolve()
    try:
        resolved.relative_to(root.resolve())
    except ValueError as exc:
        raise RuntimeError(f"BLOCKED_V3_{field.upper()}_PATH") from exc
    if not resolved.is_file():
        raise RuntimeError(f"BLOCKED_V3_{field.upper()}_PATH")
    return resolved


def _validate_contract(root: Path) -> tuple[dict[str, Any], str]:
    path = _path(root, V3_CONTRACT_REL)
    try:
        contract = _load_json(path)
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError("BLOCKED_V3_EXECUTION_CONTRACT") from exc
    if contract.get("schema_version") != "r46-v3-recovery-execution-contract-v1":
        raise RuntimeError("BLOCKED_V3_EXECUTION_CONTRACT")
    integrity = contract.get("integrity")
    if not isinstance(integrity, Mapping) or integrity.get("contract_sha256") != _canonical_hash(
        contract, "contract_sha256"
    ):
        raise RuntimeError("BLOCKED_V3_EXECUTION_CONTRACT")
    canonical = str(integrity["contract_sha256"])
    if _sha256_file(path) == "":
        raise RuntimeError("BLOCKED_V3_EXECUTION_CONTRACT")
    if contract.get("canonical_execution_root") != str(REPO):
        raise RuntimeError("BLOCKED_V3_EXECUTION_CONTRACT")
    if contract.get("frozen_output_directory") != str(REPO / V3_OUTPUT_DIR_REL):
        raise RuntimeError("BLOCKED_V3_EXECUTION_CONTRACT")
    if contract.get("parent_governance_commit") != PARENT_GOVERNANCE_COMMIT:
        raise RuntimeError("BLOCKED_V3_EXECUTION_CONTRACT")
    source_commit = contract.get("source_commit")
    if not isinstance(source_commit, str) or len(source_commit) != 40 or set(source_commit) - _HEX64:
        raise RuntimeError("BLOCKED_V3_EXECUTION_CONTRACT")
    freeze_commit = contract.get("freeze_commit")
    if freeze_commit != FREEZE_COMMIT_SENTINEL and (
        not isinstance(freeze_commit, str) or len(freeze_commit) != 40 or set(freeze_commit) - _HEX64
    ):
        raise RuntimeError("BLOCKED_V3_EXECUTION_CONTRACT")
    if freeze_commit == FREEZE_COMMIT_SENTINEL and contract.get("freeze_commit_binding") != "HEAD_AT_FREEZE":
        raise RuntimeError("BLOCKED_V3_EXECUTION_CONTRACT")
    if contract.get("v3_governance_canonical_sha256") != V3_GOVERNANCE_SHA256:
        raise RuntimeError("BLOCKED_V3_EXECUTION_CONTRACT")
    if contract.get("v2_package_canonical_sha256") != V2_PACKAGE_SHA256:
        raise RuntimeError("BLOCKED_V3_EXECUTION_CONTRACT")
    return contract, canonical


def _validate_preflight_receipt(path: Path, root: Path, contract: Mapping[str, Any]) -> tuple[dict[str, Any], str]:
    try:
        receipt = _load_json(path)
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError("BLOCKED_V3_EXECUTION_PREFLIGHT") from exc
    integrity = receipt.get("integrity")
    if (
        receipt.get("schema_version") != PREFLIGHT_SCHEMA
        or receipt.get("status") != "RECOVERY_PRE_RUN_OK"
        or receipt.get("blockers") != []
        or not isinstance(integrity, Mapping)
        or integrity.get("receipt_sha256") != _canonical_hash(receipt, "receipt_sha256")
    ):
        raise RuntimeError("BLOCKED_V3_EXECUTION_PREFLIGHT")
    if (
        receipt.get("research_id") != V3_RESEARCH_ID
        or receipt.get("candidate_id") != CANDIDATE_ID
        or receipt.get("recovery_generation") != RECOVERY_GENERATION
        or receipt.get("economic_authorization") is not False
        or receipt.get("runner_authorization_present") is not False
        or receipt.get("research_only") is not True
        or receipt.get("economic_execution") is not False
        or receipt.get("economic_run") is not False
        or receipt.get("paper_live_untouched") is not True
        or receipt.get("paper_or_live_activation") is not False
        or receipt.get("promotion") is not False
    ):
        raise RuntimeError("BLOCKED_V3_EXECUTION_PREFLIGHT")
    expected_contract = str(_path(root, V3_CONTRACT_REL).relative_to(root))
    if receipt.get("execution_contract_path") != expected_contract:
        raise RuntimeError("BLOCKED_V3_EXECUTION_PREFLIGHT")
    if receipt.get("execution_contract_sha256") != contract.get("integrity", {}).get("contract_sha256"):
        raise RuntimeError("BLOCKED_V3_EXECUTION_PREFLIGHT")
    if receipt.get("future_output_directory") != str(_path(root, V3_OUTPUT_DIR_REL)):
        raise RuntimeError("BLOCKED_V3_EXECUTION_PREFLIGHT")
    if receipt.get("source_commit") != contract.get("source_commit") or not _valid_hex40(receipt.get("freeze_commit")):
        raise RuntimeError("BLOCKED_V3_EXECUTION_PREFLIGHT")
    if contract.get("freeze_commit") != FREEZE_COMMIT_SENTINEL and receipt.get("freeze_commit") != contract.get(
        "freeze_commit"
    ):
        raise RuntimeError("BLOCKED_V3_EXECUTION_PREFLIGHT")
    if receipt.get("authorization_present") is not False:
        raise RuntimeError("BLOCKED_V3_EXECUTION_PREFLIGHT")
    return receipt, str(integrity["receipt_sha256"])


def _validate_authorization(path: Path, root: Path, receipt: Mapping[str, Any], contract: Mapping[str, Any]) -> tuple[dict[str, Any], str]:
    if not path.is_file():
        raise RuntimeError("BLOCKED_V3_EXECUTION_AUTHORIZATION")
    try:
        authorization = _load_json(path)
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError("BLOCKED_V3_EXECUTION_AUTHORIZATION") from exc
    integrity = authorization.get("integrity")
    valid = (
        authorization.get("schema_version") == AUTHORIZATION_SCHEMA
        and authorization.get("candidate_id") == CANDIDATE_ID
        and authorization.get("research_id") == V3_RESEARCH_ID
        and authorization.get("recovery_generation") == RECOVERY_GENERATION
        and authorization.get("authorized") is True
        and authorization.get("economic_execution_authorized") is True
        and authorization.get("preflight_receipt_sha256") == receipt.get("integrity", {}).get("receipt_sha256")
        and authorization.get("execution_contract_sha256") == contract.get("integrity", {}).get("contract_sha256")
        and authorization.get("source_commit") == contract.get("source_commit")
        and authorization.get("freeze_commit") == receipt.get("freeze_commit")
        and authorization.get("v3_state_canonical_sha256") == V3_STATE_CANONICAL_SHA256
        and authorization.get("v2_reservation_physical_sha256") == V2_RESERVED_SHA256
        and isinstance(authorization.get("authorization_nonce"), str)
        and bool(authorization.get("authorization_nonce"))
        and authorization.get("issued_for_output_directory") == str(_path(root, V3_OUTPUT_DIR_REL))
        and authorization.get("rerun_authorized") is False
        and authorization.get("parameter_change_authorized") is False
        and authorization.get("promotion_authorized") is False
        and isinstance(integrity, Mapping)
        and integrity.get("authorization_sha256") == _canonical_hash(authorization, "authorization_sha256")
    )
    if not valid:
        raise RuntimeError("BLOCKED_V3_EXECUTION_AUTHORIZATION")
    return authorization, str(integrity["authorization_sha256"])


def validate_execution_gate(
    preflight_path: Path | str,
    authorization_path: Path | str,
    *,
    repo_root: Path | str = REPO,
) -> dict[str, Any]:
    """Validate all persisted non-economic gates before reservation."""
    root = Path(repo_root).resolve()
    preflight = Path(preflight_path).resolve()
    authorization = Path(authorization_path).resolve()
    if preflight != _path(root, V3_PREFLIGHT_REL):
        raise RuntimeError("BLOCKED_V3_PRODUCTION_OVERRIDE")
    if authorization != _path(root, V3_AUTHORIZATION_REL):
        raise RuntimeError("BLOCKED_V3_PRODUCTION_OVERRIDE")
    from scripts.research.r46_v3_recovery_preflight_2026_09_14 import validate_recovery

    recovery = validate_recovery(root, canonical_root=root)
    if recovery.get("status") != "RECOVERY_GOVERNANCE_READY" or recovery.get("blockers") != []:
        raise RuntimeError("BLOCKED_V3_RECOVERY_GOVERNANCE")
    contract, contract_hash = _validate_contract(root)
    receipt, receipt_hash = _validate_preflight_receipt(preflight, root, contract)
    authorization_payload, authorization_hash = _validate_authorization(authorization, root, receipt, contract)
    state_path = _path(root, V3_STATE_REL)
    state = _load_recovery_state(state_path)
    status = recovery_one_shot_status(state_path)
    if status != "SEALED_UNUSED":
        if status == ONE_SHOT_RESERVED:
            raise RuntimeError("ONE_SHOT_ALREADY_RESERVED")
        raise RuntimeError("ONE_SHOT_ALREADY_CONSUMED")
    output = _path(root, V3_OUTPUT_DIR_REL)
    if output.exists() or output.is_symlink():
        raise RuntimeError("R46_OUTPUT_ALREADY_EXISTS")
    return {
        "root": root,
        "preflight_path": preflight,
        "preflight": receipt,
        "preflight_sha256": receipt_hash,
        "authorization_path": authorization,
        "authorization": authorization_payload,
        "authorization_sha256": authorization_hash,
        "contract": contract,
        "contract_sha256": contract_hash,
        "state_path": state_path,
        "state": state,
        "output_path": output,
        "governance_path": _path(root, V3_GOVERNANCE_REL),
        "governance_sha256": V3_GOVERNANCE_SHA256,
    }


def _default_statistics_pins() -> dict[str, object]:
    return {
        "statistics_plan_path": "TEST_ONLY_STATISTICS_PLAN",
        "statistics_plan_sha256": "a" * 64,
        "family_registry_path": "TEST_ONLY_FAMILY_REGISTRY",
        "family_registry_sha256": "b" * 64,
        "family_size": len(frozen_v2.STATISTIC_IDS),
        "reported_output_count": len(frozen_v2.STATISTIC_IDS),
        "inferential_family_size": len(frozen_v2.INFERENTIAL_STATISTIC_IDS),
        "inferential_ids": list(frozen_v2.INFERENTIAL_STATISTIC_IDS),
        "statistic_ids": list(frozen_v2.STATISTIC_IDS),
    }


def _tag_v3_economic_ledgers(value: object) -> None:
    """Retag only economic ledger status fields, preserving all values."""
    if isinstance(value, dict):
        if value.get("status") == frozen_v2.ECONOMIC_LEDGER_STATUS:
            value["status"] = ECONOMIC_LEDGER_STATUS
            if isinstance(value.get("ledger_sha256"), str):
                value["ledger_sha256"] = frozen_v2._canonical_hash(value, "ledger_sha256")
        for child in value.values():
            _tag_v3_economic_ledgers(child)
    elif isinstance(value, list):
        for child in value:
            _tag_v3_economic_ledgers(child)


def _coverage_status(statistics: Mapping[str, Any], timing_placebo: Mapping[str, Any]) -> tuple[str, list[str]]:
    coverage = cast(Mapping[str, Any], statistics["coverage_gates"])
    blockers: list[str] = []
    if int(coverage["event_episodes"]) < int(coverage["minimum_event_episodes"]):
        blockers.append("BLOCKED_INSUFFICIENT_EVENT_RATE")
    if int(coverage["holding_intervals"]) < int(coverage["minimum_holding_intervals"]):
        blockers.append("BLOCKED_INSUFFICIENT_HOLDING_INTERVALS")
    if float(coverage["missing_rate"]) > float(coverage["maximum_missing_rate"]) + _EPS:
        blockers.append("BLOCKED_MISSINGNESS")
    if float(coverage["capacity_suppression_rate"]) > float(coverage["maximum_capacity_suppression_rate"]) + _EPS:
        blockers.append("BLOCKED_CAPACITY_SUPPRESSION")
    if coverage["sixth_future_open_complete"] is not True or coverage["fifth_open_exit_complete"] is not True:
        blockers.append("BLOCKED_SIXTH_FUTURE_OPEN")
    if timing_placebo.get("status") != "REPORTED_NATIVE_ENGINE_DIAGNOSTIC_ONLY":
        blockers.append("BLOCKED_TIMING_PLACEBO_ENGINE_CELL")
    return ("PASS_RESEARCH_ONLY" if not blockers else "FAILED_VALID_FROZEN_TEST"), blockers


def _compute_payloads(
    *,
    package: Any,
    incumbent: Any,
    root: Path,
    state_path: Path,
    output_path: Path,
    manifest_sha256: str,
    preflight_sha256: str,
    authorization_sha256: str,
    contract_sha256: str,
    governance_sha256: str,
    incident_sha256: str,
    source_commit: str,
    freeze_commit: str,
    statistics_pins: Mapping[str, object],
    config: object | None,
    starting_equity: float = 100_000.0,
    test_only: bool,
) -> dict[str, Any]:
    """Run the frozen v2 pure computation with v3 provenance only."""
    dataset = frozen_v2.build_r46_market_dataset(package, incumbent)
    effective_config = frozen_v2._load_r46_config(config, repo_root=root, test_only=test_only)
    universe, universe_version, universe_status = frozen_v2._build_r46_universe(
        incumbent,
        config=effective_config,
        test_only=test_only,
    )
    if not test_only:
        if effective_config is None:
            raise RuntimeError("BLOCKED_CONFIG_SOURCE")
        if universe is None or universe_status != "PIT_V5_PRODUCT_UNIVERSE":
            raise RuntimeError("BLOCKED_PIT_SOURCE:universe")
    event_boundaries = tuple(
        sorted(
            {
                timestamp
                for schedule in package.schedules
                for timestamp in (schedule.entry_decision, schedule.exit_decision)
            }
        )
    )
    marker_timestamps = tuple(sorted(marker.timestamp for marker in package.markers))
    multipliers = tuple(float(value) for value in frozen_v2.COST_MULTIPLIERS)
    cells: list[dict[str, object]] = []
    ledger_cells: list[dict[str, object]] = []
    for multiplier in multipliers:
        candidate_strategy = frozen_v2.R46OverlayStrategy(package.schedules)
        control_strategy = frozen_v2._R46IncumbentStrategy(dict(frozen_v2.FROZEN_V3_PARAMETERS))
        candidate_result = frozen_v2._run_r46_engine(
            dataset,
            candidate_strategy,
            cost_multiplier=multiplier,
            starting_equity=starting_equity,
            config=effective_config,
            universe=universe,
            universe_version=universe_version,
            event_boundaries=event_boundaries,
            marker_timestamps=marker_timestamps,
            terminal_map=incumbent.terminal_map,
        )
        control_result = frozen_v2._run_r46_engine(
            dataset,
            control_strategy,
            cost_multiplier=multiplier,
            starting_equity=starting_equity,
            config=effective_config,
            universe=universe,
            universe_version=universe_version,
            marker_timestamps=marker_timestamps,
            terminal_map=incumbent.terminal_map,
        )
        candidate_ledger = frozen_v2._economic_ledger_payload(
            dataset,
            candidate_result,
            strategy=candidate_strategy,
            package=package,
            cost_multiplier=multiplier,
            arm="candidate",
            manifest_sha256=manifest_sha256,
            gate_sha256=preflight_sha256,
            universe_status=universe_status,
            universe_version=universe_version,
            source_paths=incumbent.source_paths,
            config=effective_config,
        )
        control_ledger = frozen_v2._economic_ledger_payload(
            dataset,
            control_result,
            strategy=None,
            package=package,
            cost_multiplier=multiplier,
            arm="control",
            manifest_sha256=manifest_sha256,
            gate_sha256=preflight_sha256,
            universe_status=universe_status,
            universe_version=universe_version,
            source_paths=incumbent.source_paths,
            config=effective_config,
        )
        for ledger in (candidate_ledger, control_ledger):
            ledger.update(
                {
                    "v3_governance_canonical_sha256": governance_sha256,
                    "execution_contract_sha256": contract_sha256,
                    "incident_receipt_canonical_sha256": incident_sha256,
                    "authorization_sha256": authorization_sha256,
                    "source_commit": source_commit,
                    "freeze_commit": freeze_commit,
                    "recovery_generation": RECOVERY_GENERATION,
                }
            )
        cells.append(
            {
                "cost_multiplier": multiplier,
                "candidate_metrics": frozen_v2._result_metrics(candidate_result),
                "control_metrics": frozen_v2._result_metrics(control_result),
            }
        )
        ledger_cells.append(
            {
                "cost_multiplier": multiplier,
                "candidate": candidate_ledger,
                "control": control_ledger,
            }
        )
    timing_placebo_cell = frozen_v2._run_timing_placebo_cell(
        package,
        incumbent,
        config=effective_config,
        universe=universe,
        universe_version=universe_version,
        universe_status=universe_status,
        manifest_sha256=manifest_sha256,
        gate_sha256=preflight_sha256,
        source_paths=incumbent.source_paths,
    )
    statistics = frozen_v2._build_statistics(
        package,
        [
            {
                **cell,
                "candidate_ledger": ledger_cells[index]["candidate"],
                "control_ledger": ledger_cells[index]["control"],
            }
            for index, cell in enumerate(cells)
        ],
        timing_placebo_cell=timing_placebo_cell,
    )
    statistics["statistics_pins"] = copy.deepcopy(dict(statistics_pins))
    status, coverage_blockers = _coverage_status(statistics, timing_placebo_cell)
    coverage = cast(Mapping[str, Any], statistics["coverage_gates"])
    statistics["coverage_gates"] = {
        **coverage,
        "status": "PASS" if not coverage_blockers else "FAILED_VALID_FROZEN_TEST",
        "blockers": coverage_blockers,
    }
    results_payload: dict[str, Any] = {
        "schema_version": ECONOMIC_RUN_SCHEMA,
        "status": status,
        "research_id": V3_RESEARCH_ID,
        "candidate_id": CANDIDATE_ID,
        "recovery_generation": RECOVERY_GENERATION,
        "claim_policy": CLAIM_POLICY,
        "parent_research_id": V2_RESEARCH_ID,
        "economic_execution": True,
        "economic_run": True,
        "research_only": True,
        "outcome_artifacts_present": True,
        "native_one_ledger": True,
        "shared_account": True,
        "separate_sleeve_blend": False,
        "paper_live_untouched": True,
        "paper_or_live_activation": False,
        "promotion": False,
        "integration_candidate": False,
        "auto_promotion": False,
        "no_auto_promotion": True,
        "v3_governance_canonical_sha256": governance_sha256,
        "execution_contract_sha256": contract_sha256,
        "incident_receipt_canonical_sha256": incident_sha256,
        "v3_state_canonical_sha256": _canonical_artifact_hash(state_path),
        "parent_v2_reservation_physical_sha256": V2_RESERVED_SHA256,
        "parent_v2_reservation_transition_sha256": V2_RESERVED_TRANSITION_SHA256,
        "parent_v2_governance_canonical_sha256": V2_GOVERNANCE_SHA256,
        "parent_v2_package_canonical_sha256": V2_PACKAGE_SHA256,
        "manifest_canonical_sha256": governance_sha256,
        "package_canonical_sha256": package.package_canonical_sha256,
        "preflight_receipt_sha256": preflight_sha256,
        "authorization_sha256": authorization_sha256,
        "governance_manifest_path": _relative_or_absolute(root / V3_GOVERNANCE_REL, root, test_only=test_only),
        "execution_contract_path": _relative_or_absolute(root / V3_CONTRACT_REL, root, test_only=test_only),
        "data_package_manifest_path": _relative_or_absolute(package.package_path, root, test_only=test_only),
        "preflight_receipt_path": _relative_or_absolute(root / V3_PREFLIGHT_REL, root, test_only=test_only),
        "authorization_path": _relative_or_absolute(root / V3_AUTHORIZATION_REL, root, test_only=test_only),
        "incident_receipt_path": _relative_or_absolute(root / V2_INCIDENT_REL, root, test_only=test_only),
        "source_commit": source_commit,
        "freeze_commit": freeze_commit,
        "one_shot_state_path": _relative_or_absolute(state_path, root, test_only=test_only),
        "one_shot_state_sha256": _sha256_file(state_path),
        "canonical_output_directory": str(output_path),
        "dataset_digest": dataset.content_digest(),
        "source_paths": copy.deepcopy(
            cast(Mapping[str, object], cast(Mapping[str, Any], ledger_cells[0]["candidate"]).get("source_paths", {}))
        ),
        "native_engine": "TerminalAwareResearchEngine",
        "engine_invocations": len(cells) * 2 + 2,
        "cost_multipliers": list(multipliers),
        "statistics_pins": copy.deepcopy(dict(statistics_pins)),
        "cells": cells,
        "statistics": statistics,
        "no_best_cell_selection": True,
        "output_files": {
            "results": "results.json",
            "ledger": "ledger.json",
            "report": "report.json",
            "run_receipt": "run_receipt.json",
        },
    }
    ledger_payload: dict[str, Any] = {
        "schema_version": f"{ECONOMIC_RUN_SCHEMA}:ledger",
        "status": ECONOMIC_LEDGER_STATUS,
        "research_id": V3_RESEARCH_ID,
        "candidate_id": CANDIDATE_ID,
        "recovery_generation": RECOVERY_GENERATION,
        "claim_policy": CLAIM_POLICY,
        "parent_research_id": V2_RESEARCH_ID,
        "economic_run": True,
        "research_only": True,
        "economic_execution": True,
        "outcome_artifacts_present": True,
        "native_one_ledger": True,
        "shared_account": True,
        "separate_sleeve_blend": False,
        "paper_live_untouched": True,
        "paper_or_live_activation": False,
        "promotion": False,
        "integration_candidate": False,
        "auto_promotion": False,
        "no_auto_promotion": True,
        "v3_governance_canonical_sha256": governance_sha256,
        "execution_contract_sha256": contract_sha256,
        "incident_receipt_canonical_sha256": incident_sha256,
        "v3_state_canonical_sha256": _canonical_artifact_hash(state_path),
        "parent_v2_reservation_physical_sha256": V2_RESERVED_SHA256,
        "parent_v2_reservation_transition_sha256": V2_RESERVED_TRANSITION_SHA256,
        "parent_v2_governance_canonical_sha256": V2_GOVERNANCE_SHA256,
        "parent_v2_package_canonical_sha256": V2_PACKAGE_SHA256,
        "manifest_canonical_sha256": governance_sha256,
        "package_canonical_sha256": package.package_canonical_sha256,
        "preflight_receipt_sha256": preflight_sha256,
        "authorization_sha256": authorization_sha256,
        "canonical_output_directory": str(output_path),
        "dataset_digest": dataset.content_digest(),
        "one_shot_state_path": _relative_or_absolute(state_path, root, test_only=test_only),
        "one_shot_state_sha256": _sha256_file(state_path),
        "governance_manifest_path": _relative_or_absolute(root / V3_GOVERNANCE_REL, root, test_only=test_only),
        "execution_contract_path": _relative_or_absolute(root / V3_CONTRACT_REL, root, test_only=test_only),
        "data_package_manifest_path": _relative_or_absolute(package.package_path, root, test_only=test_only),
        "preflight_receipt_path": _relative_or_absolute(root / V3_PREFLIGHT_REL, root, test_only=test_only),
        "authorization_path": _relative_or_absolute(root / V3_AUTHORIZATION_REL, root, test_only=test_only),
        "source_commit": source_commit,
        "freeze_commit": freeze_commit,
        "source_paths": copy.deepcopy(
            cast(Mapping[str, object], cast(Mapping[str, Any], ledger_cells[0]["candidate"]).get("source_paths", {}))
        ),
        "native_engine": "TerminalAwareResearchEngine",
        "engine_invocations": len(cells) * 2 + 2,
        "statistics_pins": copy.deepcopy(dict(statistics_pins)),
        "cells": ledger_cells,
        "no_best_cell_selection": True,
    }
    report_payload: dict[str, Any] = {
        "schema_version": f"{ECONOMIC_RUN_SCHEMA}:report",
        "status": status,
        "research_id": V3_RESEARCH_ID,
        "candidate_id": CANDIDATE_ID,
        "recovery_generation": RECOVERY_GENERATION,
        "claim_policy": CLAIM_POLICY,
        "parent_research_id": V2_RESEARCH_ID,
        "economic_execution": True,
        "economic_run": True,
        "research_only": True,
        "outcome_artifacts_present": True,
        "native_one_ledger": True,
        "shared_account": True,
        "separate_sleeve_blend": False,
        "paper_live_untouched": True,
        "paper_or_live_activation": False,
        "promotion": False,
        "integration_candidate": False,
        "auto_promotion": False,
        "no_auto_promotion": True,
        "v3_governance_canonical_sha256": governance_sha256,
        "execution_contract_sha256": contract_sha256,
        "incident_receipt_canonical_sha256": incident_sha256,
        "v3_state_canonical_sha256": _canonical_artifact_hash(state_path),
        "parent_v2_reservation_physical_sha256": V2_RESERVED_SHA256,
        "parent_v2_governance_canonical_sha256": V2_GOVERNANCE_SHA256,
        "parent_v2_package_canonical_sha256": V2_PACKAGE_SHA256,
        "preflight_receipt_sha256": preflight_sha256,
        "authorization_sha256": authorization_sha256,
        "primary_estimand": statistics["primary_estimand"],
        "unchanged_incumbent_comparison": statistics["unchanged_incumbent_comparison"],
        "timing_placebo_plus_10_sessions": statistics["timing_placebo_plus_10_sessions"],
        "cost_outputs": statistics["cost_outputs"],
        "capacity": statistics["capacity"],
        "missingness": statistics["missingness"],
        "coverage_gates": statistics["coverage_gates"],
        "resampling": statistics["resampling"],
        "holm": statistics["holm"],
        "statistics_pins": copy.deepcopy(dict(statistics_pins)),
        "no_thresholds_or_selection": True,
    }
    _tag_v3_economic_ledgers(results_payload)
    _tag_v3_economic_ledgers(ledger_payload)
    _tag_v3_economic_ledgers(report_payload)
    return {
        "status": status,
        "results": results_payload,
        "ledger": ledger_payload,
        "report": report_payload,
        "dataset": dataset,
        "statistics_pins": dict(statistics_pins),
    }


def _build_run_receipt(
    *,
    payloads: Mapping[str, Any],
    output_path: Path,
    state_path: Path,
    root: Path,
    preflight_sha256: str,
    authorization_sha256: str,
    contract_sha256: str,
    governance_sha256: str,
    incident_sha256: str,
    package_sha256: str,
    source_commit: str,
    freeze_commit: str,
    result_sha256: str,
    ledger_sha256: str,
    report_sha256: str,
    test_only: bool,
) -> dict[str, Any]:
    bundle_sha256 = hashlib.sha256(
        _canonical_bytes(
            {
                "results_sha256": result_sha256,
                "ledger_sha256": ledger_sha256,
                "report_sha256": report_sha256,
            }
        )
    ).hexdigest()
    receipt: dict[str, Any] = {
        "schema_version": f"{ECONOMIC_RUN_SCHEMA}:receipt",
        "status": payloads["status"],
        "research_id": V3_RESEARCH_ID,
        "candidate_id": CANDIDATE_ID,
        "recovery_generation": RECOVERY_GENERATION,
        "claim_policy": CLAIM_POLICY,
        "parent_research_id": V2_RESEARCH_ID,
        "manifest_canonical_sha256": governance_sha256,
        "package_canonical_sha256": package_sha256,
        "preflight_receipt_sha256": preflight_sha256,
        "authorization_sha256": authorization_sha256,
        "execution_contract_sha256": contract_sha256,
        "v3_governance_canonical_sha256": governance_sha256,
        "incident_receipt_canonical_sha256": incident_sha256,
        "v3_state_canonical_sha256": _canonical_artifact_hash(state_path),
        "parent_v2_reservation_physical_sha256": V2_RESERVED_SHA256,
        "parent_v2_reservation_transition_sha256": V2_RESERVED_TRANSITION_SHA256,
        "parent_v2_governance_canonical_sha256": V2_GOVERNANCE_SHA256,
        "parent_v2_package_canonical_sha256": V2_PACKAGE_SHA256,
        "governance_manifest_path": _relative_or_absolute(root / V3_GOVERNANCE_REL, root, test_only=test_only),
        "execution_contract_path": _relative_or_absolute(root / V3_CONTRACT_REL, root, test_only=test_only),
        "data_package_manifest_path": _relative_or_absolute(root / V2_PACKAGE_REL, root, test_only=test_only),
        "preflight_receipt_path": _relative_or_absolute(root / V3_PREFLIGHT_REL, root, test_only=test_only),
        "authorization_path": _relative_or_absolute(root / V3_AUTHORIZATION_REL, root, test_only=test_only),
        "incident_receipt_path": _relative_or_absolute(root / V2_INCIDENT_REL, root, test_only=test_only),
        "source_commit": source_commit,
        "freeze_commit": freeze_commit,
        "runner_sha256": _sha256_file(Path(__file__).resolve()),
        "verifier_sha256": _sha256_file(root / V3_VERIFIER_REL)
        if (root / V3_VERIFIER_REL).is_file()
        else _sha256_file(Path(__file__).resolve()),
        "source_sha256": _sha256_file(REPO / "src/parayabuck/backtest/portfolio_engine.py"),
        "runner_path": _relative_or_absolute(Path(__file__), root, test_only=test_only),
        "verifier_path": _relative_or_absolute(root / V3_VERIFIER_REL, root, test_only=test_only),
        "source_path": _relative_or_absolute(REPO / "src/parayabuck/backtest/portfolio_engine.py", root, test_only=True),
        "test_only": test_only,
        "one_shot": {
            "state_path": _relative_or_absolute(state_path, root, test_only=test_only),
            "status": ONE_SHOT_CONSUMED,
            "output_sha256": bundle_sha256,
        },
        "output_directory": str(output_path),
        "outputs": {
            "results_sha256": result_sha256,
            "ledger_sha256": ledger_sha256,
            "report_sha256": report_sha256,
            "bundle_sha256": bundle_sha256,
        },
        "cost_multipliers": [1.0, 2.0, 3.0],
        "statistics_pins": copy.deepcopy(payloads["statistics_pins"]),
        "no_best_cell_selection": True,
        "economic_execution": True,
        "economic_run": True,
        "research_only": True,
        "outcome_artifacts_present": True,
        "native_one_ledger": True,
        "shared_account": True,
        "separate_sleeve_blend": False,
        "paper_live_untouched": True,
        "paper_or_live_activation": False,
        "promotion": False,
        "integration_candidate": False,
        "auto_promotion": False,
        "no_auto_promotion": True,
        "receipt_sha256": "",
    }
    receipt["receipt_sha256"] = _canonical_hash(receipt, "receipt_sha256", nested=False)
    return receipt


def _publish_bundle(
    *,
    payloads: Mapping[str, Any],
    output_path: Path,
    state_path: Path,
    root: Path,
    preflight_sha256: str,
    authorization_sha256: str,
    contract_sha256: str,
    governance_sha256: str,
    incident_sha256: str,
    package_sha256: str,
    source_commit: str,
    freeze_commit: str,
    test_only: bool,
) -> dict[str, Any]:
    output_path.mkdir(parents=True, exist_ok=True)
    result_sha256 = _write_exclusive_payload(output_path / "results.json", payloads["results"])
    ledger_sha256 = _write_exclusive_payload(output_path / "ledger.json", payloads["ledger"])
    report_sha256 = _write_exclusive_payload(output_path / "report.json", payloads["report"])
    bundle_sha256 = hashlib.sha256(
        _canonical_bytes(
            {
                "results_sha256": result_sha256,
                "ledger_sha256": ledger_sha256,
                "report_sha256": report_sha256,
            }
        )
    ).hexdigest()
    consume_recovery_one_shot(
        state_path,
        output_path=output_path,
        receipt_path=output_path / "run_receipt.json",
        economic_output_bundle_sha256=bundle_sha256,
    )
    receipt = _build_run_receipt(
        payloads=payloads,
        output_path=output_path,
        state_path=state_path,
        root=root,
        preflight_sha256=preflight_sha256,
        authorization_sha256=authorization_sha256,
        contract_sha256=contract_sha256,
        governance_sha256=governance_sha256,
        incident_sha256=incident_sha256,
        package_sha256=package_sha256,
        source_commit=source_commit,
        freeze_commit=freeze_commit,
        result_sha256=result_sha256,
        ledger_sha256=ledger_sha256,
        report_sha256=report_sha256,
        test_only=test_only,
    )
    receipt_file_sha256 = _write_exclusive_payload(output_path / "run_receipt.json", receipt)
    return {
        "status": payloads["status"],
        "results": payloads["results"],
        "ledger": payloads["ledger"],
        "report": payloads["report"],
        "run_receipt": receipt,
        "output_dir": str(output_path),
        "run_receipt_file_sha256": receipt_file_sha256,
        "output_bundle_sha256": bundle_sha256,
    }


def _write_exclusive_payload(path: Path, payload: Mapping[str, Any]) -> str:
    encoded = (json.dumps(dict(payload), indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")
    _exclusive_bytes(path, encoded)
    return hashlib.sha256(encoded).hexdigest()


def _incident_canonical_hash(path: Path) -> str:
    payload = _load_json(path)
    integrity = payload.get("integrity")
    if not isinstance(integrity, Mapping) or not isinstance(integrity.get("incident_sha256"), str):
        raise RuntimeError("BLOCKED_V3_INCIDENT")
    computed = _canonical_hash(payload, "incident_sha256")
    if computed != integrity.get("incident_sha256"):
        raise RuntimeError("BLOCKED_V3_INCIDENT")
    return computed


def _run_production(
    *,
    gate: Mapping[str, Any],
    repo_root: Path,
) -> dict[str, Any]:
    root = repo_root.resolve()
    contract = cast(Mapping[str, Any], gate["contract"])
    package_path = _path(root, V2_PACKAGE_REL)
    state_path = _path(root, V3_STATE_REL)
    manifest_path = _path(root, V2_GOVERNANCE_REL)
    package = frozen_v2.load_r46_v2_package(package_path, repo_root=root)
    manifest = _load_json(manifest_path)
    incumbent = frozen_v2.load_incumbent_pit_inputs(repo_root=root)
    frozen_v2._validate_v5_contract_binding(manifest, package, incumbent, repo_root=root)
    config = frozen_v2._load_r46_config(None, repo_root=root, test_only=False)
    universe, universe_version, universe_status = frozen_v2._build_r46_universe(
        incumbent,
        config=config,
        test_only=False,
    )
    if config is None or universe is None or universe_status != "PIT_V5_PRODUCT_UNIVERSE":
        raise RuntimeError("BLOCKED_PIT_SOURCE:universe")
    del universe_version
    statistics_pins = contract.get("statistics_pins")
    if not isinstance(statistics_pins, Mapping):
        statistics_pins = frozen_v2._manifest_statistics_pins(manifest, root)
    incident_sha256 = _incident_canonical_hash(_path(root, V2_INCIDENT_REL))
    output_path = _path(root, V3_OUTPUT_DIR_REL)
    state_before = _sha256_file(state_path)
    if state_before != V3_STATE_SHA256:
        raise RuntimeError("BLOCKED_V3_STATE")
    reserve_recovery_one_shot(
        state_path,
        preflight_receipt_sha256=str(gate["preflight_sha256"]),
        authorization_sha256=str(gate["authorization_sha256"]),
        v3_governance_sha256=str(gate["governance_sha256"]),
        v3_execution_contract_sha256=str(gate["contract_sha256"]),
        parent_v2_governance_sha256=V2_GOVERNANCE_SHA256,
        parent_v2_package_sha256=package.package_canonical_sha256,
        v2_reservation_physical_sha256=V2_RESERVED_SHA256,
        v2_reservation_transition_sha256=V2_RESERVED_TRANSITION_SHA256,
    )
    payloads = _compute_payloads(
        package=package,
        incumbent=incumbent,
        root=root,
        state_path=state_path,
        output_path=output_path,
        manifest_sha256=str(gate["governance_sha256"]),
        preflight_sha256=str(gate["preflight_sha256"]),
        authorization_sha256=str(gate["authorization_sha256"]),
        contract_sha256=str(gate["contract_sha256"]),
        governance_sha256=str(gate["governance_sha256"]),
        incident_sha256=incident_sha256,
        source_commit=str(contract["source_commit"]),
        freeze_commit=str(gate["preflight"]["freeze_commit"]),
        statistics_pins=statistics_pins,
        config=config,
        test_only=False,
    )
    published = _publish_bundle(
        payloads=payloads,
        output_path=output_path,
        state_path=state_path,
        root=root,
        preflight_sha256=str(gate["preflight_sha256"]),
        authorization_sha256=str(gate["authorization_sha256"]),
        contract_sha256=str(gate["contract_sha256"]),
        governance_sha256=str(gate["governance_sha256"]),
        incident_sha256=incident_sha256,
        package_sha256=package.package_canonical_sha256,
        source_commit=str(contract["source_commit"]),
        freeze_commit=str(gate["preflight"]["freeze_commit"]),
        test_only=False,
    )
    from scripts.research.verify_r46_treasury_auction_unwind_tlt_v3_recovery_2026_09_14 import (
        verify_economic_run_from_files,
    )

    verify_economic_run_from_files(
        root,
        output_path / "results.json",
        output_path / "ledger.json",
        output_path / "report.json",
        output_path / "run_receipt.json",
        _path(root, V3_PREFLIGHT_REL),
    )
    return published


def _synthetic_state(path: Path) -> Path:
    payload: dict[str, Any] = {
        "schema_version": "r46-recovery-one-shot-state-v3",
        "research_id": V3_RESEARCH_ID,
        "candidate_id": CANDIDATE_ID,
        "recovery_generation": RECOVERY_GENERATION,
        "status": "SEALED_UNUSED",
        "integrity": {"artifact_sha256": ""},
    }
    payload["integrity"]["artifact_sha256"] = _canonical_hash(payload, "artifact_sha256")
    _exclusive_json(path, payload)
    return path


def _gate_values(gate: Path | str | Mapping[str, Any] | None) -> dict[str, str]:
    if gate is None:
        return {
            "manifest": "b" * 64,
            "package": "a" * 64,
            "preflight": "c" * 64,
            "source": "d" * 40,
            "freeze": "e" * 40,
        }
    payload = _load_json(Path(gate).resolve()) if not isinstance(gate, Mapping) else dict(gate)
    integrity = payload.get("integrity")
    return {
        "manifest": str(payload.get("manifest_sha256", payload.get("governance_manifest_sha256", "b" * 64))),
        "package": str(payload.get("package_sha256", payload.get("package_canonical_sha256", "a" * 64))),
        "preflight": str(
            integrity.get("receipt_sha256", "c" * 64)
            if isinstance(integrity, Mapping)
            else payload.get("receipt_sha256", "c" * 64)
        ),
        "source": str(payload.get("source_commit", "d" * 40)),
        "freeze": str(payload.get("freeze_commit", "e" * 40)),
    }


def run_recovery_test_only(
    *,
    package: Any,
    incumbent_inputs: Any,
    output_dir: Path | str,
    one_shot_state_path: Path | str | None = None,
    repo_root: Path | str | None = None,
    manifest_sha256: str = "b" * 64,
    preflight_receipt_sha256: str = "c" * 64,
    authorization_sha256: str = "d" * 64,
    execution_contract_sha256: str = "e" * 64,
    governance_sha256: str | None = None,
    incident_sha256: str = "f" * 64,
    source_commit: str = "1" * 40,
    freeze_commit: str = "2" * 40,
    statistics_pins: Mapping[str, object] | None = None,
    config: object | None = None,
    starting_equity: float = 100_000.0,
) -> dict[str, Any]:
    """TEST_ONLY synthetic seam using the same frozen computation choreography."""
    root = Path(repo_root).resolve() if repo_root is not None else Path(output_dir).resolve().parent
    output = Path(output_dir).resolve()
    state = (
        Path(one_shot_state_path).resolve()
        if one_shot_state_path is not None
        else output.parent / "r46_v3_test_one_shot_state.json"
    )
    if state == _path(REPO, V3_STATE_REL):
        raise RuntimeError("BLOCKED_V3_TEST_ONLY_STATE")
    if not state.exists():
        _synthetic_state(state)
    if output.exists():
        expected = tuple(output / name for name in ("results.json", "ledger.json", "report.json", "run_receipt.json"))
        if any(path.exists() or path.is_symlink() for path in expected):
            raise RuntimeError("R46_OUTPUT_ALREADY_EXISTS")
    status = recovery_one_shot_status(state, require_crosslinks=False)
    if status != "SEALED_UNUSED":
        if status == ONE_SHOT_RESERVED:
            raise RuntimeError("ONE_SHOT_ALREADY_RESERVED")
        raise RuntimeError("ONE_SHOT_ALREADY_CONSUMED")
    governance = governance_sha256 or manifest_sha256
    pins = statistics_pins or _default_statistics_pins()
    reserve_recovery_one_shot(
        state,
        preflight_receipt_sha256=preflight_receipt_sha256,
        authorization_sha256=authorization_sha256,
        v3_governance_sha256=governance,
        v3_execution_contract_sha256=execution_contract_sha256,
        parent_v2_governance_sha256=V2_GOVERNANCE_SHA256,
        parent_v2_package_sha256=V2_PACKAGE_SHA256,
        v2_reservation_physical_sha256=V2_RESERVED_SHA256,
        v2_reservation_transition_sha256=V2_RESERVED_TRANSITION_SHA256,
    )
    payloads = _compute_payloads(
        package=package,
        incumbent=incumbent_inputs,
        root=root,
        state_path=state,
        output_path=output,
        manifest_sha256=manifest_sha256,
        preflight_sha256=preflight_receipt_sha256,
        authorization_sha256=authorization_sha256,
        contract_sha256=execution_contract_sha256,
        governance_sha256=governance,
        incident_sha256=incident_sha256,
        source_commit=source_commit,
        freeze_commit=freeze_commit,
        statistics_pins=pins,
        config=config,
        starting_equity=starting_equity,
        test_only=True,
    )
    return _publish_bundle(
        payloads=payloads,
        output_path=output,
        state_path=state,
        root=root,
        preflight_sha256=preflight_receipt_sha256,
        authorization_sha256=authorization_sha256,
        contract_sha256=execution_contract_sha256,
        governance_sha256=governance,
        incident_sha256=incident_sha256,
        package_sha256=package.package_canonical_sha256,
        source_commit=source_commit,
        freeze_commit=freeze_commit,
        test_only=True,
    )


def run_package_backed_test_only(
    *,
    gate: Path | str | Mapping[str, Any] | None = None,
    output_dir: Path | str,
    package: Any | None = None,
    package_path: Path | str | None = None,
    one_shot_state_path: Path | str | None = None,
    repo_root: Path | str | None = None,
    incumbent_inputs: Any | None = None,
    config: object | None = None,
    starting_equity: float = 100_000.0,
) -> dict[str, Any]:
    """Compatibility-shaped TEST_ONLY entrypoint for synthetic fixtures."""
    root = Path(repo_root).resolve() if repo_root is not None else Path(output_dir).resolve().parent
    if package is None:
        if package_path is None:
            raise ValueError("test_only_package_required")
        package = frozen_v2.load_r46_v2_package(package_path, repo_root=root, allow_marker_fallback=True)
    if incumbent_inputs is None:
        incumbent_inputs = frozen_v2.load_incumbent_pit_inputs(repo_root=root)
    values = _gate_values(gate)
    return run_recovery_test_only(
        package=package,
        incumbent_inputs=incumbent_inputs,
        output_dir=output_dir,
        one_shot_state_path=one_shot_state_path,
        repo_root=root,
        manifest_sha256=values["manifest"],
        preflight_receipt_sha256=values["preflight"],
        source_commit=values["source"],
        freeze_commit=values["freeze"],
        config=config,
        starting_equity=starting_equity,
    )


def run_package_backed(
    *,
    preflight_path: Path | str | None = None,
    authorization_path: Path | str | None = None,
    output_dir: Path | str | None = None,
    package_path: Path | str | None = None,
    one_shot_state_path: Path | str | None = None,
    repo_root: Path | str = REPO,
    gate: Path | str | None = None,
    authorization: Path | str | None = None,
    starting_equity: float = 100_000.0,
    cost_multipliers: Sequence[float] | None = None,
    config: object | None = None,
    universe: object | None = None,
    incumbent_inputs: Any | None = None,
    allow_marker_fallback: bool = False,
) -> dict[str, Any]:
    """Production runner; all economic inputs are contract-pinned."""
    root = Path(repo_root).resolve()
    if gate is not None:
        if preflight_path is not None and Path(preflight_path).resolve() != Path(gate).resolve():
            raise RuntimeError("BLOCKED_V3_PRODUCTION_OVERRIDE")
        preflight_path = gate
    if authorization is not None:
        if authorization_path is not None and Path(authorization_path).resolve() != Path(authorization).resolve():
            raise RuntimeError("BLOCKED_V3_PRODUCTION_OVERRIDE")
        authorization_path = authorization
    if (
        abs(float(starting_equity) - 100_000.0) > _EPS
        or cost_multipliers is not None
        or config is not None
        or universe is not None
        or incumbent_inputs is not None
        or allow_marker_fallback
    ):
        raise RuntimeError("BLOCKED_V3_PRODUCTION_OVERRIDE")
    expected_package = _path(root, V2_PACKAGE_REL)
    expected_state = _path(root, V3_STATE_REL)
    expected_output = _path(root, V3_OUTPUT_DIR_REL)
    if package_path is not None and Path(package_path).resolve() != expected_package:
        raise RuntimeError("BLOCKED_V3_PRODUCTION_OVERRIDE")
    if one_shot_state_path is not None and Path(one_shot_state_path).resolve() != expected_state:
        raise RuntimeError("BLOCKED_V3_PRODUCTION_OVERRIDE")
    if output_dir is not None and Path(output_dir).resolve() != expected_output:
        raise RuntimeError("BLOCKED_V3_PRODUCTION_OVERRIDE")
    gate = validate_execution_gate(
        _path(root, V3_PREFLIGHT_REL) if preflight_path is None else preflight_path,
        _path(root, V3_AUTHORIZATION_REL) if authorization_path is None else authorization_path,
        repo_root=root,
    )
    return _run_production(gate=gate, repo_root=root)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preflight", type=Path, default=_path(REPO, V3_PREFLIGHT_REL))
    parser.add_argument("--authorization", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, default=_path(REPO, V3_OUTPUT_DIR_REL))
    args = parser.parse_args(argv)
    try:
        result = run_package_backed(
            preflight_path=args.preflight,
            authorization_path=args.authorization,
            output_dir=args.output_dir,
        )
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "BLOCKED", "error": str(exc)}, sort_keys=True))
        return 1
    print(json.dumps({"status": result["status"], "output_dir": result["output_dir"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
