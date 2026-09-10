"""Run the frozen A01-A06 immediate candidate matrix.

Research-only and network-free.  This runner intentionally keeps the signal
families local to this file: it does not register a product strategy and it
never edits V3, configuration, runtime databases, PAPER, or LIVE state.
"""

from __future__ import annotations

import bisect
import csv
import hashlib
import json
import math
import random
import statistics
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
MANIFEST_PATH = REPO / "data/research_manifests/immediate_candidate_freeze_2026_09_09.json"
OUT_DIR = REPO / "data/research_runs/immediate_candidates_2026_09_09"
RESULTS = OUT_DIR / "results.json"
LEDGER = OUT_DIR / "ledger.jsonl"
REPORT = REPO / "docs/research/immediate_candidates_results_2026_09_09.md"
CUTOFF = "2026-07-31"
DATA_START = "2020-01-01"
INITIAL_CASH = 100_000.0
EQUITY_ALLOCATION = 0.55
TOP_N = 8
MAX_SINGLE = 0.10
LAG_DAYS = (1, 3, 7)
COSTS = (1.0, 2.0, 3.0)
ROW_DATES: dict[int, list[str]] = {}
TARGET_CACHE: dict[tuple[str, str], tuple[str, ...]] = {}
SUPPORTED_DATES: list[str] = []

INPUTS = {
    "bars": "data/historical/equity_pit_symmetric_2016_2026_v5.csv",
    "lifetimes": "data/historical/equity_entity_lifetimes_full_v5.csv",
    "corporate_actions": "data/historical/equity_survivorship_full_corporate_actions_v5.csv",
    "pit_intervals": "data/research/external/equity_pit_universe_2026_09_07/pit_eligibility_intervals.jsonl",
    "settings": "config/settings.yaml",
    "v3_reference": "data/research_runs/h14_v3_daily_nav_comparability_2026_09_09/current_v3_daily_nav_comparability.json",
}

WINDOWS = {
    "full": ("2022-01-01", "2025-12-31"),
    "recent": ("2024-01-01", "2025-12-31"),
    "true_holdout": ("2026-01-01", "2026-07-31"),
}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def parse_ts(value: str) -> str:
    return value[:10]


def session_timestamp(day: str) -> str:
    """Stable UTC session-boundary timestamp for research ledger metadata."""
    return f"{day}T00:00:00Z"


