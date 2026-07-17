from __future__ import annotations

import uuid
from datetime import UTC, datetime

from parayabuck.config import Config
from parayabuck.execution.base import Broker
from parayabuck.models import (
    Account,
    Order,
    OrderStatus,
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
        self._current_time: datetime = datetime.now(UTC)
        self._daily_reset_balance: float = config.account.starting_balance

    def update_market_price(
        self, symbol: str, price: float, timestamp: datetime | None = None
    ) -> None:
        self._market_prices[symbol] = price
        if timestamp is not None:
            self._current_time = timestamp
        self._update_equity()

    def submit_order(self, order: Order) -> Order:
        order.order_id = str(uuid.uuid4())

        if order.symbol not in self._market_prices:
            order.status = OrderStatus.REJECTED
            return order

        fill_price = self._market_prices[order.symbol]
        self.config.get_symbol_spec(order.symbol)

        # Apply slippage
        slip = self.config.risk.slippage_cents
        if order.side == Side.BUY:
            fill_price += slip
        else:
            fill_price -= slip

        order.filled_price = fill_price
        order.filled_qty = order.qty
        order.status = OrderStatus.FILLED
        order.filled_at = self._current_time

        # Apply order to position
        self._apply_fill(order, fill_price)
        self._update_equity()

        return order

    def _apply_fill(self, order: Order, fill_price: float) -> None:
        position = self.account.get_position(order.symbol)
        commission = abs(order.qty) * self.config.risk.commission_per_share
        realized_pnl = 0.0
        closes_position = False

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
            self.account.balance += realized_pnl - commission
            self.account.daily_pnl += realized_pnl - commission

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
        for symbol, position in self.account.positions.items():
            if position.is_flat:
                continue
            price = self._market_prices.get(symbol, position.avg_price)
            spec = self.config.get_symbol_spec(symbol)
            unreal += position.unrealized_pnl(price, spec.point_value)

        self.account.unrealized_pnl = unreal
        self.account.equity = self.account.balance + unreal
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
