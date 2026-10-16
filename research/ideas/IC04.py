"""IC04 -- Kalman local-linear-trend slope vote. Idea bank `idea_bank_v2` (frozen arm text):

    A  2-of-3 vote with TSM252 replaced by Kalman slope (q = 1e-5, r = 1) > 0
    B  same with the significance test slope / sqrt(P_slope) > 1.0
    C  E2 as production and the early path's entry additionally requires Kalman slope > 0

Literal reading of the ambiguous points (documented, fixed before any screen):

* **Observed series:** the natural log of the panel's own price close (`data.price`, the series the
  production vote runs on). The log makes the filter scale-free: the gain sequence and `P` do not
  depend on the data, so on raw prices `slope / sqrt(P_slope)` would scale with the price *level*
  (a bootstrap chunk starts at 1.0, QQQ at ~50, an index at ~10,000); in log units it does not.
  The closest tested arm (deriv_vote_v1) also used the slope of the *log* close.
* **Model:** Harvey's local linear trend, state `x = (level, slope)`, `x_t = F x_{t-1} + w`,
  `F = [[1, 1], [0, 1]]`, `y_t = level_t + v`; the single "signal-to-noise ratio q" is applied to
  both state disturbances, `Q = q * I` with `q = 1e-5`, `R = r = 1`.
* **Initialisation:** `x_0 = (y_0, 0)`, diffuse-like `P_0 = 1e7 * I` (Durbin & Koopman's large-
  variance approximation); the slope vote is only read from session 1 on, and the engine's
  warm-up (the 252-session TSM/median windows production needs anyway) covers the transient.
* **Vote:** "slope > 0" / "slope / sqrt(P_slope) > 1.0" on the filtered (not smoothed) state at
  session t's close -- causal; decided at the close, filled at the next open by the engine.
* Arms A/B keep production's early re-entry path unchanged on top of the replaced vote; arm C
  keeps E2 and gates only the *entry* of the early path (`plugins_b3.helpers.r2_state_with_entry`,
  production's exit / override unchanged).

Because `Q`, `R`, `P_0` are fixed and there are no missing observations, the covariance recursion
(and therefore the Kalman gain) is the same for every path and is computed once per panel length;
only the state recursion runs over the panel, vectorised across paths.

Applicable on every window (closes only). No extra data, no engine change.
"""

from __future__ import annotations

import numpy as np

from research.fast_engine import fe_signals
from research.fast_engine.fe_config import DON_N, SMA_N
from research.plugins_b3 import helpers

ARMS = {
    "A": {"rule": "K", "leg": "2x", "cash_yield": "none", "mode": "vote_sign", "q": 1e-5, "r": 1.0},
    "B": {"rule": "K", "leg": "2x", "cash_yield": "none", "mode": "vote_z", "q": 1e-5, "r": 1.0,
          "z": 1.0},
    "C": {"rule": "K", "leg": "2x", "cash_yield": "none", "mode": "early_gate", "q": 1e-5,
          "r": 1.0},
}

P0 = 1e7


def gains(T, q, r, p0=P0):
    """The data-independent Kalman gain `(T, 2)` and filtered slope variance `(T,)`."""
    F = np.array([[1.0, 1.0], [0.0, 1.0]])
    Q = q * np.eye(2)
    Pm = p0 * np.eye(2)
    K = np.zeros((T, 2))
    pslope = np.full(T, np.nan)
    # session 0: update the prior with y_0 (x_0 = (y_0, 0) is the posterior mean we start from)
    for t in range(T):
        if t > 0:
            Pm = F @ Pm @ F.T + Q
        s = Pm[0, 0] + r
        k = Pm[:, 0] / s
        K[t] = k
        Pm = Pm - np.outer(k, Pm[0, :])
        pslope[t] = Pm[1, 1]
    return K, pslope


def kalman_slope(price, q, r):
    """`(slope, z)` `(P, T)`: filtered slope of log(price) and `slope / sqrt(P_slope)`."""
    y = np.log(np.atleast_2d(np.asarray(price, dtype=float)))
    if not np.isfinite(y).all():
        raise ValueError("IC04: the price series has a non-finite or non-positive value")
    P, T = y.shape
    K, pslope = gains(T, q, r)
    level = y[:, 0].copy()
    slope = np.zeros(P)
    out = np.zeros((P, T))
    for t in range(1, T):
        lp = level + slope
        innov = y[:, t] - lp
        level = lp + K[t, 0] * innov
        slope = slope + K[t, 1] * innov
        out[:, t] = slope
    z = out / np.sqrt(pslope)[None, :]
    return out, z


def targets(data, spec):
    price = data.price
    slope, z = kalman_slope(price, float(spec["q"]), float(spec["r"]))
    comps = fe_signals.early_components(price)
    mode = spec["mode"]
    if mode in ("vote_sign", "vote_z"):
        kal = slope > 0.0 if mode == "vote_sign" else z > float(spec["z"])
        kal[:, 0] = False
        s = fe_signals.sma_up(price, SMA_N).astype(np.int64)
        d = fe_signals.don_up(price, DON_N).astype(np.int64)
        e2 = (s + kal.astype(np.int64) + d) >= 2
        on_eff, factor = fe_signals.r2_state(price, e2, comps)
        return helpers.capped(on_eff, factor, data.vol)
    if mode == "early_gate":
        e2 = fe_signals.e2_up(price)
        gate = slope > 0.0
        gate[:, 0] = False
        on_eff, factor = helpers.r2_state_with_entry(price, e2, comps[0] & gate, comps)
        return helpers.capped(on_eff, factor, data.vol)
    raise ValueError(f"unknown mode {mode!r}")  # pragma: no cover
