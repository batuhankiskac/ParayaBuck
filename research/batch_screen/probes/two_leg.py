"""two_leg -- engineering probe for the framework's two-leg routing (NOT a registered trial).

Three arms, all of them plugin-path smoke tests of `(wants_a, wants_b, leg_b)`:

* `zeros` -- leg A is the production target series; leg B is the `1x` panel leg with a target of
  exactly zero. Leg B therefore holds no units and, with the two-leg kernel, every number must
  equal the framework's K control *bit for bit*: the probe the framework's harness asserts.
* `split` -- the production weight split 50/50 between the `2x` and `1x` legs of the same window:
  a real two-leg arm (two lot books, one cash pool) on every stage, including the bootstrap.
* `gld_sleeve` -- leg A production, leg B a fixed 0.20 GLD sleeve (`leg_b = "gld"`, an extra-data
  series). GLD starts 2004-11-18, so the main window (and the clean bootstrap's drawn sessions)
  are `NotApplicable` -- the probe for the framework's leg-B availability rule.

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
}


def targets(data, spec):
    wants = production_wants(data)
    if spec.get("sleeve") is not None:
        sleeve = float(spec["sleeve"])
        return wants * (1.0 - sleeve), np.full_like(wants, sleeve), spec["leg_b"]
    if spec.get("share_a") is not None:
        fa = float(spec["share_a"])
        return wants * fa, wants * (1.0 - fa), spec["leg_b"]
    return wants, np.zeros_like(wants), spec["leg_b"]


__all__ = ["ARMS", "production_wants", "targets"]
