"""Convert the downloaded public inputs into the study's CSV inputs.

Inputs (raw, kept verbatim under data/historical/raw_put_hedge_v1/):
  * Yahoo chart API daily bars for ^VXN, ^VIX, ^SPX, ^NDX (public market data).
  * CBOE PPUT daily index history (public index history, cdn.cboe.com).

Outputs: data/historical/put_hedge_v1_{vxn,vix,spx,ndx,pput}.csv with an ISO date and
(where available) open/high/low/close.  The raw files are not inputs to the engine; the
converted CSVs are, and DATA_MANIFEST.json records their SHA-256.
"""

from __future__ import annotations

import csv
import datetime as dt
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RAW = ROOT / "data/historical/raw_put_hedge_v1"
OUT = ROOT / "data/historical"
SOURCE = {
    "vxn": "https://query1.finance.yahoo.com/v8/finance/chart/%5EVXN (daily bars)",
    "vix": "https://query1.finance.yahoo.com/v8/finance/chart/%5EVIX (daily bars)",
    "spx": "https://query1.finance.yahoo.com/v8/finance/chart/%5ESPX (daily bars)",
    "ndx": "https://query1.finance.yahoo.com/v8/finance/chart/%5ENDX (daily bars)",
    "sp500tr": "https://query1.finance.yahoo.com/v8/finance/chart/%5ESP500TR (daily bars)",
    "pput": "https://cdn.cboe.com/api/global/us_indices/daily_prices/PPUT_History.csv",
}


def yahoo_to_csv(sym: str) -> tuple[Path, int, tuple[str, str]]:
    raw = json.loads((RAW / f"yahoo_{sym.upper()}.json").read_text())
    res = raw["chart"]["result"][0]
    ts = res["timestamp"]
    q = res["indicators"]["quote"][0]
    rows = []
    for k, t in enumerate(ts):
        c = q["close"][k]
        if c is None:
            continue
        d = dt.datetime.fromtimestamp(t, dt.UTC).date().isoformat()
        o = q["open"][k] if q["open"][k] is not None else c
        h = q["high"][k] if q["high"][k] is not None else c
        lo = q["low"][k] if q["low"][k] is not None else c
        rows.append((d, o, h, lo, c))
    rows.sort()
    dedup = {}
    for r in rows:
        dedup[r[0]] = r
    rows = [dedup[k] for k in sorted(dedup)]
    path = OUT / f"put_hedge_v1_{sym}.csv"
    with path.open("w", newline="") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(["date", "open", "high", "low", "close"])
        w.writerows(rows)
    return path, len(rows), (rows[0][0], rows[-1][0])


def pput_to_csv() -> tuple[Path, int, tuple[str, str]]:
    rows = []
    with (RAW / "cboe_PPUT.csv").open() as fh:
        for r in csv.DictReader(fh):
            m, d, y = r["DATE"].split("/")
            rows.append((f"{y}-{m}-{d}", float(r["PPUT"])))
    rows.sort()
    path = OUT / "put_hedge_v1_pput.csv"
    with path.open("w", newline="") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(["date", "close"])
        w.writerows(rows)
    return path, len(rows), (rows[0][0], rows[-1][0])


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> None:
    files = {}
    for sym in ("vxn", "vix", "spx", "ndx", "sp500tr"):
        path, n, (a, b) = yahoo_to_csv(sym)
        files[str(path.relative_to(ROOT))] = {
            "bytes": path.stat().st_size,
            "rows": n,
            "first": a,
            "last": b,
            "sha256": sha256(path),
            "source": SOURCE[sym],
        }
        print(f"{path.name}: {n} rows {a} -> {b}  {sha256(path)[:16]}")
    path, n, (a, b) = pput_to_csv()
    files[str(path.relative_to(ROOT))] = {
        "bytes": path.stat().st_size,
        "rows": n,
        "first": a,
        "last": b,
        "sha256": sha256(path),
        "source": SOURCE["pput"],
    }
    print(f"{path.name}: {n} rows {a} -> {b}  {sha256(path)[:16]}")
    print(json.dumps(files, indent=1))


if __name__ == "__main__":
    main()
