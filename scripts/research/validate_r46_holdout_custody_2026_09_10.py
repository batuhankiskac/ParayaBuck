"""Validate the R46 governance contract and prospective holdout custody.

This validator is intentionally read-only.  It never loads market data, runs an
economic worker, computes a return, or releases a holdout.  A contract that is
not yet executable reports explicit BLOCKED reasons instead of treating missing
provenance or statistics as a pass.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
MANIFEST = REPO / "data/research_manifests/r46_treasury_auction_unwind_tlt_v1_2026_09_10.json"
SCHEMA = REPO / "data/research_manifests/r46_treasury_auction_unwind_tlt_v1_2026_09_10.schema.json"
SOURCE_MATRIX = REPO / "data/research_manifests/r46_treasury_auction_unwind_tlt_v1_2026_09_10_source_matrix.json"
HOLDOUT_CONTRACT = REPO / "data/research_manifests/r46_treasury_auction_unwind_tlt_v1_2026_09_10_holdout_custody_contract.json"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
CAPABILITY_RECEIPT_SCHEMA = "r46-treasury-source-capability-v1"
CAPABILITY_RECEIPT_STATUS = "CAPABILITY_OR_PAID_BLOCKED"
CAPABILITY_ROUTE_STATUS = "PUBLIC_ROUTES_IDENTIFIED"
CAPABILITY_ADMISSION_STATUS = "DATA_ACQUISITION_REQUIRED"
CAPABILITY_RECEIPT_BLOCKER = "BLOCKED_SOURCE_CAPABILITY_RECEIPT"


def sha256(path: Path) -> str:
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


def canonical_hash(payload: dict[str, Any], field: str, *, nested: bool = False) -> str:
    value = json.loads(json.dumps(payload, ensure_ascii=False))
    if nested:
        integrity = value.get("integrity")
        if not isinstance(integrity, dict):
            raise ValueError("integrity object required")
        integrity.pop(field, None)
    else:
        value.pop(field, None)
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _safe_canonical_hash(payload: dict[str, Any], field: str, *, nested: bool = False) -> str | None:
    try:
        return canonical_hash(payload, field, nested=nested)
    except (TypeError, ValueError, AttributeError, json.JSONDecodeError):
        return None


def _git_tree_has(repo: Path, commit: str, relative: str) -> bool:
    if not commit or not relative:
        return False
    result = subprocess.run(
        ["git", "cat-file", "-e", f"{commit}:{relative}"],
        cwd=repo,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return result.returncode == 0


def _git_blob_sha256(repo: Path, commit: str, relative: str) -> str | None:
    if not commit or not relative:
        return None
    relative_path = Path(relative)
    if relative_path.is_absolute() or ".." in relative_path.parts:
        return None
    result = subprocess.run(
        ["git", "show", f"{commit}:{relative}"],
        cwd=repo,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if result.returncode != 0:
        return None
    return hashlib.sha256(result.stdout).hexdigest()


def _safe_relative_file(repo: Path, relative: str) -> Path | None:
    relative_path = Path(relative)
    if relative_path.is_absolute() or ".." in relative_path.parts or not relative:
        return None
    try:
        resolved = (repo / relative_path).resolve(strict=True)
    except (OSError, ValueError):
        return None
    if repo.resolve() not in resolved.parents or not resolved.is_file():
        return None
    return resolved


def _add(reasons: list[str], reason: str) -> None:
    if reason not in reasons:
        reasons.append(reason)


def _validate_static_file_hash(
    reasons: list[str],
    repo: Path,
    relative: str,
    expected: str | None,
    reason: str,
    *,
    base_commit: str | None = None,
) -> None:
    path = _safe_relative_file(repo, relative)
    if path is None:
        _add(reasons, reason)
        return
    if not isinstance(expected, str) or not SHA256_RE.fullmatch(expected):
        _add(reasons, reason)
        return
    if sha256(path) != expected:
        _add(reasons, reason)
    if base_commit is not None:
        base_hash = _git_blob_sha256(repo, base_commit, relative)
        if base_hash is None or base_hash != expected:
            _add(reasons, "BLOCKED_SOURCE_BASE_HASH")


def _validate_canonical_artifact_hash(
    reasons: list[str],
    repo: Path,
    relative: str,
    field: str,
    reason: str,
) -> None:
    """Check an artifact's declared canonical hash without opening data roots."""

    path = _safe_relative_file(repo, relative)
    if path is None:
        _add(reasons, reason)
        return
    try:
        payload = _load_json(path)
        integrity = payload.get("integrity")
        expected = integrity.get(field) if isinstance(integrity, dict) else None
        if not isinstance(expected, str) or not SHA256_RE.fullmatch(expected):
            _add(reasons, reason)
        elif expected != canonical_hash(payload, field, nested=True):
            _add(reasons, reason)
    except (OSError, TypeError, AttributeError, ValueError, json.JSONDecodeError):
        _add(reasons, reason)


