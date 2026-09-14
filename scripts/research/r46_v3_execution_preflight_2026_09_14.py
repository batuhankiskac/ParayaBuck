#!/usr/bin/env python3
"""Outcome-free source, package, and execution-readiness preflight for R46 v3."""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import locale
import os
import platform
import subprocess
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

recovery_validator = importlib.import_module("scripts.research.r46_v3_recovery_preflight_2026_09_14")
CANONICAL_EXECUTION_ROOT = Path("/Users/batuhankiskac/Desktop/ParayaBuck")
V3_RESEARCH_ID = "r46_treasury_auction_unwind_tlt_v3_recovery_2026_09_14"
CANDIDATE_ID = "R46"
RECOVERY_GENERATION = 1
PARENT_GOVERNANCE_COMMIT = "640097e30db857a011caa5d623e08541b2793cc8"
V2_GOVERNANCE_SHA256 = "92f7ecd5b7f6cdd2428ee221c3b4891f10d5d7ab6f16a5fcb2be952753557c35"
V2_PACKAGE_SHA256 = "c67cfdfeebc4cbc0847d5e2e6fbdebdf6928e04b3e5103606eb8bd0c3d03f6c6"
V2_STATISTICS_SHA256 = "21ebb2e453bd3587388fa800919d5ca24eb9fab4d29b21c106d8e1ce8a015331"
V2_FAMILY_SHA256 = "eeda04d795d099e7571bdbeae0ccaa2dc55141ec2f15d1f8afdc507d60b9acae"
V2_RESERVED_SHA256 = "434b5ef71015d132516474a11078c4e5866d3c91e7d265a7f7c3b20d655141e4"
V2_RESERVED_TRANSITION_SHA256 = "040d13dcba81e3d52e7d105aea9250a3c565e7b87e9b6af5c44e83b0dec28405"
V2_STATE_SHA256 = "c69e6bd76964131f41a02b4a58a1a308af1a2a1a038c8e339b1512b097b2f412"
V2_STATE_CANONICAL_SHA256 = "b2977b5f516cd4371af2201a87cb84637beb47214e74cc3d0980bbc9eaf33fab"
V2_GATE_SHA256 = "6307caa29e8a2179d36a5f2792573bb7307f22a317fa55071b348eb51ef2639c"
V2_GATE_PHYSICAL_SHA256 = "b05afe121048291edce06d54ef001363470ea3cc2e16ce58eb44f35ab9d972bc"
V3_STATE_SHA256 = "59148f1e32af95ed52bbcea9db802470d7b9b5156a58ba7ffe25d8de824b6ac7"
V3_STATE_CANONICAL_SHA256 = "0165ea19b68a5e913927cb5016a703ccc9d2be73ba01a7515372727b0bdb9421"
V3_GOVERNANCE_SHA256 = "bc49ab40e00e21deb8b4ef305c18400665d4154d4c1077a8a31dbc7da452c3db"
V3_INCIDENT_SHA256 = "bdd668747423960faddbb31982566e6c49fcc3b64fd32bb29c40775a621acb8b"
V2_INCIDENT_PHYSICAL_SHA256 = "7be069ba5a29a79313bdf77b3355ff5d5fdf319b624a3d5b6da30291609d74c0"
EVENT_IDS_SHA256 = "f726f27aaece6532de1581b5830e38cceba67c75ae02dfa79fedaffc0e9d4b0b"
TRANSITION_SCHEMA = "r46-recovery-one-shot-transition-v3"
PREFLIGHT_SCHEMA = "r46-v3-recovery-preflight-receipt-v1"
AUTHORIZATION_SCHEMA = "r46-v3-recovery-execution-authorization-v1"
FREEZE_COMMIT_SENTINEL = "CURRENT_HEAD_AT_FREEZE"
V2_NATIVE_ATTESTATION_REL = Path(
    "data/research_receipts/r46_treasury_auction_unwind_tlt_v2_2026_09_10_native_attestation.json"
)
V3_STATE_REL = Path("data/research_manifests/r46_treasury_auction_unwind_tlt_v3_recovery_2026_09_14_one_shot_state.json")
V3_GOVERNANCE_REL = Path("data/research_manifests/r46_treasury_auction_unwind_tlt_v3_recovery_2026_09_14.json")
V3_SCHEMA_REL = Path("data/research_manifests/r46_treasury_auction_unwind_tlt_v3_recovery_2026_09_14.schema.json")
V3_CONTRACT_REL = Path("data/research_manifests/r46_treasury_auction_unwind_tlt_v3_recovery_2026_09_14_execution_contract.json")
V3_CONTRACT_SCHEMA_REL = Path(
    "data/research_manifests/r46_treasury_auction_unwind_tlt_v3_recovery_2026_09_14_execution_contract.schema.json"
)
V3_PREFLIGHT_REL = Path("data/research_receipts/r46_v3_recovery_preflight_2026_09_14.json")
V3_AUTHORIZATION_REL = Path("data/research_receipts/r46_v3_execution_authorization_2026_09_14.json")
V3_OUTPUT_DIR_REL = Path("data/research/runs/r46_treasury_auction_unwind_tlt_v3_recovery_final_2026_09_14")
V3_RUNNER_REL = Path("scripts/research/r46_treasury_auction_unwind_tlt_v3_recovery_2026_09_14.py")
V3_VERIFIER_REL = Path("scripts/research/verify_r46_treasury_auction_unwind_tlt_v3_recovery_2026_09_14.py")
V3_PREFLIGHT_IMPL_REL = Path("scripts/research/r46_v3_execution_preflight_2026_09_14.py")
V2_STATE_REL = recovery_validator.V2_STATE_REL
V2_RESERVED_REL = recovery_validator.V2_RESERVED_REL
V2_CONSUMED_REL = recovery_validator.V2_CONSUMED_REL
V2_ABORTED_REL = recovery_validator.V2_ABORTED_REL
V2_GOVERNANCE_REL = recovery_validator.V2_GOVERNANCE_REL
V2_PACKAGE_REL = recovery_validator.V2_PACKAGE_REL
V2_STATISTICS_REL = recovery_validator.V2_STATISTICS_REL
V2_FAMILY_REL = recovery_validator.V2_FAMILY_REL
V2_PREFLIGHT_REL = recovery_validator.V2_PREFLIGHT_REL
V2_INCIDENT_REL = recovery_validator.V3_INCIDENT_REL
V2_OUTPUT_DIR_REL = recovery_validator.V2_OUTPUT_DIR_REL
SEARCH_ROOTS = recovery_validator.SEARCH_ROOTS
ECONOMIC_FILENAMES = recovery_validator.ECONOMIC_FILENAMES
_HEX64 = set("0123456789abcdef")


