"""ib25_2L -- IB25 (off-state residual in SHY), re-expressed on the two-leg kernel.

The bank's IB25 arms (`docs/research/idea_bank_v1.json`; parameters frozen, nothing tuned) were
registered as `ib25/A`,`ib25/B` with `needs_kernel` before `research/fast_engine2` existed. Those
arms stay in `research/ideas/ib25.py` untouched; this module adds the same two arms with the two-leg
kernel's declaration (`leg_b = "shy"`, the registered Yahoo SHY adjclose series) and the suffix
`_2L`:

    A_2L  hold 0.98 in SHY whenever the trend vote is off (production target otherwise)
    B_2L  hold the residual (0.98 - w) in SHY, re-sized only outside a 10 pp band
          (tbill_sweep S2_10's shape, different asset)

Readings fixed before the run (see `research/two_leg_arms_v1/PREREGISTRATION.md`):

* "the trend vote" is the production path state: off = `on_eff == False` (E2's 2-of-3 vote and
  the early re-entry both inactive); the production target is `fe_signals.sized_wants` of leg A
  (the synthetic 2x QLD*), which is exactly zero while the vote is off. `w` in the arm text is
  that production weight, so `hi = 0.98 - w` is the residual the sleeve may occupy.
* **A_2L** = S1: sleeve target 0.98 on every off session, else 0.
* **B_2L** = S2_10's shape, transcribed from its own code
  (`archive/research/tbill-sweep-v1-2026-10:research/tbill_sweep_v1/screen.py`): the sleeve is kept
  in `[hi - X, hi]` and reset to `max(0, hi - X/2)` on a breach, with `X = resize_band = 0.10`.
  That decision is taken on the *held* sleeve weight, which a target-weight plugin cannot see, so
  it is expressed on the accepted target (the same plugin-side band `ib32`'s C arm uses, where the
  framework's fill band is fixed): hold the accepted sleeve target while it lies inside
  `[hi - X, hi]`, otherwise reset it to `max(0, hi - X/2)`; while off the target is `0.98`. This
  reproduces S2_10's resizes (`hi` moving by more than `X` triggers one) and, unlike a symmetric
  dead-band on `hi`, never lets the sleeve exceed the current residual, so the arm's two targets
  stay within the bank's "total target is never above 0.98". No new parameter: `X` is the spec's
  `resize_band`. The rule's own fill band, gain band and December deferral (`rule = "K"`) then
  apply to each leg on its own lot book -- the campaign's execution layer, unchanged.
* **Availability.** SHY (Yahoo, adjusted close) starts 2002-07-30, so both arms are
  `NotApplicable` on the framework's fixed main window (2000-03-09 ->): `leg_b_usable` refuses a
  leg whose series does not reach the window. The bank's own oracle for IB25 is a *reduced* main
  window starting 2002-08 (its `data.main_window` field), which the framework cannot express
  (stage 1 hardcodes the main window). The arms are registered and screened anyway, so the
  outcome is recorded rather than assumed; the pre-registered reduced gate is in the study's
  PREREGISTRATION.md.
"""

from __future__ import annotations

import numpy as np

from research.fast_engine import fe_signals

ARMS = {
    "A_2L": {"rule": "K", "leg": "2x", "leg_b": "shy", "cash_yield": "none", "sleeve": "SHY",
             "weight": 0.98, "key": "shy"},
    "B_2L": {"rule": "K", "leg": "2x", "leg_b": "shy", "cash_yield": "none", "sleeve": "SHY",
             "residual": True, "total": 0.98, "resize_band": 0.10, "key": "shy"},
}

def production_state(data):
    """`(wants, on_eff, factor)` of the production E2 + early-re-entry path on this panel."""
    e2 = fe_signals.e2_up(data.price)
    comps = fe_signals.early_components(data.price)
    on_eff, factor = fe_signals.r2_state(data.price, e2, comps)
    return fe_signals.sized_wants(on_eff, factor, data.vol), on_eff, factor

def s2_sleeve(hi, off, total, band):
    """S2_10's sleeve target: hold while it lies in `[hi - band, hi]`, else `max(0, hi - band/2)`.

    `hi`, `off` are `(P, T)`; one pass over sessions, vectorised over paths (the recursion shape
    `fe_signals.r2_state` and `ib32.sticky_*` use). While `off` the target is `total`.
    """
    P, T = hi.shape
    out = np.empty((P, T), dtype=np.float64)
    fresh = np.where(off[:, 0], total, np.maximum(0.0, hi[:, 0] - band / 2.0))
    acc = fresh.copy()
    out[:, 0] = acc
    for t in range(1, T):
        h, is_off = hi[:, t], off[:, t]
        inside = (h - acc >= 0.0) & (h - acc <= band)
        fresh = np.where(is_off, total, np.maximum(0.0, h - band / 2.0))
        acc = np.where(inside & ~is_off, acc, fresh)
        out[:, t] = acc
    return out

def targets(data, spec):
    wants, on_eff, _factor = production_state(data)
    off = ~on_eff
    total = float(spec.get("weight", spec.get("total")))
    if not spec.get("residual"):
        return wants, total * off, spec["key"]
    sleeve = s2_sleeve(total - wants, off, total, float(spec["resize_band"]))
    return wants, sleeve, spec["key"]

__all__ = ["ARMS", "production_state", "s2_sleeve", "targets"]
