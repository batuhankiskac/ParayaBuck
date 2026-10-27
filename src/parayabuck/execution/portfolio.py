"""Target-weight planning and account-level portfolio risk enforcement."""

from __future__ import annotations

import math
from bisect import bisect_right
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from decimal import ROUND_DOWN, Decimal, InvalidOperation
from enum import StrEnum
from functools import lru_cache

from parayabuck.config import DownsizeGainBand, DrawdownLiquidationConfig, ExecutionConfig, RiskLimits
from parayabuck.core.models import AccountSnapshot, Market, PositionSnapshot
from parayabuck.data.adjustment import signal_session_date
from parayabuck.logger import get_logger
from parayabuck.markets.nyse_calendar import nyse_sessions
from parayabuck.markets.symbols import canonical_symbol
from parayabuck.portfolio.models import TargetPortfolio, TargetWeight
from parayabuck.utils import is_crypto

log = get_logger("parayabuck.execution.portfolio")


class PortfolioOrderSide(StrEnum):
    BUY = "buy"
    SELL = "sell"


@dataclass(frozen=True)
class AssetTradingRules:
    minimum_order_size: float
    quantity_increment: float
    minimum_order_notional: float

    def __post_init__(self) -> None:
        values = (
            self.minimum_order_size,
            self.quantity_increment,
            self.minimum_order_notional,
        )
        if any(not math.isfinite(value) or value < 0 for value in values):
            raise ValueError("Asset trading rules must be finite and non-negative")
        if self.quantity_increment <= 0:
            raise ValueError("Asset increments must be positive")


@dataclass(frozen=True)
class PortfolioOrderRequest:
    client_order_id: str
    decision_id: str
    symbol: str
    market: Market
    side: PortfolioOrderSide
    quantity: float
    reference_price: float
    decision_timestamp: datetime
    strategy_name: str
    strategy_version: str


@dataclass(frozen=True)
class BrokerWorkingBuyOrder:
    """Remaining broker BUY risk, before deduplication with durable orders."""

    client_order_id: str
    symbol: str
    market: Market
    remaining_quantity: float | None
    reference_price: float | None
    remaining_notional: float | None


@dataclass(frozen=True)
class PortfolioExecutionPolicy:
    """Shared order-existence policy consumed by backtest, paper and live.

    The exact same (target, current) pair must result in the same decision
    about whether an order SHOULD EXIST in BACKTEST, PAPER and LIVE. Fill
    mechanics may differ; order-existence policy must not. ``rebalance_band``
    skips sub-band drift so backtest and production do not diverge on small
    turnover, and ``minimum_order_notional`` is the floor for a produced
    order (production also enforces the broker-authoritative minimum, which
    is at least as large).

    ``downsize_gain_band`` (A1u, ``docs/research/gain_band_v3.md``) widens that
    band for one case only: a downward resize between two positive weights,
    whose band becomes ``B_down(g_frac)`` of the USD FIFO gain fraction of the
    units the sell would consume. Entries, exits to zero and up-resizes keep
    ``rebalance_band_pct``. The code default is inactive; the packaged
    configuration ships the schedule, mirroring the shipped YAML (the same
    split R2's ``early_reentry_weight`` uses). ``december_deferral_sessions``
    (H2, ``docs/research/band_h2_combo_v1.md``) postpones the same case out of
    the year end entirely: a downward resize whose fill session falls in the
    last N NYSE sessions of December waits for the first January decision.
    Zero (the code default) disables it; the packaged configuration ships 10.
    """

    rebalance_band_pct: float = 0.005
    minimum_order_notional: float = 10.0
    downsize_gain_band: DownsizeGainBand | None = None
    december_deferral_sessions: int = 0

    def __post_init__(self) -> None:
        if not 0 <= self.rebalance_band_pct < 1:
            raise ValueError("rebalance_band_pct must be in [0, 1)")
        if not math.isfinite(self.minimum_order_notional) or self.minimum_order_notional < 0:
            raise ValueError("minimum_order_notional must be finite and non-negative")
        if self.downsize_gain_band is not None and not isinstance(self.downsize_gain_band, DownsizeGainBand):
            raise ValueError("downsize_gain_band must be a schedule or None")
        if (
            isinstance(self.december_deferral_sessions, bool)
            or not isinstance(self.december_deferral_sessions, int)
            or self.december_deferral_sessions < 0
        ):
            raise ValueError("december_deferral_sessions must be a non-negative integer")


@dataclass(frozen=True)
class GainBandHold:
    """One downsize the gain-scaled band suppressed, at one decision.

    ``tl_fraction`` and ``tl_verdict`` are a metadata-only shadow: the same
    gain fraction in TL (lots at their buy-date USD/TRY) and the verdict the
    band would have reached on it. They never drive the USD decision and stay
    ``None`` (absent from the metadata) when the TCMB rate files are missing.
    """

    symbol: str
    g_frac: float
    band: float
    gap: float
    tl_fraction: float | None = None
    tl_verdict: str | None = None

    def as_metadata(self) -> dict[str, object]:
        """Round-trippable decision-metadata entry (``TargetPortfolio.metadata``)."""
        entry: dict[str, object] = {
            "symbol": self.symbol,
            "g_frac": float(self.g_frac),
            "band": float(self.band),
            "gap": float(self.gap),
        }
        if self.tl_fraction is not None:
            entry["gain_fraction_tl_shadow"] = float(self.tl_fraction)
        if self.tl_verdict is not None:
            entry["gain_band_tl_shadow_verdict"] = self.tl_verdict
        return entry


@dataclass(frozen=True)
class DecemberDeferralHold:
    """One downsize the December deferral window postponed, at one decision."""

    symbol: str
    target_weight: float
    current_weight: float
    sessions: int

    def as_metadata(self) -> dict[str, object]:
        """Round-trippable decision-metadata entry (``TargetPortfolio.metadata``)."""
        return {
            "symbol": self.symbol,
            "target_weight": float(self.target_weight),
            "current_weight": float(self.current_weight),
            "sessions": int(self.sessions),
        }


# Metadata key naming the symbols whose downsize the December deferral window
# postponed to the first January decision (H2,
# ``docs/research/band_h2_combo_v1.md``). Read by the intent watchdog, which
# must tolerate the deliberately held weight, and by the summaries.
DECEMBER_DEFERRAL_KEY = "december_deferral"


# Metadata key naming the symbols whose current target the RISK layer produced
# rather than the strategy: the portfolio risk overlay lowering a weight below
# the strategy's own, a halt (any reduction while halted), and a
# risk-reduction-only target construction. The gain-scaled band is a tax-timing
# device for the strategy's own drift trims, so those symbols keep the constant
# band and their sell executes (``docs/research/gain_band_integration_v1.md``).
GAIN_BAND_BYPASS_KEY = "gain_band_bypass"


