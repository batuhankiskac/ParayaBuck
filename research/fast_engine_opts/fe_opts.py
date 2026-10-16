"""fe_opts -- the option set of the extended single-leg kernel: names, defaults, packing.

`Opts` is a plain dataclass of *fixed* option names; `pack()` flattens it into the two vectors the
numba kernel takes (`float64` scalars and `int64` flags). Every default is the production value, so
`Opts()` is the engine: the kernel's option code is then unreachable (`fe_opts_sim` branches on the
same thresholds, and `parity_opts.py` asserts the result is bit-identical to `fe_sim.simulate`).

Units are the ones the engine works in: weights and vol-target fractions are fractions of NAV, the
band widths and the slice threshold are fractions of NAV too (0.005 = 0.5 pp), `fx_cost` is a
fraction of the converted amount, `settle_floor` is a fraction of NAV, and the per-session rate
arrays (`mrate`) are one-session yields (build them with `session_fold`).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

# ----------------------------------------------------------------------------- the option names
#: float64 scalars, in packing order
FLOAT_OPTS = (
    "band_up",          # IC59: up-resize threshold (production: C0_BAND)
    "band_down",        # IC59: down-resize threshold (production: C0_BAND, then A1u/H2)
    "min_notional",     # IC52: USD floor on a permitted downsize (0 = off)
    "min_notional_frac",  # IC52: NAV fraction floor, combined as max(.) with the USD floor
    "dd_start",         # IC49: NAV drawdown where the governor starts to bite (0 = off)
    "dd_floor",         # IC49: sigma_budget floor as a fraction of the vol target
    "slice_frac",       # IC53: resize size (fraction of NAV) that triggers slicing
    "slice_parts",      # IC53: equal parts of a sliced order (< 2 = off)
    "fx_cost",          # IC55: cost charged on a TRY conversion (0 = off)
    "prefund_sessions", # IC55-B: convert the liability this many sessions early (< 1 = off)
    "settle_floor",     # IC58: allowed deficit as a NAV fraction; < 0 = off (0 = cash account)
)

#: int64 flags, in packing order
INT_OPTS = (
    "dd_upsize_only",   # IC49-C: the governor caps the target, it never forces a downsize
    "early_mode",       # IC57: 0 off, 1 skip the decision, 2 skip downsizes only
    "fill_mode",        # IC54: 0 the open (production), 1 the session's (open + close) / 2
)

FLOAT_DEFAULTS = {
    "band_up": 0.005,
    "band_down": 0.005,
    "min_notional": 0.0,
    "min_notional_frac": 0.0,
    "dd_start": 0.0,
    "dd_floor": 1.0,
    "slice_frac": 0.0,
    "slice_parts": 1.0,
    "fx_cost": 0.0,
    "prefund_sessions": 0.0,
    "settle_floor": -1.0,
}

INT_DEFAULTS = {"dd_upsize_only": 0, "early_mode": 0, "fill_mode": 0}

NF = len(FLOAT_OPTS)
NI = len(INT_OPTS)


@dataclass
class Opts:
    """The kernel's option set. Defaults = production; `is_off()` says whether any is set."""

    band_up: float = 0.005
    band_down: float = 0.005
    min_notional: float = 0.0
    min_notional_frac: float = 0.0
    dd_start: float = 0.0
    dd_floor: float = 1.0
    slice_frac: float = 0.0
    slice_parts: float = 1.0
    fx_cost: float = 0.0
    prefund_sessions: float = 0.0
    settle_floor: float = -1.0
    dd_upsize_only: int = 0
    early_mode: int = 0
    fill_mode: int = 0
    #: whether the caller must supply the extra option arrays (checked by the kernel wrapper)
    extra: tuple = field(default=())

    def pack(self):
        """`(optf, opti)`: the float64 and int64 vectors the kernel takes."""
        optf = np.array([float(getattr(self, k)) for k in FLOAT_OPTS], dtype=np.float64)
        opti = np.array([int(getattr(self, k)) for k in INT_OPTS], dtype=np.int64)
        return optf, opti

    def is_off(self) -> bool:
        """True when every option is at its production value (i.e. `Opts()` is the engine)."""
        return all(float(getattr(self, k)) == float(v) for k, v in FLOAT_DEFAULTS.items()) and all(
            int(getattr(self, k)) == int(v) for k, v in INT_DEFAULTS.items()
        )

    def needs(self) -> tuple:
        """The extra arrays this option set reads (`wvol`, `mrate`, `early`, `fill`)."""
        out = []
        if self.dd_start > 0.0:
            out.append("wvol")
        if self.early_mode:
            out.append("early")
        if self.fill_mode == 2:
            out.append("fill")
        return tuple(out)


def session_fold(days, annual):
    """One per-session yield from a per-session annual rate: the session's own rate charged for
    every calendar day since the previous session (so a session after a three-day weekend earns
    three days of it).

    `annual` is `(T,)` or `(P, T)`; the calendar-day gaps are shared, so a bootstrap chunk folds
    all of its paths in one call. The engine's own idle-cash knob
    (`batch_screen.rules.cash_rates`) compounds *each* calendar day's own DTB3 instead; the two
    differ by a fraction of a basis point a year, and the margin rate is not a published cell.
    """
    from datetime import date as _date

    days = list(days)
    a = np.asarray(annual, dtype=np.float64)
    gap = np.zeros(len(days), dtype=np.float64)
    prev = None
    for k, d in enumerate(days):
        cur = _date.fromisoformat(d).toordinal()
        if prev is not None:
            gap[k] = cur - prev
        prev = cur
    return (1.0 + a / 365.0) ** gap - 1.0


__all__ = ["FLOAT_DEFAULTS", "FLOAT_OPTS", "INT_DEFAULTS", "INT_OPTS", "NF", "NI", "Opts",
           "session_fold"]
