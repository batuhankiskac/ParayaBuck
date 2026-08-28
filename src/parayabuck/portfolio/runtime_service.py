"""Production portfolio decision runtime shared by paper and live execution."""

from __future__ import annotations

import json
from dataclasses import asdict, replace
from datetime import UTC, datetime

from parayabuck.core.models import AccountSnapshot, Bar, Market
from parayabuck.data.adjustment import verify_against_provider
from parayabuck.execution.portfolio import (
    PortfolioExecutionPolicy,
    PortfolioOrderRequest,
    PortfolioOrderSide,
    PortfolioRiskLimits,
    PortfolioRiskOverlay,
    RiskOverlayDecision,
    TargetToOrderPlanner,
    floor_increment,
)
from parayabuck.logger import get_logger
from parayabuck.markets.symbols import canonical_symbol
from parayabuck.markets.timeframes import bar_is_closed, equity_day_bar_is_closed
from parayabuck.portfolio import PRODUCT_STRATEGY_NAMES, rebalance_policy_for
from parayabuck.portfolio.base import PortfolioStrategy, require_product_universe
from parayabuck.portfolio.models import TargetPortfolio, TargetWeight
from parayabuck.portfolio.runtime_models import (
    _AMBIGUOUS_ORDER_STATUSES,
    REBALANCE_PHASE_BLOCKED,
    REBALANCE_PHASE_BUY_PENDING,
    REBALANCE_PHASE_BUY_READY,
    REBALANCE_PHASE_COMPLETE,
    REBALANCE_PHASE_SELL_PENDING,
    TERMINAL_ORDER_STATUSES_SQL,
    PortfolioDecisionService,
    PortfolioRuntimeBroker,
    PortfolioRuntimeResult,
    RebalanceExecutionState,
    _decision_id,
    configuration_digest,
    is_terminal_order_status,
)
from parayabuck.portfolio.runtime_rebalance import (
    is_sell_phase_complete,
    requires_ambiguous_block,
)
from parayabuck.portfolio.runtime_repository import PortfolioStateRepository

log = get_logger("parayabuck.portfolio.runtime_service")

# Re-exports for backward compatibility (public facade)
__all__ = [
    "REBALANCE_PHASE_BLOCKED",
    "REBALANCE_PHASE_BUY_PENDING",
    "REBALANCE_PHASE_BUY_READY",
    "REBALANCE_PHASE_COMPLETE",
    "REBALANCE_PHASE_SELL_PENDING",
    "TERMINAL_ORDER_STATUSES_SQL",
    "PortfolioDecisionService",
    "PortfolioRuntime",
    "PortfolioRuntimeBroker",
    "PortfolioRuntimeResult",
    "PortfolioStateRepository",
    "RebalanceExecutionState",
    "configuration_digest",
    "is_terminal_order_status",
]


