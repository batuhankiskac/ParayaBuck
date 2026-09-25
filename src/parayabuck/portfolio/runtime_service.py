"""Production portfolio decision runtime shared by paper and live execution."""

from __future__ import annotations

import json
import math
from dataclasses import asdict, replace
from datetime import UTC, datetime
from typing import Any

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
    reserve_opening_order_slots,
)
from parayabuck.logger import get_logger
from parayabuck.markets.symbols import canonical_symbol
from parayabuck.markets.timeframes import bar_is_closed, equity_day_bar_is_closed
from parayabuck.portfolio import PRODUCT_STRATEGY_NAMES, rebalance_policy_for
from parayabuck.portfolio.base import (
    PortfolioStrategy,
    require_product_universe,
    require_product_universe_dependency,
)
from parayabuck.portfolio.models import TargetPortfolio, TargetWeight
from parayabuck.portfolio.runtime_models import (
    _AMBIGUOUS_ORDER_STATUSES,
    REBALANCE_PHASE_BLOCKED,
    REBALANCE_PHASE_BUY_PENDING,
    REBALANCE_PHASE_BUY_READY,
    REBALANCE_PHASE_COMPLETE,
    REBALANCE_PHASE_SELL_PENDING,
    TERMINAL_ORDER_STATUSES_SQL,
    BuyAdmission,
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

# Observability hook — isolated, never raises, lazy import inside functions to avoid cycles
try:
    from parayabuck.monitoring import observe as _observe
except Exception:  # pragma: no cover
    _observe = None  # type: ignore

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


def _emit_decision_summary_safe(
    *,
    decision_id: str,
    market: str | None = None,
    strategy: str | None = None,
    session_id: str | None = None,
    account_id: str | None = None,
    universe_version: str | None = None,
    planned: list[Any] | tuple[Any, ...] | None = None,
    submitted: list[Any] | tuple[Any, ...] | None = None,
    no_trade_reason: str | None = None,
) -> None:
    try:
        if _observe is None:
            return
        # Determine counts
        sells = 0
        buys = 0
        if planned is not None:
            try:
                sells = sum(
                    1
                    for o in planned
                    if getattr(o, "side", None) == "SELL"
                    or getattr(o, "side", None) == "sell"
                    or (isinstance(o, dict) and o.get("side") == "SELL")
                )
                buys = sum(
                    1
                    for o in planned
                    if getattr(o, "side", None) == "BUY"
                    or getattr(o, "side", None) == "buy"
                    or (isinstance(o, dict) and o.get("side") == "BUY")
                )
                # fallback for PortfolioOrderRequest
                if sells == 0 and buys == 0:
                    # try string comparison
                    for o in planned:
                        s = str(getattr(o, "side", "")).upper()
                        if s == "SELL":
                            sells += 1
                        elif s == "BUY":
                            buys += 1
            except Exception:
                pass
        sub = len(submitted) if submitted is not None else 0
        # map no-trade reason if no orders
        if not planned and not no_trade_reason:
            no_trade_reason = "NO_TARGET_CHANGE"
        elif planned is not None and len(planned) == 0 and not no_trade_reason:
            no_trade_reason = "NO_TARGET_CHANGE"
        _observe.on_decision_summary(
            decision_id=decision_id,
            market=str(market) if market else None,
            strategy=strategy,
            session_id=session_id,
            account_id=account_id,
            universe_version=universe_version,
            planned_sell_count=sells,
            planned_buy_count=buys,
            submitted_count=sub,
            no_trade_reason=no_trade_reason,
        )
    except Exception:
        pass


def _emit_rebalance_phase_safe(
    decision_id: str,
    from_phase: str | None,
    to_phase: str | None,
    market: str | None = None,
    account_id: str | None = None,
    session_id: str | None = None,
) -> None:
    try:
        if _observe is None:
            return
        _observe.on_rebalance_phase_changed(
            decision_id=decision_id,
            market=str(market) if market else None,
            account_id=account_id,
            session_id=session_id,
            from_phase=from_phase,
            to_phase=to_phase,
        )
    except Exception:
        pass


_no_trade_last: dict[str, str] = {}


def _emit_no_trade_safe(
    *,
    market: str | None,
    strategy: str | None,
    reason: str,
    decision_timestamp: datetime | None = None,
    data_timestamp: datetime | None = None,
    warming_symbols: tuple[str, ...] | None = None,
    missing_symbols: tuple[str, ...] | None = None,
    last_timestamps: dict[str, str] | None = None,
    next_boundary_after: str | None = None,
    target_weights: tuple[Any, ...] | None = None,
    risk_reasons: tuple[str, ...] | None = None,
    available_capacity: dict[str, float] | None = None,
    entry_latch: str | None = None,
    session_id: str | None = None,
    account_id: str | None = None,
) -> None:
    """Emit persistent portfolio_no_trade event, throttled by reason.

    Fields cover report's acceptance list: market, strategy, karar/veri zamanı,
    eksik/ısınan semboller, her sembolün son zaman damgası, bir sonraki 96h sınırı,
    hedef ve risk sonrası ağırlık, kapasite, engelleyen latch.
    Repeated identical reason for same market/strategy is suppressed (dedup).
    """
    try:
        key = f"{market}:{strategy}:{reason}"
        # Suppress exact duplicate consecutive emits (same market/strategy/reason)
        if _no_trade_last.get(key) == reason:
            # Still emit if more than an hour has passed? For now suppress.
            return
        _no_trade_last[key] = reason
        from parayabuck.monitoring.journal import emit_journal
        from parayabuck.monitoring.operational_event import create_event

        # Use deterministic id per market/strategy/reason/day so journal dedups
        day = (decision_timestamp or datetime.now(UTC)).date().isoformat()
        det_extra = f"{market}:{strategy}:{reason}:{day}"
        fields: dict[str, Any] = {
            "reason": reason,
            "decision_timestamp": decision_timestamp.isoformat() if decision_timestamp else None,
            "data_timestamp": data_timestamp.isoformat() if data_timestamp else None,
            "warming_symbols": list(warming_symbols or ()),
            "missing_symbols": list(missing_symbols or ()),
            "last_timestamps": last_timestamps or {},
            "next_boundary_after": next_boundary_after,
            "target_weights": [str(w) for w in (target_weights or ())],
            "risk_reasons": list(risk_reasons or ()),
            "available_capacity": available_capacity or {},
            "entry_latch": entry_latch,
        }
        # Human readable message for dashboard top-level
        # Example: "Kripto alınmadı: BTC/ETH/SOL günlük veri zamanları diğer sembollerle eşleşmiyor."
        reason_tr: dict[str, str] = {
            "warming_up": "veriler ısınıyor (yeterli geçmiş yok)",
            "incomplete_strategy_batch": "günlük veri zamanları diğer sembollerle eşleşmiyor",
            "not_scheduled": "rebalance zamanı değil (96 saat döngüsü)",
            "duplicate_decision": "aynı zaman damgası için zaten karar var",
            "benchmark_not_ready": "benchmark BTC verisi hazır değil",
            "incomplete_managed_batch": "yönetilen pozisyonların verisi eksik",
            "exposure_scaled_to_limit": "sermaye limiti nedeniyle hedef küçültüldü",
            "rebalance_in_progress": "önceki rebalance henüz tamamlanmadı",
            "rebalance_blocked": "satış fazı takılı",
            "account_snapshot_stale": "hesap görüntüsü eski",
            "circuit_breaker": "devre kesici aktif",
        }
        tr = reason_tr.get(reason, reason)
        market_tr = (
            "Kripto"
            if (market or "").lower() == "crypto"
            else "Hisse"
            if (market or "").lower() == "equity"
            else (market or "Piyasa")
        )
        if missing_symbols:
            detail = "/".join(missing_symbols[:3])
            msg = f"{market_tr} alınmadı: {detail} {tr}."
        else:
            msg = f"{market_tr} alınmadı: {tr}."
        evt = create_event(
            kind="portfolio_no_trade",
            message=msg,
            market=market,
            strategy=strategy,
            session_id=session_id,
            account_id=account_id,
            deterministic_extra=det_extra,
            fields=fields,
        )
        emit_journal(evt)
    except Exception:
        pass


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
        ordinary_alpha_excluded_symbols: tuple[str, ...] = (),
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
        if strategy.name in PRODUCT_STRATEGY_NAMES and not allow_static_universe:
            # Composition invariant: reject a missing/invalid PIT universe
            # BEFORE repository.begin_session creates a misleading running
            # session. Static test contexts must opt in explicitly.
            require_product_universe_dependency(universe, universe_version)
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
        self.ordinary_alpha_excluded_symbols = tuple(
            dict.fromkeys(canonical_symbol(symbol, strategy.market) for symbol in ordinary_alpha_excluded_symbols)
        )
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
        self.last_buy_admission: BuyAdmission | None = None

    def _with_paper_observability(
        self,
        target: TargetPortfolio,
        risk: RiskOverlayDecision,
        account: AccountSnapshot,
        orders: tuple[PortfolioOrderRequest, ...],
        *,
        entry_latch: str | None,
    ) -> TargetPortfolio:
        """Attach equity PAPER diagnostics after decisions are complete.

        Every value here is derived from already-computed targets, the account
        snapshot, risk result, or planned orders. It is persisted for
        operations and deliberately cannot participate in strategy math.
        """
        if self.market != Market.EQUITY:
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
                    "trend_state": target.metadata.get("trend_state"),
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

    def startup_catch_up(
        self,
        *,
        entry_latch: str | None = None,
        account: AccountSnapshot | None = None,
        reserved_position_keys: tuple[tuple[Market, str], ...] = (),
    ) -> PortfolioRuntimeResult:
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
            # Visibility: persistent no-trade event
            warming = tuple(
                sorted(s for s, ser in self.history.items() if len(ser) < self.strategy.minimum_history_bars)
            )
            last_ts = {s: (ser[-1].timestamp.isoformat() if ser else "none") for s, ser in self.history.items()}
            try:
                next_boundary = (
                    self.policy.next_boundary_after(
                        max((t for t in timestamps if t is not None), default=self.startup_catch_up_at)
                    ).isoformat()
                    if timestamps and any(timestamps)
                    else None
                )
            except Exception:
                next_boundary = None
            _emit_no_trade_safe(
                market=self.market.value if hasattr(self.market, "value") else str(self.market),
                strategy=getattr(self.strategy, "name", None),
                reason="warming_up",
                decision_timestamp=self.startup_catch_up_at,
                data_timestamp=max((t for t in timestamps if t is not None), default=None),
                warming_symbols=warming,
                last_timestamps=last_ts,
                next_boundary_after=next_boundary,
                session_id=self.session_id,
                account_id=self.account_id,
                entry_latch=entry_latch,
            )
            return PortfolioRuntimeResult(None, None, None, (), (), "warming_up")
        if len(set(timestamps)) != 1:
            self.startup_catch_up_status = "incomplete_strategy_batch"
            last_ts = {s: (ser[-1].timestamp.isoformat() if ser else "none") for s, ser in self.history.items()}
            _emit_no_trade_safe(
                market=self.market.value if hasattr(self.market, "value") else str(self.market),
                strategy=getattr(self.strategy, "name", None),
                reason="incomplete_strategy_batch",
                decision_timestamp=self.startup_catch_up_at,
                data_timestamp=max((t for t in timestamps if t is not None), default=None),
                missing_symbols=tuple(
                    sorted(
                        {
                            s
                            for s, ser in self.history.items()
                            if not ser or ser[-1].timestamp != max(t for t in timestamps if t is not None)
                        }
                    )
                ),
                last_timestamps=last_ts,
                session_id=self.session_id,
                account_id=self.account_id,
                entry_latch=entry_latch,
            )
            return PortfolioRuntimeResult(None, None, None, (), (), "incomplete_strategy_batch")
        timestamp = timestamps[0]
        assert timestamp is not None
        if self.repository.has_decision(self.account_id, self.configuration_digest, timestamp, self.market):
            self.startup_catch_up_status = "duplicate_decision"
            _emit_no_trade_safe(
                market=self.market.value if hasattr(self.market, "value") else str(self.market),
                strategy=getattr(self.strategy, "name", None),
                reason="duplicate_decision",
                decision_timestamp=timestamp,
                data_timestamp=timestamp,
                session_id=self.session_id,
                account_id=self.account_id,
            )
            return PortfolioRuntimeResult(None, None, None, (), (), "duplicate_decision")
        if (
            self.repository.latest_decision_timestamp(self.account_id, self.configuration_digest, self.market)
            is not None
        ):
            # Any prior decision for this deployment means the cadence is
            # already running: a catch-up would be an out-of-cadence re-entry.
            self.startup_catch_up_status = "catch_up_superseded"
            _emit_no_trade_safe(
                market=self.market.value if hasattr(self.market, "value") else str(self.market),
                strategy=getattr(self.strategy, "name", None),
                reason="duplicate_decision",
                decision_timestamp=timestamp,
                data_timestamp=timestamp,
                session_id=self.session_id,
                account_id=self.account_id,
            )
            return PortfolioRuntimeResult(None, None, None, (), (), "catch_up_superseded")
        if account is None:
            account = self.broker.account()
        if any(
            position.market == self.market and not position.is_flat and position.managed_qty > 1e-12
            for position in account.positions
        ):
            # Already invested in this market: never layer a catch-up entry on
            # top of an existing managed position.
            self.startup_catch_up_status = "already_invested"
            _emit_no_trade_safe(
                market=self.market.value if hasattr(self.market, "value") else str(self.market),
                strategy=getattr(self.strategy, "name", None),
                reason="already_invested",
                decision_timestamp=timestamp,
                data_timestamp=timestamp,
                session_id=self.session_id,
                account_id=self.account_id,
            )
            return PortfolioRuntimeResult(None, None, None, (), (), "already_invested")
        # Let a persisted two-phase rebalance advance first; a still-active
        # rebalance defers the catch-up (submitting fresh BUYs while an older
        # SELL is working could stack risk-increasing exposure).
        advance_result = self.advance_rebalance(
            account=account,
            reserved_position_keys=reserved_position_keys,
        )
        current_rebalance = self.repository.current_rebalance_execution(self.account_id, self.market)
        if current_rebalance is not None and current_rebalance.phase != REBALANCE_PHASE_COMPLETE:
            status = advance_result.status if advance_result is not None else "rebalance_in_progress"
            reason = "rebalance_blocked" if status == "rebalance_blocked" else "rebalance_in_progress"
            self.startup_catch_up_status = status
            _emit_no_trade_safe(
                market=self.market.value if hasattr(self.market, "value") else str(self.market),
                strategy=getattr(self.strategy, "name", None),
                reason=reason,
                decision_timestamp=timestamp,
                data_timestamp=timestamp,
                session_id=self.session_id,
                account_id=self.account_id,
            )
            return PortfolioRuntimeResult(
                current_rebalance.decision_id,
                None,
                None,
                advance_result.orders if advance_result is not None else (),
                advance_result.submitted_order_ids if advance_result is not None else (),
                status,
            )
        bars = [series[-1] for series in series_by_symbol.values()]
        result = self.process_closed_bars(
            bars,
            entry_latch=entry_latch,
            force_scheduled=True,
            decision_origin="startup_catch_up",
            account=account,
            reserved_position_keys=reserved_position_keys,
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
        reserved_position_keys: tuple[tuple[Market, str], ...] = (),
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
            last_ts = {s: (ser[-1].timestamp.isoformat() if ser else "none") for s, ser in self.history.items()}
            try:
                nb = self.policy.next_boundary_after(timestamp).isoformat() if timestamp else None
            except Exception:
                nb = None
            _emit_no_trade_safe(
                market=self.market.value if hasattr(self.market, "value") else str(self.market),
                strategy=getattr(self.strategy, "name", None),
                reason="warming_up",
                decision_timestamp=timestamp,
                data_timestamp=timestamp,
                warming_symbols=warming_symbols,
                last_timestamps=last_ts,
                next_boundary_after=nb,
                session_id=self.session_id,
                account_id=self.account_id,
                entry_latch=entry_latch,
            )
            return PortfolioRuntimeResult(None, None, None, (), (), "warming_up")
        if missing_symbols:
            last_ts = {s: (ser[-1].timestamp.isoformat() if ser else "none") for s, ser in self.history.items()}
            _emit_no_trade_safe(
                market=self.market.value if hasattr(self.market, "value") else str(self.market),
                strategy=getattr(self.strategy, "name", None),
                reason="incomplete_strategy_batch",
                decision_timestamp=timestamp,
                data_timestamp=timestamp,
                missing_symbols=missing_symbols,
                last_timestamps=last_ts,
                session_id=self.session_id,
                account_id=self.account_id,
                entry_latch=entry_latch,
            )
            return PortfolioRuntimeResult(None, None, None, (), (), "incomplete_strategy_batch")
        if self.repository.has_decision(self.account_id, self.configuration_digest, timestamp, self.market):
            _emit_no_trade_safe(
                market=self.market.value if hasattr(self.market, "value") else str(self.market),
                strategy=getattr(self.strategy, "name", None),
                reason="duplicate_decision",
                decision_timestamp=timestamp,
                data_timestamp=timestamp,
                session_id=self.session_id,
                account_id=self.account_id,
                entry_latch=entry_latch,
            )
            return PortfolioRuntimeResult(None, None, None, (), (), "duplicate_decision")
        # A persisted two-phase rebalance may have become ready since the last
        # event (trade update, restart, or reconcile). Advance it first, then
        # defer any new decision while the previous rebalance is still in
        # flight: submitting a fresh BUY while an older SELL is working could
        # stack risk-increasing exposure on top of the not-yet-sold position.
        advance_result = self.advance_rebalance(
            account=account,
            reserved_position_keys=reserved_position_keys,
        )
        # A terminal deferred BUY has just completed the persisted rebalance.
        # End this cycle before planning again: the caller's account snapshot
        # may predate that fill, so a same-cycle decision could replay the BUY.
        if advance_result is not None and advance_result.status == "rebalance_complete":
            return advance_result
        current_rebalance = self.repository.current_rebalance_execution(self.account_id, self.market)
        if current_rebalance is not None and current_rebalance.phase != REBALANCE_PHASE_COMPLETE:
            if current_rebalance.phase == REBALANCE_PHASE_BLOCKED:
                _emit_decision_summary_safe(
                    decision_id=current_rebalance.decision_id,
                    market=self.market.value if hasattr(self.market, "value") else str(self.market),
                    strategy=getattr(self, "strategy_name", None)
                    or getattr(getattr(self, "strategy", None), "name", None),
                    session_id=self.session_id,
                    account_id=self.account_id,
                    universe_version=self.universe_version,
                    planned=(),
                    submitted=(),
                    no_trade_reason="SELL_PHASE_PENDING",
                )
                _emit_no_trade_safe(
                    market=self.market.value if hasattr(self.market, "value") else str(self.market),
                    strategy=getattr(self.strategy, "name", None),
                    reason="rebalance_blocked",
                    decision_timestamp=timestamp,
                    data_timestamp=timestamp,
                    session_id=self.session_id,
                    account_id=self.account_id,
                )
                return PortfolioRuntimeResult(current_rebalance.decision_id, None, None, (), (), "rebalance_blocked")
            _emit_decision_summary_safe(
                decision_id=current_rebalance.decision_id,
                market=self.market.value if hasattr(self.market, "value") else str(self.market),
                strategy=getattr(self, "strategy_name", None) or getattr(getattr(self, "strategy", None), "name", None),
                session_id=self.session_id,
                account_id=self.account_id,
                universe_version=self.universe_version,
                planned=(),
                submitted=(),
                no_trade_reason="SELL_PHASE_PENDING",
            )
            _emit_no_trade_safe(
                market=self.market.value if hasattr(self.market, "value") else str(self.market),
                strategy=getattr(self.strategy, "name", None),
                reason="rebalance_in_progress",
                decision_timestamp=timestamp,
                data_timestamp=timestamp,
                session_id=self.session_id,
                account_id=self.account_id,
            )
            return PortfolioRuntimeResult(
                current_rebalance.decision_id,
                None,
                None,
                advance_result.orders if advance_result is not None else (),
                advance_result.submitted_order_ids if advance_result is not None else (),
                "rebalance_in_progress",
            )
        # maximum_account_age_for_entry_seconds). Direct callers (tests,
        # startup catch-up) omit it and get a fresh broker fetch, preserving
        # the historical behavior exactly.
        ready_history = dict(self.history)
        account = account or self.broker.account()
        managed = {
            canonical_symbol(position.symbol, position.market)
            for position in account.positions
            if position.market == self.market and not position.is_flat and position.managed_qty > 1e-12
        }
        if not managed.issubset(batch_symbols):
            missing_managed = managed - batch_symbols
            _emit_no_trade_safe(
                market=self.market.value if hasattr(self.market, "value") else str(self.market),
                strategy=getattr(self.strategy, "name", None),
                reason="incomplete_managed_batch",
                decision_timestamp=timestamp,
                data_timestamp=timestamp,
                missing_symbols=tuple(sorted(missing_managed)),
                session_id=self.session_id,
                account_id=self.account_id,
                entry_latch=entry_latch,
            )
            return PortfolioRuntimeResult(None, None, None, (), (), "incomplete_managed_batch")
        if self.market == Market.CRYPTO and "BTC/USD" not in ready_history:
            _emit_no_trade_safe(
                market=self.market.value if hasattr(self.market, "value") else str(self.market),
                strategy=getattr(self.strategy, "name", None),
                reason="benchmark_not_ready",
                decision_timestamp=timestamp,
                data_timestamp=timestamp,
                missing_symbols=("BTC/USD",),
                session_id=self.session_id,
                account_id=self.account_id,
            )
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
            _emit_no_trade_safe(
                market=self.market.value if hasattr(self.market, "value") else str(self.market),
                strategy=getattr(self.strategy, "name", None),
                reason="not_scheduled",
                decision_timestamp=timestamp,
                data_timestamp=timestamp,
                next_boundary_after=self.policy.next_boundary_after(timestamp).isoformat()
                if hasattr(self.policy, "next_boundary_after")
                else None,
                session_id=self.session_id,
                account_id=self.account_id,
            )
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
            ordinary_alpha_excluded_symbols=self.ordinary_alpha_excluded_symbols,
        )
        # Fail-closed PAPER product universe guard: dynamic product
        # strategies must execute against a PIT universe that exposes
        # ``contains`` and carries a non-empty ``universe_version``.
        # A missing universe would otherwise make in_universe() return True
        # for every symbol (permissive fallback). Static/non-product test
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
            _emit_no_trade_safe(
                market=self.market.value if hasattr(self.market, "value") else str(self.market),
                strategy=getattr(self.strategy, "name", None),
                reason="not_scheduled",
                decision_timestamp=timestamp,
                data_timestamp=timestamp,
                entry_latch=entry_latch,
                session_id=self.session_id,
                account_id=self.account_id,
            )
            return PortfolioRuntimeResult(None, None, None, (), (), "not_scheduled")
        raw_target = self.strategy.decide(context)
        if entry_latch is not None:
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
        reserved_slots = self._effective_reserved_position_keys(reserved_position_keys)
        open_order_symbols, open_order_sides = self._effective_open_order_constraints()
        risk = self.risk_overlay.apply(
            raw_target,
            account,
            market=self.market,
            approved_symbols=self.symbols,
            maximum_allocation=maximum_allocation,
            mode=self.mode,
            reserved_position_keys=reserved_slots,
            open_order_symbols=tuple(sorted(open_order_symbols)),
        )
        # Visibility: if risk overlay scaled to limit and left no managed
        # exposure, emit persistent no-trade (e.g., 6×10% equity blocks crypto).
        if "exposure_scaled_to_limit" in risk.reasons and sum(w.weight for w in risk.target.weights) < 1e-9:
            # Compute capacity for diagnostics
            try:
                gross_before = risk.gross_before
                gross_after = risk.gross_after
            except Exception:
                gross_before = gross_after = 0.0
            _emit_no_trade_safe(
                market=self.market.value if hasattr(self.market, "value") else str(self.market),
                strategy=getattr(self.strategy, "name", None),
                reason="exposure_scaled_to_limit",
                decision_timestamp=timestamp,
                data_timestamp=timestamp,
                target_weights=tuple(risk.target.weights),
                risk_reasons=risk.reasons,
                available_capacity={"gross_before": float(gross_before), "gross_after": float(gross_after)},
                session_id=self.session_id,
                account_id=self.account_id,
                entry_latch=entry_latch,
            )
        # Entry latch blocking check: if latch present and no buys planned
        elif entry_latch is not None and not any(w.weight > 1e-9 for w in risk.target.weights):
            _emit_no_trade_safe(
                market=self.market.value if hasattr(self.market, "value") else str(self.market),
                strategy=getattr(self.strategy, "name", None),
                reason=entry_latch.split("|")[0] if "|" in str(entry_latch) else str(entry_latch),
                decision_timestamp=timestamp,
                data_timestamp=timestamp,
                target_weights=tuple(risk.target.weights),
                risk_reasons=risk.reasons,
                session_id=self.session_id,
                account_id=self.account_id,
                entry_latch=entry_latch,
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
            open_order_symbols=open_order_symbols,
            open_order_sides=open_order_sides,
            decision_id=decision_id,
        )
        if not any(order.side == PortfolioOrderSide.SELL for order in planned):
            planned = reserve_opening_order_slots(
                planned,
                account,
                maximum_positions=self.risk_overlay.limits.maximum_positions,
                market_position_limits={Market.CRYPTO: self.risk_overlay.limits.maximum_crypto_positions},
                reserved_position_keys=reserved_slots,
                open_order_symbols=tuple(sorted(open_order_symbols)),
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
            _emit_decision_summary_safe(
                decision_id=decision_id,
                market=self.market.value if hasattr(self.market, "value") else str(self.market),
                strategy=getattr(self, "strategy_name", None) or getattr(getattr(self, "strategy", None), "name", None),
                session_id=self.session_id,
                account_id=self.account_id,
                universe_version=self.universe_version,
                planned=orders,
                submitted=submitted,
            )
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
            _emit_decision_summary_safe(
                decision_id=decision_id,
                market=self.market.value if hasattr(self.market, "value") else str(self.market),
                strategy=getattr(self, "strategy_name", None) or getattr(getattr(self, "strategy", None), "name", None),
                session_id=self.session_id,
                account_id=self.account_id,
                universe_version=self.universe_version,
                planned=orders,
                submitted=submitted,
            )
            _emit_rebalance_phase_safe(
                decision_id,
                None,
                REBALANCE_PHASE_SELL_PENDING,
                market=self.market.value if hasattr(self.market, "value") else str(self.market),
                account_id=self.account_id,
                session_id=self.session_id,
            )
            return PortfolioRuntimeResult(
                decision_id,
                risk.target,
                risk,
                orders,
                submitted,
                "sell_phase_submitted",
            )
        # Pure BUY decisions still require current account-wide gross/cash
        # headroom. Size BEFORE persisting candidate rows: a suppressed BUY
        # must not become a phantom committed order.
        admission = self._size_buys_to_safe_capacity(buys)
        buys = reserve_opening_order_slots(
            admission.orders,
            admission.account,
            maximum_positions=self.risk_overlay.limits.maximum_positions,
            market_position_limits={Market.CRYPTO: self.risk_overlay.limits.maximum_crypto_positions},
            reserved_position_keys=self._effective_reserved_position_keys(reserved_position_keys),
            open_order_symbols=tuple(sorted(self.broker.all_open_order_symbols())),
        )
        orders = buys
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
        self._record_buy_admission_observation(decision_id, admission, buys)
        submitted = self._submit_orders(buys)
        _emit_decision_summary_safe(
            decision_id=decision_id,
            market=self.market.value if hasattr(self.market, "value") else str(self.market),
            strategy=getattr(self, "strategy_name", None) or getattr(getattr(self, "strategy", None), "name", None),
            session_id=self.session_id,
            account_id=self.account_id,
            universe_version=self.universe_version,
            planned=orders,
            submitted=submitted,
        )
        return PortfolioRuntimeResult(
            decision_id,
            risk.target,
            risk,
            orders,
            submitted,
            "submitted",
        )

    def advance_rebalance(
        self,
        *,
        account: AccountSnapshot | None = None,
        reserved_position_keys: tuple[tuple[Market, str], ...] = (),
    ) -> PortfolioRuntimeResult | None:
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
            return self._submit_deferred_buys(
                current.decision_id,
                account=account,
                reserved_position_keys=reserved_position_keys,
            )
        if current.phase == REBALANCE_PHASE_BUY_PENDING:
            buys = self.repository.orders_for_decision(current.decision_id, side="buy")
            if buys and any(str(item["status"]) == "submitting" for item in buys):
                resolved = self._resolve_stuck_submission_rows(current.decision_id, buys, "buy")
                if resolved is None:
                    return None
                buys = resolved
            if buys and all(is_terminal_order_status(str(item["status"])) for item in buys):
                self.repository.update_rebalance_execution(current.decision_id, REBALANCE_PHASE_COMPLETE)
                _emit_rebalance_phase_safe(
                    current.decision_id,
                    REBALANCE_PHASE_BUY_PENDING,
                    REBALANCE_PHASE_COMPLETE,
                    market=self.market.value if hasattr(self.market, "value") else str(self.market),
                    account_id=self.account_id,
                    session_id=self.session_id,
                )
                return PortfolioRuntimeResult(current.decision_id, None, None, (), (), "rebalance_complete")
            return None
        # sell_pending: wait until every required sell is terminal.
        sells = self.repository.orders_for_decision(current.decision_id, side="sell")
        if not sells:
            # No sell rows recorded for a sell_pending rebalance: nothing can
            # ever become terminal, so re-plan the buys from the fresh state.
            self.repository.update_rebalance_execution(current.decision_id, REBALANCE_PHASE_BUY_READY)
            return self._submit_deferred_buys(
                current.decision_id,
                account=account,
                reserved_position_keys=reserved_position_keys,
            )
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
        return self._submit_deferred_buys(
            current.decision_id,
            account=account,
            reserved_position_keys=reserved_position_keys,
        )

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

    def _submit_deferred_buys(
        self,
        decision_id: str,
        *,
        account: AccountSnapshot | None = None,
        reserved_position_keys: tuple[tuple[Market, str], ...] = (),
    ) -> PortfolioRuntimeResult | None:
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
            _emit_rebalance_phase_safe(
                decision_id,
                REBALANCE_PHASE_BUY_READY,
                REBALANCE_PHASE_BLOCKED,
                market=self.market.value if hasattr(self.market, "value") else str(self.market),
                account_id=self.account_id,
                session_id=self.session_id,
            )
            return None
        try:
            adjusted_target = TargetPortfolio.from_dict(json.loads(str(decision["risk_json"]))["target"])
        except (KeyError, TypeError, ValueError) as exc:
            log.error("Cannot restore rebalance target for %s: %s", decision_id, exc)
            self.repository.update_rebalance_execution(
                decision_id, REBALANCE_PHASE_BLOCKED, status="reconciliation_required"
            )
            _emit_rebalance_phase_safe(
                decision_id,
                REBALANCE_PHASE_BUY_READY,
                REBALANCE_PHASE_BLOCKED,
                market=self.market.value if hasattr(self.market, "value") else str(self.market),
                account_id=self.account_id,
                session_id=self.session_id,
            )
            return PortfolioRuntimeResult(decision_id, None, None, (), (), "rebalance_blocked")
        account = account or self.broker.account()
        maximum_allocation = self.maximum_allocation
        if self.mode == "live":
            maximum_allocation = min(maximum_allocation, self.live_dollar_cap / max(1.0, account.equity))
        open_order_symbols, open_order_sides = self._effective_open_order_constraints()
        reserved_slots = self._effective_reserved_position_keys(reserved_position_keys)
        risk = self.risk_overlay.apply(
            adjusted_target,
            account,
            market=self.market,
            approved_symbols=self.symbols,
            maximum_allocation=maximum_allocation,
            mode=self.mode,
            reserved_position_keys=reserved_slots,
            open_order_symbols=tuple(sorted(open_order_symbols)),
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
            open_order_symbols=open_order_symbols,
            open_order_sides=open_order_sides,
            decision_id=decision_id,
        )
        buy_orders = tuple(
            PortfolioOrderRequest(**{**asdict(order), "decision_id": decision_id})
            for order in planned
            if order.side == PortfolioOrderSide.BUY
        )
        replan_needs_sell = any(order.side == PortfolioOrderSide.SELL for order in planned)
        if not buy_orders:
            self.repository.update_rebalance_execution(decision_id, REBALANCE_PHASE_COMPLETE)
            _emit_rebalance_phase_safe(
                decision_id,
                REBALANCE_PHASE_BUY_READY,
                REBALANCE_PHASE_COMPLETE,
                market=self.market.value if hasattr(self.market, "value") else str(self.market),
                account_id=self.account_id,
                session_id=self.session_id,
            )
            return PortfolioRuntimeResult(decision_id, risk.target, risk, (), (), "rebalance_complete")
        admission = self._size_buys_to_safe_capacity(buy_orders)
        buy_orders = admission.orders
        account = admission.account
        reserved_slots = self._effective_reserved_position_keys(reserved_position_keys)
        open_order_symbols, _open_order_sides = self._effective_open_order_constraints()
        buy_orders = reserve_opening_order_slots(
            buy_orders,
            account,
            maximum_positions=self.risk_overlay.limits.maximum_positions,
            market_position_limits={Market.CRYPTO: self.risk_overlay.limits.maximum_crypto_positions},
            reserved_position_keys=reserved_slots,
            open_order_symbols=tuple(sorted(open_order_symbols)),
        )
        if not buy_orders:
            log.warning(
                "Deferred BUY phase has no safe capacity; decision=%s submits nothing",
                decision_id,
            )
            if replan_needs_sell and admission.orders:
                # A terminal SELL update did not prove that its position slot
                # disappeared. Keep the deferred phase retryable until a fresh
                # account snapshot permits the replacement opening.
                return PortfolioRuntimeResult(decision_id, risk.target, risk, (), (), "rebalance_waiting_for_slot")
            self.repository.update_rebalance_execution(decision_id, REBALANCE_PHASE_COMPLETE)
            _emit_rebalance_phase_safe(
                decision_id,
                REBALANCE_PHASE_BUY_READY,
                REBALANCE_PHASE_COMPLETE,
                market=self.market.value if hasattr(self.market, "value") else str(self.market),
                account_id=self.account_id,
                session_id=self.session_id,
            )
            return PortfolioRuntimeResult(decision_id, risk.target, risk, (), (), "rebalance_complete")
        self._record_buy_admission_observation(decision_id, admission, buy_orders)
        submitted = self._submit_orders(buy_orders)
        self.repository.update_rebalance_execution(decision_id, REBALANCE_PHASE_BUY_PENDING)
        _emit_rebalance_phase_safe(
            decision_id,
            REBALANCE_PHASE_BUY_READY,
            REBALANCE_PHASE_BUY_PENDING,
            market=self.market.value if hasattr(self.market, "value") else str(self.market),
            account_id=self.account_id,
            session_id=self.session_id,
        )
        return PortfolioRuntimeResult(
            decision_id,
            risk.target,
            risk,
            buy_orders,
            submitted,
            "buy_phase_submitted",
        )

    def _effective_reserved_position_keys(
        self,
        same_cycle_keys: tuple[tuple[Market, str], ...] = (),
    ) -> tuple[tuple[Market, str], ...]:
        """Return same-cycle plus persisted working order position slots.

        A broker snapshot may remain unchanged while a submitted BUY or SELL
        is still working (or its broker outcome is ambiguous). The persisted
        portfolio order ledger is therefore part of account position-cap
        enforcement until every such order reaches a terminal status. A SELL
        remains a fixed slot while it is working: reusing that slot before a
        terminal result and fresh broker snapshot can create a seventh
        position. The planner's symbol-level open-order guard remains in
        place; this separate set reserves the account-level slot even when the
        next decision selects a different symbol.
        """
        slots = {(market, canonical_symbol(symbol, market)) for market, symbol in same_cycle_keys}
        slots.update(
            (order.market, canonical_symbol(order.symbol, order.market))
            for order in self.repository.open_orders(self.account_id)
        )
        return tuple(sorted(slots, key=lambda item: (item[0].value, item[1])))

    def _effective_open_order_symbols(self) -> set[str]:
        """Union broker-visible and account-local nonterminal order symbols.

        The broker open-order endpoint may omit a submission during a crash
        window or transiently lag the local ledger. Local rows are account and
        market scoped through their persisted session lineage, so they suppress
        duplicate planner output without importing manual/external orders.
        """
        broker_symbols, local_sides = self._effective_open_order_constraints()
        return broker_symbols | set(local_sides)

    def _effective_open_order_constraints(self) -> tuple[set[str], dict[str, frozenset[str]]]:
        """Return broker-wide symbols plus side-aware local ledger rows.

        ``all_open_order_symbols`` intentionally remains conservative because
        it has no side information: a broker-visible symbol suppresses both
        directions. Account-local rows carry a trusted side and can therefore
        permit a risk-reducing SELL past a pending BUY while retaining
        duplicate/risk-increase suppression.
        """
        broker_symbols = set(self.broker.all_open_order_symbols())
        local_sides: dict[str, set[str]] = {}
        for order in self.repository.open_orders(self.account_id):
            if order.market != self.market:
                continue
            symbol = canonical_symbol(order.symbol, order.market)
            side = str(getattr(order.side, "value", order.side)).lower()
            local_sides.setdefault(symbol, set()).add(side)
        return broker_symbols, {symbol: frozenset(sides) for symbol, sides in local_sides.items()}

    def _committed_buy_notional(
        self,
        account: AccountSnapshot,
        broker_buys: tuple[Any, ...] | None,
        unknown_broker_symbols: set[str],
    ) -> float | None:
        """Count each open BUY once; None means its risk cannot be priced."""
        marks = {
            (position.market, canonical_symbol(position.symbol, position.market)): position.current_price
            for position in account.positions
            if not position.is_flat
        }
        by_client: dict[str, float] = {}
        local_prices: dict[str, float] = {}
        local_buy_symbols: set[str] = set()
        for order in self.repository.open_orders(self.account_id):
            if str(order.side).lower() != "buy":
                continue
            remaining = order.quantity - order.cumulative_filled_quantity
            if not math.isfinite(remaining) or remaining < -1e-8:
                raise ValueError(f"Invalid durable BUY remainder: {order.client_order_id}")
            if remaining <= 1e-12:
                continue
            symbol = canonical_symbol(order.symbol, order.market)
            price = max(order.reference_price, marks.get((order.market, symbol), 0.0))
            if not math.isfinite(price) or price <= 0:
                return None
            by_client[order.client_order_id] = remaining * price
            local_prices[order.client_order_id] = price
            local_buy_symbols.add(symbol)
        if broker_buys is None:
            # A test/alternate broker without detailed OPEN orders cannot
            # prove that an unmatched working symbol is not a BUY.
            if unknown_broker_symbols - local_buy_symbols:
                return None
        else:
            seen: set[str] = set()
            for order in broker_buys:
                client_id = str(order.client_order_id).strip()
                if not client_id or client_id in seen:
                    raise ValueError("Invalid broker working BUY client ID")
                seen.add(client_id)
                symbol = canonical_symbol(order.symbol, order.market)
                price = max(
                    float(order.reference_price or 0),
                    local_prices.get(client_id, 0.0),
                    marks.get((order.market, symbol), 0.0),
                )
                if not math.isfinite(price):
                    raise ValueError(f"Invalid broker BUY reference price: {client_id}")
                remaining_qty = order.remaining_quantity
                direct_notional = order.remaining_notional
                if remaining_qty is not None and (not math.isfinite(remaining_qty) or remaining_qty < 0):
                    raise ValueError(f"Invalid broker BUY remainder: {client_id}")
                if direct_notional is not None and (not math.isfinite(direct_notional) or direct_notional < 0):
                    raise ValueError(f"Invalid broker BUY notional: {client_id}")
                if remaining_qty is None and direct_notional is None:
                    return None
                if remaining_qty and (not math.isfinite(price) or price <= 0) and direct_notional is None:
                    return None
                amount = max((remaining_qty or 0.0) * price, direct_notional or 0.0)
                by_client[client_id] = max(by_client.get(client_id, 0.0), amount)
        return sum(by_client.values())

    def _size_buys_to_safe_capacity(self, buy_orders: tuple[PortfolioOrderRequest, ...]) -> BuyAdmission:
        """One fresh account-wide gate for immediate and deferred BUYs."""
        getter = getattr(self.broker, "working_buy_orders", None)
        broker_buys = tuple(getter()) if getter is not None else None
        unknown_symbols = set(self.broker.all_open_order_symbols()) if getter is None else set()
        # Fetch positions/cash AFTER OPEN orders. An intervening fill can
        # overcount temporarily, never free headroom twice.
        account = self.broker.account()
        if not math.isfinite(account.equity) or account.equity <= 0 or not math.isfinite(account.cash):
            raise ValueError("Invalid broker account values for BUY admission")
        if any(not math.isfinite(position.market_value) or position.market_value < 0 for position in account.positions):
            raise ValueError("Invalid broker position mark for BUY admission")
        realized_gross = sum(position.market_value for position in account.positions if not position.is_flat)
        committed = self._committed_buy_notional(account, broker_buys, unknown_symbols)
        limits = self.risk_overlay.limits
        gross_headroom = max(0.0, limits.maximum_gross * account.equity - realized_gross - (committed or 0.0))
        cash_headroom = max(0.0, account.cash - limits.minimum_cash * account.equity - (committed or 0.0))
        if committed is None:
            gross_headroom = cash_headroom = 0.0
        capacity = min(gross_headroom, cash_headroom)
        if self.mode == "live":
            managed = sum(
                abs(position.managed_qty) * position.current_price
                for position in account.positions
                if not position.is_flat and position.managed_qty > 1e-12
            )
            capacity = min(capacity, max(0.0, self.live_dollar_cap - managed - (committed or 0.0)))
        marks = {
            (position.market, canonical_symbol(position.symbol, position.market)): position.current_price
            for position in account.positions
            if not position.is_flat
        }
        prices = {
            order.client_order_id: max(order.reference_price, marks.get((order.market, order.symbol), 0.0))
            for order in buy_orders
        }
        if any(not math.isfinite(price) or price <= 0 for price in prices.values()):
            raise ValueError("Invalid BUY reference price")
        notional = sum(order.quantity * prices[order.client_order_id] for order in buy_orders)
        if not math.isfinite(notional) or notional < 0:
            raise ValueError("Invalid BUY batch notional")
        reason: str | None = None
        if committed is None:
            reason = "committed_buy_unpriced"
        elif gross_headroom <= 1e-9:
            reason = "gross_headroom_exhausted"
        elif cash_headroom <= 1e-9:
            reason = "cash_headroom_exhausted"
        elif self.mode == "live" and capacity <= 1e-9:
            reason = "live_dollar_cap_exhausted"
        if notional <= 0 or capacity <= 0:
            sized: tuple[PortfolioOrderRequest, ...] = ()
        else:
            scale = min(1.0, capacity / notional)
            sized = tuple(
                PortfolioOrderRequest(
                    **{
                        **asdict(order),
                        "quantity": floor_increment(
                            order.quantity * scale,
                            self.broker.asset_trading_rules(order.symbol, order.market).quantity_increment,
                        ),
                    }
                )
                for order in buy_orders
            )
            sized = tuple(
                order
                for order in sized
                if order.quantity >= self.broker.asset_trading_rules(order.symbol, order.market).minimum_order_size
                and order.quantity * prices[order.client_order_id]
                >= self.broker.asset_trading_rules(order.symbol, order.market).minimum_order_notional
            )
            if not sized and reason is None:
                reason = "below_broker_minimum_after_sizing"
            elif scale < 1 and reason is None:
                reason = "buy_scaled_to_headroom"
        admission = BuyAdmission(account, sized, realized_gross, committed, gross_headroom, cash_headroom, reason)
        if buy_orders:
            self.last_buy_admission = admission
        if reason is not None and buy_orders:
            log.warning(
                "BUY admission adjusted: reason=%s market=%s gross_headroom=%s cash_headroom=%s committed=%s",
                reason,
                self.market.value,
                gross_headroom,
                cash_headroom,
                committed,
            )
            if not sized:
                _emit_no_trade_safe(
                    market=self.market.value,
                    strategy=self.strategy.name,
                    reason=reason,
                    decision_timestamp=buy_orders[0].decision_timestamp if buy_orders else None,
                    available_capacity={
                        "gross_headroom": gross_headroom,
                        "cash_headroom": cash_headroom,
                        "committed_buy_notional": committed or 0.0,
                    },
                    session_id=self.session_id,
                    account_id=self.account_id,
                )
        return admission

    def current_buy_capacity(self) -> BuyAdmission:
        """Read-only fresh account capacity for operator monitoring."""
        return self._size_buys_to_safe_capacity(())

    def _record_buy_admission_observation(
        self, decision_id: str, admission: BuyAdmission, orders: tuple[PortfolioOrderRequest, ...]
    ) -> None:
        """Best-effort PAPER telemetry for the final direct or deferred BUY gate."""
        if self.mode != "paper" or not orders:
            return
        writer = getattr(self.repository, "record_buy_admission_observation", None)
        if writer is None:
            return
        marks = {
            (position.market, canonical_symbol(position.symbol, position.market)): position.current_price
            for position in admission.account.positions
            if not position.is_flat
        }
        notional = sum(
            order.quantity * max(order.reference_price, marks.get((order.market, order.symbol), 0.0))
            for order in orders
        )
        try:
            writer(
                decision_id,
                tuple(order.client_order_id for order in orders),
                account_equity=admission.account.equity,
                account_cash=admission.account.cash,
                realized_gross=admission.realized_gross,
                committed_buy_notional=admission.committed_buy_notional or 0.0,
                gross_headroom=admission.gross_headroom,
                cash_headroom=admission.cash_headroom,
                admitted_batch_notional=notional,
            )
        except Exception:
            log.warning("PAPER final BUY admission observation failed for %s", decision_id, exc_info=True)
            try:
                from parayabuck.monitoring import alerts

                alerts.emit(
                    "paper_incident", message="Final BUY admission evidence unavailable", decision_id=decision_id
                )
            except Exception:
                pass

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
                if status in {
                    "submitted",
                    "new",
                    "accepted",
                    "pending_new",
                    "pending_cancel",
                    "pending_replace",
                    "accepted_for_bidding",
                    "partial_fill",
                    "filled",
                    "canceled",
                    "rejected",
                    "expired",
                }:
                    continue
                if is_terminal_order_status(status) or status in _AMBIGUOUS_ORDER_STATUSES:
                    continue
            self.repository.record_order(order, "submitting")
            submit_started_at = datetime.now(UTC)
            try:
                self.repository.record_submit_call_started(order.client_order_id, submit_started_at)
            except Exception:
                # Measurement is deliberately non-authoritative: a telemetry
                # write failure must not alter the existing broker-order path.
                log.warning("Could not record order submit start for %s", order.client_order_id, exc_info=True)
            try:
                broker_order_id = self.broker.submit_portfolio_order(order)
            except Exception:
                try:
                    self.repository.record_submit_call_returned(order.client_order_id, datetime.now(UTC))
                except Exception:
                    log.warning("Could not record order submit return for %s", order.client_order_id, exc_info=True)
                self.repository.update_order(order.client_order_id, "reconciliation_required")
                raise
            try:
                self.repository.record_submit_call_returned(order.client_order_id, datetime.now(UTC))
            except Exception:
                log.warning("Could not record order submit return for %s", order.client_order_id, exc_info=True)
            self.repository.update_order(order.client_order_id, "submitted", broker_order_id)
            submitted.append(broker_order_id)
        return tuple(submitted)

    def close(self, status: str = "stopped") -> None:
        self.repository.end_session(self.session_id, status)
