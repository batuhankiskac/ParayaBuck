"""Stream → decision chain, one live equity minute driven end to end.

Every link below runs as production code:

    ``_on_minute_bar`` → real ``TimeframeAggregator`` daily finalization →
    ``_emit_finalized_bar`` → ``_put_event(bar)`` → the real
    ``_decision_loop`` thread (batch key ``(bar.timeframe, bar.timestamp)``,
    expected symbols from ``_portfolio_runtimes``) → the real
    ``_signal_cross_check_step`` deferring the batch → the real
    ``_run_due_deferred_portfolio_batches`` re-running it once ``retry_at`` is
    due.

Only the external provider boundary is stubbed (``_historical``: the SIP
daily bar never arrives), so the batch stays deferred and never reaches the
order path. Deleting the deferred re-run from the decision loop, or queueing
the portfolio event under a timeframe label the portfolio runtimes do not
declare, breaks this test and nothing else in the suite.
"""

from __future__ import annotations

import asyncio
import sys
import threading
from datetime import UTC, date, datetime, time, timedelta
from types import SimpleNamespace
from typing import Any

import pytest

from parayabuck.config import Config, SignalCrossCheckConfig
from parayabuck.core.models import Bar, Market
from parayabuck.data.aggregator import TimeframeAggregator
from parayabuck.markets.nyse_calendar import sessions_around
from parayabuck.markets.timeframes import NEW_YORK
from parayabuck.portfolio.equity_leveraged_trend import EquityLeveragedTrendV1
from parayabuck.runtime import _STREAM_WORKER_BY_MARKET, EngineRuntime
from parayabuck.runtime_events import _Event, _EventQueue
from parayabuck.runtime_health import WorkerHealth

_SYMBOLS = ("QQQ", "QLD")
# Three minutes of the session open. The daily bar is their aggregate, and its
# high comes from the middle minute, so a bar that was passed through instead
# of aggregated cannot pass unnoticed.
_MINUTE_CLOSES = (440.0, 442.5, 439.0)
_MINUTE_VOLUME = 1_000.0
_DAILY_VOLUME = len(_MINUTE_CLOSES) * _MINUTE_VOLUME
# SIP retry interval. Short enough that the loop's next pass (bounded by its
# 0.5 s queue timeout) finds the deferred batch due almost immediately.
_RETRY_INTERVAL_SECONDS = 0.25
_CHAIN_TIMEOUT_SECONDS = 15.0


def _finished_sessions(now: datetime) -> tuple[tuple[date, datetime, datetime], ...]:
    """NYSE sessions whose daily bar is already final at ``now``.

    ``_flush_aggregators`` finalizes a daily bucket a full day after its open
    (``bar.timestamp + 1d + grace <= now``), so the loop under test only
    emits it for a session that is at least a day old. The newest such
    session is used, whatever the wall clock says.
    """
    return tuple(entry for entry in sessions_around(now) if entry[2] + timedelta(days=1) <= now)


def _session_open(session: date) -> datetime:
    """The session's opening minute in UTC (the daily bar's label)."""
    return datetime.combine(session, time(9, 30), tzinfo=NEW_YORK).astimezone(UTC)


def _minute(symbol: str, stamp: datetime, close: float) -> Bar:
    return Bar(stamp, symbol, "1m", close, close, close, close, _MINUTE_VOLUME, Market.EQUITY)


def _daily(symbol: str, open_utc: datetime, high: float = max(_MINUTE_CLOSES)) -> Bar:
    """The daily bar the aggregator must build out of the three minutes."""
    return Bar(
        open_utc,
        symbol,
        "1d",
        _MINUTE_CLOSES[0],
        high,
        min(_MINUTE_CLOSES),
        _MINUTE_CLOSES[-1],
        _DAILY_VOLUME,
        Market.EQUITY,
    )


def _history(sessions: tuple[date, ...]) -> dict[str, tuple[Bar, ...]]:
    """A few closed daily bars per symbol, as the warmup would have left them."""
    return {
        symbol: tuple(
            Bar(
                _session_open(day),
                symbol,
                "1d",
                400.0,
                405.0,
                395.0,
                400.0 + day.toordinal() % 5,
                1_000_000.0,
                Market.EQUITY,
            )
            for day in sessions
        )
        for symbol in _SYMBOLS
    }


def _caller_chain(depth: int = 12) -> tuple[str, ...]:
    """Names of the frames above the caller: which loop path called in."""
    names: list[str] = []
    frame = sys._getframe(1)
    while frame is not None and len(names) < depth:
        names.append(frame.f_code.co_name)
        frame = frame.f_back
    return tuple(names)


