"""batch_screen.extra -- series beyond `MarketData`: the registry, the aligned loader, downloaders.

A plugin never reads a CSV. It asks for a **key**:

    vix = data.extra("vix")                  # (P, T) aligned to the panel's sessions, NaN where
    vix = data.extra("vix", ffill=True)      # ... the series has no value on that session

and every key resolves through the registry `EXTRA_DATA.json`:

    "series": {"^VIX": {"file": "data/historical/batch_screen_v1_vix.csv", "column": "close",
                        "price": "close", "url": "https://query1.finance.yahoo.com/...",
                        "range": ["1990-01-02", "2026-10-15"], "rows": 9237, "bytes": 214371,
                        "sha256": "..."}},
    "keys":   {"vix": "^VIX",
               "local_index": {"windows": {"main": "^NDX", "M1": "^IXIC", "M2": "^GSPC",
                                           "M3": "^N225", "M4": "^GDAXI", "M5": "^FTSE",
                                           "GSPC_1957_1999": "^GSPC", "*": "^GSPC"}}}

A key is either a series name (one string: the same series in every window) or a window map.
Resolution of a window map is: the window's own entry, else the `"main"` entry (a bootstrap chunk
replays the main window's pool, so `"boot"`/`"oos_boot"` resolve through `"main"`... the OOS pooled
bootstrap carries its own market's window key instead), else `"*"`.

Alignment is by **date**: a session's value is the series' value on that session's own date (the
drawn source session for a bootstrap path, not the label the boot calendar carries), and `NaN`
where the series has no such date. Nothing is forward-filled unless the plugin asks (`ffill=True`);
the engine and the published control numbers are untouched by any of this -- `extra()` is plain
numpy evaluated inside `targets`.

Downloads are restricted to two public sources, `--fetch` (Yahoo chart API, JSON) and
`--fetch-fred` (FRED CSV); each series is stored as its own `date,<columns>` CSV and the registry's
`sha256`/`range`/`rows` are recomputed from the written file. A hand-made series (a public calendar,
say) is registered by adding its entry by hand and running `--verify`.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
import urllib.request
from datetime import UTC, date, datetime
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
REGISTRY_PATH = HERE / "EXTRA_DATA.json"

SCHEMA = "parayabuck.batch_screen.extradata/v1"
YAHOO_URL = ("https://query1.finance.yahoo.com/v8/finance/chart/{sym}"
             "?period1={p1}&period2={p2}&interval=1d&events=div%2Csplit")
FRED_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={sid}"
USER_AGENT = "parayabuck-research/1.0 (batch_screen extra-data fetch)"

#: the series this registry was built from, by source. `build()` fetches these; a series added by
#: hand is not in either list and is left alone.
YAHOO = ("^VIX", "^VIX3M", "^VXN", "^STOXX50E", "^GDAXI", "^N225", "^FTSE", "^IXIC", "^GSPC",
         "^NDX", "SMH", "SOXX", "HG=F", "SHY", "GLD", "TLT", "IEF", "XLB", "XLE", "XLF", "XLI",
         "XLK", "XLP", "XLU", "XLV", "XLY")
FRED = ("DGS10", "T10YIE", "DTWEXBGS", "BAA10Y", "CPIAUCSL")

#: Yahoo series whose registered column is the adjusted close (total-return-comparable: the ETFs);
#: indices, futures and every FRED series register their own raw column.
ADJUSTED = ("SMH", "SOXX", "SHY", "GLD", "TLT", "IEF", "XLB", "XLE", "XLF", "XLI", "XLK", "XLP",
            "XLU", "XLV", "XLY")

#: the OOS windows' own index, for a "same-day" cross-market signal. `main`/`boot` use ^NDX (the
#: main window's underlying); each OOS market uses the index that window trades.
LOCAL_INDEX = {"main": "^NDX", "M1": "^IXIC", "M2": "^GSPC", "GSPC_1957_1999": "^GSPC",
               "M3": "^N225", "M4": "^GDAXI", "M5": "^FTSE", "*": "^GSPC"}

#: key -> series (a string) or key -> {"windows": {window: series}}. Hand-written: it is the
#: plugin-facing vocabulary and is preserved by `build()`.
KEYS = {
    "vix": "^VIX",
    "vix3m": "^VIX3M",
    "vxn": "^VXN",
    "dgs10": "DGS10",
    "t10yie": "T10YIE",
    "dtwexbgs": "DTWEXBGS",
    "baa10y": "BAA10Y",
    "cpi": "CPIAUCSL",
    "smh": "SMH",
    "soxx": "SOXX",
    "copper": "HG=F",
    "shy": "SHY",
    "gld": "GLD",
    "tlt": "TLT",
    "ief": "IEF",
    "stoxx50e": "^STOXX50E",
    "dax": "^GDAXI",
    "nikkei": "^N225",
    "ftse": "^FTSE",
    "nasdaq": "^IXIC",
    "sp500": "^GSPC",
    "ndx": "^NDX",
    "sec_xlb": "XLB",
    "sec_xle": "XLE",
    "sec_xlf": "XLF",
    "sec_xli": "XLI",
    "sec_xlk": "XLK",
    "sec_xlp": "XLP",
    "sec_xlu": "XLU",
    "sec_xlv": "XLV",
    "sec_xly": "XLY",
    "local_index": {"windows": LOCAL_INDEX},
}


NOTE = (
    "Extra series for batch_screen plugins, one CSV per series. Sources: the Yahoo chart API "
    "(period1=0, daily, `events=div,split`: columns `date,close,adjclose`) and FRED CSV "
    "(columns `date,value`). Refresh with `python -m research.batch_screen.extra --fetch-all`, "
    "add a series with --fetch/--fetch-fred, check every hash and range with --verify. A key is a "
    "series name or a {\"windows\": {window: series}} map; `data.extra(key)` returns the series "
    "aligned to the panel's own sessions (the drawn source session inside a bootstrap chunk), NaN "
    "where the series has no value on that date. Nothing is forward-filled unless the plugin asks "
    "for `ffill=True`."
)


class ExtraError(Exception):
    """The extra-data registry or a series file is missing, malformed or out of date."""


# ----------------------------------------------------------------------------- registry
_REG = {}
_FILES = {}
_SERIES = {}


def reset() -> None:
    """Drop every cache (the registry, the parsed files, the extracted series)."""
    _REG.clear()
    _FILES.clear()
    _SERIES.clear()


def registry(path=None) -> dict:
    """The parsed `EXTRA_DATA.json` (cached)."""
    key = str(path or REGISTRY_PATH)
    if key not in _REG:
        p = Path(key)
        if not p.exists():
            raise ExtraError(f"no extra-data registry: {p}")
        reg = json.loads(p.read_text())
        if reg.get("schema") != SCHEMA:
            raise ExtraError(f"{p}: schema {reg.get('schema')!r} != {SCHEMA!r}")
        for section in ("series", "keys"):
            if not isinstance(reg.get(section), dict) or not reg[section]:
                raise ExtraError(f"{p}: {section} must be a non-empty object")
        _REG[key] = reg
    return _REG[key]


def keys(path=None) -> list:
    """Every registered key, sorted."""
    return sorted(registry(path)["keys"])


def series_names(path=None) -> list:
    """Every registered series, sorted."""
    return sorted(registry(path)["series"])


def series_meta(name: str, path=None) -> dict:
    """One series' declaration (file, column, price, url, range, sha256, ...)."""
    reg = registry(path)
    if name not in reg["series"]:
        raise ExtraError(f"unknown series {name!r}; known: {sorted(reg['series'])}")
    return reg["series"][name]


