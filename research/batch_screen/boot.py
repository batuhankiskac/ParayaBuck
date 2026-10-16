"""batch_screen.boot -- the plugin-aware block bootstrap.

`fe_boot.boot` draws the published paths and then simulates the *one* target series it knows
(production R2's). The framework needs an arbitrary plugin's targets per path, so this module adds
two numba drivers over the engine's own `fe_sim.sim_one` kernel:

    _chunk      the clean/fresh bootstrap (pre-tax base+calibrated, after-tax both modes), with a
                per-arm rule (band/usd_rule/defer) and per-arm target panels
    _pt         the pooled OOS bootstrap (pre-tax, one rule per arm)

Nothing else is new: the path draws are `fe_boot.draw_positions` (the published per-path rng
calls, which makes a run chunk-size independent), the OOS pooled draws replay
`band_h2_combo_v1.screen.boot_oos_market`'s three chunked rng calls in its own order (so its chunk
size is fixed at 500), and the market reconstruction is the published `chunk_legs` arithmetic.
`tests/test_parity.py` asserts both drivers reproduce the engine's `fe_boot.boot` and the old
pure-Python `boot_oos_market` cell for cell.
"""

from __future__ import annotations

import numpy as np
from numba import njit, prange

from research.fast_engine import fe_boot, fe_data, fe_sim
from research.fast_engine2 import fe2_boot
from research.fast_engine.fe_config import (
    BLOCK,
    EXP_Q,
    EXTRA_FIN,
    FIN,
    PRE_BOOT_PATHS,
    SEED,
    TAX_BOOT_EXT,
    TAX_BOOT_L,
    TAX_BOOT_PATHS,
    TAX_CAPITALS,
)
from research.fast_engine.fe_tax import MODE_FLAT, MODE_PRE_TAX, MODE_PROGRESSIVE

from . import windows
from .api import NotApplicable, call_targets, panel
from .rules import cash_rates, rule_of

TAX_MODES = ("progressive", "flat")
DEFAULT_CHUNK = 256
DBG0 = np.zeros((0, 6), dtype=np.float64)  # the engine's bisect buffer, always off
OOS_PATHS_PER_MARKET = 2000
OOS_BOOT_L = 252 + 2520  # ewma_band_v1: TSM_N + trv2.MEASURED
OOS_CHUNK = 500  # trv2.CHUNK: fixed, the published draw order depends on it


# ----------------------------------------------------------------------------- kernels
@njit(parallel=True, cache=True, nogil=True)
def _chunk(
    oq_b,
    cq_b,
    wq_b,
    oq_c,
    cq_c,
    wq_c,
    fx,
    upm,
    mth_of,
    yr_of,
    scale_y,
    dec_tail,
    pay_ptr,
    pay_yr,
    pay_part,
    cap_pre,
    cap_at,
    i0,
    i1,
    cost,
    yrs,
    Y0,
    M_lo,
    band,
    usd_rule,
    defer,
    rule_index,
    mode_ids,
    cash_rate,
    p0,
    n_pre,
    n_tax,
    dbg0,
    out_pre,
    out_at,
):
    """`fe_boot._boot_chunk` with per-arm target panels `(n_arms, W, T)` and per-arm rules.

    `out_pre` is `(2, n_arms, W)` (base, calibrated), `out_at` is `(n_caps, n_modes, n_arms, W)`.
    """
    W = oq_b.shape[0]
    n_arms = wq_b.shape[0]
    n_caps = out_at.shape[0]
    n_modes = out_at.shape[1]
    k = i1 - i0 + 1
    for p in prange(W):
        pg = p0 + p
        if pg < n_pre:
            for a in range(n_arms):
                nav, _d, _s, _y, _g, _gn, _h = fe_sim.sim_one(
                    oq_b[p], cq_b[p], wq_b[a, p], fx, upm, mth_of, yr_of, scale_y, dec_tail, pay_ptr,
                    pay_yr, pay_part, cap_pre, i0, i1, cost, band[a], usd_rule[a], defer[a],
                    MODE_PRE_TAX, rule_index, Y0, M_lo, cash_rate, False, dbg0,
                )
                out_pre[0, a, p] = (nav[k] / nav[0]) ** (252 / k) - 1
                nav, _d, _s, _y, _g, _gn, _h = fe_sim.sim_one(
                    oq_c[p], cq_c[p], wq_c[a, p], fx, upm, mth_of, yr_of, scale_y, dec_tail, pay_ptr,
                    pay_yr, pay_part, cap_pre, i0, i1, cost, band[a], usd_rule[a], defer[a],
                    MODE_PRE_TAX, rule_index, Y0, M_lo, cash_rate, False, dbg0,
                )
                out_pre[1, a, p] = (nav[k] / nav[0]) ** (252 / k) - 1
        if pg < n_tax:
            for ci in range(n_caps):
                for mi in range(n_modes):
                    mode = mode_ids[mi]
                    for a in range(n_arms):
                        _nav, _d, scal, _y, _g, _gn, _h = fe_sim.sim_one(
                            oq_b[p], cq_b[p], wq_b[a, p], fx, upm, mth_of, yr_of, scale_y, dec_tail,
                            pay_ptr, pay_yr, pay_part, cap_at[ci], i0, i1, cost, band[a], usd_rule[a],
                            defer[a], mode, rule_index, Y0, M_lo, cash_rate, False, dbg0,
                        )
                        out_at[ci, mi, a, p] = (scal[fe_sim.SC_LIQ] / cap_at[ci]) ** (1 / yrs) - 1