def load_manifest() -> dict[str, Any]:
    payload = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    if payload.get("status") != "FROZEN_PRE_OUTCOME" or payload.get("economic_run") is not False:
        raise RuntimeError("BLOCKED_MANIFEST_STATUS")
    if payload.get("outcome_scan_performed") is not False:
        raise RuntimeError("BLOCKED_OUTCOME_SCAN_MARKER")
    copy = dict(payload)
    expected = copy.pop("protocol_sha256")
    actual = hashlib.sha256(json.dumps(copy, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    if expected != actual:
        raise RuntimeError("BLOCKED_MANIFEST_CANONICAL_HASH")
    if sha(MANIFEST_PATH) != "8cec049f1d61d929f001b53af43a7e46ede1619243a1605e30d1dc312dd9965b":
        raise RuntimeError("BLOCKED_MANIFEST_PHYSICAL_HASH")
    if expected != "c6230ec5c8c3ef37a761c30d7425275e523528155ab73232e6d5a351ca2c27c1":
        raise RuntimeError("BLOCKED_MANIFEST_EXPECTED_CANONICAL_HASH")
    for key, relative in INPUTS.items():
        path = REPO / relative
        if not path.is_file():
            raise RuntimeError(f"BLOCKED_INPUT_MISSING:{key}")
    return payload


def verify_input_hashes(payload: dict[str, Any]) -> dict[str, str]:
    observed: dict[str, str] = {}
    inventory = payload["data_inventory"]
    for key, relative in INPUTS.items():
        path = REPO / relative
        observed[key] = sha(path)
        if key in inventory and observed[key] != inventory[key]["sha256"]:
            raise RuntimeError(f"BLOCKED_INPUT_HASH:{key}")
    return observed


def load_bars() -> tuple[dict[str, list[tuple[str, float, float, float, float, float]]], list[str], int]:
    by_symbol: dict[str, list[tuple[str, float, float, float, float, float]]] = defaultdict(list)
    all_dates: set[str] = set()
    row_count = 0
    post_cutoff = 0
    path = REPO / INPUTS["bars"]
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        required = ["timestamp", "symbol", "open", "high", "low", "close", "volume", "market"]
        if reader.fieldnames != [*required[:1], "symbol", "timeframe", *required[2:]]:
            raise RuntimeError("BLOCKED_BAR_SCHEMA")
        for row in reader:
            row_count += 1
            day = parse_ts(row["timestamp"])
            if day > CUTOFF:
                post_cutoff += 1
                continue
            all_dates.add(day)
            if day < DATA_START:
                continue
            if row["market"].casefold() != "equity":
                raise RuntimeError("BLOCKED_NON_EQUITY_BAR")
            values = tuple(float(row[field]) for field in ("open", "high", "low", "close", "volume"))
            if not all(math.isfinite(value) for value in values) or values[3] <= 0 or values[0] <= 0:
                raise RuntimeError("BLOCKED_NONFINITE_BAR")
            by_symbol[row["symbol"]].append((day, *values))
    if post_cutoff:
        raise RuntimeError(f"BLOCKED_POST_CUTOFF_ROWS:{post_cutoff}")
    for symbol, rows in by_symbol.items():
        rows.sort(key=lambda item: item[0])
        if any(a[0] == b[0] for a, b in zip(rows, rows[1:])):
            raise RuntimeError(f"BLOCKED_DUPLICATE_BAR:{symbol}")
        ROW_DATES[id(rows)] = [row[0] for row in rows]
    dates = sorted(day for day in all_dates if day >= "2021-01-01")
    SUPPORTED_DATES[:] = dates
    return by_symbol, dates, row_count


def load_pit_intervals() -> dict[str, list[tuple[str, str | None]]]:
    intervals: dict[str, list[tuple[str, str | None]]] = defaultdict(list)
    path = REPO / INPUTS["pit_intervals"]
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            intervals[row["canonical_symbol"]].append((parse_ts(row["valid_from"]), parse_ts(row["valid_to"]) if row.get("valid_to") else None))
    return intervals


def load_dividends() -> dict[str, dict[str, float]]:
    """Return cash dividends on the split-adjusted share basis of the bars."""
    raw: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    splits: dict[str, list[tuple[str, float]]] = defaultdict(list)
    seen_cash_rows: set[tuple[tuple[str, str], ...]] = set()
    path = REPO / INPUTS["corporate_actions"]
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            day = (row.get("ex_date") or "")[:10]
            if not day or day > CUTOFF:
                continue
            if "split" in (row.get("action_type") or ""):
                try:
                    ratio = float(row["new_rate"]) / float(row["old_rate"])
                except (KeyError, TypeError, ValueError, ZeroDivisionError):
                    continue
                if math.isfinite(ratio) and ratio > 0:
                    splits[row["symbol"]].append((day, ratio))
                continue
            if row.get("action_type") != "cash_dividends":
                continue
            # The frozen corporate-action export contains records duplicated
            # under different raw_id values.  Keep distinct same-day rates
            # additive (special/regular distributions), but do not credit an
            # identical event twice.
            cash_fingerprint = tuple(sorted((key, value) for key, value in row.items() if key != "raw_id"))
            if cash_fingerprint in seen_cash_rows:
                continue
            seen_cash_rows.add(cash_fingerprint)
            amount = row.get("amount") or row.get("rate") or row.get("cash_amount") or ""
            if amount:
                try:
                    value = float(amount)
                except ValueError:
                    continue
                if math.isfinite(value) and value > 0:
                    # Multiple CA records on one ex-date are additive events.
                    raw[row["symbol"]][day] += value
    dividends: dict[str, dict[str, float]] = defaultdict(dict)
    for symbol, values in raw.items():
        for day, value in values.items():
            future_split_factor = math.prod(ratio for split_day, ratio in splits.get(symbol, ()) if split_day > day)
            dividends[symbol][day] = value / future_split_factor
    return dividends


def load_lifetimes() -> dict[str, tuple[str, str]]:
    out: dict[str, tuple[str, str]] = {}
    with (REPO / INPUTS["lifetimes"]).open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            last = row.get("last_observed_bar")
            if last:
                out[row["canonical_symbol"]] = (parse_ts(row.get("first_observed_bar", DATA_START)), parse_ts(last))
    return out


def pit_ok(intervals: dict[str, list[tuple[str, str | None]]], symbol: str, day: str) -> bool:
    return any(start <= day and (end is None or day <= end) for start, end in intervals.get(symbol, ()))


def aligned_window(rows: list[tuple[str, float, float, float, float, float]], day: str, length: int) -> bool:
    """Require every lookback observation to be a consecutive supported session."""
    index = idx_at(rows, day)
    global_index = bisect.bisect_right(SUPPORTED_DATES, day) - 1
    if index < length - 1 or global_index < length - 1:
        return False
    return ROW_DATES[id(rows)][index - length + 1 : index + 1] == SUPPORTED_DATES[global_index - length + 1 : global_index + 1]


def scanner_ok(rows: list[tuple[str, float, float, float, float, float]], day: str, *, zero_volume_excluded: bool = False) -> tuple[bool, float]:
    """Apply the frozen V3 equity scanner gates on strictly prior bars."""
    index = idx_at(rows, day)
    if index < 20:
        return False, 0.0
    if not aligned_window(rows, day, 20):
        return False, 0.0
    window = rows[index - 19 : index + 1]
    if len(window) != 20 or window[-1][0] > day:
        return False, 0.0
    if any(row[4] < 5.0 or row[5] < 0 or not math.isfinite(row[4] * row[5]) for row in window):
        return False, 0.0
    if zero_volume_excluded and window[-1][5] <= 0:
        return False, 0.0
    dollar_volume = sorted(row[4] * row[5] for row in window)
    median_dv = (dollar_volume[9] + dollar_volume[10]) / 2.0
    if median_dv < 20_000_000:
        return False, median_dv
    # Production's daily ATR gate is retained as a direct OHLC quality gate.
    atr_pct = statistics.fmean((row[2] - row[3]) / row[4] for row in window)
    if not (0.005 <= atr_pct <= 0.12):
        return False, median_dv
    return True, median_dv


def idx_at(rows: list[tuple[str, float, float, float, float, float]], day: str) -> int:
    return bisect.bisect_right(ROW_DATES[id(rows)], day) - 1


def feature(candidate: str, rows: list[tuple[str, float, float, float, float, float]], day: str) -> float | None:
    index = idx_at(rows, day)
    periods = {"A01": 22, "A02": 5, "A03": 60, "A04": 126, "A05": 252, "A06": 20}[candidate]
    required = periods + 1 if candidate in {"A01", "A02", "A05", "A06"} else periods
    if index < required - 1 or not aligned_window(rows, day, required):
        return None
    window = rows[index - required + 1 : index + 1]
    closes = [row[4] for row in window]
    if any(value <= 0 or not math.isfinite(value) for value in closes):
        return None
    if candidate == "A01":
        returns = [closes[i] / closes[i - 1] - 1 for i in range(1, len(closes))]
        if len(returns) != 22:
            return None
        mean = sum(returns) / len(returns)
        return math.sqrt(sum((value - mean) ** 2 for value in returns) / len(returns))
    if candidate == "A02":
        return closes[-1] / closes[-6] - 1
    if candidate == "A03":
        vols = [row[5] for row in window]
        if any(value <= 0 or not math.isfinite(value) for value in vols):
            return None
        recent = sorted(closes[-20 + i] * vols[-20 + i] for i in range(20))
        long = sorted(closes[i] * vols[i] for i in range(len(window)))
        return statistics.median(recent) / statistics.median(long) if statistics.median(long) > 0 else None
    if candidate == "A04":
        ys = [math.log(value) for value in closes]
        n = len(ys)
        mean_x = (n - 1) / 2
        mean_y = statistics.fmean(ys)
        denom = sum((x - mean_x) ** 2 for x in range(n))
        return sum((x - mean_x) * (y - mean_y) for x, y in enumerate(ys)) / denom if denom else None
    if candidate == "A05":
        highs = [row[2] for row in window[:-1]]
        return closes[-1] / max(highs) - 1 if highs and all(math.isfinite(value) and value > 0 for value in highs) else None
    if candidate == "A06":
        path = sum(abs(closes[i] - closes[i - 1]) for i in range(1, len(closes)))
        return abs(closes[-1] - closes[0]) / path if path > 0 else None
    raise RuntimeError(f"unknown candidate {candidate}")


def order_targets(candidate: str, signal_day: str, bars: dict[str, list[tuple[str, float, float, float, float, float]]], intervals: dict[str, list[tuple[str, str | None]]]) -> tuple[str, ...]:
    cached = TARGET_CACHE.get((candidate, signal_day))
    if cached is not None:
        return cached
    values: list[tuple[float, str]] = []
    liquidity: list[tuple[float, str, float]] = []
    for symbol, rows in bars.items():
        if symbol == "SPY" or not pit_ok(intervals, symbol, signal_day):
            continue
        okay, median_dv = scanner_ok(rows, signal_day, zero_volume_excluded=candidate == "A03")
        if not okay:
            continue
        value = feature(candidate, rows, signal_day)
        if value is None:
            continue
        liquidity.append((median_dv, symbol, value))
    # Match the product Stage-A target shortlist before alpha ranking.
    liquidity.sort(key=lambda item: (-item[0], item[1]))
    for _median_dv, symbol, value in liquidity[:300]:
        values.append((value, symbol))
    values.sort(key=lambda item: (item[0], item[1]), reverse=candidate in {"A03", "A04", "A05", "A06"})
    target = tuple(symbol for _value, symbol in values[:TOP_N])
    TARGET_CACHE[(candidate, signal_day)] = target
    return target


def period_key(day: str, cadence: str) -> str:
    parsed = datetime.strptime(day, "%Y-%m-%d").date()
    if cadence == "monthly":
        return parsed.strftime("%Y-%m")
    return f"{parsed.isocalendar().year:04d}-W{parsed.isocalendar().week:02d}"


def rebalance_days(dates: list[str], cadence: str) -> list[str]:
    out: list[str] = []
    previous = None
    for day in dates:
        key = period_key(day, cadence)
        if key != previous:
            out.append(day)
            previous = key
    return out


def costs_for_trade(notional: float, side: str, multiplier: float) -> tuple[float, float, float]:
    # Existing settings: 2 bps slippage and 2 bps spread.  Regulatory fee
    # rates are the current effective-dated rates used by the product model;
    # the research ledger keeps all three components visible.
    slippage = notional * (2.0 * multiplier) / 10_000
    spread = notional * (2.0 * multiplier) / 10_000
    fee = notional * (0.0000206 if side == "sell" else 0.000003) * multiplier
    return slippage, spread, fee


def simulate(candidate: str, lag: int, cost: float, bars: dict[str, list[tuple[str, float, float, float, float, float]]], dates: list[str], intervals: dict[str, list[tuple[str, str | None]]], dividends: dict[str, dict[str, float]], lifetimes: dict[str, tuple[str, str]], *, schedule_shift_sessions: int = 0) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, float]]:
    cadence = "weekly" if candidate == "A02" else "monthly"
    active_dates = [day for day in dates if "2022-01-01" <= day <= CUTOFF]
    decisions = rebalance_days(active_dates, cadence)
    date_index = {day: index for index, day in enumerate(dates)}
    targets_by_fill: dict[str, tuple[str, str, tuple[str, ...]]] = {}
    ledger: list[dict[str, Any]] = []
    cash = INITIAL_CASH
    holdings: dict[str, float] = {}
    equity_curve: list[tuple[str, float]] = []
    total_slippage = total_spread = total_fees = turnover = 0.0
    for decision in decisions:
        dindex = date_index[decision]
        scheduled_index = dindex + schedule_shift_sessions
        if scheduled_index >= len(dates):
            continue
        scheduled_decision = dates[scheduled_index]
        signal_index = scheduled_index - lag
        if signal_index < 0:
            continue
        signal_day = dates[signal_index]
        target = order_targets(candidate, signal_day, bars, intervals)
        fill_index = scheduled_index + 1
        if fill_index >= len(dates):
            continue
        fill_day = dates[fill_index]
        targets_by_fill[fill_day] = (scheduled_decision, signal_day, target)
    for index, day in enumerate(active_dates):
        if day in targets_by_fill:
            decision_day, signal_day, target = targets_by_fill[day]
            previous = dict(holdings)
            target_weight = EQUITY_ALLOCATION / len(target) if target else 0.0
            target_values = {symbol: INITIAL_CASH * target_weight for symbol in target}
            current_equity = cash + sum(quantity * bars[symbol][idx_at(bars[symbol], day)][4] for symbol, quantity in holdings.items())
            target_values = {symbol: current_equity * target_weight for symbol in target}
            symbols = sorted(set(previous) | set(target_values))
            for symbol in symbols:
                row = bars.get(symbol, ())
                if not row:
                    continue
                price = row[idx_at(row, day)][1]
                old_value = previous.get(symbol, 0.0) * price
                new_value = target_values.get(symbol, 0.0)
                delta = new_value - old_value
                if abs(delta) < 1e-9:
                    continue
                side = "buy" if delta > 0 else "sell"
                notional = abs(delta)
                slip, spread, fee = costs_for_trade(notional, side, cost)
                pre_weight = old_value / max(current_equity, 1.0)
                if side == "buy":
                    cash -= notional + slip + spread + fee
                else:
                    cash += notional - slip - spread - fee
                quantity = new_value / price if new_value else 0.0
                if quantity:
                    holdings[symbol] = quantity
                else:
                    holdings.pop(symbol, None)
                total_slippage += slip
                total_spread += spread
                total_fees += fee
                turnover += notional / max(current_equity, 1.0)
                ledger.append({"kind": "fill", "candidate": candidate, "lag": lag, "cost": cost, "decision_day": decision_day, "decision_timestamp": session_timestamp(decision_day), "signal_day": signal_day, "signal_asof_timestamp": session_timestamp(signal_day), "fill_day": day, "fill_timestamp": session_timestamp(day), "symbol": symbol, "side": side, "pre_weight": pre_weight, "target_weight": target_weight if symbol in target_values else 0.0, "filled_weight": quantity * price / max(current_equity, 1.0), "quantity": quantity, "notional": notional, "qty": quantity, "reference_price": price, "slippage": slip, "spread": spread, "fee": fee})
            ledger.append({"kind": "target", "candidate": candidate, "lag": lag, "cost": cost, "decision_day": decision_day, "decision_timestamp": session_timestamp(decision_day), "signal_day": signal_day, "signal_asof_timestamp": session_timestamp(signal_day), "fill_day": day, "fill_timestamp": session_timestamp(day), "symbols": list(target), "target_count": len(target)})
        # Mark-to-market and cash dividends at end of each supported session.
        for symbol, quantity in list(holdings.items()):
            if day in dividends.get(symbol, {}):
                cash += quantity * dividends[symbol][day]
        # Terminal entity: force liquidation on its last observed bar.
        pre_terminal_equity = cash + sum(quantity * bars[symbol][idx_at(bars[symbol], day)][4] for symbol, quantity in holdings.items())
        for symbol, quantity in list(holdings.items()):
            if symbol in lifetimes and lifetimes[symbol][1] == day:
                row = bars[symbol][idx_at(bars[symbol], day)]
                notional = quantity * row[4]
                slip, spread, fee = costs_for_trade(notional, "sell", cost)
                cash += notional - slip - spread - fee
                total_slippage += slip
                total_spread += spread
                total_fees += fee
                turnover += notional / max(pre_terminal_equity, 1.0)
                ledger.append({"kind": "terminal", "candidate": candidate, "lag": lag, "cost": cost, "decision_day": day, "decision_timestamp": session_timestamp(day), "signal_day": day, "signal_asof_timestamp": session_timestamp(day), "fill_day": day, "fill_timestamp": session_timestamp(day), "symbol": symbol, "side": "sell", "pre_weight": notional / max(pre_terminal_equity, 1.0), "target_weight": 0.0, "filled_weight": 0.0, "quantity": 0.0, "qty": 0.0, "notional": notional, "reference_price": row[4], "slippage": slip, "spread": spread, "fee": fee, "terminal_policy": "T1_LAST_CLOSE"})
                holdings.pop(symbol, None)
        equity = cash + sum(quantity * bars[symbol][idx_at(bars[symbol], day)][4] for symbol, quantity in holdings.items())
        equity_curve.append((day, equity))
    daily = [equity_curve[i][1] / equity_curve[i - 1][1] - 1 for i in range(1, len(equity_curve)) if equity_curve[i - 1][1] > 0]
    values = [value for _day, value in equity_curve]
    peak = INITIAL_CASH
    drawdown = 0.0
    for value in values:
        peak = max(peak, value)
        drawdown = max(drawdown, 1 - value / peak)
    downside = [value for value in daily if value < 0]
    cvar = statistics.fmean(sorted(daily)[: max(1, math.ceil(len(daily) * 0.05))]) if daily else 0.0
    sharpe = statistics.fmean(daily) / statistics.pstdev(daily) * math.sqrt(252) if len(daily) > 1 and statistics.pstdev(daily) else 0.0
    sortino = statistics.fmean(daily) / math.sqrt(statistics.fmean([v * v for v in downside])) * math.sqrt(252) if downside else 0.0
    total_return = values[-1] / INITIAL_CASH - 1 if values else 0.0
    years = max((len(values) / 252), 1 / 252)
    metrics = {"start": equity_curve[0][0] if equity_curve else None, "end": equity_curve[-1][0] if equity_curve else None, "days": len(values), "ending_nav": values[-1] if values else INITIAL_CASH, "total_return": total_return, "cagr": (1 + total_return) ** (1 / years) - 1 if 1 + total_return > 0 else -1.0, "sharpe": sharpe, "sortino": sortino, "max_drawdown": drawdown, "cvar05": cvar, "worst_day": min(daily) if daily else 0.0, "turnover": turnover, "slippage": total_slippage, "spread": total_spread, "fees": total_fees, "fills": sum(1 for row in ledger if row["kind"] == "fill"), "terminal_fills": sum(1 for row in ledger if row["kind"] == "terminal"), "rebalances": sum(1 for row in ledger if row["kind"] == "target"), "finite": all(math.isfinite(float(value)) for value in (total_return, drawdown, sharpe, sortino, cvar, turnover))}
    return metrics, ledger, {day: equity for day, equity in equity_curve}