def gain_band_bypass_symbols(target: TargetPortfolio, market: Market) -> frozenset[str]:
    """Canonical symbols of ``target`` the gain-scaled band must not hold."""
    entries = (target.metadata or {}).get(GAIN_BAND_BYPASS_KEY)
    if not isinstance(entries, (tuple, list)):
        return frozenset()
    symbols: set[str] = set()
    for raw in entries:
        if not isinstance(raw, str):
            continue
        try:
            symbols.add(canonical_symbol(raw, market))
        except ValueError:
            symbols.add(raw)
    return frozenset(symbols)


def gain_band_bypass_metadata(
    metadata: Mapping[str, object],
    symbols: Iterable[str],
    market: Market,
) -> dict[str, object]:
    """``metadata`` plus ``symbols`` marked as risk-driven reductions."""
    existing = metadata.get(GAIN_BAND_BYPASS_KEY)
    merged = set(existing) if isinstance(existing, (tuple, list)) else set()
    merged.update(symbols)
    return {**metadata, GAIN_BAND_BYPASS_KEY: tuple(sorted(merged))}


@lru_cache(maxsize=32)
def _cached_december_season_sessions(year: int) -> tuple[date, ...]:
    """Cached calendar read. It raises instead of returning a failure, because
    ``lru_cache`` stores only calls that return."""
    sessions = tuple(day for day, _, _ in nyse_sessions(date(year, 11, 1), date(year, 12, 31)))
    if not sessions:
        raise RuntimeError(f"NYSE calendar returned no November/December sessions for {year}")
    return sessions


def _december_season_sessions(year: int) -> tuple[date, ...]:
    """NYSE November/December session dates of ``year``, ascending.

    November is included because the fill session of the last November session
    is the first December one, and both sides must be classified by the same
    list. An unavailable calendar returns ``()``: the deferral then never
    applies and the ordinary rule executes, exactly like the gain band's
    constant-band fallback (a tax-timing rule must not be able to stop a sell).
    The failure is not cached, so a later call reads the calendar again.
    """
    try:
        return _cached_december_season_sessions(year)
    except Exception:
        log.warning("December deferral window unavailable for %s; the ordinary rule applies", year, exc_info=True)
        return ()


def in_december_deferral_window(decision_timestamp: datetime, sessions: int) -> bool:
    """Whether this decision's FILL session is one of the last ``sessions``
    NYSE sessions of December (H2, ``docs/research/band_h2_combo_v1.md``).

    The research arm tests the FILL session of the resize (``research/
    band_h2_combo_v1/screen.py``: ``december_tail`` at 436-450, the window at
    589, and the arm branch ``if spec["defer"] and i in dec_tail and tgt < w``
    at 663), which is the session after the decision session because the engine
    decides on the completed close and trades at the next open
    (``docs/research/gain_band_integration_v1.md``, timing note). The successor
    is taken from the same ``parayabuck.markets.nyse_calendar`` schedule the
    runtime uses, so the backtest and PAPER/LIVE share one window.
    """
    if sessions <= 0:
        return False
    session = signal_session_date(decision_timestamp)
    if session.month < 11:
        # January to October: the fill session cannot be in December.
        return False
    season = _december_season_sessions(session.year)
    index = bisect_right(season, session)
    if index >= len(season):
        # The fill session is in January: nothing left to defer.
        return False
    fill = season[index]
    if fill.month != 12:
        # A November fill is never part of the December window.
        return False
    # Every December session from the fill session on is exactly the tail,
    # so the fill is inside the window iff at most `sessions` remain.
    return index >= len(season) - sessions


@dataclass(frozen=True)
class DownsizeGainBandVerdict:
    """Outcome of applying the downsize-gain-band schedule to one candidate order."""

    applied: bool
    band: float
    gap: float
    g_frac: float | None = None
    # "" when the schedule decided (or did not apply at all); otherwise why the
    # schedule could not be evaluated and the constant band was used instead.
    fallback: str = ""


def downsize_gain_fraction(
    lots: Sequence[Sequence[float]],
    units: float,
    price: float,
) -> float | None:
    """USD FIFO gain fraction of the ``units`` a sell at ``price`` would consume.

    ``lots`` are the bot's own open lots, oldest first, as
    ``(quantity, fee-inclusive unit cost)``. The per-lot gain is
    ``units_from_lot * (price - unit_cost)``; the total is floored at zero
    (the research arm A1u has no per-lot floor: a loss lot offsets a gain lot,
    and any ``g_frac <= 1 %`` selects the same first tier anyway). Returns
    ``None`` when the lots do not cover the sale, so the caller falls back to
    the constant band.
    """
    if price <= 0 or units <= 0:
        return 0.0
    remaining = units
    gain = 0.0
    for lot_quantity, lot_cost in lots:
        if remaining <= 0:
            break
        take = min(remaining, lot_quantity)
        gain += take * (price - lot_cost)
        remaining -= take
    if remaining > 1e-9:
        return None
    return max(0.0, gain) / (units * price)


def downsize_gain_fraction_tl(
    lots: Sequence[tuple[float, float, float]],
    units: float,
    price: float,
    decision_usd_try: float,
) -> float | None:
    """TL shadow of :func:`downsize_gain_fraction`, for metadata only.

    ``lots`` are ``(quantity, USD unit cost, USD/TRY on the lot's buy date)``,
    oldest first; each lot's TL cost is its USD cost times that buy-date rate,
    and the sale is valued at ``price * decision_usd_try``. Same FIFO walk and
    zero floor as the USD fraction. Returns ``None`` when the lots do not cover
    the sale or a rate is not usable.
    """
    if not (decision_usd_try > 0 and math.isfinite(decision_usd_try)):
        return None
    tl_lots = [(quantity, usd_cost * buy_rate) for quantity, usd_cost, buy_rate in lots]
    return downsize_gain_fraction(tl_lots, units, price * decision_usd_try)


def downsize_gain_band_tl_verdict(schedule: DownsizeGainBand, *, gap: float, tl_fraction: float) -> str:
    """What the band would have decided on the TL fraction: ``"hold"`` or ``"sell"``."""
    return "hold" if gap < schedule.band_for(tl_fraction) else "sell"


