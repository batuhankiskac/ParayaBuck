from __future__ import annotations

from collections import deque
from typing import Any

from parayabuck.models import Bar, Order, OrderType, Position, Side
from parayabuck.strategy.base import Strategy


class EMACrossover(Strategy):
    """EMA crossover strategy.

    Buys when fast EMA crosses above slow EMA.
    Sells (or goes short) when fast EMA crosses below slow EMA.

    Params:
        fast_ema: period for fast EMA (default 9)
        slow_ema: period for slow EMA (default 21)
        allow_short: whether to take short positions (default True)
    """

    name = "ema_crossover"

    def __init__(self, **params: Any) -> None:
        super().__init__(
            fast_ema=params.get("fast_ema", 9),
            slow_ema=params.get("slow_ema", 21),
            allow_short=params.get("allow_short", True),
        )
        self.fast_period = int(self.params["fast_ema"])
        self.slow_period = int(self.params["slow_ema"])
        self.allow_short = bool(self.params["allow_short"])
        self._prices: deque[float] = deque(maxlen=self.slow_period)
        self._prev_fast: float | None = None
        self._prev_slow: float | None = None
        self._prev_above: bool | None = None

    def _ema(self, period: int) -> float:
        if len(self._prices) < period:
            return 0.0
        prices = list(self._prices)[-period:]
        k = 2 / (period + 1)
        ema = prices[0]
        for p in prices[1:]:
            ema = p * k + ema * (1 - k)
        return ema

    def on_bar(self, bar: Bar, position: Position) -> Order | None:
        self._prices.append(bar.close)
        self._bars_seen += 1

        if len(self._prices) < self.slow_period:
            return None

        fast = self._ema(self.fast_period)
        slow = self._ema(self.slow_period)
        currently_above = fast > slow

        if self._prev_above is None:
            self._prev_fast = fast
            self._prev_slow = slow
            self._prev_above = currently_above
            return None

        # Bullish crossover: fast crosses above slow
        if currently_above and not self._prev_above:
            self._prev_fast = fast
            self._prev_slow = slow
            self._prev_above = currently_above
            if position.is_short:
                # Close short, go long
                return Order(
                    symbol=bar.symbol,
                    side=Side.BUY,
                    qty=abs(position.qty) + 1,
                    order_type=OrderType.MARKET,
                )
            if position.is_flat:
                return Order(
                    symbol=bar.symbol,
                    side=Side.BUY,
                    qty=1,
                    order_type=OrderType.MARKET,
                )

        # Bearish crossover: fast crosses below slow
        if not currently_above and self._prev_above:
            self._prev_fast = fast
            self._prev_slow = slow
            self._prev_above = currently_above
            if position.is_long:
                if self.allow_short:
                    # Close long, go short
                    return Order(
                        symbol=bar.symbol,
                        side=Side.SELL,
                        qty=position.qty + 1,
                        order_type=OrderType.MARKET,
                    )
                else:
                    # Just close long
                    return Order(
                        symbol=bar.symbol,
                        side=Side.SELL,
                        qty=position.qty,
                        order_type=OrderType.MARKET,
                    )
            if position.is_flat and self.allow_short:
                return Order(
                    symbol=bar.symbol,
                    side=Side.SELL,
                    qty=1,
                    order_type=OrderType.MARKET,
                )

        self._prev_fast = fast
        self._prev_slow = slow
        self._prev_above = currently_above
        return None
