"""ib13 -- 10-year yield momentum veto (rising long rates) (idea_bank_v1, arm A/B/C).

Arm specs (`docs/research/idea_bank_v1.json`, parameters frozen):

* **A** `weight = 0` while `DGS10_t - DGS10_{t-60} > +0.50 pp`
* **B** `weight *= 0.5` while the same change `> +0.25 pp`
* **C** 3-of-4 vote `{SMA200, TSM252, DON126, 60-session DGS10 change <= +0.50 pp}`

Literal reading and the frozen conventions:

* The series is the framework's registered FRED `DGS10` (`data.extra("dgs10", ffill=True)`): the
  10-year constant-maturity yield in percent, so the thresholds are already in percentage points
  (a change of 0.50 in the series *is* 50 bp). `ffill` carries the last published value across the
  sessions FRED does not publish (bond-market holidays, foreign sessions), so the "60-session
  change" is defined on the panel's own 60 sessions -- the literal reading of `DGS10_{t-60}`.
* A session whose change is undefined (the first 60 sessions of a panel, or a window the series
  never reaches) takes the *neutral* behaviour: no veto, no halving, and for C the vote component is
  False (the comparison `<= +0.50` cannot be affirmed), i.e. C then needs all three price votes.
* The idea bank's data note suggests a monthly GS10/TB3MS splice to extend the pre-1962 OOS
  windows. Not implemented: the arm rule names `DGS10`, the registered FRED series starts 1962-01-02
  and a monthly series with a one-month lag is a different (monthly) statistic, not the 60-session
  change the arm specifies. The most literal reading is used: `DGS10` itself, and the windows the
  series never reaches are declared `NotApplicable` (below), rather than splicing a second series
  in.
* Applicability (`NotApplicable` when the series has no finite value in the window): `M2`
  (^GSPC 1935-1956, the series starts 1962). Everything else carries DGS10 -- `M1` from its first
  session, `M3`/`M4`/`M5` and every ETF from theirs, `GSPC_1957_1999` from 1962 (the 1957-1962 part
  is neutral, as above). This is *more* coverage than the bank's `oos.evaluable` note
  (`GSPC_1957_1999`, `M1`, `M2`) assumed, because the framework's rule is per *window*, not per
  sub-period: a window the series reaches part-way is applicable and the missing part is neutral.
* Execution stays production K, `leg = 2x`. A's veto is a target-weight zero (an exit, filled by
  the engine's own rule), not a vote change.
"""

from __future__ import annotations

import numpy as np

from research.batch_screen.api import NotApplicable
from research.fast_engine import fe_signals
from research.fast_engine.fe_config import DON_N, SMA_N, TSM_N

ARMS = {
    "A": {"rule": "K", "leg": "2x", "cash_yield": "none", "lag": 60, "soft": 0.50,
          "mode": "veto"},
    "B": {"rule": "K", "leg": "2x", "cash_yield": "none", "lag": 60, "soft": 0.25,
          "mode": "halve"},
    "C": {"rule": "K", "leg": "2x", "cash_yield": "none", "lag": 60, "soft": 0.50,
          "mode": "vote4"},
}


def _series(data):
    """The 10y yield aligned to the panel (`ffill`), or `NotApplicable` if the window has none."""
    x = data.extra("dgs10", ffill=True)
    if not np.isfinite(x).any():
        raise NotApplicable("FRED DGS10 starts 1962-01-02 and has no value in this window")
    return x


def _change(x, lag):
    """`x_t - x_{t-lag}` (NaN where either side is undefined)."""
    out = np.full_like(x, np.nan)
    out[:, lag:] = x[:, lag:] - x[:, :-lag]
    return out


def targets(data, spec):
    x = _series(data)
    chg = _change(x, int(spec["lag"]))
    e2 = fe_signals.e2_up(data.price)
    comps = fe_signals.early_components(data.price)
    if spec["mode"] == "vote4":
        cond = chg <= float(spec["soft"])  # NaN (no change computable) -> False
        s = fe_signals.sma_up(data.price, SMA_N).astype(np.int8)
        t = fe_signals.tsm_up_price(data.price, TSM_N).astype(np.int8)
        d = fe_signals.don_up(data.price, DON_N).astype(np.int8)
        e2 = (s + t + d + cond.astype(np.int8)) >= 3
    on_eff, factor = fe_signals.r2_state(data.price, e2, comps)
    wants = fe_signals.sized_wants(on_eff, factor, data.vol)
    hot = chg > float(spec["soft"])  # NaN -> False
    if spec["mode"] == "veto":
        return np.where(hot, 0.0, wants)
    if spec["mode"] == "halve":
        return np.where(hot, 0.5 * wants, wants)
    return wants
