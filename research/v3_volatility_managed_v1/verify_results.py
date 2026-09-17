"""Independent, receipt-driven verifier for the volatility campaign.

The verifier deliberately does not import the candidate strategy or the
campaign metric helpers.  It replays the frozen scaler formula and recomputes
the reported metrics from serialized execution rows before declaring the run
valid.
"""

from __future__ import annotations

import bisect
import csv
import hashlib
import json
import math
import statistics
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Sequence


RESEARCH_DIR = Path(__file__).resolve().parent
ROOT = RESEARCH_DIR.parents[1]
DATA_DIR = ROOT / "data" / "historical"
CONFIG_PATH = ROOT / "config" / "settings.yaml"
BAR_PATH = DATA_DIR / "equity_pit_symmetric_2016_2026_v5.csv"
LIFETIME_PATH = DATA_DIR / "equity_entity_lifetimes_full_v5.csv"
ACTION_PATH = DATA_DIR / "equity_survivorship_full_corporate_actions_v5.csv"
PROTOCOL_PATH = RESEARCH_DIR / "FROZEN_PROTOCOL.md"

EXPECTED_MAIN_SHA = "5175a58b2fb4084e5c5a00854d3c3c94b0e819a1"
EXPECTED_BAR_SHA = "bb7aaae86bc1909c6b0a1094a59430f5ed9900bb3ec63891bbdcda8cda055139"
EXPECTED_LIFETIME_SHA = "ed675d9b6ba7f173b65ae2b5272ffb62e09d6a007ed36ea22d3aba7a36774596"
EXPECTED_ACTION_SHA = "a0a60755a3f6adfc06a5ef0e6ed82852e1584c20fdb62cefed92b765dc9adc23"
FULL_START = datetime(2017, 1, 3, tzinfo=UTC)
DEV_START = datetime(2017, 1, 3, tzinfo=UTC)
DEV_END = datetime(2021, 12, 31, 23, 59, 59, 999999, tzinfo=UTC)
VAL_START = datetime(2022, 1, 3, tzinfo=UTC)
VAL_END = datetime(2026, 7, 31, 23, 59, 59, 999999, tzinfo=UTC)
RISK_METRICS = ("sharpe", "sortino", "calmar")
ARMS = ("A_control", "B_treatment", "C_static_comparator")


def _load(name: str) -> Any:
    return json.loads((RESEARCH_DIR / name).read_text(encoding="utf-8"))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, check=True, capture_output=True, text=True).stdout.strip()


def _close(left: object, right: object, tolerance: float = 1e-8) -> bool:
    if left is None or right is None:
        return left is right
    try:
        left_float = float(left)
        right_float = float(right)
    except (TypeError, ValueError):
        return left == right
    return math.isfinite(left_float) and math.isfinite(right_float) and abs(left_float - right_float) <= tolerance * max(
        1.0, abs(left_float), abs(right_float)
    )


def _iso(raw: object) -> datetime:
    return datetime.fromisoformat(str(raw))


def _sample_std(values: Sequence[float]) -> float:
    return statistics.stdev(values) if len(values) >= 2 else 0.0


def _period_values(execution: dict[str, object], start: datetime, end: datetime) -> tuple[list[datetime], list[float]]:
    curve = execution["equity_curve"]
    timestamps = [_iso(row["timestamp"]) for row in curve]
    values = [float(row["equity"]) for row in curve]
    indices = [index for index, timestamp in enumerate(timestamps) if start <= timestamp <= end]
    if not indices:
        return [], []
    first = indices[0]
    anchor = first - 1 if first > 0 else first
    selected = list(dict.fromkeys([anchor, *indices]))
    return [timestamps[index] for index in selected], [values[index] for index in selected]


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


def _exposure_metrics(execution: dict[str, object], start: datetime, end: datetime) -> dict[str, object]:
    rows = [row for row in execution["exposure_history"] if start <= _iso(row["timestamp"]) <= end]
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
    exposures = [float(row["gross_exposure"]) for row in rows]
    positions = [int(row["position_count"]) for row in rows]
    concentrations = [float(row["maximum_concentration"]) for row in rows]
    return {
        "average_equity_exposure": statistics.fmean(exposures),
        "median_equity_exposure": statistics.median(exposures),
        "minimum_equity_exposure": min(exposures),
        "maximum_equity_exposure": max(exposures),
        "average_positions": statistics.fmean(positions),
        "maximum_positions": max(positions),
        "maximum_concentration": max(concentrations),
    }


