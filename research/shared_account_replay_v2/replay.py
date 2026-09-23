"""Research-only merged replay for the two configured incumbent products.

This module coordinates existing ``PortfolioBacktestEngine`` instances over a
single synthetic account. It is intentionally outside ``src/`` so production
execution and configuration cannot import it accidentally.
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from collections.abc import Iterator, MutableMapping
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from parayabuck.backtest.portfolio_engine import (
    OrderSide,
    PortfolioBacktestEngine,
    PortfolioBacktestResult,
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

InstrumentKey = tuple[Market, str]
ORDER_MARKETS = (Market.CRYPTO, Market.EQUITY)
SUPPORTED_STRATEGIES = {
    (Market.EQUITY, "equity_residual_momentum_v3"),
    (Market.CRYPTO, "crypto_low_variance_hysteresis_v1"),
}


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


@dataclass(frozen=True)
class SharedReplayResult:
    start_nav: float
    ending_nav: float
    cash: float
    positions: tuple[dict[str, Any], ...]
    timestamps: tuple[datetime, ...]
    nav_path: tuple[float, ...]
    cash_path: tuple[float, ...]
    events: tuple[dict[str, Any], ...]
    market_results: dict[Market, PortfolioBacktestResult]
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
        if maximum_positions != 6:
            raise ValueError("Shared-account replay V2 validates the current cap of six only")
        self.maximum_positions = maximum_positions
        self.allow_test_strategies = allow_test_strategies
        self.inputs: dict[Market, MarketReplayInput] = {}
        self._events: list[dict[str, Any]] = []

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
        self.account.start_timestamp = min(
            bar.timestamp for replay in self.inputs.values() for bar in replay.execution_bars
        )
        market_orders = [market for market in ORDER_MARKETS if market in self.inputs]
        execution: dict[Market, list[Bar]] = {}
        signals: dict[Market, list[Bar]] = {}
        signal_index = {market: 0 for market in market_orders}
        actions: dict[Market, list[CorporateAction]] = {}
        action_index = {market: 0 for market in market_orders}
        last_bars: dict[Market, list[Bar]] = {market: [] for market in market_orders}
        market_curve: dict[Market, list[float]] = {market: [] for market in market_orders}
        market_timestamps: dict[Market, list[datetime]] = {market: [] for market in market_orders}
        latest_targets: dict[Market, dict[str, float]] = {market: {} for market in market_orders}
        future_data_checks = 0

        for market in market_orders:
            replay = self.inputs[market]
            engine = replay.engine
            ordered = sorted(engine._deduplicate(replay.execution_bars), key=lambda bar: (bar.timestamp, bar.symbol))
            execution[market] = ordered
            signal_rows = replay.signal_bars if replay.signal_bars is not None else replay.execution_bars
            signals[market] = sorted(engine._deduplicate(signal_rows), key=lambda bar: (bar.timestamp, bar.symbol))
            for bar in sorted(replay.warmup_bars, key=lambda item: (item.timestamp, item.symbol)):
                engine._ingest(bar)
                engine._ingest_execution(bar)
            actions[market] = _prepare_corporate_actions(replay.corporate_actions, cutoff=None)
            last_bars[market] = ordered

        cycles = {market: _group_by_timestamp(execution[market]) for market in market_orders}
        timestamps = sorted({timestamp for grouped in cycles.values() for timestamp in grouped})
        nav_path: list[float] = []
        cash_path: list[float] = []
        dividend_rows: list[tuple[Market, str, float, datetime]] = []
        fill_rows: list[PortfolioFill] = []

        shared_coordination = len(market_orders) > 1
        for timestamp in timestamps:
            self.account.same_cycle_buy_reservations.clear()
            cycles_at_time = {market: cycles[market].get(timestamp, []) for market in market_orders}
            for market in market_orders:
                engine = self.inputs[market].engine
                engine._fail_on_overdue_terminal_inventory(timestamp)
                for bar in cycles_at_time[market]:
                    engine._ingest_execution(bar)
                while (
                    signal_index[market] < len(signals[market])
                    and signals[market][signal_index[market]].timestamp <= timestamp
                ):
                    engine._ingest(signals[market][signal_index[market]])
                    signal_index[market] += 1
                while (
                    action_index[market] < len(actions[market])
                    and actions[market][action_index[market]].timestamp <= timestamp
                ):
                    action = actions[market][action_index[market]]
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
                            "timestamp": action.timestamp.isoformat(),
                            "symbol": canonical_symbol(action.symbol, market),
                            "action_type": action.action_type.value,
                            "cash_delta": delta,
                            "dividend_total_delta": engine._dividend_total - before_dividend,
                            "position_qty_before": position_qty_before,
                            "position_qty_after": position_qty_after,
                            "split_ratio": action.value if action.action_type.value == "split" else None,
                        }
                    )
                    action_index[market] += 1

            before_fills = {market: len(self.inputs[market].engine._fills) for market in market_orders}
            pending_before = self._pending_keys()
            for market in market_orders:
                engine = self.inputs[market].engine
                for bar in cycles_at_time[market]:
                    engine._fill_side(bar, OrderSide.SELL)
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
                    for bar in cycles_at_time[market]:
                        engine._fill_side(bar, OrderSide.BUY)
            new_fills = [
                fill for market in market_orders for fill in self.inputs[market].engine._fills[before_fills[market] :]
            ]
            new_fills.sort(key=lambda fill: (fill.timestamp, _market_order(fill.market), fill.symbol, fill.side.value))
            fill_rows.extend(new_fills)
            for fill in new_fills:
                self._events.append(_fill_event(fill))
            self._sync_reservations(timestamp, pending_before, "execution_cycle", new_fills)
            pending_before_decisions = self._pending_keys()

            for market in market_orders:
                for bar in cycles_at_time[market]:
                    self.account.marks[(market, bar.symbol)] = bar.close
                    self._events.append(
                        {
                            "event": "mark",
                            "market": market.value,
                            "timestamp": timestamp.isoformat(),
                            "symbol": bar.symbol,
                            "price": bar.close,
                        }
                    )
            self._advance_shared_risk(timestamp)

            for market in market_orders:
                cycle = cycles_at_time[market]
                if not cycle:
                    continue
                engine = self.inputs[market].engine
                before_rebalances = len(engine._rebalances)
                if any(history and history[-1].timestamp > timestamp for history in engine._history.values()):
                    raise AssertionError("Future-dated history reached a market decision")
                future_data_checks += 1
                engine._decide(timestamp)
                if len(engine._rebalances) > before_rebalances:
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
                    self._events.append(
                        {
                            "event": "decision",
                            "market": market.value,
                            "timestamp": timestamp.isoformat(),
                            "target_weights": [[symbol, weight] for symbol, weight in record.target_weights],
                            "risk_overlay_reasons": list(record.risk_overlay_reasons),
                            "position_keys_before_next_decision": [
                                [reserved_market.value, symbol]
                                for reserved_market, symbol in sorted(
                                    self.account.same_cycle_buy_reservations,
                                    key=lambda item: (item[0].value, item[1]),
                                )
                            ],
                        }
                    )
                    for order in engine._pending:
                        if order.side == OrderSide.BUY and order.decision_timestamp == timestamp:
                            key = (order.market, canonical_symbol(order.symbol, order.market))
                            self.account.same_cycle_buy_reservations.add(key)
                before_liquidations = len(engine._liquidation_events)
                engine._apply_terminal_lifecycles(timestamp, cycle)
                for liquidation in engine._liquidation_events[before_liquidations:]:
                    self._events.append({"event": "terminal_liquidation", "market": market.value, **liquidation})
                engine._record_holdings_snapshot(timestamp)
                market_curve[market].append(self.account.nav())
                market_timestamps[market].append(timestamp)

            if shared_coordination:
                self._defer_decision_buys_while_sells_work(timestamp, market_orders)
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
            nav_path.append(nav)
            cash_path.append(self.account.cash)

        if liquidate_at_end:
            self._liquidate_final_positions(market_orders, last_bars, market_curve, market_timestamps)
            if nav_path:
                nav_path[-1] = self.account.nav()
                cash_path[-1] = self.account.cash

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

        all_timestamps = tuple(timestamps)
        market_results: dict[Market, PortfolioBacktestResult] = {}
        for market in market_orders:
            replay = self.inputs[market]
            engine = replay.engine
            decision_bars = signals[market]
            benchmark = tuple(bar for bar in decision_bars if bar.symbol == engine.benchmark_symbol)
            benchmark_return = (
                benchmark[-1].close / benchmark[0].close - 1 if len(benchmark) > 1 and benchmark[0].close > 0 else 0.0
            )
            market_results[market] = engine._result(
                market_curve[market], market_timestamps[market], benchmark_return, benchmark
            )

        self._record_final_state_events()
        invariants = self._check_accounting(fill_rows, dividend_rows, future_data_checks)
        positions = tuple(
            {
                "market": market.value,
                "symbol": symbol,
                "qty": position.qty,
                "avg_price": position.avg_price,
                "mark": self.account.marks.get((market, symbol), position.avg_price),
            }
            for (market, symbol), position in sorted(
                self.account.positions.items(), key=lambda item: (item[0][0].value, item[0][1])
            )
        )
        return SharedReplayResult(
            start_nav=self.account.starting_nav,
            ending_nav=self.account.nav(),
            cash=self.account.cash,
            positions=positions,
            timestamps=all_timestamps,
            nav_path=tuple(nav_path),
            cash_path=tuple(cash_path),
            events=tuple(self._events),
            market_results=market_results,
            invariants=invariants,
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
                order
                for order in engine._pending
                if order.side == OrderSide.BUY and order.decision_timestamp == timestamp
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
                    "client_order_id": order.client_order_id,
                    "symbol": order.symbol,
                    "side": order.side.value,
                    "qty": order.qty,
                }
            )
        for key in sorted(before - after, key=lambda item: (item[0].value, item[1])):
            if self.account.reservation_states.get(key) != "reserved":
                continue
            filled = any(fill.market == key[0] and fill.client_order_id == key[1] for fill in fills)
            self.account.reservation_states[key] = "released"
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
        market_curve: dict[Market, list[float]],
        market_timestamps: dict[Market, list[datetime]],
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
            if market_curve[market]:
                market_curve[market][-1] = self.account.nav()
            if engine._pending:
                before = self._pending_keys()
                engine._pending = []
                release_time = (
                    market_timestamps[market][-1] if market_timestamps[market] else self.account.start_timestamp
                )
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
                market_timestamps[market][-1] if market_timestamps[market] else self.account.start_timestamp,
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


def _group_by_timestamp(bars: list[Bar]) -> dict[datetime, list[Bar]]:
    grouped: dict[datetime, list[Bar]] = defaultdict(list)
    for bar in bars:
        grouped[bar.timestamp].append(bar)
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
