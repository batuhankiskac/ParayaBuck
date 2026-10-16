#!/usr/bin/env python3
"""Download ^NDX daily bars (Yahoo chart API) for the I2 calibration window.

One public source, no credentials, no other network use. Writes
`data/historical/vol_shape_v1_ndx.csv` with the header
`date,open,high,low,close,adjclose` and prints the SHA-256 and row count so the
DATA_MANIFEST can record them. Idempotent: an existing file is not overwritten
unless `--force` is given.

    python -m research.vol_shape_v1.fetch_ndx
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
OUT = ROOT / "data/historical/vol_shape_v1_ndx.csv"

# 1985-10-01 -> 2000-03-08 (inclusive); the calibration window of the pre-registration.
FIRST, LAST = "1985-10-01", "2000-03-08"
URL = (
    "https://query1.finance.yahoo.com/v8/finance/chart/%5ENDX"
    "?period1={p1}&period2={p2}&interval=1d&events=div%2Csplit"
)


def epoch(d: str) -> int:
    return int(datetime.strptime(d, "%Y-%m-%d").replace(tzinfo=UTC).timestamp())


def main() -> None:
    if OUT.exists() and "--force" not in sys.argv:
        print(f"{OUT} exists; pass --force to re-download")
    url = URL.format(p1=epoch(FIRST), p2=epoch(LAST))
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (research)"})
    with urllib.request.urlopen(req, timeout=60) as fh:
        raw = json.loads(fh.read().decode())
    res = raw["chart"]["result"][0]
    ts = res["timestamp"]
    q = res["indicators"]["quote"][0]
    adj = res["indicators"].get("adjclose", [{}])[0].get("adjclose")
    rows = []
    for k, t in enumerate(ts):
        if None in (q["open"][k], q["close"][k]):
            continue
        d = datetime.fromtimestamp(t, tz=UTC).strftime("%Y-%m-%d")
        rows.append(
            (
                d,
                q["open"][k],
                q["high"][k],
                q["low"][k],
                q["close"][k],
                adj[k] if adj else q["close"][k],
            )
        )
    rows.sort()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", newline="") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(["date", "open", "high", "low", "close", "adjclose"])
        for r in rows:
            w.writerow([r[0]] + [f"{v:.10g}" for v in r[1:]])
    sha = hashlib.sha256(OUT.read_bytes()).hexdigest()
    print(f"url: {url}")
    print(f"rows: {len(rows)}  first: {rows[0][0]}  last: {rows[-1][0]}")
    print(f"sha256: {sha}")


if __name__ == "__main__":
    main()
