"""MF1 -- a constant managed-futures (trend-following) sleeve beside K (`mf_sleeve_v1`).

Leg A is production K's own QLD* target scaled by `1 - s`; leg B is the hand-made trend proxy
`mf_proxy` (`research/mf_sleeve_v1/build_proxy.py`) held at a constant `0.98 * s` of NAV in every
state, so the account total never exceeds production's 0.98 cap. The sleeve is rebalanced by the
engine's own per-leg rule (0.5 pp band, A1u gain band, H2), never by a calendar. Parameters fixed
in `research/mf_sleeve_v1/PREREGISTRATION.md`.

The proxy exists only on the main window (and its bootstrap pool): every OOS market, ETF and
reduced window is `NotApplicable`.
"""

from __future__ import annotations

import numpy as np

from research.batch_screen.api import NotApplicable
from research.fast_engine import fe_signals

ARMS = {
    "S10": {"rule": "K", "leg": "2x", "leg_b": "mf_proxy", "cash_yield": "none", "sleeve": 0.10},
    "S20": {"rule": "K", "leg": "2x", "leg_b": "mf_proxy", "cash_yield": "none", "sleeve": 0.20},
    "S30": {"rule": "K", "leg": "2x", "leg_b": "mf_proxy", "cash_yield": "none", "sleeve": 0.30},
}


def targets(data, spec):
    if data.window not in ("main", "boot"):
        raise NotApplicable(f"the mf_proxy sleeve exists only on the main window (got {data.window!r})")
    e2 = fe_signals.e2_up(data.price)
    comps = fe_signals.early_components(data.price)
    on_eff, factor = fe_signals.r2_state(data.price, e2, comps)
    wants = fe_signals.sized_wants(on_eff, factor, data.vol)
    s = float(spec["sleeve"])
    sleeve = np.full_like(wants, 0.98 * s)
    return wants * (1.0 - s), sleeve, spec["leg_b"]


__all__ = ["ARMS", "targets"]
