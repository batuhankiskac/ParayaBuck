"""Deterministic economic and exposure diagnostics for the campaign."""

from __future__ import annotations

import hashlib
import json
import math
import statistics
from collections.abc import Iterable, Sequence
from datetime import datetime
from typing import Any

from parayabuck.backtest.portfolio_engine import PortfolioBacktestResult
from parayabuck.core.models import Bar


def payload_hash(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def linear_percentile(values: Sequence[float], percentile: float) -> float:
    if not values:
        return 0.0
    if not 0 <= percentile <= 100:
        raise ValueError("percentile must be in [0, 100]")
    ordered = sorted(float(value) for value in values)
    position = (len(ordered) - 1) * percentile / 100.0
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    fraction = position - lower
    return ordered[lower] + fraction * (ordered[upper] - ordered[lower])


def _sample_std(values: Sequence[float]) -> float:
    if len(values) < 2:
        return 0.0
    return statistics.stdev(values)


def _drawdown_series(values: Sequence[float], timestamps: Sequence[datetime]) -> list[tuple[datetime, float]]:
    peak = values[0] if values else 0.0
    rows: list[tuple[datetime, float]] = []
    for timestamp, value in zip(timestamps, values, strict=True):
        peak = max(peak, value)
        rows.append((timestamp, (peak - value) / peak if peak > 0 else 0.0))
    return rows


def drawdown_episodes(
    values: Sequence[float], timestamps: Sequence[datetime], *, top_n: int = 5
) -> list[dict[str, object]]:
    """Return completed/open drawdown episodes ordered by depth."""

    if not values:
        return []
    peak_value = values[0]
    peak_timestamp = timestamps[0]
    active: dict[str, object] | None = None
    episodes: list[dict[str, object]] = []
    for timestamp, value in zip(timestamps, values, strict=True):
        if value >= peak_value:
            if active is not None:
                active["recovery_timestamp"] = timestamp.isoformat()
                active["recovered"] = True
                episodes.append(active)
                active = None
            peak_value = value
            peak_timestamp = timestamp
            continue
        drawdown = (peak_value - value) / peak_value if peak_value > 0 else 0.0
        if active is None:
            active = {
                "peak_timestamp": peak_timestamp.isoformat(),
                "peak_equity": peak_value,
                "trough_timestamp": timestamp.isoformat(),
                "trough_equity": value,
                "maximum_drawdown": drawdown,
                "recovery_timestamp": None,
                "recovered": False,
            }
        elif drawdown > float(active["maximum_drawdown"]):
            active["trough_timestamp"] = timestamp.isoformat()
            active["trough_equity"] = value
            active["maximum_drawdown"] = drawdown
        active["last_timestamp"] = timestamp.isoformat()
    if active is not None:
        episodes.append(active)
    episodes.sort(key=lambda row: (-float(row["maximum_drawdown"]), str(row["peak_timestamp"])))
    return episodes[:top_n]


def _period_values(
    result: PortfolioBacktestResult, start: datetime, end: datetime
) -> tuple[list[datetime], list[float], int]:
    timestamps = list(result.curve_timestamps)
    values = list(result.equity_curve)
    indices = [index for index, timestamp in enumerate(timestamps) if start <= timestamp <= end]
    if not indices:
        return [], [], -1
    first = indices[0]
    anchor = first - 1 if first > 0 else first
    selected_indices = [anchor, *indices] if anchor != first else indices
    deduped = list(dict.fromkeys(selected_indices))
    return [timestamps[index] for index in deduped], [values[index] for index in deduped], first


def _monthly_returns(timestamps: Sequence[datetime], values: Sequence[float]) -> list[float]:
    month_end: dict[tuple[int, int], float] = {}
    for timestamp, value in zip(timestamps, values, strict=True):
        month_end[(timestamp.year, timestamp.month)] = value
    returns: list[float] = []
    previous = values[0] if values else 0.0
    for key in sorted(month_end):
        value = month_end[key]
        if previous > 0:
            returns.append(value / previous - 1.0)
        previous = value
    return returns


def _exposure_stats(
    exposure_history: Sequence[tuple[datetime, float, int, float, float]],
    start: datetime,
    end: datetime,
) -> dict[str, float | int]:
    rows = [row for row in exposure_history if start <= row[0] <= end]
    if not rows:
        return {
            "average_equity_exposure": 0.0,
            "median_equity_exposure": 0.0,
            "minimum_equity_exposure": 0.0,
            "maximum_equity_exposure": 0.0,
            "average_positions": 0.0,
            "maximum_positions": 0,
            "maximum_concentration": 0.0,
        }
    exposures = [row[1] for row in rows]
    positions = [row[2] for row in rows]
    concentrations = [row[3] for row in rows]
    return {
        "average_equity_exposure": statistics.fmean(exposures),
        "median_equity_exposure": statistics.median(exposures),
        "minimum_equity_exposure": min(exposures),
        "maximum_equity_exposure": max(exposures),
        "average_positions": statistics.fmean(positions),
        "maximum_positions": max(positions),
        "maximum_concentration": max(concentrations),
    }


def metrics_for_period(
    result: PortfolioBacktestResult,
    exposure_history: Sequence[tuple[datetime, float, int, float, float]],
    *,
    start: datetime,
    end: datetime,
    label: str,
) -> dict[str, object]:
    timestamps, values, first_index = _period_values(result, start, end)
    if not timestamps:
        raise ValueError(f"No equity curve points for {label}")
    returns = [
        values[index] / values[index - 1] - 1.0
        for index in range(1, len(values))
        if values[index - 1] > 0 and values[index] > 0
    ]
    duration_days = max(0, int((timestamps[-1] - timestamps[0]).total_seconds() / 86_400))
    years = duration_days / 365.0
    total_return = values[-1] / values[0] - 1.0 if values[0] > 0 else 0.0
    cagr = (1.0 + total_return) ** (1.0 / years) - 1.0 if years > 0 and total_return > -1 else 0.0
    mean_return = statistics.fmean(returns) if returns else 0.0
    std = _sample_std(returns)
    downside = [value for value in returns if value < 0]
    downside_std = _sample_std(downside)
    periods_per_year = len(returns) / years if years > 0 else 0.0
    annualizer = math.sqrt(max(1.0, periods_per_year)) if returns else 1.0
    sharpe = mean_return / std * annualizer if std > 0 else 0.0
    sortino = mean_return / downside_std * annualizer if downside_std > 0 else 0.0
    dd = _drawdown_series(values, timestamps)
    maximum_drawdown = max((row[1] for row in dd), default=0.0)
    dd_duration = 0
    peak_timestamp = timestamps[0]
    peak_value = values[0]
    for timestamp, value in zip(timestamps, values, strict=True):
        if value >= peak_value:
            peak_value = value
            peak_timestamp = timestamp
        elif peak_value > 0:
            dd_duration = max(dd_duration, int((timestamp - peak_timestamp).total_seconds() / 86_400))
    exposure = _exposure_stats(exposure_history, start, end)
    fills = [fill for fill in result.fills if start <= fill.timestamp <= end]
    decisions = [row for row in result.rebalances if start <= row.decision_timestamp <= end]
    slippage_costs = sum(fill.slippage_cost for fill in fills)
    spread_costs = sum(fill.spread_cost for fill in fills)
    fee_costs = sum(fill.fee for fill in fills)
    # Final-bar liquidation costs are engine events rather than fills.  Assign
    # them to a period only when that period contains the event timestamp; in
    # particular, validation must not inherit development-period costs.
    for event in result.terminal_events:
        event_timestamp = datetime.fromisoformat(str(event["timestamp"]))
        if start <= event_timestamp <= end:
            slippage_costs += float(event.get("slippage_cost", 0.0))
            spread_costs += float(event.get("spread_cost", 0.0))
            fee_costs += float(event.get("fee", 0.0))
    costs = slippage_costs + spread_costs + fee_costs
    closed = [
        pnl
        for timestamp, _symbol, pnl in result.closed_position_events
        if start <= timestamp <= end
    ]
    wins = sum(pnl for pnl in closed if pnl > 0)
    losses = abs(sum(pnl for pnl in closed if pnl < 0))
    monthly = _monthly_returns(timestamps, values)
    payload: dict[str, object] = {
        "label": label,
        "start_timestamp": timestamps[0].isoformat(),
        "end_timestamp": timestamps[-1].isoformat(),
        "starting_equity": values[0],
        "ending_equity": values[-1],
        "net_total_return": total_return,
        "total_return": total_return,
        "duration_days": duration_days,
        "cagr": cagr,
        "annualized_volatility": std * annualizer if std > 0 else 0.0,
        "sharpe": sharpe,
        "sortino": sortino,
        "max_drawdown": maximum_drawdown,
        "longest_drawdown_days": dd_duration,
        "calmar": cagr / maximum_drawdown if maximum_drawdown > 0 else 0.0,
        "worst_month": min(monthly, default=0.0),
        "profit_factor": wins / losses if losses > 0 else None,
        "turnover": (
            sum(fill.qty * fill.reference_price for fill in fills)
            / (statistics.fmean(values) if values else 1.0)
        ),
        "transaction_costs": costs,
        "slippage_costs": slippage_costs,
        "spread_costs": spread_costs,
        "regulatory_and_commission_fees": fee_costs,
        "fill_count": len(fills),
        "decision_count": len(decisions),
        "closed_position_count": len(closed),
        **exposure,
    }
    return payload


def full_metrics(
    result: PortfolioBacktestResult,
    exposure_history: Sequence[tuple[datetime, float, int, float, float]],
) -> dict[str, object]:
    return metrics_for_period(
        result,
        exposure_history,
        start=result.curve_timestamps[0],
        end=result.curve_timestamps[-1],
        label="full",
    )


def capture_diagnostics(
    control: PortfolioBacktestResult,
    treatment: PortfolioBacktestResult,
) -> dict[str, object]:
    def daily_curve(result: PortfolioBacktestResult) -> dict[tuple[int, int, int], float]:
        by_day: dict[tuple[int, int, int], float] = {}
        for timestamp, value in zip(result.curve_timestamps, result.equity_curve, strict=True):
            by_day[(timestamp.year, timestamp.month, timestamp.day)] = value
        return by_day

    def daily_spy(result: PortfolioBacktestResult) -> dict[tuple[int, int, int], float]:
        by_day: dict[tuple[int, int, int], float] = {}
        for bar in result.benchmark_bars:
            by_day[(bar.timestamp.year, bar.timestamp.month, bar.timestamp.day)] = bar.close
        return by_day

    a_curve = daily_curve(control)
    b_curve = daily_curve(treatment)
    spy = daily_spy(control)
    days = sorted(set(a_curve) & set(b_curve) & set(spy))
    up: list[tuple[float, float, float]] = []
    down: list[tuple[float, float, float]] = []
    for previous, current in zip(days, days[1:], strict=False):
        if previous not in spy or current not in spy or previous not in a_curve or current not in a_curve:
            continue
        spy_return = spy[current] / spy[previous] - 1.0 if spy[previous] > 0 else 0.0
        a_return = a_curve[current] / a_curve[previous] - 1.0 if a_curve[previous] > 0 else 0.0
        b_return = b_curve[current] / b_curve[previous] - 1.0 if b_curve[previous] > 0 else 0.0
        (up if spy_return >= 0 else down).append((a_return, b_return, spy_return))

    def summarize(rows: Sequence[tuple[float, float, float]]) -> dict[str, object]:
        a = [row[0] for row in rows]
        b = [row[1] for row in rows]
        return {
            "observations": len(rows),
            "control_mean_return": statistics.fmean(a) if a else 0.0,
            "treatment_mean_return": statistics.fmean(b) if b else 0.0,
            "treatment_minus_control": statistics.fmean([x - y for x, y in zip(b, a, strict=True)]) if a else 0.0,
            "treatment_to_control_capture": (
                statistics.fmean(b) / statistics.fmean(a) if a and statistics.fmean(a) != 0 else None
            ),
            "mean_spy_return": statistics.fmean([row[2] for row in rows]) if rows else 0.0,
        }

    return {"up_market": summarize(up), "down_market": summarize(down)}


def exposure_timing_diagnostics(
    control: PortfolioBacktestResult,
    treatment: PortfolioBacktestResult,
    audit_rows: Sequence[dict[str, object]],
) -> dict[str, object]:
    a_curve = {timestamp: value for timestamp, value in zip(control.curve_timestamps, control.equity_curve, strict=True)}
    b_curve = {timestamp: value for timestamp, value in zip(treatment.curve_timestamps, treatment.equity_curve, strict=True)}
    rows = sorted(audit_rows, key=lambda row: str(row["decision_timestamp"]))
    intervals: list[dict[str, object]] = []
    for index, row in enumerate(rows):
        current = datetime.fromisoformat(str(row["decision_timestamp"]))
        next_ts = (
            datetime.fromisoformat(str(rows[index + 1]["decision_timestamp"]))
            if index + 1 < len(rows)
            else treatment.curve_timestamps[-1]
        )
        if current not in a_curve or current not in b_curve or next_ts not in a_curve or next_ts not in b_curve:
            continue
        a_return = a_curve[next_ts] / a_curve[current] - 1.0 if a_curve[current] > 0 else 0.0
        b_return = b_curve[next_ts] / b_curve[current] - 1.0 if b_curve[current] > 0 else 0.0
        scale = float(dict(row["scaler"])["scale"])
        intervals.append(
            {
                "decision_timestamp": current.isoformat(),
                "next_decision_timestamp": next_ts.isoformat(),
                "scale": scale,
                "control_return": a_return,
                "treatment_return": b_return,
                "treatment_minus_control": b_return - a_return,
                "classification": "reduced_exposure" if scale < 1.0 else "normal_or_low_vol",
            }
        )
    normal = [row for row in intervals if row["classification"] == "normal_or_low_vol"]
    reduced = [row for row in intervals if row["classification"] == "reduced_exposure"]

    def mean_delta(rows: Sequence[dict[str, object]]) -> float:
        return statistics.fmean(float(row["treatment_minus_control"]) for row in rows) if rows else 0.0

    return {
        "weekly_intervals": intervals,
        "normal_or_low_vol": {
            "count": len(normal),
            "mean_treatment_minus_control": mean_delta(normal),
            "mean_control_return": statistics.fmean(float(row["control_return"]) for row in normal) if normal else 0.0,
        },
        "elevated_vol_reduced_exposure": {
            "count": len(reduced),
            "mean_treatment_minus_control": mean_delta(reduced),
            "mean_control_return": statistics.fmean(float(row["control_return"]) for row in reduced) if reduced else 0.0,
            "negative_control_count": sum(1 for row in reduced if float(row["control_return"]) < 0),
            "mean_delta_on_negative_control_weeks": mean_delta(
                [row for row in reduced if float(row["control_return"]) < 0]
            ),
        },
    }


__all__ = [
    "capture_diagnostics",
    "drawdown_episodes",
    "exposure_timing_diagnostics",
    "full_metrics",
    "linear_percentile",
    "metrics_for_period",
    "payload_hash",
]