def _canonical_bytes(value: Mapping[str, Any]) -> bytes:
    return json.dumps(dict(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _canonical_hash(payload: Mapping[str, Any], field: str) -> str:
    value = json.loads(json.dumps(dict(payload), sort_keys=True, ensure_ascii=False))
    integrity = value.get("integrity")
    if not isinstance(integrity, dict):
        raise ValueError("integrity_object_required")
    integrity.pop(field, None)
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
        raise ValueError(f"json_object_required:{path}")
    return value


def _valid_hex64(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and set(value) <= _HEX64


def _path(root: Path, relative: Path, *, must_exist: bool = True) -> Path:
    path = (root / relative).resolve()
    if must_exist and not path.is_file() and not path.is_dir():
        raise ValueError(f"path_missing:{relative}")
    return path


def _add(blockers: list[str], blocker: str) -> None:
    if blocker not in blockers:
        blockers.append(blocker)


def _git(root: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", *arguments],
        cwd=root,
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError("git_command_failed")
    return completed.stdout.strip()


def _git_blob(root: Path, commit: str, relative: str) -> str:
    return _git(root, "rev-parse", f"{commit}:{relative}")


def _resolved_freeze_commit(root: Path, contract: Mapping[str, Any]) -> str | None:
    value = contract.get("freeze_commit")
    if value == FREEZE_COMMIT_SENTINEL:
        try:
            return _git(root, "rev-parse", "HEAD")
        except RuntimeError:
            return None
    return value if isinstance(value, str) else None


def _environment() -> dict[str, str]:
    timezone = os.environ.get("TZ")
    if not timezone:
        try:
            localtime = Path("/etc/localtime")
            target = localtime.resolve()
            timezone = str(target).split("/zoneinfo/", 1)[-1] if "/zoneinfo/" in str(target) else ""
        except OSError:
            timezone = ""
    return {
        "os": platform.system(),
        "architecture": platform.machine(),
        "python": platform.python_version(),
        "timezone": timezone or "",
        "locale": os.environ.get("LC_ALL") or locale.setlocale(locale.LC_ALL, None),
        "dependency_lock": "uv.lock",
    }


def _validate_contract(root: Path, identity_root: Path, blockers: list[str]) -> tuple[dict[str, Any] | None, str | None]:
    contract_path = _path(root, V3_CONTRACT_REL, must_exist=False)
    schema_path = _path(root, V3_CONTRACT_SCHEMA_REL, must_exist=False)
    try:
        contract = _load_json(contract_path)
        schema = _load_json(schema_path)
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
        _add(blockers, "EXECUTION_CONTRACT_INVALID")
        return None, None
    schema_errors = recovery_validator._schema_errors(contract, schema)
    integrity = contract.get("integrity")
    try:
        canonical = _canonical_hash(contract, "contract_sha256")
    except ValueError:
        canonical = None
    if (
        schema_errors
        or not isinstance(integrity, Mapping)
        or integrity.get("contract_sha256") != canonical
        or contract.get("schema_version") != "r46-v3-recovery-execution-contract-v1"
    ):
        _add(blockers, "EXECUTION_CONTRACT_INVALID")
        return contract, canonical
    if (
        contract.get("canonical_execution_root") != str(CANONICAL_EXECUTION_ROOT)
        or contract.get("parent_governance_commit") != PARENT_GOVERNANCE_COMMIT
        or contract.get("v2_package_canonical_sha256") != V2_PACKAGE_SHA256
        or contract.get("frozen_output_directory") != str(identity_root / V3_OUTPUT_DIR_REL)
        or contract.get("authorization", {}).get("schema") != AUTHORIZATION_SCHEMA
        or contract.get("authorization", {}).get("present") is not False
    ):
        _add(blockers, "EXECUTION_CONTRACT_BINDING_INVALID")
    environment = contract.get("environment")
    if not isinstance(environment, Mapping):
        _add(blockers, "EXECUTION_ENVIRONMENT_INVALID")
    else:
        expected = {
            "os": "Darwin",
            "architecture": "arm64",
            "python": "3.13.12",
            "timezone": "Europe/Istanbul",
            "locale": "C.UTF-8",
            "dependency_lock": "uv.lock",
        }
        if any(environment.get(key) != value for key, value in expected.items()):
            _add(blockers, "EXECUTION_ENVIRONMENT_INVALID")
    source_commit = contract.get("source_commit")
    freeze_commit = contract.get("freeze_commit")
    if (
        not isinstance(source_commit, str)
        or len(source_commit) != 40
        or set(source_commit) - _HEX64
        or not isinstance(freeze_commit, str)
        or (
            freeze_commit != FREEZE_COMMIT_SENTINEL
            and (len(freeze_commit) != 40 or set(freeze_commit) - _HEX64)
        )
    ):
        _add(blockers, "SOURCE_FREEZE_INVALID")
    if freeze_commit == FREEZE_COMMIT_SENTINEL and contract.get("freeze_commit_binding") != "HEAD_AT_FREEZE":
        _add(blockers, "SOURCE_FREEZE_INVALID")
    for spec in contract.get("source_files", ()):
        if not isinstance(spec, Mapping):
            _add(blockers, "SOURCE_FILE_PINS_INVALID")
            continue
        relative = spec.get("path")
        if not isinstance(relative, str) or Path(relative).is_absolute():
            _add(blockers, "SOURCE_FILE_PINS_INVALID")
            continue
        try:
            path = _path(root, Path(relative))
            if spec.get("sha256") != sha256_file(path):
                _add(blockers, f"SOURCE_FILE_HASH_MISMATCH:{relative}")
            if isinstance(source_commit, str) and spec.get("git_blob_sha256") != _git_blob(root, source_commit, relative):
                _add(blockers, f"SOURCE_FILE_BLOB_MISMATCH:{relative}")
        except (OSError, RuntimeError, ValueError):
            _add(blockers, f"SOURCE_FILE_PIN_UNREADABLE:{relative}")
    lock = contract.get("dependency_lock")
    if not isinstance(lock, Mapping) or lock.get("path") != "uv.lock":
        _add(blockers, "DEPENDENCY_LOCK_INVALID")
    else:
        try:
            lock_path = root / "uv.lock"
            if lock.get("sha256") != sha256_file(lock_path) or lock.get("git_blob_sha256") != _git_blob(
                root, str(source_commit), "uv.lock"
            ):
                _add(blockers, "DEPENDENCY_LOCK_INVALID")
        except (OSError, RuntimeError, ValueError):
            _add(blockers, "DEPENDENCY_LOCK_INVALID")
    return contract, canonical


def _validate_lineage(root: Path, contract: Mapping[str, Any], blockers: list[str]) -> None:
    source = contract.get("source_commit")
    freeze = _resolved_freeze_commit(root, contract)
    if not isinstance(source, str) or not isinstance(freeze, str):
        return
    try:
        _git(root, "cat-file", "-e", f"{PARENT_GOVERNANCE_COMMIT}^{{commit}}")
        _git(root, "cat-file", "-e", f"{source}^{{commit}}")
        _git(root, "cat-file", "-e", f"{freeze}^{{commit}}")
        ancestor = subprocess.run(
            ["git", "merge-base", "--is-ancestor", PARENT_GOVERNANCE_COMMIT, source],
            cwd=root,
            check=False,
            capture_output=True,
        )
        if ancestor.returncode != 0:
            _add(blockers, "PARENT_GOVERNANCE_COMMIT_NOT_ANCESTOR")
        source_ancestor = subprocess.run(
            ["git", "merge-base", "--is-ancestor", source, freeze],
            cwd=root,
            check=False,
            capture_output=True,
        )
        if source_ancestor.returncode != 0:
            _add(blockers, "SOURCE_NOT_ANCESTOR_OF_FREEZE")
        if _git(root, "rev-parse", "HEAD") != freeze:
            _add(blockers, "FREEZE_COMMIT_NOT_HEAD")
        if _git(root, "rev-list", "--count", f"{source}..{freeze}") != "1":
            _add(blockers, "SOURCE_FREEZE_COMMIT_COUNT_INVALID")
        changed = _git(root, "diff", "--name-only", source, freeze).splitlines()
        allowed = (
            "data/research_manifests/",
            "data/research_receipts/",
            "docs/research/",
            "docs/plans/",
        )
        blockers.extend(
            f"FORBIDDEN_SOURCE_FREEZE_PATH:{path}" for path in sorted(path for path in changed if not path.startswith(allowed))
        )
    except (RuntimeError, OSError):
        _add(blockers, "SOURCE_FREEZE_GIT_INVALID")


def _validate_parent_and_recovery(root: Path, identity_root: Path, blockers: list[str]) -> dict[str, Any]:
    recovery = recovery_validator.validate_recovery(root, canonical_root=identity_root)
    if recovery.get("status") != "RECOVERY_GOVERNANCE_READY" or recovery.get("blockers") != []:
        _add(blockers, "RECOVERY_GOVERNANCE_BLOCKED")
        blockers.extend(str(value) for value in recovery.get("blockers", ()))
    try:
        state_path = _path(root, V3_STATE_REL)
        state = _load_json(state_path)
        if (
            sha256_file(state_path) != V3_STATE_SHA256
            or recovery_validator.canonical_hash(state, "artifact_sha256") != V3_STATE_CANONICAL_SHA256
            or state.get("status") != "SEALED_UNUSED"
            or state.get("run_count") != 0
            or state.get("release_count") != 0
            or state.get("reservation_state") != "UNRESERVED"
        ):
            _add(blockers, "V3_STATE_NOT_SEALED_UNUSED")
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        _add(blockers, "V3_STATE_INVALID")
    for relative in (
        Path(f"{V3_STATE_REL}.reserved.json"),
        Path(f"{V3_STATE_REL}.consumed.json"),
        Path(f"{V3_STATE_REL}.aborted_consumed.json"),
    ):
        if _path(root, relative, must_exist=False).exists():
            _add(blockers, "V3_TRANSITION_SIDECAR_PRESENT")
    return recovery


def _validate_package(root: Path, blockers: list[str]) -> dict[str, Any]:
    try:
        from scripts.research.build_r46_v2_data_package_2026_09_10 import (
            canonical_event_id_digest,
            consume_r46_v2_data_package,
        )

        package_path = _path(root, V2_PACKAGE_REL)
        package = consume_r46_v2_data_package(package_path, repo_root=root)
        if package.get("integrity", {}).get("manifest_sha256") != V2_PACKAGE_SHA256:
            raise ValueError("package_hash")
        coverage = package.get("coverage")
        if not isinstance(coverage, Mapping):
            raise ValueError("coverage")
        ids = package.get("expected_event_ids")
        digest = canonical_event_id_digest(ids if isinstance(ids, Sequence) else None)
        if (
            package.get("expected_event_count") != 127
            or coverage.get("auction_event_count") != 127
            or coverage.get("event_episodes") != 127
            or coverage.get("holding_intervals") != 635
            or coverage.get("fifth_open_exit") != 127
            or coverage.get("missing_rate") != 0.0
            or digest != EVENT_IDS_SHA256
        ):
            raise ValueError("package_coverage")
        artifacts = package.get("artifacts")
        artifact_hashes = {
            name: spec.get("sha256")
            for name, spec in artifacts.items()
            if isinstance(artifacts, Mapping) and isinstance(spec, Mapping)
        }
        if set(artifact_hashes) != {"documents", "bars", "dividends", "eligibility", "calendar"}:
            raise ValueError("package_artifacts")
        return {
            "manifest_sha256": V2_PACKAGE_SHA256,
            "artifact_hashes": artifact_hashes,
            "expected_event_count": 127,
            "event_episodes": 127,
            "holding_intervals": 635,
            "fifth_open_exit": 127,
            "missing_rate": 0.0,
            "event_ids_sha256": digest,
        }
    except Exception as exc:
        _add(blockers, "V2_DATA_PACKAGE_INVALID")
        return {"error": str(exc)}


def _validate_native_attestation(root: Path, blockers: list[str]) -> dict[str, Any]:
    path = _path(root, V2_NATIVE_ATTESTATION_REL, must_exist=False)
    try:
        from scripts.research.verify_r46_treasury_auction_unwind_tlt_v2_2026_09_10 import verify_attestation

        payload = _load_json(path)
        verified = verify_attestation(payload, repo_root=root)
        if verified.get("status") != "VERIFIED_NATIVE_LEDGER":
            raise ValueError("native_attestation_status")
        return {
            "path": str(V2_NATIVE_ATTESTATION_REL),
            "physical_sha256": sha256_file(path),
            "canonical_sha256": payload.get("attestation_sha256"),
            "status": verified["status"],
        }
    except Exception as exc:
        _add(blockers, "V2_NATIVE_ATTESTATION_INVALID")
        return {"error": str(exc)}


def validate_execution_preflight(
    root: Path | str = REPO,
    *,
    canonical_root: Path | str | None = None,
) -> dict[str, Any]:
    """Validate readiness without importing or calling an economic runner."""
    filesystem_root = Path(root).resolve()
    identity_root = Path(canonical_root).resolve() if canonical_root is not None else CANONICAL_EXECUTION_ROOT
    blockers: list[str] = []
    if identity_root != CANONICAL_EXECUTION_ROOT:
        _add(blockers, "EXECUTION_ROOT_MISMATCH")
    contract, contract_sha = _validate_contract(filesystem_root, identity_root, blockers)
    if contract is not None:
        _validate_lineage(filesystem_root, contract, blockers)
    recovery = _validate_parent_and_recovery(filesystem_root, identity_root, blockers)
    if recovery.get("status") != "RECOVERY_GOVERNANCE_READY" or recovery.get("blockers") != []:
        _add(blockers, "RECOVERY_GOVERNANCE_BLOCKED")
        blockers.extend(str(value) for value in recovery.get("blockers", ()))
    package = _validate_package(filesystem_root, blockers)
    native = _validate_native_attestation(filesystem_root, blockers)
    environment = _environment()
    expected_environment = {
        "os": "Darwin",
        "architecture": "arm64",
        "python": "3.13.12",
        "timezone": "Europe/Istanbul",
        "locale": "C.UTF-8",
        "dependency_lock": "uv.lock",
    }
    if any(environment.get(key) != value for key, value in expected_environment.items()):
        _add(blockers, "EXECUTION_ENVIRONMENT_MISMATCH")
    output_path = filesystem_root / V3_OUTPUT_DIR_REL
    if output_path.exists() or output_path.is_symlink():
        _add(blockers, "V3_OUTPUT_DIRECTORY_PRESENT")
    authorization_path = filesystem_root / V3_AUTHORIZATION_REL
    if authorization_path.exists() or authorization_path.is_symlink():
        _add(blockers, "V3_AUTHORIZATION_PRESENT_UNEXPECTED")
    v3_state_path = filesystem_root / V3_STATE_REL
    result: dict[str, Any] = {
        "schema_version": PREFLIGHT_SCHEMA,
        "status": "RECOVERY_PRE_RUN_OK" if not blockers else "BLOCKED",
        "blockers": sorted(set(blockers)),
        "research_id": V3_RESEARCH_ID,
        "candidate_id": CANDIDATE_ID,
        "recovery_generation": RECOVERY_GENERATION,
        "economic_authorization": False,
        "runner_authorization_present": False,
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
        "canonical_execution_root": str(identity_root),
        "execution_root_observed": str(filesystem_root),
        "environment": environment,
        "expected_environment": expected_environment,
        "execution_contract_path": str(V3_CONTRACT_REL),
        "execution_contract_sha256": contract_sha,
        "recovery_governance_path": str(V3_GOVERNANCE_REL),
        "recovery_governance_sha256": V3_GOVERNANCE_SHA256,
        "v3_state_path": str(V3_STATE_REL),
        "v3_state_physical_sha256": sha256_file(v3_state_path) if v3_state_path.is_file() else None,
        "v3_state_canonical_sha256": V3_STATE_CANONICAL_SHA256 if v3_state_path.is_file() else None,
        "v3_state_status": "SEALED_UNUSED" if v3_state_path.is_file() else None,
        "runner_path": str(V3_RUNNER_REL),
        "runner_sha256": sha256_file(filesystem_root / V3_RUNNER_REL)
        if (filesystem_root / V3_RUNNER_REL).is_file()
        else None,
        "verifier_path": str(V3_VERIFIER_REL),
        "verifier_sha256": sha256_file(filesystem_root / V3_VERIFIER_REL)
        if (filesystem_root / V3_VERIFIER_REL).is_file()
        else None,
        "preflight_path": str(V3_PREFLIGHT_IMPL_REL),
        "preflight_sha256": sha256_file(filesystem_root / V3_PREFLIGHT_IMPL_REL)
        if (filesystem_root / V3_PREFLIGHT_IMPL_REL).is_file()
        else None,
        "dependency_lock_path": "uv.lock",
        "dependency_lock_sha256": sha256_file(filesystem_root / "uv.lock")
        if (filesystem_root / "uv.lock").is_file()
        else None,
        "incident_receipt_path": str(V2_INCIDENT_REL),
        "incident_receipt_sha256": V3_INCIDENT_SHA256,
        "incident_receipt_physical_sha256": V2_INCIDENT_PHYSICAL_SHA256,
        "v2_reservation_physical_sha256": V2_RESERVED_SHA256,
        "v2_reservation_transition_sha256": V2_RESERVED_TRANSITION_SHA256,
        "v2_governance_sha256": V2_GOVERNANCE_SHA256,
        "v2_package_sha256": V2_PACKAGE_SHA256,
        "v2_statistics_sha256": V2_STATISTICS_SHA256,
        "v2_family_sha256": V2_FAMILY_SHA256,
        "v2_incident_status": "ORIGIN_UNPROVEN",
        "v2_reservation_preserved": True,
        "v2_consumed_sidecar_present": (filesystem_root / V2_CONSUMED_REL).exists(),
        "v2_aborted_sidecar_present": (filesystem_root / V2_ABORTED_REL).exists(),
        "package": package,
        "native_attestation": native,
        "future_output_directory": str(identity_root / V3_OUTPUT_DIR_REL),
        "authorization_schema": AUTHORIZATION_SCHEMA,
        "authorization_path": str(V3_AUTHORIZATION_REL),
        "authorization_present": False,
        "source_commit": contract.get("source_commit") if isinstance(contract, Mapping) else None,
        "freeze_commit": _resolved_freeze_commit(filesystem_root, contract) if isinstance(contract, Mapping) else None,
        "no_economic_outcomes_accessed": True,
        "no_economic_outputs_generated": True,
        "no_paper_live_activation": True,
        "recovery_governance_status": recovery.get("status"),
    }
    return result


def persist_preflight_receipt(
    result: Mapping[str, Any],
    *,
    root: Path | str = REPO,
    receipt_path: Path | str | None = None,
) -> Path:
    if result.get("status") != "RECOVERY_PRE_RUN_OK" or result.get("blockers") != []:
        raise RuntimeError("BLOCKED_RECOVERY_PRE_RUN")
    filesystem_root = Path(root).resolve()
    destination = Path(receipt_path).resolve() if receipt_path is not None else filesystem_root / V3_PREFLIGHT_REL
    if destination != filesystem_root / V3_PREFLIGHT_REL:
        raise RuntimeError("BLOCKED_V3_PRODUCTION_OVERRIDE")
    if (filesystem_root / V3_AUTHORIZATION_REL).exists():
        raise RuntimeError("BLOCKED_V3_AUTHORIZATION_PRESENT")
    payload = dict(result)
    payload["integrity"] = {"receipt_sha256": ""}
    payload["integrity"]["receipt_sha256"] = _canonical_hash(payload, "receipt_sha256")
    encoded = (json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = -1
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        if descriptor != -1:
            os.close(descriptor)
    directory_descriptor = os.open(destination.parent, os.O_RDONLY)
    try:
        os.fsync(directory_descriptor)
    finally:
        os.close(directory_descriptor)
    return destination


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=REPO)
    parser.add_argument("--persist-receipt", action="store_true")
    args = parser.parse_args(argv)
    result = validate_execution_preflight(args.repo_root)
    if args.persist_receipt and result["status"] == "RECOVERY_PRE_RUN_OK":
        persist_preflight_receipt(result, root=args.repo_root)
        result = {**result, "receipt_persisted": True}
    print(json.dumps(result, sort_keys=True, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
