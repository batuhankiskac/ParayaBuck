"""ic37c_confirm_v1.data -- the study's inputs: FRED/ALFRED/Yahoo fetch, the IEF proxy, the
point-in-time real-yield proxy, and the data manifest.

Two *derived* series are built here and registered by `proxy.py` into the framework's extra-data
registry (never by editing `research/batch_screen/`) so that the committed IC37 plugin, imported
unchanged, reads them through its own keys:

    ief_proxy   a synthetic IEF total-return index from FRED constant-maturity yields
                (formula in `build_ief_proxy`, validated against real IEF in `screen.py --e1`)
    be_proxy    the 10-year breakeven the real-yield gate uses: T10YIE where it exists, else
                `100 x trailing 12-month CPI change as published at that date` (ALFRED vintages)

Raw inputs are saved as `data/historical/ic37c_confirm_v1_*.csv` and their URL, date range,
row count and SHA-256 recorded in `DATA_MANIFEST.json`. Network is used only by `--fetch`.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
import urllib.request
from datetime import date, timedelta
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
HIST = ROOT / "data/historical"
MANIFEST = Path(__file__).resolve().parent / "DATA_MANIFEST.json"

USER_AGENT = "parayabuck-research/1.0 (ic37c_confirm_v1)"
FRED_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={sid}"
ALFRED_URL = ("https://alfred.stlouisfed.org/graph/alfredgraph.csv?id={sid}&vintage_date={v}")
ALFRED_LIST_URL = "https://alfred.stlouisfed.org/series/downloaddata?seid={sid}"
YAHOO_URL = ("https://query1.finance.yahoo.com/v8/finance/chart/{sym}"
             "?period1=0&period2={p2}&interval=1d&events=div%2Csplit")

#: the FRED series the study reads. DGS7/DGS10 build the IEF proxy; T10YIE is the post-2003
#: breakeven; CPIAUCSL is the point-in-time CPI behind the pre-2003 breakeven; DFII10 is
#: report-only (the real 10-year TIPS yield, 2003-01-02 ->).
FRED = ("DGS7", "DGS10", "T10YIE", "CPIAUCSL", "DFII10")

#: the IEF proxy's parameters (PREREGISTRATION section 2). IEF's average modified duration is
#: ~7.5 years; the 7.5-year point of the (DGS7, DGS10) curve is linearly interpolated.
PROXY_DURATION = 7.5
PROXY_EXPENSE = 0.0015          # IEF's expense ratio, per year
W7, W10 = 5.0 / 6.0, 1.0 / 6.0  # the 7.5-year point of the (DGS7, DGS10) line
SESSION_DAYS = 252.0

#: the CPI vintages are only needed while the gate is on the CPI proxy (before T10YIE exists).
CPI_VINTAGE_LAST = "2002-12-31"
PROXY_START = "1969-07-01"      # DGS7's first session; the proxy cannot start earlier


# ----------------------------------------------------------------------------- fetch helpers
def _get(url: str, timeout: float = 90.0) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as fh:
        return fh.read()


def _num(x: str) -> float:
    x = (x or "").strip()
    return np.nan if x in ("", ".", "NA", "null") else float(x)


def fetch_fred(sid: str, get=_get):
    """One FRED CSV: `(dates, values, url)`, missing observations dropped."""
    url = FRED_URL.format(sid=sid)
    rd = csv.reader(_get(url).decode("utf-8").splitlines())
    next(rd)
    dates, vals = [], []
    for row in rd:
        if len(row) < 2:
            continue
        v = _num(row[1])
        if np.isfinite(v):
            dates.append(row[0])
            vals.append(v)
    return dates, vals, url


def alfred_vintages(sid: str = "CPIAUCSL", get=_get):
    """The series' vintage dates from ALFRED's own download form (ascending ISO strings)."""
    html = _get(ALFRED_LIST_URL.format(sid=sid)).decode("utf-8", "replace")
    out = []
    for piece in html.split('<option value="')[1:]:
        v = piece.split('"', 1)[0]
        if len(v) == 10 and v[4] == "-" and v[7] == "-":
            out.append(v)
    return sorted(set(out))


def fetch_alfred_vintage(sid: str, vintage: str, get=_get):
    """One ALFRED vintage: `(dates, values)` of the series as it stood on `vintage`."""
    url = ALFRED_URL.format(sid=sid, v=vintage)
    rd = csv.reader(_get(url).decode("utf-8").splitlines())
    next(rd)
    dates, vals = [], []
    for row in rd:
        if len(row) < 2:
            continue
        v = _num(row[1])
        if np.isfinite(v):
            dates.append(row[0])
            vals.append(v)
    return dates, vals


def fetch_yahoo(symbol: str, get=_get):
    """One Yahoo chart series: `(dates, close, adjclose, url)`."""
    p2 = (date.today() - date(1970, 1, 1)).days * 86400
    url = YAHOO_URL.format(sym=urllib.request.quote(symbol), p2=p2)
    doc = json.loads(_get(url).decode("utf-8"))
    res = (doc["chart"]["result"] or [None])[0]
    if not res:
        raise RuntimeError(f"yahoo {symbol}: empty result")
    ts = res["timestamp"]
    q = res["indicators"]["quote"][0]
    adj = ((res["indicators"].get("adjclose") or [{}])[0].get("adjclose") if
           res["indicators"].get("adjclose") else None)
    dates, close, adjc = [], [], []
    for i, t in enumerate(ts):
        c = q["close"][i]
        if c is None:
            continue
        dates.append(date.fromordinal(date(1970, 1, 1).toordinal() + int(t) // 86400).isoformat())
        close.append(float(c))
        adjc.append(float(adj[i]) if (adj is not None and adj[i] is not None) else float(c))
    return dates, close, adjc, url


# ----------------------------------------------------------------------------- io
def write_csv(rel: str, header: list, rows) -> dict:
    """Write one `data/historical` CSV deterministically and report file, bytes, sha256."""
    p = ROOT / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    lines = [",".join(header)]
    for r in rows:
        lines.append(",".join("" if v is None or (isinstance(v, float) and not np.isfinite(v))
                              else (f"{v:.10g}" if isinstance(v, float) else str(v)) for v in r))
    p.write_text("\n".join(lines) + "\n")
    return {"file": rel, "bytes": p.stat().st_size, "sha256": sha256(p)}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def read_two_col(rel: str):
    """A `date,value` CSV as `(dates: (T,) <U10, values: (T,) float64)`."""
    d, v = [], []
    with (ROOT / rel).open() as fh:
        rd = csv.reader(fh)
        next(rd)
        for row in rd:
            if not row:
                continue
            d.append(row[0])
            v.append(_num(row[1]))
    return np.array(d, dtype="<U10"), np.array(v, dtype=np.float64)


def read_wide(rel: str):
    """A `date,<col>...` CSV as `(dates, {col: values})`."""
    with (ROOT / rel).open() as fh:
        rd = csv.reader(fh)
        head = next(rd)
        cols = {c: [] for c in head[1:]}
        dates = []
        for row in rd:
            if not row:
                continue
            dates.append(row[0])
            for c, x in zip(head[1:], row[1:]):
                cols[c].append(_num(x))
    return np.array(dates, dtype="<U10"), {c: np.array(v, dtype=np.float64) for c, v in cols.items()}


def read_vintages(rel="data/historical/ic37c_confirm_v1_cpi_vintages.csv"):
    """The CPI vintage table as `(vintage_dates, obs_dates, values)`, ascending by vintage date."""
    vd, od, vv = [], [], []
    with (ROOT / rel).open() as fh:
        rd = csv.reader(fh)
        next(rd)
        for row in rd:
            if not row:
                continue
            vd.append(row[0])
            od.append(row[1])
            vv.append(float(row[2]))
    return (np.array(vd, dtype="<U10"), np.array(od, dtype="<U10"), np.array(vv, dtype=np.float64))


def _dense(days, values, start=None, end=None):
    """Rebase a sparse `(dates, values)` series onto every calendar day in `[start, end]`,
    carrying the last observation forward (a value is a step function of the publication date)."""
    lo = start or str(days[0])
    hi = end or str(days[-1])
    n = (date.fromisoformat(hi) - date.fromisoformat(lo)).days + 1
    cal = np.array([(date.fromisoformat(lo) + timedelta(days=k)).isoformat() for k in range(n)])
    pos = np.searchsorted(days, cal, side="right") - 1
    out = np.where(pos >= 0, values[np.clip(pos, 0, None)], np.nan)
    return cal, out


# ----------------------------------------------------------------------------- derived series
def build_ief_proxy(d7=None, d10=None, end=None):
    """The synthetic IEF total-return index (PREREGISTRATION section 2).

        y75(t)  = (5/6) DGS7(t) + (1/6) DGS10(t)          # the 7.5-year point of (DGS7, DGS10)
        r(t)    = -7.5 * (y75(t) - y75(t-1)) / 100        # modified-duration price return
                  + y75(t) / 100 / 252                    # coupon accrual (carry)
                  - 0.0015 / 252                          # IEF's expense ratio
        TR(t)   = 100 * prod(1 + r)

    Built on the sessions DGS7 and DGS10 share, then published on every calendar day (the value
    is carried forward): a foreign-market session whose US bond price did not print resolves to a
    price instead of a hole, which is what the framework's leg-B availability rule needs.
    """
    d7 = d7 or read_two_col("data/historical/ic37c_confirm_v1_dgs7.csv")
    d10 = d10 or read_two_col("data/historical/ic37c_confirm_v1_dgs10.csv")
    common = np.intersect1d(d7[0], d10[0])
    y7 = d7[1][np.searchsorted(d7[0], common)]
    y10 = d10[1][np.searchsorted(d10[0], common)]
    y75 = W7 * y7 + W10 * y10
    r = (-PROXY_DURATION * np.diff(y75) / 100.0 + y75[1:] / 100.0 / SESSION_DAYS
         - PROXY_EXPENSE / SESSION_DAYS)
    level = 100.0 * np.concatenate([[1.0], np.cumprod(1.0 + r)])
    cal, dense = _dense(common, level, start=str(common[0]), end=end or str(common[-1]))
    return cal, dense, common, level


def build_be_proxy(cpi_vintages=None, t10yie=None, end=None):
    """The 10-year breakeven the gate reads (PREREGISTRATION section 3).

        BE(t) = T10YIE(t)                                  where T10YIE is published (2003-01-02 ->)
              = 100 * (CPI_pit(t) / CPI_pit(t - 1 year) - 1)   before that

    `CPI_pit(x)` is the newest CPIAUCSL value *published* on or before `x` (ALFRED vintage), so the
    pre-2003 segment is a strict point-in-time construction: no revision published later can enter.
    Published on every calendar day like the IEF proxy.
    """
    if cpi_vintages is None:
        vd, _od, obs = read_vintages()
    else:
        vd, obs = cpi_vintages
    t10yie = t10yie or read_two_col("data/historical/ic37c_confirm_v1_t10yie.csv")
    cal, cpi = _dense(vd, obs, start=PROXY_START, end=end or "2026-10-15")
    # the year-ago value: `_dense` is a step function of the publication date, so the calendar
    # date one year back carries exactly the vintage that was current then
    back = np.array([(date.fromisoformat(d) - timedelta(days=365)).isoformat() for d in cal])
    pos = np.searchsorted(cal, back, side="right") - 1
    yoy = np.where(pos >= 0, cpi / np.where(pos >= 0, cpi[np.clip(pos, 0, None)], np.nan) - 1.0,
                   np.nan)
    _tc, tvals = _dense(t10yie[0], t10yie[1], start=PROXY_START, end=str(cal[-1]))
    be = np.where(np.isfinite(tvals), tvals, 100.0 * yoy)
    return cal, be


# ----------------------------------------------------------------------------- the whole build
RAW = {
    # name -> (file, source kind, url)
    "DGS7": ("data/historical/ic37c_confirm_v1_dgs7.csv", "FRED CSV", FRED_URL.format(sid="DGS7")),
    "DGS10": ("data/historical/ic37c_confirm_v1_dgs10.csv", "FRED CSV",
              FRED_URL.format(sid="DGS10")),
    "T10YIE": ("data/historical/ic37c_confirm_v1_t10yie.csv", "FRED CSV",
               FRED_URL.format(sid="T10YIE")),
    "CPIAUCSL": ("data/historical/ic37c_confirm_v1_cpiaucsl.csv", "FRED CSV",
                 FRED_URL.format(sid="CPIAUCSL")),
    "DFII10": ("data/historical/ic37c_confirm_v1_dfii10.csv", "FRED CSV",
               FRED_URL.format(sid="DFII10")),
    "CPIAUCSL_VINTAGES": ("data/historical/ic37c_confirm_v1_cpi_vintages.csv",
                          "ALFRED vintages", ALFRED_LIST_URL.format(sid="CPIAUCSL")),
    "IEF": ("data/historical/ic37c_confirm_v1_ief.csv", "Yahoo chart API",
            YAHOO_URL.format(sym="IEF", p2="<epoch>")),
}


def build(fetch=False, verbose=True):
    """Fetch (optionally) and write every input, then build the derived series. Returns the manifest."""
    ent = {}
    if fetch:
        for sid in FRED:
            d, v, url = fetch_fred(sid)
            rel = f"data/historical/ic37c_confirm_v1_{sid.lower()}.csv"
            write_csv(rel, ["date", "value"], zip(d, v))
            if verbose:
                print(f"  {sid:9s} {rel}  {d[0]} -> {d[-1]}  {len(d)} rows", flush=True)
        # the CPI vintages the pre-2003 breakeven needs (one row per ALFRED vintage). The
        # archive starts 1972-07-21, so every session the study reads is covered point-in-time.
        vints = [v for v in alfred_vintages("CPIAUCSL") if v <= CPI_VINTAGE_LAST]
        rows = []
        for v in vints:
            dd, vv = fetch_alfred_vintage("CPIAUCSL", v)
            rows.append((v, dd[-1], vv[-1]))
        write_csv("data/historical/ic37c_confirm_v1_cpi_vintages.csv",
                  ["vintage_date", "obs_date", "value"], rows)
        if verbose:
            print(f"  CPIAUCSL vintages {vints[0]} -> {vints[-1]}  {len(vints)} rows", flush=True)
        d, c, a, _url = fetch_yahoo("IEF")
        write_csv("data/historical/ic37c_confirm_v1_ief.csv", ["date", "close", "adjclose"],
                  zip(d, c, a))
        if verbose:
            print(f"  IEF       {d[0]} -> {d[-1]}  {len(d)} rows", flush=True)

    for name, (rel, source, url) in RAW.items():
        p = ROOT / rel
        if not p.exists():
            raise FileNotFoundError(f"{p} is missing -- run `python -m "
                                    f"research.ic37c_confirm_v1.data --fetch` first")
        with p.open() as fh:
            rd = csv.reader(fh)
            head = next(rd)
            rows = [r for r in rd if r]
        ent[name] = {"file": rel, "url": url, "source": source, "columns": head[1:],
                     "range": [rows[0][0], rows[-1][0]], "rows": len(rows),
                     "bytes": p.stat().st_size, "sha256": sha256(p)}

    # derived: the two series the registry exposes
    cal, dense, common, _level = build_ief_proxy()
    info = write_csv("data/historical/ic37c_confirm_v1_ief_proxy.csv", ["date", "value"],
                     zip(cal.tolist(), dense.tolist()))
    ent["IC37C_IEF_PROXY"] = {"file": info["file"], "derived_from": ["DGS7", "DGS10"],
                              "range": [cal[0], cal[-1]], "rows": int(cal.size),
                              "source": "derived (ic37c_confirm_v1.data.build_ief_proxy)", **info}
    cal, be = build_be_proxy()
    info = write_csv("data/historical/ic37c_confirm_v1_be_proxy.csv", ["date", "value"],
                     zip(cal.tolist(), be.tolist()))
    ent["IC37C_BE_PROXY"] = {"file": info["file"], "derived_from": ["T10YIE", "CPIAUCSL vintages"],
                             "range": [cal[0], cal[-1]], "rows": int(cal.size),
                             "source": "derived (ic37c_confirm_v1.data.build_be_proxy)", **info}
    man = {
        "study": "ic37c_confirm_v1",
        "note": ("Raw inputs from FRED CSV / ALFRED vintages / the Yahoo chart API only; the two "
                 "derived series are deterministic functions of them (PREREGISTRATION sections "
                 "2-3). Nothing in data/historical owned by another study is touched."),
        "proxy": {"duration_years": PROXY_DURATION, "expense_ratio": PROXY_EXPENSE,
                  "interpolation": "y75 = 5/6 DGS7 + 1/6 DGS10", "start": PROXY_START},
        "inputs": {k: ent[k] for k in sorted(ent)},
        "fetched": date.today().isoformat(),
    }
    MANIFEST.write_text(json.dumps(man, indent=1, sort_keys=True) + "\n")
    if verbose:
        print(f"  wrote {MANIFEST.relative_to(ROOT)}", flush=True)
    return man


def verify() -> dict:
    """Re-hash every declared input against the manifest (offline)."""
    man = json.loads(MANIFEST.read_text())
    bad = []
    for name, e in man["inputs"].items():
        p = ROOT / e["file"]
        if not p.exists():
            bad.append((name, "missing"))
            continue
        if sha256(p) != e["sha256"]:
            bad.append((name, "sha256"))
    return {"ok": not bad, "bad": bad, "n": len(man["inputs"])}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m research.ic37c_confirm_v1.data")
    ap.add_argument("--fetch", action="store_true", help="download FRED/ALFRED/Yahoo inputs")
    ap.add_argument("--build", action="store_true",
                    help="rebuild the derived series + DATA_MANIFEST.json from local inputs")
    ap.add_argument("--verify", action="store_true", help="re-hash the manifest's files")
    args = ap.parse_args(argv)
    if args.fetch:
        build(fetch=True)
        return 0
    if args.build:
        build(fetch=False)
        return 0
    if args.verify:
        v = verify()
        print(f"verify {'PASS' if v['ok'] else 'FAIL'} ({v['n']} files, bad: {v['bad']})")
        return 0 if v["ok"] else 1
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
