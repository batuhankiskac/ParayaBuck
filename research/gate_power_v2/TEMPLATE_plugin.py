"""TEMPLATE_plugin -- the starting point for a batch_screen idea. Copy, do not screen as-is:

    cp research/batch_screen/TEMPLATE_plugin.py research/ideas/<your_idea>.py
    $EDITOR research/ideas/<your_idea>.py
    PYTHONPATH=. ~/.venvs/pb-research/bin/python -m research.gate_power_v2.smoke --ideas <your_idea>
    git add research/ideas/<your_idea>.py && git commit     # the runner refuses uncommitted code
    PYTHONPATH=. ~/.venvs/pb-research/bin/python -m research.gate_power_v2.run \
        --batch <your_batch> --ideas <your_idea>

What a plugin is: a module with `ARMS = {arm_id: spec}` (fixed, JSON-serialisable parameters) and

    targets(data, spec) -> (P, T) target weights, or (wants, leg)

`data` is a `batch_screen.api.MarketData` over a `(P, T)` panel -- `P` paths (1 on a real window,
a bootstrap chunk otherwise), `T` sessions -- with

    data.price      the underlying's price close (the series the production vote runs on)
    data.close/open the held leg's close/open;  data.vol = fe_signals.vol_n(data.close)
    data.div/rate   per-session dividend and DTB3 rate;  data.high/low where the window has them
    data.days/year/month, data.window, data.meta, data.leg, data.legs, data.P, data.T
    data.extra(key[, ffill])   a registered extra series (see `smoke --keys`), (P,T)
    api.NotApplicable(reason)  raise it for a window whose inputs do not exist

Everything must be vectorised numpy over `(P, T)`: the same code runs on one path and on a
256-path bootstrap chunk, and the bootstrap is what stage 1's B1 gate is built on.

The example below is a *working* demonstration of both v2 mechanisms and of nothing else: the
production target series (E2's 2-of-3 vote + the half-weight early re-entry, sized
`min(1, 0.35/vol20)`) times a ^VIX veto, with `NotApplicable` for a window whose ^VIX series is
empty. It is not a claimed improvement -- do not register it as a trial.
"""

from __future__ import annotations

import numpy as np

from research.gate_power_v2.api import NotApplicable
from research.fast_engine import fe_signals

#: one entry per arm of this idea. Keys the framework owns: `rule` ("K", "C0", ...), `leg` ("2x",
#: "1x"), `cash_yield` ("none", "tbill"), `needs_kernel` ("why" -- registered, never simulated).
#: Everything else is yours and is frozen by `spec_hash` in the registry.
ARMS = {
    "demo": {
        "rule": "K",
        "leg": "2x",
        "cash_yield": "none",
        "vix_max": 30.0,        # frozen: the veto threshold
    },
}


def targets(data, spec):
    """The production state machine, vetoed off while the extra series says stress."""
    vix = data.extra("vix", ffill=True)
    if not np.isfinite(vix).any():
        raise NotApplicable("the ^VIX series has no value in this window")

    e2 = fe_signals.e2_up(data.price)
    comps = fe_signals.early_components(data.price)
    on_eff, factor = fe_signals.r2_state(data.price, e2, comps)
    wants = fe_signals.sized_wants(on_eff, factor, data.vol)

    calm = np.nan_to_num(vix, nan=np.inf) <= spec["vix_max"]
    return wants * calm
