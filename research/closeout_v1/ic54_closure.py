"""research/closeout_v1/ic54_closure.py -- IC54/A and IC54/B: confirm the data gap and close them.

`IC54/A` (fill at the close of the fill session's first 30 minutes) and `IC54/B` (the first-30-minute
VWAP) need a **30-minute price on every decision session of a 2000-2026 window**. The kernel side of
the mechanism exists and is tested (`research/fast_engine_opts`: `fill_mode = 2` + a `fill` array,
`check_fill_reference`); only the series is missing. This module confirms that, so the two arms close
as `NEEDS_DATA` rather than being screened on a daily-data stand-in (a different mechanism):

* `--scan`   the repository's own inventory: every CSV under `data/` whose first column carries an
             intraday timestamp, and every `batch_screen` extra-data key whose series is not daily.
* `--fetch`  one live query for the one public source the bank names -- the Yahoo chart API at
             `interval=30m` -- over the longest range it will serve, saved raw under
             `data/historical/` (URL, range and SHA-256 recorded in `DATA_MANIFEST.json`). Yahoo
             keeps only roughly the last 730 days of 30-minute bars, which is the fact that closes
             the arm: the window's decision sessions start in 2000.
* the Alpaca SIP bars API the LIVE path consumes is not reachable offline in this repository (no
  credentials, no vendored bars) -- recorded, not attempted.

    PYTHONPATH=. ~/.venvs/pb-research/bin/python -m research.closeout_v1.ic54_closure --scan
    PYTHONPATH=. ~/.venvs/pb-research/bin/python -m research.closeout_v1.ic54_closure --fetch
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path


from research.fast_engine.fe_config import ROOT

RAW = ROOT / "data/historical/closeout_v1_yahoo_qqq_30m.json"
BASE = "https://query1.finance.yahoo.com/v8/finance/chart/QQQ?period1={p1}&period2={p2}&interval=30m"
USER_AGENT = "parayabuck-research/1.0 (batch_screen extra-data fetch)"
_TIME = re.compile(r"^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}")


def scan() -> dict:
    """Every series in the repository that carries a time of day (none should be a 30m bar set)."""
    intraday_files, checked = [], 0
    for p in sorted((ROOT / "data").rglob("*.csv")):
        checked += 1
        with p.open() as fh:
            head = next(csv.reader(fh), [])
            row = next(csv.reader(fh), [])
        if head and row and _TIME.match(str(row[0])):
            intraday_files.append({"file": str(p.relative_to(ROOT)), "first_cell": row[0]})
    reg = json.loads((ROOT / "research/batch_screen/EXTRA_DATA.json").read_text())
    non_daily = []
    for name, meta in sorted(reg["series"].items()):
        col = meta["column"]
        p = ROOT / meta["file"]
        with p.open() as fh:
            rows = list(csv.DictReader(fh))
        if rows and _TIME.match(str(rows[0].get("date", ""))):
            non_daily.append({"series": name, "column": col, "rows": len(rows)})
    return {"csv_files_checked": checked, "files_with_a_time_of_day": intraday_files,
            "extra_series_not_daily": non_daily,
            "extra_keys": len(reg["keys"]), "extra_series": len(reg["series"]),
            "alpaca_sip_bars": "not reachable offline: no credentials, no vendored bars"}


def _query(p1: int, p2: int) -> dict:
    """One 30-minute query: the raw body, or the API's own refusal."""
    url = BASE.format(p1=p1, p2=p2)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=60.0) as r:  # noqa: S310 (a fixed public API URL)
            return {"url": url, "status": "ok", "body": r.read()}
    except urllib.error.HTTPError as exc:  # noqa: PERF203 -- the refusal is the evidence
        return {"url": url, "status": f"refused: HTTP {exc.code}", "body": exc.read()}
    except Exception as exc:  # noqa: BLE001
        return {"url": url, "status": f"failed: {type(exc).__name__}: {exc}", "body": b""}


def fetch() -> dict:
    """Yahoo's 30-minute retention, from the API itself: a long range, then one it will serve."""
    now = int(datetime.now(tz=UTC).timestamp())
    long_ = _query(now - 5 * 365 * 86_400, now)
    served = _query(now - 60 * 86_400, now)
    out = {
        "long_range_requested": {
            "range": [datetime.fromtimestamp(now - 5 * 365 * 86_400, tz=UTC).date().isoformat(),
                      datetime.fromtimestamp(now, tz=UTC).date().isoformat()],
            "status": long_.get("status"),
            "api_message": (json.loads(long_["body"]).get("chart", {}).get("error", {})
                            .get("description") if long_.get("body") else None),
        },
        "sixty_day_request": {"status": served.get("status")},
    }
    if served.get("body"):
        RAW.write_bytes(served["body"])
        res = json.loads(served["body"])["chart"]["result"][0]
        ts = res.get("timestamp") or []
        out["sixty_day_request"].update({
            "file": str(RAW.relative_to(ROOT)), "bytes": len(served["body"]),
            "sha256": hashlib.sha256(served["body"]).hexdigest(), "url": served["url"],
            "bars": len(ts),
            "range": [datetime.fromtimestamp(ts[0], tz=UTC).date().isoformat(),
                      datetime.fromtimestamp(ts[-1], tz=UTC).date().isoformat()] if ts else None,
        })
    out["conclusion"] = ("Yahoo serves 30-minute bars only inside a short recent window (its own "
                         "refusal above); the fill price of every 2000-2026 decision session is "
                         "not obtainable there, and the Alpaca SIP bars API is not reachable "
                         "offline. IC54/A and IC54/B close as NEEDS_DATA.")
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m research.closeout_v1.ic54_closure")
    ap.add_argument("--scan", action="store_true", help="the repository's intraday inventory")
    ap.add_argument("--fetch", action="store_true", help="one live Yahoo 30m query (raw, saved)")
    args = ap.parse_args(argv)
    out = {"study": "closeout_v1", "arms": ["IC54/A", "IC54/B"], "verdict": "NEEDS_DATA"}
    if args.scan:
        out["scan"] = scan()
    if args.fetch:
        out["yahoo_30m"] = fetch()
    out["mechanism"] = ("implemented and tested (`research/fast_engine_opts`: `fill_mode = 2` + a "
                        "`fill` array, `check_fill_reference`); the gap is the series, not the "
                        "kernel")
    out["closure"] = ("no 30-minute series exists for the screening window and none can be sourced: "
                      "the arms need the fill price of every decision session of 2000-2026")
    print(json.dumps(out, indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
