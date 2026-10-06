"""Market data, feeds and the NYSE calendar (audit wave 2b).

Deterministic and offline: pinned ET wall clocks, synthetic bars, a fake
provider client and in-memory stubs. No network, no credentials, no
production paths.

Covers:
* F06 a transport error whose message embeds ``feed=sip`` is NOT an
  entitlement error, the downgrade is bounded by a re-probe, and the
  effective feed is surfaced;
* F17 session minutes and the session-close decision follow the exchange
  calendar (holiday, early close, DST), not a fixed 09:30-16:00 window;
* F18 stream health owes nothing on a holiday or after an early close;
* F19 a session-close bar missing its last minute is withheld and replaced
  by the provider's SIP daily bar, with an alert;
* F35 a provider daily bar is not final before close + SIP embargo;
* F40 a cache-served provider response is not a verification;
* F37 an unexpected FILL side is recorded and alerted, never fatal.
"""

from __future__ import annotations

import threading
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest

import parayabuck.runtime as runtime_module
from parayabuck.config import SignalCrossCheckConfig
from parayabuck.core.models import Bar, Market
from parayabuck.data.adjustment import merge_provider_authority
from parayabuck.data.aggregator import TimeframeAggregator
from parayabuck.markets import nyse_calendar
from parayabuck.markets.timeframes import (
    NEW_YORK,
    equity_day_bar_is_closed,
    is_session_open,
    provider_day_bar_is_final,
    session_close_utc,
)
from parayabuck.monitoring import health as health_module
from parayabuck.monitoring.intent_watchdog import (
    IntentWatchdogSettings,
    LatestDecision,
    evaluate_equity,
)
from parayabuck.portfolio.runtime_service import _is_partial_provider_bar, _is_unfinished_signal_bar
from parayabuck.runtime import EngineRuntime

# --- calendar fixtures ------------------------------------------------------ #
# Thanksgiving 2026 (no session), the Friday after it (13:00 ET close), a
# full session before the DST end and the first session after it.
THANKSGIVING = date(2026, 11, 26)
EARLY_CLOSE = date(2026, 11, 27)
FULL_EDT = date(2026, 10, 30)
FULL_EST = date(2026, 11, 2)
REGULAR = date(2026, 9, 25)


def _et(day: date, hour: int, minute: int = 0, second: int = 0) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, second, tzinfo=NEW_YORK).astimezone(UTC)


def _minute_bar(symbol: str, stamp: datetime, close: float) -> Bar:
    return Bar(stamp, symbol, "1m", close, close, close, close, 100, Market.EQUITY)


def _daily_bar(symbol: str, stamp: datetime, close: float) -> Bar:
    return Bar(stamp, symbol, "1d", close, close, close, close, 1_000_000, Market.EQUITY)


class _Clock(datetime):
    """A frozen UTC clock for the runtime module (pins ``datetime.now``)."""

    now_utc: datetime = datetime(2020, 1, 1, tzinfo=UTC)

    @classmethod
    def now(cls, tz: Any = None) -> datetime:  # type: ignore[override]
        return cls.now_utc.astimezone(tz) if tz is not None else cls.now_utc


def _capture_events(monkeypatch: pytest.MonkeyPatch, module: Any) -> list[tuple[str, dict[str, object]]]:
    events: list[tuple[str, dict[str, object]]] = []
    monkeypatch.setattr(module.alerts, "emit", lambda kind, **fields: events.append((kind, fields)))
    return events


# --------------------------------------------------------------------------- #
# F17 / F18 — the NYSE calendar is the single source of session truth
# --------------------------------------------------------------------------- #


def test_holiday_has_no_session_and_owes_no_bars() -> None:
    assert nyse_calendar.session_bounds(THANKSGIVING) is None
    assert session_close_utc(THANKSGIVING) is None
    for hour in (10, 12, 15):
        assert not is_session_open(_et(THANKSGIVING, hour))
    assert not is_session_open(_et(FULL_EDT, 16, 30))  # after a normal close


