#!/usr/bin/env python3
"""Independent verifier for the future R46 v3 recovery economic bundle.

This module has no import edge to the v3 runner.  It verifies the persisted
provenance and one-shot chain first, then delegates only the pure numerical
ledger checks to the already frozen v2 verifier after an in-memory provenance
normalization.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from scripts.research import verify_r46_treasury_auction_unwind_tlt_v2_2026_09_10 as frozen_verifier

REPO = Path(__file__).resolve().parents[2]
V2_RESEARCH_ID = "r46_treasury_auction_unwind_tlt_v2_2026_09_10"
V3_RESEARCH_ID = "r46_treasury_auction_unwind_tlt_v3_recovery_2026_09_14"
CANDIDATE_ID = "R46"
RECOVERY_GENERATION = 1
CLAIM_POLICY = "RETROSPECTIVE_DESCRIPTIVE_ONLY_ORIGIN_UNPROVEN"
PARENT_GOVERNANCE_COMMIT = "640097e30db857a011caa5d623e08541b2793cc8"
V2_GOVERNANCE_SHA256 = "92f7ecd5b7f6cdd2428ee221c3b4891f10d5d7ab6f16a5fcb2be952753557c35"
V2_PACKAGE_SHA256 = "c67cfdfeebc4cbc0847d5e2e6fbdebdf6928e04b3e5103606eb8bd0c3d03f6c6"
V2_STATISTICS_SHA256 = "21ebb2e453bd3587388fa800919d5ca24eb9fab4d29b21c106d8e1ce8a015331"
V2_FAMILY_SHA256 = "eeda04d795d099e7571bdbeae0ccaa2dc55141ec2f15d1f8afdc507d60b9acae"
V2_RESERVED_SHA256 = "434b5ef71015d132516474a11078c4e5866d3c91e7d265a7f7c3b20d655141e4"
V2_RESERVED_TRANSITION_SHA256 = "040d13dcba81e3d52e7d105aea9250a3c565e7b87e9b6af5c44e83b0dec28405"
V2_STATE_SHA256 = "c69e6bd76964131f41a02b4a58a1a308af1a2a1a038c8e339b1512b097b2f412"
V2_STATE_CANONICAL_SHA256 = "b2977b5f516cd4371af2201a87cb84637beb47214e74cc3d0980bbc9eaf33fab"
V3_STATE_CANONICAL_SHA256 = "0165ea19b68a5e913927cb5016a703ccc9d2be73ba01a7515372727b0bdb9421"
V3_GOVERNANCE_SHA256 = "bc49ab40e00e21deb8b4ef305c18400665d4154d4c1077a8a31dbc7da452c3db"
V3_INCIDENT_SHA256 = "bdd668747423960faddbb31982566e6c49fcc3b64fd32bb29c40775a621acb8b"
V3_INCIDENT_PHYSICAL_SHA256 = "7be069ba5a29a79313bdf77b3355ff5d5fdf319b624a3d5b6da30291609d74c0"
TRANSITION_SCHEMA = "r46-recovery-one-shot-transition-v3"
AUTHORIZATION_SCHEMA = "r46-v3-recovery-execution-authorization-v1"
PREFLIGHT_SCHEMA = "r46-v3-recovery-preflight-receipt-v1"
ECONOMIC_RUN_SCHEMA = "r46-v3-recovery-economic-run-v1"
ECONOMIC_LEDGER_STATUS = "R46_V3_RECOVERY_ECONOMIC_RUN"
V2_ECONOMIC_RUN_SCHEMA = "r46-v2-economic-run-v1"
V2_ECONOMIC_LEDGER_STATUS = "R46_V2_ECONOMIC_RUN"
V3_STATE_REL = Path("data/research_manifests/r46_treasury_auction_unwind_tlt_v3_recovery_2026_09_14_one_shot_state.json")
V3_GOVERNANCE_REL = Path("data/research_manifests/r46_treasury_auction_unwind_tlt_v3_recovery_2026_09_14.json")
V3_CONTRACT_REL = Path("data/research_manifests/r46_treasury_auction_unwind_tlt_v3_recovery_2026_09_14_execution_contract.json")
V3_PREFLIGHT_REL = Path("data/research_receipts/r46_v3_recovery_preflight_2026_09_14.json")
V3_AUTHORIZATION_REL = Path("data/research_receipts/r46_v3_execution_authorization_2026_09_14.json")
V3_INCIDENT_REL = Path("data/research_receipts/r46_v2_reservation_incident_2026_09_14.json")
V2_STATE_REL = Path("data/research_manifests/r46_treasury_auction_unwind_tlt_v2_2026_09_10_one_shot_state.json")
V2_GOVERNANCE_REL = Path("data/research_manifests/r46_treasury_auction_unwind_tlt_v2_2026_09_10.json")
V2_PACKAGE_REL = Path("data/research_manifests/r46_treasury_auction_unwind_tlt_v2_2026_09_10_data_package.json")
V2_STATISTICS_REL = Path("data/research_manifests/r46_treasury_auction_unwind_tlt_v2_2026_09_10_statistics_plan.json")
V2_FAMILY_REL = Path("data/research_manifests/r46_treasury_auction_unwind_tlt_v2_2026_09_10_family_registry.json")
V2_RESERVED_REL = Path("data/research_manifests/r46_treasury_auction_unwind_tlt_v2_2026_09_10_one_shot_state.json.reserved.json")
_HEX64 = set("0123456789abcdef")
_HEX40 = set("0123456789abcdef")


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
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


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
    return isinstance(value, str) and len(value) == 40 and set(value) <= _HEX40


def _resolve(root: Path, value: object, field: str, *, must_exist: bool = True) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field}_path_invalid")
    candidate = Path(value)
    path = (candidate if candidate.is_absolute() else root / candidate).resolve()
    try:
        path.relative_to(root.resolve())
    except ValueError as exc:
        raise ValueError(f"{field}_outside_root") from exc
    if must_exist and not path.is_file():
        raise ValueError(f"{field}_missing")
    return path


def _state_sidecar(state: Path, kind: str) -> Path:
    return state.with_name(f"{state.name}.{kind}.json")


def _canonical_file_hash(path: Path, field: str) -> str:
    payload = _load_json(path)
    integrity = payload.get("integrity")
    if not isinstance(integrity, Mapping) or not isinstance(integrity.get(field), str):
        raise ValueError(f"{field}_missing")
    computed = _canonical_hash(payload, field)
    if computed != integrity.get(field):
        raise ValueError(f"{field}_mismatch")
    return computed


def _git(root: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", *arguments],
        cwd=root,
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        raise ValueError("v3_git_lineage_invalid")
    return completed.stdout.strip()


def _verify_inner_ledger_hashes(value: object) -> None:
    if isinstance(value, Mapping):
        declared = value.get("ledger_sha256")
        if declared is not None:
            if not isinstance(declared, str) or declared != _canonical_hash(value, "ledger_sha256", nested=False):
                raise ValueError("v3_ledger_hash")
        for child in value.values():
            _verify_inner_ledger_hashes(child)
    elif isinstance(value, list):
        for child in value:
            _verify_inner_ledger_hashes(child)


def _verify_statistics_crosslink(results: Mapping[str, Any], report: Mapping[str, Any]) -> None:
    statistics = results.get("statistics")
    if not isinstance(statistics, Mapping):
        raise ValueError("v3_statistics_missing")
    for field in (
        "primary_estimand",
        "unchanged_incumbent_comparison",
        "timing_placebo_plus_10_sessions",
        "cost_outputs",
        "capacity",
        "missingness",
        "resampling",
        "holm",
        "coverage_gates",
        "statistics_pins",
    ):
        if statistics.get(field) != report.get(field):
            raise ValueError("v3_statistics_crosslink")
    primary = statistics.get("primary_estimand")
    if not isinstance(primary, Mapping):
        raise ValueError("v3_primary_statistic_missing")
    rows = primary.get("rows")
    if not isinstance(rows, list):
        raise ValueError("v3_primary_rows_missing")
    if primary.get("event_episode_count") != len(rows):
        raise ValueError("v3_primary_row_count")
    if rows:
        deltas = [float(row["delta"]) for row in rows if isinstance(row, Mapping) and "delta" in row]
        if len(deltas) != len(rows) or abs(float(primary.get("equal_weight_delta")) - sum(deltas) / len(deltas)) > 1e-9:
            raise ValueError("v3_primary_statistic_formula")


def _verify_base_state(path: Path) -> tuple[dict[str, Any], str, str]:
    state = _load_json(path)
    if (
        state.get("schema_version") != "r46-recovery-one-shot-state-v3"
        or state.get("research_id") != V3_RESEARCH_ID
        or state.get("candidate_id") != CANDIDATE_ID
        or state.get("recovery_generation") != RECOVERY_GENERATION
        or state.get("status") != "SEALED_UNUSED"
    ):
        raise ValueError("v3_state_invalid")
    raw_hash = _sha256_file(path)
    canonical_hash = _canonical_file_hash(path, "artifact_sha256")
    if state.get("integrity", {}).get("artifact_sha256") != canonical_hash:
        raise ValueError("v3_state_canonical_hash")
    return state, raw_hash, canonical_hash


def _verify_transition(path: Path, state_path: Path, expected_status: str) -> dict[str, Any]:
    transition = _load_json(path)
    if transition.get("schema_version") != TRANSITION_SCHEMA or transition.get("status") != expected_status:
        raise ValueError("v3_transition_schema_or_status")
    if transition.get("state_path") != str(state_path):
        raise ValueError("v3_transition_state_path")
    state, raw_hash, canonical_hash = _verify_base_state(state_path)
    del state
    if transition.get("state_sha256") != raw_hash or transition.get("state_canonical_sha256") != canonical_hash:
        raise ValueError("v3_transition_state_hash")
    integrity = transition.get("integrity")
    if not isinstance(integrity, Mapping) or integrity.get("transition_sha256") != _canonical_hash(
        transition, "transition_sha256"
    ):
        raise ValueError("v3_transition_hash")
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
    if any(not _valid_hex64(transition.get(field)) for field in required):
        raise ValueError("v3_transition_provenance")
    if expected_status == "RESERVED":
        return transition
    if expected_status != "CONSUMED":
        return transition
    if not _valid_hex64(transition.get("economic_output_bundle_sha256")):
        raise ValueError("v3_transition_output_hash")
    output = transition.get("output_path")
    receipt = transition.get("receipt_path")
    if not isinstance(output, str) or not Path(output).is_absolute() or not isinstance(receipt, str) or not Path(receipt).is_absolute():
        raise ValueError("v3_transition_output_path")
    if Path(receipt).parent != Path(output):
        raise ValueError("v3_transition_output_path")
    reserved_path = _state_sidecar(state_path, "reserved")
    if not reserved_path.is_file():
        raise ValueError("v3_transition_reserved_missing")
    reserved = _verify_transition(reserved_path, state_path, "RESERVED")
    if transition.get("reserved_transition_sha256") != reserved["integrity"]["transition_sha256"]:
        raise ValueError("v3_transition_chain")
    return transition


def _verify_parent_evidence(root: Path, results: Mapping[str, Any]) -> None:
    expected_files = (
        (V2_GOVERNANCE_REL, V2_GOVERNANCE_SHA256),
        (V2_PACKAGE_REL, V2_PACKAGE_SHA256),
        (V2_STATISTICS_REL, V2_STATISTICS_SHA256),
        (V2_FAMILY_REL, V2_FAMILY_SHA256),
    )
    for relative, expected in expected_files:
        path = _resolve(root, str(relative), relative.name)
        payload = _load_json(path)
        field = "manifest_sha256" if relative == V2_GOVERNANCE_REL or relative == V2_PACKAGE_REL else "artifact_sha256"
        if _canonical_file_hash(path, field) != expected:
            raise ValueError("v2_evidence_hash")
        del payload
    v2_state = _resolve(root, str(V2_STATE_REL), "v2_state")
    if _sha256_file(v2_state) != V2_STATE_SHA256 or _canonical_file_hash(v2_state, "artifact_sha256") != V2_STATE_CANONICAL_SHA256:
        raise ValueError("v2_state_hash")
    reserved = _resolve(root, str(V2_RESERVED_REL), "v2_reserved")
    if _sha256_file(reserved) != V2_RESERVED_SHA256:
        raise ValueError("v2_reserved_hash")
    reservation = _load_json(reserved)
    if reservation.get("status") != "RESERVED" or reservation.get("integrity", {}).get("transition_sha256") != V2_RESERVED_TRANSITION_SHA256:
        raise ValueError("v2_reserved_transition")
    for relative in (
        Path("data/research_manifests/r46_treasury_auction_unwind_tlt_v2_2026_09_10_one_shot_state.json.consumed.json"),
        Path("data/research_manifests/r46_treasury_auction_unwind_tlt_v2_2026_09_10_one_shot_state.json.aborted_consumed.json"),
    ):
        if _resolve(root, str(relative), relative.name, must_exist=False).exists():
            raise ValueError("v2_terminal_transition_present")
    if results.get("parent_v2_governance_canonical_sha256") != V2_GOVERNANCE_SHA256 or results.get(
        "parent_v2_package_canonical_sha256"
    ) != V2_PACKAGE_SHA256:
        raise ValueError("v3_parent_provenance")
    if results.get("parent_v2_reservation_physical_sha256") != V2_RESERVED_SHA256 or results.get(
        "parent_v2_reservation_transition_sha256"
    ) != V2_RESERVED_TRANSITION_SHA256:
        raise ValueError("v3_reservation_provenance")


def _verify_v3_provenance(
    results: Mapping[str, Any],
    ledger: Mapping[str, Any],
    report: Mapping[str, Any],
    receipt: Mapping[str, Any] | None,
    *,
    root: Path | None = None,
) -> None:
    if (
        results.get("schema_version") != ECONOMIC_RUN_SCHEMA
        or results.get("research_id") != V3_RESEARCH_ID
        or results.get("candidate_id") != CANDIDATE_ID
        or results.get("recovery_generation") != RECOVERY_GENERATION
        or results.get("claim_policy") != CLAIM_POLICY
        or results.get("parent_research_id") != V2_RESEARCH_ID
    ):
        raise ValueError("v3_results_identity")
    if ledger.get("schema_version") != f"{ECONOMIC_RUN_SCHEMA}:ledger" or ledger.get("status") != ECONOMIC_LEDGER_STATUS:
        raise ValueError("v3_ledger_identity")
    if report.get("schema_version") != f"{ECONOMIC_RUN_SCHEMA}:report" or report.get("research_id") != V3_RESEARCH_ID:
        raise ValueError("v3_report_identity")
    if receipt is not None and (
        receipt.get("schema_version") != f"{ECONOMIC_RUN_SCHEMA}:receipt"
        or receipt.get("research_id") != V3_RESEARCH_ID
    ):
        raise ValueError("v3_receipt_identity")
    payloads: tuple[Mapping[str, Any], ...] = (results, ledger, report) + (
        (receipt,) if receipt is not None else ()
    )
    _verify_inner_ledger_hashes(results)
    _verify_inner_ledger_hashes(ledger)
    _verify_inner_ledger_hashes(report)
    _verify_statistics_crosslink(results, report)
    for payload in payloads:
        if any(payload.get(field) is not value for field, value in (
            ("research_only", True),
            ("economic_execution", True),
            ("economic_run", True),
            ("outcome_artifacts_present", True),
            ("native_one_ledger", True),
            ("shared_account", True),
            ("separate_sleeve_blend", False),
            ("paper_live_untouched", True),
            ("paper_or_live_activation", False),
            ("promotion", False),
            ("integration_candidate", False),
            ("auto_promotion", False),
            ("no_auto_promotion", True),
        )):
            raise ValueError("v3_research_boundary")
    pins = (
        "v3_governance_canonical_sha256",
        "execution_contract_sha256",
        "incident_receipt_canonical_sha256",
        "parent_v2_reservation_physical_sha256",
        "parent_v2_governance_canonical_sha256",
        "parent_v2_package_canonical_sha256",
        "preflight_receipt_sha256",
        "authorization_sha256",
    )
    for payload in payloads:
        for field in pins:
            if field in payload and not _valid_hex64(payload.get(field)):
                raise ValueError("v3_provenance_hash")
    if results.get("manifest_canonical_sha256") != results.get("v3_governance_canonical_sha256"):
        raise ValueError("v3_manifest_crosslink")
    for payload in (ledger, report):
        for field in ("v3_governance_canonical_sha256", "execution_contract_sha256", "incident_receipt_canonical_sha256"):
            if payload.get(field) != results.get(field):
                raise ValueError("v3_provenance_crosslink")
    if receipt is None:
        return
    if receipt.get("outputs") is None or receipt.get("one_shot", {}).get("status") != "CONSUMED":
        raise ValueError("v3_receipt_one_shot")
    if root is None:
        return
    contract_path = _resolve(root, receipt.get("execution_contract_path"), "execution_contract")
    contract = _load_json(contract_path)
    if _canonical_file_hash(contract_path, "contract_sha256") != results.get("execution_contract_sha256"):
        raise ValueError("v3_contract_hash")
    governance_path = _resolve(root, receipt.get("governance_manifest_path"), "v3_governance")
    if _canonical_file_hash(governance_path, "manifest_sha256") != results.get("v3_governance_canonical_sha256"):
        raise ValueError("v3_governance_hash")
    preflight_path = _resolve(root, receipt.get("preflight_receipt_path"), "v3_preflight")
    if _sha256_file(preflight_path) != results.get("preflight_receipt_sha256"):
        raise ValueError("v3_preflight_physical_hash")
    preflight = _load_json(preflight_path)
    if _canonical_file_hash(preflight_path, "receipt_sha256") != preflight.get("integrity", {}).get("receipt_sha256"):
        raise ValueError("v3_preflight_canonical_hash")
    incident_path = _resolve(root, V3_INCIDENT_REL, "v2_incident")
    if (
        _sha256_file(incident_path) != V3_INCIDENT_PHYSICAL_SHA256
        or _canonical_file_hash(incident_path, "incident_sha256") != V3_INCIDENT_SHA256
        or results.get("incident_receipt_canonical_sha256") != V3_INCIDENT_SHA256
    ):
        raise ValueError("v3_incident_provenance")
    source_commit = results.get("source_commit")
    freeze_commit = results.get("freeze_commit")
    if (
        not _valid_hex40(source_commit)
        or not _valid_hex40(freeze_commit)
        or contract.get("source_commit") != source_commit
        or contract.get("freeze_commit") != freeze_commit
    ):
        raise ValueError("v3_source_freeze_provenance")
    _git(root, "cat-file", "-e", f"{PARENT_GOVERNANCE_COMMIT}^{{commit}}")
    _git(root, "cat-file", "-e", f"{source_commit}^{{commit}}")
    _git(root, "cat-file", "-e", f"{freeze_commit}^{{commit}}")
    _git(root, "merge-base", "--is-ancestor", PARENT_GOVERNANCE_COMMIT, source_commit)
    _git(root, "merge-base", "--is-ancestor", source_commit, freeze_commit)
    if _git(root, "rev-list", "--count", f"{source_commit}..{freeze_commit}") != "1":
        raise ValueError("v3_source_freeze_lineage")
    authorization_path = _resolve(root, receipt.get("authorization_path"), "authorization")
    authorization = _load_json(authorization_path)
    if (
        authorization.get("schema_version") != AUTHORIZATION_SCHEMA
        or authorization.get("authorized") is not True
        or authorization.get("economic_execution_authorized") is not True
        or _canonical_file_hash(authorization_path, "authorization_sha256") != results.get("authorization_sha256")
    ):
        raise ValueError("v3_authorization_invalid")
    _verify_parent_evidence(root, results)
    state_path = _resolve(root, receipt.get("one_shot", {}).get("state_path"), "v3_state")
    _, state_raw, state_canonical = _verify_base_state(state_path)
    if results.get("one_shot_state_sha256") != state_raw or results.get("v3_state_canonical_sha256", state_canonical) != state_canonical:
        raise ValueError("v3_state_provenance")
    consumed_path = _state_sidecar(state_path, "consumed")
    consumed = _verify_transition(consumed_path, state_path, "CONSUMED")
    expected_output = (root / Path("data/research/runs/r46_treasury_auction_unwind_tlt_v3_recovery_final_2026_09_14")).resolve()
    if (
        results.get("canonical_output_directory") != str(expected_output)
        or receipt.get("output_directory") != str(expected_output)
        or consumed.get("output_path") != str(expected_output)
        or consumed.get("receipt_path") != str(expected_output / "run_receipt.json")
    ):
        raise ValueError("v3_output_path")
    for field in (
        "preflight_receipt_sha256",
        "authorization_sha256",
        "v3_governance_sha256",
        "v3_execution_contract_sha256",
        "parent_v2_governance_sha256",
        "parent_v2_package_sha256",
        "v2_reservation_physical_sha256",
        "v2_reservation_transition_sha256",
    ):
        if consumed.get(field) != results.get(field):
            raise ValueError("v3_consumed_provenance")
    if consumed.get("economic_output_bundle_sha256") != receipt.get("outputs", {}).get("bundle_sha256"):
        raise ValueError("v3_consumed_output_crosslink")


def _normalize_for_v2(value: object) -> object:
    if isinstance(value, dict):
        normalized = {key: _normalize_for_v2(child) for key, child in value.items()}
        if normalized.get("research_id") == V3_RESEARCH_ID:
            normalized["research_id"] = V2_RESEARCH_ID
        schema = normalized.get("schema_version")
        schema_map = {
            ECONOMIC_RUN_SCHEMA: V2_ECONOMIC_RUN_SCHEMA,
            f"{ECONOMIC_RUN_SCHEMA}:ledger": f"{V2_ECONOMIC_RUN_SCHEMA}:ledger",
            f"{ECONOMIC_RUN_SCHEMA}:report": f"{V2_ECONOMIC_RUN_SCHEMA}:report",
            f"{ECONOMIC_RUN_SCHEMA}:receipt": f"{V2_ECONOMIC_RUN_SCHEMA}:receipt",
        }
        if schema in schema_map:
            normalized["schema_version"] = schema_map[schema]
        if normalized.get("status") == ECONOMIC_LEDGER_STATUS:
            normalized["status"] = V2_ECONOMIC_LEDGER_STATUS
        if "ledger_sha256" in normalized:
            normalized["ledger_sha256"] = _canonical_hash(normalized, "ledger_sha256", nested=False)
        return normalized
    if isinstance(value, list):
        return [_normalize_for_v2(child) for child in value]
    return value


def _v2_structural_verify(
    results: Mapping[str, Any],
    ledger: Mapping[str, Any],
    report: Mapping[str, Any],
    receipt: Mapping[str, Any] | None = None,
) -> dict[str, object]:
    normalized_results = _normalize_for_v2(results)
    normalized_ledger = _normalize_for_v2(ledger)
    normalized_report = _normalize_for_v2(report)
    normalized_receipt = _normalize_for_v2(receipt) if receipt is not None else None
    if not isinstance(normalized_results, dict) or not isinstance(normalized_ledger, dict) or not isinstance(normalized_report, dict):
        raise ValueError("v3_normalization_invalid")
    if normalized_receipt is not None:
        if not isinstance(normalized_receipt, dict):
            raise ValueError("v3_receipt_normalization_invalid")
        outputs = normalized_receipt.get("outputs")
        if not isinstance(outputs, dict):
            raise ValueError("v3_receipt_outputs_invalid")
        result_hash = _published_payload_hash(normalized_results)
        ledger_hash = _published_payload_hash(normalized_ledger)
        report_hash = _published_payload_hash(normalized_report)
        outputs.update(
            {
                "results_sha256": result_hash,
                "ledger_sha256": ledger_hash,
                "report_sha256": report_hash,
            }
        )
        bundle = hashlib.sha256(
            _canonical_bytes(
                {
                    "results_sha256": result_hash,
                    "ledger_sha256": ledger_hash,
                    "report_sha256": report_hash,
                }
            )
        ).hexdigest()
        outputs["bundle_sha256"] = bundle
        one_shot = normalized_receipt.get("one_shot")
        if isinstance(one_shot, dict):
            one_shot["output_sha256"] = bundle
        normalized_receipt["receipt_sha256"] = _canonical_hash(
            normalized_receipt, "receipt_sha256", nested=False
        )
    return frozen_verifier.verify_economic_run(
        normalized_results,
        normalized_ledger,
        normalized_report,
        normalized_receipt,
    )


def verify_economic_payloads_test_only(
    results: Mapping[str, Any],
    ledger: Mapping[str, Any],
    report: Mapping[str, Any],
    run_receipt: Mapping[str, Any] | None = None,
) -> dict[str, object]:
    """Verify synthetic payloads without consulting production files."""
    _verify_v3_provenance(results, ledger, report, run_receipt)
    return _v2_structural_verify(results, ledger, report, run_receipt)


def verify_economic_run_from_files(
    repo_root: Path | str,
    results_path: Path | str,
    ledger_path: Path | str,
    report_path: Path | str,
    run_receipt_path: Path | str,
    preflight_receipt_path: Path | str | None = None,
) -> dict[str, object]:
    """Read-only verification of a complete future v3 output bundle."""
    root = Path(repo_root).resolve()
    results_file = _resolve(root, str(results_path), "results")
    ledger_file = _resolve(root, str(ledger_path), "ledger")
    report_file = _resolve(root, str(report_path), "report")
    receipt_file = _resolve(root, str(run_receipt_path), "run_receipt")
    output_dir = results_file.parent
    if any(path.parent != output_dir for path in (ledger_file, report_file, receipt_file)):
        raise ValueError("v3_output_directory_crosslink")
    if (results_file.name, ledger_file.name, report_file.name, receipt_file.name) != (
        "results.json",
        "ledger.json",
        "report.json",
        "run_receipt.json",
    ):
        raise ValueError("v3_output_filenames")
    results = _load_json(results_file)
    ledger = _load_json(ledger_file)
    report = _load_json(report_file)
    receipt = _load_json(receipt_file)
    expected_hashes = {
        "results_sha256": _sha256_file(results_file),
        "ledger_sha256": _sha256_file(ledger_file),
        "report_sha256": _sha256_file(report_file),
    }
    outputs = receipt.get("outputs")
    if not isinstance(outputs, Mapping) or any(outputs.get(key) != value for key, value in expected_hashes.items()):
        raise ValueError("v3_output_hash")
    bundle = hashlib.sha256(_canonical_bytes(expected_hashes)).hexdigest()
    if outputs.get("bundle_sha256") != bundle:
        raise ValueError("v3_output_bundle_hash")
    if receipt.get("receipt_sha256") != _canonical_hash(receipt, "receipt_sha256", nested=False):
        raise ValueError("v3_run_receipt_hash")
    if receipt.get("one_shot", {}).get("output_sha256") != bundle:
        raise ValueError("v3_receipt_output_crosslink")
    if preflight_receipt_path is not None and Path(preflight_receipt_path).resolve() != _resolve(
        root, receipt.get("preflight_receipt_path"), "v3_preflight"
    ):
        raise ValueError("v3_preflight_argument_crosslink")
    _verify_v3_provenance(results, ledger, report, receipt, root=root)
    state_path = _resolve(root, receipt.get("one_shot", {}).get("state_path"), "v3_state")
    consumed = _load_json(_state_sidecar(state_path, "consumed"))
    if consumed.get("output_path") != str(output_dir.resolve()):
        raise ValueError("v3_consumed_output_path")
    if consumed.get("economic_output_bundle_sha256") != bundle:
        raise ValueError("v3_consumed_bundle_hash")
    verified = _v2_structural_verify(results, ledger, report, receipt)
    return {
        "status": "VERIFIED_R46_V3_RECOVERY_ECONOMIC_RUN_FROM_FILES",
        "schema_version": results["schema_version"],
        "output_bundle_sha256": bundle,
        "cost_cell_count": verified["cost_cell_count"],
        "promotion": False,
    }


def verify_economic_package(output_dir: Path | str, *, repo_root: Path | str | None = None) -> dict[str, object]:
    directory = Path(output_dir).resolve()
    root = Path(repo_root or REPO).resolve()
    return verify_economic_run_from_files(
        root,
        directory / "results.json",
        directory / "ledger.json",
        directory / "report.json",
        directory / "run_receipt.json",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--repo-root", type=Path, default=REPO)
    args = parser.parse_args(argv)
    try:
        result = verify_economic_package(args.output_dir, repo_root=args.repo_root)
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "BLOCKED", "error": str(exc)}, sort_keys=True))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
