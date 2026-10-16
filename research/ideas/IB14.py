"""IB14 -- credit-spread momentum (widening BAA-10y), not the spread level.  Idea bank
`idea_bank_v1`.

    A  weight = 0 while BAA10Y_t - BAA10Y_{t-60} > +0.30 pp
    B  weight *= 0.5 while the same change > +0.15 pp
    C  3-of-4 vote {SMA200, TSM252, DON126, 60-session spread change <= +0.30 pp}

The statistic is a fixed-horizon 60-session change of the credit series (no lag on the daily
series), applied to the production E2 + early-re-entry target; the K execution layer is unchanged.
A `NaN` change (series not yet 60 sessions old, or a session the series does not reach) is
*neutral*: no veto, no halving, and a "no credit stress" vote for arm C -- the framework's
convention for a missing extra series, and irrelevant on the main window, where the daily series
starts 1986 and the measured window starts 2000.

**Which series** (the idea's note: "monthly BAA-AAA (1919+) with a 1-month lag, the credit_vote_v1
OOS convention"), selected per window:

* `main`, both bootstrap families and the 21 ETF windows: the registered daily FRED `BAA10Y`
  (`baa10y`, 1986+), forward-filled over FRED's non-publication days (the credit_vote_v1
  amendment's convention for the daily series; 0.8 % of the main window's sessions).
* `GSPC_1957_1999`, `M1`, `M2`: the monthly Moody's `BAA - AAA` spread, both registered from FRED
  by this branch (`baa_m`, `aaa_m`, 1919+), used **only from the first session of the following
  month** (the 1-month lag): on a session of month M the series carries the observation of
  reference month M-1.  That value is read from the series' **own monthly grid** with an as-of
  lookup (`_asof_month`, the last reference month at or before M-1), *not* by an exact-date match
  on the panel's sessions plus a forward fill: FRED stamps a monthly observation the 1st, so a
  month whose 1st is not a trading session is never matched by an exact-date join and the
  *previous* month's value carries over (two months behind, for the whole of the month that reads
  it -- the bug this branch fixes).  The arm's own 60-session change and thresholds are then
  applied to that session-mapped series, i.e. the series is substituted and the statistic kept,
  the most literal reading of the bank's rule.
* `M3`, `M4`, `M5`: `NotApplicable`.  The idea declares its OOS evaluable set as the three US
  windows above -- credit_vote_v1's convention (credit arms were evaluated on the US windows only),
  which is the "reduced 3-window G7" the bank's note refers to.  Data exists on M4/M5 (the daily
  series covers 97 %/93 % of their sessions), so this is a declared-scope refusal, not a
  data-missing one -- flagged so the reduced G7 is read as pre-registered, not accidental.

The window a key resolves through is `extra_window or window`, so a bootstrap chunk uses its pool's
own credit series (`main` for the clean/fresh bootstraps, the market key for the pooled OOS one).
"""

from __future__ import annotations

import numpy as np

from research.batch_screen import extra as _ext
from research.batch_screen.api import NotApplicable
from research.fast_engine import fe_signals
from research.fast_engine.fe_config import SMA_N

ARMS = {
    "A": {"rule": "K", "leg": "2x", "cash_yield": "none", "threshold": 0.30, "lookback": 60,
          "action": "veto"},
    "B": {"rule": "K", "leg": "2x", "cash_yield": "none", "threshold": 0.15, "lookback": 60,
          "action": "halve"},
    "C": {"rule": "K", "leg": "2x", "cash_yield": "none", "threshold": 0.30, "lookback": 60,
          "vote": "3of4"},
}

#: the idea's declared OOS set (`data.oos.evaluable`): the three US windows, on the monthly proxy
MONTHLY_WINDOWS = ("GSPC_1957_1999", "M1", "M2")
NON_US = ("M3", "M4", "M5")
MIN_COVER = 0.99

#: (baa name, aaa name) -> (month ordinals, spread), built once per process
_MONTHLY_CACHE = {}


def _month_ordinals(days):
    """Month ordinals (months since 1970-01) of ISO day strings, shape preserved."""
    d = np.asarray(days).astype("datetime64[D]").astype("datetime64[M]")
    return d.astype(np.int64)


