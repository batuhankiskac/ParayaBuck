from __future__ import annotations

from pathlib import Path

from parayabuck.execution.alpaca import AlpacaBroker as LegacyAlpacaBroker
from parayabuck.models import Order, OrderStatus, OrderType, Side, TimeInForce
from parayabuck.utils import is_crypto
from parayabuck_v2.core.models import (
    AccountSnapshot,
    Direction,
    Fill,
    Market,
    OrderIntent,
    OrderState,
    PositionSnapshot,
)


class AlpacaExecutionBroker:
    """V2 execution boundary backed by the hardened Alpaca client."""

    def __init__(self, api_key: str, secret_key: str, paper: bool = True) -> None:
        peak_path = Path("data/runtime") / (
            "paper_equity_peak.json" if paper else "live_equity_peak.json"
        )
        self._broker = LegacyAlpacaBroker(
            api_key,
            secret_key,
            paper=paper,
            peak_state_path=peak_path,
        )

    def account(self) -> AccountSnapshot:
        account = self._broker.get_account()
        positions = tuple(
            PositionSnapshot(
                symbol=position.symbol,
                qty=position.qty,
                avg_price=position.avg_price,
                current_price=position.current_price or position.avg_price,
                market=Market.CRYPTO if is_crypto(position.symbol) else Market.EQUITY,
            )
            for position in account.positions.values()
            if not position.is_flat
        )
        return AccountSnapshot(
            cash=account.balance,
            equity=account.equity,
            peak_equity=account.peak_equity,
            daily_pnl=account.daily_pnl,
            positions=positions,
            realized_pnl=account.realized_pnl,
            unrealized_pnl=account.unrealized_pnl,
        )

    def submit(self, intent: OrderIntent) -> tuple[OrderState, Fill | None]:
        side = Side.BUY if intent.direction == Direction.LONG else Side.SELL
        tif = TimeInForce.GTC if intent.market == Market.CRYPTO else TimeInForce.DAY
        order = Order(
            symbol=intent.symbol,
            side=side,
            qty=intent.qty,
            order_type=OrderType.MARKET,
            stop_price=intent.stop_price if intent.market == Market.EQUITY else None,
            limit_price=intent.target_price if intent.market == Market.EQUITY else None,
            tif=tif,
            client_order_id=intent.client_order_id,
        )
        result = self._broker.submit_order(order)
        if intent.market == Market.CRYPTO and result.status == OrderStatus.PENDING:
            # Crypto cannot use native bracket orders. Never leave a delayed,
            # unprotected entry resting after the synchronous fill window.
            if result.order_id is not None:
                self._broker.cancel_order(result.order_id)
            if result.filled_qty <= 0:
                return OrderState.CANCELLED, None

        protection_failed = False
        if intent.market == Market.CRYPTO and result.filled_qty > 0:
            protective = self._broker.submit_crypto_protective_stop(
                intent.symbol,
                result.filled_qty,
                intent.stop_price,
                position_is_long=intent.direction == Direction.LONG,
            )
            protection_failed = protective.status in {
                OrderStatus.REJECTED,
                OrderStatus.CANCELLED,
            }
            if protection_failed:
                # Fail safe: flatten the filled quantity immediately.
                self._broker.submit_order(
                    Order(
                        symbol=intent.symbol,
                        side=Side.SELL if intent.direction == Direction.LONG else Side.BUY,
                        qty=result.filled_qty,
                        order_type=OrderType.MARKET,
                        tif=TimeInForce.GTC,
                    )
                )
        state_map = {
            OrderStatus.FILLED: OrderState.FILLED,
            OrderStatus.PENDING: OrderState.ACCEPTED,
            OrderStatus.CANCELLED: OrderState.CANCELLED,
            OrderStatus.REJECTED: OrderState.REJECTED,
        }
        state = OrderState.REJECTED if protection_failed else state_map[result.status]
        fill = None
        if result.filled_qty > 0 and result.filled_price is not None:
            fill = Fill(
                intent.client_order_id,
                intent.symbol,
                result.filled_qty,
                result.filled_price,
                intent.direction,
                result.filled_at or intent.created_at,
            )
        return state, fill

    def reconcile(self) -> None:
        account = self._broker.get_account()
        for position in account.positions.values():
            if position.is_flat or self._broker.has_open_order(position.symbol):
                continue
            crypto = is_crypto(position.symbol)
            stop_pct = 0.025 if crypto else 0.04
            stop_price = position.avg_price * (1 - stop_pct if position.is_long else 1 + stop_pct)
            protective = self._broker.submit_protective_stop(
                position.symbol,
                abs(position.qty),
                stop_price,
                position_is_long=position.is_long,
            )
            if protective.status in {OrderStatus.REJECTED, OrderStatus.CANCELLED}:
                raise RuntimeError(f"Could not restore protection for {position.symbol}")
