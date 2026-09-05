"""Read-only audit for the corrected high-proximity six-cell results.

The script consumes completed JSON artifacts only.  It never invokes an engine,
changes a manifest, or fills missing cells.  Missing, incomplete, or
misaligned inputs fail closed with ``BLOCKED_*``.
"""

from __future__ import annotations

import hashlib
import json
import math
import statistics
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
BASE = REPO / "data/research/portfolio_expansion_crypto_screen_2026_09"
ORIGINAL = BASE / "attempt_03_canonical"
CORRECTED = REPO / "data/research/high_proximity364_history_fixed_2026_09_05"
CANDIDATE = CORRECTED / "results.json"
OUT = CORRECTED / "audit_2026_09_05.json"
REPORT = REPO / "docs/research/high_proximity364_history_fixed_audit_2026_09_05.md"
INITIAL_CASH = 100_000.0
WINDOWS = ("full", "recent")
COSTS = (1.0, 2.0, 3.0)
CANDIDATE_ARM = "high_proximity364_history_fixed"
INCUMBENT_ARM = "low_variance22"
ORIGINAL_ARM = "high_proximity364"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def curve(cell: dict) -> list[tuple[datetime, float]]:
    timestamps = cell.get("equity_timestamps")
    values = cell.get("equity_curve")
    if not isinstance(timestamps, list) or not isinstance(values, list) or len(timestamps) != len(values):
        raise RuntimeError("BLOCKED_MISSING_OR_MISALIGNED_NAV")
    points = [(ts(t), float(v)) for t, v in zip(timestamps, values, strict=True)]
    if not points or points[0][1] != INITIAL_CASH or any(not math.isfinite(v) or v <= 0 for _, v in points):
        raise RuntimeError("BLOCKED_INVALID_INITIAL_OR_NONFINITE_NAV")
    return points


def daily(points: list[tuple[datetime, float]]) -> list[tuple[datetime, float]]:
    by_day: dict[object, tuple[datetime, float]] = {}
    for point in points:
        by_day[point[0].date()] = point
    return sorted(by_day.values())


def returns(points: list[tuple[datetime, float]]) -> list[float]:
    values = [v for _, v in points]
    return [values[0] / INITIAL_CASH - 1.0] + [values[i] / values[i - 1] - 1.0 for i in range(1, len(values))]


def metrics(points: list[tuple[datetime, float]], cadence: str) -> dict[str, object]:
    values = [v for _, v in points]
    peak = INITIAL_CASH
    max_dd = 0.0
    for value in values:
        peak = max(peak, value)
        max_dd = max(max_dd, 1.0 - value / peak)
    period_days = max((points[-1][0] - points[0][0]).total_seconds() / 86400.0, 1.0)
    total = values[-1] / INITIAL_CASH - 1.0
    annualization = 8760.0 if cadence == "hourly" else 365.25
    daily_returns = returns(points)
    std = statistics.stdev(daily_returns) if len(daily_returns) > 1 else 0.0
    sharpe = statistics.mean(daily_returns) / std * math.sqrt(annualization) if std > 0 else None
    return {
        "start": points[0][0].isoformat(),
        "end": points[-1][0].isoformat(),
        "observations": len(points),
        "terminal_nav": values[-1],
        "total_return": total,
        "cagr": (1.0 + total) ** (365.25 / period_days) - 1.0,
        "max_drawdown": max_dd,
        "sharpe": sharpe,
        "cadence": cadence,
        "annualization": annualization,
        "denominator": "INITIAL_CASH",
    }


def ledger_reconciliation(cell: dict) -> dict[str, object]:
    ledger = cell.get("cash_ledger")
    if not isinstance(ledger, list) or not ledger:
        raise RuntimeError("BLOCKED_MISSING_CASH_LEDGER")
    errors = []
    for row in ledger:
        cash = float(row["cash"])
        equity = float(row["equity"])
        marked = sum(float(position.get("value", 0.0)) for position in row.get("positions", {}).values())
        errors.append(abs(cash + marked - equity))
    terminal_nav = curve(cell)[-1][1]
    return {
        "initial_cash_sentinel": float(ledger[0]["cash"]) == INITIAL_CASH and float(ledger[0]["equity"]) == INITIAL_CASH,
        "max_cash_plus_marked_position_error": max(errors),
        "terminal_nav_error": abs(float(ledger[-1]["equity"]) - terminal_nav),
        "finite": all(math.isfinite(value) for row in ledger for value in (float(row["cash"]), float(row["equity"]))),
    }


def correlation(a: list[float], b: list[float]) -> float | None:
    if len(a) != len(b) or len(a) < 2:
        return None
    sa, sb = statistics.stdev(a), statistics.stdev(b)
    if sa == 0.0 or sb == 0.0:
        return None
    ma, mb = statistics.mean(a), statistics.mean(b)
    return sum((x - ma) * (y - mb) for x, y in zip(a, b, strict=True)) / ((len(a) - 1) * sa * sb)


def cell_id(cell: dict) -> str:
    return f"{cell.get('window')}/{cell.get('arm')}/{float(cell.get('cost_multiplier')):g}x"


