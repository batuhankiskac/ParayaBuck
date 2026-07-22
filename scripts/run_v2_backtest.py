#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from datetime import UTC

import pandas as pd

from parayabuck_v2.backtest import BacktestEngine
from parayabuck_v2.config import load_v2_config
from parayabuck_v2.core.models import Bar, Market


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a ParayaBuck v2 CSV backtest")
    parser.add_argument("csv")
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--market", choices=("equity", "crypto"), default="equity")
    parser.add_argument("--timeframe", default="5m")
    parser.add_argument("--starting-equity", type=float, default=10_000)
    parser.add_argument("--slippage-bps", type=float, default=2.0)
    args = parser.parse_args()

    frame = pd.read_csv(args.csv)
    timestamps = pd.to_datetime(frame["timestamp"], utc=True)
    market = Market(args.market)
    bars = [
        Bar(
            timestamp=timestamp.to_pydatetime().astimezone(UTC),
            symbol=args.symbol,
            timeframe=args.timeframe,
            open=float(row.open),
            high=float(row.high),
            low=float(row.low),
            close=float(row.close),
            volume=float(row.volume),
            market=market,
        )
        for timestamp, row in zip(timestamps, frame.itertuples(index=False), strict=True)
    ]
    config = load_v2_config()
    engine = BacktestEngine(
        config,
        starting_equity=args.starting_equity,
        slippage_bps=args.slippage_bps,
    )
    result = engine.run(bars)
    payload = asdict(result)
    payload.pop("equity_curve")
    payload.pop("realized_pnls")
    print(json.dumps(payload, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
