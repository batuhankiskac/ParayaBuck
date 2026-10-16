"""IC38 -- off-state in 1x QQQ (never fully out). idea_bank_v2, arms A/B/C.

    A  while off, hold 0.98 in the 1x index (unlevered); production target otherwise
    B  while off, hold 0.49 in the 1x index
    C  while off, hold min(1, 0.35/vol20_of_the_1x_leg) in the 1x index (a vol-targeted off-state)

Frozen readings (parameters fixed in `ARMS`; nothing tuned):

* **leg B is the window's own `1x` panel leg** -- the framework's second leg, "the same underlying
  at gearing 1 with no cost" (`api.LEGS`), built from the window's own price/div triple. So this
  idea needs **no new series and no reduced window** (top-level `spec["start"]` is deliberately
  absent: a panel leg has the window's full history, `README.md` section 1a). Both books are the
  same underlying at different gearings, which is what the arm's "never fully out" means -- no
  per-session instrument switch is involved, only the two legs' weights.
* **"the vote"** is production's E2 trend vote on the window's own price close (`fe_signals.e2_up`);
  `off` is that vote being off -- the framework's off-state recipe (`README.md` section 1b).
* **"hold X in the 1x index while off; production target otherwise"** is read as a *substitution*:
  leg A holds production's own target only while the vote is on and is exactly zero while it is off,
  leg B holds the 1x position, so the total never exceeds 0.98. Stated consequence: while E2 is off
  but the early re-entry is active, production's half-weight *2x* path is replaced by the 1x
  position (arm A holds 0.98 of it, arm B 0.49), and in the deep-bear states where the early gates
  fail the account is still invested at 1x rather than flat -- the bank's own statement of the arm.
* **arm C's weight** is production's own sizing formula `min(1, 0.35/vol20)` (the `vt` of
  `fe_signals.vt_of`, `VOL_TARGET = 0.35`) computed on the **1x leg's own** reconstructed close
  (`data.legs["1x"][1]`, as the arm text says: "vol20_of_the_1x_leg"), i.e. the underlying's own
  vol20 rather than the 2x leg's `data.vol`. The bank writes the outer cap as `1` (no leverage);
  the frozen reading keeps the campaign's published total cap instead -- `min(0.98,
  0.35/vol20_1x)` (`fe_config.CAP`, the same 0.98 every other arm and the production target obey) --
  because a weight of exactly 1.0 leaves the account no cash for costs and the advance-tax funding
  sale the after-tax stages model, and `research/plugins_d1/README.md` records this amendment. The
  practical effect: 0.98 whenever the underlying's vol20 is below 35.7%, less than that above it.
  Volatility warm-up (fewer than 20 sessions) and a zero vol take the production convention `vt = 1`
  (`fe_signals.vt_of`), so the cap 0.98 applies.
* **execution** is production K on both books (0.5 pp band, A1u gain band, H2 December deferral, per
  leg on its own FIFO lot book, next-open fills, per-leg costs), and both legs carry the account's
  own cost/financing handling -- on the 1x leg only the round-trip cost applies (no gearing, no
  financing inside the leg).
* **coverage**: all six OOS windows are evaluable (the 1x leg is each window's own index), the
  bank's stated advantage of this idea.
"""

from __future__ import annotations

import numpy as np

from research.fast_engine import fe_signals
from research.fast_engine.fe_config import CAP

ARMS = {
    "A": {"rule": "K", "leg": "2x", "leg_b": "1x", "cash_yield": "none",
          "form": "replace", "weight": 0.98},
    "B": {"rule": "K", "leg": "2x", "leg_b": "1x", "cash_yield": "none",
          "form": "replace", "weight": 0.49},
    "C": {"rule": "K", "leg": "2x", "leg_b": "1x", "cash_yield": "none",
          "form": "replace", "weight_from_vt": True, "vol_n": 20, "vol_target": 0.35,
          "cap": CAP, "leg_b_key": "1x"},
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

def vol_target_weight(data, spec):
    """`(P, T)` `min(cap, 0.35/vol20)` of the **1x leg's own** close (arm C's weight)."""
    vol = fe_signals.vol_n(data.legs[spec["leg_b_key"]][1], int(spec["vol_n"]))
    target = float(spec["vol_target"])
    vt = np.where(vol > 0, np.minimum(1.0, target / np.where(vol > 0, vol, 1.0)), 1.0)
    return np.minimum(float(spec["cap"]), vt)

def targets(data, spec):
    wants = production_wants(data)
    off = off_vote(data)
    weight = (vol_target_weight(data, spec) if spec.get("weight_from_vt")
              else np.full_like(wants, float(spec["weight"])))
    return wants * (~off), weight * off, spec["leg_b"]

__all__ = ["ARMS", "off_vote", "production_wants", "targets", "vol_target_weight"]
