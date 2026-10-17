"""a1u_control -- R2 target series under the A1u execution layer (band, no December deferral).

Used as control/candidate for positive controls.
"""

from __future__ import annotations

from research.fast_engine import fe_signals

ARMS = {
    "A1u": {"rule": "A1u", "leg": "2x", "cash_yield": "none"},
}


def targets(data, spec):
    e2 = fe_signals.e2_up(data.price)
    comps = fe_signals.early_components(data.price)
    on_eff, factor = fe_signals.r2_state(data.price, e2, comps)
    return fe_signals.sized_wants(on_eff, factor, data.vol)
