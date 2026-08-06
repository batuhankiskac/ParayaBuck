from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from itertools import groupby
from pathlib import Path

from parayabuck.core.models import Bar, Market
from parayabuck.markets.symbols import canonical_symbol
from parayabuck.portfolio.base import PortfolioContext
from parayabuck.portfolio.idempotency import IdempotencyKeyFactory
from parayabuck.portfolio.models import (
    PortfolioState,
    RebalancePolicy,
    TargetPortfolio,
)
from parayabuck.portfolio.universes import CRYPTO_UNIVERSE_VERSION, EQUITY_UNIVERSE_VERSION


class OrderSide(StrEnum):
    BUY = "buy"
    SELL = "sell"


@dataclass
class PortfolioOrder:
    client_order_id: str
    symbol: str
    market: Market
    side: OrderSide
    qty: float
    created_at: datetime
    decision_timestamp: datetime


@dataclass
class _Position:
    symbol: str
    market: Market
    timeframe: str
    qty: float
    avg_price: float
    opened_at: datetime
    entry_fee: float = 0.0
    realized_pnl_accumulator: float = 0.0


@dataclass(frozen=True)
class PortfolioFill:
    timestamp: datetime
    symbol: str
    market: Market
    side: OrderSide
    qty: float
    reference_price: float
    fill_price: float
    slippage_cost: float
    spread_cost: float
    fee: float
    decision_timestamp: datetime
    client_order_id: str = ""


@dataclass(frozen=True)
class RebalanceRecord:
    decision_timestamp: datetime
    reason: str
    target_weights: tuple[tuple[str, float], ...]
    target_cash_weight: float
    # Execution accounting: signed deltas/weights so buys and sells are not
    # folded into one positive magnitude.
    pre_trade_weights: tuple[tuple[str, float], ...]
    signed_trade_weights: tuple[tuple[str, float], ...]
    post_fill_actual_weights: tuple[tuple[str, float], ...]
    target_vs_actual_drift: float
    unfilled_notional: float
    rejected_notional: float
    expected_turnover: float
    realized_turnover: float
    cost: float
    executed_weights: tuple[tuple[str, float], ...]
    universe_version: str
    universe_snapshot_id: str
    equity_at_decision: float
    rebalance_number: int


@dataclass(frozen=True)
class SymbolContribution:
    symbol: str
    market: Market
    realized_pnl: float
    fees: float
    final_weight: float


@dataclass(frozen=True)
class PortfolioBacktestResult:
    starting_equity: float
    ending_equity: float
    total_return: float
    benchmark_return: float
    excess_return: float
    gross_return: float
    net_return: float
    max_drawdown: float
    max_drawdown_duration_days: int
    cagr: float
    annualized_volatility: float
    sharpe: float
    sortino: float
    calmar: float
    duration_days: int
    start_date: str
    end_date: str
    equity_curve: tuple[float, ...]
    curve_timestamps: tuple[datetime, ...]
    realized_pnls: tuple[float, ...]
    realized_pnl_events: tuple[tuple[datetime, str, float], ...]
    rebalances: tuple[RebalanceRecord, ...]
    fills: tuple[PortfolioFill, ...]
    weight_history: tuple[tuple[datetime, dict[str, float]], ...]
    universe_history: tuple[tuple[datetime, tuple[str, ...]], ...]
    benchmark_bars: tuple[Bar, ...]
    benchmark_symbol: str
    slippage_cost_total: float
    spread_cost_total: float
    fee_total: float
    turnover: float
    average_holdings_count: float
    average_cash_ratio: float
    rebalance_count: int
    closed_positions: int
    per_symbol: tuple[SymbolContribution, ...]
    maximum_concentration: float
    worst_month_return: float
    worst_year_return: float
    positive_month_ratio: float
    state: PortfolioState
    information_ratio: float = 0.0
    cash_yield: float = 0.0


def _adverse_bps(slippage_bps: float, spread_bps: float) -> float:
    return slippage_bps + spread_bps / 2