def downsize_gain_band_verdict(
    schedule: DownsizeGainBand | None,
    *,
    target_weight: float,
    current_weight: float,
    units: float,
    price: float,
    lots: Sequence[Sequence[float]] | None,
    managed_qty: float,
    base_band: float,
) -> DownsizeGainBandVerdict:
    """Decide which band applies to one planned resize.

    The schedule only ever replaces the band of a downward resize between two
    positive weights (``0 < target_weight < current_weight``). Anything else,
    an unavailable lot book, or a lot book that does not add up to the managed
    quantity keeps ``base_band`` — exactly the constant-band behaviour.
    """
    gap = abs(target_weight - current_weight)
    if schedule is None or not 0.0 < target_weight < current_weight:
        return DownsizeGainBandVerdict(False, base_band, gap)
    if not lots:
        return DownsizeGainBandVerdict(False, base_band, gap, None, "lots_unavailable")
    tolerance = max(1e-6 * abs(managed_qty), 1e-9)
    if abs(sum(lot_quantity for lot_quantity, _ in lots) - managed_qty) > tolerance:
        return DownsizeGainBandVerdict(False, base_band, gap, None, "lot_book_mismatch")
    g_frac = downsize_gain_fraction(lots, units, price)
    if g_frac is None:
        return DownsizeGainBandVerdict(False, base_band, gap, None, "lot_book_too_small")
    return DownsizeGainBandVerdict(True, schedule.band_for(g_frac), gap, g_frac)


def record_gain_band_holds(target: TargetPortfolio, holds: Sequence[GainBandHold]) -> TargetPortfolio:
    """Attach the downsizes the gain band suppressed to the decision metadata.

    The record is persisted with the decision (``portfolio_decisions`` →
    ``target_json``) and read back by the daily summary, so an operator can see
    that the band, not a strategy signal, kept the position. Only non-empty
    hold lists add the key: every decision without a suppressed downsize keeps
    byte-identical metadata.
    """
    if not holds:
        return target
    return replace(
        target,
        metadata={**target.metadata, "gain_band": tuple(hold.as_metadata() for hold in holds)},
    )


def record_december_deferral_holds(target: TargetPortfolio, holds: Sequence[DecemberDeferralHold]) -> TargetPortfolio:
    """Attach the downsizes the December window postponed to the decision metadata.

    Same contract as :func:`record_gain_band_holds`, on the deferral's own key:
    the record is persisted with the decision, the intent watchdog reads it so
    the deliberately held weight is not a failed order, and the summaries show
    it. Only non-empty hold lists add the key.
    """
    if not holds:
        return target
    return replace(
        target,
        metadata={**target.metadata, DECEMBER_DEFERRAL_KEY: tuple(hold.as_metadata() for hold in holds)},
    )


def portfolio_execution_policy_from_config(execution: ExecutionConfig) -> PortfolioExecutionPolicy:
    """Single semantic mapping shared by backtest, paper and live."""
    return PortfolioExecutionPolicy(
        rebalance_band_pct=execution.rebalance_band_pct,
        minimum_order_notional=execution.minimum_order_notional,
        downsize_gain_band=execution.downsize_gain_band,
        december_deferral_sessions=execution.december_downsize_deferral_sessions,
    )


# The shipped account limits. A caller that omits ``PortfolioRiskLimits`` must
# get the limits the bot actually runs with, not an older set of numbers.
_SHIPPED_RISK = RiskLimits()


@dataclass(frozen=True)
class PortfolioRiskLimits:
    daily_loss_limit: float = _SHIPPED_RISK.daily_loss_limit_pct
    drawdown_halt: float = _SHIPPED_RISK.max_drawdown_pct
    maximum_gross: float = _SHIPPED_RISK.max_gross_exposure_pct
    maximum_equity: float = _SHIPPED_RISK.max_gross_exposure_pct
    maximum_equity_asset: float = _SHIPPED_RISK.max_position_pct
    minimum_cash: float = _SHIPPED_RISK.cash_buffer_pct
    maximum_positions: int = _SHIPPED_RISK.max_positions


def _validate_unique_account_positions(account: AccountSnapshot) -> None:
    """Reject duplicate broker rows before dict-based risk/planning views."""
    seen: set[tuple[Market, str]] = set()
    for position in account.positions:
        key = (position.market, canonical_symbol(position.symbol, position.market))
        if key in seen:
            raise ValueError(
                f"Duplicate account position for {key[1]} ({key[0].value}); "
                "reconciliation is required before trading resumes"
            )
        seen.add(key)


def portfolio_risk_limits_from_config(risk: RiskLimits) -> PortfolioRiskLimits:
    """Single semantic mapping shared by backtest, paper, and live."""
    return PortfolioRiskLimits(
        daily_loss_limit=risk.daily_loss_limit_pct,
        drawdown_halt=risk.max_drawdown_pct,
        maximum_gross=risk.max_gross_exposure_pct,
        maximum_equity=risk.max_gross_exposure_pct,
        maximum_equity_asset=risk.max_position_pct,
        minimum_cash=risk.cash_buffer_pct,
        maximum_positions=risk.max_positions,
    )


DRAWDOWN_LIQUIDATION_REASON = "drawdown_liquidation"


@dataclass(frozen=True)
class DrawdownLiquidationState:
    """RED-state of the drawdown liquidation brake, persisted by the caller.

    ``liquidated_at`` is the session the current liquidation episode began
    (``None`` when not latched); ``rearmed`` records that the drawdown has since
    recovered below ``rearm_recovery_pct``. The overlay is otherwise stateless:
    with no state object it holds RED only while drawdown >= threshold, so a
    recovered account is re-admitted at once. Passing the object back each
    decision adds the cooldown and re-arm rules.
    """

    liquidated_at: date | None = None
    rearmed: bool = False


def _sessions_in_cash(liquidated_at: date, session: date) -> int:
    """NYSE sessions after ``liquidated_at`` up to and including ``session``."""
    if session <= liquidated_at:
        return 0
    try:
        return sum(1 for day, _, _ in nyse_sessions(liquidated_at, session) if liquidated_at < day <= session)
    except Exception:
        log.warning("NYSE calendar unavailable; counting weekdays for the liquidation cooldown", exc_info=True)
        days = (liquidated_at + timedelta(days=offset) for offset in range(1, (session - liquidated_at).days + 1))
        return sum(1 for day in days if day.weekday() < 5)


def _drawdown_liquidation_step(
    policy: DrawdownLiquidationConfig,
    drawdown: float,
    session: date,
    state: DrawdownLiquidationState | None,
) -> tuple[bool, DrawdownLiquidationState | None]:
    """``(RED, next state)`` of the brake for one decision. Caller ensures enabled."""
    if state is None:
        return drawdown >= policy.threshold_pct, None
    if drawdown >= policy.threshold_pct:
        # A breach while latched and not yet re-armed continues the episode;
        # otherwise (fresh, or re-armed and breached again) it starts now.
        started = state.liquidated_at if state.liquidated_at is not None and not state.rearmed else session
        return True, DrawdownLiquidationState(liquidated_at=started, rearmed=False)
    if state.liquidated_at is None:
        return False, state
    rearmed = state.rearmed or drawdown < policy.rearm_recovery_pct
    if rearmed and _sessions_in_cash(state.liquidated_at, session) >= policy.cooldown_sessions:
        return False, DrawdownLiquidationState()
    return True, DrawdownLiquidationState(liquidated_at=state.liquidated_at, rearmed=rearmed)


