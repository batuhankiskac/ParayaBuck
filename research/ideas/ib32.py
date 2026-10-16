"""IB32 -- sticky sizing input: a dead-band on vol20 instead of on the target weight.

Per `docs/research/idea_bank_v1.json` IB32:

    A  hold the last accepted vol20 until |vol20_new / vol20_accepted - 1| > 0.20
    B  the same with a 0.35 relative dead-band
    C  hold the last accepted *target weight* until the new target differs by more than 5 pp
       (the plain-band control)

A/B filter the *input*: the accepted `vol20` is the panel's sizing input `data.vol`
(`fe_signals.vol_n` of the held leg's close), and the filtered series feeds the unchanged
production sizing `min(1, 0.35/vol20_filtered)` x path factor, cap 0.98 (rule `K`). The filter is
a per-session recursion over the panel's paths; before the first finite `vol20` the accepted value
is NaN, i.e. the engine's own `vt_of` convention (NaN -> 1.0) applies exactly as it does to the
production warm-up.

C is the output-band comparison arm: it holds the *raw* production target until the new target
differs from the accepted one by more than 5 pp (`0.05` in weight units). The framework's engine
exposes four fixed rule combinations and its band is a drift band of 0.5 pp (0.5/3/8 pp for a
gain-heavy downsize under `A1u`/`K`), with no configurable width, so a plain 5 pp band on the
target can only be the plugin-side dead-band implemented here; the arm therefore keeps the
production execution layer (rule `K`) and adds the 5 pp hold on top of it. Documented so the
composition is not mistaken for an engine-level 5 pp band.

Fixed parameters: 0.20 / 0.35 / 5 pp, as the arm text. No extra data, no engine change, every
window applicable.
"""

from __future__ import annotations

import numpy as np

from research.fast_engine import fe_signals
from research.plugins_b3 import helpers

ARMS = {
    "A": {"rule": "K", "leg": "2x", "cash_yield": "none", "mode": "vol", "band": 0.20},
    "B": {"rule": "K", "leg": "2x", "cash_yield": "none", "mode": "vol", "band": 0.35},
    "C": {"rule": "K", "leg": "2x", "cash_yield": "none", "mode": "target", "band": 0.05},
}


def sticky_vol(vol, band):
    """`(P, T)` `vol` held until it moves more than `band` (relative) from the accepted value."""
    vol = np.atleast_2d(np.asarray(vol, dtype=np.float64))
    P, T = vol.shape
    out = np.full((P, T), np.nan)
    accepted = np.full(P, np.nan)
    for t in range(T):
        v = vol[:, t]
        ref = np.where(np.isfinite(accepted), accepted, 1.0)
        move = np.abs(v / ref - 1.0) > band
        accepted = np.where(np.isfinite(v) & (np.isnan(accepted) | move), v, accepted)
        out[:, t] = accepted
    return out


def sticky_target(wants, band):
    """`(P, T)` `wants` held until it differs from the accepted target by more than `band`."""
    wants = np.atleast_2d(np.asarray(wants, dtype=np.float64))
    P, T = wants.shape
    out = np.empty((P, T), dtype=np.float64)
    accepted = wants[:, 0].copy()
    out[:, 0] = accepted
    for t in range(1, T):
        move = np.abs(wants[:, t] - accepted) > band
        accepted = np.where(move, wants[:, t], accepted)
        out[:, t] = accepted
    return out


def targets(data, spec):
    e2 = fe_signals.e2_up(data.price)
    comps = fe_signals.early_components(data.price)
    on_eff, factor = fe_signals.r2_state(data.price, e2, comps)
    if spec["mode"] == "vol":
        vol = sticky_vol(data.vol, spec["band"])
        return helpers.capped(on_eff, factor, vol)
    if spec["mode"] == "target":
        return sticky_target(helpers.capped(on_eff, factor, data.vol), spec["band"])
    raise ValueError(f"unknown mode {spec['mode']!r}")  # pragma: no cover