def resolve_series(key: str, window: str, path=None) -> str:
    """The series a key means in one window (`window` = `main`, an OOS key, an ETF, `boot`)."""
    reg = registry(path)
    if key not in reg["keys"]:
        raise ExtraError(f"unknown extra key {key!r}; known: {sorted(reg['keys'])}")
    decl = reg["keys"][key]
    if isinstance(decl, str):
        name = decl
    else:
        wmap = decl.get("windows") or {}
        name = wmap.get(window)
        if name is None and window in ("boot", "oos_boot"):
            # a bootstrap chunk replays a real pool (its own market's, or the main window's for
            # the clean/fresh bootstrap), so it resolves through that pool's own window entry.
            name = wmap.get("main")
        if name is None:
            name = wmap.get("*")
        if name is None:
            raise ExtraError(f"key {key!r} has no series for window {window!r} "
                             f"(mapped: {sorted(wmap)})")
    if name not in reg["series"]:
        raise ExtraError(f"key {key!r} -> unknown series {name!r}")
    return name


# ----------------------------------------------------------------------------- series files
def _num(x: str) -> float:
    x = (x or "").strip()
    return np.nan if x in ("", ".", "NA", "null") else float(x)


def _file(rel: str, root=ROOT) -> dict:
    """One series file parsed once: `{"dates": (T,) <U10, "cols": {name: (T,) float64}}`."""
    if rel not in _FILES:
        p = Path(root) / rel
        if not p.exists():
            raise ExtraError(f"missing series file: {p}")
        with p.open() as fh:
            rd = csv.reader(fh)
            head = next(rd)
            if not head or head[0] != "date":
                raise ExtraError(f"{p}: the first column must be 'date' (got {head[:1]})")
            names = head[1:]
            dates, cols = [], {n: [] for n in names}
            for row in rd:
                if not row:
                    continue
                dates.append(row[0])
                for n, v in zip(names, row[1:]):
                    cols[n].append(_num(v))
        _FILES[rel] = {
            "dates": np.array(dates, dtype="<U10"),
            "cols": {n: np.array(v, dtype=np.float64) for n, v in cols.items()},
        }
    return _FILES[rel]


