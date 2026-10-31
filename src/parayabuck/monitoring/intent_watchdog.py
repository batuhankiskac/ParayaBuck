"""Intent-vs-reality watchdog: did the strategy actually act on schedule?

Process-liveness checks cannot see a runtime that is alive but silently not
deciding (2026-09-25..29: four sessions without an equity decision while
every health check was green). This module compares what SHOULD have
happened by now against what the decision database and the broker account
show:

* ``equity_decision_missing`` — the schedule says a decision is due (the
  last NYSE session's close plus a grace window) and no persisted decision
  covers it;
* ``equity_position_under_target`` — the latest decision targets a positive
  weight but, after the expected fill window (the next session open plus
  grace), the account holds less than ``minimum_held_fraction`` of that
  weight;
* ``equity_signal_source_hold`` — the latest decision deliberately kept the
  current position because the stream-built and SIP daily bars disagreed on
  the trend (signal cross-check). This is the true reason the account may
  not follow the strategy's primary target, so it replaces the
  under-target check for that decision.

* ``equity_position_over_target`` — the mirror of the under-target check:
  after the fill window the account still holds more of a symbol than the
  decision targeted (the everyday case is a cash target whose DAY SELL never
  filled, leaving the previous position in a downtrend). A residual is
  tolerated so partially filled exits, fractional-share rounding and the
  ordinary price drift of a held position between decisions do not raise it.
  A downsize the A1u gain-scaled band deliberately did NOT trade
  (``metadata["gain_band"]``) widens that residual by the recorded band, since
  holding above the target IS the recorded intent there; an exit to zero is
  never a band case and still alarms. A downsize the December window postponed
  (``metadata["december_deferral"]``, H2) is tolerated without limit while its
  target stays positive, because the whole resize is deliberately held until
  the first January decision re-plans it; an exit to zero is never deferred.

Everything here is pure and deterministic given ``now``; the exchange
schedule comes from :mod:`parayabuck.markets.nyse_calendar` — the one
calendar the engine uses — with holidays and early closes. Alarms are
observability only: they never gate or alter trading.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta

from parayabuck.core.models import AccountSnapshot, Market
from parayabuck.markets.nyse_calendar import NEW_YORK, calendar_degraded_reason, sessions_around
from parayabuck.markets.symbols import canonical_symbol

# Absolute weight, as a fraction of account equity, by which a position may
# exceed its target without alarming: the dust a partially filled exit leaves
# behind. An expired exit leaves the whole position, orders of magnitude more.
_EXIT_RESIDUAL_WEIGHT = 0.01
# Relative slack on a positive target: a held position's weight drifts with its
# own price between decisions (w' = w(1+r)/(1+w*r)), so a 0.60 QLD target reads
# ~0.62 after a +10% day. 10% of the target covers any single-session move a
# 2x ETF makes, while an unfilled exit (whole position vs a lower target) or a
# double fill stays far outside it.
_OVER_TARGET_RELATIVE = 0.10


def over_target_tolerance(target: float, held_band: float = 0.0) -> float:
    """Weight a holding may exceed ``target`` by before it counts as over target.

    ``held_band`` is the gain-scaled downsize band the latest decision's
    metadata recorded for this symbol (A1u,
    ``docs/research/gain_band_integration_v1.md``). That decision DELIBERATELY
    left the position above its target because the drift had not reached
    ``B_down(g_frac)`` yet, so the position may legitimately sit up to the band
    above the target until the next decision: the tolerance becomes the band
    plus the same relative slack the target itself gets, never less than the
    usual tolerance. A symbol the band did not hold, and an exit to zero (where
    the band can never apply), keep the constant tolerance, so an unfilled exit
    still alarms.
    """
    base = max(_EXIT_RESIDUAL_WEIGHT, _OVER_TARGET_RELATIVE * max(0.0, target))
    if held_band <= 0.0:
        return base
    return max(base, held_band + _OVER_TARGET_RELATIVE * max(0.0, target))


@dataclass(frozen=True)
class IntentWatchdogSettings:
    equity_decision_grace: timedelta = timedelta(minutes=45)
    equity_fill_grace: timedelta = timedelta(minutes=30)
    minimum_held_fraction: float = 0.5


@dataclass(frozen=True)
class LatestDecision:
    decision_timestamp: datetime
    weights: dict[str, float]
    # ``signal_cross_check`` metadata of the decision (None when absent).
    signal_cross_check: dict[str, object] | None = None
    # ``gain_band`` metadata of the decision: one mapping per downsize the A1u
    # gain-scaled band suppressed (None/absent when the band touched nothing).
    gain_band: tuple[dict[str, object], ...] | None = None
    # ``december_deferral`` metadata of the decision: one mapping per downsize
    # the December window postponed (H2, ``docs/research/band_h2_combo_v1.md``);
    # None/absent when nothing was deferred.
    december_deferral: tuple[dict[str, object], ...] | None = None

    @property
    def signal_source_hold(self) -> bool:
        return isinstance(self.signal_cross_check, dict) and self.signal_cross_check.get("hold") is True


@dataclass(frozen=True)
class IntentAlarm:
    code: str
    market: str
    message: str
    due_at: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


def equity_decision_time(close: datetime) -> datetime:
    """Wall time the session-close decision is expected: the session's close."""
    return close


