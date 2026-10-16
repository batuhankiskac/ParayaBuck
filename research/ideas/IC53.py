"""IC53 -- order slicing over N sessions, batch_screen plugin.

Idea bank `docs/research/idea_bank_v2.json`, three arms, every parameter frozen:

    A  a resize larger than 25 pp of NAV fills in 3 equal parts on the next 3 opens
    B  a resize larger than 50 pp fills in 5 equal parts on the next 5 opens
    C  every resize fills in 2 equal parts on the next 2 opens

Kernel mechanism: `research/fast_engine_opts`'s `slice_frac` / `slice_parts`, requested through a
registered rule. The trigger is the permitted order's size as a fraction of NAV
(`|du| x price / NAV`) against `slice_frac`; the order is then split into `slice_parts` equal
pieces filling at the following sessions' opens, and the session's own target logic is suspended
while it drains (otherwise the next session would re-place the whole order). Arm C's
`slice_frac = 0.0` slices every non-zero order, which is the arm's "every resize".

Readings fixed here: the targets are production K's; slicing is a fill schedule, not a target rule.
No extra data, every window applicable -- the arms need opens, which every panel carries (the
loader always offers them; only the OOS windows that the framework builds from a close-only source
would be affected, and none of the six is close-only in `fe_data`).
"""

from __future__ import annotations

from research.batch_screen import rules
from research.fast_engine import fe_signals

rules.register_rule("ic53_slice25_x3", band=True, usd_rule=True, defer=True,
                    opts={"slice_frac": 0.25, "slice_parts": 3.0})
rules.register_rule("ic53_slice50_x5", band=True, usd_rule=True, defer=True,
                    opts={"slice_frac": 0.50, "slice_parts": 5.0})
rules.register_rule("ic53_slice_all_x2", band=True, usd_rule=True, defer=True,
                    opts={"slice_frac": 0.0, "slice_parts": 2.0})

ARMS = {
    "A": {"rule": "ic53_slice25_x3", "leg": "2x", "cash_yield": "none"},
    "B": {"rule": "ic53_slice50_x5", "leg": "2x", "cash_yield": "none"},
    "C": {"rule": "ic53_slice_all_x2", "leg": "2x", "cash_yield": "none"},
}

def targets(data, spec):
    """Production K's target series (the schedule is a fill decision, not a target rule)."""
    e2 = fe_signals.e2_up(data.price)
    comps = fe_signals.early_components(data.price)
    on_eff, factor = fe_signals.r2_state(data.price, e2, comps)
    return fe_signals.sized_wants(on_eff, factor, data.vol)