def load_cells(path: Path, expected_count: int, label: str) -> dict[tuple[str, str, float], dict]:
    if not path.is_file():
        raise RuntimeError(f"BLOCKED_MISSING_{label.upper()}_OUTPUT")
    payload = json.loads(path.read_text(encoding="utf-8"))
    cells = payload.get("cells")
    if not isinstance(cells, list) or len(cells) != expected_count:
        raise RuntimeError(f"BLOCKED_INCOMPLETE_{label.upper()}_OUTPUT")
    mapped: dict[tuple[str, str, float], dict] = {}
    for cell in cells:
        key = (str(cell["arm"]), str(cell["window"]), float(cell["cost_multiplier"]))
        if key in mapped:
            raise RuntimeError("BLOCKED_DUPLICATE_CELL")
        curve(cell)
        mapped[key] = cell
    return mapped


def load_original() -> dict[tuple[str, str, float], dict]:
    cells: list[dict] = []
    for index in range(3):
        path = ORIGINAL / f"shard_{index}/economic_results.json"
        mapped = load_cells(path, 14, f"original_shard_{index}")
        cells.extend(mapped.values())
    result = {(str(c["arm"]), str(c["window"]), float(c["cost_multiplier"])): c for c in cells}
    if len(result) != 42:
        raise RuntimeError("BLOCKED_ORIGINAL_CELL_COVERAGE")
    return result


def audit(candidate_path: Path = CANDIDATE) -> dict[str, object]:
    candidate = load_cells(candidate_path, 6, "corrected")
    expected = {(CANDIDATE_ARM, window, cost) for window in WINDOWS for cost in COSTS}
    if set(candidate) != expected:
        raise RuntimeError("BLOCKED_CORRECTED_CELL_COVERAGE")
    original = load_original()
    rows: list[dict[str, object]] = []
    blends: list[dict[str, object]] = []
    for window in WINDOWS:
        for cost in COSTS:
            cand = candidate[(CANDIDATE_ARM, window, cost)]
            inc = original[(INCUMBENT_ARM, window, cost)]
            old = original[(ORIGINAL_ARM, window, cost)]
            cp, ip, op = curve(cand), curve(inc), curve(old)
            if [t for t, _ in cp] != [t for t, _ in ip]:
                raise RuntimeError("BLOCKED_CANDIDATE_INCUMBENT_NAV_ALIGNMENT")
            if [t for t, _ in op] != [t for t, _ in ip]:
                raise RuntimeError("BLOCKED_ORIGINAL_BASELINE_NAV_ALIGNMENT")
            cd, idaily = daily(cp), daily(ip)
            if [t for t, _ in cd] != [t for t, _ in idaily]:
                raise RuntimeError("BLOCKED_CANDIDATE_INCUMBENT_DAILY_ALIGNMENT")
            mix = [(t, (a + b) / 2.0) for (t, a), (_, b) in zip(cp, ip, strict=True)]
            half_cash = [(t, (a + INITIAL_CASH) / 2.0) for t, a in ip]
            rows.append({
                "window": window,
                "cost_multiplier": cost,
                "candidate": {"arm": CANDIDATE_ARM, "fills": len(cand.get("fills", [])), "fees": sum(float(f.get("fee", 0.0)) for f in cand.get("fills", [])), "hourly": metrics(cp, "hourly"), "daily": metrics(cd, "daily")},
                "incumbent": {"arm": INCUMBENT_ARM, "fills": len(inc.get("fills", [])), "fees": sum(float(f.get("fee", 0.0)) for f in inc.get("fills", [])), "hourly": metrics(ip, "hourly"), "daily": metrics(idaily, "daily")},
                "candidate_ledger_reconciliation": ledger_reconciliation(cand),
                "incumbent_ledger_reconciliation": ledger_reconciliation(inc),
                "original_p364_baseline": {"arm": ORIGINAL_ARM, "status": "INVALID_ALGORITHM_BASELINE_HISTORY_TRIM", "terminal_nav": op[-1][1], "fills": len(old.get("fills", []))},
            })
            bm = metrics(mix, "hourly")
            bc = metrics(half_cash, "hourly")
            blends.append({
                "window": window,
                "cost_multiplier": cost,
                "candidate": CANDIDATE_ARM,
                "incumbent": INCUMBENT_ARM,
                "blend_initial_cash_50_50": bm,
                "half_candidate_cash_control": bc,
                "delta_blend_vs_incumbent": {k: bm[k] - metrics(ip, "hourly")[k] for k in ("total_return", "cagr", "max_drawdown")},
                "delta_blend_vs_half_cash": {k: bm[k] - bc[k] for k in ("total_return", "cagr", "max_drawdown")},
                "daily_net_pnl_correlation": correlation(returns(daily(mix)), returns(idaily)),
                "diagnostic_only": True,
            })
    return {"status": "AUDIT_COMPLETE", "economic_run": False, "initial_cash": INITIAL_CASH, "candidate_cells": 6, "original_cells_reused": 42, "rows": rows, "blends": blends, "rules": "candidate and incumbent hourly NAV grids must match exactly; 50/50 uses initial capital curves; half-cash is analytical; first native point retains the INITIAL_CASH sentinel and its first-day return; hourly Sharpe annualizes 8760; daily Sharpe 365.25; correlation is daily net-PnL; original P364 is labeled invalid algorithm baseline", "input_sha256": {"candidate": sha(candidate_path), "original_shards": {str(i): sha(ORIGINAL / f"shard_{i}/economic_results.json") for i in range(3)}}}


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate-results", type=Path, default=CANDIDATE)
    args = parser.parse_args()
    result = audit(args.candidate_results)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({"status": result["status"], "output": str(OUT), "rows": len(result["rows"]), "blends": len(result["blends"]), "output_sha256": sha(OUT)}))
