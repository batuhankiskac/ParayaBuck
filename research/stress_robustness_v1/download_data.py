#!/usr/bin/env python3
"""Download research data for stress_robustness_v1 (network; research only, never used by tests).

Writes data/historical/stress_robustness_v1_daily.csv (QQQ/SPY/QLD/VOO daily
OHLC, split-adjusted, plus cash dividends per split-adjusted share),
data/historical/stress_robustness_v1_dtb3.csv (FRED DTB3) and DATA_MANIFEST.json.
"""

from __future__ import annotations

import csv
import datetime as dt
import hashlib
import io
import json
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT_BARS = ROOT / "data/historical/stress_robustness_v1_daily.csv"
OUT_TB = ROOT / "data/historical/stress_robustness_v1_dtb3.csv"
MANIFEST = Path(__file__).parent / "DATA_MANIFEST.json"
SYMBOLS = ("QQQ", "SPY", "QLD", "VOO")
YAHOO = "https://query1.finance.yahoo.com/v8/finance/chart/{sym}?period1=915148800&period2={end}&interval=1d&events=div%7Csplit"
FRED = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=DTB3"
UA = {"User-Agent": "Mozilla/5.0"}


def fetch(url: str) -> bytes:
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60) as resp:
        return resp.read()


def ny_date(ts: int, gmtoffset: int) -> str:
    return dt.datetime.fromtimestamp(ts + gmtoffset, dt.UTC).strftime("%Y-%m-%d")


def main() -> None:
    end = int(time.time())
    rows, meta = [], {}
    for sym in SYMBOLS:
        url = YAHOO.format(sym=sym, end=end)
        res = json.loads(fetch(url))["chart"]["result"][0]
        off = res["meta"]["gmtoffset"]
        q = res["indicators"]["quote"][0]
        adj = res["indicators"]["adjclose"][0]["adjclose"]
        divs = {}
        for ev in (res.get("events") or {}).get("dividends", {}).values():
            d = ny_date(ev["date"], off)
            divs[d] = divs.get(d, 0.0) + float(ev["amount"])
        n = 0
        for i, ts in enumerate(res["timestamp"]):
            if None in (q["open"][i], q["close"][i]):
                continue
            d = ny_date(ts, off)
            rows.append(
                [
                    sym,
                    d,
                    q["open"][i],
                    q["high"][i],
                    q["low"][i],
                    q["close"][i],
                    adj[i],
                    q["volume"][i],
                    divs.get(d, 0.0),
                ]
            )
            n += 1
        splits = [
            (ny_date(v["date"], off), v["splitRatio"]) for v in (res.get("events") or {}).get("splits", {}).values()
        ]
        meta[sym] = {
            "url": url,
            "rows": n,
            "first": rows[-n][1],
            "last": rows[-1][1],
            "dividend_events": len(divs),
            "splits": sorted(splits),
        }
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["symbol", "date", "open", "high", "low", "close", "adjclose", "volume", "dividend"])
    w.writerows(rows)
    OUT_BARS.write_text(buf.getvalue())
    tb = fetch(FRED)
    OUT_TB.write_bytes(tb)
    manifest = {
        "downloaded_utc": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "files": {
            str(OUT_BARS.relative_to(ROOT)): {
                "source": "Yahoo Finance chart API v8 (query1.finance.yahoo.com), interval=1d, events=div|split",
                "notes": "OHLC split-adjusted, not dividend-adjusted; dividend = cash per split-adjusted share on ex-date (NY date); adjclose is Yahoo's split+dividend adjusted close (reference only)",
                "sha256": hashlib.sha256(OUT_BARS.read_bytes()).hexdigest(),
                "symbols": meta,
            },
            str(OUT_TB.relative_to(ROOT)): {
                "source": FRED,
                "series": "DTB3, 3-Month Treasury Bill Secondary Market Rate, discount basis, percent, daily",
                "sha256": hashlib.sha256(tb).hexdigest(),
                "rows": tb.decode().count("\n") - 1,
            },
        },
    }
    MANIFEST.write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
