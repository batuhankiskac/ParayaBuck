from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from decimal import ROUND_DOWN, ROUND_HALF_UP, ROUND_UP, Decimal
from pathlib import Path
from typing import Any

from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderClass, OrderSide, QueryOrderStatus, TimeInForce
from alpaca.trading.requests import (
    GetOrdersRequest,
    MarketOrderRequest,
    StopLimitOrderRequest,
    StopLossRequest,
    StopOrderRequest,
    TakeProfitRequest,
)

from parayabuck.core.models import (
    AccountSnapshot,
    Direction,
    Fill,
    Market,
    OrderIntent,
    OrderState,
    PositionSnapshot,
)
from parayabuck.logger import get_logger
from parayabuck.utils import is_crypto
from parayabuck.utils.http import patch_session_timeout

log = get_logger("parayabuck.execution.alpaca")


class AlpacaBroker:
    """Alpaca execution boundary using the engine's native models."""

    def __init__(self, api_key: str, secret_key: str, paper: bool = True) -> None:
        self._client = TradingClient(api_key, secret_key, paper=paper)
        patch_session_timeout(self._client._session, timeout=15.0)
        self._peak_state_path = Path("data/runtime") / (
            "paper_equity_peak.json" if paper else "live_equity_peak.json"
        )
        self._peak_equity = self._load_peak_equity()
        self._asset_cache: dict[str, Any] = {}

    def account(self) -> AccountSnapshot:
        account: Any = self._client.get_account()
        if bool(getattr(account, "account_blocked", False)) or bool(
            getattr(account, "trading_blocked", False)
        ):
            raise RuntimeError("Alpaca account is blocked from trading")
        equity = float(account.equity or 0)
        previous_equity = float(account.last_equity or equity)
        positions: list[PositionSnapshot] = []
        unrealized = 0.0
        intraday_unrealized = 0.0
        for raw in self._client.get_all_positions():
            current_price = float(raw.current_price)
            unrealized += float(raw.unrealized_pl or 0)
            intraday_unrealized += float(raw.unrealized_intraday_pl or 0)
            asset_class = str(getattr(raw, "asset_class", "")).lower()
            market = (
                Market.CRYPTO
                if "crypto" in asset_class or is_crypto(str(raw.symbol))
                else Market.EQUITY
            )
            positions.append(
                PositionSnapshot(
                    symbol=str(raw.symbol),
                    qty=float(raw.qty),
                    avg_price=float(raw.avg_entry_price),
                    current_price=current_price,
                    market=market,
                )
            )
        old_peak = self._peak_equity
        self._peak_equity = max(self._peak_equity, equity)
        if self._peak_equity != old_peak:
            self._save_peak_equity()
        daily_pnl = equity - previous_equity
        return AccountSnapshot(
            cash=float(account.cash or 0),
            equity=equity,
            peak_equity=self._peak_equity,
            daily_pnl=daily_pnl,
            positions=tuple(positions),
            realized_pnl=daily_pnl - intraday_unrealized,
            unrealized_pnl=unrealized,
        )

    def submit(self, intent: OrderIntent) -> tuple[OrderState, Fill | None]:
        try:
            qty, stop_price, target_price = self._validated_values(intent)
            side = OrderSide.BUY if intent.direction == Direction.LONG else OrderSide.SELL
            tif = TimeInForce.GTC if intent.market == Market.CRYPTO else TimeInForce.DAY
            if intent.market == Market.EQUITY:
                request: Any = MarketOrderRequest(
                    symbol=intent.symbol,
                    qty=qty,
                    side=side,
                    time_in_force=tif,
                    order_class=OrderClass.BRACKET,
                    stop_loss=StopLossRequest(stop_price=stop_price),
                    take_profit=TakeProfitRequest(limit_price=target_price),
                    client_order_id=intent.client_order_id,
                )
            else:
                request = MarketOrderRequest(
                    symbol=intent.symbol,
                    qty=qty,
                    side=side,
                    time_in_force=tif,
                    client_order_id=intent.client_order_id,
                )
            result: Any = self._client.submit_order(request)
            state, fill = self._wait_for_fill(str(result.id), intent, qty)
            if intent.market == Market.CRYPTO and state == OrderState.ACCEPTED:
                self._client.cancel_order_by_id(result.id)
                if fill is None:
                    return OrderState.CANCELLED, None
            if intent.market == Market.CRYPTO and fill is not None:
                protected = self._submit_crypto_stop(intent, fill.qty, stop_price)
                if not protected:
                    self._flatten(intent, fill.qty)
                    return OrderState.REJECTED, fill
            return state, fill
        except Exception as exc:
            log.error("Order rejected: %s", exc)
            return OrderState.REJECTED, None

    def reconcile(self) -> None:
        account = self.account()
        for position in account.positions:
            if position.is_flat or self._has_open_order(position.symbol):
                continue
            is_long = position.qty > 0
            stop_pct = 0.025 if position.market == Market.CRYPTO else 0.04
            stop_price = position.avg_price * (1 - stop_pct if is_long else 1 + stop_pct)
            protected = self._submit_stop(
                position.symbol,
                position.market,
                abs(position.qty),
                stop_price,
                is_long,
            )
            if not protected:
                raise RuntimeError(f"Could not restore protection for {position.symbol}")

    def _validated_values(self, intent: OrderIntent) -> tuple[float, float, float]:
        asset = self._asset_cache.get(intent.symbol)
        if asset is None:
            asset = self._client.get_asset(intent.symbol)
            self._asset_cache[intent.symbol] = asset
        if not bool(getattr(asset, "tradable", False)):
            raise ValueError(f"Asset is not tradable: {intent.symbol}")
        qty = Decimal(str(intent.qty))
        if intent.market == Market.EQUITY and not float(intent.qty).is_integer() and not bool(
            getattr(asset, "fractionable", False)
        ):
            raise ValueError(f"Asset is not fractionable: {intent.symbol}")
        minimum = getattr(asset, "min_order_size", None)
        increment = getattr(asset, "min_trade_increment", None)
        adjusted = False
        if minimum is not None and qty < Decimal(str(minimum)):
            qty = Decimal(str(minimum))
            adjusted = True
        if increment is not None:
            step = Decimal(str(increment))
            qty = (qty / step).to_integral_value(
                rounding=ROUND_UP if adjusted else ROUND_DOWN
            ) * step
        if qty <= 0:
            raise ValueError(f"Order rounds to zero for {intent.symbol}")
        price_increment = getattr(asset, "price_increment", None)
        if price_increment is None:
            return float(qty), intent.stop_price, intent.target_price
        step = Decimal(str(price_increment))
        def rounded(value: float) -> float:
            return float(
                (Decimal(str(value)) / step).to_integral_value(rounding=ROUND_HALF_UP) * step
            )

        return float(qty), rounded(intent.stop_price), rounded(intent.target_price)

    def _wait_for_fill(
        self, order_id: str, intent: OrderIntent, qty: float, timeout: float = 5.0
    ) -> tuple[OrderState, Fill | None]:
        deadline = time.monotonic() + timeout
        fill: Fill | None = None
        while time.monotonic() < deadline:
            try:
                order: Any = self._client.get_order_by_id(order_id)
                status = str(order.status).lower().split(".")[-1]
                filled_qty = float(order.filled_qty or 0)
                filled_price = float(order.filled_avg_price or 0)
                if filled_qty > 0 and filled_price > 0:
                    fill = Fill(
                        intent.client_order_id,
                        intent.symbol,
                        filled_qty,
                        filled_price,
                        intent.direction,
                        datetime.now(UTC),
                    )
                if status == "filled":
                    return OrderState.FILLED, fill
                if status == "partially_filled":
                    state = OrderState.PARTIALLY_FILLED
                elif status in {"canceled", "cancelled"}:
                    return OrderState.CANCELLED, fill
                elif status == "expired":
                    return OrderState.EXPIRED, fill
                elif status == "rejected":
                    return OrderState.REJECTED, fill
                else:
                    state = OrderState.ACCEPTED
            except Exception:
                state = OrderState.ACCEPTED
            time.sleep(0.5)
        log.warning("Order %s not filled within %.1fs", order_id, timeout)
        return state, fill

    def _submit_crypto_stop(self, intent: OrderIntent, qty: float, stop: float) -> bool:
        return self._submit_stop(
            intent.symbol,
            intent.market,
            qty,
            stop,
            intent.direction == Direction.LONG,
        )

    def _submit_stop(
        self,
        symbol: str,
        market: Market,
        qty: float,
        stop_price: float,
        position_is_long: bool,
    ) -> bool:
        side = OrderSide.SELL if position_is_long else OrderSide.BUY
        try:
            if market == Market.CRYPTO:
                precision = 6 if stop_price < 1 else 2
                stop_price = round(stop_price, precision)
                limit_price = round(
                    stop_price * (0.995 if position_is_long else 1.005), precision
                )
                request: Any = StopLimitOrderRequest(
                    symbol=symbol,
                    qty=qty,
                    side=side,
                    time_in_force=TimeInForce.GTC,
                    stop_price=stop_price,
                    limit_price=limit_price,
                )
            else:
                request = StopOrderRequest(
                    symbol=symbol,
                    qty=qty,
                    side=side,
                    time_in_force=TimeInForce.GTC,
                    stop_price=stop_price,
                )
            self._client.submit_order(request)
            return True
        except Exception as exc:
            log.error("Protective stop failed for %s: %s", symbol, exc)
            return False

    def _flatten(self, intent: OrderIntent, qty: float) -> None:
        side = OrderSide.SELL if intent.direction == Direction.LONG else OrderSide.BUY
        self._client.submit_order(
            MarketOrderRequest(
                symbol=intent.symbol,
                qty=qty,
                side=side,
                time_in_force=TimeInForce.GTC,
            )
        )

    def _has_open_order(self, symbol: str) -> bool:
        orders = self._client.get_orders(
            GetOrdersRequest(status=QueryOrderStatus.OPEN, symbols=[symbol], nested=True)
        )
        return bool(orders)

    def _load_peak_equity(self) -> float:
        if not self._peak_state_path.exists():
            return 0.0
        try:
            return max(0.0, float(json.loads(self._peak_state_path.read_text())["peak_equity"]))
        except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
            log.warning("Could not read persisted equity peak; starting from current equity")
            return 0.0

    def _save_peak_equity(self) -> None:
        self._peak_state_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._peak_state_path.with_suffix(".tmp")
        temporary.write_text(json.dumps({"peak_equity": self._peak_equity}))
        temporary.replace(self._peak_state_path)


__all__ = ["AlpacaBroker"]
