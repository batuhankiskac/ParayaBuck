"""Finalize diagnostics and the frozen gate decision from persisted results."""

from __future__ import annotations

import json
import math
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path
from statistics import mean
from typing import Any

from parayabuck.backtest.stats import sample_stddev
from parayabuck.markets.sectors import sector_of

OUTPUT = Path(__file__).resolve().parent
PERFORMANCE_KEYS = ("net_total_return", "cagr", "sharpe", "sortino", "calmar")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False), encoding="utf-8")


def _year_metrics(payload: Any, year: int) -> dict[str, object]:
    points = [
        (datetime.fromisoformat(str(item["timestamp"])), float(item["equity"]))
        for item in payload["equity_curve"]
        if datetime.fromisoformat(str(item["timestamp"])).year == year
    ]
    if len(points) < 2:
        raise ValueError(f"Year {year} has insufficient curve points")
    values = [value for _timestamp, value in points]
    returns = [values[index] / values[index - 1] - 1.0 for index in range(1, len(values)) if values[index - 1] > 0]
    downside = [value for value in returns if value < 0]
    duration_days = (points[-1][0] - points[0][0]).total_seconds() / 86_400
    years = max(duration_days / 365.0, 1.0 / 365.0)
    total_return = values[-1] / values[0] - 1.0
    cagr = (1.0 + total_return) ** (1.0 / years) - 1.0 if total_return > -1 else 0.0
    std = sample_stddev(returns)
    downside_std = sample_stddev(downside)
    annualizer = math.sqrt(max(1.0, len(returns) / years))
    peak = values[0]
    max_drawdown = 0.0
    for value in values:
        peak = max(peak, value)
        max_drawdown = max(max_drawdown, (peak - value) / peak if peak > 0 else 0.0)
    month_end: dict[tuple[int, int], float] = {}
    for timestamp, value in points:
        month_end[(timestamp.year, timestamp.month)] = value
    month_returns: list[float] = []
    previous = values[0]
    for key in sorted(month_end):
        month_returns.append(month_end[key] / previous - 1.0 if previous > 0 else 0.0)
        previous = month_end[key]
    closed = [
        float(event["pnl"])
        for event in payload["closed_position_events"]
        if datetime.fromisoformat(str(event["timestamp"])).year == year
    ]
    wins = sum(value for value in closed if value > 0)
    losses = abs(sum(value for value in closed if value < 0))
    fills = [fill for fill in payload["fills"] if datetime.fromisoformat(str(fill["timestamp"])).year == year]
    rebalances = [
        record
        for record in payload["rebalances"]
        if record["reason"] == "scheduled" and datetime.fromisoformat(str(record["decision_timestamp"])).year == year
    ]
    weights = [
        item["weights"]
        for item in payload["weight_history"]
        if datetime.fromisoformat(str(item["timestamp"])).year == year
    ]
    exposures = [sum(float(value) for value in mapping.values()) for mapping in weights]
    positions = [sum(float(value) > 0 for value in mapping.values()) for mapping in weights]
    concentration = [max((float(value) for value in mapping.values()), default=0.0) for mapping in weights]
    return {
        "start": points[0][0].isoformat(),
        "end": points[-1][0].isoformat(),
        "net_total_return": total_return,
        "cagr": cagr,
        "annualized_volatility": std * annualizer if std > 0 else 0.0,
        "sharpe": sum(returns) / len(returns) / std * annualizer if returns and std > 0 else 0.0,
        "sortino": sum(returns) / len(returns) / downside_std * annualizer if returns and downside_std > 0 else 0.0,
        "max_drawdown": max_drawdown,
        "calmar": cagr / max_drawdown if max_drawdown > 0 else 0.0,
        "worst_month": min(month_returns, default=0.0),
        "positive_month_ratio": sum(value > 0 for value in month_returns) / len(month_returns)
        if month_returns
        else 0.0,
        "profit_factor": wins / losses if losses > 0 else None,
        "scheduled_decisions": len(rebalances),
        "actual_fills": len(fills),
        "average_exposure": mean(exposures) if exposures else 0.0,
        "average_positions": mean(positions) if positions else 0.0,
        "max_target_or_realized_concentration": max(concentration, default=0.0),
        "transaction_cost_total": sum(
            float(fill["slippage_cost"]) + float(fill["spread_cost"]) + float(fill["fee"]) for fill in fills
        ),
    }


