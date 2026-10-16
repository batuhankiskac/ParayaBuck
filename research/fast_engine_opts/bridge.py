"""bridge -- what `batch_screen` calls: the option bundle of an arm, and the two dispatchers.

An option is requested the way the framework already requests execution variants: **the arm's
`rule`**. A plugin registers a rule with `batch_screen.rules.register_rule(name, band, usd_rule,
defer, opts={...})` at import time and sets `"rule": name` in the arm's spec; `opts` carries the
option values (any `fe_opts.Opts` field) plus the two *bridge* fields that are properties of the
window rather than of the plugin:

    margin_spread   the annual margin rate over the window's own DTB3 column  (IC39)
    wht             the dividend withholding rate of the leg's construction    (IC56)

`batch_screen` never looks inside `opts`: it hands the arm (and, for a bootstrap group, the group's
panel) to `prepare_panel` / `simulate_one` / `chunk` / `pt` here, and the kernel is `sim_one_opts`.

`prepare_panel` rebuilds a panel when the arm asks for something the panel's own construction does
not have (net dividends, the `2x_spy` leg); with no such request the panel object is returned
unchanged, which is what keeps an options-off run bit-identical to the framework's.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from research.fast_engine import fe_signals
from research.fast_engine.fe_sim import DEFAULT_M

from . import fe_opts_boot
from .fe_opts import FLOAT_OPTS, INT_OPTS, NF, NI, Opts, session_fold
from .fe_opts_calendar import flags as early_flags
from .fe_opts_legs import (LEG_SPY, chunk_raw_panel, chunk_triple, clone_leg,
                           main_spy_triple, repanel, select_leg)
from .fe_opts_sim import simulate_opts

#: the `opts` keys that are *not* kernel scalars: the bridge consumes them itself
BRIDGE_FIELDS = ("margin_spread", "wht")
KERNEL_FIELDS = tuple(FLOAT_OPTS) + tuple(INT_OPTS)


def split_opts(raw) -> tuple:
    """`(Opts, bridge knobs)` of a rule's `opts` dict. Unknown keys are an error, not a silent no-op."""
    raw = dict(raw or {})
    unknown = sorted(set(raw) - set(KERNEL_FIELDS) - set(BRIDGE_FIELDS))
    if unknown:
        raise ValueError(f"unknown option {unknown}; kernel options are {list(KERNEL_FIELDS)} "
                         f"and the bridge's own are {list(BRIDGE_FIELDS)}")
    o = Opts(**{k: raw[k] for k in KERNEL_FIELDS if k in raw})
    knobs = {k: raw[k] for k in BRIDGE_FIELDS if k in raw}
    return o, knobs


def arm_opts(arm) -> tuple:
    """`(Opts, knobs)` of an arm dict (its resolved rule's `opts`, empty when the rule has none)."""
    return split_opts(arm.get("opts"))


def panel_key(arm) -> tuple:
    """The bootstrap grouping key an arm adds beyond `(leg, leg_b)`: the panel's own construction."""
    _o, knobs = arm_opts(arm)
    return (float(knobs.get("wht", 0.0) or 0.0),)


def prepare_panel(p, arm, win=None, pos=None):
    """The panel an arm actually runs on: `p` itself unless the arm asks for more.

    `wht` rebuilds the legs from net dividends (`fe_opts_legs.repanel` on a window, and
    `fe_opts_legs.chunk_raw_panel` inside a bootstrap, where the framework's chunk leg already
    embeds the gross dividend and the netting therefore has to happen upstream of the
    reconstruction); `leg == "2x_spy"` adds the S&P 500 2x leg -- from the drawn chunk's own SPY
    sessions inside a bootstrap (`pos`), from the main window's SPY column otherwise, and as an
    alias of the window's own 2x leg on a window that has no SPY series (an OOS index or one of the
    21 ETFs, which is IC40's documented degeneration there). The panel a caller already holds is
    never mutated: the result is either `p` itself (no request) or a new object.
    """
    _o, knobs = arm_opts(arm)
    wht = float(knobs.get("wht", 0.0) or 0.0)
    want_spy = arm.get("leg") == LEG_SPY
    if not wht and not want_spy:
        return p
    chunk = pos is not None and p.window == "boot"
    triple = None
    if want_spy:
        if chunk:
            triple = chunk_triple(pos, "SPY")
        else:
            try:
                triple = main_spy_triple(p.days)
            except ValueError:
                triple = None
    if chunk and wht:
        p = chunk_raw_panel(p, pos, wht=wht,
                            extra=None if triple is None else {LEG_SPY: triple})
    elif wht or triple is not None:
        p = repanel(p, wht=wht, extra=None if triple is None else {LEG_SPY: triple})
    if want_spy:
        if LEG_SPY not in p.legs:
            p = clone_leg(p)
            p.legs[LEG_SPY] = p.legs["2x"]
        p = select_leg(p, LEG_SPY)
    return p


@dataclass
class Bundle:
    """The per-arm option arrays of one panel: `(n_arms, ...)`, empty axes switch an option off."""

    optf: np.ndarray
    opti: np.ndarray
    fill: np.ndarray
    mrate: np.ndarray
    early: np.ndarray
    wvol: np.ndarray

    def row(self, a) -> dict:
        """The 1-D arrays of arm `a` (the one-path case, `simulate_opts`)."""
        return {"fill": self.fill[a], "mrate": self.mrate[a], "early": self.early[a],
                "wvol": self.wvol[a, 0]}