@dataclass(frozen=True)
class RiskOverlayDecision:
    target: TargetPortfolio
    halted: bool
    reasons: tuple[str, ...]
    gross_before: float
    gross_after: float
    # Next RED-state when the caller passed one in; ``None`` when stateless.
    drawdown_liquidation_state: DrawdownLiquidationState | None = None


class PortfolioRiskOverlay:
    def __init__(
        self,
        limits: PortfolioRiskLimits | None = None,
        *,
        drawdown_liquidation: DrawdownLiquidationConfig | None = None,
    ) -> None:
        self.limits = limits or PortfolioRiskLimits()
        # Kept outside PortfolioRiskLimits: that dataclass is hashed into the
        # LIVE configuration digest, and the default-off brake must not move it.
        self.drawdown_liquidation = drawdown_liquidation

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
        reserved_position_keys: tuple[tuple[Market, str], ...] = (),
        open_order_symbols: tuple[str, ...] = (),
        drawdown_liquidation_state: DrawdownLiquidationState | None = None,
        session_date: date | None = None,
    ) -> RiskOverlayDecision:
        """Apply the portfolio risk overlay to a strategy target.

        ``enforce_drawdown_halt``: when False (offline backtests), a
        drawdown breach is a SHADOW event — the ``portfolio_drawdown_halt``
        reason is still recorded on the decision (where production would have
        halted) but the target is not capped, so the alpha curve keeps
        trading. Production (paper/live) always enforces the halt until an
        operator starts a new risk epoch.

        Drawdown liquidation (``drawdown_liquidation`` on the overlay, default
        off) follows the same shadow rule: its reason is recorded, and managed
        targets are zeroed only when ``enforce_drawdown_halt`` is True.
        ``drawdown_liquidation_state`` is the caller's persisted RED-state
        (``None`` = stateless); ``session_date`` defaults to the decision's
        signal session. The next state is returned on the decision.
        """
        _validate_unique_account_positions(account)
        equity = account.equity
        if equity <= 0:
            raise ValueError("Account equity must be positive")
        approved = {canonical_symbol(symbol, market) for symbol in approved_symbols}
        current_weights = {
            canonical_symbol(position.symbol, position.market): position.market_value / equity
            for position in account.positions
            if not position.is_flat
        }
        gross_before = sum(current_weights.values())
        day_start_equity = max(1e-9, equity - account.daily_pnl)
        daily_loss = max(0.0, -account.daily_pnl / day_start_equity)
        halted_reasons: list[str] = []
        if daily_loss >= self.limits.daily_loss_limit:
            halted_reasons.append("daily_loss_limit")
        if account.drawdown_pct >= self.limits.drawdown_halt:
            halted_reasons.append("portfolio_drawdown_halt")
        halted = bool(halted_reasons)
        if (
            not enforce_drawdown_halt
            and "portfolio_drawdown_halt" in halted_reasons
            and "daily_loss_limit" not in halted_reasons
        ):
            # Offline backtest: record the drawdown breach as a shadow event
            # (production would have halted here) but keep the curve trading.
            # Daily-loss breaches are always enforced (session-scoped, they
            # self-recover; the drawdown peak does not).
            halted = False
        # Catastrophe brake: evaluated only when enabled, so the default path
        # never reads the decision timestamp. Its reason does not set ``halted``
        # (no circuit-breaker side effect); the zeroing below is the action.
        liquidation_policy = self.drawdown_liquidation
        liquidation_red = False
        next_liquidation_state = drawdown_liquidation_state
        if liquidation_policy is not None and liquidation_policy.enabled:
            liquidation_red, next_liquidation_state = _drawdown_liquidation_step(
                liquidation_policy,
                account.drawdown_pct,
                session_date or signal_session_date(target.decision_timestamp),
                drawdown_liquidation_state,
            )
        if liquidation_red:
            halted_reasons.append(DRAWDOWN_LIQUIDATION_REASON)
        liquidate = liquidation_red and enforce_drawdown_halt

        # Only the external portion of a mixed position is immutable: the
        # managed quantity remains mutable inventory the bot may rebalance.
        # Unapproved symbols are fully immutable (the bot holds no claim).
        def immutable_value(position: PositionSnapshot) -> float:
            if canonical_symbol(position.symbol, position.market) not in approved:
                return position.market_value
            return abs(position.external_qty) * position.current_price

        immutable_gross = sum(
            immutable_value(position) / equity
            for position in account.positions
            if not position.is_flat
            and (canonical_symbol(position.symbol, position.market) not in approved or position.external_qty > 1e-12)
        )
        immutable_sleeve = sum(
            immutable_value(position) / equity
            for position in account.positions
            if not position.is_flat
            and position.market == market
            and (canonical_symbol(position.symbol, position.market) not in approved or position.external_qty > 1e-12)
        )
        desired = {canonical_symbol(weight.symbol, market): weight.weight for weight in target.weights}
        # The strategy's own weights, before this overlay lowers any of them: a
        # target below its strategy weight is a risk-driven reduction, not a
        # discretionary trim, so the gain-scaled band must not hold it.
        strategy_desired = dict(desired)
        # Strategy alpha priority per canonical symbol (0 = unranked). When
        # the strategy declares ranks, position-count enforcement below
        # retains best-ranked names; otherwise the legacy target-weight
        # order applies unchanged.
        rank_by_symbol = {canonical_symbol(weight.symbol, market): weight.rank for weight in target.weights}
        if mode not in {"backtest", "paper", "live"}:
            raise ValueError(f"Unsupported portfolio execution mode: {mode}")
        per_asset = self.limits.maximum_equity_asset
        # Immutable external inventory per symbol: never sellable, never
        # scaled. The strategy's total desired weight for a mixed symbol is
        # split internally into external_floor + managed_desired; only the
        # managed part is mutable by the bot.
        external_weight_by_symbol: dict[str, float] = {}
        for position in account.positions:
            if position.is_flat or position.external_qty <= 1e-12:
                continue
            symbol = canonical_symbol(position.symbol, position.market)
            external_weight_by_symbol[symbol] = (
                external_weight_by_symbol.get(symbol, 0.0)
                + abs(position.external_qty) * position.current_price / equity
            )
        for symbol in list(desired):
            desired[symbol] = min(desired[symbol], per_asset)
            current = current_weights.get(symbol, 0.0)
            external_weight = external_weight_by_symbol.get(symbol, 0.0)
            managed_weight = sum(
                abs(position.managed_qty) * position.current_price / equity
                for position in account.positions
                if canonical_symbol(position.symbol, position.market) == symbol
                and position.managed_qty > 1e-12
                and not position.is_flat
            )
            if managed_weight <= 1e-12 and external_weight > 0:
                # Purely external exposure: the bot neither owns nor may sell
                # this symbol, so it is not tradable inventory at all. Keep it
                # as immutable gross and drop it from the tradable target set;
                # otherwise a strategy BUY could grow a manually opened
                # position and consume a portfolio slot.
                desired.pop(symbol)
                continue
            if external_weight > per_asset and external_weight > desired[symbol]:
                # A total-per-symbol limit exceeded solely by immutable manual
                # inventory: never sell the manual inventory, block new managed
                # additions and surface the violation.
                halted_reasons.append(f"external_exceeds_asset_limit:{symbol}")
            if halted:
                desired[symbol] = min(desired[symbol], current)
            if liquidate:
                # Managed exposure goes to zero; the external floor is kept and
                # the planner only ever sells managed units. A symbol with no
                # external floor leaves the target entirely.
                if external_weight <= 1e-12:
                    desired.pop(symbol)
                    continue
                desired[symbol] = external_weight
            # External quantities are exposure, never inventory this runtime
            # is authorized to sell; for mixed positions keep the floor.
            desired[symbol] = max(desired[symbol], external_weight)

        # ``maximum_positions`` is an account-level cap. Count every distinct
        # account slot outside this market (a foreign crypto holding in the
        # shared account still occupies one) and every positive target slot
        # already reserved earlier in this decision cycle. Current-market
        # external/unapproved inventory is also fixed: it cannot be sold by
        # this runtime, so it consumes a slot even when the strategy omits it.
        # Managed current-market positions omitted by the target remain
        # replaceable through the existing SELL->BUY two-phase path; a later
        # decision still sees the broker's fresh snapshot before reusing that
        # slot.
        account_slots = {
            (position.market, canonical_symbol(position.symbol, position.market))
            for position in account.positions
            if not position.is_flat
        }
        reserved_slots = {
            (reserved_market, canonical_symbol(symbol, reserved_market))
            for reserved_market, symbol in reserved_position_keys
        }
        # Broker-only/manual working orders are account-wide slot claims even
        # when the adapter exposes only symbols. Classify the market once and
        # canonicalize before deduplication (e.g. a foreign BTCUSD == BTC/USD).
        open_order_slots: set[tuple[Market, str]] = set()
        for symbol in open_order_symbols:
            order_market = Market.CRYPTO if is_crypto(symbol) else Market.EQUITY
            open_order_slots.add((order_market, canonical_symbol(symbol, order_market)))
        fixed_slots = {slot for slot in account_slots if slot[0] != market}
        fixed_slots.update(reserved_slots)
        fixed_slots.update(open_order_slots)
        fixed_slots.update(
            (position.market, canonical_symbol(position.symbol, position.market))
            for position in account.positions
            if not position.is_flat
            and position.market == market
            and (canonical_symbol(position.symbol, position.market) not in approved or position.external_qty > 1e-12)
        )
        fixed_current_symbols = {symbol for fixed_market, symbol in fixed_slots if fixed_market == market}
        mandatory_symbols = set(desired) & fixed_current_symbols
        available_global_slots = max(0, self.limits.maximum_positions - len(fixed_slots))
        available_market_slots = max(0, self.limits.maximum_positions - len(fixed_current_symbols))
        optional_position_limit = min(available_global_slots, available_market_slots)
        pre_position_desired = dict(desired)
        pre_position_managed_desired = max(
            0.0,
            sum(pre_position_desired.values())
            - sum(external_weight_by_symbol.get(symbol, 0.0) for symbol in pre_position_desired),
        )
        # Position-count policy: the LIMIT is risk enforcement and stays
        # intact, but WHO survives must follow the strategy's declared alpha
        # rank when one exists. Sorting by target weight instead would invent
        # a second ranking system: inverse-vol sizing (and the per-asset cap,
        # which pins several names at exactly the cap) is not alpha, so
        # weight order can evict the strategy's best ideas on symbol-order
        # tie-breaks. Unranked targets keep the legacy weight ordering.
        if any(rank > 0 for rank in rank_by_symbol.values()):
            priority = sorted(
                desired,
                key=lambda symbol: (
                    rank_by_symbol.get(symbol, 0) or math.inf,
                    -desired[symbol],
                    symbol,
                ),
            )
        else:
            priority = sorted(desired, key=lambda symbol: (-desired[symbol], symbol))
        optional_priority = [symbol for symbol in priority if symbol not in mandatory_symbols]
        kept_symbols = mandatory_symbols | set(optional_priority[:optional_position_limit])
        if len(desired) > len(kept_symbols):
            desired = {symbol: weight for symbol, weight in desired.items() if symbol in kept_symbols}
            halted_reasons.append("position_count_limit")

        sleeve_limit = self.limits.maximum_equity
        # Internally separate the three exposure layers: immutable external
        # inventory (never sellable), managed desired exposure (mutable by the
        # planner) and the total desired account exposure (external + managed
        # + unapproved). maximum_allocation caps the MANAGED sleeve only;
        # maximum_gross and the cash buffer apply to the TOTAL account
        # exposure, with the external layer counted exactly once.
        external_in_desired = sum(
            abs(position.external_qty) * position.current_price / equity
            for position in account.positions
            if not position.is_flat
            and position.external_qty > 1e-12
            and canonical_symbol(position.symbol, position.market) in desired
        )
        managed_desired = max(0.0, sum(desired.values()) - external_in_desired)
        managed_limit = max(
            0.0,
            min(
                maximum_allocation,
                sleeve_limit - immutable_sleeve,
                self.limits.maximum_gross - immutable_gross,
                1.0 - self.limits.minimum_cash - immutable_gross,
            ),
        )
        if managed_desired > managed_limit and managed_desired > 0:
            scale = managed_limit / managed_desired
            # Scale ONLY the managed desired exposure of each symbol. The
            # external floor is immutable inventory and must never shrink as
            # part of the bot's allocation. final = external_floor +
            # managed_desired * scale, so the managed sleeve lands exactly on
            # the limit while external exposure is preserved.
            desired = {
                symbol: external_weight_by_symbol.get(symbol, 0.0)
                + max(0.0, weight - external_weight_by_symbol.get(symbol, 0.0)) * scale
                for symbol, weight in desired.items()
            }
            halted_reasons.append("exposure_scaled_to_limit")
        elif (
            not desired
            and pre_position_managed_desired > 1e-12
            and managed_limit <= 1e-12
            and "exposure_scaled_to_limit" not in halted_reasons
        ):
            # A position-count trim can remove every candidate before the
            # exposure scaler runs. Preserve the existing diagnostic contract
            # when the same immutable/account gross state leaves no exposure
            # capacity at all.
            halted_reasons.append("exposure_scaled_to_limit")
        weights = tuple(
            TargetWeight(symbol, weight, "portfolio_risk_overlay")
            for symbol, weight in sorted(desired.items())
            if weight > 1e-12
        )
        # Symbols this overlay pushed BELOW the strategy's own weight, plus
        # everything it can still reduce while halted: those sells are risk
        # enforcement, so the A1u band must decide them with the constant band.
        reduced = {
            weight.symbol for weight in weights if weight.weight < strategy_desired.get(weight.symbol, 0.0) - 1e-12
        }
        if halted:
            reduced.update(weight.symbol for weight in weights)
        if liquidate:
            # Every managed position the brake zeroes (zero-weight targets are
            # dropped from ``weights``, so they are named explicitly) is a
            # risk-driven reduction: the gain band and December deferral do not
            # hold it.
            reduced.update(set(strategy_desired) | set(current_weights))
        # gross_after is the actual expected account gross: immutable external
        # exposure plus the managed weights. The external floor carried inside
        # the target weights must not be counted a second time.
        weight_symbols = {weight.symbol for weight in weights}
        external_in_weights = sum(
            abs(position.external_qty) * position.current_price / equity
            for position in account.positions
            if not position.is_flat
            and position.external_qty > 1e-12
            and canonical_symbol(position.symbol, position.market) in weight_symbols
        )
        metadata: dict[str, object] = {
            **target.metadata,
            "risk_overlay_reasons": tuple(halted_reasons),
            "risk_overlay_halted": halted,
            "risk_overlay_managed_exposure": sum(weight.weight for weight in weights) - external_in_weights,
        }
        if reduced:
            metadata = gain_band_bypass_metadata(metadata, reduced, market)
        adjusted = replace(
            target,
            weights=weights,
            cash_weight=max(0.0, 1.0 - sum(weight.weight for weight in weights)),
            metadata=metadata,
        )
        gross_after = immutable_gross + sum(weight.weight for weight in weights) - external_in_weights
        return RiskOverlayDecision(
            adjusted,
            halted,
            tuple(halted_reasons),
            gross_before,
            gross_after,
            drawdown_liquidation_state=next_liquidation_state,
        )


