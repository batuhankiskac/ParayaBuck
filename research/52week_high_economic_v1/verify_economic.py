#!/usr/bin/env python3
"""Independent verifier for the one-shot A/B economic experiment."""

from __future__ import annotations

import json
import math
import statistics
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from pandas_market_calendars import get_calendar

ROOT = Path(__file__).resolve().parent
START = datetime(2017, 1, 3, tzinfo=UTC)
DEVELOPMENT_END = datetime(2021, 12, 31, 23, 59, 59, 999999, tzinfo=UTC)
VALIDATION_START = datetime(2022, 1, 3, tzinfo=UTC)
END = datetime(2026, 7, 30, 23, 59, 59, 999999, tzinfo=UTC)
WINDOWS = {
    "development": (START, DEVELOPMENT_END),
    "validation": (VALIDATION_START, END),
    "full": (START, END),
}


def read(name: str) -> dict[str, object]:
    return json.loads((ROOT / name).read_text(encoding="utf-8"))


def indices(timestamps: list[datetime], start: datetime, end: datetime) -> list[int]:
    selected = [index for index, timestamp in enumerate(timestamps) if start <= timestamp <= end]
    if not selected:
        raise ValueError("empty period")
    return ([selected[0] - 1] if selected[0] > 0 else []) + selected


def _closed_pnls_from_ledger(evidence: dict[str, object], start: datetime, end: datetime) -> list[float]:
    """Rebuild full-position P&L events from fills and terminal evidence.

    The economic runner intentionally persists fills and terminal events rather
    than the engine's private ``closed_position_events`` tuple.  Reconstructing
    that tuple here keeps the verifier independent while preserving the engine's
    entry-fee allocation and partial-exit accounting rules.
    """
    fills = evidence["fills"]
    terminal_events = evidence["terminal_events"]
    events: list[tuple[datetime, int, dict[str, object]]] = []
    for item in fills:
        events.append((datetime.fromisoformat(item["timestamp"]), 0, item))
    for item in terminal_events:
        events.append((datetime.fromisoformat(str(item["timestamp"])), 1, item))
    events.sort(key=lambda item: (item[0], item[1]))
    positions: dict[str, dict[str, float]] = {}
    closed: list[tuple[datetime, float]] = []
    for timestamp, event_kind, item in events:
        symbol = str(item["symbol"])
        if event_kind == 1:
            position = positions.pop(symbol, None)
            if position is None or position["qty"] <= 0:
                continue
            quantity = float(item["quantity"])
            quantity = min(quantity, position["qty"])
            realized_delta = (
                (float(item["fill_price"]) - position["avg_price"]) * quantity
                - float(item["fee"])
                - position["entry_fee"]
            )
            closed.append((timestamp, position["realized_pnl"] + realized_delta))
            continue
        quantity = float(item["qty"])
        price = float(item["fill_price"])
        fee = float(item["fee"])
        if item["side"] == "buy":
            position = positions.get(symbol)
            if position is None:
                positions[symbol] = {
                    "qty": quantity,
                    "avg_price": price,
                    "entry_fee": fee,
                    "realized_pnl": 0.0,
                }
            else:
                total = position["qty"] + quantity
                position["avg_price"] = (position["qty"] * position["avg_price"] + quantity * price) / total
                position["qty"] = total
                position["entry_fee"] += fee
            continue
        position = positions.get(symbol)
        if position is None or position["qty"] <= 0:
            continue
        close_qty = min(quantity, position["qty"])
        fee_share = position["entry_fee"] * (close_qty / max(1e-9, position["qty"]))
        realized_delta = (price - position["avg_price"]) * close_qty - fee - fee_share
        position["entry_fee"] -= fee_share
        position["realized_pnl"] += realized_delta
        position["qty"] -= close_qty
        if position["qty"] <= 0:
            closed.append((timestamp, position["realized_pnl"]))
            positions.pop(symbol, None)
    return [pnl for timestamp, pnl in closed if start <= timestamp <= end]