class _SipUnavailable:
    """The provider boundary: every SIP daily-bar fetch fails.

    Records the call and the loop path it came from, so a re-run driven by
    ``_run_due_deferred_portfolio_batches`` is distinguishable from the first
    pass over a freshly closed batch.
    """

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.attempted = threading.Event()
        self.rerun = threading.Event()

    def get_session_daily_bars(  # type: ignore[no-untyped-def]
        self, symbols, start, end, *, feed="sip", adjustment="splits"
    ):
        chain = _caller_chain()
        self.calls.append(
            {
                "symbols": list(symbols),
                "start": start,
                "end": end,
                "feed": feed,
                "adjustment": adjustment,
                "chain": chain,
            }
        )
        self.attempted.set()
        if "_run_due_deferred_portfolio_batches" in chain:
            self.rerun.set()
        raise ConnectionError("SIP daily-bar provider unavailable")


class _EquityPortfolioRuntime:
    """The equity portfolio runtime, with the attributes the decision path reads."""

    def __init__(self, history: dict[str, tuple[Bar, ...]]) -> None:
        self.market = Market.EQUITY
        self.strategy = EquityLeveragedTrendV1({"signal_symbol": "QQQ", "hold_symbol": "QLD"})
        self.history = history
        self.symbols = frozenset(_SYMBOLS)
        self.decided: list[list[Bar]] = []

    def process_closed_bars(self, bars: list[Bar], **_kwargs: Any) -> Any:
        """The order path — a deferred batch must never reach it in this test."""
        self.decided.append(list(bars))
        return SimpleNamespace(status="not_scheduled", orders=(), submitted_order_ids=(), decision_id=None, target=None)


def _engine(portfolio_runtime: Any, historical: Any) -> EngineRuntime:
    """A runtime carrying only the attributes the driven chain touches."""
    now = datetime.now(UTC)
    app_config = Config(
        signal_cross_check=SignalCrossCheckConfig(
            # The session closed a day ago, so the SIP embargo is long over; a
            # short retry interval keeps the deferred re-run under a second.
            sip_embargo_minutes=0.0,
            retry_interval_seconds=_RETRY_INTERVAL_SECONDS,
        ),
    )
    engine = EngineRuntime.__new__(EngineRuntime)
    engine.app_config = app_config
    engine.config = app_config.engine
    engine._workers = {
        "pb-decisions": WorkerHealth(now, now),
        _STREAM_WORKER_BY_MARKET[Market.EQUITY]: WorkerHealth(now, now),
    }
    engine._events = _EventQueue(app_config.engine.event_queue_capacity)
    engine._ingestion_lock = threading.Lock()
    engine._portfolio_lock = threading.RLock()
    engine._state_lock = threading.RLock()
    engine._event_stats_lock = threading.Lock()
    engine.aggregators = {"1d": TimeframeAggregator("1d", app_config.engine.data_finalization_grace_seconds)}
    engine._strategy_timeframes = {"1d"}
    engine._historical = historical
    engine._portfolio_runtimes = (portfolio_runtime,)
    engine._deferred_portfolio_batches = {}
    engine._last_portfolio_status = {}
    engine._coalesced_events = 0
    engine._dropped_events = 0
    engine._oldest_received_at = None
    engine._circuit_breaker_reason = None
    engine._warming_up = False
    engine._state = "running"
    engine._running = True
    # The readiness gate is the boundary between the queue and the order path:
    # an open gate lets the batch reach the cross-check, a closed one stops it
    # before the decision. Nothing here may block it.
    engine._entry_readiness_reasons = lambda _market: ()
    return engine


def _events(engine: EngineRuntime) -> list[_Event]:
    return [item for item in engine._events.drain_for_test() if item is not None]


