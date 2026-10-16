"""IC15 -- CPI-acceleration veto (idea_bank_v2, arms A/B/C).

Arm specs (`docs/research/idea_bank_v2.json`, parameters frozen -- not tuned here):

    A  weight *= 0.5 while annualised 6-month CPI inflation > trailing 12-month CPI inflation
       + 0.5 pp
    B  weight = 0 in the same state
    C  3-of-4 vote {SMA200, TSM252, DON126, CPI acceleration <= +0.5 pp}, on iff >= 3

Literal reading and the frozen conventions:

* **Series and lag.**  FRED `CPIAUCSL` (extra key `cpi`, monthly, stamped on the first day of the
  reference month).  The bank fixes a 1-month publication lag, so at a session of month ``M`` the
  latest usable level is month ``M-1``:

      inf6  = (CPI(M-1) / CPI(M-7)) ** 2 - 1        6 months, annualised by compounding
      inf12 =  CPI(M-1) / CPI(M-13)      - 1        the trailing 12 months
      accel = 100 * (inf6 - inf12)                  in percentage points; hot <=> accel > 0.5

  Computed on the series' own monthly grid (its declared range), as `IB12` does for its CPI YoY,
  so nothing depends on the window.
* **Bootstrap chunks.**  A column replays a drawn source session, so the state is read at that
  source session's month: the source date of column ``t`` is ``extra_days[extra_index[p, t]]`` --
  the convention `extra.aligned` uses for every registered series (`data.extra` values are
  ``pool[extra_index]``) -- and the panel's own `data.days` on a real window.
* **Missing -> neutral.**  Where `accel` is undefined (before 1948-02, and the one month that reads
  the BLS's missing 2025-10 release) A and B do not act and C drops the 4th vote (2-of-3 among
  the price votes, i.e. production's vote), the convention of `IB12`'s vote arm.
* **Scope.**  A and B multiply / zero the whole production target (E2 and the early path; B's
  "weight = 0" overrides both); C feeds its vote to production's R2 state machine unchanged.
  Execution is production K.
* **Availability.**  The bank lists all six OOS windows (US CPI as a global inflation-regime
  state, M2 live from 1948).  `NotApplicable` only where the window has no defined `accel` at all.
"""

from __future__ import annotations

import numpy as np

from research.batch_screen import extra as _ext
from research.batch_screen.api import NotApplicable
from research.fast_engine import fe_signals
from research.fast_engine.fe_config import DON_N, SMA_N

#: series name -> (month ordinals, accel in pp) on the series' own monthly grid
_CACHE = {}

ARMS = {
    "A": {"rule": "K", "leg": "2x", "cash_yield": "none", "mode": "scale", "thr_pp": 0.5,
          "factor": 0.5, "lag_months": 1},
    "B": {"rule": "K", "leg": "2x", "cash_yield": "none", "mode": "scale", "thr_pp": 0.5,
          "factor": 0.0, "lag_months": 1},
    "C": {"rule": "K", "leg": "2x", "cash_yield": "none", "mode": "vote4", "thr_pp": 0.5,
          "votes": 4, "need": 3, "lag_months": 1},
}


def _month_ord(day_strings):
    d = np.asarray(day_strings).astype("datetime64[D]").astype("datetime64[M]")
    return d.astype(np.int64)


def _accel_grid(name, lag):
    ck = (name, int(lag))
    if ck not in _CACHE:
        meta = _ext.series_meta(name)
        lo, hi = np.datetime64(meta["range"][0], "M"), np.datetime64(meta["range"][1], "M")
        grid = np.arange(lo, hi + np.timedelta64(2, "M"))   # +1 month: the lag reaches past the end
        lvl = _ext.values_on(name, grid.astype("datetime64[D]").astype(str))
        k = np.arange(grid.size)

        def lv(back):
            j = k - back
            return np.where(j >= 0, lvl[np.clip(j, 0, None)], np.nan)

        cur = lv(lag)
        inf6 = (cur / lv(lag + 6)) ** 2 - 1.0
        inf12 = cur / lv(lag + 12) - 1.0
        _CACHE[ck] = (_month_ord(grid.astype("datetime64[D]").astype(str)), 100.0 * (inf6 - inf12))
    return _CACHE[ck]


def _source_days(data):
    """The source session date of every column, ``(P, T)``."""
    if data.extra_index is None:
        return np.tile(np.asarray(data.days), (data.P, 1))
    return np.asarray(data.extra_days)[np.asarray(data.extra_index)]


def cpi_accel(data, lag):
    """``(P, T)`` CPI acceleration in pp at each column's source month (NaN where undefined)."""
    months, acc = _accel_grid(_ext.resolve_series("cpi", data.extra_window or data.window), lag)
    m = _month_ord(_source_days(data))
    slot = np.searchsorted(months, m, side="right") - 1
    ok = (slot >= 0) & (months[np.clip(slot, 0, None)] == m)
    return np.where(ok, acc[np.clip(slot, 0, None)], np.nan)


def targets(data, spec):
    acc = cpi_accel(data, int(spec["lag_months"]))
    known = np.isfinite(acc)
    if not known.any():
        raise NotApplicable("CPI acceleration (FRED CPIAUCSL from 1947, 13 months of history and a "
                            "1-month lag) has no value in this window")
    hot = known & (np.where(known, acc, 0.0) > float(spec["thr_pp"]))
    comps = fe_signals.early_components(data.price)
    if spec["mode"] == "vote4":
        s = fe_signals.sma_up(data.price, SMA_N).astype(int)
        t = fe_signals.tsm_up_price(data.price).astype(int)
        d = fe_signals.don_up(data.price, DON_N).astype(int)
        v4 = np.where(known, (~hot).astype(int), 0)
        need = np.where(known, int(spec["need"]), int(spec["votes"]) - 2)
        e2 = (s + t + d + v4) >= need
        on_eff, factor = fe_signals.r2_state(data.price, e2, comps)
        return fe_signals.sized_wants(on_eff, factor, data.vol)
    e2 = fe_signals.e2_up(data.price)
    on_eff, factor = fe_signals.r2_state(data.price, e2, comps)
    wants = fe_signals.sized_wants(on_eff, factor, data.vol)
    return wants * np.where(hot, float(spec["factor"]), 1.0)


__all__ = ["ARMS", "cpi_accel", "targets"]