def recompute_metrics(execution: dict[str, object], *, start: datetime, end: datetime, label: str) -> dict[str, object]:
    timestamps, values = _period_values(execution, start, end)
    if not timestamps:
        raise ValueError(f"No curve points for {label}")
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
    standard_deviation = _sample_std(returns)
    downside = [value for value in returns if value < 0]
    downside_deviation = _sample_std(downside)
    periods_per_year = len(returns) / years if years > 0 else 0.0
    annualizer = math.sqrt(max(1.0, periods_per_year)) if returns else 1.0
    sharpe = mean_return / standard_deviation * annualizer if standard_deviation > 0 else 0.0
    sortino = mean_return / downside_deviation * annualizer if downside_deviation > 0 else 0.0
    peak = values[0] if values else 0.0
    maximum_drawdown = 0.0
    for value in values:
        peak = max(peak, value)
        maximum_drawdown = max(maximum_drawdown, (peak - value) / peak if peak > 0 else 0.0)
    drawdown_duration = 0
    peak_timestamp = timestamps[0]
    peak_value = values[0]
    for timestamp, value in zip(timestamps, values, strict=True):
        if value >= peak_value:
            peak_value = value
            peak_timestamp = timestamp
        elif peak_value > 0:
            drawdown_duration = max(drawdown_duration, int((timestamp - peak_timestamp).total_seconds() / 86_400))
    fills = [fill for fill in execution["fills"] if start <= _iso(fill["timestamp"]) <= end]
    decisions = [row for row in execution["rebalances"] if start <= _iso(row["decision_timestamp"]) <= end]
    slippage = sum(float(fill["slippage_cost"]) for fill in fills)
    spread = sum(float(fill["spread_cost"]) for fill in fills)
    fees = sum(float(fill["fee"]) for fill in fills)
    for event in execution["terminal_events"]:
        event_timestamp = _iso(event["timestamp"])
        if start <= event_timestamp <= end:
            slippage += float(event.get("slippage_cost", 0.0))
            spread += float(event.get("spread_cost", 0.0))
            fees += float(event.get("fee", 0.0))
    closed = [
        float(row["pnl"])
        for row in execution["closed_position_events"]
        if start <= _iso(row["timestamp"]) <= end
    ]
    wins = sum(value for value in closed if value > 0)
    losses = abs(sum(value for value in closed if value < 0))
    monthly = _monthly_returns(timestamps, values)
    exposure = _exposure_metrics(execution, start, end)
    return {
        "label": label,
        "start_timestamp": timestamps[0].isoformat(),
        "end_timestamp": timestamps[-1].isoformat(),
        "starting_equity": values[0],
        "ending_equity": values[-1],
        "net_total_return": total_return,
        "total_return": total_return,
        "duration_days": duration_days,
        "cagr": cagr,
        "annualized_volatility": standard_deviation * annualizer if standard_deviation > 0 else 0.0,
        "sharpe": sharpe,
        "sortino": sortino,
        "max_drawdown": maximum_drawdown,
        "longest_drawdown_days": drawdown_duration,
        "calmar": cagr / maximum_drawdown if maximum_drawdown > 0 else 0.0,
        "worst_month": min(monthly, default=0.0),
        "profit_factor": wins / losses if losses > 0 else None,
        "turnover": sum(float(fill["qty"]) * float(fill["reference_price"]) for fill in fills)
        / (statistics.fmean(values) if values else 1.0),
        "transaction_costs": slippage + spread + fees,
        "slippage_costs": slippage,
        "spread_costs": spread,
        "regulatory_and_commission_fees": fees,
        "fill_count": len(fills),
        "decision_count": len(decisions),
        "closed_position_count": len(closed),
        **exposure,
    }