def test_early_close_session_ends_at_1300_et() -> None:
    assert session_close_utc(EARLY_CLOSE) == _et(EARLY_CLOSE, 13, 0)
    assert is_session_open(_et(EARLY_CLOSE, 12, 59))
    # The IEX post-market prints between 13:00 and 15:59 are OUT of session:
    # they used to become the decision close on every early-close day.
    for hour, minute in ((13, 0), (13, 30), (14, 59), (15, 59)):
        assert not is_session_open(_et(EARLY_CLOSE, hour, minute))


@pytest.mark.parametrize("day", [FULL_EDT, FULL_EST])
def test_full_sessions_survive_the_dst_boundaries(day: date) -> None:
    assert is_session_open(_et(day, 9, 30))
    assert is_session_open(_et(day, 15, 59))
    assert not is_session_open(_et(day, 9, 29))
    assert not is_session_open(_et(day, 16, 0))
    assert session_close_utc(day) == _et(day, 16, 0)


def test_equity_day_bar_closes_at_the_actual_session_close() -> None:
    label = _et(EARLY_CLOSE, 0, 0)  # provider daily labels are midnight ET
    assert not equity_day_bar_is_closed(label, _et(EARLY_CLOSE, 12, 59))
    assert equity_day_bar_is_closed(label, _et(EARLY_CLOSE, 13, 0))
    # A regular session is unchanged: 16:00 ET, not 13:00.
    regular = _et(REGULAR, 0, 0)
    assert not equity_day_bar_is_closed(regular, _et(REGULAR, 15, 59))
    assert equity_day_bar_is_closed(regular, _et(REGULAR, 16, 0))
    # A past session is always closed.
    assert equity_day_bar_is_closed(regular, _et(FULL_EST, 12, 0))


def test_stream_health_owes_nothing_outside_the_calendar_session() -> None:
    worker = SimpleNamespace(alive=True, last_event_at=None)
    engine = EngineRuntime.__new__(EngineRuntime)
    engine.config = SimpleNamespace(account_poll_interval_seconds=15.0)
    # No bar ever delivered: on a holiday and after an early close the
    # runtime is ready anyway, mid-session it is not.
    assert engine._stream_worker_delivering(Market.EQUITY, worker, _et(THANKSGIVING, 12, 0)) is True
    assert engine._stream_worker_delivering(Market.EQUITY, worker, _et(EARLY_CLOSE, 14, 0)) is True
    assert engine._stream_worker_delivering(Market.EQUITY, worker, _et(FULL_EST, 12, 0)) is False
    # A dead worker is unhealthy whatever the calendar says.
    worker.alive = False
    assert engine._stream_worker_delivering(Market.EQUITY, worker, _et(THANKSGIVING, 12, 0)) is False


def test_out_of_process_health_agrees_with_the_runtime_calendar() -> None:
    info = {"alive": True, "last_event_at": None}
    markets = {"equity": {"enabled": True, "symbol_count": 1}}
    assert health_module._worker_stale("pb-stocks", info, _et(THANKSGIVING, 12, 0), 45.0, markets) is False
    assert health_module._worker_stale("pb-stocks", info, _et(EARLY_CLOSE, 14, 0), 45.0, markets) is False
    assert health_module._worker_stale("pb-stocks", info, _et(FULL_EST, 12, 0), 45.0, markets) is True


def test_intent_watchdog_expects_the_early_close_decision() -> None:
    """The watchdog deadline is the session's own close: a 13:00 ET close is
    expected at 13:00 ET, a 16:00 ET close stays at 16:00 ET."""
    now = _et(EARLY_CLOSE, 13, 50)
    settings = IntentWatchdogSettings(equity_decision_grace=timedelta(minutes=45))
    missing = evaluate_equity(
        now,
        None,
        None,
        running_since=_et(EARLY_CLOSE, 9, 0),
        settings=settings,
    )
    assert [alarm.code for alarm in missing] == ["equity_decision_missing"]
    decided = evaluate_equity(
        now,
        LatestDecision(_et(EARLY_CLOSE, 13, 1), {}),
        None,
        running_since=_et(EARLY_CLOSE, 9, 0),
        settings=settings,
    )
    assert decided == []