@njit(parallel=True, cache=True, nogil=True)
def _pt(
    so,
    sc,
    wq,
    fx,
    upm,
    mth_of,
    yr_of,
    scale_y,
    dec_tail,
    pay_ptr,
    pay_yr,
    pay_part,
    capital,
    i0,
    i1,
    cost,
    band,
    usd_rule,
    defer,
    rule_index,
    Y0,
    M_lo,
    cash_rate,
    dbg0,
    out,
):
    """Pre-tax, one leg group, `prange` over paths: the OOS pooled bootstrap's inner loop."""
    k = i1 - i0 + 1
    n_arms = wq.shape[0]
    for p in prange(so.shape[0]):
        for a in range(n_arms):
            nav, _d, _s, _y, _g, _gn, _h = fe_sim.sim_one(
                so[p], sc[p], wq[a, p], fx, upm, mth_of, yr_of, scale_y, dec_tail, pay_ptr, pay_yr,
                pay_part, capital, i0, i1, cost, band[a], usd_rule[a], defer[a], MODE_PRE_TAX,
                rule_index, Y0, M_lo, cash_rate, False, dbg0,
            )
            out[a, p] = (nav[k] / nav[0]) ** (252 / k) - 1


# ----------------------------------------------------------------------------- boot window
_WINDOWS = {}


class BootWindow:
    """`fe_boot.BootWindow` (the fixed 2015-09 -> 2026-09 calendar) plus the cash-yield schedule
    and an optional restricted drawing pool.

    `pool = (lo, hi)` restricts the drawn source sessions to `positions lo..hi` of the main
    window's return pool (i.e. sessions `lo + 1 .. hi + 1`, see `windows.boot_pool`). The chunks
    are then built from those sessions only -- the *same* seed/block/rng order
    (`fe_boot.draw_positions` with the pool's own length and an added offset), so a reduced-window
    arm's bootstrap pool is its own window and the control is re-drawn on that pool.
    """

    def __init__(self, cash_yield: str = "none", pool=None):
        base = _WINDOWS.get("base")
        if base is None:
            base = _WINDOWS["base"] = fe_boot.BootWindow()
        self.dates = base.dates
        self.fx = base.fx
        self.ufe = base.ufe
        self.q = base.q
        self.y = base.y
        self.npool = base.npool
        self.i0 = base.i0
        self.i1 = base.i1
        self.yrs = base.yrs
        self.pool = None if pool is None else (int(pool[0]), int(pool[1]))
        if self.pool is None:
            self.pool_lo, self.npool_draw = 0, self.npool
        else:
            lo, hi = self.pool
            if not (0 <= lo <= hi < self.npool):
                raise ValueError(f"bootstrap pool {self.pool} outside the return pool "
                                 f"[0, {self.npool})")
            self.pool_lo, self.npool_draw = lo, hi - lo + 1
        key = ("env", cash_yield)
        env = _WINDOWS.get(key)
        if env is None:
            rate = None if cash_yield == "none" else cash_rates(list(self.dates))
            env = _WINDOWS[key] = fe_sim.RunEnv(self.dates, self.fx, self.ufe, self.i0, self.i1, cash_rate=rate)
        self.env = env

    def positions(self, rng, block, n_paths) -> np.ndarray:
        """This window's chunk draw: the published three rng calls, on the restricted pool."""
        return (fe_boot.draw_positions(rng, block, n_paths, TAX_BOOT_L, self.npool_draw,
                                       TAX_BOOT_EXT) + self.pool_lo)


