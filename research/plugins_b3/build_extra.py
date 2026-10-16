"""plugins_b3 -- build and register the extra series the IB03/IB23 plugins need.

    PYTHONPATH=. ~/.venvs/pb-research/bin/python -m research.plugins_b3.build_extra

Two kinds of input, both registered through the framework's own writers and hasher
(`research.batch_screen.extra.write_series` / `.sha256`) into the framework's registry
`research/batch_screen/EXTRA_DATA.json` (never into framework *code*):

1. **underlying high/low, relative to the session close** — `high_over_close = high/close`,
   `low_over_close = low/close` — for the main window's QQQ (from the committed
   `data/historical/stress_robustness_v1_daily.csv`, the very file `fe_data.load` builds the main
   panel's price from) and for the five OOS markets' indices (from the committed
   `data/historical/ewma_band_v1_index.csv`, the file `fe_data.load_market` builds each OOS panel
   from).

   Why *relative*: on a real window the panel's price is exactly that file's close, so
   `price * high_over_close` is the literal high (equal to within one ulp). Inside a bootstrap
   chunk the panel's close is a *reconstructed* path (the drawn sessions' returns chained from
   1.0, see `batch_screen/boot.py:boot_panels`) whose level is unrelated to the source session's
   price, so the source's absolute high/low would mix two scales and produce a nonsense true
   range. The session's high/close shape is scale-free, i.e. the chunk "replays the extra series
   together with its returns", exactly as `EXTRA_DATA.json`'s note prescribes.

2. **GC=F** (COMEX gold front-month continuation, 2000-08-30 →) from the Yahoo chart API, for
   IB23's copper/gold ratio; the copper leg is the framework's already-registered `copper` key
   (HG=F, 2000-08-30 →).

Nothing here simulates, computes a strategy return, or reads a result file.
"""

from __future__ import annotations

import csv
import json
from datetime import date
from pathlib import Path

import numpy as np

from research.batch_screen import extra

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent

BARS = "data/historical/stress_robustness_v1_daily.csv"
INDEX = "data/historical/ewma_band_v1_index.csv"
REGISTRY = ROOT / "research/batch_screen/EXTRA_DATA.json"
LOCAL_MANIFEST = HERE / "DATA_MANIFEST.json"

COL_H, COL_L = "high_over_close", "low_over_close"

#: series -> (repo-relative source file, that file's symbol, the window its key resolves for)
HL_SOURCES = {
    "QQQ-HL": (BARS, "QQQ", "main"),
    "^IXIC-HL": (INDEX, "^IXIC", "M1"),
    "^GSPC-HL": (INDEX, "^GSPC", "M2"),
    "^N225-HL": (INDEX, "^N225", "M3"),
    "^GDAXI-HL": (INDEX, "^GDAXI", "M4"),
    "^FTSE-HL": (INDEX, "^FTSE", "M5"),
}
UPSTREAM = {
    "QQQ": "https://query1.finance.yahoo.com/v8/finance/chart/QQQ",
    "^IXIC": "https://query1.finance.yahoo.com/v8/finance/chart/%5EIXIC",
    "^GSPC": "https://query1.finance.yahoo.com/v8/finance/chart/%5EGSPC",
    "^N225": "https://query1.finance.yahoo.com/v8/finance/chart/%5EN225",
    "^GDAXI": "https://query1.finance.yahoo.com/v8/finance/chart/%5EGDAXI",
    "^FTSE": "https://query1.finance.yahoo.com/v8/finance/chart/%5EFTSE",
}


def _f(x) -> float:
    x = (x or "").strip()
    return np.nan if x in ("", ".", "NA") else float(x)


def _read_rows(path: Path, symbol: str) -> list:
    """`(date, high, low, close)` of one symbol's rows, ascending by date."""
    rows = {}
    with path.open() as fh:
        for r in csv.DictReader(fh):
            if r["symbol"] == symbol:
                rows[r["date"]] = (r["date"], _f(r.get("high")), _f(r.get("low")),
                                   _f(r.get("close")))
    return [rows[d] for d in sorted(rows)]


def _slug(name: str) -> str:
    return "".join(ch.lower() if ch.isalnum() else "_" for ch in name).strip("_")


def _range_rows(dates, vals) -> tuple:
    have = np.isfinite(vals)
    if not have.any():
        return [None, None], 0
    first = int(np.argmax(have))
    last = len(dates) - 1 - int(np.argmax(have[::-1]))
    return [str(dates[first]), str(dates[last])], int(have.sum())


def build_hl(root=ROOT, quiet=False) -> dict:
    """Write every relative high/low file; `{series_name: (file, columns)}`."""
    out = {}
    src_sha = {}
    for name, (src, symbol, _win) in HL_SOURCES.items():
        rows = _read_rows(Path(root) / src, symbol)
        dates = [r[0] for r in rows]
        hi, lo = [], []
        for _d, h, l, c in rows:
            ok = np.isfinite(h) and np.isfinite(l) and np.isfinite(c) and c > 0
            hi.append(h / c if ok else np.nan)
            lo.append(l / c if ok else np.nan)
        rel = f"data/historical/plugins_b3_{_slug(name)}.csv"
        info = extra.write_series(rel, dates, {COL_H: hi, COL_L: lo}, root=root)
        src_sha[src] = extra.sha256(Path(root) / src)
        out[name] = {
            "file": rel,
            "low_file": rel,
            "columns": (COL_H, COL_L),
            "range_high": _range_rows(dates, np.asarray(hi)),
            "range_low": _range_rows(dates, np.asarray(lo)),
            "rows": int((np.isfinite(hi) & np.isfinite(lo)).sum()),
            "bytes": info["bytes"],
            "sha256": info["sha256"],
            "source": f"derived from {src} ({symbol} OHLC)",
            "url": UPSTREAM[symbol],
            "derived_from": {"file": src, "sha256": src_sha[src]},
        }
        if not quiet:
            print(f"  {name:10s} {rel} {out[name]['range_high'][0]} -> "
                  f"{out[name]['range_high'][1]} ({out[name]['rows']} rows)", flush=True)
    return out


