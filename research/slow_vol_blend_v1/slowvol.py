"""slow_vol_blend_v1 -- the slower volatility estimate used in K's vol target (signal code only).

Pre-registered in PREREGISTRATION.md. Two pieces:

- `ew_sigma(c)`: the fast leg, EW-sigma with span 35 sessions. sigma_t = sqrt(252 * EWMA(r^2)) with
  the bias-corrected EWMA (weights lambda^i, normalised), lambda = 1 - 2/(35 + 1), r = simple return
  of the instrument close (the same return definition as fe_signals.vol_n).
- `lr_sigma(ew)`: the slow leg, the mean of the daily sigma series over the trailing 2,520 sessions,
  expanding until 2,520 sessions exist. The daily sigma series IS the EW-sigma series (one vol value
  per session), so the slow leg is the 10-year average level of the same estimator.

Arms (weights are built in `arm_weights`):
- S1: sigma = 0.7 * EW + 0.3 * LR, target 0.35 (replaces vol20 in K's sizing).
- S2: sigma = 0.5 * EW + 0.5 * LR, target 0.35.
- S3: S1's sigma with the target re-levelled to `target3` (a scalar calibrated on data before 2010 by
  `calibrate_target3`, then fixed).
Sizing is K's: weight = on x factor x min(1, target / sigma), cap 0.98. Warm-up (fewer than 252
sessions of history at t): every S arm uses K's own weight w_K.

Only signal arithmetic lives here: one numba kernel for the EWMA recursion (per row, prange over
rows, no simulation, no tax, no bootstrap) and numpy for everything else. Simulation goes through
fe_sim.simulate.
"""

from __future__ import annotations

import numpy as np
from numba import njit, prange

from research.fast_engine.fe_config import CAP, VOL_TARGET

SPAN = 35                      # EW-sigma span (sessions); lambda = 1 - 2/(span + 1)
LAM = 1.0 - 2.0 / (SPAN + 1)   # 34/36
LR_WINDOW = 2520               # 10 years of sessions
WARM = 252                     # minimum sessions of history before any S arm leaves K's weight
S1_W_FAST, S1_W_SLOW = 0.7, 0.3
S2_W_FAST, S2_W_SLOW = 0.5, 0.5
ANNUAL = 252.0


@njit(cache=True, nogil=True)
def _ew_row(c, lam, out):
    num = 0.0
    den = 0.0
    out[0] = np.nan
    for t in range(1, c.shape[0]):
        x = c[t] / c[t - 1] - 1.0
        num = lam * num + x * x
        den = lam * den + 1.0
        out[t] = np.sqrt(ANNUAL * num / den)


@njit(parallel=True, cache=True, nogil=True)
def _ew_panel(C, lam, out):
    for r in prange(C.shape[0]):
        _ew_row(C[r], lam, out[r])


def ew_sigma(c):
    """EW-sigma (span 35, annualised) of each row of a [paths, T] close panel; NaN at t = 0."""
    C = np.ascontiguousarray(np.atleast_2d(np.asarray(c, dtype=float)))
    out = np.full(C.shape, np.nan)
    _ew_panel(C, LAM, out)
    return out


def lr_sigma(ew):
    """Mean of the daily sigma series over the trailing LR_WINDOW sessions (expanding until it exists).

    Daily sigma at session 0 is undefined and excluded, so at t the mean has min(t, LR_WINDOW) terms.
    """
    E = np.atleast_2d(np.asarray(ew, dtype=float))
    P, T = E.shape
    z = np.where(np.isfinite(E), E, 0.0)
    cs = np.concatenate([np.zeros((P, 1)), np.cumsum(z, axis=1)], axis=1)   # cs[:, k] = sum of sigma[0 .. k-1]
    t = np.arange(T)
    lo = np.maximum(0, t + 1 - LR_WINDOW)                                   # window = sessions lo .. t
    cnt = (t + 1 - np.maximum(lo, 1)).astype(float)                          # session 0 has no sigma
    # sum over sessions lo..t = cs[t+1] - cs[lo]; session 0 contributes 0 (it is NaN, set to 0)
    out = np.full((P, T), np.nan)
    ok = cnt > 0                                                            # t = 0 has no window
    out[:, ok] = (cs[:, 1:] - cs[:, lo])[:, ok] / cnt[ok]
    return out


