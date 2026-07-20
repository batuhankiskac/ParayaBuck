from __future__ import annotations

import uuid
from datetime import UTC, datetime

from parayabuck.config import Config
from parayabuck.execution.base import Broker
from parayabuck.models import (
    Account,
    Bar,
    Order,
    OrderStatus,
    OrderType,
    Position,
    Side,
    Trade,
)


class PaperBroker(Broker):
    """Simulated broker for paper trading and backtesting.

    Executes market orders at current bar price (with slippage).
    Tracks positions, realized PnL, and account state.
    """

    def __init__(self, config: Config) -> None:
        self.config = config
        self.account = Account(
            balance=config.account.starting_balance,
            equity=config.account.starting_balance,
            peak_equity=config.account.starting_balance,
        )
        self._market_prices: dict[str, float] = {}
        self._last_bar_volume: dict[str, int] = {}
        self._current_time: datetime = datetime.now(UTC)
        self._daily_reset_balance: float = config.account.starting_balance
        self._market_bars: dict[str, Bar] = {}

    def submit_order(self, order: Order) -> Order:
        order.order_id = str(uuid.uuid4())

        if order.symbol not in self._market_prices:
            order.status = OrderStatus.REJECTED
            return order

        fill_price = self._eligible_fill_price(order)
        if fill_price is None:
            order.status = OrderStatus.PENDING
            return order
        self.config.get_symbol_spec(order.symbol)

        # Apply slippage. Two components:
        #   1. A fixed per-share slippage (`slippage_cents` from config) —
        #      models the bid/ask bounce on liquid names.
        #   2. A spread-proportional component: when the order quantity is
        #      large relative to bar volume, the fill slips further into the
        #      book. We approximate market impact as:
        #          impact = base_slip * (qty / max(bar_volume, 1))
        #      This makes illiquid symbols and oversized orders realistically
        #      more expensive — a fixed-cent slippage on a $1 stock is far
        #      too optimistic.
        slip = self.config.risk.slippage_cents
        bar_vol = self._last_bar_volume.get(order.symbol, 0)
        if bar_vol > 0:
            # Cap impact at 5x base slip so a truly huge order doesn't blow up.
            impact_mult = min(abs(order.qty) / bar_vol, 5.0)
            slip = slip * (1.0 + impact_mult)
        if order.side == Side.BUY:
            fill_price += slip
        else:
            fill_price -= slip
        # A limit order can improve but must never fill worse than its limit.
        if order.limit_price is not None:
            if order.side == Side.BUY:
                fill_price = min(fill_price, order.limit_price)
            else:
                fill_price = max(fill_price, order.limit_price)

        commission = abs(order.qty) * self.config.risk.commission_per_share
        position = self.account.get_position(order.symbol)
        if (
            order.side == Side.BUY
            and not position.is_short
            and fill_price * order.qty + commission > self.account.balance
        ):
            order.status = OrderStatus.REJECTED
            return order

        order.filled_price = fill_price
        order.filled_qty = order.qty
        order.status = OrderStatus.FILLED
        order.filled_at = self._current_time

        # Apply order to position
        self._apply_fill(order, fill_price)
        self._update_equity()

        return order

    def _eligible_fill_price(self, order: Order) -> float | None:
        """Return a realistic bar-based fill price, or None if not triggered."""
        current = self._market_prices[order.symbol]
        bar = self._market_bars.get(order.symbol)
        if order.order_type == OrderType.MARKET:
            return current
        if bar is None:
            return None
        if order.order_type == OrderType.LIMIT:
            assert order.limit_price is not None
            if order.side == Side.BUY and bar.low <= order.limit_price:
                return min(current, order.limit_price)
            if order.side == Side.SELL and bar.high >= order.limit_price:
                return max(current, order.limit_price)
            return None
        if order.order_type == OrderType.STOP:
            assert order.stop_price is not None
            if order.side == Side.BUY and bar.high >= order.stop_price:
                return max(current, order.stop_price)
            if order.side == Side.SELL and bar.low <= order.stop_price:
                return min(current, order.stop_price)
            return None
        if order.order_type == OrderType.STOP_LIMIT:
            assert order.stop_price is not None and order.limit_price is not None
            buy_fill = (
                order.side == Side.BUY
                and bar.high >= order.stop_price
                and bar.low <= order.limit_price
            )
            sell_fill = (
                order.side == Side.SELL
                and bar.low <= order.stop_price
                and bar.high >= order.limit_price
            )
            if buy_fill:
                return min(max(current, order.stop_price), order.limit_price)
            if sell_fill:
                return max(min(current, order.stop_price), order.limit_price)
        return None

    def update_market_price(
        self, symbol: str, price: float, timestamp: datetime | None = None,
        bar_volume: int = 0,
    ) -> None:
        """Update the market price for `symbol`.

        Args:
            bar_volume: Optional volume of the most recent bar. Used to
                scale slippage on the next fill (large orders on low-volume
                bars slip more — a realistic market-impact model).
        """
        self._market_prices[symbol] = price
        if bar_volume > 0:
            self._last_bar_volume[symbol] = bar_volume
        if timestamp is not None:
            self._current_time = timestamp
        self._update_equity()

    def update_market_bar(self, bar: Bar, price: float | None = None) -> None:
        """Set the current OHLC bar used by the backtest fill model."""
        self._market_bars[bar.symbol] = bar
        self.update_market_price(
            bar.symbol,
            bar.open if price is None else price,
            bar.timestamp,
            bar_volume=bar.volume,
        )

    def _apply_fill(self, order: Order, fill_price: float) -> None:
        position = self.account.get_position(order.symbol)
        commission = abs(order.qty) * self.config.risk.commission_per_share
        realized_pnl = 0.0
        closes_position = False

        # Real cash accounting: buys consume cash, sells add proceeds. Equity
        # is reconstructed from cash plus marked-to-market positions below.
        notional = fill_price * order.qty
        self.account.balance += notional if order.side == Side.SELL else -notional
        self.account.balance -= commission
        self.account.realized_pnl -= commission
        self.account.daily_pnl -= commission

        # Update position
        if position.is_flat:
            position.avg_price = fill_price
            position.qty = order.qty if order.side == Side.BUY else -order.qty
        elif (position.is_long and order.side == Side.BUY) or (
            position.is_short and order.side == Side.SELL
        ):
            # Adding to position — update average price
            total_cost = (position.avg_price * abs(position.qty)) + (fill_price * order.qty)
            new_qty = abs(position.qty) + order.qty
            position.avg_price = total_cost / new_qty
            position.qty = new_qty if position.is_long else -new_qty
        else:
            # Reducing or closing position
            closes_position = True
            close_qty = min(abs(position.qty), order.qty)
            point_value = self.config.get_symbol_spec(order.symbol).point_value
            realized_pnl = (
                (fill_price - position.avg_price)
                * close_qty
                * (1 if position.is_long else -1)
                * point_value
            )
            self.account.realized_pnl += realized_pnl
            self.account.daily_pnl += realized_pnl

            remaining = order.qty - close_qty
            if remaining > 0:
                # Reversal — open opposite position with remaining qty
                position.avg_price = fill_price
                position.qty = remaining if order.side == Side.BUY else -remaining
            else:
                if close_qty >= abs(position.qty):
                    position.qty = 0
                    position.avg_price = 0.0
                else:
                    # Partial close
                    position.qty = (abs(position.qty) - close_qty) * (1 if position.is_long else -1)

        trade = Trade(
            timestamp=self._current_time,
            symbol=order.symbol,
            side=order.side,
            qty=order.qty,
            price=fill_price,
            commission=commission,
            realized_pnl=realized_pnl - commission if realized_pnl != 0.0 else -commission,
            closes_position=closes_position,
            order_id=order.order_id,
        )
        self.account.trades.append(trade)

    def _update_equity(self) -> None:
        unreal = 0.0
        market_value = 0.0
        for symbol, position in self.account.positions.items():
            if position.is_flat:
                continue
            price = self._market_prices.get(symbol, position.avg_price)
            spec = self.config.get_symbol_spec(symbol)
            unreal += position.unrealized_pnl(price, spec.point_value)
            market_value += position.qty * price * spec.point_value

        self.account.unrealized_pnl = unreal
        self.account.equity = self.account.balance + market_value
        self.account.peak_equity = max(self.account.peak_equity, self.account.equity)

    def cancel_order(self, order_id: str) -> bool:
        # Paper broker fills immediately, nothing to cancel
        return False

    def get_position(self, symbol: str) -> Position:
        return self.account.get_position(symbol)

    def get_account(self) -> Account:
        self._update_equity()
        return self.account

    def reset_daily_pnl(self) -> None:
        self._daily_reset_balance = self.account.balance
        self.account.daily_pnl = 0.0
