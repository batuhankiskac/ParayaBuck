"""fe_tax -- the corrected Turkish after-tax engine as numba kernels over flat arrays.

Semantics are a line-for-line port of `research/band_h2_combo_v1/tax_engine.py` (itself a byte
copy of `research/gain_band_v3/tax_engine.py`, i.e. the `vt_level_tax_v1` "corrected" engine):
CBRT FX at the trade date, FIFO lots, the YI-UFE indexation rule (>= 10 % rise, month-before
conventions), the 2026 progressive tariff with deflated brackets, no loss carry-forward, tax
assessed at the year boundary and paid in March/July instalments funded by sales, flat-mode
20 % on the annual net realised USD gain with loss carry-forward, and liquidation at the window
end.

What changed is only the representation. The Python engine keeps one `[units, usd_price_with_fee,
fx_at_buy, month]` list per account and dicts keyed by year/month strings; here the lot book is
four parallel float64/int64 arrays walked with a head index, years are dense arrays indexed by
`year - Y0`, months by `year * 12 + month - 1`, and every month/year lookup the Python engine does
through a dict is served from a dense table built once per run (missing keys raise, as the dict
lookup would).

Float arithmetic is deliberately unvectorised and re-associated nowhere except where Python itself
evaluates left to right; numba compiles `fastmath=False` by default and does not contract
`a*b+c` into an FMA on this toolchain, so every kernel below is bit-identical to the Python code
(asserted by parity.py).
"""

from __future__ import annotations

import bisect
import csv
import math
from pathlib import Path

import numpy as np
from numba import njit

# ----------------------------------------------------------------------------- constants
# research/band_h2_combo_v1/tax_engine.py lines 37-40
TARIFF_2026 = ((190_000.0, 0.15), (400_000.0, 0.20), (1_000_000.0, 0.27), (5_300_000.0, 0.35), (math.inf, 0.40))
DIV_THRESHOLD_2026 = 22_000.0
ANCHOR = "2025-10"
INDEX_MIN_RISE = 0.10
FLAT_RATE = 0.20

_TAR_HI = np.array([t[0] for t in TARIFF_2026], dtype=np.float64)
_TAR_RATE = np.array([t[1] for t in TARIFF_2026], dtype=np.float64)
_TAR_INF = math.inf

# Exposed (additive; nothing in this module reads them) so that a study's kernel option can price
# the *marginal* bracket rate of a year without duplicating the tariff. `_TAR_HI` keeps its inf
# sentinel; `tariff_bracket` below is the only consumer.
TARIFF_HI, TARIFF_RATE = _TAR_HI, _TAR_RATE

# reasons accepted by `BaseAccount.sell`; the gain-by-reason accumulator is indexed by these
R_RESIZE, R_TAX, R_LIQUIDATION = 0, 1, 2
REASON_NAMES = ("resize", "tax", "liquidation")
N_REASONS = 3

# modes (engine_run's `mode` argument)
MODE_PRE_TAX, MODE_PROGRESSIVE, MODE_FLAT = 0, 1, 2
MODES = ("pre_tax", "progressive", "flat")


# ----------------------------------------------------------------------------- kernels
@njit(cache=True, nogil=True, inline="always")
def tariff_tax(base, scale):
    """`tax_engine.tariff_tax`: 2026 brackets scaled by the YI-UFE deflator."""
    tax = 0.0
    lo = 0.0
    for k in range(5):
        hi = _TAR_HI[k]
        if np.isinf(hi):
            hi = _TAR_INF
        else:
            hi = hi * scale
        if base <= lo:
            break
        m = base
        if hi < m:
            m = hi
        tax += (m - lo) * _TAR_RATE[k]
        lo = hi
    return tax


def prev_month(ym):
    """`tax_engine.prev_month` on a "YYYY-MM" string."""
    y, m = int(ym[:4]), int(ym[5:7])
    return f"{y - 1}-12" if m == 1 else f"{y}-{m - 1:02d}"


def month_index(date):
    """Session date string -> dense month index (year * 12 + month - 1)."""
    return int(date[:4]) * 12 + int(date[5:7]) - 1


def load_ufe(ufe_csv):
    """The YI-UFE month->index dict, exactly as `tax_engine.load_market` reads it."""
    with Path(ufe_csv).open() as fh:
        return {r["month"]: float(r["yiufe_index"]) for r in csv.DictReader(fh)}


def prev_month_table(ufe, month_lo, month_hi):
    """`upm[m] = ufe[prev_month(m)]` for m in [month_lo, month_hi], as a dense float64 array.

    Slot `m` holds `ufe[prev_month(m)]` -- `prev_month`'s rule, evaluated once per month instead of
    once per lot, so a caller indexes the table by the *sale* month and the *lot* month directly
    (the rule is a shift, and the table carries it). Because month indices are a linear counter,
    `prev_month(m) == m - 1` (January's previous month is the December twelve months back), but
    the shift lives in `prev_month`'s own terms here rather than as a bare `- 1` in a kernel.
    A month the YI-UFE file does not carry becomes NaN and is
    reported, so a caller can fail loudly if the simulation ever reads it -- the Python engine's
    dict lookup would raise KeyError at that point, and any NaN reaching a result means a missing
    entry was read.
    """
    out = np.full(month_hi - month_lo + 1, np.nan, dtype=np.float64)
    missing = []
    for m in range(month_lo + 1, month_hi + 1):
        y, mo = divmod(m, 12)
        key = prev_month(f"{y}-{mo + 1:02d}")
        if key in ufe:
            out[m - month_lo] = ufe[key]
        else:
            missing.append(key)
    return out, missing


def scale_table(ufe, years):
    """`Market.scale(year)` for every year in `years` (dict + earliest-October fallback)."""
    anchor = ufe[ANCHOR]
    oct_keys = sorted(k for k in ufe if k.endswith("-10"))
    out = np.empty(len(years), dtype=np.float64)
    for k, y in enumerate(years):
        key = f"{y - 1}-10"
        if key not in ufe:
            key = oct_keys[0] if oct_keys else ANCHOR
        out[k] = ufe[key] / anchor
    return out


def instalments(days, i0, i1):
    """`tax_engine.instalments`, flattened to (session, tax-year, part) triples.

    The dict the Python version builds maps a session index to the list of (year, part) pairs
    appended in `range(year(i0), year(i1) + 1)` x (03-31, 07-31) order; the same order is kept
    here, so a session's events are visited in the same sequence.
    """
    sess, yrs, parts = [], [], []
    for y in range(int(days[i0][:4]), int(days[i1][:4]) + 1):
        for md, part in (("03-31", 0), ("07-31", 1)):
            k = bisect.bisect_left(days, f"{y}-{md}")
            if i0 <= k <= i1:
                sess.append(k)
                yrs.append(y - 1)
                parts.append(part)
    return (np.array(sess, dtype=np.int64), np.array(yrs, dtype=np.int64), np.array(parts, dtype=np.int64))
