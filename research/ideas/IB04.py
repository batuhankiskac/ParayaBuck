"""IB04 -- variance-ratio (Lo-MacKinlay) trend vote, as a batch_screen plugin.

Idea bank `docs/research/idea_bank_v1.json` (main), three arms, parameters frozen by the spec:

    A  the production 2-of-3 vote with TSM252 replaced by ``VR_5 > 1.0``
    B  ... ``VR_20 > 1.0``
    C  ``E2 as production AND VR_5 > 1.0`` (a gate on the production target series)

Everywhere the spec leaves a detail open the most literal reading is fixed here:

* **Estimator.** At session ``t``, over the trailing ``n = 252`` one-session simple returns
  ``r[t-251..t]`` of ``data.price`` (the series the production vote runs on):
  ``v1`` = sample variance (ddof 1) of those 252 returns, ``vq`` = sample variance (ddof 1) of
  the ``n - q + 1`` *overlapping* ``q``-session sums inside the same window (the sums ending at
  ``t-n+q .. t``), ``VR_q = vq / (q * v1)``, the vote is ``VR_q > 1.0``.  Both variances are
  sample variances over the same block: that is the only normalisation for which ``VR = 1``
  exactly under i.i.d. returns (no mean correction, the Lo-MacKinlay statistic in its overlapping
  form).
* **Not computable -> the vote is False.**  ``t < 252`` has no full window; the production vote's
  own components also return False while their window is short (``fe_signals.sma_up`` /
  ``tsm_up_price``), so a missing VR counts as "not up" and not as "neutral".  (Spec says
  "TSM252 replaced by VR_q", i.e. slot for slot.)
* **Arms A/B keep the production early re-entry**: the vote feeds ``fe_signals.r2_state``, so the
  half-weight path and the sizing are unchanged -- production is K = R2 and the idea replaces one
  vote only.
* **Arm C gates the whole production target series** (full vote *and* early path, sized) on
  ``VR_5 > 1``: a session whose VR_5 is <= 1 (or not computable) holds nothing.
* **Inputs**: the panel's own price close only -- every window (main, the six OOS windows, the 21
  ETFs) is applicable, no extra series, no `NotApplicable`.

`targets` is pure vectorised numpy; the execution layer is the engine's `K` (the fill rule is
unchanged, as the idea's `plugin.model` says).
"""

from __future__ import annotations

import numpy as np

from research.fast_engine import fe_signals
from research.fast_engine.fe_config import DON_N, SMA_N

ARMS = {
    "A": {"rule": "K", "leg": "2x", "cash_yield": "none", "q": 5, "vr_threshold": 1.0,
          "window": 252, "mode": "vote"},
    "B": {"rule": "K", "leg": "2x", "cash_yield": "none", "q": 20, "vr_threshold": 1.0,
          "window": 252, "mode": "vote"},
    "C": {"rule": "K", "leg": "2x", "cash_yield": "none", "q": 5, "vr_threshold": 1.0,
          "window": 252, "mode": "gate"},
}


def _prefix(a):
    """``p[k] = a[:, :k].sum()`` -- a leading-zero prefix sum, length ``T + 1``."""
    return np.concatenate([np.zeros((a.shape[0], 1)), np.cumsum(a, axis=1)], axis=1)


def variance_ratio(price, q, n=252):
    """``VR_q`` on the trailing ``n`` sessions of each row; ``(P, T)``, NaN where not computable."""
    price = np.atleast_2d(np.asarray(price, dtype=np.float64))
    P, T = price.shape
    out = np.full((P, T), np.nan)
    if T <= n:
        return out
    r = np.zeros((P, T))
    r[:, 1:] = price[:, 1:] / price[:, :-1] - 1.0          # r[:, 0] is undefined, only a filler
    p1, p2 = _prefix(r), _prefix(r * r)
    # the one-session variance over the window [t-n+1, t] of returns-into-session, for t = n..T-1
    # (the engine's own convention: the first window is r[1..n], so a full n-session history)
    s1 = p1[:, n + 1 :] - p1[:, 1 : T - n + 1]
    s2 = p2[:, n + 1 :] - p2[:, 1 : T - n + 1]
    v1 = (s2 - s1 * s1 / n) / (n - 1)
    # the overlapping q-session sums Q[i] = sum(r[i-q+1..i])
    Q = np.zeros((P, T))
    Q[:, q - 1:] = p1[:, q:] - p1[:, : T - q + 1]
    q1, q2 = _prefix(Q), _prefix(Q * Q)
    m = n - q + 1                                          # #(q-sums in the window)
    sq = q1[:, n + 1 :] - q1[:, q : T - n + q]
    sq2 = q2[:, n + 1 :] - q2[:, q : T - n + q]
    vq = (sq2 - sq * sq / m) / (m - 1)
    out[:, n:] = np.where(v1 > 0, vq / (q * np.where(v1 > 0, v1, 1.0)), np.nan)
    return out


def targets(data, spec):
    """The production target series with `TSM252` swapped for the variance-ratio vote (A/B), or
    the production series gated on `VR_5 > 1` (C)."""
    price = data.price
    up = np.nan_to_num(variance_ratio(price, spec["q"], spec["window"]), nan=-np.inf) > spec["vr_threshold"]
    comps = fe_signals.early_components(price)
    if spec["mode"] == "gate":
        e2 = fe_signals.e2_up(price)
        on_eff, factor = fe_signals.r2_state(price, e2, comps)
        return fe_signals.sized_wants(on_eff, factor, data.vol) * up
    s = fe_signals.sma_up(price, SMA_N).astype(int)
    d = fe_signals.don_up(price, DON_N).astype(int)
    e2 = (s + up.astype(int) + d) >= 2
    on_eff, factor = fe_signals.r2_state(price, e2, comps)
    return fe_signals.sized_wants(on_eff, factor, data.vol)
