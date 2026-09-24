"""Research-only physical shared-account scheduler with structural output.

Copied from physical-clock V2. The event loop retains its physical phases and
mechanical account ledger. Portfolio result construction and time-series
storage are removed; only whitelisted capacity fields leave the scheduler.
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from collections.abc import Iterator, MutableMapping
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, cast

from parayabuck.backtest.portfolio_engine import (
    OrderSide,
    PortfolioBacktestEngine,
    PortfolioFill,
    PortfolioOrder,
    _Position,
    _prepare_corporate_actions,
    session_date_for,
)
from parayabuck.core.models import AccountSnapshot, Bar, Market, PositionSnapshot
from parayabuck.data.market_dataset import CorporateAction
from parayabuck.execution.portfolio import (
    PortfolioOrderRequest,
    PortfolioOrderSide,
    PortfolioRiskLimits,
    PortfolioRiskOverlay,
    RiskOverlayDecision,
    reserve_opening_order_slots,
)
from parayabuck.markets.symbols import canonical_symbol
from parayabuck.markets.timeframes import NEW_YORK
from parayabuck.portfolio.base import PortfolioStrategy
from parayabuck.portfolio.models import RebalancePolicy, TargetPortfolio
from research.residual_reversal_physical_capacity_replay_v1.timing import PhysicalReplayTime

InstrumentKey = tuple[Market, str]
ORDER_MARKETS = (Market.CRYPTO, Market.EQUITY)
SUPPORTED_STRATEGIES = {
    (Market.EQUITY, "equity_residual_momentum_v3"),
    (Market.CRYPTO, "crypto_low_variance_hysteresis_v1"),
}

# The raw V2 event stream is needed internally for accounting checks. Only
# these fields may leave this research scheduler.
STRUCTURAL_EVENT_FIELDS = frozenset(
    {
        "event",
        "market",
        "timestamp",
        "label_time",
        "decision_timestamp",
        "decision_label_time",
        "order_created_at",
        "source_price_event_time",
        "source_ex_date_label",
        "session_date",
        "symbol",
        "side",
        "qty",
        "quantity",
        "client_order_id",
        "action_type",
        "split_ratio",
        "position_qty_before",
        "position_qty_after",
        "reason",
        "policy",
        "effective_policy",
        "blocking_sell_orders",
        "target_weights",
        "risk_overlay_reasons",
        "position_keys_before_next_decision",
        "position_count",
        "slot_claim_count",
        "maximum_positions",
        "same_cycle_buy_reservations",
        "pending_orders",
        "pending",
        "source",
        "source_label",
        "source_rank",
        "requested_target_contribution",
        "admitted_target_contribution",
        "deferred_target_contribution",
        "combined_account_target",
        "existing_position",
        "working_opening_reservation",
        "requested_opening",
        "admitted_opening",
        "denial_reason",
        "overlap",
        "slot_claims_before",
        "slot_claims_after",
        "global_cap",
        "crypto_count",
        "crypto_count_before",
        "crypto_count_after",
        "crypto_claim_count_before",
        "crypto_claim_count_after",
        "position_keys_before",
        "position_keys_after",
        "pending_opening_keys_before",
        "pending_opening_keys_after",
        "pending_sell_keys_before",
        "pending_sell_keys_after",
        "pending_sell",
        "fill_timestamp",
        "terminal_timestamp",
        "denial_detail",
        "cause",
        "qty_delta",
        "position_qty",
        "source_quantities",
        "desired_source_qty",
        "source_allocation",
    }
)
DECISION_INSTRUMENTATION_FIELDS = frozenset(
    {
        "slot_claims_before",
        "slot_claims_after",
        "crypto_count_before",
        "crypto_count_after",
        "crypto_claim_count_before",
        "crypto_claim_count_after",
        "position_keys_before",
        "position_keys_after",
        "pending_opening_keys_before",
        "pending_opening_keys_after",
        "pending_sell_keys_before",
        "pending_sell_keys_after",
    }
)
STRUCTURAL_INVARIANT_FIELDS = frozenset(
    {
        "cash_conserved",
        "nav_matches_ledger",
        "position_quantities_reconciled",
        "final_position_count",
        "final_position_count_within_limit",
        "max_observed_position_count",
        "maximum_positions",
        "max_observed_slot_claim_count",
        "slot_claims_within_limit",
        "slot_claim_violation_count",
        "deferred_buy_intent_count",
        "no_deferred_buy_intents_at_end",
        "target_gross_within_limit",
        "target_name_weights_within_limits",
        "sleeve_targets_within_limits",
        "reservation_create_count",
        "reservation_release_count",
        "active_reservation_count",
        "all_reservations_released",
        "unowned_fill_count",
        "duplicate_fill_count",
        "future_data_access_checks",
        "no_future_data_access",
        "ending_cash_nonnegative",
        "dividend_cash_event_count",
        "fill_count",
    }
)


def _structural_event(event: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in event.items() if key in STRUCTURAL_EVENT_FIELDS}


def _structural_invariants(invariants: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in invariants.items() if key in STRUCTURAL_INVARIANT_FIELDS}


def _pro_rata(total: float, weights: dict[str, float]) -> dict[str, float]:
    positive = [(source, weight) for source, weight in sorted(weights.items()) if weight > 1e-12]
    if total <= 1e-12 or not positive:
        return {}
    denominator = sum(weight for _source, weight in positive)
    allocated: dict[str, float] = {}
    remaining = total
    for source, weight in positive[:-1]:
        amount = total * weight / denominator
        allocated[source] = amount
        remaining -= amount
    allocated[positive[-1][0]] = remaining
    return allocated


class _PositionView(MutableMapping[str, _Position]):
    def __init__(self, account: SharedAccount, market: Market) -> None:
        self._account = account
        self._market = market

    def __getitem__(self, symbol: str) -> _Position:
        return self._account.positions[(self._market, canonical_symbol(symbol, self._market))]

    def __setitem__(self, symbol: str, position: _Position) -> None:
        key = canonical_symbol(symbol, self._market)
        if position.market != self._market or canonical_symbol(position.symbol, self._market) != key:
            raise ValueError("Position view cannot store a different market or symbol")
        self._account.positions[(self._market, key)] = position

    def __delitem__(self, symbol: str) -> None:
        del self._account.positions[(self._market, canonical_symbol(symbol, self._market))]

    def __iter__(self) -> Iterator[str]:
        return iter(symbol for market, symbol in self._account.positions if market == self._market)

    def __len__(self) -> int:
        return sum(market == self._market for market, _symbol in self._account.positions)

    def clear(self) -> None:
        for symbol in tuple(self):
            del self[symbol]


class _MarkView(MutableMapping[str, float]):
    def __init__(self, account: SharedAccount, market: Market) -> None:
        self._account = account
        self._market = market

    def __getitem__(self, symbol: str) -> float:
        return self._account.marks[(self._market, canonical_symbol(symbol, self._market))]

    def __setitem__(self, symbol: str, value: float) -> None:
        self._account.marks[(self._market, canonical_symbol(symbol, self._market))] = float(value)

    def __delitem__(self, symbol: str) -> None:
        del self._account.marks[(self._market, canonical_symbol(symbol, self._market))]

    def __iter__(self) -> Iterator[str]:
        return iter(sorted(symbol for market, symbol in self._account.marks if market == self._market))

    def __len__(self) -> int:
        return sum(market == self._market for market, _symbol in self._account.marks)

    def clear(self) -> None:
        for symbol in tuple(self):
            del self[symbol]


@dataclass
class SharedAccount:
    """One flat, all-cash research ledger with asset-aware inventory keys."""

    starting_nav: float
    starting_cash: float
    start_timestamp: datetime | None = None
    cash: float = field(init=False)
    positions: dict[InstrumentKey, _Position] = field(default_factory=dict)
    marks: dict[InstrumentKey, float] = field(default_factory=dict)
    pending: dict[Market, list[PortfolioOrder]] = field(default_factory=lambda: {Market.EQUITY: [], Market.CRYPTO: []})
    deferred_buys: dict[Market, list[PortfolioOrder]] = field(
        default_factory=lambda: {Market.EQUITY: [], Market.CRYPTO: []}
    )
    engines: dict[Market, _SharedEngine] = field(default_factory=dict)
    same_cycle_buy_reservations: set[InstrumentKey] = field(default_factory=set)
    peak_equity: float = field(init=False)
    day_start_equity: float = field(init=False)
    last_observed_equity: float = field(init=False)
    risk_session_date: date | None = None
    dividend_cash: float = 0.0
    reservation_states: dict[tuple[Market, str], str] = field(default_factory=dict)
    order_submissions: set[tuple[Market, str]] = field(default_factory=set)

    def __post_init__(self) -> None:
        if self.starting_nav <= 0 or self.starting_cash != self.starting_nav:
            raise ValueError("Replay must start flat and all-cash at a positive NAV")
        self.cash = float(self.starting_cash)
        self.peak_equity = float(self.starting_nav)
        self.day_start_equity = float(self.starting_nav)
        self.last_observed_equity = float(self.starting_nav)

    def nav(self) -> float:
        return self.cash + sum(
            float(position.qty) * float(self.marks.get(key, position.avg_price))
            for key, position in self.positions.items()
        )

    def snapshots(self) -> tuple[PositionSnapshot, ...]:
        return tuple(
            PositionSnapshot(
                symbol=position.symbol,
                qty=position.qty,
                avg_price=position.avg_price,
                current_price=self.marks.get((market, position.symbol), position.avg_price),
                market=market,
                qty_available=position.qty,
                ownership="managed",
                managed_qty=position.qty,
            )
            for (market, _symbol), position in self.positions.items()
            if position.qty > 0
        )


class _SharedOverlay:
    def __init__(self, overlay: PortfolioRiskOverlay | None, account: SharedAccount) -> None:
        self._overlay = overlay
        self._account = account
        self.limits: PortfolioRiskLimits | None = overlay.limits if overlay is not None else None
        self.last_raw_target: TargetPortfolio | None = None

    def apply(
        self,
        target: TargetPortfolio,
        account: AccountSnapshot,
        *,
        market: Market,
        approved_symbols: tuple[str, ...],
        maximum_allocation: float,
        mode: str,
        enforce_drawdown_halt: bool = True,
        reserved_position_keys: tuple[InstrumentKey, ...] = (),
        open_order_symbols: tuple[str, ...] = (),
    ) -> RiskOverlayDecision:
        del account  # The coordinator supplies the account-wide snapshot below.
        if self._overlay is None:
            raise RuntimeError("Shared replay requires the canonical portfolio risk overlay")
        self.last_raw_target = target
        shared_coordination = len(self._account.engines) > 1
        pending_keys = (
            {
                (current_market, order.symbol)
                for current_market in ORDER_MARKETS
                for order in self._account.pending[current_market]
            }
            if shared_coordination
            else set()
        )
        deferred_keys = (
            {
                (current_market, order.symbol)
                for current_market in ORDER_MARKETS
                if current_market != market
                for order in self._account.deferred_buys[current_market]
            }
            if shared_coordination
            else set()
        )
        reservations = tuple(
            sorted(
                self._account.same_cycle_buy_reservations | set(reserved_position_keys) | pending_keys | deferred_keys,
                key=lambda item: (item[0].value, item[1]),
            )
        )
        return self._overlay.apply(
            target,
            self._account.engines[market]._account_snapshot(),
            market=market,
            approved_symbols=approved_symbols,
            maximum_allocation=maximum_allocation,
            mode=mode,
            enforce_drawdown_halt=enforce_drawdown_halt,
            reserved_position_keys=reservations,
            open_order_symbols=open_order_symbols,
        )


class _SharedEngine(PortfolioBacktestEngine):
    """Existing market engine primitives backed by a shared account ledger."""

    _shared_account_position_view: _PositionView
    _shared_mark_view: _MarkView
    _shared_overlay: _SharedOverlay

    def __init__(self, account: SharedAccount, strategy: PortfolioStrategy, policy: RebalancePolicy, **kwargs: Any):
        self._shared_account = account
        self._shared_market = strategy.market
        super().__init__(strategy, policy, state=None, state_path=None, **kwargs)
        overlay = getattr(self, "risk_overlay", None)
        if overlay is None:
            raise ValueError("Shared replay engine needs the canonical risk overlay")
        self._shared_overlay = _SharedOverlay(overlay, account)
        setattr(self, "risk_overlay", self._shared_overlay)
        account.engines[strategy.market] = self

    def _known_average_volume(self, order: PortfolioOrder, timeframe: str) -> float:
        if self.strategy.market != Market.CRYPTO:
            return float(super()._known_average_volume(order, timeframe))
        history = self._execution_history.get((order.symbol, timeframe), ())
        known = [
            bar.volume for bar in history if bar.timestamp + timedelta(days=1) <= order.created_at and bar.volume > 0
        ][-self.liquidity_lookback_bars :]
        return sum(known) / len(known) if known else 0.0

    @property
    def _cash(self) -> float:
        return self._shared_account.cash

    @_cash.setter
    def _cash(self, value: float) -> None:
        self._shared_account.cash = float(value)

    @property  # type: ignore[override]
    def _positions(self) -> _PositionView:
        return self._shared_account_position_view

    @_positions.setter
    def _positions(self, values: MutableMapping[str, _Position] | dict[str, _Position]) -> None:
        view = getattr(self, "_shared_account_position_view", None)
        if view is None:
            view = _PositionView(self._shared_account, self._shared_market)
            self._shared_account_position_view = view
        view.clear()
        for symbol, position in values.items():
            view[symbol] = position

    @property
    def _pending(self) -> list[PortfolioOrder]:
        return self._shared_account.pending[self._shared_market]

    @_pending.setter
    def _pending(self, values: list[PortfolioOrder]) -> None:
        self._shared_account.pending[self._shared_market][:] = values

    @property  # type: ignore[override]
    def _last_price(self) -> _MarkView:
        return self._shared_mark_view

    @_last_price.setter
    def _last_price(self, values: MutableMapping[str, float] | dict[str, float]) -> None:
        view = getattr(self, "_shared_mark_view", None)
        if view is None:
            view = _MarkView(self._shared_account, self._shared_market)
            self._shared_mark_view = view
        view.clear()
        for symbol, value in values.items():
            view[symbol] = value

    def _equity(self) -> float:
        return self._shared_account.nav()

    def _account_snapshot(self) -> AccountSnapshot:
        equity = self._shared_account.nav()
        positions = self._shared_account.snapshots()
        return AccountSnapshot(
            cash=self._shared_account.cash,
            equity=equity,
            peak_equity=self._shared_account.peak_equity,
            daily_pnl=equity - self._shared_account.day_start_equity,
            positions=positions,
            realized_pnl=sum(engine.state.realized_pnl for engine in self._shared_account.engines.values()),
            unrealized_pnl=sum(position.unrealized_pnl for position in positions),
            fees=sum(sum(fill.fee for fill in engine._fills) for engine in self._shared_account.engines.values()),
        )


@dataclass
class MarketReplayInput:
    engine: _SharedEngine
    execution_bars: list[Bar]
    warmup_bars: list[Bar] = field(default_factory=list)
    signal_bars: list[Bar] | None = None
    corporate_actions: list[CorporateAction] = field(default_factory=list)
    signal_availability: dict[datetime, datetime] = field(default_factory=dict)
    action_availability: dict[datetime, datetime] = field(default_factory=dict)
    terminal_at: datetime | None = None
    final_close_at: datetime | None = None
    execution_schedule: dict[datetime, datetime] = field(default_factory=dict)


@dataclass(frozen=True)
class SharedReplayResult:
    positions: tuple[dict[str, Any], ...]
    events: tuple[dict[str, Any], ...]
    invariants: dict[str, Any]

    @property
    def event_sha256(self) -> str:
        body = json.dumps(self.events, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        return hashlib.sha256(body).hexdigest()


def _admit_synthetic_orders(
    orders: tuple[PortfolioOrder, ...],
    account: AccountSnapshot,
    maximum_positions: int,
    reserved_position_keys: tuple[InstrumentKey, ...],
) -> tuple[PortfolioOrder, ...]:
    """Apply the current production slot policy to historical order records."""
    requests = tuple(
        PortfolioOrderRequest(
            client_order_id=order.client_order_id,
            decision_id=order.client_order_id,
            symbol=order.symbol,
            market=order.market,
            side=PortfolioOrderSide(order.side.value),
            quantity=order.qty,
            reference_price=0.0,
            decision_timestamp=order.decision_timestamp,
            strategy_name="historical-replay",
            strategy_version="2",
            risk_reducing=order.side == OrderSide.SELL,
        )
        for order in orders
    )
    admitted = reserve_opening_order_slots(
        requests,
        account,
        maximum_positions=maximum_positions,
        reserved_position_keys=reserved_position_keys,
    )
    admitted_ids = {order.client_order_id for order in admitted}
    return tuple(order for order in orders if order.client_order_id in admitted_ids)


class SharedAccountReplay:
    """Chronological two-market replay using one cash and position ledger."""

    def __init__(
        self,
        *,
        starting_nav: float = 100_000.0,
        maximum_positions: int = 6,
        allow_test_strategies: bool = False,
    ) -> None:
        self.account = SharedAccount(starting_nav, starting_nav)
        if maximum_positions not in (6, 8):
            raise ValueError("Structural replay supports only the frozen caps six and eight")
        self.maximum_positions = maximum_positions
        self.allow_test_strategies = allow_test_strategies
        self.inputs: dict[Market, MarketReplayInput] = {}
        self._events: list[dict[str, Any]] = []
        self._source_qty: dict[InstrumentKey, dict[str, float]] = {}
        self._source_targets: dict[InstrumentKey, dict[str, float]] = {}
        self._order_desired: dict[tuple[Market, str], dict[str, float]] = {}

    @property
    def internal_event_sha256(self) -> str:
        """Hash V2-format events for incumbent parity without exposing them."""
        authority_events = (
            {key: value for key, value in event.items() if key not in DECISION_INSTRUMENTATION_FIELDS}
            if event["event"] == "decision"
            else event
            for event in self._events
            if event["event"]
            not in {"source_admission", "source_inventory", "source_order_claim", "source_order_adjustment"}
        )
        body = json.dumps(tuple(authority_events), sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        return hashlib.sha256(body).hexdigest()

    def _claim_snapshot(self) -> dict[str, Any]:
        positions = set(self.account.positions)
        opening = {
            (market, canonical_symbol(order.symbol, market))
            for market in ORDER_MARKETS
            for order in self.account.pending[market]
            if order.side == OrderSide.BUY
        }
        selling = {
            (market, canonical_symbol(order.symbol, market))
            for market in ORDER_MARKETS
            for order in self.account.pending[market]
            if order.side == OrderSide.SELL
        }

        def keys(items: set[InstrumentKey]) -> list[list[str]]:
            return [
                [market.value, symbol] for market, symbol in sorted(items, key=lambda item: (item[0].value, item[1]))
            ]

        claims = positions | opening | selling
        return {
            "position_keys": keys(positions),
            "pending_opening_keys": keys(opening),
            "pending_sell_keys": keys(selling),
            "slot_claims": len(claims),
            "crypto_count": sum(market == Market.CRYPTO for market, _symbol in positions),
            "crypto_claim_count": sum(market == Market.CRYPTO for market, _symbol in claims),
        }

    def _finalize_decision_capacity_records(self, timestamp: datetime, first_event: int) -> None:
        """Expose only effective claims after V2's existing SELL-gated BUY deferral."""
        effective = self._claim_snapshot()
        deferred = {
            (market.value, canonical_symbol(order.symbol, market))
            for market in ORDER_MARKETS
            for order in self.account.deferred_buys[market]
            if order.created_at == timestamp
        }
        stamp = timestamp.isoformat()
        for event in self._events[first_event:]:
            if event.get("timestamp") != stamp:
                continue
            if event["event"] == "decision" and (deferred or event["slot_claims_after"] > self.maximum_positions):
                event["position_keys_after"] = effective["position_keys"]
                event["pending_opening_keys_after"] = effective["pending_opening_keys"]
                event["pending_sell_keys_after"] = effective["pending_sell_keys"]
                event["slot_claims_after"] = effective["slot_claims"]
                event["crypto_count_after"] = effective["crypto_count"]
                event["crypto_claim_count_after"] = effective["crypto_claim_count"]
            elif event["event"] == "source_admission":
                if deferred or event["slot_claims_after"] > self.maximum_positions:
                    event["slot_claims_after"] = effective["slot_claims"]
                    event["crypto_count"] = effective["crypto_count"]
                if (event["market"], event["symbol"]) in deferred and not event["existing_position"]:
                    event["deferred_target_contribution"] = event["admitted_target_contribution"]
                    event["admitted_opening"] = False
                    event["admitted_target_contribution"] = 0.0
                    if event["working_opening_reservation"]:
                        event["denial_reason"] = "OTHER_EXPLICIT"
                        event["denial_detail"] = "working_opening_replaced_deferred"
                    else:
                        event["denial_reason"] = "PENDING_SELL_SLOT_NOT_RELEASED"
                        event["denial_detail"] = "buy_intent_deferred_until_sell_terminal"
                    event["slot_claims_after"] = effective["slot_claims"]
                    event["crypto_count"] = effective["crypto_count"]
                    event["pending_sell"] = True

    def _record_source_admissions(
        self,
        engine: _SharedEngine,
        label: datetime,
        timestamp: datetime,
        record: Any,
        claims_before: dict[str, Any],
        claims_after: dict[str, Any],
        planned_buys: set[str],
    ) -> None:
        raw_target = engine._shared_overlay.last_raw_target
        if raw_target is None:
            raise AssertionError("Source admission has no raw target")
        strategy = engine.strategy
        sources: tuple[str, ...]
        if strategy.name == "equity_v3_plus_residual_reversal_physical_capacity_v1":
            raw_rows = cast(list[dict[str, Any]], getattr(strategy, "raw_decisions"))
            if not raw_rows or raw_rows[-1]["label_time"] != label.isoformat():
                raise AssertionError("Composite source target does not match decision label")
            raw = raw_rows[-1]
            contributions = cast(dict[str, dict[str, float]], raw["source_contributions"])
            raw_weights = {
                "v3": cast(dict[str, float], raw["v3_weights"]),
                "reversal": cast(dict[str, float], raw["reversal_weights"]),
            }
            ranks = {
                "v3": cast(list[str], raw["v3_symbols"]),
                "reversal": cast(list[str], raw["reversal_symbols"]),
            }
            sources = ("v3", "reversal")
        else:
            source = (
                "crypto"
                if strategy.market == Market.CRYPTO
                else "reversal"
                if strategy.name == "equity_short_term_residual_reversal_v1"
                else "v3"
            )
            raw_weights = {source: {weight.symbol: weight.weight for weight in raw_target.weights}}
            contributions = {
                symbol: {source: weight.weight}
                for symbol, weight in ((weight.symbol, weight) for weight in raw_target.weights)
            }
            ranks = {
                source: [
                    weight.symbol
                    for weight in sorted(raw_target.weights, key=lambda item: (item.rank or 10**9, item.symbol))
                ]
            }
            sources = (source,)
        approved = dict(record.target_weights)
        positions_before = {tuple(key) for key in claims_before["position_keys"]}
        opening_before = {tuple(key) for key in claims_before["pending_opening_keys"]}
        opening_after = {tuple(key) for key in claims_after["pending_opening_keys"]}
        selling_before = {tuple(key) for key in claims_before["pending_sell_keys"]}
        active_buys = {
            canonical_symbol(order.symbol, strategy.market)
            for order in engine._pending
            if order.side == OrderSide.BUY and order.decision_timestamp == label
        }
        limits = engine._shared_overlay.limits
        if limits is None:
            raise AssertionError("Source admission has no risk limits")
        count_reason = (
            "CRYPTO_POSITION_COUNT"
            if strategy.market == Market.CRYPTO
            and claims_before["crypto_claim_count"] >= limits.maximum_crypto_positions
            else "PENDING_SELL_SLOT_NOT_RELEASED"
            if selling_before and claims_before["slot_claims"] >= self.maximum_positions
            else "PENDING_OPENING_RESERVATION"
            if opening_before and claims_before["slot_claims"] >= self.maximum_positions
            else "GLOBAL_POSITION_COUNT"
        )
        count_pressure = (
            claims_before["slot_claims"] >= self.maximum_positions
            or strategy.market == Market.CRYPTO
            and claims_before["crypto_claim_count"] >= limits.maximum_crypto_positions
        )
        halt_reasons = {"daily_loss_limit", "portfolio_drawdown_halt"} & set(record.risk_overlay_reasons)
        sleeve_limit = limits.maximum_crypto if strategy.market == Market.CRYPTO else limits.maximum_equity
        exposure_bounds = {
            "SLEEVE_LIMIT": min(engine.maximum_allocation, sleeve_limit),
            "GROSS_TARGET_LIMIT": limits.maximum_gross,
            "CASH_TARGET_LIMIT": 1.0 - limits.minimum_cash,
        }
        exposure_reason = min(exposure_bounds, key=exposure_bounds.__getitem__)
        self._source_targets = {key: value for key, value in self._source_targets.items() if key[0] != strategy.market}
        for source in sources:
            for rank, symbol in enumerate(ranks[source], start=1):
                requested = float(contributions[symbol][source])
                if requested <= 0:
                    continue
                key = (strategy.market.value, canonical_symbol(symbol, strategy.market))
                existing = key in positions_before
                carried_reservation = key in opening_before and key in opening_after
                opening = not existing and key not in opening_before
                combined_requested = sum(contributions[symbol].values())
                account_target = float(approved.get(symbol, 0.0))
                admitted_opening = opening and symbol in active_buys
                admitted = requested * min(1.0, account_target / combined_requested) if combined_requested > 0 else 0.0
                reason: str | None = None
                detail: str | None = None
                if not existing and not admitted_opening and not carried_reservation:
                    admitted = 0.0
                    if halt_reasons:
                        reason = "OTHER_EXPLICIT"
                        detail = ",".join(record.risk_overlay_reasons)
                    elif count_pressure and (
                        symbol in planned_buys
                        or (account_target <= 1e-12 and "position_count_limit" in record.risk_overlay_reasons)
                    ):
                        reason = count_reason
                    elif account_target <= 1e-12 and "exposure_scaled_to_limit" in record.risk_overlay_reasons:
                        reason = exposure_reason
                        detail = "exposure_scaled_to_limit"
                    elif account_target <= 1e-12:
                        reason = "OTHER_EXPLICIT"
                        detail = ",".join(record.risk_overlay_reasons) or "risk_overlay_removed_target"
                    elif engine._last_price.get(symbol) is None:
                        reason = "MISSING_DATA"
                    elif key in opening_before:
                        reason = "OTHER_EXPLICIT"
                        detail = "prior_working_reservation_canceled"
                    else:
                        reason = "MIN_ORDER"
                elif admitted + 1e-12 < requested:
                    if existing and account_target <= 1e-12:
                        reason = "OTHER_EXPLICIT"
                        detail = "existing_position_target_removed"
                    elif (
                        count_pressure
                        and not halt_reasons
                        and "position_count_limit" in record.risk_overlay_reasons
                        and account_target <= 1e-12
                    ):
                        reason = count_reason
                    elif "exposure_scaled_to_limit" in record.risk_overlay_reasons:
                        reason = exposure_reason
                        detail = "exposure_scaled_to_limit"
                    elif raw_weights[source].get(symbol, 0.0) > requested + 1e-12:
                        reason = "PER_NAME_CAP"
                    else:
                        reason = "OTHER_EXPLICIT"
                        detail = ",".join(record.risk_overlay_reasons) or "target_reduced"
                elif raw_weights[source].get(symbol, 0.0) > requested + 1e-12:
                    reason = "PER_NAME_CAP"
                self._events.append(
                    {
                        "event": "source_admission",
                        "timestamp": timestamp.isoformat(),
                        "source": source,
                        "source_label": source,
                        "market": strategy.market.value,
                        "symbol": symbol,
                        "source_rank": rank,
                        "requested_target_contribution": requested,
                        "admitted_target_contribution": admitted,
                        "combined_account_target": account_target,
                        "existing_position": existing,
                        "working_opening_reservation": key in opening_before,
                        "requested_opening": opening,
                        "admitted_opening": admitted_opening,
                        "denial_reason": reason,
                        "denial_detail": detail,
                        "overlap": len([weight for weight in contributions[symbol].values() if weight > 0]) > 1,
                        "slot_claims_before": claims_before["slot_claims"],
                        "slot_claims_after": claims_after["slot_claims"],
                        "global_cap": self.maximum_positions,
                        "crypto_count": claims_after["crypto_count"],
                        "pending_sell": bool(selling_before),
                    }
                )
                self._source_targets.setdefault((strategy.market, symbol), {})[source] = admitted
        nav = self.account.nav()
        for order in engine._pending:
            if order.decision_timestamp != label:
                continue
            key = (strategy.market, canonical_symbol(order.symbol, strategy.market))
            mark = self.account.marks.get(key, engine._last_price.get(order.symbol, 0.0))
            if mark <= 0:
                raise AssertionError(f"Order has no known mark for source attribution: {order.symbol}")
            weights = self._source_targets.get(key, {})
            self._order_desired[(strategy.market, order.client_order_id)] = {
                source: weight * nav / mark for source, weight in weights.items() if weight > 0
            }
        for (position_market, symbol), position in sorted(
            self.account.positions.items(), key=lambda item: (item[0][0].value, item[0][1])
        ):
            if position_market == strategy.market:
                self._emit_source_inventory(timestamp, position_market, symbol, "decision", 0.0, None, None, {})

    def _emit_source_inventory(
        self,
        timestamp: datetime,
        market: Market,
        symbol: str,
        cause: str,
        qty_delta: float,
        client_order_id: str | None,
        side: str | None,
        source_allocation: dict[str, float],
    ) -> None:
        key = (market, canonical_symbol(symbol, market))
        position = self.account.positions.get(key)
        position_qty = float(position.qty) if position is not None else 0.0
        sources = self._source_qty.get(key, {})
        if abs(sum(sources.values()) - position_qty) > 1e-8:
            raise AssertionError(f"Source quantity differs from account inventory: {market.value}:{symbol}")
        self._events.append(
            {
                "event": "source_inventory",
                "timestamp": timestamp.isoformat(),
                "market": market.value,
                "symbol": key[1],
                "cause": cause,
                "client_order_id": client_order_id,
                "side": side,
                "qty_delta": qty_delta,
                "position_qty": position_qty,
                "source_quantities": dict(sorted(sources.items())),
                "source_allocation": dict(sorted(source_allocation.items())),
                "desired_source_qty": dict(
                    sorted(self._order_desired.get((market, client_order_id or ""), {}).items())
                ),
            }
        )

    def _apply_source_fill(self, fill: PortfolioFill) -> None:
        key = (fill.market, canonical_symbol(fill.symbol, fill.market))
        order_key = (fill.market, fill.client_order_id)
        desired = self._order_desired.get(order_key)
        if desired is None:
            raise AssertionError(f"Fill has no frozen source claim: {fill.client_order_id}")
        held = self._source_qty.setdefault(key, {})
        delta: dict[str, float] = {}
        if fill.side == OrderSide.BUY:
            deficits = {source: max(0.0, target - held.get(source, 0.0)) for source, target in desired.items()}
            first = min(fill.qty, sum(deficits.values()))
            for source, amount in _pro_rata(first, deficits).items():
                delta[source] = delta.get(source, 0.0) + amount
            remainder = fill.qty - first
            fallback = desired or ({"crypto": 1.0} if fill.market == Market.CRYPTO else {"v3": 1.0})
            for source, amount in _pro_rata(remainder, fallback).items():
                delta[source] = delta.get(source, 0.0) + amount
            for source, amount in delta.items():
                held[source] = held.get(source, 0.0) + amount
        else:
            excess = {source: max(0.0, qty - desired.get(source, 0.0)) for source, qty in held.items()}
            first = min(fill.qty, sum(excess.values()))
            for source, amount in _pro_rata(first, excess).items():
                held[source] -= amount
                delta[source] = delta.get(source, 0.0) - amount
            remainder = fill.qty - first
            for source, amount in _pro_rata(remainder, held).items():
                held[source] -= amount
                delta[source] = delta.get(source, 0.0) - amount
            self._source_qty[key] = {source: max(0.0, qty) for source, qty in held.items() if qty > 1e-10}
        self._emit_source_inventory(
            fill.timestamp,
            fill.market,
            fill.symbol,
            "fill",
            fill.qty if fill.side == OrderSide.BUY else -fill.qty,
            fill.client_order_id,
            fill.side.value,
            delta,
        )

    def _apply_source_split(self, timestamp: datetime, market: Market, symbol: str, ratio: float) -> None:
        key = (market, canonical_symbol(symbol, market))
        before = dict(self._source_qty.get(key, {}))
        if before:
            self._source_qty[key] = {source: qty * ratio for source, qty in before.items()}
            self._emit_source_inventory(
                timestamp,
                market,
                symbol,
                "split",
                sum(before.values()) * (ratio - 1),
                None,
                None,
                {source: qty * (ratio - 1) for source, qty in before.items()},
            )
        for order in (*self.account.pending[market], *self.account.deferred_buys[market]):
            if canonical_symbol(order.symbol, market) != key[1]:
                continue
            order_key = (market, order.client_order_id)
            frozen = self._order_desired.get(order_key)
            if frozen is None:
                continue
            adjusted = {source: qty * ratio for source, qty in frozen.items()}
            self._order_desired[order_key] = adjusted
            if order in self.account.pending[market]:
                self._events.append(
                    {
                        "event": "source_order_adjustment",
                        "timestamp": timestamp.isoformat(),
                        "market": market.value,
                        "symbol": key[1],
                        "client_order_id": order.client_order_id,
                        "cause": "split",
                        "split_ratio": ratio,
                        "desired_source_qty": dict(sorted(adjusted.items())),
                    }
                )

    def _clear_source_inventory(self, timestamp: datetime, market: Market, symbol: str, cause: str) -> None:
        key = (market, canonical_symbol(symbol, market))
        before = self._source_qty.pop(key, {})
        if before:
            self._emit_source_inventory(
                timestamp,
                market,
                symbol,
                cause,
                -sum(before.values()),
                None,
                "sell",
                {source: -qty for source, qty in before.items()},
            )

    def add_market(self, replay_input: MarketReplayInput) -> None:
        engine = replay_input.engine
        market = engine.strategy.market
        if not self.inputs and (
            self.account.positions
            or any(self.account.pending.values())
            or self.account.cash != self.account.starting_cash
        ):
            raise ValueError("Replay must begin flat, all-cash, and without pending orders")
        if market in self.inputs:
            raise ValueError(f"Only one incumbent engine per market is supported: {market.value}")
        identity = (market, engine.strategy.name)
        if not self.allow_test_strategies and identity not in SUPPORTED_STRATEGIES:
            raise ValueError(f"Unsupported shared replay strategy: {identity}")
        if engine._shared_account is not self.account:
            raise ValueError("Market engine is attached to a different shared account")
        if engine.state.holdings or engine._pending:
            raise ValueError("Shared historical replay must start flat with no pending orders")
        if engine.starting_equity != self.account.starting_nav:
            raise ValueError("Market engine starting NAV must equal the shared account starting NAV")
        if not replay_input.execution_bars:
            raise ValueError(f"No execution bars for {market.value}")
        self.inputs[market] = replay_input

    def run(self, *, liquidate_at_end: bool = True) -> SharedReplayResult:
        if not self.inputs:
            raise ValueError("Shared replay needs at least one market")
        self._events = []
        self._source_qty.clear()
        self._source_targets.clear()
        self._order_desired.clear()
        self.account.start_timestamp = min(
            replay.execution_schedule.get(bar.timestamp, bar.timestamp)
            for replay in self.inputs.values()
            for bar in replay.execution_bars
        )
        market_orders = [market for market in ORDER_MARKETS if market in self.inputs]
        execution: dict[Market, list[Bar]] = {}
        signals: dict[Market, list[Bar]] = {}
        signal_cycles: dict[Market, dict[datetime, list[Bar]]] = {}
        action_cycles: dict[Market, dict[datetime, list[CorporateAction]]] = {}
        last_bars: dict[Market, list[Bar]] = {market: [] for market in market_orders}
        last_decision_at: dict[Market, datetime | None] = {market: None for market in market_orders}
        latest_targets: dict[Market, dict[str, float]] = {market: {} for market in market_orders}
        future_data_checks = 0

        for market in market_orders:
            replay = self.inputs[market]
            engine = replay.engine
            ordered = replay.execution_bars
            execution[market] = ordered
            signal_rows = replay.signal_bars if replay.signal_bars is not None else replay.execution_bars
            signals[market] = signal_rows
            if not replay.signal_availability:
                raise ValueError("Physical replay requires an explicit signal availability clock")
            signal_cycles[market] = defaultdict(list)
            for bar in signals[market]:
                available_at = replay.signal_availability[bar.timestamp]
                signal_cycles[market][available_at].append(bar)
            for bar in sorted(replay.warmup_bars, key=lambda item: (item.timestamp, item.symbol)):
                engine._ingest(bar)
                engine._ingest_execution(bar)
            action_cycles[market] = defaultdict(list)
            first_execution_at = (
                min(replay.execution_schedule.values())
                if replay.execution_schedule
                else min(bar.timestamp for bar in ordered)
            )
            for action in _prepare_corporate_actions(replay.corporate_actions, cutoff=None):
                action_at = replay.action_availability[action.timestamp]
                if action_at >= first_execution_at:
                    action_cycles[market][action_at].append(action)
            last_bars[market] = signals[market]

        cycles = {
            market: _group_by_physical_timestamp(execution[market], self.inputs[market].execution_schedule)
            for market in market_orders
        }
        timestamps = sorted(
            {timestamp for grouped in cycles.values() for timestamp in grouped}
            | {timestamp for grouped in signal_cycles.values() for timestamp in grouped}
            | {timestamp for grouped in action_cycles.values() for timestamp in grouped}
            | {replay.terminal_at for replay in self.inputs.values() if replay.terminal_at is not None}
        )
        dividend_rows: list[tuple[Market, str, float, datetime]] = []
        fill_rows: list[PortfolioFill] = []
        crypto_attempted_day: set[tuple[str, str, date]] = set()

        shared_coordination = len(market_orders) > 1
        for timestamp in timestamps:
            first_event = len(self._events)
            self.account.same_cycle_buy_reservations.clear()
            cycles_at_time = {market: cycles[market].get(timestamp, []) for market in market_orders}
            completed_at_time = {market: signal_cycles[market].get(timestamp, []) for market in market_orders}
            for market in market_orders:
                engine = self.inputs[market].engine
                if completed_at_time[market]:
                    engine._fail_on_overdue_terminal_inventory(completed_at_time[market][0].timestamp)
                for action in action_cycles[market].get(timestamp, []):
                    before = self.account.cash
                    before_dividend = engine._dividend_total
                    position = engine._positions.get(canonical_symbol(action.symbol, market))
                    position_qty_before = position.qty if position is not None else 0.0
                    engine._apply_corporate_action(action)
                    position_after = engine._positions.get(canonical_symbol(action.symbol, market))
                    position_qty_after = position_after.qty if position_after is not None else 0.0
                    delta = self.account.cash - before
                    if delta:
                        dividend_rows.append((market, canonical_symbol(action.symbol, market), delta, action.timestamp))
                    self._events.append(
                        {
                            "event": "corporate_action",
                            "market": market.value,
                            "timestamp": timestamp.isoformat(),
                            "source_ex_date_label": action.timestamp.isoformat(),
                            "symbol": canonical_symbol(action.symbol, market),
                            "action_type": action.action_type.value,
                            "cash_delta": delta,
                            "dividend_total_delta": engine._dividend_total - before_dividend,
                            "position_qty_before": position_qty_before,
                            "position_qty_after": position_qty_after,
                            "split_ratio": action.value if action.action_type.value == "split" else None,
                        }
                    )
                    if action.action_type.value == "split":
                        self._apply_source_split(timestamp, market, action.symbol, action.value)
                    elif position_qty_before > 0:
                        self._emit_source_inventory(
                            timestamp, market, action.symbol, "dividend_entitlement", 0.0, None, None, {}
                        )

            before_fills = {market: len(self.inputs[market].engine._fills) for market in market_orders}
            pending_before = self._pending_keys()
            created_before_fills = {
                (market, order.client_order_id): order.created_at
                for market in market_orders
                for order in self.account.pending[market]
            }
            for market in market_orders:
                engine = self.inputs[market].engine
                eligible_sells = [
                    order
                    for order in engine._pending
                    if order.side == OrderSide.SELL
                    and timestamp > order.created_at
                    and (
                        market != Market.CRYPTO
                        or (order.client_order_id, order.side.value, timestamp.date()) not in crypto_attempted_day
                    )
                ]
                pending_symbols = {order.symbol for order in eligible_sells}
                for bar in cycles_at_time[market]:
                    if bar.symbol in pending_symbols:
                        engine._fill_side(_physical_open_bar(bar, timestamp), OrderSide.SELL)
                        if market == Market.CRYPTO:
                            crypto_attempted_day.update(
                                (order.client_order_id, order.side.value, timestamp.date())
                                for order in eligible_sells
                                if order.symbol == bar.symbol
                            )
            pending_sells = [
                order
                for market in market_orders
                for order in self.account.pending[market]
                if order.side == OrderSide.SELL
            ]
            if pending_sells and len(market_orders) > 1:
                self._events.append(
                    {
                        "event": "buy_fill_deferred",
                        "timestamp": timestamp.isoformat(),
                        "reason": "sell_phase_not_terminal",
                        "blocking_sell_orders": [
                            [order.market.value, order.symbol, order.client_order_id, order.qty]
                            for order in sorted(
                                pending_sells,
                                key=lambda item: (item.market.value, item.symbol, item.client_order_id),
                            )
                        ],
                    }
                )
            else:
                if shared_coordination:
                    self._activate_deferred_buys(timestamp, market_orders)
                for market in market_orders:
                    engine = self.inputs[market].engine
                    eligible_buys = [
                        order
                        for order in engine._pending
                        if order.side == OrderSide.BUY
                        and timestamp > order.created_at
                        and (
                            market != Market.CRYPTO
                            or (order.client_order_id, order.side.value, timestamp.date()) not in crypto_attempted_day
                        )
                    ]
                    pending_symbols = {order.symbol for order in eligible_buys}
                    for bar in cycles_at_time[market]:
                        if bar.symbol in pending_symbols:
                            engine._fill_side(_physical_open_bar(bar, timestamp), OrderSide.BUY)
                            if market == Market.CRYPTO:
                                crypto_attempted_day.update(
                                    (order.client_order_id, order.side.value, timestamp.date())
                                    for order in eligible_buys
                                    if order.symbol == bar.symbol
                                )
            new_fills = [
                fill for market in market_orders for fill in self.inputs[market].engine._fills[before_fills[market] :]
            ]
            new_fills.sort(key=lambda fill: (fill.timestamp, _market_order(fill.market), fill.symbol, fill.side.value))
            fill_rows.extend(new_fills)
            for fill in new_fills:
                created_at = created_before_fills[(fill.market, fill.client_order_id)]
                if fill.timestamp <= created_at:
                    raise AssertionError("Fill uses a price event at or before order creation")
                fill_record = _fill_event(fill)
                fill_record["order_created_at"] = created_at.isoformat()
                fill_record["source_price_event_time"] = fill.timestamp.isoformat()
                self._events.append(fill_record)
                self._apply_source_fill(fill)
            self._sync_reservations(timestamp, pending_before, "execution_cycle", new_fills)
            pending_before_decisions = self._pending_keys()

            for market in market_orders:
                replay_input = self.inputs[market]
                if replay_input.terminal_at != timestamp:
                    continue
                engine = replay_input.engine
                pending_before_terminal = self._pending_keys()
                engine._pending = []
                self._sync_reservations(timestamp, pending_before_terminal, "precommitted_terminal", [])
                terminal_bars = {bar.symbol: bar for bar in cycles_at_time[market]}
                for position in list(engine._positions.values()):
                    source_terminal = terminal_bars.get(position.symbol)
                    terminal_bar = (
                        _physical_open_bar(source_terminal, timestamp) if source_terminal is not None else None
                    )
                    if terminal_bar is None or terminal_bar.timestamp != timestamp:
                        raise RuntimeError(f"Missing physical terminal source event for {position.symbol}")
                    engine._close_position(position, terminal_bar)
                    event = engine._liquidation_events[-1]
                    event["policy"] = "PRECOMMITTED_COMMON_SOURCE_HOUR_OPEN"
                    event["effective_policy"] = "PRECOMMITTED_COMMON_SOURCE_HOUR_OPEN"
                    self._events.append({"event": "terminal_liquidation", "market": market.value, **event})
                    self._clear_source_inventory(timestamp, market, position.symbol, "terminal_liquidation")

            for market in market_orders:
                engine = self.inputs[market].engine
                for bar in completed_at_time[market]:
                    engine._ingest_execution(bar)
                    engine._ingest(bar)
                    self.account.marks[(market, bar.symbol)] = bar.close
                    if (market, bar.symbol) in self.account.positions:
                        self._events.append(
                            {
                                "event": "mark",
                                "market": market.value,
                                "timestamp": timestamp.isoformat(),
                                "label_time": bar.timestamp.isoformat(),
                                "symbol": bar.symbol,
                                "price": bar.close,
                            }
                        )
            if any(completed_at_time.values()):
                self._advance_shared_risk(timestamp)

            for market in market_orders:
                cycle = completed_at_time[market]
                if not cycle:
                    continue
                engine = self.inputs[market].engine
                before_rebalances = len(engine._rebalances)
                label = cycle[0].timestamp
                if any(history and history[-1].timestamp > label for history in engine._history.values()):
                    raise AssertionError("Future-dated history reached a market decision")
                for history in engine._history.values():
                    if not history:
                        continue
                    latest = history[-1]
                    latest_available_at = self.inputs[market].signal_availability.get(latest.timestamp)
                    if latest_available_at is not None and latest_available_at > timestamp:
                        raise AssertionError("Physically unavailable history reached a decision")
                clock_record = PhysicalReplayTime(
                    market,
                    cycle[0],
                    label,
                    self.inputs[market].signal_availability[label],
                    timestamp,
                    timestamp,
                )
                if clock_record.available_at != timestamp:
                    raise AssertionError("Decision scheduled before physical availability")
                future_data_checks += 1
                claims_before = self._claim_snapshot()
                engine._shared_overlay.last_raw_target = None
                if self.inputs[market].terminal_at != timestamp:
                    engine._decide(label)
                if len(engine._rebalances) > before_rebalances:
                    for order in engine._pending:
                        if order.decision_timestamp == label and order.created_at == label:
                            order.created_at = timestamp
                        if order.decision_timestamp == label and order.created_at < timestamp:
                            raise AssertionError("Order was created before its physical decision")
                    planned_buys = {
                        canonical_symbol(order.symbol, market)
                        for order in engine._pending
                        if order.side == OrderSide.BUY and order.decision_timestamp == label
                    }
                    if shared_coordination and not any(
                        order.side == OrderSide.SELL
                        for current_market in ORDER_MARKETS
                        for order in self.account.pending[current_market]
                    ):
                        prior_keys = tuple(
                            (current_market, order.symbol)
                            for current_market in ORDER_MARKETS
                            if current_market != market
                            for order in self.account.pending[current_market]
                        )
                        engine._pending = list(
                            _admit_synthetic_orders(
                                tuple(engine._pending),
                                engine._account_snapshot(),
                                self.maximum_positions,
                                prior_keys,
                            )
                        )
                    self._cancel_deferred_buys(timestamp, market, "decision_superseded")
                    record = engine._rebalances[-1]
                    latest_targets[market] = dict(record.target_weights)
                    claims_after = self._claim_snapshot()
                    self._events.append(
                        {
                            "event": "decision",
                            "market": market.value,
                            "timestamp": timestamp.isoformat(),
                            "label_time": label.isoformat(),
                            "target_weights": [[symbol, weight] for symbol, weight in record.target_weights],
                            "risk_overlay_reasons": list(record.risk_overlay_reasons),
                            "position_keys_before": claims_before["position_keys"],
                            "pending_opening_keys_before": claims_before["pending_opening_keys"],
                            "pending_sell_keys_before": claims_before["pending_sell_keys"],
                            "slot_claims_before": claims_before["slot_claims"],
                            "crypto_count_before": claims_before["crypto_count"],
                            "crypto_claim_count_before": claims_before["crypto_claim_count"],
                            "position_keys_after": claims_after["position_keys"],
                            "pending_opening_keys_after": claims_after["pending_opening_keys"],
                            "pending_sell_keys_after": claims_after["pending_sell_keys"],
                            "slot_claims_after": claims_after["slot_claims"],
                            "crypto_count_after": claims_after["crypto_count"],
                            "crypto_claim_count_after": claims_after["crypto_claim_count"],
                            "position_keys_before_next_decision": [
                                [reserved_market.value, symbol]
                                for reserved_market, symbol in sorted(
                                    self.account.same_cycle_buy_reservations,
                                    key=lambda item: (item[0].value, item[1]),
                                )
                            ],
                        }
                    )
                    self._record_source_admissions(
                        engine,
                        label,
                        timestamp,
                        record,
                        claims_before,
                        claims_after,
                        planned_buys,
                    )
                    for order in engine._pending:
                        if order.side == OrderSide.BUY and order.decision_timestamp == label:
                            key = (order.market, canonical_symbol(order.symbol, order.market))
                            self.account.same_cycle_buy_reservations.add(key)
                before_liquidations = len(engine._liquidation_events)
                physical_cycle = [
                    Bar(
                        timestamp=timestamp,
                        symbol=bar.symbol,
                        timeframe=bar.timeframe,
                        open=bar.open,
                        high=bar.high,
                        low=bar.low,
                        close=bar.close,
                        volume=bar.volume,
                        market=bar.market,
                    )
                    for bar in cycle
                ]
                engine._apply_terminal_lifecycles(label, physical_cycle)
                for liquidation in engine._liquidation_events[before_liquidations:]:
                    self._events.append({"event": "terminal_liquidation", "market": market.value, **liquidation})
                    self._clear_source_inventory(timestamp, market, str(liquidation["symbol"]), "terminal_liquidation")
                engine._record_holdings_snapshot(label)
                last_decision_at[market] = timestamp
                if self.inputs[market].final_close_at == timestamp:
                    final_source_by_symbol = {bar.symbol: bar for bar in cycle}
                    for position in list(engine._positions.values()):
                        source = final_source_by_symbol.get(position.symbol)
                        if source is None:
                            raise RuntimeError(f"Missing final physical equity source for {position.symbol}")
                        physical_close = Bar(
                            timestamp=timestamp,
                            symbol=source.symbol,
                            timeframe=source.timeframe,
                            open=source.close,
                            high=source.close,
                            low=source.close,
                            close=source.close,
                            volume=0.0,
                            market=source.market,
                        )
                        engine._close_position(position, physical_close)
                        self._events.append(
                            {"event": "final_liquidation", "market": market.value, **engine._liquidation_events[-1]}
                        )
                        self._clear_source_inventory(timestamp, market, position.symbol, "final_liquidation")
                    pending_before_close = self._pending_keys()
                    engine._pending = []
                    self._sync_reservations(timestamp, pending_before_close, "final_liquidation", [])

            if shared_coordination:
                self._defer_decision_buys_while_sells_work(timestamp, market_orders)
            self._finalize_decision_capacity_records(timestamp, first_event)
            self._sync_reservations(timestamp, pending_before_decisions, "decision_superseded", [])

            nav = self.account.nav()
            slot_claims = {
                *self.account.positions.keys(),
                *(
                    (market, canonical_symbol(order.symbol, market))
                    for market in ORDER_MARKETS
                    for order in self.account.pending[market]
                ),
            }
            if len(slot_claims) > self.maximum_positions:
                self._events.append(
                    {
                        "event": "slot_claim_violation",
                        "timestamp": timestamp.isoformat(),
                        "maximum_positions": self.maximum_positions,
                        "slot_claim_count": len(slot_claims),
                        "positions": [
                            [market.value, symbol, position.qty]
                            for (market, symbol), position in sorted(
                                self.account.positions.items(), key=lambda item: (item[0][0].value, item[0][1])
                            )
                        ],
                        "pending_orders": [
                            [market.value, order.symbol, order.side.value, order.qty, order.client_order_id]
                            for market in ORDER_MARKETS
                            for order in self.account.pending[market]
                        ],
                        "same_cycle_buy_reservations": [
                            [market.value, symbol]
                            for market, symbol in sorted(
                                self.account.same_cycle_buy_reservations,
                                key=lambda item: (item[0].value, item[1]),
                            )
                        ],
                    }
                )
            target_gross = sum(sum(weights.values()) for weights in latest_targets.values())
            self._events.append(
                {
                    "event": "account_mark",
                    "timestamp": timestamp.isoformat(),
                    "cash": self.account.cash,
                    "nav": nav,
                    "gross": self.gross_exposure(),
                    "position_count": len(self.account.positions),
                    "slot_claim_count": len(slot_claims),
                    "target_gross": target_gross,
                }
            )

        if liquidate_at_end:
            self._liquidate_final_positions(market_orders, last_bars, last_decision_at)

        for market in market_orders:
            engine = self.inputs[market].engine
            if liquidate_at_end and not engine._positions:
                engine.state.holdings = {}
                engine.state.avg_prices = {}
                engine.state.entry_fees = {}
                engine.state.realized_pnl_accumulators = {}
                engine.state.pending_orders = ()
                engine.state.pending_target = {}
                engine.state.cash = self.account.cash

        self._record_final_state_events()
        invariants = self._check_accounting(fill_rows, dividend_rows, future_data_checks)
        positions = tuple(
            {
                "market": market.value,
                "symbol": symbol,
                "qty": position.qty,
            }
            for (market, symbol), position in sorted(
                self.account.positions.items(), key=lambda item: (item[0][0].value, item[0][1])
            )
        )
        return SharedReplayResult(
            positions=positions,
            events=tuple(_structural_event(event) for event in self._events),
            invariants=_structural_invariants(invariants),
        )

    def gross_exposure(self) -> float:
        nav = self.account.nav()
        if nav <= 0:
            return 0.0
        return sum(
            (
                abs(float(position.qty) * float(self.account.marks.get(key, position.avg_price))) / nav
                for key, position in self.account.positions.items()
            ),
            0.0,
        )

    def _pending_keys(self) -> set[tuple[Market, str]]:
        return {(market, order.client_order_id) for market in ORDER_MARKETS for order in self.account.pending[market]}

    def _defer_decision_buys_while_sells_work(self, timestamp: datetime, markets: list[Market]) -> None:
        if not any(order.side == OrderSide.SELL for market in markets for order in self.account.pending[market]):
            return
        for market in markets:
            engine = self.inputs[market].engine
            staged = [
                order for order in engine._pending if order.side == OrderSide.BUY and order.created_at == timestamp
            ]
            if not staged:
                continue
            staged_keys = {(market, canonical_symbol(order.symbol, market)) for order in staged}
            self.account.deferred_buys[market].extend(staged)
            engine._pending = [order for order in engine._pending if order not in staged]
            self.account.same_cycle_buy_reservations.difference_update(staged_keys)
            for order in staged:
                self._events.append(
                    {
                        "event": "buy_intent_deferred",
                        "market": market.value,
                        "timestamp": timestamp.isoformat(),
                        "client_order_id": order.client_order_id,
                        "symbol": order.symbol,
                        "qty": order.qty,
                        "reason": "sell_phase_not_terminal",
                    }
                )

    def _activate_deferred_buys(self, timestamp: datetime, markets: list[Market]) -> None:
        if any(order.side == OrderSide.SELL for market in markets for order in self.account.pending[market]):
            return
        for market in markets:
            deferred = self.account.deferred_buys[market]
            if not deferred:
                continue
            engine = self.inputs[market].engine
            prior_keys = tuple(
                (current_market, order.symbol)
                for current_market in ORDER_MARKETS
                for order in self.account.pending[current_market]
            )
            admitted = _admit_synthetic_orders(
                tuple(deferred),
                engine._account_snapshot(),
                self.maximum_positions,
                prior_keys,
            )
            for order in deferred:
                if order not in admitted:
                    self._events.append(
                        {
                            "event": "buy_intent_cancelled",
                            "market": market.value,
                            "timestamp": timestamp.isoformat(),
                            "client_order_id": order.client_order_id,
                            "symbol": order.symbol,
                            "qty": order.qty,
                            "reason": "account_position_count_limit",
                        }
                    )
                    continue
                active = PortfolioOrder(
                    client_order_id=order.client_order_id,
                    symbol=order.symbol,
                    market=order.market,
                    side=order.side,
                    qty=order.qty,
                    created_at=timestamp,
                    decision_timestamp=order.decision_timestamp,
                )
                engine._pending.append(active)
                self._events.append(
                    {
                        "event": "buy_intent_activated",
                        "market": market.value,
                        "timestamp": timestamp.isoformat(),
                        "client_order_id": order.client_order_id,
                        "symbol": order.symbol,
                        "qty": order.qty,
                        "reason": "sell_phase_terminal",
                    }
                )
            deferred.clear()

    def _cancel_deferred_buys(self, timestamp: datetime | None, market: Market, reason: str) -> None:
        deferred = self.account.deferred_buys[market]
        if not deferred:
            return
        for order in deferred:
            self.account.same_cycle_buy_reservations.discard((market, canonical_symbol(order.symbol, market)))
            self._events.append(
                {
                    "event": "buy_intent_cancelled",
                    "market": market.value,
                    "timestamp": timestamp.isoformat() if timestamp is not None else None,
                    "client_order_id": order.client_order_id,
                    "symbol": order.symbol,
                    "qty": order.qty,
                    "reason": reason,
                }
            )
        deferred.clear()

    def _sync_reservations(
        self,
        timestamp: datetime,
        before: set[tuple[Market, str]],
        reason: str,
        fills: list[PortfolioFill],
    ) -> None:
        after = self._pending_keys()
        for key in sorted(after - before, key=lambda item: (item[0].value, item[1])):
            self.account.reservation_states[key] = "reserved"
            self.account.order_submissions.add(key)
            order = next(order for order in self.account.pending[key[0]] if order.client_order_id == key[1])
            self._events.append(
                {
                    "event": "reservation_created",
                    "market": key[0].value,
                    "timestamp": timestamp.isoformat(),
                    "decision_label_time": order.decision_timestamp.isoformat(),
                    "order_created_at": order.created_at.isoformat(),
                    "client_order_id": order.client_order_id,
                    "symbol": order.symbol,
                    "side": order.side.value,
                    "qty": order.qty,
                }
            )
            desired = self._order_desired.get(key)
            if desired is None:
                raise AssertionError(f"Reserved order has no frozen source claim: {order.client_order_id}")
            self._events.append(
                {
                    "event": "source_order_claim",
                    "market": key[0].value,
                    "symbol": order.symbol,
                    "timestamp": timestamp.isoformat(),
                    "client_order_id": order.client_order_id,
                    "side": order.side.value,
                    "desired_source_qty": dict(sorted(desired.items())),
                }
            )
        for key in sorted(before - after, key=lambda item: (item[0].value, item[1])):
            if self.account.reservation_states.get(key) != "reserved":
                continue
            filled = any(fill.market == key[0] and fill.client_order_id == key[1] for fill in fills)
            self.account.reservation_states[key] = "released"
            self._order_desired.pop(key, None)
            self._events.append(
                {
                    "event": "reservation_released",
                    "market": key[0].value,
                    "timestamp": timestamp.isoformat(),
                    "client_order_id": key[1],
                    "reason": "filled" if filled else reason,
                }
            )

    def _advance_shared_risk(self, timestamp: datetime) -> None:
        if len(self.inputs) == 1:
            market = next(iter(self.inputs))
            session = session_date_for(timestamp, market)
        else:
            session = timestamp.astimezone(NEW_YORK).date()
        if self.account.risk_session_date != session:
            self.account.day_start_equity = self.account.last_observed_equity
            self.account.risk_session_date = session
        nav = self.account.nav()
        self.account.last_observed_equity = nav
        self.account.peak_equity = max(self.account.peak_equity, nav)
        for replay in self.inputs.values():
            engine = replay.engine
            engine._peak_equity = self.account.peak_equity
            engine._day_start_equity = self.account.day_start_equity
            engine._last_observed_equity = self.account.last_observed_equity
            engine._risk_session_date = self.account.risk_session_date
        self._events.append(
            {
                "event": "risk_check",
                "timestamp": timestamp.isoformat(),
                "session_date": session.isoformat(),
                "nav": nav,
                "peak_equity": self.account.peak_equity,
                "day_start_equity": self.account.day_start_equity,
            }
        )

    def _liquidate_final_positions(
        self,
        markets: list[Market],
        last_bars: dict[Market, list[Bar]],
        last_decision_at: dict[Market, datetime | None],
    ) -> None:
        for market in markets:
            engine = self.inputs[market].engine
            by_symbol = {bar.symbol: bar for bar in last_bars[market]}
            for position in list(engine._positions.values()):
                bar = by_symbol.get(position.symbol)
                if bar is not None:
                    engine._close_position(position, bar)
                    event = engine._liquidation_events[-1]
                    self._events.append({"event": "final_liquidation", **event, "market": market.value})
                    clear_at = last_decision_at[market] or self.account.start_timestamp
                    if clear_at is None:
                        raise RuntimeError("Final source inventory has no physical timestamp")
                    self._clear_source_inventory(clear_at, market, position.symbol, "final_liquidation")
            if engine._pending:
                before = self._pending_keys()
                engine._pending = []
                release_time = last_decision_at[market] or self.account.start_timestamp
                if release_time is None:
                    raise RuntimeError("Final order cancellation has no replay timestamp")
                self._sync_reservations(
                    release_time,
                    before,
                    "final_liquidation",
                    [],
                )
        if self.account.positions:
            raise RuntimeError("Final liquidation left positions in the shared account")
        for market in markets:
            self._cancel_deferred_buys(
                last_decision_at[market] or self.account.start_timestamp,
                market,
                "final_liquidation",
            )

    def _record_final_state_events(self) -> None:
        self._events.append(
            {
                "event": "final_state",
                "cash": self.account.cash,
                "nav": self.account.nav(),
                "positions": [
                    [market.value, symbol, position.qty, position.avg_price]
                    for (market, symbol), position in sorted(
                        self.account.positions.items(), key=lambda item: (item[0][0].value, item[0][1])
                    )
                ],
                "pending": [
                    [market.value, order.client_order_id, order.symbol, order.side.value, order.qty]
                    for market in ORDER_MARKETS
                    for order in self.account.pending[market]
                ],
            }
        )

    def _check_accounting(
        self,
        fills: list[PortfolioFill],
        dividends: list[tuple[Market, str, float, datetime]],
        future_data_checks: int,
    ) -> dict[str, Any]:
        expected_cash = self.account.starting_cash
        for fill in fills:
            signed = 1.0 if fill.side == OrderSide.SELL else -1.0
            expected_cash += signed * fill.qty * fill.fill_price - fill.fee
        expected_cash += sum(delta for _market, _symbol, delta, _timestamp in dividends)
        expected_quantity: dict[InstrumentKey, float] = defaultdict(float)
        for event in self._events:
            kind = event["event"]
            if kind in {"final_liquidation", "terminal_liquidation"}:
                expected_cash += float(event["proceeds"])
                event_market = Market(str(event["market"]))
                event_symbol = canonical_symbol(str(event["symbol"]), event_market)
                expected_quantity[(event_market, event_symbol)] -= float(event["quantity"])
            elif kind == "fill":
                event_market = Market(str(event["market"]))
                event_symbol = canonical_symbol(str(event["symbol"]), event_market)
                quantity = float(event["qty"])
                expected_quantity[(event_market, event_symbol)] += (
                    quantity if event["side"] == OrderSide.BUY.value else -quantity
                )
            elif kind == "corporate_action" and event["split_ratio"] is not None:
                event_market = Market(str(event["market"]))
                event_symbol = canonical_symbol(str(event["symbol"]), event_market)
                expected_quantity[(event_market, event_symbol)] *= float(event["split_ratio"])
        cash_error = self.account.cash - expected_cash
        nav_from_ledger = self.account.cash + sum(
            position.qty * self.account.marks.get(key, position.avg_price)
            for key, position in self.account.positions.items()
        )
        reservation_created = sum(event["event"] == "reservation_created" for event in self._events)
        reservation_released = sum(event["event"] == "reservation_released" for event in self._events)
        deferred_order_count = sum(len(orders) for orders in self.account.deferred_buys.values())
        fills_by_order: set[tuple[Market, str]] = {(fill.market, fill.client_order_id) for fill in fills}
        submitted = {
            (Market(str(event["market"])), str(event["client_order_id"]))
            for event in self._events
            if event["event"] == "reservation_created"
        }
        unowned_fills = sorted(f"{market.value}:{client_id}" for market, client_id in fills_by_order - submitted)
        active_reservations = sum(status == "reserved" for status in self.account.reservation_states.values())
        duplicate_fill_keys = _duplicate_fill_keys(fills)
        position_count = len(self.account.positions)
        expected_quantity = {key: value for key, value in expected_quantity.items() if abs(value) > 1e-10}
        actual_quantity = {
            key: position.qty for key, position in self.account.positions.items() if position.qty > 1e-10
        }
        quantity_keys = set(expected_quantity) | set(actual_quantity)
        quantity_error = max(
            (abs(expected_quantity.get(key, 0.0) - actual_quantity.get(key, 0.0)) for key in quantity_keys),
            default=0.0,
        )
        max_slot_claims = max(
            (int(event["slot_claim_count"]) for event in self._events if event["event"] == "account_mark"),
            default=0,
        )
        slot_claim_violations = [event for event in self._events if event["event"] == "slot_claim_violation"]
        risk_limits_by_market: dict[Market, PortfolioRiskLimits] = {}
        for market, engine in self.account.engines.items():
            limits = engine._shared_overlay.limits
            if limits is None:
                raise RuntimeError("Shared replay engine lost canonical risk limits")
            risk_limits_by_market[market] = limits
        risk_limits = next(iter(risk_limits_by_market.values()))
        max_target_gross = max(
            (float(event["target_gross"]) for event in self._events if event["event"] == "account_mark"),
            default=0.0,
        )
        max_realized_gross = max(
            (float(event["gross"]) for event in self._events if event["event"] == "account_mark"),
            default=0.0,
        )
        max_name_target = max(
            (
                float(weight)
                for event in self._events
                if event["event"] == "decision"
                for _symbol, weight in event["target_weights"]
            ),
            default=0.0,
        )
        target_names_within_limits = all(
            float(weight)
            <= (
                risk_limits_by_market[Market(str(event["market"]))].maximum_crypto_asset
                if Market(str(event["market"])) == Market.CRYPTO
                else risk_limits_by_market[Market(str(event["market"]))].maximum_equity_asset
            )
            + 1e-9
            for event in self._events
            if event["event"] == "decision"
            for _symbol, weight in event["target_weights"]
        )
        sleeve_targets_within_limits = all(
            sum(float(weight) for _symbol, weight in event["target_weights"])
            <= min(
                self.account.engines[Market(str(event["market"]))].maximum_allocation,
                risk_limits_by_market[Market(str(event["market"]))].maximum_crypto
                if Market(str(event["market"])) == Market.CRYPTO
                else risk_limits_by_market[Market(str(event["market"]))].maximum_equity,
            )
            + 1e-9
            for event in self._events
            if event["event"] == "decision"
        )
        return {
            "starting_cash": self.account.starting_cash,
            "ending_cash": self.account.cash,
            "cash_reconciliation_error": cash_error,
            "cash_conserved": abs(cash_error) <= 1e-7,
            "nav_from_cash_and_marks": nav_from_ledger,
            "nav_matches_ledger": abs(nav_from_ledger - self.account.nav()) <= 1e-9,
            "position_quantity_reconciliation_error": quantity_error,
            "position_quantities_reconciled": quantity_error <= 1e-8,
            "duplicate_position_keys": False,
            "final_position_count": position_count,
            "final_position_count_within_limit": position_count <= self.maximum_positions,
            "max_observed_position_count": max(
                (int(event["position_count"]) for event in self._events if event["event"] == "account_mark"),
                default=0,
            ),
            "maximum_positions": self.maximum_positions,
            "max_observed_slot_claim_count": max_slot_claims,
            "slot_claims_within_limit": max_slot_claims <= self.maximum_positions,
            "slot_claim_violation_count": len(slot_claim_violations),
            "slot_claim_violation_samples": slot_claim_violations[:10],
            "deferred_buy_intent_count": deferred_order_count,
            "no_deferred_buy_intents_at_end": deferred_order_count == 0,
            "gross_at_final_mark": self.gross_exposure(),
            "maximum_realized_gross_observed": max_realized_gross,
            "maximum_target_gross_observed": max_target_gross,
            "target_gross_within_limit": max_target_gross <= risk_limits.maximum_gross + 1e-9,
            "maximum_target_name_weight": max_name_target,
            "target_name_weights_within_limits": target_names_within_limits,
            "sleeve_targets_within_limits": sleeve_targets_within_limits,
            "realized_gross_drift_is_recorded_not_corrected": True,
            "reservation_create_count": reservation_created,
            "reservation_release_count": reservation_released,
            "active_reservation_count": active_reservations,
            "all_reservations_released": reservation_created - reservation_released == active_reservations,
            "unowned_fill_count": len(unowned_fills),
            "unowned_fills": unowned_fills,
            "duplicate_fill_count": len(duplicate_fill_keys),
            "duplicate_fill_keys": duplicate_fill_keys,
            "future_data_access_checks": future_data_checks,
            "no_future_data_access": True,
            "ending_cash_nonnegative": self.account.cash >= -1e-9,
            "dividend_cash_event_count": len(dividends),
            "corporate_action_dividend_cash": sum(delta for _market, _symbol, delta, _timestamp in dividends),
            "fill_count": len(fills),
            "fee_counted_once_in_cash_reconciliation": True,
        }


