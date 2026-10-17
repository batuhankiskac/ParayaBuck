"""research/closeout_v1/make_bil_star.py -- the BIL* bill price path as an extra-data series.

`tbill_sweep_v1`'s construction, which `r2_tbill_combo_v1` also used: a synthetic T-bill ETF whose
only return is the bill yield net of BIL's 0.1356 %/yr expense ratio,

    BIL*_t = BIL*_{t-1} * (1 + (DTB3_t - 0.1356 %) / 252)          (one accrual per *session*)

The batch framework needs the series as a *date-keyed price path* (`batch_screen.extra`), and
sessions are window-specific (QQQ, the six OOS indices, the 21 ETFs and a bootstrap chunk's drawn
source sessions all have their own calendars), so the single series this study registers accrues on
**every calendar day**:

    BIL*_d = BIL*_{d-1} * (1 + (DTB3_d - 0.1356 %) / 365)          (one accrual per calendar day)

The annual yield is the same object (a year of accruals is a year of `DTB3 - 0.1356 %`); the two
conventions differ only in how the accrual is distributed inside a week (a Monday carries three
calendar days against one session). `--check` prints both totals over the main window, so the
difference is on the record rather than hidden.

The rate column is `fe_data.load_rates()` (DTB3 forward-filled from 1954-01-04 on, TB3MS before
that) through `fe_data.rate_on` -- the same schedule every window in this campaign already reads
into the panel's `rate` column (`windows.main_panel` / `oos_panel` / `instrument_panel`), so BIL*
is derived from data already committed, not from a new download. The series therefore starts where
that schedule starts (TB3MS's first observation, 1934-01) so that the `M2` window (^GSPC
1934-1956) is covered as well; the window that ends before the schedule exists, if any, is
`NotApplicable` as for any other missing input.

    PYTHONPATH=. ~/.venvs/pb-research/bin/python -m research.closeout_v1.make_bil_star --write
    PYTHONPATH=. ~/.venvs/pb-research/bin/python -m research.closeout_v1.make_bil_star --check
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from datetime import date, timedelta
from pathlib import Path

import numpy as np

from research.fast_engine import fe_data
from research.fast_engine.fe_config import ROOT

#: BIL's expense ratio (tbill_sweep_v1 / r2_tbill_combo_v1)
EXPENSE = 0.001356
#: the last calendar day the series carries (past every window's last session, 2026-09-29)
LAST_DAY = "2026-12-31"
REL = "data/historical/closeout_v1_bil_star.csv"
COLUMN = "close"
#: TB3MS's first observation: where the campaign's own rate schedule (`fe_data.rate_on`) starts
START_DAY = "1934-01-01"


def calendar_days(first: str = START_DAY, last: str = LAST_DAY) -> list[str]:
    """Every calendar day in `[first, last]` (the series must answer any window's session date)."""
    d0, d1 = date.fromisoformat(first), date.fromisoformat(last)
    return [(d0 + timedelta(days=k)).isoformat() for k in range((d1 - d0).days + 1)]


def bil_star(days: list[str], per_day: float = 365.0) -> np.ndarray:
    """The BIL* price path on `days`, `(len(days),)`; `per_day` is the accrual denominator."""
    rate = fe_data.rate_on(list(days), *fe_data.load_rates())
    acc = 1.0 + (rate - EXPENSE) / per_day
    out = np.empty(len(days), dtype=np.float64)
    level = 1.0
    for k in range(len(days)):
        if k:
            level = level * acc[k]
        out[k] = level
    return out


def session_path(days) -> np.ndarray:
    """The reference session construction (`/252` per session) on `days` -- the `--check` arm."""
    rate = fe_data.rate_on(list(days), *fe_data.load_rates())
    return np.cumprod(1.0 + (rate - EXPENSE) / 252.0)


def write(path: Path | None = None) -> Path:
    days = calendar_days()
    vals = bil_star(days)
    p = Path(path or (ROOT / REL))
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", newline="") as fh:
        w = csv.writer(fh, lineterminator="\n")  # LF: the repo's git normalisation would turn
        w.writerow(["date", COLUMN])             # CRLF into LF and break the declared SHA-256
        for d, v in zip(days, vals):
            w.writerow([d, f"{v:.10f}"])
    return p


def decl(path: Path | None = None) -> dict:
    """The `EXTRA_DATA.json` series declaration of the file just written."""
    p = Path(path or (ROOT / REL))
    with p.open() as fh:
        rows = list(csv.DictReader(fh))
    return {
        "bytes": p.stat().st_size,
        "column": COLUMN,
        "fetched": "2026-10-17",
        "file": REL,
        "price": COLUMN,
        "range": [rows[0]["date"], rows[-1]["date"]],
        "rows": len(rows),
        "sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
        "source": ("derived: BIL* = DTB3 - 0.1356 %/yr (tbill_sweep_v1's construction), accrued "
                   "per calendar day, from data/historical/stress_robustness_v1_dtb3.csv and "
                   "data/historical/ewma_band_v1_tb3ms.csv"),
        "url": "https://fred.stlouisfed.org/graph/fredgraph.csv?id=DTB3",
    }


def check() -> dict:
    """The two accrual conventions' annual yields over the main window (the /365 vs /252 choice)."""
    from research.batch_screen import windows

    days, _p, i0, i1 = windows.main_panel(0.0)
    win = [str(d) for d in days[i0:]]
    cd = calendar_days(win[0], win[-1])
    cal = bil_star(cd)[np.searchsorted(np.asarray(cd), np.asarray(win, dtype="<U10"))]
    ses = session_path(win)
    years = (date.fromisoformat(win[-1]) - date.fromisoformat(win[0])).days / 365.25
    out = {
        "window": [win[0], win[-1]],
        "sessions": len(win),
        "years": years,
        "cagr_calendar_365_pct": float((cal[-1] / cal[0]) ** (1 / years) - 1) * 100.0,
        "cagr_session_252_pct": float((ses[-1] / ses[0]) ** (1 / years) - 1) * 100.0,
        "mean_dtb3_pct": float(fe_data.rate_on(win, *fe_data.load_rates()).mean()) * 100.0,
    }
    out["cagr_diff_pp"] = out["cagr_calendar_365_pct"] - out["cagr_session_252_pct"]
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m research.closeout_v1.make_bil_star")
    ap.add_argument("--write", action="store_true", help="write the CSV and print its declaration")
    ap.add_argument("--check", action="store_true", help="print the two conventions' CAGRs")
    args = ap.parse_args(argv)
    if args.write:
        p = write()
        print(f"wrote {p} ({p.stat().st_size} bytes)")
        print("EXTRA_DATA.json series declaration:")
        print(json.dumps(decl(), indent=2, sort_keys=True))
    if args.check:
        print(json.dumps(check(), indent=2, sort_keys=True))
    if not (args.write or args.check):
        ap.print_help()
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
