"""bridge3 -- what `batch_screen` calls for a multi-leg (`"legs": [...]`) or margin arm.

An arm needs the N-leg kernel (`fe3_sim`) when it declares **more than two instruments** or a
**margin loan** on top of two:

    ARMS = {"A": {"rule": "K", "legs": ["2x", "xlu", "xlp"], "start": "auto", ...}}
    ARMS = {"C": {"rule": "margin_k", "legs": ["2x", "gld"], "margin_spread": 0.015, ...}}

`resolve_arm` records the declaration as `arm["legs"]` (a tuple of instrument keys, leg A first) and
`arm["margin_spread"]`; `stages.run_one`, `boot.boot` and `boot.oos_pooled` route such an arm here,
where the per-leg panels, the target panels and the margin schedule are built and `fe3_sim.simulate3`
is called. An arm that declares neither takes the published code path unchanged.

The instrument keys are the panel legs (`2x`/`1x`/`2x_spy`) and the registered extra series
(`xlu`, `xlf`, `gld`, `shy`, ..., `api.leg_b_keys()`); leg A is always a panel leg. `2x_spy` (IC40/
IC41's S&P 500 2x leg) is built on demand from the window's own SPY column (`fe_opts_legs`), and on
a window without an SPY series it degenerates to that window's own `2x` leg -- the same convention
`fast_engine_opts.bridge.prepare_panel` uses for IC40.

The margin schedule is `fast_engine_opts`' own: `session_fold(days, DTB3 + margin_spread)` over the
window's rate column, one entry per session, charged on a negative cash balance by `fe3_sim`.
"""

from __future__ import annotations

import numpy as np

from research.fast_engine import fe_sim
from research.fast_engine.fe_config import FIN
from research.fast_engine.fe_tax import MODE_PRE_TAX
from research.fast_engine_opts.fe_opts import session_fold
from research.fast_engine_opts.fe_opts_legs import (
    LEG_SPY,
    chunk_triple,
    clone_leg,
    main_spy_triple,
    repanel,
)

from . import fe3_sim

#: the panel leg an option leg is built from (`2x_spy` -> `2x`; everything else unchanged)
_BASE = {LEG_SPY: "2x"}
DBG0 = np.zeros((0, 6), dtype=np.float64)


def base_leg(leg: str) -> str:
    return _BASE.get(leg, leg)


def arm_legs(arm) -> tuple:
    """The arm's instruments, leg A first (1..4 keys). Prefers the resolved `arm["legs"]`."""
    legs = arm.get("legs")
    if legs:
        return tuple(legs)
    out = [arm.get("leg") or "2x"]
    if arm.get("leg_b"):
        out.append(arm["leg_b"])
    return tuple(out)


def margin_spread(arm):
    """The arm's annual margin spread (`None` = no loan), from its resolved opts."""
    v = arm.get("margin_spread")
    if v is None:
        v = (arm.get("opts") or {}).get("margin_spread")
    return None if v is None else float(v)


def route_cap(arm) -> float:
    """The arm's downsize-router weight cap (`0.0` = off), from its resolved fields."""
    v = arm.get("route_cap_pp")
    if v is None:
        v = (arm.get("opts") or {}).get("route_cap_pp")
    return 0.0 if v is None else float(v) / 100.0


def route_score(arm) -> int:
    """The router's score: 1 (`indexed`) or 0 (`usd`) -- `fe3_sim.ROUTE_SCORES`."""
    name = arm.get("route_score") or (arm.get("opts") or {}).get("route_score") or "indexed"
    return int(fe3_sim.ROUTE_SCORES[name])


def needs_kernel3(arm) -> bool:
    """True when the arm must run on `fe3_sim`: > 2 legs, a margin loan on two, a `2x_spy` leg
    beside another instrument, or a gain-aware downsize router (a cross-leg fill decision).

    A *single*-leg arm keeps the published single-leg paths: a `2x_spy` leg A is IC40's
    `bridge.prepare_panel`, and a single-leg `margin_spread` (IC39) is the option kernel's own
    `mrate`, which `fast_engine_opts.bridge.bundle_for` builds. Only a second instrument makes the
    loan this kernel's job.
    """
    legs = arm_legs(arm)
    if len(legs) >= 3 or len(set(legs)) != len(legs):
        return True
    if len(legs) == 2 and (margin_spread(arm) is not None or LEG_SPY in legs
                           or route_cap(arm) > 0.0):
        return True
    return False


# ----------------------------------------------------------------------------- panels
def _spy_triple(p, pos):
    """The S&P 500 raw triple for the panel (`None` when the window has no SPY series)."""
    if pos is not None and p.window == "boot":
        return chunk_triple(pos, "SPY")
    try:
        if np.asarray(p.days).size == p.T:
            return main_spy_triple(p.days)
    except (ValueError, KeyError):
        return None
    return None


