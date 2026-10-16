"""IC10 -- day/night volatility decomposition sizing (idea_bank_v2, arms A/B/C).

Arm specs (`docs/research/idea_bank_v2.json`, parameters frozen -- not tuned here):

    A  vt = min(1, 0.35 / sigma_night20)
    B  vt = min(1, 0.35 / sigma_mix),   sigma_mix = sqrt(sigma_night20^2 + 0.5 * sigma_day20^2)
    C  vt = min(1, 0.35 / max(vol20, 1.20 * sigma_night20))

    sigma_night20 = annualised stdev of log(open_t / close_{t-1}) over 20 sessions
    sigma_day20   = the same of log(close_t / open_t)

Literal reading and the frozen conventions:

* **Instrument.**  "The arm's own instrument daily open and close" is the *held leg*'s
  `data.open` / `data.close` -- the series production sizes on (`data.vol = vol_n(data.close)` is
  the held 2x leg's close-to-close vol20), so arm C's `max(vol20, 1.2 * sigma_night20)` compares
  two estimates of the same instrument.  On the synthetic daily-reset 2x leg the overnight move is
  `1 + 2 r_on` and the intraday move `close/open` of the same recurrence, exactly what a holder of
  that leg bears.
* **Estimator.**  Sample standard deviation (ddof = 1) of the 20 log returns ending at session t,
  times sqrt(252) -- `fe_signals.vol_n`'s window, normalisation and annualisation, on log returns
  as the spec writes them.  The first 20 sessions of a panel are undefined, and `fe_signals.vt_of`
  maps an undefined/non-positive vol to 1.0 exactly as it does for production's vol20 warm-up (no
  simulated session is affected: every window starts at index 253 or later).
* **Sizing.**  The vote and the early re-entry machine are production's (`e2_up`, `r2_state`);
  only the vol input of `fe_signals.sized_wants` changes (0.35 target, the path factor, the 0.98
  total cap are production's).  Execution is production K.
* **Availability (`NotApplicable`), fixed before any simulation from a data inspection only.**  The
  decomposition needs real opening prints.  A window (or a bootstrap chunk) whose held leg has an
  exactly-zero overnight log return on more than 5 % of its sessions is a close-only series
  (open := previous close) and is not evaluable.  Inspection of the inputs (no performance
  computed): main QQQ 1.1 % zero overnight sessions and the 21 ETFs <= 0.6 % (genuine flat opens)
  -> applicable; the OOS windows GSPC_1957_1999 100 %, M2 100 %, M1 48 %, M3 38 %, M4 15 %,
  M5 68 % -> all six are not evaluable, so the arm can at most reach stage 2 as
  `holdout_insufficient` (manual confirmation).  The bank expected five evaluable windows (it
  assumed only ^GSPC 1957-1999 was close-only); the Yahoo index opens are close-only in long
  sub-spans of every OOS market (e.g. ^FTSE 2000-2026, ^N225 before 1990), and a window-level
  rule cannot keep a partially degenerate window without mixing in a zero-variance estimator.
"""

from __future__ import annotations

import numpy as np

from research.batch_screen.api import NotApplicable
from research.fast_engine import fe_signals

#: the overnight log return counted as "no opening print" and the share that disqualifies a panel
ZERO_TOL = 1e-12
MAX_ZERO_SHARE = 0.05

ARMS = {
    "A": {"rule": "K", "leg": "2x", "cash_yield": "none", "sigma": "night", "n": 20,
          "day_weight": 0.0, "night_mult": 1.0},
    "B": {"rule": "K", "leg": "2x", "cash_yield": "none", "sigma": "mix", "n": 20,
          "day_weight": 0.5, "night_mult": 1.0},
    "C": {"rule": "K", "leg": "2x", "cash_yield": "none", "sigma": "max_vol20", "n": 20,
          "day_weight": 0.0, "night_mult": 1.20},
}


def _rolling_sd(r, n):
    """Annualised sample stdev of the n returns ending at each column (`vol_n`'s convention).

    `r` is ``(P, T)`` with column 0 undefined (no return into the first session); the output is
    NaN for the first n columns, like `fe_signals.vol_n`.
    """
    P, T = r.shape
    out = np.full((P, T), np.nan)
    x = r[:, 1:]
    z = np.zeros((P, 1))
    s1 = np.concatenate([z, np.cumsum(x, axis=1)], axis=1)
    s2 = np.concatenate([z, np.cumsum(x * x, axis=1)], axis=1)
    w1 = s1[:, n:] - s1[:, :-n]
    w2 = s2[:, n:] - s2[:, :-n]
    out[:, n:] = np.sqrt(np.maximum(w2 - w1 * w1 / n, 0) / (n - 1) * 252)
    return out


def night_day(o, c):
    """``(night, day)`` log returns of a leg, ``(P, T)``, column 0 = 0 (never read)."""
    o = np.atleast_2d(o)
    c = np.atleast_2d(c)
    night = np.zeros_like(c)
    night[:, 1:] = np.log(o[:, 1:] / c[:, :-1])
    day = np.log(c / o)
    day[:, 0] = 0.0
    return night, day


def targets(data, spec):
    n = int(spec["n"])
    night, day = night_day(data.open, data.close)
    zero_share = float(np.mean(np.abs(night[:, 1:]) < ZERO_TOL))
    if zero_share > MAX_ZERO_SHARE:
        raise NotApplicable(
            f"close-only opens: {zero_share:.1%} of the held leg's overnight returns are exactly 0 "
            f"(> {MAX_ZERO_SHARE:.0%}), so the day/night decomposition is degenerate here")
    s_night = _rolling_sd(night, n)
    if spec["sigma"] == "night":
        sig = s_night
    elif spec["sigma"] == "mix":
        s_day = _rolling_sd(day, n)
        sig = np.sqrt(s_night * s_night + float(spec["day_weight"]) * s_day * s_day)
    elif spec["sigma"] == "max_vol20":
        sig = np.maximum(data.vol, float(spec["night_mult"]) * s_night)
    else:
        raise ValueError(f"unknown sigma {spec['sigma']!r}")
    e2 = fe_signals.e2_up(data.price)
    comps = fe_signals.early_components(data.price)
    on_eff, factor = fe_signals.r2_state(data.price, e2, comps)
    return fe_signals.sized_wants(on_eff, factor, sig)


__all__ = ["ARMS", "night_day", "targets"]
