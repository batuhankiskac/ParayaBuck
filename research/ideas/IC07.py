"""IC07 -- swing-structure (higher-high / higher-low) vote.  Idea bank `idea_bank_v2`, arms frozen.

    A  2-of-3 vote {SMA200, swing up, DON126}      (the swing test replaces TSM252)
    B  2-of-3 vote {swing up, TSM252, DON126}      (the swing test replaces SMA200)
    C  the early path's entry additionally requires swing up

**Pivot definition**, the bank's wording made exact: a *pivot high* is a session whose high
strictly exceeds the highs of the 13 sessions before it and the 13 after it (26 neighbours, ties
break the pivot); a *pivot low* is the mirror on the lows.  A pivot at session `i` is only *known*
at session `i + 13` (the confirmation delay), so a signal at `t` only ever uses pivots confirmed at
or before `t` -- causal by construction.  **Swing up at `t`** = the last two confirmed pivot highs
are a higher high (the newer high > the older high) **and** the last two confirmed pivot lows are a
higher low (the newer low > the older low); with fewer than two confirmed pivots of either kind the
test is off.  Both comparisons are on the arms' own high/low series.

**Where the high/low come from.**  `plugins_b3.helpers.relative_high_low`: the panel's own OHLC on
the 21 ETF windows, otherwise the registered `underlying_relhigh`/`underlying_rellow` ratios
(session high / close, derived from the same repo OHLC the panels' prices come from) times the
panel's own price -- a ratio, so it replays correctly inside a bootstrap chunk.

**Applicability (the bank's pre-registered drops).**  The bank is internally inconsistent here: its
structured evaluable list is `GSPC_1957_1999, M1, M3, M4, M5` (no `M2`), while its note says the
dropped window is `GSPC_1957_1999` ("close-only ... reduced 5-window G7").  Each of the two windows
is excluded by one of the two statements *and* lacks real high/low data, so both are refused and
the reduced G7 runs on 4 windows (`M1`, `M3`-`M5`; the framework requires k >= 3):
`M2` (^GSPC 1935-1956) is refused because the bank's list omits it and its registered high/low are
placeholders (`h == l == c`) on 100 % of its sessions, so its "pivots" would be close pivots.
`GSPC_1957_1999` is refused because the harness builds that window close-only and the registered
`^GSPC-HL` ratio series reaches it only for 1954-1956 (6.5 % of its sessions), so pivots there
would be pivots of nothing (the bank's note; `ib03` drops it the same way).  The check is on
`extra_window or window`, so a pooled-OOS bootstrap chunk of either market is refused too.  The
main window, `M1`, `M3`-`M5`, the 21 ETFs, the synthetic panel and both bootstrap families are
applicable.  Disclosed degeneracy on the evaluable OOS windows (IB06/IB10's caveat, unchanged): the
repo files' high/low are placeholders (`h == l == c`) on 47 % of ^IXIC 1971-1999 (`M1`) and 38 % of
^N225 (`M3`), where a "high" is a close and the pivots are close pivots.

No engine change: the 2-of-3 majority, the sizing, the early machine (only its entry gate in arm C,
via `helpers.r2_state_with_entry`) and the execution layer (`rule` = K) are production's.
"""

from __future__ import annotations

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view

from research.batch_screen.api import NotApplicable
from research.fast_engine import fe_signals
from research.fast_engine.fe_config import DON_N, SMA_N
from research.plugins_b3 import helpers

#: the windows the idea bank drops (see the module docstring)
DROPPED_WINDOWS = ("GSPC_1957_1999", "M2")

ARMS = {
    "A": {"rule": "K", "leg": "2x", "cash_yield": "none", "mode": "vote_no_tsm", "k": 13},
    "B": {"rule": "K", "leg": "2x", "cash_yield": "none", "mode": "vote_no_sma", "k": 13},
    "C": {"rule": "K", "leg": "2x", "cash_yield": "none", "mode": "entry_gate", "k": 13},
}


