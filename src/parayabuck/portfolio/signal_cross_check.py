"""Second-source cross-check of the equity session-close signal.

The live decision bar is aggregated from IEX 1-minute stream bars (one
exchange, last minute before 16:00 ET). The cross-check recomputes the same
strategy decision on a second daily bar for the same New York session (the
Alpaca consolidated SIP daily bar, fetched after the close) and compares:

* ``agree`` — both sources give the same trend state: trade as usual;
* ``trend_mismatch`` — the sources disagree on the trend state: the runtime
  holds the current position for this session (no orders), records the
  flag on the decision and alerts;
* ``secondary_unavailable`` — no second-source bar within the bounded retry
  window: the runtime trades on the primary source and alerts (a missed
  session costs more than an unchecked one).

Independently of the trend, ``close_divergence`` flags any symbol whose two
closes differ by more than ``close_divergence_bps``.

Everything here is pure and deterministic; the runtime owns fetching,
retry timing and alerting. The offline backtest has one data source and
never calls this module.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Any

from parayabuck.core.models import Bar, Market
from parayabuck.data.adjustment import signal_session_date
from parayabuck.markets.nyse_calendar import NEW_YORK, session_close_utc
from parayabuck.markets.symbols import canonical_symbol
from parayabuck.portfolio.base import PortfolioContext, PortfolioStrategy

STATUS_AGREE = "agree"
STATUS_TREND_MISMATCH = "trend_mismatch"
STATUS_SECONDARY_UNAVAILABLE = "secondary_unavailable"

PRIMARY_SOURCE = "stream_iex_1m"
SECONDARY_SOURCE = "alpaca_sip_daily"


def cross_check_symbols(strategy: PortfolioStrategy) -> tuple[str, ...]:
    """Symbols whose session close feeds the decision, or () when unsupported.

    Only strategies that declare a ``signal_symbol`` (trend input) take part;
    ``hold_symbol`` is added when present because its close feeds the
    volatility-targeted weight.
    """
    signal = getattr(strategy, "signal_symbol", None)
    if not isinstance(signal, str) or not signal:
        return ()
    symbols = [canonical_symbol(signal, Market.EQUITY)]
    hold = getattr(strategy, "hold_symbol", None)
    if isinstance(hold, str) and hold:
        held = canonical_symbol(hold, Market.EQUITY)
        if held not in symbols:
            symbols.append(held)
    return tuple(symbols)


@dataclass(frozen=True)
class SourceDecision:
    """The strategy's decision on one source's session-close bars."""

    trend_state: str
    signal_close: float | None
    signal_sma: float | None
    weight: float
    closes: dict[str, float]

    def to_dict(self) -> dict[str, Any]:
        return {
            "trend_state": self.trend_state,
            "signal_close": self.signal_close,
            "signal_sma": self.signal_sma,
            "weight": self.weight,
            "closes": dict(self.closes),
        }


@dataclass(frozen=True)
class CrossCheckOutcome:
    status: str
    session_date: date
    primary: SourceDecision | None = None
    secondary: SourceDecision | None = None
    close_gap_bps: dict[str, float] = field(default_factory=dict)
    close_divergence_symbols: tuple[str, ...] = ()
    close_divergence_bps: float = 0.0
    reason: str | None = None
    attempts: int = 0

    @property
    def hold(self) -> bool:
        """The runtime keeps the current position this session."""
        return self.status == STATUS_TREND_MISMATCH

    @property
    def close_divergence(self) -> bool:
        return bool(self.close_divergence_symbols)

    def to_metadata(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "session_date": self.session_date.isoformat(),
            "primary_source": PRIMARY_SOURCE,
            "secondary_source": SECONDARY_SOURCE,
            "primary": self.primary.to_dict() if self.primary is not None else None,
            "secondary": self.secondary.to_dict() if self.secondary is not None else None,
            "close_gap_bps": dict(self.close_gap_bps),
            "close_divergence": self.close_divergence,
            "close_divergence_symbols": list(self.close_divergence_symbols),
            "close_divergence_threshold_bps": self.close_divergence_bps,
            "hold": self.hold,
            "reason": self.reason,
            "attempts": self.attempts,
        }

    def summary(self) -> str:
        """One operator sentence (English, log/alert text)."""
        session = self.session_date.isoformat()
        if self.status == STATUS_SECONDARY_UNAVAILABLE:
            return (
                f"signal cross-check {session}: second source unavailable ({self.reason}); "
                "decided on the primary IEX stream bar only"
            )
        primary = self.primary
        secondary = self.secondary
        gaps = ", ".join(f"{symbol} {gap:+.1f} bps" for symbol, gap in sorted(self.close_gap_bps.items()))
        if self.status == STATUS_TREND_MISMATCH and primary is not None and secondary is not None:
            return (
                f"signal cross-check {session}: sources disagree on the trend "
                f"(IEX stream {primary.trend_state}, SIP {secondary.trend_state}; close gaps {gaps}); "
                "holding the current position this session"
            )
        return f"signal cross-check {session}: sources agree; close gaps {gaps} exceed the divergence threshold"


def _session_tail_replaced(series: Sequence[Bar], bar: Bar) -> tuple[Bar, ...]:
    """``series`` with ``bar`` as the tail for its New York session.

    Mirrors ``PortfolioRuntime._append_signal_bar``: one session is one
    signal bar, and a same-session tail is replaced, never duplicated.
    """
    kept = list(series)
    session = signal_session_date(bar.timestamp)
    while kept and signal_session_date(kept[-1].timestamp) >= session:
        kept.pop()
    kept.append(bar)
    return tuple(kept)


def _source_decision(
    strategy: PortfolioStrategy,
    history: Mapping[str, Sequence[Bar]],
    session_bars: Mapping[str, Bar],
    decision_timestamp: datetime,
) -> SourceDecision:
    histories: dict[str, tuple[Bar, ...]] = {}
    for symbol, series in history.items():
        bar = session_bars.get(symbol)
        histories[symbol] = _session_tail_replaced(series, bar) if bar is not None else tuple(series)
    context = PortfolioContext(
        decision_timestamp=decision_timestamp,
        market=Market.EQUITY,
        timeframe=strategy.timeframe,
        history=histories,
        universe=None,
        universe_version="signal_cross_check",
        equity=1.0,
        holdings={},
        prices={symbol: series[-1].close for symbol, series in histories.items() if series},
        benchmark_symbol="SPY",
    )
    target = strategy.decide(context)
    metadata = target.metadata or {}

    def _number(value: object) -> float | None:
        return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None

    return SourceDecision(
        trend_state=str(metadata.get("trend_state", "unknown")),
        signal_close=_number(metadata.get("signal_close")),
        signal_sma=_number(metadata.get("signal_sma")),
        weight=float(sum(weight.weight for weight in target.weights)),
        closes={symbol: bar.close for symbol, bar in sorted(session_bars.items())},
    )


def compare_sources(
    strategy: PortfolioStrategy,
    history: Mapping[str, Sequence[Bar]],
    primary_bars: Mapping[str, Bar],
    secondary_bars: Mapping[str, Bar],
    decision_timestamp: datetime,
    *,
    close_divergence_bps: float,
    attempts: int = 0,
) -> CrossCheckOutcome:
    """Decide on both sources and classify their agreement.

    ``history`` is the stored signal history (prior sessions); each source's
    bars replace the decision session's tail for their symbols, so the two
    decisions differ only in the session-close bar.
    """
    session = signal_session_date(decision_timestamp)
    symbols = cross_check_symbols(strategy)
    primary = {symbol: primary_bars[symbol] for symbol in symbols if symbol in primary_bars}
    secondary = {symbol: secondary_bars[symbol] for symbol in symbols if symbol in secondary_bars}
    missing = [symbol for symbol in symbols if symbol not in secondary]
    if not symbols or missing:
        return CrossCheckOutcome(
            STATUS_SECONDARY_UNAVAILABLE,
            session,
            reason="second source missing " + ",".join(missing or ["symbols"]),
            close_divergence_bps=close_divergence_bps,
            attempts=attempts,
        )
    primary_decision = _source_decision(strategy, history, primary, decision_timestamp)
    secondary_decision = _source_decision(strategy, history, secondary, decision_timestamp)
    gaps: dict[str, float] = {}
    for symbol in symbols:
        if symbol in primary and secondary[symbol].close > 0:
            gaps[symbol] = (primary[symbol].close / secondary[symbol].close - 1.0) * 10_000.0
    divergent = tuple(sorted(symbol for symbol, gap in gaps.items() if abs(gap) > close_divergence_bps))
    status = STATUS_TREND_MISMATCH if primary_decision.trend_state != secondary_decision.trend_state else STATUS_AGREE
    return CrossCheckOutcome(
        status,
        session,
        primary=primary_decision,
        secondary=secondary_decision,
        close_gap_bps={symbol: round(gap, 3) for symbol, gap in gaps.items()},
        close_divergence_symbols=divergent,
        close_divergence_bps=close_divergence_bps,
        attempts=attempts,
    )


def secondary_unavailable(
    session: date, reason: str, *, close_divergence_bps: float, attempts: int
) -> CrossCheckOutcome:
    return CrossCheckOutcome(
        STATUS_SECONDARY_UNAVAILABLE,
        session,
        reason=reason,
        close_divergence_bps=close_divergence_bps,
        attempts=attempts,
    )


# ``fetch(symbols, start, end)`` -> {symbol: newest daily bar in [start, end]}.
SecondaryFetch = Callable[[list[str], datetime, datetime], Mapping[str, Bar]]


def nyse_close_utc(session: date) -> datetime:
    """The session's NYSE close (early closes included); 16:00 ET fallback."""
    close = session_close_utc(session)
    if close is not None:
        return close
    return datetime.combine(session, time(16, 0), tzinfo=NEW_YORK)