# ----------------------------------------------------------------------------- market panels
def _extra_index(idx):
    """The extra-series index of a reconstructed chunk, from the drawn session index `idx`.

    A chunk's market arrays carry one leading base row: column 0 is the artificial level and the
    return *into* column 1 is the first drawn session's, so drawn session `idx[p, t]` is the state
    at column `t + 1`. The extra series uses the same convention (column 0 repeats the first drawn
    session), which is also what makes it one column wider than `idx` and therefore exactly as wide
    as the market arrays a plugin sees.
    """
    return np.concatenate([idx[:, :1], idx], axis=1)


def boot_panels(win, pos, legs, fins, cache=None):
    """The published `fe_boot.chunk_legs` reconstruction, as `{(leg, fin): MarketData}`.

    `pos` is the drawn position matrix; the indices are `pos + 1` and the arrays are the pool
    `arange(1, Tsrc)`. The panel also carries the extra-series context: the pool is the *main
    window's* own session list (the bootstrap draws from its full 1999-2026 history, whatever
    labels the chunk's calendar carries), so `data.extra(key)` inside a chunk returns the drawn
    source sessions' values.
    """
    idx = pos + 1
    yy = win.y[idx - 1]
    prev = win.q[1][idx - 1]
    r_on_tr = (win.q[0][idx] + win.q[2][idx]) / prev
    r_on_p = win.q[0][idx] / prev
    r_id = win.q[1][idx] / win.q[0][idx]
    P = idx.shape[0]
    one = np.ones((P, 1))
    qc = np.concatenate([one, np.cumprod(r_on_tr * r_id, axis=1)], axis=1)
    qo = np.concatenate([one, qc[:, :-1] * r_on_tr], axis=1)
    pc = np.concatenate([one, np.cumprod(r_on_p * r_id, axis=1)], axis=1)
    yyp = np.concatenate([yy, yy[:, -1:]], axis=1)
    d1 = win.q[2][idx]
    # the 1x leg: the chunk's own total-return QQQ path (`qo`/`qc`, dividends already in), so it
    # has the 2x leg's width and no return across a block jump -- `total_return_1x` of the raw
    # drawn prices would span the jumps (and is one column short)
    o1, c1, d1_leg = qo, qc, np.zeros_like(qc)
    pool_days = windows.bars()[0]
    out = cache if cache is not None else {}
    for fin in fins:
        for leg in legs:
            out[(leg, fin)] = panel(
                win.dates,
                price=pc,
                div=d1,
                rate=yyp,
                o2=qo,
                c2=qc,
                d2=np.zeros_like(qc),
                o1=o1,
                c1=c1,
                d1=d1_leg,
                fin=fin,
                exp_q=EXP_Q,
                leg=leg,
                window="boot",
                meta={"model": "base" if fin == 0.0 else "calibrated", "fin": float(fin), "leg": leg},
                extra_days=pool_days,
                extra_index=_extra_index(idx),
                extra_window="main",
            )
    return out


def _rule_arrays(arm_list):
    band = np.array([rule_of(a["rule"])["band"] for a in arm_list], dtype=np.bool_)
    usd = np.array([rule_of(a["rule"])["usd_rule"] for a in arm_list], dtype=np.bool_)
    defer = np.array([rule_of(a["rule"])["defer"] for a in arm_list], dtype=np.bool_)
    return band, usd, defer


