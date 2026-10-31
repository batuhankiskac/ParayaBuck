"""Audit wave 2 fixes X2: market data, provider cool-down, stream stop, trade-feed clock.

Covers T2-8-4 (cache rebased on a basis change), T2-7-4 (provider cool-down),
R2-4-6 (a stop racing a run start), C2-5-3 (stream join budget) and C2-5-2
(per-order silence clock). Offline only: fake provider clients and a real
WarmupCache under ``tmp_path``. No network, no credentials, no production paths.
"""

from __future__ import annotations

import asyncio
import threading
import time
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock

import pandas as pd
import pytest

from parayabuck import runtime as runtime_module
from parayabuck.core.models import Bar, Market
from parayabuck.data.alpaca import AlpacaHistoricalData, HistoricalProviderCooldown
from parayabuck.data.alpaca_stream import AlpacaBarStream
from parayabuck.data.cache import WarmupCache
from parayabuck.execution.alpaca_updates import AlpacaTradingUpdateStream, TradeUpdate
from parayabuck.runtime import EngineRuntime

_WINDOW_START = datetime(2026, 6, 1, tzinfo=UTC)
_WINDOW_END = datetime(2026, 6, 12, tzinfo=UTC)
# Tuesday 2026-10-27 11:00 ET: inside the NYSE regular session.
_SESSION_MOMENT = datetime(2026, 10, 27, 15, 0, tzinfo=UTC)


def _session_day(day: date, close: float) -> Bar:
    """A daily bar labelled at 00:00 ET (04:00 UTC), the provider's convention."""
    stamp = datetime(day.year, day.month, day.day, 4, 0, tzinfo=UTC)
    return Bar(stamp, "AAPL", "1d", close, close + 1.0, close - 1.0, close, 1000.0, Market.EQUITY)


def _provider_frame(closes: dict[date, float]) -> pd.DataFrame:
    rows = [
        {
            "timestamp": datetime(day.year, day.month, day.day, 4, 0, tzinfo=UTC),
            "symbol": "AAPL",
            "open": close,
            "high": close + 1.0,
            "low": close - 1.0,
            "close": close,
            "volume": 1000.0,
        }
        for day, close in sorted(closes.items())
    ]
    return pd.DataFrame(rows)


class _FakeStockClient:
    """Records each provider call; answers with the next frame or raises it."""

    def __init__(self, *answers: Any) -> None:
        self._answers = list(answers)
        self.calls = 0

    def get_stock_bars(self, _request: Any) -> Any:
        self.calls += 1
        answer = self._answers.pop(0) if len(self._answers) > 1 else self._answers[0]
        if isinstance(answer, BaseException):
            raise answer
        return SimpleNamespace(df=answer)


def _history(tmp_path: Any, client: _FakeStockClient) -> AlpacaHistoricalData:
    history = AlpacaHistoricalData("test-key", "test-secret", stock_feed="iex", cache=str(tmp_path / "warmup.sqlite3"))
    history._stock_client = client
    return history


def _cached_closes(cache: WarmupCache, adjustment: str = "splits") -> dict[datetime, float]:
    bars = cache.get(
        "AAPL", "1d", market=Market.EQUITY, feed="iex", start=_WINDOW_START, end=_WINDOW_END, adjustment=adjustment
    )
    return {bar.timestamp: bar.close for bar in bars}


# -- T2-8-4: a rebased provider answer drops the cache on the old basis --------


def test_basis_change_drops_the_cached_rows_of_that_symbol_only(tmp_path: Any) -> None:
    cache = WarmupCache(str(tmp_path / "warmup.sqlite3"))
    # Pre-split closes cached on the splits basis, plus an unrelated symbol.
    cache.upsert([_session_day(date(2026, 6, day), 100.0) for day in (1, 2, 3, 4, 5)], feed="iex", adjustment="splits")
    other = Bar(datetime(2026, 6, 1, 4, 0, tzinfo=UTC), "MSFT", "1d", 10.0, 11.0, 9.0, 10.0, 1.0, Market.EQUITY)
    cache.upsert([other], feed="iex", adjustment="splits")
    # The provider rebased 1-5 June to post-split closes and has a hole on 3 June.
    client = _FakeStockClient(_provider_frame({date(2026, 6, 1): 50.0, date(2026, 6, 2): 50.0, date(2026, 6, 4): 50.0}))
    history = _history(tmp_path, client)
    history._cache = cache

    history.get_bars_batch(["AAPL"], "1d", start=_WINDOW_START, end=_WINDOW_END, adjustment="splits")

    closes = _cached_closes(cache)
    assert closes, "the provider answer is cached"
    assert set(closes.values()) == {50.0}, "no pre-split close survives beside the new basis"
    assert datetime(2026, 6, 5, 4, tzinfo=UTC) not in closes, "the 3 June hole is not refilled from the old basis"
    msft = cache.get(
        "MSFT", "1d", market=Market.EQUITY, feed="iex", start=_WINDOW_START, end=_WINDOW_END, adjustment="splits"
    )
    assert [bar.close for bar in msft] == [10.0], "other symbols keep their cache"


