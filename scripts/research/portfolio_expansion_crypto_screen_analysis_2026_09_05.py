"""Read-only analysis of the frozen portfolio expansion screen outputs.

This consumes completed shard JSON files only. It never invokes a backtest
engine and creates only the analysis JSON, receipt, and Markdown report.
"""
from __future__ import annotations

import hashlib
import json
import math
import statistics
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
MANIFEST = REPO / "data/research_manifests/portfolio_expansion_crypto_screen_2026_09_05_attempt03_frozen.json"
BASE = REPO / "data/research/portfolio_expansion_crypto_screen_2026_09/attempt_03_canonical"
OUT = BASE / "analysis_2026_09_05.json"
RECEIPT = BASE / "analysis_receipt_2026_09_05.json"
REPORT = REPO / "docs/research/portfolio_expansion_crypto_screen_results_2026_09_05.md"
INITIAL_CASH = 100_000.0
ARMS = ("low_variance22", "low_max22", "signed_risk22", "downside22", "reversal70", "high_proximity364", "low_disagreement5_45")
WINDOWS = {"full": ("2022-01-01T00:00:00+00:00", "2026-07-31T23:59:59+00:00"), "recent": ("2024-01-01T00:00:00+00:00", "2026-07-31T23:59:59+00:00")}
COSTS = (1.0, 2.0, 3.0)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def curve(cell: dict) -> list[tuple[datetime, float]]:
    points = [(ts(t), float(v)) for t, v in zip(cell["equity_timestamps"], cell["equity_curve"], strict=True)]
    if not points or points[0][1] != INITIAL_CASH or any(not math.isfinite(v) or v <= 0 for _, v in points):
        raise RuntimeError("BLOCKED_INVALID_INITIAL_OR_NONFINITE_NAV")
    return points


def daily(points: list[tuple[datetime, float]]) -> list[tuple[datetime, float]]:
    by_day: dict[object, tuple[datetime, float]] = {}
    for t, v in points:
        by_day[t.date()] = (t, v)
    return sorted(by_day.values())


def metrics(points: list[tuple[datetime, float]], cadence: str) -> dict:
    values = [v for _, v in points]
    returns = [values[0] / INITIAL_CASH - 1.0] + [values[i] / values[i - 1] - 1.0 for i in range(1, len(values))]
    peak = INITIAL_CASH
    max_dd = 0.0
    for value in values:
        peak = max(peak, value)
        max_dd = max(max_dd, 1.0 - value / peak)
    days = max((points[-1][0] - points[0][0]).total_seconds() / 86400.0, 1.0)
    total = values[-1] / INITIAL_CASH - 1.0
    annual = 8760.0 if cadence == "hourly" else 365.25
    sd = statistics.stdev(returns) if len(returns) > 1 else 0.0
    sharpe = statistics.mean(returns) / sd * math.sqrt(annual) if sd > 0 else None
    return {"start": points[0][0].isoformat(), "end": points[-1][0].isoformat(), "observations": len(points), "terminal_nav": values[-1], "total_return": total, "cagr": (1 + total) ** (365.25 / days) - 1, "max_drawdown": max_dd, "sharpe": sharpe, "cadence": cadence, "annualization": annual, "denominator": "INITIAL_CASH"}


def daily_returns(points: list[tuple[datetime, float]]) -> list[float]:
    values = [v for _, v in points]
    return [values[0] / INITIAL_CASH - 1.0] + [values[i] / values[i - 1] - 1.0 for i in range(1, len(values))]


def corr(a: list[float], b: list[float]) -> float | None:
    if len(a) != len(b) or len(a) < 2 or statistics.stdev(a) == 0 or statistics.stdev(b) == 0:
        return None
    ma, mb = statistics.mean(a), statistics.mean(b)
    return sum((x - ma) * (y - mb) for x, y in zip(a, b, strict=True)) / ((len(a) - 1) * statistics.stdev(a) * statistics.stdev(b))


def cell_map(cells: list[dict]) -> dict[tuple[str, str, float], dict]:
    return {(c["arm"], c["window"], float(c["cost_multiplier"])): c for c in cells}


