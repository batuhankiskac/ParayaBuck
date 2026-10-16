"""IB10 -- ATR%-based volatility estimate (mean of true ranges, production-feasible).
Idea bank `idea_bank_v1`.

    A  vt = min(1, 0.35 / sigma_atr),     sigma_atr = 0.63 * (ATR20 / close) * sqrt(252)
    B  vt = min(1, 0.35 / (0.5 * sigma_atr + 0.5 * vol20))
    C  vt = min(1, 0.35 / max(sigma_atr, vol20))

TR = `max(high - low, |high - previous close|, |low - previous close|)`, ATR20 = the mean of the
last 20 TRs, both from the panel's own high/low/close; the E2 vote, the early re-entry state
machine and the K execution layer are production's, only the vol-target input changes.

**Interpretation, frozen in the spec: the *gearing* factor.**  Production's sizing input is the
*traded instrument's* close-to-close vol -- `data.vol` = `vol_n(synthetic 2x leg)`, whose mean is
0.456 on the main window, not the underlying's (QQQ, mean 0.228); `vt = min(1, 0.35/vol20)` is
built to sit on that scale.  The panel exposes the *underlying's* high/low (the leg has no bars of
its own), so the range estimate must be lifted to the traded leg's scale, which for a daily-reset
fund is exactly the leg's gearing times the underlying's -- the published construction of
`range_vol_v1`-`v3` ("Parkinson P20 x 2 (2x fund)"), which is this idea's own closest-tested study
and the family whose implementability objection the idea answers.  `gearing` is read from the
panel's leg key (2x -> 2.0, 1x -> 1.0), frozen per arm, and is not a fitted constant.  Read without
it the arm is not the arm the bank describes: at the underlying's scale sigma_atr averages ~0.22,
so arm C's `max(sigma_atr, vol20)` is `vol20` -- production's sizing exactly, a no-op arm -- and
arm B's 50/50 blend is dominated by vol20 in the same way.  0.63 is Parkinson's a-priori constant
(the bank's own note: "not a fitted one"), 20 is the ATR window, sqrt(252) annualises.

`ATR20/close` is computed as the mean over the last 20 sessions of `TR/close` (each bar's true
range over its *own* close).  That is the same quantity as the naive "mean of the last 20 absolute
TRs over today's close" rescaled by the within-window level drift, and it is the only form that is
meaningful inside a bootstrap chunk, where the panel's price is a reconstructed index (it starts at
1) while the extra series replays the drawn source session's own values.

**High/low, applicability, degenerate windows.**  Same as `research/ideas/IB06.py`: the ETF windows
use the panel's own high/low; the main and OOS windows use this branch's registered
`hl_high`/`hl_low` ratios (derived from the same repo OHLC the panel's prices come from); the
^GSPC 1957-1999 window raises `NotApplicable` (close-only builder, no series to substitute); on the
evaluable windows the repo files' high/low are placeholders on 100 % of ^GSPC 1927-1956 (M2), 47 %
of ^IXIC 1971-1999 (M1) and 38 % of ^N225 (M3), where TR degenerates towards |close - previous
close| and the estimate towards a mean-absolute-return vol -- the bank's own disclosed caveat.
"""

from __future__ import annotations

import numpy as np

from research.batch_screen.api import NeedsKernel, NotApplicable
from research.fast_engine import fe_signals
from research.fast_engine.fe_config import CAP

ARMS = {
    "A": {"rule": "K", "leg": "2x", "cash_yield": "none", "blend": "atr", "parkinson": 0.63,
          "atr_sessions": 20, "gearing": 2.0},
    "B": {"rule": "K", "leg": "2x", "cash_yield": "none", "blend": "half", "parkinson": 0.63,
          "atr_sessions": 20, "gearing": 2.0},
    "C": {"rule": "K", "leg": "2x", "cash_yield": "none", "blend": "max", "parkinson": 0.63,
          "atr_sessions": 20, "gearing": 2.0},
}

ANNUAL = float(np.sqrt(252.0))
GEARING = {"2x": 2.0, "1x": 1.0}
HL_MIN_COVER = 0.90


def high_low(data):
    """The window's underlying high/low, in the panel's own price scale."""
    if data.high is not None and data.low is not None:
        return data.high, data.low
    hi = data.extra("hl_high")
    lo = data.extra("hl_low")
    if np.isfinite(hi).mean() < HL_MIN_COVER or not np.isfinite(hi[:, 0]).all():
        raise NotApplicable(
            "no high/low for this window's own instrument: the harness builds it close-only "
            "(^GSPC 1957-1999), so the registered high/low ratios fall back to the main window's "
            "QQQ series and do not cover these dates")
    return hi * data.price, lo * data.price


def tr_ratio(high, low, price):
    """(P, T) true range in units of the session's own close; the first session uses its own
    close as the previous close."""
    ratio = np.ones_like(price)
    ratio[:, 1:] = price[:, 1:] / price[:, :-1]
    hi = high / price * ratio
    lo = low / price * ratio
    tr = np.maximum(np.maximum(hi - lo, np.abs(hi - 1.0)), np.abs(lo - 1.0))
    return tr / ratio


def roll_mean(a, n):
    """Trailing mean over `n` sessions, `NaN` unless all `n` values are finite."""
    P, T = a.shape
    out = np.full_like(a, np.nan)
    if T < n:
        return out
    x = np.nan_to_num(a, nan=0.0)
    ok = np.isfinite(a).astype(np.float64)
    z = np.zeros((P, 1))
    cs = np.concatenate([z, np.cumsum(x, axis=1)], axis=1)
    cn = np.concatenate([z, np.cumsum(ok, axis=1)], axis=1)
    s = cs[:, n:] - cs[:, :-n]
    c = cn[:, n:] - cn[:, :-n]
    out[:, n - 1:] = np.where(c == n, s / n, np.nan)
    return out


def targets(data, spec):
    high, low = high_low(data)
    atr_ratio = roll_mean(tr_ratio(high, low, data.price), int(spec["atr_sessions"]))
    gear = GEARING[data.leg]
    if abs(gear - float(spec["gearing"])) > 0:
        # the arm's frozen gearing is the 2x leg's; another leg would be a different arm
        raise NeedsKernel(f"arm frozen at gearing {spec['gearing']}, panel leg {data.leg!r} "
                          f"gears at {gear}")
    sigma = gear * float(spec["parkinson"]) * ANNUAL * atr_ratio
    blend = spec["blend"]
    if blend == "half":
        denom = 0.5 * sigma + 0.5 * data.vol
    elif blend == "max":
        denom = np.maximum(sigma, data.vol)
    else:
        denom = sigma
    vt = fe_signals.vt_of(denom)  # min(1, 0.35/v), <= 0 or NaN -> 1.0
    e2 = fe_signals.e2_up(data.price)
    comps = fe_signals.early_components(data.price)
    on_eff, factor = fe_signals.r2_state(data.price, e2, comps)
    raw = on_eff * factor * vt
    fcap = np.where(raw > CAP, CAP / np.where(raw > 0, raw, 1.0), 1.0)  # fe_signals.sized_wants
    return raw * fcap
