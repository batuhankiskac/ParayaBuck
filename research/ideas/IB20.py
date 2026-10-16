"""IB20 -- semiconductor relative strength as a cyclical risk-on signal, batch_screen plugin.

Idea bank `docs/research/idea_bank_v1.json` (main), three arms, parameters frozen by the spec:

    A  4th vote: ``>= 3 of {SMA200, TSM252, DON126, r_SMH126 > r_QQQ126}``
    B  ``weight = 0 while (r_SMH126 - r_QQQ126) < -5 pp``
    C  the early path's entry additionally requires ``r_SMH126 - r_QQQ126 > 0``

Readings fixed here:

* **Series.**  ``SMH`` comes from the extra registry (`smh`, the ETF's adjusted close, 2000-06-05
  ->), QQQ's 126-session return from ``data.price`` -- the series the production vote runs on
  (the main window's QQQ *price* close, no dividends; that is the framework's price convention and
  the same series K votes on).  Both returns are simple 126-session returns.
* **Missing signal -> neutral**, the convention the idea's spec fixes in advance for the sessions
  before the signal's first valid date (its note: "the 4th vote as *neutral* (2-of-3 behaviour)").
  Applied to all three arms: A's 4th vote abstains (the threshold is 3 of 4 with the vote, 2 of 3
  without it), B does not veto, C's entry gate is inert.  A missing spread therefore never *cuts*
  exposure, and the pre-2000-12 part of the main window behaves as production.
* **Arm C** needs the early re-entry's state machine with one extra entry condition, which
  `fe_signals.r2_state` does not take as an argument.  It is reproduced here in vectorised form
  (the state is the outcome of the last decisive session: an eligible entry sets it, E2-on or
  "close <= SMA50" clears it -- which is exactly `r2_state`'s loop), and it was checked against
  `fe_signals.r2_state` on the real main window by disabling the extra condition (identical
  `on_eff`/`factor`, bit for bit).
* **Availability** (`NotApplicable`), fixed in advance by the spec: its `oos.evaluable` set is
  **empty** -- "SMH has no pre-2000 history -> G7/G8 are not evaluable for this arm; pre-register
  the reduced gate (main window + 21-ETF stage + rolling starts + bootstraps)" -- so all six OOS
  windows (and their pooled-bootstrap chunks) are declared N/A and the framework records the
  reduced holdout.  A window with no SMH value at all is N/A as well.  The main window, the 21
  ETF windows and the bootstrap chunks from the main window's pool are applicable.
"""

from __future__ import annotations

import numpy as np

from research.batch_screen.api import NotApplicable
from research.fast_engine import fe_signals
from research.fast_engine.fe_config import DON_N, SMA_N

#: the six OOS windows + their pooled-bootstrap chunks: the spec's `oos.evaluable` is empty
OOS_SCOPE = ("GSPC_1957_1999", "M1", "M2", "M3", "M4", "M5")

ARMS = {
    "A": {"rule": "K", "leg": "2x", "cash_yield": "none", "mode": "vote4", "window": 126,
          "span_pp": 0.0},
    "B": {"rule": "K", "leg": "2x", "cash_yield": "none", "mode": "veto", "window": 126,
          "span_pp": -5.0},
    "C": {"rule": "K", "leg": "2x", "cash_yield": "none", "mode": "early", "window": 126,
          "span_pp": 0.0},
}


def _relative_strength(data, n):
    """``(spread, valid)``: SMH's n-session return minus the panel price's, in percent."""
    smh = data.extra("smh")
    px = data.price
    r_s = np.full_like(smh, np.nan)
    r_q = np.full_like(px, np.nan)
    if smh.shape[1] > n:
        r_s[:, n:] = smh[:, n:] / smh[:, :-n] - 1.0
        r_q[:, n:] = px[:, n:] / px[:, :-n] - 1.0
    valid = np.isfinite(r_s) & np.isfinite(r_q)
    return 100.0 * (r_s - r_q), valid


def _early_state(price, comps, e2_on, extra_entry):
    """`fe_signals.r2_state`'s early state with one extra entry condition, vectorised."""
    sma50_up, vol_p, med_p, lag_cond = comps
    entry = sma50_up & np.isfinite(med_p) & (vol_p < med_p) & lag_cond & extra_entry & (~e2_on)
    clear = e2_on | (~sma50_up)
    ev = np.where(entry, 1, np.where(clear, -1, 0))
    last = np.maximum.accumulate(np.where(ev != 0, np.arange(ev.shape[1])[None, :], 0), axis=1)
    active = np.take_along_axis(ev, last, axis=1) > 0
    on_eff = e2_on | active
    return on_eff, np.where(e2_on, 1.0, np.where(active, 0.5, 0.0))


def _check_applicable(data):
    if data.window == "oos_boot" or data.window in OOS_SCOPE or data.meta.get("market") in OOS_SCOPE:
        raise NotApplicable(
            "the idea's spec sets oos.evaluable = [] (SMH starts 2000-06-05) and pre-registers the "
            "reduced gate main + 21 ETFs + rolling starts + bootstraps")
    if not np.isfinite(data.extra("smh")).any():
        raise NotApplicable("the SMH series has no value in this window")


def targets(data, spec):
    """The production target series with the SMH/QQQ relative strength as vote / veto / entry gate."""
    _check_applicable(data)
    spread, valid = _relative_strength(data, spec["window"])
    up = valid & (spread > spec["span_pp"])
    comps = fe_signals.early_components(data.price)
    e2 = fe_signals.e2_up(data.price)
    if spec["mode"] == "vote4":
        s = fe_signals.sma_up(data.price, SMA_N).astype(int)
        t = fe_signals.tsm_up_price(data.price).astype(int)
        d = fe_signals.don_up(data.price, DON_N).astype(int)
        votes = s + t + d + up.astype(int)
        e2 = votes >= np.where(valid, 3, 2)          # 3 of 4 with the vote, 2 of 3 without it
        on_eff, factor = fe_signals.r2_state(data.price, e2, comps)
        return fe_signals.sized_wants(on_eff, factor, data.vol)
    if spec["mode"] == "veto":
        on_eff, factor = fe_signals.r2_state(data.price, e2, comps)
        wants = fe_signals.sized_wants(on_eff, factor, data.vol)
        return np.where(valid & (spread < spec["span_pp"]), 0.0, wants)
    on_eff, factor = _early_state(data.price, comps, e2, np.where(valid, up, True))
    return fe_signals.sized_wants(on_eff, factor, data.vol)
