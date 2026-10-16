"""IC46 -- permanent duration sleeve (QLD + IEF, risk-parity-lite) as a two-leg arm (arms A/B/C).

Per `docs/research/idea_bank_v2.json` IC46 (`category: two-leg`, `second_symbol=IEF`,
`main_window: partial: IEF starts 2002-07`, arms frozen, nothing tuned):

    A  0.88 x production target in the QLD book + 0.10 in IEF, rebalanced only outside a 2 pp band
    B  0.78 x production target + 0.20 IEF, same band
    C  0.90 x production target + an IEF sleeve sized 0.08 x clip(vol20(QLD)/vol20(IEF), 0.5, 3.0)

**A and B are screened** as two-leg arms (`research/fast_engine2.fe2_sim.simulate2`): leg A = the
panel's synthetic 2x QLD* held at `equity x fe_signals.sized_wants(...)` of the production path,
leg B = the registered Yahoo IEF adjusted close (the key `data.extra("ief")` / `leg_b = "ief"`
resolves to) at the arm's fixed sleeve weight. One cash pool, a FIFO lot book per leg, one taxpayer,
and K's 0.5 pp band / A1u gain band / H2 December deferral applied per leg on that leg's own book.
The equity is scaled by 0.88 / 0.78 exactly as the arm text says, so the two targets sum to at most
0.98 (0.88 x 0.98 + 0.10 = 0.962; 0.78 x 0.98 + 0.20 = 0.964): the sleeve is funded by the lower
equity target and the account never borrows.

**Reduced window (`start = "auto"`).** IEF starts 2002-07-30, so the arm declares the window the
framework derives from its own leg-B series: session 852 (2002-07-31) -> 2026-09-29, with K
re-simulated on the same window as the paired control (`_control_w20020731.json`), the three
after-tax windows clipped up to it, the January rolling starts that begin before it dropped and both
block bootstraps drawn from the window's own sessions. The six OOS windows and the 21 ETFs are other
markets and are unchanged -- IEF has no price on the pre-2002 windows and an interior gap on the
1999-2026 ETF windows, so `leg_b_usable` refuses them (`NotApplicable`), the bank's own "IEF 2002+
-> M3/M4/M5 only; reduced 3-window G7" note.

**The 2 pp rebalance band (documented deviation, fixed before any run).** The arm's realisation
schedule is the idea ("a permanent sleeve is touched only by a 2 pp rebalance band, so its
realisations are rare" against `r2_tbill_combo_v1`'s state-keyed sleeve). That band is a band on the
*held* sleeve weight; the kernel's resize trigger is the engine's fixed 0.5 pp drift
(`fe_sim.C0_BAND`; the rule table's three booleans carry no width) with K's A1u gain band on top,
which already blocks a profitable downsize below 3 / 8 pp -- so the sleeve's realisations stay rare,
but the width is the engine's and not the arm's. A target-weight plugin cannot observe the held
weight, so a held-weight band is not expressible (the limitation `ib32`'s C arm and `ib25_2L`'s B_2L
document, there on the target side). The arms are screened with the bank's fixed weights and the
engine's own band; no parameter is added to compensate.

**Arm C is `needs_kernel`.** Its frozen formula is not fundable inside the account's own 0.98 cap:
the ratio `vol20(QLD)/vol20(IEF)` sits at the clip's upper bound for most of the window, so the
sleeve is 0.24, and `0.90 x production + sleeve` exceeds 0.98 in **58.6%** of the window's sessions
(mean excess 7.4 pp of NAV). The kernel buys that with negative cash -- `fe2_sim._buy_leg`
decrements the one cash pool with no clamp and no interest -- i.e. an interest-free loan, the
cash-loan boundary `framework_v2`/§6.2 names for IC39. Screening C as written would hand it a
phantom financing advantage, so it is registered with its frozen spec and never simulated; making it
runnable needs a loan/interest mechanism in a kernel copy or a *funded* variant of the formula, and
both change the frozen parameters.
"""

from __future__ import annotations

import numpy as np

from research.batch_screen import api
from research.fast_engine import fe_signals

NEEDS_KERNEL_C = (
    "the frozen sleeve formula (0.08 x clip(vol20(QLD)/vol20(IEF), 0.5, 3.0)) on top of 0.90 x "
    "production holds more than the account's 0.98 cap in 58.6% of the window's sessions (mean "
    "excess 7.4 pp of NAV); the kernel funds the excess with negative cash and no interest, i.e. a "
    "cash loan -- the IC39 boundary"
)

ARMS = {
    "A": {"rule": "K", "leg": "2x", "leg_b": "ief", "cash_yield": "none", "start": "auto",
          "equity": 0.88, "sleeve": 0.10},
    "B": {"rule": "K", "leg": "2x", "leg_b": "ief", "cash_yield": "none", "start": "auto",
          "equity": 0.78, "sleeve": 0.20},
    "C": {"rule": "K", "leg": "2x", "leg_b": "ief", "cash_yield": "none", "start": "auto",
          "needs_kernel": NEEDS_KERNEL_C, "inputs": ["ief"], "equity": 0.90, "sleeve_coef": 0.08,
          "sleeve_clip": [0.5, 3.0], "sleeve_vol_num": "QLD", "sleeve_vol_den": "IEF"},
}

def production_wants(data):
    """The production E2 + early-re-entry target series (`k_control`'s `targets`)."""
    e2 = fe_signals.e2_up(data.price)
    comps = fe_signals.early_components(data.price)
    on_eff, factor = fe_signals.r2_state(data.price, e2, comps)
    return fe_signals.sized_wants(on_eff, factor, data.vol)

def targets(data, spec):
    """`equity x production` in the QLD* book and the fixed IEF sleeve in the second book."""
    if spec.get("needs_kernel"):
        raise api.NeedsKernel(spec["needs_kernel"])
    wants = production_wants(data)
    return float(spec["equity"]) * wants, np.full_like(wants, float(spec["sleeve"])), spec["leg_b"]
