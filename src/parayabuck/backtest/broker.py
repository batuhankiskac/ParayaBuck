from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from parayabuck.core.models import (
    AccountSnapshot,
    Bar,
    Direction,
    Fill,
    Market,
    OrderIntent,
    OrderState,
    PositionSnapshot,
)


@dataclass
class _Position:
    symbol: str
    qty: float
    avg_price: float
    current_price: float
    market: Market
    stop: float
    target: float
    client_order_id: str


class BacktestBroker:
    """Next-bar execution simulator with commission, slippage and protective exits."""

    def __init__(
        self, starting_cash: float, slippage_bps: float = 2.0, commission: float = 0.0
    ) -> None:
        self.cash = starting_cash
        self.starting_cash = starting_cash
        self.slippage_bps = slippage_bps
        self.commission = commission
        self.peak_equity = starting_cash
        self._day_start_equity = starting_cash
        self._current_date: date | None = None
        self.positions: dict[str, _Position] = {}
        self.pending: list[OrderIntent] = []
        self.fills: list[Fill] = []
        self.realized_pnls: list[float] = []

    def account(self) -> AccountSnapshot:
        snapshots = tuple(
            PositionSnapshot(p.symbol, p.qty, p.avg_price, p.current_price, p.market)
            for p in self.positions.values()
        )
        equity = self.cash + sum(p.qty * p.current_price for p in self.positions.values())
        self.peak_equity = max(self.peak_equity, equity)
        return AccountSnapshot(
            cash=self.cash,
            equity=equity,
            peak_equity=self.peak_equity,
            daily_pnl=equity - self._day_start_equity,
            positions=snapshots,
        )

    def submit(self, order: OrderIntent) -> tuple[OrderState, Fill | None]:
        if any(item.client_order_id == order.client_order_id for item in self.pending):
            return OrderState.ACCEPTED, None
        self.pending.append(order)
        return OrderState.ACCEPTED, None

    def reconcile(self) -> None:
        return

    def advance(self, bar: Bar) -> None:
        if self._current_date != bar.timestamp.date():
            self._current_date = bar.timestamp.date()
            self._day_start_equity = self.account().equity

        position = self.positions.get(bar.symbol)
        if position is not None:
            position.current_price = bar.close
            stop_hit = bar.low <= position.stop
            target_hit = bar.high >= position.target
            if stop_hit or target_hit:
                # Conservative same-bar rule: when both hit, assume the stop filled first.
                exit_price = position.stop if stop_hit else position.target
                exit_price *= 1 - self.slippage_bps / 10_000
                self._close(position, exit_price, bar)

        remaining: list[OrderIntent] = []
        for order in self.pending:
            if order.symbol != bar.symbol or order.created_at >= bar.timestamp:
                remaining.append(order)
                continue
            if order.symbol in self.positions:
                continue
            fill_price = bar.open * (1 + self.slippage_bps / 10_000)
            notional = order.qty * fill_price + self.commission
            if notional > self.cash:
                continue
            self.cash -= notional
            self.positions[order.symbol] = _Position(
                order.symbol,
                order.qty,
                fill_price,
                bar.close,
                order.market,
                order.stop_price,
                order.target_price,
                order.client_order_id,
            )
            self.fills.append(
                Fill(
                    order.client_order_id,
                    order.symbol,
                    order.qty,
                    fill_price,
                    order.direction,
                    bar.timestamp,
                    self.commission,
                )
            )
        self.pending = remaining

    def _close(self, position: _Position, price: float, bar: Bar) -> None:
        proceeds = position.qty * price - self.commission
        self.cash += proceeds
        pnl = (price - position.avg_price) * position.qty - self.commission * 2
        self.realized_pnls.append(pnl)
        self.fills.append(
            Fill(
                position.client_order_id,
                position.symbol,
                position.qty,
                price,
                Direction.FLAT,
                bar.timestamp,
                self.commission,
            )
        )
        del self.positions[position.symbol]