def _compare_metrics(reported: dict[str, object], execution: dict[str, object], errors: list[str]) -> None:
    curve = execution["equity_curve"]
    first = _iso(curve[0]["timestamp"])
    last = _iso(curve[-1]["timestamp"])
    periods = {
        "development": (DEV_START, DEV_END),
        "validation": (VAL_START, VAL_END),
        "full": (first, last),
    }
    for label, (start, end) in periods.items():
        try:
            expected = recompute_metrics(execution, start=start, end=end, label=label)
        except Exception as exc:  # pragma: no cover - receipt failure path
            errors.append(f"metric replay {label}: {exc}")
            continue
        for key, value in expected.items():
            if key not in reported[label] or not _close(reported[label][key], value):
                errors.append(f"metric mismatch {label}.{key}: reported={reported[label].get(key)!r} expected={value!r}")


def _volatility_series(spy_rows: Sequence[dict[str, object]]) -> list[tuple[datetime, float]]:
    ordered = sorted(spy_rows, key=lambda row: _iso(row["timestamp"]))
    timestamps = [_iso(row["timestamp"]) for row in ordered]
    closes = [float(row["close"]) for row in ordered]
    result: list[tuple[datetime, float]] = []
    for index in range(21, len(closes)):
        returns = [math.log(closes[offset] / closes[offset - 1]) for offset in range(index - 20, index + 1)]
        result.append((timestamps[index], _sample_std(returns) * math.sqrt(252.0)))
    return result


def _check_scaler(errors: list[str]) -> dict[str, object]:
    rows = _load("scaler_time_series.json")
    observations = _load("volatility_observations.json")
    spy_rows = _load("spy_signal_bars.json")
    reconstructed = _volatility_series(spy_rows)
    stored_observations = [(_iso(row["timestamp"]), float(row["vol21"])) for row in observations]
    if len(reconstructed) != len(stored_observations):
        errors.append(f"volatility observation count mismatch: {len(stored_observations)} != {len(reconstructed)}")
    for index, (expected, stored) in enumerate(zip(reconstructed, stored_observations, strict=False)):
        if expected[0] != stored[0] or not _close(expected[1], stored[1], 1e-10):
            errors.append(f"VOL21 mismatch at index {index}")
            break
    timestamps = [timestamp for timestamp, _value in stored_observations]
    for row in rows:
        decision = _iso(row["decision_timestamp"])
        index = bisect.bisect_right(timestamps, decision) - 1
        baseline = [] if index < 0 else stored_observations[max(0, index - 252) : index]
        expected_count = len(baseline)
        if int(row["baseline_count"]) != expected_count:
            errors.append(f"baseline count mismatch at {decision.isoformat()}")
        if index < 0:
            expected_scale = 1.0
            expected_current_timestamp = None
            expected_current_vol = None
            expected_basevol = None
        else:
            expected_current_timestamp, expected_current_vol = stored_observations[index]
            expected_basevol = statistics.median(value for _timestamp, value in baseline) if len(baseline) == 252 else None
            expected_scale = min(1.0, expected_basevol / expected_current_vol) if expected_basevol and expected_current_vol > 0 else 1.0
            if expected_current_timestamp > decision:
                errors.append(f"future VOL21 observation used at {decision.isoformat()}")
            if baseline and baseline[-1][0] >= expected_current_timestamp:
                errors.append(f"current VOL21 was not excluded from baseline at {decision.isoformat()}")
        if row["current_vol_timestamp"] is not None and _iso(row["current_vol_timestamp"]) != expected_current_timestamp:
            errors.append(f"current VOL21 timestamp mismatch at {decision.isoformat()}")
        if expected_current_vol is None:
            if row["vol21"] is not None:
                errors.append(f"unexpected VOL21 value at {decision.isoformat()}")
        elif not _close(row["vol21"], expected_current_vol, 1e-10):
            errors.append(f"current VOL21 mismatch at {decision.isoformat()}")
        if expected_basevol is None:
            if row["basevol"] is not None:
                errors.append(f"unexpected BASEVOL value at {decision.isoformat()}")
        elif not _close(row["basevol"], expected_basevol, 1e-10):
            errors.append(f"BASEVOL mismatch at {decision.isoformat()}")
        if not _close(row["scale"], expected_scale, 1e-10):
            errors.append(f"scale mismatch at {decision.isoformat()}")
        if not 0.0 <= float(row["scale"]) <= 1.0:
            errors.append(f"scale outside [0,1] at {decision.isoformat()}")
    return {"schedule_rows": len(rows), "volatility_rows": len(reconstructed), "formula_replay": not errors}


