"""Research-only path-dependent terminal lifecycle engine (E_FULL freeze).

Implements TerminalAwareResearchEngine that extends PortfolioBacktestEngine
without mutating production code. For every entity with terminal event,
after its final observed tradable session, if held, executes terminal
lifecycle exactly once, mutating cash/quantity/realized PnL/equity and
subsequent capital/risk/rebalances. No future SELL for terminated position,
no zombie, stale last_price no longer contributes (position removed).

Policies:
  T0 — stale reference (no lifecycle, diagnostic)
  T1 — last-close minus normal sell costs
  T2 — 50% recovery plus costs
  T3 — zero recovery
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Literal

from parayabuck.backtest.portfolio_engine import (
    OrderSide,
    PortfolioBacktestEngine,
    PortfolioBacktestResult,
    PortfolioFill,
    _Position,
    equity_regulatory_fee,
)
from parayabuck.backtest.terminal_policy import TerminalClassification, should_apply_haircut
from parayabuck.core.models import Bar
from parayabuck.data.market_dataset import CashRateObservation, CorporateAction
from parayabuck.markets.symbols import canonical_symbol

TerminalPolicy = Literal["T0", "T1", "T2", "T3"]


@dataclass(frozen=True)
class TerminalInfo:
    entity_id: str
    symbol: str  # engine_symbol (unique per provider ID)
    display_symbol: str
    last_bar: datetime
    terminal_event_class: str
    evidence_class: str
    should_haircut: bool


def build_terminal_map(
    classifications: dict[str, TerminalClassification],
    dataset_end: datetime,
) -> dict[str, TerminalInfo]:
    """Map engine_symbol -> TerminalInfo, respecting exclusions."""
    out: dict[str, TerminalInfo] = {}
    for entity_id, tc in classifications.items():
        # entity_id is provider_asset_id; symbol is canonical_symbol
        # For engine we use symbol as key but ensure uniqueness via entity_id
        # If multiple entities share display symbol (ticker reuse), we will use
        # engine_symbol = f"{symbol}__{entity_id[:8]}" (handled caller-side)
        # Here we store by symbol for simple case; caller may override with engine_symbol
        out[tc.symbol] = TerminalInfo(
            entity_id=tc.entity_id,
            symbol=tc.symbol,
            display_symbol=tc.symbol,
            last_bar=tc.last_bar,
            terminal_event_class=tc.terminal_event_class,
            evidence_class=tc.evidence_class,
            should_haircut=should_apply_haircut(tc),
        )
    return out


def _terminal_proceeds(
    qty: float,
    last_close: float,
    trade_date: date,
    policy: TerminalPolicy,
    slippage_bps: float,
    spread_bps: float,
    engine: PortfolioBacktestEngine,
) -> float:
    if qty <= 0 or last_close <= 0:
        return 0.0
    if policy == "T0":
        # stale — caller should not call this for T0; return stale value
        return qty * last_close
    if policy == "T3":
        return 0.0
    factor = 0.5 if policy == "T2" else 1.0
    base_price = last_close * factor
    from parayabuck.backtest.portfolio_engine import _adverse_bps

    adverse = _adverse_bps(slippage_bps, spread_bps)
    fill_price = base_price * (1 - adverse / 10_000.0)
    # Use engine's fee model (effective-dated if enabled)
    fee = 0.0
    # Approximate market as EQUITY for fee (crypto not in survivorship)
    # Use engine's _equity_fee helper via internal call pattern
    try:
        fee = engine._equity_fee(OrderSide.SELL, qty, fill_price, trade_date)
    except Exception:
        fee = qty * 0.000195 + qty * 0.000003 + qty * fill_price * 0.0000206
    notional = qty * fill_price
    return max(0.0, notional - fee)


class TerminalAwareResearchEngine(PortfolioBacktestEngine):
    """Path-dependent terminal lifecycle wrapper.

    Usage:
        engine = TerminalAwareResearchEngine(strategy, rebalance_policy,
            terminal_map=map, policy="T1", **kwargs)
        result = engine.run(bars, ...)

    terminal_map: engine_symbol -> TerminalInfo. After each bar cycle whose
    timestamp equals TerminalInfo.last_bar, if that symbol is held, terminal
    event fires exactly once.
    """

    def __init__(
        self,
        *args: Any,
        terminal_map: dict[str, TerminalInfo] | None = None,
        policy: TerminalPolicy = "T0",
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.terminal_map: dict[str, TerminalInfo] = dict(terminal_map or {})
        self.policy: TerminalPolicy = policy
        self.terminal_events_fired: list[dict[str, Any]] = []
        self._fired_entities: set[str] = set()
        # For geometry invariant audit
        self._actual_exposure_history: list[float] = []
        self._target_holdings_history: list[int] = []

    def _execute_terminal(self, symbol: str, position: _Position, last_close: float, terminal_date: datetime) -> None:
        if symbol in self._fired_entities:
            return
        qty = position.qty
        avg_price = position.avg_price
        entry_fee = position.entry_fee
        # Record pre-event equity
        pre_equity = self._equity()
        # Compute proceeds
        if self.policy == "T0":
            # T0: do nothing (stale)
            return
        # For haircut-excluded entities, treat as T1 full proceeds (no arbitrary haircut)
        info = self.terminal_map.get(symbol)
        should = info.should_haircut if info else True
        effective_policy: TerminalPolicy = self.policy if should else "T1"
        if not should and self.policy in ("T2", "T3"):
            effective_policy = "T1"
        proceeds = _terminal_proceeds(
            qty, last_close, terminal_date.date(), effective_policy, self.slippage_bps, self.spread_bps, self
        )
        # Realized delta vs cost basis
        # Match _close_position formula: (exit_price - avg)*qty - entry_fee - fee
        # Here proceeds already is qty*fill_price - fee, so realized = proceeds - qty*avg - entry_fee
        # But _terminal_proceeds already subtracts fee, so we reuse
        # Compute exit_price and fee separately for accurate realized
        # Recompute exit_price for realized
        from parayabuck.backtest.portfolio_engine import _adverse_bps

        factor = 0.5 if effective_policy == "T2" else (0.0 if effective_policy == "T3" else 1.0)
        if effective_policy == "T3":
            exit_price = 0.0
            fee = 0.0
            proceeds = 0.0
        else:
            base_price = last_close * factor
            adverse = _adverse_bps(self.slippage_bps, self.spread_bps)
            exit_price = base_price * (1 - adverse / 10_000.0)
            try:
                fee = self._equity_fee(OrderSide.SELL, qty, exit_price, terminal_date.date())
            except Exception:
                fee = qty * 0.000195 + qty * 0.000003 + qty * exit_price * 0.0000206
            proceeds = max(0.0, qty * exit_price - fee)
        realized_delta = (
            (exit_price - avg_price) * qty - entry_fee - fee
            if effective_policy != "T3"
            else (-avg_price * qty - entry_fee)
        )
        if effective_policy == "T3":
            realized_delta = -avg_price * qty - entry_fee
            # No proceeds, but fee already 0
        lifetime = position.realized_pnl_accumulator + realized_delta
        # Mutate state path-dependently
        self._cash += proceeds
        self._realized_pnl_events.append((terminal_date, symbol, realized_delta))
        self._closed_position_events.append((terminal_date, symbol, lifetime))
        self.state.realized_pnl += realized_delta
        # Remove position and any pending orders for this symbol
        self._positions.pop(symbol, None)
        self._pending = [o for o in self._pending if o.symbol != symbol]
        # Do NOT delete _last_price; equity will no longer include it because position removed
        # Record event for accounting audit
        self.terminal_events_fired.append(
            {
                "entity_id": info.entity_id if info else position.symbol,
                "symbol": symbol,
                "display_symbol": info.display_symbol if info else symbol,
                "entry_avg": avg_price,
                "entry_qty": qty,
                "final_tradable_date": terminal_date.isoformat(),
                "quantity_before": qty,
                "account_equity_before": pre_equity,
                "last_close": last_close,
                "effective_policy": effective_policy,
                "proceeds": proceeds,
                "realized_delta": realized_delta,
                "pre_equity": pre_equity,
                "post_equity": self._equity(),
            }
        )
        self._fired_entities.add(symbol)
        # Update risk state immediately so next decision sees new equity
        self._last_observed_equity = self._equity()
        self._peak_equity = max(self._peak_equity, self._last_observed_equity)

    # Override run to inject terminal lifecycle after each bar cycle
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
        # Policy T0: delegate to parent with no lifecycle (stale)
        if self.policy == "T0":
            return super().run(
                bars,
                warmup=warmup,
                liquidate_at_end=liquidate_at_end,
                cash_rate_annualized=cash_rate_annualized,
                signal_bars=signal_bars,
                corporate_actions=corporate_actions,
                cash_rates=cash_rates,
                prepared_inputs=prepared_inputs,
            )
        # Path-dependent run
        from itertools import groupby

        restored_pending = list(self._pending)
        self._pending = []
        self._fills = []
        self._rebalances = []
        self._realized_pnl_events = []
        self._closed_position_events = []
        self._liquidation_costs = []
        self._dividend_total = 0.0
        self._cash_rate_observations = tuple(sorted(cash_rates or [], key=lambda x: x.timestamp))
        self._risk_contribution_history = []
        self._weight_history = []
        self._universe_history = []
        self._holdings_history = []
        self._actual_exposure_history = []
        self._target_holdings_history = []
        self._fired_entities = set()
        self.terminal_events_fired = []
        self._pending.extend(restored_pending)
        if cash_rate_annualized is not None:
            self.cash_rate_annualized = float(cash_rate_annualized)
        if prepared_inputs is not None:
            ordered, decision_bars = prepared_inputs
        else:
            ordered = sorted(self._deduplicate(bars), key=lambda b: (b.timestamp, b.symbol))
            decision_bars = sorted(
                self._deduplicate(signal_bars if signal_bars is not None else bars),
                key=lambda b: (b.timestamp, b.symbol),
            )
        watermark = self.state.last_processed_bar
        if watermark:
            cutoff = datetime.fromisoformat(watermark)
            ordered = [b for b in ordered if b.timestamp > cutoff]
            decision_bars = [b for b in decision_bars if b.timestamp > cutoff]
        for bar in sorted(warmup or [], key=lambda x: (x.timestamp, x.symbol)):
            self._ingest(bar)
            self._ingest_execution(bar)
        action_queue = sorted(corporate_actions or [], key=lambda x: x.timestamp)
        action_index = 0
        decision_index = 0
        curve: list[float] = []
        curve_timestamps: list[datetime] = []
        # Build fast lookup: last_bar date -> list symbols
        # Group by timestamp date for quick check
        last_bar_map: dict[datetime, list[str]] = {}
        for sym, info in self.terminal_map.items():
            last_bar_map.setdefault(info.last_bar, []).append(sym)
        for _, grouped in groupby(ordered, key=lambda b: b.timestamp):
            cycle = list(grouped)
            for bar in cycle:
                self._ingest_execution(bar)
            while decision_index < len(decision_bars) and decision_bars[decision_index].timestamp <= cycle[0].timestamp:
                self._ingest(decision_bars[decision_index])
                decision_index += 1
            while action_index < len(action_queue) and action_queue[action_index].timestamp <= cycle[0].timestamp:
                self._apply_corporate_action(action_queue[action_index])
                action_index += 1
            self._fill_cycle(cycle)
            for bar in cycle:
                # Canonicalize symbol for last_price
                self._last_price[canonical_symbol(bar.symbol, bar.market)] = bar.close
            self._update_risk_state(cycle[0].timestamp)
            self._decide(cycle[0].timestamp)
            # --- geometry invariant audit before terminal ---
            # Actual holdings count and exposure
            pos_val = sum(p.qty * self._last_price.get(p.symbol, p.avg_price) for p in self._positions.values())
            equity_now = self._equity()
            actual_exposure = pos_val / equity_now if equity_now > 0 else 0.0
            self._actual_exposure_history.append(actual_exposure)
            # --- terminal lifecycle after decision but still at this session ---
            # Fire for any symbol whose last_bar == this cycle timestamp and still held
            terminal_syms = last_bar_map.get(cycle[0].timestamp, [])
            for sym in terminal_syms:
                pos = self._positions.get(sym)
                if pos is not None and pos.qty > 0:
                    # Find last_close for this symbol in this cycle
                    # Use bar close from cycle if present else last_price
                    close_px = None
                    for b in cycle:
                        if canonical_symbol(b.symbol, b.market) == sym:
                            close_px = b.close
                            break
                    if close_px is None:
                        close_px = self._last_price.get(sym, pos.avg_price)
                    self._execute_terminal(sym, pos, close_px, cycle[0].timestamp)
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
                self.state.holdings = {}
                self.state.avg_prices = {}
                self.state.entry_fees = {}
                self.state.realized_pnl_accumulators = {}
                self.state.pending_orders = ()
                self.state.pending_target = {}
                self.state.cash = self._cash
                if self.state_path is not None:
                    self.save_state()
        benchmark = [b for b in decision_bars if b.symbol == self.benchmark_symbol]
        benchmark_return = (
            benchmark[-1].close / benchmark[0].close - 1 if len(benchmark) > 1 and benchmark[0].close > 0 else 0.0
        )
        return self._result(curve, curve_timestamps, benchmark_return, tuple(benchmark))


__all__ = ["TerminalAwareResearchEngine", "TerminalInfo", "build_terminal_map"]
