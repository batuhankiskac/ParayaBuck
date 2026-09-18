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

from parayabuck.core.models import AccountSnapshot, Bar, Market
from parayabuck.execution.portfolio import (
    AssetTradingRules,
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
_NON_TERMINAL_REBALANCE_PHASES = frozenset(
    {
        REBALANCE_PHASE_SELL_PENDING,
        REBALANCE_PHASE_BUY_READY,
        REBALANCE_PHASE_BUY_PENDING,
    }
)


def is_terminal_order_status(status: str) -> bool:
    return str(status).lower() in _TERMINAL_ORDER_STATUSES


# Single SQL source for the terminal set above: queries must never hardcode
# a second copy that can drift from is_terminal_order_status.
TERMINAL_ORDER_STATUSES_SQL = ", ".join(f"'{status}'" for status in sorted(_TERMINAL_ORDER_STATUSES))


# Allowed rebalance transitions (fail-closed state machine).
# Documented for review; enforcement is via repository + runtime.
REBALANCE_TRANSITIONS: dict[str, frozenset[str]] = {
    REBALANCE_PHASE_SELL_PENDING: frozenset({REBALANCE_PHASE_BUY_READY, REBALANCE_PHASE_BLOCKED}),
    REBALANCE_PHASE_BUY_READY: frozenset({REBALANCE_PHASE_BUY_PENDING, REBALANCE_PHASE_BLOCKED}),
    REBALANCE_PHASE_BUY_PENDING: frozenset({REBALANCE_PHASE_COMPLETE, REBALANCE_PHASE_BLOCKED}),
    REBALANCE_PHASE_BLOCKED: frozenset({REBALANCE_PHASE_SELL_PENDING}),  # new rebalance after block
    REBALANCE_PHASE_COMPLETE: frozenset({REBALANCE_PHASE_SELL_PENDING}),  # next decision starts new cycle
}


def is_allowed_rebalance_transition(frm: str, to: str) -> bool:
    """Whether a persisted rebalance phase transition is allowed."""
    return to in REBALANCE_TRANSITIONS.get(frm, frozenset())


@dataclass(frozen=True)
class OpenPortfolioOrder:
    """One non-terminal portfolio order attributed to an account."""

    client_order_id: str
    symbol: str
    market: Market
    side: str
    quantity: float
    cumulative_filled_quantity: float


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

    @property
    def active(self) -> bool:
        return self.phase in _NON_TERMINAL_REBALANCE_PHASES and self.status == "active"


@dataclass(frozen=True)
class PortfolioRuntimeResult:
    decision_id: str | None
    target: TargetPortfolio | None
    risk: RiskOverlayDecision | None
    orders: tuple[PortfolioOrderRequest, ...]
    submitted_order_ids: tuple[str, ...]
    status: str


class PortfolioRuntimeBroker(Protocol):
    def account(self) -> AccountSnapshot: ...

    def asset_trading_rules(self, symbol: str, market: Market) -> AssetTradingRules: ...

    def all_open_order_symbols(self) -> set[str]: ...

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
        ordinary_alpha_excluded_symbols: tuple[str, ...] = (),
    ) -> PortfolioContext:
        if any(not series or series[-1].timestamp != decision_timestamp for series in history.values()):
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
            prices={symbol: series[-1].close for symbol, series in history.items()},
            benchmark_symbol="BTC/USD" if strategy.market == Market.CRYPTO else "SPY",
            ordinary_alpha_excluded_symbols=ordinary_alpha_excluded_symbols,
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
) -> str:
    payload = json.dumps(
        {
            "strategy": strategy.name,
            "version": strategy.version,
            "parameters": strategy.parameter_tuple(),
            "mode": mode,
            "maximum_allocation": maximum_allocation,
            "scanner_policy_digest": universe_version,
            "risk_limits": asdict(risk_limits or PortfolioRiskLimits()),
            "execution_policy": asdict(execution_policy or PortfolioExecutionPolicy()),
        },
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(payload.encode()).hexdigest()


__all__ = [
    "REBALANCE_PHASE_BLOCKED",
    "REBALANCE_PHASE_BUY_PENDING",
    "REBALANCE_PHASE_BUY_READY",
    "REBALANCE_PHASE_COMPLETE",
    "REBALANCE_PHASE_SELL_PENDING",
    "REBALANCE_TRANSITIONS",
    "TERMINAL_ORDER_STATUSES_SQL",
    "OpenPortfolioOrder",
    "PortfolioDecisionService",
    "PortfolioRuntimeBroker",
    "PortfolioRuntimeResult",
    "RebalanceExecutionState",
    "_AMBIGUOUS_ORDER_STATUSES",
    "_NON_TERMINAL_REBALANCE_PHASES",
    "_TERMINAL_ORDER_STATUSES",
    "configuration_digest",
    "is_allowed_rebalance_transition",
    "is_terminal_order_status",
]