def slice_metrics(curve: dict[str, float], start: str, end: str) -> dict[str, Any]:
    rows = sorted((day, value) for day, value in curve.items() if start <= day <= end)
    if len(rows) < 2:
        return {"days": len(rows), "complete": False}
    vals = [value for _day, value in rows]
    rets = [vals[i] / vals[i - 1] - 1 for i in range(1, len(vals)) if vals[i - 1] > 0]
    peak = vals[0]
    max_dd = 0.0
    for value in vals:
        peak = max(peak, value)
        max_dd = max(max_dd, 1 - value / peak)
    neg = [value for value in rets if value < 0]
    vol = statistics.pstdev(rets) if len(rets) > 1 else 0.0
    return {"days": len(vals), "start": rows[0][0], "end": rows[-1][0], "total_return": vals[-1] / vals[0] - 1, "sharpe": statistics.fmean(rets) / vol * math.sqrt(252) if vol else 0.0, "sortino": statistics.fmean(rets) / math.sqrt(statistics.fmean([v * v for v in neg])) * math.sqrt(252) if neg else 0.0, "max_drawdown": max_dd, "cvar05": statistics.fmean(sorted(rets)[:max(1, math.ceil(len(rets) * 0.05))]) if rets else 0.0, "worst_day": min(rets) if rets else 0.0, "complete": True}


