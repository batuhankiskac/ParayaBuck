"""hrp_sleeve_v1 -- the three sleeve allocators (pre-registered; PREREGISTRATION.md section 4).

All three take a covariance matrix of daily simple returns over the trailing 252 sessions (sample
covariance, no shrinkage) and return long-only weights summing to 1 over the valid universe.

  ivol  naive inverse-volatility weights, w_i ∝ 1/σ_i
  hrp   Lopez de Prado (2016): correlation distance d = sqrt(½(1-ρ)), single linkage, quasi-
        diagonal leaf order, recursive bisection with inverse-variance cluster weights
  herc  Raffinot (2017) hierarchical ERC: the same tree and quasi-diagonal order as hrp, but
        within-cluster weights are equal-risk-contribution (solved numerically) and the bisection
        split alpha = 1 - V_L/(V_L+V_R) uses those cluster variances
"""
from __future__ import annotations

import numpy as np
from scipy.cluster.hierarchy import linkage
from scipy.optimize import minimize
from scipy.spatial.distance import squareform


def ivol(cov):
    v = np.sqrt(np.diag(cov))
    w = 1.0 / v
    return w / w.sum()


def _erc(cov):
    """equal risk contribution weights (long-only, sum 1) for a small covariance matrix."""
    n = cov.shape[0]
    if n == 1:
        return np.ones(1)
    w0 = ivol(cov)

    def obj(w):
        sig = w @ cov @ w
        rc = w * (cov @ w) / sig
        return float(np.sum((rc - rc.mean()) ** 2))

    res = minimize(obj, w0, method="SLSQP", bounds=[(1e-9, 1.0)] * n,
                   constraints=[{"type": "eq", "fun": lambda w: w.sum() - 1.0}],
                   options={"ftol": 1e-14, "maxiter": 500})
    w = np.clip(res.x, 0.0, None)
    return w / w.sum()


def _quasi_order(cov):
    corr = cov / np.sqrt(np.outer(np.diag(cov), np.diag(cov)))
    dist = np.sqrt(np.clip(0.5 * (1.0 - corr), 0.0, None))
    np.fill_diagonal(dist, 0.0)
    link = linkage(squareform(dist, checks=False), method="single")
    n = cov.shape[0]

    def expand(node):
        if node < n:
            return [node]
        k = int(node) - n
        return expand(int(link[k, 0])) + expand(int(link[k, 1]))

    return expand(2 * n - 2)


def _bisect(cov, order, mode):
    w = np.ones(len(order))
    clusters = [list(range(len(order)))]
    while clusters:
        c = clusters.pop()
        if len(c) <= 1:
            continue
        half = len(c) // 2
        left, right = c[:half], c[half:]
        vl, vr = _cluster_var(cov, order, left, mode), _cluster_var(cov, order, right, mode)
        alpha = 1.0 - vl / (vl + vr)
        for k in left:
            w[k] *= alpha
        for k in right:
            w[k] *= 1.0 - alpha
        clusters.append(left)
        clusters.append(right)
    out = np.zeros_like(w)
    out[order] = w
    return out / out.sum()


def _cluster_var(cov, order, idx, mode):
    sub = cov[np.ix_([order[k] for k in idx], [order[k] for k in idx])]
    if mode == "hrp":
        iv = 1.0 / np.diag(sub)
        iv = iv / iv.sum()
    else:
        iv = _erc(sub)
    return float(iv @ sub @ iv)


def hrp(cov):
    n = cov.shape[0]
    if n == 1:
        return np.ones(1)
    order = _quasi_order(cov)
    return _bisect(cov, order, "hrp")


def herc(cov):
    n = cov.shape[0]
    if n == 1:
        return np.ones(1)
    order = _quasi_order(cov)
    return _bisect(cov, order, "herc")