def _column(name: str, path=None, root=ROOT):
    """`(dates, values)` of one registered series, parsed once."""
    ck = (name, str(path or REGISTRY_PATH))
    if ck not in _SERIES:
        meta = series_meta(name, path)
        f = _file(meta["file"], root)
        col = meta["column"]
        if col not in f["cols"]:
            raise ExtraError(f"series {name!r}: {meta['file']} has no column {col!r} "
                             f"(columns: {sorted(f['cols'])})")
        _SERIES[ck] = (f["dates"], f["cols"][col])
    return _SERIES[ck]


def values_on(name: str, days, path=None, root=ROOT) -> np.ndarray:
    """One series' values aligned to `days` (ISO strings): ``(len(days),)``, NaN where missing."""
    dates, vals = _column(name, path, root)
    days = [d if isinstance(d, str) else str(d) for d in days]
    if len(dates) and len(days):
        pos = np.searchsorted(dates, np.asarray(days, dtype="<U10"))
        pos[pos >= dates.size] = dates.size - 1
        hit = dates[pos] == np.asarray(days, dtype="<U10")
        out = np.where(hit, vals[pos], np.nan)
    else:
        out = np.full(len(days), np.nan)
    return out.astype(np.float64)


def aligned(key: str, window: str, days, pool_days=None, index=None, shape=None,
            path=None, root=ROOT) -> np.ndarray:
    """The key's series for one panel, as ``(P, T)``.

    `days` are the panel's own session labels; when `index` is given (a bootstrap chunk), the
    values are the series' values on `pool_days[index[p, t]]` -- the drawn *source* sessions, which
    is what the chunk's market path replays -- and `shape` is the panel's `(P, T)`.
    """
    name = resolve_series(key, window, path)
    if index is None:
        base = values_on(name, days, path, root)
        if shape is None:
            return base.reshape(1, -1)
        return np.tile(base, (int(shape[0]), 1))
    pool = values_on(name, pool_days, path, root)
    index = np.asarray(index, dtype=np.int64)
    if index.size and (index.min() < 0 or index.max() >= pool.size):
        raise ExtraError(f"key {key!r}: index out of range for pool of {pool.size} sessions")
    return pool[index]


