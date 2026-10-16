"""plugins_c2 -- a macro series' state computed on the series' *own* calendar, read as-of a session.

The IC12 (NFCI), IC17 (HY OAS) and IC22 (broad dollar) arms are statements about a published
macro series: "above its trailing 156-week median", "up over 4 weeks", "a 60-observation change".
They are computed here on the series' own observations (the registered file, every finite row in
date order: one row per week for NFCI, one per FRED business day for the daily series), and a
session reads the value **as of its date**: the newest observation dated at or before the session.

Why not on the panel grid (`data.extra(key, ffill=True)` then panel shifts)?

* a 156-week median on the session grid is a 780-session sliding median, which inside a 256-path
  bootstrap chunk is gigabytes of temporaries; on the weekly calendar it is 156 values;
* the panel starts one warm-up year before the measured window, so a 3-year statistic computed on
  the panel would be undefined for the first two measured years (2000-2001 on main) although the
  series exists since 1971; the series' own history is the information a reader had at that date;
* inside a bootstrap chunk a column is a drawn *source* session (`api.MarketData.extra`), and the
  macro state of that source date is what "the chunk replays the extra series together with its
  returns" means -- forward-filling a weekly series across the chunk's block joins would carry a
  value from an unrelated block.

The series and its file come from the framework registry (`extra.resolve_series`, the same
SHA-256-verified file `data.extra` reads); `data.extra(key)` is also called once so the smoke runner
logs the key and an unknown key raises `extra.ExtraError` exactly as it would for any plugin.
Pure numpy, no per-session loop, no engine change.
"""

from __future__ import annotations

import warnings

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view

from research.batch_screen import extra as _extra


def window_key(data) -> str:
    return data.extra_window or data.window


def session_labels(data) -> np.ndarray:
    """`(P, T)` ISO date of the session behind every panel column (the drawn source session in a
    bootstrap chunk, the panel's own day otherwise) -- `IB14.session_calendar`'s convention."""
    if data.extra_index is not None:
        return np.asarray(data.extra_days).astype("<U10")[np.asarray(data.extra_index)]
    d = np.asarray(data.days).astype("<U10")
    if d.size == data.T - 1:  # defensive: a chunk's column 0 has no label of its own
        d = np.concatenate([d[:1], d])
    if d.size != data.T:
        raise ValueError(f"calendar length {d.size} != panel width {data.T}")
    return np.tile(d.reshape(1, -1), (data.P, 1))


def observations(data, key):
    """`(dates, values)` of the key's registered series: its finite rows, in date order."""
    data.extra(key)  # validates the key for this window and logs it for `smoke`
    name = _extra.resolve_series(key, window_key(data))
    dates, vals = _extra._column(name)
    ok = np.isfinite(vals)
    dates, vals = np.asarray(dates)[ok], np.asarray(vals, dtype=np.float64)[ok]
    order = np.argsort(dates, kind="stable")
    return dates[order], vals[order]


def lag(x, n):
    """`x` shifted `n` observations later (NaN in the first `n`)."""
    out = np.full_like(x, np.nan, dtype=np.float64)
    if n == 0:
        return np.asarray(x, dtype=np.float64).copy()
    if x.size > n:
        out[n:] = x[:-n]
    return out


def trailing_median(x, n):
    """Trailing `n`-observation median including the current one, NaN unless all `n` are finite."""
    out = np.full(x.shape, np.nan)
    if x.size < n:
        return out
    win = sliding_window_view(x, n)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        med = np.median(win, axis=1)
    full = np.isfinite(win).all(axis=1)
    out[n - 1 :] = np.where(full, med, np.nan)
    return out


def asof(dates, state, labels):
    """`state` (one value per observation) read at every session label: the newest observation
    dated at or before the label, NaN before the first observation.

    A bool `state` must be passed as float (1.0 / 0.0, NaN where undefined), so "undefined" is kept.
    """
    pos = np.searchsorted(dates, labels, side="right") - 1
    out = np.where(pos >= 0, np.asarray(state, dtype=np.float64)[np.clip(pos, 0, None)], np.nan)
    return out


def as_float(cond, defined):
    """A bool condition as 1.0 / 0.0, NaN where its inputs are undefined."""
    return np.where(defined, cond.astype(np.float64), np.nan)


__all__ = ["as_float", "asof", "lag", "observations", "session_labels", "trailing_median",
           "window_key"]