class TargetToOrderPlanner:
    def __init__(self, policy: PortfolioExecutionPolicy | None = None) -> None:
        self.policy = policy or PortfolioExecutionPolicy()

    def plan(
        self,
        target: TargetPortfolio,
        account: AccountSnapshot,
        *,
        market: Market,
        prices: dict[str, float],
        rules: dict[str, AssetTradingRules],
        approved_symbols: tuple[str, ...],
        decision_id: str,
        open_order_symbols: set[str] | None = None,
        open_order_sides: Mapping[str, set[str] | frozenset[str]] | None = None,
        client_order_salt: str = "",
        open_lots: Mapping[str, Sequence[Sequence[float]]] | None = None,
        gain_band_holds: list[GainBandHold] | None = None,
        december_deferral_holds: list[DecemberDeferralHold] | None = None,
        gain_band_tl_shadow: Callable[[str, Sequence[Sequence[float]], float, float, datetime], float | None]
        | None = None,
    ) -> tuple[PortfolioOrderRequest, ...]:
        """Build rebalance orders from a target.

        ``decision_id`` is the account-local, configuration-scoped decision
        identity from the runtime repository; it seeds the client order ids
        so that two configurations sharing a strategy name, version and
        timestamp never produce the same broker idempotency key.
        ``client_order_salt`` is the per-ledger salt that keeps those ids
        distinct from ids another ledger derives for the same decision.

        ``open_lots`` maps a symbol to the bot's own (managed) open lots,
        oldest first, as ``(quantity, fee-inclusive unit cost)``; it is the
        only input of the ``downsize_gain_band`` policy and is ignored by every
        other decision. ``gain_band_holds`` and ``december_deferral_holds`` are
        optional diagnostics sinks: one hold is appended per suppressed or
        postponed downsize, which the caller records with the decision
        (metadata / summary) instead of silently dropping the order.
        """
        _validate_unique_account_positions(account)
        approved = {canonical_symbol(symbol, market) for symbol in approved_symbols}
        # Open orders arrive broker-wide (``all_open_order_symbols``); only
        # this market's working orders must suppress order placement here. A
        # foreign crypto order symbol is never canonicalized as an equity.
        open_symbols = {
            canonical_symbol(symbol, market)
            for symbol in (open_order_symbols or set())
            if is_crypto(symbol) == (market == Market.CRYPTO)
        }
        # Account-local ledger rows retain sides, unlike the broker-wide
        # symbol-only endpoint. A lone pending BUY is safe to coexist with a
        # risk-reducing SELL, but every risk-increasing/duplicate direction
        # remains suppressed. Broker-visible symbols stay conservative and
        # suppress both directions through ``open_symbols``.
        local_order_sides: dict[str, frozenset[str]] = {}
        for raw_symbol, raw_sides in (open_order_sides or {}).items():
            if is_crypto(raw_symbol) != (market == Market.CRYPTO):
                continue
            sides = raw_sides
            if isinstance(sides, (str, PortfolioOrderSide)):
                sides = {str(getattr(sides, "value", sides))}
            local_order_sides[canonical_symbol(raw_symbol, market)] = frozenset(
                str(getattr(side, "value", side)).lower() for side in sides
            )
        positions = {
            canonical_symbol(position.symbol, position.market): position
            for position in account.positions
            if position.market == market and not position.is_flat
        }
        target_weights = {canonical_symbol(weight.symbol, market): weight.weight for weight in target.weights}
        symbols = approved & (set(target_weights) | set(positions))
        # Symbols whose current target the RISK layer produced: their downsize
        # must execute, so they are decided by the constant band.
        risk_reduced = gain_band_bypass_symbols(target, market)
        orders: list[PortfolioOrderRequest] = []
        for symbol in sorted(symbols):
            price = prices.get(symbol) or (positions[symbol].current_price if symbol in positions else 0.0)
            rule = rules.get(symbol)
            if price <= 0 or rule is None:
                continue
            position = positions.get(symbol)
            current_qty = position.qty if position is not None else 0.0
            managed_qty = position.managed_qty if position is not None else 0.0
            target_qty = target_weights.get(symbol, 0.0) * account.equity / price
            current_weight = current_qty * price / account.equity if account.equity > 0 else 0.0
            target_weight = target_weights.get(symbol, 0.0)
            gap = abs(target_weight - current_weight)
            # Shared execution policy: sub-band drift produces no order in
            # backtest, paper OR live. This keeps turnover/cost parity between
            # the historical simulator and the live broker path.
            #
            # The December window (H2) is tested FIRST, exactly as the research
            # composition K does: an in-window downsize is postponed whatever
            # its size, so the band never gets a say in December. Both cases
            # cover the same shape — a downward resize between two positive
            # weights — and must not be skipped here: whether they bind is only
            # known after the units the sell would consume are known. A
            # reduction the risk overlay produced (or any risk-reduction-only
            # decision) is not the strategy's own trim: it keeps the constant
            # band, executes, and is never deferred.
            deferral_sessions = self.policy.december_deferral_sessions
            deferral_case = (
                deferral_sessions > 0
                and 0.0 < target_weight < current_weight
                and symbol not in risk_reduced
                and gap >= self.policy.rebalance_band_pct
                and in_december_deferral_window(target.decision_timestamp, deferral_sessions)
            )
            gain_band_case = (
                self.policy.downsize_gain_band is not None
                and 0.0 < target_weight < current_weight
                and symbol not in risk_reduced
                and not deferral_case
            )
            if gap < self.policy.rebalance_band_pct and not gain_band_case:
                continue
            delta = target_qty - current_qty
            if delta < 0:
                # The bot may only sell its own (managed) units; manual
                # inventory in a mixed position is never a sellable source.
                delta = -min(abs(delta), managed_qty)
                if abs(delta) <= 1e-12:
                    continue
                if deferral_case:
                    if december_deferral_holds is not None:
                        december_deferral_holds.append(
                            DecemberDeferralHold(symbol, target_weight, current_weight, deferral_sessions)
                        )
                    continue
                if gain_band_case:
                    verdict = downsize_gain_band_verdict(
                        self.policy.downsize_gain_band,
                        target_weight=target_weight,
                        current_weight=current_weight,
                        units=abs(delta),
                        price=price,
                        lots=(open_lots or {}).get(symbol),
                        managed_qty=managed_qty,
                        base_band=self.policy.rebalance_band_pct,
                    )
                    if not verdict.applied:
                        # FAIL-SAFE: without a consistent lot book the resize is
                        # decided by the constant band, i.e. exactly as before.
                        log.info(
                            "Downsize gain band unavailable for %s (%s); using the %.4f band",
                            symbol,
                            verdict.fallback,
                            self.policy.rebalance_band_pct,
                        )
                    if gap < verdict.band:
                        if verdict.applied and gap >= self.policy.rebalance_band_pct and gain_band_holds is not None:
                            tl_fraction, tl_verdict = None, None
                            if gain_band_tl_shadow is not None:
                                # Metadata only: a shadow failure never changes the USD hold.
                                try:
                                    tl_fraction = gain_band_tl_shadow(
                                        symbol,
                                        (open_lots or {}).get(symbol) or (),
                                        abs(delta),
                                        price,
                                        target.decision_timestamp,
                                    )
                                except Exception:
                                    log.debug("TL gain shadow failed for %s", symbol, exc_info=True)
                                    tl_fraction = None
                                if tl_fraction is not None and self.policy.downsize_gain_band is not None:
                                    tl_verdict = downsize_gain_band_tl_verdict(
                                        self.policy.downsize_gain_band, gap=gap, tl_fraction=tl_fraction
                                    )
                            gain_band_holds.append(
                                GainBandHold(
                                    symbol,
                                    verdict.g_frac or 0.0,
                                    verdict.band,
                                    verdict.gap,
                                    tl_fraction,
                                    tl_verdict,
                                )
                            )
                        continue
            elif gap < self.policy.rebalance_band_pct:
                continue
            if delta > 0 and position is not None and managed_qty <= 1e-12:
                # Never grow a manually opened position; the risk overlay
                # already excludes purely external symbols, this is defense
                # in depth against an external-position BUY.
                continue
            side = PortfolioOrderSide.BUY if delta > 0 else PortfolioOrderSide.SELL
            if symbol in open_symbols:
                continue
            local_sides = local_order_sides.get(symbol)
            if local_sides is not None and not (
                side == PortfolioOrderSide.SELL and local_sides == frozenset({PortfolioOrderSide.BUY.value})
            ):
                continue
            quantity = floor_increment(abs(delta), rule.quantity_increment)
            if position is not None and side == PortfolioOrderSide.SELL:
                available = position.qty_available if position.qty_available is not None else position.qty
                quantity = min(quantity, max(0.0, available))
                quantity = floor_increment(quantity, rule.quantity_increment)
            if quantity < rule.minimum_order_size:
                continue
            if quantity * price < max(self.policy.minimum_order_notional, rule.minimum_order_notional):
                continue
            # The runtime sizes a BUY at max(reference, mark) and validates it
            # again at the reference alone. Carry the same max here so the
            # notional that was sized is the notional that is validated.
            reference_price = price
            if side == PortfolioOrderSide.BUY and position is not None:
                reference_price = max(price, position.current_price)
            orders.append(
                PortfolioOrderRequest(
                    client_order_id=_client_order_id(decision_id, symbol, side, client_order_salt),
                    decision_id=decision_id,
                    symbol=symbol,
                    market=market,
                    side=side,
                    quantity=quantity,
                    reference_price=reference_price,
                    decision_timestamp=target.decision_timestamp,
                    strategy_name=target.strategy_name,
                    strategy_version=target.strategy_version,
                )
            )
        return tuple(
            sorted(
                orders,
                key=lambda order: (
                    0 if order.side == PortfolioOrderSide.SELL else 1,
                    order.symbol,
                ),
            )
        )


