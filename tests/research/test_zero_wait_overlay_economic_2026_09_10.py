"""Read-only receipt tests for the frozen zero-wait overlay execution."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "data/research_runs/zero_wait_overlays_2026_09_10"
RESULTS = OUT / "results.json"
RECEIPT = OUT / "receipt.json"
IDS = {"ZWO_SMA126_BINARY", "ZWO_SMA200_BINARY", "ZWO_DRAWDOWN10_BINARY", "ZWO_VOL15_TARGET20", "ZWO_SMA200_OR_DRAWDOWN10"}
WINDOWS = {"full", "recent", "single_use_true_holdout"}
COSTS = {1.0, 2.0, 3.0}


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_completed_matrix_is_frozen_and_network_disabled() -> None:
    payload = json.loads(RESULTS.read_text(encoding="utf-8"))
    assert payload["status"] == "ECONOMIC_EXECUTION_COMPLETE"
    assert payload["research_only"] is True
    assert payload["network"] == "disabled"
    assert payload["no_promotion"] is True
    keys = {(row["candidate_id"], row["window"], float(row["cost_multiplier"])) for row in payload["cases"]}
    assert keys == {(candidate, window, cost) for candidate in IDS for window in WINDOWS for cost in COSTS}
    assert set(payload["primary_true_holdout_2x"]) == IDS
    assert payload["holm_20"]["family_size"] == 20


def test_artifact_hashes_and_cap_audit_are_receipted() -> None:
    result = json.loads(RESULTS.read_text(encoding="utf-8"))
    receipt = json.loads(RECEIPT.read_text(encoding="utf-8"))
    assert receipt["result_sha256"] == _sha(RESULTS)
    assert receipt["daily_nav_sha256"] == _sha(OUT / "daily_nav.csv")
    assert receipt["exposure_ledger_sha256"] == _sha(OUT / "exposure_ledger.jsonl")
    assert receipt["report_sha256"] == _sha(ROOT / "docs/research/zero_wait_overlay_results_2026_09_10.md")
    caps = result["baseline_combined_cap_audit"]
    assert caps == {"cash_min": 0.4, "crypto_max": 0.05, "equity_max": 0.55, "gross_max": 0.6, "positions_max": 6, "status": "PASS_STATIC_MONOTONIC"}
    assert all(row["max_exposure"] <= 0.55 + 1e-9 and row["max_holdings"] <= 6 for row in result["cases"])
