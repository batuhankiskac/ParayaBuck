"""ib01 -- efficiency-ratio (Kaufman) momentum vote (idea_bank_v1, arm A/B/C).

Arm specs (`docs/research/idea_bank_v1.json`, parameters frozen -- not tuned here):

* **A** 2-of-3 vote with TSM252 replaced by `ER_20 > 0.30`
* **B** the same with `ER_60 > 0.30`
* **C** E2 as production, and the early path's entry additionally requires `ER_20 > 0.30`

Literal reading and the frozen conventions (everything the spec leaves open):

* `ER_n = |c_t - c_{t-n}| / sum_{i=t-n+1..t} |c_i - c_{i-1}|` on `data.price` (the series the
  production vote runs on: QQQ's price close on the main window, the market's own index/ETF close
  on the OOS/instrument windows). A session with a zero denominator takes `ER = 0` (no net progress
  over a flat path, so no vote); sessions with fewer than `n` returns take `ER = 0` as well.
* The ER component *replaces* the TSM252 component of the same 2-of-3 vote {SMA200, TSM252,
  DON126}: `vote = (SMA200 + (ER_n > 0.30) + DON126) >= 2`, and the vote feeds production's R2
  state machine (`fe_signals.r2_state`) and `fe_signals.sized_wants` unchanged. The ER component is
  available earlier than TSM252, but no window simulates before panel index 253 (`START`), so this
  cannot change a measured session.
* Arm C keeps the production vote (`fe_signals.e2_up`) and adds `ER_20 > 0.30` to the early path's
  *entry* condition only: exit (close below SMA50) and the E2 override are untouched. The state
  machine is re-implemented vectorised (`_r2_state_gated`); with the gate set to all-True it
  reproduces `fe_signals.r2_state` bit for bit (checked in `research/plugins_b1/check_signals.py`).
* Execution stays production K (`rule = "K"`, `leg = "2x"`, `cash_yield = "none"`).
* No extra data: every window is applicable (closes only, as the idea bank records).
"""

from __future__ import annotations

import numpy as np

from research.fast_engine import fe_signals
from research.fast_engine.fe_config import SMA_N

ARMS = {
    "A": {"rule": "K", "leg": "2x", "cash_yield": "none", "er_n": 20, "er_min": 0.30,
          "gate": "vote"},
    "B": {"rule": "K", "leg": "2x", "cash_yield": "none", "er_n": 60, "er_min": 0.30,
          "gate": "vote"},
    "C": {"rule": "K", "leg": "2x", "cash_yield": "none", "er_n": 20, "er_min": 0.30,
          "gate": "early"},
}


def _efficiency_ratio(price, n):
    """Kaufman ER_n of a `(P, T)` price panel: NaN-free, 0 where it is undefined."""
    p = np.atleast_2d(np.asarray(price, dtype=np.float64))
    chg = np.abs(np.diff(p, axis=1))  # the change into session m+1 at index m
    cs = np.concatenate([np.zeros((p.shape[0], 1)), np.cumsum(chg, axis=1)], axis=1)
    net = np.zeros_like(p)
    den = np.zeros_like(p)
    net[:, n:] = np.abs(p[:, n:] - p[:, :-n])
    den[:, n:] = cs[:, n:] - cs[:, :-n]
    return np.where(den > 0, net / np.where(den > 0, den, 1.0), 0.0)


def _vote_with_third(price, third):
    """{SMA200, `third`, DON126} >= 2 -- the same shape as `fe_signals.e2_up`."""
    s = fe_signals.sma_up(price, SMA_N).astype(np.int8)
    t = np.asarray(third, dtype=bool).astype(np.int8)
    d = fe_signals.don_up(price).astype(np.int8)
    return (s + t + d) >= 2


def _r2_state_gated(price, e2_on, entry_gate):
    """`fe_signals.r2_state` with a per-session early-entry gate (vectorised last-event scan).

    Production's loop, per session: clear `early_active` when E2 is on; clear it when close <=
    SMA50; enter when `~early_active & ~e2 & finite(med) & sma50_up & vol20 < med & close >
    close_20`; then `on_eff = e2 | early_active` and `factor = 1 / 0.5 / 0`. An entry and a reset
    can never happen on the same session (entry needs `sma50_up` and `~e2`, the reset is
    `~sma50_up | e2`), so the state is exactly "the last entry is more recent than the last reset".
    `entry_gate[p, t] == True` everywhere reproduces `fe_signals.r2_state` exactly.
    """
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
    ok = _efficiency_ratio(data.price, int(spec["er_n"])) > float(spec["er_min"])
    if spec["gate"] == "vote":
        e2 = _vote_with_third(data.price, ok)
        comps = fe_signals.early_components(data.price)
        on_eff, factor = fe_signals.r2_state(data.price, e2, comps)
    else:
        on_eff, factor = _r2_state_gated(data.price, fe_signals.e2_up(data.price), ok)
    return fe_signals.sized_wants(on_eff, factor, data.vol)
