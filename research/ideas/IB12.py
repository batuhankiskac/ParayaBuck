"""IB12 -- real 10-year yield veto (nominal DGS10 minus trailing CPI inflation), batch_screen plugin.

Idea bank `docs/research/idea_bank_v1.json` (main), three arms, parameters frozen by the spec:

    A  ``weight = 0 while real10y > 2.0%``, else production
    B  ``weight *= 0.5 while real10y > 1.5%``
    C  ``3-of-4 vote {SMA200, TSM252, DON126, real10y <= 2.0%}``

    real10y = FRED DGS10 - CPI YoY, CPI monthly with **1-month publication lag**

Everywhere the spec leaves a detail open the most literal reading is fixed here:

* **The lag.**  FRED `CPIAUCSL` stamps each reference month on its first day and publishes it about
  a month later, so at a session of month ``M`` the arm uses the level of month ``M-1`` and the
  level of ``M-13``: ``yoy = CPI(M-1)/CPI(M-13) - 1`` (exactly one year of inflation, one month
  behind the reference month).  DGS10 is a daily level: no lag, read through the extra registry
  (`dgs10`, `cpi`); the CPI levels come from the series' own monthly grid (`cpi`).
* **Bootstrap chunks.**  A chunk replays the *drawn source session* of each column, so the YoY is
  computed from the source session's month (`data.extra_days` / `data.extra_index`, the source
  session each column replays; the panel's own sessions on a real window) on the CPI series' own
  monthly grid -- the same number the real window uses at that session, not a ratio of two
  unrelated resampled levels.  (This is the one statistic of this batch that a resampled extra
  series cannot carry by itself: a CPI *level ratio* needs the series' history.)
* **Not computable -> no veto / abstain.**  Where real10y is missing (either series absent on that
  session) arms A and B do not veto, and arm C drops the 4th vote (2-of-3 among the price votes).
  This is the convention the idea bank fixes for the analogous case (IB20's 4th vote, "neutral"
  before its first valid date); reading a missing rate as "above threshold" would silently cut
  exposure for the years before DGS10 starts (1962).
* **Availability** (`NotApplicable`), fixed in advance:  a window is *not* evaluable (i) when the
  idea's own `data.oos.evaluable` set scopes it out -- the spec pre-registers the US windows
  {GSPC_1957_1999, M1, M2} and its note states that M3/M4/M5 (Japan, Germany, UK) have no US macro
  overlay, so a US real-rate veto is not the arm that was pre-registered there (the data layer
  does carry DGS10 on their post-1962 sessions; letting them in would widen the pre-registered
  gate), and (ii) when the window carries **no DGS10 value at all** -- M2 (^GSPC 1935-1956) ends
  1956-12-31 while DGS10 starts 1962-01-02, so the spec's own expectation of three evaluable US
  windows is not available and the framework records the reduced holdout.  The 21 ETFs and the
  main window are applicable.
* **Inputs**: `data.price` (the vote), `data.vol` (sizing), and the two extra series above.
"""

from __future__ import annotations

import numpy as np

from research.batch_screen import extra as _ext
from research.batch_screen.api import NotApplicable
from research.fast_engine import fe_signals
from research.fast_engine.fe_config import DON_N, SMA_N

#: the OOS windows the idea's spec scopes out (its `oos.evaluable` is the three US windows)
SPEC_SCOPED_OUT = ("M3", "M4", "M5")

#: (series name) -> (month ordinals, lagged YoY), built once per process
_CPI_CACHE = {}

ARMS = {
    "A": {"rule": "K", "leg": "2x", "cash_yield": "none", "mode": "veto", "thr": 2.0, "factor": 0.0},
    "B": {"rule": "K", "leg": "2x", "cash_yield": "none", "mode": "half", "thr": 1.5, "factor": 0.5},
    "C": {"rule": "K", "leg": "2x", "cash_yield": "none", "mode": "vote4", "thr": 2.0, "votes": 4,
          "need": 3},
}


def _month_ord(day_strings):
    """Month ordinals (months since 1970-01) of ISO day strings, shape preserved."""
    d = np.asarray(day_strings).astype("datetime64[D]").astype("datetime64[M]")
    return d.astype(np.int64)