def boot(arms, seed=SEED, block=BLOCK, n_pre=PRE_BOOT_PATHS, n_tax=TAX_BOOT_PATHS,
         capitals=("100k",), modes=TAX_MODES, m=fe_sim.DEFAULT_M, chunk=DEFAULT_CHUNK,
         cash_yield="none", rule_index=True, label="boot", quiet=True, pool=None):
    """The clean/fresh block bootstrap over arbitrary plugin targets.

    Arms are grouped by `(leg, leg_b)`: a single-leg group runs the engine's kernel, a two-leg
    group (`leg_b` declared) runs `fe2_boot._boot_chunk2`. Both groups share the chunk's drawn
    paths -- the same `draw_positions` call per chunk -- so every arm's per-path CAGRs, and
    therefore the Δ against the control, stay paired.

    Returns `{"pre": {model: {arm_id: (n_pre,)}}, "tax": {cap: {mode: {arm_id: (n_tax,)}}},
    "params": {...}}` -- raw per-path CAGRs, the input of `delta_stats` and the deflation.
    """
    win = BootWindow(cash_yield, pool)
    env = win.env
    n_paths = max(n_pre, n_tax)
    cap_at = np.array([TAX_CAPITALS[c] for c in capitals], dtype=np.float64)
    cost = fe_sim.COST_PER_SIDE * m
    mode_ids = np.array([MODE_PROGRESSIVE if md == "progressive" else MODE_FLAT for md in modes], dtype=np.int64)
    ids = [a["id"] for a in arms]
    pre = {mdi: {a: np.empty(n_pre) for a in ids} for mdi in ("base", "calibrated")}
    tax = {cl: {md: {a: np.empty(n_tax) for a in ids} for md in modes} for cl in capitals}
    rng = np.random.default_rng(seed)
    groups = {}
    for arm in arms:
        groups.setdefault((arm["leg"], arm.get("leg_b")), []).append(arm)
    for p0 in range(0, n_paths, chunk):
        W = min(chunk, n_paths - p0)
        pos = win.positions(rng, block, W)
        panels = {}
        for leg in sorted({g[0] for g in groups}):
            panels.update(boot_panels(win, pos, (leg,), (FIN["base"], FIN["calibrated"]), cache=panels))
        for (leg, leg_b), arm_list in groups.items():
            if leg_b is not None:
                _two_leg_chunk(arm_list, panels, leg, leg_b, win, env, cap_at, cost, mode_ids,
                               rule_index, p0, W, n_pre, n_tax, pre, tax, capitals, modes)
                continue
            wb = np.stack([_targets(a, panels[(leg, FIN["base"])]) for a in arm_list])
            wc = np.stack([_targets(a, panels[(leg, FIN["calibrated"])]) for a in arm_list])
            band, usd, defer = _rule_arrays(arm_list)
            so = panels[(leg, FIN["base"])].legs[leg][0]
            sc = panels[(leg, FIN["base"])].legs[leg][1]
            so_c = panels[(leg, FIN["calibrated"])].legs[leg][0]
            sc_c = panels[(leg, FIN["calibrated"])].legs[leg][1]
            out_pre = np.empty((2, len(arm_list), W), dtype=np.float64)
            out_at = np.empty((len(capitals), len(modes), len(arm_list), W), dtype=np.float64)
            _chunk(
                np.ascontiguousarray(so), np.ascontiguousarray(sc), np.ascontiguousarray(wb),
                np.ascontiguousarray(so_c), np.ascontiguousarray(sc_c), np.ascontiguousarray(wc),
                env.fx, env.upm, env.mth_of, env.yr_of, env.scale_y, env.dec_tail, env.pay_ptr,
                env.pay_yr, env.pay_part, TAX_CAPITALS["100k"], cap_at, win.i0, win.i1, cost,
                win.yrs, env.Y0, env.M_lo, band, usd, defer, rule_index, mode_ids, env.cash_rate,
                p0, n_pre, n_tax, DBG0, out_pre, out_at,
            )
            npre = max(0, min(W, n_pre - p0))
            ntax = max(0, min(W, n_tax - p0))
            for ai, arm in enumerate(arm_list):
                for bi, mdi in enumerate(("base", "calibrated")):
                    if npre:
                        pre[mdi][arm["id"]][p0 : p0 + npre] = out_pre[bi, ai, :npre]
                for ci, cl in enumerate(capitals):
                    for mi, md in enumerate(modes):
                        if ntax:
                            tax[cl][md][arm["id"]][p0 : p0 + ntax] = out_at[ci, mi, ai, :ntax]
        if not quiet:
            print(f"  {label} path {min(p0 + W, n_paths)}/{n_paths}", flush=True)
    return {
        "pre": pre,
        "tax": tax,
        "params": {"seed": seed, "block": block, "n_pre": n_pre, "n_tax": n_tax,
                   "capitals": list(capitals), "modes": list(modes), "m": m,
                   "calendar": [win.dates[0], win.dates[-1]], "cash_yield": cash_yield,
                   "pool": None if win.pool is None else list(win.pool),
                   "arms": ids},
    }


