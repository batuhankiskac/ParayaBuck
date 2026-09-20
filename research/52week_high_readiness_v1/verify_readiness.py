#!/usr/bin/env python3
"""Independent evidence verifier; intentionally imports no implementation code."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def load(name: str) -> dict[str, object]:
    return json.loads((ROOT / name).read_text(encoding="utf-8"))


def main() -> int:
    design = load("DESIGN_INPUT_RECEIPT.json")
    data = load("CANONICAL_DATA_RECEIPT.json")
    actions = load("CORPORATE_ACTION_RECEIPT.json")
    lifetimes = load("LIFECYCLE_RECEIPT.json")
    costs = load("COST_MULTIPLIER_RECEIPT.json")
    parity = load("V3_CONTROL_PARITY.json")
    structural = load("STRUCTURAL_SIGNAL_RANK_AUDIT.json")

    checks: list[tuple[str, Callable[[], bool]]] = [
        ("corrected_main", lambda: design["corrected_main_sha"] == "839e92cf90e23cf83ce3cd2eb1b3ce34fefb97de"),
        ("origin_main", lambda: design["corrected_origin_main_sha"] == design["corrected_main_sha"]),
        ("design_sha", lambda: design["design_sha"] == "72b51a0e5c03f2ce0a8790b197cf360b26b33cd3"),
        ("design_hashes", lambda: bool(design["hashes_match"])),
        ("paper_v3", lambda: design["paper_strategy"] == "equity_residual_momentum_v3"),
        ("live_disabled", lambda: design["live_enabled"] is False),
        (
            "bars_hash",
            lambda: data["source_sha256"] == "bb7aaae86bc1909c6b0a1094a59430f5ed9900bb3ec63891bbdcda8cda055139",
        ),
        ("bar_rows", lambda: data["source_rows"] == data["loaded_rows"] == 11_686_832),
        ("entities", lambda: data["entity_count"] == 9_010),
        ("raw_timeframes", lambda: data["rows_by_original_timeframe"] == {"1Day": 7_771_468, "1d": 3_915_364}),
        ("normalized_timeframe", lambda: data["rows_by_normalized_timeframe"] == {"1d": 11_686_832}),
        ("no_rejected_rows", lambda: data["rejected_rows"] == 0),
        ("source_immutable", lambda: data["source_bytes_mutated"] is False),
        (
            "actions_hash",
            lambda: actions["source_sha256"] == "a0a60755a3f6adfc06a5ef0e6ed82852e1584c20fdb62cefed92b765dc9adc23",
        ),
        ("action_rows", lambda: actions["raw_action_count"] == 143_446),
        ("cash_rows", lambda: actions["eligible_cash_dividend_rows"] == 141_908),
        ("dividend_duplicates", lambda: actions["duplicate_economic_events"] == 157),
        ("applied_dividends", lambda: actions["final_applied_event_count"] == 141_751),
        ("no_non_cash_application", lambda: actions["non_cash_actions_applied"] == 0),
        (
            "lifetimes_hash",
            lambda: lifetimes["source_sha256"] == "ed675d9b6ba7f173b65ae2b5272ffb62e09d6a007ed36ea22d3aba7a36774596",
        ),
        ("lifetime_rows", lambda: lifetimes["row_count"] == 9_010),
        ("terminal_count", lambda: lifetimes["explicit_terminal_count"] == 2_151),
        ("horizon_count", lambda: lifetimes["horizon_censored_count"] == 6_858),
        ("ambiguous_explicit", lambda: lifetimes["unresolved_count"] == 1 and len(lifetimes["unresolved"]) == 1),
        ("terminal_boundary", lambda: bool(lifetimes["terminal_only_on_last_valid_bar"])),
        ("cost_scaling", lambda: bool(costs["passed"])),
        ("commission_zero", lambda: costs["commission"] == 0.0),
        (
            "dated_fee_math",
            lambda: all(
                abs(case["two_x"] - 2 * case["one_x"]) < 1e-12 and abs(case["three_x"] - 3 * case["one_x"]) < 1e-12
                for case in costs["effective_dated_boundary_cases"]
            ),
        ),
        ("v3_parity", lambda: parity["parity_passed"] is True),
        ("v3_metric_tolerance", lambda: parity["max_absolute_metric_difference"] <= 1e-8),
        ("v3_counts", lambda: parity["fresh_counts"] == parity["expected_counts"]),
        ("v3_target_hash", lambda: parity["hashes"]["fresh_targets"] == parity["hashes"]["expected_targets"]),
        ("v3_fill_hash", lambda: parity["hashes"]["fresh_fills"] == parity["hashes"]["expected_fills"]),
        (
            "v3_terminal_hash",
            lambda: parity["hashes"]["fresh_terminal_events"] == parity["hashes"]["expected_terminal_events"],
        ),
        ("candidate_identity", lambda: structural["candidate"] == "equity_52week_high_close_v1"),
        ("history_252", lambda: structural["history_count"] == 252),
        (
            "through_t",
            lambda: (
                structural["history_last_equals_t"] is True
                and structural["maximum_visible_timestamp"] == structural["decision_timestamp"]
            ),
        ),
        ("no_future", lambda: structural["future_data_visible"] is False),
        ("scanner_unchanged", lambda: structural["scanner_eligibility_unchanged"] is True),
        ("capacity", lambda: len(structural["observation_set"]) <= structural["observation_capacity"]),
        ("top_six", lambda: len(structural["selected"]) <= 6),
        ("reserved_exclusion", lambda: structural["reserved_targets"] == []),
        ("equal_weights", lambda: len({row[1] for row in structural["target_weights"]}) <= 1),
        ("single_cap", lambda: max(row[1] for row in structural["target_weights"]) <= 0.10),
        ("allocation_cap", lambda: sum(row[1] for row in structural["target_weights"]) <= 0.55 + 1e-12),
        ("month_end", lambda: all(case["decides"] is True for case in structural["month_end_cases"])),
        (
            "next_open",
            lambda: all(case["next_open"] > case["completed_session"] for case in structural["month_end_cases"]),
        ),
        ("not_registered", lambda: structural["production_registry_contains_candidate"] is False),
        ("no_candidate_economics_design", lambda: design["candidate_economics_inspected"] is False),
        ("no_candidate_economics_structural", lambda: structural["candidate_economics_inspected"] is False),
        ("no_candidate_economics_parity", lambda: parity["candidate_economics_inspected"] is False),
    ]
    failures: list[str] = []
    passed: list[str] = []
    for name, check in checks:
        try:
            (passed if check() else failures).append(name)
        except Exception:
            failures.append(name)
    payload = {
        "verifier_independence": "reads persisted JSON only; imports no candidate, scheduler, engine, or builder code",
        "check_count": len(checks),
        "passed_count": len(passed),
        "passed_checks": passed,
        "failures": failures,
        "passed": not failures,
        "candidate_economics_inspected": False,
    }
    (ROOT / "INDEPENDENT_VERIFICATION.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"check_count": len(checks), "failures": failures, "passed": not failures}))
    return int(bool(failures))


if __name__ == "__main__":
    raise SystemExit(main())
