"""Read-only bridge from the R46 governance manifest to a data package.

The preflight consumes only hash-pinned local artifacts.  It never downloads
data and never calls the backtest engine.  Governance and data-package
manifests are deliberately separate: the former freezes the contract while
the latter identifies acquired evidence and its local artifact hashes.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from pathlib import Path
from typing import Any

from scripts.research.r46_treasury_auction_readiness_2026_09_10 import (
    CUTOFF_UTC,
    DATA_PACKAGE_SCHEMA,
    GOVERNANCE_SCHEMA,
    HEX64_RE,
    normalize_auction_documents,
    validate_nyse_calendar,
    validate_tlt_bars,
    validate_tlt_dividends,
    validate_tlt_eligibility,
    verify_r46_package,
)

REPO = Path(__file__).resolve().parents[2]
SHA40_RE = re.compile(r"^[0-9a-f]{40}$")
CAPABILITY_RECEIPT_SCHEMA = "r46-treasury-source-capability-v1"
CAPABILITY_RECEIPT_STATUS = "CAPABILITY_OR_PAID_BLOCKED"
CAPABILITY_ROUTE_STATUS = "PUBLIC_ROUTES_IDENTIFIED"
CAPABILITY_ADMISSION_STATUS = "DATA_ACQUISITION_REQUIRED"
CAPABILITY_RECEIPT_BLOCKER = "governance_source_capability_receipt"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _canonical_hash(payload: dict[str, Any], field: str) -> str:
    canonical = json.loads(json.dumps(payload, ensure_ascii=False))
    integrity = canonical.get("integrity")
    if not isinstance(integrity, dict):
        raise ValueError("integrity object missing")
    integrity.pop(field, None)
    encoded = json.dumps(canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _safe_path(root: Path, value: Any) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("artifact path missing")
    relative = Path(value)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("artifact path unsafe")
    root_resolved = root.resolve()
    try:
        path = (root / relative).resolve(strict=True)
    except (OSError, ValueError) as exc:
        raise ValueError(f"artifact file missing:{value}") from exc
    if root_resolved not in path.parents or not path.is_file():
        raise ValueError(f"artifact file outside root:{value}")
    return path


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"json object required:{path}")
    return value


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(f"jsonl row is not an object:{line_number}")
        rows.append(value)
    return rows


def _read_csv(path: Path) -> list[dict[str, Any]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def _blocked(*blockers: str, checked_artifacts: int = 0) -> dict[str, Any]:
    return {
        "schema": DATA_PACKAGE_SCHEMA,
        "status": "DATA_ACQUISITION_REQUIRED",
        "blockers": sorted(set(blockers)),
        "checked_artifacts": checked_artifacts,
        "economic_execution": False,
        "no_auto_promotion": True,
        "read_only": True,
    }


def _safe_canonical_hash(payload: dict[str, Any], field: str) -> str | None:
    try:
        return _canonical_hash(payload, field)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None


def _safe_top_level_hash(payload: dict[str, Any], field: str) -> str | None:
    try:
        value = json.loads(json.dumps(payload, ensure_ascii=False))
        value.pop(field, None)
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()
    except (TypeError, ValueError, json.JSONDecodeError):
        return None


def _pinned_json(
    root: Path,
    relative: Any,
    expected: Any,
    blocker: str,
    *,
    canonical_field: str | None = None,
) -> tuple[Path | None, dict[str, Any] | None, list[str]]:
    reasons: list[str] = []
    try:
        path = _safe_path(root, relative)
    except ValueError:
        return None, None, [blocker]
    if not isinstance(expected, str) or not HEX64_RE.fullmatch(expected) or _sha256(path) != expected:
        reasons.append(blocker)
    try:
        payload = _load_json(path)
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return path, None, reasons + [blocker]
    if canonical_field is not None:
        integrity = payload.get("integrity")
        declared = integrity.get(canonical_field) if isinstance(integrity, dict) else None
        canonical = _safe_canonical_hash(payload, canonical_field)
        if not isinstance(declared, str) or not HEX64_RE.fullmatch(declared) or declared != canonical:
            reasons.append(f"{blocker}_SELF_HASH")
    return path, payload, sorted(set(reasons))


def _capability_receipt_blockers(manifest: dict[str, Any], *, provenance: dict[str, Any], root: Path) -> list[str]:
    binding = provenance.get("source_capability_receipt")
    if not isinstance(binding, dict):
        return [CAPABILITY_RECEIPT_BLOCKER]
    if (
        not isinstance(binding.get("artifact_sha256"), str)
        or not HEX64_RE.fullmatch(binding["artifact_sha256"])
        or binding.get("status") != CAPABILITY_RECEIPT_STATUS
        or binding.get("route_capability_status") != CAPABILITY_ROUTE_STATUS
        or binding.get("r46_admission_status") != CAPABILITY_ADMISSION_STATUS
    ):
        return [CAPABILITY_RECEIPT_BLOCKER]
    try:
        path = _safe_path(root, binding.get("path"))
    except ValueError:
        return [CAPABILITY_RECEIPT_BLOCKER]
    expected_raw_hash = binding.get("sha256")
    blockers: list[str] = []
    try:
        if not isinstance(expected_raw_hash, str) or not HEX64_RE.fullmatch(expected_raw_hash) or _sha256(path) != expected_raw_hash:
            blockers.append(CAPABILITY_RECEIPT_BLOCKER)
        receipt = _load_json(path)
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return [CAPABILITY_RECEIPT_BLOCKER]
    if (
        receipt.get("schema") != CAPABILITY_RECEIPT_SCHEMA
        or receipt.get("research_id") != manifest.get("research_id")
        or receipt.get("candidate_id") != manifest.get("candidate_id")
        or receipt.get("status") != CAPABILITY_RECEIPT_STATUS
        or receipt.get("route_capability_status") != CAPABILITY_ROUTE_STATUS
        or receipt.get("r46_admission_status") != CAPABILITY_ADMISSION_STATUS
        or receipt.get("artifact_sha256") != binding.get("artifact_sha256")
    ):
        blockers.append(CAPABILITY_RECEIPT_BLOCKER)
    receipt_hash = receipt.get("artifact_sha256")
    if (
        not isinstance(receipt_hash, str)
        or not HEX64_RE.fullmatch(receipt_hash)
        or receipt_hash != _safe_top_level_hash(receipt, "artifact_sha256")
    ):
        blockers.append(CAPABILITY_RECEIPT_BLOCKER)
    return sorted(set(blockers))


def _governance_blockers(manifest: dict[str, Any], *, root: Path) -> list[str]:
    blockers: list[str] = []
    if manifest.get("schema_version") != GOVERNANCE_SCHEMA:
        return ["manifest_schema"]
    if manifest.get("candidate_id") != "R46" or not isinstance(manifest.get("research_id"), str) or not manifest["research_id"].strip():
        blockers.append("manifest_identity")
    if manifest.get("status") != "FROZEN_PRE_OUTCOME":
        blockers.append("manifest_status")
    for field in ("declared_before_outcomes", "research_only", "paper_live_untouched", "no_auto_promotion"):
        if manifest.get(field) is not True:
            blockers.append(f"manifest_boundary:{field}")
    for field in ("economic_execution", "economic_run", "economic_outcomes_accessed", "outcome_artifacts_present", "promotion", "integration_candidate", "paper_or_live_activation", "auto_promotion"):
        if manifest.get(field) is not False:
            blockers.append(f"manifest_boundary:{field}")

    integrity = manifest.get("integrity")
    if not isinstance(integrity, dict) or integrity.get("manifest_sha256") != _safe_canonical_hash(manifest, "manifest_sha256"):
        blockers.append("governance_manifest_hash")
    else:
        _, _, reasons = _pinned_json(
            root,
            integrity.get("schema_path"),
            integrity.get("schema_sha256"),
            "governance_schema_pin",
        )
        blockers.extend(reasons)

    data_contract = manifest.get("data_contract")
    if not isinstance(data_contract, dict):
        blockers.append("governance_data_contract")
    else:
        _, source_matrix, reasons = _pinned_json(
            root,
            data_contract.get("source_matrix_path"),
            data_contract.get("source_matrix_sha256"),
            "governance_source_matrix_pin",
            canonical_field="artifact_sha256",
        )
        blockers.extend(reasons)
        if source_matrix is None or source_matrix.get("research_id") != manifest.get("research_id") or source_matrix.get("candidate_id") != manifest.get("candidate_id"):
            blockers.append("governance_source_matrix_contract")
        if data_contract.get("pit_source_status") != "READY_PIT_SOURCE":
            blockers.append("BLOCKED_PIT_SOURCE")
        elif (
            not isinstance(data_contract.get("pit_source_attestation_path"), str)
            or not isinstance(data_contract.get("pit_source_attestation_sha256"), str)
            or not HEX64_RE.fullmatch(data_contract["pit_source_attestation_sha256"])
        ):
            blockers.append("BLOCKED_PIT_SOURCE")

    holdout = manifest.get("holdout")
    if not isinstance(holdout, dict):
        blockers.append("BLOCKED_NO_HOLDOUT")
    else:
        _, contract, reasons = _pinned_json(
            root,
            holdout.get("contract_path"),
            holdout.get("contract_sha256"),
            "governance_holdout_contract_pin",
            canonical_field="artifact_sha256",
        )
        blockers.extend(reasons)
        if contract is None or contract.get("research_id") != manifest.get("research_id") or contract.get("candidate_id") != manifest.get("candidate_id"):
            blockers.append("governance_holdout_contract")
        if (
            holdout.get("status") != "SEALED_PROSPECTIVE_SINGLE_USE"
            or holdout.get("mode") != "prospective_single_use"
            or holdout.get("historical_holdout_accepted") is not False
            or holdout.get("accessed") is not False
            or not isinstance(holdout.get("custody_receipt_path"), str)
            or not holdout["custody_receipt_path"].strip()
        ):
            blockers.append("BLOCKED_NO_HOLDOUT")

    statistics = manifest.get("statistics")
    multiplicity = manifest.get("multiplicity")
    if not isinstance(statistics, dict) or statistics.get("status") != "READY_STATISTICS":
        blockers.append("BLOCKED_STATISTICS")
    if not isinstance(multiplicity, dict) or multiplicity.get("status") != "READY_MULTIPLICITY":
        blockers.append("BLOCKED_STATISTICS")

    execution = manifest.get("execution_contract")
    if not isinstance(execution, dict) or execution.get("native_ledger_status") != "READY_NATIVE_LEDGER":
        blockers.append("BLOCKED_NATIVE_LEDGER")
    elif (
        not isinstance(execution.get("native_ledger_attestation_path"), str)
        or not isinstance(execution.get("native_ledger_attestation_sha256"), str)
        or not HEX64_RE.fullmatch(execution["native_ledger_attestation_sha256"])
    ):
        blockers.append("BLOCKED_NATIVE_LEDGER")

    provenance = manifest.get("source_provenance")
    if not isinstance(provenance, dict) or not isinstance(provenance.get("base_commit"), str) or not SHA40_RE.fullmatch(provenance["base_commit"]) or provenance.get("clean_tree_observed") is not True:
        blockers.append("BLOCKED_SOURCE_PIN")
    if isinstance(provenance, dict):
        blockers.extend(_capability_receipt_blockers(manifest, provenance=provenance, root=root))
        for role in ("runner", "verifier", "preflight", "custody_validator"):
            declaration = provenance.get(role)
            if not isinstance(declaration, dict) or not isinstance(declaration.get("sha256"), str) or not HEX64_RE.fullmatch(declaration["sha256"]) or declaration.get("required_in_base_commit") is not True:
                blockers.append(f"BLOCKED_{role.upper()}_NOT_PINNED")
        environment = provenance.get("environment")
        if not isinstance(environment, dict) or not isinstance(environment.get("command"), str) or not environment["command"].strip() or environment["command"].startswith("UNSET_") or not isinstance(environment.get("dependency_lock_sha256"), str) or not HEX64_RE.fullmatch(environment["dependency_lock_sha256"]) or environment.get("network") != "disabled":
            blockers.append("BLOCKED_ENVIRONMENT_NOT_PINNED")
    return sorted(set(blockers))


def _relative_to_root(root: Path, path: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def _contained_existing_file(root: Path, path: Path) -> Path:
    resolved_root = root.resolve()
    resolved = path.resolve(strict=True)
    if resolved_root not in resolved.parents or not resolved.is_file():
        raise ValueError("file outside root")
    return resolved


def _receipt_reference(
    root: Path,
    spec: Any,
    *,
    blocker: str,
    governance: dict[str, Any],
    contract_canonical_hash: str | None = None,
    expected_path: Any = None,
) -> tuple[dict[str, Any] | None, list[str]]:
    if not isinstance(spec, dict):
        return None, [blocker]
    try:
        path = _safe_path(root, spec.get("path"))
    except ValueError:
        return None, [blocker]
    expected = spec.get("sha256")
    reasons: list[str] = []
    if expected_path is not None:
        try:
            if path != _safe_path(root, expected_path):
                reasons.append(blocker)
        except ValueError:
            reasons.append(blocker)
    if not isinstance(expected, str) or not HEX64_RE.fullmatch(expected) or _sha256(path) != expected:
        reasons.append(blocker)
    try:
        receipt = _load_json(path)
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return None, reasons + [blocker]
    manifest_hash = receipt.get("governance_manifest_sha256", receipt.get("manifest_sha256"))
    if manifest_hash != _safe_canonical_hash(governance, "manifest_sha256"):
        reasons.append(blocker)
    if receipt.get("research_id") != governance.get("research_id") or receipt.get("candidate_id", governance.get("candidate_id")) != governance.get("candidate_id"):
        reasons.append(blocker)
    if contract_canonical_hash is not None and receipt.get("holdout_contract_sha256") != contract_canonical_hash:
        reasons.append(blocker)
    receipt["_hash_pinned"] = not reasons
    return receipt, sorted(set(reasons))


def _validate_data_package_manifest(
    package: dict[str, Any],
    *,
    governance: dict[str, Any],
    governance_path: Path,
    root: Path,
) -> tuple[dict[str, Path], dict[str, Any], list[str]]:
    blockers: list[str] = []
    paths: dict[str, Path] = {}
    receipts: dict[str, Any] = {}
    if package.get("schema") != DATA_PACKAGE_SCHEMA:
        return paths, receipts, ["data_package_schema"]
    integrity = package.get("integrity")
    if not isinstance(integrity, dict) or integrity.get("manifest_sha256") != _safe_canonical_hash(package, "manifest_sha256"):
        blockers.append("data_package_manifest_hash")
    try:
        governance_relative = _relative_to_root(root, governance_path)
    except ValueError:
        governance_relative = ""
    if package.get("governance_manifest_path") != governance_relative or package.get("governance_manifest_sha256") != _safe_canonical_hash(governance, "manifest_sha256"):
        blockers.append("governance_data_package_crosslink")
    if package.get("research_id") != governance.get("research_id") or package.get("candidate_id") != governance.get("candidate_id"):
        blockers.append("data_package_identity")
    if package.get("historical_cutoff") != CUTOFF_UTC:
        blockers.append("historical_cutoff")

    artifacts = package.get("artifacts")
    required_artifacts = {"documents", "bars", "dividends", "eligibility", "calendar"}
    if not isinstance(artifacts, dict) or set(artifacts) != required_artifacts:
        blockers.append("artifact_declarations")
    else:
        for name in sorted(required_artifacts):
            spec = artifacts.get(name)
            if not isinstance(spec, dict) or not isinstance(spec.get("sha256"), str) or not HEX64_RE.fullmatch(spec["sha256"]):
                blockers.append(f"artifact_declaration:{name}")
                continue
            try:
                path = _safe_path(root, spec.get("path"))
            except ValueError:
                blockers.append(f"artifact_missing:{name}")
                continue
            if _sha256(path) != spec["sha256"]:
                blockers.append(f"artifact_hash:{name}")
                continue
            paths[name] = path

    market_data = package.get("market_data")
    if not isinstance(market_data, dict):
        blockers.append("market_data_contract")
    else:
        for name in ("bars", "dividends", "calendar"):
            spec = market_data.get(name)
            if not isinstance(spec, dict) or not isinstance(spec.get("available_at"), str):
                blockers.append(f"{name}_availability")

    contract_path = governance.get("holdout", {}).get("contract_path") if isinstance(governance.get("holdout"), dict) else None
    contract_canonical = None
    if isinstance(contract_path, str):
        try:
            contract = _load_json(_safe_path(root, contract_path))
            contract_canonical = contract.get("integrity", {}).get("artifact_sha256") if isinstance(contract.get("integrity"), dict) else None
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            contract_canonical = None

    if governance.get("data_contract", {}).get("pit_source_status") == "READY_PIT_SOURCE":
        data_contract = governance.get("data_contract", {})
        receipt, reasons = _receipt_reference(
            root,
            package.get("pit_source_receipt"),
            blocker="BLOCKED_PIT_SOURCE",
            governance=governance,
            expected_path=data_contract.get("pit_source_attestation_path"),
        )
        blockers.extend(reasons)
        if receipt is not None:
            receipts["pit_source"] = receipt
    if governance.get("execution_contract", {}).get("native_ledger_status") == "READY_NATIVE_LEDGER":
        execution_contract = governance.get("execution_contract", {})
        receipt, reasons = _receipt_reference(
            root,
            package.get("native_ledger_receipt"),
            blocker="BLOCKED_NATIVE_LEDGER",
            governance=governance,
            expected_path=execution_contract.get("native_ledger_attestation_path"),
        )
        blockers.extend(reasons)
        if receipt is not None:
            receipts["native_ledger"] = receipt
    if governance.get("holdout", {}).get("status") == "SEALED_PROSPECTIVE_SINGLE_USE":
        receipt, reasons = _receipt_reference(
            root,
            package.get("holdout_receipt"),
            blocker="BLOCKED_NO_HOLDOUT",
            governance=governance,
            contract_canonical_hash=contract_canonical if isinstance(contract_canonical, str) else None,
            expected_path=governance.get("holdout", {}).get("custody_receipt_path"),
        )
        blockers.extend(reasons)
        if receipt is not None:
            receipts["holdout"] = receipt
    if "holdout_attestation" in package or "pit_source_attestation" in package or "native_ledger_attestation" in package:
        blockers.append("embedded_attestation_forbidden")
    return paths, receipts, sorted(set(blockers))


def verify_r46_manifest(
    manifest_path: Path,
    *,
    data_package_manifest_path: Path | None = None,
    repo_root: Path | None = None,
) -> dict[str, Any]:
    """Verify governance plus a separate local data package, fail-closed."""

    root = (repo_root or REPO).resolve()
    try:
        governance_path = _contained_existing_file(root, manifest_path)
        governance = _load_json(governance_path)
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return _blocked("manifest_unreadable")
    governance_blockers = _governance_blockers(governance, root=root)
    if governance.get("schema_version") != GOVERNANCE_SCHEMA:
        return _blocked(*governance_blockers)
    if data_package_manifest_path is None:
        return _blocked(*governance_blockers, "data_package_manifest_required")
    try:
        package_path = _contained_existing_file(root, data_package_manifest_path)
        package = _load_json(package_path)
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return _blocked(*governance_blockers, "data_package_manifest_unreadable")
    package_paths, receipts, package_blockers = _validate_data_package_manifest(
        package,
        governance=governance,
        governance_path=governance_path,
        root=root,
    )
    if governance_blockers or package_blockers:
        return _blocked(*governance_blockers, *package_blockers, checked_artifacts=0)

    try:
        documents = _read_jsonl(package_paths["documents"])
        auction = normalize_auction_documents(documents, repo_root=root)
        expected_ids = package.get("expected_event_ids")
        if package.get("expected_event_count") != auction.get("admitted_event_count") or sorted(expected_ids or []) != sorted(event.get("canonical_event_id") for event in auction.get("events", [])):
            return _blocked("auction_archive_completeness", checked_artifacts=5)
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return _blocked("auction_lineage", checked_artifacts=0)
    try:
        market_data = package["market_data"]
        bars = validate_tlt_bars(_read_csv(package_paths["bars"]), available_at=market_data["bars"]["available_at"])
        dividends = validate_tlt_dividends(_read_csv(package_paths["dividends"]), available_at=market_data["dividends"]["available_at"])
        eligibility = validate_tlt_eligibility(_read_jsonl(package_paths["eligibility"]))
        calendar = validate_nyse_calendar(_read_jsonl(package_paths["calendar"]), available_at=market_data["calendar"]["available_at"])
    except (OSError, TypeError, ValueError, json.JSONDecodeError, csv.Error):
        return _blocked("market_data_validation", checked_artifacts=1)

    result = verify_r46_package(
        manifest=governance,
        auction=auction,
        bars=bars,
        dividends=dividends,
        eligibility=eligibility,
        calendar=calendar,
        holdout_attestation=receipts.get("holdout", {}),
    )
    return {
        **result,
        "checked_artifacts": 5,
        "read_only": True,
        "governance_manifest_sha256": _safe_canonical_hash(governance, "manifest_sha256"),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("governance_manifest", type=Path)
    parser.add_argument("--data-package-manifest", type=Path)
    parser.add_argument("--repo-root", type=Path)
    args = parser.parse_args(argv)
    result = verify_r46_manifest(
        args.governance_manifest,
        data_package_manifest_path=args.data_package_manifest,
        repo_root=args.repo_root,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
