#!/usr/bin/env python3
"""Download research data for gold_riskoff_oos_v1 (network; research only, never used by tests).

Sources (public daily history only, plus one monthly series used for validation only):
  - Yahoo Finance chart API v8, interval=1d: ^IXIC (Nasdaq Composite), ^NDX
    (Nasdaq-100), ^GSPC (S&P 500); price indices, no dividends.
  - Deutsche Bundesbank time-series API (BBEX3):
      D.XAU.DEM.EA.AC.C01  gold price, Frankfurt Stock Exchange fixing, DM per 1 kg fine gold (to 1998-12-30)
      D.USD.DEM.AA.AC.000  exchange rate on the Frankfurt exchange, DM per USD 1 (to 1998-12-30)
    (The Bundesbank's USD London gold fixing series BBEX3.D.XAU.USD.EA.AC.C05/C06 return
    HTTP 404; the FRED LBMA series GOLDAMGBD228NLBM / GOLDPMGBD228NLBM return 404; the
    LBMA price JSON is behind a Cloudflare bot block. See PREREGISTRATION.md.)
  - World Bank Commodity Price Data ("Pink Sheet"), CMO-Historical-Data-Monthly.xlsx,
    sheet "Monthly Prices", column "Gold" (London afternoon fixing, average of daily
    rates, USD/troy oz). VALIDATION ONLY; never an input to any arm.

All series are clipped to END = 1998-12-31 (the window ends 1998-12-30).
Writes:
  data/historical/gold_riskoff_oos_v1_index.csv      symbol,date,open,high,low,close
  data/historical/gold_riskoff_oos_v1_gold.csv       date,frankfurt_dem_per_kg,dem_per_usd
  data/historical/gold_riskoff_oos_v1_pinksheet.csv  month,gold_usd_oz
and research/gold_riskoff_oos_v1/DATA_MANIFEST.json.
The xlsx is parsed with the standard library (zipfile + regex); no new dependency.
"""

from __future__ import annotations

import csv
import datetime as dt
import hashlib
import io
import json
import re
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT_INDEX = ROOT / "data/historical/gold_riskoff_oos_v1_index.csv"
OUT_GOLD = ROOT / "data/historical/gold_riskoff_oos_v1_gold.csv"
OUT_PINK = ROOT / "data/historical/gold_riskoff_oos_v1_pinksheet.csv"
MANIFEST = Path(__file__).parent / "DATA_MANIFEST.json"
SYMBOLS = ("^IXIC", "^NDX", "^GSPC")
END = "1998-12-31"
YAHOO = "https://query1.finance.yahoo.com/v8/finance/chart/{sym}?period1=0&period2={end}&interval=1d"
BBK = "https://api.statistiken.bundesbank.de/rest/download/BBEX3/{key}?format=csv&lang=en"
BBK_GOLD, BBK_FX = "D.XAU.DEM.EA.AC.C01", "D.USD.DEM.AA.AC.000"
PINK = ("https://thedocs.worldbank.org/en/doc/5d903e848db1d1b83e0ec8f744e55570-0350012021/"
        "related/CMO-Historical-Data-Monthly.xlsx")
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


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def sha_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def write_csv(path: Path, header, rows) -> None:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(header)
    w.writerows(rows)
    path.write_text(buf.getvalue())


def yahoo():
    end_ts = int(time.time())
    rows, meta = [], {}
    for sym in SYMBOLS:
        url = YAHOO.format(sym=sym.replace("^", "%5E"), end=end_ts)
        raw = fetch(url)
        res = json.loads(raw)["chart"]["result"][0]
        when = utc_now()
        off = res["meta"]["gmtoffset"]
        q = res["indicators"]["quote"][0]
        by_date, dropped = {}, 0
        for i, ts in enumerate(res["timestamp"]):
            d = dt.datetime.fromtimestamp(ts + off, dt.UTC).strftime("%Y-%m-%d")
            if d > END:
                continue
            o, h, lo, c = (q[k][i] for k in ("open", "high", "low", "close"))
            if o is None or c is None or h is None or lo is None:
                dropped += 1
                continue
            by_date[d] = [sym, d, o, h, lo, c]  # keep the last row per date
        sym_rows = [by_date[d] for d in sorted(by_date)]
        rows.extend(sym_rows)
        meta[sym] = {"url": url, "retrieved_utc": when, "response_sha256": sha_bytes(raw), "rows": len(sym_rows),
                     "rows_dropped_null": dropped, "first": sym_rows[0][1], "last": sym_rows[-1][1]}
    write_csv(OUT_INDEX, ["symbol", "date", "open", "high", "low", "close"], rows)
    return meta


def bbk(key):
    url = BBK.format(key=key)
    raw = fetch(url)
    when = utc_now()
    text = raw.decode("utf-8-sig")
    out, title = {}, None
    for r in csv.reader(io.StringIO(text)):
        if len(r) >= 2 and r[0] == "" and title is None and not r[1].startswith("BBEX3"):
            title = r[1]
        if r and re.fullmatch(r"\d{4}-\d{2}-\d{2}", r[0]) and r[0] <= END and r[1] not in (".", ""):
            out[r[0]] = r[1]
    return url, when, sha_bytes(raw), title, out