def _load_relative_json(repo: Path, relative: str) -> dict[str, Any] | None:
    path = _safe_relative_file(repo, relative)
    if path is None:
        return None
    try:
        return _load_json(path)
    except (OSError, TypeError, AttributeError, ValueError, json.JSONDecodeError):
        return None


def _validate_capability_receipt_binding(
    reasons: list[str],
    repo: Path,
    provenance: dict[str, Any],
    *,
    research_id: Any,
    candidate_id: Any,
) -> None:
    """Validate the governance-bound, top-level-self-hashed capability receipt."""

    binding = provenance.get("source_capability_receipt")
    if not isinstance(binding, dict):
        _add(reasons, CAPABILITY_RECEIPT_BLOCKER)
        return
    expected_fields = {
        "artifact_sha256": None,
        "status": CAPABILITY_RECEIPT_STATUS,
        "route_capability_status": CAPABILITY_ROUTE_STATUS,
        "r46_admission_status": CAPABILITY_ADMISSION_STATUS,
    }
    for field, expected in expected_fields.items():
        value = binding.get(field)
        if field == "artifact_sha256":
            if not isinstance(value, str) or not SHA256_RE.fullmatch(value):
                _add(reasons, CAPABILITY_RECEIPT_BLOCKER)
        elif value != expected:
            _add(reasons, CAPABILITY_RECEIPT_BLOCKER)

    relative = binding.get("path")
    if not isinstance(relative, str):
        _add(reasons, CAPABILITY_RECEIPT_BLOCKER)
        return
    path = _safe_relative_file(repo, relative)
    if path is None:
        _add(reasons, CAPABILITY_RECEIPT_BLOCKER)
        return
    expected_raw_hash = binding.get("sha256")
    try:
        if not isinstance(expected_raw_hash, str) or not SHA256_RE.fullmatch(expected_raw_hash) or sha256(path) != expected_raw_hash:
            _add(reasons, CAPABILITY_RECEIPT_BLOCKER)
        receipt = _load_json(path)
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        _add(reasons, CAPABILITY_RECEIPT_BLOCKER)
        return

    if (
        receipt.get("schema") != CAPABILITY_RECEIPT_SCHEMA
        or receipt.get("research_id") != research_id
        or receipt.get("candidate_id") != candidate_id
        or receipt.get("status") != CAPABILITY_RECEIPT_STATUS
        or receipt.get("route_capability_status") != CAPABILITY_ROUTE_STATUS
        or receipt.get("r46_admission_status") != CAPABILITY_ADMISSION_STATUS
        or receipt.get("artifact_sha256") != binding.get("artifact_sha256")
    ):
        _add(reasons, CAPABILITY_RECEIPT_BLOCKER)
    receipt_hash = receipt.get("artifact_sha256")
    if (
        not isinstance(receipt_hash, str)
        or not SHA256_RE.fullmatch(receipt_hash)
        or receipt_hash != _safe_canonical_hash(receipt, "artifact_sha256")
    ):
        _add(reasons, CAPABILITY_RECEIPT_BLOCKER)


