#!/usr/bin/env python3
"""Data validation for e2_confirmation_v1 (offline). Computes no signal and no arm.

Per index (whole stored file and the campaign's data window): calendar gaps,
stale-close runs, |daily close return| > 25%, open-quality counts (null,
non-positive, O=H=L=C, open outside [low, high], open == previous close),
weekend dates. Also T-bill coverage. Writes VALIDATION.json.
"""

from __future__ import annotations

import csv
import datetime as dt
import json
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
INDEX = ROOT / "data/historical/e2_confirmation_v1_index.csv"
TB3MS = ROOT / "data/historical/e2_confirmation_v1_tb3ms.csv"
DTB3 = ROOT / "data/historical/stress_robustness_v1_dtb3.csv"
OUT = HERE / "VALIDATION.json"
GAP_DAYS = 6  # calendar days between consecutive rows (a normal weekend is 3, a long holiday weekend 4-5)
STALE_RUN = 3  # consecutive identical closes (>= 3 bars)
BIG_RET = 0.25


def f(x):
    return None if x == "" else float(x)


def load():
    raw = defaultdict(list)
    with INDEX.open() as fh:
        for r in csv.DictReader(fh):
            raw[r["symbol"]].append((r["date"], f(r["open"]), f(r["high"]), f(r["low"]), f(r["close"])))
    return raw


def check(rows):
    out = {"rows": len(rows), "first": rows[0][0], "last": rows[-1][0]}
    gaps, big, stale = [], [], []
    run_start, run_len = 0, 1
    q = defaultdict(lambda: defaultdict(int))
    weekend = []
    for k, (d, o, h, lo, c) in enumerate(rows):
        y = d[:4]
        wd = dt.date.fromisoformat(d).weekday()
        if wd >= 5:
            weekend.append(d)
        if o is None:
            q["open_null"][y] += 1
        elif o <= 0:
            q["open_nonpositive"][y] += 1
        else:
            if h is not None and lo is not None and o == h == lo == c:
                q["o_eq_h_eq_l_eq_c"][y] += 1
            if h is not None and lo is not None and (o > h * (1 + 1e-9) or o < lo * (1 - 1e-9)):
                q["open_outside_low_high"][y] += 1
            if k > 0 and o == rows[k - 1][4]:
                q["open_eq_prev_close"][y] += 1
        if k == 0:
            continue
        dp, cp = rows[k - 1][0], rows[k - 1][4]
        gd = (dt.date.fromisoformat(d) - dt.date.fromisoformat(dp)).days
        if gd >= GAP_DAYS:
            gaps.append({"from": dp, "to": d, "calendar_days": gd})
        r = c / cp - 1
        if abs(r) > BIG_RET:
            big.append({"date": d, "prev_close": cp, "close": c, "return": round(r, 6)})
        if c == cp:
            run_len += 1
        else:
            if run_len >= STALE_RUN:
                stale.append({"from": rows[run_start][0], "to": rows[k - 1][0], "bars": run_len,
                              "close": rows[run_start][4]})
            run_start, run_len = k, 1
    if run_len >= STALE_RUN:
        stale.append({"from": rows[run_start][0], "to": rows[-1][0], "bars": run_len, "close": rows[run_start][4]})
    out["calendar_gaps_ge_%d_days" % GAP_DAYS] = gaps
    out["abs_close_return_gt_25pct"] = big
    out["stale_close_runs_ge_%d_bars" % STALE_RUN] = stale
    out["weekend_dates"] = {"count": len(weekend), "first": weekend[:5], "last": weekend[-5:]}
    out["open_quality_by_year"] = {k: dict(sorted(v.items())) for k, v in sorted(q.items())}
    out["open_quality_totals"] = {k: sum(v.values()) for k, v in sorted(q.items())}
    out["rows_by_year"] = dict(sorted(((y, n) for y, n in
                                       ((y, sum(1 for r in rows if r[0][:4] == y))
                                        for y in sorted({r[0][:4] for r in rows})))))
    return out


def main() -> None:
    raw = load()
    windows = {"^IXIC": ("1971-01-01", "1999-12-31"), "^GSPC": ("1934-01-01", "1956-12-31"),
               "^N225": ("1900-01-01", "2026-09-29"), "^GDAXI": ("1900-01-01", "2026-09-29"),
               "^FTSE": ("1900-01-01", "2026-09-29")}
    res = {}
    for sym, (a, b) in windows.items():
        rows = [r for r in raw[sym] if a <= r[0] <= b]
        res[sym] = {"data_window": [a, b], **check(rows)}
        if sym == "^GSPC":
            res[sym]["pre_1934_rows_unused"] = sum(1 for r in raw[sym] if r[0] < a)
    with TB3MS.open() as fh:
        tb = [(r["observation_date"], r["TB3MS"]) for r in csv.DictReader(fh)]
    with DTB3.open() as fh:
        d3 = [(r["observation_date"], r["DTB3"]) for r in csv.DictReader(fh)]
    d3v = [x for x in d3 if x[1] not in (".", "")]
    res["TB3MS"] = {"rows": len(tb), "first": tb[0][0], "last": tb[-1][0],
                    "missing_values": [d for d, v in tb if v in (".", "")],
                    "min_pct": min(float(v) for _, v in tb if v not in (".", "")),
                    "max_pct": max(float(v) for _, v in tb if v not in (".", ""))}
    res["DTB3"] = {"rows": len(d3), "valid": len(d3v), "first": d3v[0][0], "last": d3v[-1][0],
                   "missing_value_rows": len(d3) - len(d3v)}
    OUT.write_text(json.dumps(res, indent=2) + "\n")
    for sym in windows:
        v = res[sym]
        print(sym, v["rows"], v["first"], v["last"], "gaps", len(v["calendar_gaps_ge_6_days"]),
              "big", v["abs_close_return_gt_25pct"], "stale", len(v["stale_close_runs_ge_3_bars"]),
              "weekend", v["weekend_dates"]["count"], v["open_quality_totals"])
    print(json.dumps({k: res[k] for k in ("TB3MS", "DTB3")}))


if __name__ == "__main__":
    main()
