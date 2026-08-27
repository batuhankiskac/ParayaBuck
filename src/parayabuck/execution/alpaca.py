from __future__ import annotations

import json
import math
import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import ROUND_CEILING, ROUND_DOWN, ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any
from uuid import uuid4

from alpaca.trading.client import TradingClient
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
    ExecutionEvent,
    Fill,
    Market,
    OrderIntent,
    OrderRole,
    OrderState,
    PositionSnapshot,
    ProtectionState,
)
from parayabuck.execution.ledger import ExecutionLedger, ManagedPosition
from parayabuck.execution.portfolio import (
    AssetTradingRules,
    PortfolioOrderRequest,
    PortfolioOrderSide,
    floor_increment_decimal,
)
from parayabuck.logger import get_logger
from parayabuck.markets.symbols import canonical_symbol, normalize_symbol, same_symbol
from parayabuck.utils import is_crypto
from parayabuck.utils.http import patch_session_timeout
from parayabuck.utils.io import atomic_write
from parayabuck.utils.objects import field_value

log = get_logger("parayabuck.execution.alpaca")

CRYPTO_TAKER_FEE_RESERVE = Decimal("0.0025")


class RiskStatePersistenceError(RuntimeError):
    """Peak equity sidecar is present but unreadable or invalid.

    Raised when ``paper_equity_peak.json`` / ``live_equity_peak.json`` exists
    but is malformed, truncated, non-finite, negative, or unreadable. The
    caller must halt entries fail-closed rather than fall back to 0.0 and
    erase the drawdown watermark. Missing file bootstraps to 0.0.
    """


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


class AmbiguousOrderState(RuntimeError):
    pass


class ProtectionReconciliationError(RuntimeError):
    pass


class OwnershipDriftError(ProtectionReconciliationError):
    """The broker holds fewer shares than the portfolio repository claims as
    managed. With mixed inventory the remaining shares cannot be attributed to
    the bot safely, so entries halt until an operator reconciles manually."""


@dataclass(frozen=True)
class CryptoPriceObservation:
    symbol: str
    high: float
    low: float
    source: str
    timestamp: datetime


