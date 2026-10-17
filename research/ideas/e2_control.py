"""e2_control -- E2 (pre-R2 production rule) as a batch_screen idea.

E2 = the 2-of-3 trend vote sized min(1, 0.35/vol20), no early re-entry.
Used as the control for the positive control 'R2 vs E2'.
"""

from __future__ import annotations

import numpy as np

from research.fast_engine import fe_signals

ARMS = {
    "E2": {"rule": "C0", "leg": "2x", "cash_yield": "none"},
}


def targets(data, spec):
    e2 = fe_signals.e2_up(data.price)
    factor = np.ones_like(e2, dtype=float)
    return fe_signals.sized_wants(e2, factor, data.vol)
