#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from dataclasses import asdict, replace
from datetime import UTC

import pandas as pd

from parayabuck.backtest import BacktestEngine
from parayabuck.config import load_config
from parayabuck.core.models import Bar, Market


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a ParayaBuck CSV backtest")
    parser.add_argument("csv")
    parser.add_argument("--symbol")
    parser.add_argument("--market", choices=("equity", "crypto"), default="equity")
    parser.add_argument("--timeframe", default="5m")
    parser.add_argument("--starting-equity", type=float, default=10_000)
    parser.add_argument("--slippage-bps", type=float, default=2.0)
    parser.add_argument("--spread-bps", type=float, default=0.0)
    parser.add_argument("--commission", type=float, default=0.0)
    parser.add_argument("--crypto-taker-fee-bps", type=float, default=0.0)
    args = parser.parse_args()

    frame = pd.read_csv(args.csv)
    timestamps = pd.to_datetime(frame["timestamp"], utc=True)
    if "symbol" not in frame.columns and not args.symbol:
        parser.error("--symbol is required when the dataset has no symbol column")
    bars = [
        Bar(
            timestamp=timestamp.to_pydatetime().astimezone(UTC),
            symbol=str(getattr(row, "symbol", args.symbol)),
            timeframe=str(getattr(row, "timeframe", args.timeframe)),
            open=float(row.open),
            high=float(row.high),
            low=float(row.low),
            close=float(row.close),
            volume=float(row.volume),
            market=Market(str(getattr(row, "market", args.market))),
        )
        for timestamp, row in zip(timestamps, frame.itertuples(index=False), strict=True)
    ]
    config = load_config()
    minutes = {"1m": 1, "5m": 5, "15m": 15, "30m": 30, "1h": 60, "1d": 1440}
    if args.timeframe not in minutes:
        parser.error(f"unsupported timeframe: {args.timeframe}")
    if args.timeframe == "1d":
        parser.error("1d backtests require a configured higher timeframe above 1d")
    higher_timeframe = config.engine.higher_timeframe
    if minutes[higher_timeframe] <= minutes[args.timeframe]:
        higher_timeframe = "1d"
    backtest_config = replace(
        config.engine,
        timeframe=args.timeframe,
        higher_timeframe=higher_timeframe,
        trading_enabled=False,
    )
    engine = BacktestEngine(
        backtest_config,
        starting_equity=args.starting_equity,
        slippage_bps=args.slippage_bps,
        spread_bps=args.spread_bps,
        commission=args.commission,
        crypto_taker_fee_bps=args.crypto_taker_fee_bps,
    )
    result = engine.run(bars)
    payload = asdict(result)
    payload.pop("equity_curve")
    payload.pop("realized_pnls")
    payload["ai_log_dir"] = str(engine.ai_journal.root) if engine.ai_journal is not None else None
    print(json.dumps(payload, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