def _cpi_yoy(name):
    """``(month_ordinals, YoY)`` of the CPI series on its own monthly grid, cached per process.

    ``yoy[k] = level(k-1) / level(k-13) - 1``: one year of inflation, read one month behind the
    session's reference month (the 1-month publication lag).  The grid is the series' declared
    range (monthly, stamped the first of its reference month), so nothing depends on the window --
    a bootstrap chunk's drawn source session maps to the same YoY it has in the real window.  The
    registered series has one genuine gap -- there is no 2025-10 row, the month the BLS did not
    publish during the 2025 shutdown -- so the single month that reads it (2025-11) has no YoY and
    the arm abstains there; every other month is finite.
    """
    ck = ("cpi", name)
    if ck not in _CPI_CACHE:
        meta = _ext.series_meta(name)
        lo, hi = np.datetime64(meta["range"][0], "M"), np.datetime64(meta["range"][1], "M")
        grid = np.arange(lo, hi + np.timedelta64(1, "M"))
        lvl = _ext.values_on(name, grid.astype("datetime64[D]").astype(str))
        yoy = np.full(lvl.size, np.nan)
        if lvl.size > 13:
            yoy[13:] = lvl[12:-1] / lvl[:-13] - 1.0
        _CPI_CACHE[ck] = (_month_ord(grid.astype("datetime64[D]").astype(str)), yoy)
    return _CPI_CACHE[ck]


def real_10y(data):
    """DGS10 minus the 1-month-lagged CPI year-on-year rate, in percentage points, ``(P, T)``."""
    dgs10 = data.extra("dgs10", ffill=True)
    # the source session of each column is `pool[extra_index]` -- the framework's own convention
    # (`extra.aligned` returns `pool[index]`, `boot._extra_index` prepends the base column), NOT
    # `extra_index - 1`, which would read the previous real session's month.
    src = np.asarray(data.days) if data.extra_index is None else (
        np.asarray(data.extra_days)[np.asarray(data.extra_index)])
    months, yoy = _cpi_yoy(_ext.resolve_series("cpi", data.extra_window or data.window))
    m = _month_ord(src)
    slot = np.searchsorted(months, m, side="right") - 1
    slot = np.clip(slot, 0, months.size - 1)
    rate = np.where((slot >= 0) & (months[slot] == m), yoy[slot], np.nan)
    return dgs10 - 100.0 * rate


def _check_applicable(data):
    if data.meta.get("market") in SPEC_SCOPED_OUT:
        raise NotApplicable(
            "the idea's spec sets oos.evaluable to the US windows; M3/M4/M5 are pre-registered as "
            "having no US macro overlay")
    if not np.isfinite(data.extra("dgs10")).any():
        raise NotApplicable("the DGS10 series has no value in this window (it starts 1962-01-02)")


def targets(data, spec):
    """The production target series, vetoed/halved/4th-voted by the real 10-year yield."""
    _check_applicable(data)
    rr = real_10y(data)
    hot = np.nan_to_num(rr, nan=-np.inf) > spec["thr"]          # ">2.0%" / ">1.5%", missing -> no
    comps = fe_signals.early_components(data.price)
    if spec["mode"] == "vote4":
        s = fe_signals.sma_up(data.price, SMA_N).astype(int)
        t = fe_signals.tsm_up_price(data.price).astype(int)
        d = fe_signals.don_up(data.price, DON_N).astype(int)
        ok = np.isfinite(rr)
        vote = s + t + d + np.where(ok, (rr <= spec["thr"]).astype(int), 0)
        # 3 of 4 with the vote; the 4th abstains, i.e. 2 of 3 among the price votes (not 3 of 3)
        e2 = vote >= np.where(ok, spec["need"], spec["need"] - 1)
        on_eff, factor = fe_signals.r2_state(data.price, e2, comps)
        return fe_signals.sized_wants(on_eff, factor, data.vol)
    e2 = fe_signals.e2_up(data.price)
    on_eff, factor = fe_signals.r2_state(data.price, e2, comps)
    wants = fe_signals.sized_wants(on_eff, factor, data.vol)
    if spec["mode"] == "veto":
        return np.where(hot, spec["factor"], wants)
    return wants * np.where(hot, spec["factor"], 1.0)
