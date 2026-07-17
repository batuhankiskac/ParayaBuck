from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from parayabuck.logger import get_logger
from parayabuck.models import Account, Order, Side
from parayabuck.strategy.indicators import atr
from parayabuck.utils import is_crypto

log = get_logger("parayabuck.risk.sizer")


@dataclass
class TradeStats:
    wins: int = 0
    losses: int = 0
    gross_profit: float = 0.0
    gross_loss: float = 0.0

    @property
    def total(self) -> int:
        return self.wins + self.losses

    @property
    def win_rate(self) -> float:
        return self.wins / self.total if self.total > 0 else 0.0

    @property
    def avg_win(self) -> float:
        return self.gross_profit / self.wins if self.wins > 0 else 0.0

    @property
    def avg_loss(self) -> float:
        return self.gross_loss / self.losses if self.losses > 0 else 0.0


class PositionSizer:
    """Calculates position size for orders based on ATR volatility and Kelly criterion.

    Two modes:
      1. ATR-based (default, used when no trade history available):
         qty = (equity * risk_pct) / (ATR * multiplier)
      2. Kelly-enhanced (once enough trades are recorded):
         Uses win rate and payoff ratio to adjust allocation

    The strategy returns an order with qty=1 (a "signal").
    The sizer replaces qty with the calculated amount.
    Exit orders (closing positions) keep their original qty.
    """

    def __init__(
        self,
        risk_pct: float = 0.02,
        atr_period: int = 14,
        atr_multiplier: float = 2.0,
        max_qty: float = 500,
        min_qty: float = 1,
        kelly_fraction: float = 0.25,
        kelly_min_trades: int = 20,
        use_kelly: bool = True,
        crypto_risk_pct: float = 0.01,
    ) -> None:
        self.risk_pct = risk_pct
        self.crypto_risk_pct = crypto_risk_pct
        self.atr_period = atr_period
        self.atr_multiplier = atr_multiplier
        self.max_qty = max_qty
        self.min_qty = min_qty
        self.kelly_fraction = kelly_fraction
        self.kelly_min_trades = kelly_min_trades
        self.use_kelly = use_kelly

        self._highs: dict[str, deque[float]] = {}
        self._lows: dict[str, deque[float]] = {}
        self._closes: dict[str, deque[float]] = {}
        self._stats: dict[str, TradeStats] = {}
        self._entry_prices: dict[str, float] = {}

    def update_history(self, symbol: str, high: float, low: float, close: float) -> None:
        if symbol not in self._highs:
            self._highs[symbol] = deque(maxlen=200)
            self._lows[symbol] = deque(maxlen=200)
            self._closes[symbol] = deque(maxlen=200)
        self._highs[symbol].append(high)
        self._lows[symbol].append(low)
        self._closes[symbol].append(close)

    def record_trade(self, symbol: str, pnl: float) -> None:
        if symbol not in self._stats:
            self._stats[symbol] = TradeStats()
        if pnl > 0:
            self._stats[symbol].wins += 1
            self._stats[symbol].gross_profit += pnl
        elif pnl < 0:
            self._stats[symbol].losses += 1
            self._stats[symbol].gross_loss += abs(pnl)

    def size_order(self, order: Order, account: Account) -> Order:
        """Return order with adjusted quantity. Exit orders pass through unchanged."""
        if self._is_exit(order, account):
            return order

        symbol = order.symbol
        highs = list(self._highs.get(symbol, []))
        lows = list(self._lows.get(symbol, []))
        closes = list(self._closes.get(symbol, []))

        crypto = is_crypto(symbol)
        min_qty = self.min_qty if not crypto else 0.0001

        if len(closes) < self.atr_period + 1:
            log.debug(f"PositionSizer: not enough history for {symbol}, using min_qty")
            return self._set_qty(order, min_qty)

        atr_val = atr(highs, lows, closes, self.atr_period)
        if atr_val is None or atr_val <= 0:
            return self._set_qty(order, min_qty)

        stop_distance = atr_val * self.atr_multiplier
        entry_price = closes[-1]

        equity = account.equity if account.equity > 0 else account.balance

        risk_pct = self.crypto_risk_pct if crypto else self.risk_pct
        risk_amount = equity * risk_pct
        qty = risk_amount / stop_distance

        if self.use_kelly:
            qty = self._apply_kelly(symbol, qty, equity, entry_price, stop_distance)

        # Kelly can only shrink or keep the ATR-based size, never grow it
        # (avoids 5x blow-ups when Kelly fraction > risk_pct).
        qty = min(qty, risk_amount / stop_distance)

        qty = max(qty, min_qty) if qty > 0 else 0
        qty = min(qty, self.max_qty)

        if qty == 0:
            log.debug(f"PositionSizer: {symbol} qty=0, skipping")
            return order  # will be rejected by risk manager

        # Round shares to whole numbers; keep crypto fractional.
        if not crypto:
            qty = float(int(qty))
            if qty < min_qty:
                return order

        self._entry_prices[symbol] = entry_price
        log.debug(
            f"PositionSizer: {symbol} qty={qty} "
            f"(ATR={atr_val:.2f}, stop_dist={stop_distance:.2f}, "
            f"risk=${risk_amount:.2f})"
        )
        return self._set_qty(order, qty)

    def _is_exit(self, order: Order, account: Account) -> bool:
        """An exit closes an existing position in the opposite direction.

        Note: a BUY that closes a short is also an exit and must NOT be resized
        (we must close the exact qty we hold).
        """
        pos = account.get_position(order.symbol)
        if order.side == Side.SELL and pos.is_long:
            return True
        if order.side == Side.BUY and pos.is_short:
            return True
        return False

    def _apply_kelly(
        self,
        symbol: str,
        base_qty: float,
        equity: float,
        entry_price: float,
        stop_distance: float,
    ) -> float:
        stats = self._stats.get(symbol)
        if stats is None or stats.total < self.kelly_min_trades:
            return base_qty

        if stats.win_rate <= 0 or stats.avg_loss <= 0:
            return base_qty

        win_loss_ratio = stats.avg_win / stats.avg_loss
        p = stats.win_rate
        q = 1 - p
        b = win_loss_ratio
        kelly_f = (p * b - q) / b

        if kelly_f <= 0:
            log.debug(
                f"Kelly: {symbol} negative edge (win_rate={p:.1%}, "
                f"ratio={b:.2f}) → using half ATR size"
            )
            return max(base_qty / 2, 0.0001)

        adjusted_f = kelly_f * self.kelly_fraction
        adjusted_f = min(adjusted_f, 0.10)

        capital_to_risk = equity * adjusted_f
        kelly_qty = capital_to_risk / stop_distance

        if kelly_qty < base_qty:
            log.debug(f"Kelly reduced {symbol}: {base_qty} → {kelly_qty} (f={adjusted_f:.3f})")
        return max(kelly_qty, 0.0001)

    @staticmethod
    def _set_qty(order: Order, qty: float) -> Order:
        return Order(
            symbol=order.symbol,
            side=order.side,
            qty=qty,
            order_type=order.order_type,
            limit_price=order.limit_price,
            stop_price=order.stop_price,
            tif=order.tif,
        )

    def get_stats(self, symbol: str) -> TradeStats | None:
        return self._stats.get(symbol)

    def get_all_stats(self) -> dict[str, TradeStats]:
        return self._stats
