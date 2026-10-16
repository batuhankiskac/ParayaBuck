"""research/plugins_b2/build_extra.py -- the extra series the plugin batch needs.

Two families, both registered in the framework's `research/batch_screen/EXTRA_DATA.json`:

1. **high/close and low/close ratios** of every window's own instrument, for IB06 (true-range
   entry gates / the chandelier exit) and IB10 (the ATR%-based vol estimate).  The framework hands
   a plugin the *underlying's* high/low only on the 21 ETF windows (`panel(high=..., low=...)`);
   the main window, the six OOS windows and every bootstrap chunk carry `high=None`.  The bank's
   data section for IB06/IB10 lists no new downloads (only `stress_robustness_v1_daily.csv` and
   `ewma_band_v1_index.csv`), so the ratios are derived from those two files -- the same files the
   harness builds the panel's `price` from, which makes them session-for-session aligned with it by
   construction.  The registered quantity is a *ratio* (`high/close`), not a level: inside a
   bootstrap chunk the panel's price is a reconstructed index (it starts at 1) while the extra
   series replays the drawn source session's own value, so only a scale-free series can be used
   there -- the same reason `vol_n` works on chunk prices.

2. **monthly BAA and AAA (Moody's) from FRED**, for IB14's pre-1986 US OOS windows, where the daily
   BAA10Y (already registered as the `baa10y` key, 1986+) does not exist.  The idea's note is
   "monthly BAA-AAA (1919+) with a 1-month lag, the credit_vote_v1 OOS convention".

Run from the worktree root with the research venv:

    PYTHONPATH=. ~/.venvs/pb-research/bin/python -m research.plugins_b2.build_extra

It rewrites the eight CSVs, merges this branch's series/key entries into `EXTRA_DATA.json` (every
other entry is left byte-identical), writes `research/plugins_b2/DATA_MANIFEST.json`, then runs the
framework's own `extra.verify()`.  `--offline` skips the Yahoo cross-check (see `cross_check`).
"""

from __future__ import annotations

import argparse
import csv
import json
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from research.batch_screen import extra

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = Path(__file__).resolve().parent / "DATA_MANIFEST.json"
TODAY = "2026-10-16"

#: market -> (the repo file the window's panel is built from, the `symbol` rows inside it, the
#: Yahoo symbol its provenance is checked against, the series-name suffix)
MARKETS = {
    "QQQ": ("data/historical/stress_robustness_v1_daily.csv", "QQQ", "QQQ", "qqq"),
    "IXIC": ("data/historical/ewma_band_v1_index.csv", "^IXIC", "^IXIC", "ixic"),
    "GSPC": ("data/historical/ewma_band_v1_index.csv", "^GSPC", "^GSPC", "gspc"),
    "N225": ("data/historical/ewma_band_v1_index.csv", "^N225", "^N225", "n225"),
    "GDAXI": ("data/historical/ewma_band_v1_index.csv", "^GDAXI", "^GDAXI", "gdaxi"),
    "FTSE": ("data/historical/ewma_band_v1_index.csv", "^FTSE", "^FTSE", "ftse"),
}

#: window key -> market.  `main` is the QQQ window; the six OOS windows trade the market index
#: (`fe_config.MARKETS`); `*` catches anything else (the 21 ETF windows, which carry their own
#: high/low and never reach the extra series, and the smoke's synthetic panel).
WINDOW_MARKET = {"main": "QQQ", "M1": "IXIC", "M2": "GSPC", "M3": "N225", "M4": "GDAXI",
                 "M5": "FTSE", "*": "QQQ"}

HL_FILE = "data/historical/batch_screen_plugins_b2_hl_{slug}.csv"
FRED_SERIES = (("BAA", "baa_m"), ("AAA", "aaa_m"))


