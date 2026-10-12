#!/usr/bin/env python3
"""Download the vix_filter_v1 inputs (run once; network: Yahoo chart API and FRED fredgraph only).

Writes data/historical/vix_filter_v1_{vix,vxn,vix3m,gspc}.csv (Yahoo daily OHLC, dates in
America/New_York), data/historical/vix_filter_v1_fred.csv (VIXCLS, VXNCLS; cross-check only)
and research/vix_filter_v1/DATA_MANIFEST.json (URLs, retrieval time, SHA-256, coverage).
screen.py never touches the network; it only reads the committed CSVs.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
DATA = ROOT / "data/historical"
UA = {"User-Agent": "Mozilla/5.0"}
NY = ZoneInfo("America/New_York")
END = "2026-09-29"  # last bar of stress_robustness_v1_daily.csv

YAHOO = {
    # name: (symbol, period1 (epoch), last date kept)
    "vix": ("^VIX", 631152000, END),          # 1990-01-01 ->
    "vxn": ("^VXN", 946684800, END),          # 2000-01-01 ->
    "vix3m": ("^VIX3M", 1104537600, END),     # 2005-01-01 ->
    "gspc": ("^GSPC", 567993600, "1999-12-31"),  # 1988-01-01 -> 1999-12-31 (G8 only)
}
FRED = ("VIXCLS", "VXNCLS")


def get(url: str) -> bytes:
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60) as r:
        return r.read()


def yahoo(name: str, sym: str, p1: int, last: str) -> dict:
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.request.quote(sym)}"
           f"?period1={p1}&period2=1800000000&interval=1d&events=history")
    raw = get(url)
    res = json.loads(raw)["chart"]["result"][0]
    q = res["indicators"]["quote"][0]
    rows, nulls = [], 0
    for i, ts in enumerate(res["timestamp"]):
        d = datetime.fromtimestamp(ts, NY).date().isoformat()
        if d > last:
            continue
        c = q["close"][i]
        if c is None:
            nulls += 1
            continue
        rows.append([d] + [repr(float(q[k][i])) if q[k][i] is not None else "" for k in ("open", "high", "low", "close")])
    rows.sort()
    path = DATA / f"vix_filter_v1_{name}.csv"
    with path.open("w", newline="") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(["date", "open", "high", "low", "close"])
        w.writerows(rows)
    return {
        "source": "Yahoo Finance chart API v8 (query1.finance.yahoo.com), interval=1d, events=history",
        "url": url, "symbol": sym, "rows": len(rows), "first": rows[0][0], "last": rows[-1][0],
        "null_closes_dropped": nulls,
        "notes": "Timestamps converted to America/New_York dates; rows after the last kept date dropped; OHLC as returned (float repr).",
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "line_endings": "LF",
        "raw_response_sha256": hashlib.sha256(raw).hexdigest(),
        "_path": str(path.relative_to(ROOT)),
    }


def fred() -> dict:
    series = {}
    raws = {}
    for sid in FRED:
        url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={sid}"
        raw = get(url)
        raws[sid] = (url, hashlib.sha256(raw).hexdigest())
        for r in csv.DictReader(io.StringIO(raw.decode())):
            d = r["observation_date"]
            if d <= END:
                series.setdefault(d, {})[sid] = r[sid]
    path = DATA / "vix_filter_v1_fred.csv"
    with path.open("w", newline="") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(["observation_date", *FRED])
        for d in sorted(series):
            w.writerow([d] + [series[d].get(s, "") for s in FRED])
    return {
        "source": "FRED fredgraph CSV (data cross-check and the report-only X4 VXN-source sensitivity; not used by any gated arm)",
        "url": {s: raws[s][0] for s in FRED}, "raw_response_sha256": {s: raws[s][1] for s in FRED},
        "rows": len(series), "first": min(series), "last": max(series),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "line_endings": "LF", "_path": str(path.relative_to(ROOT)),
    }


def main() -> None:
    files = {}
    for name, (sym, p1, last) in YAHOO.items():
        info = yahoo(name, sym, p1, last)
        files[info.pop("_path")] = info
    info = fred()
    files[info.pop("_path")] = info
    man = {"retrieved_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"), "files": files}
    (HERE / "DATA_MANIFEST.json").write_text(json.dumps(man, indent=2, sort_keys=True) + "\n")
    for p, i in files.items():
        print(p, i["rows"], i["first"], i["last"])


if __name__ == "__main__":
    main()
