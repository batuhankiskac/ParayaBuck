#!/usr/bin/env python3
"""Build compact, read-only reporting artifacts from the one-shot run."""

from __future__ import annotations

import json
import math
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from pandas_market_calendars import get_calendar

ROOT = Path(__file__).resolve().parent
RESULTS = json.loads((ROOT / "ECONOMIC_RESULTS.json").read_text(encoding="utf-8"))["control_a_and_candidate_b"]
GATES = json.loads((ROOT / "GATE_TABLE.json").read_text(encoding="utf-8"))
VERIFICATION = json.loads((ROOT / "INDEPENDENT_VERIFICATION.json").read_text(encoding="utf-8"))
SIGNAL = json.loads((ROOT / "CANDIDATE_B_SIGNAL_RANK_AUDIT.json").read_text(encoding="utf-8"))
COVERAGE = json.loads((ROOT / "CANDIDATE_B_DECISION_COVERAGE.json").read_text(encoding="utf-8"))

METRICS = (
    "net_total_return",
    "cagr",
    "annualized_volatility",
    "sharpe",
    "sortino",
    "maximum_drawdown",
    "calmar",
    "worst_month",
    "positive_month_ratio",
    "profit_factor",
    "annualized_one_way_target_turnover",
    "annualized_realized_turnover",
    "total_transaction_costs",
    "fill_count",
    "scheduled_decision_count",
    "average_exposure",
    "median_exposure",
    "average_positions",
    "maximum_target_concentration",
    "maximum_realized_concentration",
    "unfilled_rejected_fraction",
)


def write(name: str, value: object) -> None:
    (ROOT / name).write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def compact_cell(cell: dict[str, object]) -> dict[str, object]:
    return {
        "arm": cell["arm"],
        "cost_multiplier": cell["cost_multiplier"],
        "counts": cell["counts"],
        "hashes": cell["hashes"],
        "periods": {
            period: {metric: values.get(metric) for metric in METRICS} for period, values in cell["periods"].items()
        },
        "validation_years": cell["validation_years"],
    }


def build_results(prefix: str, filename: str) -> None:
    write(
        filename,
        {
            "source": "ECONOMIC_RESULTS.json",
            "source_scope": f"{prefix}_*",
            "metric_fields": list(METRICS),
            "arms": {key: compact_cell(value) for key, value in RESULTS.items() if key.startswith(prefix)},
        },
    )


def build_turnover() -> None:
    cell = RESULTS["candidate_b_1x"]
    records = cell["evidence"]["rebalances"]
    turnover = sorted(float(item["expected_turnover"]) for item in records)
    p95_index = max(0, math.ceil(0.95 * len(turnover)) - 1)
    yearly: dict[str, dict[str, float]] = {}
    for year in sorted({datetime.fromisoformat(item["decision_timestamp"]).year for item in records}):
        year_records = [item for item in records if datetime.fromisoformat(item["decision_timestamp"]).year == year]
        denominator = sum(float(item["expected_turnover"]) * float(item["equity_at_decision"]) for item in year_records)
        numerator = sum(float(item["unfilled_notional"]) + float(item["rejected_notional"]) for item in year_records)
        yearly[str(year)] = {
            "scheduled_decisions": len(year_records),
            "unfilled_rejected_fraction": numerator / denominator if denominator else 0.0,
        }
    write(
        "TURNOVER_DIAGNOSTICS.json",
        {
            "source": "ECONOMIC_RESULTS.json + GATE_TABLE.json",
            "candidate_b_1x_full": RESULTS["candidate_b_1x"]["periods"]["full"],
            "scheduled_turnover_p95": turnover[p95_index] if turnover else 0.0,
            "yearly": yearly,
            "fill_completion": COVERAGE["candidate_b_1x"]["fill_completion"],
            "partial_fills_included": True,
            "rejected_orders_included": True,
            "gate_limit": {
                "annualized_one_way_target_turnover": 6.0,
                "p95_scheduled_turnover": 1.0,
                "overall_unfilled": 0.05,
                "yearly_unfilled": 0.10,
            },
        },
    )


