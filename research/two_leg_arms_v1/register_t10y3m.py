"""Register FRED T10Y3M (10y - 3m constant-maturity spread) as a batch_screen extra series.

IB26's off-state IEF arms need the curve slope. `extra.py`'s own `--fetch-fred` only accepts the
ids in its module-level `FRED` tuple, so this registers the series the way `extra.py`'s README
prescribes for a series it cannot fetch itself: write the CSV and its registry entry, then
`--verify`. The file/column/hash/dates are produced by the framework's own `parse_fred` /
`write_series` / `sha256`, so the entry is byte-identical to what `extra.build` would have written.

    PYTHONPATH=. ~/.venvs/pb-research/bin/python -m research.two_leg_arms_v1.register_t10y3m
"""

from __future__ import annotations

import json
import urllib.request
from pathlib import Path

from research.batch_screen import extra

URL = extra.FRED_URL.format(sid="T10Y3M")
REL = "data/historical/two_leg_arms_v1_T10Y3M.csv"
KEY = "t10y3m"
SERIES = "T10Y3M"
MANIFEST = Path("research/two_leg_arms_v1/DATA_MANIFEST.json")


def main() -> int:
    payload = urllib.request.urlopen(URL, timeout=60).read()
    dates, vals = extra.parse_fred(payload)
    info = extra.write_series(REL, dates, {"value": vals})
    fins = [i for i, v in enumerate(vals) if v == v]
    entry = {
        "file": REL, "column": "value", "price": "level", "url": URL,
        "fetched": extra.date.today().isoformat(),
        "range": [str(dates[fins[0]]), str(dates[fins[-1]])],
        "rows": len(fins), "bytes": info["bytes"], "sha256": info["sha256"],
        "source": "FRED CSV",
    }
    reg = json.loads(Path(extra.REGISTRY_PATH).read_text())
    # the committed file keeps each branch's own insertion order (`json.dumps(indent=2)`, no
    # sorting), so the new key/series are appended rather than the whole file being rewritten
    reg["series"][SERIES] = entry
    reg["keys"][KEY] = SERIES
    Path(extra.REGISTRY_PATH).write_text(json.dumps(reg, indent=2) + "\n")
    extra.reset()
    MANIFEST.write_text(json.dumps({
        "study": "two_leg_arms_v1",
        "series": [{"key": KEY, "series": SERIES, "source": "FRED CSV", "url": URL,
                    "file": REL, "range": entry["range"], "rows": entry["rows"],
                    "sha256": entry["sha256"],
                    "used_by": "IB26 off-state curve condition (slope > 1.0 pp)"}],
        "note": "Registered through research/batch_screen/EXTRA_DATA.json (the framework's own "
                "extra-data mechanism); the existing `dgs10` key carries FRED DGS10 for arm B.",
    }, indent=1, sort_keys=True) + "\n")
    print(json.dumps(entry, indent=1, sort_keys=True))
    print("verify:", json.dumps(extra.verify([SERIES]), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
