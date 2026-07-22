from __future__ import annotations

from collections import deque
from typing import Any

from parayabuck.models import Bar, Order, OrderType, Position, Side
from parayabuck.strategy.base import Strategy
from parayabuck.strategy.indicators import adx


class EMACrossover(Strategy):
    """EMA crossover strategy with an ADX trend filter.

    Buys when fast EMA crosses above slow EMA, but ONLY when ADX > adx_threshold
    — without this filter, crossovers in a flat / choppy market produce constant
    whipsaws (cross up, cross down, cross up) that bleed the account through
    stop-losses and slippage. ADX > 20 confirms there's a real trend to ride.

    Sells (or goes short) when fast EMA crosses below slow EMA.

    Params:
        fast_ema: period for fast EMA (default 9)
        slow_ema: period for slow EMA (default 21)
        allow_short: whether to take short positions (default True)
        adx_period: ADX lookback (default 14)
        adx_threshold: min ADX to act on a crossover (default 20). Set to 0
            to disable the filter and reproduce the old behaviour.
    """

    name = "ema_crossover"

    def __init__(self, **params: Any) -> None:
        super().__init__(
            fast_ema=params.get("fast_ema", 9),
            slow_ema=params.get("slow_ema", 21),
            allow_short=params.get("allow_short", True),
            adx_period=params.get("adx_period", 14),
            adx_threshold=params.get("adx_threshold", 20),
        )
        self.fast_period = int(self.params["fast_ema"])
        self.slow_period = int(self.params["slow_ema"])
        self.allow_short = bool(self.params["allow_short"])
        self.adx_period = int(self.params["adx_period"])
        self.adx_threshold = float(self.params["adx_threshold"])
        # Keep enough history so the slow EMA has a stable seed (SMA over the
        # first `slow_period` values) rather than being anchored to the oldest
        # price in a too-short buffer. ADX needs ~ period*2+1 bars to produce
        # a value; we size the buffer so both indicators stay warm.
        self._prices: deque[float] = deque(maxlen=max(self.slow_period * 10, 200))
        self._highs: deque[float] = deque(maxlen=max(self.slow_period * 10, 200))
        self._lows: deque[float] = deque(maxlen=max(self.slow_period * 10, 200))
        self._prev_fast: float | None = None
        self._prev_slow: float | None = None
        self._prev_above: bool | None = None

    def _ema(self, period: int) -> float:
        if len(self._prices) < period:
            return 0.0
        prices = list(self._prices)[-period:]
        # Seed EMA with SMA of the first `period` values (standard EMA seeding).
        k = 2 / (period + 1)
        ema = sum(prices[:period]) / period
        for p in prices[period:]:
            ema = p * k + ema * (1 - k)
        return ema

    def _current_adx(self) -> float | None:
        if len(self._prices) < self.adx_period * 2 + 1:
            return None
        return adx(list(self._highs), list(self._lows), list(self._prices), self.adx_period)

    def on_bar(self, bar: Bar, position: Position) -> Order | None:
        self._prices.append(bar.close)
        self._highs.append(bar.high)
        self._lows.append(bar.low)
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

        # ADX filter: a crossover without trend strength is noise. Require ADX
        # above the threshold to act on NEW entries; exits are always allowed
        # because we must be able to flatten risk regardless of regime.
        adx_val = self._current_adx()
        adx_ok = self.adx_threshold <= 0 or (adx_val is not None and adx_val >= self.adx_threshold)

        # Bullish crossover: fast crosses above slow
        if currently_above and not self._prev_above:
            self._prev_fast = fast
            self._prev_slow = slow
            self._prev_above = currently_above
            if position.is_short:
                # Close short, go long — exits always allowed.
                return Order(
                    symbol=bar.symbol,
                    side=Side.BUY,
                    qty=abs(position.qty) + 1,
                    order_type=OrderType.MARKET,
                )
            if position.is_flat and adx_ok:
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
                # Exit always allowed even if ADX is weak — we should not hold
                # a long through a bearish crossover just because ADX is soft.
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
            if position.is_flat and self.allow_short and adx_ok:
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
