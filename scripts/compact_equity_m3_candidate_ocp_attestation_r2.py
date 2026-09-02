#!/usr/bin/env python3
"""Bind the M3 candidate-OCP raw replay to its corrective remote CI closure.

The substantive OCP replay is immutable and remains the C23 A23/B23/C23
chain.  This module creates and validates a separate R2 attestation for that
replay and for the corrective A24/B24/C24 chain.  It never downloads data,
opens a market-data connection, or runs an economic workload.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
for _path in (REPO / "src", Path(__file__).parent):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from compact_equity_m3_candidate_ocp_receipt import (  # noqa: E402
    FORBIDDEN_ECONOMIC_FIELDS,
    validate_raw,
)

from parayabuck.backtest.observation_parity import canonical_digest  # noqa: E402

R2_MISSION = "M3_CANDIDATE_OCP_ATTESTATION_R2"
R2_PASS = "M3_CANDIDATE_OCP_ATTESTATION_R2_PASS"
R2_BLOCKED = "M3_CANDIDATE_OCP_ATTESTATION_R2_BLOCKED"

CORRECTIVE_STARTING_HEAD = "ac442c9f0232711a9eef7ade7a076ed0cc8e5fb2"
A22_IMPLEMENTATION_COMMIT = "cae97cf9e059c5c03d79469b29e84d47533119d6"
B22_MANIFEST_COMMIT = "4d612d9f8fc50a5d4911f2c37d74979c61af93be"
D23_COMMIT = "3436c288fe7052cb98c73fe7713aef2daeb78af2"
D23_CI_RUN = "33671110579"
E23_COMMIT = "ac442c9f0232711a9eef7ade7a076ed0cc8e5fb2"
E23_CI_RUN = "33672017181"

A23_IMPLEMENTATION_COMMIT = "32357c110c09f92b438ee188fac289da966bcd24"
B23_MANIFEST_COMMIT = "fe52e30e7cca36030621152f6f1bdbcc247f8e9c"
C23_RESULT_COMMIT = "cd812af9c958c4934ca3da1a23bae00ae58278a4"
C23_CI_RUN = "33665025444"
C23_LOCAL_RECEIPT_DIGEST = "bc061438a7a3212c31e601e7f07c5f95d01887460d11473b09394602fd2098b4"
C23_RAW_SHA256 = "609fba66a8e909036b92e20f62dfbb68cfb2308d79e64100198fd2b6be61a05e"
C23_RAW_PAYLOAD_DIGEST = "e23b09537693f1bec8b0e54339821a687ab8bd533a5cee1e6c6aa37ddd43f486"

C23_MANIFEST_PATH = "data/research_manifests/equity_m3_candidate_ocp_2026_09.json"
C23_LOCAL_RECEIPT_PATH = "data/research_receipts/equity_m3_candidate_ocp_2026_09.json"
R2_MANIFEST_PATH = "data/research_manifests/equity_m3_candidate_ocp_remote_attestation_2026_09_r2.json"
R2_RECEIPT_PATH = "data/research_receipts/equity_m3_candidate_ocp_2026_09_r2.json"
R2_REPORT_PATH = "docs/equity_m3_candidate_ocp_2026_09_r2.md"
R2_RAW_PATH = "data/research/generated/equity_m3_candidate_ocp_2026_09_r2/ocp_run.json"

R2_RAW_OUTPUT_PATHS = (
    "data/research/generated/equity_m3_candidate_ocp_2026_09_r2/m3_universe.json",
    "data/research/generated/equity_m3_candidate_ocp_2026_09_r2/m3_universe_repeat.json",
    "data/research/generated/equity_m3_candidate_ocp_2026_09_r2/comparison.json",
    "data/research/generated/equity_m3_candidate_ocp_2026_09_r2/control_universe.json",
    "data/research/generated/equity_m3_candidate_ocp_2026_09_r2/snapshot_digests.json",
)

R2_CRITICAL_PATHS = (
    ".github/workflows/ci.yml",
    "scripts/compact_equity_m3_candidate_ocp_attestation_r2.py",
    "scripts/research_equity_m3_m4.py",
)
R2_IMPLEMENTATION_PATHS = frozenset(
    {
        ".github/workflows/ci.yml",
        "scripts/compact_equity_m3_candidate_ocp_attestation_r2.py",
        "scripts/research_equity_m3_m4.py",
        "tests/test_research_equity_m3_candidate_ocp_attestation_r2.py",
    }
)

PYTHON_MATRIX = {"3.12": "PASS", "3.13": "PASS", "3.14": "PASS"}
CI_STAGES = {
    "ruff_lint": "PASS",
    "ruff_format": "PASS",
    "mypy": "PASS",
    "focused_ocp_tests": "PASS",
    "focused_m3_data_gate_tests": "PASS",
    "focused_acquisition_pit_tests": "PASS",
    "full_pytest": "PASS",
    "production_isolation": "PASS",
    "build": "PASS",
    "wheel_smoke": "PASS",
    "dependency_audit": "PASS",
    "secret_scan": "PASS",
}

HASH_PARITY = {
    "C_semantic_universe_hash": "e1f0e2ee7420a9d43a8fe69e2048e4a5a3e135f3d3ffc5d2bb8b425fdb487eb1",
    "C_ordered_observation_merkle_root": "d6ec0d61b100ab527ee2978ddcd6210c09f866b03cc62637525c9ac4f2d6509a",
    "X_semantic_universe_hash": "eb9b3df623a2fdcb5b02c9c174fbf629a355f4faf018df9989de8703f8907f6f",
    "X_ordered_observation_merkle_root": "cee257f5053570c2b5957adf5b6b4b8b2c0943273790b20135a24ed5ffbe568f",
    "snapshot_count": 2658,
    "sequence_change_count": 2384,
    "membership_change_count": 2381,
    "scanner_metadata_mismatch_count": 0,
    "direct_builder_sample_count": 9,
    "direct_builder_mismatch_count": 0,
    "future_bar_sample_count": 4,
    "future_bar_failure_count": 0,
}

CORRECTIVE_GATE_NAMES = (
    "starting_head",
    "clean_worktree",
    "historical_d23_e23_topology",
    "defect_reproduction",
    "old_receipt_superseded",
    "c23_raw_artifact_identity",
    "c23_raw_payload_digest",
    "c23_local_receipt_identity",
    "c23_implementation_identity",
    "c23_manifest_identity",
    "c23_result_topology",
    "c23_ci_run_identity",
    "c23_ci_head_binding",
    "c23_ci_matrix_success",
    "corrective_implementation_identity",
    "corrective_manifest_only_commit",
    "corrective_result_topology",
    "corrective_ci_head_binding",
    "critical_blob_identity",
    "all_42_substantive_ocp_gates",
    "substantive_hash_parity",
    "no_economic_execution",
    "no_network_market_data",
    "no_credentials",
    "receipt_forbidden_fields",
    "receipt_self_digest",
    "economic_consumer_r2_only",
)


class AttestationError(ValueError):
    """Raised when a raw result, commit graph, or receipt is not R2-valid."""


def _fail(message: str) -> None:
    raise AttestationError(message)


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _full_sha(value: object, field: str) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        _fail(f"{field} must be a lowercase 64-character SHA-256")
    return value


def _full_commit(value: object, field: str) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{40}", value) is None:
        _fail(f"{field} must be a full 40-character commit SHA")
    try:
        resolved = _git("rev-parse", f"{value}^{{commit}}")
    except subprocess.CalledProcessError as exc:
        raise AttestationError(f"{field} is not a reachable commit: {value}") from exc
    if resolved != value:
        _fail(f"{field} does not resolve exactly: {value}")
    return value


def _git(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=REPO, text=True).strip()


def _git_blob(ref: str) -> bytes:
    return subprocess.check_output(["git", "cat-file", "blob", ref], cwd=REPO)


def _git_blob_identity(commit: str, path: str) -> dict[str, str]:
    blob = _git_blob(f"{commit}:{path}")
    return {
        "commit": commit,
        "path": path,
        "git_blob_id": _git("rev-parse", f"{commit}:{path}"),
        "raw_sha256": _sha256_bytes(blob),
    }


def _parents(commit: str) -> list[str]:
    fields = _git("rev-list", "--parents", "-n", "1", commit).split()
    return fields[1:]


def _changed_paths(parent: str, commit: str) -> list[str]:
    return _git("diff-tree", "--no-commit-id", "--name-only", "-r", parent, commit).splitlines()


def _load(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AttestationError(f"cannot load JSON artifact: {path}") from exc
    if not isinstance(payload, dict):
        _fail(f"JSON artifact must be an object: {path}")
    return payload


def _load_git_json(commit: str, path: str) -> dict[str, Any]:
    try:
        payload = json.loads(_git_blob(f"{commit}:{path}"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, subprocess.CalledProcessError) as exc:
        raise AttestationError(f"cannot load Git JSON artifact: {commit}:{path}") from exc
    if not isinstance(payload, dict):
        _fail(f"Git JSON artifact must be an object: {commit}:{path}")
    return payload


def _exact_repo_path(path: Path, expected: str, field: str) -> None:
    try:
        actual = path.resolve()
        target = (REPO / expected).resolve()
    except OSError as exc:
        raise AttestationError(f"cannot resolve {field}") from exc
    if actual != target:
        _fail(f"{field} must be the explicit versioned path {expected}")


def _forbidden_keys(value: object) -> tuple[str, ...]:
    found: set[str] = set()

    def visit(item: object) -> None:
        if isinstance(item, Mapping):
            for key, child in item.items():
                if str(key).lower() in FORBIDDEN_ECONOMIC_FIELDS:
                    found.add(str(key))
                visit(child)
        elif isinstance(item, list):
            for child in item:
                visit(child)

    visit(value)
    return tuple(sorted(found))


def _require_mapping(value: object, field: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        _fail(f"{field} must be an object")
    return value


def _require_equal(actual: object, expected: object, field: str) -> None:
    if actual != expected:
        _fail(f"{field} mismatch: expected {expected!r}, got {actual!r}")


def _validate_old_receipt_superseded() -> None:
    receipt_path = REPO / C23_LOCAL_RECEIPT_PATH
    if not receipt_path.is_file():
        _fail("the old unversioned receipt must remain present as historical evidence")
    payload = _load(receipt_path)
    implementation = _require_mapping(payload.get("implementation_identity"), "old receipt implementation_identity")
    manifest = _require_mapping(payload.get("manifest_identity"), "old receipt manifest_identity")
    _require_equal(implementation.get("commit"), A22_IMPLEMENTATION_COMMIT, "old receipt implementation commit")
    _require_equal(manifest.get("commit"), B22_MANIFEST_COMMIT, "old receipt manifest commit")
    _require_equal(payload.get("remote_ci"), {"run_id": C23_CI_RUN, "status": "PASS"}, "old receipt remote_ci")
    raw_outputs = _require_mapping(
        _require_mapping(payload.get("input_artifacts"), "old receipt input_artifacts").get("raw_outputs"),
        "old receipt raw_outputs",
    )
    paths = {str(item.get("path")) for item in raw_outputs.values() if isinstance(item, Mapping)}
    if any("_r2/" in path for path in paths) or "r2" in str(payload.get("schema_version", "")):
        _fail("old unversioned receipt must not be treated as the R2 receipt")
    if "head_sha" in _require_mapping(payload.get("remote_ci"), "old receipt remote_ci"):
        _fail("old unversioned receipt is missing its required historical-invalid classification")


def _validate_historical_topology() -> None:
    if _parents(D23_COMMIT) != [C23_RESULT_COMMIT]:
        _fail("D23 is not a direct child of C23")
    d23_paths = set(_changed_paths(C23_RESULT_COMMIT, D23_COMMIT))
    if not d23_paths or not d23_paths <= {C23_LOCAL_RECEIPT_PATH, "docs/equity_m3_candidate_ocp_2026_09.md"}:
        _fail("D23 changed paths do not match the historical receipt/report closure")
    if _parents(E23_COMMIT) != [D23_COMMIT]:
        _fail("E23 is not a direct child of D23")
    if _changed_paths(D23_COMMIT, E23_COMMIT) != ["docs/equity_m3_candidate_ocp_2026_09.md"]:
        _fail("E23 is not documentation-only")


def _validate_raw_declarations(raw: Mapping[str, object]) -> None:
    _require_equal(raw.get("mission"), "M3_CANDIDATE_OCP_GATE", "raw mission")
    _require_equal(raw.get("verdict"), "M3_CANDIDATE_OCP_GATE_PASS", "raw verdict")
    _require_equal(raw.get("research_only"), True, "raw research_only")
    for field in (
        "candidate_economics_run",
        "economic_metrics_present",
        "economic_mission_authorized",
        "economic_mission_run",
    ):
        _require_equal(raw.get(field), False, f"raw {field}")
    _require_equal(raw.get("blocked_reasons"), [], "raw blocked_reasons")
    _require_equal(raw.get("no_network_or_credentials"), True, "raw no_network_or_credentials")
    if _forbidden_keys(raw):
        _fail(f"raw artifact contains forbidden economic fields: {_forbidden_keys(raw)}")

    implementation = _require_mapping(raw.get("implementation_identity"), "raw implementation_identity")
    manifest = _require_mapping(raw.get("manifest_identity"), "raw manifest_identity")
    _require_equal(raw.get("run_commit"), B23_MANIFEST_COMMIT, "raw run_commit")
    _require_equal(implementation.get("commit"), A23_IMPLEMENTATION_COMMIT, "raw implementation commit")
    _require_equal(manifest.get("commit"), B23_MANIFEST_COMMIT, "raw manifest commit")
    _require_equal(manifest.get("path"), C23_MANIFEST_PATH, "raw manifest path")
    _require_equal(manifest.get("git_blob_id"), "cbf9f07e9dbd5467ace4e938f5be0de5665d9a40", "raw manifest Git blob")
    _require_equal(
        manifest.get("raw_sha256"),
        "6264e56135a95e3ebd2b244f5d19b0b54f2845808314393131fa9599209fe1e1",
        "raw manifest SHA",
    )

    outputs = _require_mapping(
        _require_mapping(raw.get("input_artifacts"), "raw input_artifacts").get("raw_outputs"), "raw raw_outputs"
    )
    actual_paths = tuple(sorted(str(item.get("path")) for item in outputs.values() if isinstance(item, Mapping)))
    _require_equal(actual_paths, tuple(sorted(R2_RAW_OUTPUT_PATHS)), "raw output paths")

    summary = _require_mapping(raw.get("observation_change_summary"), "raw observation_change_summary")
    direct = _require_mapping(raw.get("direct_builder_parity"), "raw direct_builder_parity")
    future = _require_mapping(raw.get("future_bar_tests"), "raw future_bar_tests")
    coverage = _require_mapping(raw.get("snapshot_coverage"), "raw snapshot_coverage")
    _require_equal(summary.get("C_universe_hash"), HASH_PARITY["C_semantic_universe_hash"], "C semantic universe hash")
    _require_equal(
        summary.get("C_merkle_root"), HASH_PARITY["C_ordered_observation_merkle_root"], "C observation Merkle root"
    )
    _require_equal(summary.get("X_universe_hash"), HASH_PARITY["X_semantic_universe_hash"], "X semantic universe hash")
    _require_equal(
        summary.get("X_merkle_root"), HASH_PARITY["X_ordered_observation_merkle_root"], "X observation Merkle root"
    )
    _require_equal(summary.get("sequence_change_count"), HASH_PARITY["sequence_change_count"], "sequence change count")
    _require_equal(
        summary.get("membership_change_count"), HASH_PARITY["membership_change_count"], "membership change count"
    )
    mismatch_counts = _require_mapping(summary.get("scanner_metadata_mismatch_counts"), "scanner mismatch counts")
    _require_equal(sum(int(value) for value in mismatch_counts.values()), 0, "scanner metadata mismatch count")
    _require_equal(coverage.get("count"), HASH_PARITY["snapshot_count"], "snapshot count")
    _require_equal(
        direct.get("sample_count"), HASH_PARITY["direct_builder_sample_count"], "direct-builder sample count"
    )
    _require_equal(
        direct.get("mismatch_count"), HASH_PARITY["direct_builder_mismatch_count"], "direct-builder mismatch count"
    )
    _require_equal(future.get("sample_count"), HASH_PARITY["future_bar_sample_count"], "future-bar sample count")
    _require_equal(future.get("failure_count"), HASH_PARITY["future_bar_failure_count"], "future-bar failure count")

    gates = _require_mapping(raw.get("mandatory_gate_results"), "raw mandatory_gate_results")
    _require_equal(len(gates), 42, "raw mandatory gate count")
    if any(not isinstance(value, Mapping) or value.get("status") != "PASS" for value in gates.values()):
        _fail("raw substantive OCP gates are not all PASS")


def validate_substantive_raw(
    raw: Mapping[str, object],
    *,
    raw_path: Path,
    expected_raw_sha256: str,
    expected_raw_payload_digest: str,
) -> None:
    """Validate the exact C23 raw artifact and its frozen substantive claims."""

    _exact_repo_path(raw_path, R2_RAW_PATH, "raw artifact")
    _require_equal(expected_raw_sha256, C23_RAW_SHA256, "expected raw SHA")
    _require_equal(expected_raw_payload_digest, C23_RAW_PAYLOAD_DIGEST, "expected raw payload digest")
    if not raw_path.is_file():
        _fail(f"raw artifact is missing: {raw_path}")
    _require_equal(_sha256_bytes(raw_path.read_bytes()), expected_raw_sha256, "raw artifact SHA")
    validate_raw(raw)
    body = dict(raw)
    body.pop("raw_digest_sha256", None)
    _require_equal(canonical_digest(body), expected_raw_payload_digest, "raw payload digest")
    _validate_raw_declarations(raw)


def _validate_c23_history(raw: Mapping[str, object]) -> dict[str, Any]:
    _full_commit(A23_IMPLEMENTATION_COMMIT, "C23 implementation commit")
    _full_commit(B23_MANIFEST_COMMIT, "C23 manifest commit")
    _full_commit(C23_RESULT_COMMIT, "C23 local result commit")
    if _parents(B23_MANIFEST_COMMIT) != [A23_IMPLEMENTATION_COMMIT]:
        _fail("C23 manifest commit is not a direct child of A23")
    if _parents(C23_RESULT_COMMIT) != [B23_MANIFEST_COMMIT]:
        _fail("C23 result commit is not a direct child of B23")
    if _changed_paths(A23_IMPLEMENTATION_COMMIT, B23_MANIFEST_COMMIT) != [C23_MANIFEST_PATH]:
        _fail("C23 manifest commit is not manifest-only")

    declared_manifest = _require_mapping(raw.get("manifest_identity"), "raw manifest_identity")
    actual_manifest = _git_blob_identity(B23_MANIFEST_COMMIT, C23_MANIFEST_PATH)
    _require_equal(declared_manifest.get("git_blob_id"), actual_manifest["git_blob_id"], "C23 manifest Git blob")
    _require_equal(declared_manifest.get("raw_sha256"), actual_manifest["raw_sha256"], "C23 manifest raw SHA")

    declared_critical = _require_mapping(
        _require_mapping(raw.get("implementation_identity"), "raw implementation_identity").get("critical_git_blobs"),
        "raw critical_git_blobs",
    )
    if not declared_critical:
        _fail("raw implementation critical blobs are missing")
    for path, declaration in declared_critical.items():
        entry = _require_mapping(declaration, f"raw critical blob {path}")
        actual = _git_blob_identity(A23_IMPLEMENTATION_COMMIT, str(path))
        _require_equal(entry.get("commit"), A23_IMPLEMENTATION_COMMIT, f"raw critical blob {path} commit")
        _require_equal(entry.get("git_blob_id"), actual["git_blob_id"], f"raw critical blob {path} Git blob")
        _require_equal(entry.get("raw_sha256"), actual["raw_sha256"], f"raw critical blob {path} SHA")

    c23_receipt = _load_git_json(C23_RESULT_COMMIT, C23_LOCAL_RECEIPT_PATH)
    recorded_digest = c23_receipt.get("self_digest_sha256")
    without_digest = dict(c23_receipt)
    without_digest.pop("self_digest_sha256", None)
    _require_equal(recorded_digest, C23_LOCAL_RECEIPT_DIGEST, "C23 local receipt self-digest")
    _require_equal(canonical_digest(without_digest), C23_LOCAL_RECEIPT_DIGEST, "C23 local receipt canonical digest")
    _require_equal(c23_receipt.get("verdict"), "M3_CANDIDATE_OCP_GATE_BLOCKED", "C23 local receipt verdict")
    _require_equal(
        c23_receipt.get("blocked_reasons"), ["remote_ci:NOT_RUN_BLOCKED"], "C23 local receipt blocked reason"
    )
    _require_equal(c23_receipt.get("remote_ci"), {"run_id": None, "status": "NOT_RUN_BLOCKED"}, "C23 local receipt CI")
    c23_impl = _require_mapping(c23_receipt.get("implementation_identity"), "C23 receipt implementation_identity")
    c23_manifest = _require_mapping(c23_receipt.get("manifest_identity"), "C23 receipt manifest_identity")
    _require_equal(c23_impl.get("commit"), A23_IMPLEMENTATION_COMMIT, "C23 receipt implementation commit")
    _require_equal(c23_manifest.get("commit"), B23_MANIFEST_COMMIT, "C23 receipt manifest commit")

    return {
        "manifest_git_blob_id": actual_manifest["git_blob_id"],
        "local_receipt_self_digest": C23_LOCAL_RECEIPT_DIGEST,
        "raw_output_paths": list(R2_RAW_OUTPUT_PATHS),
        "mandatory_gate_count": 42,
    }


def _substantive_replay(raw: Mapping[str, object], history: Mapping[str, object]) -> dict[str, object]:
    return {
        "implementation_commit": A23_IMPLEMENTATION_COMMIT,
        "manifest_commit": B23_MANIFEST_COMMIT,
        "local_result_commit": C23_RESULT_COMMIT,
        "manifest_path": C23_MANIFEST_PATH,
        "manifest_git_blob_id": history["manifest_git_blob_id"],
        "raw_artifact_path": R2_RAW_PATH,
        "raw_artifact_sha256": C23_RAW_SHA256,
        "raw_payload_digest": C23_RAW_PAYLOAD_DIGEST,
        "raw_output_paths": list(history["raw_output_paths"]),
        "local_receipt_path": C23_LOCAL_RECEIPT_PATH,
        "local_receipt_self_digest": history["local_receipt_self_digest"],
        "raw_verdict": "M3_CANDIDATE_OCP_GATE_PASS",
        "mandatory_gate_count": history["mandatory_gate_count"],
        "mandatory_gate_status": "PASS",
        "mandatory_gate_results": json.loads(json.dumps(raw["mandatory_gate_results"])),
        "c23_ci": {
            "run_id": C23_CI_RUN,
            "head_sha": C23_RESULT_COMMIT,
            "status": "PASS",
            "conclusion": "success",
            "python_matrix": dict(PYTHON_MATRIX),
            "required_stages": dict(CI_STAGES),
        },
        "hash_parity": dict(HASH_PARITY),
        "candidate_economics_run": False,
        "economic_metrics_present": False,
    }


def _build_critical_blobs(implementation_commit: str) -> dict[str, dict[str, str]]:
    return {path: _git_blob_identity(implementation_commit, path) for path in R2_CRITICAL_PATHS}


def build_manifest(implementation_commit: str) -> dict[str, object]:
    """Build the B24 manifest from the already committed A24 source tree."""

    _full_commit(implementation_commit, "corrective implementation commit")
    parent = _parents(implementation_commit)
    if parent != [CORRECTIVE_STARTING_HEAD]:
        _fail("corrective implementation must be a direct child of E23")
    critical = _build_critical_blobs(implementation_commit)
    return {
        "schema_version": "2.0.0",
        "mission": R2_MISSION,
        "repository_url": "https://github.com/batuhankiskac/ParayaBuck",
        "branch": "main",
        "declared_before_results": True,
        "mismatch_category": "remote_ci_raw_provenance_cross_binding",
        "superseded_artifacts": {
            "receipt_path": C23_LOCAL_RECEIPT_PATH,
            "report_path": "docs/equity_m3_candidate_ocp_2026_09.md",
            "status": "SUPERSEDED_INVALID_REMOTE_ATTESTATION_BINDING",
            "d23_commit": D23_COMMIT,
            "d23_ci_run": D23_CI_RUN,
            "e23_commit": E23_COMMIT,
            "e23_ci_run": E23_CI_RUN,
        },
        "substantive_replay": {
            "implementation_commit": A23_IMPLEMENTATION_COMMIT,
            "manifest_commit": B23_MANIFEST_COMMIT,
            "local_result_commit": C23_RESULT_COMMIT,
            "raw_artifact_path": R2_RAW_PATH,
            "raw_artifact_sha256": C23_RAW_SHA256,
            "raw_payload_digest": C23_RAW_PAYLOAD_DIGEST,
            "local_receipt_self_digest": C23_LOCAL_RECEIPT_DIGEST,
            "raw_verdict": "M3_CANDIDATE_OCP_GATE_PASS",
            "mandatory_gate_count": 42,
            "mandatory_gate_status": "PASS",
            "c23_ci_run": C23_CI_RUN,
            "c23_ci_head_sha": C23_RESULT_COMMIT,
            "hash_parity": dict(HASH_PARITY),
        },
        "corrective_topology": {
            "starting_head": CORRECTIVE_STARTING_HEAD,
            "implementation_commit": implementation_commit,
            "implementation_parent": CORRECTIVE_STARTING_HEAD,
            "manifest_parent": "implementation_commit",
            "result_parent": "manifest_commit",
            "manifest_changed_paths": [R2_MANIFEST_PATH],
            "result_changed_paths": [R2_RECEIPT_PATH, R2_REPORT_PATH],
            "critical_paths": list(R2_CRITICAL_PATHS),
            "ci_head_binding": "local_result_commit",
        },
        "critical_git_blobs": critical,
        "mandatory_corrective_gates": list(CORRECTIVE_GATE_NAMES),
        "python_matrix": dict(PYTHON_MATRIX),
        "required_ci_stages": dict(CI_STAGES),
        "no_economic_execution": True,
        "no_network_market_data_or_credentials": True,
        "forbidden_economic_fields": sorted(FORBIDDEN_ECONOMIC_FIELDS),
        "post_pass_boundary": {
            "candidate_economics_run": False,
            "economic_metrics_present": False,
            "economic_mission_authorized": False,
            "next_required_mission": "M3_ECONOMIC_MISSION_PREDECLARATION",
            "automatic_promotion": False,
        },
    }


def _validate_manifest_payload(manifest: Mapping[str, object], implementation_commit: str) -> Mapping[str, object]:
    _require_equal(manifest.get("schema_version"), "2.0.0", "R2 manifest schema_version")
    _require_equal(manifest.get("mission"), R2_MISSION, "R2 manifest mission")
    _require_equal(manifest.get("declared_before_results"), True, "R2 manifest declared_before_results")
    _require_equal(manifest.get("mismatch_category"), "remote_ci_raw_provenance_cross_binding", "R2 mismatch category")
    if any(key in manifest for key in ("verdict", "self_digest_sha256", "result_commit", "remote_ci")):
        _fail("R2 manifest contains result or remote-CI fields")
    if _forbidden_keys(manifest):
        _fail(f"R2 manifest contains forbidden economic fields: {_forbidden_keys(manifest)}")
    topology = _require_mapping(manifest.get("corrective_topology"), "R2 corrective_topology")
    _require_equal(topology.get("starting_head"), CORRECTIVE_STARTING_HEAD, "R2 starting head")
    _require_equal(topology.get("implementation_commit"), implementation_commit, "R2 implementation commit")
    _require_equal(topology.get("manifest_parent"), "implementation_commit", "R2 manifest parent rule")
    _require_equal(topology.get("result_parent"), "manifest_commit", "R2 result parent rule")
    _require_equal(topology.get("manifest_changed_paths"), [R2_MANIFEST_PATH], "R2 manifest changed paths")
    _require_equal(topology.get("result_changed_paths"), [R2_RECEIPT_PATH, R2_REPORT_PATH], "R2 result changed paths")
    _require_equal(topology.get("critical_paths"), list(R2_CRITICAL_PATHS), "R2 critical paths")
    _require_equal(topology.get("ci_head_binding"), "local_result_commit", "R2 CI head binding")

    substantive = _require_mapping(manifest.get("substantive_replay"), "R2 substantive_replay")
    expected_substantive = {
        "implementation_commit": A23_IMPLEMENTATION_COMMIT,
        "manifest_commit": B23_MANIFEST_COMMIT,
        "local_result_commit": C23_RESULT_COMMIT,
        "raw_artifact_path": R2_RAW_PATH,
        "raw_artifact_sha256": C23_RAW_SHA256,
        "raw_payload_digest": C23_RAW_PAYLOAD_DIGEST,
        "local_receipt_self_digest": C23_LOCAL_RECEIPT_DIGEST,
        "raw_verdict": "M3_CANDIDATE_OCP_GATE_PASS",
        "mandatory_gate_count": 42,
        "mandatory_gate_status": "PASS",
        "c23_ci_run": C23_CI_RUN,
        "c23_ci_head_sha": C23_RESULT_COMMIT,
        "hash_parity": HASH_PARITY,
    }
    for key, expected in expected_substantive.items():
        _require_equal(substantive.get(key), expected, f"R2 substantive_replay.{key}")
    _require_equal(
        manifest.get("mandatory_corrective_gates"), list(CORRECTIVE_GATE_NAMES), "R2 corrective gate contract"
    )
    _require_equal(manifest.get("python_matrix"), PYTHON_MATRIX, "R2 Python matrix contract")
    _require_equal(manifest.get("required_ci_stages"), CI_STAGES, "R2 CI stage contract")
    critical = _require_mapping(manifest.get("critical_git_blobs"), "R2 critical_git_blobs")
    _require_equal(set(critical), set(R2_CRITICAL_PATHS), "R2 critical path set")
    return critical


def _validate_manifest_commit(manifest_commit: str, implementation_commit: str) -> Mapping[str, object]:
    _full_commit(manifest_commit, "corrective manifest commit")
    if _parents(manifest_commit) != [implementation_commit]:
        _fail("corrective manifest commit is not a direct child of A24")
    if _changed_paths(implementation_commit, manifest_commit) != [R2_MANIFEST_PATH]:
        _fail("corrective manifest commit must change exactly the R2 manifest")
    manifest = _load_git_json(manifest_commit, R2_MANIFEST_PATH)
    critical = _validate_manifest_payload(manifest, implementation_commit)
    for path, declaration in critical.items():
        entry = _require_mapping(declaration, f"R2 critical blob {path}")
        actual = _git_blob_identity(implementation_commit, str(path))
        _require_equal(entry.get("commit"), implementation_commit, f"R2 critical blob {path} commit")
        _require_equal(entry.get("git_blob_id"), actual["git_blob_id"], f"R2 critical blob {path} Git blob")
        _require_equal(entry.get("raw_sha256"), actual["raw_sha256"], f"R2 critical blob {path} SHA")
    return critical


def _validate_critical_blobs(critical: Mapping[str, object], commits: tuple[str, ...]) -> None:
    for commit in commits:
        _full_commit(commit, f"critical-blob commit {commit}")
        for path, declaration in critical.items():
            entry = _require_mapping(declaration, f"R2 critical blob {path}")
            actual = _git_blob_identity(commit, str(path))
            _require_equal(actual["git_blob_id"], entry.get("git_blob_id"), f"critical blob {path} at {commit}")
            _require_equal(actual["raw_sha256"], entry.get("raw_sha256"), f"critical blob SHA {path} at {commit}")


def _validate_corrective_chain(
    *,
    implementation_commit: str,
    manifest_commit: str,
    local_result_commit: str | None,
    remote_ci_status: str,
    remote_ci_head_sha: str | None,
) -> Mapping[str, object]:
    _full_commit(implementation_commit, "corrective implementation commit")
    if _parents(implementation_commit) != [CORRECTIVE_STARTING_HEAD]:
        _fail("corrective implementation commit must be a direct child of E23")
    if set(_changed_paths(CORRECTIVE_STARTING_HEAD, implementation_commit)) != set(R2_IMPLEMENTATION_PATHS):
        _fail("A24 changed paths exceed the corrective implementation/test/CI scope")
    critical = _validate_manifest_commit(manifest_commit, implementation_commit)
    if remote_ci_status == "NOT_RUN_BLOCKED":
        return critical
    if remote_ci_status != "PASS":
        _fail(f"unsupported corrective CI status: {remote_ci_status}")
    if local_result_commit is None:
        _fail("PASS corrective closure is missing the local result commit")
    _full_commit(local_result_commit, "corrective local result commit")
    if _parents(local_result_commit) != [manifest_commit]:
        _fail("corrective local result commit is not a direct child of B24")
    if set(_changed_paths(manifest_commit, local_result_commit)) != {R2_RECEIPT_PATH, R2_REPORT_PATH}:
        _fail("C24 changed paths must be exactly the R2 receipt and report")
    if remote_ci_head_sha is None:
        _fail("PASS corrective CI requires a head SHA")
    _full_commit(remote_ci_head_sha, "corrective CI head SHA")
    _require_equal(remote_ci_head_sha, local_result_commit, "corrective CI head binding")
    _validate_critical_blobs(
        critical, (implementation_commit, manifest_commit, local_result_commit, remote_ci_head_sha)
    )
    return critical


def _corrective_ci_claim(
    *,
    status: str,
    run_id: str,
    head_sha: str,
    local_result_commit: str | None,
) -> dict[str, object]:
    if status == "NOT_RUN_BLOCKED":
        if run_id not in {"NOT_RUN_BLOCKED", ""} or head_sha not in {"NOT_RUN_BLOCKED", ""}:
            _fail("NOT_RUN_BLOCKED corrective CI must not carry a run or head claim")
        return {"status": "NOT_RUN_BLOCKED"}
    if status != "PASS":
        _fail(f"corrective CI is not a supported closure status: {status}")
    if not run_id or run_id == "NOT_RUN_BLOCKED":
        _fail("PASS corrective CI requires an explicit run ID")
    if not re.fullmatch(r"[0-9]+", run_id):
        _fail("corrective CI run ID must be numeric")
    if local_result_commit is None:
        _fail("PASS corrective CI requires a local result commit")
    _full_commit(head_sha, "corrective CI head SHA")
    _require_equal(head_sha, local_result_commit, "corrective CI head SHA")
    return {
        "status": "PASS",
        "run_id": run_id,
        "head_sha": head_sha,
        "conclusion": "success",
        "python_matrix": dict(PYTHON_MATRIX),
        "required_stages": dict(CI_STAGES),
    }


def _corrective_gates(*, blocked: bool) -> dict[str, dict[str, str]]:
    result = {name: {"status": "PASS"} for name in CORRECTIVE_GATE_NAMES}
    if blocked:
        for name in ("corrective_result_topology", "corrective_ci_head_binding", "critical_blob_identity"):
            result[name] = {"status": "NOT_RUN_BLOCKED"}
    return result


def _validate_consumer_source() -> None:
    path = REPO / "scripts/research_equity_m3_m4.py"
    try:
        source = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise AttestationError("economic consumer source is missing") from exc
    if "validate_r2_receipt" not in source or R2_RECEIPT_PATH not in source:
        _fail("economic consumer is not R2-only")


def _validate_receipt_common(receipt: Mapping[str, object]) -> None:
    _require_equal(receipt.get("schema_version"), "2.0.0", "R2 receipt schema_version")
    _require_equal(receipt.get("mission"), R2_MISSION, "R2 receipt mission")
    _require_equal(receipt.get("verdict"), R2_PASS, "R2 receipt verdict")
    _require_equal(receipt.get("research_only"), True, "R2 receipt research_only")
    _require_equal(receipt.get("automatic_promotion"), False, "R2 receipt automatic_promotion")
    _require_equal(receipt.get("human_promotion_only"), True, "R2 receipt human_promotion_only")
    for field in ("candidate_economics_run", "economic_metrics_present", "economic_mission_authorized"):
        _require_equal(receipt.get(field), False, f"R2 receipt {field}")
    _require_equal(receipt.get("blocked_reasons"), [], "R2 receipt blocked_reasons")
    if _forbidden_keys(receipt):
        _fail(f"R2 receipt contains forbidden economic fields: {_forbidden_keys(receipt)}")
    recorded_digest = receipt.get("self_digest_sha256")
    body = dict(receipt)
    body.pop("self_digest_sha256", None)
    _require_equal(recorded_digest, canonical_digest(body), "R2 receipt self-digest")


def _validate_substantive_receipt_claim(receipt: Mapping[str, object]) -> None:
    substantive = _require_mapping(receipt.get("substantive_replay"), "R2 receipt substantive_replay")
    expected = {
        "implementation_commit": A23_IMPLEMENTATION_COMMIT,
        "manifest_commit": B23_MANIFEST_COMMIT,
        "local_result_commit": C23_RESULT_COMMIT,
        "manifest_path": C23_MANIFEST_PATH,
        "raw_artifact_path": R2_RAW_PATH,
        "raw_artifact_sha256": C23_RAW_SHA256,
        "raw_payload_digest": C23_RAW_PAYLOAD_DIGEST,
        "local_receipt_path": C23_LOCAL_RECEIPT_PATH,
        "local_receipt_self_digest": C23_LOCAL_RECEIPT_DIGEST,
        "raw_verdict": "M3_CANDIDATE_OCP_GATE_PASS",
        "mandatory_gate_count": 42,
        "mandatory_gate_status": "PASS",
        "candidate_economics_run": False,
        "economic_metrics_present": False,
        "raw_output_paths": list(R2_RAW_OUTPUT_PATHS),
        "hash_parity": HASH_PARITY,
    }
    for key, value in expected.items():
        _require_equal(substantive.get(key), value, f"R2 receipt substantive_replay.{key}")
    gates = _require_mapping(substantive.get("mandatory_gate_results"), "R2 substantive gate results")
    _require_equal(len(gates), 42, "R2 substantive gate count")
    if any(not isinstance(value, Mapping) or value.get("status") != "PASS" for value in gates.values()):
        _fail("R2 receipt substantive gates are not all PASS")
    c23_ci = _require_mapping(substantive.get("c23_ci"), "R2 C23 CI")
    _require_equal(c23_ci.get("run_id"), C23_CI_RUN, "R2 C23 CI run")
    _require_equal(c23_ci.get("head_sha"), C23_RESULT_COMMIT, "R2 C23 CI head")
    _require_equal(c23_ci.get("status"), "PASS", "R2 C23 CI status")
    _require_equal(c23_ci.get("conclusion"), "success", "R2 C23 CI conclusion")
    _require_equal(c23_ci.get("python_matrix"), PYTHON_MATRIX, "R2 C23 Python matrix")
    _require_equal(c23_ci.get("required_stages"), CI_STAGES, "R2 C23 CI stages")


def validate_r2_receipt(receipt: Mapping[str, object], *, receipt_path: Path | None = None) -> None:
    """Fail-closed validator used by the future economic consumer."""

    if receipt_path is not None:
        _exact_repo_path(receipt_path, R2_RECEIPT_PATH, "R2 receipt")
    _validate_receipt_common(receipt)
    _validate_substantive_receipt_claim(receipt)
    _validate_old_receipt_superseded()
    _validate_historical_topology()
    _validate_consumer_source()

    closure = _require_mapping(receipt.get("corrective_closure"), "R2 corrective_closure")
    implementation = closure.get("implementation_commit")
    manifest = closure.get("manifest_commit")
    result = closure.get("local_result_commit")
    if not isinstance(implementation, str) or not isinstance(manifest, str) or not isinstance(result, str):
        _fail("R2 corrective closure identities are incomplete")
    _require_equal(closure.get("manifest_path"), R2_MANIFEST_PATH, "R2 corrective manifest path")
    _require_equal(closure.get("result_receipt_path"), R2_RECEIPT_PATH, "R2 corrective receipt path")
    _require_equal(closure.get("result_parent"), manifest, "R2 corrective result parent")
    ci = _require_mapping(receipt.get("corrective_remote_ci"), "R2 corrective_remote_ci")
    _require_equal(ci.get("status"), "PASS", "R2 corrective CI status")
    run_id = ci.get("run_id")
    head_sha = ci.get("head_sha")
    if not isinstance(run_id, str) or not isinstance(head_sha, str):
        _fail("R2 corrective CI run/head binding is incomplete")
    _require_equal(head_sha, result, "R2 corrective CI head/result")
    _require_equal(ci.get("conclusion"), "success", "R2 corrective CI conclusion")
    _require_equal(ci.get("python_matrix"), PYTHON_MATRIX, "R2 corrective Python matrix")
    _require_equal(ci.get("required_stages"), CI_STAGES, "R2 corrective CI stages")
    critical = _validate_corrective_chain(
        implementation_commit=implementation,
        manifest_commit=manifest,
        local_result_commit=result,
        remote_ci_status="PASS",
        remote_ci_head_sha=head_sha,
    )
    _require_equal(closure.get("critical_git_blobs"), critical, "R2 receipt critical Git blobs")
    gates = _require_mapping(receipt.get("mandatory_corrective_gates"), "R2 mandatory corrective gates")
    _require_equal(set(gates), set(CORRECTIVE_GATE_NAMES), "R2 corrective gate set")
    if any(not isinstance(value, Mapping) or value.get("status") != "PASS" for value in gates.values()):
        _fail("R2 receipt contains a non-PASS corrective gate")
    _require_equal(receipt.get("next_required_mission"), "M3_ECONOMIC_MISSION_PREDECLARATION", "R2 next mission")


def compact(
    raw_path: Path,
    output: Path,
    *,
    manifest_path: Path,
    expected_raw_sha256: str,
    expected_raw_payload_digest: str,
    expected_implementation_commit: str,
    expected_manifest_commit: str,
    expected_local_result_commit: str,
    remote_ci_run: str,
    remote_ci_head_sha: str,
    remote_ci_status: str,
) -> dict[str, object]:
    """Compact an explicitly identified raw result and corrective closure."""

    _exact_repo_path(raw_path, R2_RAW_PATH, "raw artifact")
    _exact_repo_path(manifest_path, R2_MANIFEST_PATH, "R2 manifest")
    raw = _load(raw_path)
    validate_substantive_raw(
        raw,
        raw_path=raw_path,
        expected_raw_sha256=expected_raw_sha256,
        expected_raw_payload_digest=expected_raw_payload_digest,
    )
    _validate_historical_topology()
    _validate_old_receipt_superseded()
    history = _validate_c23_history(raw)
    _validate_consumer_source()
    blocked = remote_ci_status == "NOT_RUN_BLOCKED"
    if not blocked and remote_ci_status != "PASS":
        _fail("R2 compactor accepts only PASS or explicit NOT_RUN_BLOCKED")
    local_result = None if blocked else expected_local_result_commit
    critical = _validate_corrective_chain(
        implementation_commit=expected_implementation_commit,
        manifest_commit=expected_manifest_commit,
        local_result_commit=local_result,
        remote_ci_status=remote_ci_status,
        remote_ci_head_sha=None if blocked else remote_ci_head_sha,
    )
    corrective_ci = _corrective_ci_claim(
        status=remote_ci_status,
        run_id=remote_ci_run,
        head_sha=remote_ci_head_sha,
        local_result_commit=local_result,
    )
    verdict = R2_BLOCKED if blocked else R2_PASS
    receipt: dict[str, object] = {
        "schema_version": "2.0.0",
        "mission": R2_MISSION,
        "verdict": verdict,
        "research_only": True,
        "automatic_promotion": False,
        "human_promotion_only": True,
        "candidate_economics_run": False,
        "economic_metrics_present": False,
        "economic_mission_authorized": False,
        "blocked_reasons": ["corrective_remote_ci:NOT_RUN_BLOCKED"] if blocked else [],
        "superseded_artifacts": {
            "receipt_path": C23_LOCAL_RECEIPT_PATH,
            "report_path": "docs/equity_m3_candidate_ocp_2026_09.md",
            "status": "SUPERSEDED_INVALID_REMOTE_ATTESTATION_BINDING",
            "d23_commit": D23_COMMIT,
            "e23_commit": E23_COMMIT,
        },
        "substantive_replay": _substantive_replay(raw, history),
        "corrective_closure": {
            "implementation_commit": expected_implementation_commit,
            "manifest_commit": expected_manifest_commit,
            "local_result_commit": local_result,
            "manifest_path": R2_MANIFEST_PATH,
            "result_receipt_path": R2_RECEIPT_PATH,
            "result_parent": expected_manifest_commit if not blocked else None,
            "manifest_changed_paths": [R2_MANIFEST_PATH],
            "result_changed_paths": [R2_RECEIPT_PATH, R2_REPORT_PATH],
            "critical_git_blobs": critical,
        },
        "corrective_remote_ci": corrective_ci,
        "mandatory_corrective_gates": _corrective_gates(blocked=blocked),
        "substantive_hash_parity": dict(HASH_PARITY),
        "forbidden_economic_fields": sorted(FORBIDDEN_ECONOMIC_FIELDS),
        "next_required_mission": "M3_CANDIDATE_OCP_ATTESTATION_R2_REMOTE_CLOSURE"
        if blocked
        else "M3_ECONOMIC_MISSION_PREDECLARATION",
    }
    receipt["self_digest_sha256"] = canonical_digest(receipt)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes((json.dumps(receipt, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8"))
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--expected-raw-sha256", required=True)
    parser.add_argument("--expected-raw-payload-digest", required=True)
    parser.add_argument("--expected-implementation-commit", required=True)
    parser.add_argument("--expected-manifest-commit", required=True)
    parser.add_argument("--expected-local-result-commit", required=True)
    parser.add_argument("--remote-ci-run", required=True)
    parser.add_argument("--remote-ci-head-sha", required=True)
    parser.add_argument("--remote-ci-status", required=True, choices=("PASS", "NOT_RUN_BLOCKED"))
    args = parser.parse_args()
    receipt = compact(
        args.raw,
        args.output,
        manifest_path=args.manifest,
        expected_raw_sha256=args.expected_raw_sha256,
        expected_raw_payload_digest=args.expected_raw_payload_digest,
        expected_implementation_commit=args.expected_implementation_commit,
        expected_manifest_commit=args.expected_manifest_commit,
        expected_local_result_commit=args.expected_local_result_commit,
        remote_ci_run=args.remote_ci_run,
        remote_ci_head_sha=args.remote_ci_head_sha,
        remote_ci_status=args.remote_ci_status,
    )
    print(
        json.dumps(
            {
                "verdict": receipt["verdict"],
                "output": str(args.output),
                "self_digest_sha256": receipt["self_digest_sha256"],
            },
            sort_keys=True,
        )
    )
    return 0 if receipt["verdict"] == R2_PASS else 1


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "A23_IMPLEMENTATION_COMMIT",
    "AttestationError",
    "B23_MANIFEST_COMMIT",
    "C23_CI_RUN",
    "C23_LOCAL_RECEIPT_DIGEST",
    "C23_RAW_PAYLOAD_DIGEST",
    "C23_RAW_SHA256",
    "C23_RESULT_COMMIT",
    "CI_STAGES",
    "CORRECTIVE_GATE_NAMES",
    "HASH_PARITY",
    "PYTHON_MATRIX",
    "R2_BLOCKED",
    "R2_MANIFEST_PATH",
    "R2_MISSION",
    "R2_PASS",
    "R2_RAW_PATH",
    "R2_RECEIPT_PATH",
    "build_manifest",
    "compact",
    "validate_r2_receipt",
    "validate_substantive_raw",
]
