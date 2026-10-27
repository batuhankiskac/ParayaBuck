"""Production portfolio decision runtime shared by paper and live execution."""

from __future__ import annotations

import json
import math
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import asdict, replace
from datetime import UTC, date, datetime, timedelta
from datetime import time as dt_time
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

from parayabuck.config import DrawdownLiquidationConfig
from parayabuck.core.models import AccountSnapshot, Bar, Market
from parayabuck.data.adjustment import signal_session_date
from parayabuck.execution.alpaca_models import DefiniteOrderRejection
from parayabuck.execution.portfolio import (
    DECEMBER_DEFERRAL_KEY,
    AssetTradingRules,
    DecemberDeferralHold,
    DrawdownLiquidationState,
    GainBandHold,
    PortfolioExecutionPolicy,
    PortfolioOrderRequest,
    PortfolioOrderSide,
    PortfolioRiskLimits,
    PortfolioRiskOverlay,
    RiskOverlayDecision,
    TargetToOrderPlanner,
    downsize_gain_fraction_tl,
    floor_increment,
    gain_band_bypass_metadata,
    record_december_deferral_holds,
    record_gain_band_holds,
    reserve_opening_order_slots,
    validate_order_batch,
)
from parayabuck.logger import get_logger
from parayabuck.markets.symbols import canonical_symbol
from parayabuck.markets.timeframes import (
    DEFAULT_SIP_EMBARGO,
    NEW_YORK,
    equity_day_bar_is_closed,
    provider_day_bar_is_final,
)
from parayabuck.portfolio import PRODUCT_STRATEGY_NAMES, rebalance_policy_for
from parayabuck.portfolio.base import (
    PortfolioStrategy,
    require_product_universe,
    require_product_universe_dependency,
)
from parayabuck.portfolio.models import MAX_WEIGHT, TargetPortfolio, TargetWeight
from parayabuck.portfolio.ownership_rebase import BUY
from parayabuck.portfolio.runtime_models import (
    _AMBIGUOUS_ORDER_STATUSES,
    REBALANCE_PHASE_BLOCKED,
    REBALANCE_PHASE_BUY_PENDING,
    REBALANCE_PHASE_BUY_READY,
    REBALANCE_PHASE_COMPLETE,
    REBALANCE_PHASE_SELL_PENDING,
    TERMINAL_ORDER_STATUSES_SQL,
    BuyAdmission,
    OpenPortfolioOrder,
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
from parayabuck.portfolio.signal_cross_check import CrossCheckOutcome
from parayabuck.tax.errors import TaxDataError
from parayabuck.tax.rates import RateBook, load_rate_book
from parayabuck.utils import is_crypto

log = get_logger("parayabuck.portfolio.runtime_service")

# A deferred BUY left at ``reconciliation_required`` normally resolves at the
# next broker reconciliation; one still ambiguous after this long is raised
# to the operator (once per episode, never per bar).
STUCK_DEFERRED_BUY_ALERT_SECONDS = 900.0

# A ``submitting`` row whose broker lookup stays unresolved this long (the
# clock starts at the first unresolved lookup) is raised to the operator and
# moved to ``reconciliation_required``, the ambiguous state a rebalance blocks
# on and reconciliation resolves. Below this it is retried silently.
STUCK_SUBMITTING_LOOKUP_ESCALATION_SECONDS = 1800.0


# Entry-latch reason EngineRuntime sets when the equity signal's
# corporate-action basis is unverified. Any target computed on that basis is
# suspect, so such a decision holds the current position (no order at all).
BASIS_UNVERIFIED_LATCH = "signal_adjustment_unverified"


def _utcnow() -> datetime:
    """Decision wall clock (module-level so tests can pin it deterministically)."""
    return datetime.now(UTC)


def _is_unfinished_signal_bar(market: Market, bar: Bar, now: datetime) -> bool:
    """An equity daily bar whose New York session has not closed at ``now``.

    The provider publishes (and the split-verification merge adopts) a
    session's daily bar while that session is still trading; its close is
    the latest trade, not the session close. The signal path must never
    decide on it. Intraday bars are untouched.
    """
    return market == Market.EQUITY and bar.timeframe == "1d" and not equity_day_bar_is_closed(bar.timestamp, now)


def _is_partial_provider_bar(bar: Bar, now: datetime, embargo: timedelta = DEFAULT_SIP_EMBARGO) -> bool:
    """A PROVIDER equity daily bar that must not enter the signal history.

    Two independent reasons, both about the newest session:

    * the session has not closed yet, so the bar's close is the latest trade
      rather than the session close;
    * the close passed less than the SIP embargo ago. Alpaca's Basic plan
      will not serve SIP history for the newest 15 minutes, so between the
      close and the end of the embargo the provider's bar for that session is
      PARTIAL — a restart inside that window would otherwise decide on a
      close up to 15 minutes stale. ``embargo`` is the configured
      ``signal_cross_check.sip_embargo_minutes``.

    Stream-aggregated bars are exempt from the second reason: they are final
    at the real session close.
    """
    if bar.market != Market.EQUITY or bar.timeframe != "1d":
        return False
    return not provider_day_bar_is_final(bar.timestamp, now, embargo)


def _sip_embargo_of(runtime: object) -> timedelta:
    """The runtime's configured SIP embargo (default for bare test doubles)."""
    embargo: timedelta = getattr(runtime, "sip_embargo", DEFAULT_SIP_EMBARGO)
    return embargo


# Rebalance ``status`` recorded when a settled BLOCKED rebalance is closed
# without submitting its deferred BUYs (phase becomes ``complete``).
REBALANCE_STATUS_CLOSED_UNRESOLVED = "closed_unresolved"

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
    reason: str | None = None,
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
            reason=reason,
        )
    except Exception:
        pass


# market/strategy/reason -> decision day it was last journalled
_no_trade_last: dict[str, str] = {}


# R3 (docs/live_readiness_v1.md): a broker-rejected SELL is retried exactly
# once with this deterministic suffix on its client order id; a retry is
# never retried again.
EXIT_SELL_RETRY_SUFFIX = "-retry1"


def is_exit_sell_retry(client_order_id: str) -> bool:
    return str(client_order_id).endswith(EXIT_SELL_RETRY_SUFFIX)


def exit_sell_retry_client_order_id(client_order_id: str) -> str:
    return f"{client_order_id}{EXIT_SELL_RETRY_SUFFIX}"


# A SELL that ends canceled, expired or done for day leaves its unfilled quantity
# on the bot's own ledger as a residual, sent as ``{leg}-residual{n}``; one SELL
# leg may take at most ``RESIDUAL_SELL_MAX_ROUNDS`` residual rounds. A SELL that
# ended with nothing filled still owes its whole quantity: the exit did not
# happen, so it is re-sent the same way.
RESIDUAL_SELL_SUFFIX = "-residual"
RESIDUAL_SELL_MAX_ROUNDS = 3
RESIDUAL_SELL_STATUSES = frozenset({"canceled", "cancelled", "expired", "done_for_day"})
# A re-planned order whose planned client id is already the id of a terminal row
# of its decision is sent under ``{id}-replan{n}``, at most this many times.
REPLAN_ORDER_SUFFIX = "-replan"
REPLAN_ORDER_MAX_ATTEMPTS = 3


def _sell_leg_root(client_order_id: str) -> str:
    """Planned id of the SELL leg a row belongs to, without its retry or residual suffixes."""
    root = str(client_order_id)
    if root.endswith(EXIT_SELL_RETRY_SUFFIX):
        root = root[: -len(EXIT_SELL_RETRY_SUFFIX)]
    head, separator, index = root.rpartition(RESIDUAL_SELL_SUFFIX)
    if separator and index.isdigit():
        return head
    return root


def _sell_chain_position(client_order_id: str, root: str) -> tuple[int, int]:
    """(residual round, retry flag) of a SELL row within its leg; a later row sorts higher."""
    rest = str(client_order_id)[len(root) :]
    retry = 1 if rest.endswith(EXIT_SELL_RETRY_SUFFIX) else 0
    if retry:
        rest = rest[: -len(EXIT_SELL_RETRY_SUFFIX)]
    if not rest:
        return (0, retry)
    return (int(rest[len(RESIDUAL_SELL_SUFFIX) :]), retry)


def emit_exit_sell_rejected_safe(
    *,
    client_order_id: str,
    symbol: str,
    market: str,
    decision_id: str | None,
    error: str = "",
) -> None:
    """Escalate a rejected SELL that exits/reduces a held position. Never raises."""
    try:
        from parayabuck.monitoring import alerts

        final = is_exit_sell_retry(client_order_id)
        if final:
            message = (
                f"SELL of {symbol} rejected again after its one retry ({client_order_id}); the position is "
                "still held: check the broker and act manually"
            )
        else:
            message = (
                f"SELL of {symbol} rejected by the broker ({client_order_id}); one retry with a new client "
                "order id follows at the next safe opportunity"
            )
        alerts.emit(
            "exit_sell_rejected",
            message=message,
            client_order_id=client_order_id,
            symbol=symbol,
            market=market,
            decision_id=decision_id or "",
            state="retry_rejected" if final else "retry_pending",
            error=error,
        )
    except Exception:
        log.warning("Exit-SELL rejection alert failed for %s", client_order_id, exc_info=True)


if TYPE_CHECKING:
    from parayabuck.execution.alpaca_client import BrokerClock

# Alpaca accepts a market-on-open (OPG) order until 09:28 ET of the session it targets.
OPG_SUBMIT_CUTOFF = dt_time(9, 28)
# Turkish calendar for the TCMB USD/TRY dates used by the shadow gain fraction.
ISTANBUL = ZoneInfo("Europe/Istanbul")
# Every order the runtime builds is a DAY market order (execution/alpaca_orders.py).
RUNTIME_ORDER_TIME_IN_FORCE = "day"


def market_clock_refusal(clock: BrokerClock, *, time_in_force: str) -> tuple[str, str] | None:
    """``(reason code, message)`` when the broker clock forbids submitting an order now, else ``None``.

    A DAY market order is never refused: the strategy decides after the close
    and Alpaca queues a DAY market order sent while the market is closed for
    the next open, which is the intended fill. An OPG order is refused once the
    09:28 ET cutoff of the session it targets has passed; that session is the
    one of ``next_open``, which is also the target while the market is open.
    """
    tif = time_in_force.lower()
    if tif == "opg":
        session = clock.next_open.astimezone(NEW_YORK).date()
        cutoff = datetime.combine(session, OPG_SUBMIT_CUTOFF, tzinfo=NEW_YORK)
        if clock.timestamp >= cutoff:
            return (
                "opg_after_cutoff",
                f"OPG order after the {OPG_SUBMIT_CUTOFF:%H:%M} ET cutoff of the {session.isoformat()} session",
            )
    return None


def _load_tl_rate_book() -> RateBook | None:
    """The default TCMB rate files, or ``None`` (the shadow is then skipped silently)."""
    try:
        return load_rate_book()
    except Exception as exc:
        log.debug("TL gain shadow skipped: USD/TRY rates unavailable (%s)", exc)
        return None


