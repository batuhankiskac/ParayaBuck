"""synthetic_power -- positive/negative controls for the batch screen's power.

Arms are K's target series perturbed, to measure the screen's ability to detect a
known edge and to reject pure noise. Not a trading idea.
"""

from __future__ import annotations

import numpy as np

from research.fast_engine import fe_signals

ARMS = {
    "syn_edge_105": {"rule": "K", "leg": "2x", "cash_yield": "none", "kind": "edge", "scale": 1.05},
    "syn_edge_110": {"rule": "K", "leg": "2x", "cash_yield": "none", "kind": "edge", "scale": 1.10},
    "syn_null": {"rule": "K", "leg": "2x", "cash_yield": "none", "kind": "null", "scale": 1.0},
}


def targets(data, spec):
    e2 = fe_signals.e2_up(data.price)
    comps = fe_signals.early_components(data.price)
    on_eff, factor = fe_signals.r2_state(data.price, e2, comps)
    w = fe_signals.sized_wants(on_eff, factor, data.vol)
    kind = spec.get("kind", "edge")
    scale = float(spec.get("scale", 1.0))
    out = np.clip(w * scale, 0.0, 1.0)
    if kind == "null":
        rng = np.random.default_rng(12345)
        noise = rng.normal(0.0, 0.05, size=out.shape)
        out = np.clip(out + noise, 0.0, 1.0)
    return out