def _two_leg_chunk(arm_list, panels, leg, leg_b, win, env, cap_at, cost, mode_ids, rule_index,
                   p0, W, n_pre, n_tax, pre, tax, capitals, modes):
    """One chunk of a two-leg group on the chunk's already drawn panels (`fe2_boot._boot_chunk2`)."""
    pb, pc = panels[(leg, FIN["base"])], panels[(leg, FIN["calibrated"])]
    ob, cb = two_leg_series(panels, leg, leg_b, FIN["base"], win.i0, win.i1)
    oc, cc = two_leg_series(panels, leg, leg_b, FIN["calibrated"], win.i0, win.i1)
    wb = np.ascontiguousarray(np.stack([_targets2(a, pb) for a in arm_list]))
    wc = np.ascontiguousarray(np.stack([_targets2(a, pc) for a in arm_list]))
    band, usd, defer = _rule_arrays(arm_list)
    out_pre = np.empty((2, len(arm_list), W), dtype=np.float64)
    out_at = np.empty((len(capitals), len(modes), len(arm_list), W), dtype=np.float64)
    fe2_boot._boot_chunk2(
        ob, cb, wb, oc, cc, wc, env.fx, env.upm, env.mth_of, env.yr_of, env.scale_y,
        env.dec_tail, env.pay_ptr, env.pay_yr, env.pay_part, TAX_CAPITALS["100k"], cap_at,
        win.i0, win.i1, cost, win.yrs, env.Y0, env.M_lo, band, usd, defer, rule_index, mode_ids,
        env.cash_rate, p0, n_pre, n_tax, DBG0, out_pre, out_at,
    )
    npre = max(0, min(W, n_pre - p0))
    ntax = max(0, min(W, n_tax - p0))
    for ai, arm in enumerate(arm_list):
        for bi, mdi in enumerate(("base", "calibrated")):
            if npre:
                pre[mdi][arm["id"]][p0 : p0 + npre] = out_pre[bi, ai, :npre]
        for ci, cl in enumerate(capitals):
            for mi, md in enumerate(modes):
                if ntax:
                    tax[cl][md][arm["id"]][p0 : p0 + ntax] = out_at[ci, mi, ai, :ntax]


def _targets(arm, p):
    """The arm's `targets` on one panel (the module travels with the arm)."""
    t = call_targets(arm["mod"], p, arm)
    if t.leg != p.leg:
        raise ValueError(f"arm {arm['id']}: targets() changed the leg to {t.leg!r}")
    if t.two_leg:
        raise ValueError(f"arm {arm['id']}: two-leg targets in a one-leg group")
    return t.wants


def _targets2(arm, p):
    """A two-leg arm's `(W, 2, T)` target panel on one panel (`p` is that model's panel)."""
    t = call_targets(arm["mod"], p, arm)
    if t.leg != p.leg:
        raise ValueError(f"arm {arm['id']}: targets() changed the leg to {t.leg!r}")
    if not t.two_leg:
        raise ValueError(f"arm {arm['id']}: single-leg targets in a two-leg group")
    return np.ascontiguousarray(np.stack([np.atleast_2d(t.wants), np.atleast_2d(t.wants_b)],
                                         axis=1))


def two_leg_series(panels, leg, leg_b, fin, i0, i1):
    """One model's `(opens, closes)` stacked `(W, 2, T)` for a two-leg group, or `NotApplicable`.

    Leg A is the group's `leg` and leg B is its declared `leg_b` -- a panel leg or a registered
    extra series (`MarketData.leg_series`). The leg-B availability rule is the framework's
    (`leg_b_usable`): a series that does not reach every session the engine reads refuses the
    window. Inside a bootstrap chunk the check is about the *drawn source* sessions (the chunk
    replays an extra series with its returns), while the reason names the chunk's own calendar.
    """
    p = panels[(leg, fin)]
    ok, why = p.leg_b_usable(leg_b, i0, i1)
    if not ok:
        raise NotApplicable(why)
    oa, ca = p.legs[leg]
    ob, cb = p.leg_series(leg_b)
    return (np.ascontiguousarray(np.stack([np.atleast_2d(oa), np.atleast_2d(ob)], axis=1)),
            np.ascontiguousarray(np.stack([np.atleast_2d(ca), np.atleast_2d(cb)], axis=1)))


