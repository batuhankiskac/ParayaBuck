"""study_data -- align the downloaded vol/underlying series onto the engine's QQQ sessions."""

from __future__ import annotations

import bisect
import csv
from functools import lru_cache

import numpy as np

from research.fast_engine import fe_data
from research.put_hedge_v1 import config as C


def _read_close(path):
    rows = []
    with open(path) as fh:
        for r in csv.DictReader(fh):
            rows.append((r["date"], float(r["close"])))
    rows.sort()
    return rows


def forward_fill(days, rows):
    ds = [r[0] for r in rows]
    out = np.empty(len(days), dtype=np.float64)
    for k, d in enumerate(days):
        j = bisect.bisect_right(ds, d) - 1
        out[k] = rows[j][1] if j >= 0 else np.nan
    return out


@lru_cache(maxsize=1)
def engine_market():
    """The engine's days plus the aligned QQQ close, VXN, VIX and DTB3 series."""
    days, arr, y = fe_data.load()
    days = list(days)
    qqq = np.ascontiguousarray(arr["QQQ"][1])
    vxn = forward_fill(days, _read_close(C.VXN_CSV)) / 100.0
    vix = forward_fill(days, _read_close(C.VIX_CSV)) / 100.0
    d3, tb = fe_data.load_rates()
    rate = fe_data.rate_on(days, d3, tb)
    # before VXN starts, hold the first value (the reduced window starts on the first VXN day)
    first = int(np.argmax(np.isfinite(vxn)))
    vxn[:first] = vxn[first]
    return days, qqq, vxn, vix, rate


def vxn_index(days):
    """First session on/after the first VXN date."""
    return bisect.bisect_left(days, C.VXN_START)


# ----------------------------------------------------------------------------- vt-parameterised signals
def vt_of(vol, target):
    """`fe_signals.vt_of` with the vol target as a parameter."""
    v = np.asarray(vol)
    return np.where(v > 0, np.minimum(1.0, target / np.where(v > 0, v, 1.0)), 1.0)


def sized_wants(on_eff, factor, vol_instr, target):
    """`fe_signals.sized_wants` with the vol target as a parameter."""
    vt = vt_of(np.atleast_2d(np.asarray(vol_instr, dtype=float)), target)
    raw = np.atleast_2d(np.asarray(on_eff, dtype=float)) * np.atleast_2d(factor) * vt
    fcap = np.where(raw > C_CAP, C_CAP / np.where(raw > 0, raw, 1.0), 1.0)
    return raw * fcap


C_CAP = 0.98


def main_inputs_vt(spread, target):
    """`fe_data.main_inputs` with the vol target as a parameter (target = 0.35 is bit-identical)."""
    from research.fast_engine import fe_signals

    days, arr, y = fe_data.load()
    q = arr["QQQ"]
    o, c = fe_signals.synthetic_2x(q[0], q[1], q[2], y + spread, fe_data.EXP_Q)
    price = q[1]
    e2 = fe_signals.e2_up(price)[0]
    vol_instr = fe_signals.vol_n(c)[0]
    comps = fe_signals.early_components(price)
    on_eff, factor = fe_signals.r2_state(price, e2, comps)
    w_r2 = sized_wants(on_eff, factor, vol_instr, target)[0]
    w_e2 = sized_wants(e2, np.ones_like(factor), vol_instr, target)[0]
    return days, o, c, w_e2, w_r2, e2
