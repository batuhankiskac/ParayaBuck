"""IB22 -- international lead-lag: the same-session European move as a causal intraday signal.
Idea bank `idea_bank_v1`.

    A  the early path's entry additionally requires 0.5*(r_GDAXI,t + r_FTSE,t) > 0
    B  the early path's entry additionally requires 0.5*(r_GDAXI,t + r_FTSE,t) + r_N225,t-1 > 0
    C  the on-state weight *= 0.75 on the session after 0.5*(r_GDAXI + r_FTSE) < -2%

Series: the registered `dax`/`ftse`/`nikkei` extras (Yahoo chart API closes), aligned by date to
the panel's sessions -- i.e. the same *calendar date*'s European/Japanese close, which on the main
window is the move that has already happened when the US close decides the trade (the timing edge
the bank claims).  Returns are close-to-close on those series (`v_t / v_{t-1} - 1`, `NaN` where a
session has no value).

Missing values are *neutral*: a session whose cross-market return is undefined does not block an
entry (arms A/B) and does not trigger the risk cut (arm C), so the arm degenerates to production
there rather than silently forbidding it.  That matters on `M3`, where ^GDAXI starts 1987-12 (61 %
of the window's sessions) and ^N225 (the window's own instrument) has no value on 38 % of its
sessions; the engine's measured window starts at session 253, before which nothing is evaluated
anyway.

**Applicability.**  The idea declares `M3`, `M4`, `M5` evaluable and refuses the three US windows
(`GSPC_1957_1999`, `M1`, `M2`, where the non-US series exist only for part of the window or not at
all) -- a reduced 3-window G7, as the bank's note says.  The main window and the 21 ETF windows are
applicable (the ETF windows start 1996+; ^GDAXI/^FTSE/^N225 cover them).

**Disclosure.**  On `M3` the panel's sessions are Japanese: the DAX/FTSE *close of the same
calendar date* happens after the Nikkei's, so on that window the "lead" is not strictly causal
before the fill; on `M4`/`M5` the European series includes the window's own instrument, so the arm
there is a same-close cross-market vote (breadth-like), not a lead-lag.  Neither affects the main
window, which is where the timing argument holds.
"""

from __future__ import annotations

import numpy as np

from research.batch_screen.api import NotApplicable
from research.fast_engine import fe_signals

ARMS = {
    "A": {"rule": "K", "leg": "2x", "cash_yield": "none", "signal": "eu_positive"},
    "B": {"rule": "K", "leg": "2x", "cash_yield": "none", "signal": "eu_plus_nikkei"},
    "C": {"rule": "K", "leg": "2x", "cash_yield": "none", "signal": "eu_cut", "cut": -0.02,
          "mult": 0.75},
}

NON_US = ("GSPC_1957_1999", "M1", "M2")


def window_key(data) -> str:
    return data.extra_window or data.window


def ret(v):
    """(P, T) close-to-close return, NaN in the first column and where the series has no value."""
    out = np.full_like(v, np.nan)
    out[:, 1:] = v[:, 1:] / v[:, :-1] - 1.0
    return out


def shift(a, n=1):
    out = np.full_like(a, np.nan)
    if a.shape[1] > n:
        out[:, n:] = a[:, :-n]
    return out


def eu_signal(data):
    """0.5*(r_GDAXI,t + r_FTSE,t), NaN where either series has no value on that session."""
    if window_key(data) in NON_US:
        raise NotApplicable(
            "the idea's declared OOS set: the non-US series (^GDAXI 1987-12+, ^FTSE 1984-01+, "
            "^N225 1965+) do not cover this window (GSPC_1957_1999 / M1 / M2)")
    return 0.5 * (ret(data.extra("dax")) + ret(data.extra("ftse")))


def early_state(price, e2_on, comps, gate):
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
                      & (vol_p[:, t] < med_p[:, t]) & lag_cond[:, t]) & gate[:, t]
        early_active = np.where(early_active & exit_cond & (~e2t), False, early_active)
        can_enter = (~early_active) & (~e2t) & np.isfinite(med_p[:, t])
        early_active = np.where(can_enter & entry_cond, True, early_active)
        on_eff[:, t] = e2t | early_active
        factor[:, t] = np.where(e2t, 1.0, np.where(early_active, 0.5, 0.0))
    return on_eff, factor


def targets(data, spec):
    eu = eu_signal(data)
    e2 = fe_signals.e2_up(data.price)
    comps = fe_signals.early_components(data.price)
    if spec["signal"] == "eu_cut":
        prev = shift(eu, 1)
        cut = np.where(np.isfinite(prev), prev < float(spec["cut"]), False)
        on_eff, factor = fe_signals.r2_state(data.price, e2, comps)
        wants = fe_signals.sized_wants(on_eff, factor, data.vol)
        return wants * np.where(cut, float(spec["mult"]), 1.0)
    value = eu if spec["signal"] == "eu_positive" else eu + shift(ret(data.extra("nikkei")), 1)
    gate = np.where(np.isfinite(value), value > 0.0, True)
    on_eff, factor = early_state(data.price, e2, comps, gate)
    return fe_signals.sized_wants(on_eff, factor, data.vol)