def bootstrap(values: list[float], *, seed: int, n: int = 10_000, block: int = 21) -> dict[str, float]:
    if not values:
        return {"resamples": 0, "mean": 0.0, "p05": 0.0, "p95": 0.0}
    rng = random.Random(seed)
    samples: list[float] = []
    for _ in range(n):
        path: list[float] = []
        while len(path) < len(values):
            start = rng.randrange(len(values))
            path.extend(values[start : start + block] or values[start : start + 1])
        path = path[: len(values)]
        samples.append(statistics.fmean(path))
    samples.sort()
    return {"resamples": n, "mean": statistics.fmean(samples), "p05": samples[int(0.05 * (n - 1))], "p95": samples[int(0.95 * (n - 1))]}


def permutation(values: list[float], *, seed: int, n: int = 10_000) -> dict[str, float]:
    if not values:
        return {"resamples": 0, "p_value_two_sided": 1.0}
    observed = abs(statistics.fmean(values))
    rng = random.Random(seed)
    exceed = 0
    for _ in range(n):
        signed = [value if rng.getrandbits(1) else -value for value in values]
        if abs(statistics.fmean(signed)) >= observed:
            exceed += 1
    return {"resamples": n, "p_value_two_sided": (exceed + 1) / (n + 1)}


def paired_diagnostics(candidate: str, curve: dict[str, float], v3_nav: dict[str, float], v3_weights: dict[str, dict[str, float]], bars: dict[str, list[tuple[str, float, float, float, float, float]]], intervals: dict[str, list[tuple[str, str | None]]]) -> dict[str, Any]:
    common = sorted(set(curve) & set(v3_nav))
    candidate_returns: list[float] = []
    v3_returns: list[float] = []
    overlaps: list[float] = []
    for previous, day in zip(common[:-1], common[1:]):
        if curve[previous] <= 0 or v3_nav[previous] <= 0:
            continue
        cr = curve[day] / curve[previous] - 1
        vr = v3_nav[day] / v3_nav[previous] - 1
        candidate_returns.append(cr)
        v3_returns.append(vr)
        v3_names = set(v3_weights.get(day, {}))
        if v3_names:
            overlaps.append(len(v3_names & set(order_targets(candidate, previous, bars, intervals))) / TOP_N)
    corr = 0.0
    if len(candidate_returns) > 1:
        cm = statistics.fmean(candidate_returns)
        vm = statistics.fmean(v3_returns)
        cd = math.sqrt(sum((x - cm) ** 2 for x in candidate_returns))
        vd = math.sqrt(sum((x - vm) ** 2 for x in v3_returns))
        corr = sum((x - cm) * (y - vm) for x, y in zip(candidate_returns, v3_returns)) / (cd * vd) if cd and vd else 0.0
    down = [cr for cr, vr in zip(candidate_returns, v3_returns) if vr < 0]
    up = [cr for cr, vr in zip(candidate_returns, v3_returns) if vr >= 0]
    yearly: dict[str, list[float]] = defaultdict(list)
    for day, value in sorted(curve.items()):
        if day in v3_nav:
            yearly[day[:4]].append(value)
    return {"exact_date_daily_return_correlation": corr, "mean_name_overlap_fraction": statistics.fmean(overlaps) if overlaps else 0.0, "v3_down_contribution": {"v3_down_days": len(down), "candidate_mean_return": statistics.fmean(down) if down else 0.0, "candidate_cvar05": statistics.fmean(sorted(down)[:max(1, math.ceil(len(down) * 0.05))]) if down else 0.0}, "declared_regime_means": {"v3_down": statistics.fmean(down) if down else 0.0, "v3_non_down": statistics.fmean(up) if up else 0.0}, "calendar_year_observation_counts": {year: len(values) for year, values in sorted(yearly.items())}}