@dataclass(frozen=True)
class CrossCheckStep:
    """What the runtime does with a decision batch right now."""

    action: str  # "proceed" or "defer"
    outcome: CrossCheckOutcome | None = None
    retry_at: datetime | None = None


@dataclass
class _PendingCheck:
    deadline: datetime
    close: datetime
    next_attempt: datetime
    attempts: int = 0
    last_error: str | None = None


class SignalCrossCheckCoordinator:
    """Bounded-retry timing for the second-source fetch (one per session).

    The first allowed attempt is at ``max(first seen, close + embargo)``;
    failed attempts are retried every ``retry_interval`` until
    ``retry_window`` after that first allowed attempt, then the decision
    proceeds on the primary source. Pure given the injected clock and fetch.
    """

    def __init__(
        self,
        *,
        enabled: bool = True,
        sip_embargo: timedelta = timedelta(minutes=15),
        retry_window: timedelta = timedelta(minutes=10),
        retry_interval: timedelta = timedelta(seconds=60),
        close_divergence_bps: float = 50.0,
        session_close: Callable[[date], datetime] = nyse_close_utc,
    ) -> None:
        self.enabled = enabled
        self.sip_embargo = sip_embargo
        self.retry_window = retry_window
        self.retry_interval = retry_interval
        self.close_divergence_bps = close_divergence_bps
        self._session_close = session_close
        self._pending: dict[date, _PendingCheck] = {}
        self.last_outcome: CrossCheckOutcome | None = None

    def pending_status(self) -> dict[str, Any]:
        # Health readers run on other threads: iterate an atomic copy.
        pending = dict(self._pending)
        return {
            session.isoformat(): {
                "attempts": state.attempts,
                "next_attempt": state.next_attempt.isoformat(),
                "deadline": state.deadline.isoformat(),
                "last_error": state.last_error,
            }
            for session, state in sorted(pending.items())
        }

    def step(
        self,
        strategy: PortfolioStrategy,
        history: Mapping[str, Sequence[Bar]],
        bars: Sequence[Bar],
        fetch: SecondaryFetch | None,
        now: datetime,
    ) -> CrossCheckStep:
        symbols = cross_check_symbols(strategy)
        daily = {
            canonical_symbol(bar.symbol, Market.EQUITY): bar
            for bar in bars
            if bar.timeframe == "1d" and bar.market == Market.EQUITY
        }
        if not self.enabled or fetch is None or not symbols or any(symbol not in daily for symbol in symbols):
            # Disabled, no second source, a strategy without a trend input, or
            # an incomplete batch (the decision path reports that itself).
            return CrossCheckStep("proceed")
        primary = {symbol: daily[symbol] for symbol in symbols}
        decision_timestamp = max(bar.timestamp for bar in primary.values())
        session = signal_session_date(decision_timestamp)
        for stale in [day for day in self._pending if day < session]:
            del self._pending[stale]
        state = self._pending.get(session)
        if state is None:
            close = self._session_close(session)
            earliest = max(now, close + self.sip_embargo)
            state = _PendingCheck(earliest + self.retry_window, close, earliest)
            self._pending[session] = state
        if now < state.next_attempt:
            return CrossCheckStep("defer", retry_at=state.next_attempt)
        state.attempts += 1
        secondary: dict[str, Bar] = {}
        error: str | None
        try:
            start = datetime.combine(session, time(0, 0), tzinfo=NEW_YORK)
            fetched = fetch(list(symbols), start, max(state.close, start + timedelta(minutes=1)))
            secondary = {
                canonical_symbol(symbol, Market.EQUITY): bar
                for symbol, bar in fetched.items()
                if signal_session_date(bar.timestamp) == session
            }
            missing = [symbol for symbol in symbols if symbol not in secondary]
            error = "no SIP daily bar for " + ",".join(missing) if missing else None
        except Exception as exc:  # provider errors are retried, never raised
            error = f"{type(exc).__name__}: {exc}"[:300]
        if error is not None and now < state.deadline:
            state.last_error = error
            state.next_attempt = min(now + self.retry_interval, state.deadline)
            return CrossCheckStep("defer", retry_at=state.next_attempt)
        del self._pending[session]
        if error is not None:
            outcome = secondary_unavailable(
                session, error, close_divergence_bps=self.close_divergence_bps, attempts=state.attempts
            )
        else:
            outcome = compare_sources(
                strategy,
                history,
                primary,
                secondary,
                decision_timestamp,
                close_divergence_bps=self.close_divergence_bps,
                attempts=state.attempts,
            )
        self.last_outcome = outcome
        return CrossCheckStep("proceed", outcome)


__all__ = [
    "PRIMARY_SOURCE",
    "SECONDARY_SOURCE",
    "STATUS_AGREE",
    "STATUS_SECONDARY_UNAVAILABLE",
    "STATUS_TREND_MISMATCH",
    "CrossCheckOutcome",
    "CrossCheckStep",
    "SignalCrossCheckCoordinator",
    "SourceDecision",
    "compare_sources",
    "cross_check_symbols",
    "nyse_close_utc",
    "secondary_unavailable",
]
