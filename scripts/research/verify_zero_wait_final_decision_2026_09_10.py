"""Verify the zero-wait final closure without rerunning any research."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scripts.research import build_zero_wait_final_decision_2026_09_10 as builder


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
    if decision.get("decision_sha256") != builder.self_hash(decision, "decision_sha256"):
        raise ValueError("decision_self_hash_mismatch")
    if decision.get("schema") != "zero-wait-final-decision-v1" or decision.get("status") != "CLOSED_ZERO_WAIT_CONSTRAINT":
        raise ValueError("decision_status_invalid")
    if decision.get("source_pins") != {path: builder.pin(repo, path) for path in builder.EXPECTED}:
        raise ValueError("decision_source_pins_changed")
    old = decision["original_34"]
    if old.get("candidate_count") != 34 or old.get("immediate_count") != 0 or old.get("counts") != {"CLOSE_ZERO_WAIT": 2, "CLOSE_NOT_IMMEDIATE": 7, "CLOSE_UNAUTHORIZED_PAID_DATA": 25, "IMMEDIATE_RETEST": 0}:
        raise ValueError("original_34_counts_invalid")
    a01 = decision["a01_a06"]
    if a01.get("candidate_count") != 6 or a01.get("classification") != "POST_OUTCOME_PROTOCOL_CHANGE_INVALID" or a01.get("admitted") != 0 or a01.get("integration") != 0 or a01.get("dropped") != 6:
        raise ValueError("a01_a06_closure_invalid")
    zwo = decision["zero_wait_overlay"]
    if zwo.get("candidate_count") != 5 or zwo.get("case_count") != 45 or zwo.get("classification") != "CLOSE_ZERO_WAIT_TESTED_FAIL_AND_INVALID_EVIDENCE" or zwo.get("admitted") != 0 or zwo.get("integration") != 0 or zwo.get("dropped") != 5 or zwo.get("all_primary_gates") != "FAIL":
        raise ValueError("zwo_closure_invalid")
    aggregate = decision["aggregate"]
    if aggregate.get("primary_attempt_count") != 45 or aggregate.get("integration_ready") != 0 or aggregate.get("admitted") != 0 or aggregate.get("family_globally_disproven") is not False:
        raise ValueError("aggregate_closure_invalid")
    policy = decision["closure_policy"]
    if policy.get("same_sample_rerun_required") is not False or policy.get("no_pending_work_marker") is not True or policy.get("production_config_paper_live_untouched") is not True:
        raise ValueError("closure_policy_invalid")
    for path in (decision_path, report_path):
        if "PENDING" in path.read_text(encoding="utf-8"):
            raise ValueError("pending_marker_present")

    manifest = _load(manifest_path)
    if manifest.get("manifest_sha256") != builder.self_hash(manifest, "manifest_sha256"):
        raise ValueError("manifest_self_hash_mismatch")
    if manifest.get("classification") != "CLOSED_ZERO_WAIT_CONSTRAINT" or manifest.get("inputs") != decision["source_pins"]:
        raise ValueError("manifest_binding_invalid")
    for label in ("decision", "report"):
        _artifact_pin(repo, manifest[label], f"manifest_{label}")

    receipt = _load(receipt_path)
    if receipt.get("receipt_sha256") != builder.self_hash(receipt, "receipt_sha256"):
        raise ValueError("receipt_self_hash_mismatch")
    if receipt.get("status") != "ZERO_WAIT_FINAL_CLOSURE_COMPLETE" or receipt.get("counts", {}).get("primary_attempts") != 45 or receipt.get("counts", {}).get("integration_ready") != 0:
        raise ValueError("receipt_status_or_counts_invalid")
    if receipt.get("admission") is not False or receipt.get("integration") is not False or receipt.get("promotion") is not False or receipt.get("family_globally_disproven") is not False or receipt.get("same_sample_rerun_required") is not False:
        raise ValueError("receipt_safety_fields_invalid")
    for label, path in (("decision", decision_path), ("manifest", manifest_path), ("report", report_path)):
        if receipt.get(label, {}).get("sha256") != builder.sha256(path):
            raise ValueError(f"receipt_artifact_hash_mismatch:{label}")
    if "ZERO_WAIT_FINAL_CLOSURE_COMPLETE" not in report_path.read_text(encoding="utf-8"):
        raise ValueError("completion_marker_missing")
    return {"status": "ZERO_WAIT_FINAL_CLOSURE_COMPLETE", "verified": True, "primary_attempts": 45, "integration_ready": 0}


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