def test_minute_stream_defers_the_equity_batch_and_the_loop_reruns_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The whole live chain, from one stream minute to the deferred re-run."""
    alerts: list[tuple[str, dict[str, object]]] = []
    monkeypatch.setattr(
        "parayabuck.runtime.alerts", SimpleNamespace(emit=lambda kind, **fields: alerts.append((kind, fields)))
    )
    sessions = _finished_sessions(datetime.now(UTC))
    assert len(sessions) >= 4, "the NYSE calendar window must hold closed sessions"
    session, open_utc, _close = sessions[-1]
    sip = _SipUnavailable()
    portfolio_runtime = _EquityPortfolioRuntime(_history([entry[0] for entry in sessions[-4:-1]]))
    engine = _engine(portfolio_runtime, sip)
    # The driven session is a day old; keep its decision window open so the
    # chain's deferred re-run is a live retry, not an expiry (the configured
    # window must stay below the watchdog grace, so patch the instance).
    engine._decision_retry_settings = lambda: (timedelta(seconds=_RETRY_INTERVAL_SECONDS), timedelta(days=30))  # type: ignore[method-assign]

    thread = threading.Thread(target=engine._decision_loop, name="test-decision-loop", daemon=True)
    thread.start()
    try:
        for offset, close in enumerate(_MINUTE_CLOSES):
            for symbol in _SYMBOLS:
                asyncio.run(engine._on_minute_bar(_minute(symbol, open_utc + timedelta(minutes=offset), close)))
        assert sip.attempted.wait(_CHAIN_TIMEOUT_SECONDS), (
            "the closed daily bar never reached the signal cross-check "
            f"(circuit breaker: {engine._circuit_breaker_reason})"
        )
        assert sip.rerun.wait(_CHAIN_TIMEOUT_SECONDS), (
            "the deferred batch was never re-run by the decision loop "
            f"(deferred: {sorted(map(str, engine._deferred_portfolio_batches))})"
        )
    finally:
        engine._running = False
        thread.join(timeout=_CHAIN_TIMEOUT_SECONDS)

    assert not thread.is_alive(), "the decision loop did not stop"
    assert engine._circuit_breaker_reason is None
    assert len(sip.calls) >= 2, "the re-run must be a second, later attempt"
    first, rerun_call = sip.calls[0], sip.calls[1]
    # The first pass closes a freshly aggregated batch; the second is the
    # deferred re-run the loop performs once retry_at is due.
    assert "_run_due_deferred_portfolio_batches" not in first["chain"]
    assert "_run_due_deferred_portfolio_batches" in rerun_call["chain"]
    assert first["symbols"] == rerun_call["symbols"] == list(_SYMBOLS)
    # The same session's SIP window on both passes: the re-run re-decides the
    # very bars the first pass deferred, from the NYSE midnight of that day.
    session_start = datetime.combine(session, time(0, 0), tzinfo=NEW_YORK)
    assert first["start"] == rerun_call["start"] == session_start
    assert (first["feed"], first["adjustment"]) == ("sip", "splits")

    # Still waiting for the SIP bar: the batch is deferred under the decision
    # timestamp, holding exactly the aggregated daily bars of both symbols.
    deferred = engine._deferred_portfolio_batches
    assert list(deferred) == [(Market.EQUITY, open_utc)]
    deferred_bars, retry_at = deferred[(Market.EQUITY, open_utc)]
    assert retry_at is not None
    assert {bar.symbol: bar for bar in deferred_bars} == {symbol: _daily(symbol, open_utc) for symbol in _SYMBOLS}

    # The SIP bar never arrived, so nothing was decided and nothing alerted.
    assert portfolio_runtime.decided == []
    assert engine._last_portfolio_status == {}
    assert alerts == []
    status = engine.signal_cross_check_status()
    assert status["last"] is None
    assert list(status["pending"]) == [session.isoformat()]
    assert status["pending"][session.isoformat()]["attempts"] == len(sip.calls)


def test_a_corrected_minute_never_queues_a_second_decision_bar() -> None:
    """A correction of an already-emitted daily bar queues nothing.

    ``_on_updated_minute_bar`` must never queue a second event for a session
    the decision loop already closed: a re-decision would place a second
    order for the same session.
    """
    sessions = _finished_sessions(datetime.now(UTC))
    assert sessions, "the NYSE calendar window must hold a closed session"
    open_utc = sessions[-1][1]
    engine = _engine(_EquityPortfolioRuntime({}), None)

    for offset, close in enumerate(_MINUTE_CLOSES):
        for symbol in _SYMBOLS:
            asyncio.run(engine._on_minute_bar(_minute(symbol, open_utc + timedelta(minutes=offset), close)))
    engine._flush_aggregators()
    closed = [item.bar for item in _events(engine)]
    assert [bar.symbol for bar in closed] == sorted(_SYMBOLS)
    assert set(closed) == {_daily(symbol, open_utc) for symbol in _SYMBOLS}

    asyncio.run(engine._on_updated_minute_bar(_minute("QQQ", open_utc + timedelta(minutes=1), 445.0)))
    assert [item.bar for item in _events(engine)] == closed


def test_a_corrected_minute_revises_the_still_open_daily_bar() -> None:
    """A correction that lands before the session-close flush is part of the
    daily decision bar the loop receives."""
    sessions = _finished_sessions(datetime.now(UTC))
    assert sessions, "the NYSE calendar window must hold a closed session"
    open_utc = sessions[-1][1]
    engine = _engine(_EquityPortfolioRuntime({}), None)

    for offset, close in enumerate(_MINUTE_CLOSES):
        for symbol in _SYMBOLS:
            asyncio.run(engine._on_minute_bar(_minute(symbol, open_utc + timedelta(minutes=offset), close)))
    asyncio.run(engine._on_updated_minute_bar(_minute("QQQ", open_utc + timedelta(minutes=1), 445.0)))
    assert _events(engine) == []
    engine._flush_aggregators()

    by_symbol = {item.bar.symbol: item.bar for item in _events(engine)}
    assert by_symbol["QQQ"] == _daily("QQQ", open_utc, high=445.0)
