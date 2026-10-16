"""The point-in-time (real-time) NFCI state series of `ic12_confirm_v1`.

The rule is `research/ideas/IC12.py`'s, unchanged: the committed `IC12.weekly_states` is called on
the series, so the lag (1 week), the trailing median (156 weeks) and the 4-week momentum are the
committed ones. What changes is *which observations the state is computed from* -- see
`PREREGISTRATION.md` §4:

* every session from the first ALFRED NFCI vintage (`2011-05-25`) on reads the state evaluated on
  the vintage that was current at that date, so nothing published later can enter;
* before that date no vintage exists, so the state is the committed plugin's own state on the
  committed current-revision series (this is the pre-registered fallback, and it is *identical* to
  `IC12`'s state on that segment -- `screen.py --harness` asserts it).

`build()` returns `(dates, state)` over every weekday of the NFCI record; a session (or a bootstrap
chunk's drawn source session) looks its own date up with the framework's `native.asof`.
"""

from __future__ import annotations

import csv
import functools
from datetime import date, timedelta
from pathlib import Path

import numpy as np

from research.ideas import IC12
from research.plugins_c2 import native

ROOT = Path(__file__).resolve().parents[2]
VINTAGE_FILE = ROOT / "data" / "historical" / "ic12_confirm_v1_nfci_vintages.csv"
LIVE_FILE = ROOT / "data" / "historical" / "plugins_c2_nfci.csv"
STATE_FILE = ROOT / "data" / "historical" / "ic12_confirm_v1_nfci_rt_state.csv"

#: arm A's spec, i.e. the veto rule's fixed parameters (B differs only in `mult`).
SPEC = dict(IC12.ARMS["A"])


def _read_pair_csv(path: Path, *, header: bool) -> tuple[np.ndarray, np.ndarray]:
    dates, vals = [], []
    with path.open() as fh:
        rd = csv.reader(fh)
        if header:
            next(rd)
        for row in rd:
            dates.append(row[0][:10])
            vals.append(float(row[1]) if row[1] else np.nan)
    return np.asarray(dates), np.asarray(vals, dtype=np.float64)


def live_series() -> tuple[np.ndarray, np.ndarray]:
    """The committed current-revision NFCI series (`(dates, values)`, ascending)."""
    d, v = _read_pair_csv(LIVE_FILE, header=True)
    o = np.argsort(d, kind="stable")
    return d[o], v[o]


@functools.lru_cache(maxsize=1)
def vintage_series() -> dict[str, tuple[np.ndarray, np.ndarray]]:
    """`{vintage_date: (dates, values)}` from the committed vintage panel (cached per process)."""
    out: dict[str, list[tuple[str, float]]] = {}
    with VINTAGE_FILE.open() as fh:
        rd = csv.reader(fh)
        next(rd)
        for v, d, x in rd:
            out.setdefault(v, []).append((d, float(x)))
    res = {}
    for v, rows in out.items():
        rows.sort()
        res[v] = (np.asarray([r[0] for r in rows]), np.asarray([r[1] for r in rows], dtype=np.float64))
    return res


def weekly_state(obs_dates: np.ndarray, obs_vals: np.ndarray) -> np.ndarray:
    """The committed `IC12` state (stress) per observation, 1.0 / 0.0 / NaN."""
    stress, _calm = IC12.weekly_states(obs_dates, obs_vals, SPEC)
    return stress


def weekdays(first: str, last: str) -> np.ndarray:
    d0, d1 = date.fromisoformat(first), date.fromisoformat(last)
    out = []
    d = d0
    while d <= d1:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return np.asarray(out)


def build() -> tuple[np.ndarray, np.ndarray]:
    """`(dates, state)`: the real-time state for every weekday from the first NFCI observation."""
    live_d, live_v = live_series()
    state_live = weekly_state(live_d, live_v)

    vint = vintage_series()
    v_dates = np.asarray(sorted(vint))
    #: the state of each vintage = the committed state of its own newest observation
    v_state = np.asarray([weekly_state(*vint[v])[-1] for v in v_dates], dtype=np.float64)

    cal = weekdays(str(live_d[0]), str(live_d[-1]))
    pos = np.searchsorted(v_dates, cal, side="right") - 1
    vintage_era = cal >= v_dates[0]
    fallback = native.asof(live_d, state_live, cal)
    out = np.where(vintage_era,
                   np.where(pos >= 0, v_state[np.clip(pos, 0, None)], np.nan),
                   fallback)
    return cal, out


@functools.lru_cache(maxsize=1)
def load() -> tuple[np.ndarray, np.ndarray]:
    """The committed real-time state table `(dates, state)`, cached per process."""
    if not STATE_FILE.exists():
        write_state_file()
    return _read_pair_csv(STATE_FILE, header=True)


def write_state_file() -> tuple[np.ndarray, np.ndarray]:
    """Rebuild `data/historical/ic12_confirm_v1_nfci_rt_state.csv` from the committed inputs."""
    cal, state = build()
    with STATE_FILE.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["date", "state"])
        for d, s in zip(cal, state):
            w.writerow([d, "" if not np.isfinite(s) else repr(float(s))])
    return cal, state


__all__ = ["SPEC", "build", "load", "live_series", "vintage_series", "weekdays", "weekly_state",
           "write_state_file"]
