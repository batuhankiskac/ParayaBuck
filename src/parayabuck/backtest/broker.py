from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from parayabuck.core.models import (
    AccountSnapshot,
    Bar,
    Direction,
    ExecutionEvent,
    Fill,
    Market,
    OrderIntent,
    OrderRole,
    OrderState,
    PositionSnapshot,
)
from parayabuck.markets.timeframes import timeframe_duration


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
    direction: Direction
    entry_fee: float


class BacktestBroker:
    """Next-bar execution simulator with commission, slippage and protective exits."""

    def __init__(
        self,
        starting_cash: float,
        slippage_bps: float = 2.0,
        commission: float = 0.0,
        *,
        spread_bps: float = 0.0,
        allow_short: bool = False,
        intrabar_policy: str = "conservative",
        crypto_taker_fee_bps: float = 0.0,
    ) -> None:
        self.cash = starting_cash
        self.starting_cash = starting_cash
        self.slippage_bps = slippage_bps
        self.commission = commission
        self.spread_bps = spread_bps
        self.allow_short = allow_short
        if intrabar_policy not in {"conservative", "optimistic", "ohlc_path"}:
            raise ValueError("Unsupported intrabar policy")
        self.intrabar_policy = intrabar_policy
        self.crypto_taker_fee_bps = crypto_taker_fee_bps
        self.peak_equity = starting_cash
        self._day_start_equity = starting_cash
        self._current_date: date | None = None
        self.positions: dict[str, _Position] = {}
        self.pending: list[OrderIntent] = []
        self.fills: list[Fill] = []
        self.realized_pnls: list[float] = []
        self.order_rejections: dict[str, str] = {}
        self.execution_events: list[ExecutionEvent] = []

    def account(self) -> AccountSnapshot:
        snapshots = tuple(
            PositionSnapshot(
                p.symbol,
                p.qty if p.direction == Direction.LONG else -p.qty,
                p.avg_price,
                p.current_price,
                p.market,
                ownership="managed",
            )
            for p in self.positions.values()
        )
        equity = self.cash + sum(
            p.qty * p.current_price * (1 if p.direction == Direction.LONG else -1)
            for p in self.positions.values()
        )
        self.peak_equity = max(self.peak_equity, equity)
        return AccountSnapshot(
            cash=self.cash,
            equity=equity,
            peak_equity=self.peak_equity,
            daily_pnl=equity - self._day_start_equity,
            positions=snapshots,
        )

    def submit(self, order: OrderIntent) -> tuple[OrderState, Fill | None]:
        if order.direction == Direction.SHORT and not self.allow_short:
            raise ValueError("Short execution is disabled in this backtest")
        if any(item.client_order_id == order.client_order_id for item in self.pending):
            return OrderState.ACCEPTED, None
        self.pending.append(order)
        self.execution_events.append(
            ExecutionEvent(
                account_id="backtest",
                client_order_id=order.client_order_id,
                broker_order_id=order.client_order_id,
                parent_order_id=None,
                role=OrderRole.ENTRY,
                symbol=order.symbol,
                market=order.market,
                state=OrderState.ACCEPTED,
                requested_qty=order.qty,
                cumulative_filled_qty=0.0,
                average_fill_price=None,
                occurred_at=order.created_at,
            )
        )
        return OrderState.ACCEPTED, None

    def reconcile(self) -> None:
        return

    def advance(self, bar: Bar) -> None:
        if self._current_date != bar.timestamp.date():
            self._current_date = bar.timestamp.date()
            self._day_start_equity = self.account().equity

        # 1. Fill eligible entries at the open.
        remaining: list[OrderIntent] = []
        for order in self.pending:
            if order.symbol != bar.symbol or order.created_at >= bar.timestamp:
                remaining.append(order)
                continue
            if order.symbol in self.positions:
                self.order_rejections[order.client_order_id] = "existing_position"
                self._entry_terminal_event(order, OrderState.REJECTED, bar, 0.0, None)
                continue
            expected_seconds = timeframe_duration(bar.timeframe).total_seconds()
            if (bar.timestamp - order.created_at).total_seconds() > expected_seconds * 1.5:
                self.order_rejections[order.client_order_id] = "stale_pending_order"
                self._entry_terminal_event(order, OrderState.EXPIRED, bar, 0.0, None)
                continue
            adverse_bps = self.slippage_bps + self.spread_bps / 2
            side_multiplier = 1 if order.direction == Direction.LONG else -1
            fill_price = bar.open * (1 + side_multiplier * adverse_bps / 10_000)
            notional = order.qty * fill_price
            entry_fee = self.commission + (
                notional * self.crypto_taker_fee_bps / 10_000
                if order.market == Market.CRYPTO
                else 0.0
            )
            if order.direction == Direction.LONG and notional + entry_fee > self.cash:
                self.order_rejections[order.client_order_id] = "insufficient_cash"
                self._entry_terminal_event(order, OrderState.REJECTED, bar, 0.0, None)
                continue
            if order.direction == Direction.LONG:
                self.cash -= notional + entry_fee
            else:
                self.cash += notional - entry_fee
            self.positions[order.symbol] = _Position(
                order.symbol,
                order.qty,
                fill_price,
                bar.close,
                order.market,
                order.stop_price,
                order.target_price,
                order.client_order_id,
                order.direction,
                entry_fee,
            )
            self.fills.append(
                Fill(
                    order.client_order_id,
                    order.symbol,
                    order.qty,
                    fill_price,
                    order.direction,
                    bar.timestamp,
                    entry_fee,
                )
            )
            self._entry_terminal_event(order, OrderState.FILLED, bar, order.qty, fill_price)
        self.pending = remaining

        # 2-4. Newly-filled and existing positions see this bar's open and
        # intrabar range. This prevents impossible survival through an entry bar.
        position = self.positions.get(bar.symbol)
        if position is not None:
            position.current_price = bar.close
            exit_price = self._exit_price(position, bar)
            if exit_price is not None:
                self._close(position, exit_price, bar)

    def _exit_price(self, position: _Position, bar: Bar) -> float | None:
        long = position.direction == Direction.LONG
        if long and bar.open <= position.stop:
            raw = bar.open
            is_stop = True
        elif not long and bar.open >= position.stop:
            raw = bar.open
            is_stop = True
        elif long and bar.open >= position.target:
            raw = position.target
            is_stop = False
        elif not long and bar.open <= position.target:
            raw = position.target
            is_stop = False
        else:
            stop_hit = bar.low <= position.stop if long else bar.high >= position.stop
            target_hit = bar.high >= position.target if long else bar.low <= position.target
            if not stop_hit and not target_hit:
                return None
            if stop_hit and target_hit:
                choose_stop = self.intrabar_policy != "optimistic"
                if self.intrabar_policy == "ohlc_path":
                    choose_stop = bar.close >= bar.open if long else bar.close <= bar.open
                raw = position.stop if choose_stop else position.target
                is_stop = choose_stop
            else:
                raw = position.stop if stop_hit else position.target
                is_stop = stop_hit
        adverse = self.slippage_bps + self.spread_bps / 2
        if is_stop:
            return raw * (1 - adverse / 10_000 if long else 1 + adverse / 10_000)
        return raw * (1 - self.slippage_bps / 10_000 if long else 1 + self.slippage_bps / 10_000)

    def _close(self, position: _Position, price: float, bar: Bar) -> None:
        exit_fee = self.commission + (
            position.qty * price * self.crypto_taker_fee_bps / 10_000
            if position.market == Market.CRYPTO
            else 0.0
        )
        if position.direction == Direction.LONG:
            self.cash += position.qty * price - exit_fee
            pnl = (price - position.avg_price) * position.qty - position.entry_fee - exit_fee
        else:
            self.cash -= position.qty * price + exit_fee
            pnl = (position.avg_price - price) * position.qty - position.entry_fee - exit_fee
        self.realized_pnls.append(pnl)
        self.fills.append(
            Fill(
                position.client_order_id,
                position.symbol,
                position.qty,
                price,
                Direction.FLAT,
                bar.timestamp,
                exit_fee,
            )
        )
        del self.positions[position.symbol]

    def _entry_terminal_event(
        self,
        order: OrderIntent,
        state: OrderState,
        bar: Bar,
        cumulative_qty: float,
        average_price: float | None,
    ) -> None:
        self.execution_events.append(
            ExecutionEvent(
                account_id="backtest",
                client_order_id=order.client_order_id,
                broker_order_id=order.client_order_id,
                parent_order_id=None,
                role=OrderRole.ENTRY,
                symbol=order.symbol,
                market=order.market,
                state=state,
                requested_qty=order.qty,
                cumulative_filled_qty=cumulative_qty,
                average_fill_price=average_price,
                occurred_at=bar.timestamp,
            )
        )

    def drain_execution_events(self) -> list[ExecutionEvent]:
        events = self.execution_events
        self.execution_events = []
        return events
