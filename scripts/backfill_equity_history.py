#!/usr/bin/env python3
"""Incremental equity history backfill using the production Alpaca pipeline.

Extends the cached historical universe backward from the existing CSV's start
(2019-05-28) to the earliest date the account's data feed serves daily bars
(probe-verified: 2016-01-04 for SPY; no bars before 2016).

The symbol list is frozen to the 1,194 symbols already present in
data/historical/equity_1200_2019_2026.csv so the extended dataset is a pure
incremental backward extension of the same (survivorship-limited) universe.
Survivorship limitation: symbols that delisted before 2019-05-28 were never in
the original CSV and are NOT in this backfill; the limitation is documented,
not solved.

Usage
-----
  python scripts/backfill_equity_history.py \
      --start 2016-01-01 --end 2019-05-27 \
      --output data/historical/equity_1200_2016_2019.csv

Writes the raw OHLCV CSV (same schema as the existing file, adjustment=splits)
and a sidecar JSON with identity + hash. No trading, no PAPER/LIVE writes.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

from dotenv import load_dotenv

REPO = Path(__file__).resolve().parents[1]
SOURCE_CSV = REPO / "data" / "historical" / "equity_1200_2019_2026.csv"


def _symbols_from_csv(path: Path) -> list[str]:
    symbols: set[str] = set()
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            symbols.add(str(row["symbol"]))
    return sorted(symbols)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", default="2016-01-01")
    parser.add_argument("--end", default="2019-05-27")
    parser.add_argument(
        "--output",
        default=str(REPO / "data" / "historical" / "equity_1200_2016_2019.csv"),
    )
    parser.add_argument("--symbols-file", default=str(SOURCE_CSV))
    parser.add_argument("--limit-symbols", type=int, default=None, help="debug: first N symbols")
    args = parser.parse_args(argv)
    load_dotenv(REPO / ".env")
    key = os.getenv("ALPACA_API_KEY", "")
    secret = os.getenv("ALPACA_SECRET_KEY", "")
    if not key or not secret:
        raise SystemExit("ALPACA_API_KEY and ALPACA_SECRET_KEY are required")
    sys.path.insert(0, str(REPO / "src"))
    from parayabuck.data.alpaca import AlpacaHistoricalData

    symbols = _symbols_from_csv(Path(args.symbols_file))
    if args.limit_symbols:
        symbols = symbols[: args.limit_symbols]
    start = datetime.fromisoformat(args.start).replace(tzinfo=UTC)
    end = datetime.fromisoformat(args.end).replace(tzinfo=UTC)
    print(
        f"backfill {len(symbols)} symbols {start.date()}..{end.date()} "
        f"(feed: configured sip, iex fallback)"
    )
    provider = AlpacaHistoricalData(key, secret, stock_feed="sip", data_delayed=False)
    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    symbols_with_data = 0
    empty = 0
    with out_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "timestamp",
                "symbol",
                "timeframe",
                "open",
                "high",
                "low",
                "close",
                "volume",
                "market",
            ],
        )
        writer.writeheader()
        batch_size = 100
        for offset in range(0, len(symbols), batch_size):
            batch = symbols[offset : offset + batch_size]
            downloaded = provider.get_bars_batch(
                batch,
                "1d",
                start=start,
                end=end,
                adjustment="splits",
            )
            for symbol in batch:
                bars = downloaded.get(symbol, ())
                if not bars:
                    empty += 1
                    continue
                symbols_with_data += 1
                for bar in sorted(bars, key=lambda b: b.timestamp):
                    writer.writerow(
                        {
                            "timestamp": bar.timestamp.isoformat(),
                            "symbol": bar.symbol,
                            "timeframe": bar.timeframe,
                            "open": bar.open,
                            "high": bar.high,
                            "low": bar.low,
                            "close": bar.close,
                            "volume": bar.volume,
                            "market": bar.market.value,
                        }
                    )
                    written += 1
            if (offset + batch_size) % 400 == 0 or offset + batch_size >= len(symbols):
                print(
                    f"  {min(offset + batch_size, len(symbols))}/{len(symbols)} "
                    f"symbols, {written} rows"
                )
    digest = hashlib.sha256(out_path.read_bytes()).hexdigest()
    first = last = None
    with out_path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)
        if rows:
            first = rows[0]["timestamp"]
            last = rows[-1]["timestamp"]
    meta = {
        "path": str(out_path),
        "start": first,
        "end": last,
        "symbols_requested": len(symbols),
        "symbols_with_data": symbols_with_data,
        "symbols_empty": empty,
        "bar_count": written,
        "sha256": digest,
        "feed": "sip (iex fallback)",
        "adjustment": "splits",
        "survivorship_limitation": (
            "symbol list frozen to the existing 2019-2026 CSV membership; "
            "delisted names are absent (known limitation, not solved)"
        ),
    }
    (out_path.with_suffix(".json")).write_text(json.dumps(meta, indent=2))
    print(json.dumps(meta, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
