#!/usr/bin/env python3
"""Download research data for e2_confirmation_v1 (network; research only, never used by tests).

Sources:
  - Yahoo Finance chart API v8, interval=1d: ^IXIC, ^GSPC, ^N225, ^GDAXI, ^FTSE
    (price indices, no dividends). Each symbol is clipped to its campaign end date.
  - FRED fredgraph CSV: TB3MS (3-month T-bill secondary market rate, monthly, 1934->).

Writes:
  data/historical/e2_confirmation_v1_index.csv  symbol,date,open,high,low,close (open/high/low may be empty)
  data/historical/e2_confirmation_v1_tb3ms.csv  observation_date,TB3MS
and research/e2_confirmation_v1/DATA_MANIFEST.json (URL, retrieval time, coverage, rows, SHA-256).
DTB3 is the committed data/historical/stress_robustness_v1_dtb3.csv (not re-downloaded).
"""

from __future__ import annotations

import csv
import datetime as dt
import hashlib
import io
import json
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT_INDEX = ROOT / "data/historical/e2_confirmation_v1_index.csv"
OUT_TB = ROOT / "data/historical/e2_confirmation_v1_tb3ms.csv"
MANIFEST = Path(__file__).parent / "DATA_MANIFEST.json"
SYMBOLS = {"^IXIC": "1999-12-31", "^GSPC": "1956-12-31", "^N225": "2026-09-29",
           "^GDAXI": "2026-09-29", "^FTSE": "2026-09-29"}
PERIOD1 = -1577923200  # 1920-01-01 UTC
YAHOO = "https://query1.finance.yahoo.com/v8/finance/chart/{sym}?period1={p1}&period2={p2}&interval=1d&events=history"
FRED = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=TB3MS"
UA = {"User-Agent": "Mozilla/5.0"}


def fetch(url: str, tries: int = 4) -> bytes:
    err = None
    for k in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=180) as resp:
                return resp.read()
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            err = e
            if isinstance(e, urllib.error.HTTPError) and e.code in (400, 403, 404):
                break
            time.sleep(5 * (k + 1))
    raise RuntimeError(f"fetch failed: {url}: {err!r}")


def utc_now() -> str:
    return dt.datetime.now(dt.UTC).isoformat(timespec="seconds")


def sha_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def fmt(x):
    return "" if x is None else repr(float(x))


def yahoo():
    rows, meta = [], {}
    for sym, end in SYMBOLS.items():
        p2 = int(dt.datetime.fromisoformat(end).replace(tzinfo=dt.UTC).timestamp()) + 3 * 86400
        url = YAHOO.format(sym=sym.replace("^", "%5E"), p1=PERIOD1, p2=p2)
        raw = fetch(url)
        when = utc_now()
        res = json.loads(raw)["chart"]["result"][0]
        off = res["meta"]["gmtoffset"]
        q = res["indicators"]["quote"][0]
        by_date, null_close, dup = {}, 0, 0
        for i, ts in enumerate(res["timestamp"]):
            d = dt.datetime.fromtimestamp(ts + off, dt.UTC).strftime("%Y-%m-%d")
            if d > end:
                continue
            o, h, lo, c = (q[k][i] for k in ("open", "high", "low", "close"))
            if c is None:
                null_close += 1
                continue
            if d in by_date:
                dup += 1
            by_date[d] = [sym, d, fmt(o), fmt(h), fmt(lo), fmt(c)]  # keep the last row per date
        sym_rows = [by_date[d] for d in sorted(by_date)]
        rows.extend(sym_rows)
        meta[sym] = {"url": url, "retrieved_utc": when, "raw_response_sha256": sha_bytes(raw),
                     "exchange_timezone": res["meta"].get("exchangeTimezoneName"), "gmtoffset_s": off,
                     "rows": len(sym_rows), "rows_dropped_null_close": null_close,
                     "duplicate_dates_kept_last": dup, "first": sym_rows[0][1], "last": sym_rows[-1][1],
                     "clip_end": end}
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["symbol", "date", "open", "high", "low", "close"])
    w.writerows(rows)
    OUT_INDEX.write_text(buf.getvalue())
    return meta


def fred():
    raw = fetch(FRED)
    when = utc_now()
    text = raw.decode("utf-8-sig")
    rdr = list(csv.reader(io.StringIO(text)))
    head = rdr[0]
    body = [r for r in rdr[1:] if r and r[0] <= "1956-12-31"]
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["observation_date", "TB3MS"])
    w.writerows([[r[0], r[1]] for r in body])
    OUT_TB.write_text(buf.getvalue())
    return {"url": FRED, "retrieved_utc": when, "raw_response_sha256": sha_bytes(raw), "raw_header": head,
            "rows": len(body), "first": body[0][0], "last": body[-1][0], "clip_end": "1956-12-31"}


def main() -> None:
    ymeta = yahoo()
    tmeta = fred()
    manifest = {
        "campaign": "e2_confirmation_v1",
        "downloaded_utc": utc_now(),
        "files": {
            str(OUT_INDEX.relative_to(ROOT)): {
                "source": "Yahoo Finance chart API v8 (query1.finance.yahoo.com), interval=1d, events=history",
                "notes": "price indices (no dividends); timestamps converted to exchange-local dates with the "
                         "response gmtoffset; rows with a null close dropped; null open/high/low kept as empty; "
                         "OHLC as served (float repr); clipped per symbol to clip_end.",
                "sha256": hashlib.sha256(OUT_INDEX.read_bytes()).hexdigest(),
                "symbols": ymeta},
            str(OUT_TB.relative_to(ROOT)): {
                "source": "FRED fredgraph CSV, series TB3MS (3-Month Treasury Bill Secondary Market Rate, "
                          "monthly average, percent)",
                "sha256": hashlib.sha256(OUT_TB.read_bytes()).hexdigest(), **tmeta},
        },
        "reused_committed_inputs": {
            "data/historical/stress_robustness_v1_dtb3.csv": "DTB3 daily 1954-01-04 -> 2026-09-28; "
                                                              "see research/stress_robustness_v1/DATA_MANIFEST.json",
        },
    }
    MANIFEST.write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
