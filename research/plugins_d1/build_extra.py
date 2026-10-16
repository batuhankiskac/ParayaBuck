"""plugins_d1 -- download and register the extra series of the IC31/IC32/IC33/IC34/IC35/IC36 plugins.

Three new Yahoo chart API series, all of them the ETF's **adjusted close** (the
total-return-comparable column, the same convention every registered ETF uses in
`research/batch_screen/EXTRA_DATA.json`):

    LQD  iShares iBoxx $ Investment Grade Corporate Bond ETF   IC31 (+ IC36 B is GLD/IEF/SHY/XLU)
    TIP  iShares TIPS Bond ETF                                 IC33
    GDX  VanEck Gold Miners ETF                                IC35

`extra.py`'s own `--fetch` only accepts the symbols in its module-level `YAHOO` tuple, so this
registers the series the way that module's README prescribes for a series it cannot fetch itself:
write the CSV, append the registry entry (the same fields and the same `write_series`/`sha256`
helpers `extra.build` would have written), write the branch's DATA_MANIFEST.json, then
`python -m research.batch_screen.extra --verify`. IC32's HYG, IC34's XLU/XLP and IC37's
IEF/DGS10/T10YIE are already registered (plugins_c2 / batch_screen_v1) and are not touched.

    PYTHONPATH=. ~/.venvs/pb-research/bin/python -m research.plugins_d1.build_extra
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import numpy as np

from research.batch_screen import extra

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
REGISTRY = Path(extra.REGISTRY_PATH)
LOCAL_MANIFEST = HERE / "DATA_MANIFEST.json"

#: plugin-facing key -> Yahoo symbol (one new series per key)
KEYS = {"lqd": "LQD", "tip": "TIP", "gdx": "GDX"}

#: what each series is for (the manifest's `used_by`)
USED_BY = {"LQD": "IC31 off-state investment-grade credit leg (and IC31 arm C's own SMA200 gate)",
           "TIP": "IC33 off-state TIPS leg", "GDX": "IC35 off-state gold-miners leg"}

def _range(dates, vals):
    v = np.asarray(vals, dtype=float)
    have = np.isfinite(v)
    if not have.any():
        return [None, None], 0
    return [str(dates[int(np.argmax(have))]),
            str(dates[len(dates) - 1 - int(np.argmax(have[::-1]))])], int(have.sum())

def fetch(sym: str) -> dict:
    """One Yahoo chart series, written to `data/historical/plugins_d1_<sym>.csv` and declared."""
    dates, cols, url = extra.fetch_yahoo(sym)
    rel = f"data/historical/plugins_d1_{sym.lower()}.csv"
    info = extra.write_series(rel, dates, cols)
    col = "adjclose"
    rng, n = _range(dates, cols[col])
    return {
        "file": rel, "column": col, "price": col, "url": url,
        "fetched": date.today().isoformat(), "range": rng, "rows": n,
        "bytes": info["bytes"], "sha256": info["sha256"], "source": "Yahoo chart API",
    }

def main() -> int:
    ser = {sym: fetch(sym) for sym in sorted(set(KEYS.values()))}
    for sym, m in sorted(ser.items()):
        print(f"  {sym:6s} {m['file']}  {m['range'][0]} -> {m['range'][1]}  {m['rows']} rows  "
              f"{m['sha256'][:12]}")

    reg = json.loads(REGISTRY.read_text())
    clash = [k for k, s in KEYS.items() if k in reg["keys"] and reg["keys"][k] != s]
    clash += [s for s in ser if s in reg["series"]]
    if clash:
        raise SystemExit(f"already registered, refusing to overwrite: {clash}")
    # appended in place (the registry's existing order and 2-space format are kept, so the diff is
    # additive and several plugin branches merge with the least conflict)
    reg["series"].update(ser)
    reg["keys"].update(KEYS)
    REGISTRY.write_text(json.dumps(reg, indent=2) + "\n")

    doc = {
        "campaign": "plugins_d1",
        "note": ("Extra series for the plugins_d1 off-state plugins (IC31-IC38): three new Yahoo "
                 "chart API adjusted closes (LQD, TIP, GDX). The framework registry "
                 "research/batch_screen/EXTRA_DATA.json carries the series and the plugin-facing "
                 "keys (lqd, tip, gdx); IC32's HYG, IC34's XLU/XLP and IC37's IEF/DGS10/T10YIE "
                 "were already registered by plugins_c2 / the batch_screen_v1 build."),
        "keys": KEYS,
        "used_by": USED_BY,
        "extra_data": ser,
        "files": {m["file"]: {"bytes": m["bytes"], "sha256": m["sha256"]} for m in ser.values()},
    }
    LOCAL_MANIFEST.write_text(json.dumps(doc, indent=1, sort_keys=True) + "\n")

    extra.reset()
    v = extra.verify(sorted(ser))
    for name in sorted(v["series"]):
        r = v["series"][name]
        print(f"  {name:6s} sha256 {'ok' if r['sha256_ok'] else 'MISMATCH'}  range "
              f"{'ok' if r['range_ok'] else r.get('range')}  {r.get('rows')} rows")
    print(f"verify {'PASS' if v['ok'] else 'FAIL'} (bad: {v['bad']})")
    return 0 if v["ok"] else 1

if __name__ == "__main__":
    raise SystemExit(main())
