"""batch_screen.windows -- the market windows a plugin is screened on, as `MarketData` panels.

Every builder reproduces the campaign's own construction bit-for-bit (`fe_data` / `fe_study`), so
the control arm's numbers are the published ones; the only addition is the 1x leg and the panel
wrapper. Windows:

    main(spread)        QQQ main window, synthetic daily-reset 2x QLD* (base / calibrated)
    oos(key)            the six out-of-sample windows (^GSPC 1957-1999, M1-M5), calibrated 2x
    instrument(key)     one of the 21 quality-passing ETFs, calibrated 2x
    boot_panel(win,p)   a bootstrap chunk of the main window's QQQ (see boot.py)
"""

from __future__ import annotations

import bisect
from dataclasses import dataclass

import numpy as np

from research.fast_engine import fe_data, fe_signals, fe_sim, fe_study
from research.fast_engine.fe_config import (
    E2_START_YEARS,
    EXP_Q,
    EXTRA_FIN,
    START,
    TAX_WINDOWS,
)

from .api import DEFAULT_LEG, LEGS, NotApplicable, panel
from .rules import cash_rates

_BARS = {}
_UFE = {}
_ENV = {}

#: a reduced window shorter than this (calendar years, from the session before its first to its
#: last) is not evaluable at all: `arm_window` raises `NotApplicable`.
MIN_WINDOW_YEARS = 10.0

#: the bootstrap pool of a reduced window: the drawn session positions of the main window's own
#: return pool (`pos = session - 1`), restricted to the window. Same seed/block semantics, a
#: shorter pool (see `boot_pool`).

@dataclass(frozen=True)
class Window:
    """The main window an arm is evaluated on: its first measured session (`i0`) and a tag.

    `default` is the published main window (2000-03-09 -> 2026-09-29); any other tag is a
    *reduced* window an arm declared with `"start"` in its spec (see README §2a and the
    pre-registration's amendment): stage 1, stage 2 and the block bootstrap then all run on
    `[i0, last]`, and the control is re-simulated on the same window so every Δ stays paired.
    """

    i0: int = START
    tag: str = "default"
    declared: str | None = None
    inputs: tuple = ()

    @property
    def reduced(self) -> bool:
        return self.tag != "default"

    def as_dict(self) -> dict:
        return {"start": int(self.i0), "tag": self.tag, "declared": self.declared,
                "inputs": list(self.inputs)}

DEFAULT_WINDOW = Window()


def bars():
    """`fe_data.load()` once per process: (days, {"QQQ"/"SPY"/"QLD": (3, T)}, dtb3)."""
    if "v" not in _BARS:
        _BARS["v"] = fe_data.load()
    return _BARS["v"]


def ufe():
    """The YI-UFE month table, parsed once per process."""
    if "v" not in _UFE:
        _UFE["v"] = fe_data.load_ufe()
    return _UFE["v"]


def try_env(days, i0, i1, cash_yield="none"):
    """The TRY-indexed `RunEnv` of a window (the after-tax stages' market)."""
    rate = None if cash_yield == "none" else cash_rates(list(days))
    return fe_sim.RunEnv(days, fe_data.fx_at(days), ufe(), i0, i1, cash_rate=rate)


def usd_env(days, i0, i1, cash_yield="none"):
    """The R-USD `RunEnv` (fx = 1, flat YI-UFE) the OOS/instrument stages run in."""
    rate = None if cash_yield == "none" else cash_rates(list(days))
    return fe_sim.RunEnv(days, *fe_data.usd_market(days), i0, i1, cash_rate=rate)


# ----------------------------------------------------------------------------- main window
def main_panel(spread: float = 0.0, leg: str = DEFAULT_LEG) -> tuple:
    """The QQQ main window: (days, panel, i0, i1). `spread` is the financing model's spread."""
    days, arr, y = bars()
    q = arr["QQQ"]
    p = panel(
        days,
        price=q[1],
        div=q[2],
        rate=y,
        o2=q[0],
        c2=q[1],
        d2=q[2],
        fin=spread,
        exp_q=EXP_Q,
        leg=leg,
        window="main",
        meta={"model": "base" if spread == 0.0 else "calibrated", "fin": float(spread), "exp_q": EXP_Q},
    )
    return days, p, START, len(days) - 1