def repo_ohlc(rel: str, symbol: str) -> tuple[list, np.ndarray, np.ndarray, np.ndarray]:
    """`(dates, high, low, close)` of one symbol in one repo CSV, ascending, as published."""
    rows = []
    with (ROOT / rel).open() as fh:
        for r in csv.DictReader(fh):
            if r["symbol"] == symbol:
                rows.append((r["date"], float(r["high"]), float(r["low"]), float(r["close"])))
    rows.sort(key=lambda t: t[0])
    dates = [r[0] for r in rows]
    return (dates, np.array([r[1] for r in rows]), np.array([r[2] for r in rows]),
            np.array([r[3] for r in rows]))


def series_url(symbol: str, last: str) -> str:
    """The Yahoo chart URL the series' provenance points at.

    `period2` is fixed to the day after the series' last session (not "now"), so re-running this
    builder cannot change the registry's `url` field for identical data: the only non-deterministic
    part of a rebuild is `cross_check`, which is evidence, not input.
    """
    p2 = int(datetime.fromisoformat(last).replace(tzinfo=UTC).timestamp()) + 86400
    return extra.YAHOO_URL.format(sym=urllib.request.quote(symbol), p1=0, p2=p2)


def write_hl(slug: str, dates, high, low, close, rel_to: str, url: str) -> dict:
    """One market's `date,high_over_close,low_over_close` file + its two registry series."""
    rel = HL_FILE.format(slug=slug)
    cols = {"high_over_close": np.asarray(high) / np.asarray(close),
            "low_over_close": np.asarray(low) / np.asarray(close)}
    info = extra.write_series(rel, dates, cols, root=ROOT)
    out = {}
    for col, key in (("high_over_close", "H"), ("low_over_close", "L")):
        vals = cols[col]
        have = np.isfinite(vals)
        out[f"P2_{key}C_{slug.upper()}"] = {
            "file": rel, "column": col, "price": "ratio", "url": url, "fetched": TODAY,
            "source": f"derived: {col} from data/historical/{rel_to.split('/')[-1]} (Yahoo chart API)",
            "range": [dates[int(np.argmax(have))], dates[len(dates) - 1 - int(np.argmax(have[::-1]))]],
            "rows": int(have.sum()), "bytes": info["bytes"], "sha256": info["sha256"],
        }
    return out, cols


def fred_entries(offline: bool, reg: dict) -> dict:
    """Fetch the two monthly FRED series and declare them (`date,value`, like the other FREDs).

    `--offline` reuses the file and the registry entry a previous run wrote (checked against the
    file's hash), so the build is idempotent without network access.
    """
    out = {}
    for fid, key in FRED_SERIES:
        rel = f"data/historical/batch_screen_v1_{fid.lower()}.csv"
        if offline:
            prev = (reg.get("series") or {}).get(fid)
            if not prev or extra.sha256(ROOT / prev["file"]) != prev["sha256"]:
                raise SystemExit(f"--offline cannot declare {fid}: no verified registry entry")
            out[fid] = (key, prev)
            continue
        dates, cols, url = extra.fetch_fred(fid)
        info = extra.write_series(rel, dates, cols, root=ROOT)
        vals = np.asarray(cols["value"], dtype=np.float64)
        have = np.isfinite(vals)
        out[fid] = (key, {
            "file": rel, "column": "value", "price": "level", "url": url, "fetched": TODAY,
            "source": "FRED CSV",
            "range": [dates[int(np.argmax(have))], dates[len(dates) - 1 - int(np.argmax(have[::-1]))]],
            "rows": int(have.sum()), "bytes": info["bytes"], "sha256": info["sha256"],
        })
    return out