def pinksheet():
    raw = fetch(PINK)
    when = utc_now()
    z = zipfile.ZipFile(io.BytesIO(raw))
    wb = z.read("xl/workbook.xml").decode()
    rels = z.read("xl/_rels/workbook.xml.rels").decode()
    rid = dict(re.findall(r'<sheet [^>]*name="([^"]+)"[^>]*r:id="([^"]+)"', wb))["Monthly Prices"]
    target = re.search(r'<Relationship [^>]*Id="%s"[^>]*Target="([^"]+)"' % rid, rels)
    if target is None:
        target = re.search(r'<Relationship [^>]*Target="([^"]+)"[^>]*Id="%s"' % rid, rels)
    sheet = "xl/" + target.group(1).lstrip("/").removeprefix("xl/")
    ss = [''.join(re.findall(r"<t[^>]*>([^<]*)</t>", s))
          for s in re.findall(r"<si>(.*?)</si>", z.read("xl/sharedStrings.xml").decode(), re.S)]
    cells = {}
    for m in re.finditer(r'<c r="([A-Z]+)(\d+)"([^>]*?)(?:/>|>(.*?)</c>)', z.read(sheet).decode(), re.S):
        col, row, attrs, body = m.group(1), int(m.group(2)), m.group(3), m.group(4) or ""
        v = re.search(r"<v>([^<]*)</v>", body)
        if v is None:
            continue
        val = ss[int(v.group(1))] if 't="s"' in attrs else v.group(1)
        cells[(row, col)] = val
    head = [(r, c) for (r, c), v in cells.items() if v.strip() == "Gold"]
    if len(head) != 1:
        raise RuntimeError(f"expected one 'Gold' header cell, found {head}")
    hr, gcol = head[0]
    out = []
    for (r, c), v in cells.items():
        if c == "A" and r > hr and re.fullmatch(r"\d{4}M\d{2}", v):
            g = cells.get((r, gcol))
            month = f"{v[:4]}-{v[5:]}"
            if g not in (None, "", "..") and month <= END[:7]:
                out.append((month, float(g)))
    out.sort()
    return {"url": PINK, "retrieved_utc": when, "response_sha256": sha_bytes(raw), "sheet": "Monthly Prices",
            "column_header": "Gold", "header_cell": f"{gcol}{hr}"}, out


def main() -> None:
    files = {}
    meta = yahoo()
    files[str(OUT_INDEX.relative_to(ROOT))] = {
        "source": "Yahoo Finance chart API v8 (query1.finance.yahoo.com), interval=1d",
        "notes": f"price indices (no dividends); OHLC as served; rows with a null field dropped; clipped to <= {END}. "
                 "^IXIC has open=high=low=close (no intraday data) before 1985.",
        "sha256": sha(OUT_INDEX), "symbols": meta}

    gu, gw, gsha, gtitle, gold = bbk(BBK_GOLD)
    fu, fw, fsha, ftitle, fx = bbk(BBK_FX)
    dates = sorted(gold)
    missing_fx = [d for d in dates if d not in fx]
    if missing_fx:
        raise RuntimeError(f"gold dates without a USD fixing: {missing_fx[:10]}")
    write_csv(OUT_GOLD, ["date", "frankfurt_dem_per_kg", "dem_per_usd"], [(d, gold[d], fx[d]) for d in dates])
    files[str(OUT_GOLD.relative_to(ROOT))] = {
        "source": "Deutsche Bundesbank time-series API (api.statistiken.bundesbank.de), dataflow BBEX3",
        "series": {
            BBK_GOLD: {"url": gu, "retrieved_utc": gw, "response_sha256": gsha, "title": gtitle,
                       "observations_kept": len(gold), "first": dates[0], "last": dates[-1]},
            BBK_FX: {"url": fu, "retrieved_utc": fw, "response_sha256": fsha, "title": ftitle,
                     "observations_kept_on_gold_dates": len(dates)},
        },
        "notes": "rows = Frankfurt dates with a gold fixing; '.' (no value) rows dropped. "
                 "USD per troy oz = frankfurt_dem_per_kg / dem_per_usd / 32.1507466.",
        "sha256": sha(OUT_GOLD), "rows": len(dates), "first": dates[0], "last": dates[-1]}

    pmeta, pink = pinksheet()
    write_csv(OUT_PINK, ["month", "gold_usd_oz"], pink)
    files[str(OUT_PINK.relative_to(ROOT))] = {
        **pmeta, "use": "validation only (monthly London PM fix average); never an arm input",
        "sha256": sha(OUT_PINK), "rows": len(pink), "first": pink[0][0], "last": pink[-1][0]}

    manifest = {
        "campaign": "gold_riskoff_oos_v1", "downloaded_utc": utc_now(), "end_clip": END, "files": files,
        "unavailable_gold_sources": {
            "https://api.statistiken.bundesbank.de/rest/download/BBEX3/D.XAU.USD.EA.AC.C05": "HTTP 404 (also C06, C07, C08)",
            "https://fred.stlouisfed.org/graph/fredgraph.csv?id=GOLDPMGBD228NLBM": "HTTP 404 (also GOLDAMGBD228NLBM)",
            "https://prices.lbma.org.uk/json/gold_pm.json": "HTTP 403 Cloudflare bot block (not circumvented)",
            "Yahoo GC=F": "starts 2000-08-30",
        },
        "dividends": "no free daily dividend series covering 1975-1998 found (Yahoo ^SP500TR starts 1988-01-04, "
                     "^XCMP 2003-09-25); arms run on price indices",
        "reused_committed_inputs": {
            "data/historical/stress_robustness_v1_dtb3.csv": "see research/stress_robustness_v1/DATA_MANIFEST.json",
        },
    }
    MANIFEST.write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
