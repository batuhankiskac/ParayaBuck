"""Fetch the ALFRED NFCI vintages and build the real-time state series (ic12_confirm_v1).

    python -m research.ic12_confirm_v1.fetch_vintages [--verify]

Downloads:
* the NFCI vintage calendar from ALFRED's download form
  (`https://alfred.stlouisfed.org/series/downloaddata?seid=NFCI`);
* each vintage's full series from
  `https://alfred.stlouisfed.org/graph/alfredgraph.csv?id=NFCI&vintage_date=<d>`;
* the current (revised) NFCI series from FRED, cross-checked against the committed
  `data/historical/plugins_c2_nfci.csv`.

Writes (data/historical):
* `ic12_confirm_v1_nfci_vintages.csv`   vintage_date,date,value  (the trailing KEEP observations of
                                        every vintage -- enough to recompute its state exactly)
* `ic12_confirm_v1_nfci_vintage_dates.csv`  vintage_date,rows,first_obs,last_obs,sha256
* `ic12_confirm_v1_nfci_live.csv`       date,value  (today's revised series)

and `research/ic12_confirm_v1/DATA_MANIFEST.json` with URL, range, rows and SHA-256 of every input.

`--verify` re-hashes the committed files against the manifest and re-downloads nothing.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import re
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HIST = ROOT / "data" / "historical"
HERE = Path(__file__).resolve().parent

CALENDAR_URL = "https://alfred.stlouisfed.org/series/downloaddata?seid=NFCI"
VINTAGE_URL = "https://alfred.stlouisfed.org/graph/alfredgraph.csv?id=NFCI&vintage_date={d}"
FRED_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=NFCI"
UA = "Mozilla/5.0 (research; parayabuck ic12_confirm_v1)"

VINTAGE_CSV = HIST / "ic12_confirm_v1_nfci_vintages.csv"
DATES_CSV = HIST / "ic12_confirm_v1_nfci_vintage_dates.csv"
LIVE_CSV = HIST / "ic12_confirm_v1_nfci_live.csv"
MANIFEST = HERE / "DATA_MANIFEST.json"

#: observations kept per vintage: the rule needs 1 + 4 + 156 = 161; 210 leaves margin.
KEEP = 210
WORKERS = 6
RETRIES = 4


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def get(url: str, tries: int = RETRIES) -> bytes:
    last = None
    for k in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.read()
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as exc:
            last = exc
            time.sleep(1.0 * (2**k))
    raise RuntimeError(f"GET {url} failed after {tries} tries: {last}")


def parse_series(raw: bytes, url: str) -> list[tuple[str, float]]:
    """`(observation_date, value)` of an ALFRED/FRED graph CSV (the second column)."""
    text = raw.decode("utf-8", "replace")
    if text.lstrip().startswith("<"):
        raise RuntimeError(f"{url} returned HTML, not a CSV")
    rd = csv.reader(io.StringIO(text))
    head = next(rd)
    if len(head) < 2:
        raise RuntimeError(f"{url}: unexpected header {head!r}")
    out = []
    for row in rd:
        if len(row) < 2 or not row[0] or row[1] in ("", "."):
            continue
        out.append((row[0], float(row[1])))
    if not out:
        raise RuntimeError(f"{url}: no observations")
    out.sort()
    return out


def vintage_calendar() -> tuple[list[str], bytes]:
    raw = get(CALENDAR_URL)
    dates = sorted(set(re.findall(r'<option value="(\d{4}-\d\d-\d\d)">', raw.decode("utf-8", "replace"))))
    if len(dates) < 700:
        raise RuntimeError(f"only {len(dates)} vintage dates parsed from {CALENDAR_URL}")
    return dates, raw


def fetch_vintages(dates: list[str]) -> tuple[dict, dict]:
    """`({vintage_date: [(obs_date, value)]}, {vintage_date: sha256 of the raw response})`."""
    done: dict[str, list[tuple[str, float]]] = {}
    raw_hash: dict[str, str] = {}
    errors: dict[str, str] = {}

    def one(d: str):
        url = VINTAGE_URL.format(d=d)
        try:
            raw = get(url)
            return d, (parse_series(raw, url), sha256_bytes(raw))
        except Exception as exc:  # noqa: BLE001  (reported, not swallowed)
            return d, exc

    t0 = time.time()
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        for k, (d, val) in enumerate(ex.map(one, dates), 1):
            if isinstance(val, Exception):
                errors[d] = str(val)
            else:
                done[d], raw_hash[d] = val
            if k % 100 == 0:
                print(f"  {k}/{len(dates)} vintages fetched ({time.time() - t0:.0f}s)", flush=True)
    if errors:
        raise RuntimeError(f"{len(errors)} vintages failed, e.g. {list(errors.items())[:3]}")
    return done, raw_hash


def write_vintage_panel(series: dict[str, list[tuple[str, float]]]) -> None:
    """The vintage panel, trailing `KEEP` observations per vintage."""
    with VINTAGE_CSV.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["vintage_date", "date", "value"])
        for v in sorted(series):
            for d, x in series[v][-KEEP:]:
                w.writerow([v, d, repr(float(x))])


def write_date_table(series: dict[str, list[tuple[str, float]]], raw_hash: dict[str, str]) -> None:
    with DATES_CSV.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["vintage_date", "rows", "first_obs", "last_obs", "sha256_raw"])
        for v in sorted(series):
            rows = series[v]
            w.writerow([v, len(rows), rows[0][0], rows[-1][0], raw_hash[v]])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--verify", action="store_true", help="re-hash the committed files only")
    args = ap.parse_args()

    if args.verify:
        man = json.loads(MANIFEST.read_text())
        bad = 0
        for rel, rec in sorted(man["files"].items()):
            p = ROOT / rel
            got = sha256_bytes(p.read_bytes()) if p.exists() else None
            ok = got == rec["sha256"]
            bad += not ok
            print(f"  {'OK ' if ok else 'BAD'} {rel} {rec['sha256'][:16]} …")
        print(f"verify: {len(man['files'])} files, {bad} mismatches")
        return 1 if bad else 0

    print("1/4 vintage calendar", flush=True)
    dates, cal_raw = vintage_calendar()
    print(f"     {len(dates)} vintages, {dates[0]} .. {dates[-1]}", flush=True)

    print("2/4 vintage series", flush=True)
    series, raw_hash = fetch_vintages(dates)
    write_vintage_panel(series)
    write_date_table(series, raw_hash)

    print("3/4 current revised series (cross-check)", flush=True)
    live_raw = get(FRED_URL)
    live = parse_series(live_raw, FRED_URL)
    with LIVE_CSV.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["date", "value"])
        for d, x in live:
            w.writerow([d, repr(float(x))])
    committed = HIST / "plugins_c2_nfci.csv"
    same = sha256_bytes(committed.read_bytes()) == sha256_bytes(LIVE_CSV.read_bytes())
    print(f"     live {live[0][0]} .. {live[-1][0]} ({len(live)} rows); "
          f"byte-identical to plugins_c2_nfci.csv: {same}", flush=True)

    print("4/4 manifest", flush=True)
    files = {}
    for p in (VINTAGE_CSV, DATES_CSV, LIVE_CSV, committed):
        b = p.read_bytes()
        files[str(p.relative_to(ROOT))] = {
            "sha256": sha256_bytes(b), "bytes": len(b),
            "rows": sum(1 for _ in p.open()) - 1,
        }
    man = {
        "campaign": "ic12_confirm_v1",
        "note": ("NFCI ALFRED vintages for the IC12 confirmation study. ALFRED's NFCI archive "
                 "begins 2011-05-25 (every earlier vintage_date returns HTTP 404); FRED's own graph "
                 "CSV ignores vintage_date, so ALFRED is the only source. The raw per-vintage "
                 "response bytes are not committed (800 files); each vintage's SHA-256 of the raw "
                 "ALFRED response is in the `sha256_raw` column of "
                 "ic12_confirm_v1_nfci_vintage_dates.csv, next to the row count and observation "
                 "range that response parsed to."),
        "extra_data": {
            "nfci_vintages": {
                "file": "data/historical/ic12_confirm_v1_nfci_vintages.csv",
                "source": "ALFRED CSV (per vintage)",
                "url": VINTAGE_URL.format(d="<vintage_date>"),
                "vintage_calendar_url": CALENDAR_URL,
                "vintage_calendar_sha256": sha256_bytes(cal_raw),
                "n_vintages": len(dates),
                "range": [dates[0], dates[-1]],
                "keep_per_vintage": KEEP,
                "rows": files["data/historical/ic12_confirm_v1_nfci_vintages.csv"]["rows"],
                "sha256": files["data/historical/ic12_confirm_v1_nfci_vintages.csv"]["sha256"],
                "fetched": time.strftime("%Y-%m-%d"),
            },
            "nfci_vintage_dates": {
                "file": "data/historical/ic12_confirm_v1_nfci_vintage_dates.csv",
                "source": "ALFRED vintage calendar",
                "url": CALENDAR_URL,
                "range": [dates[0], dates[-1]],
                "rows": files["data/historical/ic12_confirm_v1_nfci_vintage_dates.csv"]["rows"],
                "sha256": files["data/historical/ic12_confirm_v1_nfci_vintage_dates.csv"]["sha256"],
                "fetched": time.strftime("%Y-%m-%d"),
            },
            "nfci_live": {
                "file": "data/historical/ic12_confirm_v1_nfci_live.csv",
                "source": "FRED CSV",
                "url": FRED_URL,
                "range": [live[0][0], live[-1][0]],
                "rows": len(live),
                "sha256": files["data/historical/ic12_confirm_v1_nfci_live.csv"]["sha256"],
                "fetched": time.strftime("%Y-%m-%d"),
                "equals_plugins_c2_nfci": bool(same),
            },
            "nfci_committed": {
                "file": "data/historical/plugins_c2_nfci.csv",
                "source": "plugins_c2 (already committed)",
                "url": FRED_URL,
                "rows": files["data/historical/plugins_c2_nfci.csv"]["rows"],
                "sha256": files["data/historical/plugins_c2_nfci.csv"]["sha256"],
            },
        },
        "files": files,
        "keys": {
            "prefix": "ic12_confirm_v1_",
            "other_inputs": ("market/tax/FX/YI-UFE/instrument files are the campaign's committed "
                             "ones, hashed in research/batch_screen/DATA_MANIFEST.json"),
        },
    }
    MANIFEST.write_text(json.dumps(man, indent=1, sort_keys=True) + "\n")
    print(f"     wrote {MANIFEST.relative_to(ROOT)}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
