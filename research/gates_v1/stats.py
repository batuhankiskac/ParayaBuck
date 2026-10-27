"""Probabilistic Sharpe ratio, minimum track record length and deflated Sharpe ratio.

Bailey & Lopez de Prado (2012, 2014). All Sharpe ratios here are PER PERIOD (non-annualised):
SR = mean(r) / std(r, ddof=1) of the return series. `skew` is the sample skewness, `kurt` the
PEARSON kurtosis (3 for a normal series; NOT excess kurtosis). Only the standard library is used
for the normal distribution (statistics.NormalDist).

Formulas
    V[SR_hat]      = (1 - skew*SR + (kurt-1)/4 * SR^2) / (T-1)          (sampling variance)
    PSR(SR*)       = Phi( (SR - SR*) * sqrt(T-1) / sqrt(1 - skew*SR + (kurt-1)/4 * SR^2) )
    MinTRL(SR*,a)  = 1 + (1 - skew*SR + (kurt-1)/4 * SR^2) * (z_{1-a} / (SR - SR*))^2
    E[max SR]_N    = sqrt(V) * ((1-g) Phi^-1(1-1/N) + g Phi^-1(1-1/(N e)))   (g = Euler-Mascheroni)
    DSR            = PSR(SR0) with SR0 = E[max SR] over N trials, V = variance of the trials' SRs.
"""

from __future__ import annotations

import math
from statistics import NormalDist

import numpy as np

EULER_GAMMA = 0.5772156649015329
_N01 = NormalDist()


def cdf(z: float) -> float:
    return _N01.cdf(z)


def ppf(p: float) -> float:
    return _N01.inv_cdf(p)


def sample_moments(returns) -> dict:
    """Per-period SR, sample skew, Pearson kurtosis, T for a return series (NaNs dropped)."""
    r = np.asarray(returns, dtype=float)
    r = r[np.isfinite(r)]
    t = int(r.size)
    if t < 3:
        raise ValueError("need at least 3 finite returns")
    mu = float(r.mean())
    sd = float(r.std(ddof=1))
    if sd <= 0.0:
        raise ValueError("zero-variance return series")
    d = r - mu
    m2 = float(np.mean(d * d))
    skew = float(np.mean(d ** 3) / m2 ** 1.5)
    kurt = float(np.mean(d ** 4) / m2 ** 2)
    return {"T": t, "sr": mu / sd, "skew": skew, "kurt": kurt, "mean": mu, "std": sd}


def _nonnorm(sr: float, skew: float, kurt: float) -> float:
    return 1.0 - skew * sr + (kurt - 1.0) / 4.0 * sr * sr


def sr_variance(sr: float, t: int, skew: float, kurt: float) -> float:
    """Variance of the Sharpe estimate (per period) under the given moments."""
    return _nonnorm(sr, skew, kurt) / (t - 1)


def psr(sr: float, sr_star: float, t: int, skew: float = 0.0, kurt: float = 3.0) -> float:
    """Probabilistic Sharpe ratio: P(true SR > sr_star)."""
    denom = _nonnorm(sr, skew, kurt)
    if denom <= 0.0:
        raise ValueError("non-positive non-normality term; check skew/kurt")
    z = (sr - sr_star) * math.sqrt(t - 1) / math.sqrt(denom)
    return cdf(z)


def min_trl(sr: float, sr_star: float = 0.0, skew: float = 0.0, kurt: float = 3.0,
            alpha: float = 0.05) -> float:
    """Minimum track record length (observations) for PSR(sr_star) >= 1 - alpha."""
    if sr <= sr_star:
        return math.inf
    z = ppf(1.0 - alpha)
    return 1.0 + _nonnorm(sr, skew, kurt) * (z / (sr - sr_star)) ** 2


def expected_max_sr(n_trials: float, var_trials: float) -> float:
    """Expected maximum of N independent zero-skill Sharpe estimates with variance var_trials."""
    if n_trials <= 1.0:
        return 0.0
    n = float(n_trials)
    core = (1.0 - EULER_GAMMA) * ppf(1.0 - 1.0 / n) + EULER_GAMMA * ppf(1.0 - 1.0 / (n * math.e))
    return math.sqrt(var_trials) * core


def dsr(sr: float, t: int, skew: float, kurt: float, n_trials: float, var_trials: float) -> dict:
    """Deflated Sharpe: PSR against the expected maximum over the trials."""
    sr0 = expected_max_sr(n_trials, var_trials)
    return {"dsr": psr(sr, sr0, t, skew, kurt), "sr0": sr0}


def var_trials_floor(t: int) -> float:
    """Smallest admissible trial-variance: the null sampling variance 1/(T-1) (SR = 0, normal)."""
    return 1.0 / (t - 1)


def n_eff_meff(corr_matrix) -> dict:
    """Effective number of independent trials from a correlation matrix of trial returns.

    Meff = 1 + (n - 1) * (1 - rho_bar), with rho_bar the average off-diagonal correlation
    (Nyholt-style). Returns the average correlation and the Meff of the block.
    """
    c = np.asarray(corr_matrix, dtype=float)
    n = c.shape[0]
    if n < 2:
        return {"n": n, "rho_bar": None, "meff": float(n)}
    off = c[~np.eye(n, dtype=bool)]
    rho = float(off.mean())
    return {"n": n, "rho_bar": rho, "meff": 1.0 + (n - 1) * (1.0 - rho)}