def forward_fill(a: np.ndarray) -> np.ndarray:
    """Carry the last non-NaN value of each row forward (leading NaNs stay NaN)."""
    a = np.asarray(a, dtype=np.float64)
    idx = np.where(np.isnan(a), 0, np.arange(a.shape[-1]))
    np.maximum.accumulate(idx, axis=-1, out=idx)
    return np.take_along_axis(a, idx, axis=-1)


# ----------------------------------------------------------------------------- verification
def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def verify(names=None, path=None, root=ROOT) -> dict:
    """Re-hash every series file and re-read every declared date range.

    The registry's `sha256` is the file's hash when it was written and its `range` the first/last
    date with a value; both are recomputed here, so a refreshed file that was not re-declared (or a
    hand-edited registry entry) is caught before a batch runs.
    """
    reg = registry(path)
    names = sorted(reg["series"]) if names is None else list(names)
    out, bad = {}, []
    for name in names:
        meta = series_meta(name, path)
        p = Path(root) / meta["file"]
        rec = {"file": meta["file"], "sha256_ok": False, "range_ok": False}
        if not p.exists():
            rec["error"] = "missing file"
            bad.append(name)
            out[name] = rec
            continue
        h = sha256(p)
        rec["sha256_ok"] = bool(h == meta.get("sha256"))
        if not rec["sha256_ok"]:
            rec["sha256"] = h
        f = _file(meta["file"], root)
        vals = f["cols"].get(meta["column"])
        if vals is None:
            rec["error"] = f"no column {meta['column']!r}"
            bad.append(name)
            out[name] = rec
            continue
        have = ~np.isnan(vals)
        rng = [str(f["dates"][have][0]), str(f["dates"][have][-1])] if have.any() else [None, None]
        rec["range"] = rng
        rec["range_ok"] = bool(list(meta.get("range") or []) == rng)
        rec["rows"] = int(have.sum())
        if not (rec["sha256_ok"] and rec["range_ok"]):
            bad.append(name)
        out[name] = rec
    return {"ok": not bad, "n_series": len(names), "bad": bad, "series": out}


def key_table(path=None) -> list:
    """One row per key: what it resolves to, that series' file, range and coverage."""
    reg = registry(path)
    rows = []
    for key in sorted(reg["keys"]):
        decl = reg["keys"][key]
        if isinstance(decl, str):
            names = [decl]
        else:
            names = sorted(set(decl.get("windows", {}).values()))
        for name in names:
            meta = reg["series"].get(name, {})
            rows.append({"key": key, "series": name, "file": meta.get("file"),
                         "column": meta.get("column"), "price": meta.get("price"),
                         "range": meta.get("range"), "rows": meta.get("rows")})
    return rows


# ----------------------------------------------------------------------------- downloads
def _get(url: str, timeout: float = 60.0) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as fh:
        return fh.read()


def parse_yahoo(payload: bytes, symbol: str):
    """Yahoo chart JSON -> `(dates, close, adjclose)` as lists (NaN where the API has nulls)."""
    doc = json.loads(payload.decode("utf-8"))
    chart = doc.get("chart") or {}
    if chart.get("error"):
        raise ExtraError(f"yahoo {symbol}: {chart['error']}")
    res = (chart.get("result") or [None])[0]
    if not res:
        raise ExtraError(f"yahoo {symbol}: empty result")
    ts = res["timestamp"]
    q = res["indicators"]["quote"][0]
    adj = ((res["indicators"].get("adjclose") or [{}])[0].get("adjclose")
           if res["indicators"].get("adjclose") else None)
    dates = [datetime.fromtimestamp(t, tz=UTC).strftime("%Y-%m-%d") for t in ts]
    close = [np.nan if c is None else float(c) for c in q["close"]]
    if adj is None:
        adjclose = list(close)
    else:
        adjclose = [np.nan if c is None else float(c) for c in adj]
    return dates, close, adjclose