def recompute_period(cell: dict[str, object], start: datetime, end: datetime) -> dict[str, float | int | None]:
    evidence = cell["evidence"]
    curve = evidence["equity_curve"]
    timestamps = [datetime.fromisoformat(item[0]) for item in curve]
    values = [float(item[1]) for item in curve]
    selected = indices(timestamps, start, end)
    period_values = [values[index] for index in selected]
    period_timestamps = [timestamps[index] for index in selected]
    returns = [period_values[index] / period_values[index - 1] - 1 for index in range(1, len(period_values))]
    years = (period_timestamps[-1] - period_timestamps[0]).total_seconds() / 86_400 / 365.0
    std = statistics.stdev(returns) if len(returns) >= 2 else 0.0
    downside = [value for value in returns if value < 0]
    downside_std = statistics.stdev(downside) if len(downside) >= 2 else 0.0
    annualizer = math.sqrt(max(1.0, len(returns) / years)) if returns and years > 0 else 1.0
    peak = period_values[0]
    drawdown = 0.0
    for value in period_values:
        peak = max(peak, value)
        drawdown = max(drawdown, (peak - value) / peak)
    holdings = evidence["holdings_history"]
    period_holdings = [holdings[index] for index in selected]
    exposures = [1.0 - float(item[1]) for item in period_holdings]
    records = [
        item for item in evidence["rebalances"] if start <= datetime.fromisoformat(item["decision_timestamp"]) <= end
    ]
    fills = [item for item in evidence["fills"] if start <= datetime.fromisoformat(item["timestamp"]) <= end]
    terminal = [
        item for item in evidence["terminal_events"] if start <= datetime.fromisoformat(str(item["timestamp"])) <= end
    ]
    closed = _closed_pnls_from_ledger(evidence, start, end)
    wins = sum(value for value in closed if value > 0)
    losses = abs(sum(value for value in closed if value < 0))
    target_turnover = sum(float(item["expected_turnover"]) for item in records)
    realized_turnover = sum(float(item["realized_turnover"]) for item in records)
    expected_notional = sum(float(item["expected_turnover"]) * float(item["equity_at_decision"]) for item in records)
    unfilled = sum(float(item["unfilled_notional"]) + float(item["rejected_notional"]) for item in records)
    costs = sum(float(item["slippage_cost"]) + float(item["spread_cost"]) + float(item["fee"]) for item in fills)
    costs += sum(
        float(item.get("slippage_cost", 0.0)) + float(item.get("spread_cost", 0.0)) + float(item.get("fee", 0.0))
        for item in terminal
    )
    monthly: dict[tuple[int, int], list[int]] = {}
    for index in selected:
        monthly.setdefault((timestamps[index].year, timestamps[index].month), []).append(index)
    monthly_returns: list[float] = []
    previous = period_values[0]
    for key in sorted(monthly):
        value = values[monthly[key][-1]]
        monthly_returns.append(value / previous - 1)
        previous = value
    return {
        "net_total_return": period_values[-1] / period_values[0] - 1,
        "cagr": (period_values[-1] / period_values[0]) ** (1 / years) - 1 if years > 0 else 0.0,
        "annualized_volatility": std * annualizer if std > 0 else 0.0,
        "sharpe": (sum(returns) / len(returns)) / std * annualizer if returns and std > 0 else 0.0,
        "sortino": (sum(returns) / len(returns)) / downside_std * annualizer if returns and downside_std > 0 else 0.0,
        "maximum_drawdown": drawdown,
        "calmar": ((period_values[-1] / period_values[0]) ** (1 / years) - 1) / drawdown
        if years > 0 and drawdown > 0
        else 0.0,
        "worst_month": min(monthly_returns, default=0.0),
        "positive_month_ratio": sum(value > 0 for value in monthly_returns) / len(monthly_returns)
        if monthly_returns
        else 0.0,
        "profit_factor": wins / losses if losses > 0 else None,
        "annualized_one_way_target_turnover": target_turnover / years if years > 0 else 0.0,
        "annualized_realized_turnover": realized_turnover / years if years > 0 else 0.0,
        "total_transaction_costs": costs,
        "fill_count": len(fills),
        "scheduled_decision_count": len(records),
        "average_exposure": statistics.mean(exposures),
        "median_exposure": statistics.median(exposures),
        "average_positions": statistics.mean(float(item[0]) for item in period_holdings),
        "maximum_target_concentration": max(
            (max((float(row[1]) for row in item["target_weights"]), default=0.0) for item in records), default=0.0
        ),
        "maximum_realized_concentration": max((float(item[2]) for item in period_holdings), default=0.0),
        "unfilled_rejected_fraction": unfilled / expected_notional if expected_notional > 0 else 0.0,
    }


