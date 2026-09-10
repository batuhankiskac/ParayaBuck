"""Verify the zero-wait decision bundle without network or economic execution."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scripts.research import build_strategy_queue_zero_wait_decision_2026_09_09 as builder


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"json_object_required:{path}")
    return value


def _check_pin(repo: Path, declaration: dict[str, Any], label: str) -> None:
    relative = declaration.get("path")
    if not isinstance(relative, str) or Path(relative).is_absolute() or ".." in Path(relative).parts:
        raise ValueError(f"invalid_path:{label}")
    path = repo / relative
    if not path.is_file() or builder.sha256(path) != declaration.get("sha256"):
        raise ValueError(f"hash_mismatch:{label}")
    stat = path.stat()
    if stat.st_size != declaration.get("bytes") or stat.st_mtime_ns != declaration.get("mtime_ns"):
        raise ValueError(f"pin_mismatch:{label}")


def verify_bundle(repo: Path = builder.REPO, output_dir: Path = builder.DEFAULT_OUTPUT_DIR, report_path: Path = builder.DEFAULT_REPORT) -> dict[str, Any]:
    decision_path = output_dir / builder.DECISION_NAME
    manifest_path = output_dir / builder.MANIFEST_NAME
    receipt_path = output_dir / builder.RECEIPT_NAME
    decision = _load(decision_path)
    if decision.get("decision_sha256") != builder.canonical_hash(decision, "decision_sha256"):
        raise ValueError("decision_self_hash_mismatch")
    if decision.get("candidate_ids") != builder.EXPECTED_IDS or decision.get("candidate_count") != 34 or decision.get("candidate_ids_unique") is not True:
        raise ValueError("decision_inventory_invalid")
    if decision.get("immediate_retest_count") != 0 or decision.get("research_only") is not True or decision.get("network_used") is not False or decision.get("production_paper_live_untouched") is not True or decision.get("no_auto_promotion") is not True:
        raise ValueError("decision_safety_flags_invalid")
    if decision.get("counts") != {
        "CLOSE_NOT_IMMEDIATE": len(builder.CLOSE_NOT_IMMEDIATE),
        "CLOSE_UNAUTHORIZED_PAID_DATA": len(builder.CLOSE_UNAUTHORIZED_PAID_DATA),
        "CLOSE_ZERO_WAIT": len(builder.CLOSE_ZERO_WAIT),
        "IMMEDIATE_RETEST": 0,
    }:
        raise ValueError("decision_counts_invalid")
    rows = decision.get("decisions")
    if not isinstance(rows, list) or [row.get("candidate_id") for row in rows] != builder.EXPECTED_IDS:
        raise ValueError("decision_rows_invalid")
    for row in rows:
        candidate_id = row["candidate_id"]
        if row.get("classification") != builder.EXPECTED_CLASSIFICATION[candidate_id] or row.get("immediate_retest") is not False or row.get("economic_run") is not False or row.get("economic_eligible") is not False or row.get("promotion") is not False or row.get("admitted") != 0:
            raise ValueError(f"candidate_decision_invalid:{candidate_id}")
        if not isinstance(row.get("detail"), str) or not row["detail"].strip():
            raise ValueError(f"candidate_detail_missing:{candidate_id}")
    pins = decision.get("source_pins")
    if not isinstance(pins, dict) or set(pins) != {builder.AGGREGATE, builder.CANDIDATES}:
        raise ValueError("source_pins_invalid")
    for relative, declaration in pins.items():
        _check_pin(repo, declaration, relative)
    manifest = _load(manifest_path)
    if manifest.get("manifest_sha256") != builder.canonical_hash(manifest, "manifest_sha256"):
        raise ValueError("manifest_self_hash_mismatch")
    if manifest.get("inputs") != pins or manifest.get("counts") != decision["counts"] or manifest.get("immediate_retest_count") != 0:
        raise ValueError("manifest_binding_invalid")
    for label in ("decision", "report"):
        declaration = manifest.get(label)
        if not isinstance(declaration, dict) or not isinstance(declaration.get("path"), str):
            raise ValueError(f"manifest_artifact_invalid:{label}")
        path = repo / declaration["path"]
        if not path.is_file() or builder.sha256(path) != declaration.get("sha256"):
            raise ValueError(f"manifest_artifact_hash_mismatch:{label}")
    receipt = _load(receipt_path)
    if receipt.get("receipt_sha256") != builder.canonical_hash(receipt, "receipt_sha256"):
        raise ValueError("receipt_self_hash_mismatch")
    if receipt.get("candidate_count") != 34 or receipt.get("immediate_retest_count") != 0 or receipt.get("counts") != decision["counts"] or receipt.get("economic_run") is not False or receipt.get("promotion") is not False or receipt.get("network_used") is not False:
        raise ValueError("receipt_safety_flags_invalid")
    for label, path in (("decision", decision_path), ("manifest", manifest_path), ("report", report_path)):
        declaration = receipt.get(label)
        if not isinstance(declaration, dict) or declaration.get("sha256") != builder.sha256(path):
            raise ValueError(f"receipt_artifact_hash_mismatch:{label}")
    if "ZERO_WAIT_PRUNE_COMPLETE" not in report_path.read_text(encoding="utf-8"):
        raise ValueError("completion_marker_missing")
    return {"status": "ZERO_WAIT_PRUNE_COMPLETE", "candidate_count": 34, "counts": decision["counts"], "verified": True}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, default=builder.REPO)
    parser.add_argument("--output-dir", type=Path, default=builder.DEFAULT_OUTPUT_DIR)
    parser.add_argument("--report", type=Path, default=builder.DEFAULT_REPORT)
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    try:
        print(json.dumps(verify_bundle(args.repo, args.output_dir, args.report), sort_keys=True))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "FAILED", "error": str(exc), "verified": False}, sort_keys=True))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