class PortfolioBacktestEngine:
    """Portfolio-aware backtest engine with next-open fills.

    The engine mirrors the established backtest cost model (adverse slippage +
    half-spread on every fill, crypto taker fees, participation caps, partial
    fills, fractional quantities) but operates on target portfolio weights
    instead of per-bar signals:

    * decisions happen on completed bars only;
    * orders created at a decision fill at the next tradable bar's open;
    * sells execute before buys at the same timestamp so cash never goes
      negative;
    * turnover below ``turnover_band`` or ``minimum_order_notional`` is
      skipped;
    * one net delta per symbol per rebalance prevents sell/buy churn;
    * restarting with a persisted ``PortfolioState`` resumes the same
      allocation without duplicate orders.
    """

    def __init__(
        self,
        strategy: object,
        rebalance_policy: RebalancePolicy,
        *,
        starting_equity: float = 100_000.0,
        slippage_bps: float = 2.0,
        spread_bps: float = 0.0,
        commission: float = 0.0,
        crypto_taker_fee_bps: float = 0.0,
        benchmark_symbol: str = "SPY",
        max_participation_pct: float = 0.10,
        turnover_band: float = 0.005,
        minimum_order_notional: float = 10.0,
        universe: object | None = None,
        universe_version: str = "",
        state: PortfolioState | None = None,
        state_path: str | None = None,
        cash_rate_annualized: float = 0.0,
    ) -> None:
        from parayabuck.portfolio.base import PortfolioStrategy

        if not isinstance(strategy, PortfolioStrategy):
            raise TypeError("PortfolioBacktestEngine requires a PortfolioStrategy")
        if starting_equity <= 0:
            raise ValueError("Starting equity must be positive")
        if not 0 < max_participation_pct <= 1:
            raise ValueError("Participation ratio must be in (0, 1]")
        if not 0 <= turnover_band < 1:
            raise ValueError("Turnover band must be in [0, 1)")
        if minimum_order_notional < 0:
            raise ValueError("Minimum order notional must be non-negative")
        self.strategy = strategy
        self.rebalance_policy = rebalance_policy
        self.starting_equity = starting_equity
        self.slippage_bps = slippage_bps
        self.spread_bps = spread_bps
        self.commission = commission
        self.crypto_taker_fee_bps = crypto_taker_fee_bps
        self.benchmark_symbol = benchmark_symbol
        self.max_participation_pct = max_participation_pct
        self.turnover_band = turnover_band
        self.minimum_order_notional = minimum_order_notional
        self.universe = universe
        self.universe_version = (
            universe_version
            or (
                EQUITY_UNIVERSE_VERSION
                if strategy.market == Market.EQUITY
                else CRYPTO_UNIVERSE_VERSION
            )
        )
        self.state_path = Path(state_path) if state_path else None
        self.cash_rate_annualized = float(cash_rate_annualized)
        self.state = state or PortfolioState(
            strategy_name=strategy.name, strategy_version=strategy.version
        )
        self._idempotency = IdempotencyKeyFactory(strategy.name, strategy.version)
        self._history: dict[tuple[str, str], list[Bar]] = {}
        self._positions: dict[str, _Position] = {}
        self._pending: list[PortfolioOrder] = []
        self._fills: list[PortfolioFill] = []
        self._rebalances: list[RebalanceRecord] = []
        self._realized_pnl_events: list[tuple[datetime, str, float]] = []
        self._previous_timestamp: dict[Market, datetime | None] = {
            Market.EQUITY: None,
            Market.CRYPTO: None,
        }
        self._last_price: dict[str, float] = dict(self.state.last_prices)
        self._weight_history: list[tuple[datetime, dict[str, float]]] = []
        self._universe_history: list[tuple[datetime, tuple[str, ...]]] = []
        self._holdings_history: list[tuple[int, float, float]] = []
        self._rebalance_number = self.state.rebalance_count
        if self.state.last_rebalance_at is not None:
            # Period detection resumes from the last decision so a restart in
            # the middle of a month does not fire an unscheduled rebalance.
            self._previous_timestamp[strategy.market] = self.state.last_rebalance_at
        # Cash is authoritative when persisted (schema v2); a v1 state without
        # cash derives it from holdings once at migration time.
        if self.state.cash is not None:
            self._cash = self.state.cash
        else:
            self._cash = starting_equity
        if self.state.holdings:
            for symbol, quantity in self.state.holdings.items():
                avg_price = self.state.avg_prices.get(symbol, 0.0)
                if quantity <= 0 or avg_price <= 0:
                    continue
                self._positions[symbol] = _Position(
                    symbol,
                    Market.CRYPTO if "/" in symbol else Market.EQUITY,
                    strategy.timeframe,
                    quantity,
                    avg_price,
                    self.state.last_rebalance_at or datetime(1970, 1, 1, tzinfo=UTC),
                    entry_fee=self.state.entry_fees.get(symbol, 0.0),
                    realized_pnl_accumulator=self.state.realized_pnl_accumulators.get(
                        symbol, 0.0
                    ),
                )
                if self.state.cash is None:
                    # v1 migration: derive cash from the holdings cost basis.
                    self._cash -= quantity * avg_price
        # Pending orders decided before the restart must survive it; they are
        # re-armed so fills resume where the interrupted run left off.
        for raw_order in self.state.pending_orders:
            order = self._order_from_dict(raw_order)
            if order is not None:
                self._pending.append(order)

    # -- public API ---------------------------------------------------------

    def run(
        self,
        bars: list[Bar],
        warmup: list[Bar] | None = None,
        *,
        liquidate_at_end: bool = True,
        cash_rate_annualized: float | None = None,
    ) -> PortfolioBacktestResult:
        # Orders pending from a restored state survive the fresh-run reset so
        # a restart resumes fills exactly where the interrupted run left off.
        restored_pending = [order for order in self._pending]
        self._pending = []
        self._fills = []
        self._rebalances = []
        self._realized_pnl_events = []
        self._weight_history = []
        self._universe_history = []
        self._holdings_history = []
        self._pending.extend(restored_pending)
        if cash_rate_annualized is not None:
            self.cash_rate_annualized = float(cash_rate_annualized)
        ordered = sorted(self._deduplicate(bars), key=lambda bar: (bar.timestamp, bar.symbol))
        watermark = self.state.last_processed_bar
        if watermark:
            cutoff = datetime.fromisoformat(watermark)
            ordered = [bar for bar in ordered if bar.timestamp > cutoff]
        for bar in sorted(warmup or [], key=lambda item: (item.timestamp, item.symbol)):
            self._ingest(bar)
        curve: list[float] = []
        curve_timestamps: list[datetime] = []
        for _, grouped in groupby(ordered, key=lambda bar: bar.timestamp):
            cycle = list(grouped)
            for bar in cycle:
                self._ingest(bar)
            self._fill_cycle(cycle)
            for bar in cycle:
                self._last_price[canonical_symbol(bar.symbol, bar.market)] = bar.close
            self._decide(cycle[0].timestamp)
            curve.append(self._equity())
            curve_timestamps.append(cycle[0].timestamp)
            self._record_holdings_snapshot(cycle[0].timestamp)
        if liquidate_at_end and self._positions:
            last_bar: dict[tuple[str, str], Bar] = {self._key(bar): bar for bar in ordered}
            for position in list(self._positions.values()):
                exit_bar = last_bar.get((position.symbol, position.timeframe))
                if exit_bar is not None:
                    self._close_position(position, exit_bar)
            if curve:
                curve.append(self._equity())
                curve_timestamps.append(curve_timestamps[-1])
            if not self._positions:
                # The state must reflect the liquidation: holdings and pending
                # orders are gone and cash already holds the proceeds, so a
                # later restart cannot resurrect phantom positions.
                self.state.holdings = {}
                self.state.avg_prices = {}
                self.state.entry_fees = {}
                self.state.realized_pnl_accumulators = {}
                self.state.pending_orders = ()
                self.state.pending_target = {}
                self.state.cash = self._cash
                if self.state_path is not None:
                    self.save_state()
        benchmark = [bar for bar in ordered if bar.symbol == self.benchmark_symbol]
        benchmark_return = (
            benchmark[-1].close / benchmark[0].close - 1
            if len(benchmark) > 1 and benchmark[0].close > 0
            else 0.0
        )
        return self._result(curve, curve_timestamps, benchmark_return, tuple(benchmark))

    def save_state(self) -> None:
        if self.state_path is None:
            return
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.state_path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(self.state.to_dict(), indent=2, ensure_ascii=False, sort_keys=True),
            encoding="utf-8",
        )
        temporary.replace(self.state_path)

    @staticmethod
    def load_state(path: str | Path) -> PortfolioState | None:
        source = Path(path)
        if not source.exists():
            return None
        raw = json.loads(source.read_text(encoding="utf-8"))
        return PortfolioState.from_dict(raw if isinstance(raw, dict) else {})

    # -- internals ----------------------------------------------------------

    def _key(self, bar: Bar) -> tuple[str, str]:
        return canonical_symbol(bar.symbol, bar.market), bar.timeframe

    @staticmethod
    def _order_to_dict(order: PortfolioOrder) -> dict[str, object]:
        return {
            "client_order_id": order.client_order_id,
            "symbol": order.symbol,
            "market": order.market.value,
            "side": order.side.value,
            "qty": order.qty,
            "created_at": order.created_at.isoformat(),
            "decision_timestamp": order.decision_timestamp.isoformat(),
        }

    def _order_from_dict(self, raw: dict[str, object]) -> PortfolioOrder | None:
        try:
            return PortfolioOrder(
                client_order_id=str(raw["client_order_id"]),
                symbol=str(raw["symbol"]),
                market=Market(str(raw["market"])),
                side=OrderSide(str(raw["side"])),
                qty=float(str(raw["qty"])),
                created_at=datetime.fromisoformat(str(raw["created_at"])),
                decision_timestamp=datetime.fromisoformat(str(raw["decision_timestamp"])),
            )
        except (KeyError, ValueError):
            return None

    def _ingest(self, bar: Bar) -> None:
        series = self._history.setdefault(self._key(bar), [])
        if not series or series[-1].timestamp < bar.timestamp:
            series.append(bar)

    @staticmethod
    def _deduplicate(bars: list[Bar]) -> list[Bar]:
        unique: dict[tuple[str, str, datetime], Bar] = {}
        for bar in bars:
            key = (canonical_symbol(bar.symbol, bar.market), bar.timeframe, bar.timestamp)
            previous = unique.get(key)
            if previous is None:
                unique[key] = bar
            elif (previous.open, previous.high, previous.low, previous.close, previous.volume) != (
                bar.open,
                bar.high,
                bar.low,
                bar.close,
                bar.volume,
            ):
                raise ValueError(
                    f"Conflicting duplicate bars for {bar.symbol} at {bar.timestamp.isoformat()}"
                )
        return list(unique.values())

    def _record_holdings_snapshot(self, timestamp: datetime) -> None:
        active = [
            position
            for position in self._positions.values()
            if position.opened_at <= timestamp
        ]
        position_value = sum(
            position.qty * self._last_price.get(position.symbol, position.avg_price)
            for position in active
        )
        equity = self._equity()
        cash_ratio = max(0.0, (equity - position_value) / equity) if equity > 0 else 1.0
        single_values = [
            position.qty * self._last_price.get(position.symbol, position.avg_price)
            for position in active
        ]
        concentration = (
            max(single_values) / equity if equity > 0 and single_values else 0.0
        )
        self._holdings_history.append((len(active), cash_ratio, concentration))
        # Persist holdings after every completed bar (fills included) so a
        # restart resumes the exact allocation of the last processed bar.
        self.state.holdings = {
            symbol: position.qty for symbol, position in self._positions.items() if position.qty > 0
        }
        self.state.avg_prices = {
            symbol: position.avg_price
            for symbol, position in self._positions.items()
            if position.qty > 0
        }
        self.state.entry_fees = {
            symbol: position.entry_fee
            for symbol, position in self._positions.items()
            if position.qty > 0
        }
        self.state.realized_pnl_accumulators = {
            symbol: position.realized_pnl_accumulator
            for symbol, position in self._positions.items()
            if position.qty > 0
        }
        self.state.cash = self._cash
        self.state.last_prices = dict(self._last_price)
        self.state.pending_orders = tuple(self._order_to_dict(order) for order in self._pending)
        self.state.last_processed_bar = timestamp.isoformat()
        if self.state_path is not None:
            self.save_state()

    def _equity(self) -> float:
        return self._cash + sum(
            position.qty * self._last_price.get(position.symbol, position.avg_price)
            for position in self._positions.values()
        )

    def _fill_cycle(self, cycle: list[Bar]) -> None:
        for bar in cycle:
            self._fill_side(bar, OrderSide.SELL)
        for bar in cycle:
            self._fill_side(bar, OrderSide.BUY)

    def _fill_side(self, bar: Bar, side: OrderSide) -> None:
        symbol = canonical_symbol(bar.symbol, bar.market)
        remaining: list[PortfolioOrder] = []
        for order in self._pending:
            if order.symbol != symbol or order.side != side:
                remaining.append(order)
                continue
            if order.created_at >= bar.timestamp:
                remaining.append(order)
                continue
            if bar.volume <= 0:
                remaining.append(order)
                continue
            fillable = min(order.qty, bar.volume * self.max_participation_pct)
            if fillable <= 0:
                remaining.append(order)
                continue
            adverse = _adverse_bps(self.slippage_bps, self.spread_bps)
            side_multiplier = 1 if side == OrderSide.BUY else -1
            fill_price = bar.open * (1 + side_multiplier * adverse / 10_000)
            slippage_cost = fillable * bar.open * self.slippage_bps / 10_000
            spread_cost = fillable * bar.open * (self.spread_bps / 2) / 10_000
            fee = self.commission + (
                fillable * fill_price * self.crypto_taker_fee_bps / 10_000
                if order.market == Market.CRYPTO
                else 0.0
            )
            filled_qty = fillable
            if side == OrderSide.BUY:
                notional = fillable * fill_price
                if notional + fee > self._cash:
                    remaining.append(order)
                    continue
                self._cash -= notional + fee
                position = self._positions.get(symbol)
                if position is None:
                    self._positions[symbol] = _Position(
                        symbol,
                        order.market,
                        bar.timeframe,
                        fillable,
                        fill_price,
                        order.created_at,
                        entry_fee=fee,
                    )
                else:
                    total = position.qty + fillable
                    position.avg_price = (
                        position.qty * position.avg_price + fillable * fill_price
                    ) / total
                    position.qty = total
                    position.entry_fee += fee
            else:
                position = self._positions.get(symbol)
                if position is None or position.qty <= 0:
                    continue
                close_qty = min(fillable, position.qty)
                filled_qty = close_qty
                notional = close_qty * fill_price
                self._cash += notional - fee
                # The entry-fee share is a real cost of the closed portion:
                # it reduces realized PnL on partial AND full exits alike.
                fee_share = position.entry_fee * (close_qty / max(1e-9, position.qty))
                position.entry_fee -= fee_share
                # state.realized_pnl is updated with the NET realized delta at
                # every fill (partial and full); it is never credited again at
                # full close, so persisted state always agrees with the
                # economic result.
                realized_delta = (
                    (fill_price - position.avg_price) * close_qty - fee - fee_share
                )
                position.realized_pnl_accumulator += realized_delta
                self.state.realized_pnl += realized_delta
                position.qty -= close_qty
                if position.qty <= 0:
                    self._realized_pnl_events.append(
                        (bar.timestamp, symbol, position.realized_pnl_accumulator)
                    )
                    self._positions.pop(symbol, None)
            self._fills.append(
                PortfolioFill(
                    timestamp=bar.timestamp,
                    symbol=symbol,
                    market=order.market,
                    side=side,
                    qty=filled_qty,
                    reference_price=bar.open,
                    fill_price=fill_price,
                    slippage_cost=slippage_cost,
                    spread_cost=spread_cost,
                    fee=fee,
                    decision_timestamp=order.decision_timestamp,
                    client_order_id=order.client_order_id,
                )
            )
            if filled_qty < order.qty:
                remaining.append(
                    PortfolioOrder(
                        order.client_order_id,
                        order.symbol,
                        order.market,
                        order.side,
                        order.qty - filled_qty,
                        bar.timestamp,
                        order.decision_timestamp,
                    )
                )
        self._pending = remaining

    def _decide(self, timestamp: datetime) -> None:
        market = self.strategy.market
        previous = self._previous_timestamp[market]
        self._previous_timestamp[market] = timestamp
        if not self.rebalance_policy.is_period_start(previous, timestamp):
            return
        context = self._build_context(timestamp)
        if not self.strategy.supports(context):
            return
        target = self.strategy.decide(context)
        self._rebalance_number += 1
        self._apply_target(target)
        self._record_rebalance(target)

    def _build_context(self, timestamp: datetime) -> PortfolioContext:
        history: dict[str, tuple[Bar, ...]] = {}
        prices: dict[str, float] = {}
        for (symbol, _timeframe), bars in self._history.items():
            eligible = [bar for bar in bars if bar.timestamp <= timestamp]
            if not eligible:
                continue
            history[symbol] = tuple(eligible)
            prices[symbol] = eligible[-1].close
        holdings = {
            symbol: position.qty for symbol, position in self._positions.items() if position.qty > 0
        }
        benchmark = "BTC/USD" if self.strategy.market == Market.CRYPTO else "SPY"
        return PortfolioContext(
            decision_timestamp=timestamp,
            market=self.strategy.market,
            timeframe=self.strategy.timeframe,
            history=history,
            universe=self.universe,
            universe_version=self.universe_version,
            equity=self._equity(),
            holdings=holdings,
            prices=prices,
            benchmark_symbol=benchmark,
        )

    def _apply_target(self, target: TargetPortfolio) -> None:
        # A new decision supersedes every order from the previous one: the
        # latest target is authoritative, so stale pending orders are dropped
        # and the delta is recomputed against executed quantities only.
        self._pending = []
        equity = self._equity()
        symbol_targets: dict[str, float] = {}
        for weight in target.weights:
            symbol = canonical_symbol(weight.symbol, self.strategy.market)
            symbol_targets[symbol] = weight.weight
        for symbol in self._positions:
            symbol_targets.setdefault(symbol, 0.0)
        self._current_pre_trade_weights: dict[str, float] = {}
        self._expected_turnover = 0.0
        for symbol, target_weight in symbol_targets.items():
            price = self._last_price.get(symbol)
            if price is None or price <= 0:
                continue
            current_position = self._positions.get(symbol)
            current_qty = current_position.qty if current_position is not None else 0.0
            current_weight = current_qty * price / equity if equity > 0 else 0.0
            self._current_pre_trade_weights[symbol] = current_weight
            if abs(target_weight - current_weight) < self.turnover_band:
                continue
            target_qty = target_weight * equity / price
            delta = target_qty - current_qty
            if abs(delta) * price < self.minimum_order_notional:
                continue
            side = OrderSide.BUY if delta > 0 else OrderSide.SELL
            self._expected_turnover += abs(target_weight - current_weight)
            self._pending.append(
                PortfolioOrder(
                    client_order_id=self._idempotency.key(
                        decision_timestamp=target.decision_timestamp,
                        symbol=symbol,
                        side=side.value,
                        rebalance_number=self._rebalance_number,
                    ),
                    symbol=symbol,
                    market=self.strategy.market,
                    side=side,
                    qty=abs(delta),
                    created_at=target.decision_timestamp,
                    decision_timestamp=target.decision_timestamp,
                )
            )
        self.state.pending_target = dict(symbol_targets)

    def _record_rebalance(self, target: TargetPortfolio) -> None:
        decision = target.decision_timestamp
        target_map = {
            canonical_symbol(weight.symbol, self.strategy.market): weight.weight
            for weight in target.weights
        }
        signed = tuple(
            sorted(
                (
                    symbol,
                    weight - self._current_pre_trade_weights.get(symbol, 0.0),
                )
                for symbol, weight in target_map.items()
                if abs(weight - self._current_pre_trade_weights.get(symbol, 0.0)) > 1e-9
            )
        )
        self._rebalances.append(
            RebalanceRecord(
                decision_timestamp=decision,
                reason=target.rebalance_reason.value,
                target_weights=tuple(
                    sorted((weight.symbol, weight.weight) for weight in target.weights)
                ),
                target_cash_weight=target.cash_weight,
                pre_trade_weights=tuple(
                    sorted(self._current_pre_trade_weights.items())
                ),
                signed_trade_weights=signed,
                post_fill_actual_weights=(),
                target_vs_actual_drift=0.0,
                unfilled_notional=0.0,
                rejected_notional=0.0,
                expected_turnover=self._expected_turnover,
                realized_turnover=0.0,
                cost=0.0,
                executed_weights=(),
                universe_version=target.universe_version or self.universe_version,
                universe_snapshot_id=target.universe_snapshot_id,
                equity_at_decision=self._equity(),
                rebalance_number=self._rebalance_number,
            )
        )
        self._weight_history.append(
            (decision, dict((weight.symbol, weight.weight) for weight in target.weights))
        )
        symbols = self._universe_symbols(decision)
        if symbols is not None:
            self._universe_history.append((decision, symbols))
        self.state.last_target = {
            weight.symbol: weight.weight for weight in target.weights
        }
        self.state.last_rebalance_at = decision
        self.state.rebalance_count = self._rebalance_number
        self.state.universe_version = target.universe_version or self.universe_version
        if self.state_path is not None:
            self.save_state()

    def _universe_symbols(self, timestamp: datetime) -> tuple[str, ...] | None:
        snapshots = getattr(self.universe, "snapshots", ())
        eligible = [item for item in snapshots if item.effective_at <= timestamp]
        if not eligible:
            return None
        return tuple(sorted(eligible[-1].symbols))

    def _close_position(self, position: _Position, bar: Bar) -> None:
        if position.qty <= 0:
            return
        adverse = _adverse_bps(self.slippage_bps, self.spread_bps)
        exit_price = bar.close * (1 - adverse / 10_000)
        fee = self.commission + (
            position.qty * exit_price * self.crypto_taker_fee_bps / 10_000
            if position.market == Market.CRYPTO
            else 0.0
        )
        realized = (
            (exit_price - position.avg_price) * position.qty - position.entry_fee - fee
        )
        self._cash += position.qty * exit_price - fee
        self._realized_pnl_events.append((bar.timestamp, position.symbol, realized))
        self.state.realized_pnl += realized
        self._positions.pop(position.symbol, None)

    def _result(
        self,
        curve: list[float],
        curve_timestamps: list[datetime],
        benchmark_return: float,
        benchmark_bars: tuple[Bar, ...],
    ) -> PortfolioBacktestResult:
        ending = curve[-1] if curve else self.starting_equity
        peak = self.starting_equity
        peak_index = 0
        max_drawdown = 0.0
        max_drawdown_duration_days = 0
        for index, equity in enumerate(curve):
            if equity > peak:
                peak = equity
                peak_index = index
            elif equity < peak and peak > 0:
                drawdown = (peak - equity) / peak
                max_drawdown = max(max_drawdown, drawdown)
                if curve_timestamps:
                    duration_days = (
                        curve_timestamps[index] - curve_timestamps[peak_index]
                    ).total_seconds() / 86_400
                    max_drawdown_duration_days = max(max_drawdown_duration_days, int(duration_days))
        returns = [
            curve[i] / curve[i - 1] - 1
            for i in range(1, len(curve))
            if curve[i - 1] > 0
        ]
        downside = [value for value in returns if value < 0]
        mean_return = sum(returns) / len(returns) if returns else 0.0
        std = self._stddev(returns)
        downside_std = self._stddev(downside)
        start_ts = curve_timestamps[0] if curve_timestamps else None
        end_ts = curve_timestamps[-1] if curve_timestamps else None
        duration_days = (
            int((end_ts - start_ts).total_seconds() / 86_400) if start_ts and end_ts else 0
        )
        years = duration_days / 365.0
        total_return = ending / self.starting_equity - 1
        cagr = (
            ((ending / self.starting_equity) ** (1 / years) - 1)
            if years > 0 and ending > 0
            else 0.0
        )
        periods_per_year = len(returns) / years if years > 0 else 0.0
        annualizer = math.sqrt(max(1.0, periods_per_year)) if returns else 1.0
        pnls = tuple(event for _, _, event in self._realized_pnl_events)
        fills = tuple(self._fills)
        rebalances = tuple(self._enrich_rebalances())
        slippage_total = sum(fill.slippage_cost for fill in fills)
        spread_total = sum(fill.spread_cost for fill in fills)
        fee_total = sum(fill.fee for fill in fills)
        avg_equity = sum(curve) / len(curve) if curve else self.starting_equity
        traded_notional = sum(fill.qty * fill.reference_price for fill in fills)
        turnover = traded_notional / avg_equity if avg_equity > 0 else 0.0
        total_cost = slippage_total + spread_total + fee_total
        cost_ratio = total_cost / avg_equity if avg_equity > 0 else 0.0
        gross_return = (1 + total_return) / max(1e-9, 1 - cost_ratio) - 1 if cost_ratio < 1 else 0.0
        holdings_counts = [count for count, _cash, _conc in self._holdings_history]
        cash_ratios = [cash for _count, cash, _conc in self._holdings_history]
        concentration = [conc for _count, _cash, conc in self._holdings_history]
        monthly = self._monthly_returns(curve, curve_timestamps)
        monthly_values = [value for value in monthly.values() if value is not None]
        worst_month = min(monthly_values, default=0.0)
        worst_year = self._worst_year(curve, curve_timestamps)
        positive_month_ratio = (
            sum(1 for value in monthly_values if value > 0) / len(monthly_values)
            if monthly_values
            else 0.0
        )
        per_symbol = self._symbol_contributions()
        benchmark_closes = [bar.close for bar in benchmark_bars if bar.close > 0]
        benchmark_returns = [
            benchmark_closes[index] / benchmark_closes[index - 1] - 1
            for index in range(1, len(benchmark_closes))
            if benchmark_closes[index - 1] > 0
        ]
        excess = [
            portfolio - benchmark
            for portfolio, benchmark in zip(returns, benchmark_returns, strict=False)
        ]
        excess_mean = sum(excess) / len(excess) if excess else 0.0
        excess_std = self._stddev(excess)
        information_ratio = (
            excess_mean / excess_std * annualizer if excess_std > 0 else 0.0
        )
        avg_cash_ratio = (
            sum(cash_ratios) / len(cash_ratios) if cash_ratios else 0.0
        )
        cash_yield = avg_cash_ratio * self.cash_rate_annualized
        self.state.holdings = {
            symbol: position.qty for symbol, position in self._positions.items() if position.qty > 0
        }
        self.state.avg_prices = {
            symbol: position.avg_price
            for symbol, position in self._positions.items()
            if position.qty > 0
        }
        self.state.entry_fees = {
            symbol: position.entry_fee
            for symbol, position in self._positions.items()
            if position.qty > 0
        }
        self.state.realized_pnl_accumulators = {
            symbol: position.realized_pnl_accumulator
            for symbol, position in self._positions.items()
            if position.qty > 0
        }
        self.state.cash = self._cash
        self.state.last_prices = dict(self._last_price)
        return PortfolioBacktestResult(
            starting_equity=self.starting_equity,
            ending_equity=ending,
            total_return=total_return,
            benchmark_return=benchmark_return,
            excess_return=total_return - benchmark_return,
            gross_return=gross_return,
            net_return=total_return,
            max_drawdown=max_drawdown,
            max_drawdown_duration_days=max_drawdown_duration_days,
            cagr=cagr,
            annualized_volatility=std * annualizer if std > 0 else 0.0,
            sharpe=mean_return / std * annualizer if std > 0 else 0.0,
            sortino=mean_return / downside_std * annualizer if downside_std > 0 else 0.0,
            calmar=cagr / max_drawdown if max_drawdown > 0 else 0.0,
            duration_days=duration_days,
            start_date=start_ts.isoformat() if start_ts else "",
            end_date=end_ts.isoformat() if end_ts else "",
            equity_curve=tuple(curve),
            curve_timestamps=tuple(curve_timestamps),
            realized_pnls=pnls,
            realized_pnl_events=tuple(self._realized_pnl_events),
            rebalances=rebalances,
            fills=fills,
            weight_history=tuple(self._weight_history),
            universe_history=tuple(self._universe_history),
            benchmark_bars=benchmark_bars,
            benchmark_symbol=self.benchmark_symbol,
            slippage_cost_total=slippage_total,
            spread_cost_total=spread_total,
            fee_total=fee_total,
            turnover=turnover,
            average_holdings_count=(
                sum(holdings_counts) / len(holdings_counts) if holdings_counts else 0.0
            ),
            average_cash_ratio=sum(cash_ratios) / len(cash_ratios) if cash_ratios else 0.0,
            rebalance_count=len(self._rebalances),
            closed_positions=len(self._realized_pnl_events),
            per_symbol=per_symbol,
            maximum_concentration=max(concentration, default=0.0),
            worst_month_return=worst_month,
            worst_year_return=worst_year,
            positive_month_ratio=positive_month_ratio,
            state=self.state,
            information_ratio=information_ratio,
            cash_yield=cash_yield,
        )

    def _enrich_rebalances(self) -> tuple[RebalanceRecord, ...]:
        """Attach executed weights/turnover/cost to each decision from the fills
        that actually occurred after it (fills happen on the next tradable bar)."""
        fills_by_decision: dict[datetime, list[PortfolioFill]] = {}
        for fill in self._fills:
            fills_by_decision.setdefault(fill.decision_timestamp, []).append(fill)
        enriched: list[RebalanceRecord] = []
        for record in self._rebalances:
            decision_fills = fills_by_decision.get(record.decision_timestamp, ())
            signed_notional: dict[str, float] = {}
            executed: dict[str, float] = {}
            cost = 0.0
            for fill in decision_fills:
                sign = 1 if fill.side == OrderSide.BUY else -1
                signed_notional[fill.symbol] = (
                    signed_notional.get(fill.symbol, 0.0) + sign * fill.qty * fill.fill_price
                )
                executed[fill.symbol] = (
                    executed.get(fill.symbol, 0.0) + fill.qty * fill.fill_price
                )
                cost += fill.slippage_cost + fill.spread_cost + fill.fee
            equity = record.equity_at_decision
            pre_trade = dict(record.pre_trade_weights)
            actual_weights: dict[str, float] = {}
            signed_weights: dict[str, float] = {}
            for symbol, notional in signed_notional.items():
                if equity > 0:
                    signed_weights[symbol] = notional / equity
                    actual_weights[symbol] = pre_trade.get(symbol, 0.0) + notional / equity
            # Drift/unfilled must account for symbols sold to zero as well
            # (e.g. a full liquidation, where the target is empty but sells
            # happen): the check set is the union of targets and executed.
            drift = 0.0
            target_map = dict(record.target_weights)
            drift_symbols = set(target_map) | set(signed_weights)
            for symbol in sorted(drift_symbols):
                target_weight = target_map.get(symbol, 0.0)
                actual = actual_weights.get(symbol, pre_trade.get(symbol, 0.0))
                drift = max(drift, abs(target_weight - actual))
            expected_notional = sum(
                abs(target_weight - pre_trade.get(symbol, 0.0)) * equity
                for symbol, target_weight in target_map.items()
            )
            sold = {symbol for symbol in signed_notional if symbol not in target_map}
            expected_notional += sum(
                abs(pre_trade.get(symbol, 0.0)) * equity for symbol in sold
            )
            realized_notional = sum(abs(value) for value in signed_notional.values())
            executed_weights = tuple(
                sorted(
                    ((symbol, value / equity) for symbol, value in executed.items() if equity > 0)
                )
            )
            realized_turnover = sum(abs(value) for _, value in executed_weights)
            unfilled = max(0.0, expected_notional - realized_notional)
            enriched.append(
                RebalanceRecord(
                    decision_timestamp=record.decision_timestamp,
                    reason=record.reason,
                    target_weights=record.target_weights,
                    target_cash_weight=record.target_cash_weight,
                    pre_trade_weights=record.pre_trade_weights,
                    signed_trade_weights=tuple(sorted(signed_weights.items())),
                    post_fill_actual_weights=tuple(sorted(actual_weights.items())),
                    target_vs_actual_drift=drift,
                    unfilled_notional=unfilled,
                    rejected_notional=0.0,
                    expected_turnover=record.expected_turnover,
                    realized_turnover=realized_turnover,
                    cost=cost,
                    executed_weights=executed_weights,
                    universe_version=record.universe_version,
                    universe_snapshot_id=record.universe_snapshot_id,
                    equity_at_decision=equity,
                    rebalance_number=record.rebalance_number,
                )
            )
        return tuple(enriched)

    def _monthly_returns(
        self, curve: list[float], curve_timestamps: list[datetime]
    ) -> dict[tuple[int, int], float | None]:
        buckets: dict[tuple[int, int], list[int]] = {}
        for index, timestamp in enumerate(curve_timestamps):
            buckets.setdefault((timestamp.year, timestamp.month), []).append(index)
        monthly: dict[tuple[int, int], float | None] = {}
        previous = self.starting_equity
        for (year, month), indices in sorted(buckets.items()):
            value = curve[indices[-1]]
            monthly[(year, month)] = value / previous - 1 if previous > 0 else None
            previous = value
        return monthly

    def _worst_year(self, curve: list[float], curve_timestamps: list[datetime]) -> float:
        by_year: dict[int, list[int]] = {}
        for index, timestamp in enumerate(curve_timestamps):
            by_year.setdefault(timestamp.year, []).append(index)
        worst = 0.0
        previous = self.starting_equity
        for year in sorted(by_year):
            indices = by_year[year]
            value = curve[indices[-1]]
            year_return = value / previous - 1 if previous > 0 else 0.0
            worst = min(worst, year_return)
            previous = value
        return worst

    def _symbol_contributions(self) -> tuple[SymbolContribution, ...]:
        fee_by_symbol: dict[str, float] = {}
        for fill in self._fills:
            fee_by_symbol[fill.symbol] = fee_by_symbol.get(fill.symbol, 0.0) + fill.fee
        realized_by_symbol: dict[str, float] = {}
        for _timestamp, symbol, pnl in self._realized_pnl_events:
            realized_by_symbol[symbol] = realized_by_symbol.get(symbol, 0.0) + pnl
        symbols = sorted(set(fee_by_symbol) | set(realized_by_symbol))
        final_equity = self._equity()
        contributions: list[SymbolContribution] = []
        for symbol in symbols:
            position = self._positions.get(symbol)
            final_weight = 0.0
            if position is not None and final_equity > 0:
                final_weight = (
                    position.qty * self._last_price.get(symbol, position.avg_price) / final_equity
                )
            contributions.append(
                SymbolContribution(
                    symbol=symbol,
                    market=Market.CRYPTO if "/" in symbol else Market.EQUITY,
                    realized_pnl=realized_by_symbol.get(symbol, 0.0),
                    fees=fee_by_symbol.get(symbol, 0.0),
                    final_weight=final_weight,
                )
            )
        return tuple(contributions)

    @staticmethod
    def _stddev(values: list[float]) -> float:
        if len(values) < 2:
            return 0.0
        mean = sum(values) / len(values)
        return math.sqrt(sum((value - mean) ** 2 for value in values) / (len(values) - 1))


__all__ = [
    "OrderSide",
    "PortfolioBacktestEngine",
    "PortfolioBacktestResult",
    "PortfolioFill",
    "RebalanceRecord",
    "SymbolContribution",
]
