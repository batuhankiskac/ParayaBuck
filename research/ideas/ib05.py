"""ib05 -- Hurst-exponent (rescaled-range) regime gate on the trend vote (idea_bank_v1, A/B/C).

Arm specs (`docs/research/idea_bank_v1.json`, parameters frozen):

* **A** E2 on iff the 2-of-3 vote holds AND `H(252, block sizes 10/20/40/80) > 0.50`
* **B** the same with `H > 0.55`
* **C** the early-path entry additionally requires `H > 0.50`

Literal reading and the frozen conventions (the estimator's free choices are fixed here, once):

* `H` is estimated by rescaled range on the trailing **252 returns** of `data.price`. For each block
  size `n` the window is cut into `J = floor(252 / n)` non-overlapping blocks of `n` returns counted
  from the window's start (the remainder is dropped, the standard R/S convention); the block's
  rescaled range is `R/S` with `R = max_k Z_k - min_k Z_k`, `Z_k` the cumulative sum of the block's
  returns net of the block mean, and `S` the block's sample standard deviation (`ddof = 1`);
  `RS_n` is the mean of `R/S` over the blocks of that size with `S > 0`.
* `H` is the ordinary-least-squares slope of `ln RS_n` on `ln n` over the four points
  `n = 10, 20, 40, 80` with equal weights. If any `RS_n` is undefined (a block size with no usable
  block, or fewer than 252 returns of history) the session's `H` is NaN and the gate is False.
* The gate enters the **vote** for A/B (`e2 = fe_signals.e2_up(price) & (H > threshold)`), so the
  early path sees the gated vote as off exactly as it sees the production vote; for C the
  production vote is kept and the gate is added to the early path's *entry* only (the vectorised
  state machine of `ib01`, verified against `fe_signals.r2_state`).
* Sizing (`fe_signals.sized_wants`), the R2 early-path mechanics and the K execution layer are
  production's. No extra data: every window is applicable (closes only).
* Cost note: the estimator is the expensive one of the bank ("heavy estimator") -- the R/S sums are
  O(1016) vectorised slice operations per panel, which a 256-path bootstrap chunk amortises over
  every path in the chunk (no Python loop over paths or sessions).
"""

from __future__ import annotations

import numpy as np

from research.fast_engine import fe_signals

ARMS = {
    "A": {"rule": "K", "leg": "2x", "cash_yield": "none", "h_min": 0.50, "gate": "vote"},
    "B": {"rule": "K", "leg": "2x", "cash_yield": "none", "h_min": 0.55, "gate": "vote"},
    "C": {"rule": "K", "leg": "2x", "cash_yield": "none", "h_min": 0.50, "gate": "early"},
}

WINDOW = 252
BLOCKS = (10, 20, 40, 80)


def _hurst(price, window=WINDOW, blocks=BLOCKS):
    """`(P, T)` rolling R/S Hurst exponent of the trailing `window` returns; NaN until available."""
    p = np.atleast_2d(np.asarray(price, dtype=np.float64))
    P, T = p.shape
    out = np.full((P, T), np.nan)
    if T - 1 < window:
        return out
    r = p[:, 1:] / p[:, :-1] - 1.0  # r[:, i] is the return of session i + 1
    c = np.concatenate([np.zeros((P, 1)), np.cumsum(r, axis=1)], axis=1)
    c2 = np.concatenate([np.zeros((P, 1)), np.cumsum(r * r, axis=1)], axis=1)
    t0, t1 = window, T - 1  # sessions whose trailing 252 returns exist
    n_t = t1 - t0 + 1
    lx = np.log(np.asarray(blocks, dtype=np.float64))
    lx -= lx.mean()
    varx = float((lx * lx).sum())
    ys = []
    for n in blocks:
        acc = np.zeros((P, n_t))
        cnt = np.zeros((P, n_t))
        for j in range(window // n):
            o = j * n - window  # the block's first return index is t + o
            base = c[:, t0 + o : t1 + 1 + o]
            span = c[:, t0 + o + n : t1 + 1 + o + n] - base
            hi = np.zeros((P, n_t))
            lo = np.zeros((P, n_t))
            for k in range(n + 1):
                z = c[:, t0 + o + k : t1 + 1 + o + k] - base
                z -= (k / n) * span
                np.maximum(hi, z, out=hi)
                np.minimum(lo, z, out=lo)
            d1 = c[:, t0 + o + n : t1 + 1 + o + n] - base
            d2 = c2[:, t0 + o + n : t1 + 1 + o + n] - c2[:, t0 + o : t1 + 1 + o]
            sdev = np.sqrt(np.maximum((d2 - d1 * d1 / n) / (n - 1), 0.0))
            ok = sdev > 0
            acc += np.where(ok, (hi - lo) / np.where(ok, sdev, 1.0), 0.0)
            cnt += ok
        rs = np.where(cnt > 0, acc / np.where(cnt > 0, cnt, 1.0), np.nan)
        ys.append(np.where(rs > 0, np.log(rs), np.nan))
    y = np.stack(ys)
    good = np.isfinite(y).all(axis=0)
    yf = np.where(np.isfinite(y), y, 0.0)
    ybar = yf.mean(axis=0)
    slope = (lx[:, None, None] * (yf - ybar)).sum(axis=0) / varx
    out[:, t0 : t1 + 1] = np.where(good, slope, np.nan)
    return out


def _r2_state_gated(price, e2_on, entry_gate):
    """`fe_signals.r2_state` with a per-session early-entry gate (see `ib01`, identical code)."""
    price = np.atleast_2d(np.asarray(price, dtype=np.float64))
    e2_on = np.atleast_2d(np.asarray(e2_on, dtype=bool))
    gate = np.atleast_2d(np.asarray(entry_gate, dtype=bool))
    sma50_up, vol_p, med_p, lag_cond = fe_signals.early_components(price)
    entry = (~e2_on) & sma50_up & np.isfinite(med_p) & (vol_p < med_p) & lag_cond & gate
    reset = e2_on | (~sma50_up)
    idx = np.arange(price.shape[1])
    last_entry = np.maximum.accumulate(np.where(entry, idx, -1), axis=1)
    last_reset = np.maximum.accumulate(np.where(reset, idx, -1), axis=1)
    active = last_entry > last_reset
    return e2_on | active, np.where(e2_on, 1.0, np.where(active, 0.5, 0.0))


def targets(data, spec):
    calm = _hurst(data.price) > float(spec["h_min"])  # NaN (warm-up) -> False
    if spec["gate"] == "vote":
        e2 = fe_signals.e2_up(data.price) & calm
        on_eff, factor = fe_signals.r2_state(data.price, e2, fe_signals.early_components(data.price))
    else:
        on_eff, factor = _r2_state_gated(data.price, fe_signals.e2_up(data.price), calm)
    return fe_signals.sized_wants(on_eff, factor, data.vol)
