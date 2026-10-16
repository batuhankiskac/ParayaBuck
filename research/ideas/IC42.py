"""IC42 -- two-leg routing on the TRY-indexed (taxable) gain, not the USD gain (bank arms A/B/C).

Per `docs/research/idea_bank_v2.json` IC42 (`category: two-leg`, `second_symbol=QQQ`,
`kernel_scope: after_tax_stage_only`, arms frozen, nothing tuned):

    A  route downsizes to the leg with the smaller TRY-indexed FIFO gain fraction (route cap
       10 pp); band widths = K's A1u on the USD fraction
    B  as A with a route cap of 5 pp
    C  as A, and the band widths are themselves keyed to the indexed-TRY fraction
       (0.5 / 3 / 8 pp at <=1% / <=5% / >5%)

Delivered through the framework's two-leg fill options (`research/fast_engine2/fe2_opts.py`,
`research/batch_screen/README.md` §1b), which implement exactly these choices on the kernel's own
semantics -- the routed quantity is what the band test and the fill use, the router fires on a
session where both legs downsize and moves units from the leg with the larger TRY-indexed fraction
to the one with the smaller, bounded by the cap, by the other leg's own sale and by the cheap
leg's remaining units:

    fill_knobs     {"band_score": "usd", "route_cap_pp": 10.0 | 5.0} / {"band_score": "indexed",
                    "route_cap_pp": 10.0}
    kernel_scope   "after_tax_stage_only" -- every pre-tax stage runs the published kernel and only
                   the taxed stages carry the options, as the arm text's `kernel_scope` says

**The two books (reading, fixed before any run).** The bank's arm text fixes the fill decision and
no weight split; its own routing idea IB28 is defined "with QQQ and QLD both held (IB27)" and
IC41's `closest_tested` records IB28's mechanism as "defined in the bank only for QQQ+QLD". The
account therefore holds IB27's beta-matched **QQQ + QLD** blend -- leg A = the panel's synthetic 2x
QLD*, leg B = the panel's `1x` (the same index at gearing 1, the campaign's conventions, no fund
expense) -- in IB27's arm-B form, applied on every session:

    beta = 2 * min(0.98, f * vt),   cash = min(0.98, beta),
    w_QLD = beta - cash  (leg A),   w_QQQ = cash - w_QLD  (leg B)

with `vt = min(1, 0.35/vol20)` on leg A's own vol (`data.vol`) and `f` the production path factor
of `fe_signals.r2_state` (1 on E2, 0.5 on the early re-entry, 0 otherwise, so the early leg is
unlevered QQQ*). The two books deliver one index exposure (`2*w_QLD + w_QQQ = beta`) and their
total invested weight is `cash = min(0.98, beta) <= 0.98`, so the split stays inside the account's
own cap and routing between the books is (very nearly) a lot-selection rule, which is what the
bank's pre-tax note assumes ("pre-tax, the routing is a pure lot-selection rule ... and changes
nothing; its content is the after-tax stage - state that in the pre-registration (a reduced
pre-tax gate: non-inferiority only)").

No extra data, no reduced window: both legs are panel legs, so every window is applicable. The
second book keeps its own FIFO lot book and the one taxpayer pools both legs' realisations.
"""

from __future__ import annotations

import numpy as np

from research.fast_engine import fe_signals
from research.fast_engine.fe_config import CAP

ARMS = {
    "A": {"rule": "K", "leg": "2x", "leg_b": "1x", "cash_yield": "none",
          "fill_knobs": {"band_score": "usd", "route_cap_pp": 10.0},
          "kernel_scope": "after_tax_stage_only"},
    "B": {"rule": "K", "leg": "2x", "leg_b": "1x", "cash_yield": "none",
          "fill_knobs": {"band_score": "usd", "route_cap_pp": 5.0},
          "kernel_scope": "after_tax_stage_only"},
    "C": {"rule": "K", "leg": "2x", "leg_b": "1x", "cash_yield": "none",
          "fill_knobs": {"band_score": "indexed", "route_cap_pp": 10.0},
          "kernel_scope": "after_tax_stage_only"},
}

def two_leg_base(data):
    """IB27's beta-matched QQQ + QLD books `(w_qld, w_qqq)` on the production path (arm B)."""
    e2 = fe_signals.e2_up(data.price)
    comps = fe_signals.early_components(data.price)
    _on, factor = fe_signals.r2_state(data.price, e2, comps)
    vt = fe_signals.vt_of(data.vol)
    beta = 2.0 * np.minimum(CAP, factor * vt)
    cash = np.minimum(CAP, beta)
    w_qld = beta - cash
    return w_qld, cash - w_qld

def targets(data, spec):
    """The blend's two books; the arm's own routed fill decision lives in `fill_knobs`."""
    w_qld, w_qqq = two_leg_base(data)
    return w_qld, w_qqq, spec["leg_b"]
