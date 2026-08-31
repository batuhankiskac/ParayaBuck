#!/usr/bin/env python3
"""Validate a raw OCP receipt and emit its tracked compact representation.

The parity runner writes a raw receipt outside the tracked evidence tree.  This
compactor is deliberately small and allowlisted: it verifies the raw payload's
canonical digest, its committed manifest/source chain, and the runner bytes
before copying only the fields needed for connector-visible evidence.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
DEFAULT_RAW = REPO / "data/research/equity_candidate_observation_parity_2026_08/parity_receipt.json"
DEFAULT_OUTPUT = REPO / "data/research_receipts/equity_candidate_observation_parity_corrective_2026_08.json"
RUNNER_PATH = "scripts/research_candidate_observation_parity.py"

from parayabuck.backtest.observation_parity import canonical_digest  # noqa: E402


def _sha256_bytes(data: bytes, *, normalize_newlines: bool = False) -> str:
    if normalize_newlines:
        data = data.replace(b"\r\n", b"\n")
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path, *, normalize_newlines: bool = False) -> str:
    return _sha256_bytes(path.read_bytes(), normalize_newlines=normalize_newlines)


def _git_rev_parse(ref: str) -> str:
    return subprocess.check_output(["git", "rev-parse", ref], cwd=REPO, text=True).strip()


def _git_blob(ref: str) -> bytes:
    return subprocess.check_output(["git", "cat-file", "blob", ref], cwd=REPO)


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


def _validate_source_provenance(raw: dict[str, Any]) -> None:
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


def _validate_raw(raw: dict[str, Any], raw_bytes: bytes) -> str:
    declared = raw.get("receipt_sha256")
    body = dict(raw)
    body.pop("receipt_sha256", None)
    if not isinstance(declared, str) or canonical_digest(body) != declared:
        raise ValueError("raw receipt canonical digest mismatch")
    _validate_source_provenance(raw)
    dirty_before = raw.get("dirty_paths_before")
    dirty_after = raw.get("dirty_paths_after")
    if dirty_before != [] or dirty_after != []:
        raise ValueError("raw receipt must prove a clean worktree before and after the run")
    if raw.get("policy", {}).get("candidate_economics_run") is not False:
        raise ValueError("raw receipt is not research-only")
    checks = raw.get("checks")
    if not isinstance(checks, dict):
        raise ValueError("raw receipt is missing parity checks")
    for name, check in checks.items():
        if not isinstance(check, dict):
            raise ValueError(f"raw receipt check is malformed: {name}")
        if name in {"E1_canonical_v3_explicit_self_parity", "E2_synthetic_changed_ranker_canary"}:
            comparison = check.get("comparison")
            passed = check.get("passed")
            if passed is None and isinstance(comparison, dict):
                passed = comparison.get("passed")
            if passed is not True:
                raise ValueError(f"raw receipt check did not pass: {name}")
        elif name == "E3_candidate_execution_boundary":
            if check.get("candidate_economics_run") is not False:
                raise ValueError("raw receipt E3 must not run candidate economics")
        else:
            raise ValueError(f"raw receipt contains an unknown parity check: {name}")
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
