from __future__ import annotations

from collections import deque
from typing import Any

from parayabuck.logger import get_logger
from parayabuck.models import Bar, Order, OrderType, Position, Side
from parayabuck.strategy.base import Strategy
from parayabuck.strategy.indicators import zscore

log = get_logger("parayabuck.strategy.pairs")


class PairsTrading(Strategy):
    """Statistical arbitrage / pairs trading.

    Trades the spread between two cointegrated stocks.
    Market-neutral: long one, short the other.

    Signal:
      - Compute spread = log(price_A) - beta * log(price_B)
      - Compute z-score of spread over lookback window
      - z > entry_threshold  → short A, long B (spread too wide, expect reversion)
      - z < -entry_threshold → long A, short B (spread too narrow)
      - |z| < exit_threshold  → close position (spread reverted to mean)

    Params:
        symbol_a: First stock (e.g. "KO")
        symbol_b: Second stock (e.g. "PEP")
        lookback: Window for z-score & hedge ratio (default 60)
        entry_z: Z-score threshold to enter (default 2.0)
        exit_z: Z-score threshold to exit (default 0.5)
        beta: Hedge ratio (shares of B per share of A). If None, computed dynamically.
        max_position: Max shares of A per leg (default 50)
    """

    name = "pairs_trading"

    def __init__(self, **params: Any) -> None:
        super().__init__(
            symbol_a=params.get("symbol_a", "KO"),
            symbol_b=params.get("symbol_b", "PEP"),
            lookback=params.get("lookback", 60),
            entry_z=params.get("entry_z", 2.0),
            exit_z=params.get("exit_z", 0.5),
            beta=params.get("beta"),
            max_position=params.get("max_position", 50),
        )
        self.symbol_a = str(self.params["symbol_a"])
        self.symbol_b = str(self.params["symbol_b"])
        self.symbols = [self.symbol_a, self.symbol_b]
        self.lookback = int(self.params["lookback"])
        self.entry_z = float(self.params["entry_z"])
        self.exit_z = float(self.params["exit_z"])
        self.fixed_beta = self.params.get("beta")
        self.beta = float(self.fixed_beta) if self.fixed_beta else 1.0
        self.max_position = int(self.params["max_position"])

        self._prices_a: deque[float] = deque(maxlen=self.lookback * 2)
        self._prices_b: deque[float] = deque(maxlen=self.lookback * 2)
        self._spreads: deque[float] = deque(maxlen=self.lookback * 2)
        self._last_bar_a: Bar | None = None
        self._state: str = "flat"  # flat, long_a_short_b, short_a_long_b

    def _compute_beta(self) -> None:
        if len(self._prices_a) < self.lookback:
            return
        a = list(self._prices_a)[-self.lookback :]
        b = list(self._prices_b)[-self.lookback :]
        n = len(a)
        if n < 10:
            return
        mean_a = sum(a) / n
        mean_b = sum(b) / n
        cov = sum((a[i] - mean_a) * (b[i] - mean_b) for i in range(n)) / n
        var_b = sum((b[i] - mean_b) ** 2 for i in range(n)) / n
        if var_b > 0:
            self.beta = cov / var_b

    def _update_spread(self, price_a: float, price_b: float) -> float:
        import math

        if price_a <= 0 or price_b <= 0:
            return 0.0
        spread = math.log(price_a) - self.beta * math.log(price_b)
        self._spreads.append(spread)
        return spread

    def _current_z(self) -> float | None:
        if len(self._spreads) < self.lookback:
            return None
        return zscore(list(self._spreads), self.lookback)

    def on_bar(self, bar: Bar, position: Position) -> Order | None:
        self._bars_seen += 1

        if bar.symbol == self.symbol_a:
            self._last_bar_a = bar
            self._prices_a.append(bar.close)
        elif bar.symbol == self.symbol_b:
            self._prices_b.append(bar.close)
        else:
            return None

        if len(self._prices_a) < self.lookback or len(self._prices_b) < self.lookback:
            return None

        if not self.fixed_beta:
            self._compute_beta()

        price_a = self._prices_a[-1]
        price_b = self._prices_b[-1]
        self._update_spread(price_a, price_b)

        z = self._current_z()
        if z is None:
            return None

        qty_a = self.max_position
        qty_b = max(int(qty_a * self.beta), 1)

        if self._state == "flat":
            if z > self.entry_z:
                self._state = "short_a_long_b"
                log.info(
                    f"PAIRS ENTRY: short {qty_a} {self.symbol_a} + long {qty_b} {self.symbol_b} "
                    f"(z={z:.2f}, beta={self.beta:.2f})"
                )
                # A leg returned; B leg queued for the engine to submit.
                self._pending_orders.append(
                    Order(
                        symbol=self.symbol_b,
                        side=Side.BUY,
                        qty=qty_b,
                        order_type=OrderType.MARKET,
                    )
                )
                return Order(
                    symbol=self.symbol_a,
                    side=Side.SELL,
                    qty=qty_a,
                    order_type=OrderType.MARKET,
                )
            elif z < -self.entry_z:
                self._state = "long_a_short_b"
                log.info(
                    f"PAIRS ENTRY: long {qty_a} {self.symbol_a} + short {qty_b} {self.symbol_b} "
                    f"(z={z:.2f}, beta={self.beta:.2f})"
                )
                self._pending_orders.append(
                    Order(
                        symbol=self.symbol_b,
                        side=Side.SELL,
                        qty=qty_b,
                        order_type=OrderType.MARKET,
                    )
                )
                return Order(
                    symbol=self.symbol_a,
                    side=Side.BUY,
                    qty=qty_a,
                    order_type=OrderType.MARKET,
                )

        elif self._state == "long_a_short_b":
            if z > -self.exit_z:
                log.info(f"PAIRS EXIT (long_a_short_b): z={z:.2f} reverted")
                self._state = "flat"
                # Close A (sell) and close B (buy back short).
                self._pending_orders.append(
                    Order(
                        symbol=self.symbol_b,
                        side=Side.BUY,
                        qty=qty_b,
                        order_type=OrderType.MARKET,
                    )
                )
                return Order(
                    symbol=self.symbol_a,
                    side=Side.SELL,
                    qty=qty_a,
                    order_type=OrderType.MARKET,
                )

        elif self._state == "short_a_long_b":
            if z < self.exit_z:
                log.info(f"PAIRS EXIT (short_a_long_b): z={z:.2f} reverted")
                self._state = "flat"
                # Close A (buy back short) and close B (sell long).
                self._pending_orders.append(
                    Order(
                        symbol=self.symbol_b,
                        side=Side.SELL,
                        qty=qty_b,
                        order_type=OrderType.MARKET,
                    )
                )
                return Order(
                    symbol=self.symbol_a,
                    side=Side.BUY,
                    qty=qty_a,
                    order_type=OrderType.MARKET,
                )

        return None
