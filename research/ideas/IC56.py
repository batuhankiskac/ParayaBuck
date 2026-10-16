"""IC56 -- dividend-withholding cost on the traded leg, batch_screen plugin.

Idea bank `docs/research/idea_bank_v2.json`, three arms, every parameter frozen:

    A  leg dividends are net of 20% withholding
    B  leg dividends are net of 15% withholding (a reduced-rate claim)
    C  20% withholding on the on-state and early legs, with the withheld amount claimed as a
       credit against the Turkish liability (report-only in the after-tax model)

Kernel mechanism: `research/fast_engine_opts`'s panel option `wht` (a *bridge* field, not a kernel
scalar), requested through a registered rule. The leg is rebuilt from dividends net of the
withholding rate before the engine's own `synthetic_2x` recurrence runs (`fe_opts_legs.repanel` on a
window, `chunk_raw_panel` inside a bootstrap chunk, where the netting happens upstream of the
chunk's leg reconstruction), so the withholding is a construction of the traded instrument -- the
flow the engine's total-return recurrence otherwise assumes gross. The arm has one leg: the same
withholding applies to the on-state position and to the half-weight early position (they are the
same instrument), which is arm C's "on-state and early legs".

**Arm C's credit is not simulated.** Its own text calls the claim *report-only in the after-tax
model*: crediting the withheld amount against the Turkish liability would change `fe_tax`'s
assessment step (an after-tax-model change, not a kernel option), so arm C registers exactly what
the bank's rule fixes -- 20% withholding -- and its declaration is the same simulation as arm A.
The registration is kept (the trial is what the ledger must count); the credit's size can be read
off arm A's leg-level withholding flow.

Readings fixed here: the targets are production K's. Established data only, no downloads. On a
price-only window (the six OOS indices, the 21 ETFs) there is no dividend, so the arm is identical
to K there -- the bank's own reduced statement.
"""

from __future__ import annotations

from research.batch_screen import rules
from research.fast_engine import fe_signals

#: the two frozen withholding rates (A/C default treaty rate, B the reduced-rate claim)
WHT_DEFAULT = 0.20
WHT_TREATY = 0.15

rules.register_rule("ic56_wht20", band=True, usd_rule=True, defer=True, opts={"wht": WHT_DEFAULT})
rules.register_rule("ic56_wht15", band=True, usd_rule=True, defer=True, opts={"wht": WHT_TREATY})

ARMS = {
    "A": {"rule": "ic56_wht20", "leg": "2x", "cash_yield": "none"},
    "B": {"rule": "ic56_wht15", "leg": "2x", "cash_yield": "none"},
    # C == A in the simulation; the foreign-tax credit is report-only (see the docstring)
    "C": {"rule": "ic56_wht20", "leg": "2x", "cash_yield": "none"},
}

def targets(data, spec):
    """Production K's target series (the withholding is a leg construction, not a target rule)."""
    e2 = fe_signals.e2_up(data.price)
    comps = fe_signals.early_components(data.price)
    on_eff, factor = fe_signals.r2_state(data.price, e2, comps)
    return fe_signals.sized_wants(on_eff, factor, data.vol)
