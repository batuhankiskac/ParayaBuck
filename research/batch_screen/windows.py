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

import numpy as np

from research.fast_engine import fe_data, fe_signals, fe_sim
from research.fast_engine.fe_config import EXP_Q, EXTRA_FIN, START

from .api import DEFAULT_LEG, panel
from .rules import cash_rates

_BARS = {}
_UFE = {}
_ENV = {}


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
    "bars",
    "e2_of",
    "instrument_panel",
    "instrument_start",
    "main_panel",
    "oos_panel",
    "rolling_start_index",
    "try_env",
    "ufe",
    "usd_env",
    "window_cells",
]
