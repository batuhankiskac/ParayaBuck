"""ib27_2L -- IB27 (beta-matched QQQ + QLD blend), re-expressed on the two-leg kernel.

The bank's IB27 arms (`docs/research/idea_bank_v1.json`; parameters frozen, nothing tuned) were
registered as `ib27/A`,`ib27/B`,`ib27/C` with `needs_kernel` before `research/fast_engine2`
existed. Those arms stay in `research/ideas/ib27.py` untouched; this module adds the same three
arms with the two-leg kernel's declaration (leg A = the panel's synthetic 2x QLD*, leg B = the
panel's `1x`, i.e. the same underlying fully invested with no fund expense) and the suffix `_2L`:

    A_2L  blend in the on-state only (f = 1); the early leg stays production half-weight QLD
    B_2L  blend everywhere, including the early leg (f = 0.5), which makes the early leg unlevered QQQ
    C_2L  blend only when vt >= 0.60; production QLD otherwise

Readings fixed before the run (see `research/two_leg_arms_v1/PREREGISTRATION.md`):

* `B = 2 * min(0.98, f * vt)`, `C = min(0.98, B)`, `w_QLD = B - C` (leg A), `w_QQQ = C - w_QLD`
  (leg B). `vt = min(1, 0.35/vol20)` is the production sizing input on leg A's own vol
  (`data.vol`), and `f` is the production path factor (1 on E2, 0.5 on the early re-entry, 0
  otherwise) from `fe_signals.r2_state` -- the spec's `f` (1.0 / 0.5) names the arm's path factor,
  so the implementation reads it per session rather than from the spec.
* **Long-only tie-break (pre-registered).** The bank's `B = 2*f*vt` and `C = min(0.98, B)` are
  exactly the formula above while `f*vt <= 0.98`; beyond it, `w_QQQ = 1.96 - B` turns negative
  (a QQQ *short*, at most -0.04), which the kernel's single-cash-pool, long-only account does not
  model. The arm therefore caps the delivered beta at production's own cap (`min(0.98, f*vt)`,
  i.e. `B <= 1.96`), where the blend degenerates to production's `0.98` QLD. The bank's own
  parenthetical ("w_QQQ = 0 and w_QLD = C when C = B") contradicts its formula (`w_QLD = B - C`);
  the formula governs, and at `C = B` it gives the all-QQQ, minimum-carry leg the mechanism prose
  argues for.
* **Disclosed approximation.** The bank's pre-tax sketch priced the blend as one synthetic fund.
  Here both legs are real kernel legs with their own FIFO lot books, so the after-tax cells (the
  ones the study is judged on) are exact for the construction, not approximated. The `1x` leg
  carries the campaign's synthetic conventions: no fund expense and no financing (the bank's
  "QQQ ~20 bp/yr" is therefore not charged -- a disclosed, small bias in the arm's favour).
* The legs' weights are static functions of the production path state and `vt`, so every window
  with a leg-A vol series is applicable (no extra data).
"""

from __future__ import annotations

import numpy as np

from research.fast_engine import fe_signals
from research.fast_engine.fe_config import CAP

ARMS = {
    "A_2L": {"rule": "K", "leg": "2x", "leg_b": "1x", "cash_yield": "none",
             "blend": "on_state_only", "f": 1.0},
    "B_2L": {"rule": "K", "leg": "2x", "leg_b": "1x", "cash_yield": "none",
             "blend": "everywhere", "f": 0.5},
    "C_2L": {"rule": "K", "leg": "2x", "leg_b": "1x", "cash_yield": "none",
             "blend": "vt_ge_0.60", "vt_min": 0.60},
}

def blend(f, vt):
    """`(w_QLD, w_QQQ)` of the beta-matched construction for a path factor `f` and a vt panel."""
    beta = 2.0 * np.minimum(CAP, f * vt)          # long-only: production's delivered beta, capped
    cash = np.minimum(CAP, beta)
    w_qld = beta - cash
    return w_qld, cash - w_qld

def targets(data, spec):
    e2 = fe_signals.e2_up(data.price)
    comps = fe_signals.early_components(data.price)
    on_eff, factor = fe_signals.r2_state(data.price, e2, comps)
    wants = fe_signals.sized_wants(on_eff, factor, data.vol)
    vt = fe_signals.vt_of(data.vol)
    # the blend's path factor is the production one on every session it is applied to (the on-state
    # mask implies f = 1, so arms A and B differ only in *where* the blend is used, not in how);
    # arm C's mask is on vt, and on an early session it is the early factor that enters B = 2*f*vt.
    w_qld, w_qqq = blend(factor, vt)
    if spec["blend"] == "everywhere":
        mask = np.ones_like(factor, dtype=bool)
    elif spec["blend"] == "on_state_only":
        mask = factor == 1.0
    elif spec["blend"] == "vt_ge_0.60":
        mask = vt >= float(spec["vt_min"])
    else:
        raise ValueError(f"unknown blend {spec['blend']!r}")
    return np.where(mask, w_qld, wants), np.where(mask, w_qqq, 0.0), "1x"

__all__ = ["ARMS", "blend", "targets"]