def last_due_equity_session(now: datetime, grace: timedelta) -> tuple[date, datetime] | None:
    """The newest session whose decision deadline (decision time + grace) has passed."""
    for session, _open, close in reversed(sessions_around(now)):
        deadline = equity_decision_time(close) + grace
        if deadline <= now:
            return session, deadline
    return None


def next_equity_open_after(moment: datetime) -> datetime | None:
    for _session, market_open, _close in sessions_around(moment):
        if market_open > moment:
            return market_open
    return None


def held_weights(account: AccountSnapshot, market: Market) -> dict[str, float]:
    if account.equity <= 0:
        return {}
    held: dict[str, float] = {}
    for position in account.positions:
        if position.market != market or position.is_flat:
            continue
        symbol = canonical_symbol(position.symbol, position.market)
        held[symbol] = held.get(symbol, 0.0) + position.market_value / account.equity
    return held


def _under_target(
    market: Market,
    latest: LatestDecision,
    account: AccountSnapshot | None,
    settings: IntentWatchdogSettings,
    due_at: datetime,
) -> list[IntentAlarm]:
    if account is None:
        return []
    held = held_weights(account, market)
    short: list[str] = []
    for raw_symbol, target in sorted(latest.weights.items()):
        if target <= 1e-9:
            continue
        try:
            symbol = canonical_symbol(raw_symbol, market)
        except ValueError:
            symbol = raw_symbol
        actual = held.get(symbol, 0.0)
        if actual < target * settings.minimum_held_fraction:
            short.append(f"{symbol} held {actual:.1%} vs target {target:.1%}")
    if not short:
        return []
    return [
        IntentAlarm(
            code=f"{market.value}_position_under_target",
            market=market.value,
            message=(
                f"{market.value} decision {latest.decision_timestamp.isoformat()} not reflected in the account "
                f"after the fill window: " + "; ".join(short)
            ),
            due_at=due_at.isoformat(),
        )
    ]


def _recorded_bands(latest: LatestDecision, market: Market) -> dict[str, float]:
    """Gain-scaled bands the latest decision recorded, per canonical symbol.

    ``metadata["gain_band"]`` is the planner's own record of the downsizes A1u
    deliberately did not trade (``GainBandHold.as_metadata``). The largest band
    wins when a symbol appears twice; malformed rows are ignored, never fatal.
    """
    entries = latest.gain_band
    if not isinstance(entries, (tuple, list)):
        return {}
    bands: dict[str, float] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        raw_symbol, band = entry.get("symbol"), entry.get("band")
        if not isinstance(raw_symbol, str) or not isinstance(band, (int, float)) or isinstance(band, bool):
            continue
        try:
            symbol = canonical_symbol(raw_symbol, market)
        except ValueError:
            symbol = raw_symbol
        bands[symbol] = max(bands.get(symbol, 0.0), float(band))
    return bands


def _deferred_symbols(latest: LatestDecision, market: Market) -> frozenset[str]:
    """Canonical symbols the latest decision postponed through the December window.

    ``metadata["december_deferral"]`` is the planner's own record of the
    downsizes H2 deliberately did not trade (``DecemberDeferralHold``). A row
    is used only when it carries a symbol; malformed rows are ignored, never
    fatal.
    """
    entries = latest.december_deferral
    if not isinstance(entries, (tuple, list)):
        return frozenset()
    symbols: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        raw_symbol = entry.get("symbol")
        if not isinstance(raw_symbol, str):
            continue
        try:
            symbols.add(canonical_symbol(raw_symbol, market))
        except ValueError:
            symbols.add(raw_symbol)
    return frozenset(symbols)


