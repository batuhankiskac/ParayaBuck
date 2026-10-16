"""IC37 -- real-yield-gated Treasury off-state (IEF). idea_bank_v2, arms A/B/C.

    A  while off, hold 0.98 IEF if real10y = DGS10 - T10YIE > +0.25%, else cash
    B  the same with the threshold at +1.00%
    C  while off, hold the residual (0.98 - w) in IEF under the +0.25% condition

Frozen readings (parameters fixed in `ARMS`; nothing tuned):

* **"the vote"** is production's E2 trend vote on the window's own price close (`fe_signals.e2_up`);
  `off` is that vote being off -- the framework's off-state recipe (`README.md` section 1b).
* **"hold 0.98 IEF while off"** (arms A/B) is read as a *substitution*: leg A holds production's own
  target only while the vote is on and is exactly zero while it is off, leg B holds the sleeve, so
  the total never exceeds 0.98. Stated consequence: while E2 is off but the early re-entry is
  active, production's half-weight QLD path is *replaced* by the sleeve.
* **"the residual (0.98 - w)"** (arm C) is `leg B = max(0, 0.98 - w)` while off under the same
  condition, `w` being production's own target weight on that session (its early-path weight or
  zero); leg A keeps production's target unchanged, so while the vote is off the sleeve tops the
  account up to 0.98 and while it is on the sleeve is zero.
* **the condition** is the bank's literal one: the 10-year real yield, `DGS10 - T10YIE`, in the
  percentage points FRED publishes (so "+0.25%" is `> 0.25`, "+1.00%" is `> 1.00`), evaluated on the
  session's own date with both series read `ffill=True` (FRED has no value on non-publication
  sessions; the condition sees the last known yield pair). A window in which either series has no
  value at all is `NotApplicable` -- the framework's convention for a missing input, and the
  bank's "live from 2003-01 (breakeven start)".
* **"else cash"**: the sleeve is zero *and* leg A stays zero while the vote is off, so the account
  is in cash in the states the gate rejects.
* **the instrument never switches**: leg B is the fixed registered `ief` series with a
  state-dependent weight; leg A stays the 2x panel leg. `ief` is a different instrument from leg
  A's synthetic 2x QLD*, which is what makes it a second book rather than a resize.
* **window**: `start = "auto"` over the declared inputs (`dgs10`, `t10yie`, `ief`): the window
  begins one session after the first session every one of them covers (T10YIE starts 2003-01-02, so
  the binding input is the breakeven) and K is re-simulated on that window as control
  (`_control_w20030103.json`). Windows before the breakeven's first session are `NotApplicable` --
  the bank's "2003+ -> M3/M4/M5" reduced G7.
* **execution** is production K on both books (0.5 pp band, A1u gain band, H2 December deferral, per
  leg on its own FIFO lot book, next-open fills, per-leg costs).
* **leg-B prices** are IEF's adjusted close for both that leg's fill and its close (the framework's
  convention for an extra-series leg). `ief`/`dgs10`/`t10yie` were already registered (batch_screen
  v1); no new data.
"""

from __future__ import annotations

import numpy as np

from research.batch_screen.api import NotApplicable
from research.fast_engine import fe_signals

ARMS = {
    "A": {"rule": "K", "leg": "2x", "leg_b": "ief", "cash_yield": "none", "start": "auto",
          "inputs": ["dgs10", "t10yie"], "form": "replace", "weight": 0.98, "gate": "real10y",
          "real_min_pp": 0.25},
    "B": {"rule": "K", "leg": "2x", "leg_b": "ief", "cash_yield": "none", "start": "auto",
          "inputs": ["dgs10", "t10yie"], "form": "replace", "weight": 0.98, "gate": "real10y",
          "real_min_pp": 1.00},
    "C": {"rule": "K", "leg": "2x", "leg_b": "ief", "cash_yield": "none", "start": "auto",
          "inputs": ["dgs10", "t10yie"], "form": "residual", "total": 0.98, "gate": "real10y",
          "real_min_pp": 0.25},
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

def sleeve_condition(data, spec):
    """`(P, T)` bool: the sleeve may be held on this session (the arm's own gate, if any)."""
    if spec.get("gate") is None:
        return np.ones(data.price.shape, dtype=bool)
    nominal = data.extra("dgs10", ffill=True)
    breakeven = data.extra("t10yie", ffill=True)
    for key, series in (("dgs10", nominal), ("t10yie", breakeven)):
        if not np.isfinite(series).any():
            raise NotApplicable(f"the {key} series has no value in this window")
    return (nominal - breakeven) > float(spec["real_min_pp"])

def targets(data, spec):
    wants = production_wants(data)
    off = off_vote(data)
    hold = off & sleeve_condition(data, spec)
    if spec["form"] == "residual":
        return wants, np.maximum(0.0, float(spec["total"]) - wants) * hold, spec["leg_b"]
    return wants * (~off), float(spec["weight"]) * hold, spec["leg_b"]

__all__ = ["ARMS", "off_vote", "production_wants", "sleeve_condition", "targets"]
