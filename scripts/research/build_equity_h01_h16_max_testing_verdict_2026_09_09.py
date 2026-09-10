"""Build the final local/public exhaustion verdict matrix for equity H01-H16."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
PROTOCOL = REPO / "data/research_runs/equity_h01_h16_standalone_overlay_2026_09_09/protocol_results.json"
LOCAL = REPO / "data/research_runs/equity_local_diagnostics_2026_09_09/diagnostic_results.json"
H14C = REPO / "data/research_runs/equity_h14c_posthoc_2026_09_09/results.json"
H01_H05 = REPO / "data/research/equity_h01_h05_causal_input_gate_2026_09_08_r18/diagnostic_receipt.json"
SCRIPT = REPO / "scripts/research/build_equity_h01_h16_max_testing_verdict_2026_09_09.py"
TEST = REPO / "tests/research/test_equity_h01_h16_max_testing_verdict_2026_09_09.py"
OUT = REPO / "data/research_runs/equity_h01_h16_max_testing_verdict_2026_09_09"
RESULTS = OUT / "results.json"
REPORT = OUT / "report.md"
MANIFEST = OUT / "manifest.json"
RECEIPT = OUT / "receipt.json"
PENDING = "PENDING_FREEZE"
EXPECTED_IDS = [f"H{i:02d}" for i in range(1, 17)]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical_hash(payload: dict[str, Any], field: str) -> str:
    value = dict(payload)
    value[field] = PENDING
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def _evidence(protocol_row: dict[str, Any], local: dict[str, Any], h14c: dict[str, Any], h01_h05: dict[str, Any], candidate_id: str) -> dict[str, Any]:
    if candidate_id in {"H01", "H02", "H03", "H04", "H05"}:
        summary = local["h01_h05_existing_summary"]
        return {"receipt_rows": summary["row_count"], "decision_rows": summary["decision_count"], "feature_coverage": summary["feature_coverage"], "feature_gate_status": summary["feature_gate_status"], "receipt_status": h01_h05["status"]}
    if candidate_id == "H06":
        return {"candidate_rows": local["h06"]["candidate_rows"], "price_join_rows": local["h06"]["63_session_price_join_rows"], "full_recent_holdout": [local["h06"]["returns_by_window"][name]["count"] for name in ("full", "recent", "true_holdout")], "bootstrap": local["h06"]["bootstrap"], "placebo": local["h06"]["fixed_plus_7_day_calendar_placebo"]}
    if candidate_id == "H10":
        return {"exact_pairs": local["h10"]["exact_pairs"], "price_join_rows": local["h10"]["price_join_rows"], "returns": local["h10"]["returns"], "bootstrap": local["h10"]["bootstrap"]}
    if candidate_id == "H13":
        return {"route_status": local["h13"]["status"], "corporate_action_rows": local["corporate_action_scan"]["rows"], "raw_adjusted_invariant_checks": local["corporate_action_scan"]["raw_adjusted_invariant_checks"]}
    if candidate_id == "H14":
        return {"h14a_status": local["h14b"]["base_h14_status"], "h14a_cells": local["h14b"]["base_h14_cells"], "h14b_active_days": local["h14b"]["spare_slot_dates"], "h14b_active_months": local["h14b"]["spare_slot_event_months"], "h14b_cost_metrics": local["h14b"]["joint_nav_cost_metrics"]}
    if candidate_id == "H15":
        return local["h15"]
    if candidate_id == "H14C":
        return h14c
    return {"protocol_status": protocol_row["current_authoritative_status"], "standalone_status": protocol_row["standalone_status"], "terminal_blocker": protocol_row["terminal_blocker"]}


def build() -> dict[str, Any]:
    protocol = json.loads(PROTOCOL.read_text(encoding="utf-8"))
    local = json.loads(LOCAL.read_text(encoding="utf-8"))
    h14c = json.loads(H14C.read_text(encoding="utf-8"))
    h01_h05 = json.loads(H01_H05.read_text(encoding="utf-8"))
    protocol_rows = {row["candidate_id"]: row for row in protocol["rows"]}
    categories = {"H01": "EXACT_EXTERNAL_BLOCKER", "H02": "EXACT_EXTERNAL_BLOCKER", "H03": "SCHEMA_ONLY_NO_ECONOMIC_RUN", "H04": "EXACT_EXTERNAL_BLOCKER", "H05": "EXACT_EXTERNAL_BLOCKER", "H06": "RETROSPECTIVE_EVENT_DIAGNOSTIC_NOT_CANDIDATE", "H07": "EXACT_EXTERNAL_BLOCKER", "H08": "RETROSPECTIVE_METADATA_EVENT_DIAGNOSTIC_NOT_CANDIDATE", "H09": "EXACT_EXTERNAL_BLOCKER", "H10": "RETROSPECTIVE_METADATA_EVENT_DIAGNOSTIC_NOT_CANDIDATE", "H11": "EXACT_EXTERNAL_BLOCKER", "H12": "EXACT_EXTERNAL_BLOCKER", "H13": "EXACT_EXTERNAL_BLOCKER", "H14": "TESTED_INFEASIBLE_DIAGNOSTIC", "H15": "RETROSPECTIVE_LIFECYCLE_DIAGNOSTIC_NOT_CANDIDATE", "H16": "EXACT_EXTERNAL_BLOCKER"}
    rows = []
    for candidate_id in EXPECTED_IDS:
        source = protocol_rows[candidate_id]
        rows.append({"candidate_id": candidate_id, "category": categories[candidate_id], "definition_status": source["definition_status"], "measured_economics": False, "economic_status": "NOT_RUN", "economic_admitted": False, "integration_candidate": False, "authoritative_gate_status": source["current_authoritative_status"], "research_status": source["standalone_status"], "terminal_blocker": source["terminal_blocker"], "evidence": _evidence(source, local, h14c, h01_h05, candidate_id)})
    rows.append({"candidate_id": "H14C", "category": "POST_HOC_EXPLORATORY_DIAGNOSTIC_NOT_CANDIDATE", "definition_status": "FROZEN_AMENDMENT_POST_H14B", "measured_economics": False, "economic_status": "NOT_RUN", "economic_admitted": False, "integration_candidate": False, "authoritative_gate_status": h14c["status"], "research_status": h14c["status"], "terminal_blocker": "post-hoc definition and zero active true-holdout days; no independent holdout", "evidence": {"active_capacity_days": h14c["active_capacity_days"], "active_capacity_months": h14c["active_capacity_months"], "true_holdout_active_days": h14c["true_holdout_active_days"], "v3_weights_preserved": h14c["v3_weights_preserved"]}})
    results = {"schema": "equity-h01-h16-max-testing-verdict-v3", "status": "EQUITY_MAX_TESTING_COMPLETE_V3", "candidate_count": 17, "primary_candidate_count": 16, "candidate_ids": [*EXPECTED_IDS, "H14C"], "economic_run": False, "integration_candidate_count": 0, "promotion": False, "no_ohlcv_substitution": True, "no_production_paper_live": True, "rows": rows, "interpretation": "H14a/H14b/H14c remain diagnostic or infeasible; H06/H08/H10/H15 event/lifecycle observations are retrospective diagnostics and are not candidate-strategy evidence. Other rows have explicit data, PIT, schema, or access blockers; no row is disproven or admitted."}
    result_bytes = (json.dumps(results, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode()
    report_lines = ["# Equity H01–H16 maximum local/public testing verdict — 2026-09-09", "", "Status: `EQUITY_MAX_TESTING_COMPLETE_V3`. This is a research-only exhaustion record. No economic admission, integration, promotion, PAPER, LIVE, production, or configuration action occurred.", "", "| ID | Category | Status | Terminal blocker |", "|---|---|---|---|"]
    for row in rows:
        report_lines.append(f"| {row['candidate_id']} | {row['category']} | {row['research_status']} | {row['terminal_blocker']} |")
    report_lines.extend(["", results["interpretation"], "", f"H14b measured {local['h14b']['spare_slot_dates']} active capacity days over {local['h14b']['spare_slot_event_months']} months; H14c preserves V3, adds at most five percent only on those capacity days, and has zero active true-holdout days. H14c is post-hoc exploratory and does not pass complementarity.", ""])
    report_bytes = "\n".join(report_lines).encode()
    manifest = {"schema": "equity-h01-h16-max-testing-manifest-v1", "status": results["status"], "result": {"path": str(RESULTS.relative_to(REPO)), "sha256": hashlib.sha256(result_bytes).hexdigest()}, "report": {"path": str(REPORT.relative_to(REPO)), "sha256": hashlib.sha256(report_bytes).hexdigest()}, "script": {"path": str(SCRIPT.relative_to(REPO)), "sha256": sha256(SCRIPT)}, "test": {"path": str(TEST.relative_to(REPO)), "sha256": sha256(TEST)}, "inputs": {str(path.relative_to(REPO)): {"sha256": sha256(path), "bytes": path.stat().st_size} for path in (PROTOCOL, LOCAL, H14C, H01_H05)}, "candidate_count": 17, "candidate_ids": [*EXPECTED_IDS, "H14C"], "economic_run": False, "integration_candidate_count": 0, "manifest_sha256": PENDING}
    manifest["manifest_sha256"] = canonical_hash(manifest, "manifest_sha256")
    manifest_bytes = (json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode()
    receipt = {"schema": "equity-h01-h16-max-testing-receipt-v1", "status": results["status"], "manifest": {"path": str(MANIFEST.relative_to(REPO)), "sha256": hashlib.sha256(manifest_bytes).hexdigest(), "canonical_sha256": manifest["manifest_sha256"]}, "result": {"path": str(RESULTS.relative_to(REPO)), "sha256": hashlib.sha256(result_bytes).hexdigest()}, "report": {"path": str(REPORT.relative_to(REPO)), "sha256": hashlib.sha256(report_bytes).hexdigest()}, "candidate_count": 17, "economic_run": False, "integration_candidate_count": 0, "receipt_sha256": PENDING}
    receipt["receipt_sha256"] = canonical_hash(receipt, "receipt_sha256")
    receipt_bytes = (json.dumps(receipt, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode()
    _atomic({RESULTS: result_bytes, REPORT: report_bytes, MANIFEST: manifest_bytes, RECEIPT: receipt_bytes})
    verify()
    return {"status": results["status"], "candidate_count": 17, "manifest_sha256": manifest["manifest_sha256"], "receipt_sha256": receipt["receipt_sha256"]}


def _atomic(files: dict[Path, bytes]) -> None:
    staged = []
    try:
        for path, data in files.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
            temporary.write_bytes(data)
            staged.append((temporary, path))
        for temporary, path in staged:
            os.replace(temporary, path)
    finally:
        for temporary, _ in staged:
            temporary.unlink(missing_ok=True)


def verify() -> dict[str, Any]:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    if manifest.get("status") != "EQUITY_MAX_TESTING_COMPLETE_V3" or manifest.get("candidate_count") != 17 or manifest.get("candidate_ids") != [*EXPECTED_IDS, "H14C"] or manifest.get("economic_run") is not False or manifest.get("integration_candidate_count") != 0:
        raise ValueError("max_testing_manifest_policy_invalid")
    if manifest.get("manifest_sha256") != canonical_hash(manifest, "manifest_sha256"):
        raise ValueError("max_testing_manifest_self_hash_mismatch")
    for key in ("result", "report", "script", "test"):
        declaration = manifest[key]
        path = REPO / declaration["path"]
        if not path.is_file() or sha256(path) != declaration["sha256"]:
            raise ValueError(f"max_testing_hash_mismatch:{declaration['path']}")
    for relative, declaration in manifest["inputs"].items():
        path = REPO / relative
        if not path.is_file() or sha256(path) != declaration["sha256"]:
            raise ValueError(f"max_testing_input_hash_mismatch:{relative}")
    results = json.loads(RESULTS.read_text(encoding="utf-8"))
    if results.get("candidate_count") != 17 or len(results.get("rows", [])) != 17 or results.get("economic_run") is not False or results.get("integration_candidate_count") != 0 or any(row.get("economic_admitted") is not False or row.get("integration_candidate") is not False for row in results["rows"]):
        raise ValueError("max_testing_results_policy_invalid")
    receipt = json.loads(RECEIPT.read_text(encoding="utf-8"))
    if receipt.get("receipt_sha256") != canonical_hash(receipt, "receipt_sha256") or receipt.get("manifest", {}).get("canonical_sha256") != manifest["manifest_sha256"] or receipt.get("result", {}).get("sha256") != manifest["result"]["sha256"] or receipt.get("report", {}).get("sha256") != manifest["report"]["sha256"]:
        raise ValueError("max_testing_receipt_binding_invalid")
    return {"status": "EQUITY_MAX_TESTING_VERIFIED", "candidate_count": 17, "integration_candidate_count": 0}


if __name__ == "__main__":
    print(json.dumps(build(), sort_keys=True))
