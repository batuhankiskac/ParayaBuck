"""Runtime-specific immutable models and vocabulary.

Extracted from ``runtime_service.py`` for cohesion: pure data, terminal
order vocabulary, rebalance phases, and decision helpers. No DB, no broker,
no risk decisions beyond type definitions.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Protocol

from parayabuck.config import DrawdownLiquidationConfig
from parayabuck.core.models import AccountSnapshot, Bar, Market
from parayabuck.execution.portfolio import (
    AssetTradingRules,
    BrokerWorkingBuyOrder,
    PortfolioExecutionPolicy,
    PortfolioOrderRequest,
    PortfolioRiskLimits,
    RiskOverlayDecision,
)
from parayabuck.markets.symbols import canonical_symbol
from parayabuck.portfolio.base import PortfolioContext, PortfolioStrategy
from parayabuck.portfolio.models import TargetPortfolio

# Alpaca order states stored by the repository: canonical statuses written by
# record_order/update_order plus raw TradingStream event names written by
# apply_trade_update ("fill", "partial_fill"). An order is terminal once it
# cannot fill any more quantity.
_TERMINAL_ORDER_STATUSES = frozenset(
    {
        "filled",
        "canceled",
        "cancelled",
        "expired",
        "rejected",
        "done_for_day",
        "fill",
    }
)

# Order outcomes that are neither terminal nor provably working: the order
# may or may not exist at the broker. Dependent BUY orders must not be
# submitted while a required SELL sits in this state.
_AMBIGUOUS_ORDER_STATUSES = frozenset({"reconciliation_required", "manual_intervention_required"})

# Two-phase rebalance execution phases (persisted, restart-safe).
REBALANCE_PHASE_SELL_PENDING = "sell_pending"
REBALANCE_PHASE_BUY_READY = "buy_ready"
REBALANCE_PHASE_BUY_PENDING = "buy_pending"
REBALANCE_PHASE_COMPLETE = "complete"
REBALANCE_PHASE_BLOCKED = "blocked"


def is_terminal_order_status(status: str) -> bool:
    return str(status).lower() in _TERMINAL_ORDER_STATUSES


# Single SQL source for the terminal set above: queries must never hardcode
# a second copy that can drift from is_terminal_order_status.
TERMINAL_ORDER_STATUSES_SQL = ", ".join(f"'{status}'" for status in sorted(_TERMINAL_ORDER_STATUSES))


@dataclass(frozen=True)
class OpenPortfolioOrder:
    """One non-terminal portfolio order attributed to an account."""

    client_order_id: str
    symbol: str
    market: Market
    side: str
    quantity: float
    cumulative_filled_quantity: float
    broker_order_id: str = ""
    reference_price: float = 0.0
    # Row status and its latest write time (creation or broker-call start or
    # return); reconciliation uses them to close a never-accepted order.
    status: str = ""
    last_updated_at: datetime | None = None
    # True while a broker submit call has started and has not returned: its
    # outcome is unknown, so reconciliation must not close the row yet.
    submit_call_in_flight: bool = False


@dataclass(frozen=True)
class RebalanceExecutionState:
    """Persisted two-phase rebalance lifecycle for one decision.

    A decision with both SELL and BUY legs executes in two phases: the
    risk-reducing sells are submitted first and the dependent buys only after
    every required sell is terminal and a fresh account snapshot proves the
    remaining BUY exposure is still safe. The state survives process restarts
    so a rebalance interrupted between phases resumes exactly once.
    """

    decision_id: str
    account_id: str
    market: Market
    phase: str
    status: str
    updated_at: datetime


@dataclass(frozen=True)
class PortfolioRuntimeResult:
    decision_id: str | None
    target: TargetPortfolio | None
    risk: RiskOverlayDecision | None
    orders: tuple[PortfolioOrderRequest, ...]
    submitted_order_ids: tuple[str, ...]
    status: str


@dataclass(frozen=True)
class BuyAdmission:
    account: AccountSnapshot
    orders: tuple[PortfolioOrderRequest, ...]
    realized_gross: float
    committed_buy_notional: float | None
    gross_headroom: float
    cash_headroom: float
    reason: str | None


class PortfolioRuntimeBroker(Protocol):
    def account(self) -> AccountSnapshot: ...

    def asset_trading_rules(self, symbol: str, market: Market) -> AssetTradingRules: ...

    def all_open_order_symbols(self) -> set[str]: ...

    def working_buy_orders(self) -> tuple[BrokerWorkingBuyOrder, ...]: ...

    def submit_portfolio_order(self, order: PortfolioOrderRequest) -> str: ...


class PortfolioDecisionService:
    """Build a strategy context exclusively from finalized point-in-time bars."""

    def build_context(
        self,
        strategy: PortfolioStrategy,
        history: dict[str, list[Bar]],
        account: AccountSnapshot,
        decision_timestamp: datetime,
        *,
        universe: object | None,
        universe_version: str,
        required_symbols: tuple[str, ...] | None = None,
    ) -> PortfolioContext:
        """Context for one decision.

        Every ``required_symbols`` series (all of ``history`` when None) must
        end on ``decision_timestamp``. Other series ride along as history
        but price only when they are current too: a stale benchmark close
        never becomes an order reference price.
        """
        required = tuple(history) if required_symbols is None else required_symbols
        if any(not history.get(symbol) or history[symbol][-1].timestamp != decision_timestamp for symbol in required):
            raise ValueError("Portfolio context requires a complete closed-bar batch")
        return PortfolioContext(
            decision_timestamp=decision_timestamp,
            market=strategy.market,
            timeframe=strategy.timeframe,
            history={symbol: tuple(series) for symbol, series in history.items()},
            universe=universe,
            universe_version=universe_version,
            equity=account.equity,
            holdings={
                canonical_symbol(position.symbol, position.market): position.qty
                for position in account.positions
                if position.market == strategy.market
            },
            prices={
                symbol: series[-1].close
                for symbol, series in history.items()
                if series and series[-1].timestamp == decision_timestamp
            },
            benchmark_symbol="SPY",
        )


def _decision_id(account_id: str, checksum: str, timestamp: datetime, market: Market) -> str:
    return hashlib.sha256(f"{account_id}|{checksum}|{timestamp.isoformat()}|{market.value}".encode()).hexdigest()[:24]


def configuration_digest(
    strategy: PortfolioStrategy,
    mode: str,
    maximum_allocation: float,
    universe_version: str,
    risk_limits: PortfolioRiskLimits | None = None,
    execution_policy: PortfolioExecutionPolicy | None = None,
    drawdown_liquidation: DrawdownLiquidationConfig | None = None,
) -> str:
    payload: dict[str, object] = {
        "strategy": strategy.name,
        "version": strategy.version,
        "parameters": strategy.parameter_tuple(),
        "mode": mode,
        "maximum_allocation": maximum_allocation,
        "scanner_policy_digest": universe_version,
        "risk_limits": asdict(risk_limits or PortfolioRiskLimits()),
        "execution_policy": asdict(execution_policy or PortfolioExecutionPolicy()),
    }
    # The default-off catastrophe brake joins the digest only while enabled, so
    # every existing digest (and every LIVE approval bound to one) is unchanged.
    if drawdown_liquidation is not None and drawdown_liquidation.enabled:
        payload["drawdown_liquidation"] = asdict(drawdown_liquidation)
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


__all__ = [
    "REBALANCE_PHASE_BLOCKED",
    "REBALANCE_PHASE_BUY_PENDING",
    "REBALANCE_PHASE_BUY_READY",
    "REBALANCE_PHASE_COMPLETE",
    "REBALANCE_PHASE_SELL_PENDING",
    "TERMINAL_ORDER_STATUSES_SQL",
    "OpenPortfolioOrder",
    "PortfolioDecisionService",
    "PortfolioRuntimeBroker",
    "PortfolioRuntimeResult",
    "RebalanceExecutionState",
    "_AMBIGUOUS_ORDER_STATUSES",
    "_TERMINAL_ORDER_STATUSES",
    "configuration_digest",
    "is_terminal_order_status",
]
