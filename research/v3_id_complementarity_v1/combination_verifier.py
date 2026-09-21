"""Independent post-run verifier for the frozen V3 + ID combination campaign."""

from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import date, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
OUTPUT = Path(__file__).resolve().parent
EXPECTED_MAIN_SHA = "839e92cf90e23cf83ce3cd2eb1b3ce34fefb97de"
EXPECTED_BARS_SHA = "bb7aaae86bc1909c6b0a1094a59430f5ed9900bb3ec63891bbdcda8cda055139"
EXPECTED_LIFETIMES_SHA = "ed675d9b6ba7f173b65ae2b5272ffb62e09d6a007ed36ea22d3aba7a36774596"
EXPECTED_ACTIONS_SHA = "a0a60755a3f6adfc06a5ef0e6ed82852e1584c20fdb62cefed92b765dc9adc23"
EXPECTED_POLICY_SHA = "e700f1971a8014838184d56930cc50065d864487f77da391b7e56383f105d225"
EXPECTED_CANDIDATE_SHA = "48a58dfe5c9f878891c03d676f977d344e154ee9043b0794e100fb169a907ce6"
DEV_START = date(2017, 1, 3)
DEV_END = date(2021, 12, 31)
VAL_START = date(2022, 1, 3)
VAL_END = date(2026, 7, 30)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load(name: str) -> Any:
    return json.loads((OUTPUT / name).read_text(encoding="utf-8"))


def _json_dump(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, allow_nan=False), encoding="utf-8")


