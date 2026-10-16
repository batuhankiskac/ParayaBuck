"""research/plugins_c1/build_extra.py -- the extra series this plugin batch needs.

One series, from FRED CSV: **DFF** (effective federal funds rate, daily, business days), the input
of IC16 (`data.extra("dff", ffill=True)`).  It is registered in the framework's
`research/batch_screen/EXTRA_DATA.json` as series `DFF` / key `dff` -- the same shape as the
existing FRED series there (`date,value`, percent, `price: "level"`).

IC21's breakeven series (`t10yie`) and every other input of these eight ideas is already
registered by `batch_screen_v1`, so nothing else is fetched.

Run from the worktree root with the research venv:

    PYTHONPATH=. ~/.venvs/pb-research/bin/python -m research.plugins_c1.build_extra

It fetches DFF from FRED, writes `data/historical/batch_screen_v1_dff.csv`, splices the series and
key entries into `EXTRA_DATA.json` (**byte-identical everywhere else**: the file is edited
textually, in sorted position, never re-serialised, so two plugin branches touching it do not fight
over indentation), writes `research/plugins_c1/DATA_MANIFEST.json` with the source URL, the date
range, the row count and the SHA-256, then runs the framework's own `extra.verify()`.  `--offline`
re-verifies the committed file against the committed registry entry instead of fetching.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import numpy as np

from research.batch_screen import extra

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = Path(__file__).resolve().parent / "DATA_MANIFEST.json"
TODAY = "2026-10-16"

#: (FRED series id, plugin-facing key, file slug)
SERIES = (("DFF", "dff", "dff"),)

ROLES = {"DFF": "IC16 (Fed-funds tightening-cycle veto): the 126-session change of the daily "
                "effective federal funds rate"}

_ENTRY = re.compile(r'^    "([^"]+)": ')


def declaration(dates, cols, url: str, rel: str, info: dict) -> dict:
    """The registry declaration of one fetched series (the shape `extra.verify` re-reads)."""
    vals = np.asarray(cols["value"], dtype=np.float64)
    have = np.isfinite(vals)
    first, last = int(np.argmax(have)), len(dates) - 1 - int(np.argmax(have[::-1]))
    return {
        "file": rel,
        "column": "value",
        "price": "level",
        "url": url,
        "fetched": TODAY,
        "source": "FRED CSV",
        "range": [dates[first], dates[last]],
        "rows": int(have.sum()),
        "bytes": info["bytes"],
        "sha256": info["sha256"],
    }


def _key_line(key: str, series: str) -> str:
    return f'    "{key}": "{series}",\n'


def _series_lines(name: str, payload: dict) -> list:
    """One series entry, as the registry's own text (`indent=2`, re-indented to 4 spaces, comma)."""
    body = json.dumps({name: payload}, indent=2).splitlines()[1:-1]
    out = ["  " + b + "\n" for b in body]
    out[-1] = out[-1].rstrip("\n") + ",\n"
    return out


def _place(lines: list, start: int, name: str, where: str) -> int:
    """The line index a new `    "name": ...` entry of the block starting at `start` goes to."""
    for i in range(start, len(lines)):
        if lines[i] == "  },\n":  # the block's closing brace
            break
        m = _ENTRY.match(lines[i])
        if m and m.group(1) > name:
            return i
    raise SystemExit(f"cannot place {where} {name!r}: the block is not sorted (or is empty)")


def splice(reg_path: Path, additions: dict, keys: dict) -> list:
    """Insert `additions` (series -> declaration) and `keys` (key -> series), in sorted position.

    Only whole new lines are added: every existing byte of the registry is preserved, and a name
    already present with the same declaration is left alone (so an idempotent rebuild is a no-op).
    """
    reg = json.loads(reg_path.read_text())
    lines = reg_path.read_text().splitlines(keepends=True)
    added = []

    for block, names, render in (("keys", {k: keys[k] for k in sorted(keys)},
                                  lambda k: [_key_line(k, keys[k])]),
                                 ("series", {n: additions[n] for n in sorted(additions)},
                                  lambda n: _series_lines(n, additions[n]))):
        start = next(i for i, ln in enumerate(lines) if ln.startswith(f'  "{block}": {{'))
        for name in names:
            if reg[block].get(name) == names[name]:
                continue
            if name in reg[block]:
                raise SystemExit(f"registry conflict: {block} {name!r} is declared differently")
            pos = _place(lines, start + 1, name, block[:-1] if block == "keys" else "series")
            lines[pos:pos] = render(name)
            reg[block][name] = names[name]
            added.append(f"{block} {name}")

    if added:
        reg_path.write_text("".join(lines))
        extra.reset()
    return added


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m research.plugins_c1.build_extra")
    ap.add_argument("--offline", action="store_true",
                    help="no network: keep the committed file and re-verify the registry entry")
    args = ap.parse_args(argv)

    reg_path = extra.REGISTRY_PATH
    reg = json.loads(reg_path.read_text())
    series, keys, manifest = {}, {}, {"study": "plugins_c1", "built": TODAY, "series": {}}

    for fid, key, slug in SERIES:
        rel = f"data/historical/batch_screen_v1_{slug}.csv"
        prev = (reg.get("series") or {}).get(fid)
        if args.offline or (prev and extra.sha256(ROOT / prev["file"]) == prev["sha256"]):
            if not prev:
                raise SystemExit(f"--offline cannot declare {fid}: no verified registry entry")
            if extra.sha256(ROOT / prev["file"]) != prev["sha256"]:
                raise SystemExit(f"{fid}: the committed file does not match its SHA-256")
            series[fid] = dict(prev)
            print(f"  {fid:4s} {series[fid]['rows']:6d} rows  {series[fid]['range'][0]} -> "
                  f"{series[fid]['range'][1]}  (committed file, SHA-256 verified)")
        else:
            dates, cols, url = extra.fetch_fred(fid)
            info = extra.write_series(rel, dates, cols, root=ROOT)
            series[fid] = declaration(dates, cols, url, rel, info)
            print(f"  {fid:4s} {series[fid]['rows']:6d} rows  {series[fid]['range'][0]} -> "
                  f"{series[fid]['range'][1]}  -> {rel}")
        keys[key] = fid
        manifest["series"][fid] = dict(series[fid], role=ROLES.get(fid, ""))

    print(f"  registry: {splice(reg_path, series, keys) or 'nothing to add'} "
          f"({len(json.loads(reg_path.read_text())['series'])} series)")

    v = extra.verify(names=sorted(series))
    print(f"  verify {'PASS' if v['ok'] else 'FAIL'} ({len(series)} series; bad: {v['bad']})")
    manifest["verify"] = {"ok": bool(v["ok"]),
                          "series": {n: v["series"][n] for n in sorted(v["series"])}}
    manifest["registry"] = {"file": "research/batch_screen/EXTRA_DATA.json",
                            "series_added": sorted(series), "keys_added": sorted(keys)}
    MANIFEST.write_text(json.dumps(manifest, indent=1, sort_keys=True) + "\n")
    print(f"  manifest -> {MANIFEST.relative_to(ROOT)}")
    return 0 if v["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
