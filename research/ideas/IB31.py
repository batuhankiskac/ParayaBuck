"""IB31 -- volatility-spike exit for the early leg (replace the SMA50 break).  Idea bank
`idea_bank_v1`.

    A  the early leg exits when vol20 > 1.5 * median252(vol20)
    B  the same with 2.0
    C  the early leg exits on (close < SMA50) OR (vol20 > 1.5 * median252(vol20))

Only the early path's *exit* condition changes.  Entry (close > SMA50 AND vol20 < its 252-session
median AND close > close 20 sessions ago), the E2 vote's override, the half weight and the K
execution layer are production's.  Arms A and B replace the price-level exit outright, as the
title says; arm C is the union and can therefore only fire earlier.

The state machine is the engine's own `fe_signals.r2_state` loop copied verbatim (`_early_exit`
below) with `exit_cond` substituted -- same order of operations, same `np.where` masks, so the
production exit (`~sma50_up`) is bit-identical to `fe_signals.r2_state`.  `vol20` is
`fe_signals.early_components`' price vol20 and its median is the same trailing 252-session
`MED_WINDOW` median production's entry filter uses, so the spike test is on the same series and
the same window the idea refers to.

Applicable on every window (closes only).
"""

from __future__ import annotations

import numpy as np

from research.fast_engine import fe_signals

ARMS = {
    "A": {"rule": "K", "leg": "2x", "cash_yield": "none", "vol_mult": 1.5, "keep_sma50": False},
    "B": {"rule": "K", "leg": "2x", "cash_yield": "none", "vol_mult": 2.0, "keep_sma50": False},
    "C": {"rule": "K", "leg": "2x", "cash_yield": "none", "vol_mult": 1.5, "keep_sma50": True},
}


def early_state(price, e2_on, comps, exit_cond):
    """`fe_signals.r2_state` with a supplied per-session exit condition (same loop, verbatim)."""
    price = np.atleast_2d(np.asarray(price, dtype=float))
    e2_on = np.atleast_2d(np.asarray(e2_on, dtype=bool))
    P, T = price.shape
    sma50_up, vol_p, med_p, lag_cond = comps
    early_active = np.zeros(P, dtype=bool)
    on_eff = np.zeros((P, T), dtype=bool)
    factor = np.zeros((P, T), dtype=float)
    for t in range(T):
        e2t = e2_on[:, t]
        early_active = np.where(e2t, False, early_active)
        entry_cond = (sma50_up[:, t] & np.isfinite(med_p[:, t])
                      & (vol_p[:, t] < med_p[:, t]) & lag_cond[:, t])
        early_active = np.where(early_active & exit_cond[:, t] & (~e2t), False, early_active)
        can_enter = (~early_active) & (~e2t) & np.isfinite(med_p[:, t])
        early_active = np.where(can_enter & entry_cond, True, early_active)
        on_eff[:, t] = e2t | early_active
        factor[:, t] = np.where(e2t, 1.0, np.where(early_active, 0.5, 0.0))
    return on_eff, factor


def targets(data, spec):
    price = data.price
    comps = fe_signals.early_components(price)
    sma50_up, vol_p, med_p, _lag = comps
    spike = np.isfinite(med_p) & (vol_p > float(spec["vol_mult"]) * med_p)
    exit_cond = spike if not spec["keep_sma50"] else ((~sma50_up) | spike)
    e2 = fe_signals.e2_up(price)
    on_eff, factor = early_state(price, e2, comps, exit_cond)
    return fe_signals.sized_wants(on_eff, factor, data.vol)