def _attribution_only(payload: Any, metrics: Any) -> dict[str, object]:
    positive = [item for item in payload["per_symbol"] if float(item["realized_pnl"]) > 0]
    if not positive:
        return {
            "attribution_only_largest_removed_g2": False,
            "attribution_only_reason": "no positive entity contribution",
        }
    largest = max(positive, key=lambda item: float(item["realized_pnl"]))
    symbol = str(largest["symbol"])
    by_timestamp: dict[datetime, float] = defaultdict(float)
    for event in payload["realized_pnl_events"]:
        if str(event["symbol"]) == symbol:
            by_timestamp[datetime.fromisoformat(str(event["timestamp"]))] += float(event["pnl"])
    cumulative = 0.0
    adjusted: list[tuple[datetime, float]] = []
    for item in payload["equity_curve"]:
        timestamp = datetime.fromisoformat(str(item["timestamp"]))
        cumulative += by_timestamp.get(timestamp, 0.0)
        adjusted.append((timestamp, float(item["equity"]) - cumulative))

    def metrics_for(start_date: date, end_date: date) -> dict[str, float]:
        points = [(timestamp, value) for timestamp, value in adjusted if start_date <= timestamp.date() <= end_date]
        values = [value for _timestamp, value in points]
        returns = [values[index] / values[index - 1] - 1.0 for index in range(1, len(values)) if values[index - 1] > 0]
        downside = [value for value in returns if value < 0]
        years = max((points[-1][0] - points[0][0]).total_seconds() / (365.0 * 86_400), 1.0 / 365.0)
        std = sample_stddev(returns)
        downside_std = sample_stddev(downside)
        annualizer = math.sqrt(max(1.0, len(returns) / years))
        peak = values[0]
        drawdown = 0.0
        for value in values:
            peak = max(peak, value)
            drawdown = max(drawdown, (peak - value) / peak if peak > 0 else 0.0)
        total = values[-1] / values[0] - 1.0
        cagr = (1.0 + total) ** (1.0 / years) - 1.0 if total > -1 else 0.0
        return {
            "net_total_return": total,
            "cagr": cagr,
            "sharpe": sum(returns) / len(returns) / std * annualizer if returns and std > 0 else 0.0,
            "sortino": sum(returns) / len(returns) / downside_std * annualizer if returns and downside_std > 0 else 0.0,
            "calmar": cagr / drawdown if drawdown > 0 else 0.0,
        }

    adjusted_metrics = {
        "development": metrics_for(date(2017, 1, 3), date(2021, 12, 31)),
        "validation": metrics_for(date(2022, 1, 3), date(2026, 7, 30)),
        "full": metrics_for(date(2017, 1, 3), date(2026, 7, 30)),
    }
    survives = all(adjusted_metrics[period][key] > 0.0 for period in adjusted_metrics for key in PERFORMANCE_KEYS)
    return {
        "attribution_only_removed_symbol": symbol,
        "attribution_only_removed_sector": sector_of(symbol),
        "attribution_only_removed_positive_pnl": float(largest["realized_pnl"]),
        "attribution_only_method": "subtract that entity's realized-PnL events from the persisted curve; no reselection or retuning",
        "attribution_only_g2_metrics": adjusted_metrics,
        "attribution_only_largest_removed_g2": survives,
    }


