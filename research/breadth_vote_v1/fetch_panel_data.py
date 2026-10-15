#!/usr/bin/env python3
"""Fetch the two inputs this study needs that the repository does not already hold:

  * ^RUT (Russell 2000) daily bars -- the IWM proxy before IWM's 2000-05-26 inception.
  * ^GSPC (S&P 500) daily bars  -- the SPY proxy before SPY's 1993-01-29 inception. The
    repository's index file (`ewma_band_v1_index.csv`) holds ^GSPC only for 1927-1956 (its
    e2_confirmation_v1 M2 window) and ^IXIC only for 1971-1999, neither of which spans the
    panel-imposed OOS windows.

Source: Yahoo Finance chart API (public, no key):
  https://query1.finance.yahoo.com/v8/finance/chart/<SYMBOL>?period1=0&period2=9999999999&interval=1d

Writes data/historical/breadth_vote_v1_{rut,gspc}.csv with columns date,open,high,low,close
(close is the split-adjusted close; both are indices, so there are no dividends). Run from the
worktree root; the study's DATA_MANIFEST.json records the URL, date range and SHA-256.
"""

from __future__ import annotations

import csv
import hashlib
import json
import sys
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SYMBOLS = {"rut": "%5ERUT", "gspc": "%5EGSPC"}
URL = "https://query1.finance.yahoo.com/v8/finance/chart/{sym}?period1=0&period2=9999999999&interval=1d"


def fetch(sym: str) -> list[tuple[str, float, float, float, float]]:
    req = urllib.request.Request(URL.format(sym=sym), headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=120) as fh:
        payload = json.load(fh)
    res = payload["chart"]["result"][0]
    q = res["indicators"]["quote"][0]
    rows = []
    for k, t in enumerate(res["timestamp"]):
        o, h, lo, c = q["open"][k], q["high"][k], q["low"][k], q["close"][k]
        if None in (o, h, lo, c):
            continue
        rows.append((datetime.fromtimestamp(t, tz=UTC).date().isoformat(), o, h, lo, c))
    rows.sort()
    return rows


def main() -> None:
    for name, sym in SYMBOLS.items():
        out = ROOT / f"data/historical/breadth_vote_v1_{name}.csv"
        rows = fetch(sym)
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("w", newline="") as fh:
            w = csv.writer(fh, lineterminator="\n")
            w.writerow(["date", "open", "high", "low", "close"])
            for d, o, h, lo, c in rows:
                w.writerow([d, f"{o:.10g}", f"{h:.10g}", f"{lo:.10g}", f"{c:.10g}"])
        print(f"{out}: {len(rows)} rows, {rows[0][0]} -> {rows[-1][0]}, "
              f"{out.stat().st_size} bytes, sha256 {hashlib.sha256(out.read_bytes()).hexdigest()}")


if __name__ == "__main__":
    sys.exit(main())
