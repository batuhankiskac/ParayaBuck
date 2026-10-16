"""IC30 -- stock-bond correlation regime (idea_bank_v2, arms A/B/C).

Arm specs (`docs/research/idea_bank_v2.json`, parameters frozen -- not tuned here):

    A  weight *= 0.75 while corr60(QQQ, IEF) > +0.30
    B  weight *= 0.50 while corr60(QQQ, IEF) > +0.50
    C  3-of-4 vote {SMA200, TSM252, DON126, corr60 <= +0.30}, on iff >= 3

Literal reading and the frozen conventions:

* **corr60** is the Pearson correlation of the 60 daily simple returns ending at session t of
  "QQQ" -- the arm's own `data.price` (QQQ's price close on the main window, the market's own
  index/ETF close on the OOS and instrument windows: the series the production vote runs on) --
  and of the registered Yahoo `IEF` close (extra key `ief`, 2002-07-30 ->, last value carried over
  sessions IEF did not trade).
* **Paired returns.**  Each column pairs the price return into that column with the IEF return of
  the *same* session it replays: the panel's own session on a real window, the drawn source
  session inside a bootstrap chunk (``extra_days[extra_index[p, t]]``, `extra.aligned`'s
  convention).  The IEF return is computed on the pool's own calendar before the draw, so the
  chunk's correlation is between genuinely simultaneous returns (differencing the replayed IEF
  *levels* across block boundaries would add spurious jumps).
* **Undefined -> neutral.**  A correlation whose 60-session window contains an undefined return
  (before IEF exists, the window's first sessions, a chunk window touching a pre-2002 source
  session) or has zero variance is undefined: A and B do not act and C drops the 4th vote
  (production's 2-of-3), the convention of `IB12`'s vote arm.
* **Scope.**  A and B multiply the whole production target (E2 and the early path); C feeds its
  vote to production's R2 state machine unchanged.  Execution is production K.
* **Availability.**  `NotApplicable` where IEF has no value in the window (M1, M2,
  GSPC_1957_1999); M3/M4/M5 (post-2002 span), the main window and the ETFs are applicable -- the
  bank's 3-window note.
"""

from __future__ import annotations

import numpy as np

from research.batch_screen import extra as _ext
from research.batch_screen.api import NotApplicable
from research.fast_engine import fe_signals
from research.fast_engine.fe_config import DON_N, SMA_N

ARMS = {
    "A": {"rule": "K", "leg": "2x", "cash_yield": "none", "n": 60, "thr": 0.30, "factor": 0.75,
          "mode": "scale"},
    "B": {"rule": "K", "leg": "2x", "cash_yield": "none", "n": 60, "thr": 0.50, "factor": 0.50,
          "mode": "scale"},
    "C": {"rule": "K", "leg": "2x", "cash_yield": "none", "n": 60, "thr": 0.30, "mode": "vote4",
          "votes": 4, "need": 3},
}


def source_returns(data, key):
    """``(P, T)`` daily simple return of an extra series on the session each column replays."""
    name = _ext.resolve_series(key, data.extra_window or data.window)
    pool = np.asarray(data.days) if data.extra_index is None else np.asarray(data.extra_days)
    lv = _ext.forward_fill(_ext.values_on(name, pool).reshape(1, -1))[0]
    r = np.full(lv.size, np.nan)
    r[1:] = lv[1:] / lv[:-1] - 1.0
    if data.extra_index is None:
        out = np.tile(r, (data.P, 1))
    else:
        out = r[np.asarray(data.extra_index)]
    out[:, 0] = np.nan
    return out


def price_returns(price):
    """``(P, T)`` simple returns of the panel's price close, column 0 NaN."""
    p = np.atleast_2d(np.asarray(price, dtype=np.float64))
    out = np.full(p.shape, np.nan)
    out[:, 1:] = p[:, 1:] / p[:, :-1] - 1.0
    return out


def rolling_corr(a, b, n):
    """Pearson correlation of the n paired values ending at t; NaN if any is undefined."""
    P, T = a.shape
    bad = ~(np.isfinite(a) & np.isfinite(b))
    x = np.where(bad, 0.0, a)
    y = np.where(bad, 0.0, b)
    z = np.zeros((P, 1))

    def win(v):
        s = np.concatenate([z, np.cumsum(v, axis=1)], axis=1)
        return s[:, n:] - s[:, :-n]

    sx, sy, sxx, syy, sxy, sb = win(x), win(y), win(x * x), win(y * y), win(x * y), win(bad)
    cov = sxy - sx * sy / n
    vx = sxx - sx * sx / n
    vy = syy - sy * sy / n
    den = np.sqrt(np.maximum(vx, 0.0) * np.maximum(vy, 0.0))
    ok = (sb == 0) & (den > 0)
    c = np.where(ok, cov / np.where(ok, den, 1.0), np.nan)
    out = np.full((P, T), np.nan)
    out[:, n - 1:] = np.clip(c, -1.0, 1.0)
    return out


def targets(data, spec):
    if not np.isfinite(data.extra("ief")).any():
        raise NotApplicable("Yahoo IEF starts 2002-07-30 and has no value in this window")
    corr = rolling_corr(price_returns(data.price), source_returns(data, "ief"), int(spec["n"]))
    known = np.isfinite(corr)
    hot = known & (np.where(known, corr, 0.0) > float(spec["thr"]))
    comps = fe_signals.early_components(data.price)
    if spec["mode"] == "vote4":
        s = fe_signals.sma_up(data.price, SMA_N).astype(int)
        t = fe_signals.tsm_up_price(data.price).astype(int)
        d = fe_signals.don_up(data.price, DON_N).astype(int)
        v4 = np.where(known, (~hot).astype(int), 0)
        need = np.where(known, int(spec["need"]), int(spec["votes"]) - 2)
        e2 = (s + t + d + v4) >= need
        on_eff, factor = fe_signals.r2_state(data.price, e2, comps)
        return fe_signals.sized_wants(on_eff, factor, data.vol)
    e2 = fe_signals.e2_up(data.price)
    on_eff, factor = fe_signals.r2_state(data.price, e2, comps)
    wants = fe_signals.sized_wants(on_eff, factor, data.vol)
    return wants * np.where(hot, float(spec["factor"]), 1.0)


__all__ = ["ARMS", "price_returns", "rolling_corr", "source_returns", "targets"]