def main() -> None:
    manifest = load_manifest()
    observed_hashes = verify_input_hashes(manifest)
    bars, dates, row_count = load_bars()
    intervals = load_pit_intervals()
    dividends = load_dividends()
    lifetimes = load_lifetimes()
    v3 = json.loads((REPO / INPUTS["v3_reference"]).read_text(encoding="utf-8"))
    v3_nav = {row["date"]: float(row["nav"]) for row in v3["daily_nav"] if row["date"] <= CUTOFF}
    v3_weights = {row["timestamp"][:10]: row["weights"] for row in v3["weight_history"] if row["timestamp"][:10] <= CUTOFF}
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    if LEDGER.exists():
        LEDGER.unlink()
    cases: list[dict[str, Any]] = []
    primary_curves: dict[str, dict[str, float]] = {}
    for candidate in manifest["candidates"]:
        cid = candidate["id"]
        for lag in LAG_DAYS:
            for cost in COSTS:
                metrics, ledger, curve = simulate(cid, lag, cost, bars, dates, intervals, dividends, lifetimes)
                with LEDGER.open("a", encoding="utf-8") as handle:
                    for row in ledger:
                        handle.write(json.dumps(row, sort_keys=True) + "\n")
                windows = {name: slice_metrics(curve, *bounds) for name, bounds in WINDOWS.items()}
                # Fixed 95/5 overlay uses only exact same-date V3 NAV rows.
                common = sorted(set(curve) & set(v3_nav))
                overlay = {day: 0.95 * v3_nav[day] + 0.05 * curve[day] for day in common}
                overlap = []
                capacity_records = []
                max_total_positions = manifest["evaluation"]["v3_overlay"]["caps"]["max_total_positions"]
                for day in common:
                    v3_names = set(v3_weights.get(day, {}))
                    if not v3_names:
                        continue
                    candidate_limit = max(0, min(TOP_N, max_total_positions - len(v3_names)))
                    candidate_names = order_targets(cid, day, bars, intervals)[:candidate_limit]
                    names = v3_names | set(candidate_names)
                    eligible = candidate_limit > 0 and len(names) <= max_total_positions
                    overlap.append(eligible)
                    capacity_records.append({"date": day, "dated_v3_position_count": len(v3_names), "candidate_position_limit": candidate_limit, "candidate_positions_used": len(candidate_names), "eligible": eligible, "name_overlap_count": len(v3_names & set(candidate_names))})
                capacity_days = sum(overlap)
                position_counts = [row["dated_v3_position_count"] for row in capacity_records]
                capacity = {"eligible_days": capacity_days, "v3_weights_preserved": True, "no_forward_fill": True, "caps": manifest["evaluation"]["v3_overlay"]["caps"], "dated_v3_state_days": len(capacity_records), "dated_v3_position_count_min": min(position_counts) if position_counts else None, "dated_v3_position_count_max": max(position_counts) if position_counts else None, "candidate_position_rule": "min(candidate_top_n, max_total_positions - dated_v3_position_count)", "candidate_position_limit_zero_days": sum(row["candidate_position_limit"] == 0 for row in capacity_records)}
                case = {"candidate_id": cid, "name": candidate["name"], "lag": lag, "cost_multiplier": cost, "metrics": metrics, "windows": windows, "paired_diagnostics": paired_diagnostics(cid, curve, v3_nav, v3_weights, bars, intervals), "overlay": {"common_exact_v3_dates": len(common), "first": common[0] if common else None, "last": common[-1] if common else None, "fixed_95_5": {name: slice_metrics(overlay, *bounds) for name, bounds in WINDOWS.items()}, "capacity_aware": capacity}, "verdict": {"status": "RESEARCH_ONLY_NO_PROMOTION", "data_gate": "PASS", "statistical_gate": "REPORTED_NO_AUTO_ADMISSION", "capacity_gate": "REPORTED_NO_AUTO_ADMISSION"}, "economic_run": True, "promotion": False, "integration": False}
                cases.append(case)
                if lag == 1 and cost == 1.0:
                    primary_curves[cid] = curve
    # Primary-only statistical diagnostics are predeclared and repeated with fixed seeds.
    stats: dict[str, Any] = {}
    for cid, curve in primary_curves.items():
        common = sorted(set(curve) & set(v3_nav))
        values = [(curve[day] / curve[prev] - 1) - (v3_nav[day] / v3_nav[prev] - 1) for prev, day in zip(common[:-1], common[1:]) if curve[prev] > 0 and v3_nav[prev] > 0]
        _placebo_metrics, _placebo_ledger, placebo_curve = simulate(cid, 1, 1.0, bars, dates, intervals, dividends, lifetimes, schedule_shift_sessions=7)
        stats[cid] = {"bootstrap_21_session": bootstrap(values, seed=20260909), "paired_sign_permutation": permutation(values, seed=20260910), "control": "V3 exact-date daily NAV", "paired_observation_count": len(values), "placebo": {"shift_sessions": 7, "status": "DIAGNOSTIC_ONLY", "schedule_shift_applied": True, "windows": {name: slice_metrics(placebo_curve, *bounds) for name, bounds in WINDOWS.items()}}}
    payload = {"schema": "immediate-candidates-economic-results-v1", "status": "COMPLETE_RESEARCH_ONLY", "economic_run": True, "promotion": False, "integration": False, "manifest_physical_sha256": sha(MANIFEST_PATH), "manifest_canonical_sha256": manifest["protocol_sha256"], "input_hashes": observed_hashes, "bar_row_count_verified": row_count, "post_cutoff_rows": 0, "v3_exact_date_join_only": True, "v3_forward_fill_days": 0, "cases": cases, "primary_statistics": stats, "ledger_sha256": sha(LEDGER), "counts": {"candidates": len(manifest["candidates"]), "cases": len(cases), "ledger_lines": sum(1 for _ in LEDGER.open(encoding="utf-8"))}}
    RESULTS.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    REPORT.write_text(render_report(payload), encoding="utf-8")
    receipt = {"schema": "immediate-candidates-economic-receipt-v1", "status": payload["status"], "results": str(RESULTS.relative_to(REPO)), "results_sha256": sha(RESULTS), "report": str(REPORT.relative_to(REPO)), "report_sha256": sha(REPORT), "ledger": str(LEDGER.relative_to(REPO)), "ledger_sha256": sha(LEDGER), "manifest_physical_sha256": payload["manifest_physical_sha256"], "manifest_canonical_sha256": payload["manifest_canonical_sha256"], "economic_run": True, "promotion": False, "integration": False}
    (OUT_DIR / "receipt.json").write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"status": payload["status"], "cases": len(cases), "ledger_lines": payload["counts"]["ledger_lines"], "results_sha256": receipt["results_sha256"]}, sort_keys=True))


