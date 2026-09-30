from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from parayabuck.web import server
from parayabuck.web.routers import dashboard
from parayabuck.web.services import performance
from tests.helpers import make_broker

T0 = datetime(2026, 9, 1, tzinfo=UTC)


class _FakeBroker:
    def __init__(self, points: list[tuple[datetime, float]] | None = None, error: Exception | None = None) -> None:
        self.points = points if points is not None else [(T0, 100.0), (T0 + timedelta(days=1), 110.0)]
        self.error = error
        self.calls: list[tuple[str, str]] = []

    def portfolio_history(self, period: str, timeframe: str) -> list[tuple[datetime, float]]:
        self.calls.append((period, timeframe))
        if self.error is not None:
            raise self.error
        return self.points


class _FakeManager:
    def __init__(self, broker: Any = None, paper: bool = True) -> None:
        self.broker = broker
        self.paper = paper
        self.snapshot_calls = 0

    def is_paper_mode(self) -> bool:
        return self.paper

    def fetch_account_snapshot(self) -> dict[str, Any]:
        self.snapshot_calls += 1
        return {}


@pytest.fixture(autouse=True)
def _clear_cache() -> Any:
    performance.reset_cache()
    yield
    performance.reset_cache()


def _bench(_manager: Any, start: datetime, end: datetime, minutes: int) -> list[tuple[datetime, float]]:
    return [(start, 500.0), (end, 505.0)]


def test_performance_returns_equity_and_benchmark_series() -> None:
    broker = _FakeBroker()
    payload = performance.portfolio_performance(_FakeManager(broker), "1m", benchmark_fetcher=_bench)

    assert broker.calls == [("1M", "1D")]
    assert payload["period"] == "1M"
    assert payload["available"] is True
    assert payload["benchmark_symbol"] == "VOO"
    assert payload["equity"] == [
        {"t": T0.isoformat(), "v": 100.0},
        {"t": (T0 + timedelta(days=1)).isoformat(), "v": 110.0},
    ]
    assert [point["v"] for point in payload["benchmark"]] == [500.0, 505.0]
    assert "benchmark_error" not in payload


def test_performance_rejects_unknown_period() -> None:
    with pytest.raises(ValueError, match="Unknown period"):
        performance.portfolio_performance(_FakeManager(_FakeBroker()), "10Y", benchmark_fetcher=_bench)


def test_performance_without_broker_is_unavailable_and_not_cached() -> None:
    manager = _FakeManager(broker=None)
    first = performance.portfolio_performance(manager, "1M", benchmark_fetcher=_bench)
    assert first["available"] is False
    assert first["reason"]
    assert manager.snapshot_calls == 1

    manager.broker = _FakeBroker()
    second = performance.portfolio_performance(manager, "1M", benchmark_fetcher=_bench)
    assert second["available"] is True


def test_performance_history_failure_degrades() -> None:
    manager = _FakeManager(_FakeBroker(error=RuntimeError("boom")))
    payload = performance.portfolio_performance(manager, "1W", benchmark_fetcher=_bench)
    assert payload["available"] is False
    assert "boom" in payload["reason"]


def test_performance_benchmark_failure_keeps_portfolio_series() -> None:
    def failing(*_args: Any) -> list[tuple[datetime, float]]:
        raise ValueError("no data")

    payload = performance.portfolio_performance(_FakeManager(_FakeBroker()), "3M", benchmark_fetcher=failing)
    assert payload["available"] is True
    assert len(payload["equity"]) == 2
    assert payload["benchmark"] == []
    assert payload["benchmark_error"] == "no data"


def test_performance_cache_is_per_period_and_mode_and_expires() -> None:
    broker = _FakeBroker()
    now = [1000.0]
    manager = _FakeManager(broker)

    def call(period: str) -> dict[str, Any]:
        return performance.portfolio_performance(manager, period, benchmark_fetcher=_bench, clock=lambda: now[0])

    call("1M")
    call("1M")
    assert len(broker.calls) == 1
    call("1A")
    assert len(broker.calls) == 2
    manager.paper = False
    call("1M")
    assert len(broker.calls) == 3
    now[0] += performance.CACHE_TTL_SECONDS + 1
    call("1M")
    assert len(broker.calls) == 4


def test_alpaca_portfolio_history_drops_unfunded_and_invalid_points(tmp_path: Path) -> None:
    broker = make_broker(tmp_path)
    requests: list[Any] = []
    stamps = [1_788_000_000 + i * 86_400 for i in range(5)]

    def get_portfolio_history(request: Any) -> Any:
        requests.append(request)
        return SimpleNamespace(timestamp=stamps, equity=[0.0, None, float("nan"), 100.5, 101.25])

    broker._client = SimpleNamespace(get_portfolio_history=get_portfolio_history)

    points = broker.portfolio_history("1M", "1D")

    assert requests[0].period == "1M"
    assert requests[0].timeframe == "1D"
    assert points == [
        (datetime.fromtimestamp(stamps[3], UTC), 100.5),
        (datetime.fromtimestamp(stamps[4], UTC), 101.25),
    ]


def test_cached_account_snapshot_keeps_the_flat_shape() -> None:
    manager = server.EngineManager()
    manager.last_account = {"equity": 1234.5, "cash": 10.0, "positions": [{"symbol": "QLD"}]}
    manager._last_account_monotonic = time.monotonic()

    snapshot = manager.fetch_account_snapshot()

    assert snapshot["equity"] == 1234.5
    assert snapshot["positions"] == [{"symbol": "QLD"}]
    assert snapshot["source"] == "cache"
    assert snapshot["stale"] is False
    assert "account" not in snapshot


def test_performance_route_rejects_unknown_period(monkeypatch: pytest.MonkeyPatch) -> None:
    from parayabuck.web import deps

    monkeypatch.setattr(deps, "get_engine_manager", lambda _request: _FakeManager(_FakeBroker()))
    request = Request({"type": "http", "method": "GET", "path": "/api/performance", "headers": [], "query_string": b""})

    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(dashboard.dashboard_performance(request, period="bogus"))
    assert excinfo.value.status_code == 422