def reserve_opening_order_slots(
    orders: tuple[PortfolioOrderRequest, ...],
    account: AccountSnapshot,
    *,
    maximum_positions: int,
    reserved_position_keys: tuple[tuple[Market, str], ...] = (),
    open_order_symbols: tuple[str, ...] = (),
) -> tuple[PortfolioOrderRequest, ...]:
    """Admit opening BUYs against occupied and working account slots.

    Caller order is the existing deterministic planner priority. A requested
    exit never releases a slot; only a fresh zero-position snapshot does.
    """
    slots = {
        (position.market, canonical_symbol(position.symbol, position.market))
        for position in account.positions
        if not position.is_flat
    }
    slots.update((market, canonical_symbol(symbol, market)) for market, symbol in reserved_position_keys)
    for symbol in open_order_symbols:
        market = Market.CRYPTO if is_crypto(symbol) else Market.EQUITY
        slots.add((market, canonical_symbol(symbol, market)))
    admitted: list[PortfolioOrderRequest] = []
    for order in orders:
        key = (order.market, canonical_symbol(order.symbol, order.market))
        if order.side == PortfolioOrderSide.SELL or key in slots:
            admitted.append(order)
        elif len(slots) < maximum_positions:
            slots.add(key)
            admitted.append(order)
    return tuple(admitted)