def render_report(payload: dict[str, Any]) -> str:
    lines = ["# Immediate candidate ekonomik testi — 2026-09-09", "", "Durum: `COMPLETE_RESEARCH_ONLY`. A01-A06 frozen manifestten, tuning veya en iyi kol seçimi yapılmadan çalıştırıldı. Entegrasyon, promotion, PAPER ve LIVE işlemi yapılmadı.", "", f"Manifest physical SHA-256: `{payload['manifest_physical_sha256']}`; canonical protocol SHA-256: `{payload['manifest_canonical_sha256']}`.", f"Doğrulanan bar satırı: `{payload['bar_row_count_verified']}`; cutoff sonrası reddedilen satır: `{payload['post_cutoff_rows']}`; V3 forward-fill günü: `{payload['v3_forward_fill_days']}`.", "", "Primary matris (lag 1, maliyet 1x)", "", "| Aday | Full getiri | Recent getiri | True holdout getiri | Full max DD | Full Sharpe | Full Sortino | Full turnover | V3 corr | İsim örtüşmesi | V3-down gün | Cap uygun gün | Gate |", "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|"]
    for cid in sorted(payload["primary_statistics"]):
        case = next(item for item in payload["cases"] if item["candidate_id"] == cid and item["lag"] == 1 and item["cost_multiplier"] == 1.0)
        w = case["windows"]
        lines.append(f"| {cid} | {w['full'].get('total_return')} | {w['recent'].get('total_return')} | {w['true_holdout'].get('total_return')} | {w['full'].get('max_drawdown')} | {w['full'].get('sharpe')} | {w['full'].get('sortino')} | {case['metrics'].get('turnover')} | {case['paired_diagnostics'].get('exact_date_daily_return_correlation')} | {case['paired_diagnostics'].get('mean_name_overlap_fraction')} | {case['paired_diagnostics']['v3_down_contribution'].get('v3_down_days')} | {case['overlay']['capacity_aware']['eligible_days']} | {case['verdict']['status']} |")
    lines += ["", "Tüm 54 aday hücresi (lag 1/3/7 × maliyet 1x/2x/3x) `results.json` içinde tutuldu. Her hücrede full/recent/true-holdout metrikleri ve T1 terminal close+cost muhasebesi bulunur. V3 eşlemesi yalnızca aynı tarihleri kullanır; 2026-07-30 ve 2026-07-31 forward-fill edilmedi.", "", "Primary istatistik tanıları frozen 10.000 örnekli 21-session moving-block bootstrap ve 10.000 paired-sign permutation için aday eksi V3 günlük getirilerini kullanır. +7-session schedule placebo gerçekten çalıştırılır, diagnostic olarak tutulur ve seçimde kullanılmaz. Bunlar araştırma gate’leridir; sonuçlardan otomatik admission veya promotion çıkarılmaz.", ""]
    return "\n".join(lines)


if __name__ == "__main__":
    main()
