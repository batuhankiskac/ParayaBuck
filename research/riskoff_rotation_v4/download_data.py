#!/usr/bin/env python3
from __future__ import annotations
import csv, datetime as dt, hashlib, io, json, time, urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "data/historical" / "riskoff_rotation_v4_etfs.csv"
MANIFEST = Path(__file__).parent / "DATA_MANIFEST.json"
EQUITY = ("XLB","XLE","XLF","XLI","XLK","XLP","XLU","XLV","XLY",
          "EWJ","EWG","EWU","EWA","EWH","EWS","EWL",
          "EFA","EEM","SPY","DIA","IWM")
DEF = ("IEF","GLD")
SYMBOLS = EQUITY + DEF
YAHOO = "https://query1.finance.yahoo.com/v8/finance/chart/{sym}?period1=915148800&period2={end}&interval=1d&events=div%7Csplit"
UA = {"User-Agent": "Mozilla/5.0"}

def fetch(url):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60) as r:
        return r.read()

def ny_date(ts, off):
    return dt.datetime.fromtimestamp(ts + off, dt.UTC).strftime("%Y-%m-%d")

def main():
    end = int(time.time())
    rows = []
    meta = {}
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
        n=0
        for i, ts in enumerate(res["timestamp"]):
            if None in (q["open"][i], q["high"][i], q["low"][i], q["close"][i], adj[i]):
                continue
            d = ny_date(ts, off)
            rows.append([sym,d,q["open"][i],q["high"][i],q["low"][i],q["close"][i],adj[i],q["volume"][i],divs.get(d,0.0)])
            n+=1
        meta[sym] = {"url":url,"rows":n,"first":rows[-n][1] if n else None,"last":rows[-1][1] if rows else None}
        print(sym, n, meta[sym]["first"], meta[sym]["last"], flush=True)
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["symbol","date","open","high","low","close","adjclose","volume","dividend"])
    w.writerows(rows)
    OUT.write_text(buf.getvalue())
    man = {
        "downloaded_utc": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "file": str(OUT.relative_to(ROOT)),
        "source": "Yahoo Finance chart API v8",
        "sha256": hashlib.sha256(OUT.read_bytes()).hexdigest(),
        "symbols": meta,
        "quality_rule": "range_vol_v2 _market_quality_ok ratio <=0.05",
        "tbill_source": "data/historical/stress_robustness_v1_dtb3.csv (DTB3)",
    }
    MANIFEST.write_text(json.dumps(man, indent=2)+"\n")
    print("done", OUT)

if __name__ == "__main__":
    main()
