"""IB33 -- realised-skewness crash-risk scaling, as a batch_screen plugin.

Idea bank `docs/research/idea_bank_v1.json` (main), three arms, parameters frozen by the spec:

    A  ``weight *= 0.75 while skew252 < -0.5``
    B  ``weight *= 0.75 while skew252 < -1.0``
    C  ``weight *= clip(1 + skew252, 0.60, 1.10)``

Readings fixed here:

* **Skewness estimator.**  ``skew252`` at session ``t`` is the population (method-of-moments)
  sample skewness of the trailing 252 one-session simple returns of `data.price`:
  ``m3 / m2**1.5`` with ``m2 = mean((r-mu)^2)``, ``m3 = mean((r-mu)^3)`` over the 252 returns --
  the standard realised-skewness estimator (`scipy.stats.skew`'s default, bias=True).  The
  bias-corrected variant differs by ~0.2 % at n = 252, far below the -0.5/-1.0 thresholds.
* **NaN -> multiplier 1**: before the window is full (t < 252) there is no skewness, so the arm is
  production there ("no information" rather than an assumed tail); the same for a session whose
  returns are not finite.
* **Cap.**  The multiplier is applied to the *pre-cap* production target and the total cap 0.98 is
  re-applied (the production invariant; arm C's 1.10 would otherwise lever the account past
  100 %).  Arms A/B (multiplier <= 1) therefore coincide with the production target times 0.75
  while their condition holds.
* **Inputs**: the panel's own price close -- every window is applicable, no `NotApplicable`.

`targets` is pure vectorised numpy; the execution layer is the engine's `K`.
"""

from __future__ import annotations

import numpy as np

from research.fast_engine import fe_signals
from research.fast_engine.fe_config import CAP

ARMS = {
    "A": {"rule": "K", "leg": "2x", "cash_yield": "none", "mode": "below", "thr": -0.5,
          "factor": 0.75, "window": 252},
    "B": {"rule": "K", "leg": "2x", "cash_yield": "none", "mode": "below", "thr": -1.0,
          "factor": 0.75, "window": 252},
    "C": {"rule": "K", "leg": "2x", "cash_yield": "none", "mode": "linear", "lo": 0.60,
          "hi": 1.10, "window": 252},
}


def _cap98(raw):
    """`fe_signals.sized_wants`' total cap, on a pre-cap weight panel."""
    fcap = np.where(raw > CAP, CAP / np.where(raw > 0, raw, 1.0), 1.0)
    return raw * fcap


def _prefix(a):
    """``p[k] = a[:, :k].sum()`` -- a leading-zero prefix sum, length ``T + 1``."""
    return np.concatenate([np.zeros((a.shape[0], 1)), np.cumsum(a, axis=1)], axis=1)


def skewness(price, n=252):
    """Trailing ``n``-session population skewness of the simple returns; ``(P, T)``, NaN if short."""
    price = np.atleast_2d(np.asarray(price, dtype=np.float64))
    P, T = price.shape
    out = np.full((P, T), np.nan)
    if T <= n:
        return out
    r = np.zeros((P, T))
    r[:, 1:] = price[:, 1:] / price[:, :-1] - 1.0
    p1, p2, p3 = _prefix(r), _prefix(r * r), _prefix(r * r * r)
    s1 = p1[:, n:] - p1[:, : T - n + 1]
    s2 = p2[:, n:] - p2[:, : T - n + 1]
    s3 = p3[:, n:] - p3[:, : T - n + 1]
    mu = s1 / n
    m2 = s2 / n - mu * mu
    m3 = s3 / n - 3.0 * mu * (s2 / n) + 2.0 * mu ** 3
    out[:, n - 1:] = np.where(m2 > 0, m3 / np.where(m2 > 0, m2, 1.0) ** 1.5, np.nan)
    return out


def targets(data, spec):
    """The production target series scaled by the realised-skewness multiplier, capped at 0.98."""
    sk = skewness(data.price, spec["window"])
    if spec["mode"] == "below":
        mult = np.where(np.nan_to_num(sk, nan=np.inf) < spec["thr"], spec["factor"], 1.0)
    else:
        mult = np.clip(1.0 + np.nan_to_num(sk, nan=0.0), spec["lo"], spec["hi"])
    e2 = fe_signals.e2_up(data.price)
    comps = fe_signals.early_components(data.price)
    on_eff, factor = fe_signals.r2_state(data.price, e2, comps)
    raw = on_eff * factor * fe_signals.vt_of(data.vol)
    return _cap98(raw * mult)
