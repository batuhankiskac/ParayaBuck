from __future__ import annotations

import math
from collections import deque
from typing import Any

from parayabuck.logger import get_logger
from parayabuck.models import Bar, Order, OrderType, Position, Side
from parayabuck.strategy.base import Strategy
from parayabuck.strategy.indicators import zscore

log = get_logger("parayabuck.strategy.pairs")


# Default pairs universe — cointegrated / economically-linked equity pairs.
# The previous implementation hardcoded only KO/PEP; this list is config-
# overridable (pass `pairs` as a list of [a, b] tuples) but provides a sane
# starting set of well-known economically-linked pairs.
DEFAULT_PAIRS: list[tuple[str, str]] = [
    ("KO", "PEP"),  # beverages
    ("XOM", "CVX"),  # integrated oil
    ("JPM", "BAC"),  # money-center banks
    ("PG", "KO"),  # consumer staples
    ("UNH", "JNJ"),  # healthcare
    ("GS", "MS"),  # investment banks
    ("HD", "LOW"),  # home improvement
    ("UPS", "FDX"),  # logistics
]


class PairsTrading(Strategy):
    """Statistical arbitrage / pairs trading.

    Trades the spread between two cointegrated stocks.
    Market-neutral: long one, short the other.

    Signal:
      - Compute spread = log(price_A) - beta * log(price_B)
      - Compute z-score of spread over lookback window
      - z > entry_threshold  -> short A, long B (spread too wide, expect reversion)
      - z < -entry_threshold -> long A, short B (spread too narrow)
      - |z| < exit_threshold  -> close position (spread reverted to mean)

    Half-life filter:
      - Before entering, the strategy estimates the Ornstein-Uhlenbeck
        half-life of mean reversion from the rolling spread series. If the
        half-life is too long (> max_half_life, default 60 bars), the spread
        reverts so slowly that holding it ties up capital for an uneconomic
        amount of time — we skip the signal. If the half-life is too short
        (< min_half_life, default 2 bars), the spread is likely noise rather
        than a real cointegration relationship.

    Params:
        symbol_a: First stock (e.g. "KO")
        symbol_b: Second stock (e.g. "PEP")
        lookback: Window for z-score & hedge ratio (default 60)
        entry_z: Z-score threshold to enter (default 2.0)
        exit_z: Z-score threshold to exit (default 0.5)
        beta: Hedge ratio (shares of B per share of A). If None, computed dynamically.
        max_position: Max shares of A per leg (default 50)
        min_half_life: Skip signals if estimated spread half-life is below this
            (default 2). Too short => noise, not mean reversion.
        max_half_life: Skip signals if estimated spread half-life is above this
            (default 60). Too long => capital tied up too long.
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
            min_half_life=params.get("min_half_life", 2.0),
            max_half_life=params.get("max_half_life", 60.0),
            use_half_life_filter=params.get("use_half_life_filter", True),
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
        self.min_half_life = float(self.params["min_half_life"])
        self.max_half_life = float(self.params["max_half_life"])
        self.use_half_life_filter = bool(self.params["use_half_life_filter"])

        self._prices_a: deque[float] = deque(maxlen=self.lookback * 2)
        self._prices_b: deque[float] = deque(maxlen=self.lookback * 2)
        self._spreads: deque[float] = deque(maxlen=self.lookback * 2)
        self._last_bar_a: Bar | None = None
        self._state: str = "flat"  # flat, long_a_short_b, short_a_long_b
        self._last_half_life: float = 0.0

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
        if price_a <= 0 or price_b <= 0:
            return 0.0
        spread = math.log(price_a) - self.beta * math.log(price_b)
        self._spreads.append(spread)
        return spread

    def _current_z(self) -> float | None:
        if len(self._spreads) < self.lookback:
            return None
        return zscore(list(self._spreads), self.lookback)

    def _estimate_half_life(self) -> float | None:
        """Estimate Ornstein-Uhlenbeck half-life of mean reversion of spread.

        Regresses delta(spread_t) on spread_{t-1}:
            delta_spread = -alpha * spread_lagged + epsilon
        Half-life = log(2) / alpha.

        Returns None if there's not enough data or the spread isn't mean-
        reverting (alpha <= 0).
        """
        spreads = list(self._spreads)
        if len(spreads) < self.lookback:
            return None
        # Use the same lookback window as z-score for consistency.
        s = spreads[-self.lookback :]
        n = len(s) - 1
        if n < 10:
            return None
        # Lag-1 regression: y = delta_spread, x = spread_{t-1}
        x_mean = sum(s[:-1]) / n
        y_mean = sum(s[i + 1] - s[i] for i in range(n)) / n
        num = sum((s[i] - x_mean) * (s[i + 1] - s[i] - y_mean) for i in range(n))
        den = sum((s[i] - x_mean) ** 2 for i in range(n))
        if den <= 0:
            return None
        alpha = -num / den
        if alpha <= 0:
            # Spread is a random walk or trending — not mean-reverting.
            return None
        return math.log(2) / alpha

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

        # Estimate half-life once per bar (cheap, O(lookback)). Used as an
        # entry filter — exits are always allowed because we must be able to
        # flatten a position whose statistical properties have degraded.
        half_life: float | None = None
        if self.use_half_life_filter and self._state == "flat":
            half_life = self._estimate_half_life()
            if half_life is not None:
                self._last_half_life = half_life
                if half_life < self.min_half_life or half_life > self.max_half_life:
                    log.debug(
                        f"PAIRS skip {self.symbol_a}/{self.symbol_b}: half-life "
                        f"{half_life:.1f} outside [{self.min_half_life}, "
                        f"{self.max_half_life}]"
                    )
                    return None

        qty_a = self.max_position
        qty_b = max(int(qty_a * self.beta), 1)

        if self._state == "flat":
            if z > self.entry_z:
                self._state = "short_a_long_b"
                hl_str = f" hl={half_life:.1f}" if half_life else ""
                log.info(
                    f"PAIRS ENTRY: short {qty_a} {self.symbol_a} + long {qty_b} {self.symbol_b} "
                    f"(z={z:.2f}, beta={self.beta:.2f}{hl_str})"
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
                hl_str = f" hl={half_life:.1f}" if half_life else ""
                log.info(
                    f"PAIRS ENTRY: long {qty_a} {self.symbol_a} + short {qty_b} {self.symbol_b} "
                    f"(z={z:.2f}, beta={self.beta:.2f}{hl_str})"
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