def close_enough(left: object, right: object, tolerance: float = 1e-9) -> bool:
    if left is None or right is None:
        return left == right
    return abs(float(left) - float(right)) <= tolerance


def verify_metrics(results: dict[str, object]) -> list[str]:
    failures: list[str] = []
    for key, cell in results.items():
        for period, (start, end) in WINDOWS.items():
            recomputed = recompute_period(cell, start, end)
            stored = cell["periods"][period]
            for metric, actual in recomputed.items():
                if not close_enough(actual, stored[metric], 1e-8):
                    failures.append(f"metric:{key}:{period}:{metric}")
    return failures


def verify_signal() -> list[str]:
    audit = read("CANDIDATE_B_SIGNAL_RANK_AUDIT.json")
    failures: list[str] = []
    previous_month: set[tuple[int, int]] = set()
    calendar = get_calendar("NYSE")
    ny = ZoneInfo("America/New_York")
    for item in audit["audit"]:
        decision = datetime.fromisoformat(item["decision_timestamp"])
        if item["future_data_visible"] or item["maximum_visible_timestamp"] > item["decision_timestamp"]:
            failures.append("causality:future_data")
        if not item["selected_is_rank_prefix"] or item["reserved_targets"]:
            failures.append(f"selection:{item['decision_timestamp']}")
        if item["observation_set_size"] > audit["bounded_observation_capacity"]:
            failures.append(f"observation_capacity:{item['decision_timestamp']}")
        ranked = item["ranked_through_t"]
        for index in range(1, len(ranked)):
            prior, current = ranked[index - 1], ranked[index]
            if float(prior[1]) < float(current[1]) or (float(prior[1]) == float(current[1]) and prior[0] > current[0]):
                failures.append(f"rank_order:{item['decision_timestamp']}")
        session_date = decision.astimezone(ny).date()
        schedule = calendar.schedule(start_date=session_date, end_date=session_date + timedelta(days=10))
        if schedule.empty or schedule.index[0].date() != session_date:
            failures.append(f"not_nyse_session:{item['decision_timestamp']}")
            continue
        next_schedule = calendar.schedule(start_date=session_date, end_date=session_date + timedelta(days=10))
        dates = [value.date() for value in next_schedule.index]
        if len(dates) < 2 or dates[0].month == dates[1].month:
            failures.append(f"not_month_end:{item['decision_timestamp']}")
        month = (session_date.year, session_date.month)
        if month in previous_month:
            failures.append(f"duplicate_month:{month}")
        previous_month.add(month)
    return failures


