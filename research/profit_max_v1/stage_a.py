#!/usr/bin/env python3
"""Stage A close-to-close screen for profit maximization v1 (see PREREGISTRATION.md)."""

from __future__ import annotations

import csv
import json
import math
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BARS = ROOT / "data/historical/profit_max_v1_daily_split_sip.csv"
ACTIONS = ROOT / "data/historical/profit_max_v1_corporate_actions.csv"
START, END = "2017-01-03", "2026-07-30"
WINDOWS = {"full": (START, END), "2017_2021": (START, "2021-12-31"), "2022_2026": ("2022-01-01", END)}
YEARS = [str(y) for y in range(2017, 2027)]
ARMS = {
    "S0": ("SPY", None), "S1": ("QQQ", None),
    "L1": ("SSO", None), "L2": ("UPRO", None), "L3": ("QLD", None), "L4": ("TQQQ", None),
    "T0": ("SPY", "SPY"), "T1": ("SSO", "SPY"), "T2": ("UPRO", "SPY"), "T3": ("QLD", "QQQ"), "T4": ("TQQQ", "QQQ"),
}
SIDE_BPS = 4.0


def load():
    bars = defaultdict(dict)
    with BARS.open() as handle:
        for row in csv.DictReader(handle):
            bars[row["symbol"]][row["timestamp"][:10]] = (float(row["open"]), float(row["close"]))
    divs = defaultdict(dict)
    with ACTIONS.open() as handle:
        for row in csv.DictReader(handle):
            divs[row["symbol"]][row["ex_date"]] = divs[row["symbol"]].get(row["ex_date"], 0.0) + float(row["rate"])
    return bars, divs


def simulate(bars, divs, asset, signal, mult):
    days = sorted(d for d in bars["SPY"] if d <= END)
    first = days.index(next(d for d in days if d >= START))
    sig_days = sorted(bars[signal]) if signal else []
    closes = [bars[signal][d][1] for d in sig_days] if signal else []
    sig_index = {d: i for i, d in enumerate(sig_days)}
    cost = SIDE_BPS * mult / 10_000
    nav, held, curve = 1.0, False, []
    for i in range(first, len(days)):
        day, prev = days[i], days[i - 1]
        o, c = bars[asset][day]
        pc = bars[asset][prev][1]
        # desired position decided on prev close
        if signal:
            j = sig_index[prev]
            want = j >= 199 and closes[j] > sum(closes[j - 199 : j + 1]) / 200
        else:
            want = True
        if i == first:
            held_open = False
        else:
            held_open = held
        # overnight: prev close -> today open with the position held into the open
        if held_open:
            nav *= (o + divs[asset].get(day, 0.0)) / pc
        if want != held_open:
            nav *= 1 - cost
        if want:
            nav *= c / o
        held = want
        curve.append((day, nav))
    if held:
        nav *= 1 - cost
        curve[-1] = (curve[-1][0], nav)
    return curve


def metrics(curve, a, b):
    seg = [v for d, v in curve if a <= d <= b]
    years = (len(seg)) / 252
    peak, dd = seg[0], 0.0
    for v in seg:
        peak, dd = max(peak, v), max(dd, 1 - v / max(peak, v))
    rets = [seg[i] / seg[i - 1] - 1 for i in range(1, len(seg))]
    mu = sum(rets) / len(rets)
    sd = math.sqrt(sum((r - mu) ** 2 for r in rets) / len(rets))
    return {"cagr": (seg[-1] / seg[0]) ** (1 / years) - 1, "max_dd": dd, "sharpe": mu / sd * math.sqrt(252) if sd else 0.0}


def main() -> None:
    bars, divs = load()
    out = {}
    for mult in (1, 2, 3):
        for arm, (asset, signal) in ARMS.items():
            curve = simulate(bars, divs, asset, signal, mult)
            res = {name: metrics(curve, a, b) for name, (a, b) in WINDOWS.items()}
            res["years"] = {y: metrics(curve, f"{y}-01-01", f"{y}-12-31")["cagr"] for y in YEARS}
            res["ending_multiple"] = curve[-1][1]
            out.setdefault(str(mult), {})[arm] = res
    s0 = out["2"]["S0"]
    for arm, res in out["2"].items():
        res["stage_a_pass"] = arm != "S0" and all(res[w]["cagr"] > s0[w]["cagr"] for w in WINDOWS)
    (Path(__file__).parent / "RESULTS_STAGE_A.json").write_text(json.dumps(out, indent=2, sort_keys=True) + "\n")
    for mult in ("1", "2"):
        print(f"--- costs {mult}x")
        for arm, res in out[mult].items():
            f = res["full"]
            print(f"{arm} {ARMS[arm][0]:5s}{'/'+ARMS[arm][1] if ARMS[arm][1] else '':5s} CAGR {f['cagr']*100:6.2f}%  Sharpe {f['sharpe']:.2f}  "
                  f"MaxDD {f['max_dd']*100:5.1f}%  17-21 {res['2017_2021']['cagr']*100:6.2f}%  22-26 {res['2022_2026']['cagr']*100:6.2f}%  "
                  f"x{res['ending_multiple']:.2f}{'  PASS' if res.get('stage_a_pass') else ''}")


if __name__ == "__main__":
    main()