def _read_lifecycles() -> dict[str, datetime]:
    result: dict[str, datetime] = {}
    with LIFETIME_PATH.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            if str(row.get("current_status")) == "inactive":
                result[str(row["canonical_symbol"]).upper()] = datetime.fromisoformat(str(row["last_observed_bar"]))
    return result


def _check_execution(
    payload: dict[str, object],
    arm: str,
    cost_key: str,
    execution_bars: dict[str, object],
    scaler_by_decision: dict[datetime, float],
    static_allocation: float,
    lifecycles: dict[str, datetime],
    errors: list[str],
) -> None:
    execution = payload["execution"]
    curve = execution["equity_curve"]
    timestamps = [_iso(row["timestamp"]) for row in curve]
    values = [float(row["equity"]) for row in curve]
    if timestamps != sorted(set(timestamps)):
        errors.append(f"{arm}/{cost_key}: equity curve timestamps are not unique ascending")
    if any(value <= 0 for value in values):
        errors.append(f"{arm}/{cost_key}: non-positive NAV")
    if values and not _close(values[-1], execution["ending_equity"]):
        errors.append(f"{arm}/{cost_key}: ending equity does not match curve")
    decisions = {_iso(row["decision_timestamp"]) for row in execution["rebalances"]}
    for fill in execution["fills"]:
        fill_timestamp = _iso(fill["timestamp"])
        decision_timestamp = _iso(fill["decision_timestamp"])
        if fill_timestamp <= decision_timestamp:
            errors.append(f"{arm}/{cost_key}: fill is not strictly after its decision")
        if decision_timestamp not in decisions:
            errors.append(f"{arm}/{cost_key}: fill references unknown decision")
        key = f"{fill['symbol']}|{fill_timestamp.isoformat()}"
        receipt = execution_bars.get(key)
        if receipt is None:
            errors.append(f"{arm}/{cost_key}: missing execution-bar receipt {key}")
        else:
            if not _close(fill["reference_price"], receipt["open"], 1e-10):
                errors.append(f"{arm}/{cost_key}: fill reference price is not next-bar open for {key}")
            first_later = receipt.get("first_later_timestamp")
            if first_later is not None and _iso(first_later) != fill_timestamp:
                errors.append(f"{arm}/{cost_key}: fill did not use first later bar for {key}")
    fill_slippage = sum(float(fill["slippage_cost"]) for fill in execution["fills"])
    fill_spread = sum(float(fill["spread_cost"]) for fill in execution["fills"])
    fill_fees = sum(float(fill["fee"]) for fill in execution["fills"])
    terminal_slippage = sum(float(event.get("slippage_cost", 0.0)) for event in execution["terminal_events"])
    terminal_spread = sum(float(event.get("spread_cost", 0.0)) for event in execution["terminal_events"])
    terminal_fees = sum(float(event.get("fee", 0.0)) for event in execution["terminal_events"])
    if not _close(execution["slippage_cost_total"], fill_slippage + terminal_slippage):
        errors.append(f"{arm}/{cost_key}: slippage total mismatch")
    if not _close(execution["spread_cost_total"], fill_spread + terminal_spread):
        errors.append(f"{arm}/{cost_key}: spread total mismatch")
    if not _close(execution["fee_total"], fill_fees + terminal_fees):
        errors.append(f"{arm}/{cost_key}: fee total mismatch")
    for row in execution["exposure_history"]:
        if float(row["gross_exposure"]) > 0.60 + 1e-7:
            errors.append(f"{arm}/{cost_key}: gross exposure cap exceeded")
        if int(row["position_count"]) > 6:
            errors.append(f"{arm}/{cost_key}: position count cap exceeded")
        if float(row["maximum_concentration"]) < -1e-9 or not math.isfinite(float(row["maximum_concentration"])):
            errors.append(f"{arm}/{cost_key}: invalid realized concentration diagnostic")
        if float(row["cash_ratio"]) < 0.40 - 1e-7:
            errors.append(f"{arm}/{cost_key}: cash floor exceeded")
    for record in execution["rebalances"]:
        target_weights = {str(symbol): float(weight) for symbol, weight in record["target_weights"]}
        if sum(target_weights.values()) + float(record["target_cash_weight"]) > 1.0 + 1e-7:
            errors.append(f"{arm}/{cost_key}: target weights exceed NAV")
        if len(target_weights) > 6 or any(weight > 0.10 + 1e-7 for weight in target_weights.values()):
            errors.append(f"{arm}/{cost_key}: target slot or asset cap exceeded")
        gross = sum(target_weights.values())
        decision = _iso(record["decision_timestamp"])
        if arm == "B_treatment":
            maximum = 0.55 * scaler_by_decision.get(decision, 1.0)
        elif arm == "C_static_comparator":
            maximum = static_allocation
        else:
            maximum = 0.55
        if gross > maximum + 1e-6:
            errors.append(f"{arm}/{cost_key}: managed allocation cap exceeded at {decision.isoformat()}")
    for event in execution["terminal_events"]:
        symbol = str(event["symbol"]).upper()
        event_timestamp = _iso(event["timestamp"])
        if event.get("terminal_event_class") is not None:
            expected = lifecycles.get(symbol)
            if expected is None or expected != event_timestamp:
                errors.append(f"{arm}/{cost_key}: terminal lifecycle event mismatch for {symbol}")


