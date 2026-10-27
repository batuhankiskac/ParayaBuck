"""fe_opts_legs -- the two panel mechanisms: dividend withholding (IC56) and a second underlying.

Both are *leg construction*, not kernel logic: they change the price series the engine holds, so
they are built here from the panel's raw input triple (`MarketData.raw`, recorded by
`batch_screen.api.panel`) instead of inside the kernel.

  `repanel(p, wht=...)`   rebuild a panel whose legs are built from dividends net of `wht`
                          (the engine's own 2x recurrence, one extra multiply per session).
  `repanel(p, extra=...)` add legs built from another underlying's raw triple -- the S&P 500 leg of
                          IC40 -- under a new name (`2x_spy`), leaving the Nasdaq legs untouched.

The withholding drag is `2 * wht * div[t] / close[t-1]` of the daily return, applied before the
2x recurrence, which is what netting the dividend at the source means (the fund receives the net
dividend; its swap financing is unaffected). A price-only window (`div == 0`, the OOS indices and
the 21 ETFs) is unchanged, as are the bootstrap chunks' net-of-nothing paths when `wht == 0`.
"""

from __future__ import annotations

import copy

import numpy as np

from research.batch_screen import api
from research.fast_engine import fe_data, fe_signals
from research.fast_engine.fe_config import EXP_Q

__all__ = ["LEG_SPY", "chunk_triple", "clone_leg", "main_spy_triple", "repanel", "select_leg"]

#: the name of the S&P 500 2x leg IC40 holds
LEG_SPY = "2x_spy"


def repanel(p, *, wht=0.0, fin=None, exp_q=None, extra=None):
    """A panel rebuilt from its recorded raw triple: dividends net of `wht`, extra legs added.

    `extra` is `{name: (open, close, div)}` of another underlying (aligned to the panel's own
    columns), built with the same `fin`/`exp_q` as the panel. With `wht == 0` and no `extra` the
    original panel is returned untouched, so an arm that does not ask for either keeps the
    framework's own arrays (which is what makes an options-off run bit-identical).
    """
    if (not wht or wht <= 0.0) and not extra:
        return p
    raw2, raw1 = p.raw
    if raw2 is None:
        raise ValueError("this panel was built without its raw triple (api.panel without raw=)")
    o2, c2, d2 = (np.atleast_2d(np.asarray(x, dtype=np.float64)).copy() for x in raw2)
    o1, c1, d1 = (np.atleast_2d(np.asarray(x, dtype=np.float64)).copy() for x in raw1)
    if wht:
        d2 = d2 * (1.0 - wht)
        d1 = d1 * (1.0 - wht)
    fin = p.meta.get("fin", 0.0) if fin is None else fin
    exp_q = p.meta.get("exp_q", EXP_Q) if exp_q is None else exp_q
    out = api.panel(
        p.days,
        price=p.price,
        div=(p.div * (1.0 - wht)) if wht else p.div,
        rate=p.rate,
        o2=o2,
        c2=c2,
        d2=d2,
        o1=o1,
        c1=c1,
        d1=d1,
        fin=fin,
        exp_q=exp_q,
        leg=p.leg,
        window=p.window,
        high=p.high,
        low=p.low,
        meta={**p.meta, "wht": float(wht)},
        extra_days=p.extra_days,
        extra_index=p.extra_index,
        extra_window=p.extra_window,
        col_days=p.col_days,
    )
    out.raw = ((o2, c2, d2), (o1, c1, d1))  # what *this* panel was built from
    for name, triple in (extra or {}).items():
        eo, ec, ed = (np.atleast_2d(np.asarray(x, dtype=np.float64)) for x in triple)
        out.legs[name] = _leg_of(eo, ec, ed, fin, exp_q)
    return out


def _leg_of(o, c, d, fin, exp_q):
    """`fe_signals.synthetic_2x` on a raw triple: the leg's (open, close)."""
    rate = np.zeros_like(c)
    return fe_signals.synthetic_2x(o, c, d, rate + fin, exp_q)


def clone_leg(p):
    """A copy of the panel with its own `legs` dict, so adding a leg cannot touch the original."""
    out = copy.copy(p)
    out.legs = dict(p.legs)
    return out


