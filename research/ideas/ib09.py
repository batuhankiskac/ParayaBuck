"""ib09 -- vol-of-vol sizing: scale down when realised volatility is itself unstable (bank A/B/C).

Arm specs (`docs/research/idea_bank_v1.json`, parameters frozen):

* **A** `weight *= clip(median_252(vov) / vov_t, 0.60, 1.00)`, `vov_t` = std of the last 20 daily
  `vol20` values
* **B** the same with floor 0.80
* **C** the same multiplier applied only while the vote is on (the early path unchanged)

Literal reading and the frozen conventions:

* `vol20` is `data.vol` (`fe_signals.vol_n` of the *held leg's* close) -- the same series production
  sizes with, so the multiplier modulates exactly the quantity the vol target reads.
* `vov_t` is the **sample** standard deviation (`ddof = 1`) of the trailing 20 values of `vol20`,
  i.e. the estimator `vol_n` itself uses one level down; it is NaN until 20 `vol20` values exist.
* `median_252(vov)` is the production trailing-median helper (`fe_signals.rolling_median_vol`,
  252 sessions, NaN until full).
* Wherever `vov` or its median is still NaN (the first 20 + 252 sessions of a panel) or `vov <= 0`,
  the multiplier is the neutral **1.00** -- the production weight is unchanged during the warm-up.
* A/B multiply the whole production target (E2 and the early path); C multiplies only the sessions
  where the vote is on (`factor == 1.0`), leaving the half-weight early path untouched.
* Execution stays K, `leg = 2x`, no cash yield. No extra data (own closes only) -> every window is
  applicable.
"""

from __future__ import annotations

import numpy as np

from research.fast_engine import fe_signals

ARMS = {
    "A": {"rule": "K", "leg": "2x", "cash_yield": "none", "floor": 0.60, "scope": "all"},
    "B": {"rule": "K", "leg": "2x", "cash_yield": "none", "floor": 0.80, "scope": "all"},
    "C": {"rule": "K", "leg": "2x", "cash_yield": "none", "floor": 0.60, "scope": "vote"},
}

VOV_N = 20
MED_WINDOW = 252


def _rolling_std(x, n):
    """Trailing `n`-value sample std (ddof 1) of a `(P, T)` series; NaN until `n` values exist.

    A leading-NaN series (the vol estimator's own warm-up) is handled by summing the finite values
    and counting them: a window with fewer than `n` finite values stays NaN instead of silently
    averaging NaNs as zeros.
    """
    a = np.atleast_2d(np.asarray(x, dtype=np.float64))
    fin = np.isfinite(a)
    z = np.nan_to_num(a, nan=0.0)
    zc = np.concatenate([np.zeros((a.shape[0], 1)), np.cumsum(z, axis=1)], axis=1)
    z2 = np.concatenate([np.zeros((a.shape[0], 1)), np.cumsum(z * z, axis=1)], axis=1)
    cnt = np.concatenate([np.zeros((a.shape[0], 1)), np.cumsum(fin, axis=1)], axis=1)
    w1 = zc[:, n:] - zc[:, :-n]
    w2 = z2[:, n:] - z2[:, :-n]
    wc = cnt[:, n:] - cnt[:, :-n]
    out = np.full_like(a, np.nan)
    out[:, n - 1 :] = np.where(wc == n, np.sqrt(np.maximum((w2 - w1 * w1 / n) / (n - 1), 0.0)),
                               np.nan)
    return out


def _vov_multiplier(vol, floor):
    """`clip(median_252(vov) / vov, floor, 1.0)`, neutral 1.0 where either input is unusable."""
    vov = _rolling_std(vol, VOV_N)
    med = fe_signals.rolling_median_vol(vov, MED_WINDOW)
    ok = np.isfinite(vov) & np.isfinite(med) & (vov > 0.0)
    ratio = med / np.where(ok, vov, 1.0)
    return np.where(ok, np.clip(ratio, float(floor), 1.0), 1.0)


def targets(data, spec):
    e2 = fe_signals.e2_up(data.price)
    comps = fe_signals.early_components(data.price)
    on_eff, factor = fe_signals.r2_state(data.price, e2, comps)
    wants = fe_signals.sized_wants(on_eff, factor, data.vol)
    mult = _vov_multiplier(data.vol, spec["floor"])
    if spec["scope"] == "vote":
        return wants * np.where(factor == 1.0, mult, 1.0)
    return wants * mult