# ----------------------------------------------------------------------------- OOS / instrument
def oos_panel(key: str, leg: str = DEFAULT_LEG) -> tuple:
    """One OOS window: (days, panel, i0, i1) -- `fe_study.oos_window`'s construction."""
    if key == "GSPC_1957_1999":
        days, o, c, y, start = fe_data.load_gspc()
    else:
        days, o, c, y, start = fe_data.load_market(key)
    p = panel(
        days,
        price=c,
        div=np.zeros_like(c),
        rate=y,
        o2=o,
        c2=c,
        d2=np.zeros_like(c),
        fin=EXTRA_FIN,
        exp_q=EXP_Q,
        leg=leg,
        window=key,
        meta={"market": key, "fin": EXTRA_FIN},
    )
    return days, p, start, len(days) - 1


def instrument_panel(key: str, leg: str = DEFAULT_LEG) -> tuple:
    """One of the 21 quality-passing ETFs: (days, panel, high, low, i0, i1)."""
    days, o, h, lo, c, y = fe_data.load_instrument(key)
    p = panel(
        days,
        price=c,
        div=np.zeros_like(c),
        rate=y,
        o2=o,
        c2=c,
        d2=np.zeros_like(c),
        fin=EXTRA_FIN,
        exp_q=EXP_Q,
        leg=leg,
        window=key,
        high=h,
        low=lo,
        meta={"instrument": key, "fin": EXTRA_FIN},
    )
    return days, p, h, lo, START, len(days) - 1


def instrument_start(days, key: str) -> int:
    """The ETF windows' start: `fe_study.stage_e1`'s `max(warm-up, E1_RINDEX_START)`."""
    import bisect

    from research.fast_engine.fe_config import E1_RINDEX_START

    return max(START, bisect.bisect_left(days, E1_RINDEX_START))


def rolling_start_index(days, year: int) -> int:
    """`fe_study.stage_e2`'s January start index."""
    import bisect

    return bisect.bisect_left(days, f"{year}-01-01")

# ----------------------------------------------------------------------------- reduced windows
def win_tag(days, i0: int) -> str:
    """The record's window tag: `default` for the published start, else `w<first session>`."""
    return "default" if int(i0) == START else "w" + str(days[int(i0)]).replace("-", "")


def input_start_index(days, key: str, window: str = "main"):
    """First session index of `days` on which the extra-data `key` has a value, or None.

    The series' own registered range (`EXTRA_DATA.json`) is what this reads; the panel's
    alignment is by date (`extra.values_on`), so the answer is the first session the arm's input
    really covers.
    """
    from . import extra as _ext

    v = _ext.values_on(_ext.resolve_series(key, window), days)
    fin = np.flatnonzero(np.isfinite(v))
    return int(fin[0]) if fin.size else None


def input_legs(arm) -> tuple:
    """The extra-data instruments an arm's declaration adds to its inputs (`leg_b`, `legs`).

    A panel leg (`1x`/`2x`/`2x_spy`) has the window's own history and never moves the window; an
    extra-data key (LQD, IEF, XLU, GLD, ...) does.
    """
    legs = arm.get("legs")
    if not legs:
        legs = (arm.get("leg"), arm.get("leg_b"))
    return tuple(k for k in legs if k and k not in LEGS)


def arm_window(arm, days=None) -> Window:
    """The main window an arm declared (`spec["start"]`), resolved against the data.

    Rules (pre-registered, see README §2a):

    * no `start` -> the published window (`START`, tag `default`): nothing changes for an arm
      that does not ask for a reduced window;
    * `start = "YYYY-MM-DD"` -> the first session at or after it, never before the published
      warm-up start (`START`, the 2001-session vote's own beginning);
    * `start = "auto"` -> the later of the published start and the **first session every declared
      input covers**, moved one session forward (the engine reads the previous close at `i0 - 1`,
      so the input must reach `i0 - 1` for the fill to be defined). The inputs are the arm's
      `inputs` list plus every declared leg (`leg_b`, or each key of `legs`) that is an extra-data
      series (a panel leg has the window's own history). This is the rule that unblocks an idea
      whose second instrument starts after 2000-03-09 (LQD 2002-07, IEF 2002-07, TIP 2003-12, GLD
      2004-11, GDX 2006-05, HYG 2007-04 ...): before its own start the arm has no price, and the
      framework refuses a leg-B series with a hole rather than invent one.

    A window shorter than `MIN_WINDOW_YEARS` calendar years is `NotApplicable`, and so is an
    `"auto"` window whose declared input has no value anywhere in the panel.
    """
    declared = arm.get("start")
    if declared is None:
        # opt-in: an arm that declares nothing keeps the published window untouched, so every
        # published number (and every arm of every earlier batch) is unchanged
        return DEFAULT_WINDOW
    if days is None:
        days = bars()[0]
    i0 = START
    inputs = tuple(arm.get("inputs") or ())
    for key in input_legs(arm):
        if key not in inputs:
            inputs = inputs + (key,)
    if declared == "auto":
        for key in inputs:
            j = input_start_index(days, key)
            if j is None:
                raise NotApplicable(f"the window is \"auto\" but the input {key!r} has no value "
                                    "in this panel")
            i0 = max(i0, j + 1)
    else:
        i0 = max(i0, bisect.bisect_left(days, str(declared)))
    win = Window(i0=int(i0), tag=win_tag(days, i0), declared=declared, inputs=inputs)
    if win.i0 >= len(days):
        raise NotApplicable(f"the declared window start (session {win.i0}) is past the end of this "
                            f"panel ({len(days)} sessions)")
    years = fe_sim.calendar_years(days[win.i0 - 1], days[-1])
    if years < MIN_WINDOW_YEARS:
        raise NotApplicable(
            f"the reduced window {days[win.i0]}..{days[-1]} is {years:.1f} years long, less than "
            f"the pre-registered {MIN_WINDOW_YEARS:.0f}")
    return win


