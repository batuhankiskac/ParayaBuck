"""IB03 -- ATR/EMA channel (Keltner) instead of a Donchian midpoint, as a batch_screen idea.

The arm is the production E2 vote (or its early re-entry gate) with **one** component swapped for
a volatility-scaled channel, per `docs/research/idea_bank_v1.json` IB03:

    A  DON126 `close > midpoint(126-session max/min)` -> `close > EMA20 + 1.0 * ATR20`   (2-of-3)
    B  SMA200 `close > SMA200`                        -> `close > EMA50 + 0.5 * ATR20`   (2-of-3)
    C  the early leg's entry gate `close > SMA50`     -> `close > EMA20 + 1.0 * ATR20`

Fixed parameters (frozen by the registry's `spec_hash`, not tuned): the Keltner constants come
straight from the arm text above; the remaining two votes, the R2 sizing (`min(1, 0.35/vol20)` x
path factor, cap 0.98) and the execution layer (rule `K`) are production.

**Readings of the ambiguous parts (documented, chosen literally).**

1. *"EMA_n"* is the standard exponential moving average, `alpha = 2/(n+1)`, seeded with the mean
   of the first `n` closes (the usual charting/Keltner convention).
2. *"ATR_n"* is Wilder's ATR (Wilder 1978, the reference the bank cites): `TR = max(h-l,
   |h-prev_close|, |l-prev_close|)`, seeded with the mean of the first `n` TRs, then
   `ATR_t = ((n-1) ATR_{t-1} + TR_t)/n`. Arm B's `EMA50 + 0.5 * ATR20` really is EMA over 50
   sessions with ATR over 20, as written.
3. Arm C replaces the **entry** gate only: production exits the early position on `close < SMA50`
   and production's vote override still wins, so an early position entered above the channel
   exits below the SMA50. That asymmetry is what the arm's one-clause text says.
4. The high/low: on the ETF windows the panel carries the instrument's own OHLC (used verbatim);
   on the main window and the five OOS markets the derived relative series
   `underlying_relhigh`/`underlying_rellow` (the source session's `high/close`, built by
   `research/plugins_b3/build_extra.py` from the very files the harness builds those panels'
   prices from) is multiplied by the panel's own price -- the literal high/low on a real window,
   and the same intraday shape replayed inside a bootstrap chunk (see `helpers.relative_high_low`).
5. **`GSPC_1957_1999` is `NotApplicable`**, exactly as the idea bank pre-registers ("that one
   window is dropped and a reduced 5-window G7 is pre-registered"): the harness's loader for it is
   close-only (`fe_data.load_gspc`), and a true-range statistic there would need a loader change
   that moves the published control cells. (The file itself does carry OHLC 1950-1999; the
   pre-registration is honoured rather than over-ridden.) The synthetic smoke panel has no OHLC
   either and reports `n/a`.

No engine change: `targets` returns target weights, the rule comes from the spec.
"""

from __future__ import annotations

from research.batch_screen.api import NotApplicable
from research.fast_engine import fe_signals
from research.fast_engine.fe_config import DON_N, SMA_N
from research.plugins_b3 import helpers

#: the one window the idea bank pre-registers as dropped (close-only harness loader)
DROPPED_WINDOW = "GSPC_1957_1999"

ARMS = {
    # A: the Donchian-midpoint vote -> the Keltner channel
    "A": {"rule": "K", "leg": "2x", "cash_yield": "none", "component": "don",
          "ema_n": 20, "atr_n": 20, "k": 1.0},
    # B: the SMA200 vote -> the Keltner channel
    "B": {"rule": "K", "leg": "2x", "cash_yield": "none", "component": "sma",
          "ema_n": 50, "atr_n": 20, "k": 0.5},
    # C: the early leg's entry gate -> the Keltner channel
    "C": {"rule": "K", "leg": "2x", "cash_yield": "none", "component": "early",
          "ema_n": 20, "atr_n": 20, "k": 1.0},
}


def targets(data, spec):
    if data.window == DROPPED_WINDOW:
        raise NotApplicable(
            "fe_data.load_gspc is close-only, so no true range can be built for "
            f"{DROPPED_WINDOW}; the idea bank drops this window (reduced 5-window G7)"
        )
    price = data.price
    high, low = helpers.relative_high_low(data)
    channel = helpers.keltner_up(price, high, low, spec["ema_n"], spec["atr_n"], spec["k"])

    s = fe_signals.sma_up(price, SMA_N).astype(int)
    t = fe_signals.tsm_up_price(price).astype(int)
    d = fe_signals.don_up(price, DON_N).astype(int)
    comps = fe_signals.early_components(price)

    component = spec["component"]
    if component == "don":
        e2 = (s + t + channel.astype(int)) >= 2
        on_eff, factor = fe_signals.r2_state(price, e2, comps)
    elif component == "sma":
        e2 = (t + d + channel.astype(int)) >= 2
        on_eff, factor = fe_signals.r2_state(price, e2, comps)
    elif component == "early":
        e2 = fe_signals.e2_up(price)
        on_eff, factor = helpers.r2_state_with_entry(price, e2, channel, comps)
    else:  # pragma: no cover - the specs above are the only ones registered
        raise ValueError(f"unknown component {component!r}")
    return helpers.capped(on_eff, factor, data.vol)