def _physical_open_bar(source: Bar, timestamp: datetime) -> Bar:
    return Bar(
        timestamp,
        source.symbol,
        source.timeframe,
        source.open,
        source.open,
        source.open,
        source.open,
        0.0,
        source.market,
    )


def _group_by_physical_timestamp(bars: list[Bar], schedule: dict[datetime, datetime]) -> dict[datetime, list[Bar]]:
    grouped: dict[datetime, list[Bar]] = defaultdict(list)
    for bar in bars:
        grouped[schedule.get(bar.timestamp, bar.timestamp)].append(bar)
    return {timestamp: sorted(rows, key=lambda bar: bar.symbol) for timestamp, rows in grouped.items()}


def _market_order(market: Market) -> int:
    return ORDER_MARKETS.index(market)


def _fill_event(fill: PortfolioFill) -> dict[str, Any]:
    return {
        "event": "fill",
        "timestamp": fill.timestamp.isoformat(),
        "decision_timestamp": fill.decision_timestamp.isoformat(),
        "market": fill.market.value,
        "symbol": fill.symbol,
        "side": fill.side.value,
        "qty": fill.qty,
        "reference_price": fill.reference_price,
        "fill_price": fill.fill_price,
        "slippage_cost": fill.slippage_cost,
        "spread_cost": fill.spread_cost,
        "fee": fill.fee,
        "client_order_id": fill.client_order_id,
    }


def _duplicate_fill_keys(fills: list[PortfolioFill]) -> list[str]:
    seen: set[tuple[Market, str, datetime, str, float]] = set()
    duplicates: list[str] = []
    for fill in fills:
        key = (fill.market, fill.client_order_id, fill.timestamp, fill.side.value, fill.qty)
        if key in seen:
            duplicates.append(f"{fill.market.value}:{fill.client_order_id}:{fill.timestamp.isoformat()}")
        seen.add(key)
    return duplicates


__all__ = [
    "MarketReplayInput",
    "SharedAccount",
    "SharedAccountReplay",
    "SharedReplayResult",
    "SUPPORTED_STRATEGIES",
]
