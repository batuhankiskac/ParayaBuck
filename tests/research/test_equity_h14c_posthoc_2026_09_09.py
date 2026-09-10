from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from scripts.research import run_equity_h14c_posthoc_2026_09_09 as h14c
from scripts.research import run_equity_local_diagnostics_2026_09_09 as h14b


def test_h14c_is_posthoc_and_capacity_bounded() -> None:
    result = h14c.build()
    assert result["status"] == "POST_HOC_EXPLORATORY_DIAGNOSTIC_ONLY"
    payload = json.loads(h14c.RESULTS.read_text(encoding="utf-8"))
    assert payload["definition_timing"] == "POST_HOC_AFTER_H14B_RESULTS"
    assert payload["active_capacity_days"] == 31
    assert payload["active_capacity_months"] == 14
    assert payload["true_holdout_active_days"] == 0
    assert payload["v3_weights_preserved"] is True
    assert payload["inactive_v3_unchanged"] is True
    assert payload["economic_run"] is False
    assert payload["integration_candidate_count"] == 0
    assert len(payload["metrics"]) == 3
    full = payload["metrics"][0]["windows"]["full"]
    assert full["bootstrap"]["resamples"] == 10000
    assert full["permutation"]["resamples"] == 10000
    assert payload["placebo_shift_trading_days"] == 7
    assert payload["placebo_status"] == "POST_REVIEW_DIAGNOSTIC_SHIFTED_7_TRADING_DAYS"
    assert full["placebo_plus_7_calendar"]["status"] == "POST_REVIEW_DIAGNOSTIC_SHIFTED_7_TRADING_DAYS"
    assert full["placebo_plus_7_calendar"]["candidate_observation_days"] == 37
    assert "cagr" in full["delta_overlay_minus_v3"]
    assert payload["metrics"][0]["turnover_cost_status"] == "NOT_RUN_NO_TARGET_TURNOVER_LEDGER"
    assert h14c.verify()["status"] == "H14C_POSTHOC_VERIFIED"


def test_native_five_percent_sleeve_is_not_scaled_twice() -> None:
    """The native candidate cell already contains 95% cash and 5% SPY."""
    current = json.loads(h14c.H14_CURRENT.read_text(encoding="utf-8"))
    receipt = json.loads(h14c.H14_RECEIPT.read_text(encoding="utf-8"))
    v3_nav = {h14c.parse_date(row["timestamp"]): float(row["nav"]) for row in current["daily_nav"]}
    candidate = next(row for row in receipt["cells"] if row["cell"] == "full_2022" and row["mode"] == "candidate" and row["cost_multiplier"] == 1.0)
    assert {row["target_cash_weight"] for row in candidate["rebalances"]} == {0.95, 1.0}
    assert {row["weights"].get("SPY", 0.0) for row in candidate["marked_positions"]} == {0.0, 0.05}
    candidate_nav = {h14c.parse_date(row["timestamp"]): float(row["nav"]) for row in candidate["equity_curve"]}
    event_dates = {h14c.parse_date(event[key]) for event in candidate["event_trace"] for key in ("l0", "f1", "f2", "f3")}
    weight_history = sorted(current["weight_history"], key=lambda row: row["timestamp"])
    capacity = {}
    for when in event_dates:
        prior = [row for row in weight_history if h14c.parse_date(row["timestamp"]) < when]
        weights = prior[-1]["weights"] if prior else {}
        equity = sum(abs(float(value)) for value in weights.values())
        positions = sum(abs(float(value)) > 1e-12 for value in weights.values())
        gross = equity
        capacity[when] = equity + 0.05 <= 0.55 and gross + 0.05 <= 0.60 and 1.0 - equity - 0.05 >= 0.40 and positions + 1 <= 6
    dates = sorted(set(v3_nav) & set(candidate_nav))
    expected_h14b = []
    expected_h14c = []
    for previous, when in zip(dates[:-1], dates[1:], strict=True):
        v3_return = v3_nav[when] / v3_nav[previous] - 1.0
        candidate_return = candidate_nav[when] / candidate_nav[previous] - 1.0
        active = when in event_dates and capacity.get(when, False)
        expected_h14b.append((when, 0.95 * v3_return + candidate_return if active else 0.95 * v3_return))
        expected_h14c.append((when, v3_return + candidate_return if active else v3_return))
    def total(values: list[tuple[date, float]]) -> float:
        return __import__("math").prod(1.0 + value for when, value in values if date(2022, 1, 1) <= when <= date(2025, 12, 31)) - 1.0
    h14b_payload = json.loads(h14b.RESULTS.read_text(encoding="utf-8"))["h14b"]["joint_nav_cost_metrics"][0]["windows"]["full"]["overlay"]["total_return"]
    h14c_payload = json.loads(h14c.RESULTS.read_text(encoding="utf-8"))["metrics"][0]["windows"]["full"]["overlay"]["total_return"]
    assert h14b_payload == pytest.approx(total(expected_h14b))
    assert h14c_payload == pytest.approx(total(expected_h14c))


@pytest.mark.parametrize("target", [h14c.MANIFEST, h14c.RESULTS, h14c.RECEIPT])
def test_h14c_mutation_fails_closed(target: Path) -> None:
    original = target.read_bytes()
    try:
        payload = json.loads(original)
        if target == h14c.MANIFEST:
            payload["candidate_weight_max"] = 0.06
        elif target == h14c.RESULTS:
            payload["economic_run"] = True
        else:
            payload["receipt_sha256"] = "0" * 64
        target.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        with pytest.raises(ValueError):
            h14c.verify()
    finally:
        target.write_bytes(original)
