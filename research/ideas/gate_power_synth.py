"""gate_power_synth -- proper synthetic power arms for gate_power_v2.

K's own targets, unchanged, on legs with injected drift/noise. Not a trading idea.
"""
from __future__ import annotations

import numpy as np

from research.fast_engine import fe_signals

ARMS = {
    "syn_edge": {"rule": "K", "leg": "2x_d", "cash_yield": "none"},
    "syn_edge_i": {"rule": "K", "leg": "2x_di", "cash_yield": "none"},
    "syn_edge_p": {"rule": "K", "leg": "2x_dp", "cash_yield": "none"},
    "syn_null0": {"rule": "K", "leg": "2x_d", "cash_yield": "none"},
    "syn_noise_p": {"rule": "K", "leg": "2x_np", "cash_yield": "none"},
    "syn_noise_m": {"rule": "K", "leg": "2x_nm", "cash_yield": "none"},
}


def targets(data, spec):
    e2 = fe_signals.e2_up(data.price)
    comps = fe_signals.early_components(data.price)
    on_eff, factor = fe_signals.r2_state(data.price, e2, comps)
    w = fe_signals.sized_wants(on_eff, factor, data.vol)
    return np.clip(w, 0.0, 1.0)