def cross_check(offline: bool) -> dict:
    """Compare the in-repo OHLC with a fresh Yahoo chart fetch, per market (evidence, not input).

    The registered series are the *in-repo* ratios, so this only documents that the repo's OHLC
    is the Yahoo chart data it claims to be on the sessions both have.  Never fails the build.
    """
    if offline:
        return {"skipped": "--offline"}
    out = {}
    for name, (rel, symbol, ys, _slug) in MARKETS.items():
        rec = {"repo_file": rel, "yahoo": ys}
        days, hi, lo, cl = repo_ohlc(rel, symbol)
        flat = (hi == lo) & (lo == cl)
        rec.update({"n_repo": len(days), "repo_flat_share": round(float(flat.mean()), 4)})
        try:
            url = extra.YAHOO_URL.format(sym=urllib.request.quote(ys), p1=0,
                                         p2=int(datetime.now(tz=UTC).timestamp()))
            doc = json.loads(extra._get(url).decode("utf-8"))
            res = (doc.get("chart") or {}).get("result") or [None]
            q = res[0]["indicators"]["quote"][0]
            ydates = [datetime.fromtimestamp(t, tz=UTC).strftime("%Y-%m-%d")
                      for t in res[0]["timestamp"]]
            yh = {d: v for d, v in zip(ydates, q["high"]) if v is not None}
            yl = {d: v for d, v in zip(ydates, q["low"]) if v is not None}
            yc = {d: v for d, v in zip(ydates, q["close"]) if v is not None}
            shared = [d for d in days if d in yc]
            idx = {d: i for i, d in enumerate(days)}
            dh = np.array([abs(hi[idx[d]] - yh[d]) / yc[d] for d in shared if d in yh])
            dl = np.array([abs(lo[idx[d]] - yl[d]) / yc[d] for d in shared if d in yl])
            dc = np.array([abs(cl[idx[d]] - yc[d]) / yc[d] for d in shared])
            rec.update({"n_shared": len(shared),
                        "close_max_relerr": float(dc.max()) if dc.size else None,
                        "high_max_relerr": float(dh.max()) if dh.size else None,
                        "low_max_relerr": float(dl.max()) if dl.size else None})
        except Exception as exc:  # noqa: BLE001 -- evidence only, never a build failure
            rec["error"] = f"{type(exc).__name__}: {exc}"
        out[name] = rec
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m research.plugins_b2.build_extra")
    ap.add_argument("--offline", action="store_true", help="skip the Yahoo cross-check")
    args = ap.parse_args(argv)

    reg_path = extra.REGISTRY_PATH
    reg = json.loads(reg_path.read_text())
    series, keys, manifest = {}, {}, {"study": "plugins_b2", "built": TODAY, "series": {}}

    for slug, (rel, symbol, ys, sl) in MARKETS.items():
        days, hi, lo, cl = repo_ohlc(rel, symbol)
        entries, _cols = write_hl(sl, days, hi, lo, cl, rel, series_url(ys, days[-1]))
        series.update(entries)
        manifest["series"].update({k: dict(v) for k, v in entries.items()})
        print(f"  {slug:6s} {len(days):6d} sessions  {days[0]} -> {days[-1]}  -> "
              f"{HL_FILE.format(slug=sl)}")

    fred = fred_entries(args.offline, reg)
    for fid, (key, entry) in fred.items():
        series[fid] = entry
        keys[key] = fid
        manifest["series"][fid] = dict(entry)
        print(f"  {fid:6s} {entry['rows']:6d} months    {entry['range'][0]} -> {entry['range'][1]}"
              f"  -> {entry['file']}")

    for key, col in (("hl_high", "H"), ("hl_low", "L")):
        keys[key] = {"windows": {w: f"P2_{col}C_{m}" for w, m in WINDOW_MARKET.items()}}

    merged_s = {**reg["series"], **series}
    merged_k = {**reg["keys"], **keys}
    reg["series"] = {k: merged_s[k] for k in sorted(merged_s)}
    reg["keys"] = {k: merged_k[k] for k in sorted(merged_k)}
    reg_path.write_text(json.dumps(reg, indent=1, sort_keys=True) + "\n")
    extra.reset()
    print(f"  registry: {len(series)} series and {len(keys)} keys merged into {reg_path.name}")

    v = extra.verify(names=sorted(series))
    print(f"  verify {'PASS' if v['ok'] else 'FAIL'} ({len(series)} series; bad: {v['bad']})")

    manifest["cross_check"] = cross_check(args.offline)
    MANIFEST.write_text(json.dumps(manifest, indent=1, sort_keys=True) + "\n")
    print(f"  manifest -> {MANIFEST.relative_to(ROOT)}")
    return 0 if v["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