def build_gold(root=ROOT, quiet=False) -> dict:
    """Fetch GC=F from the Yahoo chart API and write it as the `gold` key's series."""
    dates, cols, url = extra.fetch_yahoo("GC=F")
    rows = [(d, c) for d, c in zip(dates, cols["close"]) if np.isfinite(c)]
    rel = "data/historical/plugins_b3_gc_f.csv"
    info = extra.write_series(rel, [r[0] for r in rows], {"close": [r[1] for r in rows]}, root=root)
    if not quiet:
        print(f"  {'GC=F':10s} {rel} {rows[0][0]} -> {rows[-1][0]} ({len(rows)} rows)", flush=True)
    return {"file": rel, "range": [rows[0][0], rows[-1][0]], "rows": len(rows),
            "bytes": info["bytes"], "sha256": info["sha256"], "url": url,
            "source": "Yahoo chart API"}


def entries(hl: dict, gold: dict) -> dict:
    """The `EXTRA_DATA.json` series entries this build declares."""
    today = date.today().isoformat()
    out = {}
    for name, m in hl.items():
        for col, (rng, n), tag in ((COL_H, m["range_high"], ""), (COL_L, m["range_low"], "-LOW")):
            out[name + tag] = {
                "file": m["file"], "column": col, "price": "ratio", "source": m["source"],
                "url": m["url"], "fetched": today, "range": rng, "rows": n,
                "bytes": m["bytes"], "sha256": m["sha256"],
                "derived_from": dict(m["derived_from"], column="high,low,close"),
            }
    out["GC=F"] = {"file": gold["file"], "column": "close", "price": "close",
                   "source": gold["source"], "url": gold["url"], "fetched": today,
                   "range": gold["range"], "rows": gold["rows"], "bytes": gold["bytes"],
                   "sha256": gold["sha256"]}
    return out


def register(hl: dict, gold: dict, root=ROOT) -> dict:
    """Add every series and key to the framework registry and write this branch's manifest."""
    ser = entries(hl, gold)
    reg = json.loads(REGISTRY.read_text())
    reg["series"].update(ser)
    # Every key resolves for every window kind (the framework's own convention, cf. `local_index`):
    # `GSPC_1957_1999` maps to the index that window trades, and the `*` catch-all to the main
    # instrument's own shape. Neither is reached by the IB03 plugin -- it uses the panel's own OHLC
    # on the ETF windows and refuses `GSPC_1957_1999` by name before calling `extra` at all (the
    # pre-registered drop, see research/ideas/ib03.py) -- they exist so an unmapped window fails
    # open as *the main instrument's* shape rather than as a missing key.
    keys = {w: name for name, (_s, _y, w) in HL_SOURCES.items()}
    keys["GSPC_1957_1999"] = "^GSPC-HL"
    keys["*"] = "QQQ-HL"
    lows = {w: name + "-LOW" for name, (_s, _y, w) in HL_SOURCES.items()}
    lows["GSPC_1957_1999"] = "^GSPC-HL-LOW"
    lows["*"] = "QQQ-HL-LOW"
    reg["keys"]["underlying_relhigh"] = {"windows": keys}
    reg["keys"]["underlying_rellow"] = {"windows": lows}
    reg["keys"]["gold"] = "GC=F"
    REGISTRY.write_text(json.dumps(reg, indent=1, sort_keys=True) + "\n")

    doc = {"campaign": "plugins_b3",
           "note": ("Extra series for the plugins_b3 IB03/IB23 plugins: the underlying's high/low "
                    "relative to its own close, derived from the committed harness inputs (the "
                    "main panel's QQQ file and the five OOS markets' index file), plus GC=F "
                    "downloaded from the Yahoo chart API. Sources, ranges and SHA-256 below; the "
                    "framework registry research/batch_screen/EXTRA_DATA.json carries the series "
                    "and the plugin-facing keys."),
           "extra_data": ser,
           "files": {m["file"]: {"bytes": m["bytes"], "sha256": m["sha256"]}
                     for m in ser.values()},
           "sources": {m["derived_from"]["file"]: {"sha256": m["derived_from"]["sha256"]}
                       for m in ser.values() if "derived_from" in m}}
    LOCAL_MANIFEST.write_text(json.dumps(doc, indent=1, sort_keys=True) + "\n")
    return reg


def main(argv=None) -> int:
    hl = build_hl()
    gold = build_gold()
    reg = register(hl, gold)
    extra.reset()
    bad = extra.verify()["bad"]
    print(f"registry: {len(reg['series'])} series, {len(reg['keys'])} keys; verify bad={bad}")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
