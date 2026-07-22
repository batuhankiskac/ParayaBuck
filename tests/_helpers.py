"""Shared test helpers for strategy unit tests.

Provides synthetic-bar generators that build deterministic price series so
strategy behaviour can be asserted without needing real market data. The
helpers are pure — no I/O, no network — so tests run in milliseconds.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Literal

from parayabuck.models import Bar, Position


def make_bar(
    ts: datetime,
    symbol: str,
    open_: float,
    high: float,
    low: float,
    close: float,
    volume: int = 1000,
    timeframe: str = "15m",
) -> Bar:
    """Construct a Bar with validation-friendly OHLC relationships."""
    return Bar(
        timestamp=ts,
        symbol=symbol,
        timeframe=timeframe,
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=volume,
    )


def bars_from_closes(
    closes: list[float],
    symbol: str = "TEST",
    start: datetime | None = None,
    bar_minutes: int = 15,
    volume: int = 1000,
    high_low_spread: float = 0.01,
) -> list[Bar]:
    """Build a bar series from a list of closes.

    Each bar's OHLC is synthesised deterministically from its close:
      open  = previous close (or first close if i==0)
      high  = max(open, close) * (1 + high_low_spread/2)
      low   = min(open, close) * (1 - high_low_spread/2)

    This keeps every bar internally consistent (low <= open,close <= high)
    while letting us drive a strategy purely from a sequence of closes —
    exactly what's needed to test signal logic without depending on real data.
    """
    if start is None:
        start = datetime(2025, 1, 1, 9, 30, tzinfo=UTC)
    bars: list[Bar] = []
    for i, c in enumerate(closes):
        ts = start + timedelta(minutes=bar_minutes * i)
        o = closes[i - 1] if i > 0 else c
        hi = max(o, c) * (1 + high_low_spread / 2)
        lo = min(o, c) * (1 - high_low_spread / 2)
        bars.append(make_bar(ts, symbol, o, hi, lo, c, volume=volume))
    return bars


def trending_up(n: int = 100, start_price: float = 100.0, drift: float = 0.005) -> list[float]:
    """A monotone-ish upward series with small noise — bull-market shape."""
    closes: list[float] = []
    p = start_price
    for i in range(n):
        # Linear drift + small deterministic oscillation.
        noise = ((i % 5) - 2) * drift * 0.3
        p = p * (1 + drift + noise)
        closes.append(p)
    return closes


def trending_down(n: int = 100, start_price: float = 100.0, drift: float = 0.005) -> list[float]:
    """A monotone-ish downward series — bear-market shape."""
    closes: list[float] = []
    p = start_price
    for i in range(n):
        noise = ((i % 5) - 2) * drift * 0.3
        p = p * (1 - drift + noise)
        closes.append(p)
    return closes


def sideways(
    n: int = 100, base: float = 100.0, amplitude: float = 1.0, period: int = 20
) -> list[float]:
    """An oscillating range-bound series — mean-reversion-friendly shape."""
    import math

    closes: list[float] = []
    for i in range(n):
        closes.append(base + amplitude * math.sin(2 * math.pi * i / period))
    return closes


def flat_position(symbol: str = "TEST") -> Position:
    return Position(symbol=symbol, qty=0, avg_price=0.0)


def long_position(symbol: str = "TEST", qty: float = 10, avg_price: float = 100.0) -> Position:
    return Position(symbol=symbol, qty=qty, avg_price=avg_price)


def short_position(symbol: str = "TEST", qty: float = -10, avg_price: float = 100.0) -> Position:
    return Position(symbol=symbol, qty=qty, avg_price=avg_price)


SignalType = Literal["buy", "sell", "none"]


def classify_signal(order) -> SignalType:
    """Classify an Order (or None) as a buy / sell / none signal."""
    if order is None:
        return "none"
    from parayabuck.models import Side

    return "buy" if order.side == Side.BUY else "sell"


def run_strategy(strategy, bars, position=None):
    """Feed `bars` to `strategy` one by one; return the last non-None order.

    Useful for one-shot assertions: "given this series, what's the final signal?".
    For multi-leg strategies (pairs, sector rotation), use `run_strategy_all`.
    """
    from parayabuck.models import Position as _Pos

    pos = position if position is not None else _Pos(symbol=bars[0].symbol)
    last_order = None
    for b in bars:
        o = strategy.on_bar(b, pos)
        if o is not None:
            last_order = o
        # Drain multi-leg pending orders so they don't accumulate.
        strategy.pop_pending_orders()
    return last_order


def run_strategy_all(strategy, bars, position=None):
    """Feed `bars` to `strategy`; return all orders emitted (in order)."""
    from parayabuck.models import Position as _Pos

    pos = position if position is not None else _Pos(symbol=bars[0].symbol)
    orders = []
    for b in bars:
        o = strategy.on_bar(b, pos)
        if o is not None:
            orders.append(o)
        orders.extend(strategy.pop_pending_orders())
    return orders
