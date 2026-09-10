"""Integrity and provenance tests for the zero-wait overlay predeclaration.

These tests intentionally do not import or execute an economic runner.
"""

from __future__ import annotations

import csv
import hashlib
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "data/research_manifests/zero_wait_overlay_freeze_2026_09_10.json"


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _manifest() -> dict:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def _canonical_manifest_digest(payload: dict) -> str:
    copy = json.loads(json.dumps(payload))
    copy["integrity"].pop("manifest_sha256", None)
    encoded = json.dumps(copy, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def test_manifest_is_frozen_and_has_no_economic_outcomes() -> None:
    payload = _manifest()
    assert payload["status"] == "FROZEN_ZERO_WAIT_OVERLAY_PROTOCOL"
    assert payload["declared_before_outcomes"] is True
    assert payload["research_only"] is True
    assert payload["economic_run"] is False
    assert payload["economic_outcomes_accessed"] is False
    assert payload["integrity"]["outcome_artifacts_present"] is False
    assert payload["integrity"]["manifest_sha256"] != "TO_BE_REPLACED"
    assert payload["integrity"]["manifest_sha256"] == _canonical_manifest_digest(payload)
    assert len(payload["candidates"]) == payload["candidate_count"] == 5
    forbidden = {"results", "outcomes", "economic_results", "performance_results"}
    def nested_keys(value: object) -> set[str]:
        if isinstance(value, dict):
            return set(value) | {key for child in value.values() for key in nested_keys(child)}
        if isinstance(value, list):
            return {key for child in value for key in nested_keys(child)}
        return set()
    assert forbidden.isdisjoint(nested_keys(payload))


def test_candidate_formulas_and_caps_are_fully_predeclared() -> None:
    payload = _manifest()
    candidates = payload["candidates"]
    assert len({item["id"] for item in candidates}) == 5
    assert all(item["scale_domain"] == [0.0, 1.0] for item in candidates)
    assert all(item["warmup_sessions"] > 0 for item in candidates)
    by_id = {item["id"]: item for item in candidates}
    assert by_id["ZWO_SMA126_BINARY"]["formula"] == "g_d = 1 if C_t > mean(C[t-125:t+1]) else 0"
    assert by_id["ZWO_SMA200_BINARY"]["formula"] == "g_d = 1 if C_t > mean(C[t-199:t+1]) else 0"
    assert by_id["ZWO_DRAWDOWN10_BINARY"]["formula"] == "g_d = 0 if C_t / max(C[t-251:t+1]) - 1 <= -0.10 else 1"
    assert by_id["ZWO_VOL15_TARGET20"]["formula"] == "g_d = clip(0.15 / (stdev_sample(r[t-19:t+1]) * sqrt(252)), 0, 1)"
    assert by_id["ZWO_SMA200_OR_DRAWDOWN10"]["formula"] == (
        "g_d = 1 if (C_t > mean(C[t-199:t+1])) and (C_t / max(C[t-251:t+1]) - 1 > -0.10) else 0"
    )
    assert payload["signal_alignment"]["lookahead_policy"] == "strictly_prior_completed_bar"
    assert payload["signal_alignment"]["missing_data_fail_safe"] == (
        "g_d = 0.0 (cash for the managed equity sleeve), with reason recorded; never carry a future value or silently preserve risk."
    )
    caps = payload["caps"]
    assert caps["equity_gross_max"] == 0.55
    assert caps["combined_gross_max"] == 0.60
    assert caps["combined_cash_min"] == 0.40
    assert caps["total_positions_max"] == 6
    assert caps["long_only"] is True
    assert caps["leverage_allowed"] is False
    assert caps["new_symbols_allowed"] is False
    assert "do not truncate" in caps["position_policy"]
    proof = caps["monotonic_cap_proof"]
    assert "0 <= g_d <= 1" in proof["premise"]
    assert "0 <= w_overlay[d,s] <= w_v3[d,s]" in proof["equity_weight"]
    assert "copied unchanged" in proof["crypto_and_other_sleeves"]
    assert proof["baseline_evidence"]["combined_runtime_audit"] == "required_before_outcomes"
    assert proof["baseline_evidence"]["missing_evidence_policy"] == "FAIL_CLOSED"


def test_session_dates_are_explicit_and_end_is_exclusive() -> None:
    payload = _manifest()
    calendar = payload["evaluation_protocol"]["session_calendar"]
    assert calendar["evaluation_end_exclusive_session_date"] == "2026-07-30"
    assert calendar["spy_last_session_date"] == "2026-07-30"
    assert calendar["v3_last_dated_session_date"] == "2026-07-29"
    assert calendar["common_last_session_date"] == "2026-07-29"
    for window in payload["evaluation_protocol"]["windows"].values():
        assert "start_session_date" in window
        assert window["end_session_date_exclusive"] == "2026-07-30"


def test_spy_coverage_and_source_hash_are_pinned() -> None:
    payload = _manifest()
    source = ROOT / payload["data_contract"]["spy_daily_bars"]["path"]
    assert _sha(source) == payload["data_contract"]["spy_daily_bars"]["sha256"]
    first = last = None
    count = 0
    with source.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            if row["symbol"] != "SPY" or row["timeframe"] != "1d" or row["market"] != "equity":
                continue
            count += 1
            first = first or row["timestamp"]
            last = row["timestamp"]
    contract = payload["data_contract"]["spy_daily_bars"]
    assert count == contract["rows_observed"] == 2658
    assert first == contract["first_timestamp"]
    assert last == contract["last_timestamp"]
    assert contract["price_basis"] == "split_adjusted_daily_ohlcv"
    assert contract["dividend_signal_policy"] == (
        "SPY signal uses split-adjusted close only; cash dividends are not added to the signal series"
    )


def test_v3_receipt_and_source_pins_match_current_files() -> None:
    payload = _manifest()
    v3 = payload["v3_control"]
    receipt_path = ROOT / v3["receipt"]["path"]
    assert receipt_path.name == "control_r3.json"
    assert _sha(receipt_path) == v3["receipt"]["sha256"]
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert receipt["strategy"]["name"] == v3["strategy_name"]
    assert receipt["strategy"]["version"] == v3["strategy_version"]
    for key, expected in v3["component_hashes"].items():
        assert receipt["components"][key] == expected
    assert receipt["policy"]["allocation"] == v3["allocation"]
    assert receipt["counts"]["fills"] == v3["receipt"]["counts"]["fills"]
    assert receipt["counts"]["rebalance_decisions"] == v3["receipt"]["counts"]["rebalance_decisions"]
    assert receipt["counts"]["terminal_events"] == v3["receipt"]["counts"]["terminal_events"]
    assert receipt["config_digest_sha256"] == v3["scanner"]["config_digest_sha256"]
    assert receipt["policy"]["evaluation_end"][:10] == payload["evaluation_protocol"]["session_calendar"]["evaluation_end_exclusive_session_date"]
    baseline = payload["caps"]["monotonic_cap_proof"]["baseline_evidence"]
    assert receipt["metrics"]["max_exposure"] == baseline["observed_max_equity_exposure"]
    assert receipt["metrics"]["max_holdings"] == baseline["observed_max_equity_positions"]
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    assert head == payload["source_provenance"]["base_commit"]
    for relative, expected in payload["source_provenance"]["source_files"].items():
        assert _sha(ROOT / relative) == expected


def test_protocol_has_fixed_robustness_and_cost_cells_without_running_them() -> None:
    payload = _manifest()
    evaluation = payload["evaluation_protocol"]
    costs = payload["reweighting_cost_model"]
    assert costs["target_notional"] == "target_notional[d,s] = NAV_at_decision[d] * g_d * w_v3[d,s]."
    assert costs["marked_overlay_notional"] == (
        "marked_overlay_notional[d,s] = the quantity of the already-held overlay position immediately before d multiplied by the eligible execution reference price at d."
    )
    assert costs["delta_notional"] == (
        "delta[d,s] = target_notional[d,s] - marked_overlay_notional[d,s]; this is the sole notional submitted as an overlay adjustment order after the scaled parent fill is accounted for."
    )
    assert "never book an unscaled parent fill" in costs["dated_fills"]
    assert costs["cash_return_policy"] == (
        "Residual cash earns 0.0 annualized return, compounded daily; no cash-rate observations, future value, or implicit risk asset substitution may be introduced."
    )
    assert "Before any economic run" in costs["runner_source_requirement"]
    assert costs["cost_stress"].startswith("For multiplier m in {1,2,3}")
    assert "commission fixed" in costs["cost_stress"]
    assert "effective-dated equity regulatory fee schedule unchanged" in costs["cost_stress"]
    assert "scaled-parent traded notional" in costs["turnover_definition"]
    assert payload["evaluation_protocol"]["metric_definitions"]["daily_CVaR_95"].startswith("Positive loss magnitude")
    assert "four inferential endpoints" in payload["evaluation_protocol"]["multiple_testing"]["family"]
    assert evaluation["cost_multipliers"] == [1.0, 2.0, 3.0]
    assert evaluation["block_bootstrap"] == {
        "method": "20-session circular block bootstrap on paired candidate-minus-V3 daily observations",
        "resamples": 10000,
        "seed": 20260910,
        "interval": "two-sided 95%",
    }
    assert evaluation["permutation_placebo"]["resamples"] == 10000
    assert evaluation["multiple_testing"] == {
        "method": "Holm step-down correction",
        "family": "five candidates multiplied by the four inferential endpoints max_drawdown, daily_CVaR_95, V3_down_day_loss_reduction, and annualized_return_opportunity_cost; turnover, realized cost, and capacity remain descriptive or hard feasibility gates",
        "alpha": 0.05,
    }
    assert costs["cost_stress"] == (
        "For multiplier m in {1,2,3}, multiply only the configured slippage_bps and spread_bps by m. Keep commission fixed and apply the effective-dated equity regulatory fee schedule unchanged at each fill date."
    )


def test_v3_reproduction_command_and_pins_are_complete() -> None:
    payload = _manifest()
    reproduction = payload["source_provenance"]["v3_reproduction"]
    assert reproduction["runner_path"] == "scripts/research_v3_current_parity_control.py"
    assert reproduction["runner_sha256"] == payload["source_provenance"]["source_files"][reproduction["runner_path"]]
    assert "--output" in reproduction["command"]
    assert reproduction["command"].endswith("control_r3.json")
    receipt_path = ROOT / payload["v3_control"]["receipt"]["path"]
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert receipt["receipt_sha256"] == reproduction["expected_receipt_sha256"]
    assert _sha(receipt_path) == reproduction["expected_physical_receipt_sha256"]
    assert receipt["source_commit"] == reproduction["expected_source_commit"] == payload["source_provenance"]["base_commit"]
    assert receipt["dataset"] == reproduction["expected_dataset_hashes"]
    assert reproduction["expected_counts"] == {"rebalance_decisions": 495, "fills": 1890, "terminal_events": 12}
    assert reproduction["expected_components"] == payload["v3_control"]["component_hashes"]
    assert reproduction["environment"]["network"] == "disabled"
    assert "Any command" in reproduction["mismatch_policy"]