def test_matching_provider_answer_keeps_the_cache(tmp_path: Any) -> None:
    cache = WarmupCache(str(tmp_path / "warmup.sqlite3"))
    cache.upsert([_session_day(date(2026, 6, day), 100.0) for day in (1, 2)], feed="iex", adjustment="splits")
    client = _FakeStockClient(
        _provider_frame({date(2026, 6, 1): 100.0, date(2026, 6, 2): 100.0, date(2026, 6, 3): 101.0})
    )
    history = _history(tmp_path, client)
    history._cache = cache

    history.get_bars_batch(["AAPL"], "1d", start=_WINDOW_START, end=_WINDOW_END, adjustment="splits")

    assert _cached_closes(cache) == {
        datetime(2026, 6, 1, 4, tzinfo=UTC): 100.0,
        datetime(2026, 6, 2, 4, tzinfo=UTC): 100.0,
        datetime(2026, 6, 3, 4, tzinfo=UTC): 101.0,
    }


def test_basis_check_compares_only_overlapping_instants() -> None:
    cached = [_session_day(date(2026, 6, 1), 100.0)]
    assert AlpacaHistoricalData._basis_changed(cached, [_session_day(date(2026, 6, 1), 50.0)]) is True
    assert AlpacaHistoricalData._basis_changed(cached, [_session_day(date(2026, 6, 1), 100.0 + 1e-9)]) is False
    assert AlpacaHistoricalData._basis_changed(cached, [_session_day(date(2026, 6, 2), 50.0)]) is False
    assert AlpacaHistoricalData._basis_changed([], [_session_day(date(2026, 6, 1), 50.0)]) is False


def test_invalidate_symbol_is_scoped_to_feed_adjustment_and_timeframe(tmp_path: Any) -> None:
    cache = WarmupCache(str(tmp_path / "warmup.sqlite3"))
    day = _session_day(date(2026, 6, 1), 100.0)
    cache.upsert([day], feed="iex", adjustment="splits")
    cache.upsert([day], feed="iex", adjustment="all")
    cache.upsert([day], feed="sip", adjustment="splits")

    removed = cache.invalidate_symbol("AAPL", feed="iex", timeframe="1d", adjustment="splits")

    assert removed == 1
    assert _cached_closes(cache, adjustment="splits") == {}
    assert _cached_closes(cache, adjustment="all") == {day.timestamp: 100.0}
    assert (
        len(
            cache.get(
                "AAPL",
                "1d",
                market=Market.EQUITY,
                feed="sip",
                start=_WINDOW_START,
                end=_WINDOW_END,
                adjustment="splits",
            )
        )
        == 1
    )


# -- T2-7-4: a provider failure opens a bounded cool-down ----------------------


def test_failure_opens_a_cooldown_and_the_provider_is_not_asked_again(tmp_path: Any) -> None:
    client = _FakeStockClient(ConnectionError("provider down"))
    history = _history(tmp_path, client)

    with pytest.raises(ConnectionError, match="provider down"):
        history.get_bars_batch(["AAPL"], "1d", start=_WINDOW_START, end=_WINDOW_END, adjustment="splits")
    with pytest.raises(HistoricalProviderCooldown):
        history.get_bars_batch(["AAPL"], "1d", start=_WINDOW_START, end=_WINDOW_END, adjustment="splits")

    assert client.calls == 1, "no second provider request inside the cool-down"
    status = history.feed_status()
    assert status["provider_failure_streak"] == 1
    assert status["provider_cooldown_until"] is not None


