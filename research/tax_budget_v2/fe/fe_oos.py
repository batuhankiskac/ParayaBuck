"""fe_oos -- the pooled out-of-sample bootstrap (gate G8) as one numba kernel.

`band_h2_combo_v1.boot_oos_market` ran one Python `engine_run` per path; the draws, the market
reconstruction and the statistic are its code, but the per-path simulation is `fe_sim.sim_one`
under `prange`, one path per iteration (so the result cannot depend on the thread count), and the
event logs are off (the parent study reports the OOS windows, not their event logs).

The draws are the parent's three per-chunk rng calls in the same order (`integers` for the start,
`random` for the jump mask, `integers` for the fresh draws, then the `(t-1 + 1) % npool`
recurrence), so `oos_boot(seed=20261011, block=20)` draws the same positions the Python version
did. Only M1-M5 are pooled (^GSPC 1957-1999 has no pre-1954 window for the financing model and is
not part of G8). Pre-tax, calibrated, m = 2, R-USD rule: all three are the gate's convention.
"""

from __future__ import annotations

import numpy as np
from numba import njit, prange

from . import fe_data, fe_signals, fe_sim
from .fe_config import BLOCK, EXP_Q, EXTRA_FIN, M, SEED, START, TSM_N
from .fe_tax import MODE_PRE_TAX

OOS_PATHS_PER_MARKET = 2_000
OOS_BOOT_L = TSM_N + 2_520  # 2,772: 252 warm-up + 2,520 measured sessions
OOS_CHUNK = 500
OOS_MARKETS = ("M1", "M2", "M3", "M4", "M5")


@njit(parallel=True, cache=True, nogil=True)
def _oos_chunk(
    oq,
    cq,
    wq,
    fx,
    upm,
    mth_of,
    yr_of,
    scale_y,
    dec_tail,
    jan_head,
    pay_ptr,
    pay_yr,
    pay_part,
    i0,
    i1,
    cost,
    Y0,
    M_lo,
    band,
    usd_rule,
    defer,
    yearend,
    realloc,
    out,
):
    """One chunk of OOS paths, one arm per row of the rule arrays, `prange` over paths."""
    W = oq.shape[0]
    n_arms = band.shape[0]
    k = i1 - i0 + 1
    rate = np.zeros(0, dtype=np.float64)
    ye_tr0 = np.zeros((0, 10), dtype=np.float64)
    re_tr0 = np.zeros((0, 4), dtype=np.float64)
    dbg0 = np.zeros((0, 6), dtype=np.float64)
    for p in prange(W):
        for a in range(n_arms):
            nav, _d, _s, _y, _g, _gn, _h = fe_sim.sim_one(
                oq[p],
                cq[p],
                wq[p],
                fx,
                upm,
                mth_of,
                yr_of,
                scale_y,
                dec_tail,
                pay_ptr,
                pay_yr,
                pay_part,
                1.0,
                i0,
                i1,
                cost,
                band[a],
                usd_rule[a],
                defer[a],
                yearend[a],
                realloc[a],
                jan_head,
                ye_tr0,
                re_tr0,
                MODE_PRE_TAX,
                False,
                Y0,
                M_lo,
                rate,
                False,
                dbg0,
            )
            out[a, p] = (nav[k] / nav[0]) ** (252 / k) - 1


def oos_boot_market(key, rng, arms, paths=OOS_PATHS_PER_MARKET, chunk=OOS_CHUNK, m=M):
    """The pooled-bootstrap leg of one OOS market: `{arm: [paths]}` of per-path CAGR.

    The market series is the resampled one (2,773 points: the 1.0 baseline plus one return per
    drawn position) but the *calendar* is the market's own first 2,773 sessions, exactly as
    `band_h2_combo_v1.boot_oos_market` had it when it drove `engine_run(START, cc.shape[1] - 1)`.
    So the measured window is sessions [START, OOS_BOOT_L] (2,520 sessions) and the year-end /
    December / January masks come from that fixed calendar, not from the resampled dates.
    """
    days, o, c, y, _start = fe_data.load_market(key)
    pool = np.arange(_start, len(days))
    npool = len(pool)
    i0, i1 = START, OOS_BOOT_L
    if len(days) <= i1:
        raise ValueError(f"{key}: only {len(days)} sessions, need {i1 + 1} for the fixed calendar")
    env = fe_sim.RunEnv(days, *fe_data.usd_market(days), i0, i1)
    cost = fe_sim.COST_PER_SIDE * m
    band = np.array([fe_sim.RULE[a]["band"] for a in arms], dtype=np.bool_)
    usd_rule = np.array([fe_sim.RULE[a]["usd_rule"] for a in arms], dtype=np.bool_)
    defer = np.array([fe_sim.RULE[a]["defer"] for a in arms], dtype=np.bool_)
    yearend = np.array([fe_sim.RULE[a]["yearend"] for a in arms], dtype=np.int64)
    realloc = np.array([fe_sim.RULE[a]["realloc"] for a in arms], dtype=np.bool_)
    out = np.empty((len(arms), paths), dtype=np.float64)
    for p0 in range(0, paths, chunk):
        P = min(chunk, paths - p0)
        pos = np.empty((P, OOS_BOOT_L), dtype=np.int64)
        pos[:, 0] = rng.integers(0, npool, P)
        jump = rng.random((P, OOS_BOOT_L)) < 1 / BLOCK
        fresh = rng.integers(0, npool, (P, OOS_BOOT_L))
        for t in range(1, OOS_BOOT_L):
            pos[:, t] = np.where(jump[:, t], fresh[:, t], (pos[:, t - 1] + 1) % npool)
        idx = pool[pos]
        r_on = o[idx] / c[idx - 1]
        r_id = c[idx] / o[idx]
        one = np.ones((P, 1))
        cc = np.concatenate([one, np.cumprod(r_on * r_id, axis=1)], axis=1)
        oo = np.concatenate([one, cc[:, :-1] * r_on], axis=1)
        yy = y[idx - 1]
        yyp = np.concatenate([yy, yy[:, -1:]], axis=1)
        e2 = fe_signals.e2_up(cc)
        so, sc = fe_signals.synthetic_2x(oo, cc, np.zeros_like(cc), yyp + EXTRA_FIN, EXP_Q)
        _w_e2, w_r2 = fe_signals.wants_of(cc, e2, fe_signals.vol_n(sc))
        _oos_chunk(
            so,
            sc,
            w_r2,
            env.fx,
            env.upm,
            env.mth_of,
            env.yr_of,
            env.scale_y,
            env.dec_tail,
            env.jan_head,
            env.pay_ptr,
            env.pay_yr,
            env.pay_part,
            i0,
            i1,
            cost,
            env.Y0,
            env.M_lo,
            band,
            usd_rule,
            defer,
            yearend,
            realloc,
            out[:, p0 : p0 + P],
        )
    return {a: out[k] for k, a in enumerate(arms)}


def oos_boot(arms, seed=SEED, block=BLOCK, paths=OOS_PATHS_PER_MARKET, chunk=OOS_CHUNK, m=M):
    """G8: the pooled M1-M5 bootstrap. `{arm: [paths]}` per market plus `pooled` + `per_market`."""
    rng = np.random.default_rng(seed)
    per = {key: oos_boot_market(key, rng, arms, paths=paths, chunk=chunk, m=m) for key in OOS_MARKETS}
    pooled = {a: np.concatenate([per[key][a] for key in OOS_MARKETS]) for a in arms}
    return {"per_market": per, "pooled": pooled}
