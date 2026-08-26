#!/usr/bin/env python3
# ruff: noqa: E501
"""Data acquisition for optimization experiments (2026-08).

Produces (NO project code touched):
  data/historical/equity_1200_dividends.csv   cash dividends (ex-date) for every
                                              symbol in the equity research CSV
  data/historical/crypto_expanded_1h.csv      original 18 pairs with 1h gaps
                                              backfilled + additional liquid
                                              Alpaca USD pairs, full window

Usage: fetch_data.py dividends|crypto|all
"""

from __future__ import annotations

import os
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

REPO = Path("/Users/batuhankiskac/Desktop/ParayaBuck")
sys.path.insert(0, str(REPO))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(str(REPO / ".env"))

import pandas as pd  # noqa: E402
from alpaca.data.historical import CryptoHistoricalDataClient  # noqa: E402
from alpaca.data.historical.corporate_actions import (  # noqa: E402
    CorporateActionsClient,
    CorporateActionsRequest,
)
from alpaca.data.requests import CryptoBarsRequest  # noqa: E402
from alpaca.data.timeframe import TimeFrame  # noqa: E402

OUT_DIR = REPO / "data" / "historical"
EQUITY_CSV = OUT_DIR / "equity_1200_2016_2026.csv"
DIV_CSV = OUT_DIR / "equity_1200_dividends.csv"
CRYPTO_BASE = OUT_DIR / "crypto_18_1h_extended.csv"
CRYPTO_OUT = OUT_DIR / "crypto_expanded_1h.csv"

CR_START = datetime(2021, 1, 1, tzinfo=UTC)
CR_END = datetime(2026, 7, 31, 23, 59, 59, tzinfo=UTC)
DIV_START = datetime(2016, 1, 1, tzinfo=UTC)
DIV_END = datetime(2026, 7, 31, tzinfo=UTC)

NEW_PAIRS = [
    "TRX/USD", "SHIB/USD", "ATOM/USD", "XLM/USD", "ETC/USD", "NEAR/USD",
    "APT/USD", "OP/USD", "SUI/USD", "INJ/USD", "FET/USD", "GRT/USD",
    "SAND/USD", "MANA/USD", "AXS/USD", "EOS/USD", "IMX/USD", "GALA/USD",
    "APE/USD", "CRV/USD", "LDO/USD", "MKR/USD", "STX/USD", "HBAR/USD",
    "TIA/USD", "SEI/USD", "RUNE/USD", "PEPE/USD",
]


def fetch_dividends() -> None:
    symbols = sorted(pd.read_csv(EQUITY_CSV, usecols=["symbol"])["symbol"].unique())
    print(f"{len(symbols)} equity symbols", flush=True)
    client = CorporateActionsClient(os.environ["ALPACA_API_KEY"], os.environ["ALPACA_SECRET_KEY"])
    frames = []
    for index, symbol in enumerate(symbols):
        for attempt in range(4):
            try:
                resp = client.get_corporate_actions(
                    CorporateActionsRequest(
                        symbols=[symbol], types=["cash_dividend"],
                        start=DIV_START.date().isoformat(), end=DIV_END.date().isoformat(),
                    )
                )
                break
            except Exception as exc:  # noqa: BLE001
                print(f"  {symbol}: retry {attempt}: {exc}", flush=True)
                time.sleep(15 * (attempt + 1))
        else:
            continue
        frame = resp.df.reset_index()
        if len(frame):
            frame["symbol"] = symbol
            frames.append(frame)
        if (index + 1) % 100 == 0:
            print(f"  {index + 1}/{len(symbols)} symbols", flush=True)
        time.sleep(0.35)
    if not frames:
        raise SystemExit("no dividend data fetched")
    merged = pd.concat(frames, ignore_index=True)
    # ex_date semantics: holder as of ex-date open receives the dividend.
    merged["ex_date"] = pd.to_datetime(merged["ex_date"])
    merged = merged.dropna(subset=["ex_date", "rate"])
    out = pd.DataFrame(
        {
            "timestamp": merged["ex_date"].dt.strftime("%Y-%m-%dT00:00:00Z"),
            "symbol": merged["symbol"],
            "action_type": "dividend",
            "value": merged["rate"].astype(float),
        }
    ).sort_values(["timestamp", "symbol"])
    out.to_csv(DIV_CSV, index=False)
    print(f"wrote {DIV_CSV} ({len(out)} dividends, {out['symbol'].nunique()} symbols)", flush=True)


