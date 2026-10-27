"""hmm -- causal two-state Gaussian HMM on daily log returns (hmm_regime_v1).

Numba kernels and a small Python driver: Baum-Welch fit (scaled forward-backward), the causal
forward (filtered) probability P(turbulent | returns up to t), the expanding yearly refit schedule
and the three arm weight kernels H1-H3. No simulation, tax or bootstrap logic lives here; the screen
passes the weights to fe_sim / fe_boot. Constants are the pre-registered values (PREREGISTRATION.md).
"""

from __future__ import annotations

import bisect
import math

import numpy as np
from numba import njit

P_ON = 0.8              # H2 gate turns on after two consecutive closes with P(turbulent) > P_ON
P_OFF = 0.5             # H2 gate turns off (weight restored) at the first close with P(turbulent) < P_OFF
SIG_FLOOR = 1e-4        # daily sd floor (0.01 %/day)
MAX_ITER = 1000
TOL = 1e-10             # relative change in log-likelihood
SIG_INIT = (0.6, 1.6)   # initial state sds as multiples of the sample sd (calm, turbulent)
A_DIAG_INIT = (0.98, 0.95)
MIN_TRAIN = 500         # sessions; the first refit is always far beyond this
LOG_SQRT_2PI = 0.5 * math.log(2.0 * math.pi)


# ----------------------------------------------------------------------------- Baum-Welch
@njit(cache=True)
def _em_step(x, pi, A, mu, sig):
    """One Baum-Welch iteration. Returns the updated parameters and the log-likelihood of the input."""
    T = x.shape[0]
    b = np.empty((T, 2))
    shift = np.empty(T)
    for t in range(T):
        z0 = (x[t] - mu[0]) / sig[0]
        z1 = (x[t] - mu[1]) / sig[1]
        l0 = -0.5 * z0 * z0 - math.log(sig[0]) - LOG_SQRT_2PI
        l1 = -0.5 * z1 * z1 - math.log(sig[1]) - LOG_SQRT_2PI
        m = l0 if l0 > l1 else l1
        shift[t] = m
        b[t, 0] = math.exp(l0 - m)
        b[t, 1] = math.exp(l1 - m)
    alpha = np.empty((T, 2))
    c = np.empty(T)
    a0 = pi[0] * b[0, 0]
    a1 = pi[1] * b[0, 1]
    s = a0 + a1
    alpha[0, 0] = a0 / s
    alpha[0, 1] = a1 / s
    c[0] = s
    for t in range(1, T):
        p0 = alpha[t - 1, 0] * A[0, 0] + alpha[t - 1, 1] * A[1, 0]
        p1 = alpha[t - 1, 0] * A[0, 1] + alpha[t - 1, 1] * A[1, 1]
        a0 = p0 * b[t, 0]
        a1 = p1 * b[t, 1]
        s = a0 + a1
        alpha[t, 0] = a0 / s
        alpha[t, 1] = a1 / s
        c[t] = s
    loglik = 0.0
    for t in range(T):
        loglik += math.log(c[t]) + shift[t]
    beta = np.empty((T, 2))
    beta[T - 1, 0] = 1.0
    beta[T - 1, 1] = 1.0
    for t in range(T - 2, -1, -1):
        u0 = b[t + 1, 0] * beta[t + 1, 0]
        u1 = b[t + 1, 1] * beta[t + 1, 1]
        beta[t, 0] = (A[0, 0] * u0 + A[0, 1] * u1) / c[t + 1]
        beta[t, 1] = (A[1, 0] * u0 + A[1, 1] * u1) / c[t + 1]
    gam = np.empty((T, 2))
    for t in range(T):
        g0 = alpha[t, 0] * beta[t, 0]
        g1 = alpha[t, 1] * beta[t, 1]
        s = g0 + g1
        gam[t, 0] = g0 / s
        gam[t, 1] = g1 / s
    xi = np.zeros((2, 2))
    for t in range(T - 1):
        u0 = b[t + 1, 0] * beta[t + 1, 0]
        u1 = b[t + 1, 1] * beta[t + 1, 1]
        x00 = alpha[t, 0] * A[0, 0] * u0
        x01 = alpha[t, 0] * A[0, 1] * u1
        x10 = alpha[t, 1] * A[1, 0] * u0
        x11 = alpha[t, 1] * A[1, 1] * u1
        s = x00 + x01 + x10 + x11
        xi[0, 0] += x00 / s
        xi[0, 1] += x01 / s
        xi[1, 0] += x10 / s
        xi[1, 1] += x11 / s
    pi_new = np.empty(2)
    pi_new[0] = gam[0, 0]
    pi_new[1] = gam[0, 1]
    A_new = np.empty((2, 2))
    for i in range(2):
        r = xi[i, 0] + xi[i, 1]
        A_new[i, 0] = xi[i, 0] / r
        A_new[i, 1] = xi[i, 1] / r
    mu_new = np.empty(2)
    sig_new = np.empty(2)
    for j in range(2):
        sw = 0.0
        sx = 0.0
        for t in range(T):
            sw += gam[t, j]
            sx += gam[t, j] * x[t]
        mu_new[j] = sx / sw
        sv = 0.0
        for t in range(T):
            d = x[t] - mu_new[j]
            sv += gam[t, j] * d * d
        sig_new[j] = max(math.sqrt(sv / sw), SIG_FLOOR)
    return pi_new, A_new, mu_new, sig_new, loglik


