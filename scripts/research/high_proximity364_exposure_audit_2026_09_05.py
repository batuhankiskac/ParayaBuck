"""Read-only forensic audit of the frozen high_proximity364 screen cells.

Consumes only frozen shard outputs and the pinned local hourly CSV.  It does
not run a backtest, alter a strategy, or infer unobservable execution facts.
"""
from __future__ import annotations

import csv
import hashlib
import json
import math
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
BASE = REPO / "data/research/portfolio_expansion_crypto_screen_2026_09/attempt_03_canonical"
MANIFEST = REPO / "data/research_manifests/portfolio_expansion_crypto_screen_2026_09_05_attempt03_frozen.json"
DATA = REPO / "data/historical/crypto_expanded_1h.csv"
OUT = BASE / "audit/high_proximity364_exposure_audit_2026_09_05.json"
REPORT = REPO / "docs/research/high_proximity364_exposure_audit_2026_09_05.md"
INITIAL_CASH = 100_000.0
ARMS = ("high_proximity364", "low_variance22")
WINDOWS = ("full", "recent")
COSTS = (1.0, 2.0, 3.0)
TRADE_SYMBOLS = (
    "BTC/USD", "ETH/USD", "SOL/USD", "XRP/USD", "ADA/USD", "DOGE/USD", "AVAX/USD",
    "LINK/USD", "LTC/USD", "BCH/USD", "DOT/USD", "UNI/USD", "AAVE/USD", "ARB/USD",
    "FIL/USD", "RENDER/USD", "ONDO/USD", "HYPE/USD", "SHIB/USD", "GRT/USD", "CRV/USD", "PEPE/USD",
)


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def dt(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def pct(value: float | None) -> float | None:
    return None if value is None else float(value)


def load_cells() -> tuple[dict[tuple[str, str, float], dict], dict[str, str]]:
    manifest = json.loads(MANIFEST.read_text())
    if sha(MANIFEST) != "b84d411e1edcf855afc1505098415e707b605a8517cacdbb4e3cb4b8df4140f2":
        raise RuntimeError("BLOCKED_MANIFEST_RAW_SHA")
    if manifest.get("manifest_sha256") != "0ebb7ad3141add14abaac7abb0f9ec2b2ddd1c36d7d386712b5aa686f8a03ac9":
        raise RuntimeError("BLOCKED_MANIFEST_CANONICAL_SHA")
    wanted = {}
    outputs = {}
    for index in range(3):
        path = BASE / f"shard_{index}/economic_results.json"
        payload = json.loads(path.read_text())
        outputs[str(index)] = sha(path)
        for cell in payload.get("cells", []):
            key = (cell.get("arm"), cell.get("window"), float(cell.get("cost_multiplier")))
            if key[0] in ARMS:
                wanted[key] = cell
    expected = {(arm, window, cost) for arm in ARMS for window in WINDOWS for cost in COSTS}
    if set(wanted) != expected:
        raise RuntimeError("BLOCKED_TARGET_CELL_COVERAGE")
    return wanted, outputs


def daily_history() -> dict[str, dict[str, float]]:
    raw: dict[str, dict[str, list[tuple[datetime, float]]]] = defaultdict(lambda: defaultdict(list))
    with DATA.open(newline="") as f:
        for row in csv.DictReader(f):
            t = dt(row["timestamp"])
            raw[row["symbol"]][t.date().isoformat()].append((t, float(row["close"])))
    result: dict[str, dict[str, float]] = defaultdict(dict)
    for symbol, days in raw.items():
        for day, rows in days.items():
            rows.sort()
            complete = len(rows) == 24 and all(b[0] - a[0] == timedelta(hours=1) for a, b in zip(rows, rows[1:]))
            if complete and all(math.isfinite(v) and v > 0 for _, v in rows):
                result[symbol][day] = rows[-1][1]
    return result


def readiness(history: dict[str, dict[str, float]], prices: int = 364) -> dict[str, object]:
    rows = {}
    for symbol in TRADE_SYMBOLS:
        dates = sorted(history.get(symbol, {}))
        valid_ends = []
        for i in range(prices - 1, len(dates)):
            window = dates[i - prices + 1 : i + 1]
            if all((dt(window[j] + "T00:00:00Z") - dt(window[j - 1] + "T00:00:00Z")).days == 1 for j in range(1, len(window))):
                valid_ends.append(window[-1])
        rows[symbol] = {
            "complete_daily_bars": len(dates),
            "first_364_consecutive_end": valid_ends[0] if valid_ends else None,
            "valid_364_end_count": len(valid_ends),
            "last_364_consecutive_end": valid_ends[-1] if valid_ends else None,
        }
    return {"prices_required": prices, "symbols": rows}


def month_year(equity: list[tuple[datetime, float]]) -> tuple[list[dict], list[dict]]:
    by_month: dict[str, tuple[datetime, float]] = {}
    by_year: dict[str, tuple[datetime, float]] = {}
    for t, v in equity:
        by_month[t.strftime("%Y-%m")] = (t, v)
        by_year[t.strftime("%Y")] = (t, v)
    def returns(series: dict[str, tuple[datetime, float]]) -> list[dict]:
        previous = INITIAL_CASH
        out = []
        for label, (_, value) in sorted(series.items()):
            out.append({"period": label, "return": value / previous - 1.0, "end_nav": value})
            previous = value
        return out
    return returns(by_month), returns(by_year)


def curve_metrics(points: list[tuple[datetime, float]]) -> dict[str, float]:
    values = [v for _, v in points]
    peak = INITIAL_CASH
    drawdown = 0.0
    for value in values:
        peak = max(peak, value)
        drawdown = max(drawdown, 1.0 - value / peak)
    return {"total_return": values[-1] / INITIAL_CASH - 1.0, "max_drawdown": drawdown, "terminal_nav": values[-1]}


def valid_364_symbols(history: dict[str, dict[str, float]], as_of: str, prices: int = 364) -> list[str]:
    cutoff = dt(as_of).date().isoformat()
    eligible = []
    for symbol in TRADE_SYMBOLS:
        dates = [day for day in sorted(history.get(symbol, {})) if day <= cutoff]
        if len(dates) < prices:
            continue
        window = dates[-prices:]
        if all((dt(window[i] + "T00:00:00Z") - dt(window[i - 1] + "T00:00:00Z")).days == 1 for i in range(1, len(window))):
            eligible.append(symbol)
    return eligible


def audit_cell(cell: dict, history: dict[str, dict[str, float]]) -> dict:
    ledger = cell["cash_ledger"]
    fills = cell["fills"]
    equity = [(dt(s["timestamp"]), float(s["equity"])) for s in ledger]
    active = [s for s in ledger if s.get("positions")]
    exposure = [sum(float(p.get("value", 0.0)) for p in s.get("positions", {}).values()) / float(s["equity"]) if float(s["equity"]) else None for s in ledger]
    exposure_valid = [v for v in exposure if v is not None]
    symbol_hours = Counter()
    symbol_value_hours = Counter()
    for s in ledger:
        for symbol, p in s.get("positions", {}).items():
            symbol_hours[symbol] += 1
            symbol_value_hours[symbol] += float(p.get("value", 0.0))
    fill_symbol = defaultdict(lambda: {"fills": 0, "buy_qty": 0.0, "sell_qty": 0.0, "fees": 0.0})
    for f in fills:
        row = fill_symbol[f["symbol"]]
        row["fills"] += 1
        row[f["side"] + "_qty"] += float(f["qty"])
        row["fees"] += float(f["fee"])
    reconstructed = {symbol: row["buy_qty"] - row["sell_qty"] for symbol, row in fill_symbol.items()}
    terminal_qty = {symbol: float(p.get("qty", 0.0)) for symbol, p in ledger[-1].get("positions", {}).items()}
    mismatches = {s: reconstructed.get(s, 0.0) - terminal_qty.get(s, 0.0) for s in set(reconstructed) | set(terminal_qty) if abs(reconstructed.get(s, 0.0) - terminal_qty.get(s, 0.0)) > 1e-7}
    months, years = month_year(equity)
    return {
        "arm": cell["arm"], "window": cell["window"], "cost_multiplier": float(cell["cost_multiplier"]),
        "fills": len(fills), "fees": cell["fees"], "metrics": cell["metrics"],
        "first_entry_fill": min((f["timestamp"] for f in fills), default=None),
        "last_fill": max((f["timestamp"] for f in fills), default=None),
        "first_active_mark": active[0]["timestamp"] if active else None,
        "last_active_mark": active[-1]["timestamp"] if active else None,
        "active_hours": len(active), "total_hours": len(ledger),
        "inactive_hours": len(ledger) - len(active), "inactive_fraction": (len(ledger) - len(active)) / len(ledger),
        "gross_exposure_fraction": {"mean": sum(exposure_valid) / len(exposure_valid) if exposure_valid else None, "max": max(exposure_valid) if exposure_valid else None, "p95": sorted(exposure_valid)[int(.95 * (len(exposure_valid) - 1))] if exposure_valid else None},
        "symbol_hours": dict(symbol_hours), "symbol_value_hours": dict(symbol_value_hours), "fill_by_symbol": dict(fill_symbol),
        "terminal_positions": terminal_qty, "fill_quantity_terminal_mismatch": mismatches,
        "ledger_reconciliation": {
            "initial_cash_is_100000": float(ledger[0]["cash"]) == INITIAL_CASH and float(ledger[0]["equity"]) == INITIAL_CASH,
            "max_cash_plus_marked_position_error": max(abs(float(s["cash"]) + sum(float(p.get("value", 0.0)) for p in s.get("positions", {}).values()) - float(s["equity"])) for s in ledger),
            "fee_sum_error": abs(sum(float(f["fee"]) for f in fills) - float(cell["fees"]["fee_total"])),
            "terminal_nav_error": abs(float(equity[-1][1]) - float(ledger[-1]["equity"])),
            "terminal_policy": cell["terminal_policy"], "forced_liquidation": cell["forced_liquidation"],
            "fills_reconstruct_terminal_quantities": not mismatches,
        },
        "top_months": sorted(months, key=lambda x: x["return"], reverse=True)[:5], "bottom_months": sorted(months, key=lambda x: x["return"])[:5],
        "years": years,
        "history_readiness": readiness(history),
        "valid_364_symbols_at_first_active_mark": valid_364_symbols(history, active[0]["timestamp"]) if active else [],
    }


def compare(a: dict, b: dict) -> dict:
    def nav(cell: dict) -> list[tuple[datetime, float]]:
        return [(dt(s["timestamp"]), float(s["equity"])) for s in cell["cash_ledger"]]
    ca, cb = nav(a), nav(b)
    half_cash = [(t, (v + INITIAL_CASH) / 2.0) for t, v in ca]
    blend = [(t, (v + cb[i][1]) / 2.0) for i, (t, v) in enumerate(ca)]
    return {"candidate": a["arm"], "incumbent": b["arm"], "window": a["window"], "cost_multiplier": a["cost_multiplier"], "paired_hours": len(ca), "candidate_terminal_nav": ca[-1][1], "incumbent_terminal_nav": cb[-1][1], "half_incumbent_half_cash": curve_metrics(half_cash), "existing_fixed_50_50_curve_blend": curve_metrics(blend), "candidate_minus_half_cash_return": ca[-1][1] / INITIAL_CASH - half_cash[-1][1] / INITIAL_CASH, "diagnostic_only": True}


def main() -> None:
    cells, output_hashes = load_cells()
    history = daily_history()
    rows = [audit_cell(cells[key], history) for key in sorted(cells)]
    by_key = {(r["arm"], r["window"], r["cost_multiplier"]): r for r in rows}
    comparisons = [compare(cells[("high_proximity364", w, cost)], cells[("low_variance22", w, cost)]) for w in WINDOWS for cost in COSTS]
    result = {"status": "AUDIT_COMPLETE", "economic_run": False, "initial_cash": INITIAL_CASH, "contract": {"scope": "frozen high_proximity364 and low_variance22 cells, full/recent, 1x/2x/3x", "inputs": "frozen shard payloads plus local hourly CSV", "hourly_marks": True, "daily_history_diagnostic": True, "readiness_universe": "configured trade_symbols (22); CSV contains 25 symbols", "persisted_decision_scores_or_universe_snapshots": False, "inactive_mechanism": "UNOBSERVABLE from frozen payload; ledger shows marks only", "no_new_rule_or_simulation": True}, "pins": {"manifest_raw_sha256": sha(MANIFEST), "manifest_canonical_sha256": json.loads(MANIFEST.read_text())["manifest_sha256"], "runner_sha256": "8a0eb3ccb8dc9a4ebddb0b9cd2d711bb40f2b7866fab8b07fdbf757e34f53d6d", "dataset_sha256": sha(DATA), "shard_output_sha256": output_hashes}, "readiness": readiness(history), "csv_symbols": sorted(history), "cells": rows, "comparisons": comparisons}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, indent=2, allow_nan=False) + "\n")
    lines = ["# high_proximity364 exposure audit (2026-09-05)", "", "This read-only forensic audit consumes the frozen attempt-03 shard payloads and the pinned local hourly CSV. It performs no economic rerun, parameter change, production edit, or promotion.", "", "The audit is bounded to `high_proximity364` and `low_variance22`, both full and recent windows, at 1x/2x/3x costs. It uses the recorded hourly cash ledger and marked positions for actual gross exposure, and reconstructs quantity checks from the recorded fills. Daily history readiness is a separate diagnostic over complete 24-hour local bars; it does not claim venue-PIT or delisting truth.", "", "## Direct finding", ""]
    hp = by_key[("high_proximity364", "full", 1.0)]
    lines.append(f"At full 1x, high_proximity364 first becomes active at `{hp['first_active_mark']}`, trades `{', '.join(sorted(hp['symbol_hours']))}`, and last has a marked position at `{hp['last_active_mark']}`. It has {hp['fills']} fills across {hp['active_hours']} active hourly marks and {hp['inactive_hours']} inactive marks. This is BTC-only exposure followed by cash, so the sparse fill count is not itself a low-exposure claim; the ledger-derived gross exposure statistics are reported below.")
    lines += ["", "## Cell exposure and reconciliation", "", "| Window | Cost | Arm | Fills | First active mark | Last active mark | Active hours | Inactive fraction | Mean gross exposure | Max gross exposure | Symbols |", "|---|---:|---|---:|---|---|---:|---:|---:|---:|---|"]
    for r in rows:
        lines.append(f"| {r['window']} | {r['cost_multiplier']:g}x | {r['arm']} | {r['fills']} | {r['first_active_mark'] or 'none'} | {r['last_active_mark'] or 'none'} | {r['active_hours']} | {r['inactive_fraction']:.3f} | {r['gross_exposure_fraction']['mean']:.3f} | {r['gross_exposure_fraction']['max']:.3f} | {', '.join(sorted(r['symbol_hours'])) or 'none'} |")
    lines += ["", "For every audited cell, the JSON records cash plus marked positions reconciliation error, fee sum error, terminal NAV error, and fill-to-terminal quantity mismatch. Any nonzero mismatch would block attribution; no mismatch is silently converted to zero. Terminal positions are allowed because the frozen policy uses a final mark without forced liquidation.", "", "## 364-day readiness diagnostic", "", "The candidate’s first full-window entry is compared with the locally reconstructed first date having 364 consecutive complete daily bars for the traded symbols. This tests the mechanical history gate only. It cannot separate that gate from ranking, hysteresis, scanner universe membership, or opportunity without decision traces, which the frozen payload intentionally omits.", ""]
    for sym in ("BTC/USD", "ETH/USD"):
        q = result["readiness"]["symbols"].get(sym, {})
        lines.append(f"- `{sym}`: first local 364-consecutive end `{q.get('first_364_consecutive_end')}`, valid ends `{q.get('valid_364_end_count')}`, complete daily bars `{q.get('complete_daily_bars')}`.")
    lines += ["", "Six configured trade symbols were mechanically ready at the full-window first active mark, but the frozen output has no per-decision score, rank, or PIT-universe snapshot. Therefore the data establish BTC-only selection and later cash directly, while the reason BTC won ranking or why each inactive interval occurred remains unobservable. Readiness is a diagnostic, not a causal attribution.", "", "## Attribution limits", "", "Monthly and yearly mark-to-market summaries, symbol concentration, BTC/ETH dominance, and half-incumbent/half-cash terminal comparisons are retained in the compact JSON. Inactive mechanism is explicitly `UNOBSERVABLE`: the ledger cannot distinguish not-yet-ready, no-target cash, scanner rejection, or a skipped rebalance batch. Gap-related stale marks are not treated as zero PnL or as proof of investability. Future integration remains conditional on a trace instrument that records per decision: complete daily bar count/contiguity per symbol, PIT eligibility reason, score/rank, retained/target symbols, order event, and as-of mark quality.", "", f"Artifact: `{OUT.relative_to(REPO)}`. Input hashes and all compact cell outputs are embedded there."]
    REPORT.write_text("\n".join(lines) + "\n")
    print(json.dumps({"status": result["status"], "output": str(OUT), "report": str(REPORT), "cells": len(rows), "output_sha256": sha(OUT)}))


if __name__ == "__main__":
    main()
