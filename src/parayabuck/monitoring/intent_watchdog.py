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
  filled, leaving the previous position in a downtrend). A small residual is
  tolerated so partially filled exits and fractional-share rounding do not
  raise it.

Everything here is pure and deterministic given ``now``; the exchange
schedule comes from pandas-market-calendars (the calendar the warmup and
month-end helpers already use), with holidays and early closes. Alarms are
observability only: they never gate or alter trading.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta
from functools import lru_cache

from parayabuck.core.models import AccountSnapshot, Market
from parayabuck.markets.symbols import canonical_symbol
from parayabuck.markets.timeframes import NEW_YORK

# The runtime flushes the session-close daily bar no earlier than 16:00 ET
# (EngineRuntime._flush_aggregators), including on early-close days.
_EQUITY_DECISION_FLOOR = time(16, 0)
_CALENDAR_LOOKBACK_DAYS = 14
# Absolute weight, as a fraction of account equity, by which a position may
# exceed its target without alarming: the dust a partially filled exit leaves
# behind. An expired exit leaves the whole position, orders of magnitude more.
_EXIT_RESIDUAL_WEIGHT = 0.01


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


@lru_cache(maxsize=64)
def nyse_sessions(start: date, end: date) -> tuple[tuple[date, datetime, datetime], ...]:
    """``(session_date, open_utc, close_utc)`` for every NYSE session in [start, end]."""
    from pandas_market_calendars import get_calendar

    schedule = get_calendar("NYSE").schedule(start_date=start, end_date=end)
    return tuple(
        (index.date(), row.market_open.to_pydatetime(), row.market_close.to_pydatetime())
        for index, row in schedule.iterrows()
    )


def _sessions_around(now: datetime) -> tuple[tuple[date, datetime, datetime], ...]:
    today = now.astimezone(NEW_YORK).date()
    return nyse_sessions(
        today - timedelta(days=_CALENDAR_LOOKBACK_DAYS), today + timedelta(days=_CALENDAR_LOOKBACK_DAYS)
    )


def equity_decision_time(session: date, close: datetime) -> datetime:
    """Wall time the session-close decision is expected (never before 16:00 ET)."""
    floor = datetime.combine(session, _EQUITY_DECISION_FLOOR, tzinfo=NEW_YORK)
    return max(close, floor)


def last_due_equity_session(now: datetime, grace: timedelta) -> tuple[date, datetime] | None:
    """The newest session whose decision deadline (decision time + grace) has passed."""
    for session, _open, close in reversed(_sessions_around(now)):
        deadline = equity_decision_time(session, close) + grace
        if deadline <= now:
            return session, deadline
    return None


def next_equity_open_after(moment: datetime) -> datetime | None:
    for _session, market_open, _close in _sessions_around(moment):
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
        if actual > target + _EXIT_RESIDUAL_WEIGHT:
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
) -> list[IntentAlarm]:
    alarms: list[IntentAlarm] = []
    due = last_due_equity_session(now, settings.equity_decision_grace)
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
        close = next((c for s, _o, c in _sessions_around(now) if s == session), None)
        decided_at = equity_decision_time(session, close) if close is not None else latest.decision_timestamp
        next_open = next_equity_open_after(decided_at)
        if next_open is not None:
            fill_deadline = next_open + settings.equity_fill_grace
            if now >= fill_deadline:
                alarms.extend(_under_target(Market.EQUITY, latest, account, settings, fill_deadline))
                alarms.extend(_over_target(Market.EQUITY, latest, account, fill_deadline))
    return alarms


def alarm_codes(alarms: Iterable[IntentAlarm]) -> list[str]:
    return sorted({alarm.code for alarm in alarms})


__all__ = [
    "IntentAlarm",
    "IntentWatchdogSettings",
    "LatestDecision",
    "alarm_codes",
    "equity_decision_time",
    "evaluate_equity",
    "held_weights",
    "last_due_equity_session",
    "next_equity_open_after",
    "nyse_sessions",
]
