"""plugins_c2 -- fetch and register the extra series the IC12/IC17/IC27 plugins need.

Three downloads, from the two sources the framework allows:

    NFCI           FRED CSV  https://fred.stlouisfed.org/graph/fredgraph.csv?id=NFCI
                   weekly Chicago Fed National Financial Conditions Index, 1971-01-08 ->
    BAMLH0A0HYM2   FRED CSV  https://fred.stlouisfed.org/graph/fredgraph.csv?id=BAMLH0A0HYM2
                   ICE BofA US High Yield Index option-adjusted spread
    HYG            Yahoo     https://query1.finance.yahoo.com/v8/finance/chart/HYG
                   iShares iBoxx $ High Yield Corporate Bond ETF, adjusted close

**The ICE BofA cap.**  FRED serves only the **last three years** of every ICE BofA series through
the graph CSV endpoint (`cosd`/`coed` are ignored for them: `?id=BAMLH0A0HYM2&cosd=1996-12-31`
still returns the trailing ~790 sessions, while `?id=BAA10Y` returns its full 1986+ history and
`?id=DTWEXBGS` honours `cosd`).  The file registered here is therefore the whole history FRED
exposes -- the `hyoas` key starts where FRED starts serving it -- and `research/ideas/IC17.py`
refuses the windows that do not overlap it.  Nothing else on FRED or Yahoo carries the series
(`^HYOAS`, `HYOAS`, `^BAMLH0A0HYM2` are 404 on the chart API; `JNK`/`BKLN` are bond *price* ETFs,
not the OAS, and would be a different signal).

Run it from the worktree root with the research venv:

    PYTHONPATH=. ~/.venvs/pb-research/bin/python -m research.plugins_c2.build_extra
    PYTHONPATH=. ~/.venvs/pb-research/bin/python -m research.batch_screen.extra --verify
"""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

from research.batch_screen import extra

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
REGISTRY = ROOT / "research/batch_screen/EXTRA_DATA.json"
LOCAL_MANIFEST = HERE / "DATA_MANIFEST.json"

#: series -> (file, source, the extra key it is published under)
SERIES = {
    "NFCI": ("data/historical/plugins_c2_nfci.csv", "FRED CSV", "nfci"),
    "BAMLH0A0HYM2": ("data/historical/plugins_c2_hyoas.csv", "FRED CSV", "hyoas"),
    "HYG": ("data/historical/plugins_c2_hyg.csv", "Yahoo chart API", "hyg"),
}

#: the Yahoo series whose registered column is the adjusted (total-return-comparable) close
ADJUSTED = ("HYG",)

ICE_CAP_NOTE = (
    "FRED's graph CSV endpoint serves only the trailing three years of every ICE BofA series "
    "(BAMLH0A0HYM2 here), so this series' registered range is what FRED exposes, not 1996-12-31."
)


def fetch(name: str, timeout: float = 60.0):
    """`(dates, columns, url, column, price)` of one series, from the framework's own fetchers."""
    if name in ADJUSTED:
        dates, cols, url = extra.fetch_yahoo(name, timeout=timeout)
        column, price = "adjclose", "adjclose"
        if not any(v == v for v in cols["adjclose"]):  # pragma: no cover -- the API always sends it
            column, price = "close", "close"
        cols = {column: cols[column]}
    else:
        dates, cols, url = extra.fetch_fred(name, timeout=timeout)
        column, price = "value", "level"
    return dates, cols, url, column, price


def build(root=ROOT, quiet=False) -> dict:
    """Fetch every series, write its file, and return the registry entries."""
    today = date.today().isoformat()
    out = {}
    for name, (rel, source, key) in SERIES.items():
        dates, cols, url, column, price = fetch(name)
        vals = list(cols[column])
        info = extra.write_series(rel, dates, {column: vals}, root=root)
        have = [i for i, v in enumerate(vals) if v == v]
        entry = {
            "file": rel,
            "column": column,
            "price": price,
            "source": source,
            "url": url,
            "fetched": today,
            "range": [dates[have[0]], dates[have[-1]]],
            "rows": len(have),
            "bytes": info["bytes"],
            "sha256": info["sha256"],
        }
        if name == "BAMLH0A0HYM2":
            entry["note"] = ICE_CAP_NOTE
        out[name] = entry
        if not quiet:
            print(f"  {name:12s} {rel}  {entry['range'][0]} -> {entry['range'][1]}  "
                  f"{entry['rows']} rows  {info['bytes']} B", flush=True)
    return out


def register(entries: dict, root=ROOT) -> dict:
    """Add every series and its key to the framework registry; write this branch's manifest."""
    reg = json.loads(REGISTRY.read_text())
    reg["series"].update(entries)
    for name, (_rel, _src, key) in SERIES.items():
        reg["keys"][key] = name
    # additions only, appended in the committed file's own layout (indent 2, insertion order) so
    # the diff against the merged registry is the new entries and nothing else
    REGISTRY.write_text(json.dumps(reg, indent=2) + "\n")

    doc = {
        "campaign": "plugins_c2",
        "note": ("Extra series for the plugins_c2 IC12/IC17/IC27 batch_screen plugins: the weekly "
                 "Chicago Fed NFCI and the ICE BofA US high-yield OAS from FRED CSV, and the HYG "
                 "adjusted close from the Yahoo chart API. " + ICE_CAP_NOTE +
                 " The framework registry research/batch_screen/EXTRA_DATA.json carries the series "
                 "and the plugin-facing keys (nfci, hyoas, hyg)."),
        "extra_data": entries,
        "keys": {key: name for name, (_rel, _src, key) in SERIES.items()},
        "files": {m["file"]: {"bytes": m["bytes"], "sha256": m["sha256"]}
                  for m in entries.values()},
    }
    LOCAL_MANIFEST.write_text(json.dumps(doc, indent=1, sort_keys=True) + "\n")
    return reg


def main(argv=None) -> int:
    entries = build()
    reg = register(entries)
    extra.reset()
    bad = extra.verify()["bad"]
    print(f"registry: {len(reg['series'])} series, {len(reg['keys'])} keys; verify bad={bad}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