def fetch_yahoo(symbol: str, period2=None, timeout: float = 60.0, get=_get):
    """One Yahoo chart series: `(dates, {"close": [...], "adjclose": [...]}, url)`."""
    p2 = int(period2 if period2 is not None else datetime.now(tz=UTC).timestamp())
    url = YAHOO_URL.format(sym=urllib.request.quote(symbol), p1=0, p2=p2)
    dates, close, adjclose = parse_yahoo(get(url, timeout), symbol)
    rows = [(d, c, a) for d, c, a in zip(dates, close, adjclose) if np.isfinite(c)]
    return ([r[0] for r in rows], {"close": [r[1] for r in rows],
                                   "adjclose": [r[2] for r in rows]}, url)


def parse_fred(payload: bytes):
    """FRED CSV (`observation_date,VALUE`) -> `(dates, values)`; '.'/'' become NaN and drop out."""
    text = payload.decode("utf-8")
    rd = csv.reader(text.splitlines())
    head = next(rd)
    if len(head) < 2:
        raise ExtraError(f"fred: unexpected header {head}")
    dates, vals = [], []
    for row in rd:
        if not row:
            continue
        v = _num(row[1])
        if np.isfinite(v):
            dates.append(row[0])
            vals.append(v)
    return dates, vals


def fetch_fred(sid: str, timeout: float = 60.0, get=_get):
    """One FRED CSV series: `(dates, {"value": [...]}, url)`."""
    url = FRED_URL.format(sid=sid)
    dates, vals = parse_fred(get(url, timeout))
    return dates, {"value": [float(v) for v in vals]}, url


def series_file(symbol: str) -> str:
    """The repo-relative file one series is stored in."""
    slug = "".join(ch.lower() if ch.isalnum() else "_" for ch in symbol).strip("_")
    return f"data/historical/batch_screen_v1_{slug}.csv"


def write_series(rel: str, dates, cols: dict, root=ROOT) -> dict:
    """Write one series file deterministically (`date,<col>,...`, ascending dates) and report."""
    p = Path(root) / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    order = sorted(cols)
    lines = ["date," + ",".join(order)]
    for i, d in enumerate(dates):
        cells = []
        for c in order:
            v = cols[c][i]
            cells.append("" if v is None or not np.isfinite(v) else f"{v:.10g}")
        lines.append(f"{d}," + ",".join(cells))
    p.write_text("\n".join(lines) + "\n")
    return {"file": rel, "bytes": p.stat().st_size, "sha256": sha256(p)}


def _load_or_init(path=None) -> dict:
    """The registry document, created from the module's constants when the file does not exist."""
    p = Path(path or REGISTRY_PATH)
    if p.exists():
        reg = json.loads(p.read_text())
        if reg.get("schema") != SCHEMA:
            raise ExtraError(f"{p}: schema {reg.get('schema')!r} != {SCHEMA!r}")
    else:
        reg = {"schema": SCHEMA, "series": {}}
    reg.setdefault("series", {})
    # `KEYS` is the initial vocabulary: entries added to the registry by hand (or by a plugin
    # branch) win, and a key added to the module since the last build is picked up.
    reg["keys"] = {**KEYS, **reg.get("keys", {})}
    return reg


