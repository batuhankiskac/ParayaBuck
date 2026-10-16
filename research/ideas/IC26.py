"""IC26 -- seasonal daylight (SAD) exposure tilt (idea_bank_v2, arms A/B/C).

Arm specs (`docs/research/idea_bank_v2.json`, parameters frozen -- not tuned here):

* **A** `target *= (1 - 0.15 * night_factor_t)`, `night_factor` in `[0, 1]` peaking at the autumn
  equinox
* **B** the same with coefficient `0.30`
* **C** mirror sign: `target *= (1 + 0.15 * night_factor_t)` capped at the production target `* 1.0`
  (an exposure control)

Literal reading and the frozen conventions (everything the spec leaves open):

* **the night-length measure.**  `H_t` = hours of night at 40.7 N on the session's date from the
  standard solar-declination formula (Cooper 1969: `decl = 23.44 * sin(2*pi*(284 + doy)/365)`,
  `night = 24 - 2*acos(-tan(lat)*tan(decl))*24/(2*pi)`; clipped where |cos| > 1, i.e. polar day).
  `night_factor_t = clip((H(d) - H(d - 30)) / max_year(H(d) - H(d - 30)), 0, 1)` with `d` the
  session's calendar day of year -- the **night-length change** over the preceding 30 calendar days
  (~21 sessions, "a measure of daylight change"), which is **maximal at the autumn equinox** (the
  night lengthens fastest there) exactly as the spec says, not at the winter solstice.  The
  positive-part clip is the fall weighting: only the autumn lengthening counts; the spring
  shortening (a negative change) is discarded.  The change is a pure function of the date (the
  365-day year wraps around), so it is defined on every session with no warm-up and no dependence
  on which sessions precede it -- inside a bootstrap chunk a session-lagged difference would
  straddle the resampled block boundaries.  `max_year` is the maximum of the same change over the
  365-day grid, so the normalisation is window-independent and the factor is in `[0, 1]` by
  construction (0 on the spring/summer half of the year).
  The alternative reading -- KKL's level variable `H_t - 12`, fall-weighted 2x -- peaks at the
  *winter solstice* and contradicts "peaking at the autumn equinox"; it is not implemented.
* **the dates** come from the framework's session-label convention: the panel's own `days` on a
  plain window, the **drawn source sessions** `extra_days[extra_index]` inside a bootstrap chunk
  (`c1_lib.day_labels`) -- the chunk replays those real sessions' returns, so the seasonal factor
  is aligned with them.  `data.days` in a chunk is the chunk's synthesised label calendar and is
  one column shorter than the market arrays (framework finding 1), so it cannot be indexed here.
* **arm C is a no-op by construction**, and that is the spec's own label: with the cap written as
  "the production target `* 1.0`", `min(target * (1 + 0.15 * nf), target) == target` for every
  session, i.e. the arm equals production K exactly (a mirror *control*, not a live tilt).  It is
  implemented literally, not reinterpreted as a live up-tilt (which would cap at 0.98 instead) and
  not dropped: the orchestrator's ledger counts it, and its Δ is exactly 0.
* the tilt multiplies the finished production target (`on_eff x factor x vt`, capped at 0.98), so
  it can only lower it and needs no re-cap.  Execution stays production K.
* no extra data: every window is applicable (dates only, as the bank records).
"""

from __future__ import annotations

import numpy as np

from research.fast_engine import fe_signals
from research.plugins_c1 import c1_lib

ARMS = {
    "A": {"rule": "K", "leg": "2x", "cash_yield": "none", "coef": 0.15, "lag_days": 30, "sign": -1},
    "B": {"rule": "K", "leg": "2x", "cash_yield": "none", "coef": 0.30, "lag_days": 30, "sign": -1},
    "C": {"rule": "K", "leg": "2x", "cash_yield": "none", "coef": 0.15, "lag_days": 30, "sign": +1},
}

#: the study's latitude (Kamstra-Kramer-Levi's 40.7 N)
LATITUDE = 40.7


def night_hours(doy) -> np.ndarray:
    """Hours of night at `LATITUDE` for day-of-year `doy` (Cooper's declination)."""
    decl = np.radians(23.44 * np.sin(2.0 * np.pi * (284.0 + np.asarray(doy, dtype=np.float64)) / 365.0))
    lat = np.radians(LATITUDE)
    cos_w = np.clip(-np.tan(lat) * np.tan(decl), -1.0, 1.0)
    return 24.0 - np.arccos(cos_w) * 24.0 / np.pi


def night_change(doy, lag_days: int) -> np.ndarray:
    """`H(doy) - H(doy - lag_days)` on the 365-day year (wrapping into the previous year)."""
    doy = np.asarray(doy, dtype=np.float64)
    prev = np.mod(doy - 1.0 - lag_days, 365.0) + 1.0
    return night_hours(doy) - night_hours(prev)


def _max_year_change(lag_days: int) -> float:
    """`max_year(H(d) - H(d - lag_days))`: the peak of the night-length increase over the year."""
    return float(np.max(night_change(np.arange(1.0, 366.0), lag_days)))


def night_factor(data, spec) -> np.ndarray:
    """`(P, T)` the normalised fall-weighted night-length change in `[0, 1]`."""
    lab = np.asarray(c1_lib.day_labels(data), dtype="datetime64[D]")
    doy = np.minimum((lab - lab.astype("datetime64[Y]")).astype(np.int64) + 1, 365)
    lag = int(spec["lag_days"])
    return np.clip(night_change(doy, lag) / _max_year_change(lag), 0.0, 1.0)


def targets(data, spec):
    """The production target scaled by the SAD seasonal factor."""
    e2 = fe_signals.e2_up(data.price)
    comps = fe_signals.early_components(data.price)
    on_eff, factor = fe_signals.r2_state(data.price, e2, comps)
    base = fe_signals.sized_wants(on_eff, factor, data.vol)
    tilt = 1.0 + float(spec["sign"]) * float(spec["coef"]) * night_factor(data, spec)
    return np.minimum(base * tilt, base) if spec["sign"] > 0 else base * tilt
