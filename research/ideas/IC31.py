"""IC31 -- off-state in investment-grade credit (LQD). idea_bank_v2, arms A/B/C.

    A  while the vote is off, hold 0.98 in LQD; production target otherwise
    B  while the vote is off, hold the residual (0.98 - w) in LQD (paired with production's cash
       on the rest)
    C  hold 0.98 LQD while off AND LQD > its own SMA200, else cash

Frozen readings (every parameter is fixed here and in `ARMS`; nothing is tuned):

* **"the vote"** is production's E2 trend vote -- at least 2 of 3 on the window's own price close
  (`fe_signals.e2_up`): close > SMA200, 252-session price return > 0, close > the midpoint of the
  126-session channel. `off` is that vote being off. This is the framework's off-state recipe
  (`README.md` section 1b, fixture `probes/two_leg.py::gld_offstate`), applied to the instrument
  this idea names.
* **"hold 0.98 in LQD while off; production target otherwise"** (arm A, arm C) is read as a
  *substitution*: leg A holds production's own target only while the vote is on (while off leg A
  is exactly zero) and leg B holds the sleeve, so the account's total never exceeds 0.98. The
  literal consequence, stated because it matters: in the states where E2 is off but the early
  re-entry is active, production's half-weight QLD path is replaced by the sleeve, not kept
  alongside it.
* **"the residual (0.98 - w)"** (arm B) is read as `leg B = max(0, 0.98 - w)` while off, where `w`
  is production's own target weight on that session (its early-path weight or zero). Leg A keeps
  production's target unchanged -- that is "paired with production's cash on the rest" -- so while
  the vote is off the sleeve tops the account up to 0.98 and while it is on the sleeve is zero.
  No resize band: the arm text names none (IB25's sleeve had one; this one does not).
* **arm C's gate** is the bank's literal condition: LQD's own adjusted close above its own
  200-session simple moving average, i.e. `close > SMA200`, false while the average is not defined
  (fewer than 200 sessions). "else cash" then means the sleeve is zero *and* leg A stays zero while
  the vote is off, so the account is in cash.
* **the instrument never switches**: leg B is the fixed registered `lqd` series with a
  state-dependent weight, which is what the two-leg kernel holds (no per-session leg selection).
* **window**: LQD starts 2002-07-30, so the arm declares `start = "auto"`: the window begins one
  session after the first session LQD covers (the engine reads the previous close at `i0 - 1`),
  and K is re-simulated on the same window as control (`_control_w20020731.json`). Windows before
  LQD's first session are `NotApplicable` (the framework's `leg_b_usable` rule), so the 2000-2002
  bear market is not covered -- the bank states the same handicap.
* **execution** is production K on both books: 0.5 pp resize band, the A1u gain band and the H2
  December deferral on each leg's own FIFO lot book, next-open fills, per-leg costs.
* **leg-B prices** are the series' adjusted close, used for both that leg's fill and its close
  (the framework's stated convention for an extra-series leg). No new series: `lqd` is registered
  by `research/plugins_d1/build_extra.py`.
"""

from __future__ import annotations

import numpy as np

from research.batch_screen.api import NotApplicable
from research.fast_engine import fe_signals

ARMS = {
    "A": {"rule": "K", "leg": "2x", "leg_b": "lqd", "cash_yield": "none", "start": "auto",
          "inputs": [], "form": "replace", "weight": 0.98},
    "B": {"rule": "K", "leg": "2x", "leg_b": "lqd", "cash_yield": "none", "start": "auto",
          "inputs": [], "form": "residual", "total": 0.98},
    "C": {"rule": "K", "leg": "2x", "leg_b": "lqd", "cash_yield": "none", "start": "auto",
          "inputs": [], "form": "replace", "weight": 0.98, "gate": "own_sma", "sma_n": 200},
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
    series = data.extra(spec["leg_b"], ffill=True)
    if not np.isfinite(series).any():
        raise NotApplicable(f"the {spec['leg_b']} series has no value in this window")
    return fe_signals.sma_up(series, int(spec["sma_n"]))

def targets(data, spec):
    wants = production_wants(data)
    off = off_vote(data)
    hold = off & sleeve_condition(data, spec)
    if spec["form"] == "residual":
        return wants, np.maximum(0.0, float(spec["total"]) - wants) * hold, spec["leg_b"]
    return wants * (~off), float(spec["weight"]) * hold, spec["leg_b"]

__all__ = ["ARMS", "off_vote", "production_wants", "sleeve_condition", "targets"]
