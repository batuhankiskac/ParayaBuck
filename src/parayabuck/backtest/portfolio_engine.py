from __future__ import annotations

import json
import math
from bisect import bisect_right
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from enum import StrEnum
from itertools import pairwise
from pathlib import Path

from parayabuck.backtest.regulatory_fees import rates_for_trade_date
from parayabuck.backtest.stats import sample_stddev
from parayabuck.core.models import AccountSnapshot, Bar, Market, PositionSnapshot
from parayabuck.data.market_dataset import (
    CashRateObservation,
    CorporateAction,
    CorporateActionType,
    MarketDataset,
)
from parayabuck.execution.portfolio import (
    PortfolioExecutionPolicy,
    PortfolioRiskLimits,
    PortfolioRiskOverlay,
)
from parayabuck.logger import get_logger
from parayabuck.markets.symbols import canonical_symbol
from parayabuck.markets.timeframes import NEW_YORK
from parayabuck.portfolio import PRODUCT_STRATEGY_NAMES
from parayabuck.portfolio.base import PortfolioContext, PortfolioStrategy, require_product_universe
from parayabuck.portfolio.idempotency import IdempotencyKeyFactory
from parayabuck.portfolio.models import (
    PortfolioState,
    RebalancePolicy,
    TargetPortfolio,
    TargetWeight,
)
from parayabuck.portfolio.universes import CRYPTO_UNIVERSE_VERSION, EQUITY_UNIVERSE_VERSION
from parayabuck.utils.io import atomic_write

log = get_logger("parayabuck.backtest.portfolio_engine")

# Context histories are trimmed to at least this many bars. It covers every
# in-tree strategy's trailing window (the equity 200-session SMA is the
# largest) with headroom; the profit_max_v1 validation ran with this value.
_MAX_CONTEXT_HISTORY_BARS = 274


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
    # Risk-overlay outcome at this decision: whether the overlay halted
    # (daily-loss/drawdown breach) and which limit tripped. The record stays
    # observable so reports can explain why a target was capped.
    risk_overlay_halted: bool = False
    risk_overlay_reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class SymbolContribution:
    symbol: str
    market: Market
    realized_pnl: float
    fees: float
    final_weight: float


@dataclass(frozen=True)
class TerminalLifecycle:
    """Point-in-time evidence for an entity's final real market-data bar."""

    last_bar: datetime
    event_class: str


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
    # Immutable audit rows for terminal/final liquidation.  These rows are
    # deliberately separate from decision-driven fills: liquidation is an
    # engine event and must remain replayable without inventing a decision.
    terminal_events: tuple[dict[str, object], ...] = ()
    information_ratio: float = 0.0
    cash_yield: float = 0.0
    dividend_total: float = 0.0
    cash_hurdle_return: float = 0.0
    benchmark_sharpe: float = 0.0
    benchmark_max_drawdown: float = 0.0
    benchmark_calmar: float = 0.0
    maximum_risk_contribution: float = 0.0
    # Every sell is a realized-PnL event. Full-close events separately carry
    # the complete lifetime PnL of positions that reached zero quantity.
    closed_position_events: tuple[tuple[datetime, str, float], ...] = ()
    # Offline backtests record drawdown breaches as SHADOW events (where
    # production would have halted) instead of halting the curve; each entry
    # is (decision timestamp, "portfolio_drawdown_halt").
    drawdown_halt_shadow_events: tuple[tuple[datetime, str], ...] = ()


def _adverse_bps(slippage_bps: float, spread_bps: float) -> float:
    return slippage_bps + spread_bps / 2


def session_date_for(timestamp: datetime, market: Market) -> date:
    """Trading-session date used for daily-loss risk accounting.

    Equity uses the New York trading date: a regular session can run past the
    UTC midnight boundary, and resetting daily PnL at UTC midnight would split
    one session across two days. Crypto trades 24/7 with no session close, so
    the UTC calendar date is the explicit daily reset boundary for crypto.
    """
    if market == Market.EQUITY:
        return timestamp.astimezone(NEW_YORK).date()
    return timestamp.date()


def equity_regulatory_fee(
    side: OrderSide,
    quantity: float,
    price: float,
    *,
    taf_per_share: float,
    taf_cap: float,
    cat_per_share: float,
    sec_fee_rate: float,
) -> float:
    """Raw TAF/CAT/SEC accrual before Alpaca's daily penny rounding."""
    cat = quantity * cat_per_share
    if side == OrderSide.BUY:
        return cat
    taf = quantity * taf_per_share
    if taf_cap > 0:
        taf = min(taf, taf_cap)
    return cat + taf + quantity * price * sec_fee_rate