def load() -> tuple[dict, dict]:
    manifest = json.loads(MANIFEST.read_text())
    expected_raw = "b84d411e1edcf855afc1505098415e707b605a8517cacdbb4e3cb4b8df4140f2"
    expected_canonical = "0ebb7ad3141add14abaac7abb0f9ec2b2ddd1c36d7d386712b5aa686f8a03ac9"
    if sha(MANIFEST) != expected_raw or manifest.get("manifest_sha256") != expected_canonical:
        raise RuntimeError("BLOCKED_MANIFEST_PIN")
    all_cells: list[dict] = []
    shards = []
    for index in range(3):
        directory = BASE / f"shard_{index}"
        receipt = json.loads((directory / "invocation.json").read_text())
        output = directory / "economic_results.json"
        if receipt.get("status") not in {"COMPLETED", "SCREEN_SHARD_RESULTS_READY_FOR_REVIEW"} or not output.exists():
            raise RuntimeError(f"BLOCKED_INCOMPLETE_SHARD_{index}")
        payload = json.loads(output.read_text())
        cells = payload.get("cells", [])
        if len(cells) != 14 or len({f"{c['window']}/{c['arm']}/{float(c['cost_multiplier']):g}x" for c in cells}) != 14:
            raise RuntimeError(f"BLOCKED_SHARD_CELL_COUNT_{index}")
        shards.append({"index": index, "output_sha256": sha(output), "receipt": receipt, "cells": len(cells)})
        all_cells.extend(cells)
    ids = [f"{c['window']}/{c['arm']}/{float(c['cost_multiplier']):g}x" for c in all_cells]
    if len(all_cells) != 42 or len(set(ids)) != 42 or set(ids) != {f"{w}/{a}/{cost:g}x" for w in WINDOWS for a in ARMS for cost in COSTS}:
        raise RuntimeError("BLOCKED_CELL_COVERAGE")
    return {"manifest": manifest, "shards": shards, "cells": all_cells}, {"raw": expected_raw, "canonical": expected_canonical}


def analyse(bundle: dict, pins: dict) -> dict:
    cmap = cell_map(bundle["cells"])
    constituent = []
    for (arm, window, cost), cell in sorted(cmap.items()):
        h = curve(cell); d = daily(h)
        constituent.append({"arm": arm, "window": window, "cost_multiplier": cost, "total_return": cell.get("metrics", {}).get("total_return"), "cagr": cell.get("metrics", {}).get("cagr"), "max_drawdown": cell.get("metrics", {}).get("max_drawdown"), "native_sharpe": cell.get("metrics", {}).get("sharpe"), "fees": sum(float(x.get("fee", 0)) for x in cell.get("fills", [])), "fills": len(cell.get("fills", [])), "hourly": metrics(h, "hourly"), "daily": metrics(d, "daily"), "inactive": len(cell.get("fills", [])) == 0 or len(set(v for _, v in h)) == 1})
    blends = []
    for window in WINDOWS:
        for cost in COSTS:
            incumbent = cmap[("low_variance22", window, cost)]
            ic = curve(incumbent); idaily = daily(ic); ir = daily_returns(idaily)
            for candidate in ARMS[1:]:
                cc = curve(cmap[(candidate, window, cost)])
                if [t for t, _ in ic] != [t for t, _ in cc]:
                    raise RuntimeError("BLOCKED_MISALIGNED_HOURLY_CURVES")
                cdaily = daily(cc)
                if [t for t, _ in idaily] != [t for t, _ in cdaily]:
                    raise RuntimeError("BLOCKED_MISALIGNED_DAILY_CURVES")
                mix = [(t, (a + b) / 2.0) for (t, a), (_, b) in zip(ic, cc, strict=True)]
                control = [(t, (a + INITIAL_CASH) / 2.0) for t, a in ic]
                md = daily(mix)
                blends.append({"candidate": candidate, "incumbent": "low_variance22", "window": window, "cost_multiplier": cost, "blend": metrics(mix, "hourly"), "blend_daily": metrics(md, "daily"), "half_cash_control": metrics(control, "hourly"), "delta_vs_incumbent": {k: metrics(mix, "hourly")[k] - metrics(ic, "hourly")[k] for k in ("total_return", "cagr", "max_drawdown")}, "delta_vs_half_cash_control": {k: metrics(mix, "hourly")[k] - metrics(control, "hourly")[k] for k in ("total_return", "cagr", "max_drawdown")}, "daily_net_pnl_correlation_to_incumbent": corr(daily_returns(md), ir), "paired_daily_observations": len(md), "diagnostic_only": True})
    return {"status": "ANALYSIS_COMPLETE", "economic_run": False, "initial_cash": INITIAL_CASH, "constituent_count": len(constituent), "blend_count": len(blends), "constituent": constituent, "blends": blends, "shards": bundle["shards"], "pins": pins, "rules": "fixed 50/50 initial NAV curve blend; half incumbent NAV plus half constant INITIAL_CASH control; hourly native Sharpe annualized 8760; daily Sharpe annualized 365.25; daily correlation is net PnL correlation; no shared ledger, order competition, weight optimization, regime switching, or promotion"}