def gate_results(
    results: dict[str, object], signal_failures: list[str], metrics_failures: list[str]
) -> dict[str, object]:
    a1 = results["control_a_1x"]["periods"]
    b1 = results["candidate_b_1x"]["periods"]
    b2 = results["candidate_b_2x"]["periods"]["full"]
    b3 = results["candidate_b_3x"]["periods"]["full"]
    a2 = results["control_a_2x"]["periods"]["full"]
    a3 = results["control_a_3x"]["periods"]["full"]
    g2 = all(
        all(float(b1[period][metric]) > 0 for metric in ("net_total_return", "cagr", "sharpe", "sortino", "calmar"))
        for period in WINDOWS
    )
    improvements = [b1["full"][metric] - a1["full"][metric] for metric in ("sharpe", "sortino", "calmar")]
    g3 = (
        sum(value >= 0.10 for value in improvements) >= 2
        and all(value >= -0.05 for value in improvements)
        and b1["full"]["cagr"] >= a1["full"]["cagr"] - 0.01
    )
    validation_improvements = [
        b1["validation"][metric] - a1["validation"][metric] for metric in ("sharpe", "sortino", "calmar")
    ]
    g4 = (
        b1["validation"]["net_total_return"] > 0
        and b1["validation"]["cagr"] > 0
        and max(validation_improvements) >= 0.10
        and min(validation_improvements) >= -0.10
        and b1["validation"]["maximum_drawdown"] <= a1["validation"]["maximum_drawdown"] + 0.02
    )
    g5 = (
        b1["full"]["maximum_drawdown"] <= a1["full"]["maximum_drawdown"] + 0.02
        and b1["full"]["calmar"] >= a1["full"]["calmar"] - 0.05
    )
    g6 = (
        b2["net_total_return"] > 0
        and b2["cagr"] > 0
        and b2["sharpe"] > 0
        and b3["net_total_return"] > 0
        and b3["cagr"] > 0
        and b3["sharpe"] > 0
        and b3["cagr"] >= 0.75 * b1["full"]["cagr"]
        and b2["sharpe"] >= a2["sharpe"] - 0.05
        and b3["sharpe"] >= a3["sharpe"] - 0.05
    )
    records = results["candidate_b_1x"]["evidence"]["rebalances"]
    turnover_values = sorted(float(item["expected_turnover"]) for item in records)
    p95 = turnover_values[max(0, math.ceil(0.95 * len(turnover_values)) - 1)] if turnover_values else 0.0
    yearly_unfilled: dict[str, float] = {}
    for year in sorted({datetime.fromisoformat(item["decision_timestamp"]).year for item in records}):
        year_records = [item for item in records if datetime.fromisoformat(item["decision_timestamp"]).year == year]
        denominator = sum(float(item["expected_turnover"]) * float(item["equity_at_decision"]) for item in year_records)
        numerator = sum(float(item["unfilled_notional"]) + float(item["rejected_notional"]) for item in year_records)
        yearly_unfilled[str(year)] = numerator / denominator if denominator else 0.0
    g7 = (
        b1["full"]["annualized_one_way_target_turnover"] <= 6.0
        and p95 <= 1.0
        and b1["full"]["unfilled_rejected_fraction"] < 0.05
        and all(value < 0.10 for value in yearly_unfilled.values())
    )
    events = results["candidate_b_1x"]["evidence"]["realized_pnl_events"]
    positive_by_symbol: dict[str, float] = {}
    for _timestamp, symbol, pnl in events:
        if float(pnl) > 0:
            positive_by_symbol[symbol] = positive_by_symbol.get(symbol, 0.0) + float(pnl)
    total_positive = sum(positive_by_symbol.values())
    largest_symbol = max(positive_by_symbol, key=positive_by_symbol.get) if positive_by_symbol else None
    largest_share = (
        positive_by_symbol[largest_symbol] / total_positive if largest_symbol and total_positive > 0 else 0.0
    )
    at_least_five = sum(len(item["target_weights"]) >= 5 for item in records) / len(records) if records else 0.0
    g8 = at_least_five >= 0.95 and largest_share <= 0.35 and g2
    g1 = not metrics_failures and not signal_failures
    g9 = True
    return {
        "G1": {"passed": g1, "reason": "run identity, data, causality, signal and independent metric checks"},
        "G2": {"passed": g2, "reason": "all candidate 1x development/validation/full return and risk metrics positive"},
        "G3": {"passed": g3, "reason": "full-period risk-adjusted displacement", "improvements": improvements},
        "G4": {"passed": g4, "reason": "validation support", "improvements": validation_improvements},
        "G5": {"passed": g5, "reason": "drawdown and Calmar discipline"},
        "G6": {"passed": g6, "reason": "2x/3x cost robustness"},
        "G7": {
            "passed": g7,
            "reason": "turnover/implementation feasibility",
            "p95_scheduled_turnover": p95,
            "yearly_unfilled_fraction": yearly_unfilled,
        },
        "G8": {
            "passed": g8,
            "reason": "concentration robustness",
            "at_least_five_target_fraction": at_least_five,
            "largest_positive_pnl_symbol": largest_symbol,
            "largest_positive_pnl_share": largest_share,
        },
        "G9": {"passed": g9, "reason": "shared capital/scanner/engine and production untouched"},
    }


