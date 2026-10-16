"""IC39 -- margin-financed QQQ instead of QLD (broker leverage), batch_screen plugin.

Idea bank `docs/research/idea_bank_v2.json`, three arms, every parameter frozen:

    A  hold the 2x exposure as QQQ plus a cash loan at the frozen broker margin rate (no expense
       ratio, dividends on the whole position)
    B  as A but the loan is capped so gross exposure never exceeds 1.6x (the margin-call
       mitigation)
    C  as A with the margin rate stressed +2 pp (the cost-sensitivity arm)

Kernel mechanism: `research/fast_engine_opts`'s `mrate` (IC39) -- the account's negative cash
balance accrues `cash x mrate[i]` every session -- requested through a registered rule
(`rules.register_rule(..., opts={"margin_spread": ...})`). The leg is the panel's `1x` (the
underlying itself, `api.total_return_1x`); a weight above 1 makes the cash balance negative, which
is the loan.

Readings fixed here (the spec's wording made concrete; nothing is tuned):

* **The exposure is production's.**  `wt = sized_wants(on_eff, factor, vol_2x)`: the production
  state machine and production's *sizing input*, i.e. the vol of the window's own `2x` leg
  (`data.legs["2x"]`), not of the 1x leg the arm now holds.  The 1x leg's vol is about half of it,
  so sizing on `data.vol` would raise the exposure and confound the cost comparison the idea is
  about ("the *same* 2x exposure, financed at the broker").  The bank's "wt/2 in QQQ plus a wt/2
  loan" is realised as a position weight of `2 x wt` of NAV: 1.0 of NAV is the owner's own cash
  and `2 x wt - 1` is the loan (the arm text's `wt` is the *exposure*, `2 x` production's weight).
* **Arm B's cap is a gross-exposure cap**: the position weight is `min(1.6, 2 x wt)`, i.e. the
  loan never exceeds 0.6 of NAV.
* **The margin rate.**  `margin_spread` is the annual rate charged *over the window's own DTB3
  column* (`bridge` folds it per session), 1.5 pp for A/B (the kernel_opts package's frozen
  reading of "the live broker's published margin rate") and 3.5 pp for C (+2 pp stress).
* **Applicability**: no extra data.  Every window is applicable -- the OOS indices and the 21
  ETFs offer the same `1x`/`2x` legs and their own TB3MS column, exactly as the bank records.
"""

from __future__ import annotations

import numpy as np

from research.batch_screen import rules
from research.fast_engine import fe_signals

#: the frozen broker margin rate over DTB3 (A/B) and its +2 pp stress (C)
MARGIN_SPREAD_PP = 0.015
MARGIN_SPREAD_STRESS_PP = 0.035
#: arm B's gross-exposure cap (the margin-call mitigation)
GROSS_CAP_B = 1.6

rules.register_rule("ic39_margin_qqq", band=True, usd_rule=True, defer=True,
                    opts={"margin_spread": MARGIN_SPREAD_PP})
rules.register_rule("ic39_margin_qqq_stress", band=True, usd_rule=True, defer=True,
                    opts={"margin_spread": MARGIN_SPREAD_STRESS_PP})

ARMS = {
    "A": {"rule": "ic39_margin_qqq", "leg": "1x", "cash_yield": "none"},
    "B": {"rule": "ic39_margin_qqq", "leg": "1x", "cash_yield": "none", "gross_cap": GROSS_CAP_B},
    "C": {"rule": "ic39_margin_qqq_stress", "leg": "1x", "cash_yield": "none"},
}

def targets(data, spec):
    """2x of production's target weight, on the panel's 1x leg (the loan is the negative cash)."""
    e2 = fe_signals.e2_up(data.price)
    comps = fe_signals.early_components(data.price)
    on_eff, factor = fe_signals.r2_state(data.price, e2, comps)
    wt = fe_signals.sized_wants(on_eff, factor, fe_signals.vol_n(data.legs["2x"][1]))
    pos = 2.0 * wt
    cap = spec.get("gross_cap")
    if cap is not None:
        pos = np.minimum(float(cap), pos)
    return pos