def _coverage(execution: dict[str, object], schedule_rows: Sequence[dict[str, object]]) -> dict[str, object]:
    schedule = [_iso(row["decision_timestamp"]) for row in schedule_rows]
    eligible = {
        timestamp for timestamp, row in zip(schedule, schedule_rows, strict=True) if bool(row["eligible"])
    }
    actual = {_iso(row["decision_timestamp"]) for row in execution["rebalances"]}

    def period(start: datetime, end: datetime) -> dict[str, object]:
        expected = [timestamp for timestamp in schedule if start <= timestamp <= end and timestamp in eligible]
        observed = [timestamp for timestamp in expected if timestamp in actual]
        return {
            "eligible_weeks": len(expected),
            "observed_decisions": len(observed),
            "coverage_ratio": len(observed) / len(expected) if expected else 0.0,
            "first_observed": min(observed).isoformat() if observed else None,
            "last_observed": max(observed).isoformat() if observed else None,
        }

    longest = 0
    run = 0
    for timestamp in sorted(eligible):
        if timestamp in actual:
            run = 0
        else:
            run += 1
            longest = max(longest, run)
    development = period(DEV_START, DEV_END)
    validation = period(VAL_START, VAL_END)
    missing = sorted(eligible - actual)
    unexpected = sorted(actual - set(schedule))
    return {
        "schedule_weeks": len(schedule),
        "eligible_weeks": len(eligible),
        "observed_decisions": len(actual & eligible),
        "coverage_ratio": len(actual & eligible) / len(eligible) if eligible else 0.0,
        "unexpected_decisions": [timestamp.isoformat() for timestamp in unexpected],
        "missing_eligible_decisions": [timestamp.isoformat() for timestamp in missing],
        "development": development,
        "validation": validation,
        "longest_unexplained_gap_weeks": longest,
        "pass": (
            development["coverage_ratio"] >= 0.90
            and validation["coverage_ratio"] >= 0.90
            and longest <= 8
            and not unexpected
        ),
    }


