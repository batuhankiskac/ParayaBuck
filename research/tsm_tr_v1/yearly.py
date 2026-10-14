#!/usr/bin/env python3
"""tsm_tr_v1 report-only per-year returns (see PREREGISTRATION.md, "Reported but not gated").

Imports screen.py (its main() is never run) and re-simulates the four main arms on the main
window at m = 2, base financing, printing the calendar-year returns and the per-year difference
against the control P0. Not gated, not part of RESULTS.json; it exists to show where the TSM
substitutions earn and lose.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("tsm_screen", HERE / "screen.py")
tsm = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(tsm)

ARMS = ("C0", "P0", "P1", "P2")
M = 2


def main() -> None:
    mi = tsm.main_inputs(0.0)  # base financing
    price, tr, rates, o, c, v20 = mi["price"], mi["tr"], mi["rates"], mi["o"], mi["c"], mi["vol"]
    days, start = mi["days"], tsm.START
    comps = tsm.early_components(price)
    navs = {}
    for arm in ARMS:
        w = tsm.arm_legs(price, tr, rates, v20, arm, comps)[0]
        navs[arm] = tsm.simulate_band(o, c, w, tsm.COST_PER_SIDE * M, start, tsm.C0_BAND)[0][0]
    dates = days[start - 1:]
    years = sorted({d[:4] for d in dates[1:]})
    out = {"window": [dates[1], dates[-1]], "arms": list(ARMS), "m": M, "financing": "base",
           "trades_per_year": {}, "yearly": {}}
    for yr in years:
        row = {}
        for arm in ARMS:
            r = tsm.seg(dates, navs[arm], f"{yr}-01-01", f"{yr}-12-31")
            row[arm] = {"return": r["total_return"], "from": r["from"], "to": r["to"]}
        for arm in ("P1", "P2"):
            row[f"{arm}-P0_pp"] = (row[arm]["return"] - row["P0"]["return"]) * 100
        out["yearly"][yr] = row
    by = [y for y in years if by_diff(out, y, "P1") != 0 or by_diff(out, y, "P2") != 0]
    out["years_with_any_difference"] = by
    print(json.dumps(out, indent=2, sort_keys=True))
    print()
    hdr = f"{'year':6s} {'C0':>9s} {'P0':>9s} {'P1':>9s} {'P2':>9s} {'P1-P0':>8s} {'P2-P0':>8s}"
    print(hdr)
    for yr in years:
        r = out["yearly"][yr]
        mark = " *" if yr in by else ""
        print(f"{yr:6s} {r['C0']['return'] * 100:8.2f}% {r['P0']['return'] * 100:8.2f}% "
              f"{r['P1']['return'] * 100:8.2f}% {r['P2']['return'] * 100:8.2f}% "
              f"{r['P1-P0_pp']:+7.3f} {r['P2-P0_pp']:+7.3f}{mark}")


def by_diff(out, yr, arm):
    return out["yearly"][yr][f"{arm}-P0_pp"]


if __name__ == "__main__":
    main()
