#!/usr/bin/env python3
"""Download research data for leverage_frontier_v1 (network; research only, never used by tests).

Sources (public daily history only):
  - Yahoo Finance chart API v8, interval=1d: TQQQ (synthetic-3x validation),
    IEF (BOND10 validation), GLD (gold, 2004-11 ->), GC=F (gold futures
    front-month continuation, pre-GLD proxy and GLD cross-check).
  - FRED fredgraph.csv: DGS10 (10y constant-maturity yield, BOND10 input) and
    the LBMA gold fixing series GOLDAMGBD228NLBM / GOLDPMGBD228NLBM (attempted;
    recorded as unavailable if FRED no longer serves them).

Writes only the needed columns, clipped to END (the last bar of the committed
stress_robustness_v1 inputs, so every series ends on the same day):
  data/historical/leverage_frontier_v1_daily.csv   symbol,date,open,close,dividend
  data/historical/leverage_frontier_v1_dgs10.csv   observation_date,DGS10
  data/historical/leverage_frontier_v1_gold_fred.csv (only if a FRED gold series exists)
and research/leverage_frontier_v1/DATA_MANIFEST.json.
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
OUT_BARS = ROOT / "data/historical/leverage_frontier_v1_daily.csv"
OUT_DGS10 = ROOT / "data/historical/leverage_frontier_v1_dgs10.csv"
OUT_GOLD_FRED = ROOT / "data/historical/leverage_frontier_v1_gold_fred.csv"
MANIFEST = Path(__file__).parent / "DATA_MANIFEST.json"
SYMBOLS = ("TQQQ", "IEF", "GLD", "GC=F")
START_TS = 915148800  # 1999-01-01
END = "2026-09-29"  # last bar of data/historical/stress_robustness_v1_daily.csv
YAHOO = "https://query1.finance.yahoo.com/v8/finance/chart/{sym}?period1={start}&period2={end}&interval=1d&events=div%7Csplit"
FRED = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={sid}"
FRED_GOLD = ("GOLDAMGBD228NLBM", "GOLDPMGBD228NLBM")
UA = {"User-Agent": "Mozilla/5.0"}


def fetch(url: str, tries: int = 4) -> bytes:
    err = None
    for k in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=180) as resp:
                return resp.read()
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:  # retry transient errors
            err = e
            if isinstance(e, urllib.error.HTTPError) and e.code in (400, 404):
                break
            time.sleep(5 * (k + 1))
    raise RuntimeError(f"fetch failed: {url}: {err!r}")


def ny_date(ts: int, gmtoffset: int) -> str:
    return dt.datetime.fromtimestamp(ts + gmtoffset, dt.UTC).strftime("%Y-%m-%d")


def utc_now() -> str:
    return dt.datetime.now(dt.UTC).isoformat(timespec="seconds")


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def fred_csv(sid: str):
    """Return (url, retrieved_utc, rows[(date, value_str)]) clipped to END; header column = sid."""
    url = FRED.format(sid=sid)
    raw = fetch(url).decode()
    when = utc_now()
    rd = csv.reader(io.StringIO(raw))
    head = next(rd)
    if len(head) < 2 or head[1] != sid:
        raise RuntimeError(f"unexpected FRED header for {sid}: {head}")
    rows = [(r[0], r[1]) for r in rd if r and r[0] <= END]
    return url, when, rows


def main() -> None:
    end_ts = int(time.time())
    rows, meta = [], {}
    for sym in SYMBOLS:
        url = YAHOO.format(sym=sym.replace("=", "%3D"), start=START_TS, end=end_ts)
        res = json.loads(fetch(url))["chart"]["result"][0]
        when = utc_now()
        off = res["meta"]["gmtoffset"]
        q = res["indicators"]["quote"][0]
        divs = {}
        for ev in (res.get("events") or {}).get("dividends", {}).values():
            d = ny_date(ev["date"], off)
            divs[d] = divs.get(d, 0.0) + float(ev["amount"])
        sym_rows, dropped = [], 0
        for i, ts in enumerate(res["timestamp"]):
            d = ny_date(ts, off)
            if d > END:
                continue
            if q["open"][i] is None or q["close"][i] is None:
                dropped += 1
                continue
            sym_rows.append([sym, d, q["open"][i], q["close"][i], divs.get(d, 0.0)])
        # Yahoo occasionally repeats a date (intraday stub); keep the last row per date
        by_date = {r[1]: r for r in sym_rows}
        sym_rows = [by_date[d] for d in sorted(by_date)]
        rows.extend(sym_rows)
        splits = [(ny_date(v["date"], off), v["splitRatio"]) for v in (res.get("events") or {}).get("splits", {}).values()]
        meta[sym] = {
            "url": url,
            "retrieved_utc": when,
            "rows": len(sym_rows),
            "rows_dropped_null_open_or_close": dropped,
            "first": sym_rows[0][1],
            "last": sym_rows[-1][1],
            "dividend_events": sum(1 for r in sym_rows if r[4]),
            "splits": sorted(splits),
        }
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["symbol", "date", "open", "close", "dividend"])
    w.writerows(rows)
    OUT_BARS.write_text(buf.getvalue())

    files = {
        str(OUT_BARS.relative_to(ROOT)): {
            "source": "Yahoo Finance chart API v8 (query1.finance.yahoo.com), interval=1d, events=div|split",
            "notes": "OHLC split-adjusted, not dividend-adjusted (same convention as stress_robustness_v1_daily.csv); "
                     "dividend = cash per split-adjusted share on ex-date (NY date); only open/close/dividend kept; "
                     f"clipped to <= {END}. GC=F is the unadjusted front-month COMEX gold futures continuation.",
            "sha256": sha(OUT_BARS),
            "symbols": meta,
        }
    }

    url, when, d10 = fred_csv("DGS10")
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["observation_date", "DGS10"])
    w.writerows(d10)
    OUT_DGS10.write_text(buf.getvalue())
    vals = [r for r in d10 if r[1] not in ("", ".")]
    files[str(OUT_DGS10.relative_to(ROOT))] = {
        "source": url,
        "retrieved_utc": when,
        "series": "DGS10, Market Yield on U.S. Treasury Securities at 10-Year Constant Maturity, percent, daily",
        "sha256": sha(OUT_DGS10),
        "rows": len(d10),
        "rows_with_value": len(vals),
        "first": d10[0][0],
        "last": d10[-1][0],
        "notes": f"clipped to <= {END}; '.' = no observation (holiday)",
    }

    gold_attempts = {}
    for sid in FRED_GOLD:
        try:
            url, when, g = fred_csv(sid)
        except Exception as e:  # recorded, not guessed
            gold_attempts[sid] = {"url": FRED.format(sid=sid), "attempted_utc": utc_now(), "status": f"unavailable: {e}"}
            continue
        gv = [r for r in g if r[1] not in ("", ".")]
        if not gv:
            gold_attempts[sid] = {"url": url, "attempted_utc": when, "status": "no observations"}
            continue
        buf = io.StringIO()
        w = csv.writer(buf, lineterminator="\n")
        w.writerow(["observation_date", sid])
        w.writerows(g)
        OUT_GOLD_FRED.write_text(buf.getvalue())
        files[str(OUT_GOLD_FRED.relative_to(ROOT))] = {
            "source": url, "retrieved_utc": when, "sha256": sha(OUT_GOLD_FRED),
            "rows": len(g), "rows_with_value": len(gv), "first": g[0][0], "last": g[-1][0],
        }
        gold_attempts[sid] = {"url": url, "attempted_utc": when, "status": "ok"}
        break

    manifest = {
        "campaign": "leverage_frontier_v1",
        "downloaded_utc": utc_now(),
        "end_clip": END,
        "files": files,
        "fred_gold_attempts": gold_attempts,
        "reused_committed_inputs": {
            "data/historical/stress_robustness_v1_daily.csv": "see research/stress_robustness_v1/DATA_MANIFEST.json",
            "data/historical/stress_robustness_v1_dtb3.csv": "see research/stress_robustness_v1/DATA_MANIFEST.json",
        },
    }
    MANIFEST.write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
