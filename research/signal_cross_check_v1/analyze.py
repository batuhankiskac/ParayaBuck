#!/usr/bin/env python3
"""How often would the signal cross-check have held the VT35 position?

Offline, read-only, deterministic. Two questions:

1. Two real vendors: the live history is Alpaca SIP split-adjusted daily
   bars; the decision-day close comes from a different source. With the
   Yahoo Finance close (stress_robustness_v1, official consolidated close,
   split-adjusted) as that other source, on how many sessions would the QQQ
   trend state (close vs SMA200 over 199 prior SIP closes + that session's
   close) differ from the all-SIP state? How often would QQQ or QLD closes
   differ by more than 50 bps (rule d)?
2. Proxy for the live IEX last-minute close: no historical IEX minute bars
   are stored, so the IEX-vs-official gap is modeled as zero-mean noise of
   size sigma bps. For each session the probability that such noise flips
   the trend is Phi(-|distance to SMA| / sigma); the sum is the expected
   number of rule-(b) holds. Distance buckets give the model-free bound.

Inputs:
    data/historical/stress_robustness_v1_daily.csv (Yahoo, in the repo)
    <sip csv>: git show archive/research/profit-max-v1-2026-09:\
data/historical/profit_max_v1_daily_split_sip.csv > /tmp/sip.csv

Usage:
    python research/signal_cross_check_v1/analyze.py --sip /tmp/sip.csv \
        --out research/signal_cross_check_v1/RESULTS.json
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from datetime import UTC, date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
YAHOO = ROOT / "data/historical/stress_robustness_v1_daily.csv"
NEW_YORK = ZoneInfo("America/New_York")
SMA = 200
DIVERGENCE_BPS = 50.0
DISTANCE_BUCKETS_BPS = (2, 5, 10, 25, 50)
NOISE_SIGMAS_BPS = (2.0, 5.0, 10.0, 20.0)
WINDOW_START = date(2017, 1, 3)  # Stage B window start
WINDOW_END = date(2026, 7, 30)


def _phi(x: float) -> float:
    return 0.5 * math.erfc(-x / math.sqrt(2.0))


def load_yahoo(path: Path) -> dict[str, dict[date, float]]:
    closes: dict[str, dict[date, float]] = {}
    with path.open(newline="") as handle:
        for row in csv.DictReader(handle):
            closes.setdefault(row["symbol"], {})[date.fromisoformat(row["date"])] = float(row["close"])
    return closes


def load_sip(path: Path) -> dict[str, dict[date, float]]:
    closes: dict[str, dict[date, float]] = {}
    with path.open(newline="") as handle:
        for row in csv.DictReader(handle):
            stamp = datetime.fromisoformat(row["timestamp"]).astimezone(UTC).astimezone(NEW_YORK).date()
            closes.setdefault(row["symbol"], {})[stamp] = float(row["close"])
    return closes


def trend_rows(history: dict[date, float]) -> list[tuple[date, float, float]]:
    """(session, close, SMA200 including that close) for every session with 200 bars."""
    sessions = sorted(history)
    rows = []
    window_sum = 0.0
    for index, session in enumerate(sessions):
        window_sum += history[session]
        if index >= SMA:
            window_sum -= history[sessions[index - SMA]]
        if index >= SMA - 1:
            rows.append((session, history[session], window_sum / SMA))
    return rows


def distance_analysis(rows: list[tuple[date, float, float]], label: str) -> dict[str, object]:
    distances = [abs(close / sma - 1.0) * 1e4 for _session, close, sma in rows]
    buckets = {f"within_{b}bps": sum(1 for d in distances if d < b) for b in DISTANCE_BUCKETS_BPS}
    expected = {f"sigma_{s:g}bps": round(sum(_phi(-d / s) for d in distances), 2) for s in NOISE_SIGMAS_BPS}
    years = (rows[-1][0] - rows[0][0]).days / 365.25 if rows else 0.0
    return {
        "source": label,
        "first": rows[0][0].isoformat() if rows else None,
        "last": rows[-1][0].isoformat() if rows else None,
        "sessions": len(rows),
        "years": round(years, 2),
        "sessions_with_close_within_bps_of_sma200": buckets,
        "expected_trend_flips_from_close_noise": expected,
        "expected_trend_flips_per_year": {
            key: round(value / years, 3) if years else None for key, value in expected.items()
        },
    }


def vendor_comparison(sip: dict[str, dict[date, float]], yahoo: dict[str, dict[date, float]]) -> dict[str, object]:
    qqq = sip["QQQ"]
    sessions = sorted(qqq)
    mismatches = []
    divergence_sessions = []
    gaps: dict[str, list[float]] = {"QQQ": [], "QLD": []}
    compared = 0
    for index in range(SMA - 1, len(sessions)):
        session = sessions[index]
        if not WINDOW_START <= session <= WINDOW_END:
            continue
        other = yahoo["QQQ"].get(session)
        if other is None:
            continue
        compared += 1
        prior = [qqq[day] for day in sessions[index - SMA + 1 : index]]
        sip_close = qqq[session]
        sip_trend = sip_close > (sum(prior) + sip_close) / SMA
        other_trend = other > (sum(prior) + other) / SMA
        if sip_trend != other_trend:
            mismatches.append(
                {"session": session.isoformat(), "sip_close": sip_close, "yahoo_close": other, "sip_uptrend": sip_trend}
            )
        divergent = []
        for symbol in ("QQQ", "QLD"):
            left, right = sip[symbol].get(session), yahoo[symbol].get(session)
            if left is None or right is None or left <= 0:
                continue
            gap = (right / left - 1.0) * 1e4
            gaps[symbol].append(gap)
            if abs(gap) > DIVERGENCE_BPS:
                divergent.append({"symbol": symbol, "gap_bps": round(gap, 2), "sip": left, "yahoo": right})
        if divergent:
            divergence_sessions.append({"session": session.isoformat(), "symbols": divergent})

    def _stats(values: list[float]) -> dict[str, float | int]:
        ordered = sorted(abs(v) for v in values)
        if not ordered:
            return {"n": 0}
        return {
            "n": len(ordered),
            "exact_match": sum(1 for v in ordered if v < 1e-9),
            "mean_abs_bps": round(sum(ordered) / len(ordered), 4),
            "p99_abs_bps": round(ordered[int(0.99 * (len(ordered) - 1))], 4),
            "max_abs_bps": round(ordered[-1], 4),
        }

    return {
        "window": [WINDOW_START.isoformat(), WINDOW_END.isoformat()],
        "sessions_compared": compared,
        "trend_state_mismatches": len(mismatches),
        "trend_state_mismatch_sessions": mismatches,
        "close_divergence_sessions_over_50bps": len(divergence_sessions),
        "close_divergence_examples": divergence_sessions[:20],
        "close_gap_stats": {symbol: _stats(values) for symbol, values in gaps.items()},
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sip", required=True, help="profit_max_v1_daily_split_sip.csv from the archive tag")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    yahoo = load_yahoo(YAHOO)
    sip = load_sip(Path(args.sip))
    sip_rows = [row for row in trend_rows(sip["QQQ"]) if WINDOW_START <= row[0] <= WINDOW_END]
    yahoo_rows = trend_rows(yahoo["QQQ"])
    results = {
        "vendor_comparison_sip_vs_yahoo": vendor_comparison(sip, yahoo),
        "iex_close_proxy_sip_2017_2026": distance_analysis(sip_rows, "alpaca_sip_split"),
        "iex_close_proxy_yahoo_1999_2026": distance_analysis(yahoo_rows, "yahoo_split"),
    }
    Path(args.out).write_text(json.dumps(results, indent=2, sort_keys=True) + "\n")
    comparison = results["vendor_comparison_sip_vs_yahoo"]
    print(
        f"SIP vs Yahoo {comparison['sessions_compared']} sessions: "  # type: ignore[index]
        f"trend mismatches={comparison['trend_state_mismatches']} "  # type: ignore[index]
        f"close divergences>50bps={comparison['close_divergence_sessions_over_50bps']}"  # type: ignore[index]
    )
    for key in ("iex_close_proxy_sip_2017_2026", "iex_close_proxy_yahoo_1999_2026"):
        block = results[key]
        print(key, block["sessions"], block["sessions_with_close_within_bps_of_sma200"])  # type: ignore[index]
        print("  expected flips", block["expected_trend_flips_from_close_noise"])  # type: ignore[index]
        print("  per year", block["expected_trend_flips_per_year"])  # type: ignore[index]


if __name__ == "__main__":
    main()
