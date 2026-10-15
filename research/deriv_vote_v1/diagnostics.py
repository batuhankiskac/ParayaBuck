#!/usr/bin/env python3
"""deriv_vote_v1 diagnostics (report-only, run after the pre-registered --run).

Explains the gate outcomes without touching the pre-registered arms, gates or
RESULTS.json:

  * year-by-year NAV returns of C0..C4 (base financing, m = 2) on the main window;
  * vote disagreement between each candidate and C0 (arm on / C0 off, arm off / C0 on)
    over the measured window and since 2017;
  * the vote timeline (transition dates) of every arm in the two windows that decide the
    verdict, 2000-01 -> 2003-12 and 2018-01 -> 2026-09;
  * in-market share per year for every arm.

Writes research/deriv_vote_v1/diagnostics.json. Read-only with respect to every other
file.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("dv_screen", HERE / "screen.py")
dv = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(dv)


def main() -> None:
    days, arr, o, c, price, v20 = dv.main_inputs(0.0)
    dates = days[dv.START - 1:]
    got = dv.arm_wants(price, v20, dv.ARMS)
    votes = dv.votes_by_arm(price, dv.ARMS)
    navs = {}
    for arm in dv.ARMS:
        nav = dv.simulate_band(o, c, got[arm][0], dv.COST_PER_SIDE * 2, dv.START, dv.C0_BAND)[0][0]
        navs[arm] = nav

    # ---- year by year ----
    yrs = sorted({d[:4] for d in dates[1:]})
    yoy = {}
    for arm in dv.ARMS:
        nav = navs[arm]
        row = {}
        for y in yrs:
            idx = [k for k, d in enumerate(dates) if d.startswith(y)]
            if len(idx) < 2:
                continue
            row[y] = float(nav[idx[-1]] / nav[idx[0]] - 1)
        yoy[arm] = row
    # 2017+ (from the last close <= 2016-12-31, as arm_stats) for reference
    k0 = max(k for k, d in enumerate(dates) if d <= "2016-12-31")
    tail = {arm: float(navs[arm][-1] / navs[arm][k0] - 1) for arm in dv.ARMS}

    # ---- vote disagreement ----
    c0v = votes["C0"][0][dv.START - 1:]
    k17 = next(k for k, d in enumerate(dates) if d > "2016-12-31")
    dis = {}
    for arm in dv.CANDIDATES:
        v = votes[arm][0][dv.START - 1:]
        dis[arm] = {
            "full": {"arm_on_c0_off": int(np.sum(v & (~c0v))), "arm_off_c0_on": int(np.sum((~v) & c0v)),
                     "agree_share": float(np.mean(v == c0v)), "sessions": int(len(v))},
            "since_2017": {"arm_on_c0_off": int(np.sum(v[k17:] & (~c0v[k17:]))),
                           "arm_off_c0_on": int(np.sum((~v[k17:]) & c0v[k17:])),
                           "agree_share": float(np.mean(v[k17:] == c0v[k17:])),
                           "sessions": int(len(v[k17:]))},
        }

    # ---- vote timeline in the two decisive windows ----
    def timeline(lo, hi):
        kk = [k for k, d in enumerate(dates) if lo <= d <= hi]
        out = {}
        for arm in dv.ARMS:
            v = votes[arm][0][dv.START - 1:]
            tr = [(dates[kk[0]], "on" if v[kk[0]] else "off")]
            for k in kk[1:]:
                if v[k] != v[k - 1]:
                    tr.append((dates[k], "on" if v[k] else "off"))
            out[arm] = tr
        return out

    # ---- in-market share per year (target > 0 at decision time) ----
    inmkt = {}
    for arm in dv.ARMS:
        w = got[arm][0][0]
        row = {}
        for y in yrs:
            idx = [k for k, d in enumerate(dates) if d.startswith(y) and k >= dv.START - 1]
            if not idx:
                continue
            row[y] = float(np.mean(w[idx] > 0))
        inmkt[arm] = row

    # ---- sub-period returns: what the gates actually turned on ----
    def ret(arm, a, b):
        ia = max(k for k, d in enumerate(dates) if d <= a)
        ib = max(k for k, d in enumerate(dates) if d <= b)
        return float(navs[arm][ib] / navs[arm][ia] - 1)

    subperiods = {
        "2000_bear_2000-03-08_2002-12-31": ("2000-03-08", "2002-12-31"),
        "gfc_2008_2007-12-31_2009-03-09": ("2007-12-31", "2009-03-09"),
        "2017_to_2024_2016-12-30_2024-12-31": ("2016-12-30", "2024-12-31"),
        "2025_2024-12-31_2025-12-31": ("2024-12-31", "2025-12-31"),
        "2025_exit_window_2025-03-05_2025-05-01": ("2025-03-05", "2025-05-01"),
        "2025_after_reentry_2025-05-01_2025-12-31": ("2025-05-01", "2025-12-31"),
        "2026_to_end_2025-12-31_2026-09-29": ("2025-12-31", "2026-09-29"),
    }
    sub = {name: {arm: ret(arm, a, b) for arm in dv.ARMS} for name, (a, b) in subperiods.items()}
    # 2017+ CAGR with 2025's return removed (year count kept): isolates the year that decides G3
    y2025 = {arm: ret(arm, "2024-12-31", "2025-12-31") for arm in dv.ARMS}
    yrs17 = dv.trv2.calendar_years("2016-12-30", dates[-1])
    cagr17_ex2025 = {arm: float(((tail[arm] + 1) / (1 + y2025[arm])) ** (1 / yrs17) - 1) for arm in dv.ARMS}
    # exposure detail over the 2025 exit window for every arm
    kk = [k for k, d in enumerate(dates) if "2025-03-05" <= d <= "2025-05-01"]
    exposure = {}
    for arm in dv.ARMS:
        w = got[arm][0][0][dv.START - 1:][kk]  # targets are full-history; align to `dates`
        exposure[arm] = {"sessions": len(kk), "share_flat": float(np.mean(w <= 0)),
                         "share_below_half_full": float(np.mean(w < 0.45)),
                         "mean_weight": float(np.mean(w)), "max_weight": float(w.max())}

    out = {
        "campaign": "deriv_vote_v1",
        "subperiod_returns_base_m2": sub,
        "return_2025": y2025,
        "cagr_2017_to_end_excluding_2025": cagr17_ex2025,
        "exposure_2025_03_05_to_2025_05_01": exposure,
        "note": ("report-only diagnostics computed by diagnostics.py from screen.py's own functions; "
                 "no arm, gate or parameter of the pre-registered run is changed"),
        "window": {"start": dates[1], "end": dates[-1], "sessions": len(dates) - 1},
        "year_by_year_return_base_m2": yoy,
        "cumulative_2017_to_end": tail,
        "vote_disagreement_vs_C0": dis,
        "vote_timeline_2000_2003": timeline("2000-01-01", "2003-12-31"),
        "vote_timeline_2018_2026": timeline("2018-01-01", "2026-09-29"),
        "in_market_share_by_year": inmkt,
    }
    (HERE / "diagnostics.json").write_text(json.dumps(dv.trv2.rnd(out), indent=2, sort_keys=True) + "\n")
    print("diagnostics written")


if __name__ == "__main__":
    main()
