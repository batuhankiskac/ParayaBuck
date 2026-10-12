#!/usr/bin/env python3
"""Post-hoc (not pre-registered, written after RESULTS.json): Alpaca-style cash sweep vs S2_10 at
several sweep spreads below DTB3. Main window, base financing, m = 2. Writes POSTHOC.json."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("tbs", HERE / "screen.py")
s = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(s)


def main() -> None:
    days, arr, y, o, c, wq, on, sig = s.hist_inputs(0.0)
    dates = days[s.START - 1:]
    yy = y[None, :]
    cost = s.COST_PER_SIDE * 2
    out = {"window": [dates[1], dates[-1]], "financing": "base", "m": 2}
    c0 = s.trv2.curve_stats(dates, s.run_arm("C0", o, c, wq, on, yy, cost, s.START)[0][0])["cagr"]
    s2 = s.trv2.curve_stats(dates, s.run_arm("S2_10", o, c, wq, on, yy, cost, s.START)[0][0])["cagr"]
    out["C0_cagr"], out["S2_10_cagr"] = c0, s2
    rows = {}
    for spread in (0.0, 0.005, 0.01, 0.015, 0.02, 0.025):
        s.ALPACA_SPREAD = spread
        r = {}
        for arm in ("A_OFF", "A_ALL"):
            r[arm] = s.trv2.curve_stats(dates, s.run_arm(arm, o, c, wq, on, yy, cost, s.START)[0][0])["cagr"]
        rows[f"{spread:.3f}"] = r
    out["alpaca_cagr_by_spread"] = rows
    (HERE / "POSTHOC.json").write_text(json.dumps(s.trv2.rnd(out), indent=2, sort_keys=True) + "\n")
    print(f"C0 {c0 * 100:.2f}% S2_10 {s2 * 100:.2f}%")
    for k, r in rows.items():
        print(f"spread {k}: A_OFF {r['A_OFF'] * 100:.2f}% A_ALL {r['A_ALL'] * 100:.2f}%")


if __name__ == "__main__":
    main()