def _monthly_spread(baa, aaa):
    """``(month ordinals, spread)`` of the BAA-AAA spread on the series' own monthly grid.

    The grid is the months both series declare (FRED dates a monthly observation the 1st of its
    reference month); months missing from either series are dropped, so the as-of lookup below can
    never return a hole.  Nothing depends on the window -- a bootstrap chunk's drawn source session
    maps to the same value it has in the real window.
    """
    ck = (baa, aaa)
    if ck not in _MONTHLY_CACHE:
        lo = max(np.datetime64(_ext.series_meta(baa)["range"][0], "M"),
                 np.datetime64(_ext.series_meta(aaa)["range"][0], "M"))
        hi = min(np.datetime64(_ext.series_meta(baa)["range"][1], "M"),
                 np.datetime64(_ext.series_meta(aaa)["range"][1], "M"))
        grid = np.arange(lo, hi + np.timedelta64(1, "M"))
        values = (_ext.values_on(baa, grid.astype("datetime64[D]").astype(str))
                  - _ext.values_on(aaa, grid.astype("datetime64[D]").astype(str)))
        have = np.isfinite(values)
        _MONTHLY_CACHE[ck] = (grid[have].astype(np.int64), values[have])
    return _MONTHLY_CACHE[ck]


def _asof_month(months, values, m):
    """``(P, T)`` values of the last month ordinal ``<= m`` (NaN before the series starts)."""
    slot = np.searchsorted(months, m, side="right") - 1
    return np.where(slot >= 0, values[np.clip(slot, 0, None)], np.nan)


def window_key(data) -> str:
    return data.extra_window or data.window


def session_calendar(data) -> np.ndarray:
    """`(P, T)` session labels, one per panel column (see the module docstring)."""
    if data.extra_index is not None:  # a bootstrap chunk: the drawn source sessions
        return np.asarray(data.extra_days)[np.asarray(data.extra_index)]
    d = np.asarray(data.days, dtype="<U10")
    if d.size == data.T - 1:  # defensive: a chunk column 0 has no label of its own
        d = np.concatenate([d[:1], d])
    if d.size != data.T:
        raise ValueError(f"calendar length {d.size} != panel width {data.T}")
    return np.tile(d.reshape(1, -1), (data.P, 1))


def shift(a, n):
    """`a` shifted `n` sessions forward (NaN in the first `n` columns)."""
    out = np.full_like(a, np.nan)
    if a.shape[1] > n:
        out[:, n:] = a[:, :-n]
    return out


def credit_series(data, spec):
    """`(series, what)`: the window's credit series (see the module docstring)."""
    w = window_key(data)
    if w in NON_US:
        raise NotApplicable(
            "the idea's declared OOS set: the credit arms are evaluated on the US windows only "
            "(credit_vote_v1's convention); M3-M5 are not evaluated for this arm")
    if w in MONTHLY_WINDOWS:
        what = "monthly BAA-AAA (1-month lag)"
        months, spread = _monthly_spread(_ext.resolve_series("baa_m", w),
                                         _ext.resolve_series("aaa_m", w))
        mk = _month_ordinals(session_calendar(data))
        # the 1-month publication lag: on a session of month M the series carries the observation of
        # reference month M-1, as an as-of lookup on the series' own monthly grid (no reliance on a
        # panel column of month M-1 existing, and valid for a chunk's non-monotone drawn sessions).
        raw = _asof_month(months, spread, mk - 1)
        if np.isfinite(raw).mean() < MIN_COVER:
            raise NotApplicable(f"the {what} series does not cover this window's sessions")
        return raw, what
    series = data.extra("baa10y", ffill=True)
    if np.isfinite(series).mean() < MIN_COVER:
        raise NotApplicable("the daily BAA10Y series does not cover this window's sessions")
    return series, "daily BAA10Y"


def credit_change(data, spec):
    """The 60-session change of the window's credit series (NaN where undefined)."""
    series, _what = credit_series(data, spec)
    return series - shift(series, int(spec["lookback"]))


def targets(data, spec):
    chg = credit_change(data, spec)
    stressed = np.where(np.isfinite(chg), chg > float(spec["threshold"]), False)
    e2 = fe_signals.e2_up(data.price)
    comps = fe_signals.early_components(data.price)
    if spec.get("vote") == "3of4":
        sma = fe_signals.sma_up(data.price, SMA_N)
        tsm = fe_signals.tsm_up_price(data.price)
        don = fe_signals.don_up(data.price)
        credit = np.where(np.isfinite(chg), ~stressed, True)
        vote = (sma.astype(int) + tsm.astype(int) + don.astype(int) + credit.astype(int)) >= 3
        on_eff, factor = fe_signals.r2_state(data.price, vote, comps)
        return fe_signals.sized_wants(on_eff, factor, data.vol)
    on_eff, factor = fe_signals.r2_state(data.price, e2, comps)
    wants = fe_signals.sized_wants(on_eff, factor, data.vol)
    if spec["action"] == "halve":
        return wants * np.where(stressed, 0.5, 1.0)
    return wants * (~stressed)