def build_concentration() -> None:
    cell = RESULTS["candidate_b_1x"]
    positive_by_symbol: dict[str, float] = {}
    for _timestamp, symbol, pnl in cell["evidence"]["realized_pnl_events"]:
        if float(pnl) > 0:
            positive_by_symbol[symbol] = positive_by_symbol.get(symbol, 0.0) + float(pnl)
    total_positive = sum(positive_by_symbol.values())
    ranking = sorted(positive_by_symbol.items(), key=lambda item: (-item[1], item[0]))
    filled_symbols = sorted(
        {item["symbol"] for item in cell["evidence"]["fills"]}
        | {item["symbol"] for item in cell["evidence"]["terminal_events"]}
    )
    write(
        "CONCENTRATION_DIAGNOSTICS.json",
        {
            "source": "ECONOMIC_RESULTS.json + GATE_TABLE.json",
            "unique_filled_symbols": filled_symbols,
            "unique_filled_symbol_count": len(filled_symbols),
            "sector_or_category_context": "unavailable in the frozen canonical input; no sector inference applied",
            "at_least_five_target_fraction": GATES["gates"]["G8"]["at_least_five_target_fraction"],
            "largest_positive_pnl_symbol": GATES["gates"]["G8"]["largest_positive_pnl_symbol"],
            "largest_positive_pnl_share": GATES["gates"]["G8"]["largest_positive_pnl_share"],
            "positive_pnl_contribution_ranking": [
                {
                    "symbol": symbol,
                    "positive_realized_pnl": pnl,
                    "share": pnl / total_positive if total_positive else 0.0,
                }
                for symbol, pnl in ranking
            ],
            "maximum_realized_concentration": cell["periods"]["full"]["maximum_realized_concentration"],
            "attribution_only_largest_entity_removal": "not rerun or re-optimized",
        },
    )


def build_cadence() -> None:
    calendar = get_calendar("NYSE")
    ny = ZoneInfo("America/New_York")
    decisions = SIGNAL["audit"]
    records = RESULTS["candidate_b_1x"]["evidence"]["rebalances"]
    fill_by_decision: dict[str, list[datetime]] = {}
    for fill in RESULTS["candidate_b_1x"]["evidence"]["fills"]:
        fill_by_decision.setdefault(fill["decision_timestamp"], []).append(datetime.fromisoformat(fill["timestamp"]))
    rows: list[dict[str, object]] = []
    for item in decisions:
        decision = datetime.fromisoformat(item["decision_timestamp"])
        session = decision.astimezone(ny).date()
        schedule = calendar.schedule(start_date=session, end_date=session + timedelta(days=10))
        month_end = bool(
            not schedule.empty
            and schedule.index[0].date() == session
            and len(schedule.index) >= 2
            and schedule.index[0].month != schedule.index[1].month
        )
        executions = fill_by_decision.get(item["decision_timestamp"], [])
        rows.append(
            {
                "decision_timestamp": item["decision_timestamp"],
                "nyse_session_date": session.isoformat(),
                "is_last_nyse_session_of_month": month_end,
                "first_execution_timestamp": min(executions).isoformat() if executions else None,
                "all_executions_after_decision": all(value > decision for value in executions),
            }
        )
    write(
        "CADENCE_AUDIT.json",
        {
            "source": "CANDIDATE_B_SIGNAL_RANK_AUDIT.json + ECONOMIC_RESULTS.json",
            "scheduled_decisions": len(decisions),
            "actual_decisions": len(records),
            "skipped_decisions": 0,
            "first_decision": decisions[0]["decision_timestamp"],
            "last_decision": decisions[-1]["decision_timestamp"],
            "all_decisions_nyse_month_end": all(row["is_last_nyse_session_of_month"] for row in rows),
            "all_execution_timestamps_after_decision": all(row["all_executions_after_decision"] for row in rows),
            "decision_rows": rows,
        },
    )


def main() -> None:
    build_results("control_a", "CONTROL_A_RESULTS.json")
    build_results("candidate_b", "CANDIDATE_B_RESULTS.json")
    write(
        "COST_STRESS.json",
        {
            "source": "ECONOMIC_RESULTS.json",
            "candidate_b": {
                key.removeprefix("candidate_b_"): {
                    "full": value["periods"]["full"],
                    "cost_multiplier": value["cost_multiplier"],
                }
                for key, value in RESULTS.items()
                if key.startswith("candidate_b_")
            },
            "control_a": {
                key.removeprefix("control_a_"): {
                    "full": value["periods"]["full"],
                    "cost_multiplier": value["cost_multiplier"],
                }
                for key, value in RESULTS.items()
                if key.startswith("control_a_")
            },
        },
    )
    write(
        "YEARLY_VALIDATION.json",
        {
            "source": "ECONOMIC_RESULTS.json",
            "candidate_b_1x": RESULTS["candidate_b_1x"]["validation_years"],
            "control_a_1x": RESULTS["control_a_1x"]["validation_years"],
        },
    )
    build_turnover()
    build_concentration()
    build_cadence()


if __name__ == "__main__":
    main()
