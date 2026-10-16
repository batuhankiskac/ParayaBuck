"""batch_screen.deflation -- the multiple-testing correction of the stage-2 decision.

Every arm ever registered is a trial; the ledger's N is the count. The framework's decision
statistic is the after-tax CAGR advantage over K on the campaign's block bootstrap (seed 20261011,
block 20, 1,000 paths, $100k, progressive = B1-recent), whose per-path Δ vector every screened arm
carries in its result file. Because **the resampled paths are identical for every arm** (the draw
is a deterministic function of (seed, block, path index)), the Δ vectors are paired across arms,
which is what makes a max-statistic procedure valid.

Three tests, all documented in README.md:

`white_rc`          White's (2000) Reality Check: p = P*(max_a t*_a >= max_a t_a)
`rw_stepdown`       Romano-Wolf (2005) stepM, studentized: stepdown p-values, rejections are a
                    prefix of the arms ordered by t
`deflated_sharpe`   Bailey & Lopez de Prado (2014): the probability that the selected arm's
                    advantage-Sharpe exceeds the expected maximum of N independent trials

The null distribution of the max statistic is built by a *second-level* bootstrap: resample the
B per-path Δ values with replacement (the same resampled path indices for every arm, preserving
their dependence), recenter each arm's resample to its own mean, studentize, and take the max over
the still-active arms. Assumptions: (1) the first-level block bootstrap is a valid approximation of
the estimator's sampling distribution; (2) arms are paired on the same paths; (3) the second-level
resampling adds no information beyond that distribution; (4) 20-session blocks cannot capture
serial dependence longer than 20 sessions (the campaign's pre-existing bootstrap choice).
"""

from __future__ import annotations

import math
from statistics import NormalDist

import numpy as np

_N = NormalDist()
EULER = 0.5772156649015329


def _matrix(deltas: dict) -> tuple:
    arms = sorted(deltas)
    return arms, np.asarray([deltas[a] for a in arms], dtype=np.float64).T  # (B, A)


def _null(d, resamples, seed):
    """The recentered, studentized second-level bootstrap of the per-arm means."""
    rng = np.random.default_rng(seed)
    B, A = d.shape
    idx = rng.integers(0, B, size=(resamples, B))
    boots = d[idx]                       # (R, B, A)
    means = boots.mean(axis=1)           # (R, A)
    ses = boots.std(axis=1, ddof=1) / math.sqrt(B)
    theta = d.mean(axis=0)
    with np.errstate(divide="ignore", invalid="ignore"):
        t = np.where(ses > 0, (means - theta) / ses, np.where(means > theta, np.inf, -np.inf))
    return theta, t


def _observed(d):
    B = d.shape[0]
    theta = d.mean(axis=0)
    se = d.std(axis=0, ddof=1) / math.sqrt(B)
    with np.errstate(divide="ignore", invalid="ignore"):
        t = np.where(se > 0, theta / se, np.where(theta > 0, np.inf, -np.inf))
    return theta, se, t


def white_rc(deltas: dict, resamples=2000, seed=20261016) -> dict:
    """White's Reality Check p-value of ``max_a mean(Δ_a) <= 0``."""
    arms, d = _matrix(deltas)
    _theta, t_star = _null(d, resamples, seed)
    _theta_o, _se, t_obs = _observed(d)
    p = float(np.mean(t_star.max(axis=1) >= t_obs.max()))
    return {"p_value": p, "n_arms": len(arms), "resamples": resamples,
            "best_arm": arms[int(np.argmax(t_obs))], "t_max": float(t_obs.max())}


def rw_stepdown(deltas: dict, alpha=0.05, resamples=2000, seed=20261016) -> dict:
    """Romano-Wolf stepdown p-values (studentized max statistic) for every arm.

    The arms are ordered by the observed t (descending); at step `k` the reference set is the
    arms still under test (the positional suffix, since a step that fails to reject stops the
    procedure), and the p-values are made monotone along that order. Rejections are therefore a
    prefix; `rejected` lists the arms whose adjusted p <= alpha.
    """
    arms, d = _matrix(deltas)
    _theta_o, _se, t_obs = _observed(d)
    _theta, t_star = _null(d, resamples, seed)
    order = list(np.argsort(-t_obs))
    adjusted = {}
    prev = 0.0
    for pos, a in enumerate(order):
        rest = order[pos:]
        m = t_star[:, rest].max(axis=1)
        prev = max(prev, float(np.mean(m >= t_obs[a])))
        adjusted[arms[a]] = prev
    rejected = [a for a in arms if adjusted[a] <= alpha]
    return {
        "alpha": alpha, "resamples": resamples,
        "p_adjusted": adjusted,
        "p_unadjusted": {arms[i]: float(np.mean(t_star[:, i] >= t_obs[i])) for i in range(len(arms))},
        "t": {arms[i]: float(t_obs[i]) for i in range(len(arms))},
        "order": [arms[i] for i in order],
        "rejected": sorted(rejected),
    }


