"""Audit wave 3 regressions for split continuity, provider cool-down and cache retention (W02).

Synthetic fixtures only: pinned timestamps, a frozen clock for the provider
cool-down, fake stock clients, and tmp_path caches. No network, credentials or
production paths.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest

from parayabuck.core.models import Bar, Market
from parayabuck.data import alpaca as alpaca_module
from parayabuck.data.adjustment import split_continuity_breach
from parayabuck.data.alpaca import AlpacaHistoricalData, HistoricalProviderCooldown
from parayabuck.runtime import EngineRuntime, _signal_verify_retry_delay

D0 = datetime(2026, 10, 19, 4, 0, tzinfo=UTC)
D1 = datetime(2026, 10, 20, 4, 0, tzinfo=UTC)
D2 = datetime(2026, 10, 21, 4, 0, tzinfo=UTC)
D3 = datetime(2026, 10, 22, 4, 0, tzinfo=UTC)

FROZEN = datetime(2026, 10, 27, 15, 0, tzinfo=UTC)


def _bar(symbol: str, stamp: datetime, close: float = 100.0) -> Bar:
    return Bar(stamp, symbol, "1d", close, close, close, close, 1000.0, Market.EQUITY)


# --- T3-1-1 / T3-1-2 / T3-1-3: split continuity over the whole provider window --


def test_t3_1_1_unrebased_split_is_caught_when_the_stored_tail_is_post_split() -> None:
    # Split on D2 (2-for-1). The stream already stored D2 on the post-split
    # basis, so the split is no longer the newest provider bar: the D1 -> D2
    # jump sits inside the unrebased provider window and must still halt.
    stored = [_bar("AAA", D0, 100.0), _bar("AAA", D1, 100.0), _bar("AAA", D2, 50.0)]
    provider = [*stored, _bar("AAA", D3, 50.5)]
    assert split_continuity_breach(provider, stored) is True


def test_t3_1_2_a_stream_built_prior_does_not_hide_an_unrebased_split() -> None:
    # One stored prior differs from the provider by aggregation noise. That
    # is not a rebase, so the unrebased 2-for-1 on D3 must still be caught.
    stored = [_bar("AAA", D0, 100.0), _bar("AAA", D1, 100.2), _bar("AAA", D2, 100.0)]
    provider = [_bar("AAA", D0, 100.0), _bar("AAA", D1, 100.0), _bar("AAA", D2, 100.0), _bar("AAA", D3, 50.0)]
    assert split_continuity_breach(provider, stored) is True


def test_t3_1_2_a_rebased_window_stays_consistent_with_a_stream_built_prior() -> None:
    # The provider rebased its priors (ratio 2): a consistent basis, even
    # though one stored prior carries aggregation noise.
    stored = [_bar("AAA", D0, 100.0), _bar("AAA", D1, 100.2), _bar("AAA", D2, 100.0)]
    rebased = [_bar("AAA", D0, 50.0), _bar("AAA", D1, 50.0), _bar("AAA", D2, 50.0), _bar("AAA", D3, 50.0)]
    assert split_continuity_breach(rebased, stored) is False


def test_t3_1_3_a_non_standard_three_for_two_ratio_halts_for_an_operator() -> None:
    stored = [_bar("AAA", D0, 100.0), _bar("AAA", D1, 100.0), _bar("AAA", D2, 100.0)]
    provider = [*stored, _bar("AAA", D3, 66.7)]  # 3-for-2 reads 1.5
    assert split_continuity_breach(provider, stored) is True


def test_t3_1_3_two_for_one_with_a_large_split_day_rally_halts() -> None:
    # A 2-for-1 with a 30% split-day rally reads 1.54, outside the standard band.
    stored = [_bar("AAA", D0, 100.0), _bar("AAA", D1, 100.0), _bar("AAA", D2, 100.0)]
    provider = [*stored, _bar("AAA", D3, 65.0)]
    assert split_continuity_breach(provider, stored) is True


def test_t3_1_3_an_ordinary_twenty_percent_move_passes() -> None:
    stored = [_bar("AAA", D0, 100.0), _bar("AAA", D1, 100.0), _bar("AAA", D2, 100.0)]
    provider = [*stored, _bar("AAA", D3, 80.0)]  # reads 1.25: an ordinary move
    assert split_continuity_breach(provider, stored) is False


def test_a_large_drop_inside_a_rebased_window_is_not_a_breach() -> None:
    # Priors rebased by the 2-for-1 (consistent basis); the drop on D3 is genuine.
    stored = [_bar("AAA", D0, 100.0), _bar("AAA", D1, 100.0), _bar("AAA", D2, 100.0)]
    rebased = [_bar("AAA", D0, 50.0), _bar("AAA", D1, 50.0), _bar("AAA", D2, 50.0), _bar("AAA", D3, 25.0)]
    assert split_continuity_breach(rebased, stored) is False


def test_split_continuity_needs_a_shared_session() -> None:
    stored = [_bar("AAA", D0, 100.0)]
    provider = [_bar("AAA", D2, 100.0), _bar("AAA", D3, 50.0)]
    assert split_continuity_breach(provider, stored) is False


# --- shared fixtures for the provider client ---------------------------------


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """A frozen clock for the alpaca module: ``now`` is read from this mapping."""
    state = {"now": FROZEN}

    class _ClockDatetime(datetime):
        @classmethod
        def now(cls, tz: Any = None) -> datetime:
            current: datetime = state["now"]
            return current if tz is None else current.astimezone(tz)

    monkeypatch.setattr(alpaca_module, "datetime", _ClockDatetime)
    # The patched class is what the module's isinstance checks test against,
    # so window bounds passed to the client must be instances of it.
    state["window_start"] = _ClockDatetime(2026, 6, 1, tzinfo=UTC)
    state["window_end"] = _ClockDatetime(2026, 6, 12, tzinfo=UTC)
    return state


def _history(tmp_path: Any, client: Any) -> AlpacaHistoricalData:
    history = AlpacaHistoricalData("test-key", "test-secret", stock_feed="iex", cache=str(tmp_path / "warmup.sqlite3"))
    history._stock_client = client
    return history


class _StockClient:
    """Answers each call from a script: an exception is raised, a frame is returned."""

    def __init__(self, *answers: Any) -> None:
        self._answers = list(answers)
        self.calls = 0

    def get_stock_bars(self, _request: Any) -> Any:
        self.calls += 1
        answer = self._answers.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        return SimpleNamespace(df=answer)


# --- T3-1-4: the cool-down clock starts when the failure is recorded ----------


def test_t3_1_4_cooldown_is_counted_from_when_the_failure_is_recorded(tmp_path: Any, clock: dict[str, Any]) -> None:
    history = _history(tmp_path, None)

    class _TimeoutClient:
        def get_stock_bars(self, _request: Any) -> Any:
            # The request spends its 30 s timeout before it fails.
            clock["now"] = FROZEN + timedelta(seconds=30)
            raise ConnectionError("timed out")

    history._stock_client = _TimeoutClient()
    with pytest.raises(ConnectionError):
        history.get_bars_batch(
            ["AAPL"], "1d", start=clock["window_start"], end=clock["window_end"], adjustment="splits"
        )

    assert history._provider_cooldown_until == FROZEN + timedelta(seconds=60)


# --- T3-1-5: the session-close cross-check honours the same cool-down ---------


def test_t3_1_5_cross_check_is_refused_inside_the_cooldown_without_a_provider_call(
    tmp_path: Any, clock: dict[str, Any]
) -> None:
    client = _StockClient(SimpleNamespace(empty=True))
    history = _history(tmp_path, client)
    history._provider_cooldown_until = FROZEN + timedelta(minutes=1)

    with pytest.raises(HistoricalProviderCooldown):
        history.get_session_daily_bars(["AAPL"], clock["window_start"], clock["window_end"])

    assert client.calls == 0


def test_t3_1_5_cross_check_network_failure_opens_the_shared_cooldown(tmp_path: Any, clock: dict[str, Any]) -> None:
    client = _StockClient(ConnectionError("provider down"))
    history = _history(tmp_path, client)

    with pytest.raises(ConnectionError):
        history.get_session_daily_bars(["AAPL"], clock["window_start"], clock["window_end"])
    with pytest.raises(HistoricalProviderCooldown):
        history.get_session_daily_bars(["AAPL"], clock["window_start"], clock["window_end"])

    assert client.calls == 1
    assert history._provider_failure_streak == 1


def test_t3_1_5_cross_check_answer_ends_the_outage_streak(tmp_path: Any, clock: dict[str, Any]) -> None:
    client = _StockClient(SimpleNamespace(empty=True))
    history = _history(tmp_path, client)
    history._provider_failure_streak = 3
    history._provider_cooldown_until = FROZEN - timedelta(seconds=1)

    assert history.get_session_daily_bars(["AAPL"], clock["window_start"], clock["window_end"]) == {}

    assert client.calls == 1
    assert history._provider_failure_streak == 0
    assert history._provider_cooldown_until is None


# --- T3-2-1: the verifier's in-call retry waits out a short cool-down ---------


def test_t3_2_1_retry_waits_for_a_short_cooldown_to_end() -> None:
    cooldown = FROZEN + timedelta(seconds=30)
    client = SimpleNamespace(_provider_cooldown_until=cooldown)
    delay = _signal_verify_retry_delay(client, 0, FROZEN)
    assert delay == pytest.approx(30.05)


def test_t3_2_1_retry_does_not_wait_longer_than_the_bound() -> None:
    client = SimpleNamespace(_provider_cooldown_until=FROZEN + timedelta(minutes=2))
    assert _signal_verify_retry_delay(client, 0, FROZEN) == pytest.approx(0.5)


def test_t3_2_1_retry_uses_the_short_backoff_without_a_cooldown() -> None:
    assert _signal_verify_retry_delay(SimpleNamespace(), 0, FROZEN) == pytest.approx(0.5)
    assert _signal_verify_retry_delay(SimpleNamespace(), 1, FROZEN) == pytest.approx(1.0)
    expired = SimpleNamespace(_provider_cooldown_until=FROZEN - timedelta(seconds=1))
    assert _signal_verify_retry_delay(expired, 0, FROZEN) == pytest.approx(0.5)


# --- T3-2-3: the cool-down field reports only a cool-down still in force -------


def test_t3_2_3_feed_status_reports_only_an_active_cooldown(tmp_path: Any, clock: dict[str, Any]) -> None:
    history = _history(tmp_path, None)
    history._provider_cooldown_until = FROZEN + timedelta(minutes=5)
    assert history.feed_status()["provider_cooldown_until"] == (FROZEN + timedelta(minutes=5)).isoformat()

    history._provider_cooldown_until = FROZEN - timedelta(seconds=1)
    assert history.feed_status()["provider_cooldown_until"] is None


# --- D3-3-9: the cool-down raises an alert on entry and on recovery -----------


@pytest.fixture
def provider_alerts(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, dict[str, object]]]:
    events: list[tuple[str, dict[str, object]]] = []
    monkeypatch.setattr(
        "parayabuck.runtime.alerts",
        type("A", (), {"emit": staticmethod(lambda kind, **kw: events.append((kind, kw)))}),
    )
    return events


def test_d3_3_9_cooldown_alerts_once_on_entry_and_once_on_recovery(provider_alerts: list[Any]) -> None:
    engine = EngineRuntime.__new__(EngineRuntime)
    snapshots: list[dict[str, object]] = [
        {"available": True, "provider_cooldown_until": None},
        {"available": True, "provider_cooldown_until": FROZEN.isoformat(), "provider_failure_streak": 1},
        {"available": True, "provider_cooldown_until": FROZEN.isoformat(), "provider_failure_streak": 2},
        {"available": True, "provider_cooldown_until": None, "provider_failure_streak": 0},
    ]
    engine.market_data_status = lambda: snapshots.pop(0)  # type: ignore[method-assign]

    for _ in range(4):
        engine._check_provider_cooldown()

    assert [(kind, kw["state"]) for kind, kw in provider_alerts] == [
        ("data_provider_unavailable", "cooldown"),
        ("data_provider_unavailable", "recovered"),
    ]


def test_d3_3_9_a_healthy_start_raises_no_cooldown_alert(provider_alerts: list[Any]) -> None:
    engine = EngineRuntime.__new__(EngineRuntime)
    engine.market_data_status = lambda: {"available": True, "provider_cooldown_until": None}  # type: ignore[method-assign]

    engine._check_provider_cooldown()

    assert provider_alerts == []


# --- C3-5-5: the warmup cache is pruned by the live warmup's retention ---------


def test_c3_5_5_prune_cache_drops_bars_past_the_retention_window(tmp_path: Any) -> None:
    history = _history(tmp_path, None)
    old = Bar(datetime(2020, 1, 2, tzinfo=UTC), "AAPL", "1d", 100.0, 100.0, 100.0, 100.0, 1000.0, Market.EQUITY)
    recent = Bar(
        datetime(2026, 6, 1, 4, 0, tzinfo=UTC), "AAPL", "1d", 101.0, 101.0, 101.0, 101.0, 1000.0, Market.EQUITY
    )
    history._cache.upsert([old, recent], feed="iex", adjustment="splits")

    assert history.prune_cache(FROZEN) == 1
    assert history.prune_cache(FROZEN) == 0
    kept = history._cache.get(
        "AAPL",
        "1d",
        market=Market.EQUITY,
        feed="iex",
        start=datetime(2020, 1, 1, tzinfo=UTC),
        end=datetime(2026, 12, 31, tzinfo=UTC),
        adjustment="splits",
    )
    assert [bar.timestamp for bar in kept] == [recent.timestamp]
