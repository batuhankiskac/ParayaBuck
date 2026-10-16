"""IB08 -- realised-volatility term-structure sizing, as a batch_screen plugin.

Idea bank `docs/research/idea_bank_v1.json` (main), three arms, parameters frozen by the spec:

    A  ``vt = min(1, 0.35 / (vol20 * (vol20/vol252)**0.5))``
    B  ``vt = min(1, 0.35 / (vol20 * (vol20/vol252)**1.0))``
    C  ``vt = min(1, 0.35/vol20) * clip(vol252/vol20, 0.85, 1.00)``

Readings fixed here (the spec's own words are kept verbatim above):

* **Which series the two vols come from.**  ``vol20`` is production's sizing input,
  ``data.vol = fe_signals.vol_n(data.close)`` -- the vol of the *held leg* (the 2x leg), the same
  series the control's ``min(1, 0.35/vol20)`` reads.  ``vol252`` is ``fe_signals.vol_n(data.close,
  252)`` on the same series, so the arm is a pure sizing change against K and the term-structure
  ratio compares two windows of one series.  (The idea's ``signal_inputs`` says "the arm's own
  instrument close"; ``data.close`` is the held instrument, the 2x leg of the window.)
* **Guards.**  Where ``vol252`` (or the ratio) is not yet computable (t < 252 sessions) the ratio
  is treated as 1 -- the arm falls back to production sizing exactly, which is the "no information"
  reading -- and ``vol <= 0`` keeps ``fe_signals.vt_of``'s own convention (``vt = 1``).  Weights
  stay finite and non-negative everywhere.
* **Cap.**  The production total cap 0.98 is re-applied after the modified sizing (same arithmetic
  as ``fe_signals.sized_wants``'s ``fcap``); for arm C the multiplier is <= 1, so the cap only ever
  binds where production binds.
* **Inputs**: the panel's own instrument only -- every window is applicable, no `NotApplicable`.

`targets` is pure vectorised numpy; the execution layer is the engine's `K`.
"""

from __future__ import annotations

import numpy as np

from research.fast_engine import fe_signals
from research.fast_engine.fe_config import CAP, VOL_TARGET

ARMS = {
    "A": {"rule": "K", "leg": "2x", "cash_yield": "none", "mode": "ratio", "lam": 0.5,
          "vol_fast": 20, "vol_slow": 252},
    "B": {"rule": "K", "leg": "2x", "cash_yield": "none", "mode": "ratio", "lam": 1.0,
          "vol_fast": 20, "vol_slow": 252},
    "C": {"rule": "K", "leg": "2x", "cash_yield": "none", "mode": "clip", "lo": 0.85, "hi": 1.00,
          "vol_fast": 20, "vol_slow": 252},
}


def _cap98(raw):
    """`fe_signals.sized_wants`' total cap, on a pre-cap weight panel."""
    fcap = np.where(raw > CAP, CAP / np.where(raw > 0, raw, 1.0), 1.0)
    return raw * fcap


def targets(data, spec):
    """The production target series with the vol-target sizing replaced by the arm's form."""
    vol20 = data.vol
    vt0 = fe_signals.vt_of(vol20)
    vol252 = fe_signals.vol_n(data.close, spec["vol_slow"])
    if spec["mode"] == "ratio":
        ratio = vol20 / vol252
        veff = vol20 * ratio ** spec["lam"]
        ok = np.isfinite(veff) & (veff > 0)
        vt = np.where(ok, np.minimum(1.0, VOL_TARGET / np.where(ok, veff, 1.0)), vt0)
    else:
        ratio = np.clip(vol252 / vol20, spec["lo"], spec["hi"])
        vt = vt0 * np.where(np.isfinite(ratio), ratio, 1.0)
    e2 = fe_signals.e2_up(data.price)
    comps = fe_signals.early_components(data.price)
    on_eff, factor = fe_signals.r2_state(data.price, e2, comps)
    return _cap98(on_eff * factor * vt)