# --------------------------------------------------------------------------- #
# F17 / F19 — the session-close flush follows the calendar
# --------------------------------------------------------------------------- #


def _flush_engine(monkeypatch: pytest.MonkeyPatch, clock: datetime) -> EngineRuntime:
    _Clock.now_utc = clock
    monkeypatch.setattr(runtime_module, "datetime", _Clock)
    engine = EngineRuntime.__new__(EngineRuntime)
    engine.config = SimpleNamespace(data_finalization_grace_seconds=2.0)
    engine._ingestion_lock = threading.RLock()
    engine.aggregators = {"1d": TimeframeAggregator("1d", 2.0)}
    engine._historical = None
    engine.app_config = SimpleNamespace(signal_cross_check=SignalCrossCheckConfig(sip_embargo_minutes=15.0))
    engine.emitted = []  # type: ignore[attr-defined]
    engine._emit_finalized_bar = lambda tf, bar: engine.emitted.append((tf, bar))  # type: ignore[method-assign]
    return engine


def _feed_session_minutes(engine: EngineRuntime, day: date, *, minutes: int) -> None:
    """Feed ``minutes`` one-minute bars starting at the 09:30 ET open."""
    for offset in range(minutes):
        engine.aggregators["1d"].update(_minute_bar("SPY", _et(day, 9, 30) + timedelta(minutes=offset), 100.0 + offset))