def select_leg(p, name):
    """The same panel with `leg` = `name` (the leg must exist) and `vol` recomputed for it."""
    out = clone_leg(p)
    if name not in out.legs:
        raise KeyError(f"the panel has no leg {name!r}; it offers {sorted(out.legs)}")
    out.leg = name
    out.vol = fe_signals.vol_n(out.legs[name][1])
    return out


def chunk_triple(pos, sym="SPY"):
    """`(open, close, dividend)` of one instrument along a bootstrap chunk's drawn positions.

    The returned arrays carry the framework's reconstruction convention: one leading base column
    (the close of the session *before* the first drawn one, so the return into the first drawn
    session is that session's own return) followed by one column per drawn session. The level is
    the drawn sessions' **own** returns compounded forward -- column `t + 1` steps column `t` by
    the drawn session's `close / previous close`, whose previous close is *its own*
    (`c[idx - 1]`, exactly `boot_panels`' `prev = win.q[1][idx - 1]`) -- so a block jump (two
    unrelated sessions drawn back to back) does not span the calendar. Concatenating the drawn
    sessions' raw prices instead made the return across a jump the ratio of two unrelated
    sessions; for the 2x leg one such return below -50 % gives a gross factor `1 + 2r - cost` <= 0,
    the cumprod path flips sign and the leg's price (and the NAV, and the per-path CAGR) goes
    non-positive/NaN -- the IC56/IC40 bootstrap NaN of `docs/research/batch_bank_v3.md`. On a
    contiguous draw the level is the raw price path, so the triple is unchanged there up to
    floating-point rounding.
    """
    days, arr, _y = fe_data.load()
    o, c, d = arr[sym]
    idx = pos + 1
    prev = c[idx - 1]
    base = prev[:, :1]
    zero = np.zeros_like(base)
    # the synthetic level: base times the drawn sessions' own price-return cumprod
    lvl = np.concatenate([base, base * np.cumprod(c[idx] / prev, axis=1)], axis=1)
    o2 = np.concatenate([base, lvl[:, :-1] * o[idx] / prev], axis=1)
    c2 = lvl
    d2 = np.concatenate([zero, lvl[:, :-1] * d[idx] / prev], axis=1)
    return o2, c2, d2


def chunk_raw_panel(p, pos, wht=0.0, extra=None, sym="QQQ"):
    """One bootstrap chunk's panel rebuilt from the drawn *raw* triple of `sym`.

    The framework's chunk panel is built from a total-return path with a zero dividend column, so
    a withholding cannot be applied to it: netting has to happen upstream of the reconstruction.
    The rebuilt legs are the same series (the drawn sessions' returns unchanged, each stepped from
    its *own* previous close -- see `chunk_triple`) and the same width; the panel's
    `price`/`div`/`rate` and its extra-series context are carried over unchanged.
    """
    o2, c2, d2 = chunk_triple(pos, sym)
    if wht:
        d2 = d2 * (1.0 - wht)
    fin = p.meta.get("fin", 0.0)
    exp_q = p.meta.get("exp_q", EXP_Q)
    out = api.panel(
        p.days,
        price=p.price,
        div=(p.div * (1.0 - wht)) if wht else p.div,
        rate=p.rate,
        o2=o2,
        c2=c2,
        d2=d2,
        o1=o2,
        c1=c2,
        d1=d2,
        fin=fin,
        exp_q=exp_q,
        leg=p.leg,
        window=p.window,
        meta={**p.meta, "wht": float(wht)},
        extra_days=p.extra_days,
        extra_index=p.extra_index,
        extra_window=p.extra_window,
        col_days=p.col_days,
    )
    out.raw = ((o2, c2, d2), (o2, c2, d2))
    for name, triple in (extra or {}).items():
        eo, ec, ed = (np.atleast_2d(np.asarray(x, dtype=np.float64)) for x in triple)
        out.legs[name] = _leg_of(eo, ec, ed, fin, exp_q)
    return out


def window_triple(sym="SPY"):
    """`(open, close, dividend)` of one instrument over the main window's own session list."""
    days, arr, _y = fe_data.load()
    return arr[sym]


def main_spy_triple(days=None):
    """SPY's raw triple aligned to the main window (`days` must be the CSV's own list)."""
    d, arr, _y = fe_data.load()
    if days is not None and len(days) != len(d):
        raise ValueError("the main window's session list is not the CSV's own")
    return arr["SPY"]
