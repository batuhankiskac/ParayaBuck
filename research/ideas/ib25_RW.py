"""ib25_RW -- IB25 (off-state residual in SHY) on the reduced window the framework can declare.

The bank's IB25 arms (`docs/research/idea_bank_v1.json`; parameters frozen, nothing tuned) exist
twice already: `research/ideas/ib25.py` registers them `needs_kernel` (pre-`fast_engine2`) and
`research/ideas/ib25_2L.py` re-expresses them on the two-leg kernel, where they are recorded
`not_applicable` because SHY (2002-07-30) does not reach the framework's fixed main window. Both
files are untouched. This module adds the same two arms once more, with **new arm names** (`_RW`,
the suffix this branch uses for reduced-window arms) and the declaration that makes them
evaluable: `start = "auto"`, i.e. the window derived from the arm's own leg-B series.

    A_RW  hold 0.98 in SHY whenever the trend vote is off (production target otherwise)
    B_RW  hold the residual (0.98 - w) in SHY, re-sized only outside a 10 pp band
          (tbill_sweep S2_10's shape, different asset)

Readings (the same ones `ib25_2L` fixed and `research/two_leg_arms_v1/PREREGISTRATION.md` records):

* "the trend vote" is the production path state: off = `on_eff == False` (E2's 2-of-3 vote and the
  early re-entry both inactive); the production target `fe_signals.sized_wants` of leg A (the
  synthetic 2x QLD*) is exactly zero while the vote is off, so `w` in the arm text is that weight
  and `hi = 0.98 - w` is the residual the sleeve may occupy.
* **A_RW** = S1: sleeve target 0.98 on every off session, else 0.
* **B_RW** = S2_10's shape, transcribed from its own code
  (`archive/research/tbill-sweep-v1-2026-10:research/tbill_sweep_v1/screen.py`, verbatim from
  `ib25_2L.py`): the sleeve is kept in `[hi - X, hi]` and reset to `max(0, hi - X/2)` on a breach,
  `X = resize_band = 0.10`. That decision is taken on the *held* sleeve weight, which a
  target-weight plugin cannot see, so it is expressed on the accepted target (the same plugin-side
  band `ib32`'s C arm uses, where the framework's fill band is fixed): hold the accepted sleeve
  target while it lies inside `[hi - X, hi]`, otherwise reset it to `max(0, hi - X/2)`; while off
  the target is 0.98. This reproduces S2_10's resizes (`hi` moving by more than `X` triggers one)
  and never lets the sleeve exceed the current residual, so `wants + wants_b <= 0.98`. The rule's
  own fill band, gain band and December deferral (`rule = "K"`) then apply to each leg on its own
  lot book -- the campaign's execution layer, unchanged. No new parameter: `X` is the spec's
  `resize_band`.
* **Reduced window.** `start = "auto"` with `leg_b = "shy"` gives session 852 (2002-07-31) ->
  2026-09-29, with K re-simulated on the same window as the paired control
  (`_control_w20020731.json`), the after-tax windows clipped up, the early January rolling starts
  dropped and both bootstraps drawn from the window's own sessions -- i.e. the bank's own oracle for
  IB25 ("main_window: reduced start: 2002-08"), expressed the one way the framework allows, plus the
  `gld_offstate`-style probe window. The six OOS windows and the 21 ETFs are unchanged: SHY has no
  price there, so `leg_b_usable` refuses them (`NotApplicable`), which is the bank's own "SHY starts
  2002 -> reduced gate (main window, rolling starts, bootstraps)".
"""

from __future__ import annotations

import numpy as np

from research.fast_engine import fe_signals

ARMS = {
    "A_RW": {"rule": "K", "leg": "2x", "leg_b": "shy", "cash_yield": "none", "start": "auto",
             "sleeve": "SHY", "weight": 0.98, "key": "shy"},
    "B_RW": {"rule": "K", "leg": "2x", "leg_b": "shy", "cash_yield": "none", "start": "auto",
             "sleeve": "SHY", "residual": True, "total": 0.98, "resize_band": 0.10, "key": "shy"},
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
    acc = np.where(off[:, 0], total, np.maximum(0.0, hi[:, 0] - band / 2.0))
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
