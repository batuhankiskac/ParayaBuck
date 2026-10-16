"""IB06 -- range-expansion / contraction entries and an ATR-chandelier exit.  Idea bank
`idea_bank_v1`.

    A  the early path's entry additionally requires TR_t > 1.5 * ATR20
    B  the early path's entry additionally requires the 7-session range < 0.5 * ATR20
    C  the on-state is forced to 0 when close < (the highest close of the last 20 sessions)
       - 2.5 * ATR20

TR = `max(high - low, |high - previous close|, |low - previous close|)`, ATR20 = the mean of the
last 20 TRs.  Arms A/B are *additional entry gates* on the production early path (its entry
condition, the E2 vote, the half weight and the exit are production's); arm C is an overlay on the
produced target (the whole on-state, E2 and the early leg alike), as the bank's wording says.

**Where the high/low come from.**  The framework hands a plugin the underlying's high/low only on
the 21 ETF windows; the main window, the six OOS windows and both bootstrap families carry
`high=None`.  The bank's data section for IB06 lists no new download, so the arm reads the
registered `hl_high`/`hl_low` extra series (this branch's `research/plugins_b2/build_extra.py`):
`high/close` and `low/close` ratios derived from the *same two repo files the panels' prices come
from* (`stress_robustness_v1_daily.csv` for the QQQ main window, `ewma_band_v1_index.csv` for the
OOS indices), which makes them session-for-session aligned with `data.price` by construction.  A
ratio (not a level) is required because inside a bootstrap chunk the panel's price is a
reconstructed index (it starts at 1) while the extra series replays the drawn source session's own
value.  When the panel does carry high/low (the ETFs), those are used instead.

**Scaling.**  Everything downstream is a ratio (TR/close, range/close, ATR/close, highest
close/close), so the arm is gearing-free and reads the same on a 2x leg as on the underlying.

**Applicability.**  `main`, `M1`-`M5`, the 21 ETFs and both bootstrap families: applicable.  The
^GSPC 1957-1999 window is refused (`NotApplicable`): the harness builds it close-only from
`ewma_band_v1_gspc.csv` (whose high/low are placeholders: `h == l == c` on 100 % of its rows), the
registered map has no series for it, and the fallback would be the main window's QQQ series on
dates that do not overlap it.  Disclosed degeneracy on the *evaluable* OOS windows: the repo files'
high/low are placeholders (`h == l == c`) on 100 % of ^GSPC 1927-1956 (the `M2` window), 47 % of
^IXIC 1971-1999 (`M1`) and 38 % of ^N225 (`M3`), where TR degenerates to |close - previous close|;
this is IB10's own disclosed caveat for the close-only windows and is why those windows' range
information is thin rather than absent.
"""

from __future__ import annotations

import numpy as np

from research.batch_screen.api import NotApplicable
from research.fast_engine import fe_signals

ARMS = {
    "A": {"rule": "K", "leg": "2x", "cash_yield": "none", "gate": "expansion", "tr_mult": 1.5,
          "atr_sessions": 20},
    "B": {"rule": "K", "leg": "2x", "cash_yield": "none", "gate": "contraction",
          "range_sessions": 7, "range_mult": 0.5, "atr_sessions": 20},
    "C": {"rule": "K", "leg": "2x", "cash_yield": "none", "exit": "chandelier",
          "atr_mult": 2.5, "lookback": 20, "atr_sessions": 20},
}

#: the registered high/low ratios must cover this fraction of the panel, else the series is not
#: the window's own instrument's (see the module docstring)
HL_MIN_COVER = 0.90


def high_low(data):
    """The window's underlying high/low, in the panel's own price scale."""
    if data.high is not None and data.low is not None:
        return data.high, data.low
    hi = data.extra("hl_high")
    lo = data.extra("hl_low")
    if np.isfinite(hi).mean() < HL_MIN_COVER or not np.isfinite(hi[:, 0]).all():
        raise NotApplicable(
            "no high/low for this window's own instrument: the harness builds it close-only "
            "(^GSPC 1957-1999), so the registered high/low ratios fall back to the main window's "
            "QQQ series and do not cover these dates")
    return hi * data.price, lo * data.price