def sized_arm(on, factor, sigma, target):
    """K's sizing with a given vol estimate and target: on x factor x min(1, target / sigma), cap CAP."""
    on = np.atleast_2d(np.asarray(on, dtype=float))
    factor = np.atleast_2d(np.asarray(factor, dtype=float))
    sigma = np.atleast_2d(np.asarray(sigma, dtype=float))
    vt = np.where(sigma > 0, np.minimum(1.0, target / np.where(sigma > 0, sigma, 1.0)), 1.0)
    raw = on * factor * vt
    return np.minimum(CAP, raw)


def blend(ew, lr, w_fast, w_slow):
    return w_fast * ew + w_slow * lr


def arm_weights(c, on, factor, w_k, target3):
    """Target weights of S1, S2, S3 for a [paths, T] close panel; K's weight w_k where warm-up applies."""
    ew = ew_sigma(c)
    lr = lr_sigma(ew)
    t = np.arange(ew.shape[1])
    warm = (t >= WARM)[None, :] & np.isfinite(ew) & np.isfinite(lr)
    s1 = blend(ew, lr, S1_W_FAST, S1_W_SLOW)
    s2 = blend(ew, lr, S2_W_FAST, S2_W_SLOW)
    w1 = np.where(warm, sized_arm(on, factor, s1, VOL_TARGET), w_k)
    w2 = np.where(warm, sized_arm(on, factor, s2, VOL_TARGET), w_k)
    w3 = np.where(warm, sized_arm(on, factor, s1, target3), w_k)
    return {"S1": w1, "S2": w2, "S3": w3, "EW": ew, "LR": lr, "S1sigma": np.where(warm, s1, np.nan)}


def calibrate_target3(on, factor, w_k, s1sigma, mask, iters=200):
    """Target c such that the mean QQQ-equivalent exposure (2 x weight) on vote-on days in `mask` equals K's.

    Pure bisection on a monotone function of c; deterministic. Inputs are the base-financing main-window
    arrays, and `mask` restricts the calendar to data before 2010 (the caller builds it).
    """
    on = np.asarray(on, dtype=bool).ravel()
    sel = on & np.asarray(mask, dtype=bool).ravel()
    k_mean = 2.0 * np.mean(np.asarray(w_k, dtype=float).ravel()[sel])
    f = np.asarray(factor, dtype=float).ravel()[sel]
    s = np.asarray(s1sigma, dtype=float).ravel()[sel]
    ok = np.isfinite(s)
    # warm-up days keep K's weight in the arms, so they enter the calibration at K's own weight
    wk_sel = np.asarray(w_k, dtype=float).ravel()[sel]
    warm_days = ~ok

    def mean_exposure(c):
        vt = np.where(s > 0, np.minimum(1.0, c / np.where(s > 0, s, 1.0)), 1.0)
        w = np.minimum(CAP, f * vt)
        w = np.where(warm_days, wk_sel, w)
        return 2.0 * np.mean(w)

    lo, hi = 1e-3, 10.0
    for _ in range(iters):
        mid = 0.5 * (lo + hi)
        if mean_exposure(mid) < k_mean:
            lo = mid
        else:
            hi = mid
    c = 0.5 * (lo + hi)
    return {"target3": float(c), "k_mean_exposure_vote_on_pre2010": float(k_mean),
            "arm_mean_exposure_at_target3": float(mean_exposure(c)), "n_days_pre2010_vote_on": int(sel.sum())}
