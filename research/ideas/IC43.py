"""IC43 -- two-leg loss-netting route (bank arms A/B/C).

Per `docs/research/idea_bank_v2.json` IC43 (`category: two-leg`, `second_symbol=QQQ`,
`kernel_scope: after_tax_stage_only`, arms frozen, nothing tuned):

    A  on a band-permitted downsize, additionally sell the loss leg for up to 25% of the
       downsize's realised TRY gain (only if a loss lot exists)
    B  the same with a 50% cap
    C  the same with a 25% cap, but only while the year-to-date realised TRY gain exceeds
       100,000 TRY

Delivered through the framework's loss-netting option (`research/fast_engine2/fe2_opts.py`,
`research/batch_screen/README.md` §1b): after a leg's downsize fill realises a **positive TRY gain
in a taxed mode**, the other leg's FIFO loss lots are sold (FIFO, indexed per unit) up to
`loss_net_frac` x that gain, gated on the year-to-date realised TRY gain exceeding
`loss_net_min_ytd_try` (0 = no gate):

    fill_knobs     {"loss_net_frac": 0.25 | 0.50 | 0.25, "loss_net_min_ytd_try": 0 | 100000}
    kernel_scope   "after_tax_stage_only" -- the pre-tax stages run the published kernel (the
                   netting only exists in a taxed mode anyway), as the arm text says

The netting sale walks the other leg's FIFO book and consumes it as one sale, so a profitable lot
in front of a loss lot is consumed by that sale rather than skipped (the kernel's documented
convention); it is reason 3 (`R_NET`), a reason that exists only in the option copy.

**The two books (reading, fixed before any run).** The bank's arm text fixes only the fill decision;
its own routing idea IB28 is defined "with QQQ and QLD both held (IB27)" and IC41's `closest_tested`
records IB28's mechanism as "defined in the bank only for QQQ+QLD". The account therefore holds
IB27's beta-matched **QQQ + QLD** blend -- leg A = the panel's synthetic 2x QLD*, leg B = the
panel's `1x` (the same index at gearing 1, the campaign's conventions, no fund expense) -- in
IB27's arm-B form, applied on every session:

    beta = 2 * min(0.98, f * vt),   cash = min(0.98, beta),
    w_QLD = beta - cash  (leg A),   w_QQQ = cash - w_QLD  (leg B)

with `vt = min(1, 0.35/vol20)` on leg A's own vol (`data.vol`) and `f` the production path factor
of `fe_signals.r2_state` (1 on E2, 0.5 on the early re-entry, 0 otherwise, so the early leg is
unlevered QQQ*). The books deliver one index exposure (`2*w_QLD + w_QQQ = beta`) at a total
invested weight of `cash = min(0.98, beta) <= 0.98`, so both books carry lots and the netting
route has a second book to sell from, without leaving the account's own cap.

No extra data, no reduced window: both legs are panel legs, so every window is applicable. The one
taxpayer pools both legs' realisations into one annual net TRY gain, which is what makes the
same-session pairing usable under the no-carry-forward rule.
"""

from __future__ import annotations

import numpy as np

from research.fast_engine import fe_signals
from research.fast_engine.fe_config import CAP

ARMS = {
    "A": {"rule": "K", "leg": "2x", "leg_b": "1x", "cash_yield": "none",
          "fill_knobs": {"loss_net_frac": 0.25}, "kernel_scope": "after_tax_stage_only"},
    "B": {"rule": "K", "leg": "2x", "leg_b": "1x", "cash_yield": "none",
          "fill_knobs": {"loss_net_frac": 0.50}, "kernel_scope": "after_tax_stage_only"},
    "C": {"rule": "K", "leg": "2x", "leg_b": "1x", "cash_yield": "none",
          "fill_knobs": {"loss_net_frac": 0.25, "loss_net_min_ytd_try": 100000.0},
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
    """The blend's two books; the arm's own loss-netting route lives in `fill_knobs`."""
    w_qld, w_qqq = two_leg_base(data)
    return w_qld, w_qqq, spec["leg_b"]