def _emit_order_rejected_safe(
    *,
    client_order_id: str,
    symbol: str,
    market: str,
    decision_id: str,
    reason: str,
    error: str,
    side: str | None = None,
) -> None:
    """Record one broker-rejected order. Never affects the order flow."""
    try:
        from parayabuck.monitoring import alerts, metrics

        metrics.record_event("order_rejected")
        alerts.emit(
            "order_rejected",
            message=f"Portfolio order rejected by the broker: {client_order_id}",
            client_order_id=client_order_id,
            symbol=symbol,
            market=market,
            decision_id=decision_id,
            reason=reason,
            error=error,
            side=side or "",
        )
    except Exception:
        log.warning("Order-rejection alert failed for %s", client_order_id, exc_info=True)
    if str(side or "").lower() == "sell":
        emit_exit_sell_rejected_safe(
            client_order_id=client_order_id,
            symbol=symbol,
            market=market,
            decision_id=decision_id,
            error=error,
        )


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
    Repeat of the same reason for the same market/strategy is suppressed for
    that day only; a new day (or a new reason) re-emits.
    """
    try:
        key = f"{market}:{strategy}:{reason}"
        # The journal id hashes ``occurred_at``, so nothing dedups per day at
        # the sink. Suppression must therefore be day-scoped here: the
        # dashboard banner reads the newest portfolio_no_trade event with no
        # age filter (web/services/dashboard.py), so suppressing forever freezes
        # it on the first day's reason.
        day = (decision_timestamp or datetime.now(UTC)).date().isoformat()
        if _no_trade_last.get(key) == day:
            return
        _no_trade_last[key] = day
        from parayabuck.monitoring.journal import emit_journal
        from parayabuck.monitoring.operational_event import create_event

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
        # Example: "Hisse alınmadı: QLD/QQQ günlük veri zamanları diğer sembollerle eşleşmiyor."
        reason_tr: dict[str, str] = {
            "warming_up": "veriler ısınıyor (yeterli geçmiş yok)",
            "incomplete_strategy_batch": "günlük veri zamanları diğer sembollerle eşleşmiyor",
            "not_scheduled": "rebalance zamanı değil",
            "duplicate_decision": "aynı zaman damgası için zaten karar var",
            "catch_up_superseded": "son seans zaten karar döngüsünde, telafi kararı gerekmiyor",
            "incomplete_managed_batch": "yönetilen pozisyonların verisi eksik",
            "exposure_scaled_to_limit": "sermaye limiti nedeniyle hedef küçültüldü",
            "rebalance_in_progress": "önceki rebalance henüz tamamlanmadı",
            "rebalance_blocked": "satış fazı takılı",
            "account_snapshot_stale": "hesap görüntüsü eski",
            "circuit_breaker": "devre kesici aktif",
        }
        tr = reason_tr.get(reason, reason)
        market_tr = "Hisse" if (market or "").lower() == "equity" else (market or "Piyasa")
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


def _downsize_hold_reason(target: TargetPortfolio, orders: tuple[Any, ...] | list[Any]) -> str | None:
    """Decision-summary reason when every intended order was a held downsize.

    ``GAIN_BAND_HOLD`` (A1u) is preferred when a decision recorded both a band
    hold and a December deferral; the persisted metadata carries both records
    either way, and the daily summary names each of them.
    """
    if orders:
        return None
    metadata = target.metadata or {}
    if metadata.get("gain_band"):
        return "GAIN_BAND_HOLD"
    if metadata.get(DECEMBER_DEFERRAL_KEY):
        return "DECEMBER_DEFERRAL_HOLD"
    return None


def _no_order_reason(
    target: TargetPortfolio,
    orders: tuple[Any, ...] | list[Any],
    *,
    entry_capped: bool,
    account: AccountSnapshot,
    market: Market,
    symbols: Iterable[str],
    rebalance_band: float,
) -> str | None:
    """Decision-summary reason for a decision that planned no order.

    ``None`` means NO_TARGET_CHANGE: the target already matches the holdings.
    The planner drops a sub-band or sub-minimum change silently, so that case
    is named from the target gap against the band (a gap above the band that
    still produced no order is the minimum-notional or lot-size floor).
    """
    if orders:
        return None
    if entry_capped:
        return "ENTRY_LATCH"
    downsize = _downsize_hold_reason(target, orders)
    if downsize is not None:
        return downsize
    if account.equity <= 0:
        return None
    approved = set(symbols)
    held = {
        canonical_symbol(position.symbol, position.market): position.market_value / account.equity
        for position in account.positions
        if position.market == market and not position.is_flat
    }
    targeted = {canonical_symbol(weight.symbol, market): weight.weight for weight in target.weights}
    largest_gap = max(
        (
            abs(targeted.get(symbol, 0.0) - held.get(symbol, 0.0))
            for symbol in set(targeted) | set(held)
            if symbol in approved
        ),
        default=0.0,
    )
    if largest_gap <= 1e-9:
        return None
    return "REBALANCE_BAND" if largest_gap < rebalance_band else "MIN_NOTIONAL"


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
        sip_embargo: timedelta = DEFAULT_SIP_EMBARGO,
        live_buy_gap_margin: float = 0.03,
        drawdown_liquidation: DrawdownLiquidationConfig | None = None,
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
        if not math.isfinite(live_buy_gap_margin) or not 0 <= live_buy_gap_margin < 0.5:
            raise ValueError("live_buy_gap_margin must be in [0, 0.5)")
        # LIVE-only gap-safe BUY cap (docs/live_readiness_v1.md R1).
        self.live_buy_gap_margin = float(live_buy_gap_margin)
        self.universe = universe
        self.universe_version = universe_version
        self.account_id = account_id
        self.allow_static_universe = bool(allow_static_universe)
        # How long after the close a provider daily bar stays partial.
        self.sip_embargo = sip_embargo
        self.policy = rebalance_policy_for(self.strategy)
        self.execution_policy = execution_policy or PortfolioExecutionPolicy()
        self.decision_service = PortfolioDecisionService()
        self.risk_overlay = PortfolioRiskOverlay(risk_limits, drawdown_liquidation=drawdown_liquidation)
        self.planner = TargetToOrderPlanner(self.execution_policy)
        self.history: dict[str, list[Bar]] = {symbol: [] for symbol in self.symbols}
        # Split-basis bridge (equity only): warmup seeds this history on the
        # SPLITS basis; live aggregation appends RAW bars PROVISIONALLY.
        # Before any scheduled decision, EngineRuntime verifies the series
        # against a batched provider splits tail and REPLACES it wholesale
        # when the provider rebased (data/adjustment.py). The runtime keeps
        # no cumulative factor math — the provider is the sole authority.
        self.signal_verified_through_by_symbol: dict[str, datetime] = {}
        # Newest New York session per symbol for which a FINAL provider bar
        # was adopted (partial provider bars are never adopted).
        self.provider_final_session_by_symbol: dict[str, date] = {}
        self.signal_last_verified_at: datetime | None = None
        self.configuration_digest = configuration_digest(
            strategy,
            mode,
            maximum_allocation,
            universe_version,
            self.risk_overlay.limits,
            self.execution_policy,
            drawdown_liquidation=self.risk_overlay.drawdown_liquidation,
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
        # Operator alert keys already raised by this process (edge-triggered).
        self._raised_operator_alerts: set[tuple[str, str]] = set()
        # Whether the current process_closed_bars call persisted its decision.
        self._decision_recorded = False

    def _market_name(self) -> str:
        return self.market.value if hasattr(self.market, "value") else str(self.market)

    def _daily_equity_signal(self) -> bool:
        return self.market == Market.EQUITY and getattr(self.strategy, "timeframe", None) == "1d"

    def _session_key(self, timestamp: datetime) -> object:
        """Identity of the decision period a signal bar belongs to.

        Equity daily bars carry two labels for one session (stream 09:30 ET,
        provider 00:00 ET), so they are matched by New York session date;
        every other series by its exact timestamp.
        """
        return signal_session_date(timestamp) if self._daily_equity_signal() else timestamp

    def _required_symbols(self) -> tuple[str, ...]:
        """History symbols the strategy reads at a decision (all by default).

        A symbol the strategy never reads (the SPY benchmark) must not hold
        a decision back by arriving late or not at all.
        """
        declared = getattr(self.strategy, "decision_symbols", None)
        if declared is None:
            return tuple(self.history)
        wanted = {canonical_symbol(symbol, self.market) for symbol in declared}
        required = tuple(symbol for symbol in self.history if symbol in wanted)
        return required or tuple(self.history)

    def _align_session_tails(self, timestamp: datetime) -> tuple[datetime, tuple[str, ...]]:
        """Give every history tail on ``timestamp``'s session ONE label.

        Returns ``(decision_timestamp, missing_required_symbols)``. The label
        is the newest one present on that session (the stream's 09:30 ET
        label whenever any stream bar exists), so a provider bar fetched for
        a late symbol joins the stream bars of the same session instead of
        forming its own, never-complete batch.
        """
        key = self._session_key(timestamp)
        in_session = {
            symbol: series[-1]
            for symbol, series in self.history.items()
            if series and self._session_key(series[-1].timestamp) == key
        }
        label = max((bar.timestamp for bar in in_session.values()), default=timestamp)
        for symbol, bar in in_session.items():
            if bar.timestamp != label:
                self.history[symbol][-1] = replace(bar, timestamp=label)
        missing = tuple(sorted(symbol for symbol in self._required_symbols() if symbol not in in_session))
        return label, missing

    def _session_decided(self, timestamp: datetime) -> bool:
        """Whether a decision exists for ``timestamp``'s decision period,
        under either equity daily label."""
        if self.repository.has_decision(self.account_id, self.configuration_digest, timestamp, self.market):
            return True
        # A configuration change (new digest) must not re-decide a session an
        # earlier configuration already decided: the newest decision of any
        # digest counts for its session.
        latest = self.repository.latest_decision_target(self.account_id, self.market)
        if latest is not None and self._session_key(latest[0]) == self._session_key(timestamp):
            return True
        if not self._daily_equity_signal():
            return False
        key = self._session_key(timestamp)
        return any(
            self._session_key(decided) == key
            for decided in self.repository.recent_decision_timestamps(
                self.account_id, self.configuration_digest, self.market
            )
        )

    def _managed_buy_dates(self, symbol: str, count: int) -> list[date] | None:
        """Istanbul trade dates of the ``count`` buys behind the open FIFO lots of ``symbol``.

        A sale consumes the oldest lots first, so the open lots are always the
        last ``count`` buys still open, in order: the tail of the buy history
        pairs one-to-one with ``owned_fifo_lots``. ``None`` when the history
        holds fewer buys than lots, so the shadow stays absent.
        """
        buys = [
            occurred_at
            for occurred_at, kind, quantity, price, _fee in self.repository._owned_events(
                self.account_id, symbol, self.market
            )
            if kind == BUY and price is not None and quantity > 0
        ]
        if count <= 0 or len(buys) < count:
            return None
        return [instant.astimezone(ISTANBUL).date() for instant in buys[-count:]]

    def _gain_band_tl_shadow_probe(
        self,
    ) -> Callable[[str, Sequence[Sequence[float]], float, float, datetime], float | None]:
        """Metadata-only TL gain fraction for one downsize the USD band holds.

        Each lot's TL cost is its USD cost times USD/TRY on its buy date; the sale
        is valued at USD/TRY on the decision date. The TCMB files are loaded at
        most once per planning call; when they are missing the probe returns
        ``None`` and the decision metadata simply omits the shadow fields.
        """
        rates: dict[str, RateBook | None] = {}

        def probe(
            symbol: str,
            lots: Sequence[Sequence[float]],
            units: float,
            price: float,
            decision_timestamp: datetime,
        ) -> float | None:
            if "book" not in rates:
                rates["book"] = _load_tl_rate_book()
            book = rates["book"]
            if book is None or not lots:
                return None
            buy_dates = self._managed_buy_dates(symbol, len(lots))
            if buy_dates is None:
                return None
            instant = decision_timestamp if decision_timestamp.tzinfo else decision_timestamp.replace(tzinfo=UTC)
            try:
                decision_rate = book.usd_try_for_trade_date(instant.astimezone(ISTANBUL).date()).rate
                lot_rates = [
                    (float(quantity), float(cost), book.usd_try_for_trade_date(buy_date).rate)
                    for (quantity, cost), buy_date in zip(lots, buy_dates, strict=True)
                ]
            except TaxDataError as exc:
                log.debug("TL gain shadow skipped for %s: %s", symbol, exc)
                return None
            return downsize_gain_fraction_tl(lot_rates, units, price, decision_rate)

        return probe

    def _managed_open_lots(self, symbols: Iterable[str]) -> dict[str, tuple[tuple[float, float], ...]] | None:
        """FIFO managed lots per symbol for the downsize gain band, else ``None``.

        Reconstructed from the bot's own persisted fills of this account: a lot
        is a filled BUY (fill price plus that fill's fees per unit), consumed
        oldest first and rescaling with a booked split rebase, so external or
        manually held units are never part of a lot. ``None`` (the band is
        inactive) skips the queries entirely. Symbols without open lots are
        omitted; the planner then falls back to the constant band for them and
        says so at INFO.
        """
        if self.execution_policy.downsize_gain_band is None:
            return None
        lots: dict[str, tuple[tuple[float, float], ...]] = {}
        for symbol in symbols:
            canonical = canonical_symbol(symbol, self.market)
            if canonical in lots:
                continue
            found = self.repository.owned_fifo_lots(self.account_id, canonical, self.market)
            if found:
                lots[canonical] = found
        return lots

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
        # H2: a symbol this decision deliberately held through the December
        # window has no planned delta; the receipt says why (the telemetry log
        # prints this mapping as planned_delta=).
        raw_deferred = target.metadata.get(DECEMBER_DEFERRAL_KEY)
        deferred: dict[str, object] = {}
        for entry in raw_deferred if isinstance(raw_deferred, (tuple, list)) else ():
            if isinstance(entry, dict) and entry.get("symbol"):
                deferred[str(entry["symbol"])] = entry.get("sessions")
        details = {
            symbol: {
                "target_weight": float(target_weights.get(symbol, 0.0)),
                "current_position": float(current_positions.get(symbol, 0.0)),
                "planned_order_delta": float(planned_delta.get(symbol, 0.0)),
                **({"december_deferral_sessions": deferred[symbol]} if symbol in deferred else {}),
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
            if _is_partial_provider_bar(bar, now, _sip_embargo_of(self)):
                continue
            series = self.history[symbol]
            if not series or series[-1].timestamp < bar.timestamp:
                series.append(bar)
        timestamp_sets = [{bar.timestamp for bar in series} for series in self.history.values() if series]
        if len(timestamp_sets) == len(self.history):
            common_timestamps = set.intersection(*timestamp_sets)
            if common_timestamps and self.previous_timestamp is None:
                # ``previous_timestamp`` is the last DECIDED timestamp, not the
                # last bar ever seen. A warmup retry re-seeds history and can
                # pull in the current session's provider bar (labelled 00:00
                # ET) while the hours:24 cadence buckets by UTC day — advancing
                # into that session would make the real 16:00 stream bar a
                # non-boundary and silently skip the session's decision. Only
                # a runtime that has never decided takes its high-water from
                # the seed.
                self.previous_timestamp = max(common_timestamps)

    def prune_unfinished_signal_sessions(self, now: datetime | None = None) -> int:
        """Drop equity daily bars whose session has not closed from the
        signal history tail. Returns the number of bars dropped.

        Unfinished sessions can only sit at the tail (history is ordered), so
        this is a cheap tail scan. The completed session re-enters later via
        the finalized stream bar or the next provider verification.
        """
        if self.market != Market.EQUITY or getattr(self.strategy, "timeframe", None) != "1d":
            return 0
        now = now or _utcnow()
        dropped = 0
        for symbol, series in self.history.items():
            while series and _is_unfinished_signal_bar(self.market, series[-1], now):
                removed = series.pop()
                dropped += 1
                log.warning(
                    "Ignored unfinished equity daily bar in signal history: symbol=%s session_label=%s close=%s",
                    symbol,
                    removed.timestamp.isoformat(),
                    removed.close,
                )
        return dropped

    def startup_catch_up(
        self,
        *,
        entry_latch: str | None = None,
        account: AccountSnapshot | None = None,
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
        market.

        A prior decision normally supersedes the catch-up. The exception is a
        scheduled boundary with NO decision for it: a restart or outage
        across the session close leaves that session undecided forever
        otherwise, so it is decided here as an ordinary (non-forced) session
        decision — including while a position is held, since a trend exit is
        exactly the decision that must not be skipped.
        """
        self.startup_catch_up_at = datetime.now(UTC)
        # An intra-session restart may have adopted the provider's forming
        # bar for today; the catch-up decides on the last COMPLETED session.
        self.prune_unfinished_signal_sessions()
        series_by_symbol = dict(self.history)
        required = self._required_symbols()
        timestamps = [
            series_by_symbol[symbol][-1].timestamp if series_by_symbol[symbol] else None for symbol in required
        ]
        if any(timestamp is None for timestamp in timestamps) or any(
            len(series_by_symbol[symbol]) < self.strategy.minimum_history_bars for symbol in required
        ):
            self.startup_catch_up_status = "warming_up"
            # Visibility: persistent no-trade event
            warming = tuple(sorted(s for s in required if len(self.history[s]) < self.strategy.minimum_history_bars))
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
                market=self._market_name(),
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
        present = [timestamp for timestamp in timestamps if timestamp is not None]
        if len({self._session_key(timestamp) for timestamp in present}) != 1:
            self.startup_catch_up_status = "incomplete_strategy_batch"
            last_ts = {s: (ser[-1].timestamp.isoformat() if ser else "none") for s, ser in self.history.items()}
            _emit_no_trade_safe(
                market=self._market_name(),
                strategy=getattr(self.strategy, "name", None),
                reason="incomplete_strategy_batch",
                decision_timestamp=self.startup_catch_up_at,
                data_timestamp=max((t for t in timestamps if t is not None), default=None),
                missing_symbols=tuple(
                    sorted(
                        {
                            s
                            for s in required
                            if not self.history[s]
                            or self._session_key(self.history[s][-1].timestamp) != self._session_key(max(present))
                        }
                    )
                ),
                last_timestamps=last_ts,
                session_id=self.session_id,
                account_id=self.account_id,
                entry_latch=entry_latch,
            )
            return PortfolioRuntimeResult(None, None, None, (), (), "incomplete_strategy_batch")
        session = self._session_key(present[0])
        timestamp = max(
            series[-1].timestamp
            for series in series_by_symbol.values()
            if series and self._session_key(series[-1].timestamp) == session
        )
        if self._session_decided(timestamp):
            self.startup_catch_up_status = "duplicate_decision"
            # The decision's rows may still be ``submitting`` from a crash before
            # the broker call; a verdict now stops them counting as open orders.
            self._resolve_stale_submitting_rows()
            _emit_no_trade_safe(
                market=self._market_name(),
                strategy=getattr(self.strategy, "name", None),
                reason="duplicate_decision",
                decision_timestamp=timestamp,
                data_timestamp=timestamp,
                session_id=self.session_id,
                account_id=self.account_id,
            )
            return PortfolioRuntimeResult(None, None, None, (), (), "duplicate_decision")
        prior_decision = self.repository.latest_decision_timestamp(
            self.account_id, self.configuration_digest, self.market
        )
        # A scheduled boundary that has NO persisted decision is a session the
        # process was not alive for (a deploy/restart across the close). It
        # must be decided now — a decision is due whether or not a position is
        # held, so the catch-up entry guard below does not apply to it.
        recovering_session = prior_decision is not None and self.policy.is_period_start(prior_decision, timestamp)
        if prior_decision is not None and not recovering_session:
            # The cadence is already running and the newest completed session
            # is not a new period start: a catch-up would be an out-of-cadence
            # re-entry.
            self.startup_catch_up_status = "catch_up_superseded"
            _emit_no_trade_safe(
                market=self._market_name(),
                strategy=getattr(self.strategy, "name", None),
                reason="catch_up_superseded",
                decision_timestamp=timestamp,
                data_timestamp=timestamp,
                session_id=self.session_id,
                account_id=self.account_id,
            )
            return PortfolioRuntimeResult(None, None, None, (), (), "catch_up_superseded")
        if account is None:
            account = self.broker.account()
        if not recovering_session and any(
            position.market == self.market and not position.is_flat and position.managed_qty > 1e-12
            for position in account.positions
        ):
            # Already invested in this market: never layer a catch-up entry on
            # top of an existing managed position. A RECOVERED session
            # decision is exempt: it is a scheduled decision that can trim or
            # exit the very position it finds.
            self.startup_catch_up_status = "already_invested"
            _emit_no_trade_safe(
                market=self._market_name(),
                strategy=getattr(self.strategy, "name", None),
                reason="already_invested",
                decision_timestamp=timestamp,
                data_timestamp=timestamp,
                session_id=self.session_id,
                account_id=self.account_id,
            )
            return PortfolioRuntimeResult(None, None, None, (), (), "already_invested")
        # A deferred BUY is an entry: a latched market keeps it deferred even
        # though no fresh decision is planned here. An open rebalance decided
        # for an earlier session never submits its BUY side (see process_closed_bars).
        stale_decision = self._stale_rebalance_decision(timestamp)
        advance_result = self.advance_rebalance(
            account=account,
            submit_deferred_buys=entry_latch is None and stale_decision is None,
        )
        if stale_decision is not None:
            self._close_superseded_rebalance(stale_decision)
        if advance_result is not None and advance_result.status == "rebalance_complete":
            # The fill that completed the rebalance postdates the snapshot
            # taken above: plan the catch-up from a fresh broker account.
            account = self.broker.account()
        current_rebalance = self.repository.current_rebalance_execution(self.account_id, self.market)
        superseded_sells_working = self._superseded_sells_working()
        open_rebalance = (
            current_rebalance
            if current_rebalance is not None and current_rebalance.phase != REBALANCE_PHASE_COMPLETE
            else None
        )
        if open_rebalance is not None or superseded_sells_working:
            status = advance_result.status if advance_result is not None else "rebalance_in_progress"
            reason = "rebalance_blocked" if status == "rebalance_blocked" else "rebalance_in_progress"
            self.startup_catch_up_status = status
            _emit_no_trade_safe(
                market=self._market_name(),
                strategy=getattr(self.strategy, "name", None),
                reason=reason,
                decision_timestamp=timestamp,
                data_timestamp=timestamp,
                session_id=self.session_id,
                account_id=self.account_id,
            )
            return PortfolioRuntimeResult(
                open_rebalance.decision_id if open_rebalance is not None else None,
                None,
                None,
                advance_result.orders if advance_result is not None else (),
                advance_result.submitted_order_ids if advance_result is not None else (),
                status,
            )
        bars = [series[-1] for series in series_by_symbol.values() if series]
        result = self.process_closed_bars(
            bars,
            entry_latch=entry_latch,
            # A recovered session is a REAL cadence boundary (the policy check
            # above), so the ordinary target applies — no forced full target.
            force_scheduled=not recovering_session,
            # The PAPER receipt vocabulary only knows these two origins; the
            # recovery is distinguished by startup_catch_up_status instead.
            decision_origin="startup_catch_up",
            account=account,
        )
        self.startup_catch_up_status = f"recovered_session:{result.status}" if recovering_session else result.status
        return result

    def _append_signal_bar(self, bar: Bar) -> None:
        """Append one completed bar to the strategy's signal history.

        Equity bars arrive from the RAW stream and are appended
        PROVISIONALLY: they sit on the current as-traded basis, which equals
        the provider's splits basis except on a split day. Scheduled
        decisions never run before EngineRuntime verified/rebuilt this
        history against a batched provider splits tail (fail closed).

        An equity tail on the SAME New York session (a provider bar labeled
        00:00 ET, adopted while that session was still forming) is REPLACED
        by the completed stream bar labeled 09:30 ET: one session is one
        signal bar, and the decision bar must be the history tail. A tail the
        provider verification already ADOPTED as that session's FINAL bar is
        the splits-basis authority: it keeps its values and only takes the
        stream label, because the RAW stream bar would mix an as-traded close
        into rebased history on a split day.
        """
        symbol = canonical_symbol(bar.symbol, bar.market)
        series = self.history[symbol]
        if series and series[-1].timestamp >= bar.timestamp:
            return
        if (
            self.market == Market.EQUITY
            and bar.timeframe == "1d"
            and series
            and signal_session_date(series[-1].timestamp) == signal_session_date(bar.timestamp)
        ):
            adopted_through = getattr(self, "provider_final_session_by_symbol", {}).get(symbol)
            if adopted_through is not None and signal_session_date(series[-1].timestamp) <= adopted_through:
                series[-1] = replace(series[-1], timestamp=bar.timestamp)
            else:
                series[-1] = bar
            return
        series.append(bar)

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
        a basis is trustworthy. No-op for a non-equity market.
        """
        from parayabuck.data.adjustment import merge_provider_authority

        if self.market != Market.EQUITY:
            return 0
        changed_symbols = 0
        now = _utcnow()
        for symbol, series in self.history.items():
            # Never adopt a provider bar for a session that is still trading or
            # is inside the SIP embargo: its close is partial, not the session
            # close. The stream's own complete bar is kept until the provider
            # republishes the final one.
            provider = [
                bar
                for bar in provider_splits.get(symbol) or ()
                if not _is_partial_provider_bar(bar, now, _sip_embargo_of(self))
            ]
            if not provider:
                continue
            newest_final = max(signal_session_date(bar.timestamp) for bar in provider)
            adopted = self.__dict__.setdefault("provider_final_session_by_symbol", {})
            if adopted.get(symbol) is None or newest_final > adopted[symbol]:
                adopted[symbol] = newest_final
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

    def _drawdown_liquidation_inputs(
        self,
        decision_timestamp: datetime,
    ) -> tuple[DrawdownLiquidationState | None, date | None]:
        """Persisted RED-state and NYSE session for ``risk_overlay.apply``.

        ``(None, None)`` when the brake is off, so the overlay call is exactly
        the pre-brake call and the repository is not read. When enabled, a
        missing row starts from an empty state so the latch can engage.
        """
        policy = self.risk_overlay.drawdown_liquidation
        if policy is None or not policy.enabled:
            return None, None
        stored = self.repository.drawdown_liquidation_state(self.account_id, self.market)
        state = stored if stored is not None else DrawdownLiquidationState()
        return state, signal_session_date(decision_timestamp)

    def _drawdown_liquidation_write(self, risk: RiskOverlayDecision) -> DrawdownLiquidationState | None:
        """RED-state to persist with a decision; ``None`` (no write) when the brake is off."""
        policy = self.risk_overlay.drawdown_liquidation
        if policy is None or not policy.enabled:
            return None
        return risk.drawdown_liquidation_state

    def process_closed_bars(
        self,
        bars: list[Bar],
        *,
        entry_latch: str | None = None,
        force_scheduled: bool = False,
        decision_origin: str = "scheduled",
        account: AccountSnapshot | None = None,
        signal_cross_check: CrossCheckOutcome | None = None,
    ) -> PortfolioRuntimeResult:
        """Decide on a closed batch and plan/submit its orders.

        A failure before the decision is persisted (a broker read raising,
        for instance) restores the cadence high-water, so a retry of the same
        batch decides the session instead of finding it consumed.
        """
        previous_timestamp = self.previous_timestamp
        self._decision_recorded = False
        try:
            return self._process_closed_bars(
                bars,
                entry_latch=entry_latch,
                force_scheduled=force_scheduled,
                decision_origin=decision_origin,
                account=account,
                signal_cross_check=signal_cross_check,
            )
        except BaseException:
            if not self._decision_recorded:
                self.previous_timestamp = previous_timestamp
            raise

    def _process_closed_bars(
        self,
        bars: list[Bar],
        *,
        entry_latch: str | None,
        force_scheduled: bool,
        decision_origin: str,
        account: AccountSnapshot | None,
        signal_cross_check: CrossCheckOutcome | None,
    ) -> PortfolioRuntimeResult:
        """Body of :meth:`process_closed_bars`.

        ``signal_cross_check`` is the runtime's second-source comparison for
        this session (None when not run, e.g. offline or disabled). It is
        recorded on the decision; when it says the sources disagree on the
        trend (``hold``), a scheduled decision keeps the current position:
        the target is the current weights and no order is planned.
        """
        now = _utcnow()
        eligible = [
            bar
            for bar in bars
            if bar.market == self.market
            and bar.timeframe == self.strategy.timeframe
            and canonical_symbol(bar.symbol, bar.market) in self.history
            and not _is_unfinished_signal_bar(self.market, bar, now)
        ]
        # Defense in depth: the decision context reads the whole history, so
        # an unfinished session adopted earlier must not be its tail either.
        self.prune_unfinished_signal_sessions(now)
        if not eligible:
            return PortfolioRuntimeResult(None, None, None, (), (), "no_eligible_bars")
        for bar in sorted(eligible, key=lambda item: (item.timestamp, item.symbol)):
            self._append_signal_bar(bar)
        # One decision per session: every tail on the batch's session shares
        # one label, and only the symbols the strategy reads must be present.
        timestamp, missing_symbols = self._align_session_tails(max(bar.timestamp for bar in eligible))
        required = self._required_symbols()
        # ``boundary`` is the real cadence; ``scheduled`` additionally honors
        # a forced catch-up decision (startup catch-up), which must apply the
        # FULL target like any boundary decision.
        boundary = self.policy.is_period_start(self.previous_timestamp, timestamp)
        scheduled = boundary or force_scheduled
        previous_timestamp = self.previous_timestamp
        warming_symbols = tuple(
            sorted(symbol for symbol in required if len(self.history[symbol]) < self.strategy.minimum_history_bars)
        )
        if warming_symbols:
            last_ts = {s: (ser[-1].timestamp.isoformat() if ser else "none") for s, ser in self.history.items()}
            try:
                nb = self.policy.next_boundary_after(timestamp).isoformat() if timestamp else None
            except Exception:
                nb = None
            _emit_no_trade_safe(
                market=self._market_name(),
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
                market=self._market_name(),
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
            # The cadence high-water stays put: the late symbol (e.g. from the
            # SIP session-close fallback) completes this session later.
            return PortfolioRuntimeResult(None, None, None, (), (), "incomplete_strategy_batch")
        # A complete batch consumes its period whatever happens next.
        self.previous_timestamp = timestamp
        if self._session_decided(timestamp):
            _emit_no_trade_safe(
                market=self._market_name(),
                strategy=getattr(self.strategy, "name", None),
                reason="duplicate_decision",
                decision_timestamp=timestamp,
                data_timestamp=timestamp,
                session_id=self.session_id,
                account_id=self.account_id,
                entry_latch=entry_latch,
            )
            return PortfolioRuntimeResult(None, None, None, (), (), "duplicate_decision")
        # A deferred BUY is an entry, so a latched market keeps it deferred
        # (the latch caps fresh targets below; the persisted BUY side of an
        # earlier decision needs the same gate). An open rebalance decided for
        # an earlier session never submits its BUY side: its target is stale.
        stale_decision = self._stale_rebalance_decision(timestamp)
        advance_result = self.advance_rebalance(
            account=account,
            submit_deferred_buys=entry_latch is None and stale_decision is None,
        )
        if stale_decision is not None:
            self._close_superseded_rebalance(stale_decision)
        # A terminal deferred BUY has just completed the persisted rebalance.
        # End this cycle before planning again: the caller's account snapshot
        # may predate that fill, so a same-cycle decision could replay the BUY.
        # No decision was recorded for this session, so its cadence high-water
        # is restored and the session is planned on a later cycle.
        if advance_result is not None and advance_result.status == "rebalance_complete":
            self.previous_timestamp = previous_timestamp
            return advance_result
        current_rebalance = self.repository.current_rebalance_execution(self.account_id, self.market)
        if (
            current_rebalance is not None
            and current_rebalance.phase == REBALANCE_PHASE_BLOCKED
            and self._close_settled_blocked_rebalance(current_rebalance.decision_id)
        ):
            current_rebalance = self.repository.current_rebalance_execution(self.account_id, self.market)
        if current_rebalance is not None and current_rebalance.phase != REBALANCE_PHASE_COMPLETE:
            # Nothing was planned for this session: keep it due so it is
            # decided once the rebalance settles.
            self.previous_timestamp = previous_timestamp
            if current_rebalance.phase == REBALANCE_PHASE_BLOCKED:
                _emit_decision_summary_safe(
                    decision_id=current_rebalance.decision_id,
                    market=self._market_name(),
                    strategy=getattr(getattr(self, "strategy", None), "name", None),
                    session_id=self.session_id,
                    account_id=self.account_id,
                    universe_version=self.universe_version,
                    planned=(),
                    submitted=(),
                    no_trade_reason="SELL_PHASE_PENDING",
                )
                _emit_no_trade_safe(
                    market=self._market_name(),
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
                market=self._market_name(),
                strategy=getattr(getattr(self, "strategy", None), "name", None),
                session_id=self.session_id,
                account_id=self.account_id,
                universe_version=self.universe_version,
                planned=(),
                submitted=(),
                no_trade_reason="SELL_PHASE_PENDING",
            )
            _emit_no_trade_safe(
                market=self._market_name(),
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
        if self._superseded_sells_working():
            self.previous_timestamp = previous_timestamp
            _emit_no_trade_safe(
                market=self._market_name(),
                strategy=getattr(self.strategy, "name", None),
                reason="rebalance_in_progress",
                decision_timestamp=timestamp,
                data_timestamp=timestamp,
                session_id=self.session_id,
                account_id=self.account_id,
            )
            return PortfolioRuntimeResult(None, None, None, (), (), "rebalance_in_progress")
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
        current_symbols = {
            symbol for symbol, series in self.history.items() if series and series[-1].timestamp == timestamp
        }
        if not managed.issubset(current_symbols):
            missing_managed = managed - current_symbols
            self.previous_timestamp = previous_timestamp
            _emit_no_trade_safe(
                market=self._market_name(),
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
                market=self._market_name(),
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
            required_symbols=required,
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
                market=self._market_name(),
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
        # Whether the entry latch removed a BUY the strategy wanted (no-trade reason).
        entry_capped = False
        if entry_latch is not None:
            # runtime may only reduce risk. Cap every target at the current
            # weight so the planner emits SELL orders only; entries stay
            # blocked while protective/risk-reducing exits keep flowing.
            current_weights = {
                canonical_symbol(position.symbol, position.market): (position.market_value / account.equity)
                for position in account.positions
                if position.market == self.market and not position.is_flat
            }
            entry_capped = any(
                weight.weight > current_weights.get(weight.symbol, 0.0) + 1e-9 for weight in raw_target.weights
            )
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
                metadata=gain_band_bypass_metadata(
                    {**raw_target.metadata, "entry_risk_latch": entry_latch},
                    (
                        weight.symbol
                        for weight in raw_target.weights
                        if min(weight.weight, current_weights.get(weight.symbol, 0.0)) > 0
                    ),
                    self.market,
                ),
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
                metadata=gain_band_bypass_metadata(
                    {**raw_target.metadata, "intermediate_risk_reduction": True},
                    (weight.symbol for weight in reducing_weights),
                    self.market,
                ),
            )
        if force_scheduled or decision_origin != "scheduled":
            # Tag the decision origin on the FINAL target so both the normal
            # forced path and any entry-latch/reduction path carry it into the
            # persisted decision and the returned result. A recovering session
            # (restart across the close) is a real scheduled decision but is
            # not forced, so its startup_catch_up origin is tagged too.
            raw_target = replace(
                raw_target,
                metadata={**raw_target.metadata, "decision_origin": decision_origin},
            )
        signal_hold = signal_cross_check is not None and signal_cross_check.hold and scheduled
        # An unverified basis can make the strategy's SELL as wrong as its
        # BUY (a pre-split close looks like a crash below the SMA), so the
        # latch holds the position rather than only capping entries.
        basis_hold = entry_latch is not None and BASIS_UNVERIFIED_LATCH in entry_latch.split("|")
        hold_reason = "signal_source_mismatch" if signal_hold else (BASIS_UNVERIFIED_LATCH if basis_hold else None)
        if signal_cross_check is not None:
            raw_target = replace(
                raw_target,
                metadata={**raw_target.metadata, "signal_cross_check": signal_cross_check.to_metadata()},
            )
        if hold_reason is not None:
            # The two data sources disagree on the trend, or the signal basis
            # is unverified: keep exactly what is held this session. The
            # strategy's own metadata stays on the decision for the record.
            # The held weights are raw broker marks, so a gap-up fill that left
            # cash negative can push one past 1.0 (and a non-positive equity
            # makes the ratio meaningless). A hold plans NO order either way,
            # so an unrepresentable mark keeps the strategy target instead of
            # raising ValueError and halting the runtime for the day.
            held_weights = (
                {
                    canonical_symbol(position.symbol, position.market): (position.market_value / account.equity)
                    for position in account.positions
                    if position.market == self.market and not position.is_flat
                }
                if account.equity > 0
                else {}
            )
            held_weights = {
                symbol: min(max(weight, 0.0), MAX_WEIGHT)
                for symbol, weight in held_weights.items()
                if symbol in self.symbols
            }
            held_total = sum(held_weights.values())
            if held_total > MAX_WEIGHT:
                # Gross above equity (negative cash): a long-only target cannot
                # represent what is held, so scale it back to a full book.
                held_weights = {symbol: weight / held_total for symbol, weight in held_weights.items()}
                held_total = MAX_WEIGHT
            held_targets = tuple(
                TargetWeight(symbol, weight, "signal_source_hold" if signal_hold else "signal_basis_hold", rank=1)
                for symbol, weight in sorted(held_weights.items())
                if weight > 0
            )
            raw_target = replace(
                raw_target,
                weights=held_targets,
                cash_weight=max(0.0, 1.0 - held_total),
                metadata={
                    **raw_target.metadata,
                    ("signal_source_hold" if signal_hold else "signal_basis_hold"): True,
                    ("signal_source_hold_target" if signal_hold else "signal_basis_hold_target"): (
                        "held" if held_targets else "strategy"
                    ),
                },
            )
        if any(canonical_symbol(weight.symbol, self.market) not in self.symbols for weight in raw_target.weights):
            raise ValueError("Strategy emitted a symbol outside the active universe")
        maximum_allocation = self.maximum_allocation
        if self.mode == "live":
            maximum_allocation = min(maximum_allocation, self.live_dollar_cap / max(1.0, account.equity))
        self._resolve_stale_submitting_rows()
        open_orders = self.repository.open_orders(self.account_id)
        reserved_slots = self._effective_reserved_position_keys(open_orders)
        open_order_symbols, open_order_sides = self._effective_open_order_constraints(open_orders)
        liquidation_state, liquidation_session = self._drawdown_liquidation_inputs(raw_target.decision_timestamp)
        risk = self.risk_overlay.apply(
            raw_target,
            account,
            market=self.market,
            approved_symbols=self.symbols,
            maximum_allocation=maximum_allocation,
            mode=self.mode,
            reserved_position_keys=reserved_slots,
            open_order_symbols=tuple(sorted(open_order_symbols)),
            drawdown_liquidation_state=liquidation_state,
            session_date=liquidation_session,
        )
        # Visibility: if risk overlay scaled to limit and left no managed
        # exposure, emit persistent no-trade.
        if "exposure_scaled_to_limit" in risk.reasons and sum(w.weight for w in risk.target.weights) < 1e-9:
            # Compute capacity for diagnostics
            gross_before = risk.gross_before
            gross_after = risk.gross_after
            _emit_no_trade_safe(
                market=self._market_name(),
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
                market=self._market_name(),
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
        # Trading rules only for what the planner can touch (targeted or held):
        # a benchmark-only symbol must never fail the decision on its rules.
        held_symbols = {
            canonical_symbol(position.symbol, position.market)
            for position in account.positions
            if position.market == self.market and not position.is_flat
        }
        targeted_symbols = {canonical_symbol(weight.symbol, self.market) for weight in risk.target.weights}
        rules = {
            symbol: self.broker.asset_trading_rules(symbol, self.market)
            for symbol in self.symbols
            if symbol in held_symbols or symbol in targeted_symbols
        }
        # The order idempotency key must be scoped to the account-local
        # decision identity (account + configuration digest + timestamp +
        # market), otherwise two configurations sharing a strategy name,
        # version and timestamp collide on the same client_order_id and the
        # second decision's orders are silently dropped by the repository.
        decision_id = _decision_id(self.account_id, self.configuration_digest, timestamp, self.market)
        gain_band_holds: list[GainBandHold] = []
        december_deferral_holds: list[DecemberDeferralHold] = []
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
            client_order_salt=self.repository.client_order_salt(decision_id),
            open_lots=self._managed_open_lots(held_symbols | targeted_symbols),
            gain_band_holds=gain_band_holds,
            december_deferral_holds=december_deferral_holds,
            gain_band_tl_shadow=self._gain_band_tl_shadow_probe(),
        )
        if gain_band_holds:
            risk = replace(risk, target=record_gain_band_holds(risk.target, gain_band_holds))
        if december_deferral_holds:
            risk = replace(risk, target=record_december_deferral_holds(risk.target, december_deferral_holds))
        if not any(order.side == PortfolioOrderSide.SELL for order in planned):
            planned = reserve_opening_order_slots(
                planned,
                account,
                maximum_positions=self.risk_overlay.limits.maximum_positions,
                reserved_position_keys=reserved_slots,
                open_order_symbols=tuple(sorted(open_order_symbols)),
            )
        if hold_reason is not None:
            # A hold never trades, not even a sub-cap trim the risk overlay
            # may have produced from drifted current weights.
            planned = ()
            _emit_no_trade_safe(
                market=self._market_name(),
                strategy=getattr(self.strategy, "name", None),
                reason=hold_reason,
                decision_timestamp=timestamp,
                data_timestamp=timestamp,
                target_weights=tuple(risk.target.weights),
                risk_reasons=risk.reasons,
                session_id=self.session_id,
                account_id=self.account_id,
                entry_latch=entry_latch,
            )
        observed_target = self._with_paper_observability(
            risk.target,
            risk,
            account,
            planned,
            entry_latch=entry_latch or hold_reason,
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
                drawdown_liquidation_state=self._drawdown_liquidation_write(risk),
            )
            self._decision_recorded = True
            # Orders are already persisted as submitting; submit them idempotently.
            submitted = self._submit_orders(orders)
            _emit_decision_summary_safe(
                decision_id=decision_id,
                market=self._market_name(),
                strategy=getattr(getattr(self, "strategy", None), "name", None),
                session_id=self.session_id,
                account_id=self.account_id,
                universe_version=self.universe_version,
                planned=orders,
                submitted=submitted,
                no_trade_reason=(
                    hold_reason.upper()
                    if hold_reason is not None
                    else _no_order_reason(
                        risk.target,
                        orders,
                        entry_capped=entry_capped,
                        account=account,
                        market=self.market,
                        symbols=self.symbols,
                        rebalance_band=self.planner.policy.rebalance_band_pct,
                    )
                ),
            )
            return PortfolioRuntimeResult(
                decision_id,
                risk.target,
                risk,
                orders,
                submitted,
                "signal_source_hold" if signal_hold else ("signal_basis_hold" if basis_hold else "submitted"),
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
                drawdown_liquidation_state=self._drawdown_liquidation_write(risk),
            )
            self._decision_recorded = True
            submitted = self._submit_orders(sells)
            _emit_decision_summary_safe(
                decision_id=decision_id,
                market=self._market_name(),
                strategy=getattr(getattr(self, "strategy", None), "name", None),
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
                market=self._market_name(),
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
            reserved_position_keys=self._effective_reserved_position_keys(open_orders),
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
            drawdown_liquidation_state=self._drawdown_liquidation_write(risk),
        )
        self._decision_recorded = True
        self._record_buy_admission_observation(decision_id, admission, buys)
        submitted = self._submit_orders(buys)
        _emit_decision_summary_safe(
            decision_id=decision_id,
            market=self._market_name(),
            strategy=getattr(getattr(self, "strategy", None), "name", None),
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
        submit_deferred_buys: bool = True,
        submit_gate: Callable[[PortfolioOrderRequest], str | None] | None = None,
    ) -> PortfolioRuntimeResult | None:
        """Advance a persisted two-phase rebalance by one step.

        ``sell_pending``: while any required SELL is still working (or its
        outcome is ambiguous), the dependent BUY orders stay deferred. When
        every required SELL is terminal, a fresh broker account snapshot is
        taken, the remaining BUY side is re-planned against the safe managed
        capacity and those BUY orders are submitted exactly once. A re-plan
        that still needs a SELL submits that SELL first and keeps the BUY side
        deferred until it settles. A rejected/canceled/expired/partially-filled
        SELL therefore yields the
        re-planned (smaller) BUY quantities; an ambiguous SELL outcome blocks
        the rebalance with ``reconciliation_required`` and submits nothing.
        A SELL that ends canceled or expired with unfilled quantity leaves a
        residual, read from the decision's own order rows (never from an
        account snapshot). The residual is submitted under a deterministic
        ``-residual{n}`` id, the phase stays ``sell_pending`` and no BUY is
        sent until it settles; at most ``RESIDUAL_SELL_MAX_ROUNDS`` rounds per
        leg, after which the rebalance closes without its BUYs and the operator
        is alerted.

        ``buy_pending``: once every deferred BUY is terminal the rebalance is
        marked complete.

        Returns the transition result, or ``None`` when the rebalance cannot
        advance yet (or nothing is in flight). This is safe to call from a
        trade-update callback, a bar decision and process startup, and is
        restart-safe because all phase transitions are persisted.
        ``submit_deferred_buys=False`` (the engine is halted) still runs the
        terminal bookkeeping — resolving stuck rows, completing a buy_pending
        phase, promoting a finished sell phase to ``buy_ready`` — but never
        submits the risk-increasing BUY side. The phase stays ``buy_ready``
        and the next un-halted advance submits it exactly once.

        ``submit_gate`` (optional) is called with each order right before its
        broker submit. A non-None reason skips that submit: the order is not
        booked as sent and stays pending for a later advance. ``None`` (the
        default) changes nothing.
        """
        current = self.repository.current_rebalance_execution(self.account_id, self.market)
        if current is None or current.phase == REBALANCE_PHASE_COMPLETE:
            return None
        if current.phase == REBALANCE_PHASE_BLOCKED:
            return PortfolioRuntimeResult(current.decision_id, None, None, (), (), "rebalance_blocked")
        if current.phase == REBALANCE_PHASE_BUY_READY:
            if not submit_deferred_buys:
                self._log_deferred_buy_withheld(current.decision_id)
                return None
            return self._submit_deferred_buys(
                current.decision_id,
                account=account,
                submit_gate=submit_gate,
            )
        if current.phase == REBALANCE_PHASE_BUY_PENDING:
            buys = self.repository.orders_for_decision(current.decision_id, side="buy")
            if buys and any(str(item["status"]) == "submitting" for item in buys):
                resolved = self._resolve_stuck_submission_rows(current.decision_id, buys, "buy")
                if resolved is None:
                    return None
                buys = resolved
            self._alert_stuck_deferred_buys(current, buys)
            if buys and all(is_terminal_order_status(str(item["status"])) for item in buys):
                self.repository.update_rebalance_execution(current.decision_id, REBALANCE_PHASE_COMPLETE)
                _emit_rebalance_phase_safe(
                    current.decision_id,
                    REBALANCE_PHASE_BUY_PENDING,
                    REBALANCE_PHASE_COMPLETE,
                    market=self._market_name(),
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
            if not submit_deferred_buys:
                self._log_deferred_buy_withheld(current.decision_id)
                return None
            return self._submit_deferred_buys(
                current.decision_id,
                account=account,
                submit_gate=submit_gate,
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
        residual = self._residual_sell_result(current.decision_id, tuple(sells), submit_gate=submit_gate)
        if residual is not None:
            return residual
        self.repository.update_rebalance_execution(current.decision_id, REBALANCE_PHASE_BUY_READY)
        if not submit_deferred_buys:
            self._log_deferred_buy_withheld(current.decision_id)
            return None
        return self._submit_deferred_buys(
            current.decision_id,
            account=account,
            submit_gate=submit_gate,
        )

    def _residual_sell_result(
        self,
        decision_id: str,
        sell_rows: tuple[dict[str, object], ...],
        submit_gate: Callable[[PortfolioOrderRequest], str | None] | None = None,
    ) -> PortfolioRuntimeResult | None:
        """Submit the residual SELLs left by partially filled, then canceled or expired, SELLs.

        Read only from this decision's order rows, never from an account
        snapshot: a SELL that filled completely leaves no residual, so a stale
        position cannot trigger a second SELL. The latest row of a leg is the
        one that matters; it leaves a residual when it ended canceled, expired
        or done for day with unfilled quantity, including a leg with nothing
        filled (a canceled DAY SELL at the close, or one that never reached the
        broker, still owes its exit). A leg with a rejected row that has no
        retry yet belongs to ``retry_rejected_exit_sells`` and is skipped.

        Each leg is judged on its own. A leg that has used its rounds, or whose
        residual cannot be sent, is alerted; the other legs' residuals are still
        sent. The rebalance closes without its BUYs only once no residual is
        left to send, so the BUY side never runs while an exit is owed.

        Returns the submit result when residuals were sent (the rebalance stays
        ``sell_pending``, so no BUY is sent), the closing result when nothing
        can be sent and a leg is unsettled, or ``None`` when nothing needs
        selling. Runs even when the engine is halted: a residual SELL only
        reduces exposure.
        """
        legs: dict[str, list[dict[str, object]]] = {}
        for row in sell_rows:
            legs.setdefault(_sell_leg_root(str(row["client_order_id"])), []).append(row)
        candidates: list[tuple[str, dict[str, object], float, int]] = []
        exhausted: list[str] = []
        for root, rows in legs.items():
            ids = {str(row["client_order_id"]) for row in rows}
            if any(
                str(row["status"]) == "rejected" and f"{row['client_order_id']}{EXIT_SELL_RETRY_SUFFIX}" not in ids
                for row in rows
            ):
                continue
            tail = max(rows, key=lambda row: _sell_chain_position(str(row["client_order_id"]), root))
            if str(tail["status"]) not in RESIDUAL_SELL_STATUSES:
                continue
            filled = float(str(tail.get("cumulative_filled_quantity") or 0.0))
            remaining = float(str(tail["quantity"])) - filled
            if remaining <= 1e-12:
                continue
            rounds = max(_sell_chain_position(str(row["client_order_id"]), root)[0] for row in rows)
            if rounds >= RESIDUAL_SELL_MAX_ROUNDS:
                exhausted.append(root)
                continue
            candidates.append((root, tail, remaining, rounds + 1))
        if not candidates and not exhausted:
            return None
        residuals: list[PortfolioOrderRequest] = []
        dropped: list[str] = []
        if candidates:
            decision = self.repository.decision_row(decision_id)
            try:
                target = TargetPortfolio.from_dict(json.loads(str(decision["target_json"])))
            except (KeyError, TypeError, ValueError):
                log.error("Residual SELL skipped: decision %s target unreadable; rebalance blocked", decision_id)
                self.repository.update_rebalance_execution(
                    decision_id, REBALANCE_PHASE_BLOCKED, status="reconciliation_required"
                )
                return PortfolioRuntimeResult(decision_id, None, None, (), (), "rebalance_blocked")
            for root, tail, remaining, round_index in candidates:
                residual = self._residual_sell_request(decision_id, target, tail, root, round_index, remaining)
                if residual is None:
                    dropped.append(root)
                else:
                    residuals.append(residual)
        for root in exhausted:
            self._raise_operator_alert_once(
                (root, "residual_sell_exhausted"),
                message="Residual SELL still unfilled after the round limit; deferred BUYs discarded",
                category="residual_sell_exhausted",
                decision_id=decision_id,
                client_order_id=root,
            )
        for root in dropped:
            self._hold_unsettled_sell_leg(decision_id, root, "residual_sell_dropped")
        if residuals:
            for residual in residuals:
                log.warning(
                    "Residual SELL submitted; BUYs held until it settles: market=%s decision=%s client_order_id=%s qty=%s",
                    self._market_name(),
                    decision_id,
                    residual.client_order_id,
                    residual.quantity,
                )
            submitted = self._submit_orders(tuple(residuals), submit_gate=submit_gate)
            return PortfolioRuntimeResult(
                decision_id, None, None, tuple(residuals), submitted, "residual_sell_submitted"
            )
        if exhausted or dropped:
            return self._close_unsettled_sell_legs(
                decision_id, [*exhausted, *dropped], from_phase=REBALANCE_PHASE_SELL_PENDING
            )
        return None

    def _residual_sell_request(
        self,
        decision_id: str,
        target: TargetPortfolio,
        tail: dict[str, object],
        root: str,
        round_index: int,
        remaining: float,
    ) -> PortfolioOrderRequest | None:
        """The residual SELL for one leg, or ``None`` when it is not tradable.

        The quantity is the unfilled remainder floored to the asset increment.
        A residual that fails ``validate_order_batch`` (below minimum size or
        notional) is not sent: it is dust that cannot be sold, and the leg
        closes as before.
        """
        symbol = canonical_symbol(str(tail["symbol"]), self.market)
        try:
            rules = self.broker.asset_trading_rules(symbol, self.market)
        except ValueError:
            # The broker reports the symbol untradable (or its metadata is unreadable):
            # the residual cannot be sent. The caller holds the leg; nothing halts.
            log.warning(
                "Residual SELL not sent (no trading rules): market=%s symbol=%s",
                self._market_name(),
                symbol,
                exc_info=True,
            )
            return None
        order = PortfolioOrderRequest(
            client_order_id=f"{root}{RESIDUAL_SELL_SUFFIX}{round_index}",
            decision_id=decision_id,
            symbol=symbol,
            market=self.market,
            side=PortfolioOrderSide.SELL,
            quantity=floor_increment(remaining, rules.quantity_increment),
            reference_price=float(str(tail["reference_price"] or 0.0)),
            decision_timestamp=target.decision_timestamp,
            strategy_name=target.strategy_name,
            strategy_version=target.strategy_version,
        )
        problems = validate_order_batch(
            (order,),
            {symbol: rules},
            minimum_order_notional=self.planner.policy.minimum_order_notional,
        )
        if problems:
            log.warning(
                "Residual SELL not sent (not tradable): market=%s client_order_id=%s reason=%s",
                self._market_name(),
                order.client_order_id,
                "; ".join(reason for _, reason in problems),
            )
            return None
        return order

    def _hold_unsettled_sell_leg(self, decision_id: str, leg: str, reason: str) -> None:
        """Alert and journal a SELL leg whose exit cannot be sent; the BUYs stay held."""
        self._raise_operator_alert_once(
            (leg, reason),
            message="A SELL exit cannot be sent; deferred BUYs are held and the rebalance closes once no exit is left",
            category=reason,
            decision_id=decision_id,
            client_order_id=leg,
        )
        _emit_no_trade_safe(
            market=self._market_name(),
            strategy=getattr(self.strategy, "name", None),
            reason=reason,
            session_id=self.session_id,
            account_id=self.account_id,
        )

    def _close_unsettled_sell_legs(
        self,
        decision_id: str,
        legs: Sequence[str],
        *,
        from_phase: str = REBALANCE_PHASE_BUY_READY,
    ) -> PortfolioRuntimeResult:
        """Close the rebalance without its BUYs while a SELL leg still owes its exit.

        The deferred BUY side is discarded, never sent: the exit it depends on
        was not done. The next scheduled decision re-plans from fresh state.
        """
        log.critical(
            "Rebalance closed with an unsettled SELL leg; deferred BUYs discarded: market=%s decision=%s legs=%s",
            self._market_name(),
            decision_id,
            ",".join(legs),
        )
        self.repository.update_rebalance_execution(
            decision_id, REBALANCE_PHASE_COMPLETE, status=REBALANCE_STATUS_CLOSED_UNRESOLVED
        )
        _emit_rebalance_phase_safe(
            decision_id,
            from_phase,
            REBALANCE_PHASE_COMPLETE,
            market=self._market_name(),
            account_id=self.account_id,
            session_id=self.session_id,
            reason=REBALANCE_STATUS_CLOSED_UNRESOLVED,
        )
        return PortfolioRuntimeResult(decision_id, None, None, (), (), "rebalance_complete")

    def _log_deferred_buy_withheld(self, decision_id: str) -> None:
        """Record that a ready BUY side was held back by a halt (F12).

        Submitting here would be the one risk-increasing action taken while
        the runtime is halted — the exact thing the halt exists to prevent.
        The phase is persisted, so the BUY is submitted by the first advance
        after the operator's verified reset.
        """
        log.warning(
            "Deferred BUY not submitted while the runtime is halted: market=%s decision=%s phase=%s",
            self._market_name(),
            decision_id,
            REBALANCE_PHASE_BUY_READY,
        )

    def _stale_rebalance_decision(self, timestamp: datetime) -> str | None:
        """Decision id of the open rebalance when it was decided for an earlier session.

        ``None`` when no rebalance is open or the open one belongs to
        ``timestamp``'s own session.
        """
        current = self.repository.current_rebalance_execution(self.account_id, self.market)
        if current is None or current.phase == REBALANCE_PHASE_COMPLETE:
            return None
        decision = self.repository.decision_row(current.decision_id)
        decided_at = datetime.fromisoformat(str(decision["decision_timestamp"])) if decision is not None else None
        if decided_at is not None and self._session_key(decided_at) == self._session_key(timestamp):
            return None
        return current.decision_id

    def _superseded_sells_working(self) -> bool:
        """Whether a superseded rebalance still has SELLs working at the broker.

        Its BUY side was discarded, but its SELLs keep their own outcomes. A
        new decision waits for them, so no BUY is planned ahead of a pending
        exit (sells before buys).
        """
        current = self.repository.current_rebalance_execution(self.account_id, self.market)
        if (
            current is None
            or current.phase != REBALANCE_PHASE_COMPLETE
            or current.status != REBALANCE_STATUS_CLOSED_UNRESOLVED
        ):
            return False
        # A crash-left ``submitting`` SELL is resolved here, as the planning path does.
        self._resolve_stale_submitting_rows()
        sells = self.repository.orders_for_decision(current.decision_id, side="sell")
        return any(not is_terminal_order_status(str(item["status"])) for item in sells)

    def _close_superseded_rebalance(self, decision_id: str) -> None:
        """Discard the BUY side of a rebalance a newer session has superseded.

        Only a BUY side not yet at the broker is discarded: ``sell_pending``
        (its SELLs stay with their own outcomes) and ``buy_ready`` (withheld).
        Without the close, a later SELL settlement would submit the stale BUYs
        through the advance path. A ``buy_pending`` rebalance already has its
        BUYs at the broker and is left to finish; BLOCKED is handled by
        ``_close_settled_blocked_rebalance``.
        """
        current = self.repository.current_rebalance_execution(self.account_id, self.market)
        if (
            current is None
            or current.decision_id != decision_id
            or current.phase not in (REBALANCE_PHASE_SELL_PENDING, REBALANCE_PHASE_BUY_READY)
        ):
            return
        self.repository.update_rebalance_execution(
            decision_id, REBALANCE_PHASE_COMPLETE, status=REBALANCE_STATUS_CLOSED_UNRESOLVED
        )
        log.warning(
            "Open rebalance superseded by a new session; deferred BUYs discarded: market=%s decision=%s phase=%s",
            self._market_name(),
            decision_id,
            current.phase,
        )
        _emit_rebalance_phase_safe(
            decision_id,
            current.phase,
            REBALANCE_PHASE_COMPLETE,
            market=self._market_name(),
            account_id=self.account_id,
            session_id=self.session_id,
            reason=REBALANCE_STATUS_CLOSED_UNRESOLVED,
        )

    def _close_settled_blocked_rebalance(self, decision_id: str) -> bool:
        """Close a BLOCKED rebalance whose orders have all settled.

        Only the decision path calls this, and the engine runs that path only
        while the market has no entry halt, i.e. after reconciliation verified
        the blocked decision. The stale deferred BUYs are never submitted: the
        rebalance is closed as ``complete``/``closed_unresolved`` and the next
        scheduled decision re-plans from a fresh target. A block whose decision
        cannot be restored is raised to the operator once and closed the same
        way once its orders settle (it can never be resumed). Returns True when
        the rebalance was closed.
        """
        recoverable = self._rebalance_target_recoverable(decision_id)
        if not recoverable:
            self._raise_operator_alert_once(
                (decision_id, "rebalance_unrecoverable"),
                message="Blocked rebalance decision cannot be restored; its deferred BUYs are discarded",
                category="rebalance_unrecoverable",
                decision_id=decision_id,
            )
        orders = self.repository.orders_for_decision(decision_id)
        if not orders or not all(is_terminal_order_status(str(item["status"])) for item in orders):
            return False
        self.repository.update_rebalance_execution(
            decision_id, REBALANCE_PHASE_COMPLETE, status=REBALANCE_STATUS_CLOSED_UNRESOLVED
        )
        log.warning(
            "Blocked rebalance closed after its orders settled; deferred BUYs discarded: market=%s decision=%s",
            self._market_name(),
            decision_id,
        )
        _emit_rebalance_phase_safe(
            decision_id,
            REBALANCE_PHASE_BLOCKED,
            REBALANCE_PHASE_COMPLETE,
            market=self._market_name(),
            account_id=self.account_id,
            session_id=self.session_id,
            reason=REBALANCE_STATUS_CLOSED_UNRESOLVED,
        )
        return True

    def _rebalance_target_recoverable(self, decision_id: str) -> bool:
        """Whether the decision's risk-adjusted target can still be restored."""
        decision = self.repository.decision_row(decision_id)
        if decision is None:
            return False
        try:
            TargetPortfolio.from_dict(json.loads(str(decision["risk_json"]))["target"])
        except (KeyError, TypeError, ValueError):
            return False
        return True

    def _alert_stuck_deferred_buys(self, current: RebalanceExecutionState, buys: tuple[dict[str, object], ...]) -> None:
        """Raise one operator alert per episode of an ambiguous deferred BUY.

        The ``buy_pending`` phase is written when the BUY batch is submitted,
        so its ``updated_at`` bounds how long an ambiguous BUY has waited for
        reconciliation. No phase change and no halt: reconciliation remains
        the path that resolves the order.
        """
        key = (current.decision_id, "stuck_deferred_buy")
        stuck = [str(item["client_order_id"]) for item in buys if str(item["status"]) == "reconciliation_required"]
        if not stuck:
            self._operator_alert_keys().discard(key)
            return
        updated_at = current.updated_at
        if updated_at.tzinfo is None:
            updated_at = updated_at.replace(tzinfo=UTC)
        age = (datetime.now(UTC) - updated_at).total_seconds()
        if age < STUCK_DEFERRED_BUY_ALERT_SECONDS:
            return
        self._raise_operator_alert_once(
            key,
            message="Deferred BUY still awaiting reconciliation",
            category="stuck_deferred_buy",
            decision_id=current.decision_id,
            client_order_ids=",".join(stuck),
            age_seconds=round(age),
        )

    def _alert_completed_with_ambiguous_buys(self, decision_id: str) -> None:
        """Raise one operator alert when a rebalance completes over an ambiguous BUY.

        A BUY whose submit raised stays ``reconciliation_required`` while the
        phase is still ``buy_ready``; the re-plan then suppresses its symbol
        as an open order and may complete the rebalance with that BUY still
        unresolved. No phase change and no halt: the row keeps counting as an
        open order and reconciliation remains the path that resolves it.
        """
        stuck = [
            str(item["client_order_id"])
            for item in self.repository.orders_for_decision(decision_id, side="buy")
            if str(item["status"]) == "reconciliation_required"
        ]
        if not stuck:
            return
        self._raise_operator_alert_once(
            (decision_id, "rebalance_completed_with_ambiguous_buy"),
            message="Rebalance completed while a deferred BUY awaits reconciliation",
            category="rebalance_completed_with_ambiguous_buy",
            decision_id=decision_id,
            client_order_ids=",".join(stuck),
        )

    def _operator_alert_keys(self) -> set[tuple[str, str]]:
        keys = getattr(self, "_raised_operator_alerts", None)
        if keys is None:
            keys = self._raised_operator_alerts = set()
        return keys

    def _raise_operator_alert_once(self, key: tuple[str, str], *, message: str, **fields: object) -> None:
        keys = self._operator_alert_keys()
        if key in keys:
            return
        log.critical("%s: market=%s %s", message, self._market_name(), fields)
        try:
            from parayabuck.monitoring import alerts

            alerts.emit(
                "paper_incident",
                message=message,
                incident_id=f"{key[1]}:{key[0]}",
                market=self._market_name(),
                **fields,
            )
        except Exception:
            # Not recorded as raised: the next occurrence retries the alert.
            log.warning("Operator alert emit failed: key=%s", key, exc_info=True)
            return
        keys.add(key)

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
        changed = self._resolve_submitting_rows(orders)
        if changed is None:
            return None
        if not changed:
            return orders
        return self.repository.orders_for_decision(decision_id, side=side)

    def _resolve_submitting_rows(self, orders: tuple[dict[str, object], ...]) -> bool | None:
        """Resolve each ``submitting`` row against the broker.

        Returns whether any row changed, or ``None`` when the broker lacks the
        lookup. A row whose lookup fails stays ``submitting`` until the lookup
        has stayed unresolved past the escalation threshold.
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
                log.warning(
                    "Submission lookup failed; row stays submitting: client_order_id=%s",
                    client_order_id,
                    exc_info=True,
                )
                outcome = None
            if outcome is None:
                # No verdict yet: retry on a later advance, escalating once the
                # lookup has stayed unresolved past the threshold.
                if self._escalate_unresolved_submission(client_order_id):
                    changed = True
                continue
            self._submission_lookup_failures().pop(client_order_id, None)
            if isinstance(outcome, tuple):
                status, broker_order_id = outcome
            else:
                status, broker_order_id = outcome, str(item.get("broker_order_id") or "")
            self.repository.update_order(client_order_id, status, broker_order_id)
            if status == "canceled":
                # The broker has no such order, so it was never sent and is not re-sent here: the
                # work it belonged to is not retried this session. Raise it rather than drop it silently.
                self._raise_operator_alert_once(
                    (client_order_id, "submission_resolved_canceled"),
                    message="Submitting order resolved as canceled by the broker lookup; it was not re-sent",
                    category="submission_resolved_canceled",
                    client_order_id=client_order_id,
                    side=str(item.get("side") or ""),
                )
            changed = True
        return changed

    def _escalate_unresolved_submission(self, client_order_id: str) -> bool:
        """Escalate a ``submitting`` row whose broker lookup stays unresolved.

        The first unresolved lookup starts the clock. A restart restarts it,
        which only delays escalation. Past ``STUCK_SUBMITTING_LOOKUP_ESCALATION_SECONDS``
        the operator is alerted once and the row moves to ``reconciliation_required``:
        a rebalance then blocks (halting entries pending reconciliation) and
        reconciliation resolves the order. Returns True when the row was escalated.
        """
        now = _utcnow()
        failures = self._submission_lookup_failures()
        age = (now - failures.setdefault(client_order_id, now)).total_seconds()
        if age < STUCK_SUBMITTING_LOOKUP_ESCALATION_SECONDS:
            return False
        failures.pop(client_order_id, None)
        self._raise_operator_alert_once(
            (client_order_id, "stuck_submission_lookup"),
            message="Submitting order unresolved after repeated broker lookup failures; escalated to reconciliation",
            category="stuck_submission_lookup",
            client_order_id=client_order_id,
            age_seconds=round(age),
        )
        self.repository.update_order(client_order_id, "reconciliation_required")
        log.critical("Submitting order escalated to reconciliation_required: client_order_id=%s", client_order_id)
        return True

    def _submission_lookup_failures(self) -> dict[str, datetime]:
        failures: dict[str, datetime] | None = getattr(self, "_submission_lookup_failed_since", None)
        if failures is None:
            failures = self._submission_lookup_failed_since = {}
        return failures

    def _resolve_stale_submitting_rows(self) -> None:
        """Resolve rows of earlier decisions still at ``submitting``.

        A crash during a direct batch leaves its unsent rows at
        ``submitting`` with no rebalance phase to resolve them. Each is
        looked up by its deterministic client order id: a found order takes
        its real status, an unknown one is canceled, and a failed lookup
        leaves the row ``submitting`` so it still counts as open (fail
        closed). Runs before a new decision exists, so its rows are never
        touched.
        """
        rows = self.repository.submitting_orders(self.account_id, self.market)
        if rows:
            self._resolve_submitting_rows(rows)

    def _submit_deferred_buys(
        self,
        decision_id: str,
        *,
        account: AccountSnapshot | None = None,
        submit_gate: Callable[[PortfolioOrderRequest], str | None] | None = None,
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
                market=self._market_name(),
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
                market=self._market_name(),
                account_id=self.account_id,
                session_id=self.session_id,
            )
            return PortfolioRuntimeResult(decision_id, None, None, (), (), "rebalance_blocked")
        # A crash between persisting a BUY row and its broker acknowledgement
        # leaves it at ``submitting`` in this phase. Resolve it against the
        # broker first, as the pending phases do: left as is, the re-plan would
        # suppress its symbol and could complete the rebalance with the row
        # never resolved. An unresolved row waits for a later advance.
        buys = self.repository.orders_for_decision(decision_id, side="buy")
        if any(str(item["status"]) == "submitting" for item in buys):
            resolved = self._resolve_stuck_submission_rows(decision_id, buys, "buy")
            if resolved is None or any(str(item["status"]) == "submitting" for item in resolved):
                return None
            buys = resolved
        # A BUY the broker already accepted (a crash landed between its
        # submit and the phase flip below) is suppressed by the re-plan as an
        # open order. Such a working BUY keeps the rebalance in
        # ``buy_pending`` instead of completing it, so the pending phase and
        # its stuck-BUY alert keep tracking the order to a terminal status.
        # An ambiguous row (its submit raised) keeps the completion path and
        # its dedicated alert (``_alert_completed_with_ambiguous_buys``).
        working_buy = any(
            not is_terminal_order_status(str(item["status"])) and str(item["status"]) not in _AMBIGUOUS_ORDER_STATUSES
            for item in buys
        )
        account = account or self.broker.account()
        maximum_allocation = self.maximum_allocation
        if self.mode == "live":
            maximum_allocation = min(maximum_allocation, self.live_dollar_cap / max(1.0, account.equity))
        open_orders = self.repository.open_orders(self.account_id)
        open_order_symbols, open_order_sides = self._effective_open_order_constraints(open_orders)
        reserved_slots = self._effective_reserved_position_keys(open_orders)
        # Re-plan reads the persisted RED-state but never writes it: the state
        # belongs to the decision that produced it, not to the BUY re-plan.
        liquidation_state, liquidation_session = self._drawdown_liquidation_inputs(adjusted_target.decision_timestamp)
        risk = self.risk_overlay.apply(
            adjusted_target,
            account,
            market=self.market,
            approved_symbols=self.symbols,
            maximum_allocation=maximum_allocation,
            mode=self.mode,
            reserved_position_keys=reserved_slots,
            open_order_symbols=tuple(sorted(open_order_symbols)),
            drawdown_liquidation_state=liquidation_state,
            session_date=liquidation_session,
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
        # Rules only for the symbols this plan can touch (held or targeted). One
        # untradable symbol elsewhere in the universe must not block the BUY phase.
        universe = set(self.symbols)
        held_or_targeted = {
            canonical_symbol(position.symbol, position.market)
            for position in account.positions
            if position.market == self.market and not position.is_flat
        } | {canonical_symbol(weight.symbol, self.market) for weight in risk.target.weights}
        rules = self._trading_rules_for(sorted(held_or_targeted & universe))
        untradable_held = sorted((held_or_targeted & universe) - set(rules))
        if untradable_held:
            # A held symbol that cannot be traded has an exit owed that cannot be sent:
            # the BUY side is held and the rebalance closes, nothing halts.
            for symbol in untradable_held:
                self._hold_unsettled_sell_leg(decision_id, symbol, "held_symbol_untradable")
            return self._close_unsettled_sell_legs(decision_id, untradable_held)
        gain_band_holds: list[GainBandHold] = []
        december_deferral_holds: list[DecemberDeferralHold] = []
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
            client_order_salt=self.repository.client_order_salt(decision_id),
            open_lots=self._managed_open_lots(held_or_targeted),
            gain_band_holds=gain_band_holds,
            december_deferral_holds=december_deferral_holds,
            gain_band_tl_shadow=self._gain_band_tl_shadow_probe(),
        )
        # A re-plan that planned a SELL still owes that exit even when the SELL is
        # left to its retry path (below): the BUYs wait for the slot it holds.
        replan_needs_sell = any(order.side == PortfolioOrderSide.SELL for order in planned)
        planned = self._replan_orders_with_fresh_ids(decision_id, planned)
        if gain_band_holds:
            risk = replace(risk, target=record_gain_band_holds(risk.target, gain_band_holds))
        if december_deferral_holds:
            risk = replace(risk, target=record_december_deferral_holds(risk.target, december_deferral_holds))
        # Sells before buys (C-2): a re-plan that still needs a SELL submits it and
        # keeps every BUY deferred until that SELL settles.
        replan_sells = tuple(order for order in planned if order.side == PortfolioOrderSide.SELL)
        if replan_sells:
            return self._submit_replan_sells(decision_id, risk, replan_sells, rules, submit_gate=submit_gate)
        buy_orders = tuple(
            PortfolioOrderRequest(**{**asdict(order), "decision_id": decision_id})
            for order in planned
            if order.side == PortfolioOrderSide.BUY
        )
        if not buy_orders and working_buy:
            return self._enter_buy_pending(decision_id, risk, (), ())
        if not buy_orders:
            self.repository.update_rebalance_execution(decision_id, REBALANCE_PHASE_COMPLETE)
            self._alert_completed_with_ambiguous_buys(decision_id)
            _emit_rebalance_phase_safe(
                decision_id,
                REBALANCE_PHASE_BUY_READY,
                REBALANCE_PHASE_COMPLETE,
                market=self._market_name(),
                account_id=self.account_id,
                session_id=self.session_id,
            )
            return PortfolioRuntimeResult(decision_id, risk.target, risk, (), (), "rebalance_complete")
        admission = self._size_buys_to_safe_capacity(buy_orders)
        buy_orders = admission.orders
        account = admission.account
        open_orders = self.repository.open_orders(self.account_id)
        reserved_slots = self._effective_reserved_position_keys(open_orders)
        open_order_symbols, _open_order_sides = self._effective_open_order_constraints(open_orders)
        buy_orders = reserve_opening_order_slots(
            buy_orders,
            account,
            maximum_positions=self.risk_overlay.limits.maximum_positions,
            reserved_position_keys=reserved_slots,
            open_order_symbols=tuple(sorted(open_order_symbols)),
        )
        buy_orders = self._validated_batch(buy_orders, rules)
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
            if working_buy:
                return self._enter_buy_pending(decision_id, risk, (), ())
            self.repository.update_rebalance_execution(decision_id, REBALANCE_PHASE_COMPLETE)
            self._alert_completed_with_ambiguous_buys(decision_id)
            _emit_rebalance_phase_safe(
                decision_id,
                REBALANCE_PHASE_BUY_READY,
                REBALANCE_PHASE_COMPLETE,
                market=self._market_name(),
                account_id=self.account_id,
                session_id=self.session_id,
            )
            return PortfolioRuntimeResult(decision_id, risk.target, risk, (), (), "rebalance_complete")
        # The admission evidence references its order rows, which the direct
        # BUY path writes with the decision; the re-planned BUYs exist only
        # now. Each row is written just before its own submit (the
        # observation keeps the whole batch's notional): a failed submit then
        # leaves no ``submitting`` row for a later BUY that never reached the
        # broker, so the next re-plan submits it instead of suppressing it.
        # ``_submit_orders`` records the same row (INSERT OR IGNORE).
        # A BUY side whose decision is no longer the newest one belongs to a
        # session that is over: it is never sent from here. Its rows stay
        # ``buy_ready`` for the session-boundary close to settle.
        latest_decision = self.repository.latest_decision_id(self.account_id, self.market)
        if latest_decision is not None and latest_decision != decision_id:
            log.warning(
                "Deferred BUY not submitted: decision is no longer the latest one: market=%s decision=%s latest=%s",
                self._market_name(),
                decision_id,
                latest_decision,
            )
            return PortfolioRuntimeResult(decision_id, risk.target, risk, (), (), "stale_decision_buy_held")
        submitted: tuple[str, ...] = ()
        for order in buy_orders:
            if submit_gate is not None:
                reason = submit_gate(order)
                if reason is not None:
                    # Held before any row is written: the BUY stays deferred and the next advance re-plans it.
                    log.warning(
                        "Deferred BUY held by the submit gate; left pending: market=%s decision=%s client_order_id=%s reason=%s",
                        self._market_name(),
                        decision_id,
                        order.client_order_id,
                        reason,
                    )
                    return PortfolioRuntimeResult(decision_id, risk.target, risk, (), submitted, "submit_blocked")
            self.repository.record_order(order, "submitting")
            self._record_buy_admission_observation(decision_id, admission, buy_orders, linked=(order,))
            submitted += self._submit_orders((order,), rules)
        return self._enter_buy_pending(decision_id, risk, buy_orders, submitted)

    def _submit_replan_sells(
        self,
        decision_id: str,
        risk: RiskOverlayDecision,
        sells: tuple[PortfolioOrderRequest, ...],
        rules: Mapping[str, AssetTradingRules],
        *,
        submit_gate: Callable[[PortfolioOrderRequest], str | None] | None = None,
    ) -> PortfolioRuntimeResult:
        """Submit the SELL side a re-plan still needs and hold the BUY side.

        Sells before buys: the re-planned SELLs go out first, the phase returns
        to ``sell_pending`` and the BUYs are re-planned once those SELLs settle.
        A SELL that fails validation is not sent: its leg is held (alert and
        journal) and, when no SELL can go, the rebalance closes without its BUYs.
        """
        valid = self._validated_batch(sells, rules)
        sent_ids = {order.client_order_id for order in valid}
        dropped = [order.symbol for order in sells if order.client_order_id not in sent_ids]
        for symbol in dropped:
            self._hold_unsettled_sell_leg(decision_id, symbol, "replan_sell_dropped")
        if not valid:
            return self._close_unsettled_sell_legs(decision_id, dropped)
        submitted = self._submit_orders(valid, rules, submit_gate=submit_gate)
        self.repository.update_rebalance_execution(decision_id, REBALANCE_PHASE_SELL_PENDING)
        _emit_rebalance_phase_safe(
            decision_id,
            REBALANCE_PHASE_BUY_READY,
            REBALANCE_PHASE_SELL_PENDING,
            market=self._market_name(),
            account_id=self.account_id,
            session_id=self.session_id,
        )
        for order in valid:
            log.warning(
                "Re-planned SELL submitted; BUYs held until it settles: market=%s decision=%s client_order_id=%s qty=%s",
                self._market_name(),
                decision_id,
                order.client_order_id,
                order.quantity,
            )
        return PortfolioRuntimeResult(decision_id, risk.target, risk, valid, submitted, "replan_sell_submitted")

    def _replan_orders_with_fresh_ids(
        self,
        decision_id: str,
        orders: Sequence[PortfolioOrderRequest],
    ) -> tuple[PortfolioOrderRequest, ...]:
        """Give a re-planned order a new client id when its planned id is already terminal.

        The planned id carries no quantity, so a re-plan would reuse the id of
        its own canceled or rejected row: the insert is ignored, the order is
        skipped and the rebalance completes with no BUY and no alert. Such an
        order is sent as ``{id}-replan{n}`` (the first free ``n``). Past
        ``REPLAN_ORDER_MAX_ATTEMPTS`` it is not sent and the operator is alerted.

        A SELL whose planned id is a rejected row is left out: a rejected exit
        belongs to ``retry_rejected_exit_sells`` (one retry, then an alert), and
        a second SELL from the re-plan would sell the same shares twice.
        """
        if not orders:
            return ()
        statuses = {
            str(row["client_order_id"]): str(row["status"]) for row in self.repository.orders_for_decision(decision_id)
        }
        fresh: list[PortfolioOrderRequest] = []
        for order in orders:
            planned_id = order.client_order_id
            if planned_id not in statuses or not is_terminal_order_status(statuses[planned_id]):
                fresh.append(order)
                continue
            if statuses[planned_id] == "rejected" and order.side == PortfolioOrderSide.SELL:
                continue
            candidates = [f"{planned_id}{REPLAN_ORDER_SUFFIX}{n}" for n in range(1, REPLAN_ORDER_MAX_ATTEMPTS + 1)]
            free = next((candidate for candidate in candidates if candidate not in statuses), None)
            if free is None:
                self._raise_operator_alert_once(
                    (planned_id, "replan_order_exhausted"),
                    message="Re-planned order exhausted its fresh client ids; it is not sent",
                    category="replan_order_exhausted",
                    decision_id=decision_id,
                    client_order_id=planned_id,
                )
                continue
            fresh.append(replace(order, client_order_id=free))
        return tuple(fresh)

    def _trading_rules_for(self, symbols: Iterable[str]) -> dict[str, AssetTradingRules]:
        """Broker trading rules for the given symbols, leaving out any the broker refuses.

        A symbol with no rules cannot be sized, so the planner skips it. The
        operator is alerted once per symbol and the rest of the plan goes on.
        """
        rules: dict[str, AssetTradingRules] = {}
        for symbol in symbols:
            try:
                rules[symbol] = self.broker.asset_trading_rules(symbol, self.market)
            except ValueError:
                log.warning("No trading rules for %s; left out of this plan", symbol, exc_info=True)
                self._raise_operator_alert_once(
                    (symbol, "asset_untradable"),
                    message="Broker refused trading rules for a symbol; it is left out of the plan",
                    category="asset_untradable",
                    market=self._market_name(),
                    symbol=symbol,
                )
        return rules

    def _validated_batch(
        self,
        orders: tuple[PortfolioOrderRequest, ...],
        rules_by_symbol: Mapping[str, AssetTradingRules] | None = None,
    ) -> tuple[PortfolioOrderRequest, ...]:
        """Drop the orders of a planned batch that ``validate_order_batch`` rejects.

        Only the offending orders are dropped (a conflicting pair is dropped as
        a whole). A dropped row still ``submitting`` is marked ``rejected`` so it
        never counts as working, and the order-rejected alert is raised for it.
        Valid orders are returned unchanged and nothing is sent for the rest.
        """
        if not orders:
            return orders
        rules: dict[str, AssetTradingRules] = dict(rules_by_symbol or {})
        for order in orders:
            if order.symbol not in rules:
                rules[order.symbol] = self.broker.asset_trading_rules(order.symbol, order.market)
        problems = validate_order_batch(
            orders,
            rules,
            minimum_order_notional=self.planner.policy.minimum_order_notional,
        )
        if not problems:
            return orders
        reasons: dict[str, list[str]] = {}
        for client_order_id, reason in problems:
            reasons.setdefault(client_order_id, []).append(reason)
        recorded = {
            str(row["client_order_id"]): str(row["status"])
            for row in self.repository.orders_for_decision(orders[0].decision_id)
        }
        for order in orders:
            if order.client_order_id not in reasons:
                continue
            reason = "; ".join(reasons[order.client_order_id])
            log.error(
                "Order batch validation dropped order; it is not sent: market=%s client_order_id=%s reason=%s",
                self._market_name(),
                order.client_order_id,
                reason,
            )
            if recorded.get(order.client_order_id) == "submitting":
                self.repository.update_order(order.client_order_id, "rejected")
            _emit_order_rejected_safe(
                client_order_id=order.client_order_id,
                symbol=order.symbol,
                market=self._market_name(),
                decision_id=order.decision_id,
                reason="invalid_batch_order",
                error=reason,
                side=order.side.value,
            )
        return tuple(order for order in orders if order.client_order_id not in reasons)

    def _market_clock_gate(self, orders: tuple[PortfolioOrderRequest, ...]) -> tuple[PortfolioOrderRequest, ...]:
        """Refuse, locally and before any broker submit, an order the broker clock forbids now.

        Consulted once per batch and only when the broker exposes ``clock()``. A
        refused order is recorded ``rejected`` (an order already at ``submitting``
        is marked so) and alerted, like a locally invalid order; the rest of the
        batch is untouched. A failed clock read keeps today's behaviour: the batch
        is submitted and a warning is logged, so an unavailable clock never
        blocks an exit.
        """
        if not orders:
            return orders
        clock_reader = getattr(self.broker, "clock", None)
        if not callable(clock_reader):
            return orders
        try:
            refusal = market_clock_refusal(clock_reader(), time_in_force=RUNTIME_ORDER_TIME_IN_FORCE)
        except Exception:
            log.warning("Broker market clock unavailable; submitting without the clock gate", exc_info=True)
            return orders
        if refusal is None:
            return orders
        code, message = refusal
        recorded = {
            str(row["client_order_id"]): str(row["status"])
            for row in self.repository.orders_for_decision(orders[0].decision_id)
        }
        refused: set[str] = set()
        for order in orders:
            status = recorded.get(order.client_order_id)
            if status is not None and status != "submitting":
                # Already handled by the idempotency check of the submit loop.
                continue
            if status is None:
                self.repository.record_order(order, "rejected")
            else:
                self.repository.update_order(order.client_order_id, "rejected")
            refused.add(order.client_order_id)
            log.error(
                "Order refused by the broker clock: market=%s client_order_id=%s reason=%s",
                self._market_name(),
                order.client_order_id,
                message,
            )
            _emit_order_rejected_safe(
                client_order_id=order.client_order_id,
                symbol=order.symbol,
                market=self._market_name(),
                decision_id=order.decision_id,
                reason=code,
                error=message,
                side=order.side.value,
            )
        return tuple(order for order in orders if order.client_order_id not in refused)

    def _enter_buy_pending(
        self,
        decision_id: str,
        risk: RiskOverlayDecision,
        buy_orders: tuple[PortfolioOrderRequest, ...],
        submitted: tuple[str, ...],
    ) -> PortfolioRuntimeResult:
        """Flip a deferred rebalance to ``buy_pending`` once its BUYs are working."""
        self.repository.update_rebalance_execution(decision_id, REBALANCE_PHASE_BUY_PENDING)
        _emit_rebalance_phase_safe(
            decision_id,
            REBALANCE_PHASE_BUY_READY,
            REBALANCE_PHASE_BUY_PENDING,
            market=self._market_name(),
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
        open_orders: tuple[OpenPortfolioOrder, ...],
    ) -> tuple[tuple[Market, str], ...]:
        """Return the persisted working-order position slots.

        A broker snapshot may remain unchanged while a submitted BUY or SELL
        is still working (or its broker outcome is ambiguous). The persisted
        portfolio order ledger is therefore part of account position-cap
        enforcement until every such order reaches a terminal status. A SELL
        remains a fixed slot while it is working: reusing that slot before a
        terminal result and fresh broker snapshot can create a seventh
        position. The planner's symbol-level open-order guard remains in
        place; this separate set reserves the account-level slot even when the
        next decision selects a different symbol.

        ``open_orders`` is the caller's working-order snapshot and is required:
        re-reading it here would let one decision cycle resolve two different
        ledgers.
        """
        slots = {(order.market, canonical_symbol(order.symbol, order.market)) for order in open_orders}
        return tuple(sorted(slots, key=lambda item: (item[0].value, item[1])))

    def _effective_open_order_constraints(
        self,
        open_orders: tuple[OpenPortfolioOrder, ...],
    ) -> tuple[set[str], dict[str, frozenset[str]]]:
        """Return broker-wide symbols plus side-aware local ledger rows.

        ``all_open_order_symbols`` intentionally remains conservative because
        it has no side information: a broker-visible symbol suppresses both
        directions. Account-local rows carry a trusted side and can therefore
        permit a risk-reducing SELL past a pending BUY while retaining
        duplicate/risk-increase suppression.

        ``open_orders`` is the caller's snapshot, for the same single-read
        reason as in ``_effective_reserved_position_keys``.
        """
        broker_symbols = set(self.broker.all_open_order_symbols())
        local_sides: dict[str, set[str]] = {}
        for order in open_orders:
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
        gap_headroom: float | None = None
        if self.mode == "live":
            managed = sum(
                abs(position.managed_qty) * position.current_price
                for position in account.positions
                if not position.is_flat and position.managed_qty > 1e-12
            )
            capacity = min(capacity, max(0.0, self.live_dollar_cap - managed - (committed or 0.0)))
            # R1 gap-safe cap: a DAY market BUY sized at the close fills at
            # the next open. Size it so notional x (1 + margin) still fits in
            # the non-borrowed buying power, min(cash, non-marginable buying
            # power) less committed BUYs: a gap-up open of up to the margin
            # never borrows on a (multiplier >= 1) margin account.
            spendable = account.cash
            non_marginable = getattr(account, "non_marginable_buying_power", None)
            if non_marginable is not None and math.isfinite(non_marginable):
                spendable = min(spendable, non_marginable)
            margin = float(getattr(self, "live_buy_gap_margin", 0.03))
            gap_headroom = max(0.0, (spendable - (committed or 0.0)) / (1.0 + margin))
            if committed is None:
                gap_headroom = 0.0
            capacity = min(capacity, gap_headroom)
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
        elif self.mode == "live" and gap_headroom is not None and gap_headroom <= 1e-9:
            reason = "gap_safe_headroom_exhausted"
        elif self.mode == "live" and capacity <= 1e-9:
            reason = "live_dollar_cap_exhausted"
        if notional <= 0 or capacity <= 0:
            sized: tuple[PortfolioOrderRequest, ...] = ()
        else:
            scale = min(1.0, capacity / notional)
            # One rules lookup per symbol for the whole sizing pass.
            rules = {
                (order.symbol, order.market): self.broker.asset_trading_rules(order.symbol, order.market)
                for order in buy_orders
            }
            sized = tuple(
                PortfolioOrderRequest(
                    **{
                        **asdict(order),
                        "quantity": floor_increment(
                            order.quantity * scale,
                            rules[(order.symbol, order.market)].quantity_increment,
                        ),
                    }
                )
                for order in buy_orders
            )
            # The same notional floor validate_order_batch applies: the stricter of the policy and the symbol's rule.
            sized = tuple(
                order
                for order in sized
                if order.quantity >= rules[(order.symbol, order.market)].minimum_order_size
                and order.quantity * prices[order.client_order_id]
                >= max(
                    self.planner.policy.minimum_order_notional,
                    rules[(order.symbol, order.market)].minimum_order_notional,
                )
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
        self,
        decision_id: str,
        admission: BuyAdmission,
        orders: tuple[PortfolioOrderRequest, ...],
        *,
        linked: tuple[PortfolioOrderRequest, ...] | None = None,
    ) -> None:
        """Best-effort PAPER telemetry for the final direct or deferred BUY gate.

        ``orders`` is the admitted batch (its notional is recorded); ``linked``
        narrows which of its order rows the observation is written for.
        """
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
                tuple(order.client_order_id for order in (orders if linked is None else linked)),
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

    def _submit_orders(
        self,
        orders: tuple[PortfolioOrderRequest, ...],
        rules_by_symbol: Mapping[str, AssetTradingRules] | None = None,
        *,
        submit_gate: Callable[[PortfolioOrderRequest], str | None] | None = None,
    ) -> tuple[str, ...]:
        """Persist and submit one order batch with broker idempotency keys.

        The client order id is deterministic ``parayabuck-{decision_id}-{salt}-{symbol}-{side}``
        (``salt`` persisted once per ledger) so a crash between persistence and broker acknowledgement cannot produce a
        duplicate broker order (Alpaca rejects a reused client id and the
        broker's ambiguity recovery resolves the existing order). Duplicate
        decisions produce the same id and ``record_order`` is ``INSERT OR IGNORE``
        so only one logical order exists; restart never duplicates, a partial
        fill leaves the same key and the re-planned residual is the only new
        work, and a broker timeout marks ``reconciliation_required`` and
        requires ``reconcile()`` before retry.
        """
        # A malformed order never reaches the broker: invalid ones are dropped
        # here, before any of the batch is recorded as ``submitting``.
        orders = self._validated_batch(orders, rules_by_symbol)
        # The broker clock gate runs after local validation, so only sendable orders are checked.
        orders = self._market_clock_gate(orders)
        submitted: list[str] = []
        # Every order in a batch shares one decision id, so the ledger is read
        # once. The map is keyed by client order id, which is stable per
        # decision, so a row recorded in an earlier batch is still found.
        existing_by_client: dict[str, str] = {}
        existing_shape: dict[str, tuple[str, str, float]] = {}
        if orders:
            rows = self.repository.orders_for_decision(orders[0].decision_id)
            existing_by_client = {str(row.get("client_order_id")): str(row.get("status", "")) for row in rows}
            existing_shape = {
                str(row.get("client_order_id")): (
                    str(row.get("symbol", "")),
                    str(row.get("side", "")),
                    float(str(row.get("quantity") or 0.0)),
                )
                for row in rows
            }
        for index, order in enumerate(orders):
            # Idempotency: a logical order that already has a row is never sent
            # again under its id, whatever its status: a working, terminal,
            # ambiguous or stopped row is decided by the broker or by
            # reconciliation, never by a second submit. Only a row still at
            # ``submitting`` is sent again, and only for the same order: the
            # direct path persists its batch at ``submitting`` just before this
            # call, and Alpaca refuses a duplicate client id, so a row that did
            # reach the broker cannot be doubled. A changed order never reuses it.
            already = existing_by_client.get(order.client_order_id)
            if already is not None:
                if already != "submitting":
                    continue
                symbol, side, quantity = existing_shape.get(order.client_order_id, ("", "", float("nan")))
                if symbol != order.symbol or side != order.side.value or abs(quantity - order.quantity) > 1e-9:
                    log.error(
                        "Submitting row does not match the order it would resend; not sent: client_order_id=%s",
                        order.client_order_id,
                    )
                    continue
            if submit_gate is not None:
                reason = submit_gate(order)
                if reason is not None:
                    # Held before the row is written: nothing is booked as sent and the order stays pending.
                    log.warning(
                        "Submit held by the submit gate; order left pending: market=%s client_order_id=%s reason=%s",
                        self._market_name(),
                        order.client_order_id,
                        reason,
                    )
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
            except DefiniteOrderRejection as exc:
                # A broker 4xx the recovery lookup proved never reached the
                # broker: the row is REJECTED (terminal), not ambiguous. The
                # dependent rebalance re-plans against the real state, so the
                # runtime keeps running instead of halting (F15).
                try:
                    self.repository.record_submit_call_returned(order.client_order_id, datetime.now(UTC))
                except Exception:
                    log.warning("Could not record order submit return for %s", order.client_order_id, exc_info=True)
                self.repository.update_order(order.client_order_id, "rejected")
                _emit_order_rejected_safe(
                    client_order_id=order.client_order_id,
                    symbol=order.symbol,
                    market=self._market_name(),
                    decision_id=order.decision_id,
                    reason="broker_rejected",
                    error=str(exc),
                    side=order.side.value,
                )
                continue
            except Exception:
                try:
                    self.repository.record_submit_call_returned(order.client_order_id, datetime.now(UTC))
                except Exception:
                    log.warning("Could not record order submit return for %s", order.client_order_id, exc_info=True)
                self.repository.update_order(order.client_order_id, "reconciliation_required")
                # The batch's later orders were never sent: their rows, written
                # at ``submitting`` with the decision, are canceled now. Left
                # as is they are missing at the broker, so reconciliation
                # reports drift and halts decisions before any resolver runs.
                for untried in orders[index + 1 :]:
                    if existing_by_client.get(untried.client_order_id) == "submitting":
                        self.repository.update_order(untried.client_order_id, "canceled")
                raise
            try:
                self.repository.record_submit_call_returned(order.client_order_id, datetime.now(UTC))
            except Exception:
                log.warning("Could not record order submit return for %s", order.client_order_id, exc_info=True)
            self.repository.update_order(order.client_order_id, "submitted", broker_order_id)
            submitted.append(broker_order_id)
        return tuple(submitted)

    def retry_rejected_exit_sells(
        self, account: AccountSnapshot, *, submit_gate: Callable[[PortfolioOrderRequest], str | None] | None = None
    ) -> tuple[str, ...]:
        """Retry each broker-rejected SELL of the newest decision ONCE (R3).

        A SELL exits or reduces a held position, so a rejection must not
        leave the position held until the next session's decision. The caller
        invokes this at the next safe opportunity (fresh account snapshot,
        no halt, no breaker, not warming up). Each rejected SELL row gets at
        most one retry, with the deterministic client order id
        ``<original>-retry1`` persisted under the same decision; a rejected
        retry is escalated and never retried again, so this cannot loop. The
        retry quantity is re-sized to what the managed position still holds
        (and the broker says is available), floored to the asset increment,
        and skipped while any order in the symbol is open. Returns the
        broker order ids that were submitted.
        """
        decision_id = self.repository.latest_decision_id(self.account_id, self.market)
        if decision_id is None:
            return ()
        rejected = [
            row
            for row in self.repository.orders_for_decision(decision_id, side=PortfolioOrderSide.SELL.value)
            if str(row["status"]) == "rejected"
            and not is_exit_sell_retry(str(row["client_order_id"]))
            and not self.repository.has_order(exit_sell_retry_client_order_id(str(row["client_order_id"])))
        ]
        if not rejected:
            return ()
        decision = self.repository.decision_row(decision_id)
        if decision is None:
            return ()
        try:
            target = TargetPortfolio.from_dict(json.loads(str(decision["target_json"])))
        except (KeyError, TypeError, ValueError):
            log.warning("Rejected SELL retry skipped: decision %s target unreadable", decision_id)
            return ()
        open_symbols = {
            canonical_symbol(order.symbol, order.market) for order in self.repository.open_orders(self.account_id)
        }
        # Broker-wide symbols include foreign crypto orders, which must not be
        # canonicalized as equities (that raises), as in TargetToOrderPlanner.
        open_symbols |= {
            canonical_symbol(str(symbol), self.market)
            for symbol in self.broker.all_open_order_symbols()
            if is_crypto(str(symbol)) == (self.market == Market.CRYPTO)
        }
        positions = {
            canonical_symbol(position.symbol, position.market): position
            for position in account.positions
            if position.market == self.market and not position.is_flat
        }
        retries: list[PortfolioOrderRequest] = []
        for row in rejected:
            symbol = canonical_symbol(str(row["symbol"]), self.market)
            if symbol in open_symbols:
                continue
            position = positions.get(symbol)
            if position is None or position.qty <= 0 or position.managed_qty <= 1e-12:
                # Nothing managed is held any more: the exit already happened.
                continue
            filled = float(str(row.get("cumulative_filled_quantity") or 0.0))
            remaining = max(0.0, float(str(row["quantity"])) - filled)
            sellable = position.managed_qty
            if position.qty_available is not None:
                sellable = min(sellable, max(0.0, position.qty_available))
            try:
                rules = self.broker.asset_trading_rules(symbol, self.market)
            except ValueError:
                # Untradable now: no retry can be sent. The exit is still owed, so say so.
                self._raise_operator_alert_once(
                    (str(row["client_order_id"]), "exit_sell_untradable"),
                    message="Rejected SELL cannot be retried: the broker refuses the symbol's trading rules",
                    category="exit_sell_untradable",
                    decision_id=decision_id,
                    client_order_id=str(row["client_order_id"]),
                    symbol=symbol,
                )
                continue
            quantity = floor_increment(min(remaining, sellable), rules.quantity_increment)
            price = position.current_price if position.current_price > 0 else float(str(row["reference_price"] or 0.0))
            minimum_notional = max(self.planner.policy.minimum_order_notional, rules.minimum_order_notional)
            if price <= 0 or quantity < rules.minimum_order_size or quantity * price < minimum_notional:
                continue
            retries.append(
                PortfolioOrderRequest(
                    client_order_id=exit_sell_retry_client_order_id(str(row["client_order_id"])),
                    decision_id=decision_id,
                    symbol=symbol,
                    market=self.market,
                    side=PortfolioOrderSide.SELL,
                    quantity=quantity,
                    reference_price=price,
                    decision_timestamp=target.decision_timestamp,
                    strategy_name=target.strategy_name,
                    strategy_version=target.strategy_version,
                )
            )
        if not retries:
            return ()
        for retry in retries:
            log.warning(
                "Retrying rejected SELL once: market=%s decision=%s client_order_id=%s qty=%s",
                self._market_name(),
                decision_id,
                retry.client_order_id,
                retry.quantity,
            )
        return self._submit_orders(tuple(retries), submit_gate=submit_gate)

    def close(self, status: str = "stopped") -> None:
        self.repository.end_session(self.session_id, status)