def validate_manifest(repo: Path = REPO) -> list[str]:
    """Return fail-closed blockers for the R46 pre-outcome contract."""

    reasons: list[str] = []
    try:
        payload = _load_json(repo / MANIFEST.relative_to(REPO))
    except (OSError, ValueError, json.JSONDecodeError):
        return ["INVALID_MANIFEST"]

    required = {
        "schema_version": "r46-governance-manifest-v1",
        "candidate_id": "R46",
        "status": "FROZEN_PRE_OUTCOME",
    }
    for field, expected in required.items():
        if payload.get(field) != expected:
            _add(reasons, "INVALID_MANIFEST_SCHEMA")
    for field in ("declared_before_outcomes", "paper_live_untouched", "research_only", "no_auto_promotion"):
        if payload.get(field) is not True:
            _add(reasons, "INVALID_OUTCOME_BOUNDARY")
    for field in ("economic_execution", "economic_run", "promotion", "integration_candidate", "auto_promotion", "paper_or_live_activation"):
        if payload.get(field) is not False:
            _add(reasons, "INVALID_SAFETY_BOUNDARY")
    for field in ("economic_outcomes_accessed", "outcome_artifacts_present"):
        if payload.get(field) is not False:
            _add(reasons, "INVALID_OUTCOME_BOUNDARY")

    provenance = payload.get("source_provenance")
    base_commit = provenance.get("base_commit") if isinstance(provenance, dict) else None
    integrity = payload.get("integrity")
    manifest_canonical_hash = _safe_canonical_hash(payload, "manifest_sha256", nested=True)
    if not isinstance(integrity, dict) or integrity.get("manifest_sha256") != manifest_canonical_hash:
        _add(reasons, "INVALID_MANIFEST_SELF_HASH")
    elif integrity.get("schema_path"):
        _validate_static_file_hash(
            reasons,
            repo,
            str(integrity["schema_path"]),
            integrity.get("schema_sha256"),
            "BLOCKED_SCHEMA_PIN",
            base_commit=base_commit,
        )
    else:
        _add(reasons, "BLOCKED_SCHEMA_PIN")

    data_contract = payload.get("data_contract")
    if not isinstance(data_contract, dict):
        _add(reasons, "INVALID_DATA_CONTRACT")
    else:
        source_path = data_contract.get("source_matrix_path")
        _validate_static_file_hash(
            reasons,
            repo,
            str(source_path or ""),
            data_contract.get("source_matrix_sha256"),
            "BLOCKED_SOURCE_MATRIX_PIN",
            base_commit=base_commit,
        )
        _validate_canonical_artifact_hash(reasons, repo, str(source_path or ""), "artifact_sha256", "INVALID_SOURCE_MATRIX_SELF_HASH")
        source_payload = _load_relative_json(repo, str(source_path or ""))
        if source_payload is None:
            _add(reasons, "INVALID_SOURCE_MATRIX_CONTRACT")
        else:
            if (
                source_payload.get("schema_version") != "r46-source-matrix-v1"
                or source_payload.get("research_id") != payload.get("research_id")
                or source_payload.get("candidate_id") != payload.get("candidate_id")
                or source_payload.get("declared_before_outcomes") is not True
                or source_payload.get("economic_execution") is not False
                or source_payload.get("economic_run") is not False
                or source_payload.get("economic_outcomes_accessed") is not False
                or source_payload.get("outcome_artifacts_present") is not False
            ):
                _add(reasons, "INVALID_SOURCE_MATRIX_CONTRACT")
            record_schema = source_payload.get("record_schema")
            forbidden_fields = (
                "price_return",
                "NAV",
                "P&L",
                "drawdown",
                "CVaR",
                "Sharpe",
            )
            if (
                not isinstance(record_schema, dict)
                or not isinstance(record_schema.get("outcome_fields_not_exposed_to_preflight"), list)
                or not set(forbidden_fields).issubset(record_schema["outcome_fields_not_exposed_to_preflight"])
            ):
                _add(reasons, "INVALID_SOURCE_MATRIX_CONTRACT")
        if data_contract.get("pit_source_status") != "READY_PIT_SOURCE":
            _add(reasons, "BLOCKED_PIT_SOURCE")

    holdout = payload.get("holdout")
    if not isinstance(holdout, dict):
        _add(reasons, "INVALID_HOLDOUT_CONTRACT")
    else:
        contract_path = holdout.get("contract_path")
        _validate_static_file_hash(
            reasons,
            repo,
            str(contract_path or ""),
            holdout.get("contract_sha256"),
            "BLOCKED_HOLDOUT_CONTRACT_PIN",
            base_commit=base_commit,
        )
        _validate_canonical_artifact_hash(reasons, repo, str(contract_path or ""), "artifact_sha256", "INVALID_HOLDOUT_CONTRACT_SELF_HASH")
        contract_payload = _load_relative_json(repo, str(contract_path or ""))
        if contract_payload is None:
            _add(reasons, "INVALID_HOLDOUT_CUSTODY_CONTRACT")
        else:
            required_true = (
                "independent_custodian",
                "operator_attestation",
                "independent_reviewer",
                "immutable_storage",
                "encrypted_at_rest",
                "access_log_append_only",
                "raw_holdout_hidden_until_release",
                "one_release_only",
                "no_same_sample_rerun",
                "no_formula_or_family_change_after_seal",
            )
            requirements = contract_payload.get("requirements")
            release_policy = contract_payload.get("release_policy")
            if (
                contract_payload.get("schema_version") != "r46-holdout-custody-contract-v1"
                or contract_payload.get("research_id") != payload.get("research_id")
                or contract_payload.get("candidate_id") != payload.get("candidate_id")
                or contract_payload.get("holdout_id") != f"{payload.get('research_id')}:prospective_holdout"
                or contract_payload.get("mode") != "prospective_single_use"
                or contract_payload.get("historical_holdout_accepted") is not False
                or not isinstance(requirements, dict)
                or any(requirements.get(field) is not True for field in required_true)
                or not isinstance(release_policy, dict)
                or release_policy.get("release_is_single_use") is not True
                or release_policy.get("release_receipt_required") is not True
            ):
                _add(reasons, "INVALID_HOLDOUT_CUSTODY_CONTRACT")
        if holdout.get("mode") != "prospective_single_use" or holdout.get("historical_holdout_accepted") is not False:
            _add(reasons, "INVALID_HOLDOUT_MODE")
        if holdout.get("status") == "BLOCKED_NO_HOLDOUT":
            _add(reasons, "BLOCKED_NO_HOLDOUT")
        elif holdout.get("status") != "SEALED_PROSPECTIVE_SINGLE_USE":
            _add(reasons, "BLOCKED_NO_HOLDOUT")

    stats = payload.get("statistics")
    multiplicity = payload.get("multiplicity")
    if not isinstance(stats, dict) or stats.get("status") != "BLOCKED_STATISTICS" or stats.get("power_justified") is not False or stats.get("minimums") is not None:
        _add(reasons, "INVALID_STATISTICS_BOUNDARY")
    else:
        _add(reasons, "BLOCKED_STATISTICS")
    if not isinstance(multiplicity, dict) or multiplicity.get("status") != "BLOCKED_STATISTICS" or multiplicity.get("family_size") is not None or multiplicity.get("alpha") is not None:
        _add(reasons, "INVALID_MULTIPLICITY_BOUNDARY")
    else:
        _add(reasons, "BLOCKED_STATISTICS")

    execution_contract = payload.get("execution_contract")
    if not isinstance(execution_contract, dict) or execution_contract.get("native_ledger_status") != "READY_NATIVE_LEDGER":
        _add(reasons, "BLOCKED_NATIVE_LEDGER")

    provenance = payload.get("source_provenance")
    if not isinstance(provenance, dict):
        _add(reasons, "BLOCKED_SOURCE_PIN")
        return reasons

    _validate_capability_receipt_binding(
        reasons,
        repo,
        provenance,
        research_id=payload.get("research_id"),
        candidate_id=payload.get("candidate_id"),
    )

    base_commit = provenance.get("base_commit")
    if not isinstance(base_commit, str) or not re.fullmatch(r"[0-9a-f]{40}", base_commit):
        _add(reasons, "BLOCKED_SOURCE_PIN")
    source_commit = provenance.get("source_commit")
    if not isinstance(source_commit, str) or not re.fullmatch(r"[0-9a-f]{40}", source_commit):
        _add(reasons, "BLOCKED_SOURCE_PIN")
    elif isinstance(base_commit, str) and source_commit != base_commit:
        _add(reasons, "BLOCKED_SOURCE_PIN")
    if provenance.get("clean_tree_observed") is not True:
        _add(reasons, "BLOCKED_CLEAN_TREE_NOT_ATTESTED")

    source_files = provenance.get("source_files")
    if not isinstance(source_files, dict) or not source_files:
        _add(reasons, "BLOCKED_SOURCE_PIN")
    else:
        for relative, declaration in source_files.items():
            expected = declaration.get("sha256") if isinstance(declaration, dict) else None
            _validate_static_file_hash(
                reasons,
                repo,
                str(relative),
                expected,
                "BLOCKED_SOURCE_PIN",
                base_commit=str(base_commit) if isinstance(base_commit, str) else None,
            )
            if isinstance(base_commit, str) and not _git_tree_has(repo, base_commit, str(relative)):
                _add(reasons, "BLOCKED_SOURCE_PIN")

    roles = ("runner", "verifier", "preflight", "custody_validator")
    for role in roles:
        declaration = provenance.get(role)
        label = role.upper()
        if not isinstance(declaration, dict):
            _add(reasons, f"BLOCKED_{label}_NOT_PINNED")
            _add(reasons, f"BLOCKED_{label}_NOT_IN_BASE")
            continue
        relative = str(declaration.get("path") or "")
        expected = declaration.get("sha256")
        if not isinstance(expected, str) or not SHA256_RE.fullmatch(expected):
            _add(reasons, f"BLOCKED_{label}_NOT_PINNED")
        _validate_static_file_hash(
            reasons,
            repo,
            relative,
            expected,
            f"BLOCKED_{label}_NOT_PINNED",
            base_commit=str(base_commit) if isinstance(base_commit, str) else None,
        )
        if declaration.get("required_in_base_commit") is not True:
            _add(reasons, f"BLOCKED_{label}_NOT_IN_BASE")
        elif not _git_tree_has(repo, str(base_commit or ""), relative):
            _add(reasons, f"BLOCKED_{label}_NOT_IN_BASE")

    environment = provenance.get("environment")
    if not isinstance(environment, dict):
        _add(reasons, "BLOCKED_ENVIRONMENT_NOT_PINNED")
    else:
        command = environment.get("command")
        dependency_lock = environment.get("dependency_lock_sha256")
        if (
            not isinstance(command, str)
            or not command.strip()
            or command.startswith("UNSET_")
            or not isinstance(dependency_lock, str)
            or not SHA256_RE.fullmatch(dependency_lock)
            or environment.get("network") != "disabled"
        ):
            _add(reasons, "BLOCKED_ENVIRONMENT_NOT_PINNED")

    return reasons