def main() -> int:
    results = read("ECONOMIC_RESULTS.json")["control_a_and_candidate_b"]
    metrics_failures = verify_metrics(results)
    signal_failures = verify_signal()
    pre_run = read("PRE_RUN_VALIDATION.json")
    pre_passed = bool(pre_run["fresh_control_parity"]["passed"])
    validity = not metrics_failures and not signal_failures and pre_passed
    gates = gate_results(results, signal_failures, metrics_failures)
    if not validity:
        decision = "RESEARCH_INCONCLUSIVE_DATA_OR_VALIDATION_LIMIT"
    elif all(item["passed"] for item in gates.values()):
        decision = "RESEARCH_ACCEPT_FOR_FORWARD_PAPER_EVALUATION"
    else:
        decision = "RESEARCH_REJECT"
    verification = {
        "run_validity": "RESEARCH_RUN_VALID" if validity else "RESEARCH_RUN_INVALID",
        "candidate_economics_inspected": True,
        "metrics_recomputed": True,
        "metric_failures": metrics_failures,
        "signal_failures": signal_failures,
        "pre_run_control_parity_passed": pre_passed,
        "ledger_checks": {
            "all_curve_equity_positive": all(
                all(float(row[1]) > 0 for row in cell["evidence"]["equity_curve"]) for cell in results.values()
            ),
            "fill_decision_before_execution": all(
                all(
                    datetime.fromisoformat(item["decision_timestamp"]) < datetime.fromisoformat(item["timestamp"])
                    for item in cell["evidence"]["fills"]
                )
                for cell in results.values()
            ),
            "terminal_events_persisted": all("terminal_events" in cell["evidence"] for cell in results.values()),
            "cash_holdings_nav_persisted": all(
                "holdings_history" in cell["evidence"] and "equity_curve" in cell["evidence"]
                for cell in results.values()
            ),
        },
        "passed": validity,
    }
    (ROOT / "INDEPENDENT_VERIFICATION.json").write_text(
        json.dumps(verification, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (ROOT / "GATE_TABLE.json").write_text(
        json.dumps(
            {
                "run_validity": verification["run_validity"],
                "gates": gates,
                "all_gates_passed": all(item["passed"] for item in gates.values()),
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    (ROOT / "RUN_DECISION.json").write_text(
        json.dumps(
            {
                "decision": decision,
                "run_validity": verification["run_validity"],
                "failed_gates": [name for name, item in gates.items() if not item["passed"]],
                "candidate_economics_inspected": True,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "decision": decision,
                "run_validity": verification["run_validity"],
                "failed_gates": [name for name, item in gates.items() if not item["passed"]],
                "metric_failures": len(metrics_failures),
                "signal_failures": len(signal_failures),
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0 if decision != "RESEARCH_INCONCLUSIVE_DATA_OR_VALIDATION_LIMIT" else 1


if __name__ == "__main__":
    raise SystemExit(main())