def tr_ratio(high, low, price):
    """(P, T) true range in units of the session's own close; the first session uses its own
    close as the previous close."""
    ratio = np.ones_like(price)
    ratio[:, 1:] = price[:, 1:] / price[:, :-1]
    hi = high / price * ratio
    lo = low / price * ratio
    tr = np.maximum(np.maximum(hi - lo, np.abs(hi - 1.0)), np.abs(lo - 1.0))
    return tr / ratio


def roll_mean(a, n):
    """Trailing mean over `n` sessions, `NaN` unless all `n` values are finite."""
    P, T = a.shape
    out = np.full_like(a, np.nan)
    if T < n:
        return out
    x = np.nan_to_num(a, nan=0.0)
    ok = np.isfinite(a).astype(np.float64)
    z = np.zeros((P, 1))
    cs = np.concatenate([z, np.cumsum(x, axis=1)], axis=1)
    cn = np.concatenate([z, np.cumsum(ok, axis=1)], axis=1)
    s = cs[:, n:] - cs[:, :-n]
    c = cn[:, n:] - cn[:, :-n]
    out[:, n - 1:] = np.where(c == n, s / n, np.nan)
    return out


def roll_minmax(a, n, how):
    """Trailing `n`-session max or min; `NaN` for the first `n - 1` sessions."""
    out = np.full_like(a, np.nan)
    if a.shape[1] >= n:
        win = np.lib.stride_tricks.sliding_window_view(a, n, axis=1)
        out[:, n - 1:] = (win.max if how == "max" else win.min)(axis=-1)
    return out


def entry_gate(data, spec):
    """The arm's additional early-entry gate (bool, `(P, T)`)."""
    high, low = high_low(data)
    trc = tr_ratio(high, low, data.price)
    atr = roll_mean(trc, int(spec["atr_sessions"]))
    if spec["gate"] == "expansion":
        return np.isfinite(atr) & (trc > float(spec["tr_mult"]) * atr)
    rng = (roll_minmax(high, int(spec["range_sessions"]), "max")
           - roll_minmax(low, int(spec["range_sessions"]), "min")) / data.price
    return np.isfinite(atr) & (rng < float(spec["range_mult"]) * atr)


def early_state(price, e2_on, comps, gate=None):
    """`fe_signals.r2_state` with the entry condition ANDed with `gate` (same loop, verbatim)."""
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
        exit_cond = ~sma50_up[:, t]
        entry_cond = (sma50_up[:, t] & np.isfinite(med_p[:, t])
                      & (vol_p[:, t] < med_p[:, t]) & lag_cond[:, t])
        if gate is not None:
            entry_cond = entry_cond & gate[:, t]
        early_active = np.where(early_active & exit_cond & (~e2t), False, early_active)
        can_enter = (~early_active) & (~e2t) & np.isfinite(med_p[:, t])
        early_active = np.where(can_enter & entry_cond, True, early_active)
        on_eff[:, t] = e2t | early_active
        factor[:, t] = np.where(e2t, 1.0, np.where(early_active, 0.5, 0.0))
    return on_eff, factor


def targets(data, spec):
    price = data.price
    e2 = fe_signals.e2_up(price)
    comps = fe_signals.early_components(price)
    if spec.get("exit") == "chandelier":
        high, low = high_low(data)
        atr = roll_mean(tr_ratio(high, low, price), int(spec["atr_sessions"]))
        peak = roll_minmax(price, int(spec["lookback"]), "max")
        fire = np.isfinite(atr) & ((price / peak - 1.0) < -float(spec["atr_mult"]) * atr)
        on_eff, factor = fe_signals.r2_state(price, e2, comps)
        return fe_signals.sized_wants(on_eff, factor, data.vol) * (~fire)
    on_eff, factor = early_state(price, e2, comps, gate=entry_gate(data, spec))
    return fe_signals.sized_wants(on_eff, factor, data.vol)