def prepare(p, legs, pos=None):
    """A panel carrying every leg of `legs`: `2x_spy` is added (or degenerated) on demand.

    The panel a caller already holds is never mutated. On a window without an SPY series
    (`2x_spy` is the window's own index, e.g. an OOS market or an ETF), the option leg degenerates
    to that window's own `2x` leg -- `fast_engine_opts.bridge.prepare_panel`'s documented IC40
    convention.
    """
    if LEG_SPY not in legs or LEG_SPY in p.legs:
        return p
    triple = _spy_triple(p, pos)
    if triple is not None:
        return repanel(p, extra={LEG_SPY: triple})
    out = clone_leg(p)
    out.legs[LEG_SPY] = out.legs["2x"]
    return out


def leg_series(p, legs, i0, i1):
    """One model's `(opens, closes)` stacked `(P, N, T)` for the declared legs.

    A leg whose series does not reach every session the engine reads (the framework's
    `leg_b_usable` rule) makes the window `NotApplicable`.
    """
    from research.batch_screen import api

    ops, cls = [], []
    for key in legs:
        ok, why = p.leg_b_usable(key, i0, i1)
        if not ok:
            raise api.NotApplicable(why)
        o, c = p.leg_series(key)
        ops.append(np.atleast_2d(np.asarray(o, dtype=np.float64)))
        cls.append(np.atleast_2d(np.asarray(c, dtype=np.float64)))
    return (np.ascontiguousarray(np.stack(ops, axis=1)),
            np.ascontiguousarray(np.stack(cls, axis=1)))


def wants_panel(t, want_shape):
    """The `(P, N, T)` target panel of a `Targets` (leg order as declared)."""
    rows = [np.asarray(w, dtype=np.float64).reshape(want_shape) for _leg, w in t.leg_wants()]
    return np.ascontiguousarray(np.stack(rows, axis=1))


# ----------------------------------------------------------------------------- margin
def margin_rate(arm, panel):
    """The `(T,)` per-session margin rate of an arm on one panel, or `None` (no loan)."""
    spread = margin_spread(arm)
    if spread is None:
        return None
    days = np.asarray(panel.days)
    T = int(panel.T)
    W = min(T, days.size)
    rate = np.atleast_2d(np.asarray(panel.rate, dtype=np.float64))
    if rate.shape[1] < W:
        raise ValueError("panel.rate does not carry one entry per session")
    mr = np.zeros(T, dtype=np.float64)
    mr[:W] = session_fold(list(days[:W]), rate[0, :W] + spread)
    return mr


def mrate_panel(arm_list, panel):
    """The `(n_arms, T)` margin-rate bundle of a bootstrap group (a zero-width row is "off")."""
    rows = [margin_rate(a, panel) for a in arm_list]
    if all(r is None for r in rows):
        return np.zeros((len(arm_list), 0), dtype=np.float64)
    T = int(panel.T)
    out = np.zeros((len(arm_list), T), dtype=np.float64)
    for i, r in enumerate(rows):
        if r is not None:
            out[i] = r
    return out


# ----------------------------------------------------------------------------- one run
def simulate_multi(arm, t, data, env, capital, i0, i1, mode="pre_tax", m=fe_sim.DEFAULT_M,
                   rule_index=True, trace=False):
    """One N-leg run on one window: `fe3_sim.simulate3` with the arm's panels and margin rate."""
    if data.P != 1:
        raise ValueError("simulate_multi runs one path; the bootstrap uses bridge3.boot_chunk")
    legs = tuple(k for k, _w in t.leg_wants())
    data = prepare(data, legs)
    op, cl = leg_series(data, legs, i0, i1)
    wq = wants_panel(t, data.price.shape)
    return fe3_sim.simulate3(op[0], cl[0], wq[0], env, capital, i0, i1, arm=arm["rule"], mode=mode,
                             m=m, rule_index=rule_index, trace=trace,
                             mrate=margin_rate(arm, data), route_cap=route_cap(arm),
                             route_score=route_score(arm))


# ----------------------------------------------------------------------------- bootstrap
def _targets_n(arm, p):
    """A multi-leg arm's `(W, N, T)` target panel on one chunk panel."""
    from research.batch_screen.api import call_targets

    t = call_targets(arm["mod"], p, arm)
    if t.leg != arm["leg"]:
        raise ValueError(f"arm {arm['id']}: targets() changed leg A to {t.leg!r}")
    return wants_panel(t, p.price.shape)


