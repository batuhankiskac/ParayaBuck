"""IB19 -- US presidential-election cycle tilt.

Per `docs/research/idea_bank_v1.json` IB19, a calendar multiplier on the production target weight
(`min(1, 0.35/vol20)` x path factor, cap 0.98, rule `K`), from the calendar year alone. The term
year is `year % 4` for the US cycle (election years 1932..2024 are the years divisible by 4, so
`year % 4 == 0` is the fourth/election year of a term, 3 the third, and 1-2 the first half):

    A  target *= 1.00 in term years 3-4, *= 0.85 in years 1-2
    B  target *= 0.85 in the election year only
    C  target *= 0.85 in years 3-4, 1.00 in years 1-2  (the mirror)

The multiplier is applied to the *final* target weight (both the voted and the early leg), which
is what "target *= ..." in the arm text says, and production's total cap 0.98 still binds
afterwards; the tilt is a level change, not a signal change, so the production execution layer
sees only a smaller target. Fixed parameter: 0.85 (declared in every arm's spec).

**Availability (pre-registered).** The political cycle is US-specific, so the idea bank lists only
`GSPC_1957_1999`, M1 and M2 as evaluable; this plugin raises `NotApplicable` on the three non-US
OOS markets (M3 ^N225, M4 ^GDAXI, M5 ^FTSE). Everything else -- the main window, its bootstrap
chunks, the 21 US-listed ETFs -- is applicable.

No extra data, no engine change.
"""

from __future__ import annotations

import numpy as np

from research.batch_screen.api import NotApplicable
from research.fast_engine import fe_signals
from research.plugins_b3 import helpers

#: pre-registered: the cycle is a US calendar effect and these three windows are not US markets
NOT_EVALUABLE = ("M3", "M4", "M5")

ARMS = {
    "A": {"rule": "K", "leg": "2x", "cash_yield": "none", "mode": "years_1_2", "tilt": 0.85},
    "B": {"rule": "K", "leg": "2x", "cash_yield": "none", "mode": "election_year",
          "tilt": 0.85},
    "C": {"rule": "K", "leg": "2x", "cash_yield": "none", "mode": "years_3_4", "tilt": 0.85},
}


def multiplier(year, mode, tilt):
    """The `(T,)` term-year multiplier; `year` is the panel's own session-year vector, aligned to
    its columns by `helpers.session_year`."""
    y = np.asarray(year, dtype=np.int64)
    term = y % 4  # 0 = election year (year 4 of the term), 3 = year 3, 1-2 = years 1-2
    if mode == "years_1_2":
        cut = (term == 1) | (term == 2)  # 0.85 in years 1-2, 1.00 in years 3-4
    elif mode == "election_year":
        cut = term == 0
    elif mode == "years_3_4":
        cut = (term == 3) | (term == 0)  # the mirror: 0.85 in years 3-4
    else:  # pragma: no cover - the specs above are the only ones registered
        raise ValueError(f"unknown mode {mode!r}")
    return np.where(cut, tilt, 1.0)


def targets(data, spec):
    if data.window in NOT_EVALUABLE:
        raise NotApplicable(
            f"{data.window} is not a US market; the idea bank declares the presidential cycle "
            "evaluable on the US windows only"
        )
    e2 = fe_signals.e2_up(data.price)
    comps = fe_signals.early_components(data.price)
    on_eff, factor = fe_signals.r2_state(data.price, e2, comps)
    wants = helpers.capped(on_eff, factor, data.vol)
    mult = multiplier(helpers.session_year(data), spec["mode"], spec["tilt"])
    return helpers.scaled(wants, mult)