def floor_increment(value: float, increment: float) -> float:
    """Floor ``value`` to a whole multiple of the broker ``increment``.

    Single source of truth for order-quantity rounding: used by the planner,
    by AlpacaBroker submission/fill-verification paths and by the portfolio
    repository so all three can never disagree on a floored quantity.
    """
    if not math.isfinite(value) or not math.isfinite(increment) or increment <= 0:
        raise ValueError(
            f"floor_increment requires finite positive increment, got value={value!r} increment={increment!r}"
        )
    raw = Decimal(str(value))
    step = Decimal(str(increment))
    return float((raw / step).to_integral_value(rounding=ROUND_DOWN) * step)


def floor_increment_decimal(value: Decimal, increment: Decimal) -> Decimal:
    """``floor_increment`` for callers already holding exact ``Decimal`` values.

    Same operation sequence as :func:`floor_increment` — divide, round down to
    a whole multiple, multiply back — without a float round-trip that could
    perturb broker-exact quantities.
    """
    return (value / increment).to_integral_value(rounding=ROUND_DOWN) * increment


def _client_order_id(decision_id: str, symbol: str, side: PortfolioOrderSide, salt: str = "") -> str:
    """Deterministic broker idempotency key for one decision leg.

    Format ``parayabuck-{decision_id}-{salt}-{symbol}-{side}`` is stable
    across restarts and unique per decision/symbol/side. The decision_id is
    scoped to account+digest+timestamp+market, but a fresh ledger (or another
    process on the same account) derives the same decision_id, so the
    persisted per-ledger ``salt`` keeps its key from matching — and adopting —
    a broker order this ledger never created. An empty salt yields the legacy
    ``parayabuck-{decision_id}-{symbol}-{side}`` form. Persisted via ``portfolio_orders``;
    duplicate decisions map to the same logical order and ``INSERT OR IGNORE``
    prevents a second broker submission. Partial fills keep the same key and
    only the residual is re-planned; broker timeouts leave the row at
    ``reconciliation_required`` and require a ``reconcile()`` before retry.
    """
    canonical = canonical_symbol(symbol, Market.CRYPTO if "/" in symbol else Market.EQUITY)
    if salt:
        return f"parayabuck-{decision_id}-{salt}-{canonical}-{side.value}"
    return f"parayabuck-{decision_id}-{canonical}-{side.value}"