class PortfolioRuntime:
    def __init__(
        self,
        strategy: PortfolioStrategy,
        symbols: tuple[str, ...],
        broker: PortfolioRuntimeBroker,
        repository: PortfolioStateRepository,
        *,
        mode: str,
        maximum_allocation: float,
        risk_limits: PortfolioRiskLimits | None = None,
        universe: object | None = None,
        universe_version: str = "runtime",
        account_id: str = "unknown",
        live_dollar_cap: float = 0.0,
        execution_policy: PortfolioExecutionPolicy | None = None,
        allow_static_universe: bool = False,
    ) -> None:
        if mode not in {"paper", "live"}:
            raise ValueError("PortfolioRuntime mode must be paper or live")
        if not symbols:
            raise ValueError("PortfolioRuntime requires an active universe")
        if not 0 < maximum_allocation <= 1:
            raise ValueError("maximum_allocation must be in (0, 1]")
        if mode == "live" and live_dollar_cap <= 0:
            raise ValueError("Live portfolio execution requires a positive hard dollar cap")
        self.strategy = strategy
        self.market = strategy.market
        self.symbols = tuple(dict.fromkeys(canonical_symbol(symbol, strategy.market) for symbol in symbols))
        self.broker = broker
        self.repository = repository
        self.mode = mode
        self.maximum_allocation = maximum_allocation
        self.live_dollar_cap = live_dollar_cap
        self.universe = universe
        self.universe_version = universe_version
        self.account_id = account_id
        self.allow_static_universe = bool(allow_static_universe)
        self.policy = rebalance_policy_for(self.strategy)
        self.execution_policy = execution_policy or PortfolioExecutionPolicy()
        self.decision_service = PortfolioDecisionService()
        self.risk_overlay = PortfolioRiskOverlay(risk_limits)
        self.planner = TargetToOrderPlanner(self.execution_policy)
        self.history: dict[str, list[Bar]] = {symbol: [] for symbol in self.symbols}
        # Split-basis bridge (equity only): warmup seeds this history on the
        # SPLITS basis; live aggregation appends RAW bars PROVISIONALLY.
        # Before any scheduled decision, EngineRuntime verifies the series
        # against a batched provider splits tail and REPLACES it wholesale
        # when the provider rebased (data/adjustment.py). The runtime keeps
        # no cumulative factor math — the provider is the sole authority.
        self.signal_verified_through_by_symbol: dict[str, datetime] = {}
        self.signal_last_verified_at: datetime | None = None
        self.configuration_digest = configuration_digest(
            strategy,
            mode,
            maximum_allocation,
            universe_version,
            self.risk_overlay.limits,
            self.execution_policy,
        )
        self.previous_timestamp = repository.latest_decision_timestamp(
            account_id, self.configuration_digest, self.market
        )
        self.session_id = repository.begin_session(account_id, self.configuration_digest, mode)
        # Startup catch-up observability: last attempt status and when it was
        # attempted (UTC). Set on every startup_catch_up call, including
        # blocked/early returns; used by the engine health surface.
        self.startup_catch_up_status: str | None = None
        self.startup_catch_up_at: datetime | None = None

    def _with_paper_observability(
        self,
        target: TargetPortfolio,
        risk: RiskOverlayDecision,
        account: AccountSnapshot,
        orders: tuple[PortfolioOrderRequest, ...],
        *,
        entry_latch: str | None,
    ) -> TargetPortfolio:
        """Attach V3 PAPER diagnostics after decisions are complete.

        Every value here is derived from already-computed targets, the account
        snapshot, risk result, or planned orders. It is persisted for
        operations and deliberately cannot participate in strategy math.
        """
        if self.strategy.name != "equity_residual_momentum_v3":
            return target
        verified = {
            symbol: timestamp.isoformat()
            for symbol, timestamp in sorted(self.signal_verified_through_by_symbol.items())
        }
        verified_through = min(verified.values()) if verified else None
        current_positions = {
            canonical_symbol(position.symbol, position.market): float(position.qty)
            for position in account.positions
            if position.market == self.market and not position.is_flat
        }
        planned_delta: dict[str, float] = {}
        for order in orders:
            signed_quantity = order.quantity if order.side == PortfolioOrderSide.BUY else -order.quantity
            planned_delta[order.symbol] = planned_delta.get(order.symbol, 0.0) + signed_quantity
        target_weights = {weight.symbol: weight.weight for weight in target.weights}
        symbols = sorted(set(current_positions) | set(target_weights) | set(planned_delta))
        qualified_raw = target.metadata.get("qualified_symbols", 0)
        ranked_raw = target.metadata.get("ranked_symbols", 0)
        selected_raw = target.metadata.get("selected_symbols", ())
        qualified_count = int(qualified_raw) if isinstance(qualified_raw, (int, float)) else 0
        ranked_count = int(ranked_raw) if isinstance(ranked_raw, (int, float)) else 0
        selected_symbols = list(selected_raw) if isinstance(selected_raw, (list, tuple)) else []
        details = {
            symbol: {
                "target_weight": float(target_weights.get(symbol, 0.0)),
                "current_position": float(current_positions.get(symbol, 0.0)),
                "planned_order_delta": float(planned_delta.get(symbol, 0.0)),
            }
            for symbol in symbols
        }
        reason = entry_latch or str(target.metadata.get("risk_off_detail", "")) or None
        return replace(
            target,
            metadata={
                **target.metadata,
                "paper_observability": {
                    "strategy_name": target.strategy_name,
                    "strategy_version": target.strategy_version,
                    "decision_timestamp": target.decision_timestamp.isoformat(),
                    "signal_verified_through": verified_through,
                    "signal_verified_through_by_symbol": verified,
                    "qualified_symbol_count": qualified_count,
                    "ranked_symbol_count": ranked_count,
                    "selected_symbols": selected_symbols,
                    "risk_overlay_gross_after": float(risk.gross_after),
                    "cash_weight": float(target.cash_weight),
                    "blocked_or_skipped_reason": reason,
                    "symbols": details,
                },
            },
        )

    def seed(self, bars: list[Bar]) -> None:
        """Seed only closed, approved bars without producing a decision.

        Forming (in-progress) bars are rejected: Alpaca's historical feed
        includes the partial bar for the current bucket, and an appended
        forming bar would block the true closed bar (same timestamp) via the
        ``series[-1].timestamp < bar.timestamp`` append rule, leaving a
        partial close in decision history indefinitely.
        """
        now = datetime.now(UTC)
        for bar in sorted(bars, key=lambda item: (item.timestamp, item.symbol)):
            symbol = canonical_symbol(bar.symbol, bar.market)
            if bar.market != self.market or bar.timeframe != self.strategy.timeframe or symbol not in self.history:
                continue
            if self.market == Market.CRYPTO:
                if not bar_is_closed(bar.timestamp, bar.timeframe, now):
                    continue
            elif bar.timeframe == "1d" and not equity_day_bar_is_closed(bar.timestamp, now):
                continue
            series = self.history[symbol]
            if not series or series[-1].timestamp < bar.timestamp:
                series.append(bar)
        timestamp_sets = [{bar.timestamp for bar in series} for series in self.history.values() if series]
        if len(timestamp_sets) == len(self.history):
            common_timestamps = set.intersection(*timestamp_sets)
            if common_timestamps:
                common_latest = max(common_timestamps)
                self.previous_timestamp = max(filter(None, (self.previous_timestamp, common_latest)))

    def startup_catch_up(self, *, entry_latch: str | None = None) -> PortfolioRuntimeResult:
        """Decide immediately on the latest completed warmup bars.

        Restores BACKTEST/PAPER parity: the backtest engine fires its initial
        decision on the first usable bar (``is_period_start(None, ts)`` is
        True), while a runtime seeded mid-period would otherwise sit in cash
        until the next cadence boundary. This forces a full-target decision on
        the latest closed bars — reusing every existing gate in
        ``process_closed_bars`` (duplicate decision, rebalance advance/phase,
        managed-batch completeness, benchmark, high-water, risk overlay,
        planner, two-phase sells-then-buys, idempotency keys) — only when the
        deployment has never decided and holds no managed position in this
        market. Never re-enters after ANY prior decision (restart safety).
        """
        self.startup_catch_up_at = datetime.now(UTC)
        series_by_symbol = dict(self.history)
        timestamps = [series[-1].timestamp if series else None for series in series_by_symbol.values()]
        if any(timestamp is None for timestamp in timestamps) or any(
            len(series) < self.strategy.minimum_history_bars for series in series_by_symbol.values()
        ):
            self.startup_catch_up_status = "warming_up"
            return PortfolioRuntimeResult(None, None, None, (), (), "warming_up")
        if len(set(timestamps)) != 1:
            self.startup_catch_up_status = "incomplete_strategy_batch"
            return PortfolioRuntimeResult(None, None, None, (), (), "incomplete_strategy_batch")
        timestamp = timestamps[0]
        assert timestamp is not None
        if self.repository.has_decision(self.account_id, self.configuration_digest, timestamp, self.market):
            self.startup_catch_up_status = "duplicate_decision"
            return PortfolioRuntimeResult(None, None, None, (), (), "duplicate_decision")
        if (
            self.repository.latest_decision_timestamp(self.account_id, self.configuration_digest, self.market)
            is not None
        ):
            # Any prior decision for this deployment means the cadence is
            # already running: a catch-up would be an out-of-cadence re-entry.
            self.startup_catch_up_status = "catch_up_superseded"
            return PortfolioRuntimeResult(None, None, None, (), (), "catch_up_superseded")
        account = self.broker.account()
        if any(
            position.market == self.market and not position.is_flat and position.managed_qty > 1e-12
            for position in account.positions
        ):
            # Already invested in this market: never layer a catch-up entry on
            # top of an existing managed position.
            self.startup_catch_up_status = "already_invested"
            return PortfolioRuntimeResult(None, None, None, (), (), "already_invested")
        # Let a persisted two-phase rebalance advance first; a still-active
        # rebalance defers the catch-up (submitting fresh BUYs while an older
        # SELL is working could stack risk-increasing exposure).
        self.advance_rebalance()
        current_rebalance = self.repository.current_rebalance_execution(self.account_id, self.market)
        if current_rebalance is not None and current_rebalance.phase != REBALANCE_PHASE_COMPLETE:
            self.startup_catch_up_status = "rebalance_in_progress"
            return PortfolioRuntimeResult(current_rebalance.decision_id, None, None, (), (), "rebalance_in_progress")
        bars = [series[-1] for series in series_by_symbol.values()]
        result = self.process_closed_bars(
            bars,
            entry_latch=entry_latch,
            force_scheduled=True,
            decision_origin="startup_catch_up",
        )
        self.startup_catch_up_status = result.status
        return result

    def update_symbols(self, symbols: tuple[str, ...]) -> tuple[tuple[str, ...], tuple[str, ...]]:
        """Replace ephemeral members without changing the approved policy identity."""
        updated = tuple(dict.fromkeys(canonical_symbol(symbol, self.market) for symbol in symbols))
        if not updated:
            raise ValueError("PortfolioRuntime requires an active universe")
        current = set(self.symbols)
        desired = set(updated)
        added = tuple(sorted(desired - current))
        removed = tuple(sorted(current - desired))
        for symbol in added:
            self.history[symbol] = []
        for symbol in removed:
            self.history.pop(symbol, None)
        # New members arrive unverified: warmup seeding is followed by the
        # normal batched provider verification before any decision.
        self.signal_verified_through_by_symbol = {}
        self.signal_last_verified_at = None
        self.symbols = updated
        return added, removed

    def _append_signal_bar(self, bar: Bar) -> None:
        """Append one completed bar to the strategy's signal history.

        Equity bars arrive from the RAW stream and are appended
        PROVISIONALLY: they sit on the current as-traded basis, which equals
        the provider's splits basis except on a split day. Scheduled
        decisions never run before EngineRuntime verified/rebuilt this
        history against a batched provider splits tail (fail closed).
        Crypto bars pass through untouched.
        """
        symbol = canonical_symbol(bar.symbol, bar.market)
        series = self.history[symbol]
        if series and series[-1].timestamp >= bar.timestamp:
            return
        series.append(bar)

    def signal_history_matches_provider(self, provider_splits: dict[str, list[Bar]]) -> bool:
        """Whether every equity symbol's stored history agrees with the
        provider's split-adjusted closes on overlapping timestamps."""
        if self.market != Market.EQUITY:
            return True
        for symbol, series in self.history.items():
            provider = provider_splits.get(symbol)
            if provider is None:
                continue
            if not verify_against_provider(tuple(series), provider):
                return False
        return True

    def adopt_authoritative_signal_history(
        self,
        provider_splits: dict[str, list[Bar]],
        verified_through_by_symbol: dict[str, datetime],
    ) -> int:
        """Replace each symbol's signal history with the PROVIDER series for
        covered timestamps (retaining out-of-scope older history and newer
        provisional bars), and record PER-SYMBOL verification watermarks.

        Returns the number of symbols whose history changed. Callers MUST
        verify per-symbol adequacy first; this method never decides whether
        a basis is trustworthy. No-op for crypto.
        """
        from parayabuck.data.adjustment import merge_provider_authority

        if self.market != Market.EQUITY:
            return 0
        changed_symbols = 0
        for symbol, series in self.history.items():
            provider = provider_splits.get(symbol)
            if not provider:
                continue
            merged, changed = merge_provider_authority(tuple(series), provider)
            if changed:
                self.history[symbol] = list(merged)
                changed_symbols += 1
        for symbol, verified_through in verified_through_by_symbol.items():
            if symbol in self.history and verified_through is not None:
                previous = self.signal_verified_through_by_symbol.get(symbol)
                if previous is None or verified_through > previous:
                    self.signal_verified_through_by_symbol[symbol] = verified_through
        self.signal_last_verified_at = datetime.now(UTC)
        return changed_symbols

    def process_closed_bars(
        self,
        bars: list[Bar],
        *,
        entry_latch: str | None = None,
        force_scheduled: bool = False,
        decision_origin: str = "scheduled",
        account: AccountSnapshot | None = None,
    ) -> PortfolioRuntimeResult:
        eligible = [
            bar
            for bar in bars
            if bar.market == self.market
            and bar.timeframe == self.strategy.timeframe
            and canonical_symbol(bar.symbol, bar.market) in self.history
        ]
        if not eligible:
            return PortfolioRuntimeResult(None, None, None, (), (), "no_eligible_bars")
        timestamp = max(bar.timestamp for bar in eligible)
        batch_symbols = {canonical_symbol(bar.symbol, bar.market) for bar in eligible if bar.timestamp == timestamp}
        for bar in sorted(eligible, key=lambda item: (item.timestamp, item.symbol)):
            self._append_signal_bar(bar)
        # ``boundary`` is the real cadence; ``scheduled`` additionally honors
        # a forced catch-up decision (startup catch-up), which must apply the
        # FULL target like any boundary decision.
        boundary = self.policy.is_period_start(self.previous_timestamp, timestamp)
        scheduled = boundary or force_scheduled
        # Receiving a timed-out partial batch explicitly skips that rebalance
        # period. A later day must not turn it into a silent delayed rebalance.
        self.previous_timestamp = timestamp
        warming_symbols = tuple(
            sorted(
                symbol for symbol, series in self.history.items() if len(series) < self.strategy.minimum_history_bars
            )
        )
        missing_symbols = tuple(
            sorted(symbol for symbol, series in self.history.items() if not series or series[-1].timestamp != timestamp)
        )
        if warming_symbols:
            return PortfolioRuntimeResult(None, None, None, (), (), "warming_up")
        if missing_symbols:
            return PortfolioRuntimeResult(None, None, None, (), (), "incomplete_strategy_batch")
        ready_history = dict(self.history)
        if self.repository.has_decision(self.account_id, self.configuration_digest, timestamp, self.market):
            return PortfolioRuntimeResult(None, None, None, (), (), "duplicate_decision")
        # A persisted two-phase rebalance may have become ready since the last
        # event (trade update, restart, or reconcile). Advance it first, then
        # defer any new decision while the previous rebalance is still in
        # flight: submitting a fresh BUY while an older SELL is working could
        # stack risk-increasing exposure on top of the not-yet-sold position.
        self.advance_rebalance()
        current_rebalance = self.repository.current_rebalance_execution(self.account_id, self.market)
        if current_rebalance is not None and current_rebalance.phase != REBALANCE_PHASE_COMPLETE:
            if current_rebalance.phase == REBALANCE_PHASE_BLOCKED:
                return PortfolioRuntimeResult(current_rebalance.decision_id, None, None, (), (), "rebalance_blocked")
            return PortfolioRuntimeResult(
                current_rebalance.decision_id,
                None,
                None,
                (),
                (),
                "rebalance_in_progress",
            )
        # The engine hands in one immutable snapshot shared across the whole
        # processing cycle (freshness enforced at the engine level against
        # maximum_account_age_for_entry_seconds). Direct callers (tests,
        # startup catch-up) omit it and get a fresh broker fetch, preserving
        # the historical behavior exactly.
        account = account or self.broker.account()
        managed = {
            canonical_symbol(position.symbol, position.market)
            for position in account.positions
            if position.market == self.market and not position.is_flat and position.managed_qty > 1e-12
        }
        if not managed.issubset(batch_symbols):
            return PortfolioRuntimeResult(None, None, None, (), (), "incomplete_managed_batch")
        if self.market == Market.CRYPTO and "BTC/USD" not in ready_history:
            return PortfolioRuntimeResult(None, None, None, (), (), "benchmark_not_ready")
        # Non-scheduled batch fast path: when the strategy does not override
        # the intermediate-risk hook (base implementation is constant False),
        # the batch can never produce a decision, so the persisted
        # high-water update and the full context build below are pure waste.
        # The strategy-hook identity check keeps exact semantics for any
        # strategy that DOES override the hook: those keep the original path
        # (context built, hook evaluated, possible risk-reducing decision).
        if (
            not scheduled
            and type(self.strategy).requires_intermediate_risk_rebalance
            is PortfolioStrategy.requires_intermediate_risk_rebalance
        ):
            return PortfolioRuntimeResult(None, None, None, (), (), "not_scheduled")
        persisted_high_water = self.repository.high_water(self.account_id)
        account = replace(
            account,
            peak_equity=max(account.peak_equity, persisted_high_water, account.equity),
        )
        self.repository.update_high_water(self.account_id, account.equity)
        context = self.decision_service.build_context(
            self.strategy,
            ready_history,
            account,
            timestamp,
            universe=self.universe,
            universe_version=self.universe_version,
        )
        # Fail-closed PAPER product universe guard: dynamic product
        # strategies must execute against a PIT universe that exposes
        # ``contains`` and carries a non-empty ``universe_version``.
        # A missing universe would otherwise make in_universe() return True
        # for every symbol (permissive fallback). Static/non-product research
        # contexts explicitly opt in via ``allow_static_universe=True`` on the
        # runtime or are synthetic non-PRODUCT strategies that retain
        # permissive behaviour; PAPER product execution never opts in.
        if self.allow_static_universe:
            require_product_universe(context, allow_static_context=True)
        else:
            is_product = self.strategy.name in PRODUCT_STRATEGY_NAMES
            if is_product:
                require_product_universe(context)
            elif context.universe is not None:
                require_product_universe(context)
        if not scheduled and not self.strategy.requires_intermediate_risk_rebalance(context):
            return PortfolioRuntimeResult(None, None, None, (), (), "not_scheduled")
        raw_target = self.strategy.decide(context)
        if entry_latch is not None:
            # Entry-risk latch: the strategy may propose anything, but the
            # runtime may only reduce risk. Cap every target at the current
            # weight so the planner emits SELL orders only; entries stay
            # blocked while protective/risk-reducing exits keep flowing.
            current_weights = {
                canonical_symbol(position.symbol, position.market): (position.market_value / account.equity)
                for position in account.positions
                if position.market == self.market and not position.is_flat
            }
            raw_target = replace(
                raw_target,
                weights=tuple(
                    TargetWeight(
                        weight.symbol,
                        min(weight.weight, current_weights.get(weight.symbol, 0.0)),
                        "entry_risk_latch_reduction",
                        rank=weight.rank,
                    )
                    for weight in raw_target.weights
                    if min(weight.weight, current_weights.get(weight.symbol, 0.0)) > 0
                ),
                cash_weight=1.0
                - sum(min(weight.weight, current_weights.get(weight.symbol, 0.0)) for weight in raw_target.weights),
                metadata={
                    **raw_target.metadata,
                    "entry_risk_latch": entry_latch,
                },
            )
        if not scheduled:
            current_weights = {
                canonical_symbol(position.symbol, position.market): (position.market_value / account.equity)
                for position in account.positions
                if position.market == self.market and not position.is_flat
            }
            reducing_weights = tuple(
                TargetWeight(
                    weight.symbol,
                    min(weight.weight, current_weights.get(weight.symbol, 0.0)),
                    "intermediate_risk_reduction_only",
                    rank=weight.rank,
                )
                for weight in raw_target.weights
                if min(weight.weight, current_weights.get(weight.symbol, 0.0)) > 0
            )
            raw_target = replace(
                raw_target,
                weights=reducing_weights,
                cash_weight=1.0 - sum(weight.weight for weight in reducing_weights),
                metadata={**raw_target.metadata, "intermediate_risk_reduction": True},
            )
        if force_scheduled:
            # Tag the decision origin on the FINAL target so both the normal
            # forced path and any entry-latch/reduction path carry it into the
            # persisted decision and the returned result.
            raw_target = replace(
                raw_target,
                metadata={**raw_target.metadata, "decision_origin": decision_origin},
            )
        if any(canonical_symbol(weight.symbol, self.market) not in self.symbols for weight in raw_target.weights):
            raise ValueError("Strategy emitted a symbol outside the active universe")
        maximum_allocation = self.maximum_allocation
        if self.mode == "live":
            maximum_allocation = min(maximum_allocation, self.live_dollar_cap / max(1.0, account.equity))
        risk = self.risk_overlay.apply(
            raw_target,
            account,
            market=self.market,
            approved_symbols=self.symbols,
            maximum_allocation=maximum_allocation,
            mode=self.mode,
        )
        rules = {symbol: self.broker.asset_trading_rules(symbol, self.market) for symbol in self.symbols}
        # The order idempotency key must be scoped to the account-local
        # decision identity (account + configuration digest + timestamp +
        # market), otherwise two configurations sharing a strategy name,
        # version and timestamp collide on the same client_order_id and the
        # second decision's orders are silently dropped by the repository.
        decision_id = _decision_id(self.account_id, self.configuration_digest, timestamp, self.market)
        planned = self.planner.plan(
            risk.target,
            account,
            market=self.market,
            prices=context.prices,
            rules=rules,
            approved_symbols=self.symbols,
            open_order_symbols=self.broker.all_open_order_symbols(),
            decision_id=decision_id,
        )
        observed_target = self._with_paper_observability(
            risk.target,
            risk,
            account,
            planned,
            entry_latch=entry_latch,
        )
        if observed_target is not risk.target:
            risk = replace(risk, target=observed_target)
        orders = tuple(PortfolioOrderRequest(**{**asdict(order), "decision_id": decision_id}) for order in planned)
        sells = tuple(order for order in orders if order.side == PortfolioOrderSide.SELL)
        buys = tuple(order for order in orders if order.side == PortfolioOrderSide.BUY)
        if not buys:
            # Pure risk-reducing decision: nothing depends on a sell outcome,
            # so the whole order set can be submitted immediately. Persist
            # decision + orders atomically so a crash cannot leave a
            # half-decision (decision without orders).
            decision_id = self.repository.record_decision_with_orders(
                self.session_id,
                self.account_id,
                self.configuration_digest,
                risk.target,
                risk,
                self.market,
                orders,
                initial_status="submitting",
            )
            # Orders are already persisted as submitting; submit them idempotently.
            submitted = self._submit_orders(orders)
            return PortfolioRuntimeResult(
                decision_id,
                risk.target,
                risk,
                orders,
                submitted,
                "submitted",
            )
        if sells:
            # Two-phase rebalance. Submit the risk-reducing sells now and
            # persist the dependent buys in the rebalance-execution state;
            # they are submitted only after every required sell is terminal
            # and a fresh account snapshot proves the exposure is safe.
            # Atomic: decision + sell orders + rebalance marker in one tx.
            decision_id = self.repository.record_decision_with_orders(
                self.session_id,
                self.account_id,
                self.configuration_digest,
                risk.target,
                risk,
                self.market,
                sells,
                initial_status="submitting",
                buy_orders_json=json.dumps([asdict(order) for order in buys], sort_keys=True, default=str),
                rebalance_phase=REBALANCE_PHASE_SELL_PENDING,
            )
            submitted = self._submit_orders(sells)
            return PortfolioRuntimeResult(
                decision_id,
                risk.target,
                risk,
                orders,
                submitted,
                "sell_phase_submitted",
            )
        # Pure BUY decision with no SELL leg: there is nothing to wait for,
        # so the BUY phase begins immediately. Atomic decision+orders.
        decision_id = self.repository.record_decision_with_orders(
            self.session_id,
            self.account_id,
            self.configuration_digest,
            risk.target,
            risk,
            self.market,
            buys,
            initial_status="submitting",
        )
        submitted = self._submit_orders(buys)
        return PortfolioRuntimeResult(
            decision_id,
            risk.target,
            risk,
            orders,
            submitted,
            "submitted",
        )

    def advance_rebalance(self) -> PortfolioRuntimeResult | None:
        """Advance a persisted two-phase rebalance by one step.

        ``sell_pending``: while any required SELL is still working (or its
        outcome is ambiguous), the dependent BUY orders stay deferred. When
        every required SELL is terminal, a fresh broker account snapshot is
        taken, the remaining BUY side is re-planned against the safe managed
        capacity and those BUY orders are submitted exactly once. A
        rejected/canceled/expired/partially-filled SELL therefore yields the
        re-planned (smaller) BUY quantities; an ambiguous SELL outcome blocks
        the rebalance with ``reconciliation_required`` and submits nothing.

        ``buy_pending``: once every deferred BUY is terminal the rebalance is
        marked complete.

        Returns the transition result, or ``None`` when the rebalance cannot
        advance yet (or nothing is in flight). This is safe to call from a
        trade-update callback, a bar decision and process startup, and is
        restart-safe because all phase transitions are persisted.
        """
        current = self.repository.current_rebalance_execution(self.account_id, self.market)
        if current is None or current.phase == REBALANCE_PHASE_COMPLETE:
            return None
        if current.phase == REBALANCE_PHASE_BLOCKED:
            return PortfolioRuntimeResult(current.decision_id, None, None, (), (), "rebalance_blocked")
        if current.phase == REBALANCE_PHASE_BUY_READY:
            return self._submit_deferred_buys(current.decision_id)
        if current.phase == REBALANCE_PHASE_BUY_PENDING:
            buys = self.repository.orders_for_decision(current.decision_id, side="buy")
            if buys and any(str(item["status"]) == "submitting" for item in buys):
                resolved = self._resolve_stuck_submission_rows(current.decision_id, buys, "buy")
                if resolved is None:
                    return None
                buys = resolved
            if buys and all(is_terminal_order_status(str(item["status"])) for item in buys):
                self.repository.update_rebalance_execution(current.decision_id, REBALANCE_PHASE_COMPLETE)
                return PortfolioRuntimeResult(current.decision_id, None, None, (), (), "rebalance_complete")
            return None
        # sell_pending: wait until every required sell is terminal.
        sells = self.repository.orders_for_decision(current.decision_id, side="sell")
        if not sells:
            # No sell rows recorded for a sell_pending rebalance: nothing can
            # ever become terminal, so re-plan the buys from the fresh state.
            self.repository.update_rebalance_execution(current.decision_id, REBALANCE_PHASE_BUY_READY)
            return self._submit_deferred_buys(current.decision_id)
        if any(str(item["status"]) == "submitting" for item in sells):
            # A crash between repository persistence and broker acknowledgement
            # leaves a row at ``submitting``. Resolve it against the broker so
            # a restart never wedges the rebalance; a broker that is
            # unreachable leaves the row for a later attempt.
            resolved = self._resolve_stuck_submission_rows(current.decision_id, sells, "sell")
            if resolved is None:
                return None
            sells = resolved
        statuses = [str(item["status"]) for item in sells]
        if requires_ambiguous_block(statuses):
            self.repository.update_rebalance_execution(
                current.decision_id,
                REBALANCE_PHASE_BLOCKED,
                status="reconciliation_required",
            )
            return PortfolioRuntimeResult(current.decision_id, None, None, (), (), "rebalance_blocked")
        if not is_sell_phase_complete(statuses):
            return None
        self.repository.update_rebalance_execution(current.decision_id, REBALANCE_PHASE_BUY_READY)
        return self._submit_deferred_buys(current.decision_id)

    def _resolve_stuck_submission_rows(
        self, decision_id: str, orders: tuple[dict[str, object], ...], side: str
    ) -> tuple[dict[str, object], ...] | None:
        """Resolve order rows left at ``submitting`` by a crash between
        repository persistence and broker acknowledgement.

        The broker's deterministic client-order-id lookup proves whether the
        order ever reached Alpaca: an unknown id is canceled (it can never
        fill), a filled order is recovered at its real price through the
        broker's fill-recovery callback, and a working order is marked
        submitted. Returns the re-read rows, or ``None`` when the broker was
        unreachable or lacks the lookup — the caller retries without
        guessing.
        """
        resolver = getattr(self.broker, "resolve_portfolio_submission", None)
        if resolver is None:
            return None
        changed = False
        for item in orders:
            if str(item["status"]) != "submitting":
                continue
            client_order_id = str(item["client_order_id"])
            try:
                outcome = resolver(client_order_id, str(item.get("broker_order_id") or ""))
            except Exception:
                # Broker unreachable: do not guess; retry on a later advance.
                continue
            if outcome is None:
                continue
            if isinstance(outcome, tuple):
                status, broker_order_id = outcome
            else:
                status, broker_order_id = outcome, str(item.get("broker_order_id") or "")
            self.repository.update_order(client_order_id, status, broker_order_id)
            changed = True
        if not changed:
            return orders
        return self.repository.orders_for_decision(decision_id, side=side)

    def _submit_deferred_buys(self, decision_id: str) -> PortfolioRuntimeResult | None:
        """Re-plan and submit the BUY side of a completed SELL phase.

        The original decision target is re-applied against a FRESH broker
        account snapshot so the submitted BUY quantities reflect the actual
        post-sell state: a rejected or partially-filled SELL leaves more
        exposure, so the re-planned BUY is smaller (or absent) instead of
        blindly replaying the original quantity.
        """
        decision = self.repository.decision_row(decision_id)
        if decision is None:
            self.repository.update_rebalance_execution(decision_id, REBALANCE_PHASE_BLOCKED)
            return None
        try:
            adjusted_target = TargetPortfolio.from_dict(json.loads(str(decision["risk_json"]))["target"])
        except (KeyError, TypeError, ValueError) as exc:
            log.error("Cannot restore rebalance target for %s: %s", decision_id, exc)
            self.repository.update_rebalance_execution(
                decision_id, REBALANCE_PHASE_BLOCKED, status="reconciliation_required"
            )
            return PortfolioRuntimeResult(decision_id, None, None, (), (), "rebalance_blocked")
        account = self.broker.account()
        maximum_allocation = self.maximum_allocation
        if self.mode == "live":
            maximum_allocation = min(maximum_allocation, self.live_dollar_cap / max(1.0, account.equity))
        risk = self.risk_overlay.apply(
            adjusted_target,
            account,
            market=self.market,
            approved_symbols=self.symbols,
            maximum_allocation=maximum_allocation,
            mode=self.mode,
        )
        prices: dict[str, float] = {symbol: series[-1].close for symbol, series in self.history.items() if series}
        for position in account.positions:
            prices.setdefault(canonical_symbol(position.symbol, position.market), position.current_price)
        # Restart recovery can run before warmup repopulated history; the
        # decision-time reference prices of the deferred BUY orders are the
        # authoritative fallback so the BUY side is never lost.
        for stored in self.repository.rebalance_buy_orders(decision_id):
            symbol = str(stored.get("symbol", ""))
            reference = stored.get("reference_price")
            if symbol and reference is not None:
                prices.setdefault(symbol, float(str(reference)))
        rules = {symbol: self.broker.asset_trading_rules(symbol, self.market) for symbol in self.symbols}
        planned = self.planner.plan(
            risk.target,
            account,
            market=self.market,
            prices=prices,
            rules=rules,
            approved_symbols=self.symbols,
            open_order_symbols=self.broker.all_open_order_symbols(),
            decision_id=decision_id,
        )
        buy_orders = tuple(
            PortfolioOrderRequest(**{**asdict(order), "decision_id": decision_id})
            for order in planned
            if order.side == PortfolioOrderSide.BUY
        )
        if not buy_orders:
            self.repository.update_rebalance_execution(decision_id, REBALANCE_PHASE_COMPLETE)
            return PortfolioRuntimeResult(decision_id, risk.target, risk, (), (), "rebalance_complete")
        buy_orders = self._size_buys_to_safe_capacity(buy_orders, account)
        if not buy_orders:
            log.warning(
                "Deferred BUY phase has no safe capacity; decision=%s submits nothing",
                decision_id,
            )
            self.repository.update_rebalance_execution(decision_id, REBALANCE_PHASE_COMPLETE)
            return PortfolioRuntimeResult(decision_id, risk.target, risk, (), (), "rebalance_complete")
        submitted = self._submit_orders(buy_orders)
        self.repository.update_rebalance_execution(decision_id, REBALANCE_PHASE_BUY_PENDING)
        return PortfolioRuntimeResult(
            decision_id,
            risk.target,
            risk,
            buy_orders,
            submitted,
            "buy_phase_submitted",
        )

    def _size_buys_to_safe_capacity(
        self,
        buy_orders: tuple[PortfolioOrderRequest, ...],
        account: AccountSnapshot,
    ) -> tuple[PortfolioOrderRequest, ...]:
        """Cap the re-planned BUY side by what the fresh account can absorb.

        A rejected/canceled/expired/partial SELL leaves the account with more
        exposure and less cash than the original decision assumed. The BUY
        phase must therefore be re-sized against three hard constraints, all
        evaluated on the FRESH broker snapshot:

        * cash: buy notional must not exceed available cash;
        * maximum gross: current broker gross + buy notional must stay within
          the configured maximum gross exposure;
        * live hard dollar cap: managed exposure after the buys must stay
          within the authorized cap.

        Orders that fall below the broker minimum after scaling are dropped;
        if nothing remains, the BUY phase submits nothing. Margin is never
        used as a risk control.
        """
        prices: dict[str, float] = {order.symbol: order.reference_price for order in buy_orders}
        for position in account.positions:
            prices.setdefault(canonical_symbol(position.symbol, position.market), position.current_price)
        equity = max(1e-9, account.equity)
        current_gross = sum(position.market_value for position in account.positions if not position.is_flat)
        managed_value = sum(
            abs(position.managed_qty) * position.current_price
            for position in account.positions
            if not position.is_flat and position.managed_qty > 1e-12
        )
        limits = self.risk_overlay.limits
        gross_capacity = max(0.0, limits.maximum_gross * equity - current_gross)
        cash_capacity = max(0.0, account.cash)
        if self.mode == "live":
            cash_capacity = min(cash_capacity, max(0.0, self.live_dollar_cap - managed_value))
        capacity = min(gross_capacity, cash_capacity)
        notional = sum(order.quantity * prices.get(order.symbol, order.reference_price) for order in buy_orders)
        if notional <= 0:
            return ()
        if notional > capacity:
            scale = capacity / notional
            if scale <= 0:
                return ()
            buy_orders = tuple(
                PortfolioOrderRequest(
                    **{
                        **asdict(order),
                        "quantity": floor_increment(
                            order.quantity * scale,
                            self.broker.asset_trading_rules(order.symbol, self.market).quantity_increment,
                        ),
                    }
                )
                for order in buy_orders
            )
        sized: list[PortfolioOrderRequest] = []
        for order in buy_orders:
            rule = self.broker.asset_trading_rules(order.symbol, self.market)
            price = prices.get(order.symbol, order.reference_price)
            if order.quantity < rule.minimum_order_size:
                continue
            if order.quantity * price < rule.minimum_order_notional:
                continue
            sized.append(order)
        return tuple(sized)

    def _submit_orders(self, orders: tuple[PortfolioOrderRequest, ...]) -> tuple[str, ...]:
        """Persist and submit one order batch with broker idempotency keys.

        The client order id is deterministic ``parayabuck-{decision_id}-{symbol}-{side}``
        so a crash between persistence and broker acknowledgement cannot produce a
        duplicate broker order (Alpaca rejects a reused client id and the
        broker's ambiguity recovery resolves the existing order). Duplicate
        decisions produce the same id and ``record_order`` is ``INSERT OR IGNORE``
        so only one logical order exists; restart never duplicates, a partial
        fill leaves the same key and the re-planned residual is the only new
        work, and a broker timeout marks ``reconciliation_required`` and
        requires ``reconcile()`` before retry.
        """
        submitted: list[str] = []
        for order in orders:
            # Idempotency: if the logical order already exists with a terminal
            # or submitted status, do not submit a duplicate broker order.
            existing = self.repository.orders_for_decision(order.decision_id)
            already = next((o for o in existing if str(o.get("client_order_id")) == order.client_order_id), None)
            if already is not None:
                status = str(already.get("status", ""))
                if status in {"submitted", "filled", "partial_fill", "canceled", "rejected", "expired"}:
                    continue
                if is_terminal_order_status(status) or status in _AMBIGUOUS_ORDER_STATUSES:
                    continue
            self.repository.record_order(order, "submitting")
            try:
                broker_order_id = self.broker.submit_portfolio_order(order)
            except Exception:
                self.repository.update_order(order.client_order_id, "reconciliation_required")
                raise
            self.repository.update_order(order.client_order_id, "submitted", broker_order_id)
            submitted.append(broker_order_id)
        return tuple(submitted)

    def close(self, status: str = "stopped") -> None:
        self.repository.end_session(self.session_id, status)