def fit_hmm(x):
    """Deterministic Baum-Welch from a fixed start; states relabelled so state 1 has the larger sd."""
    x = np.ascontiguousarray(x, dtype=np.float64)
    sd = float(np.std(x))
    pi = np.array([0.5, 0.5])
    A = np.array([[A_DIAG_INIT[0], 1 - A_DIAG_INIT[0]], [1 - A_DIAG_INIT[1], A_DIAG_INIT[1]]])
    mu = np.array([float(np.mean(x)), float(np.mean(x))])
    sig = np.array([SIG_INIT[0] * sd, SIG_INIT[1] * sd])
    prev = -np.inf
    ll = -np.inf
    it = 0
    converged = False
    for it in range(1, MAX_ITER + 1):
        pi, A, mu, sig, ll = _em_step(x, pi, A, mu, sig)
        if abs(ll - prev) <= TOL * max(1.0, abs(ll)):
            converged = True
            break
        prev = ll
    if sig[0] > sig[1]:  # relabel: state 1 = turbulent (larger sd)
        pi = pi[::-1].copy()
        A = A[::-1, ::-1].copy()
        mu = mu[::-1].copy()
        sig = sig[::-1].copy()
    return {"A": A, "mu": mu, "sig": sig, "loglik_last": float(ll), "iterations": it, "converged": converged,
            "n_obs": int(x.shape[0])}


def stationary(A):
    """Stationary distribution of the two-state chain (the filter's prior)."""
    d = A[0, 1] + A[1, 0]
    return np.array([A[1, 0] / d, A[0, 1] / d])


# ----------------------------------------------------------------------------- causal filter
@njit(cache=True)
def _filter_row(x, pi0, A, mu, sig, out):
    """Forward recursion, filtered output: out[t] = P(state 1 | x_0..x_t). Uses no future observations."""
    a0p = pi0[0]
    a1p = pi0[1]
    for t in range(x.shape[0]):
        z0 = (x[t] - mu[0]) / sig[0]
        z1 = (x[t] - mu[1]) / sig[1]
        l0 = -0.5 * z0 * z0 - math.log(sig[0]) - LOG_SQRT_2PI
        l1 = -0.5 * z1 * z1 - math.log(sig[1]) - LOG_SQRT_2PI
        m = l0 if l0 > l1 else l1
        if t == 0:
            pr0 = a0p
            pr1 = a1p
        else:
            pr0 = a0p * A[0, 0] + a1p * A[1, 0]
            pr1 = a0p * A[0, 1] + a1p * A[1, 1]
        w0 = pr0 * math.exp(l0 - m)
        w1 = pr1 * math.exp(l1 - m)
        s = w0 + w1
        a0p = w0 / s
        a1p = w1 / s
        out[t] = a1p
    return out