def build(only=None, path=None, root=ROOT, fetch_yahoo_=fetch_yahoo, fetch_fred_=fetch_fred,
          fetchers=None, timeout=60.0, verbose=True) -> dict:
    """(Re)fetch every registered series (or `only`, a list of series names) and re-declare it.

    Every Yahoo symbol in `YAHOO` and every FRED id in `FRED` is fetched and its file rewritten;
    `only` restricts the work (a series registered by hand is never touched). The registry's
    `sha256`, `range`, `rows`, `bytes`, `url` and `fetched` are recomputed from the written files.
    """
    reg = _load_or_init(path)
    todo = list(only) if only else list(YAHOO) + list(FRED)
    unknown = [s for s in todo if s not in set(YAHOO) | set(FRED)]
    if unknown:
        raise ExtraError(f"not a downloadable series: {unknown} (hand-made series are declared "
                         "by editing the registry and checked with --verify)")
    sha = fetchers if fetchers is not None else {}
    today = date.today().isoformat()
    done = {}
    for sym in todo:
        if sym in sha:
            dates, cols, url = sha[sym]
        elif sym in YAHOO:
            dates, cols, url = fetch_yahoo_(sym, timeout=timeout)
        else:
            dates, cols, url = fetch_fred_(sym, timeout=timeout)
        rel = reg["series"].get(sym, {}).get("file") or series_file(sym)
        info = write_series(rel, dates, cols, root)
        cols_order = sorted(cols)
        col = ("adjclose" if (sym in ADJUSTED and "adjclose" in cols) else cols_order[-1])
        vals = np.asarray(cols[col], dtype=np.float64)
        have = np.isfinite(vals)
        reg["series"][sym] = {
            "file": rel, "column": col,
            "price": {"adjclose": "adjclose", "value": "level"}.get(col, "close"),
            "url": url, "fetched": today,
            "range": [str(dates[int(np.argmax(have))]),
                      str(dates[len(dates) - 1 - int(np.argmax(have[::-1]))])],
            "rows": int(have.sum()), "bytes": info["bytes"], "sha256": info["sha256"],
            "source": "Yahoo chart API" if sym in YAHOO else "FRED CSV",
        }
        done[sym] = reg["series"][sym]
        if verbose:
            print(f"  {sym:12s} {reg['series'][sym]['file']}  {info['bytes']:>9d} B  "
                  f"{reg['series'][sym]['range'][0]} -> {reg['series'][sym]['range'][1]}  "
                  f"{reg['series'][sym]['rows']} rows", flush=True)
    reg["series"] = {k: reg["series"][k] for k in sorted(reg["series"])}
    reg["keys"] = {k: reg["keys"][k] for k in sorted(reg["keys"])}
    reg["fetched"] = today
    reg["note"] = NOTE
    Path(path or REGISTRY_PATH).write_text(json.dumps(reg, indent=1, sort_keys=True) + "\n")
    reset()
    return done


# ----------------------------------------------------------------------------- CLI
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m research.batch_screen.extra")
    ap.add_argument("--fetch", help="comma-separated registered Yahoo series to (re)fetch")
    ap.add_argument("--fetch-fred", help="comma-separated registered FRED series to (re)fetch")
    ap.add_argument("--fetch-all", action="store_true", help="refetch every registered series")
    ap.add_argument("--verify", action="store_true", help="re-hash every file and check its range")
    ap.add_argument("--keys", action="store_true", help="print the key table")
    ap.add_argument("--timeout", type=float, default=60.0)
    args = ap.parse_args(argv)
    if args.fetch or args.fetch_fred or args.fetch_all:
        only = ([s for s in (args.fetch or "").split(",") if s]
                + [s for s in (args.fetch_fred or "").split(",") if s])
        build(only=only or None, timeout=args.timeout)
        return 0
    if args.verify:
        v = verify()
        for name in sorted(v["series"]):
            r = v["series"][name]
            print(f"  {name:12s} sha256 {'ok' if r['sha256_ok'] else 'MISMATCH'}  "
                  f"range {'ok' if r['range_ok'] else r.get('range')}  {r.get('rows')} rows")
        print(f"verify {'PASS' if v['ok'] else 'FAIL'} ({v['n_series']} series, "
              f"bad: {v['bad']})")
        return 0 if v["ok"] else 1
    if args.keys:
        for r in key_table():
            print(f"  {r['key']:12s} <- {r['series']:12s} {r['column']:>8s} "
                  f"{(r['range'] or [None, None])[0]} -> {(r['range'] or [None, None])[1]}  "
                  f"({r['price']})")
        return 0
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
