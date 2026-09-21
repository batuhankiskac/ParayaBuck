"""Independent, read-only verification of the frozen economic output.

The verifier intentionally reads persisted JSON/JSONL and the frozen source
CSV rather than importing the runner's result objects.  It recomputes signal
IDs, chronology, boundary metrics, role/cap invariants, and cost scaling.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import subprocess
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path
from typing import Any

from parayabuck.backtest.stats import sample_stddev

ROOT = Path(__file__).resolve().parents[2]
OUTPUT = Path(__file__).resolve().parent
BARS_PATH = ROOT / "data/historical/equity_pit_symmetric_2016_2026_v5.csv"
EXPECTED_BARS_SHA = "bb7aaae86bc1909c6b0a1094a59430f5ed9900bb3ec63891bbdcda8cda055139"
ENDPOINT = datetime.fromisoformat("2026-07-30T04:00:00+00:00")
DEV_START_DATE = date(2017, 1, 3)
DEV_END_DATE = date(2021, 12, 31)
VAL_START_DATE = date(2022, 1, 3)


def _json(path: Path) -> Any:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(path)
    return value


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()


def _timestamp_for_date(payload: Any, target: date) -> datetime:
    for item in payload["equity_curve"]:
        timestamp = datetime.fromisoformat(str(item["timestamp"]))
        if timestamp.date() == target:
            return timestamp
    raise ValueError(f"No curve session for {target.isoformat()}")


def _period_metrics(payload: Any, start_date: date, end_date: date) -> dict[str, float | int | None]:
    start = _timestamp_for_date(payload, start_date)
    end = _timestamp_for_date(payload, end_date)
    points = [
        (datetime.fromisoformat(str(item["timestamp"])), float(item["equity"]))
        for item in payload["equity_curve"]
        if start <= datetime.fromisoformat(str(item["timestamp"])) <= end
    ]
    if not points or points[0][0] != start or points[-1][0] != end:
        raise ValueError(f"Missing exact boundary for {start.isoformat()}..{end.isoformat()}")
    values = [value for _timestamp, value in points]
    returns = [values[index] / values[index - 1] - 1.0 for index in range(1, len(values)) if values[index - 1] > 0]
    downside = [value for value in returns if value < 0]
    duration_days = (points[-1][0] - points[0][0]).total_seconds() / 86_400
    years = duration_days / 365.0
    total_return = values[-1] / values[0] - 1.0
    cagr = (1.0 + total_return) ** (1.0 / years) - 1.0 if years > 0 and total_return > -1 else 0.0
    std = sample_stddev(returns)
    downside_std = sample_stddev(downside)
    annualizer = math.sqrt(max(1.0, len(returns) / years)) if returns and years > 0 else 1.0
    peak = values[0]
    max_drawdown = 0.0
    for value in values:
        peak = max(peak, value)
        max_drawdown = max(max_drawdown, (peak - value) / peak if peak > 0 else 0.0)
    monthly: dict[tuple[int, int], float] = {}
    for timestamp, value in points:
        monthly[(timestamp.year, timestamp.month)] = value
    month_returns: list[float] = []
    previous = values[0]
    for key in sorted(monthly):
        value = monthly[key]
        month_returns.append(value / previous - 1.0 if previous > 0 else 0.0)
        previous = value
    return {
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
    }


def _verify_signals() -> dict[str, object]:
    required: dict[str, set[str]] = defaultdict(set)
    records: list[dict[str, Any]] = []
    with (OUTPUT / "SIGNAL_AUDIT.jsonl").open(encoding="utf-8") as handle:
        for line in handle:
            event = json.loads(line)
            decision = datetime.fromisoformat(str(event["decision_timestamp"]))
            for record in event.get("records", []):
                if not record.get("scoreable"):
                    continue
                symbol = str(record["symbol"])
                sessions = [
                    str(record["formation_anchor_session"]),
                    str(record["formation_endpoint_session"]),
                    *[str(value) for value in record["formation_sessions"]],
                    *[str(value) for value in record["skipped_sessions"]],
                ]
                for session in sessions:
                    required[symbol].add(session)
                    if datetime.fromisoformat(session) > decision:
                        raise ValueError(f"Signal uses a future session: {symbol} {session} > {decision.isoformat()}")
                records.append(record)
    closes: dict[tuple[str, str], float] = {}
    remaining = {symbol: set(values) for symbol, values in required.items()}
    with BARS_PATH.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        for raw in reader:
            symbol = str(raw["symbol"])
            if symbol not in remaining:
                continue
            timestamp = datetime.fromisoformat(str(raw["timestamp"]).replace("Z", "+00:00")).isoformat()
            if timestamp in remaining[symbol]:
                closes[(symbol, timestamp)] = float(raw["close"])
                remaining[symbol].discard(timestamp)
    if any(remaining.values()):
        missing = {symbol: sorted(values)[:3] for symbol, values in remaining.items() if values}
        raise ValueError(f"Signal sessions missing from frozen bars: {missing}")
    checked = 0
    for record in records:
        symbol = str(record["symbol"])
        anchor = str(record["formation_anchor_session"])
        endpoint = str(record["formation_endpoint_session"])
        formation = [str(value) for value in record["formation_sessions"]]
        skipped = [str(value) for value in record["skipped_sessions"]]
        if len(formation) != 252 or len(skipped) != 21 or int(record["denominator"]) != 252:
            raise ValueError(f"Frozen indexing lengths invalid for {symbol}")
        timestamps = [anchor, *formation, *skipped]
        prices = [closes[(symbol, timestamp)] for timestamp in timestamps]
        returns = [prices[index] / prices[index - 1] - 1.0 for index in range(1, 253)]
        pret = prices[252] / prices[0] - 1.0
        positive = sum(value > 0 for value in returns)
        negative = sum(value < 0 for value in returns)
        zero = sum(value == 0 for value in returns)
        sign = 1 if pret > 0 else (-1 if pret < 0 else 0)
        identifier = sign * (negative / 252.0 - positive / 252.0)
        if endpoint != formation[-1]:
            raise ValueError(f"Formation endpoint mismatch for {symbol}")
        for key, expected in (
            ("pret", pret),
            ("positive_count", positive),
            ("negative_count", negative),
            ("zero_count", zero),
            ("sign_pret", sign),
            ("id", identifier),
        ):
            actual = float(record[key])
            if abs(actual - float(expected)) > 1e-12:
                raise ValueError(f"Signal mismatch {symbol} {key}: {actual} != {expected}")
        checked += 1
    return {"passed": True, "scoreable_records_checked": checked, "symbols_checked": len(required)}


def _verify_result(path: Path, period_metrics: Any) -> dict[str, object]:
    payload = _json(path)
    summary = payload["summary"]
    checks: list[str] = []
    for name, (start, end) in {
        "development": (DEV_START_DATE, DEV_END_DATE),
        "validation": (VAL_START_DATE, ENDPOINT.date()),
        "full": (DEV_START_DATE, ENDPOINT.date()),
    }.items():
        recomputed = _period_metrics(payload, start, end)
        stored = period_metrics[name]
        for key, value in recomputed.items():
            if value is None:
                continue
            if abs(float(value) - float(stored[key])) > 1e-10:
                raise ValueError(f"Metric mismatch {path.name} {name} {key}")
        checks.append(name)
    for fill in payload["fills"]:
        decision = datetime.fromisoformat(str(fill["decision_timestamp"]))
        timestamp = datetime.fromisoformat(str(fill["timestamp"]))
        if not decision < timestamp:
            raise ValueError(f"Causal fill violation in {path.name}")
    reserved = {"SPY", "SH", "PSQ", "DOG"}
    for record in payload["rebalances"]:
        target = {str(symbol): float(weight) for symbol, weight in record["target_weights"]}
        if reserved & set(target):
            raise ValueError(f"Reserved role traded in {path.name}: {reserved & set(target)}")
        if any(weight > 0.100000001 for weight in target.values()):
            raise ValueError(f"Per-name cap violation in {path.name}")
        if sum(target.values()) > 0.550000001:
            raise ValueError(f"Maximum allocation violation in {path.name}")
    return {"passed": True, "periods_checked": checks, "identity": summary["strategy_name"]}


def _verify_cost_scaling() -> dict[str, object]:
    totals: dict[str, list[float]] = {"slippage_cost_total": [], "spread_cost_total": [], "fee_total": []}
    for multiplier in (1.0, 2.0, 3.0):
        summary = _json(OUTPUT / f"CANDIDATE_B_{multiplier:g}x_RESULTS.json")["summary"]
        for key in totals:
            totals[key].append(float(summary[key]))
    for values in totals.values():
        if not (values[1] >= values[0] - 1e-9 and values[2] >= values[1] - 1e-9):
            raise ValueError(f"Cost scaling is not monotonic: {values}")
    return {"passed": True, "totals": totals}


def main() -> int:
    manifest = _json(OUTPUT / "RUN_MANIFEST.json")
    if _sha(BARS_PATH) != EXPECTED_BARS_SHA:
        raise ValueError("Frozen bars hash changed")
    if manifest.get("candidate") != "equity_information_discreteness_continuous_v1":
        raise ValueError("Candidate identity mismatch")
    schedule = _json(OUTPUT / "SCHEDULE.json")["timestamps"]
    if not schedule or schedule[-1] != ENDPOINT.isoformat():
        raise ValueError("Schedule endpoint mismatch")
    signal = _verify_signals()
    metrics: Any = _json(OUTPUT / "METRICS.json")
    result_checks: dict[str, object] = {}
    for arm, prefix in (("control_a", "CONTROL_A"), ("candidate_b", "CANDIDATE_B")):
        for multiplier in (1.0, 2.0, 3.0):
            label = f"{multiplier:g}x"
            result_checks[f"{arm}/{label}"] = _verify_result(
                OUTPUT / f"{prefix}_{label}_RESULTS.json",
                metrics[arm][label],
            )
    cost = _verify_cost_scaling()
    verification = {
        "passed": True,
        "verified_at": datetime.now().astimezone().isoformat(),
        "branch": _git("branch", "--show-current"),
        "head": _git("rev-parse", "HEAD"),
        "signal_recomputation": signal,
        "result_checks": result_checks,
        "cost_scaling": cost,
        "causality": "every persisted fill decision_timestamp is strictly before fill timestamp",
        "roles": "SPY, SH, PSQ and DOG absent from ordinary target weights",
        "metrics": "development/validation/full curves recomputed from persisted continuous path",
    }
    (OUTPUT / "INDEPENDENT_VERIFICATION.json").write_text(
        json.dumps(verification, indent=2, sort_keys=True, allow_nan=False), encoding="utf-8"
    )
    gate_table = _json(OUTPUT / "GATE_TABLE.json")
    gates = gate_table.get("gates", {})
    if isinstance(gates, dict):
        gates["G1_validity_causality_data_pit_roles_identity_independent_verifier"] = True
        gate_table["all_pass"] = all(bool(value) for value in gates.values())
    (OUTPUT / "GATE_TABLE.json").write_text(json.dumps(gate_table, indent=2, sort_keys=True), encoding="utf-8")
    decision = "RESEARCH_ACCEPT_FOR_FORWARD_PAPER_EVALUATION" if gate_table.get("all_pass") else "RESEARCH_REJECT"
    (OUTPUT / "DECISION.json").write_text(
        json.dumps({"decision": decision, "gates": gate_table}, indent=2, sort_keys=True), encoding="utf-8"
    )
    (OUTPUT / "FINAL_REPORT.md").write_text(
        "# Information Discreteness Economic v1\n\n"
        f"Decision: `{decision}`\n\n"
        "This is one frozen research-only economic experiment. Control A is the "
        "fresh contemporaneous `equity_residual_momentum_v3`; Candidate B is the "
        "frozen positive-PRET, lower-ID alternative. Development and validation "
        "metrics were sliced from one continuous state path. The independent "
        "verifier recomputed persisted signal IDs, exact boundaries, fill causality, "
        "roles, caps, and cost monotonicity.\n\n"
        "See `METRICS.json`, `GATE_TABLE.json`, `TURNOVER_DIAGNOSTICS.json`, "
        "`CONCENTRATION_DIAGNOSTICS.json`, and `INDEPENDENT_VERIFICATION.json`.\n",
        encoding="utf-8",
    )
    print(json.dumps(verification, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
