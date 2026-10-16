"""IC06 -- overnight-drift trend vote (idea_bank_v2, arms A/B/C).

Arm specs (`docs/research/idea_bank_v2.json`, parameters frozen -- not tuned here):

* **A** 2-of-3 vote with TSM252 replaced by the 252-session cumulative close->open log return `> 0`
* **B** as A **and** the 252-session cumulative open->close log return `<= 0` (both conditions)
* **C** the early path's *entry* additionally requires the 20-session cumulative close->open log
  return `> 0`

Literal reading and the frozen conventions (everything the spec leaves open):

* the sub-returns are the **held leg's own** (`data.open`, `data.close`, i.e. the 2x leg): that is
  what "the arm's own instrument daily open and close" says and what the position actually earns
  overnight.  The panel also offers the gearing-1 leg (`data.legs["1x"]`, the underlying's exact
  sub-returns); on the main window the two agree in sign on 97.84 % of the 6 679 measured sessions
  (the disagreement is confined to near-zero crossings -- the 2x overnight sum is ~2x the
  underlying's, which never flips a decisive sign).  `check_plugins.py` reports that share.
* `close->open` is `log(open[t] / close[t-1])` and `open->close` is `log(close[t] / open[t])`; the
  cumulative figures are trailing 252- and 20-session sums of those, NaN until the window is
  complete -- and a NaN comparison is False (numpy semantics), so an incomplete window votes OFF.
  The measured window starts at index 253 (`fe_config.START`), so no measured session is affected.
* arm A replaces the TSM252 component of the 2-of-3 vote; arm B keeps that slot and ANDs the
  intraday condition into it; arm C keeps production's vote (`fe_signals.e2_up`) and gates only the
  early path's entry (`c1_lib.r2_state_gated` -- exit and the E2 override are untouched).  The vote
  feeds production's early state machine and `sized_wants` unchanged; execution stays production K.
* **`NotApplicable` on `GSPC_1957_1999` and `M2`**: `fe_data.load_gspc` sets `open := previous
  close`, and `load_market`'s placeholder rule does the same for the whole `M2` (^GSPC 1935-1956)
  window -- measured: 100.0 % of both windows' sessions have `open == close[t-1]` **exactly**, so
  the overnight sub-return is identically zero and the statistic carries no information there.  The
  idea bank's own note drops both windows for this reason, leaving `{M1, M3, M4, M5}` for the
  reduced G7 (`k = 4`, so the framework's rule wants `n_c >= k - 1 = 3`).  The test is
  `all(|r_overnight| == 0)`, not a NaN test: a window whose opens are merely *partly* placeholders
  (M1 48 %, M3 38 %, M5 68 % of sessions -- the repo files' own missing opens) stays applicable,
  exactly as the bank records it.
"""

from __future__ import annotations

import numpy as np

from research.batch_screen.api import NotApplicable
from research.fast_engine import fe_signals
from research.plugins_c1 import c1_lib

ARMS = {
    "A": {"rule": "K", "leg": "2x", "cash_yield": "none", "n": 252, "mode": "vote_overnight"},
    "B": {"rule": "K", "leg": "2x", "cash_yield": "none", "n": 252, "mode": "vote_both"},
    "C": {"rule": "K", "leg": "2x", "cash_yield": "none", "n_early": 20, "mode": "early_gate"},
}


def _sub_returns(open_, close, kind: str) -> np.ndarray:
    """`(P, T)` per-session log sub-return of one leg: `"on"` = close->open, `"id"` = open->close."""
    o = np.atleast_2d(np.asarray(open_, dtype=np.float64))
    c = np.atleast_2d(np.asarray(close, dtype=np.float64))
    r = np.full_like(c, np.nan)
    if kind == "on":
        r[:, 1:] = np.log(o[:, 1:] / c[:, :-1])
    else:
        r[:, 1:] = np.log(c[:, 1:] / o[:, 1:])
    return r


def targets(data, spec):
    """The production state machine under the overnight-drift vote / entry gate."""
    night = _sub_returns(data.open, data.close, "on")
    if not np.any(np.abs(np.nan_to_num(night, nan=0.0)) > 0):
        raise NotApplicable("the window's opens are placeholders (open == previous close on every "
                            "session): the overnight sub-return is identically zero")

    if spec["mode"] == "early_gate":
        gate = c1_lib.trailing_sum(night, int(spec["n_early"])) > 0.0
        on_eff, factor = c1_lib.r2_state_gated(data.price, fe_signals.e2_up(data.price), gate)
    else:
        ok = c1_lib.trailing_sum(night, int(spec["n"])) > 0.0
        if spec["mode"] == "vote_both":
            day = _sub_returns(data.open, data.close, "id")
            ok = ok & (c1_lib.trailing_sum(day, int(spec["n"])) <= 0.0)
        vote = c1_lib.vote_2_of_3(data.price, ok)
        comps = fe_signals.early_components(data.price)
        on_eff, factor = fe_signals.r2_state(data.price, vote, comps)
    return fe_signals.sized_wants(on_eff, factor, data.vol)
