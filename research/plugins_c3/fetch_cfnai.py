"""Register FRED `CFNAI` as the batch_screen extra key `cfnai` (IC13's input).

The framework downloads a series with

    python -m research.batch_screen.extra --fetch-fred CFNAI

which requires the id to be listed in `research/batch_screen/extra.py`'s `FRED` tuple -- an edit of
the framework module, which this branch must not make. The registry entry is therefore declared by
hand instead, exactly as the plugin author guide's "add a hand-made series ... write its
`date,value` CSV and its registry entry, then `--verify`" path (and the `FOMC_DATES`/`FOMC_WEEK`
precedent of `research/plugins_b1`) prescribes. The download itself and the file format are the
framework's own: `extra._get` (the same User-Agent), `extra.parse_fred`, `extra.write_series`
(`date,value`, `%.10g`, ascending) and `extra.sha256`. The registry document is rewritten in the
committed file's own layout (`json.dumps(reg, indent=2)`, existing key order kept, the new entries
added in place) so the diff against the merged framework file is the new entry only; a later
framework-side `build()` refresh reproduces the same entry.

    python -m research.plugins_c3.fetch_cfnai

Source: FRED CSV, https://fred.stlouisfed.org/graph/fredgraph.csv?id=CFNAI (monthly, 1967-03 ->).
"""

from __future__ import annotations

import json
from datetime import date

import numpy as np

from research.batch_screen import extra as _ext

SERIES = "CFNAI"
KEY = "cfnai"


def main() -> int:
    payload = _ext._get(_ext.FRED_URL.format(sid=SERIES))  # the framework's own fetch
    dates, vals_list = _ext.parse_fred(payload)  # `fetch_fred` wraps this the same way
    cols = {"value": vals_list}
    rel = _ext.series_file(SERIES)
    info = _ext.write_series(rel, dates, cols)
    vals = np.asarray(cols["value"], dtype=np.float64)
    have = np.isfinite(vals)
    reg = _ext._load_or_init()
    reg["series"][SERIES] = {
        "file": rel,
        "column": "value",
        "price": "level",
        "url": _ext.FRED_URL.format(sid=SERIES),
        "fetched": date.today().isoformat(),
        "range": [str(dates[int(np.argmax(have))]), str(dates[len(dates) - 1 - int(np.argmax(have[::-1]))])],
        "rows": int(have.sum()),
        "bytes": info["bytes"],
        "sha256": info["sha256"],
        "source": "FRED CSV",
    }
    reg["keys"][KEY] = SERIES
    _ext.REGISTRY_PATH.write_text(json.dumps(reg, indent=2) + "\n")
    _ext.reset()
    meta = reg["series"][SERIES]
    print(f"{SERIES}: {meta['file']}  {meta['bytes']} B  {meta['rows']} rows  "
          f"{meta['range'][0]} -> {meta['range'][1]}  sha256 {meta['sha256']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