def filter_1d(x, fit):
    x = np.ascontiguousarray(x, dtype=np.float64)
    out = np.empty(x.shape[0])
    return _filter_row(x, stationary(fit["A"]), fit["A"], fit["mu"], fit["sig"], out)


@njit(cache=True)
def filter_2d(X, pi0, A, mu, sig):
    """Row-wise causal filter for resampled bootstrap paths with fixed parameters (no refit)."""
    N, T = X.shape
    out = np.empty((N, T))
    for n in range(N):
        _filter_row(X[n], pi0, A, mu, sig, out[n])
    return out


# ----------------------------------------------------------------------------- expanding refit
def expanding_p(x, dates, train_from, refit_dates):
    """Causal P(turbulent) on an observation series with yearly expanding refits.

    Refit k fits on observations dated in [train_from, refit_dates[k]) only. The filter for that
    year runs over the observations up to the next refit (prior = stationary distribution of the
    fitted chain, first observation = train_from), and only the observations of that year take its
    output. P is NaN before the first refit (warm-up). Returns (p, list of fitted parameter dicts).
    """
    T = len(x)
    p = np.full(T, np.nan)
    fits = []
    lo = bisect.bisect_left(dates, train_from)
    bounds = [bisect.bisect_left(dates, rd) for rd in refit_dates] + [T]
    for k, rd in enumerate(refit_dates):
        i_k, end = bounds[k], bounds[k + 1]
        if i_k - lo < MIN_TRAIN or end <= i_k:
            continue
        fit = fit_hmm(x[lo:i_k])
        f = filter_1d(x[lo:end], fit)
        p[i_k:end] = f[i_k - lo:end - lo]
        fits.append({"refit_date": rd, "train_from": dates[lo], "train_to": dates[i_k - 1],
                     "A": fit["A"].tolist(), "mu": fit["mu"].tolist(), "sig": fit["sig"].tolist(),
                     "iterations": fit["iterations"], "converged": fit["converged"], "n_obs": fit["n_obs"],
                     "loglik_last": fit["loglik_last"]})
    return p, fits


# ----------------------------------------------------------------------------- arm weights
@njit(cache=True)
def hmm_arms(P, wk, v):
    """Arm weights from P(turbulent), K's weight wk and the E2 vote v (2-D arrays, rows = paths).

    NaN P (warm-up) gives K's weight in every arm and resets the gate and latch.
    H1 = wk * (1 - P).
    H2 = wk, zero while the gate G is on. G turns on when P > P_ON at two consecutive closes and turns
         off when P < P_OFF.
    H3 = wk, zero while the exit latch L is on. L is set at a close with the E2 vote on and G on when
         the vote was on and H3 held at the previous close, or L was already on; L clears when the vote
         or G turns off. H2 therefore acts only as an exit inside a vote-on episode.
    gate = G (diagnostic).
    """
    N, T = wk.shape
    h1 = np.empty((N, T))
    h2 = np.empty((N, T))
    h3 = np.empty((N, T))
    gate = np.zeros((N, T), dtype=np.bool_)
    for n in range(N):
        g = False
        lat = False
        prev_p = np.nan
        prev_v = False
        prev_w3 = 0.0
        for t in range(T):
            p = P[n, t]
            k = wk[n, t]
            vt = v[n, t]
            if np.isnan(p):
                h1[n, t] = k
                h2[n, t] = k
                h3[n, t] = k
                g = False
                lat = False
                prev_p = np.nan
                prev_v = vt
                prev_w3 = k
                continue
            if g:
                if p < P_OFF:
                    g = False
            else:
                if p > P_ON and prev_p > P_ON:
                    g = True
            new_lat = False
            if vt and g:
                if lat:
                    new_lat = True
                elif prev_v and prev_w3 > 0.0:
                    new_lat = True
            lat = new_lat
            w3 = 0.0 if lat else k
            h1[n, t] = k * (1.0 - p)
            h2[n, t] = 0.0 if g else k
            h3[n, t] = w3
            gate[n, t] = g
            prev_p = p
            prev_v = vt
            prev_w3 = w3
    return h1, h2, h3, gate
