#!/usr/bin/env python3
"""Buy-and-hold total-return benchmarks (dividends reinvested at the ex-date close)."""

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
WINDOWS = {"full": (START, END), "2017_2021": (START, "2021-12-31"), "2022_2026": ("2022-01-01", END), "2022": ("2022-01-01", "2022-12-31")}


def main() -> None:
    closes: dict[str, dict[str, float]] = defaultdict(dict)
    with BARS.open() as handle:
        for row in csv.DictReader(handle):
            closes[row["symbol"]][row["timestamp"][:10]] = float(row["close"])
    divs: dict[str, dict[str, float]] = defaultdict(dict)
    with ACTIONS.open() as handle:
        for row in csv.DictReader(handle):
            divs[row["symbol"]][row["ex_date"]] = divs[row["symbol"]].get(row["ex_date"], 0.0) + float(row["rate"])
    out = {}
    for symbol, series in sorted(closes.items()):
        days = sorted(d for d in series if START <= d <= END)
        if days[0] > "2017-01-10":
            continue
        shares, nav = 1.0 / series[days[0]], []
        for day in days:
            shares *= 1 + divs[symbol].get(day, 0.0) / series[day]
            nav.append((day, shares * series[day]))
        res = {}
        for name, (a, b) in WINDOWS.items():
            seg = [v for d, v in nav if a <= d <= b]
            years = len(seg) / 252
            peak, dd = seg[0], 0.0
            for v in seg:
                peak = max(peak, v)
                dd = max(dd, 1 - v / peak)
            rets = [seg[i] / seg[i - 1] - 1 for i in range(1, len(seg))]
            mu = sum(rets) / len(rets)
            sd = math.sqrt(sum((r - mu) ** 2 for r in rets) / len(rets))
            res[name] = {"cagr": (seg[-1] / seg[0]) ** (1 / years) - 1, "max_dd": dd, "sharpe": mu / sd * math.sqrt(252), "total": seg[-1] / seg[0] - 1}
        out[symbol] = res
        f = res["full"]
        print(f"{symbol:5s} CAGR {f['cagr']*100:6.2f}%  Sharpe {f['sharpe']:.2f}  MaxDD {f['max_dd']*100:5.1f}%  "
              f"2017-21 {res['2017_2021']['cagr']*100:6.2f}%  2022-26 {res['2022_2026']['cagr']*100:6.2f}%  2022 {res['2022']['total']*100:6.1f}%  x{1+f['total']:.2f}")
    (Path(__file__).parent / "BUY_HOLD.json").write_text(json.dumps(out, indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
