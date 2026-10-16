"""valuation_v1 data acquisition: Shiller's monthly dataset + the ^NDX calibration window.

Network use is limited to the two public sources named in `PREREGISTRATION.md` §2. Everything is
written into `data/historical/` with the study prefix and hashed into `DATA_MANIFEST.json`:

  valuation_v1_shiller_source.xls    the raw workbook exactly as downloaded (input, not parsed)
  valuation_v1_shiller_monthly.csv   the monthly signal series the study consumes
  valuation_v1_ndx_daily.csv         Yahoo ^NDX 1d bars, 1985-10-01 -> 2000-03-20 (V4 calibration)

Run: python -m research.valuation_v1.download_data
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import urllib.request
from pathlib import Path

from research.valuation_v1 import xls_biff

ROOT = Path(__file__).resolve().parents[2]
HIST = ROOT / "data/historical"

SHILLER_URL = (
    "https://img1.wsimg.com/blobby/go/e5e77e0b-59d1-44d9-ab25-4763ac982e53/downloads/ie_data.xls"
)
NDX_URL = (
    "https://query1.finance.yahoo.com/v8/finance/chart/%5ENDX"
    "?period1=491529600&period2=953769600&interval=1d&events=div%7Csplit"
)
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}

SHILLER_XLS = HIST / "valuation_v1_shiller_source.xls"
SHILLER_CSV = HIST / "valuation_v1_shiller_monthly.csv"
NDX_CSV = HIST / "valuation_v1_ndx_daily.csv"


def fetch(url: str) -> bytes:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=60) as fh:
        return fh.read()


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _num(x) -> float:
    return float(x) if isinstance(x, (int, float)) and not isinstance(x, bool) else math.nan


def month_of(x: float) -> tuple[int, int]:
    """Shiller's `YYYY.MM` date (the tenth of a year is October, not January)."""
    y = int(math.floor(x + 1e-9))
    m = int(round((x - y) * 100))
    if not 1 <= m <= 12:
        raise ValueError(f"bad Shiller date {x!r}")
    return y, m


def build_monthly(xls_path: Path, out_csv: Path) -> dict:
    """Raw columns -> the study's monthly signal series; returns validation rows."""
    _names, sheets = xls_biff.load(xls_path)
    cells = sheets["Data"]
    recs = []
    for r in sorted({r for r, _c in cells}):
        v = {c: cells[(r, c)] for rr, c in cells if rr == r}
        if not isinstance(v.get(0), (int, float)):
            continue
        y, m = month_of(v[0])
        recs.append(
            {
                "month": f"{y:04d}-{m:02d}",
                "P": _num(v.get(1)),
                "D": _num(v.get(2)),
                "E": _num(v.get(3)),
                "CPI": _num(v.get(4)),
                "shiller_cape": _num(v.get(12)),
                "shiller_fwd10": _num(v.get(19)),
            }
        )
    # derived: EY, CAPE (10y trailing mean of real earnings), forward 10y real total return
    real_e = [r["E"] / r["CPI"] if r["E"] == r["E"] and r["CPI"] == r["CPI"] else math.nan for r in recs]
    for i, r in enumerate(recs):
        r["EY"] = r["E"] / r["P"] if r["E"] == r["E"] and r["P"] == r["P"] and r["P"] else math.nan
        win = real_e[i - 119 : i + 1]
        r["CAPE"] = (r["P"] / r["CPI"]) / (sum(win) / len(win)) if len(win) == 120 and all(v == v for v in win) else math.nan
    real_r = [math.nan] + [
        (recs[k]["P"] + recs[k]["D"] / 12) / recs[k - 1]["P"] * (recs[k - 1]["CPI"] / recs[k]["CPI"]) - 1
        if all(recs[k][c] == recs[k][c] and recs[k - 1][c] == recs[k - 1][c] for c in ("P", "D", "CPI"))
        and recs[k - 1]["P"]
        else math.nan
        for k in range(1, len(recs))
    ]
    for i, r in enumerate(recs):
        fwd = real_r[i + 1 : i + 121]
        r["F10"] = (math.prod(1 + v for v in fwd)) ** 0.1 - 1 if len(fwd) == 120 and all(v == v for v in fwd) else math.nan

    cols = ("month", "P", "D", "E", "CPI", "EY", "CAPE", "F10", "shiller_cape", "shiller_fwd10")
    with out_csv.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(cols)
        for r in recs:
            w.writerow([r["month"]] + ["" if r[c] != r[c] else repr(r[c]) for c in cols[1:]])

    def cmp_rows(a: str, b: str) -> dict:
        pair = [(r[a], r[b]) for r in recs if r[a] == r[a] and r[b] == r[b]]
        if not pair:
            return {"n": 0}
        xs, ys = zip(*pair)
        mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
        cov = sum((x - mx) * (y - my) for x, y in pair)
        sx = math.sqrt(sum((x - mx) ** 2 for x in xs))
        sy = math.sqrt(sum((y - my) ** 2 for y in ys))
        return {
            "n": len(pair),
            "max_abs_diff": max(abs(x - y) for x, y in pair),
            "corr": cov / (sx * sy) if sx and sy else None,
        }

    return {
        "months": len(recs),
        "first": recs[0]["month"],
        "last": recs[-1]["month"],
        "last_with_E": max(r["month"] for r in recs if r["E"] == r["E"]),
        "last_with_CAPE": max(r["month"] for r in recs if r["CAPE"] == r["CAPE"]),
        "last_with_F10": max(r["month"] for r in recs if r["F10"] == r["F10"]),
        "cross_check_CAPE_vs_shiller": cmp_rows("CAPE", "shiller_cape"),
        "cross_check_F10_vs_shiller": cmp_rows("F10", "shiller_fwd10"),
    }


