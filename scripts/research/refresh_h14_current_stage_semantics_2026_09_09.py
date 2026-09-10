"""Refresh only the H14 semantics in the current H01-H05/H14 stage record."""

from __future__ import annotations

import hashlib
import json
import os
from copy import deepcopy
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
STAGE = REPO / "data/research_runs/equity_h01_h05_h14_stage_eligibility_2026_09_09_r18/stage_eligibility_record.json"
V3_ARTIFACT = REPO / "data/research_runs/h14_v3_daily_nav_comparability_2026_09_09/current_v3_daily_nav_comparability.json"
V3_REPORT = REPO / "docs/research/h14_v3_daily_nav_comparability_2026_09_09.md"
V3_RECEIPT = REPO / "data/research_runs/h14_v3_daily_nav_comparability_2026_09_09/focused_test_collection_receipt.json"
V3_MANIFEST = REPO / "data/research_manifests/h14_turn_of_month_protocol_2026_09_06_r4.json"
STATUS = "H14_PORTFOLIO_CONTRIBUTION_TESTED_BUT_INFEASIBLE_REMAINS_DIAGNOSTIC"
STALE_H14_TEXT = (
    "PORTFOLIO_CONTRIBUTION_" + "UNTESTED",
    "no reusable current " + "V3",
    "NOT_" + "COMPARABLE",
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical_hash(value: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def file_pin(path: Path) -> dict[str, Any]:
    return {"path": str(path.relative_to(REPO)), "bytes": path.stat().st_size, "sha256": sha256(path)}


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"json_object_required:{path}")
    return value


def validate_v3(artifact: dict[str, Any]) -> None:
    if artifact.get("status") != STATUS or artifact.get("economic_claim") is not False or artifact.get("promotion") is not False:
        raise ValueError("h14_v3_status_or_policy_invalid")
    if artifact.get("parity", {}).get("status") != "PASS":
        raise ValueError("h14_v3_parity_not_pass")
    if artifact.get("counts", {}).get("daily_nav_rows") != 2658:
        raise ValueError("h14_v3_daily_nav_count_invalid")
    cells = artifact.get("evaluation", {}).get("cells")
    if not isinstance(cells, list) or len(cells) != 18 or any(cell.get("v3_h14_daily_return_correlation") is None for cell in cells):
        raise ValueError("h14_v3_cell_or_correlation_count_invalid")
    controls = artifact.get("evaluation", {}).get("controls", {})
    if len(controls) != 18 or any("fixed 95% V3 + 5% H14" not in str(value) for value in controls.values()):
        raise ValueError("h14_v3_fixed_sleeve_invalid")
    if any(cell.get("shared_capital", {}).get("rejected_days", 0) <= 0 for cell in cells):
        raise ValueError("h14_v3_shared_cap_violation_missing")


def _h14_entry(artifact: dict[str, Any]) -> dict[str, Any]:
    cells = artifact["evaluation"]["cells"]
    rejected = {
        f"{cell['cell']}/{cell['mode']}/{cell['cost_multiplier']}x": cell["shared_capital"]["rejected_days"]
        for cell in cells
    }
    return {
        "blockers": [
            "frozen shared total-positions cap is violated on active H14 days; no feasible integration candidate",
            "the frozen H14 matrix specifies full/recent cells and cost stress but no walk-forward or holdout cells",
            "descriptive joint NAV, drawdown, tail and regime metrics do not establish economic eligibility or promotion",
        ],
        "correlation_rows": len(cells),
        "current_v3_daily_nav_rows": artifact["counts"]["daily_nav_rows"],
        "current_v3_parity": artifact["parity"]["status"],
        "economic_eligible": False,
        "eligible": False,
        "field": "SPY-only fixed turn-of-month P0 close decision; L0 open entry; F3 close decision; F4 open exit",
        "fixed_sleeve": "fixed 95% V3 + 5% H14 normalized sleeve",
        "fixed_weighting": "95% V3 + 5% H14",
        "integration_candidate": False,
        "matrix": "18 cells = candidate/passive/calendar_control x 2 windows x 1x/2x/3x",
        "portfolio_contribution_run": True,
        "portfolio_contribution_scope": "diagnostic only; no economic eligibility or promotion",
        "promotion": False,
        "shared_position_cap": 6,
        "shared_position_cap_violations": rejected,
        "status": STATUS,
        "window": "full_2022 and recent_2024",
    }


def build_stage(current: dict[str, Any], artifact: dict[str, Any]) -> dict[str, Any]:
    updated = deepcopy(current)
    updated["decision"] = "NO_CANDIDATE_ELIGIBLE_FOR_ECONOMIC_OR_INTEGRATION_PROMOTION"
    updated["frozen_contracts"]["H14"] = _h14_entry(artifact)
    evidence = updated["evidence"]
    evidence.update(
        {
            "h14_v3_artifact": str(V3_ARTIFACT.relative_to(REPO)),
            "h14_v3_artifact_sha256": sha256(V3_ARTIFACT),
            "h14_v3_artifact_canonical_sha256": artifact["artifact_sha256"],
            "h14_v3_report": str(V3_REPORT.relative_to(REPO)),
            "h14_v3_report_sha256": sha256(V3_REPORT),
            "h14_v3_focused_receipt": str(V3_RECEIPT.relative_to(REPO)),
            "h14_v3_focused_receipt_sha256": sha256(V3_RECEIPT),
            "h14_v3_execution_manifest": str(V3_MANIFEST.relative_to(REPO)),
            "h14_v3_execution_manifest_sha256": sha256(V3_MANIFEST),
            "h14_v3_status": STATUS,
        }
    )
    updated["validation"]["h14_current_v3"] = {
        "artifact": str(V3_ARTIFACT.relative_to(REPO)),
        "artifact_sha256": sha256(V3_ARTIFACT),
        "cell_count": 18,
        "correlation_rows": 18,
        "daily_nav_rows": artifact["counts"]["daily_nav_rows"],
        "economic_run": False,
        "exit_code": 0,
        "parity": "PASS",
        "portfolio_contribution_run": True,
        "result": "current V3 parity PASS; diagnostic H14 joint run is infeasible under shared position cap",
        "status": STATUS,
    }
    h14_action = "H14 current-V3 parity and the 18-cell diagnostic are complete; keep integration_candidate=false because shared position-cap violations make the frozen joint ledger infeasible."
    updated["next_actions"] = list(dict.fromkeys([*updated["next_actions"], h14_action]))
    updated["record_sha256"] = canonical_hash({key: value for key, value in updated.items() if key != "record_sha256"})
    return updated


def validate_stage(path: Path = STAGE) -> dict[str, Any]:
    stage = load_json(path)
    expected = canonical_hash({key: value for key, value in stage.items() if key != "record_sha256"})
    if stage.get("record_sha256") != expected:
        raise ValueError("stage_record_self_hash_mismatch")
    h14 = stage.get("frozen_contracts", {}).get("H14", {})
    if h14.get("status") != STATUS or h14.get("current_v3_parity") != "PASS" or h14.get("current_v3_daily_nav_rows") != 2658 or h14.get("correlation_rows") != 18 or h14.get("fixed_sleeve") != "fixed 95% V3 + 5% H14 normalized sleeve" or h14.get("portfolio_contribution_run") is not True or h14.get("economic_eligible") is not False or h14.get("integration_candidate") is not False or h14.get("promotion") is not False:
        raise ValueError("stage_h14_semantics_invalid")
    text = json.dumps(h14, ensure_ascii=False)
    if any(marker in text for marker in STALE_H14_TEXT):
        raise ValueError("stage_h14_stale_semantics")
    for key, path_value in {
        "h14_v3_artifact": V3_ARTIFACT,
        "h14_v3_report": V3_REPORT,
        "h14_v3_focused_receipt": V3_RECEIPT,
        "h14_v3_execution_manifest": V3_MANIFEST,
    }.items():
        if stage["evidence"][key] != str(path_value.relative_to(REPO)) or stage["evidence"][f"{key}_sha256"] != sha256(path_value):
            raise ValueError(f"stage_h14_dependency_mismatch:{key}")
    return stage


def refresh(path: Path = STAGE) -> dict[str, Any]:
    current = load_json(path)
    artifact = load_json(V3_ARTIFACT)
    validate_v3(artifact)
    updated = build_stage(current, artifact)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(json.dumps(updated, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        validate_stage(temporary)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return validate_stage(path)


if __name__ == "__main__":
    result = refresh()
    print(json.dumps({"status": "H14_CURRENT_STAGE_SEMANTICS_FIXED", "record_sha256": result["record_sha256"], "stage_sha256": sha256(STAGE)}, sort_keys=True))