# ----------------------------------------------------------------------------- OOS pooled bootstrap
def _oos_market_panels(days, o, c, y, start, pos, legs, m_cache, key):
    """`boot_oos_market`'s reconstruction for one market: pool `arange(start, len(days))`."""
    idx = start + pos
    r_on = o[idx] / c[idx - 1]
    r_id = c[idx] / o[idx]
    P = idx.shape[0]
    cols = _extra_index(idx)
    cc = np.concatenate([np.ones((P, 1)), np.cumprod(r_on * r_id, axis=1)], axis=1)
    oo = np.concatenate([np.ones((P, 1)), cc[:, :-1] * r_on], axis=1)
    yy = y[idx - 1]
    yyp = np.concatenate([yy, yy[:, -1:]], axis=1)
    for leg in legs:
        m_cache[leg] = panel(
            days,
            price=cc,
            div=np.zeros_like(cc),
            rate=yyp,
            o2=oo,
            c2=cc,
            d2=np.zeros_like(cc),
            fin=EXTRA_FIN,
            exp_q=EXP_Q,
            leg=leg,
            window="oos_boot",
            meta={"market": key, "fin": EXTRA_FIN, "leg": leg},
            extra_days=days,
            extra_index=cols,
            extra_window=key,
            # the chunk's columns are the drawn *source* sessions of the market's own calendar
            # (the panel's `days` is that market's whole session list, so it cannot label them)
            col_days=np.asarray(days)[cols[0]],
        )
    return m_cache