def deflated_sharpe(sr_obs: float, n_trials: int, var_sr: float, n_obs: int, skew: float,
                    kurt: float) -> dict:
    """Bailey & Lopez de Prado (2014) deflated Sharpe ratio of one arm.

    `sr_obs` is the arm's advantage Sharpe (mean/std of its per-path Δ), `n_obs` the number of
    bootstrap paths it was estimated from, `var_sr` the cross-sectional variance of the trial
    Sharpes, `n_trials` N (the ledger count), `skew`/`kurt` of the Δ sample (raw kurtosis).
    """
    n = max(2, int(n_trials))
    e = EULER
    sr0 = math.sqrt(max(var_sr, 0.0)) * ((1 - e) * _N.inv_cdf(1 - 1 / n) + e * _N.inv_cdf(1 - 1 / (n * e)))
    den = 1 - skew * sr_obs + (kurt - 1) / 4 * sr_obs**2
    if den <= 0:
        return {"dsr": 0.0, "sr0": sr0, "note": "non-normal correction non-positive"}
    z = (sr_obs - sr0) * math.sqrt(max(n_obs - 1, 1)) / math.sqrt(den)
    return {"dsr": float(_N.cdf(z)), "sr0": sr0, "sr_obs": sr_obs, "z": float(z),
            "n_trials": n, "n_obs": int(n_obs), "var_sr": var_sr}


def arm_sharpe(delta, alpha=0.05) -> dict:
    """The advantage-Sharpe of one Δ vector plus the normal-theory one-sided p-value.

    A zero-variance sample carries no evidence either way: `sharpe = 0` and `p = 1`.
    """
    d = np.asarray(delta, dtype=np.float64)
    m, s = float(d.mean()), float(d.std(ddof=1))
    if not (s > 0):
        return {"sharpe": 0.0, "mean_pp": m * 100, "sd_pp": 0.0, "n": int(len(d)), "skew": 0.0,
                "kurt": 3.0, "p_normal": 1.0, "p": 1.0}
    sr = m / s
    z = sr * math.sqrt(len(d))
    return {"sharpe": sr, "mean_pp": m * 100, "sd_pp": s * 100, "n": int(len(d)),
            "skew": float(((d - m) ** 3).mean() / s**3),
            "kurt": float(((d - m) ** 4).mean() / s**4),
            "p_normal": float(1 - _N.cdf(z)), "p": float(1 - _N.cdf(z))}


def deflate(deltas: dict, n_trials: int, alpha=0.05, resamples=2000, seed=20261016) -> dict:
    """The whole deflation pass over a batch's (or the ledger's) Δ vectors."""
    out = {"n_trials": int(n_trials), "n_arms_with_delta": len(deltas), "alpha": alpha,
           "resamples": resamples, "seed": seed}
    if not deltas:
        out["note"] = "no Δ vectors; nothing to deflate"
        return out
    out["white_rc"] = white_rc(deltas, resamples, seed)
    out["rw_stepdown"] = rw_stepdown(deltas, alpha, resamples, seed)
    sh = {a: arm_sharpe(v) for a, v in deltas.items()}
    sr = np.array([sh[a]["sharpe"] for a in sorted(sh)])
    var_sr = float(sr.var(ddof=1)) if sr.size > 1 else 0.0
    dsr = {}
    for a in sorted(sh):
        cell = deflated_sharpe(sh[a]["sharpe"], n_trials, var_sr, sh[a]["n"], sh[a]["skew"], sh[a]["kurt"])
        cell["p_normal"] = sh[a]["p_normal"]
        cell["pass_5pct"] = bool(cell["dsr"] >= 0.95)
        dsr[a] = cell
    out["sharpe"] = sh
    out["var_sr"] = var_sr
    out["deflated_sharpe"] = dsr
    best = max(sorted(sh), key=lambda a: sh[a]["sharpe"])
    out["best_by_sharpe"] = best
    out["declared"] = sorted(a for a in dsr if dsr[a]["pass_5pct"] and a in out["rw_stepdown"]["rejected"])
    return out


__all__ = ["arm_sharpe", "deflate", "deflated_sharpe", "rw_stepdown", "white_rc"]