def _over_target(
    market: Market,
    latest: LatestDecision,
    account: AccountSnapshot | None,
    due_at: datetime,
) -> list[IntentAlarm]:
    """Managed holdings the account still carries above what was decided for."""
    if account is None:
        return []
    held = held_weights(account, market)
    bands = _recorded_bands(latest, market)
    deferred = _deferred_symbols(latest, market)
    targets: dict[str, float] = {}
    for raw_symbol, weight in latest.weights.items():
        try:
            symbol = canonical_symbol(raw_symbol, market)
        except ValueError:
            symbol = raw_symbol
        targets[symbol] = targets.get(symbol, 0.0) + float(weight)
    over: list[str] = []
    for symbol in sorted(set(held) | set(targets)):
        actual = held.get(symbol, 0.0)
        target = targets.get(symbol, 0.0)
        # A December deferral holds the WHOLE downsize by construction, so any
        # excess over a POSITIVE target is the recorded intent until the first
        # January decision re-plans it (H2, ``docs/research/
        # band_h2_combo_v1.md``). An exit to zero is never a deferral case
        # (and never recorded), so an unfilled exit still alarms.
        if target > 1e-9 and symbol in deferred:
            continue
        # A recorded band only justifies a holding above a POSITIVE target: the
        # band never applies to an exit, so a target of zero keeps the constant
        # tolerance and an unfilled exit still alarms.
        held_band = bands.get(symbol, 0.0) if target > 1e-9 else 0.0
        if actual > target + over_target_tolerance(target, held_band):
            over.append(f"{symbol} held {actual:.1%} vs target {target:.1%}")
    if not over:
        return []
    return [
        IntentAlarm(
            code=f"{market.value}_position_over_target",
            market=market.value,
            message=(
                f"{market.value} decision {latest.decision_timestamp.isoformat()} left more than it targeted in the "
                f"account after the fill window: " + "; ".join(over)
            ),
            due_at=due_at.isoformat(),
        )
    ]


def evaluate_equity(
    now: datetime,
    latest: LatestDecision | None,
    account: AccountSnapshot | None,
    *,
    running_since: datetime | None,
    settings: IntentWatchdogSettings,
    calendar_degraded: bool | None = None,
) -> list[IntentAlarm]:
    alarms: list[IntentAlarm] = []
    if calendar_degraded is None:
        calendar_degraded = calendar_degraded_reason() is not None
    # While the NYSE calendar is degraded the weekday fallback counts holidays as
    # due sessions, so a missed-decision alarm would be false (and would fail
    # readiness and the dead-man). The check is skipped until the calendar is back,
    # as the premarket probe skips its schedule comparisons.
    due = None if calendar_degraded else last_due_equity_session(now, settings.equity_decision_grace)
    if due is not None:
        due_session, deadline = due
        latest_session = latest.decision_timestamp.astimezone(NEW_YORK).date() if latest is not None else None
        # Without any decision history only a runtime that was up across the
        # expected decision can have missed it (a fresh deployment decides
        # through the startup catch-up instead).
        expected_while_running = (
            running_since is not None and running_since <= deadline - settings.equity_decision_grace
        )
        if (latest_session is not None and latest_session < due_session) or (
            latest_session is None and expected_while_running
        ):
            last = latest_session.isoformat() if latest_session is not None else "none"
            alarms.append(
                IntentAlarm(
                    code="equity_decision_missing",
                    market=Market.EQUITY.value,
                    message=(
                        f"no equity decision recorded for NYSE session {due_session.isoformat()} "
                        f"(due by {deadline.isoformat()}); last decision session {last}"
                    ),
                    due_at=deadline.isoformat(),
                )
            )
    if latest is not None and latest.signal_source_hold:
        check = latest.signal_cross_check or {}
        primary = check.get("primary")
        secondary = check.get("secondary")
        primary_trend = primary.get("trend_state") if isinstance(primary, dict) else None
        secondary_trend = secondary.get("trend_state") if isinstance(secondary, dict) else None
        hold_session = latest.decision_timestamp.astimezone(NEW_YORK).date().isoformat()
        alarms.append(
            IntentAlarm(
                code="equity_signal_source_hold",
                market=Market.EQUITY.value,
                message=(
                    f"equity decision for session {hold_session} held the current position: the IEX stream bar "
                    f"({primary_trend}) and the SIP daily bar ({secondary_trend}) "
                    f"disagree on the trend; close gaps bps {check.get('close_gap_bps')}"
                ),
                due_at=latest.decision_timestamp.isoformat(),
            )
        )
    elif latest is not None:
        session = latest.decision_timestamp.astimezone(NEW_YORK).date()
        close = next((c for s, _o, c in sessions_around(now) if s == session), None)
        decided_at = equity_decision_time(close) if close is not None else latest.decision_timestamp
        next_open = next_equity_open_after(decided_at)
        if next_open is not None:
            fill_deadline = next_open + settings.equity_fill_grace
            if now >= fill_deadline:
                alarms.extend(_under_target(Market.EQUITY, latest, account, settings, fill_deadline))
                alarms.extend(_over_target(Market.EQUITY, latest, account, fill_deadline))
    return alarms


def degraded_reasons(health: object) -> list[str]:
    """Active intent-watchdog alarm codes from a RuntimeHealth object or dict."""
    watchdog = (
        getattr(health, "intent_watchdog", None) if not isinstance(health, dict) else health.get("intent_watchdog")
    )
    reasons = watchdog.get("degraded_reasons") if isinstance(watchdog, dict) else None
    return [str(reason) for reason in reasons] if isinstance(reasons, list) else []


__all__ = [
    "IntentAlarm",
    "IntentWatchdogSettings",
    "LatestDecision",
    "degraded_reasons",
    "equity_decision_time",
    "evaluate_equity",
    "held_weights",
    "last_due_equity_session",
    "next_equity_open_after",
    "over_target_tolerance",
]
