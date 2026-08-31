#!/usr/bin/env python3
"""Validate a raw OCP receipt and emit its tracked compact representation.

The parity runner writes a raw receipt outside the tracked evidence tree.  This
compactor is deliberately small and allowlisted: it verifies the raw payload's
canonical digest, its committed manifest/source chain, and the runner bytes
before copying only the fields needed for connector-visible evidence.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
DEFAULT_RAW = REPO / "data/research/equity_candidate_observation_parity_2026_08/parity_receipt.json"
DEFAULT_OUTPUT = REPO / "data/research_receipts/equity_candidate_observation_parity_corrective_2026_08.json"
RUNNER_PATH = "scripts/research_candidate_observation_parity.py"

from parayabuck.backtest.observation_parity import canonical_digest  # noqa: E402
from parayabuck.research.normalized_hash import sha256_bytes, sha256_file  # noqa: E402

EXPECTED_CHECKS = frozenset(
    {
        "E1_canonical_v3_explicit_self_parity",
        "E2_synthetic_changed_ranker_canary",
        "E3_candidate_execution_boundary",
    }
)
EXPECTED_UNIVERSE_HASH = "d51ab5c816f9614cf9508d5ab6529e4cd8cad9d7d1feb886b233a3ad3556a011"


def _sha256_bytes(data: bytes, *, normalize_newlines: bool = False) -> str:
    return sha256_bytes(data, normalize_newlines=normalize_newlines)


def _sha256_file(path: Path, *, normalize_newlines: bool = False) -> str:
    return sha256_file(path, normalize_newlines=normalize_newlines)


def _git_rev_parse(ref: str) -> str:
    return subprocess.check_output(["git", "rev-parse", ref], cwd=REPO, text=True).strip()


def _git_blob(ref: str) -> bytes:
    return subprocess.check_output(["git", "cat-file", "blob", ref], cwd=REPO)


def _require_sha256(value: object, field: str) -> str:
    if not isinstance(value, str) or len(value) != 64:
        raise ValueError(f"raw receipt {field} must be a 64-character SHA-256")
    try:
        int(value, 16)
    except ValueError as exc:
        raise ValueError(f"raw receipt {field} is not hexadecimal SHA-256") from exc
    return value


def _require_nonnegative_int(value: object, field: str, *, positive: bool = False) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < (1 if positive else 0):
        qualifier = "positive" if positive else "non-negative"
        raise ValueError(f"raw receipt {field} must be a {qualifier} integer")
    return value


def _require_commit(value: object, field: str) -> str:
    if not isinstance(value, str) or len(value) != 40:
        raise ValueError(f"raw receipt {field} must be a full 40-character commit SHA")
    try:
        resolved = _git_rev_parse(f"{value}^{{commit}}")
    except subprocess.CalledProcessError as exc:
        raise ValueError(f"raw receipt {field} is not a reachable commit: {value}") from exc
    if resolved != value:
        raise ValueError(f"raw receipt {field} does not resolve exactly: {value}")
    return value


def _validate_artifact_provenance(
    artifact: object,
    run_commit: str,
    field: str,
    *,
    require_tracked: bool = False,
) -> None:
    if not isinstance(artifact, dict):
        raise ValueError(f"raw receipt {field} provenance is missing")
    path = artifact.get("path")
    if not isinstance(path, str) or not path or Path(path).is_absolute() or ".." in Path(path).parts:
        raise ValueError(f"raw receipt {field}.path must be a repository-relative path")
    if artifact.get("commit") != run_commit:
        raise ValueError(f"raw receipt {field}.commit does not equal run_commit")
    tracked = artifact.get("git_tracked")
    if not isinstance(tracked, bool):
        raise ValueError(f"raw receipt {field}.git_tracked must be boolean")
    recorded = _require_sha256(artifact.get("sha256"), f"{field}.sha256")
    raw_sha = _require_sha256(artifact.get("raw_sha256"), f"{field}.raw_sha256")
    repo_path = REPO / path
    if not repo_path.is_file():
        raise ValueError(f"raw receipt {field} path is missing from the checkout: {path}")
    if _sha256_file(repo_path, normalize_newlines=True) != recorded:
        raise ValueError(f"raw receipt {field}.sha256 does not match the current artifact")
    if _sha256_file(repo_path, normalize_newlines=False) != raw_sha:
        raise ValueError(f"raw receipt {field}.raw_sha256 does not match the current artifact")
    try:
        blob = _git_blob(f"{run_commit}:{path}")
    except subprocess.CalledProcessError:
        if require_tracked or tracked:
            raise ValueError(f"raw receipt {field} claims a tracked artifact absent from the pinned tree") from None
        if artifact.get("provenance") != "external_untracked":
            raise ValueError(f"raw receipt {field} must explicitly mark external/untracked provenance")
        if artifact.get("git_blob_sha256") is not None or artifact.get("git_blob_normalized_sha256") is not None:
            raise ValueError(f"raw receipt {field} external artifact must not claim a Git blob")
        return
    if not tracked or artifact.get("provenance") != "git_blob":
        raise ValueError(f"raw receipt {field} is present in the pinned tree but not marked git_blob")
    blob_sha = _require_sha256(artifact.get("git_blob_sha256"), f"{field}.git_blob_sha256")
    blob_normalized_sha = _require_sha256(
        artifact.get("git_blob_normalized_sha256"), f"{field}.git_blob_normalized_sha256"
    )
    if blob_sha != _sha256_bytes(blob):
        raise ValueError(f"raw receipt {field}.git_blob_sha256 does not match the pinned tree")
    if blob_normalized_sha != _sha256_bytes(blob, normalize_newlines=True) or blob_normalized_sha != recorded:
        raise ValueError(f"raw receipt {field} normalized hash does not match the pinned tree")


def _validate_source_provenance(raw: dict[str, Any]) -> dict[str, Any]:
    run_commit = _require_commit(raw.get("run_commit"), "run_commit")
    implementation = _require_commit(raw.get("implementation_commit"), "implementation_commit")
    base = _require_commit(raw.get("base_commit"), "base_commit")
    if raw.get("source_commit") != implementation:
        raise ValueError("raw receipt source_commit must equal implementation_commit")
    if _git_rev_parse(f"{run_commit}^") != implementation:
        raise ValueError("raw receipt run commit is not the direct child of implementation_commit")
    run_parent = raw.get("run_parent_commit")
    if run_parent != implementation:
        raise ValueError("raw receipt run_parent_commit does not match run_commit parent")
    implementation_parent = raw.get("implementation_parent_commit")
    if implementation_parent != _git_rev_parse(f"{implementation}^"):
        raise ValueError("raw receipt implementation_parent_commit does not match source history")
    if (
        subprocess.run(["git", "merge-base", "--is-ancestor", base, implementation], cwd=REPO, check=False).returncode
        != 0
    ):
        raise ValueError("raw receipt implementation is not descended from base_commit")

    manifest = raw.get("manifest")
    if not isinstance(manifest, dict):
        raise ValueError("raw receipt is missing manifest provenance")
    manifest_path = manifest.get("path")
    if not isinstance(manifest_path, str) or not manifest_path:
        raise ValueError("raw receipt manifest path is missing")
    try:
        manifest_blob = _git_blob(f"{run_commit}:{manifest_path}")
    except subprocess.CalledProcessError as exc:
        raise ValueError("raw receipt manifest is absent from the pinned run tree") from exc
    if manifest.get("commit") != run_commit:
        raise ValueError("raw receipt manifest commit does not equal run_commit")
    if manifest.get("blob_sha256") != _sha256_bytes(manifest_blob):
        raise ValueError("raw receipt manifest blob hash does not match the pinned run tree")

    recorded_tree = raw.get("source_tree_sha1")
    if recorded_tree != _git_rev_parse(f"{run_commit}^{{tree}}"):
        raise ValueError("raw receipt source tree does not match run_commit")
    implementation_tree = raw.get("implementation_tree_sha1")
    if implementation_tree != _git_rev_parse(f"{implementation}^{{tree}}"):
        raise ValueError("raw receipt implementation tree does not match implementation_commit")

    runner_blob = _git_blob(f"{run_commit}:{RUNNER_PATH}")
    if raw.get("runner_sha256") != _sha256_bytes(runner_blob, normalize_newlines=True):
        raise ValueError("raw receipt runner hash does not match the pinned run tree")
    try:
        manifest_payload = json.loads(manifest_blob.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("raw receipt manifest is not valid UTF-8 JSON") from exc
    if not isinstance(manifest_payload, dict):
        raise ValueError("raw receipt manifest must be a JSON object")
    return manifest_payload


def _validate_comparison(check: dict[str, Any], name: str, *, require_positive_changes: bool = False) -> dict[str, Any]:
    comparison = check.get("comparison")
    if not isinstance(comparison, dict) or comparison.get("passed") is not True:
        raise ValueError(f"raw receipt check did not pass: {name}")
    snapshots = comparison.get("snapshot_count")
    if not isinstance(snapshots, dict):
        raise ValueError(f"raw receipt {name} snapshot_count is missing")
    for field in ("aligned", "control", "candidate"):
        _require_nonnegative_int(snapshots.get(field), f"{name}.comparison.snapshot_count.{field}", positive=True)
    mismatch_counts = comparison.get("mismatch_counts")
    if not isinstance(mismatch_counts, dict) or not mismatch_counts:
        raise ValueError(f"raw receipt {name} mismatch_counts are missing")
    for field, value in mismatch_counts.items():
        if _require_nonnegative_int(value, f"{name}.comparison.mismatch_counts.{field}") != 0:
            raise ValueError(f"raw receipt {name} has a non-zero mismatch count: {field}")
    if not isinstance(comparison.get("failure_reasons"), list) or comparison["failure_reasons"]:
        raise ValueError(f"raw receipt {name} has failure reasons")
    observation_set = comparison.get("observation_set")
    if not isinstance(observation_set, dict):
        raise ValueError(f"raw receipt {name} observation_set is missing")
    sequence_changes = _require_nonnegative_int(
        observation_set.get("sequence_change_count"), f"{name}.comparison.observation_set.sequence_change_count"
    )
    membership_changes = _require_nonnegative_int(
        observation_set.get("membership_change_count"), f"{name}.comparison.observation_set.membership_change_count"
    )
    if require_positive_changes and (sequence_changes <= 0 or membership_changes <= 0):
        raise ValueError(f"raw receipt {name} did not demonstrate a changed observation ranker")
    return comparison


def _validate_e1(check: dict[str, Any], manifest: dict[str, Any]) -> None:
    comparison = _validate_comparison(check, "E1_canonical_v3_explicit_self_parity")
    hashes = {
        "control_universe_hash": check.get("control_universe_hash"),
        "explicit_override_universe_hash": check.get("explicit_override_universe_hash"),
        "expected_universe_hash": check.get("expected_universe_hash"),
        "comparison.control_universe_hash": comparison.get("control_universe_hash"),
        "comparison.candidate_universe_hash": comparison.get("candidate_universe_hash"),
    }
    expected = manifest.get("canonical_control", {}).get("component_hashes", {}).get("universe")
    if expected != EXPECTED_UNIVERSE_HASH:
        raise ValueError("pinned manifest canonical universe hash is not the frozen value")
    for field, value in hashes.items():
        if _require_sha256(value, f"E1.{field}") != expected:
            raise ValueError(f"raw receipt E1 {field} does not match the frozen universe hash")
    if check.get("all_snapshots_identity") is not True:
        raise ValueError("raw receipt E1 does not prove all snapshot identities")
    pins = check.get("pins_and_capacity")
    if not isinstance(pins, dict) or _require_nonnegative_int(pins.get("mismatch_count"), "E1.pins_and_capacity") != 0:
        raise ValueError("raw receipt E1 has a pin/capacity mismatch")


def _validate_e2(check: dict[str, Any]) -> None:
    comparison = _validate_comparison(check, "E2_synthetic_changed_ranker_canary", require_positive_changes=True)
    if (
        _require_nonnegative_int(check.get("candidate_rank_outside_pool_count"), "E2.candidate_rank_outside_pool_count")
        != 0
    ):
        raise ValueError("raw receipt E2 contains a candidate rank outside the eligible pool")
    if check.get("future_bar_leak_detected") is not False:
        raise ValueError("raw receipt E2 detected future-bar leakage")
    candidate_hash = _require_sha256(check.get("candidate_universe_hash"), "E2.candidate_universe_hash")
    repeated_hash = _require_sha256(
        check.get("repeated_candidate_universe_hash"), "E2.repeated_candidate_universe_hash"
    )
    if candidate_hash != repeated_hash:
        raise ValueError("raw receipt E2 repeated candidate universe hash differs")
    _require_sha256(check.get("data_sha256"), "E2.data_sha256")
    if not check.get("direct_observation_set") or not check.get("candidate_latest_observation_set"):
        raise ValueError("raw receipt E2 observation sets must be non-empty")
    # ``_validate_comparison`` already proves every comparison mismatch count is zero.
    _ = comparison


def _validate_e3(check: dict[str, Any]) -> None:
    if check.get("candidate_economics_run") is not False:
        raise ValueError("raw receipt E3 must not run candidate economics")
    if check.get("fresh_instance_checks") is not True:
        raise ValueError("raw receipt E3 must explicitly prove fresh-instance checks")
    if set(check) != {"candidate_economics_run", "fresh_instance_checks", "C|C", "X|C", "X|X"}:
        raise ValueError("raw receipt E3 must contain exactly C|C, X|C, X|X and boundary flags")
    configured = {"C|C", "X|C"}
    for basis in configured:
        case = check[basis]
        if not isinstance(case, dict) or case.get("observation_basis") != "configured":
            raise ValueError(f"raw receipt E3 {basis} must use the configured observation basis")
        if not isinstance(case.get("execution_strategy_identity"), dict):
            raise ValueError(f"raw receipt E3 {basis} execution identity is missing")
    candidate = check["X|X"]
    if not isinstance(candidate, dict) or candidate.get("observation_basis") != "candidate_owned":
        raise ValueError("raw receipt E3 X|X must use the candidate-owned observation basis")
    observation_identity = candidate.get("observation_strategy_identity")
    execution_identity = candidate.get("execution_strategy_identity")
    if not isinstance(observation_identity, dict) or not isinstance(execution_identity, dict):
        raise ValueError("raw receipt E3 X|X identities are incomplete")
    if observation_identity != execution_identity:
        raise ValueError("raw receipt E3 X|X observation and execution identities differ")
    for field in ("market", "name", "version", "timeframe", "parameters", "parameter_digest_sha256"):
        if field not in observation_identity:
            raise ValueError(f"raw receipt E3 X|X identity is missing {field}")
    _require_sha256(observation_identity["parameter_digest_sha256"], "E3.X|X.parameter_digest_sha256")
    _require_sha256(candidate.get("observation_universe_hash"), "E3.X|X.observation_universe_hash")


def _validate_raw(raw: dict[str, Any], raw_bytes: bytes) -> str:
    declared = raw.get("receipt_sha256")
    body = dict(raw)
    body.pop("receipt_sha256", None)
    if not isinstance(declared, str) or canonical_digest(body) != declared:
        raise ValueError("raw receipt canonical digest mismatch")
    manifest = _validate_source_provenance(raw)
    dirty_before = raw.get("dirty_paths_before")
    dirty_after = raw.get("dirty_paths_after")
    if dirty_before != [] or dirty_after != []:
        raise ValueError("raw receipt must prove a clean worktree before and after the run")
    if raw.get("policy", {}).get("candidate_economics_run") is not False:
        raise ValueError("raw receipt is not research-only")
    checks = raw.get("checks")
    if not isinstance(checks, dict):
        raise ValueError("raw receipt is missing parity checks")
    if set(checks) != EXPECTED_CHECKS:
        raise ValueError(f"raw receipt checks must be exactly {sorted(EXPECTED_CHECKS)}")
    if not all(isinstance(checks[name], dict) for name in EXPECTED_CHECKS):
        raise ValueError("raw receipt parity checks must be JSON objects")
    _validate_e1(checks["E1_canonical_v3_explicit_self_parity"], manifest)
    _validate_e2(checks["E2_synthetic_changed_ranker_canary"])
    _validate_e3(checks["E3_candidate_execution_boundary"])

    run_commit = _require_commit(raw["run_commit"], "run_commit")
    config = raw.get("config")
    _validate_artifact_provenance(config, run_commit, "config", require_tracked=True)
    if not isinstance(config, dict) or config.get("path") != "config/settings.yaml":
        raise ValueError("raw receipt config path must be config/settings.yaml")
    _require_sha256(config.get("semantic_digest_sha256"), "config.semantic_digest_sha256")
    if raw.get("config_digest_sha256") != config.get("semantic_digest_sha256"):
        raise ValueError("raw receipt config semantic digest is inconsistent")

    dataset = raw.get("dataset")
    if not isinstance(dataset, dict) or not isinstance(dataset.get("artifacts"), dict):
        raise ValueError("raw receipt dataset artifact provenance is missing")
    artifacts = dataset["artifacts"]
    expected_artifacts = {"bars", "lifetimes", "corporate_actions"}
    if set(artifacts) != expected_artifacts:
        raise ValueError("raw receipt dataset artifacts must contain bars, lifetimes, corporate_actions exactly")
    for name in expected_artifacts:
        _validate_artifact_provenance(artifacts[name], run_commit, f"dataset.artifacts.{name}")
    flat_fields = {
        "bars": ("bars_path", "bars_sha256"),
        "lifetimes": ("lifetimes_path", "lifetimes_sha256"),
        "corporate_actions": ("corporate_actions_path", "corporate_actions_sha256"),
    }
    for name, (path_field, hash_field) in flat_fields.items():
        if dataset.get(path_field) != artifacts[name].get("path") or dataset.get(hash_field) != artifacts[name].get(
            "sha256"
        ):
            raise ValueError(f"raw receipt dataset flat field disagrees with {name} provenance")
    return declared


def compact(raw_path: Path = DEFAULT_RAW, output: Path = DEFAULT_OUTPUT) -> dict[str, Any]:
    raw_path = raw_path if raw_path.is_absolute() else REPO / raw_path
    output = output if output.is_absolute() else REPO / output
    raw_bytes = raw_path.read_bytes()
    raw = json.loads(raw_bytes.decode("utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("raw receipt must be a JSON object")
    raw_payload_digest = _validate_raw(raw, raw_bytes)

    fields = (
        "schema_version",
        "receipt_name",
        "source_commit",
        "run_commit",
        "base_commit",
        "implementation_commit",
        "implementation_parent_commit",
        "run_parent_commit",
        "source_tree_sha1",
        "implementation_tree_sha1",
        "python_version",
        "runner_sha256",
        "config_digest_sha256",
        "config",
        "manifest",
        "invocation",
        "hash_policy",
        "dataset",
        "policy",
        "checks",
        "dirty_paths_before",
        "dirty_paths_after",
    )
    compacted: dict[str, Any] = {field: raw[field] for field in fields if field in raw}
    compacted["raw_payload_receipt_sha256"] = raw_payload_digest
    compacted["raw_receipt_sha256"] = _sha256_bytes(raw_bytes)
    compacted["compactor"] = {
        "path": "scripts/compact_candidate_observation_parity_receipt.py",
        "sha256": _sha256_file(Path(__file__), normalize_newlines=True),
    }
    compacted["compact_receipt_sha256"] = canonical_digest(compacted)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(compacted, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
    return compacted


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw", type=Path, default=DEFAULT_RAW)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    payload = compact(args.raw, args.output)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