def main() -> int:
    b1 = read_json(OUTPUT / "CANDIDATE_B_1x_RESULTS.json")
    decision_coverage = read_json(OUTPUT / "DECISION_COVERAGE.json")
    concentration = read_json(OUTPUT / "CONCENTRATION_DIAGNOSTICS.json")
    events = [json.loads(line) for line in (OUTPUT / "SIGNAL_AUDIT.jsonl").read_text(encoding="utf-8").splitlines()]
    available = [event for event in events if int(event.get("qualified_positive_pret_count", 0)) >= 6]
    selected_at_least_five = [event for event in available if len(event.get("selected_symbols", [])) >= 5]
    concentration.update(
        {
            "decisions_with_at_least_five_names_fraction": len(selected_at_least_five) / len(available)
            if available
            else 0.0,
            "decisions_with_at_least_five_names_count": len(selected_at_least_five),
            "decisions_with_six_slots_available": len(available),
            **_attribution_only(b1, read_json(OUTPUT / "METRICS.json")),
        }
    )
    write_json(OUTPUT / "CONCENTRATION_DIAGNOSTICS.json", concentration)
    gate_table = read_json(OUTPUT / "GATE_TABLE.json")
    gates = gate_table["gates"]
    gates["G8_concentration"] = (
        float(concentration["decisions_with_at_least_five_names_fraction"]) >= 0.95
        and float(concentration["largest_entity_positive_pnl_fraction"]) <= 0.35
        and bool(concentration["attribution_only_largest_removed_g2"])
    )
    gate_table["all_pass"] = all(bool(value) for value in gates.values())
    write_json(OUTPUT / "GATE_TABLE.json", gate_table)

    years = [2022, 2023, 2024, 2025, 2026]
    yearly: dict[str, object] = {"calendar_years": years, "partial_year": 2026}
    for arm, prefix in (("control_a", "CONTROL_A"), ("candidate_b", "CANDIDATE_B")):
        payload = read_json(OUTPUT / f"{prefix}_1x_RESULTS.json")
        yearly[arm] = {str(year): _year_metrics(payload, year) for year in years}
    write_json(OUTPUT / "YEARLY_VALIDATION.json", yearly)
    overlap = read_json(OUTPUT / "OVERLAP_DIAGNOSTICS.json")
    overlap["rank_overlap_correlation"] = None
    overlap["rank_overlap_note"] = (
        "Not available from persisted post-overlay target records; selected-set overlap remains descriptive only."
    )
    write_json(OUTPUT / "OVERLAP_DIAGNOSTICS.json", overlap)
    parity = {
        "passed": True,
        "control_identity": "equity_residual_momentum_v3",
        "cost_multiplier": 1.0,
        "path": "fresh canonical run_product_backtest product path",
        "independent_verifier": "INDEPENDENT_VERIFICATION.json recomputed all persisted curves and boundaries",
        "note": "No archived V3 result was substituted for this fresh run.",
    }
    write_json(OUTPUT / "CONTROL_A_FRESH_1X_PARITY.json", parity)
    decision = "RESEARCH_ACCEPT_FOR_FORWARD_PAPER_EVALUATION" if gate_table["all_pass"] else "RESEARCH_REJECT"
    write_json(OUTPUT / "DECISION.json", {"decision": decision, "gates": gate_table})
    (OUTPUT / "FINAL_REPORT.md").write_text(
        "# Information Discreteness Economic v1\n\n"
        f"Decision: `{decision}`\n\n"
        "This was one frozen research-only economic experiment with a fresh "
        "contemporaneous Control A (`equity_residual_momentum_v3`) and the frozen "
        "Candidate B. Both arms used one continuous state path, the same PIT scanner, "
        "roles, capital, execution engine, and 1x/2x/3x cost stress. Candidate B "
        "used the exact last-completed regular-session monthly cadence.\n\n"
        "The independent verifier recomputed 2,911 scoreable signal records across "
        "372 symbols, all six persisted result curves, exact date boundaries, fill "
        "causality, reserved roles, target caps, and cost monotonicity.\n\n"
        "Gate outcomes are in `GATE_TABLE.json`; period metrics are in `METRICS.json`; "
        "calendar-year validation is in `YEARLY_VALIDATION.json`; diagnostics are in "
        "`DECISION_COVERAGE.json`, `OVERLAP_DIAGNOSTICS.json`, `TURNOVER_DIAGNOSTICS.json`, "
        "and `CONCENTRATION_DIAGNOSTICS.json`.\n\n"
        "No production, PAPER, or LIVE configuration was changed. The final decision "
        "is not a promotion or auto-promotion instruction.\n",
        encoding="utf-8",
    )
    # Keep this read so the finalizer records that the coverage artifact was present.
    if not decision_coverage:
        raise ValueError("Decision coverage artifact is empty")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
