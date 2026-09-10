from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from scripts.research import run_equity_local_diagnostics_2026_09_09 as diagnostics


def test_forward_return_requires_full_future_window() -> None:
    rows = [{"date": date(2022, 1, index), "open": 10.0, "close": 11.0} for index in range(1, 4)]
    assert diagnostics.forward_return(rows, date(2021, 12, 31), 3) == (rows[0]["date"], pytest.approx(0.1))
    assert diagnostics.forward_return(rows, date(2022, 1, 1), 3) is None


def test_build_has_exact_candidates_and_no_economic_admission() -> None:
    result = diagnostics.build()
    assert result["status"] == "LOCAL_DIAGNOSTICS_COMPLETE_NO_ECONOMIC_ADMISSION"
    payload = json.loads(diagnostics.RESULTS.read_text(encoding="utf-8"))
    assert list(payload["candidates"]) == diagnostics.EXPECTED_IDS
    assert payload["economic_run"] is False
    assert payload["integration_candidate_count"] == 0
    assert payload["h06"]["candidate_rows"] == 274
    assert payload["h06"]["63_session_price_join_rows"] == 242
    assert payload["h06"]["bootstrap"]["resamples"] == 10000
    assert payload["h06"]["permutation"]["status"] == "NOT_RUN_NO_FULL_ISSUER_LABEL_PANEL"
    assert payload["h10"]["exact_pairs"] == 3
    assert payload["h13"]["status"] == "BLOCKED_AUTHORIZED_INTRADAY_ENTITLEMENT"
    assert len(payload["h13"]["route_evidence"]) == 3
    assert payload["h14b"]["base_h14_result_preserved"] is True
    assert payload["h14b"]["spare_slot_dates"] == 31
    assert len(payload["h14b"]["joint_nav_cost_metrics"]) == 3
    metric = payload["h14b"]["joint_nav_cost_metrics"][0]
    full = metric["windows"]["full"]
    assert {"total_return", "cagr", "sharpe", "sortino", "max_drawdown", "cvar_05"} <= set(full["delta_overlay_minus_v3"])
    assert full["contribution_bootstrap"]["resamples"] == 10000
    assert full["contribution_permutation"]["resamples"] == 10000
    placebo = full["placebo_plus_7_calendar"]
    assert placebo["status"] == "POST_REVIEW_DIAGNOSTIC_SHIFTED_7_TRADING_DAYS"
    assert placebo["candidate_observation_days"] == 37
    assert placebo["shifted_schedule_capacity_days"] == 37
    assert metric["turnover_cost_status"] == "NOT_RUN_NO_TARGET_TURNOVER_LEDGER"
    assert payload["corporate_action_scan"]["rows"] == 143446
    assert payload["bar_scan"]["raw_adjusted_columns_present"] == {"raw": False, "adjusted": False, "split_factor": False}
    assert diagnostics.verify()["status"] == "LOCAL_DIAGNOSTICS_VERIFIED"


@pytest.mark.parametrize("target", [diagnostics.MANIFEST, diagnostics.RESULTS, diagnostics.RECEIPT])
def test_mutation_fails_closed(target: Path) -> None:
    original = target.read_bytes()
    try:
        payload = json.loads(original)
        if target == diagnostics.MANIFEST:
            payload["candidate_count"] = 15
        elif target == diagnostics.RESULTS:
            payload["integration_candidate_count"] = 1
        else:
            payload["receipt_sha256"] = "0" * 64
        target.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        with pytest.raises(ValueError):
            diagnostics.verify()
    finally:
        target.write_bytes(original)