def _independent_gates(
    metrics: dict[str, dict[str, object]],
    stress: dict[str, dict[str, dict[str, object]]],
    coverage: dict[str, dict[str, object]],
    structural_valid: bool,
    no_new: bool,
    no_promotion: bool,
) -> dict[str, dict[str, object]]:
    a_dev = metrics["A_control"]["development"]
    b_dev = metrics["B_treatment"]["development"]
    c_dev = metrics["C_static_comparator"]["development"]
    a_full = metrics["A_control"]["full"]
    b_full = metrics["B_treatment"]["full"]
    c_full = metrics["C_static_comparator"]["full"]
    a_val = metrics["A_control"]["validation"]
    b_val = metrics["B_treatment"]["validation"]
    improvements_a = {metric: float(b_full[metric]) - float(a_full[metric]) for metric in RISK_METRICS}
    improvements_c = {metric: float(b_full[metric]) - float(c_full[metric]) for metric in RISK_METRICS}
    a_2x = stress["A_control"]["2x"]["metrics"]["full"]
    b_1x = metrics["B_treatment"]["full"]
    b_2x = stress["B_treatment"]["2x"]["metrics"]["full"]
    c_2x = stress["C_static_comparator"]["2x"]["metrics"]["full"]
    b_3x = stress["B_treatment"]["3x"]["metrics"]["full"]
    return {
        "gate_1": {"pass": structural_valid and all(bool(row["pass"]) for row in coverage.values())},
        "gate_2": {
            "pass": sum(value > 0.05 for value in improvements_a.values()) >= 2
            and all(value >= -0.03 for value in improvements_a.values())
        },
        "gate_3": {
            "pass": float(b_full["max_drawdown"]) <= float(a_full["max_drawdown"]) - 0.02
            and float(b_full["max_drawdown"]) <= float(a_full["max_drawdown"]) * 0.90
        },
        "gate_4": {"pass": float(a_full["cagr"]) - float(b_full["cagr"]) <= 0.01},
        "gate_5": {
            "pass": sum(value > 0 for value in improvements_c.values()) >= 2
            and float(c_full["cagr"]) - float(b_full["cagr"]) <= 0.005
        },
        "gate_6": {
            "pass": float(b_val["max_drawdown"]) < float(a_val["max_drawdown"])
            and any(float(b_val[metric]) > float(a_val[metric]) for metric in RISK_METRICS)
            and float(a_val["cagr"]) - float(b_val["cagr"]) <= 0.015
            and bool(coverage["B_treatment"]["pass"])
        },
        "gate_7": {
            "pass": all(math.isfinite(float(b_2x[metric])) for metric in RISK_METRICS)
            and float(b_2x["net_total_return"]) > 0.0
            and all(float(b_1x[metric]) <= 0 or float(b_2x[metric]) >= 0 for metric in RISK_METRICS)
            and not all(
                float(b_2x[metric]) < float(a_2x[metric]) and float(b_2x[metric]) < float(c_2x[metric])
                for metric in RISK_METRICS
            )
            and float(b_3x["net_total_return"]) > -0.20
        },
        "gate_8": {"pass": no_new and no_promotion},
    }


