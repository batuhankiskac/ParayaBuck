from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field, replace
from datetime import date, datetime

from parayabuck.backtest.trades import (
    EXIT_BREAK_EVEN,
    EXIT_END_OF_TEST,
    EXIT_STOP,
    EXIT_TARGET,
    EXIT_TIME,
    EXIT_TRAILING_STOP,
    TradeRecord,
)
from parayabuck.core.models import (
    AccountSnapshot,
    Bar,
    Direction,
    ExecutionEvent,
    ExitPolicy,
    Fill,
    Market,
    OrderIntent,
    OrderRole,
    OrderState,
    PositionSnapshot,
    Regime,
    TrailingStopType,
)
from parayabuck.features.indicators import atr
from parayabuck.markets.timeframes import timeframe_duration


@dataclass
class _Position:
    symbol: str
    qty: float
    avg_price: float
    current_price: float
    market: Market
    stop: float
    target: float
    client_order_id: str
    direction: Direction
    entry_fee: float
    strategy: str = "unknown"
    entry_timestamp: datetime | None = None
    strategy_version: str = "1.0.0"
    exit_policy: ExitPolicy = field(default_factory=ExitPolicy)
    volatility_at_entry: float | None = None
    entry_score: float = 0.0
    normalized_expected_r: float | None = None
    entry_regime: Regime = Regime.UNKNOWN
    initial_stop: float | None = None
    initial_risk: float = 0.0
    highest_high: float | None = None
    lowest_low: float | None = None
    bars_held: int = 0
    pending_stop: float | None = None
    pending_stop_reason: str = EXIT_STOP
    active_stop_reason: str = EXIT_STOP
    pending_time_exit: bool = False
    max_favorable: float = 0.0
    max_adverse: float = 0.0
    stop_triggered: bool = False
    exit_reason: str = ""
    _acc_pnl: float = 0.0
    _acc_fees: float = 0.0
    _acc_exit_qty: float = 0.0
    _exit_value: float = 0.0

    def augment(self, qty: float, price: float, fee: float) -> None:
        """Merge a partial entry fill into an existing position from the same order."""
        total_qty = self.qty + qty
        self.avg_price = (self.qty * self.avg_price + qty * price) / total_qty
        self.qty = total_qty
        self.entry_fee += fee
        self.initial_risk = abs(self.avg_price - self.stop)


