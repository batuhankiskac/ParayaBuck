#!/usr/bin/env python3
"""Robustness of the Stage A trend arms (not a selection step): SMA length, weekly checks, signal index."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from stage_a import END, START, WINDOWS, load, metrics  # noqa: E402

import datetime as dt


def simulate(bars, divs, asset, signal, sma, weekly, mult=2):
    days = sorted(d for d in bars["SPY"] if d <= END)
    first = days.index(next(d for d in days if d >= START))
    sig_days = sorted(bars[signal])
    closes = [bars[signal][d][1] for d in sig_days]
    idx = {d: i for i, d in enumerate(sig_days)}
    cost = 4.0 * mult / 10_000
    nav, held, curve, switches = 1.0, False, [], 0
    for i in range(first, len(days)):
        day, prev = days[i], days[i - 1]
        o, c = bars[asset][day]
        pc = bars[asset][prev][1]
        j = idx[prev]
        trend = j >= sma - 1 and closes[j] > sum(closes[j - sma + 1 : j + 1]) / sma
        new_week = dt.date.fromisoformat(day).isocalendar()[1] != dt.date.fromisoformat(prev).isocalendar()[1]
        want = trend if (not weekly or new_week or i == first) else held
        held_open = held if i > first else False
        if held_open:
            nav *= (o + divs[asset].get(day, 0.0)) / pc
        if want != held_open:
            nav *= 1 - cost
            switches += 1
        if want:
            nav *= c / o
        held = want
        curve.append((day, nav))
    return curve, switches


def main() -> None:
    bars, divs = load()
    print("asset signal sma  weekly | CAGR full  17-21  22-26  MaxDD  switches/yr")
    for asset, signal in (("QLD", "QQQ"), ("TQQQ", "QQQ"), ("QLD", "SPY"), ("TQQQ", "SPY"), ("SSO", "SPY"), ("QQQ", "QQQ")):
        for sma in (100, 150, 200, 250):
            for weekly in (False, True):
                curve, sw = simulate(bars, divs, asset, signal, sma, weekly)
                m = {k: metrics(curve, a, b) for k, (a, b) in WINDOWS.items()}
                print(f"{asset:5s} {signal:4s} {sma:4d} {str(weekly):6s} | {m['full']['cagr']*100:6.2f}% {m['2017_2021']['cagr']*100:6.2f}% "
                      f"{m['2022_2026']['cagr']*100:6.2f}% {m['full']['max_dd']*100:5.1f}%  {sw/9.57:4.1f}")


if __name__ == "__main__":
    main()
