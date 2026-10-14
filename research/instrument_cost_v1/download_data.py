#!/usr/bin/env python3
"""Download real daily bars for QQQ / QLD / TQQQ from the Yahoo chart API.

Network is used only for this public market data. Writes one CSV per symbol to
data/historical/instrument_cost_v1_<symbol>.csv with the same columns as the
existing research inputs (stress_robustness_v1_daily.csv):

    symbol,date,open,high,low,close,adjclose,volume,dividend

`close`/`open`/`high`/`low` are Yahoo's split-adjusted prices; `dividend` is
reconstructed from the split+dividend adjusted `adjclose` series as

    dividend[t] = close[t-1] * (adjclose[t]/adjclose[t-1]) - close[t]

so that (close[t] + dividend[t]) / close[t-1] is exactly the fund's close-to-close
total return. No dependency on Yahoo's raw dividend events (which are not
split-adjusted).

Usage: python research/instrument_cost_v1/download_data.py
"""

from __future__ import annotations

import csv
import hashlib
import json
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
OUTDIR = ROOT / "data/historical"

TICKERS = ("QQQ", "QLD", "TQQQ")
PERIOD1 = 1262304000   # 2010-01-01 UTC
PERIOD2 = 1794000000   # 2026-11-07 UTC (past the last bar)
CHART = ("https://query1.finance.yahoo.com/v8/finance/chart/{sym}"
         "?period1={p1}&period2={p2}&interval=1d&events=div%2Csplit")


def fetch(sym: str) -> dict:
    url = CHART.format(sym=sym, p1=PERIOD1, p2=PERIOD2)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (research)"})
    with urllib.request.urlopen(req, timeout=60) as fh:
        return json.load(fh)


def bars(sym: str) -> list[tuple]:
    d = fetch(sym)["chart"]["result"][0]
    q = d["indicators"]["quote"][0]
    adj = d["indicators"]["adjclose"][0]["adjclose"]
    rows = []
    prev_close = None
    for k, ts in enumerate(d["timestamp"]):
        o, h, lo, c = q["open"][k], q["high"][k], q["low"][k], q["close"][k]
        a, v = adj[k], q["volume"][k]
        if None in (o, h, lo, c, a):
            continue
        date = datetime.fromtimestamp(ts, tz=timezone.utc).date().isoformat()
        div = 0.0
        if prev_close is not None and k > 0 and adj[k - 1]:
            div = prev_close * (a / adj[k - 1]) - c
            if div < 0 and div > -1e-6:
                div = 0.0
        rows.append((sym, date, o, h, lo, c, a, v, div))
        prev_close = c
    return rows


def main() -> None:
    manifest = {"source": "Yahoo Finance chart API v8 (query1.finance.yahoo.com)", "tickers": {}}
    for sym in TICKERS:
        rows = bars(sym)
        path = OUTDIR / f"instrument_cost_v1_{sym.lower()}.csv"
        with path.open("w", newline="") as fh:
            w = csv.writer(fh, lineterminator="\n")
            w.writerow(("symbol", "date", "open", "high", "low", "close", "adjclose", "volume", "dividend"))
            for r in rows:
                w.writerow((r[0], r[1], f"{r[2]:.6f}", f"{r[3]:.6f}", f"{r[4]:.6f}", f"{r[5]:.6f}",
                            f"{r[6]:.6f}", int(r[7]), f"{r[8]:.8f}"))
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        manifest["tickers"][sym] = {
            "path": str(path.relative_to(ROOT)),
            "url": CHART.format(sym=sym, p1=PERIOD1, p2=PERIOD2),
            "first_date": rows[0][1], "last_date": rows[-1][1], "rows": len(rows),
            "sha256": digest,
        }
        print(f"{sym}: {len(rows)} rows {rows[0][1]} -> {rows[-1][1]} sha256 {digest[:16]}")
    (HERE / "DOWNLOAD_PROVENANCE.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