def test_a_permanent_error_does_not_open_the_cooldown(tmp_path: Any) -> None:
    client = _FakeStockClient(RuntimeError("bad request"), _provider_frame({date(2026, 6, 1): 100.0}))
    history = _history(tmp_path, client)

    with pytest.raises(RuntimeError, match="bad request"):
        history.get_bars_batch(["AAPL"], "1d", start=_WINDOW_START, end=_WINDOW_END, adjustment="splits")
    history.get_bars_batch(["AAPL"], "1d", start=_WINDOW_START, end=_WINDOW_END, adjustment="splits")

    assert client.calls == 2, "a non-network error is not an outage and does not start a cool-down"
    assert history._provider_cooldown_until is None


def test_cooldown_delay_doubles_per_failure_and_is_capped(tmp_path: Any) -> None:
    history = _history(tmp_path, _FakeStockClient(RuntimeError("x")))
    now = datetime(2026, 10, 27, 15, 0, tzinfo=UTC)
    delays = []
    for _ in range(7):
        history._note_provider_failure(now)
        delays.append(int((history._provider_cooldown_until - now).total_seconds()))

    assert delays == [30, 60, 120, 240, 300, 300, 300]
    assert history._provider_failure_streak == 7


def test_provider_answer_after_the_cooldown_resets_the_streak(tmp_path: Any) -> None:
    client = _FakeStockClient(ConnectionError("provider down"), _provider_frame({date(2026, 6, 1): 100.0}))
    history = _history(tmp_path, client)
    with pytest.raises(ConnectionError):
        history.get_bars_batch(["AAPL"], "1d", start=_WINDOW_START, end=_WINDOW_END, adjustment="splits")
    # The cool-down has elapsed: the next request reaches the provider again.
    history._provider_cooldown_until = datetime.now(UTC) - timedelta(seconds=1)

    history.get_bars_batch(["AAPL"], "1d", start=_WINDOW_START, end=_WINDOW_END, adjustment="splits")

    assert client.calls == 2
    assert history._provider_failure_streak == 0
    assert history._provider_cooldown_until is None


def test_cooldown_serves_the_cache_when_it_covers_the_window(tmp_path: Any) -> None:
    cache = WarmupCache(str(tmp_path / "warmup.sqlite3"))
    sessions = [date(2026, 6, day) for day in range(1, 12) if date(2026, 6, day).weekday() < 5]
    cache.upsert([_session_day(day, 100.0) for day in sessions], feed="iex", adjustment="splits")
    client = _FakeStockClient(ConnectionError("provider down"))
    history = _history(tmp_path, client)
    history._cache = cache
    history._provider_cooldown_until = datetime.now(UTC) + timedelta(minutes=1)

    served = history.get_bars_batch(["AAPL"], "1d", start=_WINDOW_START, end=_WINDOW_END, adjustment="splits")

    assert client.calls == 0
    assert served["AAPL"], "the feed-matched cache answers during the cool-down"


# -- R2-4-6 / C2-5-3: a stop that races a run start is not lost ---------------


def _race_stop_against_sdk_start(stream: Any, sdk: Mock, run: Any) -> float:
    """Call ``run`` so that the SDK loop appears only after a stop() has begun.

    The fake SDK run() starts a helper thread that calls stop(), waits on an
    event until that helper has entered stop(), and only then sets the loop on
    this thread. The race is forced by the event, not by a sleep. Returns how long
    the helper's stop() took.
    """
    stopped_after: list[float] = []
    stop_entered = threading.Event()

    def stop_worker() -> None:
        started = time.monotonic()
        stop_entered.set()
        stream.stop()
        stopped_after.append(time.monotonic() - started)

    def fake_sdk_run(*_args: Any) -> None:
        helper = threading.Thread(target=stop_worker)
        helper.start()
        assert stop_entered.wait(5), "the helper never entered stop()"
        sdk._loop = object()  # alpaca-py creates its event loop on entry to run()
        helper.join(timeout=5)
        assert not helper.is_alive(), "stop() did not return once the loop appeared"

    sdk.run.side_effect = fake_sdk_run
    run()
    return stopped_after[0]


def test_trade_stream_stop_racing_the_sdk_start_reaches_the_sdk_loop() -> None:
    stream = AlpacaTradingUpdateStream.__new__(AlpacaTradingUpdateStream)
    sdk = Mock()
    sdk._loop = None
    stream._stream = sdk

    _race_stop_against_sdk_start(stream, sdk, stream.run)

    sdk.stop.assert_called_once_with()


