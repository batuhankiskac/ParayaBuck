"""IB02 -- median-location trend vote (robust mean vs mean).  Idea bank `idea_bank_v1`.

    A  MED200 (the median of the last 200 closes) replaces SMA200 in the 2-of-3 vote
    B  MED100 replaces SMA200
    C  3-of-4 vote {SMA200, MED200, TSM252, DON126}, on iff >= 3

Literally as the bank's arm specs say: only the location statistic of the *level* vote changes,
the other two votes (TSM252, DON126), the 2-of-3 majority, the early re-entry state machine, the
vol-target sizing and the K execution layer are production's.  The trailing median is the engine's
own `fe_signals.rolling_median_vol` (the production early path's 252-session vol median, reused as
a generic trailing-median kernel: it is O(T) per path and needs no `(T, window)` materialisation,
which a 200-session window over a 256-path bootstrap chunk would not fit in memory).  Its
no-interior-NaN guard is satisfied by a price series; `NaN` before the window is full counts as
"vote off", exactly like `sma_up`'s convention.

Applicable on every window (closes only): main, all six OOS windows, the 21 ETFs, both bootstrap
families.  Nothing outside `data.price` is read.
"""

from __future__ import annotations

from research.fast_engine import fe_signals
from research.fast_engine.fe_config import SMA_N

ARMS = {
    "A": {"rule": "K", "leg": "2x", "cash_yield": "none", "median_window": 200, "vote": "2of3"},
    "B": {"rule": "K", "leg": "2x", "cash_yield": "none", "median_window": 100, "vote": "2of3"},
    "C": {"rule": "K", "leg": "2x", "cash_yield": "none", "median_window": 200, "vote": "3of4"},
}


def targets(data, spec):
    """The production target series with the level vote's mean replaced by a trailing median."""
    price = data.price
    med = fe_signals.rolling_median_vol(price, int(spec["median_window"]))
    med_up = price > med  # NaN median -> False, the sma_up convention
    tsm = fe_signals.tsm_up_price(price)
    don = fe_signals.don_up(price)
    if spec["vote"] == "3of4":
        sma = fe_signals.sma_up(price, SMA_N)
        e2 = (sma.astype(int) + med_up.astype(int) + tsm.astype(int) + don.astype(int)) >= 3
    else:
        e2 = (med_up.astype(int) + tsm.astype(int) + don.astype(int)) >= 2
    comps = fe_signals.early_components(price)
    on_eff, factor = fe_signals.r2_state(price, e2, comps)
    return fe_signals.sized_wants(on_eff, factor, data.vol)
