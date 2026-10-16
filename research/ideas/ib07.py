"""IB07 -- a mean-reverting GARCH(1,1) volatility forecast instead of backward-looking vol20.

Per `docs/research/idea_bank_v1.json` IB07, the sizing input `vol20` is replaced by (or blended
with) the one-step GARCH(1,1) forecast of the arm's own instrument close:

    sigma^2_{t+1} = omega + 0.10 * r_t^2 + 0.85 * sigma^2_t,  omega = 7.9365e-06

(`omega = (0.20^2 / 252) * 0.05`, i.e. an unconditional annualised volatility of 20 % at the fixed
`alpha + beta = 0.95`; `r` are daily simple returns, so `sigma2` is a daily variance and
`sigma_ann = sqrt(252 * sigma2)`, exactly as the arm text writes it). Everything else -- the E2
vote, the half-weight early re-entry, the 0.98 cap and the execution layer (rule `K`) -- is
production.

    A  vt = min(1, 0.35 / sigma_ann)                        (the forecast alone)
    B  vt = min(1, 0.35 / max(vol20, sigma_ann))            (never size above realised vol)
    C  vt = min(1, 0.35 / (0.5 * vol20 + 0.5 * sigma_ann))   (half-way blend)

Fixed parameters: `omega`, `alpha`, `beta` and the seed are frozen by the arm text; the seed is
"the sample variance of the first 20 returns" (`ddof = 1`, over the first 20 sessions' returns),
from which the recursion runs forward. Before the seed the forecast is NaN, and the engine's own
`vt_of` convention (NaN -> `vt = 1.0`) applies, exactly as it does for `vol20` in its own 20-session
warm-up. `vol20` here is the panel's sizing input `data.vol` (`fe_signals.vol_n` of the held leg's
close, the same series production sizes with), and the GARCH returns are built from that same
`data.close`, so A/B/C all size off one instrument's close.

No extra data, no engine change, every window applicable.
"""

from __future__ import annotations

import numpy as np

from research.fast_engine import fe_signals
from research.plugins_b3 import helpers

#: sessions per year (the arm text's `sqrt(252 * sigma2)`)
ANNUAL = 252.0

#: the frozen GARCH(1,1) constants of the arm text, declared in every arm's spec
GARCH = {"omega": 7.9365e-06, "alpha": 0.10, "beta": 0.85, "seed_n": 20}

ARMS = {
    "A": {"rule": "K", "leg": "2x", "cash_yield": "none", "mode": "garch", **GARCH},
    "B": {"rule": "K", "leg": "2x", "cash_yield": "none", "mode": "max", **GARCH},
    "C": {"rule": "K", "leg": "2x", "cash_yield": "none", "mode": "mix", **GARCH},
}


def garch_sigma(close, omega, alpha, beta, seed_n):
    """`(P, T)` annualised one-step GARCH(1,1) forecast `sqrt(252 * sigma2)`, NaN before the seed.

    The seed at session `seed_n` is the sample variance (`ddof = 1`) of that session's trailing
    `seed_n` returns (`r_1 .. r_20`); the recursion then runs one session at a time
    (`sigma2_{t} = omega + alpha * r_{t-1}^2 + beta * sigma2_{t-1}`), vectorised over paths.
    """
    close = np.atleast_2d(np.asarray(close, dtype=np.float64))
    P, T = close.shape
    out = np.full((P, T), np.nan)
    if T <= seed_n or not np.isfinite(close).all():
        return out
    r = close[:, 1:] / close[:, :-1] - 1.0  # r[:, j] is session j+1's return
    head = r[:, :seed_n]
    state = np.where(np.isfinite(head).all(axis=1), head.var(axis=1, ddof=1), np.nan)
    out[:, seed_n] = np.sqrt(ANNUAL * state)
    for t in range(seed_n + 1, T):
        state = omega + alpha * r[:, t - 1] ** 2 + beta * state
        out[:, t] = np.sqrt(ANNUAL * state)
    return out


def targets(data, spec):
    e2 = fe_signals.e2_up(data.price)
    comps = fe_signals.early_components(data.price)
    on_eff, factor = fe_signals.r2_state(data.price, e2, comps)
    sigma = garch_sigma(data.close, spec["omega"], spec["alpha"], spec["beta"], spec["seed_n"])
    mode = spec["mode"]
    if mode == "garch":
        vol_in = sigma
    elif mode == "max":
        vol_in = np.maximum(data.vol, sigma)
    elif mode == "mix":
        vol_in = 0.5 * data.vol + 0.5 * sigma
    else:  # pragma: no cover - the specs above are the only ones registered
        raise ValueError(f"unknown mode {mode!r}")
    return helpers.capped(on_eff, factor, vol_in)
