"""IC44 -- two-leg band widths keyed to the indexed (taxable) gain fraction (bank arms A/B/C).

Per `docs/research/idea_bank_v2.json` IC44 (`category: two-leg`, `second_symbol=QQQ`,
`kernel_scope: all_stages`, arms frozen, nothing tuned):

    A  band widths 0.5 / 3 / 8 pp at an indexed-TRY FIFO gain fraction of <=1% / <=5% / >5%
       (K with the score swapped), evaluated per leg
    B  as A plus a fourth width step: 12 pp above a 15% indexed fraction
    C  widths 0.5 / 3 / 8 pp on max(USD fraction, indexed fraction) per leg (the conservative
       combination)

Delivered through the framework's band-score options (`research/fast_engine2/fe2_opts.py`,
`research/batch_screen/README.md` §1b), which key K's A1u widths on exactly that state variable:

    fill_knobs     {"band_score": "indexed"} | {"band_score": "indexed", "band_step4_pp": 12.0}
                   | {"band_score": "max"}
    kernel_scope   "all_stages" -- the widths change the pre-tax trim schedule too, so every stage
                   carries the option, as the arm text's `kernel_scope` says

The band is already evaluated **per leg** on that leg's own lot book (the kernel's pass 2c), so the
two books each set their own width from their own FIFO head; the fourth step's 0.15 gate is fixed
in the option copy, not a second parameter.

**The two books (reading, fixed before any run).** The bank's arm text fixes only the band score;
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
invested weight of `cash = min(0.98, beta) <= 0.98`, so each book carries its own lots and its own
FIFO head for the per-leg score the arm asks for.

No extra data, no reduced window: both legs are panel legs, so every window is applicable.
"""

from __future__ import annotations

import numpy as np

from research.fast_engine import fe_signals
from research.fast_engine.fe_config import CAP

ARMS = {
    "A": {"rule": "K", "leg": "2x", "leg_b": "1x", "cash_yield": "none",
          "fill_knobs": {"band_score": "indexed"}, "kernel_scope": "all_stages"},
    "B": {"rule": "K", "leg": "2x", "leg_b": "1x", "cash_yield": "none",
          "fill_knobs": {"band_score": "indexed", "band_step4_pp": 12.0},
          "kernel_scope": "all_stages"},
    "C": {"rule": "K", "leg": "2x", "leg_b": "1x", "cash_yield": "none",
          "fill_knobs": {"band_score": "max"}, "kernel_scope": "all_stages"},
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
    """The blend's two books; the arm's own band score lives in `fill_knobs`."""
    w_qld, w_qqq = two_leg_base(data)
    return w_qld, w_qqq, spec["leg_b"]