def build_ndx(url: str, out_csv: Path) -> dict:
    import datetime as _dt
    import json as _json

    obj = _json.loads(fetch(url).decode())
    res = obj["chart"]["result"][0]
    q = res["indicators"]["quote"][0]
    rows = []
    for k, ts in enumerate(res["timestamp"]):
        if q["close"][k] is None or q["open"][k] is None:
            continue
        rows.append((_dt.datetime.fromtimestamp(ts, _dt.UTC).date().isoformat(), q["open"][k], q["close"][k]))
    rows.sort()
    with out_csv.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(("date", "open", "close"))
        for d, op, cl in rows:
            w.writerow((d, repr(float(op)), repr(float(cl))))
    return {"sessions": len(rows), "first": rows[0][0], "last": rows[-1][0]}


def main() -> None:
    HIST.mkdir(parents=True, exist_ok=True)
    if not SHILLER_XLS.exists():
        SHILLER_XLS.write_bytes(fetch(SHILLER_URL))
    shiller = build_monthly(SHILLER_XLS, SHILLER_CSV)
    ndx = build_ndx(NDX_URL, NDX_CSV)
    manifest = {
        "campaign": "valuation_v1",
        "files": {
            "data/historical/valuation_v1_shiller_source.xls": {
                "source_url": SHILLER_URL,
                "source": "Robert Shiller, ie_data.xls (shillerdata.com), workbook last saved 2024-09-04",
                "sha256": sha256(SHILLER_XLS),
                "bytes": SHILLER_XLS.stat().st_size,
                "used_by": "input of build_monthly; parsed by research/valuation_v1/xls_biff.py",
            },
            "data/historical/valuation_v1_shiller_monthly.csv": {
                "source_url": SHILLER_URL,
                "source": "derived from the workbook's Data sheet columns Date/P/D/E/CPI",
                "date_range": [shiller["first"], shiller["last"]],
                "sha256": sha256(SHILLER_CSV),
                "bytes": SHILLER_CSV.stat().st_size,
                "used_by": "EY_REF, CAPE_Q90, and the monthly multipliers of V1-V4",
            },
            "data/historical/valuation_v1_ndx_daily.csv": {
                "source_url": NDX_URL,
                "source": "Yahoo Finance chart API, ^NDX daily (price index, no dividends)",
                "date_range": [ndx["first"], ndx["last"]],
                "sha256": sha256(NDX_CSV),
                "bytes": NDX_CSV.stat().st_size,
                "used_by": "V4_C calibration window 1985-10-01 -> 2000-03-08",
            },
        },
        "derived": {
            "EY": "E / P (nominal month)",
            "CAPE": "(P/CPI) / mean of the trailing 120 months of E/CPI",
            "F10": "annualized forward 10-year real total return, (P + D/12)/P lagged, CPI-deflated",
        },
        "checks": shiller,
        "ndx": ndx,
    }
    (ROOT / "research/valuation_v1/DATA_MANIFEST.json").write_text(json.dumps(manifest, indent=1) + "\n")
    print(json.dumps({"shiller": shiller, "ndx": ndx}, indent=1))


if __name__ == "__main__":
    main()
