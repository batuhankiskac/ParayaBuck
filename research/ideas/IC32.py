"""IC32 -- off-state in high-yield credit (HYG). idea_bank_v2, arms A/B/C.

    A  while off, hold 0.98 HYG
    B  hold 0.98 HYG while off AND HYG > its own SMA200, else cash
    C  hold 0.98 HYG while off AND the momentum leg (TSM252 of the arm's own close) > 0, else cash

Frozen readings (nothing is tuned here; the parameters are fixed in `ARMS`):

* **"the vote"** is production's E2 trend vote on the window's own price close (`fe_signals.e2_up`);
  `off` is that vote being off -- the framework's off-state recipe (`README.md` section 1b,
  `probes/two_leg.py::gld_offstate`).
* **"hold 0.98 HYG while off; production target otherwise"** is read as a *substitution*: leg A is
  production's own target while the vote is on and exactly zero while it is off, leg B is the
  sleeve, so the total never exceeds 0.98. Stated consequence: in the states where E2 is off but
  the early re-entry is active, production's half-weight QLD path is *replaced* by the sleeve.
* **arm B's gate** is literal: HYG's own adjusted close above its own 200-session SMA, false while
  that average is not defined. "else cash" then means the sleeve is zero and leg A stays zero while
  the vote is off.
* **arm C's gate** is production's own momentum vote: `TSM252` of the arm's own close, i.e. the
  252-session price return of the panel's price series (`fe_signals.tsm_up_price(data.price, 252)`)
  positive -- the same series and lookback production's 2-of-3 vote uses, and the bank's
  "the momentum leg still positive". It is *not* computed on HYG (that is arm B's gate); the bank's
  `signal_inputs` lists "the arm's own instrument close" separately from the HYG download.
* **the instrument never switches**: leg B is the fixed registered `hyg` series with a
  state-dependent weight (no per-session leg selection); leg A stays the 2x panel leg.
* **window**: HYG starts 2007-04-11, so `start = "auto"` -- the window begins one session after the
  first session HYG covers and K is re-simulated on it as control (`_control_w20070412.json`).
  Windows before HYG's first session are `NotApplicable` (the framework's `leg_b_usable`), which is
  the bank's "2007+ -> M3/M4/M5 only" reduced G7.
* **execution** is production K on both books (0.5 pp band, A1u gain band, H2 December deferral, per
  leg on its own FIFO lot book, next-open fills, per-leg costs).
* **leg-B prices** are HYG's adjusted close for both that leg's fill and its close (the framework's
  convention for an extra-series leg). `hyg` was already registered (plugins_c2); no new data.
"""

from __future__ import annotations

import numpy as np

from research.batch_screen.api import NotApplicable
from research.fast_engine import fe_signals

#: the momentum lookback of production's own TSM vote (bank: "TSM252")
TSM_N = 252

ARMS = {
    "A": {"rule": "K", "leg": "2x", "leg_b": "hyg", "cash_yield": "none", "start": "auto",
          "inputs": [], "form": "replace", "weight": 0.98},
    "B": {"rule": "K", "leg": "2x", "leg_b": "hyg", "cash_yield": "none", "start": "auto",
          "inputs": [], "form": "replace", "weight": 0.98, "gate": "own_sma", "sma_n": 200},
    "C": {"rule": "K", "leg": "2x", "leg_b": "hyg", "cash_yield": "none", "start": "auto",
          "inputs": [], "form": "replace", "weight": 0.98, "gate": "tsm", "tsm_n": TSM_N},
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
    gate = spec.get("gate")
    if gate is None:
        return np.ones(data.price.shape, dtype=bool)
    if gate == "tsm":
        return fe_signals.tsm_up_price(data.price, int(spec["tsm_n"]))
    series = data.extra(spec["leg_b"], ffill=True)
    if not np.isfinite(series).any():
        raise NotApplicable(f"the {spec['leg_b']} series has no value in this window")
    return fe_signals.sma_up(series, int(spec["sma_n"]))

def targets(data, spec):
    wants = production_wants(data)
    off = off_vote(data)
    hold = off & sleeve_condition(data, spec)
    return wants * (~off), float(spec["weight"]) * hold, spec["leg_b"]

__all__ = ["ARMS", "off_vote", "production_wants", "sleeve_condition", "targets"]