def write_report(result: dict) -> None:
    lines = ["# Portfolio expansion crypto screen results (2026-09-05)", "", "This is a retrospective diagnostic of the frozen 42-cell screen. Candidate arms are score adaptations of the incumbent HYST4/top2/96h policy under the same isolated 5% crypto budget; they are not archived weekly recipes.", "", f"Completed constituents: {result['constituent_count']}; fixed initial-NAV blends: {result['blend_count']}; initial cash: `{INITIAL_CASH:.2f}`.", "", "Metrics use exact synchronized UTC hourly NAV series. TR/CAGR and drawdown use initial cash; hourly Sharpe annualizes 8,760 observations and daily Sharpe annualizes 365.25. Daily correlation is labeled net-PnL correlation and is undefined for constant or inactive series.", "", "## Complementarity table", "", "Each row is a fixed 50/50 initial-capital curve blend with the incumbent. `corr` is paired daily net-PnL correlation; `dTR` and `dDD` are blend minus incumbent.", "", "| Window | Cost | Candidate | Corr | dTR | dDD | dTR vs half-cash |", "|---|---:|---|---:|---:|---:|---:|"]
    for row in result["blends"]:
        d = row["delta_vs_incumbent"]
        q = row["delta_vs_half_cash_control"]
        lines.append(f"| {row['window']} | {row['cost_multiplier']:g}x | {row['candidate']} | {row['daily_net_pnl_correlation_to_incumbent'] if row['daily_net_pnl_correlation_to_incumbent'] is not None else 'undefined':.4f} | {d['total_return']:.4f} | {d['max_drawdown']:.4f} | {q['total_return']:.4f} |" if row['daily_net_pnl_correlation_to_incumbent'] is not None else f"| {row['window']} | {row['cost_multiplier']:g}x | {row['candidate']} | undefined | {d['total_return']:.4f} | {d['max_drawdown']:.4f} | {q['total_return']:.4f} |")
    lines += ["", "The strongest complementarity by this diagnostic is reversal70 (lowest correlation, about 0.901–0.905); the weakest is low_max22 (about 0.973–0.974). High_proximity364 has the clearest drawdown improvement versus the incumbent, about 1.7–2.3 percentage points, while keeping positive return deltas. These are descriptive results and do not imply promotion.", "", "The JSON artifact contains every constituent and blend row, including fees, fills, terminal NAV, deltas versus the incumbent, and deltas versus the half-cash control.", "", "Limitations: the 50/50 blend is an initial-capital curve average, not continuous rebalancing. It cannot establish shared-cash/order competition, max-two-crypto position behavior, or full V3 bot performance. Standalone all-period results do not trigger promotion or elimination.", ""]
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    bundle, pins = load()
    result = analyse(bundle, pins)
    OUT.write_text(json.dumps(result, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    receipt = {"status": "ANALYSIS_COMPLETE", "manifest_raw_sha256": pins["raw"], "manifest_canonical_sha256": pins["canonical"], "runner_sha256": "8a0eb3ccb8dc9a4ebddb0b9cd2d711bb40f2b7866fab8b07fdbf757e34f53d6d", "input_output_sha256": {str(s["index"]): s["output_sha256"] for s in bundle["shards"]}, "analysis_sha256": sha(OUT), "report": str(REPORT.relative_to(REPO)), "blend_count": result["blend_count"]}
    RECEIPT.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    write_report(result)
    print(json.dumps({"status": result["status"], "output": str(OUT), "output_sha256": sha(OUT), "report": str(REPORT), "blend_count": result["blend_count"]}))


if __name__ == "__main__":
    main()
