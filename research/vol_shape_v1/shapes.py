"""vol_shape_v1 — the exposure-shape sizing rule and the pre-2000 calibration of `c_p`.

The production sizing schedule is `vt(σ) = min(1, 0.35/σ20)` (K/C0, `p = 1`). This study's arms
replace it with

    vt_p(σ) = min(1, c_p / σ20**p)

at matched average weight: `c_p` is solved **once** on the pre-2000 window
1985-10-01 → 2000-03-08 (^NDX daily bars, `data/historical/vol_shape_v1_ndx.csv`) so that
`mean(vt_p) = mean(vt_1)` over the sessions where `σ20` is defined. Nothing else changes: the E2
vote, the early-re-entry state machine, the 0.98 total cap, the 0.5 pp band, A1u's gain-scaled
downsize band and H2's December deferral are production K's.

NaN/zero conventions mirror `fe_signals.vt_of` exactly (`σ <= 0` or NaN -> `vt = 1.0`), so at
`p = 1.0, c = 0.35` the schedule and the whole target series are bit-identical to the engine's
(asserted in `--harness`).
"""

from __future__ import annotations

import bisect
import csv
from pathlib import Path

import numpy as np

from research.fast_engine import fe_data, fe_signals
from research.fast_engine.fe_config import CAP, EXP_Q, MARKETS, VOL_TARGET

ROOT = Path(__file__).resolve().parents[2]
NDX_CSV = ROOT / "data/historical/vol_shape_v1_ndx.csv"

# the survey's I2 arms: p = 1 is K. NOTE: these labels are the *exponent* arms of
# idea_survey_v5 §4.2 -- "E2" here is p = 1.25, not the production E2 vote.
P_LIST = {"E1": 0.75, "E2": 1.25, "E3": 1.5, "E4": 2.0}
OOS_MARKETS = tuple(MARKETS)  # M1..M5, the G8 pooling order
CAL_FIRST, CAL_LAST = "1985-10-01", "2000-03-08"
PLATEAU_REL = 0.10  # the +/-10 % c_p robustness row (report only)


def vt_shape(vol, p, c):
    """`min(1, c/vol**p)` with production's NaN/<=0 convention (vol <= 0 or NaN -> 1.0)."""
    v = np.asarray(vol, dtype=float)
    pos = v > 0
    den = np.where(pos, v, 1.0) ** p
    return np.where(pos, np.minimum(1.0, c / den), 1.0)


def apply_shape(on_eff, factor, vol_instr, p, c):
    """`fe_signals.sized_wants` with the shape `(p, c)`; `(1.0, 0.35)` is bit-identical."""
    on = np.atleast_2d(np.asarray(on_eff, dtype=float))
    f = np.atleast_2d(np.asarray(factor, dtype=float))
    vt = vt_shape(np.atleast_2d(np.asarray(vol_instr, dtype=float)), p, c)
    raw = on * f * vt
    fcap = np.where(raw > CAP, CAP / np.where(raw > 0, raw, 1.0), 1.0)
    return raw * fcap


def targets(price, vol_instr, p, c, e2=None, comps=None):
    """(on_eff, factor, wants) of the R2 early-re-entry state machine under the shape `(p, c)`."""
    price = np.atleast_2d(np.asarray(price, dtype=float))
    if e2 is None:
        e2 = fe_signals.e2_up(price)
    e2 = np.atleast_2d(np.asarray(e2, dtype=bool))
    if comps is None:
        comps = fe_signals.early_components(price)
    on_eff, factor = fe_signals.r2_state(price, e2, comps)
    return on_eff, factor, apply_shape(on_eff, factor, vol_instr, p, c)


# ----------------------------------------------------------------------------- calibration window
def ndx_days():
    """The ^NDX calibration window's sessions and OHLC."""
    days, opn, clo = [], [], []
    with NDX_CSV.open() as fh:
        for r in csv.DictReader(fh):
            if CAL_FIRST <= r["date"] <= CAL_LAST:
                days.append(r["date"])
                opn.append(float(r["open"]))
                clo.append(float(r["close"]))
    return days, np.array(opn), np.array(clo)


def ndx_vol():
    """σ20 of the synthetic 2× leg on ^NDX (base financing, zero dividends — the index
    convention every OOS stage of this campaign uses), the schedule K sizes on."""
    days, o, c = ndx_days()
    _d3, _tb = fe_data.load_rates()
    y = fe_data.rate_on(days, _d3, _tb)
    _so, sc = fe_signals.synthetic_2x(o, c, np.zeros_like(c), y, EXP_Q)
    return days, fe_signals.vol_n(sc)[0]


def solve_c(vol, p, target_mean, lo=1e-9, hi=1e3, iters=200):
    """`c` with `mean(vt_p)` = `target_mean` over the finite-σ sessions; bisection, monotone."""
    fin = np.isfinite(vol)
    v = vol[fin]

    def mean_at(c):
        return float(np.mean(vt_shape(v, p, c)))

    if mean_at(hi) < target_mean:
        return hi
    for _ in range(iters):
        mid = 0.5 * (lo + hi)
        if mean_at(mid) < target_mean:
            lo = mid
        else:
            hi = mid
        if hi - lo <= 1e-15 * max(1.0, hi):
            break
    return 0.5 * (lo + hi)


def calibrate(verbose=False):
    """Solve every arm's `c_p` once on the pre-2000 window. Returns a result dict."""
    days, vol = ndx_vol()
    fin = np.isfinite(vol)
    target_mean = float(np.mean(vt_shape(vol[fin], 1.0, VOL_TARGET)))
    cs = {"C0": VOL_TARGET}
    for arm, p in P_LIST.items():
        cs[arm] = solve_c(vol, p, target_mean)
    out = {
        "window": {"first": days[0], "last": days[-1], "sessions": len(days),
                   "sessions_with_vol20": int(fin.sum())},
        "source": "data/historical/vol_shape_v1_ndx.csv (^NDX daily, Yahoo chart API)",
        "target_mean_vt_of_K": target_mean,
        "arms": {},
    }
    for arm in ("C0", *P_LIST):
        p = 1.0 if arm == "C0" else P_LIST[arm]
        c = cs[arm]
        m = float(np.mean(vt_shape(vol[fin], p, c)))
        row = {
            "p": p,
            "c": c,
            "mean_vt_cal_window": m,
            "mean_vt_diff_pp": (m - target_mean) * 100,
            "plateau": {},
        }
        for rel in (-PLATEAU_REL, PLATEAU_REL):
            row["plateau"][f"c{rel:+.0%}"] = {
                "c": c * (1 + rel),
                "mean_vt": float(np.mean(vt_shape(vol[fin], p, c * (1 + rel)))),
            }
        out["arms"][arm] = row
        if verbose:
            print(f"  {arm}: p={p} c={c!r} mean_vt={m!r} (target {target_mean!r})")
    out["c"] = cs
    return out


def window_indices(days, a, b):
    """(i0, i1) of a [a, b] date window: the engine's bisect convention."""
    return bisect.bisect_left(days, a), bisect.bisect_right(days, b) - 1
