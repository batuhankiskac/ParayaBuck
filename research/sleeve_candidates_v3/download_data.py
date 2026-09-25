#!/usr/bin/env python3
"""Download the frozen ETF inputs for the sleeve candidates v3 research campaign.

Research-only data acquisition, isolated from PAPER/LIVE state. Fetches SIP
daily split-adjusted bars and cash dividends from Alpaca market data using the
credentials in ``.env`` and writes them in the canonical-v5 CSV schemas that
``load_csv_bars`` / ``load_csv_corporate_actions`` accept, plus a manifest with
SHA-256 identities. Never places orders and never touches runtime state.

    .venv/bin/python research/sleeve_candidates_v3/download_data.py
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import sys
from datetime import UTC, date, datetime
from pathlib import Path

from alpaca.data.enums import Adjustment, CorporateActionsType, DataFeed
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.historical.corporate_actions import CorporateActionsClient
from alpaca.data.requests import CorporateActionsRequest, StockBarsRequest
from alpaca.data.timeframe import TimeFrame
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = ROOT / "data/historical"
BARS_PATH = OUT_DIR / "sleeve_candidates_v3_daily_split_sip.csv"
ACTIONS_PATH = OUT_DIR / "sleeve_candidates_v3_corporate_actions.csv"
MANIFEST_PATH = ROOT / "research/sleeve_candidates_v3/DATA_MANIFEST.json"

# Every sleeve reads this one campaign file so all candidates share one source.
SYMBOLS = (
    "SPY",
    "QQQ",
    "TLT",
    "IEF",
    "GLD",
    "IAU",
    "OUNZ",
    "DBC",
    "AGG",
    "RSP",
    "EUSA",
    "SCHD",
    "VIG",
    "DGRO",
    "BTAL",
    "XYLD",
    "QYLD",
    "USMV",
    "EFAV",
    "EEMV",
    "MTUM",
    "QUAL",
    "IJR",
    "IWM",
    "BIL",
    "SHV",
    "VGLT",
    "SHY",
    "VGSH",
)
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


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _iso(value: date | None) -> str:
    return "" if value is None else value.isoformat()


def download_bars(client: StockHistoricalDataClient) -> int:
    request = StockBarsRequest(
        symbol_or_symbols=list(SYMBOLS),
        timeframe=TimeFrame.Day,
        start=START,
        end=END,
        adjustment=Adjustment.SPLIT,
        feed=DataFeed.SIP,
    )
    bar_set = client.get_stock_bars(request)
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
    rows.sort(key=lambda row: (row[0], row[1]))
    with BARS_PATH.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(BAR_HEADER)
        writer.writerows(rows)
    return len(rows)


def download_dividends(client: CorporateActionsClient) -> int:
    rows = []
    for year in range(START.year, END.year + 1):
        request = CorporateActionsRequest(
            symbols=list(SYMBOLS),
            types=[CorporateActionsType.CASH_DIVIDEND],
            start=date(year, 1, 1),
            end=min(date(year, 12, 31), END.date()),
        )
        action_set = client.get_corporate_actions(request)
        for action in action_set.data.get("cash_dividends", []):
            if action.symbol not in SYMBOLS or action.ex_date > END.date():
                continue
            rows.append(
                (
                    "cash_dividends",
                    action.symbol,
                    _iso(action.ex_date),
                    _iso(action.record_date),
                    _iso(action.payable_date),
                    repr(float(action.rate)),
                    "",
                    "",
                    action.symbol,
                    action.symbol,
                    str(action.id),
                )
            )
    rows = sorted(set(rows), key=lambda row: (row[2], row[1], row[10]))
    with ACTIONS_PATH.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(ACTION_HEADER)
        writer.writerows(rows)
    return len(rows)


def main() -> int:
    load_dotenv(ROOT / ".env")
    key, secret = os.environ.get("ALPACA_API_KEY"), os.environ.get("ALPACA_SECRET_KEY")
    if not key or not secret:
        print("ALPACA_API_KEY / ALPACA_SECRET_KEY missing", file=sys.stderr)
        return 2
    bar_rows = download_bars(StockHistoricalDataClient(key, secret))
    action_rows = download_dividends(CorporateActionsClient(key, secret))
    manifest = {
        "campaign": "sleeve_candidates_v3",
        "downloaded_at": datetime.now(UTC).isoformat(),
        "provider": "alpaca",
        "feed": "sip",
        "adjustment": "split",
        "symbols": list(SYMBOLS),
        "start": START.isoformat(),
        "end": END.isoformat(),
        "bars": {
            "path": str(BARS_PATH.relative_to(ROOT)),
            "rows": bar_rows,
            "sha256": _sha256(BARS_PATH),
        },
        "corporate_actions": {
            "path": str(ACTIONS_PATH.relative_to(ROOT)),
            "rows": action_rows,
            "sha256": _sha256(ACTIONS_PATH),
        },
    }
    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(manifest, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