def main() -> int:
    errors: list[str] = []
    checks: dict[str, object] = {}
    structural_valid = True
    try:
        manifest = _load("run_manifest.json")
        receipt = _load("DATA_RECEIPT.json")
        schedule_rows = _load("scaler_time_series.json")
        static_freeze = _load("STATIC_COMPARATOR_FREEZE.json")
        if _git("rev-parse", "main") != EXPECTED_MAIN_SHA or _git("rev-parse", "origin/main") != EXPECTED_MAIN_SHA:
            errors.append("main or origin/main does not match frozen baseline")
        checks["repository_identity"] = {
            "main_sha": _git("rev-parse", "main"),
            "origin_main_sha": _git("rev-parse", "origin/main"),
            "branch": _git("branch", "--show-current"),
            "branch_source_sha": manifest.get("branch_source_sha"),
        }
        if manifest.get("baseline_main_sha") != EXPECTED_MAIN_SHA:
            errors.append("run manifest baseline SHA mismatch")
        if _sha256(PROTOCOL_PATH) != manifest.get("protocol_sha256"):
            errors.append("protocol hash mismatch")
        for name, path, expected in (
            ("bars", BAR_PATH, EXPECTED_BAR_SHA),
            ("lifecycles", LIFETIME_PATH, EXPECTED_LIFETIME_SHA),
            ("corporate_actions", ACTION_PATH, EXPECTED_ACTION_SHA),
        ):
            actual = _sha256(path)
            if actual != expected or actual != receipt[name]["sha256"] or actual != manifest["data_hashes"][name]:
                errors.append(f"{name} data hash mismatch")
        for relative, expected in dict(manifest.get("source_hashes", {})).items():
            path = ROOT / relative
            if not path.exists() or _sha256(path) != expected:
                errors.append(f"source hash mismatch: {relative}")
        checks["protocol_and_data_hashes"] = not errors

        scaler_check = _check_scaler(errors)
        checks["scaler_replay"] = scaler_check
        scaler_rows = _load("scaler_time_series.json")
        scaler_by_decision = {
            _iso(row["decision_timestamp"]): float(row["scale"])
            for row in scaler_rows
        }
        development_rows = [
            row for row in scaler_rows if bool(row["eligible"]) and DEV_START <= _iso(row["decision_timestamp"]) <= DEV_END
        ]
        mean_scale = statistics.fmean(float(row["scale"]) for row in development_rows) if development_rows else 0.0
        static_allocation = 0.55 * mean_scale
        if not _close(static_freeze.get("development_scaler_mean"), mean_scale, 1e-10):
            errors.append("static comparator mean does not match eligible development scaler rows")
        if not _close(static_freeze.get("static_allocation"), static_allocation, 1e-10):
            errors.append("static comparator allocation does not match frozen mean")
        checks["static_comparator_freeze"] = {"mean_scale": mean_scale, "static_allocation": static_allocation}

        arms = {arm: _load(filename) for arm, filename in (
            ("A_control", "control_a.json"),
            ("B_treatment", "treatment_b.json"),
            ("C_static_comparator", "comparator_c.json"),
        )}
        if arms["A_control"]["strategy_name"] != "equity_residual_momentum_v3":
            errors.append("control identity is not current equity V3")
        if arms["B_treatment"]["strategy_name"] != "equity_v3_volatility_managed_v1":
            errors.append("treatment identity mismatch")
        if arms["C_static_comparator"]["strategy_name"] != "equity_residual_momentum_v3":
            errors.append("static comparator identity is not current equity V3")
        audit_a = _load("control_audit.json")
        audit_b = _load("scaler_audit.json")
        by_a = {_iso(row["decision_timestamp"]): row for row in audit_a}
        by_b = {_iso(row["decision_timestamp"]): row for row in audit_b}
        if set(by_a) != set(by_b):
            errors.append("control/treatment audit decision timestamps differ")
        for timestamp in sorted(set(by_a) & set(by_b)):
            left = by_a[timestamp]
            right = by_b[timestamp]
            if left["selected_symbols"] != right["selected_symbols"]:
                errors.append(f"selected symbol identity changed at {timestamp.isoformat()}")
            if left["alpha_ranks"] != right["alpha_ranks"]:
                errors.append(f"alpha ranks changed at {timestamp.isoformat()}")
            left_weights = {str(symbol): float(weight) for symbol, weight in left["pre_scale_target_weights"].items()}
            right_weights = {str(symbol): float(weight) for symbol, weight in right["pre_scale_target_weights"].items()}
            if left_weights.keys() != right_weights.keys() or any(
                not _close(left_weights[symbol], right_weights[symbol], 1e-10) for symbol in left_weights
            ):
                errors.append(f"pre-scale V3 weights changed at {timestamp.isoformat()}")
            scale = float(right["scaler"]["scale"])
            post = {str(symbol): float(weight) for symbol, weight in right["post_scale_target_weights"].items()}
            if post.keys() != left_weights.keys() or any(
                not _close(post[symbol], left_weights[symbol] * scale, 1e-10) for symbol in left_weights
            ):
                errors.append(f"treatment scaling is not proportional at {timestamp.isoformat()}")
        checks["v3_identity_and_selection_parity"] = not any(
            "identity" in error or "symbol" in error or "rank" in error or "weights" in error or "scaling" in error
            for error in errors
        )

        execution_bars = _load("execution_bar_receipt.json")
        lifecycles = _read_lifecycles()
        for arm, payload in arms.items():
            _check_execution(payload, arm, "1x", execution_bars, scaler_by_decision, static_allocation, lifecycles, errors)
            _compare_metrics(payload["metrics"], payload["execution"], errors)
        stress = _load("cost_stress.json")
        for arm in ARMS:
            for cost_key in ("2x", "3x"):
                payload = stress[arm][cost_key]
                _check_execution(payload, arm, cost_key, execution_bars, scaler_by_decision, static_allocation, lifecycles, errors)
                _compare_metrics(payload["metrics"], payload["execution"], errors)
        checks["execution_nav_caps_costs"] = not any(
            "fill" in error or "NAV" in error or "cap" in error or "total mismatch" in error or "metric" in error
            for error in errors
        )

        independent_coverage = {
            arm: _coverage(arms[arm]["execution"], schedule_rows) for arm in ARMS
        }
        reported_coverage = _load("decision_coverage.json")
        if independent_coverage != reported_coverage:
            errors.append("independent decision coverage does not match reported coverage")
        checks["coverage"] = independent_coverage

        no_new = True
        symbols_a = set()
        for row in audit_a:
            symbols_a.update(row["selected_symbols"])
        symbols_b = set()
        for row in audit_b:
            symbols_b.update(row["selected_symbols"])
            symbols_b.update(row["post_scale_target_weights"])
        if not symbols_b.issubset(symbols_a):
            errors.append("treatment introduced an instrument absent from control")
            no_new = False
        if len(symbols_b) > len(symbols_a):
            no_new = False
        production_diff = subprocess.run(
            ["git", "diff", "--name-only", "main", "--", "config", "src", "README.md", "docs"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.splitlines()
        no_promotion = not production_diff and "mode: paper" in CONFIG_PATH.read_text(encoding="utf-8") and "enabled: false" in CONFIG_PATH.read_text(encoding="utf-8")
        if production_diff:
            errors.append(f"production files changed: {production_diff}")
        if not no_promotion:
            errors.append("promotion/live configuration changed")
        checks["no_new_instruments_or_slots"] = no_new
        checks["no_promotion_changes"] = no_promotion
        structural_valid = not errors

        metrics = {arm: arms[arm]["metrics"] for arm in ARMS}
        independent_metrics_hash = hashlib.sha256(
            json.dumps(metrics, sort_keys=True, allow_nan=False).encode("utf-8")
        ).hexdigest()
        independent_gate_table = _independent_gates(
            metrics,
            stress,
            independent_coverage,
            structural_valid,
            no_new,
            no_promotion,
        )
        output = {
            "research_run_valid": structural_valid,
            "structural_valid": structural_valid,
            "validity_status": "RESEARCH_RUN_VALID" if structural_valid else "RESEARCH_RUN_INVALID",
            "errors": errors,
            "checks": checks,
            "protocol_sha256": _sha256(PROTOCOL_PATH),
            "data_sha256": {
                "bars": _sha256(BAR_PATH),
                "lifecycles": _sha256(LIFETIME_PATH),
                "corporate_actions": _sha256(ACTION_PATH),
            },
            "independent_metrics_sha256": independent_metrics_hash,
            "independent_gate_table": independent_gate_table,
            "no_new_instruments_or_slots": no_new,
            "no_promotion_changes": no_promotion,
            "branch_sha_at_verification": _git("rev-parse", "HEAD"),
        }
    except Exception as exc:  # pragma: no cover - fatal receipt path
        structural_valid = False
        errors.append(f"verifier exception: {type(exc).__name__}: {exc}")
        output = {
            "research_run_valid": False,
            "structural_valid": False,
            "validity_status": "RESEARCH_RUN_INVALID",
            "errors": errors,
            "checks": checks,
            "independent_gate_table": {},
            "no_new_instruments_or_slots": False,
            "no_promotion_changes": False,
        }
    (RESEARCH_DIR / "independent_verification.json").write_text(
        json.dumps(output, indent=2, sort_keys=True, allow_nan=False), encoding="utf-8"
    )
    print(output["validity_status"])
    if output["errors"]:
        print("; ".join(str(error) for error in output["errors"][:12]))
    return 0 if output["research_run_valid"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