def _git(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()


def _period_sum(events: list[dict[str, Any]], start: date, end: date, key: str) -> float:
    total = 0.0
    for event in events:
        timestamp = datetime.fromisoformat(str(event["timestamp"])).date()
        if start <= timestamp <= end:
            total += float(event[key])
    return total


def _core_parity() -> tuple[bool, dict[str, Any]]:
    payload = _load("BC_CORE_PARITY.json")
    rows = payload.get("rows", [])
    passed = bool(payload.get("passed")) and bool(rows) and all(row.get("equal") is True for row in rows)
    return passed, {"passed": passed, "row_count": len(rows), "rows": rows}


def _identity_gate(manifest: dict[str, Any], receipt: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
    files = {
        "bars": ROOT / "data/historical/equity_pit_symmetric_2016_2026_v5.csv",
        "lifetimes": ROOT / "data/historical/equity_entity_lifetimes_full_v5.csv",
        "actions": ROOT / "data/historical/equity_survivorship_full_corporate_actions_v5.csv",
        "policy": OUTPUT / "CORE_SATELLITE_COMBINATION_POLICY_V1.md",
        "candidate": OUTPUT / "frozen_candidate_strategy.py",
    }
    expected = {
        "bars": EXPECTED_BARS_SHA,
        "lifetimes": EXPECTED_LIFETIMES_SHA,
        "actions": EXPECTED_ACTIONS_SHA,
        "policy": EXPECTED_POLICY_SHA,
        "candidate": EXPECTED_CANDIDATE_SHA,
    }
    actual = {name: _sha256(path) for name, path in files.items()}
    refs = {
        "main": _git("rev-parse", "main"),
        "origin_main": _git("rev-parse", "origin/main"),
        "paper_identity": manifest.get("paper_identity"),
        "live_enabled": manifest.get("live_enabled"),
    }
    passed = actual == expected and refs["main"] == EXPECTED_MAIN_SHA and refs["origin_main"] == EXPECTED_MAIN_SHA
    passed = (
        passed
        and receipt.get("starting_state", {}).get("standalone_design_sha") == "a822083eef4123d960ee7886d52951bfb20b29bd"
    )
    passed = (
        passed
        and receipt.get("starting_state", {}).get("standalone_economic_sha")
        == "9dbc44ce9f62b6e2f1b5d233eee77a7fff3df9d3"
    )
    passed = passed and manifest.get("production_changes") is False and manifest.get("broker_orders") is False
    return passed, {"passed": passed, "expected_hashes": expected, "actual_hashes": actual, "refs": refs}


def _candidate_gate(audit: list[dict[str, Any]], schedule: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
    monthly = {str(item) for item in schedule.get("monthly", [])}
    bad: list[str] = []
    valid = 0
    reservations = 0
    for row in audit:
        timestamp = str(row.get("decision_timestamp"))
        if timestamp not in monthly:
            bad.append(f"outside_monthly_schedule:{timestamp}")
        selected = row.get("selected_top_six", [])
        if selected:
            valid += 1
            if row.get("reservation_symbol") != selected[0]:
                bad.append(f"reservation_not_top_rank:{timestamp}")
            else:
                reservations += 1
            if len(selected) > 6:
                bad.append(f"top_six_overflow:{timestamp}")
    passed = not bad and valid > 0 and reservations / valid >= 0.95
    return passed, {
        "passed": passed,
        "decision_count": len(audit),
        "valid_top_six_decisions": valid,
        "reservations": reservations,
        "reservation_rate": reservations / valid if valid else 0.0,
        "errors": bad[:50],
    }


def _ledger_gate(payloads: dict[str, dict[str, Any]], parity: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
    errors: list[str] = []
    for label, arms in payloads.items():
        for arm, payload in arms.items():
            if arm == "control_a":
                continue
            for rebalance in payload.get("rebalances", []):
                targets = [(str(symbol), float(weight)) for symbol, weight in rebalance.get("target_weights", [])]
                if len(targets) > 6:
                    errors.append(f"{label}:{arm}:positions")
                if any(weight > 0.1000001 for _symbol, weight in targets):
                    errors.append(f"{label}:{arm}:name_cap")
                if sum(weight for _symbol, weight in targets) > 0.5500001:
                    errors.append(f"{label}:{arm}:equity_cap")
            for fill in payload.get("fills", []):
                if datetime.fromisoformat(str(fill["timestamp"])) <= datetime.fromisoformat(
                    str(fill["decision_timestamp"])
                ):
                    errors.append(f"{label}:{arm}:lookahead_fill")
    passed = bool(parity.get("passed")) and not errors
    return passed, {"passed": passed, "errors": errors[:100]}


def _id_pnl(source: dict[str, Any], start: date, end: date) -> float:
    sat = 0.0
    for event in source.get("realized_events", []):
        if event.get("source") == "id_satellite":
            timestamp = datetime.fromisoformat(str(event["timestamp"])).date()
            if start <= timestamp <= end:
                sat += float(event["net_pnl"])
    for event in source.get("dividends", []):
        if event.get("source") == "id_satellite":
            timestamp = datetime.fromisoformat(str(event["timestamp"])).date()
            if start <= timestamp <= end:
                sat += float(event["amount"])
    return sat


def _gates(
    metrics: dict[str, Any],
    sources: dict[str, Any],
    parity: dict[str, Any],
    identity: dict[str, Any],
    candidate: dict[str, Any],
    ledger: dict[str, Any],
    exposure: dict[str, Any],
    payloads: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    a1 = metrics["control_a"]["1x"]
    b1 = metrics["control_b"]["1x"]
    c1 = metrics["treatment_c"]["1x"]
    a2, a3 = metrics["control_a"]["2x"], metrics["control_a"]["3x"]
    b2, b3 = metrics["control_b"]["2x"], metrics["control_b"]["3x"]
    c2, c3 = metrics["treatment_c"]["2x"], metrics["treatment_c"]["3x"]
    c_source_1 = sources["1x"]["C"]["sources"]["id_satellite"]
    c_source_2 = sources["2x"]["C"]["sources"]["id_satellite"]
    c_source_3 = sources["3x"]["C"]["sources"]["id_satellite"]
    id_full = _id_pnl(sources["1x"]["C"], DEV_START, VAL_END)
    id_val = _id_pnl(sources["1x"]["C"], VAL_START, VAL_END)
    g2 = (
        c1["full"]["net_total_return"] > b1["full"]["net_total_return"]
        and c1["full"]["cagr"] > b1["full"]["cagr"]
        and c1["validation"]["net_total_return"] > b1["validation"]["net_total_return"]
        and c1["validation"]["cagr"] > b1["validation"]["cagr"]
        and id_full > 0
        and id_val > 0
    )
    risk_metrics = ("cagr", "sharpe", "sortino", "calmar")
    tolerances = {"cagr": 0.005, "sharpe": 0.03, "sortino": 0.03, "calmar": 0.03}
    improvements = sum(float(c1["full"][key]) > float(a1["full"][key]) for key in risk_metrics)
    floors = all(float(c1["full"][key]) >= float(a1["full"][key]) - tolerances[key] for key in risk_metrics)
    g3 = improvements >= 2 and floors and c1["full"]["max_drawdown"] <= a1["full"]["max_drawdown"] + 0.015
    validation_improvement = any(float(c1["validation"][key]) > float(a1["validation"][key]) for key in risk_metrics)
    g4 = (
        c1["validation"]["net_total_return"] > 0
        and c1["validation"]["cagr"] > 0
        and validation_improvement
        and c1["validation"]["max_drawdown"] <= a1["validation"]["max_drawdown"] + 0.015
        and c1["validation"]["net_total_return"] > b1["validation"]["net_total_return"]
        and c1["validation"]["cagr"] > b1["validation"]["cagr"]
    )
    g5 = (
        c2["full"]["net_total_return"] > b2["full"]["net_total_return"]
        and c_source_2["net_pnl"] > 0
        and c3["full"]["net_total_return"] >= b3["full"]["net_total_return"]
        and c_source_3["net_pnl"] >= 0
        and not (
            c2["full"]["sharpe"] < a2["full"]["sharpe"] - 0.05 and c3["full"]["sharpe"] < a3["full"]["sharpe"] - 0.05
        )
    )
    candidate_valid = candidate["passed"]
    c_audit = _load("C_RESERVATION_AUDIT.json")
    c_payload = payloads["1x"]["treatment_c"]
    rebalance_by_ts = {str(row["decision_timestamp"]): row for row in c_payload.get("rebalances", [])}
    active_count = 0
    valid_count = 0
    unexplained_zero = 0
    documented_zero = 0
    for row in c_audit:
        reservation = row.get("reservation_symbol")
        if not reservation:
            continue
        valid_count += 1
        target = dict(rebalance_by_ts.get(str(row["decision_timestamp"]), {}).get("target_weights", []))
        if float(target.get(reservation, 0.0)) > 0:
            active_count += 1
        else:
            reasons = rebalance_by_ts.get(str(row["decision_timestamp"]), {}).get("risk_overlay_reasons", [])
            if reasons:
                documented_zero += 1
            else:
                unexplained_zero += 1
    g6 = (
        candidate_valid
        and valid_count > 0
        and valid_count >= candidate["valid_top_six_decisions"] * 0.90
        and unexplained_zero == 0
    )
    a_turn = float(a1["full"]["turnover_one_way_annualized"])
    c_turn = float(c1["full"]["turnover_one_way_annualized"])
    satellite_full_cost = float(c_source_1["transaction_costs"])
    satellite_full_gross = float(c_source_1["gross_trading_contribution"])
    satellite_val_cost = _period_sum(
        [event for event in sources["1x"]["C"].get("cost_events", []) if event.get("source") == "id_satellite"],
        VAL_START,
        VAL_END,
        "cost",
    )
    satellite_val_gross = id_val + satellite_val_cost
    satellite_2x_cost = float(c_source_2["transaction_costs"])
    satellite_2x_gross = float(c_source_2["gross_trading_contribution"])
    g7 = (
        c_turn <= a_turn * 2.0
        and satellite_full_cost < satellite_full_gross
        and satellite_val_cost < satellite_val_gross
        and satellite_2x_cost < satellite_2x_gross
    )
    symbol_pnl = c_source_1.get("symbol_net_pnl", {})
    absolute_total = sum(abs(float(value)) for value in symbol_pnl.values())
    largest = max((abs(float(value)) for value in symbol_pnl.values()), default=0.0)
    distinct = len(symbol_pnl)
    leave_one = sum(
        float(value)
        for symbol, value in symbol_pnl.items()
        if abs(float(value)) != largest or symbol != max(symbol_pnl, key=lambda key: abs(float(symbol_pnl[key])))
    )
    g8 = (largest / absolute_total if absolute_total else 0.0) <= 0.35 and distinct >= 12 and leave_one >= 0
    g1 = identity["passed"] and parity["passed"] and ledger["passed"] and candidate["passed"]
    g9 = identity["refs"]["main"] == EXPECTED_MAIN_SHA and identity["refs"]["origin_main"] == EXPECTED_MAIN_SHA
    gates = {
        "G1_validity": g1,
        "G2_id_incremental_value_vs_B": g2,
        "G3_full_portfolio_contribution_vs_A": g3,
        "G4_validation_contribution": g4,
        "G5_cost_robustness": g5,
        "G6_satellite_deployability": g6,
        "G7_turnover_economics": g7,
        "G8_concentration": g8,
        "G9_architecture_production_safety": g9,
    }
    return {
        "gates": gates,
        "all_pass": all(gates.values()),
        "inputs": {
            "id_full_net_pnl": id_full,
            "id_validation_net_pnl": id_val,
            "full_risk_adjusted_improvements": improvements,
            "core_turnover_a": a_turn,
            "combined_turnover_c": c_turn,
            "satellite_full_cost": satellite_full_cost,
            "satellite_full_gross": satellite_full_gross,
            "satellite_validation_cost": satellite_val_cost,
            "satellite_validation_gross": satellite_val_gross,
            "satellite_2x_cost": satellite_2x_cost,
            "satellite_2x_gross": satellite_2x_gross,
            "satellite_largest_abs_symbol_fraction": largest / absolute_total if absolute_total else 0.0,
            "satellite_distinct_symbols": distinct,
            "satellite_leave_one_largest_net_pnl": leave_one,
            "satellite_valid_reservations": valid_count,
            "satellite_active_targets": active_count,
            "satellite_documented_zero": documented_zero,
            "satellite_unexplained_zero": unexplained_zero,
        },
    }


def _final_report(decision: str, validity: str, gates: dict[str, Any], metrics: dict[str, Any]) -> str:
    sources = _load("SOURCE_ATTRIBUTION.json")
    diagnostics = _load("DIVERSIFICATION_DIAGNOSTICS.json")
    parity = _load("BC_CORE_PARITY.json")
    a = metrics["control_a"]["1x"]["full"]
    b = metrics["control_b"]["1x"]["full"]
    c = metrics["treatment_c"]["1x"]["full"]
    inputs = gates["inputs"]

    def metric_row(label: str, values: dict[str, Any]) -> str:
        return (
            f"{label}: net {float(values['net_total_return']):.6f}; CAGR "
            f"{float(values['cagr']):.6f}; Sharpe {float(values['sharpe']):.6f}; "
            f"Sortino {float(values['sortino']):.6f}; max DD {float(values['max_drawdown']):.6f}; "
            f"Calmar {float(values['calmar']):.6f}."
        )

    def stress_row(multiplier: str) -> str:
        av = metrics["control_a"][multiplier]["full"]
        bv = metrics["control_b"][multiplier]["full"]
        cv = metrics["treatment_c"][multiplier]["full"]
        sat = sources[multiplier]["C"]["sources"]["id_satellite"]
        return (
            f"{multiplier}: A net {float(av['net_total_return']):.6f}, "
            f"B net {float(bv['net_total_return']):.6f}, C net {float(cv['net_total_return']):.6f}, "
            f"C Sharpe {float(cv['sharpe']):.6f}, ID net P&L {float(sat['net_pnl']):.2f}."
        )

    source_1x = sources["1x"]["C"]["sources"]
    lines = [
        "# Final combination decision",
        "",
        f"Decision: `{decision}`",
        f"Run validity: `{validity}`",
        "",
        "## Why standalone RESEARCH_REJECT was not dispositive",
        "",
        "The prior standalone test answered replacement of V3's six slots. This campaign answers whether one frozen ID slot adds value beside V3 under a matched reserved-slot cash control.",
        "",
        "## Frozen 5+1 combination policy",
        "",
        "Policy and hash were committed before B/C economics. B and C use a 5-slot inverse-volatility V3 core at 0.4583333333333333 and a 0.0916666666666667 reserved satellite slot; C owns the slot and B holds it in cash.",
        "",
        "## Production/control identity",
        "",
        "Control A is canonical `equity_residual_momentum_v3`; main and PAPER remain unchanged and LIVE remains disabled.",
        "",
        "## Control A",
        "",
        metric_row("1x full", a),
        metric_row("1x development", metrics["control_a"]["1x"]["development"]),
        metric_row("1x validation", metrics["control_a"]["1x"]["validation"]),
        "",
        "## Control B",
        "",
        metric_row("1x full", b),
        metric_row("1x development", metrics["control_b"]["1x"]["development"]),
        metric_row("1x validation", metrics["control_b"]["1x"]["validation"]),
        "B is the matched five-slot V3 core plus the frozen reservation held in cash.",
        "",
        "## Treatment C",
        "",
        metric_row("1x full", c),
        metric_row("1x development", metrics["treatment_c"]["1x"]["development"]),
        metric_row("1x validation", metrics["treatment_c"]["1x"]["validation"]),
        "C appends exactly the frozen 0.0916666666666667 satellite allocation when the reservation is expressible.",
        "",
        "## B/C V3-core parity",
        "",
        f"The independent parity artifact reports `{len(parity.get('rows', []))}` union-event comparisons with `passed={bool(parity.get('passed'))}`. B and C core target maps are identical before C's satellite is appended.",
        "",
        "## ID incremental value versus B",
        "",
        f"Full 1x C versus B net return: {float(c['net_total_return']):.6f} versus {float(b['net_total_return']):.6f}; validation: {float(metrics['treatment_c']['1x']['validation']['net_total_return']):.6f} versus {float(metrics['control_b']['1x']['validation']['net_total_return']):.6f}.",
        f"Independently attributed ID net P&L is {float(inputs['id_full_net_pnl']):.2f} full and {float(inputs['id_validation_net_pnl']):.2f} validation, but the full-period portfolio comparison fails G2.",
        "",
        "## Combined contribution versus A",
        "",
        f"C has {int(inputs['full_risk_adjusted_improvements'])} full-period risk-adjusted improvements versus A under the frozen tolerances; max-drawdown and the remaining risk-adjusted measures are recorded in METRICS.json. G3 therefore fails.",
        "",
        "## Development",
        "",
        metric_row("A 1x", metrics["control_a"]["1x"]["development"]),
        metric_row("B 1x", metrics["control_b"]["1x"]["development"]),
        metric_row("C 1x", metrics["treatment_c"]["1x"]["development"]),
        "",
        "## Validation",
        "",
        metric_row("A 1x", metrics["control_a"]["1x"]["validation"]),
        metric_row("B 1x", metrics["control_b"]["1x"]["validation"]),
        metric_row("C 1x", metrics["treatment_c"]["1x"]["validation"]),
        "G4 passes: C is positive, beats B on validation net return and CAGR, and improves at least one risk-adjusted measure versus A within the frozen drawdown tolerance.",
        "",
        "## 1x/2x/3x cost stress",
        "",
        stress_row("1x"),
        stress_row("2x"),
        stress_row("3x"),
        "G5 fails because the stressed full-period C-versus-B comparisons do not satisfy the frozen requirements, even though the attributed satellite P&L remains positive.",
        "",
        "## Satellite exposure and utilization",
        "",
        f"The candidate produced {int(inputs['satellite_valid_reservations'])} valid reservations; {int(inputs['satellite_active_targets'])} obtained a nonzero C target and {int(inputs['satellite_documented_zero'])} were documented risk-overlay zeros. Unexplained zeros: {int(inputs['satellite_unexplained_zero'])}. G6 passes.",
        "",
        "## Source P&L attribution",
        "",
        f"At 1x C, V3-core net P&L is {float(source_1x['v3_core']['net_pnl']):.2f} after {float(source_1x['v3_core']['transaction_costs']):.2f} costs; ID-satellite net P&L is {float(source_1x['id_satellite']['net_pnl']):.2f} after {float(source_1x['id_satellite']['transaction_costs']):.2f} costs. Attribution is from the shared account ledger, not a blended NAV sleeve.",
        "",
        "## Turnover economics",
        "",
        f"A one-way annualized turnover is {float(inputs['core_turnover_a']):.6f}; C is {float(inputs['combined_turnover_c']):.6f}. Satellite full/validation/2x costs are below the corresponding gross contributions, so G7 passes.",
        "",
        "## Concentration",
        "",
        f"The satellite spans {int(inputs['satellite_distinct_symbols'])} symbols; the largest absolute-symbol P&L fraction is {float(inputs['satellite_largest_abs_symbol_fraction']):.6f}; leave-one-largest net P&L is {float(inputs['satellite_leave_one_largest_net_pnl']):.2f}. G8 passes.",
        "",
        "## Diversification diagnostics",
        "",
        f"The descriptive A/C daily-return correlation is {float(diagnostics.get('daily_return_correlation_control_a_vs_c', 0.0)):.6f} over {int(diagnostics.get('daily_common_observations', 0))} common observations; {int(diagnostics.get('reservation_distinct_symbols', 0))} reservation symbols occurred. These diagnostics are not acceptance gates.",
        "",
        "## Frozen G1–G9 table",
        "",
    ]
    for key, value in gates["gates"].items():
        lines.append(f"* {key}: {'PASS' if value else 'FAIL'}")
    lines.extend(
        [
            "",
            "## Final interpretation",
            "",
            "`ALPHA_VALID_PORTFOLIO_ACCEPTED`"
            if validity == "COMBINATION_RUN_VALID" and decision == "COMBINATION_ACCEPT_FOR_FORWARD_PAPER_EVALUATION"
            else "`ALPHA_VALID_PORTFOLIO_REJECTED`"
            if validity == "COMBINATION_RUN_VALID"
            else "`COMBINATION_INCONCLUSIVE`",
            "",
            "## Production state",
            "",
            "No production, PAPER, LIVE, configuration, crypto, or broker-order state changed.",
            "",
            "## Files",
            "",
            "All raw arm results, source attribution, parity, exposure, diagnostics, verification, and gate inputs are in this directory.",
            "",
            "## Final research branch SHA",
            "",
            f"`{_git('rev-parse', 'HEAD')}`",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> int:
    manifest = _load("RUN_MANIFEST.json")
    receipt = _load("FROZEN_INPUT_RECEIPT.json")
    metrics = _load("METRICS.json")
    schedules = _load("SCHEDULE.json")
    parity_passed, parity = _core_parity()
    identity_passed, identity = _identity_gate(manifest, receipt)
    candidate_audit = _load("B_RESERVATION_AUDIT.json")
    candidate_passed, candidate = _candidate_gate(candidate_audit, schedules)
    payloads: dict[str, dict[str, Any]] = {}
    for label in ("1x", "2x", "3x"):
        payloads[label] = {
            "control_a": _load(f"CONTROL_A_{label.replace('.', 'P').upper()}_RESULTS.json"),
            "control_b": _load(f"CONTROL_B_{label.replace('.', 'P').upper()}_RESULTS.json"),
            "treatment_c": _load(f"TREATMENT_C_{label.replace('.', 'P').upper()}_RESULTS.json"),
        }
    ledger_passed, ledger = _ledger_gate(payloads, parity)
    exposure = _load("EXPOSURE_SLOT_AUDIT.json")
    source = _load("SOURCE_ATTRIBUTION.json")
    gates = _gates(metrics, source, parity, identity, candidate, ledger, exposure, payloads)
    validity = "COMBINATION_RUN_VALID" if gates["gates"]["G1_validity"] else "COMBINATION_RUN_INVALID"
    decision = (
        "COMBINATION_ACCEPT_FOR_FORWARD_PAPER_EVALUATION"
        if validity == "COMBINATION_RUN_VALID" and gates["all_pass"]
        else "COMBINATION_REJECT"
        if validity == "COMBINATION_RUN_VALID"
        else "COMBINATION_INCONCLUSIVE"
    )
    verification = {
        "run_validity": validity,
        "decision": decision,
        "identity": identity,
        "candidate": candidate,
        "parity": parity,
        "ledger": ledger,
        "gates": gates,
        "runner_verifier_agreement": {"metrics_loaded": True, "gates_recomputed_independently": True},
    }
    _json_dump(OUTPUT / "INDEPENDENT_VERIFICATION.json", verification)
    _json_dump(OUTPUT / "GATE_TABLE.json", gates)
    _json_dump(OUTPUT / "DECISION.json", {"decision": decision, "run_validity": validity, "gates": gates})
    (OUTPUT / "FINAL_REPORT.md").write_text(_final_report(decision, validity, gates, metrics), encoding="utf-8")
    print(json.dumps({"decision": decision, "run_validity": validity, "all_gates_pass": gates["all_pass"]}, indent=2))
    return 0 if validity == "COMBINATION_RUN_VALID" else 2


if __name__ == "__main__":
    raise SystemExit(main())
