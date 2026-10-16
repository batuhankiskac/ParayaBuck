"""plugins_b3 -- the extra-data alignment check (no simulation, no performance number).

Asserts, for the main window and each of the five OOS windows the IB03 plugin is applicable on,
that `price * data.extra("underlying_relhigh")` reproduces the source file's own high (and the low
likewise) on every session of the window, i.e. that the derived relative series is the literal
high/low of the panel's own price series. Tolerance 1e-9 relative: the framework's
`extra.write_series` stores ten significant digits (`%.10g`).

    PYTHONPATH=. ~/.venvs/pb-research/bin/python -m research.plugins_b3.check_data
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

import numpy as np

from research.batch_screen import api, windows

ROOT = Path(__file__).resolve().parents[2]
SOURCES = {
    "main": ("data/historical/stress_robustness_v1_daily.csv", "QQQ"),
    "M1": ("data/historical/ewma_band_v1_index.csv", "^IXIC"),
    "M2": ("data/historical/ewma_band_v1_index.csv", "^GSPC"),
    "M3": ("data/historical/ewma_band_v1_index.csv", "^N225"),
    "M4": ("data/historical/ewma_band_v1_index.csv", "^GDAXI"),
    "M5": ("data/historical/ewma_band_v1_index.csv", "^FTSE"),
}


def _source_rows(rel, symbol):
    out = {}
    with (ROOT / rel).open() as fh:
        for r in csv.DictReader(fh):
            if r["symbol"] == symbol:
                out[r["date"]] = (float(r["high"]), float(r["low"]))
    return out


def panel(window: str) -> api.MarketData:
    if window == "main":
        _days, p, _i0, _i1 = windows.main_panel()
    else:
        _days, p, _i0, _i1 = windows.oos_panel(window)
    return p


def main(argv=None) -> int:
    bad = 0
    for window in ("main", "M1", "M2", "M3", "M4", "M5"):
        p = panel(window)
        rows = _source_rows(*SOURCES[window])
        hi = np.array([rows.get(d, (np.nan, np.nan))[0] for d in p.days])
        lo = np.array([rows.get(d, (np.nan, np.nan))[1] for d in p.days])
        have = np.isfinite(hi) & np.isfinite(lo)
        relh, rell = p.extra("underlying_relhigh"), p.extra("underlying_rellow")
        dh = np.nanmax(np.abs(p.price[0] * relh[0] - hi) / np.maximum(hi, 1e-12))
        dl = np.nanmax(np.abs(p.price[0] * rell[0] - lo) / np.maximum(lo, 1e-12))
        share = float(np.mean(np.isfinite(relh) & np.isfinite(rell)))
        ok = bool(dh < 1e-9 and dl < 1e-9 and share > 0.999
                  and np.array_equal(np.isfinite(relh[0]), have))
        bad += not ok
        print(f"  {window:5s} T={p.T:5d} covered={share:6.2%} max rel err high={dh:.2e} "
              f"low={dl:.2e} {'ok' if ok else 'MISMATCH'}", flush=True)
    print(f"alignment {'PASS' if not bad else 'FAIL'} ({bad} windows)")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
