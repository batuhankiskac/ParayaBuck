"""IC19 -- oil-shock veto. Idea bank `idea_bank_v2` (frozen arm text):

    A  weight *= 0.5 while r_oil(126) > +50% AND WTI > median252(WTI)
    B  weight = 0 in the same state
    C  3-of-4 vote {SMA200, TSM252, DON126, r_oil(126) <= +50%}, on iff >= 3

Series: FRED `DCOILWTICO` (Cushing WTI spot, daily, 1986-01-02 ->). `r_oil(126)` is the
126-observation return and `median252` the trailing 252-observation median (including the current
one) on FRED's own observation sessions (`research/plugins_c4/signals.py: oil_state`), dated on
the observation date (same-day, no lag: the bank states none) and carried forward to every calendar
day (registered keys `oil_r126`, `oil_med_gap` = WTI - median252(WTI)). Why precomputed rather than
taken over the panel: see `plugins_c4/signals.py` (a 126/252-session window inside a 20-session
bootstrap block would mix oil price levels of different eras). The 2020-04-20 negative print is
kept as published; the ratio is computed literally.

Literal reading: "weight" is production K's whole target weight; arm B sets it to zero in the
state; arm C replaces E2's 2-of-3 vote with the 3-of-4 vote (production's early re-entry on top).
The A/B state needs both conditions defined; a session where either is undefined is neutral.

**Applicability (pre-registered by the bank).** `M2` (1934-1956) predates the series and is
refused; `GSPC_1957_1999` and `M1` are covered from 1986-07 and follow the neutral convention
before (`plugins_c4/common.py`). No engine change.
"""

from __future__ import annotations

import numpy as np

from research.plugins_c4 import common

NOT_EVALUABLE = ("M2",)

ARMS = {
    "A": {"rule": "K", "leg": "2x", "cash_yield": "none", "mode": "scale", "thr": 0.50,
          "mult": 0.5},
    "B": {"rule": "K", "leg": "2x", "cash_yield": "none", "mode": "scale", "thr": 0.50,
          "mult": 0.0},
    "C": {"rule": "K", "leg": "2x", "cash_yield": "none", "mode": "vote", "thr": 0.50},
}


def targets(data, spec):
    common.refuse(data, NOT_EVALUABLE, "DCOILWTICO starts 1986-01-02, after this window ends")
    r126 = common.state(data, "oil_r126")
    thr = float(spec["thr"])
    if spec["mode"] == "scale":
        gap = data.extra("oil_med_gap")
        ok = np.isfinite(r126) & np.isfinite(gap)
        shock = ok & (np.where(ok, r126, 0.0) > thr) & (np.where(ok, gap, 0.0) > 0.0)
        wants = common.production(data)[-1]
        return wants * np.where(shock, float(spec["mult"]), 1.0)
    if spec["mode"] == "vote":
        ok = np.isfinite(r126)
        return common.vote4(data, np.where(ok, r126, np.inf) <= thr, ok)
    raise ValueError(f"unknown mode {spec['mode']!r}")  # pragma: no cover
