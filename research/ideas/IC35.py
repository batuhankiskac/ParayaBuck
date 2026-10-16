"""IC35 -- off-state in gold miners (GDX). idea_bank_v2, arms A/B/C.

    A  while off, hold 0.98 GDX
    B  while off, hold 0.49 GDX + 0.49 cash (half size)
    C  hold 0.98 GDX while off AND GDX > its own SMA200, else cash

Frozen readings (parameters fixed in `ARMS`; nothing tuned):

* **"the vote"** is production's E2 trend vote on the window's own price close (`fe_signals.e2_up`);
  `off` is that vote being off -- the framework's off-state recipe (`README.md` section 1b).
* **"hold X GDX while off"** is read as a *substitution*: leg A holds production's own target only
  while the vote is on and is exactly zero while it is off, leg B holds the sleeve, so the total
  never exceeds 0.98. Stated consequence: while E2 is off but the early re-entry is active,
  production's half-weight QLD path is *replaced* by the sleeve. Arm B's "half size" is the literal
  0.49, the rest staying in cash.
* **arm C's gate** is literal: GDX's own adjusted close above its own 200-session SMA, false while
  that average is not defined. "else cash" then means the sleeve is zero and leg A stays zero while
  the vote is off.
* **the instrument never switches**: leg B is the fixed registered `gdx` series with a
  state-dependent weight (no per-session leg selection); leg A stays the 2x panel leg.
* **window**: GDX starts 2006-05-22, so `start = "auto"` -- the window begins one session after the
  first session GDX covers and K is re-simulated on it as control (`_control_w20060523.json`).
  Windows before GDX's first session are `NotApplicable` (the framework's `leg_b_usable` rule),
  which is the bank's "2006+ -> M3/M4/M5 only" reduced G7.
* **execution** is production K on both books (0.5 pp band, A1u gain band, H2 December deferral, per
  leg on its own FIFO lot book, next-open fills, per-leg costs).
* **leg-B prices** are GDX's adjusted close for both that leg's fill and its close (the framework's
  convention for an extra-series leg). `gdx` is registered by `research/plugins_d1/build_extra.py`.
"""

from __future__ import annotations

import numpy as np

from research.batch_screen.api import NotApplicable
from research.fast_engine import fe_signals

ARMS = {
    "A": {"rule": "K", "leg": "2x", "leg_b": "gdx", "cash_yield": "none", "start": "auto",
          "inputs": [], "form": "replace", "weight": 0.98},
    "B": {"rule": "K", "leg": "2x", "leg_b": "gdx", "cash_yield": "none", "start": "auto",
          "inputs": [], "form": "replace", "weight": 0.49},
    "C": {"rule": "K", "leg": "2x", "leg_b": "gdx", "cash_yield": "none", "start": "auto",
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
    return wants * (~off), float(spec["weight"]) * hold, spec["leg_b"]

__all__ = ["ARMS", "off_vote", "production_wants", "sleeve_condition", "targets"]