def boot_chunk(arm_list, panels, legs, win, env, cap_at, cost, mode_ids, rule_index, p0, W,
               n_pre, n_tax, pos, pre, tax, capitals, modes):
    """One bootstrap chunk of a multi-leg group on the chunk's already drawn panels.

    `panels` is the dict `boot.boot_panels` returns for the base legs; the panel is taken from
    `base_leg(legs[0])` (every panel carries `2x`/`1x`) and option legs are added on top.
    """
    from . import fe3_boot

    pb = prepare(panels[(base_leg(legs[0]), FIN["base"])], legs, pos)
    pc = prepare(panels[(base_leg(legs[0]), FIN["calibrated"])], legs, pos)
    ob, cb = leg_series(pb, legs, win.i0, win.i1)
    oc, cc = leg_series(pc, legs, win.i0, win.i1)
    wb = np.ascontiguousarray(np.stack([_targets_n(a, pb) for a in arm_list]))
    wc = np.ascontiguousarray(np.stack([_targets_n(a, pc) for a in arm_list]))
    band = np.array([fe_sim.RULE[a["rule"]]["band"] for a in arm_list], dtype=np.bool_)
    usd = np.array([fe_sim.RULE[a["rule"]]["usd_rule"] for a in arm_list], dtype=np.bool_)
    defer = np.array([fe_sim.RULE[a["rule"]]["defer"] for a in arm_list], dtype=np.bool_)
    mb = mrate_panel(arm_list, pb)
    mc = mrate_panel(arm_list, pc)
    route_caps = np.array([route_cap(a) for a in arm_list], dtype=np.float64)
    route_scores = np.array([route_score(a) for a in arm_list], dtype=np.int64)
    out_pre = np.empty((2, len(arm_list), W), dtype=np.float64)
    out_at = np.empty((len(capitals), len(modes), len(arm_list), W), dtype=np.float64)
    fe3_boot._boot_chunk3(
        ob, cb, wb, oc, cc, wc, env.fx, env.upm, env.mth_of, env.yr_of, env.scale_y, env.dec_tail,
        env.pay_ptr, env.pay_yr, env.pay_part, 100_000.0, cap_at, win.i0, win.i1, cost, win.yrs,
        env.Y0, env.M_lo, band, usd, defer, rule_index, mode_ids, env.cash_rate, mb, mc,
        route_caps, route_scores, p0, n_pre, n_tax, DBG0, out_pre, out_at,
    )
    npre = max(0, min(W, n_pre - p0))
    ntax = max(0, min(W, n_tax - p0))
    for ai, arm in enumerate(arm_list):
        for bi, md in enumerate(("base", "calibrated")):
            if npre:
                pre[md][arm["id"]][p0 : p0 + npre] = out_pre[bi, ai, :npre]
        for ci, cl in enumerate(capitals):
            for mi, md in enumerate(modes):
                if ntax:
                    tax[cl][md][arm["id"]][p0 : p0 + ntax] = out_at[ci, mi, ai, :ntax]


def pt_group(arm_list, panel, legs, env, i0, i1, cost, rule_index, capital, out):
    """The pooled-OOS inner loop for a multi-leg group (`fe3_boot._pt3`)."""
    from . import fe3_boot

    panel = prepare(panel, legs)
    ob, cb = leg_series(panel, legs, i0, i1)
    wq = np.ascontiguousarray(np.stack([_targets_n(a, panel) for a in arm_list]))
    band = np.array([fe_sim.RULE[a["rule"]]["band"] for a in arm_list], dtype=np.bool_)
    usd = np.array([fe_sim.RULE[a["rule"]]["usd_rule"] for a in arm_list], dtype=np.bool_)
    defer = np.array([fe_sim.RULE[a["rule"]]["defer"] for a in arm_list], dtype=np.bool_)
    mrate = mrate_panel(arm_list, panel)
    route_caps = np.array([route_cap(a) for a in arm_list], dtype=np.float64)
    route_scores = np.array([route_score(a) for a in arm_list], dtype=np.int64)
    fe3_boot._pt3(ob, cb, wq, env.fx, env.upm, env.mth_of, env.yr_of, env.scale_y, env.dec_tail,
                  env.pay_ptr, env.pay_yr, env.pay_part, capital, i0, i1, cost, band, usd, defer,
                  rule_index, env.Y0, env.M_lo, env.cash_rate, mrate, route_caps, route_scores,
                  DBG0, out)


__all__ = [
    "arm_legs",
    "base_leg",
    "boot_chunk",
    "leg_series",
    "margin_rate",
    "margin_spread",
    "mrate_panel",
    "needs_kernel3",
    "prepare",
    "pt_group",
    "route_cap",
    "route_score",
    "simulate_multi",
    "wants_panel",
]
