#!/usr/bin/env python3
"""Stage A2 risk sweet-spot screen (see PREREGISTRATION.md)."""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from stage_a import END, START, WINDOWS, load, metrics  # noqa: E402

SMA = 200
BAND = 0.005


def _vol(closes: list[float], n: int = 20) -> float:
    rets = [closes[i] / closes[i - 1] - 1 for i in range(len(closes) - n, len(closes))]
    mu = sum(rets) / n
    return math.sqrt(sum((r - mu) ** 2 for r in rets) / (n - 1)) * math.sqrt(252)


def targets(arm: str, up: bool, qld_closes: list[float]) -> dict[str, float]:
    mixes = {"M25": 0.25, "M50": 0.50, "M75": 0.75, "M100": 1.0}
    if arm in mixes:
        f = mixes[arm]
        return {"QLD": f, "QQQ": 1 - f} if up else {}
    if arm.startswith("VT"):
        if not up:
            return {}
        return {"QLD": min(1.0, int(arm[2:]) / 100 / _vol(qld_closes))}
    if arm.startswith("G30"):
        inner = targets("M50" if arm == "G30M50" else "M100", up, qld_closes)
        return {"GLD": 0.30, **{k: 0.70 * v for k, v in inner.items()}}
    raise ValueError(arm)


def simulate(bars, divs, arm, mult):
    days = sorted(d for d in bars["SPY"] if d <= END)
    first = days.index(next(d for d in days if d >= START))
    qqq = [bars["QQQ"][d][1] for d in days]
    cost = 4.0 * mult / 10_000
    nav, weights, curve = 1.0, {}, []  # weights = fraction of NAV per asset at last close
    for i in range(first, len(days)):
        day, prev = days[i], days[i - 1]
        up = i - 1 >= SMA - 1 and qqq[i - 1] > sum(qqq[i - SMA : i]) / SMA
        qld_hist = [bars["QLD"][d][1] for d in days[i - 21 : i]]
        want = targets(arm, up, qld_hist)
        # overnight: prev close -> open
        growth = {s: (bars[s][day][0] + divs[s].get(day, 0.0)) / bars[s][prev][1] for s in weights}
        value = {s: w * growth[s] for s, w in weights.items()}
        cash = 1.0 - sum(weights.values())
        total = cash + sum(value.values())
        nav *= total
        weights = {s: v / total for s, v in value.items()}
        if any(abs(want.get(s, 0.0) - weights.get(s, 0.0)) > BAND for s in set(want) | set(weights)):
            turnover = sum(abs(want.get(s, 0.0) - weights.get(s, 0.0)) for s in set(want) | set(weights))
            nav *= 1 - cost * turnover
            weights = {s: w for s, w in want.items() if w > 0}
        # open -> close
        growth = {s: bars[s][day][1] / bars[s][day][0] for s in weights}
        value = {s: w * growth[s] for s, w in weights.items()}
        cash = 1.0 - sum(weights.values())
        total = cash + sum(value.values())
        nav *= total
        weights = {s: v / total for s, v in value.items()}
        curve.append((day, nav))
    return curve


def main() -> None:
    bars, divs = load()
    arms = ("M25", "M50", "M75", "M100", "VT25", "VT35", "G30", "G30M50")
    s0 = json.loads((Path(__file__).parent / "RESULTS_STAGE_A.json").read_text())["2"]["S0"]
    out = {}
    for arm in arms:
        curve = simulate(bars, divs, arm, 2)
        res = {name: metrics(curve, a, b) for name, (a, b) in WINDOWS.items()}
        res["y2022"] = metrics(curve, "2022-01-01", "2022-12-31")
        res["qualifies"] = res["full"]["max_dd"] <= s0["full"]["max_dd"] and all(
            res[w]["cagr"] > s0[w]["cagr"] for w in WINDOWS
        )
        out[arm] = res
    ok = [a for a in arms if out[a]["qualifies"]]
    selected = max(ok, key=lambda a: out[a]["full"]["cagr"], default=None)
    (Path(__file__).parent / "RESULTS_STAGE_A2.json").write_text(json.dumps({"arms": out, "selected": selected}, indent=2) + "\n")
    print(f"S0 SPY  CAGR {s0['full']['cagr']*100:6.2f}%  MaxDD {s0['full']['max_dd']*100:5.1f}%  17-21 {s0['2017_2021']['cagr']*100:6.2f}%  22-26 {s0['2022_2026']['cagr']*100:6.2f}%")
    for arm in arms:
        r = out[arm]
        print(f"{arm:7s} CAGR {r['full']['cagr']*100:6.2f}%  Sharpe {r['full']['sharpe']:.2f}  MaxDD {r['full']['max_dd']*100:5.1f}%  "
              f"2022 {((1+r['y2022']['cagr'])**1-1)*100:6.1f}%  17-21 {r['2017_2021']['cagr']*100:6.2f}%  22-26 {r['2022_2026']['cagr']*100:6.2f}%"
              f"{'  QUALIFIES' if r['qualifies'] else ''}")
    print("selected:", selected)


if __name__ == "__main__":
    main()