def _prepare_corporate_actions(
    actions: list[CorporateAction] | None,
    *,
    cutoff: datetime | None,
) -> list[CorporateAction]:
    """Return one replay-safe action per economic event, in event order."""
    prepared: list[CorporateAction] = []
    seen: set[tuple[str, datetime, CorporateActionType, float]] = set()
    for action in actions or []:
        if cutoff is not None and action.timestamp <= cutoff:
            continue
        key = (
            canonical_symbol(action.symbol, Market.EQUITY),
            action.timestamp,
            action.action_type,
            action.value,
        )
        if key in seen:
            continue
        seen.add(key)
        prepared.append(action)
    return sorted(prepared, key=lambda item: item.timestamp)


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
        equity_taf_per_share: float = 0.0,
        equity_taf_cap: float = 0.0,
        equity_cat_per_share: float = 0.0,
        equity_sec_fee_rate: float = 0.0,
        equity_use_effective_dated_fees: bool = False,
        equity_regulatory_fee_multiplier: float = 1.0,
        benchmark_symbol: str = "SPY",
        max_participation_pct: float = 0.10,
        liquidity_lookback_bars: int = 20,
        turnover_band: float = 0.005,
        minimum_order_notional: float = 10.0,
        execution_policy: PortfolioExecutionPolicy | None = None,
        universe: object | None = None,
        universe_version: str = "",
        state: PortfolioState | None = None,
        state_path: str | None = None,
        cash_rate_annualized: float = 0.0,
        risk_limits: PortfolioRiskLimits | None = None,
        maximum_allocation: float = 1.0,
        enforce_drawdown_halt: bool = False,
        allow_static_universe: bool = False,
        terminal_lifecycles: Mapping[str, TerminalLifecycle] | None = None,
    ) -> None:
        if not isinstance(strategy, PortfolioStrategy):
            raise TypeError("PortfolioBacktestEngine requires a PortfolioStrategy")
        if starting_equity <= 0:
            raise ValueError("Starting equity must be positive")
        if not 0 < max_participation_pct <= 1:
            raise ValueError("Participation ratio must be in (0, 1]")
        if liquidity_lookback_bars <= 0:
            raise ValueError("Liquidity lookback must be positive")
        if not 0 <= turnover_band < 1:
            raise ValueError("Turnover band must be in [0, 1)")
        if minimum_order_notional < 0:
            raise ValueError("Minimum order notional must be non-negative")
        if not 0 < maximum_allocation <= 1:
            raise ValueError("Maximum allocation must be in (0, 1]")
        if execution_policy is not None:
            # One shared order-existence policy: the configured band and
            # notional floor are authoritative over any legacy constructor
            # defaults so BACKTEST and PAPER/LIVE cannot drift apart.
            turnover_band = execution_policy.rebalance_band_pct
            minimum_order_notional = execution_policy.minimum_order_notional
        cost_inputs = (
            slippage_bps,
            spread_bps,
            commission,
            crypto_taker_fee_bps,
            equity_taf_per_share,
            equity_taf_cap,
            equity_cat_per_share,
            equity_sec_fee_rate,
        )
        if any(not math.isfinite(value) or value < 0 for value in cost_inputs):
            raise ValueError("Cost inputs must be finite and non-negative")
        if not math.isfinite(equity_regulatory_fee_multiplier) or equity_regulatory_fee_multiplier <= 0:
            raise ValueError("Equity regulatory fee multiplier must be finite and positive")
        self.strategy = strategy
        self.rebalance_policy = rebalance_policy
        self.starting_equity = starting_equity
        self.slippage_bps = slippage_bps
        self.spread_bps = spread_bps
        self.commission = commission
        self.crypto_taker_fee_bps = crypto_taker_fee_bps
        self.equity_taf_per_share = equity_taf_per_share
        self.equity_taf_cap = equity_taf_cap
        self.equity_cat_per_share = equity_cat_per_share
        self.equity_sec_fee_rate = equity_sec_fee_rate
        self.equity_use_effective_dated_fees = equity_use_effective_dated_fees
        self.equity_regulatory_fee_multiplier = equity_regulatory_fee_multiplier
        self.benchmark_symbol = benchmark_symbol
        self.max_participation_pct = max_participation_pct
        self.liquidity_lookback_bars = liquidity_lookback_bars
        self.turnover_band = turnover_band
        self.minimum_order_notional = minimum_order_notional
        self.universe = universe
        self.universe_version = universe_version or (
            EQUITY_UNIVERSE_VERSION if strategy.market == Market.EQUITY else CRYPTO_UNIVERSE_VERSION
        )
        self.allow_static_universe = bool(allow_static_universe)
        self.terminal_lifecycles = self._validated_terminal_lifecycles(terminal_lifecycles)
        self.state_path = Path(state_path) if state_path else None
        self.cash_rate_annualized = float(cash_rate_annualized)
        self.risk_overlay = PortfolioRiskOverlay(risk_limits) if risk_limits is not None else None
        self.maximum_allocation = maximum_allocation
        # Offline backtests default to SHADOW drawdown halts (the alpha curve
        # keeps trading; breaches are recorded as events). Production-policy
        # simulations opt into real enforcement with this constructor flag.
        self.enforce_drawdown_halt = bool(enforce_drawdown_halt)
        self.state = state or PortfolioState(strategy_name=strategy.name, strategy_version=strategy.version)
        self._idempotency = IdempotencyKeyFactory(strategy.name, strategy.version)
        self._history: dict[tuple[str, str], list[Bar]] = {}
        self._execution_history: dict[tuple[str, str], list[Bar]] = {}
        self._positions: dict[str, _Position] = {}
        self._pending: list[PortfolioOrder] = []
        self._fills: list[PortfolioFill] = []
        self._liquidation_costs: list[tuple[str, float, float, float]] = []
        self._liquidation_events: list[dict[str, object]] = []
        self._rebalances: list[RebalanceRecord] = []
        self._realized_pnl_events: list[tuple[datetime, str, float]] = []
        self._closed_position_events: list[tuple[datetime, str, float]] = []
        self._dividend_total = 0.0
        self._cash_rate_observations: tuple[CashRateObservation, ...] = ()
        self._risk_contribution_history: list[float] = []
        self._previous_timestamp: dict[Market, datetime | None] = {
            Market.EQUITY: None,
            Market.CRYPTO: None,
        }
        self._last_price: dict[str, float] = dict(self.state.last_prices)
        self._weight_history: list[tuple[datetime, dict[str, float]]] = []
        self._universe_history: list[tuple[datetime, tuple[str, ...]]] = []
        self._holdings_history: list[tuple[int, float, float]] = []
        self._rebalance_number = self.state.rebalance_count
        # Explicit daily-loss/high-water lifecycle. day-start equity is the
        # previous session's closing equity, never the current post-close
        # equity; the risk state advances on every processed bar so drawdown
        # and daily PnL stay live between scheduled rebalances. A persisted
        # v3 state restores the lifecycle so a restart behaves identically to
        # an uninterrupted run (high-water before restart, drawdown crossed
        # after restart, mid-session day-start).
        self._peak_equity = self.state.peak_equity if self.state.peak_equity is not None else starting_equity
        self._day_start_equity = (
            self.state.day_start_equity if self.state.day_start_equity is not None else starting_equity
        )
        self._last_observed_equity = (
            self.state.last_observed_equity if self.state.last_observed_equity is not None else starting_equity
        )
        self._risk_session_date: date | None = None
        if self.state.risk_session_date:
            try:
                self._risk_session_date = date.fromisoformat(self.state.risk_session_date)
            except ValueError:
                self._risk_session_date = None
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
                    realized_pnl_accumulator=self.state.realized_pnl_accumulators.get(symbol, 0.0),
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
        signal_bars: list[Bar] | None = None,
        corporate_actions: list[CorporateAction] | None = None,
        cash_rates: list[CashRateObservation] | None = None,
        prepared_inputs: tuple[list[Bar], list[Bar]] | None = None,
    ) -> PortfolioBacktestResult:
        """Run the event loop.

        ``prepared_inputs`` is an optimization hook: ``(ordered, decision_bars)``
        already deduplicated, timestamp/symbol-sorted and watermark-filtered.
        It is deterministic (dedup/sort are pure functions of the input), so
        reusing one preparation across identical trial runs yields identical
        results while skipping the per-trial sort of the full bar set.
        """
        # a restart resumes fills exactly where the interrupted run left off.
        restored_pending = list(self._pending)
        self._pending = []
        self._fills = []
        self._rebalances = []
        self._realized_pnl_events = []
        self._closed_position_events = []
        self._liquidation_costs = []
        self._liquidation_events = []
        self._dividend_total = 0.0
        self._cash_rate_observations = tuple(sorted(cash_rates or [], key=lambda item: item.timestamp))
        self._risk_contribution_history = []
        self._weight_history = []
        self._universe_history = []
        self._holdings_history = []
        self._pending.extend(restored_pending)
        if cash_rate_annualized is not None:
            self.cash_rate_annualized = float(cash_rate_annualized)
        if prepared_inputs is not None:
            ordered, decision_bars = prepared_inputs
        else:
            ordered = sorted(self._deduplicate(bars), key=lambda bar: (bar.timestamp, bar.symbol))
            if signal_bars is None:
                # The same bars decide and execute: one dedup/sort pass is
                # enough and the watermark filter below rebinds both names.
                decision_bars = ordered
            else:
                decision_bars = sorted(
                    self._deduplicate(signal_bars),
                    key=lambda bar: (bar.timestamp, bar.symbol),
                )
        watermark = self.state.last_processed_bar
        cutoff: datetime | None = None
        if watermark:
            cutoff = datetime.fromisoformat(watermark)
            ordered = [bar for bar in ordered if bar.timestamp > cutoff]
            decision_bars = [bar for bar in decision_bars if bar.timestamp > cutoff]
        # Warmup is already sorted and canonicalized in _prepared_window; avoid per-trial sort.
        for bar in warmup or []:
            self._ingest(bar)
            self._ingest_execution(bar)
        action_queue = _prepare_corporate_actions(corporate_actions, cutoff=cutoff)
        action_index = 0
        decision_index = 0
        curve: list[float] = []
        curve_timestamps: list[datetime] = []
        # Local aliases for hot loop (avoid attribute lookups per bar)
        _ingest_exec = self._ingest_execution
        _ingest = self._ingest
        _fill_cycle = self._fill_cycle
        _last_price = self._last_price
        _update_risk = self._update_risk_state
        _decide = self._decide
        _equity = self._equity
        _record_holdings = self._record_holdings_snapshot
        # Manual grouping to avoid groupby lambda overhead
        n = len(ordered)
        i = 0
        while i < n:
            ts = ordered[i].timestamp
            j = i + 1
            while j < n and ordered[j].timestamp == ts:
                j += 1
            cycle = ordered[i:j]
            self._fail_on_overdue_terminal_inventory(ts)
            for bar in cycle:
                _ingest_exec(bar)
            while decision_index < len(decision_bars) and decision_bars[decision_index].timestamp <= ts:
                _ingest(decision_bars[decision_index])
                decision_index += 1
            while action_index < len(action_queue) and action_queue[action_index].timestamp <= ts:
                self._apply_corporate_action(action_queue[action_index])
                action_index += 1
            _fill_cycle(cycle)
            for bar in cycle:
                _last_price[bar.symbol] = bar.close
            _update_risk(ts)
            _decide(ts)
            self._apply_terminal_lifecycles(ts, cycle)
            curve.append(_equity())
            curve_timestamps.append(ts)
            _record_holdings(ts)
            i = j
        if liquidate_at_end and self._positions:
            # Build last-bar map only for positions we actually need, scanning
            # ordered in reverse and stopping early. Original built dict of all
            # 428k bars per trial (8% of 3y run) just to liquidate 5 holdings.
            needed_symbols = {pos.symbol for pos in self._positions.values()}
            last_bar: dict[tuple[str, str], Bar] = {}
            # Timeframe is strategy timeframe for all positions
            tf = self.strategy.timeframe
            for bar in reversed(ordered):
                if bar.symbol in needed_symbols and (bar.symbol, tf) not in last_bar:
                    last_bar[(bar.symbol, tf)] = bar
                    if len(last_bar) == len(needed_symbols):
                        break
            for position in list(self._positions.values()):
                exit_bar = last_bar.get((position.symbol, position.timeframe))
                if exit_bar is not None:
                    self._close_position(position, exit_bar)
            if curve:
                # Liquidation occurs after the final observed bar.  Update
                # that bar's account value in place instead of serializing a
                # duplicate timestamp that cannot be ordered/replayed.
                curve[-1] = self._equity()
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
        benchmark = [bar for bar in decision_bars if bar.symbol == self.benchmark_symbol]
        benchmark_return = (
            benchmark[-1].close / benchmark[0].close - 1 if len(benchmark) > 1 and benchmark[0].close > 0 else 0.0
        )
        return self._result(curve, curve_timestamps, benchmark_return, tuple(benchmark))

    def run_dataset(
        self,
        dataset: MarketDataset,
        *,
        liquidate_at_end: bool = True,
        cash_rate_annualized: float | None = None,
    ) -> PortfolioBacktestResult:
        """Run a validated dual-series dataset through the normal event loop."""
        if dataset.market != self.strategy.market:
            raise ValueError("Dataset market does not match strategy market")
        if dataset.timeframe != self.strategy.timeframe:
            raise ValueError("Dataset timeframe does not match strategy timeframe")
        return self.run(
            list(dataset.executable_bars),
            liquidate_at_end=liquidate_at_end,
            cash_rate_annualized=cash_rate_annualized,
            signal_bars=list(dataset.signal_bars),
            corporate_actions=list(dataset.corporate_actions),
            cash_rates=list(dataset.cash_rates),
        )

    def save_state(self) -> None:
        if self.state_path is None:
            return
        atomic_write(
            self.state_path,
            json.dumps(self.state.to_dict(), indent=2, ensure_ascii=False, sort_keys=True),
        )

    @staticmethod
    def load_state(path: str | Path) -> PortfolioState | None:
        source = Path(path)
        if not source.exists():
            return None
        raw = json.loads(source.read_text(encoding="utf-8"))
        return PortfolioState.from_dict(raw if isinstance(raw, dict) else {})

    # -- internals ----------------------------------------------------------

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
        except (KeyError, ValueError) as exc:
            log.warning("Dropping malformed persisted pending order: %s", exc)
            return None

    def _ingest(self, bar: Bar) -> None:
        self._append_history(self._history, bar)

    def _ingest_execution(self, bar: Bar) -> None:
        self._append_history(self._execution_history, bar)

    def _append_history(self, target: dict[tuple[str, str], list[Bar]], bar: Bar) -> None:
        key = (bar.symbol, bar.timeframe)
        series = target.get(key)
        if series is None:
            target[key] = [bar]
        elif series[-1].timestamp < bar.timestamp:
            series.append(bar)

    def _apply_corporate_action(self, action: CorporateAction) -> None:
        symbol = canonical_symbol(action.symbol, self.strategy.market)
        position = self._positions.get(symbol)
        if position is None or position.qty <= 0:
            return
        if action.action_type == CorporateActionType.DIVIDEND:
            cash_credit = position.qty * action.value
            self._cash += cash_credit
            self._dividend_total += cash_credit
            return
        ratio = action.value
        position.qty *= ratio
        position.avg_price /= ratio
        for order in self._pending:
            if order.symbol == symbol:
                order.qty *= ratio

    @staticmethod
    def _deduplicate(bars: list[Bar]) -> list[Bar]:
        unique: dict[tuple[str, str, datetime], Bar] = {}
        for bar in bars:
            canonical = canonical_symbol(bar.symbol, bar.market)
            if canonical != bar.symbol:
                bar = Bar(
                    timestamp=bar.timestamp,
                    symbol=canonical,
                    timeframe=bar.timeframe,
                    open=bar.open,
                    high=bar.high,
                    low=bar.low,
                    close=bar.close,
                    volume=bar.volume,
                    market=bar.market,
                )
            key = (canonical, bar.timeframe, bar.timestamp)
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
                raise ValueError(f"Conflicting duplicate bars for {bar.symbol} at {bar.timestamp.isoformat()}")
        return list(unique.values())

    def _record_holdings_snapshot(self, timestamp: datetime) -> None:
        active = [position for position in self._positions.values() if position.opened_at <= timestamp]
        position_value = sum(
            position.qty * self._last_price.get(position.symbol, position.avg_price) for position in active
        )
        equity = self._equity()
        cash_ratio = max(0.0, (equity - position_value) / equity) if equity > 0 else 1.0
        single_values = [
            position.qty * self._last_price.get(position.symbol, position.avg_price) for position in active
        ]
        concentration = max(single_values) / equity if equity > 0 and single_values else 0.0
        self._holdings_history.append((len(active), cash_ratio, concentration))
        # Refresh the persisted snapshot fields after every completed bar
        # (fills included) so a restart resumes the exact allocation of the
        # last processed bar.
        self.state.pending_orders = tuple(self._order_to_dict(order) for order in self._pending)
        self.state.last_processed_bar = timestamp.isoformat()
        # Persist the daily-loss/high-water lifecycle alongside holdings/cash
        # at every completed cycle so a restart resumes the same risk state.
        self.state.peak_equity = self._peak_equity
        self.state.day_start_equity = self._day_start_equity
        self.state.last_observed_equity = self._last_observed_equity
        self.state.risk_session_date = (
            self._risk_session_date.isoformat() if self._risk_session_date is not None else None
        )
        if self.state_path is not None:
            # Only the persisted state needs the mirrored books; without a
            # state path _result() syncs them once before the result is read.
            self._sync_state_bookkeeping()
            self.save_state()

    def _sync_state_bookkeeping(self) -> None:
        """Mirror live positions, cash and prices into the persisted state."""
        self.state.holdings = {symbol: position.qty for symbol, position in self._positions.items() if position.qty > 0}
        self.state.avg_prices = {
            symbol: position.avg_price for symbol, position in self._positions.items() if position.qty > 0
        }
        self.state.entry_fees = {
            symbol: position.entry_fee for symbol, position in self._positions.items() if position.qty > 0
        }
        self.state.realized_pnl_accumulators = {
            symbol: position.realized_pnl_accumulator
            for symbol, position in self._positions.items()
            if position.qty > 0
        }
        self.state.cash = self._cash
        self.state.last_prices = dict(self._last_price)

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

    def _known_average_volume(self, order: PortfolioOrder, timeframe: str) -> float:
        """Average volume observable when the order was created.

        The fill bar has already been ingested when execution runs, so using
        ``bar.volume`` would leak the future session's completed volume into
        an opening fill. The cutoff makes the capacity model point-in-time.
        """
        history = self._execution_history.get((order.symbol, timeframe), [])
        # The history is timestamp-ascending, so the point-in-time cutoff is a
        # bisect and the window is walked backwards: no full-series scan (and
        # no per-bar volume filter) on every pending-order fill attempt.
        end = bisect_right(history, order.created_at, key=lambda bar: bar.timestamp)
        window: list[float] = []
        for index in range(end - 1, -1, -1):
            volume = history[index].volume
            if volume > 0:
                window.append(volume)
                if len(window) >= self.liquidity_lookback_bars:
                    break
        return sum(window) / len(window) if window else 0.0

    def _equity_fee(self, side: OrderSide, quantity: float, price: float, trade_date: date) -> float:
        """Regulatory fee for one equity fill at the trade date.

        With effective-dated fees the SEC/TAF/CAT rates follow the official
        schedule; otherwise the configured flat rates apply.
        """
        if self.equity_use_effective_dated_fees:
            rates = rates_for_trade_date(trade_date)
            fee = equity_regulatory_fee(
                side,
                quantity,
                price,
                taf_per_share=rates.taf_per_share,
                taf_cap=rates.taf_cap,
                cat_per_share=rates.cat_per_share,
                sec_fee_rate=rates.sec_fee_rate,
            )
        else:
            fee = equity_regulatory_fee(
                side,
                quantity,
                price,
                taf_per_share=self.equity_taf_per_share,
                taf_cap=self.equity_taf_cap,
                cat_per_share=self.equity_cat_per_share,
                sec_fee_rate=self.equity_sec_fee_rate,
            )
        return fee * self.equity_regulatory_fee_multiplier

    def _fill_side(self, bar: Bar, side: OrderSide) -> None:
        symbol = bar.symbol
        remaining: list[PortfolioOrder] = []
        for order in self._pending:
            if order.symbol != symbol or order.side != side:
                remaining.append(order)
                continue
            if order.created_at >= bar.timestamp:
                remaining.append(order)
                continue
            known_average_volume = self._known_average_volume(order, bar.timeframe)
            if known_average_volume <= 0:
                remaining.append(order)
                continue
            fillable = min(order.qty, known_average_volume * self.max_participation_pct)
            if side == OrderSide.SELL:
                position = self._positions.get(symbol)
                if position is None or position.qty <= 0:
                    continue
                # Costs (fee/slippage/spread) and the recorded fill must be
                # based on the quantity actually executed: a sell capped by
                # current holdings must not charge for unsold shares.
                fillable = min(fillable, position.qty)
            adverse = _adverse_bps(self.slippage_bps, self.spread_bps)
            side_multiplier = 1 if side == OrderSide.BUY else -1
            fill_price = bar.open * (1 + side_multiplier * adverse / 10_000)
            slippage_cost = fillable * bar.open * self.slippage_bps / 10_000
            spread_cost = fillable * bar.open * (self.spread_bps / 2) / 10_000
            fee = self.commission
            if order.market == Market.CRYPTO:
                fee += fillable * fill_price * self.crypto_taker_fee_bps / 10_000
            else:
                fee += self._equity_fee(side, fillable, fill_price, bar.timestamp.date())
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
                    position.avg_price = (position.qty * position.avg_price + fillable * fill_price) / total
                    position.qty = total
                    position.entry_fee += fee
            else:
                position = self._positions.get(symbol)
                assert position is not None  # guaranteed by the SELL gate above
                close_qty = fillable
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
                realized_delta = (fill_price - position.avg_price) * close_qty - fee - fee_share
                position.realized_pnl_accumulator += realized_delta
                self.state.realized_pnl += realized_delta
                position.qty -= close_qty
                self._realized_pnl_events.append((bar.timestamp, symbol, realized_delta))
                if position.qty <= 0:
                    self._closed_position_events.append((bar.timestamp, symbol, position.realized_pnl_accumulator))
                    self._positions.pop(symbol, None)
            self._fills.append(
                PortfolioFill(
                    timestamp=bar.timestamp,
                    symbol=symbol,
                    market=order.market,
                    side=side,
                    qty=fillable,
                    reference_price=bar.open,
                    fill_price=fill_price,
                    slippage_cost=slippage_cost,
                    spread_cost=spread_cost,
                    fee=fee,
                    decision_timestamp=order.decision_timestamp,
                    client_order_id=order.client_order_id,
                )
            )
            if fillable < order.qty:
                remaining.append(
                    PortfolioOrder(
                        order.client_order_id,
                        order.symbol,
                        order.market,
                        order.side,
                        order.qty - fillable,
                        bar.timestamp,
                        order.decision_timestamp,
                    )
                )
        self._pending = remaining

    def _decide(self, timestamp: datetime) -> None:
        market = self.strategy.market
        previous = self._previous_timestamp[market]
        self._previous_timestamp[market] = timestamp
        scheduled = self.rebalance_policy.is_period_start(previous, timestamp)
        # A non-scheduled bar can only produce a decision through an
        # intermediate risk rebalance. The base implementation ignores the
        # context and always returns False, so when the strategy does not
        # override it no context construction (per-symbol history copy) is
        # needed at all — this skips ~80% of bar cycles for weekly/96h
        # strategies. Overriding strategies keep the full per-bar path.
        if not scheduled and (
            type(self.strategy).requires_intermediate_risk_rebalance
            is PortfolioStrategy.requires_intermediate_risk_rebalance
        ):
            return
        context = self._build_context(timestamp)
        # Fail-closed product universe guard: a missing or version-less PIT
        # universe must halt the decision instead of silently treating every
        # symbol as in-universe via the permissive PortfolioContext.in_universe
        # fallback. Static/non-product test contexts explicitly opt in via
        # ``allow_static_universe=True`` on the engine or bypass the guard
        # when the strategy is not a PRODUCT strategy (synthetic strategies).
        # Product backtests run with the default fail-closed guard.
        if not self.allow_static_universe:
            # Only enforce for dynamic product strategies; synthetic
            # strategies (not in PRODUCT_STRATEGY_NAMES) retain permissive
            # behaviour without an explicit opt-in.
            if self.strategy.name in PRODUCT_STRATEGY_NAMES:
                require_product_universe(context)
            else:
                # Non-product synthetic strategy: allow permissive universe
                # but still validate version if universe is present.
                if context.universe is not None:
                    require_product_universe(context, allow_static_context=False)
        else:
            require_product_universe(context, allow_static_context=True)
        if not self.strategy.supports(context):
            return
        # LIVE PARITY completeness gate: decisions require a closed bar for
        # every STREAMED OBSERVATION symbol (what the live runtime streams),
        # not for every pool member. A halted/absent pool member must not
        # block the whole market (the strategy skips gapped names internally);
        # snapshots without an observation set fall back to the full pool.
        expected_symbols = self._effective_observation_symbols(timestamp)
        if expected_symbols is not None and any(
            symbol not in context.history
            or not context.history[symbol]
            or context.history[symbol][-1].timestamp != timestamp
            for symbol in expected_symbols
        ):
            log.warning(
                "Historical strategy batch incomplete; decision skipped: timestamp=%s",
                timestamp.isoformat(),
            )
            return
        if not scheduled and not self.strategy.requires_intermediate_risk_rebalance(context):
            return
        target = self.strategy.decide(context)
        if not scheduled:
            current_weights = {
                position.symbol: (
                    position.qty * self._last_price.get(position.symbol, position.avg_price) / max(context.equity, 1.0)
                )
                for position in self._positions.values()
            }
            reducing_weights = tuple(
                TargetWeight(
                    weight.symbol,
                    min(weight.weight, current_weights.get(weight.symbol, 0.0)),
                    "intermediate_risk_reduction_only",
                    rank=weight.rank,
                )
                for weight in target.weights
                if min(weight.weight, current_weights.get(weight.symbol, 0.0)) > 0
            )
            target = replace(
                target,
                weights=reducing_weights,
                cash_weight=1.0 - sum(weight.weight for weight in reducing_weights),
                metadata={**target.metadata, "intermediate_risk_reduction": True},
            )
        if self.risk_overlay is not None:
            risk = self.risk_overlay.apply(
                target,
                self._account_snapshot(),
                market=self.strategy.market,
                approved_symbols=tuple(context.history),
                maximum_allocation=self.maximum_allocation,
                mode="backtest",
                # Offline backtests record drawdown breaches as shadow
                # events instead of halting: the peak is monotonic, so an
                # enforced halt would freeze the alpha curve permanently.
                # Production-policy simulations set the ctor flag to enforce.
                enforce_drawdown_halt=self.enforce_drawdown_halt,
            )
            target = risk.target
        self._rebalance_number += 1
        self._apply_target(target)
        self._record_rebalance(target)

    def _update_risk_state(self, timestamp: datetime) -> None:
        """Advance the daily-loss/high-water lifecycle for one processed bar.

        Called for every bar cycle (not only when a strategy rebalances) so
        the peak equity and the session-day start equity track the portfolio
        between scheduled decisions. When the trading-session date rolls over,
        the day-start equity becomes the last equity observed in the previous
        session — that is the previous session's closing equity, never the
        current post-close equity.
        """
        equity = self._equity()
        session = session_date_for(timestamp, self.strategy.market)
        if self._risk_session_date != session:
            self._day_start_equity = self._last_observed_equity
            self._risk_session_date = session
        self._last_observed_equity = equity
        self._peak_equity = max(self._peak_equity, equity)

    def _account_snapshot(self) -> AccountSnapshot:
        """Observational snapshot of the portfolio for the risk overlay.

        This never mutates risk state: daily-loss and high-water bookkeeping
        live in the explicit ``_update_risk_state`` lifecycle, which runs on
        every bar regardless of whether a decision happens here.
        """
        equity = self._equity()
        positions = tuple(
            PositionSnapshot(
                symbol=position.symbol,
                qty=position.qty,
                avg_price=position.avg_price,
                current_price=self._last_price.get(position.symbol, position.avg_price),
                market=position.market,
                qty_available=position.qty,
                ownership="managed",
                managed_qty=position.qty,
            )
            for position in self._positions.values()
        )
        return AccountSnapshot(
            cash=self._cash,
            equity=equity,
            peak_equity=self._peak_equity,
            daily_pnl=equity - self._day_start_equity,
            positions=positions,
            realized_pnl=self.state.realized_pnl,
            unrealized_pnl=sum(position.unrealized_pnl for position in positions),
            fees=sum(fill.fee for fill in self._fills),
        )

    def _build_context(self, timestamp: datetime) -> PortfolioContext:
        # Strategies read only trailing windows of each symbol's history (the
        # largest in-tree read is the equity beta window + 1 = 274 bars), so
        # the per-symbol context history is trimmed to a safe upper bound of
        # the strategy's own declared minimum. This removes the O(B) full
        # history copy per bar cycle (O(B^2) over the run) without changing
        # any value a strategy can observe: membership keys, the last bar,
        # and every trailing window are preserved, and a symbol with less
        # history than its window keeps its true (shorter) length.
        trim = max(self.strategy.minimum_history_bars, _MAX_CONTEXT_HISTORY_BARS)
        # The market/benchmark series is exempt from trimming: strategies
        # timestamp-INNER-JOIN stock windows against it, and a stock with
        # recent data holes spans more calendar time than the benchmark's
        # trailing window, so the benchmark dict must stay full to preserve
        # every aligned pair (and therefore the exact scores).
        benchmark_key = "BTC/USD" if self.strategy.market == Market.CRYPTO else "SPY"
        visible_symbols = self._effective_observation_symbols(timestamp)
        visible = set(visible_symbols) if visible_symbols is not None else None
        history: dict[str, tuple[Bar, ...]] = {}
        prices: dict[str, float] = {}
        for (symbol, _timeframe), bars in self._history.items():
            if not bars:
                continue
            if visible is not None and symbol not in visible:
                continue
            # FAST PATH: _history lists are maintained timestamp-ascending and,
            # during a backtest, all ingested bars are <= decision timestamp
            # (we ingest in global timestamp order and decide immediately after
            # the batch). The original list-comprehension `eligible = [b for b
            # in bars if b.timestamp <= timestamp]` therefore almost always
            # copies the whole list only to trim it. Avoid the O(B) scan by
            # checking the tail and slicing directly. Fallback to a reversed
            # scan handles the rare out-of-order or future-bar case without
            # changing observable behavior.
            eligible: list[Bar] | None = None
            # Common case: bars are sorted and tail <= timestamp -> all eligible
            if bars[-1].timestamp <= timestamp:
                # All bars are eligible; apply trim without scanning.
                if symbol != benchmark_key and len(bars) > trim:
                    eligible = bars[-trim:]
                else:
                    eligible = bars
            else:
                # Rare fallback: bars not sorted or contain future timestamps.
                # Collect eligible bars by scanning. For non-benchmark we only
                # need the last `trim` eligible bars, so scan backwards and
                # stop early; for benchmark we need all eligible.
                if symbol != benchmark_key:
                    collected: list[Bar] = []
                    for bar in reversed(bars):
                        if bar.timestamp <= timestamp:
                            collected.append(bar)
                            if len(collected) >= trim:
                                break
                    if not collected:
                        continue
                    collected.reverse()
                    eligible = collected
                else:
                    # Benchmark: linear forward scan with early break (sorted)
                    # Fall back to full comprehension if not sorted.
                    # Check sortedness cheaply by tail check failure already;
                    # just do comprehension as safe fallback.
                    filtered = [bar for bar in bars if bar.timestamp <= timestamp]
                    if not filtered:
                        continue
                    eligible = filtered
            if not eligible:
                continue
            history[symbol] = tuple(eligible)
            # Target sizing uses executable/raw prices; signal history may be
            # total-return adjusted and is intentionally not a cash price.
            prices[symbol] = self._last_price.get(symbol, eligible[-1].close)
        holdings = {symbol: position.qty for symbol, position in self._positions.items() if position.qty > 0}
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
            if delta == 0.0:
                # A zero turnover_band lets an already-on-target symbol past
                # the band test; it must not become a zero-quantity order
                # (and a commission charged for no shares).
                continue
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
        target_map = {canonical_symbol(weight.symbol, self.strategy.market): weight.weight for weight in target.weights}
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
        raw_reasons = target.metadata.get("risk_overlay_reasons", ())
        overlay_reasons = tuple(str(item) for item in raw_reasons) if isinstance(raw_reasons, (tuple, list)) else ()
        self._rebalances.append(
            RebalanceRecord(
                decision_timestamp=decision,
                reason=target.rebalance_reason.value,
                target_weights=tuple(sorted((weight.symbol, weight.weight) for weight in target.weights)),
                target_cash_weight=target.cash_weight,
                pre_trade_weights=tuple(sorted(self._current_pre_trade_weights.items())),
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
                risk_overlay_halted=bool(target.metadata.get("risk_overlay_halted", False)),
                risk_overlay_reasons=overlay_reasons,
            )
        )
        self._weight_history.append((decision, {weight.symbol: weight.weight for weight in target.weights}))
        self._record_risk_contribution(target)
        symbols = self._universe_symbols(decision)
        if symbols is not None:
            self._universe_history.append((decision, symbols))
        self.state.last_target = {weight.symbol: weight.weight for weight in target.weights}
        self.state.last_rebalance_at = decision
        self.state.rebalance_count = self._rebalance_number
        self.state.universe_version = target.universe_version or self.universe_version
        if self.state_path is not None:
            self.save_state()

    def _record_risk_contribution(self, target: TargetPortfolio) -> None:
        contributions: list[float] = []
        for weight in target.weights:
            symbol = canonical_symbol(weight.symbol, self.strategy.market)
            bars = self._history.get((symbol, self.strategy.timeframe), ())
            closes = [bar.close for bar in bars[-61:] if bar.close > 0]
            returns = [
                closes[index] / closes[index - 1] - 1 for index in range(1, len(closes)) if closes[index - 1] > 0
            ]
            volatility = sample_stddev(returns)
            contributions.append(abs(weight.weight) * volatility)
        total = sum(contributions)
        if total > 0:
            self._risk_contribution_history.append(max(contributions) / total)
        elif target.weights:
            self._risk_contribution_history.append(max(weight.weight for weight in target.weights))

    def _universe_symbols(self, timestamp: datetime) -> tuple[str, ...] | None:
        snapshots = getattr(self.universe, "snapshots", ())
        if not snapshots:
            return None
        index = bisect_right(snapshots, timestamp, key=lambda item: item.effective_at)
        if index == 0:
            return None
        return tuple(sorted(snapshots[index - 1].symbols))

    def _effective_observation_symbols(self, timestamp: datetime) -> tuple[str, ...] | None:
        """Return the runtime-sized symbol context for this decision.

        Product snapshots carry the ordered pre-session observation set and
        its stream capacity. References plus managed positions/open orders
        are reserved first, then the current observation membership fills the
        remaining slots. Pins affect observability only; PIT eligibility is
        still owned by ``PointInTimeUniverse.contains`` so a removed holding
        can be marked and exited without becoming an alpha candidate.

        Legacy snapshots without an explicit capacity retain the historical
        full-context behavior; their completeness gate still falls back to
        the snapshot observation/full pool below.
        """
        snapshots = getattr(self.universe, "snapshots", ())
        if not snapshots:
            return None
        index = bisect_right(snapshots, timestamp, key=lambda item: item.effective_at)
        if index == 0:
            return None
        snapshot = snapshots[index - 1]

        def is_active(symbol: str) -> bool:
            terminal = self.terminal_lifecycles.get(symbol)
            if terminal is None:
                return True
            return terminal.last_bar >= timestamp

        observation = tuple(symbol for symbol in (getattr(snapshot, "observation_set", ()) or ()) if is_active(symbol))
        capacity = int(getattr(snapshot, "maximum_stream_symbols", 0) or 0)
        if capacity <= 0:
            if observation:
                return tuple(sorted(observation))
            return tuple(sorted(symbol for symbol in snapshot.symbols if is_active(symbol)))

        references = tuple(symbol for symbol in (getattr(snapshot, "pinned_symbols", ()) or ()) if is_active(symbol))
        managed = tuple(
            dict.fromkeys(
                (
                    *(symbol for symbol, position in self._positions.items() if position.qty > 0 and is_active(symbol)),
                    *(order.symbol for order in self._pending if is_active(order.symbol)),
                )
            )
        )
        reserved = tuple(dict.fromkeys((*references, *managed)))
        if len(reserved) > capacity:
            raise RuntimeError("Managed pins and references exceed the historical stream capacity")
        base = observation or tuple(sorted(symbol for symbol in snapshot.symbols if is_active(symbol)))
        # An optional PIT lifecycle map can identify inactive entities. Once
        # an entity has reached its final observed
        # bar, the live broker would no longer report it as an active
        # stream member; retaining it in the historical completeness gate
        # would wedge every later decision on a bar that can never arrive.
        # Keep the symbol on its terminal bar (the caller may close a held
        # position after that cycle), then remove it for later cycles.
        # Equity runtime stores active membership as a set and restores it in
        # deterministic symbol order when managed pins are applied. Crypto's
        # observation ordering remains configuration/alpha ordered.
        tail = tuple(sorted(base)) if self.strategy.market == Market.EQUITY and managed else base
        return tuple(dict.fromkeys((*reserved, *tail)))[:capacity]

    def _validated_terminal_lifecycles(
        self,
        lifecycles: Mapping[str, TerminalLifecycle] | None,
    ) -> dict[str, TerminalLifecycle]:
        if lifecycles is not None and not isinstance(lifecycles, Mapping):
            raise TypeError("terminal_lifecycles must be a mapping")
        validated: dict[str, TerminalLifecycle] = {}
        for raw_symbol, lifecycle in (lifecycles or {}).items():
            if not isinstance(lifecycle, TerminalLifecycle):
                raise TypeError("terminal_lifecycles values must be TerminalLifecycle instances")
            if not isinstance(lifecycle.last_bar, datetime):
                raise TypeError("Terminal lifecycle last_bar must be a datetime")
            if lifecycle.last_bar.tzinfo is None or lifecycle.last_bar.utcoffset() is None:
                raise ValueError("Terminal lifecycle timestamps must be timezone-aware")
            if not isinstance(lifecycle.event_class, str):
                raise TypeError("Terminal lifecycle event_class must be a string")
            if not lifecycle.event_class.strip():
                raise ValueError("Terminal lifecycle event_class must not be empty")
            symbol = canonical_symbol(raw_symbol, self.strategy.market)
            if symbol in validated:
                raise ValueError(f"Duplicate terminal lifecycle for canonical symbol {symbol}")
            validated[symbol] = lifecycle
        return validated

    def _fail_on_overdue_terminal_inventory(self, timestamp: datetime) -> None:
        managed = {symbol for symbol, position in self._positions.items() if position.qty > 0}
        managed.update(order.symbol for order in self._pending)
        overdue = sorted(
            symbol
            for symbol in managed
            if symbol in self.terminal_lifecycles and self.terminal_lifecycles[symbol].last_bar < timestamp
        )
        if overdue:
            raise RuntimeError("Terminal lifecycle bar was not processed for managed inventory: " + ", ".join(overdue))

    def _apply_terminal_lifecycles(self, timestamp: datetime, cycle: list[Bar]) -> None:
        due = {
            symbol: lifecycle
            for symbol, lifecycle in self.terminal_lifecycles.items()
            if lifecycle.last_bar == timestamp
        }
        if not due:
            return
        self._pending = [order for order in self._pending if order.symbol not in due]
        bars = {
            canonical_symbol(bar.symbol, bar.market): bar
            for bar in cycle
            if bar.market == self.strategy.market and bar.timeframe == self.strategy.timeframe
        }
        for position in list(self._positions.values()):
            lifecycle = due.get(position.symbol)
            if lifecycle is None or position.qty <= 0:
                continue
            bar = bars.get(position.symbol)
            if bar is None:
                raise RuntimeError(
                    f"Terminal lifecycle requires a real final execution bar: {position.symbol} {timestamp.isoformat()}"
                )
            self._close_position(position, bar)
            event = self._liquidation_events[-1]
            event["policy"] = "TERMINAL_BAR"
            event["effective_policy"] = "TERMINAL_BAR"
            event["terminal_event_class"] = lifecycle.event_class

    def _close_position(self, position: _Position, bar: Bar) -> None:
        if position.qty <= 0:
            return
        adverse = _adverse_bps(self.slippage_bps, self.spread_bps)
        exit_price = bar.close * (1 - adverse / 10_000)
        fee = self.commission
        if position.market == Market.CRYPTO:
            fee += position.qty * exit_price * self.crypto_taker_fee_bps / 10_000
        else:
            fee += self._equity_fee(OrderSide.SELL, position.qty, exit_price, bar.timestamp.date())
        realized_delta = (exit_price - position.avg_price) * position.qty - position.entry_fee - fee
        lifetime_realized = position.realized_pnl_accumulator + realized_delta
        self._cash += position.qty * exit_price - fee
        self._realized_pnl_events.append((bar.timestamp, position.symbol, realized_delta))
        self._closed_position_events.append((bar.timestamp, position.symbol, lifetime_realized))
        self.state.realized_pnl += realized_delta
        # Liquidation costs are real but not decision-driven: fold them into
        # the cost/fee totals instead of recording a synthetic fill (which
        # would violate the fills-strictly-after-decisions invariant).
        self._liquidation_costs.append(
            (
                position.symbol,
                position.qty * bar.close * self.slippage_bps / 10_000,
                position.qty * bar.close * (self.spread_bps / 2) / 10_000,
                fee,
            )
        )
        self._liquidation_events.append(
            {
                "symbol": position.symbol,
                "timestamp": bar.timestamp.isoformat(),
                "quantity": position.qty,
                "policy": "FINAL_BAR",
                "effective_policy": "FINAL_BAR",
                "last_close": bar.close,
                "fill_price": exit_price,
                "proceeds": max(0.0, position.qty * exit_price - fee),
                "fee": fee,
                "slippage_cost": position.qty * bar.close * self.slippage_bps / 10_000,
                "spread_cost": position.qty * bar.close * (self.spread_bps / 2) / 10_000,
                "cash_before": self._cash - (position.qty * exit_price - fee),
                "cash_after": self._cash,
                "position_removed": True,
            }
        )
        self._positions.pop(position.symbol, None)

    def _cash_hurdle_return(self, timestamps: list[datetime]) -> float:
        if len(timestamps) < 2:
            return 0.0
        observations = self._cash_rate_observations
        if observations and observations[0].timestamp > timestamps[0]:
            raise ValueError("Cash-rate series does not cover the backtest start")
        value = 1.0
        index = 0
        active_rate = self.cash_rate_annualized
        # Adjacent-pair iteration on purpose: each accrual interval spans
        # previous->current, so one fewer pair than timestamps is expected.
        for previous, current in pairwise(timestamps):
            while index < len(observations) and observations[index].timestamp <= previous:
                active_rate = observations[index].annualized_rate
                index += 1
            years = max(0.0, (current - previous).total_seconds()) / (365.25 * 86400)
            value *= (1.0 + active_rate) ** years
        return value - 1.0

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
                    duration_days = (curve_timestamps[index] - curve_timestamps[peak_index]).total_seconds() / 86_400
                    max_drawdown_duration_days = max(max_drawdown_duration_days, int(duration_days))
        returns = [curve[i] / curve[i - 1] - 1 for i in range(1, len(curve)) if curve[i - 1] > 0]
        downside = [value for value in returns if value < 0]
        mean_return = sum(returns) / len(returns) if returns else 0.0
        std = sample_stddev(returns)
        downside_std = sample_stddev(downside)
        start_ts = curve_timestamps[0] if curve_timestamps else None
        end_ts = curve_timestamps[-1] if curve_timestamps else None
        duration_days = int((end_ts - start_ts).total_seconds() / 86_400) if start_ts and end_ts else 0
        years = duration_days / 365.0
        total_return = ending / self.starting_equity - 1
        cagr = ((ending / self.starting_equity) ** (1 / years) - 1) if years > 0 and ending > 0 else 0.0
        periods_per_year = len(returns) / years if years > 0 else 0.0
        annualizer = math.sqrt(max(1.0, periods_per_year)) if returns else 1.0
        pnls = tuple(event for _, _, event in self._realized_pnl_events)
        fills = tuple(self._fills)
        rebalances = tuple(self._enrich_rebalances())
        slippage_total = sum(fill.slippage_cost for fill in fills) + sum(
            slippage for _symbol, slippage, _spread, _fee in self._liquidation_costs
        )
        spread_total = sum(fill.spread_cost for fill in fills) + sum(
            spread for _symbol, _slippage, spread, _fee in self._liquidation_costs
        )
        fee_total = sum(fill.fee for fill in fills) + sum(
            fee for _symbol, _slippage, _spread, fee in self._liquidation_costs
        )
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
            sum(1 for value in monthly_values if value > 0) / len(monthly_values) if monthly_values else 0.0
        )
        per_symbol = self._symbol_contributions()
        # Excess returns are aligned BY DATE: each series is reduced to
        # close-to-previous-trading-day returns keyed by date, so intraday
        # runs (e.g. 1h crypto) never compare hourly portfolio moves against
        # a return that pretends to be daily. Days missing from one series
        # drop out of the excess series instead of misaligning it.
        benchmark_closes_by_day: dict[tuple[int, int, int], float] = {}
        for bar in benchmark_bars:
            day = (bar.timestamp.year, bar.timestamp.month, bar.timestamp.day)
            benchmark_closes_by_day[day] = bar.close
        portfolio_by_day: dict[tuple[int, int, int], float] = {}
        for ts, value in zip(curve_timestamps, curve, strict=True):
            portfolio_by_day[(ts.year, ts.month, ts.day)] = value
        excess: list[float] = []
        benchmark_days = sorted(benchmark_closes_by_day)
        for index, day in enumerate(benchmark_days):
            if index == 0 or day not in portfolio_by_day:
                continue
            previous_day = benchmark_days[index - 1]
            previous_portfolio = portfolio_by_day.get(previous_day)
            if (
                benchmark_closes_by_day[day] <= 0
                or benchmark_closes_by_day[previous_day] <= 0
                or previous_portfolio is None
                or portfolio_by_day[day] <= 0
            ):
                continue
            daily_benchmark_return = benchmark_closes_by_day[day] / benchmark_closes_by_day[previous_day] - 1
            portfolio_return = portfolio_by_day[day] / previous_portfolio - 1
            excess.append(portfolio_return - daily_benchmark_return)
        excess_mean = sum(excess) / len(excess) if excess else 0.0
        excess_std = sample_stddev(excess)
        daily_periods_per_year = len(excess) / years if years > 0 else 0.0
        information_ratio = excess_mean / excess_std * math.sqrt(daily_periods_per_year) if excess_std > 0 else 0.0
        avg_cash_ratio = sum(cash_ratios) / len(cash_ratios) if cash_ratios else 0.0
        cash_hurdle_return = self._cash_hurdle_return(curve_timestamps)
        cash_yield = avg_cash_ratio * cash_hurdle_return
        benchmark_returns = [
            benchmark_bars[index].close / benchmark_bars[index - 1].close - 1
            for index in range(1, len(benchmark_bars))
            if benchmark_bars[index - 1].close > 0
        ]
        benchmark_mean = sum(benchmark_returns) / len(benchmark_returns) if benchmark_returns else 0.0
        benchmark_std = sample_stddev(benchmark_returns)
        benchmark_periods = len(benchmark_returns) / years if years > 0 else 0.0
        benchmark_sharpe = (
            benchmark_mean / benchmark_std * math.sqrt(benchmark_periods)
            if benchmark_std > 0 and benchmark_periods > 0
            else 0.0
        )
        benchmark_peak = benchmark_bars[0].close if benchmark_bars else 0.0
        benchmark_max_drawdown = 0.0
        for bar in benchmark_bars:
            benchmark_peak = max(benchmark_peak, bar.close)
            if benchmark_peak > 0:
                benchmark_max_drawdown = max(
                    benchmark_max_drawdown,
                    (benchmark_peak - bar.close) / benchmark_peak,
                )
        benchmark_cagr = (1 + benchmark_return) ** (1 / years) - 1 if years > 0 and benchmark_return > -1 else 0.0
        benchmark_calmar = benchmark_cagr / benchmark_max_drawdown if benchmark_max_drawdown > 0 else 0.0
        self._sync_state_bookkeeping()
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
            closed_position_events=tuple(self._closed_position_events),
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
            average_holdings_count=(sum(holdings_counts) / len(holdings_counts) if holdings_counts else 0.0),
            average_cash_ratio=avg_cash_ratio,
            rebalance_count=len(self._rebalances),
            closed_positions=len(self._closed_position_events),
            per_symbol=per_symbol,
            maximum_concentration=max(concentration, default=0.0),
            worst_month_return=worst_month,
            worst_year_return=worst_year,
            positive_month_ratio=positive_month_ratio,
            state=self.state,
            terminal_events=tuple(dict(event) for event in self._liquidation_events),
            information_ratio=information_ratio,
            cash_yield=cash_yield,
            dividend_total=self._dividend_total,
            cash_hurdle_return=cash_hurdle_return,
            benchmark_sharpe=benchmark_sharpe,
            benchmark_max_drawdown=benchmark_max_drawdown,
            benchmark_calmar=benchmark_calmar,
            maximum_risk_contribution=max(self._risk_contribution_history, default=0.0),
            drawdown_halt_shadow_events=tuple(
                (record.decision_timestamp, "portfolio_drawdown_halt")
                for record in rebalances
                if "portfolio_drawdown_halt" in record.risk_overlay_reasons
            ),
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
                signed_notional[fill.symbol] = signed_notional.get(fill.symbol, 0.0) + sign * fill.qty * fill.fill_price
                executed[fill.symbol] = executed.get(fill.symbol, 0.0) + fill.qty * fill.fill_price
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
                abs(target_weight - pre_trade.get(symbol, 0.0)) * equity for symbol, target_weight in target_map.items()
            )
            sold = {symbol for symbol in signed_notional if symbol not in target_map}
            expected_notional += sum(abs(pre_trade.get(symbol, 0.0)) * equity for symbol in sold)
            realized_notional = sum(abs(value) for value in signed_notional.values())
            executed_weights = tuple(
                sorted(((symbol, value / equity) for symbol, value in executed.items() if equity > 0))
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
                    risk_overlay_halted=record.risk_overlay_halted,
                    risk_overlay_reasons=record.risk_overlay_reasons,
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
        year_returns: list[float] = []
        previous = self.starting_equity
        for year in sorted(by_year):
            indices = by_year[year]
            value = curve[indices[-1]]
            year_returns.append(value / previous - 1 if previous > 0 else 0.0)
            previous = value
        return min(year_returns, default=0.0)

    def _symbol_contributions(self) -> tuple[SymbolContribution, ...]:
        fee_by_symbol: dict[str, float] = {}
        for fill in self._fills:
            fee_by_symbol[fill.symbol] = fee_by_symbol.get(fill.symbol, 0.0) + fill.fee
        for symbol, _slippage, _spread, fee in self._liquidation_costs:
            fee_by_symbol[symbol] = fee_by_symbol.get(symbol, 0.0) + fee
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
                final_weight = position.qty * self._last_price.get(symbol, position.avg_price) / final_equity
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


__all__ = [
    "OrderSide",
    "PortfolioBacktestEngine",
    "PortfolioBacktestResult",
    "PortfolioFill",
    "RebalanceRecord",
    "SymbolContribution",
    "TerminalLifecycle",
]