class BacktestBroker:
    """Next-bar execution simulator with commission, slippage and protective exits.

    Fills are volume-aware: an order may only consume up to a participation
    ratio of the bar's volume, so illiquid bars produce partial fills and the
    remainder stays pending for the next bar. Protective exits obey the same
    constraint, which keeps low-liquidity results from looking optimistic.
    """

    def __init__(
        self,
        starting_cash: float,
        slippage_bps: float = 2.0,
        commission: float = 0.0,
        *,
        spread_bps: float = 0.0,
        allow_short: bool = False,
        intrabar_policy: str = "conservative",
        crypto_taker_fee_bps: float = 0.0,
        max_entry_participation_pct: float = 0.10,
        max_exit_participation_pct: float = 0.10,
    ) -> None:
        self.cash = starting_cash
        self.starting_cash = starting_cash
        self.slippage_bps = slippage_bps
        self.commission = commission
        self.spread_bps = spread_bps
        self.allow_short = allow_short
        if intrabar_policy not in {"conservative", "optimistic", "ohlc_path"}:
            raise ValueError("Unsupported intrabar policy")
        self.intrabar_policy = intrabar_policy
        self.crypto_taker_fee_bps = crypto_taker_fee_bps
        if not 0 < max_entry_participation_pct <= 1 or not 0 < max_exit_participation_pct <= 1:
            raise ValueError("Participation ratios must be in (0, 1]")
        self.max_entry_participation_pct = max_entry_participation_pct
        self.max_exit_participation_pct = max_exit_participation_pct
        self.peak_equity = starting_cash
        self._day_start_equity = starting_cash
        self._current_date: date | None = None
        self.positions: dict[str, _Position] = {}
        self.pending: list[OrderIntent] = []
        self.fills: list[Fill] = []
        self.realized_pnls: list[float] = []
        self.trades: list[TradeRecord] = []
        self.order_rejections: dict[str, str] = {}
        self.execution_events: list[ExecutionEvent] = []
        self._entry_requested_qty: dict[str, float] = {}
        self._entry_cumulative_fill: dict[str, float] = {}
        self._entry_fill_value: dict[str, float] = {}
        self._bar_history: dict[str, deque[Bar]] = defaultdict(lambda: deque(maxlen=128))

    def account(self) -> AccountSnapshot:
        snapshots = tuple(
            PositionSnapshot(
                p.symbol,
                p.qty if p.direction == Direction.LONG else -p.qty,
                p.avg_price,
                p.current_price,
                p.market,
                ownership="managed",
                stop_price=p.stop,
                initial_risk=p.initial_risk,
                bars_held=p.bars_held,
                trailing_stop=(
                    p.stop if p.active_stop_reason == EXIT_TRAILING_STOP else None
                ),
                highest_high=p.highest_high,
                lowest_low=p.lowest_low,
                pending_trailing_stop=p.pending_stop,
            )
            for p in self.positions.values()
        )
        equity = self.cash + sum(
            p.qty * p.current_price * (1 if p.direction == Direction.LONG else -1)
            for p in self.positions.values()
        )
        self.peak_equity = max(self.peak_equity, equity)
        return AccountSnapshot(
            cash=self.cash,
            equity=equity,
            peak_equity=self.peak_equity,
            daily_pnl=equity - self._day_start_equity,
            positions=snapshots,
        )

    def submit(self, order: OrderIntent) -> tuple[OrderState, Fill | None]:
        if order.direction == Direction.SHORT and not self.allow_short:
            raise ValueError("Short execution is disabled in this backtest")
        if any(item.client_order_id == order.client_order_id for item in self.pending):
            return OrderState.ACCEPTED, None
        self.pending.append(order)
        self._entry_requested_qty[order.client_order_id] = order.qty
        self._entry_cumulative_fill[order.client_order_id] = 0.0
        self._entry_fill_value[order.client_order_id] = 0.0
        self.execution_events.append(
            ExecutionEvent(
                account_id="backtest",
                client_order_id=order.client_order_id,
                broker_order_id=order.client_order_id,
                parent_order_id=None,
                role=OrderRole.ENTRY,
                symbol=order.symbol,
                market=order.market,
                state=OrderState.ACCEPTED,
                requested_qty=order.qty,
                cumulative_filled_qty=0.0,
                average_fill_price=None,
                occurred_at=order.created_at,
            )
        )
        return OrderState.ACCEPTED, None

    def reconcile(self) -> None:
        return

    def advance(self, bar: Bar) -> None:
        if self._current_date != bar.timestamp.date():
            self._current_date = bar.timestamp.date()
            self._day_start_equity = self.account().equity
        self._bar_history[bar.symbol].append(bar)

        # 1. Fill eligible entries at the open.
        remaining: list[OrderIntent] = []
        for order in self.pending:
            if order.symbol != bar.symbol or order.created_at >= bar.timestamp:
                remaining.append(order)
                continue
            if order.symbol in self.positions and (
                self.positions[order.symbol].client_order_id != order.client_order_id
            ):
                self.order_rejections[order.client_order_id] = "existing_position"
                self._entry_terminal_event(order, OrderState.REJECTED, bar)
                continue
            expected_seconds = timeframe_duration(bar.timeframe).total_seconds()
            if (bar.timestamp - order.created_at).total_seconds() > expected_seconds * 1.5:
                self.order_rejections[order.client_order_id] = "stale_pending_order"
                self._entry_terminal_event(order, OrderState.EXPIRED, bar)
                continue
            fillable = self._fillable_qty(order.qty, bar, self.max_entry_participation_pct)
            if fillable <= 0:
                remaining.append(order)
                continue
            adverse_bps = self.slippage_bps + self.spread_bps / 2
            side_multiplier = 1 if order.direction == Direction.LONG else -1
            fill_price = bar.open * (1 + side_multiplier * adverse_bps / 10_000)
            notional = fillable * fill_price
            entry_fee = self.commission + (
                notional * self.crypto_taker_fee_bps / 10_000
                if order.market == Market.CRYPTO
                else 0.0
            )
            if order.direction == Direction.LONG and notional + entry_fee > self.cash:
                self.order_rejections[order.client_order_id] = "insufficient_cash"
                self._entry_terminal_event(order, OrderState.REJECTED, bar)
                continue
            existing = self.positions.get(order.symbol)
            if existing is not None:
                existing.augment(fillable, fill_price, entry_fee)
            else:
                self.positions[order.symbol] = _Position(
                    order.symbol,
                    fillable,
                    fill_price,
                    bar.close,
                    order.market,
                    order.stop_price,
                    order.target_price,
                    order.client_order_id,
                    order.direction,
                    entry_fee,
                    strategy=order.strategy,
                    entry_timestamp=bar.timestamp,
                    strategy_version=order.strategy_version,
                    exit_policy=order.exit_policy,
                    volatility_at_entry=order.volatility_at_entry,
                    entry_score=order.signal_score,
                    normalized_expected_r=order.normalized_expected_r,
                    entry_regime=order.signal_regime,
                    initial_stop=order.stop_price,
                    initial_risk=abs(fill_price - order.stop_price),
                    highest_high=fill_price,
                    lowest_low=fill_price,
                )
            if order.direction == Direction.LONG:
                self.cash -= notional + entry_fee
            else:
                self.cash += notional - entry_fee
            self.fills.append(
                Fill(
                    order.client_order_id,
                    order.symbol,
                    fillable,
                    fill_price,
                    order.direction,
                    bar.timestamp,
                    entry_fee,
                )
            )
            self._entry_cumulative_fill[order.client_order_id] = (
                self._entry_cumulative_fill.get(order.client_order_id, 0.0) + fillable
            )
            self._entry_fill_value[order.client_order_id] = (
                self._entry_fill_value.get(order.client_order_id, 0.0) + fillable * fill_price
            )
            self._entry_terminal_event(
                order,
                OrderState.FILLED if fillable >= order.qty else OrderState.PARTIALLY_FILLED,
                bar,
            )
            if fillable < order.qty:
                # The unfilled remainder keeps working as a fresh order so the
                # staleness check cannot expire a partially-filled entry that
                # is still being built up bar by bar.
                remaining.append(replace(order, qty=order.qty - fillable, created_at=bar.timestamp))
        self.pending = remaining

        # 2-4. Newly-filled and existing positions see this bar's open and
        # intrabar range. This prevents impossible survival through an entry bar.
        position = self.positions.get(bar.symbol)
        if position is not None:
            self._activate_pending_stop(position)
            position.current_price = bar.close
            exit_price: float | None = None
            exit_reason = ""
            if position.pending_time_exit:
                exit_price = self._market_exit_price(position, bar)
                exit_reason = EXIT_TIME
                position.exit_reason = EXIT_TIME
                self._cancel_remainder(position.symbol, position.client_order_id, bar)
            elif position.stop_triggered:
                # A triggered stop-market exit keeps liquidating the remaining
                # quantity at the open until flat, even if price has recovered
                # beyond the stop level.
                exit_price = self._market_exit_price(position, bar)
                exit_reason = position.exit_reason
            else:
                self._track_excursions(position, bar)
                exit_result = self._exit_price(position, bar)
                if exit_result is not None:
                    exit_price, exit_reason = exit_result
                    if exit_reason in {EXIT_STOP, EXIT_BREAK_EVEN, EXIT_TRAILING_STOP}:
                        # From here on the remainder is a triggered market exit.
                        position.stop_triggered = True
                        position.exit_reason = exit_reason
                    # A partial target fill leaves a resting limit at the
                    # target price: it is re-evaluated on every later bar
                    # together with the still-armed stop, and only fills when
                    # price reaches the target again.
                    # The trade's exit has fired either way, so its unfilled
                    # entry portion is done: no later bar re-enters it.
                    self._cancel_remainder(position.symbol, position.client_order_id, bar)
            if exit_price is not None:
                exitable = self._fillable_qty(position.qty, bar, self.max_exit_participation_pct)
                if exitable > 0:
                    if exitable >= position.qty:
                        self._close(position, exit_price, exit_reason, bar)
                    else:
                        self._close(position, exit_price, exit_reason, bar, qty=exitable)
            current_position = self.positions.get(bar.symbol)
            if current_position is not None and exit_price is None:
                self._advance_exit_policy(current_position)

    @staticmethod
    def _fillable_qty(requested: float, bar: Bar, participation_pct: float) -> float:
        """Maximum fillable quantity on this bar under the participation cap."""
        if bar.volume <= 0:
            return 0.0
        participation_cap = bar.volume * participation_pct
        if requested < participation_cap:
            return requested
        return participation_cap

    def _market_exit_price(self, position: _Position, bar: Bar) -> float:
        """Fill price for a triggered exit that liquidates at the open."""
        adverse = self.slippage_bps + self.spread_bps / 2
        if position.direction == Direction.LONG:
            return bar.open * (1 - adverse / 10_000)
        return bar.open * (1 + adverse / 10_000)

    def _cancel_remainder(self, symbol: str, client_order_id: str, bar: Bar) -> None:
        """Cancel the unfilled entry portion once its position ended terminally.

        Without this, a partially-built position that is stopped out leaves its
        remainder pending, and a later bar silently re-enters the same trade.
        """
        remaining: list[OrderIntent] = []
        for order in self.pending:
            if order.symbol == symbol and order.client_order_id == client_order_id:
                self.order_rejections[order.client_order_id] = "entry_canceled_on_exit"
                self._entry_terminal_event(order, OrderState.CANCELLED, bar)
            else:
                remaining.append(order)
        self.pending = remaining

    def _track_excursions(self, position: _Position, bar: Bar) -> None:
        if position.entry_timestamp is None or bar.timestamp < position.entry_timestamp:
            return
        if position.direction == Direction.LONG:
            adverse = (position.avg_price - bar.low) / position.avg_price
            favorable = (bar.high - position.avg_price) / position.avg_price
        else:
            adverse = (bar.high - position.avg_price) / position.avg_price
            favorable = (position.avg_price - bar.low) / position.avg_price
        position.max_adverse = max(position.max_adverse, adverse)
        position.max_favorable = max(position.max_favorable, favorable)
        position.highest_high = max(position.highest_high or bar.high, bar.high)
        position.lowest_low = min(position.lowest_low or bar.low, bar.low)
        position.bars_held += 1

    def _activate_pending_stop(self, position: _Position) -> None:
        candidate = position.pending_stop
        if candidate is None:
            return
        if position.direction == Direction.LONG and candidate > position.stop:
            position.stop = candidate
            position.active_stop_reason = position.pending_stop_reason
        elif position.direction == Direction.SHORT and candidate < position.stop:
            position.stop = candidate
            position.active_stop_reason = position.pending_stop_reason
        position.pending_stop = None

    def _advance_exit_policy(self, position: _Position) -> None:
        """Schedule dynamic exits using only the just-completed bar history."""
        policy = position.exit_policy
        if policy.max_holding_bars is not None and position.bars_held >= policy.max_holding_bars:
            position.pending_time_exit = True

        if position.initial_risk <= 0:
            return
        favorable_r = (
            ((position.highest_high or position.avg_price) - position.avg_price)
            / position.initial_risk
            if position.direction == Direction.LONG
            else (
                position.avg_price - (position.lowest_low or position.avg_price)
            )
            / position.initial_risk
        )
        candidates: list[tuple[float, str]] = []
        if policy.break_even_trigger_r is not None and favorable_r >= policy.break_even_trigger_r:
            candidates.append((self._cost_covered_break_even(position), EXIT_BREAK_EVEN))

        trailing_active = (
            policy.trailing_type == TrailingStopType.CHANDELIER
            and policy.trailing_atr_multiple is not None
            and favorable_r >= (policy.trailing_activation_r or 0.0)
        )
        if trailing_active:
            trailing_multiple = policy.trailing_atr_multiple
            assert trailing_multiple is not None
            current_atr = atr(
                list(self._bar_history[position.symbol]), policy.trailing_atr_period
            )
            volatility = current_atr or position.volatility_at_entry
            if volatility is not None and volatility > 0:
                if position.direction == Direction.LONG:
                    trailing = (position.highest_high or position.avg_price) - (
                        trailing_multiple * volatility
                    )
                else:
                    trailing = (position.lowest_low or position.avg_price) + (
                        trailing_multiple * volatility
                    )
                candidates.append((trailing, EXIT_TRAILING_STOP))
        if not candidates:
            return
        if position.direction == Direction.LONG:
            candidate, reason = max(candidates, key=lambda item: item[0])
            if candidate <= position.stop:
                return
            if position.pending_stop is not None and position.pending_stop > candidate:
                return
        else:
            candidate, reason = min(candidates, key=lambda item: item[0])
            if candidate >= position.stop:
                return
            if position.pending_stop is not None and position.pending_stop < candidate:
                return
        position.pending_stop = candidate
        position.pending_stop_reason = reason

    def _cost_covered_break_even(self, position: _Position) -> float:
        entry_notional = position.qty * position.avg_price
        entry_fee_fraction = position.entry_fee / entry_notional if entry_notional > 0 else 0.0
        adverse_fraction = (self.slippage_bps + self.spread_bps / 2) / 10_000
        fee_fraction = (
            self.crypto_taker_fee_bps / 10_000
            if position.market == Market.CRYPTO
            else 0.0
        )
        if position.direction == Direction.LONG:
            denominator = max(1e-9, 1 - adverse_fraction - fee_fraction)
            return position.avg_price * (1 + entry_fee_fraction) / denominator
        return position.avg_price * (1 - entry_fee_fraction) / (
            1 + adverse_fraction + fee_fraction
        )

    def _exit_price(self, position: _Position, bar: Bar) -> tuple[float, str] | None:
        long = position.direction == Direction.LONG
        if long and bar.open <= position.stop:
            raw = bar.open
            is_stop = True
        elif not long and bar.open >= position.stop:
            raw = bar.open
            is_stop = True
        elif long and bar.open >= position.target:
            raw = position.target
            is_stop = False
        elif not long and bar.open <= position.target:
            raw = position.target
            is_stop = False
        else:
            stop_hit = bar.low <= position.stop if long else bar.high >= position.stop
            target_hit = bar.high >= position.target if long else bar.low <= position.target
            if not stop_hit and not target_hit:
                return None
            if stop_hit and target_hit:
                choose_stop = self.intrabar_policy != "optimistic"
                if self.intrabar_policy == "ohlc_path":
                    choose_stop = bar.close >= bar.open if long else bar.close <= bar.open
                raw = position.stop if choose_stop else position.target
                is_stop = choose_stop
            else:
                raw = position.stop if stop_hit else position.target
                is_stop = stop_hit
        adverse = self.slippage_bps + self.spread_bps / 2
        if is_stop:
            return (
                raw * (1 - adverse / 10_000 if long else 1 + adverse / 10_000),
                position.active_stop_reason,
            )
        return (
            raw * (1 - self.slippage_bps / 10_000 if long else 1 + self.slippage_bps / 10_000),
            EXIT_TARGET,
        )

    def liquidate_at_end(self, bar: Bar) -> None:
        """Close a still-open position at the final bar's close, exit costs applied.

        Makes the ending equity fully realized so total return, trade count,
        win rate and expectancy all measure the same economic scope.
        """
        position = self.positions.get(bar.symbol)
        if position is None:
            return
        adverse_bps = self.slippage_bps + self.spread_bps / 2
        if position.direction == Direction.LONG:
            exit_price = bar.close * (1 - adverse_bps / 10_000)
        else:
            exit_price = bar.close * (1 + adverse_bps / 10_000)
        self._close(position, exit_price, EXIT_END_OF_TEST, bar)

    def _close(
        self,
        position: _Position,
        price: float,
        exit_reason: str,
        bar: Bar,
        *,
        qty: float | None = None,
    ) -> None:
        close_qty = position.qty if qty is None else qty
        if close_qty <= 0 or close_qty > position.qty:
            raise ValueError("Invalid close quantity")
        exit_fee = self.commission + (
            close_qty * price * self.crypto_taker_fee_bps / 10_000
            if position.market == Market.CRYPTO
            else 0.0
        )
        entry_fee_share = position.entry_fee * (close_qty / position.qty)
        if position.direction == Direction.LONG:
            self.cash += close_qty * price - exit_fee
            pnl = (price - position.avg_price) * close_qty - entry_fee_share - exit_fee
        else:
            self.cash -= close_qty * price + exit_fee
            pnl = (position.avg_price - price) * close_qty - entry_fee_share - exit_fee
        position._acc_pnl += pnl
        position._acc_fees += entry_fee_share + exit_fee
        position._acc_exit_qty += close_qty
        position._exit_value += close_qty * price
        self.fills.append(
            Fill(
                position.client_order_id,
                position.symbol,
                close_qty,
                price,
                Direction.FLAT,
                bar.timestamp,
                exit_fee,
            )
        )
        if close_qty >= position.qty:
            # Flat: emit one logical trade aggregated over every partial exit
            # slice, so performance metrics and Monte Carlo see trades the way
            # the strategy does instead of one record per partial fill.
            avg_exit_price = (
                position._exit_value / position._acc_exit_qty
                if position._acc_exit_qty > 0
                else price
            )
            self.realized_pnls.append(position._acc_pnl)
            self.trades.append(
                TradeRecord(
                    client_order_id=position.client_order_id,
                    symbol=position.symbol,
                    market=position.market,
                    strategy=position.strategy,
                    direction=position.direction,
                    qty=position._acc_exit_qty,
                    entry_time=position.entry_timestamp or bar.timestamp,
                    exit_time=bar.timestamp,
                    entry_price=position.avg_price,
                    exit_price=avg_exit_price,
                    pnl=position._acc_pnl,
                    return_pct=(
                        (avg_exit_price - position.avg_price)
                        * (1 if position.direction == Direction.LONG else -1)
                        / position.avg_price
                        if position.avg_price > 0
                        else 0.0
                    ),
                    mae_pct=position.max_adverse,
                    mfe_pct=position.max_favorable,
                    exit_reason=position.exit_reason or exit_reason,
                    fees=position._acc_fees,
                    strategy_version=position.strategy_version,
                    exit_policy_id=position.exit_policy.policy_id,
                    initial_risk=position.initial_risk,
                    bars_held=position.bars_held,
                    entry_score=position.entry_score,
                    normalized_expected_r=position.normalized_expected_r,
                    entry_regime=position.entry_regime,
                )
            )
            del self.positions[position.symbol]
        else:
            position.qty -= close_qty
            position.entry_fee -= entry_fee_share

    def _entry_terminal_event(
        self,
        order: OrderIntent,
        state: OrderState,
        bar: Bar,
    ) -> None:
        """Record an entry lifecycle event with contract-correct quantities.

        ``requested_qty`` is the original order size (a partially filled
        remainder keeps the same client_order_id, so it is looked up from the
        submission), ``cumulative_filled_qty`` is the true running total
        across every partial fill slice, and ``average_fill_price`` is the
        quantity-weighted average of those slices, not the latest fill price.
        """
        requested = self._entry_requested_qty.get(order.client_order_id, order.qty)
        cumulative = self._entry_cumulative_fill.get(order.client_order_id, 0.0)
        fill_value = self._entry_fill_value.get(order.client_order_id, 0.0)
        self.execution_events.append(
            ExecutionEvent(
                account_id="backtest",
                client_order_id=order.client_order_id,
                broker_order_id=order.client_order_id,
                parent_order_id=None,
                role=OrderRole.ENTRY,
                symbol=order.symbol,
                market=order.market,
                state=state,
                requested_qty=requested,
                cumulative_filled_qty=cumulative,
                average_fill_price=fill_value / cumulative if cumulative > 0 else None,
                occurred_at=bar.timestamp,
            )
        )
        if state != OrderState.PARTIALLY_FILLED:
            self._entry_requested_qty.pop(order.client_order_id, None)
            self._entry_cumulative_fill.pop(order.client_order_id, None)
            self._entry_fill_value.pop(order.client_order_id, None)

    def drain_execution_events(self) -> list[ExecutionEvent]:
        events = self.execution_events
        self.execution_events = []
        return events
