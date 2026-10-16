"""IC33 -- off-state in TIPS (TIP). idea_bank_v2, arms A/B/C.

    A  while off, hold 0.98 TIP
    B  while off, hold the residual (0.98 - w) in TIP
    C  hold 0.98 TIP while off AND the 126-session change of the breakeven (T10YIE) > 0, else cash

Frozen readings (parameters fixed in `ARMS`; nothing tuned):

* **"the vote"** is production's E2 trend vote on the window's own price close (`fe_signals.e2_up`);
  `off` is that vote being off -- the framework's off-state recipe (`README.md` section 1b).
* **"hold 0.98 TIP while off; production target otherwise"** (arm A; arm C adds the gate) is read
  as a *substitution*: leg A holds production's own target only while the vote is on and is exactly
  zero while it is off, leg B holds the sleeve, so the total never exceeds 0.98. Stated
  consequence: while E2 is off but the early re-entry is active, production's half-weight QLD path
  is *replaced* by the sleeve.
* **"the residual (0.98 - w)"** (arm B) is `leg B = max(0, 0.98 - w)` while off, `w` being
  production's own target weight on that session (its early-path weight or zero); leg A keeps
  production's target unchanged, so while the vote is off the sleeve tops the account up to 0.98
  and while it is on the sleeve is zero. No resize band: the arm text names none.
* **arm C's gate** is the bank's literal condition on FRED `T10YIE` (the 10-year breakeven, in
  percentage points): `T10YIE[t] - T10YIE[t-126] > 0`, false while 126 sessions of history are not
  available. The series is read with `ffill=True` (FRED has no value on non-publication sessions,
  and a condition should see the last known breakeven), and a window in which it has no value at
  all is `NotApplicable`. "else cash" means the sleeve is zero and leg A stays zero while the vote
  is off.
* **the instrument never switches**: leg B is the fixed registered `tip` series with a
  state-dependent weight; leg A stays the 2x panel leg.
* **window**: TIP starts 2003-12-05, so `start = "auto"` -- the window begins one session after
  the first session every declared input covers (TIP and, for arm C, T10YIE) and K is re-simulated
  on the same window as control (`_control_w20031208.json`). Windows before TIP's first session are
  `NotApplicable` (the framework's `leg_b_usable`), the bank's "2003+ -> M3/M4/M5" reduced G7.
* **execution** is production K on both books (0.5 pp band, A1u gain band, H2 December deferral, per
  leg on its own FIFO lot book, next-open fills, per-leg costs).
* **leg-B prices** are TIP's adjusted close for both that leg's fill and its close (the framework's
  convention for an extra-series leg). `tip` is registered by `research/plugins_d1/build_extra.py`.
"""

from __future__ import annotations

import numpy as np

from research.batch_screen.api import NotApplicable
from research.fast_engine import fe_signals

#: the breakeven change lookback the bank fixes ("the 126-session change")
BE_LAG = 126

ARMS = {
    "A": {"rule": "K", "leg": "2x", "leg_b": "tip", "cash_yield": "none", "start": "auto",
          "inputs": [], "form": "replace", "weight": 0.98},
    "B": {"rule": "K", "leg": "2x", "leg_b": "tip", "cash_yield": "none", "start": "auto",
          "inputs": [], "form": "residual", "total": 0.98},
    "C": {"rule": "K", "leg": "2x", "leg_b": "tip", "cash_yield": "none", "start": "auto",
          "inputs": ["t10yie"], "form": "replace", "weight": 0.98, "gate": "breakeven",
          "be_key": "t10yie", "be_lag": BE_LAG},
}

def production_wants(data):
    """Production's target series: E2's vote plus the half-weight early re-entry, vol-sized."""
    e2 = fe_signals.e2_up(data.price)
    comps = fe_signals.early_components(data.price)
    on_eff, factor = fe_signals.r2_state(data.price, e2, comps)
    return fe_signals.sized_wants(on_eff, factor, data.vol)

def off_vote(data):
    """`(P, T)` bool: the E2 trend vote is off on this session."""
    return ~fe_signals.e2_up(data.price)

def lag(a, n):
    """`(P, T)` `a` shifted `n` sessions back (NaN before the window is full)."""
    out = np.full_like(a, np.nan)
    out[:, n:] = a[:, :-n]
    return out

def sleeve_condition(data, spec):
    """`(P, T)` bool: the sleeve may be held on this session (the arm's own gate, if any)."""
    if spec.get("gate") is None:
        return np.ones(data.price.shape, dtype=bool)
    be = data.extra(spec["be_key"], ffill=True)
    if not np.isfinite(be).any():
        raise NotApplicable(f"the {spec['be_key']} series has no value in this window")
    return (be - lag(be, int(spec["be_lag"]))) > 0.0

def targets(data, spec):
    wants = production_wants(data)
    off = off_vote(data)
    hold = off & sleeve_condition(data, spec)
    if spec["form"] == "residual":
        return wants, np.maximum(0.0, float(spec["total"]) - wants) * hold, spec["leg_b"]
    return wants * (~off), float(spec["weight"]) * hold, spec["leg_b"]

__all__ = ["ARMS", "lag", "off_vote", "production_wants", "sleeve_condition", "targets"]
