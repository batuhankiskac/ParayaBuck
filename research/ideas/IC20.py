"""IC20 -- bond-volatility regime veto (idea_bank_v2, arms A/B/C).

Arm specs (`docs/research/idea_bank_v2.json`, parameters frozen -- not tuned here):

    A  weight *= 0.5 while vol20(IEF) > 1.5 * median252(vol20(IEF))
    B  weight = 0    while vol20(IEF) > 2.0 * median252(vol20(IEF))
    C  the multiplier of A applied only while the vote is on (early path unchanged)

Literal reading and the frozen conventions:

* **vol20(IEF)** is `fe_signals.vol_n`'s estimator (annualised sample stdev of the 20 simple daily
  returns ending at t) on the registered Yahoo `IEF` close (extra key `ief`, 2002-07-30 ->, last
  published value carried over sessions IEF did not trade).  **median252** is the trailing
  252-session median of vol20 *including* t (production's `med_p` convention); undefined unless
  all 252 vol values are defined.
* **Returns, not levels, are replayed.**  Each column carries the IEF return of the session it
  replays: on a real window the panel's own sessions; inside a bootstrap chunk the drawn *source*
  session (``extra_days[extra_index[p, t]]``, the convention `extra.aligned` applies to every
  registered series).  The source return is computed on the pool's own calendar before the draw,
  so a chunk's vol20 is the vol of 20 genuinely observed IEF daily returns -- the same treatment
  the chunk gives QQQ.  (Differencing the replayed *levels* across a block boundary would inject
  one spurious jump between two unrelated dates every 20 columns.)
* **Undefined -> neutral.**  Before IEF exists (and inside the first 20 + 251 sessions of history,
  or a chunk window that contains a pre-2002 source session) the state is undefined and the
  multiplier is 1.0.
* **Scope.**  A and B act on the whole production target (E2 and the early path; B's "weight = 0"
  overrides both).  C applies A's 0.5 only where production's 2-of-3 vote (`e2_up`) is on, so the
  half-weight early re-entry is untouched.  Execution is production K.
* **Availability.**  `NotApplicable` where IEF has no value in the window (M1, M2,
  GSPC_1957_1999 end before 2002-07-30); M3/M4/M5, the main window and the ETFs carry IEF for
  their post-2002 span.  This matches the bank's 3-window note; the framework applies the reduced
  G7 rule on the applicable windows.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from research.batch_screen import extra as _ext
from research.batch_screen.api import NotApplicable
from research.fast_engine import fe_signals

ARMS = {
    "A": {"rule": "K", "leg": "2x", "cash_yield": "none", "n": 20, "med_window": 252,
          "ratio": 1.5, "factor": 0.5, "scope": "all"},
    "B": {"rule": "K", "leg": "2x", "cash_yield": "none", "n": 20, "med_window": 252,
          "ratio": 2.0, "factor": 0.0, "scope": "all"},
    "C": {"rule": "K", "leg": "2x", "cash_yield": "none", "n": 20, "med_window": 252,
          "ratio": 1.5, "factor": 0.5, "scope": "vote"},
}


def source_returns(data, key):
    """``(P, T)`` daily simple return of an extra series on the session each column replays.

    Computed on the pool calendar (the panel's own days on a real window), level forward-filled,
    then indexed by the column's source session; column 0 (the chunk's base row / the window's
    first session) is NaN, like the market arrays' missing return into column 0.
    """
    name = _ext.resolve_series(key, data.extra_window or data.window)
    pool = np.asarray(data.days) if data.extra_index is None else np.asarray(data.extra_days)
    lv = _ext.forward_fill(_ext.values_on(name, pool).reshape(1, -1))[0]
    r = np.full(lv.size, np.nan)
    r[1:] = lv[1:] / lv[:-1] - 1.0
    if data.extra_index is None:
        out = np.tile(r, (data.P, 1))
    else:
        out = r[np.asarray(data.extra_index)]
    out[:, 0] = np.nan
    return out


def rolling_sd(r, n):
    """Annualised sample stdev of the n returns ending at t; NaN if any of them is NaN."""
    P, T = r.shape
    bad = ~np.isfinite(r)
    x = np.where(bad, 0.0, r)
    z = np.zeros((P, 1))
    s1 = np.concatenate([z, np.cumsum(x, axis=1)], axis=1)
    s2 = np.concatenate([z, np.cumsum(x * x, axis=1)], axis=1)
    sb = np.concatenate([z, np.cumsum(bad, axis=1)], axis=1)
    out = np.full((P, T), np.nan)
    w1 = s1[:, n:] - s1[:, :-n]
    w2 = s2[:, n:] - s2[:, :-n]
    wb = sb[:, n:] - sb[:, :-n]
    v = np.sqrt(np.maximum(w2 - w1 * w1 / n, 0) / (n - 1) * 252)
    out[:, n - 1:] = np.where(wb > 0, np.nan, v)
    return out


def rolling_quantile(x, window, q):
    """Trailing `window`-quantile per row (linear interpolation); NaN unless all values defined."""
    df = pd.DataFrame(np.asarray(x, dtype=np.float64).T)
    roll = df.rolling(window, min_periods=window)
    out = roll.median() if q == 0.5 else roll.quantile(q, interpolation="linear")
    return out.to_numpy().T.copy()


def targets(data, spec):
    if not np.isfinite(data.extra("ief")).any():
        raise NotApplicable("Yahoo IEF starts 2002-07-30 and has no value in this window")
    r = source_returns(data, "ief")
    v = rolling_sd(r, int(spec["n"]))
    med = rolling_quantile(v, int(spec["med_window"]), 0.5)
    known = np.isfinite(v) & np.isfinite(med)
    hot = known & (np.where(known, v, 0.0) > float(spec["ratio"]) * np.where(known, med, 0.0))
    e2 = fe_signals.e2_up(data.price)
    comps = fe_signals.early_components(data.price)
    on_eff, factor = fe_signals.r2_state(data.price, e2, comps)
    wants = fe_signals.sized_wants(on_eff, factor, data.vol)
    if spec["scope"] == "vote":
        hot = hot & e2
    return wants * np.where(hot, float(spec["factor"]), 1.0)


__all__ = ["ARMS", "rolling_quantile", "rolling_sd", "source_returns", "targets"]