def test_session_close_flush_waits_for_the_early_close(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = _flush_engine(monkeypatch, _et(EARLY_CLOSE, 12, 0))
    _feed_session_minutes(engine, EARLY_CLOSE, minutes=210)  # 09:30 .. 12:59
    engine._flush_aggregators()
    assert engine.emitted == []  # type: ignore[attr-defined]  # still trading

    _Clock.now_utc = _et(EARLY_CLOSE, 13, 0, 45)
    engine._flush_aggregators()
    assert [(tf, bar.close) for tf, bar in engine.emitted] == [("1d", 100.0 + 209)]  # type: ignore[attr-defined]
    # A post-close IEX print is not a session minute at all: it is neither
    # aggregated into the daily bar nor flushed at the session close.
    engine.aggregators["1d"].update(_minute_bar("SPY", _et(EARLY_CLOSE, 13, 30), 999.0))
    _Clock.now_utc = _et(EARLY_CLOSE, 13, 1)
    engine._flush_aggregators()
    assert len(engine.emitted) == 1  # type: ignore[attr-defined]


def test_no_session_close_flush_on_a_holiday(monkeypatch: pytest.MonkeyPatch) -> None:
    """Thanksgiving has no close, so nothing may be flushed at 16:30 as if it
    had one: the same-day bucket stays open for the real session's close."""
    engine = _flush_engine(monkeypatch, _et(THANKSGIVING, 16, 30))
    engine.aggregators["1d"].update(_minute_bar("SPY", _et(THANKSGIVING, 10, 0), 100.0))
    engine._flush_aggregators()
    assert engine.emitted == []  # type: ignore[attr-defined]
    assert engine.aggregators["1d"].incomplete_session_close == {}


def test_late_final_minute_lands_in_the_close_candles(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = _flush_engine(monkeypatch, _et(EARLY_CLOSE, 13, 0, 1))
    _feed_session_minutes(engine, EARLY_CLOSE, minutes=209)  # 09:30 .. 12:58
    engine._flush_aggregators()
    assert engine.emitted == []  # type: ignore[attr-defined]  # inside the close grace
    engine.aggregators["1d"].update(_minute_bar("SPY", _et(EARLY_CLOSE, 12, 59), 500.0))  # late final minute
    _Clock.now_utc = _et(EARLY_CLOSE, 13, 0) + timedelta(
        seconds=runtime_module._SESSION_CLOSE_FLUSH_GRACE_SECONDS + 0.1
    )
    engine._flush_aggregators()
    assert [(tf, bar.close) for tf, bar in engine.emitted] == [("1d", 500.0)]  # type: ignore[attr-defined]


# --------------------------------------------------------------------------- #
# F19 — a session-close bar missing its last minute is withheld
# --------------------------------------------------------------------------- #


def test_aggregator_withholds_an_incomplete_session_close_bucket() -> None:
    close = _et(REGULAR, 16, 0)
    aggregator = TimeframeAggregator("1d", 2.0)
    # Feed 09:30 .. 15:58 and leave 15:59 missing.
    for offset in range(389):
        aggregator.update(_minute_bar("SPY", _et(REGULAR, 9, 30) + timedelta(minutes=offset), 100.0 + offset))
    assert aggregator.flush_session_close(close, expected_close=close) == []
    assert aggregator.incomplete_session_close == {"SPY": _et(REGULAR, 15, 58)}
    # Reported once: a second flush of the same session is silent.
    assert aggregator.flush_session_close(close, expected_close=close) == []
    assert aggregator.incomplete_session_close == {}


def test_aggregator_emits_a_complete_session_close_bucket() -> None:
    close = _et(REGULAR, 16, 0)
    aggregator = TimeframeAggregator("1d", 2.0)
    for offset in range(390):  # 09:30 .. 15:59 inclusive
        aggregator.update(_minute_bar("SPY", _et(REGULAR, 9, 30) + timedelta(minutes=offset), 100.0 + offset))
    flushed = aggregator.flush_session_close(close, expected_close=close)
    assert len(flushed) == 1
    assert flushed[0].close == pytest.approx(100.0 + 389)
    assert aggregator.incomplete_session_close == {}


class _Historical:
    def __init__(self, bars: dict[str, Bar] | None = None, error: Exception | None = None) -> None:
        self.bars = bars or {}
        self.error = error
        self.calls: list[dict[str, object]] = []

    def get_session_daily_bars(self, symbols, start, end, *, feed, adjustment):  # noqa: ANN001
        self.calls.append({"symbols": list(symbols), "feed": feed, "adjustment": adjustment, "end": end})
        if self.error is not None:
            raise self.error
        return {symbol: bar for symbol, bar in self.bars.items() if symbol in symbols}


def test_incomplete_session_close_falls_back_to_the_sip_daily_bar(monkeypatch: pytest.MonkeyPatch) -> None:
    events = _capture_events(monkeypatch, runtime_module)
    close = _et(EARLY_CLOSE, 13, 0)
    engine = _flush_engine(monkeypatch, close)
    engine._historical = _Historical({"SPY": _daily_bar("SPY", _et(EARLY_CLOSE, 0, 0), 432.1)})

    _feed_session_minutes(engine, EARLY_CLOSE, minutes=209)  # 12:58: 12:59 never arrived
    _Clock.now_utc = close + timedelta(seconds=45)
    engine._flush_aggregators()

    assert engine.emitted == []  # type: ignore[attr-defined]  # withheld, not guessed
    assert [kind for kind, _fields in events] == ["session_close_bar_incomplete"]
    fallback = engine._session_close_fallback  # type: ignore[attr-defined]
    assert fallback.symbols == ("SPY",)
    assert fallback.retry_at == close + timedelta(minutes=15)

    # Before the SIP embargo nothing is emitted: the provider's bar for that
    # session is only final at close + embargo.
    engine._run_session_close_fallback(close + timedelta(minutes=5))
    assert engine.emitted == []  # type: ignore[attr-defined]

    engine._run_session_close_fallback(close + timedelta(minutes=15))
    assert [(tf, bar.close) for tf, bar in engine.emitted] == [("1d", 432.1)]  # type: ignore[attr-defined]
    assert engine._session_close_fallback is None  # type: ignore[attr-defined]
    assert engine._historical.calls == [  # type: ignore[attr-defined]
        {"symbols": ["SPY"], "feed": "sip", "adjustment": "splits", "end": close}
    ]


def test_session_close_fallback_gives_up_within_bounded_attempts(monkeypatch: pytest.MonkeyPatch) -> None:
    _capture_events(monkeypatch, runtime_module)
    close = _et(EARLY_CLOSE, 13, 0)
    engine = _flush_engine(monkeypatch, close)
    engine._historical = _Historical(error=ConnectionError("provider down"))
    engine._arm_session_close_fallback({"SPY": close - timedelta(minutes=2)}, close, close)
    now = close + timedelta(minutes=15)
    for _attempt in range(runtime_module._SESSION_CLOSE_FALLBACK_ATTEMPTS + 1):
        engine._run_session_close_fallback(now)
        now += timedelta(minutes=1)
    # Bounded: an unreachable provider never becomes an endless retry loop,
    # and the intent watchdog owns the missing-decision alarm.
    assert engine._session_close_fallback is None  # type: ignore[attr-defined]
    assert engine.emitted == []  # type: ignore[attr-defined]


def test_market_data_status_reports_the_fallback_and_the_effective_feed() -> None:
    engine = EngineRuntime.__new__(EngineRuntime)
    engine._historical = SimpleNamespace(
        feed_status=lambda: {"configured": "sip", "effective": "iex", "sip_fallback_active": True}
    )
    engine._session_close_fallback = SimpleNamespace(
        session=EARLY_CLOSE,
        symbols=("SPY",),
        attempts=1,
        retry_at=datetime(2026, 11, 27, 18, 15, tzinfo=UTC),
    )
    status = engine.market_data_status()
    assert status["available"] is True
    assert status["effective"] == "iex"
    assert status["session_close_fallback"]["session_date"] == "2026-11-27"
    # A runtime with no client reports unavailability instead of raising.
    assert EngineRuntime.__new__(EngineRuntime).market_data_status() == {"available": False}


# --------------------------------------------------------------------------- #
# F35 — a provider daily bar is not final before close + SIP embargo
# --------------------------------------------------------------------------- #


def test_provider_daily_bar_is_partial_inside_the_sip_embargo() -> None:
    label = _et(EARLY_CLOSE, 0, 0)
    assert not provider_day_bar_is_final(label, _et(EARLY_CLOSE, 13, 0))
    assert not provider_day_bar_is_final(label, _et(EARLY_CLOSE, 13, 10))
    assert provider_day_bar_is_final(label, _et(EARLY_CLOSE, 13, 15))
    # A zero embargo (a real-time SIP plan) makes the close itself the point.
    assert provider_day_bar_is_final(label, _et(EARLY_CLOSE, 13, 0), timedelta(0))


def test_the_embargo_gate_applies_to_provider_bars_only() -> None:
    """A deploy restart at 16:05 ET must not decide on the provider's 15
    minutes stale bar — but the stream's own complete bar is final at the
    real close and must stay eligible for the decision."""
    provider = _daily_bar("SPY", _et(REGULAR, 0, 0), 500.0)  # provider label
    stream = _daily_bar("SPY", _et(REGULAR, 9, 30), 500.0)  # stream label, same session
    at = _et(REGULAR, 16, 5)
    assert _is_partial_provider_bar(provider, at) is True
    assert _is_partial_provider_bar(provider, _et(REGULAR, 16, 20)) is False
    assert _is_unfinished_signal_bar(Market.EQUITY, stream, at) is False
    # Non-daily bars are untouched by both gates.
    assert _is_partial_provider_bar(_minute_bar("SPY", _et(REGULAR, 15, 0), 1.0), at) is False


def test_same_label_provider_revision_is_reported_as_a_change() -> None:
    label = _et(REGULAR, 0, 0)
    stored = [_daily_bar("SPY", label - timedelta(days=1), 499.0), _daily_bar("SPY", label, 500.0)]
    # The provider republishes today's bar once it stops being partial: the
    # same label, a new close. That is a change, not rounding noise.
    provider = [_daily_bar("SPY", label - timedelta(days=1), 499.0), _daily_bar("SPY", label, 500.6)]
    merged, changed = merge_provider_authority(tuple(stored), provider)
    assert changed
    assert merged[-1].close == pytest.approx(500.6)
    # An identical series is not a change.
    assert merge_provider_authority(tuple(stored), list(stored))[1] is False


# --------------------------------------------------------------------------- #
# F06 — only a genuine entitlement error downgrades the feed
# --------------------------------------------------------------------------- #


def _sip_history(get_stock_bars: Any) -> Any:
    from parayabuck.data.alpaca import AlpacaHistoricalData

    history = AlpacaHistoricalData.__new__(AlpacaHistoricalData)
    history._stock_feed = "sip"
    history._configured_stock_feed = "sip"
    history._sip_fallback_active = False
    history._sip_quote_fallback_active = False
    history._sip_fallback_since = None
    history._sip_embargo = timedelta(minutes=15)
    history._provider_fallbacks = 0
    history._last_batch_degraded = False
    history._stock_client = SimpleNamespace(get_stock_bars=get_stock_bars)
    history._cache = SimpleNamespace(get=lambda *a, **k: [], upsert=lambda *a, **k: None)
    return history


def _window() -> tuple[datetime, datetime]:
    end = datetime(2026, 8, 5, 20, 0, tzinfo=UTC)
    return end - timedelta(days=30), end


def _feed_name(request: Any) -> str:
    return str(request.feed).lower().split(".")[-1]


class _Forbidden(Exception):
    """The Alpaca APIError shape: a 403 with a JSON body."""

    status_code = 403
    response = SimpleNamespace(text='{"code": 40310000, "message": "insufficient privileges for SIP data"}')


# The exact shape a DNS failure produces: the request URL, feed=sip included.
_TRANSPORT_ERROR = ConnectionError(
    "HTTPSConnectionPool(host='data.alpaca.markets', port=443): Max retries exceeded with url: "
    "/v2/stocks/QQQ/bars?timeframe=1Day&start=2026-07-06&end=2026-08-05&adjustment=splits&feed=sip "
    "(Caused by NameResolutionError('<urlopen error [Errno -3] Temporary failure in name resolution>'))"
)


def test_transport_error_mentioning_sip_does_not_downgrade_the_feed() -> None:
    from parayabuck.data.alpaca import AlpacaHistoricalData

    assert AlpacaHistoricalData._is_entitlement_error(_TRANSPORT_ERROR) is False
    history = _sip_history(lambda request: (_ for _ in ()).throw(_TRANSPORT_ERROR))
    start, end = _window()
    with pytest.raises(ConnectionError):
        history.get_bars_batch(["QQQ"], "1d", start=start, end=end)
    assert history._sip_fallback_active is False
    assert history.feed_status()["effective"] == "sip"


def test_403_with_an_entitlement_body_downgrades_and_is_surfaced() -> None:
    from parayabuck.data.alpaca import AlpacaHistoricalData

    assert AlpacaHistoricalData._is_entitlement_error(_Forbidden()) is True
    history = _sip_history(lambda request: (_ for _ in ()).throw(_Forbidden()))
    start, end = _window()
    with pytest.raises(_Forbidden):
        history.get_bars_batch(["QQQ"], "1d", start=start, end=end)
    status = history.feed_status()
    assert history._sip_fallback_active is True
    assert status["effective"] == "iex" and status["sip_fallback_active"] is True
    assert status["sip_fallback_since"] is not None
    assert status["sip_reprobe_interval_minutes"] == 360


def test_recent_sip_embargo_is_not_an_entitlement_error() -> None:
    from parayabuck.data.alpaca import AlpacaHistoricalData

    class Embargoed(Exception):
        status_code = 403
        response = SimpleNamespace(text='{"message":"subscription does not permit querying recent SIP data"}')

    assert AlpacaHistoricalData._is_entitlement_error(Embargoed()) is False
    history = _sip_history(lambda request: (_ for _ in ()).throw(Embargoed()))
    start, end = _window()
    with pytest.raises(Embargoed):
        history.get_bars_batch(["QQQ"], "1d", start=start, end=end)
    assert history._sip_fallback_active is False


def test_the_downgrade_is_bounded_by_a_sip_reprobe() -> None:
    import pandas

    feeds: list[str] = []

    def _refuse(request: Any) -> Any:
        feeds.append(_feed_name(request))
        raise _Forbidden()

    history = _sip_history(_refuse)
    start, end = _window()
    with pytest.raises(_Forbidden):
        history.get_bars_batch(["QQQ"], "1d", start=start, end=end)
    assert history._sip_fallback_active is True
    assert feeds[-1] == "iex"

    # The entitlement is granted: the bounded re-probe restores SIP without a
    # restart, and the operator sees the recovery.
    history._stock_client.get_stock_bars = lambda request: (
        feeds.append(_feed_name(request)) or SimpleNamespace(df=pandas.DataFrame())
    )
    history._sip_fallback_since = datetime.now(UTC) - timedelta(hours=7)
    assert history.get_bars_batch(["QQQ"], "1d", start=start, end=end) == {"QQQ": []}
    assert history._sip_fallback_active is False
    assert history.feed_status()["effective"] == "sip"
    assert feeds[-1] == "sip"


def test_market_data_degradation_alerts_once_per_transition(monkeypatch: pytest.MonkeyPatch) -> None:
    events = _capture_events(monkeypatch, runtime_module)
    state = {"fallback": True}
    engine = EngineRuntime.__new__(EngineRuntime)
    engine._historical = SimpleNamespace(
        feed_status=lambda: {
            "configured": "sip",
            "effective": "iex" if state["fallback"] else "sip",
            "sip_fallback_active": state["fallback"],
            "note": "one exchange only",
        }
    )
    # A downgrade already in effect at the first observation alerts (E10).
    engine._check_market_data_feed()
    assert [(kind, fields["state"]) for kind, fields in events] == [("market_data_degraded", "degraded")]
    engine._check_market_data_feed()  # unchanged: silent
    assert len(events) == 1
    state["fallback"] = False
    engine._check_market_data_feed()
    assert [fields["state"] for _kind, fields in events] == ["degraded", "recovered"]


def test_healthy_first_market_data_observation_is_a_silent_baseline(monkeypatch: pytest.MonkeyPatch) -> None:
    events = _capture_events(monkeypatch, runtime_module)
    engine = EngineRuntime.__new__(EngineRuntime)
    engine._historical = SimpleNamespace(feed_status=lambda: {"configured": "sip", "effective": "sip"})

    engine._check_market_data_feed()

    assert events == []


# --------------------------------------------------------------------------- #
# F40 — a cache-served provider response is not a verification
# --------------------------------------------------------------------------- #


def test_cache_served_verification_is_not_a_success(monkeypatch: pytest.MonkeyPatch) -> None:
    events = _capture_events(monkeypatch, runtime_module)
    engine = EngineRuntime.__new__(EngineRuntime)
    equity = SimpleNamespace(
        market=Market.EQUITY,
        strategy=SimpleNamespace(minimum_history_bars=2, timeframe="1d"),
        history={"AAA": []},
        symbols=("AAA",),
        signal_verified_through_by_symbol={},
    )
    engine._symbols_by_market = {Market.EQUITY: {"AAA"}}
    engine._portfolio_runtimes = (equity,)
    # The outage path: the client answers from its local warmup cache and
    # says so, which is exactly what must NOT count as verified.
    engine._historical = SimpleNamespace(
        get_bars_batch=lambda *a, **k: {"AAA": [_daily_bar("AAA", _et(REGULAR, 0, 0), 100.0)]},
        _last_batch_degraded=True,
    )
    engine._signal_adjustment_state().update({"healthy": True, "last_success_at": None})
    assert engine.verify_equity_signal_basis() is False
    assert engine._signal_adjustment_state()["healthy"] is False
    assert [kind for kind, _fields in events] == ["signal_adjustment_failure"]
    assert equity.signal_verified_through_by_symbol == {}