__all__ = [
    "DECEMBER_DEFERRAL_KEY",
    "DRAWDOWN_LIQUIDATION_REASON",
    "DrawdownLiquidationState",
    "GAIN_BAND_BYPASS_KEY",
    "AssetTradingRules",
    "DecemberDeferralHold",
    "DownsizeGainBandVerdict",
    "GainBandHold",
    "PortfolioExecutionPolicy",
    "PortfolioOrderRequest",
    "PortfolioOrderSide",
    "PortfolioRiskLimits",
    "PortfolioRiskOverlay",
    "RiskOverlayDecision",
    "TargetToOrderPlanner",
    "downsize_gain_band_tl_verdict",
    "downsize_gain_band_verdict",
    "downsize_gain_fraction",
    "downsize_gain_fraction_tl",
    "floor_increment",
    "floor_increment_decimal",
    "gain_band_bypass_metadata",
    "gain_band_bypass_symbols",
    "in_december_deferral_window",
    "portfolio_execution_policy_from_config",
    "record_december_deferral_holds",
    "record_gain_band_holds",
    "validate_order_batch",
]


def validate_order_batch(
    orders: Sequence[PortfolioOrderRequest],
    rules_by_symbol: Mapping[str, AssetTradingRules],
    *,
    minimum_order_notional: float,
) -> list[tuple[str, str]]:
    """Problems in one planned order batch, as ``(client_order_id, reason)``.

    Checked before any order of the batch is recorded as ``submitting``. Each
    order must have a positive finite quantity that is a whole multiple of its
    symbol's quantity increment, at least the symbol's minimum size, and a
    notional at or above both ``minimum_order_notional`` and the symbol's
    minimum notional. Conflicting orders are reported together: the same
    (symbol, side) twice, or a BUY and a SELL for one symbol, leaves the batch
    ambiguous, so every order of the conflict is listed and the caller drops
    them all rather than guessing which one is right. An empty list means the
    batch may be sent.
    """
    problems: list[tuple[str, str]] = []
    legs: dict[tuple[str, str], list[str]] = {}
    sides_by_symbol: dict[str, set[str]] = {}
    for order in orders:
        legs.setdefault((order.symbol, order.side.value), []).append(order.client_order_id)
        sides_by_symbol.setdefault(order.symbol, set()).add(order.side.value)
    for (symbol, side), client_order_ids in legs.items():
        if len(client_order_ids) > 1:
            for client_order_id in client_order_ids:
                problems.append((client_order_id, f"duplicate {side} order for {symbol} in batch"))
    for order in orders:
        if len(sides_by_symbol[order.symbol]) > 1:
            problems.append((order.client_order_id, f"BUY and SELL both planned for {order.symbol}"))
    for order in orders:
        quantity = order.quantity
        price = order.reference_price
        if not math.isfinite(quantity) or quantity <= 0:
            problems.append((order.client_order_id, f"quantity must be positive and finite, got {quantity!r}"))
            continue
        rule = rules_by_symbol.get(order.symbol)
        if rule is None:
            problems.append((order.client_order_id, f"no trading rules for {order.symbol}"))
            continue
        if rule.quantity_increment <= 0:
            problems.append((order.client_order_id, f"non-positive quantity increment for {order.symbol}"))
            continue
        # Exact grid test on the decimal values: a float quotient cannot tell a
        # 9-decimal quantity from one off the grid when the increment is 1e-9.
        try:
            off_grid = Decimal(str(quantity)) % Decimal(str(rule.quantity_increment)) != 0
        except InvalidOperation:
            off_grid = True
        if off_grid:
            problems.append(
                (
                    order.client_order_id,
                    f"quantity {quantity!r} is not a multiple of increment {rule.quantity_increment!r}",
                )
            )
        if floor_increment(quantity, rule.quantity_increment) <= 0 or quantity < rule.minimum_order_size:
            problems.append(
                (order.client_order_id, f"quantity {quantity!r} below minimum size {rule.minimum_order_size!r}")
            )
        if not math.isfinite(price) or price <= 0:
            problems.append((order.client_order_id, f"reference price must be positive and finite, got {price!r}"))
            continue
        if quantity * price < max(minimum_order_notional, rule.minimum_order_notional):
            problems.append(
                (
                    order.client_order_id,
                    f"notional {quantity * price!r} below minimum {max(minimum_order_notional, rule.minimum_order_notional)!r}",
                )
            )
    return problems
