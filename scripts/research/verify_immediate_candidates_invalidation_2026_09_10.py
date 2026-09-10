"""Verify the immediate A01-A06 invalidation bundle without rerunning work."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scripts.research import build_immediate_candidates_invalidation_2026_09_10 as builder


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"json_object_required:{path}")
    return value


def _artifact_pin(repo: Path, declaration: dict[str, Any], label: str) -> None:
    path = repo / declaration["path"]
    if not path.is_file() or builder.sha256(path) != declaration.get("sha256"):
        raise ValueError(f"artifact_hash_mismatch:{label}")


def verify_bundle(repo: Path = builder.REPO, output_dir: Path = builder.OUTPUT_DIR, report_path: Path = builder.REPORT) -> dict[str, Any]:
    decision_path, manifest_path, receipt_path = (output_dir / name for name in ("decision.json", "manifest.json", "receipt.json"))
    decision = _load(decision_path)
    if decision.get("decision_sha256") != builder.canonical_hash(decision, "decision_sha256"):
        raise ValueError("decision_self_hash_mismatch")
    if decision.get("classification") != "POST_OUTCOME_PROTOCOL_CHANGE_INVALID" or decision.get("candidate_ids") != ["A01", "A02", "A03", "A04", "A05", "A06"]:
        raise ValueError("decision_classification_invalid")
    for field, expected in (("diagnostic_metrics_preserved", True), ("diagnostic_metrics_reuse_prohibited", True), ("duplicate_dividend_unresolved", True), ("full_rerun_required", False), ("capacity_joint_nav_missing", True), ("admission", False), ("integration", False), ("promotion", False), ("production_paper_live_untouched", True), ("network_used", False), ("economic_work_executed_by_builder", False)):
        if decision.get(field) is not expected:
            raise ValueError(f"decision_safety_field_invalid:{field}")
    pins = decision.get("source_pins")
    if not isinstance(pins, dict) or set(pins) != {builder.MANIFEST, builder.RESULTS, builder.RUN_RECEIPT, builder.RUN_REPORT, builder.RUN_LEDGER, builder.RUNNER, builder.RUN_VERIFIER}:
        raise ValueError("source_pin_inventory_invalid")
    for label, declaration in pins.items():
        _artifact_pin(repo, declaration, label)
    if pins[builder.MANIFEST]["sha256"] != builder.EXPECTED_MANIFEST_SHA:
        raise ValueError("frozen_manifest_pin_invalid")
    manifest = _load(manifest_path)
    if manifest.get("manifest_sha256") != builder.canonical_hash(manifest, "manifest_sha256"):
        raise ValueError("manifest_self_hash_mismatch")
    if manifest.get("classification") != decision["classification"] or manifest.get("inputs") != pins:
        raise ValueError("manifest_binding_invalid")
    for label in ("decision", "report"):
        _artifact_pin(repo, manifest[label], f"manifest_{label}")
    receipt = _load(receipt_path)
    if receipt.get("receipt_sha256") != builder.canonical_hash(receipt, "receipt_sha256"):
        raise ValueError("receipt_self_hash_mismatch")
    if receipt.get("classification") != decision["classification"] or receipt.get("candidate_count") != 6 or receipt.get("full_rerun_required") is not False or receipt.get("admission") is not False or receipt.get("integration") is not False or receipt.get("promotion") is not False:
        raise ValueError("receipt_safety_fields_invalid")
    for label, path in (("decision", decision_path), ("manifest", manifest_path), ("report", report_path)):
        if receipt.get(label, {}).get("sha256") != builder.sha256(path):
            raise ValueError(f"receipt_artifact_hash_mismatch:{label}")
    if "IMMEDIATE_CANDIDATES_INVALIDATION_COMPLETE" not in report_path.read_text(encoding="utf-8"):
        raise ValueError("completion_marker_missing")
    return {"status": "IMMEDIATE_CANDIDATES_INVALIDATION_COMPLETE", "classification": decision["classification"], "verified": True}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, default=builder.REPO)
    parser.add_argument("--output-dir", type=Path, default=builder.OUTPUT_DIR)
    parser.add_argument("--report", type=Path, default=builder.REPORT)
    args = parser.parse_args()
    try:
        print(json.dumps(verify_bundle(args.repo, args.output_dir, args.report), sort_keys=True))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "FAILED", "error": str(exc), "verified": False}, sort_keys=True))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
