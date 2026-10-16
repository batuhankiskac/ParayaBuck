"""IC05 -- return-entropy regime gate (idea_bank_v2, arms A/B/C).

Arm specs (`docs/research/idea_bank_v2.json`, parameters frozen -- not tuned here):

    A  E2 on iff the 2-of-3 vote holds AND ApEn(126, m=2) <= its trailing 252-session median
    B  the same with the threshold at the trailing 252-session 40th percentile
    C  the early-path entry additionally requires ApEn(126, m=2) <= its trailing 252-session median

Literal reading and the frozen conventions:

* **ApEn** is Pincus' (1991) approximate entropy of the last N = 126 daily simple returns of
  `data.price` (QQQ's price close on the main window, the market's own index/ETF close on the OOS
  and instrument windows -- the series the vote runs on; entropy is scale-invariant, so the 2x leg
  would give the same sequence statistic), embedding dimension m = 2:

      x_i = (u_i, ..., u_{i+m-1}),  C_i^m = #{j : max_k |u_{i+k} - u_{j+k}| <= r} / (N - m + 1)
      Phi^m = mean_i ln C_i^m,      ApEn = Phi^m - Phi^{m+1}

  with self-matches counted (Pincus' definition, so every C_i > 0).  The bank fixes N and m but
  not the tolerance; the standard choice r = 0.2 x the window's standard deviation (population SD,
  ddof = 0, Pincus' usual 0.1-0.25 SD range) is used, which keeps ApEn invariant to the level and
  scale of the returns as the bank's mechanism requires.  The window of session t is the 126
  returns ending at t (sessions t-125 .. t, so defined from column 126 on).
* **Threshold.**  The trailing 252-session median / 40th percentile (linear interpolation, the
  `numpy.percentile` default) of the ApEn series *including* t -- production's `med_p` convention
  for its own vol median; undefined until 252 ApEn values exist.
* **Undefined -> gate closed.**  Before the threshold exists (the first 126 + 251 columns of a
  panel) the gate is False: A/B hold E2 off, C blocks early entries.  An unverifiable condition is
  False, the convention of the other gates of this batch; every simulated window starts at index
  253 or later, so this touches at most the first ~125 sessions of a window's measured span.
* **Scope.**  A/B replace production's vote by ``e2_up & gate`` and feed it to production's R2
  state machine (`fe_signals.r2_state`) and sizing unchanged -- the early re-entry path can still
  act while the gated E2 is off.  C keeps the production vote and adds the gate to the early
  path's *entry* condition only (exit below SMA50 and the E2 override untouched), with `ib01`'s
  vectorised state machine `_r2_state_gated` (all-True gate == `fe_signals.r2_state` bit for bit).
  Execution is production K.
* **Cost.**  ApEn is O(N^2) per session; it is a numba kernel over the ``(P, T)`` panel (signal
  construction only -- no simulation, tax or bootstrap code).
* **Availability.**  Closes only: every window is applicable.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
from numba import njit, prange

from research.fast_engine import fe_signals

ARMS = {
    "A": {"rule": "K", "leg": "2x", "cash_yield": "none", "N": 126, "m": 2, "r_sd": 0.2,
          "q_window": 252, "q": 0.5, "gate": "vote"},
    "B": {"rule": "K", "leg": "2x", "cash_yield": "none", "N": 126, "m": 2, "r_sd": 0.2,
          "q_window": 252, "q": 0.4, "gate": "vote"},
    "C": {"rule": "K", "leg": "2x", "cash_yield": "none", "N": 126, "m": 2, "r_sd": 0.2,
          "q_window": 252, "q": 0.5, "gate": "early"},
}


@njit(cache=False, nogil=True)
def _apen_window(u, m, r):
    """Pincus' ApEn(m, r) of one window `u` (self-matches counted)."""
    N = u.shape[0]
    n1 = N - m + 1          # templates of length m
    n2 = N - m              # templates of length m + 1
    c1 = np.ones(n1)        # self-match
    c2 = np.ones(n2)
    for i in range(n1):
        for j in range(i + 1, n1):
            ok = True
            for k in range(m):
                if abs(u[i + k] - u[j + k]) > r:
                    ok = False
                    break
            if ok:
                c1[i] += 1.0
                c1[j] += 1.0
                if j < n2 and abs(u[i + m] - u[j + m]) <= r:
                    c2[i] += 1.0
                    c2[j] += 1.0
    phi1 = 0.0
    for i in range(n1):
        phi1 += math.log(c1[i] / n1)
    phi2 = 0.0
    for i in range(n2):
        phi2 += math.log(c2[i] / n2)
    return phi1 / n1 - phi2 / n2