def test_bar_stream_stop_racing_the_sdk_start_reaches_the_sdk_loop() -> None:
    stream = AlpacaBarStream.__new__(AlpacaBarStream)
    sdk = Mock()
    sdk._loop = None
    stream.stock = sdk

    _race_stop_against_sdk_start(stream, sdk, stream.run_stock)

    sdk.stop.assert_called_once_with()


def test_stop_of_a_never_started_stream_does_not_wait() -> None:
    stream = AlpacaTradingUpdateStream.__new__(AlpacaTradingUpdateStream)
    sdk = Mock()
    sdk._loop = None
    stream._stream = sdk

    # Completion, not a wall-clock bound: stop() must return on its own thread.
    helper = threading.Thread(target=stream.stop, daemon=True)
    helper.start()
    helper.join(timeout=10)

    assert not helper.is_alive(), "stop() of a never-started stream did not return"
    sdk.stop.assert_not_called()


# -- C2-5-3: the stream join budget covers the SDK poll and the 2 s close ------


def test_stream_stop_join_covers_the_sdk_poll_and_the_close_timeout() -> None:
    assert runtime_module._STREAM_STOP_JOIN_SECONDS >= 5 + 2
    assert {"pb-stocks", "pb-trade-updates"} == set(runtime_module._STREAM_THREAD_NAMES)


# -- C2-5-2: the open-order silence clock is per order -------------------------


def _stream_runtime_with_orders(orders: tuple[Any, ...]) -> tuple[EngineRuntime, list[str]]:
    runtime = EngineRuntime.__new__(EngineRuntime)
    runtime._running = True
    runtime._trade_feed_probe_at = None
    runtime._order_update_at = {}
    runtime.broker = SimpleNamespace(account_id="acct")
    runtime._portfolio_repository = SimpleNamespace(open_orders=lambda _account_id: orders)
    latches: list[str] = []
    runtime._set_reconcile_pending = lambda reason: latches.append(reason)  # type: ignore[method-assign]
    return runtime, latches


def test_a_fill_on_one_order_does_not_hide_a_silent_open_order() -> None:
    orders = (SimpleNamespace(client_order_id="filled"), SimpleNamespace(client_order_id="silent"))
    runtime, latches = _stream_runtime_with_orders(orders)
    runtime._order_update_at = {
        "silent": _SESSION_MOMENT - timedelta(seconds=301),
        "filled": _SESSION_MOMENT - timedelta(seconds=5),
    }

    runtime._maybe_check_trade_feed_freshness(now=_SESSION_MOMENT)

    assert latches == ["trade_feed_stale"]


def test_recent_updates_on_every_open_order_do_not_latch() -> None:
    orders = (SimpleNamespace(client_order_id="a"), SimpleNamespace(client_order_id="b"))
    runtime, latches = _stream_runtime_with_orders(orders)
    runtime._order_update_at = {
        "a": _SESSION_MOMENT - timedelta(seconds=120),
        "b": _SESSION_MOMENT - timedelta(seconds=10),
    }

    runtime._maybe_check_trade_feed_freshness(now=_SESSION_MOMENT)

    assert latches == []


def test_a_closed_order_drops_its_clock_and_a_new_order_starts_one() -> None:
    runtime, latches = _stream_runtime_with_orders((SimpleNamespace(client_order_id="new"),))
    runtime._order_update_at = {"closed": _SESSION_MOMENT - timedelta(hours=2)}

    runtime._maybe_check_trade_feed_freshness(now=_SESSION_MOMENT)

    assert "closed" not in runtime._order_update_at
    assert runtime._order_update_at == {"new": _SESSION_MOMENT}
    assert latches == []


def test_trade_update_stamps_its_own_order() -> None:
    runtime = EngineRuntime.__new__(EngineRuntime)
    runtime._order_update_at = {}
    runtime._portfolio_repository = None
    runtime._schedule_reconcile = lambda: None  # type: ignore[method-assign]
    update = TradeUpdate(
        event="fill",
        client_order_id="c-9",
        broker_order_id="b-9",
        symbol="QLD",
        market=Market.EQUITY,
        filled_qty=1.0,
        average_fill_price=10.0,
        timestamp=datetime.now(UTC),
    )

    asyncio.run(runtime._handle_trade_update(update))

    assert "c-9" in runtime._order_update_at
    assert runtime._last_trade_update_at is not None
