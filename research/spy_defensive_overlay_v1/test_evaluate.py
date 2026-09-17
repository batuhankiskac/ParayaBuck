from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest

from research.spy_defensive_overlay_v1.data import FROZEN_HASHES
from research.spy_defensive_overlay_v1.evaluate import evaluate_campaign, evaluate_files


def _metrics(*, cagr: float, drawdown: float, sharpe: float, sortino: float, calmar: float) -> dict[str, float]:
    return {
        "cagr": cagr,
        "max_drawdown": drawdown,
        "sharpe": sharpe,
        "sortino": sortino,
        "calmar": calmar,
        "turnover": 1.0,
        "transaction_costs": 100.0,
    }


def _coverage() -> dict[str, object]:
    return {
        "scheduled_opportunities": 10,
        "recorded_decisions": 9,
        "skipped_decisions": 1,
        "accounting_complete": True,
        "skips": [{"timestamp": "2020-01-01T00:00:00+00:00", "reason": "complete_bar_gate"}],
        "development_decisions": 4,
        "validation_decisions": 5,
    }


def _capacity() -> dict[str, object]:
    rows = []
    for index in range(5):
        realized = 0.075 if index < 4 else 0.0
        targets = [[f"V{slot}", 0.085] for slot in range(5)] if index < 4 else [[f"V{slot}", 0.10] for slot in range(5)]
        if realized:
            targets.append(["SH", realized])
        rows.append(
            {
                "intended_sh_exposure": 0.10,
                "realized_overlay_sh_exposure": realized,
                "unused_equity_allocation": 0.05,
                "capacity_loss_unused_equity_allocation": 0.05,
                "total_positions": len(targets),
                "target_weights": targets,
                "capacity_loss_reasons": ["position_slot_limit"],
            }
        )
    return {"rows": rows, "average_unused_equity_allocation": 0.05}


def _accepted_fixture() -> dict[str, object]:
    results: dict[str, object] = {
        "campaign": "equity_spy_defensive_overlay_v1",
        "data_hashes": dict(FROZEN_HASHES),
        "independent_verification": {"status": "PASS"},
        "runs": {},
    }
    runs = results["runs"]
    assert isinstance(runs, dict)
    for cost in (1, 2, 3):
        for arm in ("A_incumbent", "B_defensive_cash", "C_defensive_sh"):
            if cost < 3:
                full = {
                    "A_incumbent": _metrics(cagr=0.10, drawdown=0.20, sharpe=1.00, sortino=1.00, calmar=1.00),
                    "B_defensive_cash": _metrics(cagr=0.09, drawdown=0.19, sharpe=1.02, sortino=1.02, calmar=1.02),
                    "C_defensive_sh": _metrics(cagr=0.08, drawdown=0.18, sharpe=1.10, sortino=0.95, calmar=1.00),
                }[arm]
                development = {
                    "A_incumbent": _metrics(cagr=0.10, drawdown=0.20, sharpe=1.00, sortino=1.00, calmar=1.00),
                    "B_defensive_cash": _metrics(cagr=0.09, drawdown=0.195, sharpe=1.02, sortino=1.02, calmar=1.02),
                    "C_defensive_sh": _metrics(cagr=0.08, drawdown=0.19, sharpe=1.05, sortino=1.00, calmar=1.00),
                }[arm]
                validation = {
                    "A_incumbent": _metrics(cagr=0.10, drawdown=0.20, sharpe=1.00, sortino=1.00, calmar=1.00),
                    "B_defensive_cash": _metrics(cagr=0.08, drawdown=0.195, sharpe=1.02, sortino=1.02, calmar=1.02),
                    "C_defensive_sh": _metrics(cagr=0.07, drawdown=0.19, sharpe=1.05, sortino=1.00, calmar=1.00),
                }[arm]
            else:
                full = {
                    "A_incumbent": _metrics(cagr=0.10, drawdown=0.20, sharpe=1.00, sortino=1.00, calmar=1.00),
                    "B_defensive_cash": _metrics(cagr=0.08, drawdown=0.20, sharpe=1.00, sortino=1.00, calmar=1.00),
                    "C_defensive_sh": _metrics(cagr=0.06, drawdown=0.20, sharpe=1.00, sortino=0.99, calmar=0.99),
                }[arm]
                development = full
                validation = full
            runs[f"{arm}_{cost}x"] = {
                "periods": {"full": full, "development": development, "validation": validation},
                "coverage": _coverage(),
                "capacity": _capacity() if arm == "C_defensive_sh" else {"rows": []},
            }
    return results


def test_exact_threshold_edges_accept() -> None:
    gates, decision = evaluate_campaign(_accepted_fixture())

    assert decision["verdict"] == "RESEARCH_ACCEPT_FOR_FORWARD_PAPER_EVALUATION"
    assert decision["all_gates_passed"] is True
    assert all(row["passed"] for row in gates["gates"].values())
    assert gates["gates"]["2"]["details"]["drawdown_reduction"] == pytest.approx(0.02)
    assert gates["gates"]["8"]["details"]["realized_at_least_0p075_rate"] == pytest.approx(0.80)


def test_valid_evidence_with_failed_economic_gate_rejects() -> None:
    results = _accepted_fixture()
    results["runs"]["C_defensive_sh_1x"]["periods"]["full"]["max_drawdown"] = 0.181

    gates, decision = evaluate_campaign(results)

    assert gates["gates"]["2"]["passed"] is False
    assert decision["verdict"] == "RESEARCH_REJECT"


def test_missing_or_failed_independent_verification_is_inconclusive() -> None:
    results = _accepted_fixture()
    results["independent_verification"] = {"status": "FAIL"}

    gates, decision = evaluate_campaign(results)

    assert gates["gates"]["1"]["passed"] is False
    assert decision["verdict"] == "RESEARCH_INCONCLUSIVE_DATA_OR_VALIDATION_LIMIT"


def test_malformed_campaign_results_are_inconclusive() -> None:
    results = _accepted_fixture()
    del results["runs"]["C_defensive_sh_3x"]["periods"]["full"]["cagr"]

    gates, decision = evaluate_campaign(results)

    assert gates["gates"]["1"]["passed"] is False
    assert gates["gates"]["1"]["details"]["result_schema_valid"] is False
    assert decision["verdict"] == "RESEARCH_INCONCLUSIVE_DATA_OR_VALIDATION_LIMIT"


def test_development_validation_opposite_directions_fail_gate6() -> None:
    results = _accepted_fixture()
    development = results["runs"]["C_defensive_sh_1x"]["periods"]["development"]
    development["max_drawdown"] = 0.21
    development["sharpe"] = 0.95

    gates, decision = evaluate_campaign(results)

    assert gates["gates"]["6"]["passed"] is False
    assert decision["verdict"] == "RESEARCH_REJECT"


def test_files_write_gate_table_and_exactly_one_allowed_decision(tmp_path: Path) -> None:
    results = deepcopy(_accepted_fixture())
    results.pop("independent_verification")
    source = tmp_path / "campaign_results.json"
    source.write_text(json.dumps(results), encoding="utf-8")
    (tmp_path / "independent_verification.json").write_text(json.dumps({"status": "PASS"}), encoding="utf-8")

    decision = evaluate_files(source, output_dir=tmp_path)

    assert decision["verdict"] == "RESEARCH_ACCEPT_FOR_FORWARD_PAPER_EVALUATION"
    assert json.loads((tmp_path / "decision.json").read_text())["verdict"] == decision["verdict"]
    assert set(json.loads((tmp_path / "gate_table.json").read_text())["gates"]) == {str(i) for i in range(1, 9)}