@njit(parallel=True, cache=False)
def _apen_panel(ret, N, m, r_sd, out):
    """ApEn of the N returns ending at each column; `ret[:, 0]` is unused (no return into col 0)."""
    P, T = ret.shape
    cells = P * T
    for c in prange(cells):
        p = c // T
        t = c - p * T
        if t < N:
            continue
        u = ret[p, t - N + 1: t + 1]
        ok = True
        mu = 0.0
        for k in range(N):
            if not np.isfinite(u[k]):
                ok = False
                break
            mu += u[k]
        if not ok:
            continue
        mu /= N
        ss = 0.0
        for k in range(N):
            ss += (u[k] - mu) * (u[k] - mu)
        sd = math.sqrt(ss / N)
        if sd <= 0.0:
            continue
        out[p, t] = _apen_window(u, m, r_sd * sd)


def apen(price, N, m, r_sd):
    """``(P, T)`` ApEn of `price`'s simple returns; NaN where undefined."""
    p = np.atleast_2d(np.asarray(price, dtype=np.float64))
    ret = np.full(p.shape, np.nan)
    ret[:, 1:] = p[:, 1:] / p[:, :-1] - 1.0
    out = np.full(p.shape, np.nan)
    _apen_panel(np.ascontiguousarray(ret), int(N), int(m), float(r_sd), out)
    return out


def rolling_quantile(x, window, q):
    """Trailing `window`-quantile per row (linear interpolation); NaN unless all values defined."""
    df = pd.DataFrame(np.asarray(x, dtype=np.float64).T)
    roll = df.rolling(window, min_periods=window)
    out = roll.median() if q == 0.5 else roll.quantile(q, interpolation="linear")
    return out.to_numpy().T.copy()


def _r2_state_gated(price, e2_on, entry_gate):
    """`fe_signals.r2_state` with a per-session early-entry gate (`ib01`'s vectorised scan).

    The state is "the last entry is more recent than the last reset" (an entry needs `sma50_up`
    and `~e2`, a reset is `~sma50_up | e2`, so they never coincide); an all-True gate reproduces
    `fe_signals.r2_state` exactly.
    """
    price = np.atleast_2d(np.asarray(price, dtype=np.float64))
    e2_on = np.atleast_2d(np.asarray(e2_on, dtype=bool))
    gate = np.atleast_2d(np.asarray(entry_gate, dtype=bool))
    sma50_up, vol_p, med_p, lag_cond = fe_signals.early_components(price)
    entry = (~e2_on) & sma50_up & np.isfinite(med_p) & (vol_p < med_p) & lag_cond & gate
    reset = e2_on | (~sma50_up)
    idx = np.arange(price.shape[1])
    last_entry = np.maximum.accumulate(np.where(entry, idx, -1), axis=1)
    last_reset = np.maximum.accumulate(np.where(reset, idx, -1), axis=1)
    active = last_entry > last_reset
    return e2_on | active, np.where(e2_on, 1.0, np.where(active, 0.5, 0.0))


def entropy_gate(price, spec):
    """``(P, T)`` bool: ApEn <= its trailing quantile (False where either is undefined)."""
    a = apen(price, spec["N"], spec["m"], spec["r_sd"])
    thr = rolling_quantile(a, int(spec["q_window"]), float(spec["q"]))
    known = np.isfinite(a) & np.isfinite(thr)
    return known & (np.where(known, a, np.inf) <= np.where(known, thr, -np.inf))


def targets(data, spec):
    gate = entropy_gate(data.price, spec)
    e2 = fe_signals.e2_up(data.price)
    if spec["gate"] == "vote":
        comps = fe_signals.early_components(data.price)
        on_eff, factor = fe_signals.r2_state(data.price, e2 & gate, comps)
    elif spec["gate"] == "early":
        on_eff, factor = _r2_state_gated(data.price, e2, gate)
    else:
        raise ValueError(f"unknown gate {spec['gate']!r}")
    return fe_signals.sized_wants(on_eff, factor, data.vol)


__all__ = ["ARMS", "apen", "entropy_gate", "rolling_quantile", "targets"]
