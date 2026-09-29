#!/usr/bin/env python3
"""Download the extra ETF inputs for rule variants v1 (TLT, IEF, BIL, SGOV, GLD).

Research-only, read-only market-data access; never places orders and never
touches runtime state. Same source and conventions as
``research/profit_max_v1/download_data.py`` (tag archive/research/profit-max-v1-2026-09):
Alpaca SIP daily bars with split adjustment, cash dividends restated to
post-split shares (Alpaca rates are per share as paid; bars are split-adjusted).

    DOTENV=/path/to/.env .venv/bin/python research/rule_variants_v1/download_data.py
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import sys
from collections import defaultdict
from datetime import UTC, date, datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
DATA = HERE / "data"
BARS_PATH = DATA / "rule_variants_v1_daily_split_sip.csv"
ACTIONS_PATH = DATA / "rule_variants_v1_corporate_actions.csv"
MANIFEST_PATH = HERE / "DATA_MANIFEST.json"
ARCHIVE_TAG = "archive/research/profit-max-v1-2026-09"
ARCHIVE_FILES = {
    "data/historical/profit_max_v1_daily_split_sip.csv": "87e20c9e32f0e42f67f6e9bd4c4e51b5e06003277bd9b3a875e25df3cbf24b0e",
    "data/historical/profit_max_v1_corporate_actions.csv": "c0efd11fa4492d89a1ff997b8cc86ba600f98204197e177b63b82a9cc485fdcd",
}

SYMBOLS = ("GLD", "TLT", "IEF", "BIL", "SGOV")
START = datetime(2015, 1, 1, tzinfo=UTC)
END = datetime(2026, 7, 31, 23, 59, 59, tzinfo=UTC)
BAR_HEADER = ("timestamp", "symbol", "timeframe", "open", "high", "low", "close", "volume", "market")
ACTION_HEADER = (
    "action_type",
    "symbol",
    "ex_date",
    "record_date",
    "payable_date",
    "rate",
    "old_rate",
    "new_rate",
    "source_symbol",
    "target_symbol",
    "raw_id",
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def restate_to_split_basis(rate: float, symbol: str, ex_date: date, splits: list[tuple[str, date, float]]) -> float:
    """Express a per-share-as-paid dividend in post-split (split-adjusted bar) shares."""
    factor = 1.0
    for split_symbol, split_date, ratio in splits:
        if split_symbol == symbol and ex_date < split_date:
            factor *= ratio
    return rate / factor


def _iso(value: date | None) -> str:
    return "" if value is None else value.isoformat()


def yield_check(bars_path: Path, actions_path: Path) -> dict[str, dict[str, float]]:
    """Calendar-year dividend yield per symbol (sum of rates / year-end close)."""
    closes: dict[str, dict[str, float]] = defaultdict(dict)
    with bars_path.open() as handle:
        for row in csv.DictReader(handle):
            closes[row["symbol"]][row["timestamp"][:10]] = float(row["close"])
    paid: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    with actions_path.open() as handle:
        for row in csv.DictReader(handle):
            paid[row["symbol"]][row["ex_date"][:4]] += float(row["rate"])
    out: dict[str, dict[str, float]] = {}
    for symbol, by_year in paid.items():
        out[symbol] = {}
        for year, total in sorted(by_year.items()):
            days = [d for d in closes[symbol] if d.startswith(year)]
            if days:
                out[symbol][year] = round(total / closes[symbol][max(days)], 5)
    return out


def main() -> int:
    from alpaca.data.enums import Adjustment, CorporateActionsType, DataFeed
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.data.historical.corporate_actions import CorporateActionsClient
    from alpaca.data.requests import CorporateActionsRequest, StockBarsRequest
    from alpaca.data.timeframe import TimeFrame
    from dotenv import load_dotenv

    load_dotenv(os.environ.get("DOTENV", ROOT / ".env"))
    key, secret = os.environ.get("ALPACA_API_KEY"), os.environ.get("ALPACA_SECRET_KEY")
    if not key or not secret:
        print("ALPACA_API_KEY / ALPACA_SECRET_KEY missing", file=sys.stderr)
        return 2
    DATA.mkdir(parents=True, exist_ok=True)

    bar_set = StockHistoricalDataClient(key, secret).get_stock_bars(
        StockBarsRequest(
            symbol_or_symbols=list(SYMBOLS),
            timeframe=TimeFrame.Day,
            start=START,
            end=END,
            adjustment=Adjustment.SPLIT,
            feed=DataFeed.SIP,
        )
    )
    rows = []
    for symbol in SYMBOLS:
        bars = bar_set.data.get(symbol, [])
        if not bars:
            raise RuntimeError(f"No bars returned for {symbol}")
        for bar in bars:
            rows.append(
                (
                    bar.timestamp.astimezone(UTC).isoformat(),
                    symbol,
                    "1d",
                    repr(float(bar.open)),
                    repr(float(bar.high)),
                    repr(float(bar.low)),
                    repr(float(bar.close)),
                    repr(float(bar.volume)),
                    "equity",
                )
            )
    rows.sort(key=lambda r: (r[0], r[1]))
    with BARS_PATH.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(BAR_HEADER)
        writer.writerows(rows)

    client = CorporateActionsClient(key, secret)
    raw, splits = [], []
    for year in range(START.year, END.year + 1):
        window = dict(start=date(year, 1, 1), end=min(date(year, 12, 31), END.date()))
        divs = client.get_corporate_actions(
            CorporateActionsRequest(symbols=list(SYMBOLS), types=[CorporateActionsType.CASH_DIVIDEND], **window)
        )
        for a in divs.data.get("cash_dividends", []):
            if a.symbol in SYMBOLS and a.ex_date <= END.date():
                raw.append(a)
        sp = client.get_corporate_actions(
            CorporateActionsRequest(
                symbols=list(SYMBOLS),
                types=[CorporateActionsType.FORWARD_SPLIT, CorporateActionsType.REVERSE_SPLIT],
                **window,
            )
        )
        for kind in ("forward_splits", "reverse_splits"):
            for a in sp.data.get(kind, []):
                if a.symbol in SYMBOLS and a.ex_date <= END.date():
                    splits.append((a.symbol, a.ex_date, float(a.new_rate) / float(a.old_rate)))
    splits = sorted(set(splits))
    actions = sorted(
        {
            (
                "cash_dividends",
                a.symbol,
                _iso(a.ex_date),
                _iso(a.record_date),
                _iso(a.payable_date),
                repr(restate_to_split_basis(float(a.rate), a.symbol, a.ex_date, splits)),
                "",
                "",
                a.symbol,
                a.symbol,
                str(a.id),
            )
            for a in raw
        },
        key=lambda r: (r[2], r[1], r[10]),
    )
    with ACTIONS_PATH.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(ACTION_HEADER)
        writer.writerows(actions)

    first_bar = {s: min(r[0][:10] for r in rows if r[1] == s) for s in SYMBOLS}
    manifest = {
        "campaign": "rule_variants_v1",
        "downloaded_at": datetime.now(UTC).isoformat(),
        "provider": "alpaca",
        "feed": "sip",
        "adjustment": "split",
        "dividend_rates": "restated to post-split shares (rate / product of later split ratios)",
        "splits_applied": [[s, d.isoformat(), r] for s, d, r in splits],
        "symbols": list(SYMBOLS),
        "first_bar": first_bar,
        "start": START.isoformat(),
        "end": END.isoformat(),
        "bars": {"path": str(BARS_PATH.relative_to(ROOT)), "rows": len(rows), "sha256": sha256(BARS_PATH)},
        "corporate_actions": {
            "path": str(ACTIONS_PATH.relative_to(ROOT)),
            "rows": len(actions),
            "sha256": sha256(ACTIONS_PATH),
        },
        "annual_dividend_yield_check": yield_check(BARS_PATH, ACTIONS_PATH),
        "archived_inputs": {
            "tag": ARCHIVE_TAG,
            "local_copy_dir": "data/research/rule_variants_v1/",
            "files": ARCHIVE_FILES,
        },
    }
    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                k: manifest[k]
                for k in ("splits_applied", "first_bar", "bars", "corporate_actions", "annual_dividend_yield_check")
            },
            indent=1,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