def _fetch_pair_bars(client: CryptoHistoricalDataClient, pair: str,
                     ranges: list[tuple[datetime, datetime]]) -> pd.DataFrame:
    frames = []
    for start, end in ranges:
        request = CryptoBarsRequest(
            symbol_or_symbols=pair, timeframe=TimeFrame.Hour, start=start, end=end
        )
        frame = client.get_crypto_bars(request).df
        if len(frame):
            frame = frame.reset_index()
            if "symbol" not in frame.columns:
                frame["symbol"] = pair
            frames.append(frame)
        time.sleep(0.35)
    if not frames:
        return pd.DataFrame()
    merged = pd.concat(frames, ignore_index=True)
    merged["symbol"] = pair
    return merged


def fetch_crypto() -> None:
    base = pd.read_csv(CRYPTO_BASE)
    base["timestamp"] = pd.to_datetime(base["timestamp"], utc=True, format="ISO8601")
    existing = sorted(base["symbol"].unique())
    print(f"base pairs: {len(existing)}; rows: {len(base):,}", flush=True)

    client = CryptoHistoricalDataClient()

    # --- gap detection within each existing pair's observed span -------------
    backfill_frames = []
    for symbol in existing:
        series = base.loc[base["symbol"] == symbol, "timestamp"].sort_values()
        span_start, span_end = series.iloc[0], series.iloc[-1]
        gaps = series.diff()
        hole_indices = gaps[gaps > timedelta(hours=1)].index
        ranges: list[tuple[datetime, datetime]] = []
        for hole_index in hole_indices:
            gap_start = base.loc[hole_index - 1, "timestamp"] + timedelta(hours=1)
            gap_end = base.loc[hole_index, "timestamp"] - timedelta(hours=1)
            gap_start = max(gap_start, CR_START)
            gap_end = min(gap_end, CR_END)
            if gap_start <= gap_end:
                ranges.append((gap_start.to_pydatetime(), gap_end.to_pydatetime()))
        # leading hole: dataset window starts 2021-01-01; if the pair's first
        # bar is later than that AND the pair was actually listed, Alpaca will
        # simply return nothing before listing — safe to probe.
        if span_start > pd.Timestamp(CR_START):
            ranges.append((CR_START, span_start.to_pydatetime() - timedelta(hours=1)))
        total_hours = sum(int((end - start).total_seconds() // 3600) + 1 for start, end in ranges)
        if not ranges:
            continue
        print(f"  {symbol}: {len(ranges)} gap range(s), ~{total_hours:,}h to fetch", flush=True)
        frame = _fetch_pair_bars(client, symbol, ranges)
        if len(frame):
            backfill_frames.append(frame)
            print(f"    fetched {len(frame):,} bars", flush=True)

    # --- additional pairs ----------------------------------------------------
    new_frames = []
    kept_pairs = []
    for pair in NEW_PAIRS:
        frame = _fetch_pair_bars(client, pair, [(CR_START, CR_END)])
        if len(frame) < 5000:  # ignore pairs with almost no coverage
            print(f"  {pair}: skipped ({len(frame)} rows)", flush=True)
            continue
        new_frames.append(frame)
        kept_pairs.append(pair)
        print(f"  {pair}: {len(frame):,} bars "
              f"({frame['timestamp'].min()} .. {frame['timestamp'].max()})", flush=True)

    columns = ["timestamp", "symbol", "open", "high", "low", "close", "volume"]
    parts = [base[columns]]
    for frame in backfill_frames + new_frames:
        frame = frame.copy()
        frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True)
        if "trade_count" in frame.columns:
            frame = frame.drop(columns=[c for c in ("trade_count", "vwap") if c in frame.columns])
        parts.append(frame[columns])
    merged = pd.concat(parts, ignore_index=True)
    merged = merged.sort_values(["timestamp", "symbol"]).drop_duplicates(
        subset=["timestamp", "symbol"], keep="first"
    )
    merged["timestamp"] = merged["timestamp"].dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    merged.to_csv(CRYPTO_OUT, index=False)
    print(f"wrote {CRYPTO_OUT}: {len(merged):,} rows, "
          f"{merged['symbol'].nunique()} pairs (kept new: {kept_pairs})", flush=True)


if __name__ == "__main__":
    task = sys.argv[1] if len(sys.argv) > 1 else "all"
    if task in ("dividends", "all"):
        fetch_dividends()
    if task in ("crypto", "all"):
        fetch_crypto()
