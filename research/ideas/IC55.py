"""IC55 -- FX conversion cost and the tax-payment schedule, batch_screen plugin.

Idea bank `docs/research/idea_bank_v2.json`, three arms, every parameter frozen:

    A  charge 0.50% on every TRY conversion when the liability is paid (a measurement arm: the
       same rule as K, priced honestly)
    B  pre-fund the liability: convert and set aside the due amount three months before the due
       date (a payment-timing rule)
    C  charge 1.00% (the cost stress)

Kernel mechanism: `research/fast_engine_opts`'s `fx_cost` (A/C) and `prefund_sessions` (B),
requested through a registered rule. `fx_cost` divides every TRY conversion at the tax step by
`1 - fx_cost`, so the account really pays the spread; `prefund_sessions = 63` converts the amount
due into a TRY reserve 63 sessions (three months) before the due date, out of **idle cash only** --
a fully invested account pre-funds nothing (`prefund_usd = 0`), so the arm never forces a sale and
never changes the tax base.

Scope, as the bank declares it (`kernel_scope: "after_tax_stage_only"`, arm by arm): both options
act only inside the taxed step of the kernel, and a pre-tax stage assesses no liability, so the
pre-tax stages are non-inferior to K by construction. The bank's `prefund_sessions = 63` is the
kernel's own unit (sessions, not calendar months).

Readings fixed here: the targets are production K's; the arm changes the after-tax accounting. No
extra data (the CBRT USD/TRY and YI-UFE series are the framework's own). Every window applicable.
"""

from __future__ import annotations

from research.batch_screen import rules
from research.fast_engine import fe_signals

#: the two frozen spread assumptions of the bank's arms A and C
FX_COST_PP = 0.005
FX_COST_STRESS_PP = 0.01
#: three months of sessions, the bank's pre-funding lead (arm B)
PREFUND_SESSIONS = 63.0

rules.register_rule("ic55_fx50bp", band=True, usd_rule=True, defer=True,
                    opts={"fx_cost": FX_COST_PP})
rules.register_rule("ic55_fx100bp", band=True, usd_rule=True, defer=True,
                    opts={"fx_cost": FX_COST_STRESS_PP})
rules.register_rule("ic55_prefund63", band=True, usd_rule=True, defer=True,
                    opts={"prefund_sessions": PREFUND_SESSIONS})

#: both options live in the taxed step of the kernel; the pre-tax stages are the engine's own
SCOPE = "after_tax_stage_only"

ARMS = {
    "A": {"rule": "ic55_fx50bp", "leg": "2x", "cash_yield": "none", "kernel_scope": SCOPE},
    "B": {"rule": "ic55_prefund63", "leg": "2x", "cash_yield": "none", "kernel_scope": SCOPE,
          "prefund_sessions": PREFUND_SESSIONS},
    "C": {"rule": "ic55_fx100bp", "leg": "2x", "cash_yield": "none", "kernel_scope": SCOPE},
}

def targets(data, spec):
    """Production K's target series (the arm changes the tax step, not the target)."""
    e2 = fe_signals.e2_up(data.price)
    comps = fe_signals.early_components(data.price)
    on_eff, factor = fe_signals.r2_state(data.price, e2, comps)
    return fe_signals.sized_wants(on_eff, factor, data.vol)