def oos_pooled(arms, markets=("M1", "M2", "M3", "M4", "M5"), per_market=OOS_PATHS_PER_MARKET,
               seed=SEED, block=BLOCK, m=fe_sim.DEFAULT_M, cash_yield="none", rule_index=False,
               quiet=True):
    """The pooled OOS bootstrap (G8): block-`block` over each market, one shared rng.

    The draw replays `band_h2_combo_v1.screen.boot_oos_market` exactly (`OOS_CHUNK` = 500, the
    published chunked rng calls, pool `arange(start, len(days))`), so the control reproduces that
    function's per-path CAGRs bit for bit; the calendar is each market's own real session list
    with `i0 = START`, `i1 = OOS_BOOT_L - 1`, exactly as the published run did.
    """
    rng = np.random.default_rng(seed)
    ids = [a["id"] for a in arms]
    groups = {}
    for a in arms:
        groups.setdefault((a["leg"], a.get("leg_b")), []).append(a)
    legs = sorted({g[0] for g in groups})
    leg_of = {a["id"]: (a["leg"], a.get("leg_b")) for a in arms}
    per = {g: {k: {a["id"]: [] for a in g_arms} for k in markets} for g, g_arms in groups.items()}
    for key in markets:
        days, o, c, y, start = fe_data.load_market(key)
        npool = len(days) - start
        env = windows.usd_env(days, 253, OOS_BOOT_L, cash_yield)
        for c0 in range(0, per_market, OOS_CHUNK):
            P = min(OOS_CHUNK, per_market - c0)
            pos = np.empty((P, OOS_BOOT_L), dtype=np.int64)
            pos[:, 0] = rng.integers(0, npool, P)
            jump = rng.random((P, OOS_BOOT_L)) < 1 / block
            fresh = rng.integers(0, npool, (P, OOS_BOOT_L))
            for t in range(1, OOS_BOOT_L):
                pos[:, t] = np.where(jump[:, t], fresh[:, t], (pos[:, t - 1] + 1) % npool)
            panels = _oos_market_panels(days, o, c, y, start, pos, tuple(legs), {}, key)
            for (leg, leg_b), arm_list in groups.items():
                p = panels[leg]
                if leg_b is not None:
                    ok, why = p.leg_b_usable(leg_b, 253, OOS_BOOT_L)
                    if not ok:
                        raise NotApplicable(why)
                    oa, ca = p.legs[leg]
                    ob, cb = p.leg_series(leg_b)
                    ob2 = np.ascontiguousarray(np.stack([np.atleast_2d(oa), np.atleast_2d(ob)], axis=1))
                    cb2 = np.ascontiguousarray(np.stack([np.atleast_2d(ca), np.atleast_2d(cb)], axis=1))
                    wq2 = np.ascontiguousarray(np.stack([_targets2(a, p) for a in arm_list]))
                    band2, usd2, defer2 = _rule_arrays(arm_list)
                    out = np.empty((len(arm_list), P), dtype=np.float64)
                    fe2_boot._pt2(
                        ob2, cb2, wq2, env.fx, env.upm, env.mth_of, env.yr_of, env.scale_y,
                        env.dec_tail, env.pay_ptr, env.pay_yr, env.pay_part, 1.0, 253, OOS_BOOT_L,
                        fe_sim.COST_PER_SIDE * m, band2, usd2, defer2, rule_index, env.Y0,
                        env.M_lo, env.cash_rate, DBG0, out,
                    )
                    for ai, arm in enumerate(arm_list):
                        per[(leg, leg_b)][key][arm["id"]].append(out[ai])
                    continue
                wq = np.stack([_targets(a, p) for a in arm_list])
                band, usd, defer = _rule_arrays(arm_list)
                out = np.empty((len(arm_list), P), dtype=np.float64)
                _pt(
                    np.ascontiguousarray(p.legs[leg][0]), np.ascontiguousarray(p.legs[leg][1]),
                    np.ascontiguousarray(wq), env.fx, env.upm, env.mth_of, env.yr_of, env.scale_y,
                    env.dec_tail, env.pay_ptr, env.pay_yr, env.pay_part, 1.0, 253, OOS_BOOT_L,
                    fe_sim.COST_PER_SIDE * m, band, usd, defer, rule_index, env.Y0, env.M_lo,
                    env.cash_rate, DBG0, out,
                )
                for ai, arm in enumerate(arm_list):
                    per[(leg, leg_b)][key][arm["id"]].append(out[ai])
        if not quiet:
            print(f"  oos_boot {key} {per_market} paths", flush=True)
    per_market_out = {k: {} for k in markets}
    pooled = {}
    for g in groups:
        for a in ids:
            if leg_of[a] != g:
                continue
            pooled[a] = np.concatenate([np.concatenate(per[g][k][a]) for k in markets])
            for k in markets:
                per_market_out[k][a] = np.concatenate(per[g][k][a])
    return {
        "per_market": per_market_out,
        "pooled": pooled,
        "params": {"seed": seed, "block": block, "per_market": per_market, "m": m,
                   "markets": list(markets), "cash_yield": cash_yield, "arms": ids},
    }


# ----------------------------------------------------------------------------- statistics
def delta_stats(d, resamples=2000, seed=20261016):
    """The published Δ cells plus the bootstrap p-value of the mean.

    `d` is the per-path difference (arm − control) of the CAGRs on the *same* resampled paths --
    or, for a *level* cell (`boot_level`, e.g. the control's own paths), the per-path CAGRs
    themselves, in which case `cagr_median` is the published `cagr_median` cell.
    `p_mean` is the percentile-bootstrap one-sided p-value of H0: E[Δ] ≤ 0: the share of
    second-level resamples of the `len(d)` path values whose mean is ≤ 0.
    """
    d = np.asarray(d, dtype=np.float64)
    n = d.size
    rng = np.random.default_rng(seed)
    means = d[rng.integers(0, n, size=(resamples, n))].mean(axis=1)
    return {
        "cagr_median": float(np.median(d)),
        "median_delta_pp": float(np.median(d) * 100),
        "mean_delta_pp": float(np.mean(d) * 100),
        "share_gt_0": float(np.mean(d > 0)),
        "p05_delta_pp": float(np.percentile(d, 5) * 100),
        "p95_delta_pp": float(np.percentile(d, 95) * 100),
        "p_mean": float(np.mean(means <= 0.0)),
        "n_paths": int(n),
    }


__all__ = [
    "BootWindow",
    "DEFAULT_CHUNK",
    "OOS_BOOT_L",
    "OOS_CHUNK",
    "OOS_PATHS_PER_MARKET",
    "TAX_MODES",
    "boot",
    "boot_panels",
    "delta_stats",
    "oos_pooled",
]