@dataclass(frozen=True)
class QuantityResolution:
    verified: bool
    available_qty: float
    source: str


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
        portfolio_fill_recovered: (Callable[[str, str, float, float, datetime, str, float], None] | None) = None,
        portfolio_stop_recorded: (Callable[[str, Market, float, str, float], None] | None) = None,
        portfolio_flat_reconciled: (Callable[[str, Market], object] | None) = None,
        crypto_stop_limit_offset_bps: float = 25.0,
        crypto_emergency_exit_bps: float = 75.0,
        crypto_stop_cancel_timeout_seconds: float = 5.0,
        crypto_stop_cancel_poll_interval_seconds: float = 0.25,
        runtime_state_dir: str | Path | None = None,
        peak_state_path: str | Path | None = None,
    ) -> None:
        self._client = TradingClient(api_key, secret_key, paper=paper)
        patch_session_timeout(self._client._session, timeout=15.0)
        self._paper = paper
        if peak_state_path is not None:
            self._peak_state_path = Path(peak_state_path)
        elif runtime_state_dir is not None:
            self._peak_state_path = Path(runtime_state_dir) / (
                "paper_equity_peak.json" if paper else "live_equity_peak.json"
            )
        else:
            self._peak_state_path = Path("data/runtime") / (
                "paper_equity_peak.json" if paper else "live_equity_peak.json"
            )
        self._asset_cache: dict[str, Any] = {}
        self.manage_external_positions = manage_external_positions
        self.ledger = ledger
        self._peak_epoch = 0
        self._peak_equity = 0.0
        self._peak_account_id: str | None = None
        self._peak_reconciled = False
        # Validate sidecar fail-closed immediately for corrupt files, but do NOT
        # decide the peak yet — that is account-scoped and happens on first
        # account() after account_id is known. For ledger=None (no account
        # scoping needed) eagerly bootstrap from legacy sidecar so old unit
        # tests that construct a broker without a ledger keep working.
        if ledger is None and self._peak_state_path.exists():
            # Eager bootstrap when there is no ledger to scope against.
            try:
                raw = self._peak_state_path.read_text(encoding="utf-8")
            except OSError as exc:
                raise RiskStatePersistenceError(
                    f"Peak equity file unreadable at {self._peak_state_path}: {exc}"
                ) from exc
            try:
                data = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise RiskStatePersistenceError(
                    f"Peak equity file is malformed JSON at {self._peak_state_path}: {exc}"
                ) from exc
            if not isinstance(data, dict) or "peak_equity" not in data:
                raise RiskStatePersistenceError(
                    f"Peak equity file missing 'peak_equity' key at {self._peak_state_path}"
                )
            try:
                peak = float(data["peak_equity"])
            except (TypeError, ValueError) as exc:
                raise RiskStatePersistenceError(
                    f"Peak equity value is not a number at {self._peak_state_path}: {data.get('peak_equity')!r}"
                ) from exc
            if not math.isfinite(peak):
                raise RiskStatePersistenceError(f"Peak equity is non-finite at {self._peak_state_path}: {peak!r}")
            if peak < 0:
                raise RiskStatePersistenceError(f"Peak equity is negative at {self._peak_state_path}: {peak!r}")
            try:
                epoch = int(data.get("epoch", 0) or 0)
            except (TypeError, ValueError) as exc:
                raise RiskStatePersistenceError(
                    f"Peak epoch is not an integer at {self._peak_state_path}: {data.get('epoch')!r}"
                ) from exc
            if epoch < 0:
                raise RiskStatePersistenceError(f"Peak epoch is negative at {self._peak_state_path}: {epoch!r}")
            self._peak_equity = float(peak)
            self._peak_epoch = int(epoch)
            # Preserve plaintext account_id if present, else remain None
            acct = data.get("account_id")
            if isinstance(acct, str) and acct.strip():
                self._peak_account_id = acct.strip()
                self._peak_reconciled = True
            else:
                # Legacy file without binding — keep unreconciled so first
                # account() will rebind it, but keep the peak visible for
                # no-ledger callers (tests).
                self._peak_account_id = None
                self._peak_reconciled = False
        self.portfolio_owned_quantities = portfolio_owned_quantities
        self.portfolio_open_orders = portfolio_open_orders
        self.portfolio_fill_recovered = portfolio_fill_recovered
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

    @property
    def account_suffix(self) -> str:
        return self._account_id[-4:] if self._account_id else ""

    def close(self) -> None:
        """Close the Alpaca REST session pool (no-op for fake clients)."""
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

    def snapshot_if_fresh(self, max_age_seconds: float) -> AccountSnapshot | None:
        """Return the last fetched account snapshot if it is at most
        ``max_age_seconds`` old, else ``None``.

        The snapshot is immutable and broker-authoritative at its fetch time.
        Callers that require post-write broker state (e.g. the drift check
        after booking a fill) must call ``account()`` instead; callers that
        tolerate the config-sanctioned entry-gate freshness (for example the
        trade-update risk-state persistence, which the 15s account loop
        re-verifies with a fresh fetch) may reuse it to avoid a redundant
        get_account + get_all_positions round-trip.
        """
        cached = self._snapshot_cache
        cached_at = self._snapshot_cached_at
        if cached is None or cached_at is None:
            return None
        if time.monotonic() - cached_at <= max_age_seconds:
            return cached
        return None

    def account(self) -> AccountSnapshot:
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
        for raw in raw_positions:
            current_price = float(raw.current_price)
            unrealized += float(raw.unrealized_pl or 0)
            intraday_unrealized += float(raw.unrealized_intraday_pl or 0)
            asset_class = str(getattr(raw, "asset_class", "")).lower()
            market = Market.CRYPTO if "crypto" in asset_class or is_crypto(str(raw.symbol)) else Market.EQUITY
            canonical = canonical_symbol(str(raw.symbol), market)
            managed = managed_positions.get(canonical)
            raw_qty = float(raw.qty)
            ledger_owned = float(managed.owned_qty) if managed is not None else 0.0
            repo_owned = float(owned_quantities.get(canonical, 0.0))
            # The portfolio repository is the canonical ownership system. The
            # legacy ledger may still hold recovery positions; positive
            # ownership in BOTH systems on one symbol is an explicit
            # reconciliation invariant and must never be resolved by guessing
            # which quantity is real.
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
            # Actual owned quantities decide managed inventory: a ledger
            # record alone never makes the whole broker position managed.
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
            # Fail-closed: a working open order exempts the shortfall only
            # when it is a managed portfolio SELL (risk-reducing exit) from
            # this account's repository with enough remaining quantity to
            # explain the discrepancy. A manual BUY/SELL or any unrelated
            # open order never suppresses ownership drift.
            repo_orders = self._portfolio_open_order_map()
            for canonical, owned_qty, raw_qty, market in drift_candidates:
                if self._open_order_explains_drift(canonical, owned_qty, raw_qty, market, repo_orders):
                    continue
                raise OwnershipDriftError(
                    f"Ownership drift for {canonical}: repository managed "
                    f"qty={owned_qty} exceeds broker position qty={raw_qty}"
                )
        # Account-scoped peak reconciliation: must happen before max(...) so
        # the peak is bound to this account_id. Fail closed on identity change.
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
        orders = self._client.get_orders(GetOrdersRequest(status=QueryOrderStatus.OPEN, nested=True))
        return {
            canonical_symbol(str(self._field(order, "symbol", "")))
            for order in orders or ()
            if self._field(order, "symbol", "")
        }

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
        rules = self.asset_trading_rules(order.symbol, order.market)
        quantity = Decimal(str(order.quantity))
        increment = Decimal(str(rules.quantity_increment))
        quantity = floor_increment_decimal(quantity, increment)
        if quantity < Decimal(str(rules.minimum_order_size)):
            _emit_alert(
                "order_rejected",
                message=(f"Portfolio order below minimum size: {order.symbol} client_order_id={order.client_order_id}"),
                symbol=order.symbol,
                market=order.market.value,
                client_order_id=order.client_order_id,
                reason="below_minimum_size",
            )
            raise ValueError(f"Portfolio order is below minimum size: {order.symbol}")
        if float(quantity) * order.reference_price < rules.minimum_order_notional:
            _emit_alert(
                "order_rejected",
                message=(
                    f"Portfolio order below minimum notional: {order.symbol} client_order_id={order.client_order_id}"
                ),
                symbol=order.symbol,
                market=order.market.value,
                client_order_id=order.client_order_id,
                reason="below_minimum_notional",
            )
            raise ValueError(f"Portfolio order is below minimum notional: {order.symbol}")
        if self._has_open_order(order.symbol):
            _emit_alert(
                "order_rejected",
                message=(
                    f"Portfolio order rejected: open order already exists for "
                    f"{order.symbol} client_order_id={order.client_order_id}"
                ),
                symbol=order.symbol,
                market=order.market.value,
                client_order_id=order.client_order_id,
                reason="open_order_exists",
            )
            raise ValueError(f"Open order already exists for {order.symbol}")
        request = MarketOrderRequest(
            symbol=order.symbol,
            qty=float(quantity),
            side=(OrderSide.BUY if order.side == PortfolioOrderSide.BUY else OrderSide.SELL),
            time_in_force=(TimeInForce.GTC if order.market == Market.CRYPTO else TimeInForce.DAY),
            client_order_id=order.client_order_id,
        )
        result = self._submit_with_recovery(request, order.client_order_id)
        broker_order_id = str(self._field(result, "id", "") or "")
        if not broker_order_id:
            _emit_alert(
                "ambiguous_order",
                message=(
                    f"Alpaca order response has no id for client_order_id={order.client_order_id} symbol={order.symbol}"
                ),
                symbol=order.symbol,
                market=order.market.value,
                client_order_id=order.client_order_id,
            )
            raise AmbiguousOrderState(f"Alpaca order response has no id for {order.client_order_id}")
        return broker_order_id

    def reconcile(self) -> dict[str, list[str]]:
        """Synchronize broker, stream and ledger state after any drift.

        Returns a categorized reconciliation report with 7 explicit buckets
        (never silently overwrites a material difference):
        ``MATCH``, ``LOCAL_STALE``, ``BROKER_STALE``, ``QUANTITY_MISMATCH``,
        ``UNKNOWN_ORDER``, ``MISSING_LOCAL_ORDER``, ``MISSING_BROKER_ORDER``.
        Callers must treat any non-empty mismatch bucket as fail-closed and
        require a verified ``reconcile()`` before new orders. Protective
        stop handling and fill-activity backfill run as before; any material
        drift still raises ``ProtectionReconciliationError``/``OwnershipDriftError``
        so ``runtime.py`` halts entries.

        Passes (in order): fill-activity fee backfill and offline-fill
        recovery, pending crypto exit / stop-failover completion, stale
        managed entries proven flat by the position snapshot, and protective
        stop (re)submission for every managed or opted-in position. Any
        failure raises so the caller can halt entries until the drift is
        understood — the ledger is the durable source of truth for owned
        quantities, never the broker snapshot alone.
        """
        categories: dict[str, list[str]] = {
            "MATCH": [],
            "LOCAL_STALE": [],
            "BROKER_STALE": [],
            "QUANTITY_MISMATCH": [],
            "UNKNOWN_ORDER": [],
            "MISSING_LOCAL_ORDER": [],
            "MISSING_BROKER_ORDER": [],
        }
        account = self.account()
        manage_external = bool(getattr(self, "manage_external_positions", True))
        portfolio_owned_provider = getattr(self, "portfolio_owned_quantities", None)
        portfolio_owned = set(portfolio_owned_provider()) if portfolio_owned_provider is not None else set()
        ledger = self.ledger
        managed = ledger.managed_for_account(self._account_id or "unknown") if ledger is not None else {}
        fill_activities_reconciled = True
        if ledger is not None:
            fill_activities_reconciled = self._reconcile_fill_activity_fees()
            # Fill recovery can create managed ownership that was absent from
            # the snapshot taken above. Refresh it before protection checks.
            managed = ledger.managed_for_account(self._account_id or "unknown")
        broker_symbols = {
            canonical_symbol(position.symbol, position.market) for position in account.positions if not position.is_flat
        }
        # --- 7-category holdings reconciliation (fail-closed) ---
        _ledger_quantities: dict[str, float] = {}
        for _sym, _pos in (managed or {}).items():
            _ledger_quantities[_sym] = _ledger_quantities.get(_sym, 0.0) + float(getattr(_pos, "owned_qty", 0.0))
        _portfolio_quantities: dict[str, float] = {}
        if portfolio_owned:
            _owned_provider = getattr(self, "portfolio_owned_quantities", None)
            if _owned_provider is not None:
                try:
                    _owned_map = _owned_provider() if callable(_owned_provider) else set()
                    if isinstance(_owned_map, dict):
                        _portfolio_quantities = {str(k): float(v) for k, v in _owned_map.items()}
                    elif isinstance(_owned_map, set):
                        for _s in _owned_map:
                            _portfolio_quantities[str(_s)] = _portfolio_quantities.get(str(_s), 0.0) + 1.0
                except Exception:
                    _portfolio_quantities = {str(s): 1.0 for s in portfolio_owned}
        _broker_quantities: dict[str, float] = {}
        for _p in account.positions:
            if not getattr(_p, "is_flat", False):
                _canon = canonical_symbol(getattr(_p, "symbol", ""), getattr(_p, "market", Market.EQUITY))
                _broker_quantities[_canon] = _broker_quantities.get(_canon, 0.0) + abs(float(getattr(_p, "qty", 0.0)))
        _local_symbols = set(_ledger_quantities) | set(_portfolio_quantities) | set(portfolio_owned)
        _all_symbols = _local_symbols | set(_broker_quantities)
        for _sym in sorted(_all_symbols):
            if _sym in _ledger_quantities:
                _local_qty = float(_ledger_quantities[_sym])
            elif _sym in _portfolio_quantities:
                _local_qty = float(_portfolio_quantities[_sym])
            else:
                _local_qty = 0.0
            _broker_qty = float(_broker_quantities.get(_sym, 0.0))
            if _local_qty > 1e-12 and _broker_qty > 1e-12:
                _tol = max(1e-9, _local_qty * 1e-8)
                if abs(_local_qty - _broker_qty) <= _tol:
                    categories["MATCH"].append(_sym)
                else:
                    categories["QUANTITY_MISMATCH"].append(_sym)
            elif _local_qty > 1e-12 and _broker_qty <= 1e-12:
                categories["LOCAL_STALE"].append(_sym)
                categories["MISSING_BROKER_ORDER"].append(_sym)
            elif _broker_qty > 1e-12 and _local_qty <= 1e-12:
                categories["BROKER_STALE"].append(_sym)
                categories["MISSING_LOCAL_ORDER"].append(_sym)
        if portfolio_owned and (flat_reconciler := getattr(self, "portfolio_flat_reconciled", None)):
            # A portfolio symbol with no broker position is stale: a missed
            # stop fill (stream downtime) or a manual sell would otherwise
            # leave the repository claiming managed ownership of a position
            # that no longer exists. Query Alpaca order history before doing
            # anything: recover the real fill at its actual price when it
            # exists, keep ownership when an order is genuinely still working,
            # and only otherwise record an explicit reconciliation anomaly —
            # never a synthetic precise fill at an invented price.
            for symbol in portfolio_owned:
                if symbol in broker_symbols:
                    continue
                market = Market.CRYPTO if is_crypto(symbol) else Market.EQUITY
                if self._reconcile_stale_portfolio_ownership(symbol, market):
                    continue
                flat_reconciler(symbol, market)
        if ledger is not None:
            self._reconcile_crypto_exit_state(ledger)
            self._reconcile_stale_ledger_entries(ledger, broker_symbols, fill_activities_reconciled)
        # One broker-wide open-orders fetch for the whole pass instead of a
        # per-position N+1 (each protected position used to issue its own
        # get_orders(status=OPEN, symbols=[symbol])). The fetch is lazy (only
        # when a position actually reaches the protection check) and guarded
        # so clients without get_orders keep the per-position behavior.
        # _open_protective_orders replicates the server-side symbol filter
        # client-side, so per-symbol semantics are identical; the snapshot is
        # taken once per pass and a mid-pass external cancel is re-verified
        # on the next 60s cadence exactly like any other protection drift.
        open_orders: Any = None
        client = getattr(self, "_client", None)
        get_orders = getattr(client, "get_orders", None) if client is not None else None
        for position in account.positions:
            is_long = position.qty > 0
            if position.is_flat:
                continue
            symbol = canonical_symbol(position.symbol, position.market)
            ownership = managed.get(symbol)
            # Portfolio-owned crypto positions are bot exposure without a
            # ledger lot: protect them with an emergency-exit stop so the
            # position cannot ride to zero unguarded. Equity portfolio
            # positions stay unprotected (their risk model is the overlay).
            portfolio_owned_position = position.market == Market.CRYPTO and symbol in portfolio_owned
            if ownership is None and not portfolio_owned_position and not manage_external:
                log.info("External position left unchanged during reconciliation: %s", symbol)
                continue
            broker_qty_is_fee_adjusted = False
            if ownership is not None:
                tolerance = max(1e-9, ownership.owned_qty * 1e-8)
                minimum_fee_adjusted_qty = ownership.owned_qty
                if position.market == Market.CRYPTO:
                    minimum_fee_adjusted_qty *= float(Decimal(1) - CRYPTO_TAKER_FEE_RESERVE)
                if position.market == Market.CRYPTO and abs(position.qty) + tolerance < minimum_fee_adjusted_qty:
                    if ledger is not None:
                        ledger.mark_manual_intervention(
                            self._account_id or "unknown",
                            ownership.entry_client_order_id,
                        )
                    raise ProtectionReconciliationError(
                        f"Broker quantity is below managed ledger quantity for {symbol}"
                    )
                if position.market == Market.CRYPTO and abs(position.qty) + tolerance < ownership.owned_qty:
                    broker_qty_is_fee_adjusted = True
                    log.info(
                        "Accepted fee-adjusted crypto ownership: symbol=%s ledger_qty=%s broker_qty=%s",
                        symbol,
                        ownership.owned_qty,
                        abs(position.qty),
                    )
                stop_price = getattr(ownership, "current_stop", None) or ownership.original_stop
                total_qty = min(abs(position.qty), ownership.owned_qty)
            else:
                # Portfolio-owned crypto exposure gets emergency-exit
                # protection anchored to the configurable offset; the
                # backwards-compatible opt-in policy covers explicitly
                # managed external positions with fixed percentages.
                if portfolio_owned_position:
                    stop_pct = self.crypto_emergency_exit_bps / 10_000
                    # A portfolio-owned position may be mixed with manual
                    # inventory: only the managed quantity is protected, so an
                    # emergency stop can never consume the user's own units.
                    total_qty = min(abs(position.qty), position.managed_qty)
                else:
                    stop_pct = 0.025 if position.market == Market.CRYPTO else 0.04
                    total_qty = abs(position.qty)
                stop_price = position.avg_price * (1 - stop_pct if is_long else 1 + stop_pct)
            available_qty = abs(position.qty_available) if position.qty_available is not None else total_qty
            required_qty = total_qty
            if position.market == Market.CRYPTO and position.qty_available == 0 and not broker_qty_is_fee_adjusted:
                # Alpaca may deduct the taker fee from the acquired asset. A
                # stop can therefore reserve every sellable unit while being
                # up to 0.25% smaller than the raw position quantity. Only use
                # this tolerance when Alpaca confirms nothing remains
                # available; otherwise reconcile protects the remaining units.
                required_qty = total_qty * float(Decimal(1) - CRYPTO_TAKER_FEE_RESERVE)
                required_qty = self._round_qty_down(position.symbol, required_qty)
            if open_orders is None and get_orders is not None:
                open_orders = get_orders(GetOrdersRequest(status=QueryOrderStatus.OPEN, nested=True))
            # Positional open_orders: test doubles monkeypatch this method
            # with positional-only lambdas and must keep working.
            has_protection = self._has_protective_order(position.symbol, is_long, required_qty, open_orders)
            if not has_protection and ownership is not None and ownership.broker_order_id and available_qty <= 0:
                # Alpaca's open-orders endpoint can return only the active
                # take-profit leg of a filled bracket. Its paired stop remains
                # HELD and is visible only when the filled parent is fetched
                # with nested legs. Treat that held stop as active protection;
                # otherwise reconciliation tries to submit a second stop while
                # the bracket has reserved the entire position.
                parent_order_ids: str | set[str] = ownership.broker_order_id
                if ledger is not None:
                    parent_order_ids = ledger.managed_entry_broker_order_ids(
                        self._account_id or "unknown",
                        symbol,
                    )
                has_protection = self._has_nested_bracket_protection(
                    parent_order_ids,
                    position.symbol,
                    is_long,
                    required_qty,
                )
            if has_protection:
                if ownership is not None and ledger is not None:
                    ledger.set_protection_state(
                        self._account_id or "unknown",
                        ownership.entry_client_order_id,
                        ProtectionState.ACTIVE,
                    )
                continue
            mismatch = getattr(self, "_last_protection_mismatch", "missing")
            if mismatch in {"under", "over"}:
                if ownership is not None and ledger is not None:
                    ledger.set_protection_state(
                        self._account_id or "unknown",
                        ownership.entry_client_order_id,
                        ProtectionState.REQUESTED,
                    )
                # Add only the currently available remainder when protection is
                # undersized. Cancelling the existing stop first creates a
                # needless unprotected window and can churn fee-adjusted crypto
                # stops on every reconciliation cycle. Oversized protection
                # must be cancelled and broker-confirmed before replacement.
                if mismatch == "over":
                    protected_qty = float(getattr(self, "_last_protected_qty", 0.0))
                    if position.market == Market.CRYPTO and protected_qty <= total_qty + max(1e-9, total_qty * 1e-8):
                        # Fee-reserve band: ``required`` was reduced to ~99.75%
                        # of the position when nothing was sellable
                        # (qty_available == 0), so the engine's OWN full-size
                        # stop always reads "over". It covers at most the whole
                        # position — the healthy protected state. Cancelling it
                        # to re-submit the same size every cycle churned stops
                        # and raised "No available quantity to protect" on the
                        # stale pre-cancel snapshot.
                        if ownership is not None and ledger is not None:
                            ledger.set_protection_state(
                                self._account_id or "unknown",
                                ownership.entry_client_order_id,
                                ProtectionState.ACTIVE,
                            )
                        continue
                    self._cancel_protective_orders(position.symbol, is_long)
            qty = min(total_qty, available_qty)
            if mismatch == "under":
                protected_qty = float(getattr(self, "_last_protected_qty", 0.0))
                qty = min(qty, max(0.0, required_qty - protected_qty))
            if position.market == Market.CRYPTO:
                qty = self._available_position_qty(
                    position.symbol,
                    qty,
                    reserve_fee_on_lookup_failure=False,
                )
                if qty <= 0:
                    raise RuntimeError(f"No available quantity to protect for {position.symbol}")
            elif qty <= 0:
                raise RuntimeError(f"No available quantity to protect for {position.symbol}")
            protective_client_id = (
                f"{ownership.entry_client_order_id}-stop-{uuid4().hex[:8]}"
                if ownership is not None
                else f"parayabuck-stop-{uuid4().hex}"
                if portfolio_owned_position
                else None
            )
            accepted_id = self._submit_stop(
                position.symbol,
                position.market,
                qty,
                stop_price,
                is_long,
                client_order_id=protective_client_id,
            )
            if (
                accepted_id is not None
                and portfolio_owned_position
                and (stop_recorder := getattr(self, "portfolio_stop_recorded", None)) is not None
            ):
                # Portfolio-owned positions have no ledger lot; their stop is
                # booked in the portfolio repository so the fill lifecycle can
                # release ownership when the position is flat. The retry path
                # may have accepted a different client order id; record the
                # accepted one so the fill is attributed to the SELL leg. The
                # stop trigger price is the slippage reference: attributing an
                # entry price instead would book the whole entry-to-stop loss
                # as execution slippage.
                stop_recorder(position.symbol, position.market, qty, accepted_id, stop_price)
            if accepted_id is None:
                if ownership is not None and ledger is not None:
                    ledger.set_protection_state(
                        self._account_id or "unknown",
                        ownership.entry_client_order_id,
                        ProtectionState.FAILED,
                    )
                    if ledger is not None:
                        ledger.mark_manual_intervention(
                            self._account_id or "unknown",
                            ownership.entry_client_order_id,
                        )
                raise ProtectionReconciliationError(f"Could not restore managed protection for {position.symbol}")
            if ownership is not None and ledger is not None:
                ledger.set_protection_state(
                    self._account_id or "unknown",
                    ownership.entry_client_order_id,
                    ProtectionState.ACTIVE,
                )
        # --- Order-level reconciliation categories (fail-closed) ---
        try:
            _local_open_ids: set[str] = set()
            _local_provider = getattr(self, "portfolio_open_orders", None)
            if _local_provider is not None:
                try:
                    for _o in _local_provider() or ():
                        _cid = str(getattr(_o, "client_order_id", "")).strip()
                        if _cid:
                            _local_open_ids.add(_cid)
                except Exception:
                    pass
            _broker_open_ids: set[str] = set()
            try:
                _client = getattr(self, "_client", None)
                _get_orders = getattr(_client, "get_orders", None) if _client is not None else None
                if _get_orders is not None:
                    _broker_orders = _get_orders(GetOrdersRequest(status=QueryOrderStatus.OPEN, nested=True))
                    for _bo in _broker_orders or ():
                        _bcid = str(
                            getattr(_bo, "client_order_id", "") or self._field(_bo, "client_order_id", "")
                        ).strip()
                        if _bcid:
                            _broker_open_ids.add(_bcid)
            except Exception:
                pass
            for _cid in sorted(_broker_open_ids - _local_open_ids):
                categories["UNKNOWN_ORDER"].append(_cid)
            for _cid in sorted(_local_open_ids - _broker_open_ids):
                categories["MISSING_BROKER_ORDER"].append(_cid)
                categories["MISSING_LOCAL_ORDER"].append(_cid)
        except Exception:
            pass
        return categories

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
        """Backfill fees when TradingStream fill payloads omit commission.

        The pass syncs incrementally: a persisted per-account watermark
        (max transaction_time of previously reconciled activities) becomes an
        ``after`` bound with a bounded look-back window. Application stays
        idempotent (stable activity ids, ON CONFLICT upserts), so re-reading
        the look-back window is harmless; the window only exists to catch
        activities that appear in the feed later than their transaction_time
        (Alpaca settles fills within minutes; 24h is a generous bound). The
        watermark advances only after a fully successful pass, so a failed
        pass re-reads the same window on the next reconcile — offline-fill
        recovery and fee attribution are unchanged for any activity within
        the window, and a fresh database (no watermark) scans full history
        exactly like before.
        """
        request = getattr(getattr(self, "_client", None), "_request", None)
        ledger = self.ledger
        if request is None or ledger is None:
            return False
        account_id = self._account_id or "unknown"
        watermark = None
        watermark_getter = getattr(ledger, "get_reconcile_watermark", None)
        watermark_setter = getattr(ledger, "set_reconcile_watermark", None)
        if watermark_getter is not None:
            watermark = watermark_getter(account_id, "fill_activity")
        activities: list[dict[str, Any]] = []
        page_token: str | None = None
        seen_tokens: set[str] = set()
        try:
            while True:
                params: dict[str, object] = {"direction": "desc", "page_size": 100}
                if watermark is not None:
                    params["after"] = (watermark - timedelta(hours=24)).isoformat()
                if page_token is not None:
                    params["page_token"] = page_token
                payload = request("GET", "/account/activities/FILL", data=params)
                page = payload if isinstance(payload, list) else []
                activities.extend(item for item in page if isinstance(item, dict))
                if len(page) < 100:
                    break
                next_token = str(page[-1].get("id", "") or "")
                if not next_token or next_token in seen_tokens:
                    # The API did not advance the cursor (or returned a
                    # repeated page): the loop must stop, but silently
                    # stopping would drop fee attribution and offline-fill
                    # recovery for every activity past this point.
                    log.warning(
                        "Fill activity pagination stopped at %d activities: "
                        "page cursor was not honored; reconciliation of "
                        "older fill activities is skipped",
                        len(activities),
                    )
                    break
                seen_tokens.add(next_token)
                page_token = next_token
        except Exception as exc:
            log.warning("Fill activity fee reconciliation deferred: %s", exc)
            return False
        activities.sort(key=lambda item: str(item.get("transaction_time") or item.get("date") or ""))
        recovered_buy_fills: dict[str, list[tuple[float, float, datetime, str, str]]] = {}
        # Watermark tracks only activities that were actually processed: an
        # activity skipped by a transient parse failure must not be
        # acknowledged, or it would permanently drop out of the reconciliation
        # window (the pre-incremental design retried it every pass).
        applied_watermark: datetime | None = None
        for activity in activities:
            fee_raw = activity.get("commission", activity.get("fee", 0))
            try:
                fee = abs(float(fee_raw or 0))
            except (TypeError, ValueError):
                continue
            timestamp_raw = activity.get("transaction_time") or activity.get("date") or datetime.now(UTC).isoformat()
            try:
                occurred_at = datetime.fromisoformat(str(timestamp_raw))
                if occurred_at.tzinfo is None:
                    occurred_at = occurred_at.replace(tzinfo=UTC)
            except ValueError:
                occurred_at = datetime.now(UTC)
            activity_id = str(activity.get("id", "") or "")
            broker_order_id = str(activity.get("order_id", "") or "")
            side = str(activity.get("side", "") or "").lower().split(".")[-1]
            symbol = str(activity.get("symbol", "") or "")
            try:
                qty = abs(float(activity.get("qty", 0) or 0))
                price = float(activity.get("price", 0) or 0)
            except (TypeError, ValueError):
                qty = 0.0
                price = 0.0
            # Recover an exit that filled while TradingStream was offline.
            # A stable activity id makes this idempotent across reconciliations.
            if activity_id and broker_order_id and symbol and side == "sell" and qty > 0 and price > 0:
                market = Market.CRYPTO if "/" in symbol else Market.EQUITY
                # One ledger row per broker order: a single order can produce
                # several fill activities (partial fills, fee legs), and the
                # orders table enforces a unique broker_order_id. A stable
                # client id derived from the broker order makes every activity
                # of that order merge into the same row (ON CONFLICT) instead
                # of colliding on the unique index. If the broker order is
                # ALREADY in the ledger under a different client id (a
                # portfolio SELL row, or a recovery row from an earlier
                # reconciliation), the exit is already booked — inserting a
                # second row would violate the unique broker-order index and
                # brick engine startup.
                order_lookup = getattr(ledger, "order_for_broker_id", None)
                existing_client = (
                    order_lookup(self._account_id or "unknown", broker_order_id) if order_lookup is not None else None
                )
                if existing_client is not None:
                    log.info(
                        "Sell activity already booked in the ledger; recording "
                        "the fee leg only: broker_order_id=%s symbol=%s",
                        broker_order_id,
                        symbol,
                    )
                    fee_recorder = getattr(ledger, "record_fee_activity", None)
                    if fee_recorder is not None:
                        fee_recorder(activity_id, existing_client, fee, occurred_at)
                else:
                    ledger.apply_execution_event(
                        ExecutionEvent(
                            account_id=self._account_id or "unknown",
                            client_order_id=f"alpaca-fill-activity-{broker_order_id}",
                            broker_order_id=broker_order_id,
                            parent_order_id=None,
                            role=OrderRole.MANUAL_EXIT,
                            symbol=canonical_symbol(symbol, market),
                            market=market,
                            state=OrderState.FILLED,
                            requested_qty=qty,
                            cumulative_filled_qty=qty,
                            average_fill_price=price,
                            occurred_at=occurred_at,
                            fee=fee,
                            activity_id=activity_id,
                        )
                    )
            if activity_id and broker_order_id and symbol and side == "buy" and qty > 0 and price > 0:
                recovered_buy_fills.setdefault(broker_order_id, []).append(
                    (qty, price, occurred_at, activity_id, symbol)
                )
            applied = ledger.apply_fee_activity(
                self._account_id or "unknown",
                activity_id=activity_id,
                broker_order_id=broker_order_id,
                fee=fee,
                occurred_at=occurred_at,
            )
            if applied_watermark is None or occurred_at > applied_watermark:
                applied_watermark = occurred_at
            if applied:
                log.info(
                    "Fee activity applied: broker_order_id=%s fee=%s recovery_action=fill_activity_reconciliation",
                    activity.get("order_id"),
                    fee,
                )
        entry_lookup = getattr(ledger, "entry_order_for_broker_id", None)
        if entry_lookup is not None:
            for broker_order_id, fills in recovered_buy_fills.items():
                recovered = entry_lookup(
                    self._account_id or "unknown",
                    broker_order_id,
                )
                if recovered is None:
                    continue
                order, known_cumulative = recovered
                observed_qty = sum(item[0] for item in fills)
                cumulative = min(order.qty, max(known_cumulative, observed_qty))
                if cumulative <= known_cumulative:
                    continue
                observed_notional = sum(item[0] * item[1] for item in fills)
                average_price = observed_notional / observed_qty
                latest = max(fills, key=lambda item: item[2])
                state = (
                    OrderState.FILLED
                    if cumulative >= order.qty - max(1e-9, order.qty * 1e-8)
                    else OrderState.PARTIALLY_FILLED
                )
                ledger.apply_execution_event(
                    ExecutionEvent(
                        account_id=self._account_id or "unknown",
                        client_order_id=order.client_order_id,
                        broker_order_id=broker_order_id,
                        parent_order_id=None,
                        role=OrderRole.ENTRY,
                        symbol=order.symbol,
                        market=order.market,
                        state=state,
                        requested_qty=order.qty,
                        cumulative_filled_qty=cumulative,
                        average_fill_price=average_price,
                        occurred_at=latest[2],
                        activity_id=latest[3],
                    )
                )
                log.warning(
                    "Entry fill recovered from account activity: market=%s symbol=%s "
                    "entry_client_order_id=%s broker_order_id=%s cumulative_qty=%s",
                    order.market.value,
                    order.symbol,
                    order.client_order_id,
                    broker_order_id,
                    cumulative,
                )
        if watermark_setter is not None and applied_watermark is not None:
            watermark_setter(account_id, "fill_activity", applied_watermark)
        return True

    def _reconcile_crypto_exit(self, position: ManagedPosition) -> None:
        ledger = self.ledger
        if ledger is None:
            raise ProtectionReconciliationError(
                f"Cannot recover crypto exit for {position.canonical_symbol}: no ledger"
            )
        get_by_client_id = getattr(self._client, "get_order_by_client_id", None)
        if get_by_client_id is None or not position.exit_client_order_id:
            ledger.mark_manual_intervention(self._account_id, position.entry_client_order_id)
            raise ProtectionReconciliationError(f"Cannot recover crypto exit for {position.canonical_symbol}")
        try:
            order = get_by_client_id(position.exit_client_order_id)
        except Exception as exc:
            ledger.mark_manual_intervention(self._account_id, position.entry_client_order_id)
            raise ProtectionReconciliationError(
                f"Crypto exit lookup is ambiguous for {position.canonical_symbol}"
            ) from exc
        status_name = self._normalized_field(order, "status")
        state = {
            "filled": OrderState.FILLED,
            "partially_filled": OrderState.PARTIALLY_FILLED,
            "canceled": OrderState.CANCELLED,
            "cancelled": OrderState.CANCELLED,
            "rejected": OrderState.REJECTED,
            "expired": OrderState.EXPIRED,
        }.get(status_name, OrderState.ACCEPTED)
        qty = float(self._field(order, "qty", position.owned_qty) or position.owned_qty)
        filled_qty = float(self._field(order, "filled_qty", 0) or 0)
        average = self._field(order, "filled_avg_price")
        role = OrderRole.TAKE_PROFIT if "-target-exit" in position.exit_client_order_id else OrderRole.EMERGENCY_EXIT
        if filled_qty > 0 and average is not None:
            ledger.apply_execution_event(
                ExecutionEvent(
                    self._account_id,
                    position.exit_client_order_id,
                    str(self._field(order, "id", "") or ""),
                    None,
                    role,
                    position.canonical_symbol,
                    Market.CRYPTO,
                    state,
                    qty,
                    filled_qty,
                    float(average),
                    datetime.now(UTC),
                )
            )
        if state == OrderState.FILLED:
            ledger.set_exit_state(
                self._account_id,
                position.entry_client_order_id,
                target_state=("target_exit_filled" if role == OrderRole.TAKE_PROFIT else "emergency_exit_filled"),
            )
            return
        if state in {OrderState.CANCELLED, OrderState.REJECTED, OrderState.EXPIRED}:
            self._reprotect_after_terminal_exit(position)
            return
        ledger.mark_manual_intervention(self._account_id, position.entry_client_order_id)
        raise ProtectionReconciliationError(f"Crypto exit remains {state.value} for {position.canonical_symbol}")

    def _reprotect_after_terminal_exit(self, position: ManagedPosition) -> None:
        ledger = self.ledger
        if ledger is None:
            raise ProtectionReconciliationError(f"Cannot re-protect {position.canonical_symbol}: no ledger")
        current = next(
            (
                item
                for item in ledger.managed_entries_for_account(self._account_id)
                if item.entry_client_order_id == position.entry_client_order_id
            ),
            None,
        )
        if current is None or current.owned_qty <= 0:
            return
        resolution = self.resolve_available_position_qty(
            current.canonical_symbol,
            current.owned_qty,
            reserve_fee_on_lookup_failure=False,
        )
        if not resolution.verified or resolution.available_qty <= 0:
            ledger.mark_manual_intervention(self._account_id, current.entry_client_order_id)
            raise ProtectionReconciliationError(f"Remaining exit quantity is unverified for {current.canonical_symbol}")
        client_id = f"{current.entry_client_order_id}-stop-reprotect"
        log.warning(
            "Re-protection requested: market=crypto symbol=%s entry_client_order_id=%s "
            "managed_qty=%s verified_available_qty=%s quantity_source=%s",
            current.canonical_symbol,
            current.entry_client_order_id,
            current.owned_qty,
            resolution.available_qty,
            resolution.source,
        )
        protected = self._submit_stop(
            current.canonical_symbol,
            Market.CRYPTO,
            resolution.available_qty,
            current.current_stop or current.original_stop,
            True,
            client_order_id=client_id,
        )
        if protected is None:
            if self._has_protective_order(current.canonical_symbol, True, resolution.available_qty):
                ledger.mark_manual_intervention(self._account_id, current.entry_client_order_id)
                raise AmbiguousOrderState(
                    f"Protective stop exists while re-protection failed for {current.canonical_symbol}"
                )
            intent = OrderIntent(
                current.source_signal_id,
                current.canonical_symbol,
                Market.CRYPTO,
                Direction.LONG,
                resolution.available_qty,
                current.filled_avg_price,
                current.current_stop or current.original_stop,
                current.original_target,
                current.strategy,
                client_order_id=current.entry_client_order_id,
            )
            exit_fill, exit_broker_id = self._flatten(intent, resolution.available_qty)
            # Record the flatten like submit() does: without this the managed
            # position stays open in the ledger with owned_qty > 0 although
            # the broker is flat, and the exit PnL is never attributed.
            ledger.apply_execution_event(
                ExecutionEvent(
                    account_id=self._account_id or "unknown",
                    client_order_id=exit_fill.client_order_id,
                    broker_order_id=exit_broker_id,
                    parent_order_id=None,
                    role=OrderRole.EMERGENCY_EXIT,
                    symbol=current.canonical_symbol,
                    market=Market.CRYPTO,
                    state=OrderState.FILLED,
                    requested_qty=exit_fill.qty,
                    cumulative_filled_qty=exit_fill.qty,
                    average_fill_price=exit_fill.price,
                    occurred_at=exit_fill.timestamp,
                )
            )
            ledger.set_exit_state(
                self._account_id or "unknown",
                current.entry_client_order_id,
                stop_state="open",
                target_state="emergency_exit_filled",
                clear_exit_order=True,
            )
            return
        ledger.set_exit_state(
            self._account_id,
            current.entry_client_order_id,
            stop_state="open",
            target_state="reprotected_after_terminal_exit",
            clear_exit_order=True,
        )
        log.warning(
            "Re-protection active: market=crypto symbol=%s entry_client_order_id=%s order_client_id=%s managed_qty=%s",
            current.canonical_symbol,
            current.entry_client_order_id,
            client_id,
            resolution.available_qty,
        )

    def _has_nested_bracket_protection(
        self,
        parent_order_ids: str | set[str],
        symbol: str,
        position_is_long: bool,
        required_qty: float,
    ) -> bool:
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
        increment = getattr(self._asset(symbol), "price_increment", None)
        if increment is not None:
            return Decimal(str(increment))
        if market == Market.EQUITY:
            # Alpaca: stocks >= $1 trade in pennies, sub-dollar stocks in 1/10000.
            return Decimal("0.01") if reference_price >= 1 else Decimal("0.0001")
        return Decimal("0.01") if reference_price >= 1 else Decimal("0.000001")

    @staticmethod
    def _round_to_step(value: float, step: Decimal) -> float:
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
        client_order_id = client_order_id or f"parayabuck-stop-{uuid4().hex}"
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
        """Drive synthetic crypto OCO using an idempotent ledger claim.

        The broker-side stop always remains active until a target or failover
        path has atomically won the exit claim and its cancellation is confirmed.
        """
        ledger = self.ledger
        if ledger is None or not self._account_id:
            return None
        symbol = canonical_symbol(observation.symbol, Market.CRYPTO)
        lock = self._symbol_exit_locks.setdefault(symbol, threading.Lock())
        if not lock.acquire(blocking=False):
            log.warning(
                "Crypto exit observation deferred because symbol lock is busy: symbol=%s source=%s observed_at=%s",
                symbol,
                observation.source,
                observation.timestamp.isoformat(),
            )
            return None
        try:
            entries = [
                item
                for item in ledger.managed_entries_for_account(self._account_id)
                if item.market == Market.CRYPTO and item.canonical_symbol == symbol
            ]
            for item in entries:
                reason: str | None = None
                target_seen = observation.high >= item.original_target
                active_stop = item.current_stop or item.original_stop
                stop_seen = observation.low <= active_stop
                if target_seen and stop_seen:
                    log.critical(
                        "Intrabar path ambiguous: market=crypto symbol=%s "
                        "entry_client_order_id=%s stop_state=%s target_state=%s "
                        "recovery_action=resolve_broker_stop",
                        symbol,
                        item.entry_client_order_id,
                        item.stop_state,
                        item.target_state,
                    )
                    if not item.protective_stop_broker_order_id:
                        ledger.mark_manual_intervention(self._account_id, item.entry_client_order_id)
                        raise AmbiguousOrderState(f"Intrabar stop/target order is unknown for {symbol}")
                    stop = self._client.get_order_by_id(item.protective_stop_broker_order_id)
                    stop_status = self._normalized_field(stop, "status")
                    if stop_status == "filled":
                        claimed = ledger.claim_symbol_exit(
                            self._account_id,
                            item.entry_client_order_id,
                            reason="intrabar_stop",
                        )
                        if claimed is not None:
                            self._cancel_stop_then_exit(claimed, "intrabar_stop")
                        return "stop"
                    ledger.mark_manual_intervention(self._account_id, item.entry_client_order_id)
                    raise AmbiguousOrderState(f"Intrabar stop/target path unresolved for {symbol}: {stop_status}")
                if target_seen:
                    reason = "target"
                else:
                    emergency_price = active_stop * (1 - self.crypto_emergency_exit_bps / 10_000)
                    if observation.low <= emergency_price:
                        reason = "stop_failover"
                    elif observation.low <= active_stop and item.stop_state == "open":
                        ledger.set_exit_state(
                            self._account_id,
                            item.entry_client_order_id,
                            stop_state="triggered_unfilled",
                        )
                        log.warning(
                            "Crypto stop triggered but remains unfilled: market=crypto symbol=%s source=%s",
                            symbol,
                            observation.source,
                        )
                if reason is None:
                    continue
                claimed = ledger.claim_symbol_exit(
                    self._account_id,
                    item.entry_client_order_id,
                    reason=reason,
                )
                if claimed is None:
                    continue
                log.warning(
                    "Crypto %s triggered: market=crypto symbol=%s managed_qty=%s source=%s",
                    reason,
                    symbol,
                    claimed.owned_qty,
                    observation.source,
                )
                self._cancel_stop_then_exit(claimed, reason)
                return reason
            return None
        finally:
            lock.release()

    def _cancel_stop_then_exit(self, position: ManagedPosition, reason: str) -> None:
        ledger = self.ledger
        if ledger is None:
            raise AmbiguousOrderState(f"Cannot cancel stop for {position.canonical_symbol}: no ledger")
        stop_id = position.protective_stop_broker_order_id
        if not stop_id:
            ledger.mark_manual_intervention(self._account_id, position.entry_client_order_id)
            raise AmbiguousOrderState(f"Protective stop id is unknown for {position.canonical_symbol}")
        try:
            self._client.cancel_order_by_id(stop_id)
        except Exception as exc:
            # A timeout can mean either cancel succeeded or the stop filled;
            # _poll_stop_cancellation disambiguates. Log the cause for the
            # operator instead of hiding it.
            log.warning(
                "Cancel request for stop %s of %s reported an error: %s",
                stop_id,
                position.canonical_symbol,
                exc,
            )
        stop, stop_status = self._poll_stop_cancellation(position, stop_id)
        if stop_status == "filled":
            filled_qty = float(self._field(stop, "filled_qty", 0) or 0)
            filled_price = float(self._field(stop, "filled_avg_price", 0) or 0)
            if filled_qty > 0 and filled_price > 0:
                ledger.apply_execution_event(
                    ExecutionEvent(
                        self._account_id,
                        position.protective_stop_client_order_id or f"{position.entry_client_order_id}-stop",
                        stop_id,
                        None,
                        OrderRole.PROTECTIVE_STOP,
                        position.canonical_symbol,
                        Market.CRYPTO,
                        OrderState.FILLED,
                        position.owned_qty,
                        filled_qty,
                        filled_price,
                        datetime.now(UTC),
                    )
                )
            ledger.set_exit_state(
                self._account_id,
                position.entry_client_order_id,
                stop_state="filled",
                target_state="cancelled_by_stop_fill",
            )
            return
        if stop_status not in {"canceled", "cancelled", "expired"}:
            ledger.mark_manual_intervention(self._account_id, position.entry_client_order_id)
            raise AmbiguousOrderState(
                f"Stop cancellation is unconfirmed for {position.canonical_symbol}: {stop_status or 'unknown'}"
            )
        ledger.set_exit_state(
            self._account_id,
            position.entry_client_order_id,
            stop_state="cancelled",
        )
        qty = self._available_position_qty(
            position.canonical_symbol,
            position.owned_qty,
            reserve_fee_on_lookup_failure=False,
        )
        if qty <= 0:
            ledger.mark_manual_intervention(self._account_id, position.entry_client_order_id)
            raise AmbiguousOrderState(f"No verified managed quantity available for {position.canonical_symbol}")
        suffix = "target-exit" if reason == "target" else "emergency-exit"
        client_id = f"{position.entry_client_order_id}-{suffix}"
        result = self._submit_with_recovery(
            MarketOrderRequest(
                symbol=normalize_symbol(position.canonical_symbol, Market.CRYPTO).trading_symbol,
                qty=qty,
                side=OrderSide.SELL,
                time_in_force=TimeInForce.GTC,
                client_order_id=client_id,
            ),
            client_id,
        )
        broker_id = str(self._field(result, "id", "") or "")
        if not broker_id:
            ledger.mark_manual_intervention(self._account_id, position.entry_client_order_id)
            raise AmbiguousOrderState(f"Exit broker id is unknown for {position.canonical_symbol}")
        ledger.set_exit_state(
            self._account_id,
            position.entry_client_order_id,
            target_state="target_exit_submitted" if reason == "target" else "emergency_exit_submitted",
            exit_client_order_id=client_id,
            exit_broker_order_id=broker_id,
        )
        intent = OrderIntent(
            position.source_signal_id,
            position.canonical_symbol,
            Market.CRYPTO,
            Direction.LONG,
            qty,
            position.filled_avg_price,
            position.original_stop,
            position.original_target,
            position.strategy,
            client_order_id=client_id,
        )
        state, fill = self._wait_for_fill(broker_id, intent, timeout=5.0)
        if fill is not None:
            ledger.apply_execution_event(
                ExecutionEvent(
                    self._account_id,
                    client_id,
                    broker_id,
                    None,
                    OrderRole.TAKE_PROFIT if reason == "target" else OrderRole.EMERGENCY_EXIT,
                    position.canonical_symbol,
                    Market.CRYPTO,
                    state,
                    qty,
                    fill.qty,
                    fill.price,
                    fill.timestamp,
                )
            )
        if state == OrderState.FILLED:
            ledger.set_exit_state(
                self._account_id,
                position.entry_client_order_id,
                target_state="target_exit_filled" if reason == "target" else "emergency_exit_filled",
            )
            return
        if state == OrderState.PARTIALLY_FILLED:
            ledger.set_exit_state(
                self._account_id,
                position.entry_client_order_id,
                target_state="target_exit_partially_filled",
            )
            try:
                self._client.cancel_order_by_id(broker_id)
            except Exception as exc:
                log.warning(
                    "Partial crypto exit cancel response ambiguous: broker_order_id=%s error=%s",
                    broker_id,
                    exc,
                )
            terminal_state, final_fill = self._wait_for_fill(
                broker_id,
                intent,
                timeout=getattr(self, "crypto_stop_cancel_timeout_seconds", 5.0),
            )
            if final_fill is not None:
                ledger.apply_execution_event(
                    ExecutionEvent(
                        self._account_id,
                        client_id,
                        broker_id,
                        None,
                        OrderRole.TAKE_PROFIT if reason == "target" else OrderRole.EMERGENCY_EXIT,
                        position.canonical_symbol,
                        Market.CRYPTO,
                        terminal_state,
                        qty,
                        final_fill.qty,
                        final_fill.price,
                        final_fill.timestamp,
                    )
                )
            if terminal_state == OrderState.FILLED:
                ledger.set_exit_state(
                    self._account_id,
                    position.entry_client_order_id,
                    target_state="target_exit_filled" if reason == "target" else "emergency_exit_filled",
                )
                return
            if terminal_state not in {
                OrderState.CANCELLED,
                OrderState.REJECTED,
                OrderState.EXPIRED,
            }:
                ledger.mark_manual_intervention(self._account_id, position.entry_client_order_id)
                raise AmbiguousOrderState(
                    "Partial crypto exit cancellation is unconfirmed for "
                    f"{position.canonical_symbol}: {terminal_state.value}"
                )
            self._reprotect_after_terminal_exit(position)
            return
        ledger.mark_manual_intervention(self._account_id, position.entry_client_order_id)
        raise AmbiguousOrderState(
            f"Crypto {reason} exit is not terminal for {position.canonical_symbol}: {state.value}"
        )

    def _poll_stop_cancellation(self, position: Any, stop_id: str) -> tuple[Any, str]:
        ledger = self.ledger
        if ledger is None:
            raise AmbiguousOrderState(f"Stop cancellation is unverified for {position.canonical_symbol}: no ledger")
        deadline = time.monotonic() + getattr(self, "crypto_stop_cancel_timeout_seconds", 5.0)
        last_stop: Any = None
        last_status = ""
        cancellation_lookup_logged = False
        while time.monotonic() < deadline:
            try:
                last_stop = self._client.get_order_by_id(stop_id)
                last_status = self._normalized_field(last_stop, "status")
                filled_qty = float(self._field(last_stop, "filled_qty", 0) or 0)
                filled_price = float(self._field(last_stop, "filled_avg_price", 0) or 0)
                if filled_qty > 0 and filled_price > 0:
                    state = OrderState.FILLED if last_status == "filled" else OrderState.PARTIALLY_FILLED
                    ledger.apply_execution_event(
                        ExecutionEvent(
                            self._account_id,
                            position.protective_stop_client_order_id or f"{position.entry_client_order_id}-stop",
                            stop_id,
                            None,
                            OrderRole.PROTECTIVE_STOP,
                            position.canonical_symbol,
                            Market.CRYPTO,
                            state,
                            position.owned_qty,
                            filled_qty,
                            filled_price,
                            datetime.now(UTC),
                        )
                    )
                if last_status in {"filled", "canceled", "cancelled", "expired"}:
                    log.info(
                        "Stop cancellation confirmed: market=crypto symbol=%s "
                        "order_client_id=%s broker_order_id=%s stop_state=%s",
                        position.canonical_symbol,
                        position.protective_stop_client_order_id,
                        stop_id,
                        last_status,
                    )
                    return last_stop, last_status
                if last_status == "rejected":
                    break
            except Exception as exc:
                # A read failure is not evidence that the stop disappeared.
                # Log the first failure so a permanently failing endpoint is
                # not indistinguishable from a slow one.
                if not cancellation_lookup_logged:
                    log.warning(
                        "Stop cancellation lookup failed for %s (%s): %s",
                        position.canonical_symbol,
                        stop_id,
                        exc,
                    )
                    cancellation_lookup_logged = True
            time.sleep(getattr(self, "crypto_stop_cancel_poll_interval_seconds", 0.01))
        ledger.mark_manual_intervention(self._account_id, position.entry_client_order_id)
        raise AmbiguousOrderState(
            f"Stop cancellation is unconfirmed for {position.canonical_symbol}: {last_status or 'lookup_failed'}"
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
        orders = self._client.get_orders(GetOrdersRequest(status=QueryOrderStatus.OPEN, symbols=[symbol], nested=True))
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
        if open_orders is None:
            orders: Any = self._client.get_orders(
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
        for order in self._open_protective_orders(symbol, position_is_long):
            order_id = self._field(order, "id")
            if order_id:
                self._client.cancel_order_by_id(order_id)
                self._wait_for_cancel_confirmation(str(order_id), symbol)

    def _wait_for_cancel_confirmation(self, order_id: str, symbol: str) -> None:
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
        return str(self._field(value, name, "")).lower().split(".")[-1]

    @staticmethod
    def _field(value: Any, name: str, default: Any = None) -> Any:
        return field_value(value, name, default)

    def _query_persisted_peak(self, account_id: str) -> tuple[float | None, int | None]:
        """Return (max_persisted_peak, epoch) for ``account_id``, or (None,None) if none.

        Fail-closed: any unexpected SQLite read failure raises
        ``RiskStatePersistenceError`` and must prevent peak reconciliation.
        (None,None) is only returned when the query succeeds and genuinely finds
        no rows for this account (first run). Malformed individual
        ``risk_state`` payloads are tolerated per-row, but if *all* rows for the
        account are unreadable/corrupt, this is a durability failure, not
        \"no history\".
        """
        if not account_id:
            return None, None
        if self.ledger is None:
            return None, None
        # Do not swallow DB errors — they must fail closed.
        with self.ledger._lock:
            # -- portfolio_high_water --
            hw_peak: float | None = None
            hw_epoch: int | None = None
            row = None
            try:
                row = self.ledger.connection.execute(
                    "SELECT equity, epoch FROM portfolio_high_water WHERE account_id = ?",
                    (account_id,),
                ).fetchone()
            except Exception as exc:
                if "no such table" in str(exc):
                    row = None
                else:
                    raise RiskStatePersistenceError(
                        f"portfolio_high_water read failed for {account_id}: {exc}"
                    ) from exc
            if row is not None:
                try:
                    hw_peak = float(row[0])
                    hw_epoch = int(row[1] or 0)
                except Exception as exc:
                    raise RiskStatePersistenceError(
                        f"portfolio_high_water row corrupt for {account_id}: {row!r}: {exc}"
                    ) from exc
                if hw_peak is not None and (not math.isfinite(hw_peak) or hw_peak < 0):
                    raise RiskStatePersistenceError(f"portfolio_high_water peak corrupt for {account_id}: {hw_peak!r}")
            # -- risk_state --
            try:
                rows = self.ledger.connection.execute(
                    "SELECT payload FROM risk_state WHERE account_id = ?",
                    (account_id,),
                ).fetchall()
            except Exception as exc:
                if "no such table" in str(exc):
                    rows = []
                else:
                    raise RiskStatePersistenceError(f"risk_state read failed for {account_id}: {exc}") from exc
            rs_peak: float | None = None
            rs_epoch: int | None = None
            if rows:
                best: dict[int, float] = {}
                corrupt = 0
                for (payload_str,) in rows:
                    try:
                        p = json.loads(payload_str)
                        pe = int(p.get("peak_epoch", 0) or 0)
                        pk = float(p.get("peak_equity", 0))
                        if not math.isfinite(pk) or pk < 0:
                            corrupt += 1
                            continue
                        if pe not in best or pk > best[pe]:
                            best[pe] = pk
                    except Exception:
                        corrupt += 1
                        continue
                if best:
                    max_epoch = max(best.keys())
                    rs_peak = best[max_epoch]
                    rs_epoch = max_epoch
                elif len(rows) > 0:
                    # All rows unreadable/corrupt — not \"no history\"
                    raise RiskStatePersistenceError(
                        f"risk_state rows for {account_id} all unreadable/corrupt: {len(rows)} rows"
                    )
            if hw_peak is None and rs_peak is None:
                return None, None
            if hw_peak is None:
                return rs_peak, rs_epoch
            if rs_peak is None:
                return hw_peak, hw_epoch
            if hw_epoch is None or rs_epoch is None:
                return max(hw_peak, rs_peak), hw_epoch if hw_epoch is not None else rs_epoch
            if hw_epoch != rs_epoch:
                return (hw_peak, hw_epoch) if hw_epoch > rs_epoch else (rs_peak, rs_epoch)
            return max(hw_peak, rs_peak), hw_epoch

    def _ensure_peak_reconciled(self, account_id: str, current_equity: float) -> None:
        """Account-scoped peak reconciliation, called once per account_id.

        Loads the sidecar, filters persisted state WHERE account_id = ?, and
        decides the peak per rules A-F. Sidecar is ``{"account_id": str,
        "peak_equity": float, "epoch": int}``. Legacy sidecars without
        ``account_id`` are handled: if SQLite for this account exists, peak is
        max(legacy, persisted) then sidecar is rewritten bound; if no SQLite,
        legacy is used with a warning and rebound. A sidecar whose
        ``account_id`` (or ``account_fingerprint`` SHA256) does not match
        ``account_id`` is ignored and replaced by persisted for this account.
        Epoch comparisons happen only inside one account.
        """
        if not account_id:
            account_id = "unknown"
        # Defensive defaults for brokers constructed via __new__ in unit tests
        if not hasattr(self, "_peak_state_path"):
            self._peak_state_path = Path("data/runtime") / (
                "paper_equity_peak.json" if getattr(self, "_paper", True) else "live_equity_peak.json"
            )
        if not hasattr(self, "_peak_account_id"):
            self._peak_account_id = None
        if not hasattr(self, "_peak_epoch"):
            self._peak_epoch = 0
        if not hasattr(self, "_peak_equity"):
            self._peak_equity = 0.0
        if not hasattr(self, "_peak_reconciled"):
            self._peak_reconciled = False
        if not hasattr(self, "ledger"):
            self.ledger = None
        # Fail closed on identity change
        existing = getattr(self, "_peak_account_id", None)
        if existing is not None and existing != account_id:
            raise RuntimeError(
                f"Account identity changed from {existing} to {account_id} — fail closed; peak is account-scoped and must not cross accounts"
            )
        if getattr(self, "_peak_reconciled", False) and existing == account_id:
            return
        # No ledger -> no account-scoped persistence; just bind and keep current peak (for __new__ test mocks)
        if getattr(self, "ledger", None) is None:
            self._peak_account_id = account_id
            self._peak_reconciled = True
            return
        # Load sidecar
        import hashlib

        sidecar_peak: float | None = None
        sidecar_epoch = 0
        sidecar_account_id: str | None = None
        sidecar_exists = self._peak_state_path.exists()
        is_legacy = False
        if sidecar_exists:
            try:
                raw = self._peak_state_path.read_text(encoding="utf-8")
            except OSError as exc:
                raise RiskStatePersistenceError(
                    f"Peak equity file unreadable at {self._peak_state_path}: {exc}"
                ) from exc
            try:
                data = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise RiskStatePersistenceError(
                    f"Peak equity file is malformed JSON at {self._peak_state_path}: {exc}"
                ) from exc
            if not isinstance(data, dict) or "peak_equity" not in data:
                raise RiskStatePersistenceError(
                    f"Peak equity file missing 'peak_equity' key at {self._peak_state_path}"
                )
            try:
                peak = float(data["peak_equity"])
            except (TypeError, ValueError) as exc:
                raise RiskStatePersistenceError(
                    f"Peak equity value is not a number at {self._peak_state_path}: {data.get('peak_equity')!r}"
                ) from exc
            if not math.isfinite(peak):
                raise RiskStatePersistenceError(f"Peak equity is non-finite at {self._peak_state_path}: {peak!r}")
            if peak < 0:
                raise RiskStatePersistenceError(f"Peak equity is negative at {self._peak_state_path}: {peak!r}")
            try:
                epoch_val = int(data.get("epoch", 0) or 0)
            except (TypeError, ValueError) as exc:
                raise RiskStatePersistenceError(
                    f"Peak epoch is not an integer at {self._peak_state_path}: {data.get('epoch')!r}"
                ) from exc
            if epoch_val < 0:
                raise RiskStatePersistenceError(f"Peak epoch is negative at {self._peak_state_path}: {epoch_val!r}")
            # Determine binding
            has_account_id = "account_id" in data
            has_fingerprint = "account_fingerprint" in data
            if has_account_id:
                val = data.get("account_id")
                if val is None:
                    is_legacy = True
                    sidecar_peak = peak
                    sidecar_epoch = epoch_val
                    sidecar_account_id = None
                elif isinstance(val, str):
                    acct = val.strip()
                    if not acct:
                        is_legacy = True
                        sidecar_peak = peak
                        sidecar_epoch = epoch_val
                        sidecar_account_id = None
                    elif acct != account_id:
                        # Mismatch — ignore sidecar
                        log.warning(
                            "Peak sidecar account mismatch: sidecar=%s current=%s — ignoring sidecar",
                            acct,
                            account_id,
                        )
                        sidecar_peak = None
                        sidecar_epoch = 0
                        sidecar_account_id = acct
                        is_legacy = False
                    else:
                        sidecar_peak = peak
                        sidecar_epoch = epoch_val
                        sidecar_account_id = acct
                        is_legacy = False
                else:
                    raise RiskStatePersistenceError(
                        f"Peak account_id is not a string at {self._peak_state_path}: {val!r}"
                    )
            elif has_fingerprint:
                fp_raw = data.get("account_fingerprint")
                fp = str(fp_raw).strip() if fp_raw is not None else ""
                if not fp:
                    is_legacy = True
                    sidecar_peak = peak
                    sidecar_epoch = epoch_val
                    sidecar_account_id = None
                else:
                    current_fp = hashlib.sha256(account_id.encode()).hexdigest()
                    if fp != current_fp:
                        log.warning(
                            "Peak sidecar fingerprint mismatch: sidecar=%s current_account=%s — ignoring sidecar",
                            fp[:8],
                            account_id[:8],
                        )
                        sidecar_peak = None
                        sidecar_epoch = 0
                        sidecar_account_id = fp
                        is_legacy = False
                    else:
                        sidecar_peak = peak
                        sidecar_epoch = epoch_val
                        sidecar_account_id = account_id
                        is_legacy = False
            else:
                # Legacy without binding
                is_legacy = True
                sidecar_peak = peak
                sidecar_epoch = epoch_val
                sidecar_account_id = None
        persisted_peak, persisted_epoch = self._query_persisted_peak(account_id)
        # Decide peak for this account
        if sidecar_peak is None and persisted_peak is None:
            # Case A: genuine first run for this account
            peak_final = 0.0
            epoch_final = 0
        elif sidecar_peak is None and persisted_peak is not None:
            peak_final = float(persisted_peak)
            epoch_final = int(persisted_epoch or 0)
        elif sidecar_peak is not None and persisted_peak is None:
            if is_legacy:
                log.warning(
                    "Legacy peak sidecar without account_id for account=%s — rewriting bound sidecar",
                    account_id,
                )
            peak_final = float(sidecar_peak)
            epoch_final = int(sidecar_epoch or 0)
        else:
            assert sidecar_peak is not None and persisted_peak is not None
            if is_legacy:
                # Legacy: max(legacy, persisted) then bind
                if float(sidecar_peak) >= float(persisted_peak):
                    peak_final = float(sidecar_peak)
                    epoch_final = int(sidecar_epoch or 0)
                else:
                    peak_final = float(persisted_peak)
                    epoch_final = int(persisted_epoch or 0)
                log.warning(
                    "Legacy sidecar peak=%s persisted=%s for account=%s — resolved to %s (epoch %s)",
                    sidecar_peak,
                    persisted_peak,
                    account_id,
                    peak_final,
                    epoch_final,
                )
            else:
                # Both account-bound: epoch mismatch -> newer epoch wins; same epoch -> max
                s_epoch = int(sidecar_epoch or 0)
                p_epoch = int(persisted_epoch or 0)
                if s_epoch != p_epoch:
                    if p_epoch > s_epoch:
                        peak_final = float(persisted_peak)
                        epoch_final = p_epoch
                    else:
                        peak_final = float(sidecar_peak)
                        epoch_final = s_epoch
                else:
                    peak_final = max(float(sidecar_peak), float(persisted_peak))
                    epoch_final = s_epoch
        self._peak_equity = float(peak_final)
        self._peak_epoch = int(epoch_final)
        self._peak_account_id = account_id
        self._peak_reconciled = True
        # Rewrite sidecar bound to current if needed
        needs_rewrite = False
        if not sidecar_exists:
            needs_rewrite = True
        elif is_legacy:
            needs_rewrite = True
        elif sidecar_peak is None:
            # mismatch case where sidecar was ignored
            needs_rewrite = True
        elif sidecar_account_id != account_id:
            needs_rewrite = True
        elif sidecar_peak is not None and (
            abs(float(sidecar_peak) - float(peak_final)) > 1e-9 or int(sidecar_epoch or 0) != int(epoch_final)
        ):
            needs_rewrite = True
        # Also ensure file is written with account_id binding even if peak matches but file was legacy/mismatch
        if needs_rewrite:
            # Only write if we have a meaningful peak (including 0 for first run)
            # For first run with no history we still want to avoid creating empty sidecar until equity known?
            # But spec says after reconciliation, rewrite bound to current if needed (atomic_write).
            # For genuine first run (both None) keep file absent until next equity save.
            if sidecar_peak is None and persisted_peak is None:
                # No history: do not create sidecar yet; it will be created on first equity max save
                pass
            else:
                try:
                    payload = json.dumps(
                        {"account_id": account_id, "peak_equity": float(peak_final), "epoch": int(epoch_final)}
                    )
                    atomic_write(self._peak_state_path, payload)
                except Exception:
                    log.warning("Failed to reconcile peak sidecar to account %s", account_id, exc_info=True)

    def _load_peak_equity(self) -> float:
        """Legacy entry point — now account-scoped.

        When ``_peak_account_id`` is already known, delegates to
        ``_ensure_peak_reconciled`` for that account. Otherwise preserves the
        old no-ledger bootstrap path (0.0 or sidecar value) so unit tests that
        construct a broker without a ledger keep working. Corrupt sidecars
        still fail closed.
        """
        # If we already have an account binding, use it
        acct = getattr(self, "_peak_account_id", None) or getattr(self, "_account_id", None)
        if acct:
            # Account known — do full reconciliation (current_equity not yet known, pass peak)
            self._ensure_peak_reconciled(str(acct), float(getattr(self, "_peak_equity", 0.0) or 0.0))
            return float(self._peak_equity)
        # No account known and no ledger — keep old behavior for backward compat
        if self.ledger is None:
            return float(self._peak_equity)
        # No account yet but ledger present — cannot decide; return current (0)
        return float(self._peak_equity)

    def _save_peak_equity(self) -> None:
        if not math.isfinite(self._peak_equity) or self._peak_equity < 0:
            raise ValueError(f"Cannot persist non-finite or negative peak: {self._peak_equity!r}")
        epoch = int(getattr(self, "_peak_epoch", 0) or 0)
        account_id = getattr(self, "_peak_account_id", None) or getattr(self, "_account_id", None) or "unknown"
        # _peak_account_id must be set after reconciliation; fail closed if caller tries to save before it
        if getattr(self, "_peak_account_id", None) is None and getattr(self, "_account_id", None):
            # Ensure account binding before save
            account_id = str(getattr(self, "_account_id"))
            self._peak_account_id = account_id
        payload = json.dumps({"account_id": account_id, "peak_equity": self._peak_equity, "epoch": epoch})
        atomic_write(self._peak_state_path, payload)

    def reset_peak_equity(self, equity: float) -> None:
        """Start a new risk epoch: rebase the drawdown peak to ``equity``.

        The peak is deliberately monotonic (drawdown is measured against the
        highest observed equity); only a verified operator action may rebase
        it. ``equity`` must be positive. Account-scoped: increments epoch
        only for the current account and queries persisted state only for
        that account.
        """
        if equity <= 0:
            raise ValueError("Cannot reset the equity peak to a non-positive value")
        account_id = getattr(self, "_peak_account_id", None) or getattr(self, "_account_id", None)
        if not account_id:
            raise RuntimeError("Cannot reset peak before account is known (peak is account-scoped)")
        account_id = str(account_id)
        # New epoch is max of sidecar and persisted +1 for THIS account only
        _, persisted_epoch = self._query_persisted_peak(account_id)
        current_epoch = int(getattr(self, "_peak_epoch", 0) or 0)
        if persisted_epoch is not None:
            current_epoch = max(current_epoch, int(persisted_epoch))
        self._peak_epoch = current_epoch + 1
        self._peak_equity = float(equity)
        self._peak_account_id = account_id
        self._peak_reconciled = True
        self._save_peak_equity()
        log.warning(
            "Equity peak rebased to %.2f (epoch %d, %s)",
            self._peak_equity,
            self._peak_epoch,
            "paper" if self._paper else "live",
        )


__all__ = ["AlpacaBroker", "CryptoPriceObservation", "RiskStatePersistenceError"]
