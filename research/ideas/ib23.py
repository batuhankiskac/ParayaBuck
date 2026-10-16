"""IB23 -- copper/gold ratio trend (growth-vs-safety) as a regime filter.

Per `docs/research/idea_bank_v1.json` IB23, with `R = HG=F / GC=F` (both COMEX front-month
continuations, daily closes, aligned by date) and `r_R(n)` its trailing `n`-session return:

    A  3-of-4 vote {SMA200, TSM252, DON126, r_R(252) > 0}
    B  the early-path entry is permitted only while r_R(126) >= 0
    C  weight *= 0.75 while R is below its trailing 252-session median

Everything else is production: the R2 sizing (`min(1, 0.35/vol20)` x path factor, cap 0.98), the
early re-entry machine, the execution layer (rule `K`). Fixed parameters: the 252/126 sessions and
the 0.75 multiplier are the arm text's; the copper key is the framework's registered `copper`
(HG=F) and gold is this branch's registered `gold` (GC=F, `research/plugins_b3/build_extra.py`).

**Data availability (pre-registered).** Both futures start 2000-08-30, so the idea bank declares
the OOS set empty ("no pre-2000 futures data in the repo -> reduced gate (main window, 21-ETF
stage, bootstraps)") and the main window a reduced start (~2001-09, i.e. 252 sessions of warm-up).
This plugin therefore raises `NotApplicable` on all six OOS windows. On the main window, its
bootstrap chunks and the ETFs it keeps the *literal* window boundaries and follows the campaign's
"unavailable -> production" convention (see `helpers.nan_clip_ratio`): while a session has no
copper/gold history (before 2000-08-30) or no full 252-session return yet, arm A uses production's
2-of-3 vote, arm B permits the early entry as production does and arm C's multiplier is 1.0 -- so
the pre-signal part of a window behaves exactly like the K control rather than manufacturing a
cash position (or a signal) out of a data gap. The sign of that choice is disclosed here: the
1.4-year prefix of the main window is a K clone by construction, not a measured arm effect.

No engine change.
"""

from __future__ import annotations

import numpy as np

from research.batch_screen.api import NotApplicable
from research.fast_engine import fe_signals
from research.fast_engine.fe_config import DON_N, SMA_N
from research.plugins_b3 import helpers

#: pre-registered: no copper/gold history before 2000-08-30, so no OOS window is evaluable
NOT_EVALUABLE = ("GSPC_1957_1999", "M1", "M2", "M3", "M4", "M5")

ARMS = {
    "A": {"rule": "K", "leg": "2x", "cash_yield": "none", "mode": "vote", "n": 252},
    "B": {"rule": "K", "leg": "2x", "cash_yield": "none", "mode": "gate", "n": 126},
    "C": {"rule": "K", "leg": "2x", "cash_yield": "none", "mode": "median", "n": 252,
          "mult": 0.75},
}


def ratio(data):
    """`(P, T)` HG=F / GC=F, NaN where either leg is unknown."""
    copper = data.extra("copper")
    gold = data.extra("gold")
    good = np.isfinite(copper) & np.isfinite(gold) & (gold > 0)
    r = np.where(good, np.where(good, copper, 1.0) / np.where(good, gold, 1.0), np.nan)
    if not np.isfinite(r).any():
        raise NotApplicable("neither HG=F nor GC=F has a value in this window")
    return r


def trailing_return(a, n):
    """`(P, T)` `a_t / a_{t-n} - 1`, NaN where either end is unknown."""
    return a / helpers.lag(a, n) - 1.0


def targets(data, spec):
    if data.window in NOT_EVALUABLE:
        raise NotApplicable(
            f"HG=F and GC=F both start 2000-08-30, so {data.window} has no copper/gold history; "
            "the idea bank pre-registers an empty OOS set for this arm"
        )
    price = data.price
    r = ratio(data)
    n = spec["n"]
    rr = trailing_return(r, n)
    ok = np.isfinite(rr)

    e2 = fe_signals.e2_up(price)
    comps = fe_signals.early_components(price)
    mode = spec["mode"]

    if mode == "vote":
        s = fe_signals.sma_up(price, SMA_N).astype(np.int64)
        t = fe_signals.tsm_up_price(price).astype(np.int64)
        d = fe_signals.don_up(price, DON_N).astype(np.int64)
        vote = (ok & (rr > 0)).astype(np.int64)
        e2 = helpers.vote_3_of_4(s, t, d, vote, ok)
        on_eff, factor = fe_signals.r2_state(price, e2, comps)
        return helpers.capped(on_eff, factor, data.vol)

    if mode == "gate":
        entry_up = comps[0] & (~ok | (rr >= 0))
        on_eff, factor = helpers.r2_state_with_entry(price, e2, entry_up, comps)
        return helpers.capped(on_eff, factor, data.vol)

    if mode == "median":
        med = helpers.trailing_median_full(r, n)
        below = np.isfinite(med) & (r < med)
        on_eff, factor = fe_signals.r2_state(price, e2, comps)
        wants = helpers.capped(on_eff, factor, data.vol)
        return helpers.scaled(wants, np.where(below, spec["mult"], 1.0))
    raise ValueError(f"unknown mode {mode!r}")  # pragma: no cover
