"""two_leg -- engineering probe for the framework's two-leg routing (NOT a registered trial).

Arms, all of them plugin-path smoke tests of `(wants_a, wants_b, leg_b)`:

* `zeros` -- leg A is the production target series; leg B is the `1x` panel leg with a target of
  exactly zero. Leg B therefore holds no units and, with the two-leg kernel, every number must
  equal the framework's K control *bit for bit*: the probe the framework's harness asserts.
* `split` -- the production weight split 50/50 between the `2x` and `1x` legs of the same window:
  a real two-leg arm (two lot books, one cash pool) on every stage, including the bootstrap.
* `gld_sleeve` -- leg A production, leg B a fixed 0.20 GLD sleeve (`leg_b = "gld"`, an extra-data
  series). GLD starts 2004-11-18, so the main window (and the clean bootstrap's drawn sessions)
  are `NotApplicable` -- the probe for the framework's leg-B availability rule.
* `gld_offstate` -- the *declared-window* form of the same instrument: `start = "auto"`, leg B a
  fixed 0.98 GLD sleeve **while the trend vote is off** and zero while it is on. This is the
  `idea_bank_v2` off-state pattern (IC31-IC37: "while the trend vote is off, hold 0.98 in
  LQD/HYG/TIP/XLU|XLP/GDX/IEF") written as a *fixed* leg B with a state-dependent weight: the
  instrument never changes, only its weight does, so no per-session instrument switch is needed.
  The declared window is what makes it evaluable -- the framework refuses a leg-B series that does
  not reach every session the engine reads, so the arm runs from 2004-11-19 (`arm_window`'s
  `"auto"` rule) with K re-simulated on the same window as control. The probe's economics are
  deliberately naive (while the vote is off the whole sleeve is GLD and leg A holds nothing, so
  the early half-weight path is not held in those states); the contract it demonstrates is the
  mechanism.
* `onex_offstate` -- the same rule with `leg_b = "1x"`, the window's own index at gearing 1
  (IC38): a *panel* leg has the window's full history, so this arm needs no reduced window and is
  the control's window with a second book.

Lives in `probes/`, outside `research/ideas/`, so it is never registered as a trial (see
`README.md` section 5).
"""

from __future__ import annotations

import numpy as np

from research.fast_engine import fe_signals


def production_wants(data):
    """The production R2/E2 target series of the panel (`k_control`'s targets)."""
    e2 = fe_signals.e2_up(data.price)
    comps = fe_signals.early_components(data.price)
    on_eff, factor = fe_signals.r2_state(data.price, e2, comps)
    return fe_signals.sized_wants(on_eff, factor, data.vol)


ARMS = {
    "zeros": {"rule": "K", "leg": "2x", "leg_b": "1x", "cash_yield": "none"},
    "split": {"rule": "K", "leg": "2x", "leg_b": "1x", "cash_yield": "none", "share_a": 0.5},
    "gld_sleeve": {"rule": "K", "leg": "2x", "leg_b": "gld", "cash_yield": "none",
                   "sleeve": 0.20},
    "gld_offstate": {"rule": "K", "leg": "2x", "leg_b": "gld", "cash_yield": "none",
                     "start": "auto", "off_weight": 0.98},
    # the same mechanism on a *panel* leg (IC38: "while off, hold 0.98 in the 1x index"): the 1x
    # leg has the window's own history, so no reduced window is needed
    "onex_offstate": {"rule": "K", "leg": "2x", "leg_b": "1x", "cash_yield": "none",
                      "off_weight": 0.98},
}


def targets(data, spec):
    if spec.get("off_weight") is not None:
        # the off-state pattern: the sleeve is held only while the trend vote is off, and leg A
        # only while it is on (so the two never overlap and the arm is always at most 0.98 invested)
        off = 1.0 - fe_signals.e2_up(data.price)
        on = 1.0 - off
        return (production_wants(data) * on,
                np.full_like(off, float(spec["off_weight"])) * off, spec["leg_b"])
    wants = production_wants(data)
    if spec.get("sleeve") is not None:
        sleeve = float(spec["sleeve"])
        return wants * (1.0 - sleeve), np.full_like(wants, sleeve), spec["leg_b"]
    if spec.get("share_a") is not None:
        fa = float(spec["share_a"])
        return wants * fa, wants * (1.0 - fa), spec["leg_b"]
    return wants, np.zeros_like(wants), spec["leg_b"]


__all__ = ["ARMS", "production_wants", "targets"]