def _extremes(series, k, want_max):
    """`(P, T)` bool: `series[i]` (strictly) exceeds / undercuts the `k` values on each side.

    `np.fmax.reduce` / `np.fmin.reduce` ignore NaNs, so a NaN neighbour cannot create a pivot and a
    NaN candidate is never one.
    """
    series = np.atleast_2d(np.asarray(series, dtype=np.float64))
    P, T = series.shape
    out = np.zeros((P, T), dtype=bool)
    if T < 2 * k + 1:
        return out
    win = sliding_window_view(series, k, axis=1)  # win[:, j] = series[j : j + k]
    reduce_ = np.fmax.reduce if want_max else np.fmin.reduce
    before = np.full((P, T), -np.inf if want_max else np.inf)
    after = np.full((P, T), -np.inf if want_max else np.inf)
    before[:, k:] = reduce_(win[:, : T - k], axis=2)   # at i: series[i - k : i]
    after[:, : T - k] = reduce_(win[:, 1:], axis=2)    # at i: series[i + 1 : i + k + 1]
    out[:, k : T - k] = np.where(want_max, series[:, k : T - k] > before[:, k : T - k],
                                 series[:, k : T - k] < before[:, k : T - k])
    out[:, k : T - k] &= np.where(want_max, series[:, k : T - k] > after[:, k : T - k],
                                  series[:, k : T - k] < after[:, k : T - k])
    return out


def _events(pivots, k):
    """`(P, T)` int64: at session `c` the pivot index confirmed at `c` (`c - k`), else -1."""
    P, T = pivots.shape
    ev = np.full((P, T), -1, dtype=np.int64)
    if T > k:
        ev[:, k:] = np.where(pivots[:, : T - k], np.arange(T - k, dtype=np.int64), -1)
    return ev


def _last_two(ev):
    """`(m1, m2)`: the index of the newest and the second-newest event at each session (-1 if none).

    `m2 = running_max(min(ev, m1 shifted by one))`: where `ev` sets a new maximum the previous
    maximum becomes the candidate, elsewhere `ev` itself is -- the standard running top-2 scan,
    with both arrays produced by `np.maximum.accumulate`.
    """
    m1 = np.maximum.accumulate(ev, axis=1)
    prev = np.concatenate([np.full(ev.shape[:1] + (1,), -1, dtype=np.int64), m1[:, :-1]], axis=1)
    m2 = np.maximum.accumulate(np.minimum(ev, prev), axis=1)
    return m1, m2


def _higher(series, m1, m2):
    """`series[m1] > series[m2]`, False unless both pivots exist."""
    n = series.shape[1]
    v1 = np.take_along_axis(series, np.clip(m1, 0, n - 1), axis=1)
    v2 = np.take_along_axis(series, np.clip(m2, 0, n - 1), axis=1)
    return (m1 >= 0) & (m2 >= 0) & (v1 > v2)


def swing_up(high, low, k):
    """`(P, T)` bool: the last two confirmed pivot highs are higher *and* the lows are higher."""
    highs = _last_two(_events(_extremes(high, k, True), k))
    lows = _last_two(_events(_extremes(low, k, False), k))
    return _higher(high, *highs) & _higher(low, *lows)


def targets(data, spec):
    w = data.extra_window or data.window
    if w in DROPPED_WINDOWS:
        why = ("fe_data.load_gspc is close-only and the registered high/low covers 1954-1956 only"
               if w == "GSPC_1957_1999" else
               "the registered ^GSPC 1935-1956 high/low are placeholders (h == l == c)")
        raise NotApplicable(f"{why}; outside the idea bank's evaluable OOS set")
    price = data.price
    high, low = helpers.relative_high_low(data)
    k = int(spec["k"])
    swing = swing_up(high, low, k)
    comps = fe_signals.early_components(price)
    s = fe_signals.sma_up(price, SMA_N).astype(np.int64)
    t = fe_signals.tsm_up_price(price).astype(np.int64)
    d = fe_signals.don_up(price, DON_N).astype(np.int64)
    sw = swing.astype(np.int64)

    mode = spec["mode"]
    if mode == "entry_gate":
        on_eff, factor = helpers.r2_state_with_entry(price, fe_signals.e2_up(price),
                                                     comps[0] & swing, comps)
        return helpers.capped(on_eff, factor, data.vol)
    vote = (sw + t + d) >= 2 if mode == "vote_no_sma" else (s + sw + d) >= 2
    on_eff, factor = fe_signals.r2_state(price, vote, comps)
    return helpers.capped(on_eff, factor, data.vol)