def bundle_for(arm_list, panel) -> Bundle | None:
    """The option bundle of a group of arms on one panel, or None when no arm asks for anything."""
    parsed = [arm_opts(a) for a in arm_list]
    if all(o.is_off() and not k for o, k in parsed):
        return None
    n = len(arm_list)
    # the option arrays are indexed by *session* like the market arrays (`wq[i - 1]`, `early[i]`),
    # and a bootstrap chunk's legs are one column wider than its calendar (`win.dates`), so the
    # width is the panel's own: `panel.vol` follows the leg's width exactly.
    T = int(panel.vol.shape[-1])
    P = panel.P
    optf = np.zeros((n, NF), dtype=np.float64)
    opti = np.zeros((n, NI), dtype=np.int64)
    fill = np.zeros((n, T), dtype=np.float64)
    mrate = np.zeros((n, T), dtype=np.float64)
    early = np.zeros((n, T), dtype=np.bool_)
    wvol = np.zeros((n, P, T), dtype=np.float64)
    need_fill = need_mrate = need_early = need_vol = False
    for a, (o, knobs) in enumerate(parsed):
        optf[a], opti[a] = o.pack()
        if o.fill_mode == 2:
            need_fill = True
        if knobs.get("margin_spread") is not None:
            need_mrate = True
        if o.early_mode:
            need_early = True
        if o.dd_start > 0.0:
            need_vol = True
            wvol[a] = panel.vol
    if need_early:
        f = early_flags(panel.days)
        early[:, : f.shape[0]] = f[None, :]
    if need_mrate:
        rate = np.atleast_2d(np.asarray(panel.rate, dtype=np.float64))
        if rate.shape[0] != 1 and rate.shape[0] != P:
            raise ValueError("panel.rate does not match the panel's path count")
        if rate.shape[1] < len(panel.days):
            raise ValueError("panel.rate must have one entry per session")
        for a, (_o, knobs) in enumerate(parsed):
            spread = knobs.get("margin_spread")
            if spread is None:
                continue
            fold = session_fold(panel.days, rate[:, : len(panel.days)] + float(spread))
            mrate[a, : len(panel.days)] = fold[0]
    return Bundle(
        optf=optf,
        opti=opti,
        fill=fill if need_fill else np.zeros((n, 0)),
        mrate=mrate if need_mrate else np.zeros((n, 0)),
        early=early if need_early else np.zeros((n, 0), dtype=np.bool_),
        wvol=wvol if need_vol else np.zeros((n, P, 0)),
    )


# ----------------------------------------------------------------------------- dispatchers
def simulate_one(arm, t, data, env, capital, i0, i1, mode, m=DEFAULT_M, rule_index=True,
                 trace=False, dbg=None):
    """One arm on one window -- `stages.run_one`'s single-leg branch, with the options."""
    if t.two_leg:
        raise ValueError("the option kernel is single-leg; an arm cannot declare both")
    b = bundle_for([arm], data)
    oa, ca = data.legs[t.leg]
    row = b.row(0)
    return simulate_opts(oa, ca, t.wants, env, capital, i0, i1, arm=arm["rule"], mode=mode, m=m,
                         rule_index=rule_index, trace=trace, dbg=dbg, opts=arm_opts(arm)[0],
                         fill=row["fill"], mrate=row["mrate"], early=row["early"],
                         wvol=row["wvol"])


def chunk(bundle_b, bundle_c, *args):
    """`batch_screen.boot._chunk`'s argument list, with the options of both financing models."""
    return fe_opts_boot.chunk_opts(
        *args,
        bundle_b.optf, bundle_b.opti, bundle_b.fill, bundle_b.mrate, bundle_b.early, bundle_b.wvol,
        bundle_c.optf, bundle_c.opti, bundle_c.fill, bundle_c.mrate, bundle_c.early, bundle_c.wvol,
    )


def pt(bundle, *args):
    """`batch_screen.boot._pt`'s argument list, with the options."""
    return fe_opts_boot.pt_opts(
        *args, bundle.optf, bundle.opti, bundle.fill, bundle.mrate, bundle.early, bundle.wvol,
    )


def base_leg(leg: str) -> str:
    """The panel leg an option leg is built from (`2x_spy` -> `2x`; everything else unchanged).

    A window's panels are always constructed for the two engine legs; an option leg is added on
    top of one of them (`prepare_panel`), so the *panel construction* and the bootstrap's group
    looping use the base name while the arms keep the option leg's name.
    """
    return "2x" if leg == LEG_SPY else leg


def leg_vol_panel(closes):
    """`fe_signals.vol_n` of a leg's close panel: the governor's sizing input (IC49)."""
    return fe_signals.vol_n(np.atleast_2d(np.asarray(closes, dtype=np.float64)))


__all__ = ["BRIDGE_FIELDS", "Bundle", "KERNEL_FIELDS", "arm_opts", "bundle_for", "chunk",
           "leg_vol_panel", "panel_key", "prepare_panel", "pt", "simulate_one", "split_opts"]
