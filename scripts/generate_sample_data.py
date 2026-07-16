#!/usr/bin/env python3
"""Generate synthetic OHLCV data for testing the backtest engine.

Creates a CSV file with realistic-looking price data using a random walk
with mild upward drift and bar-to-bar volatility.

Usage:
    python scripts/generate_sample_data.py --symbol MES --timeframe 5m \\
        --bars 5000 --output data/historical/MES_5m_2024.csv
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta

import numpy as np
import pandas as pd


def generate_bars(
    symbol: str = "MES",
    timeframe: str = "5m",
    n_bars: int = 5000,
    start_price: float = 5000.0,
    drift: float = 0.0001,
    volatility: float = 0.003,
    start_date: str = "2024-01-01",
) -> pd.DataFrame:
    minutes = int(timeframe[:-1]) if timeframe.endswith("m") else 60

    # Trading hours: 18:00 prev day → 17:00 next day (ET), skip weekends
    # For simplicity, generate continuous bars with 5m intervals on weekdays
    start_dt = datetime.strptime(start_date, "%Y-%m-%d")
    timestamps: list[datetime] = []
    t = start_dt
    while len(timestamps) < n_bars:
        if t.weekday() < 5:  # Mon-Fri
            # Skip 17:00-18:00 maintenance break
            hour = t.hour
            if not (hour == 17 and t.minute < 60):
                timestamps.append(t)
        t += timedelta(minutes=minutes)

    timestamps = timestamps[:n_bars]

    # Random walk with drift
    np.random.seed(42)
    returns = np.random.normal(drift, volatility, n_bars)
    prices = np.zeros(n_bars)
    prices[0] = start_price
    for i in range(1, n_bars):
        prices[i] = prices[i - 1] * (1 + returns[i])

    # Build OHLC from close prices
    data = []
    for i in range(n_bars):
        close = round(prices[i], 2)
        # Intrabar range based on volatility
        bar_range = abs(prices[i] * volatility * np.random.uniform(0.5, 1.5))
        high = round(close + bar_range * np.random.uniform(0.3, 1.0), 2)
        low = round(close - bar_range * np.random.uniform(0.3, 1.0), 2)
        open_p = round(low + (high - low) * np.random.uniform(0.2, 0.8), 2)

        data.append({
            "timestamp": timestamps[i],
            "open": open_p,
            "high": max(high, open_p, close),
            "low": min(low, open_p, close),
            "close": close,
            "volume": int(np.random.uniform(100, 5000)),
        })

    return pd.DataFrame(data)


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate sample OHLCV data")
    parser.add_argument("--symbol", default="MES", help="Symbol name")
    parser.add_argument("--timeframe", default="5m", help="Timeframe (e.g. 5m, 1h)")
    parser.add_argument("--bars", type=int, default=5000, help="Number of bars")
    parser.add_argument("--start-price", type=float, default=5000.0, help="Starting price")
    parser.add_argument("--start-date", default="2024-01-01", help="Start date (YYYY-MM-DD)")
    parser.add_argument("--output", default=None, help="Output CSV path")
    args = parser.parse_args()

    output = args.output or f"data/historical/{args.symbol}_{args.timeframe}_2024.csv"

    df = generate_bars(
        symbol=args.symbol,
        timeframe=args.timeframe,
        n_bars=args.bars,
        start_price=args.start_price,
        start_date=args.start_date,
    )
    df.to_csv(output, index=False)
    print(f"Generated {len(df)} bars for {args.symbol} {args.timeframe}")
    print(f"Period: {df['timestamp'].iloc[0]} → {df['timestamp'].iloc[-1]}")
    print(f"Price range: {df['low'].min():.2f} → {df['high'].max():.2f}")
    print(f"Saved to: {output}")


if __name__ == "__main__":
    main()
