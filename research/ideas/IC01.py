"""IC01 -- 52-week-high proximity vote (idea_bank_v2, arms A/B/C).

Arm specs (`docs/research/idea_bank_v2.json`, parameters frozen -- not tuned here):

* **A** 2-of-3 vote with TSM252 replaced by `close / max(close over the last 252 sessions) > 0.90`
* **B** the same with `> 0.95`
* **C** 3-of-4 vote `{SMA200, TSM252, DON126, close/max(close, 252) > 0.90}`, on iff `>= 3`

Literal reading and the frozen conventions (everything the spec leaves open):

* the statistic is computed on `data.price` -- the series the production vote runs on (QQQ's price
  close on the main window, the market's own index/ETF close on the OOS and instrument windows),
  exactly as `fe_signals.e2_up` does.  The rolling maximum **includes the session itself**
  (`max(close[t-251..t])`), which is the standard 52-week-high proximity statistic; before 252
  sessions the window is incomplete and the component votes OFF (`NaN -> False`).  The measured
  window starts at index 253 (`fe_config.START`), so no measured session is affected by that
  warm-up rule.
* arms A/B *replace* the TSM252 component of the same 2-of-3 vote; arm C *adds* the statistic as a
  fourth vote with a 3-of-4 quorum.  The vote then feeds production's early-re-entry state machine
  (`fe_signals.r2_state`) and `fe_signals.sized_wants` unchanged: the early path, the vol target
  `min(1, 0.35/vol20)` and the 0.98 cap are production's.
* execution stays production K (`rule = "K"`, `leg = "2x"`, `cash_yield = "none"`).
* no extra data: every window is applicable (closes only, as the idea bank records).
"""

from __future__ import annotations

import numpy as np

from research.fast_engine import fe_signals
from research.plugins_c1 import c1_lib

ARMS = {
    "A": {"rule": "K", "leg": "2x", "cash_yield": "none",
          "high_n": 252, "high_thr": 0.90, "vote": "replace"},
    "B": {"rule": "K", "leg": "2x", "cash_yield": "none",
          "high_n": 252, "high_thr": 0.95, "vote": "replace"},
    "C": {"rule": "K", "leg": "2x", "cash_yield": "none",
          "high_n": 252, "high_thr": 0.90, "vote": "3of4"},
}


def targets(data, spec):
    """The production state machine under the 52-week-high-proximity vote."""
    high = c1_lib.trailing_max(data.price, int(spec["high_n"]))
    thr = float(spec["high_thr"])
    near = np.where(np.isfinite(high), data.price > thr * np.where(np.isfinite(high), high, 1.0),
                    False)
    vote = (c1_lib.vote_2_of_3(data.price, near) if spec["vote"] == "replace"
            else c1_lib.vote_3_of_4(data.price, near))
    comps = fe_signals.early_components(data.price)
    on_eff, factor = fe_signals.r2_state(data.price, vote, comps)
    return fe_signals.sized_wants(on_eff, factor, data.vol)