def validate_holdout_receipt(receipt: dict[str, Any], manifest: dict[str, Any], *, repo: Path = REPO) -> list[str]:
    """Validate a future custody receipt without opening its data root."""

    reasons: list[str] = []
    required = (
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
    )
    for field in required:
        if field not in receipt:
            _add(reasons, f"CUSTODY_FIELD_MISSING:{field}")

    expected_research_id = manifest.get("research_id")
    expected_holdout_id = f"{expected_research_id}:prospective_holdout"
    if receipt.get("schema_version") != "r46-holdout-custody-receipt-v1":
        _add(reasons, "CUSTODY_SCHEMA_INVALID")
    if receipt.get("research_id") != expected_research_id or receipt.get("holdout_id") != expected_holdout_id:
        _add(reasons, "CUSTODY_ID_MISMATCH")
    if receipt.get("status") != "SEALED_PROSPECTIVE_SINGLE_USE":
        _add(reasons, "CUSTODY_STATUS_INVALID")
    manifest_integrity = manifest.get("integrity")
    manifest_canonical_hash = None
    if isinstance(manifest_integrity, dict):
        try:
            manifest_canonical_hash = canonical_hash(manifest, "manifest_sha256", nested=True)
        except (TypeError, ValueError, AttributeError, json.JSONDecodeError):
            manifest_canonical_hash = None
    if receipt.get("manifest_sha256") != manifest_canonical_hash:
        _add(reasons, "CUSTODY_MANIFEST_HASH_MISMATCH")
    holdout_contract_path = manifest.get("holdout", {}).get("contract_path") if isinstance(manifest.get("holdout"), dict) else None
    contract_path = _safe_relative_file(repo, holdout_contract_path) if isinstance(holdout_contract_path, str) else None
    contract_canonical_hash = None
    if contract_path is not None:
        try:
            contract_payload = _load_json(contract_path)
            contract_integrity = contract_payload.get("integrity")
            if isinstance(contract_integrity, dict):
                contract_canonical_hash = canonical_hash(contract_payload, "artifact_sha256", nested=True)
        except (OSError, TypeError, AttributeError, ValueError, json.JSONDecodeError):
            contract_canonical_hash = None
    if (
        contract_path is None
        or not isinstance(receipt.get("holdout_contract_sha256"), str)
        or not SHA256_RE.fullmatch(receipt["holdout_contract_sha256"])
        or receipt["holdout_contract_sha256"] != contract_canonical_hash
    ):
        _add(reasons, "CUSTODY_CONTRACT_HASH_MISMATCH")
    for field in ("data_root_sha256", "access_log_sha256"):
        if not isinstance(receipt.get(field), str) or not SHA256_RE.fullmatch(receipt[field]):
            _add(reasons, f"CUSTODY_HASH_INVALID:{field}")
    try:
        sealed_at = datetime.fromisoformat(str(receipt.get("sealed_at", "")).replace("Z", "+00:00"))
        holdout_start = datetime.fromisoformat(str(receipt.get("holdout_start", "")).replace("Z", "+00:00"))
        holdout_end = datetime.fromisoformat(str(receipt.get("holdout_end_exclusive", "")).replace("Z", "+00:00"))
        if holdout_start.tzinfo is None or holdout_end.tzinfo is None or holdout_start >= holdout_end:
            raise ValueError("holdout bounds invalid")
        if sealed_at.tzinfo is None:
            raise ValueError("seal timezone required")
        if sealed_at > holdout_start:
            raise ValueError("holdout sealed after start")
    except ValueError:
        _add(reasons, "CUSTODY_SEAL_TIME_INVALID")
    if receipt.get("storage_class") not in {"WORM", "IMMUTABLE_OBJECT"}:
        _add(reasons, "CUSTODY_STORAGE_NOT_IMMUTABLE")
    for field in ("encrypted_at_rest", "access_log_append_only", "raw_holdout_hidden_until_release"):
        if receipt.get(field) is not True:
            _add(reasons, f"CUSTODY_{field.upper()}_INVALID")
    if receipt.get("outcomes_accessed_before_release") is not False:
        _add(reasons, "CUSTODY_OUTCOME_ACCESS_INVALID")
    if receipt.get("one_release_only") is not True:
        _add(reasons, "CUSTODY_ONE_RELEASE_POLICY_INVALID")
    if receipt.get("release_count") != 0:
        _add(reasons, "CUSTODY_RELEASE_COUNT_INVALID")

    operator = receipt.get("operator")
    custodian = receipt.get("custodian")
    reviewer = receipt.get("independent_reviewer")
    actor_ids: list[str] = []
    for label, actor in (("operator", operator), ("custodian", custodian), ("independent_reviewer", reviewer)):
        if (
            not isinstance(actor, dict)
            or not isinstance(actor.get("id"), str)
            or not actor["id"].strip()
            or not isinstance(actor.get("signature"), str)
            or not actor["signature"].strip()
        ):
            _add(reasons, "CUSTODY_SIGNATURE_MISSING")
        else:
            actor_ids.append(actor["id"])
    if isinstance(operator, dict) and isinstance(custodian, dict) and operator.get("id") == custodian.get("id"):
        _add(reasons, "CUSTODIAN_MUST_DIFFER_FROM_OPERATOR")
    if len(actor_ids) == 3 and len(set(actor_ids)) != 3:
        _add(reasons, "CUSTODY_ACTORS_NOT_DISTINCT")
    return reasons


def validate_receipt(path: Path, *, repo: Path = REPO) -> dict[str, Any]:
    try:
        receipt = _load_json(path)
        manifest = _load_json(repo / MANIFEST.relative_to(REPO))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return {"status": "INVALID", "reasons": [f"INVALID_RECEIPT:{exc}"]}
    reasons = validate_manifest(repo)
    reasons.extend(validate_holdout_receipt(receipt, manifest, repo=repo))
    _add(reasons, "BLOCKED_SIGNATURE_VERIFICATION")
    return {"status": "VALID" if not reasons else "INVALID", "reasons": reasons}


def validate_bundle(*, repo: Path = REPO) -> dict[str, Any]:
    reasons = validate_manifest(repo)
    _add(reasons, "BLOCKED_SIGNATURE_VERIFICATION")
    return {"status": "READY" if not reasons else "BLOCKED", "reasons": reasons}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--receipt", type=Path, help="validate a custody receipt without reading its data root")
    parser.add_argument("--json", action="store_true", help="emit machine-readable output")
    args = parser.parse_args(argv)
    if args.receipt is not None:
        result = validate_receipt(args.receipt)
        print(json.dumps(result, sort_keys=True))
        return 0 if result["status"] == "VALID" else 1
    result = validate_bundle()
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