def after_tax_windows(days, win: Window | None = None) -> dict:
    """The three published after-tax windows, clipped up to the window's own start.

    A window that starts before the arm's reduced start is **clipped** (`max(declared start,
    win.i0)`), not dropped: the arm's after-tax block then measures its own window against a
    control re-simulated on the same one, and the record's `window` cell names the window that
    really ran. An arm whose reduced start leaves fewer than three *distinct* windows (after
    2017 the three clip onto the same window) is still evaluated on all three keys -- the gate
    iterates `TAX_WINDOWS` -- and the record shows the clipping.
    """
    win = win or DEFAULT_WINDOW
    out = {}
    for name, (a, b) in TAX_WINDOWS.items():
        j0, j1 = fe_study.window_indices(days, a, b)
        out[name] = (max(j0, win.i0), j1)
    return out


def rolling_starts(days, win: Window | None = None) -> dict:
    """The E2 rolling January starts, dropping the ones that begin before the window."""
    win = win or DEFAULT_WINDOW
    out = {}
    for yr in E2_START_YEARS:
        i0 = rolling_start_index(days, yr)
        if i0 >= win.i0:
            out[str(yr)] = i0
    return out


def boot_pool(days, win: Window | None = None) -> tuple:
    """The block bootstrap's restricted source pool, as `(lo, hi)` in drawn-position space.

    `fe_boot.draw_positions` draws positions in `[0, npool)` and the chunk builder reads
    `q[pos + 1]`, so session `i` of the main window is position `i - 1` (session 0 is never drawn:
    the published pool is `arange(1, Tsrc)`). Restricting the pool to the window keeps the
    seed/block semantics (one `integers`, one `random`, one `integers` per path, in the published
    order) with `npool` = the number of sessions the window covers.
    """
    win = win or DEFAULT_WINDOW
    if not win.reduced:
        # the published pool: every session but the first, from the top of the history -- a
        # default-window arm must draw exactly the paths the campaign's B1/B2 draw
        return 0, len(days) - 2
    return int(win.i0) - 1, len(days) - 2


# ----------------------------------------------------------------------------- helpers
def e2_of(price):
    """The E2 vote of a price panel, as the plugins use it."""
    return fe_signals.e2_up(price)


def window_cells(nav, days, i0, i1):
    """`fe_study.window_stats`-equivalent cells of one engine NAV path."""
    dates = list(days[i0 - 1 : i1 + 1])
    years = fe_sim.calendar_years(dates[0], dates[-1])
    return {
        "cagr": float(fe_sim.cagr(nav[0], nav[-1], years)),
        "max_dd": float(fe_sim.max_dd(nav.reshape(1, -1))[0]),
        "years": float(years),
        "window": {"start": dates[0], "end": dates[-1], "sessions": len(dates) - 1},
        "turnover_annual": None,
    }


__all__ = [
    "DEFAULT_WINDOW",
    "MIN_WINDOW_YEARS",
    "Window",
    "after_tax_windows",
    "arm_window",
    "bars",
    "boot_pool",
    "e2_of",
    "input_start_index",
    "instrument_panel",
    "instrument_start",
    "main_panel",
    "oos_panel",
    "rolling_start_index",
    "rolling_starts",
    "try_env",
    "ufe",
    "usd_env",
    "win_tag",
    "window_cells",
]
