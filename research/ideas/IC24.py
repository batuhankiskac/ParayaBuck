"""IC24 -- turn-of-quarter exposure tilt. Idea bank `idea_bank_v2` (frozen arm text):

    A  target *= 0.75 in the 3 sessions ending the last session of Feb/May/Aug/Nov
    B  target = min(0.98, target * 1.10) in the 3 sessions spanning the quarter turn
       (last session of the quarter and the two after)
    C  mirror: target *= 0.75 in the 3 sessions after the quarter turn

Literal reading (documented, fixed before any screen):

* "target" is production K's target weight (E2 + early re-entry, `min(1, 0.35/vol20)` x path
  factor, cap 0.98); the tilt multiplies it at the closes of the masked sessions (decided at the
  close, filled at the next open -- the engine's timing, unchanged). Arm B's `min(0.98, .)` is
  production's total cap (`plugins_b3.helpers.scaled`).
* **Arm A is implemented as written: Feb/May/Aug/Nov**, i.e. the last three sessions of the month
  *before* each quarter-end month (the last session of the month and the two before it). The arm
  text does not say Mar/Jun/Sep/Dec; the bank's literal months are kept, not "corrected".
* Arm B: the last session of Mar/Jun/Sep/Dec and the next two sessions.
* Arm C: the three sessions after the last session of the quarter (sessions 1-3 of the new
  quarter), i.e. B's window shifted one session later, without the quarter's last session.
* "last session of a month" = a column whose next column falls in a different month. The final
  column of a panel has no successor and is never a month end (no information beyond the panel).
  On a bootstrap chunk the session labels are the chunk's own calendar (`data.days`, its first
  label repeated for the artificial base column -- `plugins_b3.helpers.session_year`'s rule).

Applicable on every window (dates only). No extra data, no engine change.
"""

from __future__ import annotations

import numpy as np

from research.fast_engine import fe_signals
from research.plugins_b3 import helpers

ARMS = {
    "A": {"rule": "K", "leg": "2x", "cash_yield": "none", "mode": "pre_quarter_month_end",
          "months": [2, 5, 8, 11], "mult": 0.75},
    "B": {"rule": "K", "leg": "2x", "cash_yield": "none", "mode": "quarter_turn",
          "months": [3, 6, 9, 12], "mult": 1.10},
    "C": {"rule": "K", "leg": "2x", "cash_yield": "none", "mode": "after_quarter_turn",
          "months": [3, 6, 9, 12], "mult": 0.75},
}


def column_months(data):
    """`(T,)` month of each panel column (see the module note on bootstrap chunks)."""
    m = np.asarray(data.month, dtype=np.int64)
    T = data.price.shape[1]
    if m.size == T:
        return m
    if m.size == T - 1:
        return np.concatenate([m[:1], m])
    raise ValueError(f"the panel's month vector has {m.size} entries for {T} sessions")


def month_end(months):
    """`(T,)` True on the last column of each month (never on the panel's final column)."""
    me = np.zeros(months.size, dtype=bool)
    me[:-1] = months[1:] != months[:-1]
    return me


def shifted(mask, k):
    """`out[t] = mask[t - k]` (k > 0: k sessions after; k < 0: |k| sessions before), False off-edge."""
    out = np.zeros_like(mask)
    if k > 0:
        out[k:] = mask[:-k]
    elif k < 0:
        out[:k] = mask[-k:]
    else:
        out[:] = mask
    return out


def session_mask(data, spec):
    months = column_months(data)
    me = month_end(months) & np.isin(months, np.asarray(spec["months"], dtype=np.int64))
    mode = spec["mode"]
    if mode == "pre_quarter_month_end":   # the month end and the two sessions before it
        return me | shifted(me, -1) | shifted(me, -2)
    if mode == "quarter_turn":            # the quarter's last session and the two after
        return me | shifted(me, 1) | shifted(me, 2)
    if mode == "after_quarter_turn":      # the three sessions after the quarter's last session
        return shifted(me, 1) | shifted(me, 2) | shifted(me, 3)
    raise ValueError(f"unknown mode {mode!r}")  # pragma: no cover


def targets(data, spec):
    e2 = fe_signals.e2_up(data.price)
    comps = fe_signals.early_components(data.price)
    on_eff, factor = fe_signals.r2_state(data.price, e2, comps)
    wants = helpers.capped(on_eff, factor, data.vol)
    mask = session_mask(data, spec)
    mult = np.where(mask, float(spec["mult"]), 1.0)[None, :]
    return helpers.scaled(wants, mult)
