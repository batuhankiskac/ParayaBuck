from __future__ import annotations

import json
import math
import re
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import ROUND_CEILING, ROUND_DOWN, ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any
from uuid import uuid4

from alpaca.trading.enums import OrderSide, QueryOrderStatus, TimeInForce
from alpaca.trading.requests import (
    GetOrderByIdRequest,
    GetOrdersRequest,
    MarketOrderRequest,
    StopLimitOrderRequest,
    StopOrderRequest,
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
from parayabuck.execution.alpaca_client import AlpacaClient
from parayabuck.execution.ledger import ExecutionLedger, ManagedPosition
from parayabuck.execution.portfolio import (
    AssetTradingRules,
    BrokerWorkingBuyOrder,
    PortfolioOrderRequest,
    floor_increment_decimal,
)
from parayabuck.execution.risk_peak import RiskPeakService
from parayabuck.logger import get_logger
from parayabuck.markets.symbols import canonical_symbol, normalize_symbol, same_symbol
from parayabuck.utils import is_crypto

log = get_logger("parayabuck.execution.alpaca")

from parayabuck.execution.alpaca_account import AccountService  # noqa: E402
from parayabuck.execution.alpaca_crypto import CryptoService  # noqa: E402
from parayabuck.execution.alpaca_orders import OrderService  # noqa: E402
from parayabuck.execution.alpaca_protection import ProtectionService  # noqa: E402
from parayabuck.execution.alpaca_reconciliation import ReconciliationService  # noqa: E402

from .alpaca_models import (  # noqa: E402
    CRYPTO_TAKER_FEE_RESERVE,
    AmbiguousOrderState,
    CryptoPriceObservation,
    OwnershipDriftError,
    ProtectionReconciliationError,
    QuantityResolution,
    RiskStatePersistenceError,
)


def _emit_alert(kind: str, *, message: str, **fields: object) -> None:
    """Emit one execution alert without ever affecting order flow.

    The ``parayabuck.monitoring.alerts`` import is guarded so a missing (or
    broken) alerts module degrades to a logged no-op instead of failing a
    submission or a fill.
    """
    try:
        from parayabuck.monitoring import alerts
    except ImportError:
        log.debug("alerts module unavailable; skipping %s alert", kind)
        return
    try:
        alerts.emit(kind, message=message, **fields)
    except Exception:
        log.warning("Alert emission failed for kind=%s (order flow unaffected)", kind, exc_info=True)


class AlpacaBroker:
    """Alpaca execution boundary using the engine's native models."""

    def __init__(
        self,
        api_key: str,
        secret_key: str,
        paper: bool = True,
        *,
        manage_external_positions: bool = False,
        ledger: ExecutionLedger | None = None,
        portfolio_owned_quantities: Callable[[], dict[str, float]] | None = None,
        portfolio_open_orders: Callable[[], object] | None = None,
        portfolio_fill_recovered: (Callable[..., None] | None) = None,
        portfolio_order_quantity_adopted: (Callable[[str, float], None] | None) = None,
        portfolio_stop_recorded: (Callable[[str, Market, float, str, float], None] | None) = None,
        portfolio_flat_reconciled: (Callable[[str, Market], object] | None) = None,
        crypto_stop_limit_offset_bps: float = 25.0,
        crypto_emergency_exit_bps: float = 75.0,
        crypto_stop_cancel_timeout_seconds: float = 5.0,
        crypto_stop_cancel_poll_interval_seconds: float = 0.25,
        runtime_state_dir: str | Path | None = None,
        peak_state_path: str | Path | None = None,
        client: Any | None = None,
        client_adapter: Any | None = None,
    ) -> None:
        if client_adapter is not None:
            self._client_adapter = client_adapter
            try:
                object.__setattr__(
                    self, "_client_inner", getattr(client_adapter, "_client", getattr(client_adapter, "client", None))
                )
                object.__setattr__(self, "_asset_cache_inner", getattr(client_adapter, "_asset_cache", {}))
            except Exception:
                pass
        elif client is not None:
            self._client_adapter = AlpacaClient(api_key, secret_key, paper=paper, client=client)
            object.__setattr__(self, "_client_inner", self._client_adapter._client)
            object.__setattr__(self, "_asset_cache_inner", self._client_adapter._asset_cache)
        else:
            self._client_adapter = AlpacaClient(api_key, secret_key, paper=paper)
            object.__setattr__(self, "_client_inner", self._client_adapter._client)
            object.__setattr__(self, "_asset_cache_inner", self._client_adapter._asset_cache)
        self._paper = paper
        self._risk_peak = RiskPeakService(
            paper=paper, ledger=ledger, peak_state_path=peak_state_path, runtime_state_dir=runtime_state_dir
        )
        self.manage_external_positions = manage_external_positions
        self.ledger = ledger
        self.portfolio_owned_quantities = portfolio_owned_quantities
        self.portfolio_open_orders = portfolio_open_orders
        self.portfolio_fill_recovered = portfolio_fill_recovered
        self.portfolio_order_quantity_adopted = portfolio_order_quantity_adopted
        self.portfolio_stop_recorded = portfolio_stop_recorded
        self.portfolio_flat_reconciled = portfolio_flat_reconciled
        self._account_id = ""
        # Immutable snapshot cache: every fresh account() seeds it; callers
        # that tolerate bounded staleness (config-sanctioned entry-gate
        # freshness) may reuse it via snapshot_if_fresh() instead of issuing
        # another get_account + get_all_positions round-trip.
        self._snapshot_cache: AccountSnapshot | None = None
        self._snapshot_cached_at: float | None = None
        self.crypto_stop_limit_offset_bps = crypto_stop_limit_offset_bps
        self.crypto_emergency_exit_bps = crypto_emergency_exit_bps
        self.crypto_stop_cancel_timeout_seconds = crypto_stop_cancel_timeout_seconds
        self.crypto_stop_cancel_poll_interval_seconds = crypto_stop_cancel_poll_interval_seconds
        self._symbol_exit_locks: dict[str, threading.Lock] = {}
        self._symbol_exit_locks_guard = threading.Lock()
        # Composition: services own the heavy lifting; facade delegates.
        self._account_service = AccountService(self)
        self._order_service = OrderService(self)
        self._protection_service = ProtectionService(self)
        self._crypto_service = CryptoService(self)
        self._reconciliation_service = ReconciliationService(self)

    @property
    def _client(self) -> Any:
        adapter = self.__dict__.get("_client_adapter")
        if adapter is not None:
            return adapter._client
        return self.__dict__.get("_client_inner")

    @_client.setter
    def _client(self, value: Any) -> None:
        adapter = self.__dict__.get("_client_adapter")
        if adapter is not None:
            try:
                adapter._client = value
            except Exception:
                pass
            sess = getattr(value, "_session", None)
            if sess is not None:
                try:
                    from parayabuck.utils.http import patch_session_timeout

                    patch_session_timeout(sess, timeout=15.0)
                except Exception:
                    pass
        else:
            try:
                adapter_obj = AlpacaClient.__new__(AlpacaClient)
                adapter_obj._client = value
                existing_cache = self.__dict__.get("_asset_cache_inner") or self.__dict__.get("_asset_cache") or {}
                if isinstance(existing_cache, dict):
                    adapter_obj._asset_cache = existing_cache
                else:
                    adapter_obj._asset_cache = {}
                adapter_obj._closed = False
                sess = getattr(value, "_session", None)
                if sess is not None:
                    try:
                        from parayabuck.utils.http import patch_session_timeout

                        patch_session_timeout(sess, timeout=15.0)
                    except Exception:
                        pass
                self.__dict__["_client_adapter"] = adapter_obj
            except Exception:
                pass
        self.__dict__["_client_inner"] = value

    @property
    def _asset_cache(self) -> Any:
        adapter = self.__dict__.get("_client_adapter")
        if adapter is not None:
            return adapter._asset_cache
        return self.__dict__.get("_asset_cache_inner", {})

    @_asset_cache.setter
    def _asset_cache(self, value: Any) -> None:
        adapter = self.__dict__.get("_client_adapter")
        if adapter is not None:
            try:
                adapter._asset_cache = value
            except Exception:
                pass
        self.__dict__["_asset_cache_inner"] = value
        if adapter is not None and isinstance(value, dict):
            try:
                adapter._asset_cache = value
            except Exception:
                pass

    # -- Peak sidecar delegation (risk_peak service, facade API unchanged) --
    def _get_peak_service(self) -> RiskPeakService:
        rp = self.__dict__.get("_risk_peak")
        if rp is not None:
            try:
                rp.ledger = self.__dict__.get("ledger", self.__dict__.get("_ledger"))
            except Exception:
                pass
            try:
                rp._paper = bool(self.__dict__.get("_paper", True))
            except Exception:
                pass
            try:
                acct = self.__dict__.get("_account_id", "")
                rp._broker_account_id = acct if acct else None
            except Exception:
                pass
            return rp  # type: ignore[no-any-return]
        paper = bool(self.__dict__.get("_paper", True))
        ledger = self.__dict__.get("ledger", self.__dict__.get("_ledger"))
        peak_path = self.__dict__.get("_peak_state_path")
        if peak_path is None and "_peak_state_path" in self.__dict__:
            peak_path = self.__dict__["_peak_state_path"]
        rp = RiskPeakService(paper=paper, ledger=ledger, peak_state_path=peak_path)
        # Override eager-loaded state with broker's own attributes (for __new__ test isolation).
        # Base __new__ brokers start with no peak binding; service eager load would otherwise
        # retain stale file account_id (e.g. 4edcc...) and cause false fail-closed.
        for attr, svc_attr in [
            ("_peak_equity", "_peak_equity"),
            ("_peak_epoch", "_peak_epoch"),
            ("_peak_account_id", "_peak_account_id"),
            ("_peak_reconciled", "_peak_reconciled"),
        ]:
            if attr in self.__dict__:
                try:
                    setattr(rp, svc_attr, self.__dict__[attr])
                except Exception:
                    pass
            else:
                # No broker attribute -> reset to clean defaults, discarding eager file load
                # (matches base __new__ semantics where _peak_account_id is None)
                try:
                    if svc_attr == "_peak_equity":
                        rp._peak_equity = 0.0
                    elif svc_attr == "_peak_epoch":
                        rp._peak_epoch = 0
                    elif svc_attr == "_peak_account_id":
                        rp._peak_account_id = None
                    elif svc_attr == "_peak_reconciled":
                        rp._peak_reconciled = False
                except Exception:
                    pass
        try:
            acct = self.__dict__.get("_account_id", "")
            rp._broker_account_id = acct if acct else None
        except Exception:
            pass
        self.__dict__["_risk_peak"] = rp
        return rp

    @property
    def _peak_state_path(self) -> Path:
        rp = self.__dict__.get("_risk_peak")
        if rp is not None:
            return rp.peak_state_path  # type: ignore[no-any-return]
        return self.__dict__.get("_peak_state_path", Path("data/runtime/paper_equity_peak.json"))  # type: ignore[no-any-return]

    @_peak_state_path.setter
    def _peak_state_path(self, value: Path) -> None:
        rp = self.__dict__.get("_risk_peak")
        if rp is not None:
            rp.peak_state_path = Path(value)
        else:
            self.__dict__["_peak_state_path"] = Path(value)

    @property
    def _peak_equity(self) -> float:
        rp = self.__dict__.get("_risk_peak")
        if rp is not None:
            return float(rp.peak_equity)
        return float(self.__dict__.get("_peak_equity", 0.0))

    @_peak_equity.setter
    def _peak_equity(self, value: float) -> None:
        rp = self.__dict__.get("_risk_peak")
        if rp is not None:
            rp.peak_equity = float(value)
        else:
            self.__dict__["_peak_equity"] = float(value)

    @property
    def _peak_epoch(self) -> int:
        rp = self.__dict__.get("_risk_peak")
        if rp is not None:
            return int(rp.peak_epoch)
        return int(self.__dict__.get("_peak_epoch", 0))

    @_peak_epoch.setter
    def _peak_epoch(self, value: int) -> None:
        rp = self.__dict__.get("_risk_peak")
        if rp is not None:
            rp.peak_epoch = int(value)
        else:
            self.__dict__["_peak_epoch"] = int(value)

    @property
    def _peak_account_id(self) -> str | None:
        rp = self.__dict__.get("_risk_peak")
        if rp is not None:
            return rp.peak_account_id  # type: ignore[no-any-return]
        return self.__dict__.get("_peak_account_id")

    @_peak_account_id.setter
    def _peak_account_id(self, value: str | None) -> None:
        rp = self.__dict__.get("_risk_peak")
        if rp is not None:
            rp.peak_account_id = value
        else:
            self.__dict__["_peak_account_id"] = value

    @property
    def _peak_reconciled(self) -> bool:
        rp = self.__dict__.get("_risk_peak")
        if rp is not None:
            return bool(rp.peak_reconciled)
        return bool(self.__dict__.get("_peak_reconciled", False))

    @_peak_reconciled.setter
    def _peak_reconciled(self, value: bool) -> None:
        rp = self.__dict__.get("_risk_peak")
        if rp is not None:
            rp.peak_reconciled = bool(value)
        else:
            self.__dict__["_peak_reconciled"] = bool(value)

    @property
    def account_suffix(self) -> str:
        return self._account_id[-4:] if self._account_id else ""

    def close(self) -> None:
        """Close the Alpaca REST session pool (no-op for fake clients)."""
        adapter = self.__dict__.get("_client_adapter")
        if adapter is not None:
            try:
                adapter.close()
            except Exception as exc:
                log.warning("Alpaca session close warning: %s", exc)
            return
        session = getattr(self._client, "_session", None)
        if session is None:
            return
        try:
            session.close()
        except Exception as exc:
            log.warning("Alpaca session close warning: %s", exc)

    @property
    def account_id(self) -> str:
        return self._account_id

    def portfolio_history(self, period: str, timeframe: str) -> list[tuple[datetime, float]]:
        """Broker equity history for display only; never feeds trading state.

        Points before the account was funded (zero equity) and non-finite
        values are dropped so the series starts at the first real balance.
        """
        from alpaca.trading.requests import GetPortfolioHistoryRequest

        raw: Any = self._client.get_portfolio_history(GetPortfolioHistoryRequest(period=period, timeframe=timeframe))
        stamps = list(getattr(raw, "timestamp", None) or [])
        values = list(getattr(raw, "equity", None) or [])
        points: list[tuple[datetime, float]] = []
        for stamp, value in zip(stamps, values, strict=False):
            if value is None:
                continue
            equity = float(value)
            if not math.isfinite(equity) or equity <= 0:
                continue
            points.append((datetime.fromtimestamp(int(stamp), UTC), equity))
        return points

    def snapshot_if_fresh(self, max_age_seconds: float) -> AccountSnapshot | None:
        """Return the last fetched account snapshot if it is at most
        ``max_age_seconds`` old, else ``None``.

        Delegates to :class:`AccountService` when composed; falls back to
        local cache for uninitialized ``__new__`` test doubles.
        """
        svc = self.__dict__.get("_account_service")
        if svc is not None:
            try:
                return svc.snapshot_if_fresh(max_age_seconds)  # type: ignore[no-any-return]
            except Exception:
                pass
        cached = self._snapshot_cache
        cached_at = self._snapshot_cached_at
        if cached is None or cached_at is None:
            return None
        if time.monotonic() - cached_at <= max_age_seconds:
            return cached
        return None

    def account(self) -> AccountSnapshot:
        svc = self.__dict__.get("_account_service")
        if svc is not None:
            return svc.account()  # type: ignore[no-any-return]
        account: Any = self._client.get_account()
        self._account_id = str(getattr(account, "id", "") or getattr(account, "account_number", ""))
        if (
            bool(getattr(account, "account_blocked", False))
            or bool(getattr(account, "trading_blocked", False))
            or bool(getattr(account, "trade_suspended_by_user", False))
        ):
            raise RuntimeError("Alpaca account is blocked from trading")
        equity = float(account.equity or 0)
        previous_equity = float(account.last_equity or equity)
        positions: list[PositionSnapshot] = []
        ledger = self.ledger
        managed_positions = ledger.managed_for_account(self._account_id or "unknown") if ledger is not None else {}
        owned_quantities: dict[str, float] = {}
        portfolio_owned_provider = getattr(self, "portfolio_owned_quantities", None)
        if portfolio_owned_provider is not None:
            owned_quantities = portfolio_owned_provider() or {}
        unrealized = 0.0
        intraday_unrealized = 0.0
        raw_positions: Any = self._client.get_all_positions()
        drift_candidates: list[tuple[str, float, float, Market]] = []
        seen_position_keys: set[tuple[Market, str]] = set()
        for raw in raw_positions:
            current_price = float(raw.current_price)
            unrealized += float(raw.unrealized_pl or 0)
            intraday_unrealized += float(raw.unrealized_intraday_pl or 0)
            asset_class = str(getattr(raw, "asset_class", "")).lower()
            market = Market.CRYPTO if "crypto" in asset_class or is_crypto(str(raw.symbol)) else Market.EQUITY
            canonical = canonical_symbol(str(raw.symbol), market)
            position_key = (market, canonical)
            if position_key in seen_position_keys:
                raise OwnershipDriftError(
                    f"Duplicate broker position for {canonical} ({market.value}); "
                    "reconciliation is required before trading resumes"
                )
            seen_position_keys.add(position_key)
            managed = managed_positions.get(canonical)
            raw_qty = float(raw.qty)
            ledger_owned = float(managed.owned_qty) if managed is not None else 0.0
            repo_owned = float(owned_quantities.get(canonical, 0.0))
            if ledger_owned > 1e-12 and repo_owned > 1e-12:
                raise OwnershipDriftError(
                    "Dual ownership lineage for "
                    f"{canonical}: ledger managed qty={ledger_owned} and "
                    f"portfolio managed qty={repo_owned} are both positive; "
                    "reconciliation is required before trading resumes"
                )
            total_owned = ledger_owned + repo_owned
            fee_reserve = float(Decimal(1) - CRYPTO_TAKER_FEE_RESERVE) if market == Market.CRYPTO else 1.0
            if (
                managed is None
                and total_owned > 1e-12
                and abs(raw_qty) > 1e-12
                and abs(raw_qty) + max(1e-9, total_owned * 1e-8) < total_owned * fee_reserve
            ):
                drift_candidates.append((canonical, total_owned, raw_qty, market))
            managed_qty = min(abs(raw_qty), total_owned)
            external_qty = abs(raw_qty) - managed_qty
            ownership = (
                "managed"
                if managed_qty > 1e-12 and external_qty <= 1e-12
                else "mixed"
                if managed_qty > 1e-12
                else "external"
            )
            positions.append(
                PositionSnapshot(
                    symbol=canonical,
                    qty=raw_qty,
                    avg_price=float(raw.avg_entry_price),
                    current_price=current_price,
                    market=market,
                    qty_available=(
                        float(raw.qty_available) if getattr(raw, "qty_available", None) is not None else None
                    ),
                    ownership=ownership,
                    managed_qty=managed_qty,
                    stop_price=(managed.current_stop or managed.original_stop if managed is not None else None),
                    initial_risk=(
                        abs(float(raw.avg_entry_price) - managed.original_stop) if managed is not None else None
                    ),
                )
            )
        if drift_candidates:
            repo_orders = self._portfolio_open_order_map()
            for canonical, owned_qty, raw_qty, market in drift_candidates:
                if self._open_order_explains_drift(canonical, owned_qty, raw_qty, market, repo_orders):
                    continue
                raise OwnershipDriftError(
                    f"Ownership drift for {canonical}: repository managed "
                    f"qty={owned_qty} exceeds broker position qty={raw_qty}"
                )
        self._ensure_peak_reconciled(self._account_id or "unknown", equity)
        old_peak = self._peak_equity
        self._peak_equity = max(self._peak_equity, equity)
        if self._peak_equity != old_peak:
            self._save_peak_equity()
        daily_pnl = equity - previous_equity
        cash = float(account.cash or 0)
        crypto_buying_power_raw = getattr(account, "non_marginable_buying_power", None)
        crypto_status = str(getattr(account, "crypto_status", "")).upper().split(".")[-1]
        snapshot = AccountSnapshot(
            cash=cash,
            equity=equity,
            peak_equity=self._peak_equity,
            daily_pnl=daily_pnl,
            positions=tuple(positions),
            realized_pnl=daily_pnl - intraday_unrealized,
            unrealized_pnl=unrealized,
            crypto_buying_power=(float(crypto_buying_power_raw) if crypto_buying_power_raw is not None else cash),
            crypto_trading_enabled=crypto_status == "ACTIVE",
            fees=(ledger.total_fees_for_account(self._account_id or "unknown") if ledger is not None else 0.0),
        )
        self._snapshot_cache = snapshot
        self._snapshot_cached_at = time.monotonic()
        return snapshot

    def asset_trading_rules(self, symbol: str, market: Market) -> AssetTradingRules:
        """Broker-authoritative increments introduced by Alpaca asset metadata."""
        asset = self._asset(symbol)
        if not bool(getattr(asset, "tradable", False)):
            raise ValueError(f"Asset is not tradable: {symbol}")
        minimum = float(getattr(asset, "min_order_size", None) or 0.000000001)
        quantity_increment = float(getattr(asset, "min_trade_increment", None) or 0.000000001)
        price_increment = float(
            getattr(asset, "price_increment", None) or (0.01 if market == Market.EQUITY else 0.000001)
        )
        minimum_notional = float(
            getattr(asset, "min_order_notional", None) or (1.0 if market == Market.EQUITY else 10.0)
        )
        return AssetTradingRules(
            canonical_symbol(symbol, market),
            market,
            minimum,
            quantity_increment,
            price_increment,
            minimum_notional,
        )

    def all_open_order_symbols(self) -> set[str]:
        """Broker-wide open order symbols (managed and manual).

        This is deliberately NOT the portfolio view: submission guards and
        the planner skip any symbol with a working order, managed or not,
        because the bot must not stack orders onto an existing flow.
        """
        orders = self._get_open_orders(GetOrdersRequest(status=QueryOrderStatus.OPEN, nested=True))
        return {
            canonical_symbol(str(self._field(order, "symbol", "")))
            for order in orders or ()
            if self._field(order, "symbol", "")
        }

    def _get_open_orders(self, request: Any) -> Any:
        """Use the adapter's exhaustive OPEN-order query for every broker path."""
        adapter = self.__dict__.get("_client_adapter")
        getter = getattr(adapter, "get_open_orders", None) if adapter is not None else None
        if getter is None:
            raise RuntimeError("Alpaca adapter does not support exhaustive open-order retrieval")
        return getter(request)

    def working_buy_orders(self) -> tuple[BrokerWorkingBuyOrder, ...]:
        """Exhaustive broker BUY commitments; an unpriced order stays unknown."""
        result: list[BrokerWorkingBuyOrder] = []
        seen: set[str] = set()
        for order in self.working_orders():
            if self._normalized_field(order, "side") != "buy":
                continue
            client_id = str(self._field(order, "client_order_id", "") or "").strip()
            symbol = str(self._field(order, "symbol", "") or "").strip()
            if not client_id or client_id in seen or not symbol:
                raise ValueError("Invalid broker working BUY identity")
            seen.add(client_id)
            market = Market.CRYPTO if is_crypto(symbol) else Market.EQUITY
            filled = float(self._field(order, "filled_qty", 0) or 0)
            raw_qty = self._field(order, "qty", None)
            quantity = float(raw_qty) if raw_qty is not None else None
            raw_notional = self._field(order, "notional", None)
            notional = float(raw_notional) if raw_notional is not None else None
            prices = [
                float(value)
                for field in ("limit_price", "stop_price", "filled_avg_price")
                if (value := self._field(order, field, None)) is not None
            ]
            if (
                not math.isfinite(filled)
                or filled < 0
                or (quantity is not None and (not math.isfinite(quantity) or quantity < filled))
                or (notional is not None and (not math.isfinite(notional) or notional < 0))
                or any(not math.isfinite(price) or price <= 0 for price in prices)
                or (quantity is None and notional is None)
            ):
                raise ValueError(f"Invalid broker working BUY quantity or price for {client_id}")
            average = self._field(order, "filled_avg_price", None)
            remaining_notional = (
                max(0.0, notional - filled * float(average))
                if notional is not None and (filled == 0 or average is not None)
                else None
            )
            result.append(
                BrokerWorkingBuyOrder(
                    client_order_id=client_id,
                    symbol=canonical_symbol(symbol, market),
                    market=market,
                    remaining_quantity=max(0.0, quantity - filled) if quantity is not None else None,
                    reference_price=max(prices) if prices else None,
                    remaining_notional=remaining_notional,
                )
            )
        return tuple(result)

    def working_orders(self) -> tuple[Any, ...]:
        """Complete OPEN order collection for operator slot/order counts."""
        return tuple(self._get_open_orders(GetOrdersRequest(status=QueryOrderStatus.OPEN, nested=True)))

    def _reconcile_stale_portfolio_ownership(self, symbol: str, market: Market) -> bool:
        """Resolve one stale portfolio symbol against real broker state.

        Returns True when the symbol is handled (real fill recovered, or an
        order is genuinely still working at the broker) and False when the
        caller should record an ownership adjustment.

        The race this handles: the broker filled a stop while the
        TradingStream update was delayed, the broker became flat, and the
        repository still claims ownership with a non-terminal exit order.
        """
        provider = getattr(self, "portfolio_open_orders", None)
        if provider is None:
            return False
        orders = [
            order
            for order in provider() or ()
            if str(getattr(order, "symbol", "")) == symbol
            and getattr(order, "market", None) in {None, market}
            and str(getattr(order, "side", "")).lower() == "sell"
        ]
        if not orders:
            return False
        for order in orders:
            client_order_id = str(getattr(order, "client_order_id", ""))
            broker_order_id = str(getattr(order, "broker_order_id", ""))
            if not client_order_id:
                continue
            broker_order = self._fetch_broker_order(client_order_id, broker_order_id)
            if broker_order is None:
                # No information at the broker about this order; do not guess.
                continue
            status = self._normalized_field(broker_order, "status")
            if status == "filled":
                recovered = self._fill_from_broker_order(broker_order, client_order_id, broker_order_id)
                if recovered is not None:
                    if fill_recovered := getattr(self, "portfolio_fill_recovered", None):
                        fill_recovered(*recovered)
                    return True
                continue
            if status not in {
                "canceled",
                "cancelled",
                "rejected",
                "expired",
                "done_for_day",
            }:
                # The exit is genuinely still working at the broker; keep
                # ownership until it completes.
                return True
            # The order died without filling: fall through to the adjustment.
        return False

    def _fetch_broker_order(self, client_order_id: str, broker_order_id: str) -> Any | None:
        """Resolve one repo order at Alpaca. A 404/unknown order is ``None``;
        network failures propagate so reconciliation never guesses."""
        get_by_client = getattr(self._client, "get_order_by_client_id", None)
        if get_by_client is not None:
            try:
                order = get_by_client(client_order_id)
            except Exception as exc:
                if getattr(exc, "status_code", None) != 404:
                    raise
                order = None
            if order is not None:
                return order
        if broker_order_id:
            get_by_id = getattr(self._client, "get_order_by_id", None)
            if get_by_id is not None:
                try:
                    return get_by_id(broker_order_id)
                except Exception as exc:
                    if getattr(exc, "status_code", None) != 404:
                        raise
        return None

    def resolve_portfolio_submission(
        self, client_order_id: str, broker_order_id: str = ""
    ) -> tuple[str, str] | str | None:
        """Resolve a portfolio order row left at ``submitting`` by a crash
        between repository persistence and broker acknowledgement.

        The deterministic client order id proves whether the order ever
        reached Alpaca:

        * unknown at the broker (404) -> ``"canceled"``: it can never fill;
        * present and filled -> the real fill is recovered through
          ``portfolio_fill_recovered`` (the same path startup reconciliation
          uses) and ``"filled"`` is returned;
        * present and terminal (canceled/expired/rejected/done_for_day) ->
          that status;
        * present and still working -> ``"submitted"``.

        Returns ``None`` when the broker is unreachable or the lookup
        capability is missing; the caller retries later without guessing.
        """
        get_by_client = getattr(self._client, "get_order_by_client_id", None)
        if get_by_client is None:
            return None
        broker_order = self._fetch_broker_order(client_order_id, broker_order_id)
        if broker_order is None:
            return "canceled"
        status = str(self._normalized_field(broker_order, "status") or "").lower()
        if status == "filled":
            recovered = self._fill_from_broker_order(broker_order, client_order_id, broker_order_id)
            if recovered is not None:
                if fill_recovered := getattr(self, "portfolio_fill_recovered", None):
                    fill_recovered(*recovered)
                return "filled"
            # The order filled but its fill could not be extracted; keep the
            # row non-terminal so ownership is not silently released.
            return "submitted"
        if status in {"canceled", "cancelled", "expired", "rejected", "done_for_day"}:
            return status
        return "submitted"

    def _fill_from_broker_order(
        self, order: Any, client_order_id: str, broker_order_id: str
    ) -> tuple[str, str, float, float, datetime, str, float] | None:
        """Extract the real fill from a filled Alpaca order."""
        filled_qty = float(self._field(order, "filled_qty", 0) or self._field(order, "cumulative_qty", 0) or 0)
        avg_price = float(self._field(order, "filled_avg_price", 0) or 0)
        if filled_qty <= 0 or avg_price <= 0:
            return None
        filled_at_raw = self._field(
            order, "filled_at", self._field(order, "submitted_at", datetime.now(UTC).isoformat())
        )
        occurred_at = datetime.fromisoformat(str(filled_at_raw))
        if occurred_at.tzinfo is None:
            occurred_at = occurred_at.replace(tzinfo=UTC)
        return (
            client_order_id,
            str(self._field(order, "id", "") or broker_order_id),
            filled_qty,
            avg_price,
            occurred_at,
            f"{client_order_id}:recovered:{filled_qty}",
            0.0,
        )

    def portfolio_open_order_symbols(self) -> set[str]:
        """Symbols with a non-terminal order owned by the portfolio path.

        Repository/account-aware: manual broker orders never appear here, so
        they cannot pin universe slots or suppress ownership drift.
        """
        provider = getattr(self, "portfolio_open_orders", None)
        if provider is None:
            return set()
        orders = provider() or ()
        return {canonical_symbol(str(getattr(order, "symbol", ""))) for order in orders if getattr(order, "symbol", "")}

    def _portfolio_open_order_map(self) -> dict[str, list[object]]:
        """Open portfolio orders grouped by canonical symbol (SELL legs)."""
        provider = getattr(self, "portfolio_open_orders", None)
        if provider is None:
            return {}
        grouped: dict[str, list[object]] = {}
        for order in provider() or ():
            symbol = str(getattr(order, "symbol", ""))
            if not symbol or str(getattr(order, "side", "")).lower() != "sell":
                continue
            market = getattr(order, "market", None)
            canonical = canonical_symbol(symbol, market)
            grouped.setdefault(canonical, []).append(order)
        return grouped

    @staticmethod
    def _open_order_explains_drift(
        canonical: str,
        owned_qty: float,
        raw_qty: float,
        market: Market,
        orders_by_symbol: dict[str, list[object]],
    ) -> bool:
        """Whether a working managed SELL can explain the ownership shortfall.

        The exemption requires a closing/risk-reducing SELL that belongs to
        the current portfolio repository/account, on the same symbol, with a
        remaining quantity large enough to cover the discrepancy. Anything
        else — a manual order, a BUY, or an under-sized SELL — keeps the
        drift fail-closed.
        """
        fee_reserve = float(Decimal(1) - CRYPTO_TAKER_FEE_RESERVE) if market == Market.CRYPTO else 1.0
        shortfall = max(0.0, owned_qty * fee_reserve - abs(raw_qty))
        if shortfall <= 1e-12:
            return False
        for order in orders_by_symbol.get(canonical, ()):
            order_market = getattr(order, "market", None)
            if order_market is not None and order_market != market:
                continue
            quantity = float(getattr(order, "quantity", 0.0) or 0.0)
            filled = float(getattr(order, "cumulative_filled_quantity", 0.0) or 0.0)
            remaining = max(0.0, quantity - filled)
            if remaining >= shortfall - max(1e-9, shortfall * 1e-8):
                return True
        return False

    def submit_portfolio_order(self, order: PortfolioOrderRequest) -> str:
        """Submit one simple long/cash rebalance leg with ambiguity recovery."""
        svc = self.__dict__.get("_order_service")
        if svc is None:
            svc = OrderService(self)
            self.__dict__["_order_service"] = svc
        return svc.submit_portfolio_order(order)

    def reconcile(self) -> dict[str, list[str]]:
        """Synchronize broker, stream and ledger state after any drift."""
        svc = self.__dict__.get("_reconciliation_service")
        if svc is None:
            svc = ReconciliationService(self)
            self.__dict__["_reconciliation_service"] = svc
        return svc.reconcile()

    def _reconcile_crypto_exit_state(self, ledger: Any) -> None:
        """Drive pending crypto exits and stop-failover paths to completion."""
        for entry in ledger.managed_entries_for_account(self._account_id or "unknown"):
            if entry.market != Market.CRYPTO:
                continue
            if entry.exit_client_order_id:
                self._reconcile_crypto_exit(entry)
            elif entry.stop_state == "cancel_requested" and entry.target_state in {
                "target_triggered",
                "stop_failover_triggered",
            }:
                self._cancel_stop_then_exit(
                    entry,
                    "target" if entry.target_state == "target_triggered" else "stop_failover",
                )

    def _reconcile_stale_ledger_entries(
        self,
        ledger: Any,
        broker_symbols: set[str],
        fill_activities_reconciled: bool,
    ) -> None:
        """Close managed entries the broker snapshot proves flat."""
        for entry in ledger.managed_entries_for_account(self._account_id or "unknown"):
            if entry.owned_qty <= 0 or entry.canonical_symbol in broker_symbols:
                continue
            if entry.exit_client_order_id:
                self._reconcile_crypto_exit(entry)
                continue
            if entry.market == Market.EQUITY and not fill_activities_reconciled:
                ledger.mark_manual_intervention(
                    self._account_id or "unknown",
                    entry.entry_client_order_id,
                )
                log.warning(
                    "Managed ledger close deferred: market=equity symbol=%s "
                    "entry_client_order_id=%s "
                    "recovery_action=await_fill_activity_reconciliation",
                    entry.canonical_symbol,
                    entry.entry_client_order_id,
                )
                continue
            # ``account()`` has successfully returned Alpaca's complete
            # position snapshot.  A managed symbol absent from that
            # snapshot is flat at the broker (for example, after a
            # bracket leg filled while the trading stream was offline).
            # Keep the durable ownership ledger in sync instead of
            # repeatedly halting all reconciliation on a stale entry.
            ledger.mark_position_closed(self._account_id or "unknown", entry.entry_client_order_id)
            log.warning(
                "Stale managed ledger entry closed: market=%s symbol=%s "
                "entry_client_order_id=%s managed_qty=%s "
                "recovery_action=broker_position_snapshot_confirmed_flat",
                entry.market.value,
                entry.canonical_symbol,
                entry.entry_client_order_id,
                entry.owned_qty,
            )

    def _reconcile_fill_activity_fees(self) -> bool:
        svc = self.__dict__.get("_reconciliation_service")
        if svc is None:
            try:
                svc = ReconciliationService(self)
                self.__dict__["_reconciliation_service"] = svc
            except Exception:
                return False
        return svc._reconcile_fill_activity_fees()

    def _reconcile_crypto_exit(self, position: ManagedPosition) -> None:
        svc = self.__dict__.get("_reconciliation_service")
        if svc is None:
            try:
                svc = ReconciliationService(self)
                self.__dict__["_reconciliation_service"] = svc
            except Exception:
                return
        svc._reconcile_crypto_exit(position)
        return

    def _reprotect_after_terminal_exit(self, position: ManagedPosition) -> None:
        svc = self.__dict__.get("_crypto_service") or self.__dict__.get("_reconciliation_service")
        if svc is not None and hasattr(svc, "_reprotect_after_terminal_exit"):
            svc._reprotect_after_terminal_exit(position)
            return None
        # Fallback
        return

    def _has_nested_bracket_protection(
        self,
        parent_order_ids: str | set[str],
        symbol: str,
        position_is_long: bool,
        required_qty: float,
    ) -> bool:
        svc = self.__dict__.get("_protection_service")
        if svc is not None:
            return svc._has_nested_bracket_protection(parent_order_ids, symbol, position_is_long, required_qty)  # type: ignore[no-any-return]
        closing_side = "sell" if position_is_long else "buy"

        def protective_qty(order: Any) -> Decimal:
            side = self._normalized_field(order, "side")
            order_type = self._normalized_field(order, "type")
            stop_price = self._field(order, "stop_price")
            status = self._normalized_field(order, "status")
            if (
                side == closing_side
                and status not in {"canceled", "expired", "filled", "rejected"}
                and (stop_price is not None or order_type in {"stop", "stop_limit", "trailing_stop"})
            ):
                # Only the still-working remainder protects the position; an
                # already-filled portion must never be double-counted.
                return max(
                    Decimal(0),
                    abs(Decimal(str(self._field(order, "qty", 0) or 0)))
                    - abs(Decimal(str(self._field(order, "filled_qty", 0) or 0))),
                )
            return sum(
                (protective_qty(leg) for leg in (self._field(order, "legs") or ())),
                start=Decimal(0),
            )

        ids = {parent_order_ids} if isinstance(parent_order_ids, str) else parent_order_ids
        protected = Decimal(0)
        for parent_order_id in ids:
            parent: Any = self._client.get_order_by_id(
                parent_order_id,
                GetOrderByIdRequest(nested=True),
            )
            if same_symbol(str(self._field(parent, "symbol", symbol)), symbol):
                protected += protective_qty(parent)
        matches, self._last_protection_mismatch = self._protection_matches(protected, Decimal(str(required_qty)))
        self._last_protected_qty = float(protected)
        return matches

    def _asset(self, symbol: str) -> Any:
        adapter = self.__dict__.get("_client_adapter")
        if adapter is not None:
            return adapter._asset(symbol)
        identity = normalize_symbol(symbol)
        cache_key = identity.canonical
        asset = (
            self._asset_cache.get(cache_key)
            or self._asset_cache.get(symbol)
            or self._asset_cache.get(identity.asset_lookup_symbol)
        )
        if asset is None:
            asset = self._client.get_asset(identity.asset_lookup_symbol)
            self._asset_cache[cache_key] = asset
        return asset

    def _price_step(self, symbol: str, market: Market, reference_price: float) -> Decimal:
        adapter = self.__dict__.get("_client_adapter")
        if adapter is not None:
            return adapter._price_step(symbol, market, reference_price)  # type: ignore[no-any-return]
        increment = getattr(self._asset(symbol), "price_increment", None)
        if increment is not None:
            return Decimal(str(increment))
        if market == Market.EQUITY:
            return Decimal("0.01") if reference_price >= 1 else Decimal("0.0001")
        return Decimal("0.01") if reference_price >= 1 else Decimal("0.000001")

    @staticmethod
    def _round_to_step(value: float, step: Decimal) -> float:
        try:
            return AlpacaClient._round_to_step(value, step)
        except Exception:
            rounded = (Decimal(str(value)) / step).to_integral_value(rounding=ROUND_HALF_UP) * step
            return float(rounded)

    def _wait_for_fill(
        self, order_id: str, intent: OrderIntent, timeout: float = 5.0
    ) -> tuple[OrderState, Fill | None]:
        deadline = time.monotonic() + timeout
        fill: Fill | None = None
        state = OrderState.ACCEPTED
        while time.monotonic() < deadline:
            try:
                order: Any = self._client.get_order_by_id(order_id)
                status = self._normalized_field(order, "status")
                filled_qty = float(self._field(order, "filled_qty", 0) or 0)
                filled_price = float(self._field(order, "filled_avg_price", 0) or 0)
                if filled_qty > 0 and filled_price > 0:
                    fill = Fill(
                        intent.client_order_id,
                        intent.symbol,
                        filled_qty,
                        filled_price,
                        intent.direction,
                        datetime.now(UTC),
                        commission=float(self._field(order, "fees", 0) or 0),
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
            except Exception as exc:
                log.warning("Order status lookup failed for %s: %s", order_id, exc)
                state = OrderState.ACCEPTED
            time.sleep(0.5)
        log.warning("Order %s not filled within %.1fs", order_id, timeout)
        return state, fill

    def _available_position_qty(
        self,
        symbol: str,
        requested_qty: float,
        *,
        reserve_fee_on_lookup_failure: bool,
        attempts: int = 5,
    ) -> float:
        resolution = self.resolve_available_position_qty(
            symbol,
            requested_qty,
            reserve_fee_on_lookup_failure=reserve_fee_on_lookup_failure,
            attempts=attempts,
        )
        return resolution.available_qty

    def resolve_available_position_qty(
        self,
        symbol: str,
        requested_qty: float,
        *,
        reserve_fee_on_lookup_failure: bool = False,
        attempts: int = 5,
    ) -> QuantityResolution:
        """Resolve an increment-safe close quantity and its verification source.

        The fee-reserve fallback is permitted only immediately after a verified
        crypto entry fill while protection is being installed. Exit paths must
        use the default fail-closed policy.
        """
        requested = Decimal(str(requested_qty))
        step = self._qty_step(symbol)
        saw_position = False
        attempted_logged = False

        for attempt in range(attempts):
            try:
                # Alpaca's position endpoint keys crypto by the legacy
                # no-slash form (``BTCUSD``); the canonical ``BTC/USD`` 404s.
                position: Any = self._client.get_open_position(normalize_symbol(symbol).asset_lookup_symbol)
                saw_position = True
                raw_available = (
                    position.get("qty_available")
                    if isinstance(position, dict)
                    else getattr(position, "qty_available", None)
                )
                if raw_available is None:
                    raw_available = (
                        position.get("qty", 0) if isinstance(position, dict) else getattr(position, "qty", 0)
                    )
                available = abs(Decimal(str(raw_available or 0)))
                qty = min(requested, available)
                qty = floor_increment_decimal(qty, step)
                if qty > 0:
                    if qty < requested:
                        log.info(
                            "Adjusted closing quantity for %s from %s to available %s",
                            symbol,
                            requested,
                            qty,
                        )
                    return QuantityResolution(True, float(qty), "broker_position")
            except AttributeError:
                # The SDK payload shape changed or the position vanished;
                # a lookup failure must fail closed but stay observable.
                log.warning(
                    "Position payload for %s is missing expected fields on attempt %d/%d",
                    symbol,
                    attempt + 1,
                    attempts,
                )
                break
            except Exception as exc:
                if not attempted_logged:
                    log.warning(
                        "Quantity lookup failed for %s on attempt %d/%d: %s",
                        symbol,
                        attempt + 1,
                        attempts,
                        exc,
                    )
                    attempted_logged = True
            if attempt + 1 < attempts:
                time.sleep(0.2)

        if saw_position:
            return QuantityResolution(True, 0.0, "broker_position_no_available_qty")
        if not reserve_fee_on_lookup_failure:
            return QuantityResolution(False, 0.0, "broker_lookup_failed")
        requested *= Decimal(1) - CRYPTO_TAKER_FEE_RESERVE
        requested = floor_increment_decimal(requested, step)
        return QuantityResolution(False, float(requested), "verified_fill_fee_reserve")

    def _submit_stop(
        self,
        symbol: str,
        market: Market,
        qty: float,
        stop_price: float,
        position_is_long: bool,
        client_order_id: str | None = None,
    ) -> str | None:
        """Submit a protective stop and return the accepted client order id.

        The retry path accepts a different client order id than the one that
        failed; callers that book the stop (e.g. the portfolio repository)
        must use the returned id so a later fill is attributed to it.
        """
        svc = self.__dict__.get("_protection_service")
        if svc is not None:
            return svc.submit_stop(symbol, market, qty, stop_price, position_is_long, client_order_id=client_order_id)  # type: ignore[no-any-return]
        client_order_id = client_order_id or f"parayabuck-stop-{uuid4().hex}"
        assert client_order_id is not None
        side = OrderSide.SELL if position_is_long else OrderSide.BUY
        step = self._price_step(symbol, market, stop_price)
        stop_price = self._round_to_step(stop_price, step)
        qty_number: float = qty
        equity_tif = TimeInForce.GTC
        if market == Market.EQUITY:
            qty_decimal = Decimal(str(qty))
            if qty_decimal == qty_decimal.to_integral_value():
                qty_number = int(qty_decimal)
            else:
                # Alpaca supports fractional equity stops only with DAY time-in-force.
                qty_number = float(qty_decimal.quantize(Decimal("0.000000001"), rounding=ROUND_DOWN))
                equity_tif = TimeInForce.DAY
        if qty_number <= 0 or stop_price <= 0:
            log.warning(
                "Skipping protective stop for %s: qty=%s stop=%s rounds to zero",
                symbol,
                qty,
                stop_price,
            )
            return None
        try:
            if market == Market.CRYPTO:
                offset = Decimal(str(getattr(self, "crypto_stop_limit_offset_bps", 25.0))) / Decimal(10000)
                stop_decimal = Decimal(str(stop_price))
                distance_steps = max(
                    1,
                    int((stop_decimal * offset / step).to_integral_value(rounding=ROUND_CEILING)),
                )
                limit_decimal = stop_decimal + (-distance_steps * step if position_is_long else distance_steps * step)
                limit_price = float(limit_decimal)
                if limit_price <= 0:
                    log.warning(
                        "Skipping protective stop for %s: limit price rounds to %s",
                        symbol,
                        limit_price,
                    )
                    return None
                request: Any = StopLimitOrderRequest(
                    symbol=symbol,
                    qty=qty_number,
                    side=side,
                    time_in_force=TimeInForce.GTC,
                    stop_price=stop_price,
                    limit_price=limit_price,
                    client_order_id=client_order_id,
                )
            else:
                request = StopOrderRequest(
                    symbol=symbol,
                    qty=qty_number,
                    side=side,
                    time_in_force=equity_tif,
                    stop_price=stop_price,
                    client_order_id=client_order_id,
                )
            result = self._submit_with_recovery(request, client_order_id)
            if market == Market.CRYPTO:
                broker_id = str(self._field(result, "id", "") or "")
                ledger = self.ledger
                if broker_id and ledger is not None and "-stop" in client_order_id:
                    entry_client_order_id = client_order_id.rsplit("-stop", 1)[0]
                    ledger.register_protective_stop(
                        self._account_id or "unknown",
                        entry_client_order_id,
                        client_order_id,
                        broker_id,
                    )
            return client_order_id
        except AmbiguousOrderState:
            log.critical(
                "Stop submit ambiguous: market=%s symbol=%s order_client_id=%s "
                "recovery_action=protection_reconciliation_required",
                market.value,
                symbol,
                client_order_id,
            )
            raise
        except Exception as exc:
            available = self._available_qty_from_error(exc)
            if market == Market.CRYPTO and 0 < available < qty_number:
                available = self._round_qty_down(symbol, available)
                if available > 0:
                    log.warning(
                        "Retrying protective stop for %s with Alpaca available qty %s",
                        symbol,
                        available,
                    )
                    try:
                        retry_client_order_id = f"parayabuck-stop-{uuid4().hex}"
                        retry_request = StopLimitOrderRequest(
                            symbol=symbol,
                            qty=available,
                            side=side,
                            time_in_force=TimeInForce.GTC,
                            stop_price=stop_price,
                            limit_price=limit_price,
                            client_order_id=retry_client_order_id,
                        )
                        result = self._submit_with_recovery(retry_request, retry_client_order_id)
                        ledger = self.ledger
                        broker_id = str(self._field(result, "id", "") or "")
                        if ledger is not None and broker_id and "-stop" in client_order_id:
                            ledger.register_protective_stop(
                                self._account_id or "unknown",
                                client_order_id.rsplit("-stop", 1)[0],
                                retry_client_order_id,
                                broker_id,
                            )
                        return retry_client_order_id
                    except AmbiguousOrderState:
                        raise
                    except Exception as retry_exc:
                        exc = retry_exc
            log.error("Protective stop failed for %s: %s", symbol, exc)
            return None

    def _round_qty_down(self, symbol: str, qty: float) -> float:
        return float(floor_increment_decimal(Decimal(str(qty)), self._qty_step(symbol)))

    def _qty_step(self, symbol: str) -> Decimal:
        """The asset's minimum trade increment, or a conservative default."""
        increment = getattr(self._asset(symbol), "min_trade_increment", None)
        return Decimal(str(increment)) if increment is not None else Decimal("0.000000001")

    @staticmethod
    def _available_qty_from_error(exc: Exception) -> float:
        """Extract the broker-reported available quantity from a rejection.

        Alpaca rejects an oversized sell with a JSON body whose ``available``
        value lives inside the message TEXT (``{"code": 40211000, "message":
        "insufficient qty available for base (requested: X, available: Y)"}``),
        not as a top-level key. Both shapes are accepted so synthetic and
        broker-native errors resolve identically.
        """
        message = str(exc)
        start = message.find("{")
        if start >= 0:
            try:
                payload = json.loads(message[start:])
                if "available" in payload:
                    return max(0.0, float(payload["available"]))
                message = str(payload.get("message", ""))
            except (TypeError, ValueError):
                pass
        match = re.search(r"available:\s*([0-9]+(?:\.[0-9]+)?)", message)
        return max(0.0, float(match.group(1))) if match else 0.0

    def _flatten(self, intent: OrderIntent, qty: float) -> tuple[Fill, str]:
        # Never call close_position(symbol): it closes the account's full
        # quantity and can consume manually-owned units of the same asset.
        side = OrderSide.SELL if intent.direction == Direction.LONG else OrderSide.BUY
        closing_qty = qty
        if intent.market == Market.CRYPTO:
            closing_qty = self._available_position_qty(
                intent.symbol,
                qty,
                reserve_fee_on_lookup_failure=intent.direction == Direction.LONG,
            )
        if closing_qty <= 0:
            raise RuntimeError(f"No available quantity to flatten for {intent.symbol}")
        before_qty = self._position_qty(intent.symbol)
        exit_client_order_id = f"{intent.client_order_id}-emergency-exit"
        result = self._submit_with_recovery(
            MarketOrderRequest(
                symbol=normalize_symbol(intent.symbol, intent.market).trading_symbol,
                qty=closing_qty,
                side=side,
                time_in_force=TimeInForce.GTC,
                client_order_id=exit_client_order_id,
            ),
            exit_client_order_id,
        )
        order_id = self._field(result, "id")
        if not order_id:
            raise AmbiguousOrderState(f"Emergency exit has no broker id for {intent.symbol}")
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline:
            try:
                order = self._client.get_order_by_id(order_id)
            except Exception as exc:
                # An emergency exit is exactly where a transient lookup error
                # must not abort the poll: keep polling like _wait_for_fill.
                log.warning("Emergency exit status lookup failed for %s: %s", order_id, exc)
                time.sleep(0.25)
                continue
            status = self._normalized_field(order, "status")
            if status == "filled":
                expected = max(0.0, before_qty - closing_qty)
                after_qty = self._position_qty(intent.symbol)
                tolerance = max(1e-9, closing_qty * 1e-8)
                if after_qty <= expected + tolerance:
                    price = float(self._field(order, "filled_avg_price", 0) or 0)
                    if price <= 0:
                        raise AmbiguousOrderState(f"Emergency exit fill price is unverified for {intent.symbol}")
                    return (
                        Fill(
                            exit_client_order_id,
                            intent.symbol,
                            closing_qty,
                            price,
                            Direction.FLAT,
                            datetime.now(UTC),
                        ),
                        str(order_id),
                    )
                raise AmbiguousOrderState(
                    f"Emergency exit filled but position quantity is unverified for {intent.symbol}"
                )
            if status in {"rejected", "canceled", "cancelled", "expired"}:
                raise RuntimeError(f"Emergency exit {status} for exact managed quantity {intent.symbol}")
            time.sleep(0.25)
        raise AmbiguousOrderState(f"Emergency exit timed out for {intent.symbol}")

    def _position_qty(self, symbol: str) -> float:
        try:
            position = self._client.get_open_position(normalize_symbol(symbol).asset_lookup_symbol)
        except Exception as exc:
            message = str(exc).lower()
            if "position does not exist" in message or "404" in message:
                return 0.0
            raise
        return abs(float(self._field(position, "qty", 0) or 0))

    def process_crypto_price(self, observation: CryptoPriceObservation) -> str | None:
        """Drive synthetic crypto OCO using an idempotent ledger claim."""
        svc = self.__dict__.get("_crypto_service")
        if svc is None:
            svc = CryptoService(self)
            self.__dict__["_crypto_service"] = svc
        return svc.process_crypto_price(observation)

    def _cancel_stop_then_exit(self, position: ManagedPosition, reason: str) -> None:
        svc = self.__dict__.get("_crypto_service")
        if svc is not None:
            return svc._cancel_stop_then_exit(position, reason)  # type: ignore[no-any-return]
        # Fallback for __new__ without service
        return None

    def _poll_stop_cancellation(self, position: Any, stop_id: str) -> tuple[Any, str]:
        svc = self.__dict__.get("_crypto_service")
        if svc is not None:
            return svc._poll_stop_cancellation(position, stop_id)  # type: ignore[no-any-return]
        # Fallback
        raise AmbiguousOrderState(
            f"Stop cancellation is unverified for {getattr(position, 'canonical_symbol', 'unknown')}: no service"
        )

    def _submit_with_recovery(self, request: Any, client_order_id: str) -> Any:
        try:
            return self._client.submit_order(request)
        except Exception as submit_exc:
            # Structured broker validation failures are definite rejections:
            # Alpaca did not accept the requested quantity, so recovery may
            # safely size down without risking a duplicate order.
            if self._available_qty_from_error(submit_exc) > 0:
                raise
            get_by_client_id = getattr(self._client, "get_order_by_client_id", None)
            if get_by_client_id is not None:
                lookup_error: Exception | None = None
                try:
                    recovered: Any = get_by_client_id(client_order_id)
                except Exception as lookup_exc:
                    recovered = None
                    lookup_error = lookup_exc
                if recovered is not None:
                    status = self._normalized_field(recovered, "status")
                    if status in {"rejected", "canceled", "cancelled", "expired"}:
                        # A broker-confirmed terminal response is known, not
                        # ambiguous. Preserve the original submit failure.
                        raise submit_exc
                    log.warning(
                        "Recovered Alpaca order %s after submit error: %s",
                        client_order_id,
                        submit_exc,
                    )
                    return recovered
                if getattr(lookup_error, "status_code", None) == 404:
                    # The deterministic client id was never accepted: the
                    # original submit failure is the definite outcome, not an
                    # ambiguity (mirrors resolve_portfolio_submission).
                    raise submit_exc
                if lookup_error is not None:
                    log.warning(
                        "Recovery lookup failed for %s after submit error: %s",
                        client_order_id,
                        lookup_error,
                    )
            _emit_alert(
                "ambiguous_order",
                message=(f"Broker write outcome unknown for client_order_id={client_order_id}: {submit_exc}"),
                client_order_id=client_order_id,
                reason="broker_write_outcome_unknown",
            )
            raise AmbiguousOrderState(
                f"Broker write outcome unknown for client_order_id={client_order_id}: {submit_exc}"
            ) from submit_exc

    def _has_open_order(self, symbol: str) -> bool:
        orders = self._get_open_orders(GetOrdersRequest(status=QueryOrderStatus.OPEN, symbols=[symbol], nested=True))
        if not orders:
            return False
        return any(same_symbol(str(self._field(order, "symbol", symbol)), symbol) for order in orders)

    def _has_protective_order(
        self,
        symbol: str,
        position_is_long: bool,
        required_qty: float,
        open_orders: Any = None,
    ) -> bool:
        svc = self.__dict__.get("_protection_service")
        if svc is not None:
            return svc._has_protective_order(symbol, position_is_long, required_qty, open_orders)  # type: ignore[no-any-return]
        protective_orders = self._open_protective_orders(symbol, position_is_long, open_orders)
        protected = sum(
            (
                max(
                    Decimal(0),
                    abs(Decimal(str(self._field(order, "qty", 0) or 0)))
                    - abs(Decimal(str(self._field(order, "filled_qty", 0) or 0))),
                )
                for order in protective_orders
            ),
            start=Decimal(0),
        )
        matches, self._last_protection_mismatch = self._protection_matches(protected, Decimal(str(required_qty)))
        self._last_protected_qty = float(protected)
        return matches

    def _open_protective_orders(self, symbol: str, position_is_long: bool, open_orders: Any = None) -> list[Any]:
        svc = self.__dict__.get("_protection_service")
        if svc is not None:
            return svc._open_protective_orders(symbol, position_is_long, open_orders)  # type: ignore[no-any-return]
        if open_orders is None:
            orders: Any = self._get_open_orders(
                GetOrdersRequest(status=QueryOrderStatus.OPEN, symbols=[symbol], nested=True)
            )
        else:
            orders = open_orders
        if isinstance(orders, dict):
            log.warning(
                "Unexpected get_orders payload shape for %s; treating as no open orders",
                symbol,
            )
            return []
        closing_side = "sell" if position_is_long else "buy"
        canonical = canonical_symbol(symbol)
        ledger = self.ledger
        account_id = getattr(self, "_account_id", "") or "unknown"
        legacy_external_management = ledger is None and bool(getattr(self, "manage_external_positions", True))
        managed_order_ids = ledger.managed_order_ids(account_id, canonical) if ledger is not None else set()
        managed_client_ids = ledger.managed_client_order_ids(account_id, canonical) if ledger is not None else set()

        def is_owned(order: Any, inherited: bool = False) -> bool:
            order_id = str(self._field(order, "id", "") or "")
            client_id = str(self._field(order, "client_order_id", "") or "")
            parent_id = str(self._field(order, "parent_order_id", "") or "")
            return (
                inherited
                or legacy_external_management
                or order_id in managed_order_ids
                or parent_id in managed_order_ids
                or client_id in managed_client_ids
                or client_id.startswith("parayabuck-")
            )

        protective: list[Any] = []

        def visit(order: Any, inherited: bool = False) -> None:
            owned = is_owned(order, inherited)
            side = self._normalized_field(order, "side")
            order_type = self._normalized_field(order, "type")
            stop_price = self._field(order, "stop_price")
            status = self._normalized_field(order, "status")
            if (
                owned
                and side == closing_side
                and status
                not in {
                    "canceled",
                    "cancelled",
                    "done_for_day",
                    "expired",
                    "filled",
                    "rejected",
                    "replaced",
                    "stopped",
                }
                and (stop_price is not None or order_type in {"stop", "stop_limit", "trailing_stop"})
            ):
                protective.append(order)
                return
            for leg in self._field(order, "legs") or ():
                visit(leg, owned)

        for order in orders or ():
            # The per-symbol server filter (symbols=[symbol]) of the legacy
            # per-position fetch is replicated here so a shared broker-wide
            # open-orders batch keeps identical per-symbol semantics. An
            # order with an unknown symbol is never protective for this
            # position.
            if not same_symbol(str(self._field(order, "symbol", "")), symbol):
                continue
            visit(order)
        return protective

    def _cancel_protective_orders(self, symbol: str, position_is_long: bool) -> None:
        svc = self.__dict__.get("_protection_service")
        if svc is not None:
            return svc._cancel_protective_orders(symbol, position_is_long)  # type: ignore[no-any-return]
        for order in self._open_protective_orders(symbol, position_is_long):
            order_id = self._field(order, "id")
            if order_id:
                self._client.cancel_order_by_id(order_id)
                self._wait_for_cancel_confirmation(str(order_id), symbol)

    def _wait_for_cancel_confirmation(self, order_id: str, symbol: str) -> None:
        svc = self.__dict__.get("_protection_service")
        if svc is not None:
            return svc._wait_for_cancel_confirmation(order_id, symbol)  # type: ignore[no-any-return]
        deadline = time.monotonic() + getattr(self, "crypto_stop_cancel_timeout_seconds", 5.0)
        last_status = ""
        while time.monotonic() < deadline:
            try:
                order = self._client.get_order_by_id(order_id)
                last_status = self._normalized_field(order, "status")
                if last_status in {"canceled", "cancelled", "expired", "rejected"}:
                    return
                if last_status == "filled":
                    _emit_alert(
                        "ambiguous_order",
                        message=(f"Protective order filled while cancellation was pending for {symbol}"),
                        symbol=symbol,
                        reason="protective_order_filled_during_cancel",
                    )
                    raise AmbiguousOrderState(f"Protective order filled while cancellation was pending for {symbol}")
            except AmbiguousOrderState:
                raise
            except Exception:
                # A lookup failure is not proof that the protective order was
                # cancelled. Keep polling until the bounded deadline.
                pass
            time.sleep(getattr(self, "crypto_stop_cancel_poll_interval_seconds", 0.25))
        _emit_alert(
            "ambiguous_order",
            message=(f"Protective order cancellation is unconfirmed for {symbol}: {last_status or 'lookup_failed'}"),
            symbol=symbol,
            reason="cancel_unconfirmed",
            last_status=last_status or "lookup_failed",
        )
        raise AmbiguousOrderState(
            f"Protective order cancellation is unconfirmed for {symbol}: {last_status or 'lookup_failed'}"
        )

    @staticmethod
    def _protection_matches(protected: Decimal, required: Decimal) -> tuple[bool, str]:
        tolerance = max(Decimal("0.000000001"), required * Decimal("0.000000001"))
        mismatch = "missing" if protected == 0 else ("under" if protected < required - tolerance else "over")
        return abs(protected - required) <= tolerance, mismatch

    def _normalized_field(self, value: Any, name: str) -> str:
        adapter = self.__dict__.get("_client_adapter")
        if adapter is not None:
            return adapter._normalized_field(value, name)  # type: ignore[no-any-return]
        return str(self._field(value, name, "")).lower().split(".")[-1]

    @staticmethod
    def _field(value: Any, name: str, default: Any = None) -> Any:
        try:
            return AlpacaClient._field(value, name, default)
        except Exception:
            from parayabuck.utils.objects import field_value as _fv

            return _fv(value, name, default)

    def _query_persisted_peak(self, account_id: str) -> tuple[float | None, int | None]:
        return self._get_peak_service().query_persisted_peak(account_id)

    def _ensure_peak_reconciled(self, account_id: str, current_equity: float) -> None:
        svc = self._get_peak_service()
        svc.ensure_reconciled(account_id, current_equity)

    def _load_peak_equity(self) -> float:
        svc = self._get_peak_service()
        try:
            svc._broker_account_id = self.__dict__.get("_account_id") or None
        except Exception:
            pass
        acct = getattr(svc, "_peak_account_id", None) or self.__dict__.get("_account_id")
        return svc.load_peak_equity(str(acct) if acct else None)

    def _save_peak_equity(self) -> None:
        svc = self._get_peak_service()
        try:
            svc._broker_account_id = self.__dict__.get("_account_id") or None
        except Exception:
            pass
        acct = getattr(svc, "_peak_account_id", None) or self.__dict__.get("_account_id")
        svc.save_peak_equity(str(acct) if acct else None)
        try:
            self.__dict__["_peak_equity"] = float(svc.peak_equity)
            self.__dict__["_peak_epoch"] = int(svc.peak_epoch)
            if svc.peak_account_id is not None:
                self.__dict__["_peak_account_id"] = svc.peak_account_id
            self.__dict__["_peak_reconciled"] = bool(svc.peak_reconciled)
        except Exception:
            pass

    def reset_peak_equity(self, equity: float) -> None:
        svc = self._get_peak_service()
        try:
            svc._broker_account_id = self.__dict__.get("_account_id") or None
        except Exception:
            pass
        acct = getattr(svc, "_peak_account_id", None) or self.__dict__.get("_account_id")
        svc.reset_peak_equity(equity, str(acct) if acct else None)
        try:
            self.__dict__["_peak_equity"] = float(svc.peak_equity)
            self.__dict__["_peak_epoch"] = int(svc.peak_epoch)
            if svc.peak_account_id is not None:
                self.__dict__["_peak_account_id"] = svc.peak_account_id
            self.__dict__["_peak_reconciled"] = bool(svc.peak_reconciled)
        except Exception:
            pass


__all__ = [
    "AlpacaBroker",
    "AmbiguousOrderState",
    "CRYPTO_TAKER_FEE_RESERVE",
    "CryptoPriceObservation",
    "OwnershipDriftError",
    "ProtectionReconciliationError",
    "QuantityResolution",
    "RiskStatePersistenceError",
]
