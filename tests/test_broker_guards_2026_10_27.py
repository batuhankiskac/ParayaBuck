"""Broker guards: empty-position fail-closed, broker clock, premarket probe."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from parayabuck.execution.alpaca_client import BrokerClock
from parayabuck.monitoring.premarket import premarket_probe
from tests.fake_client import FakeTradingClient
from tests.helpers import make_broker


class _ClockClient(FakeTradingClient):
    """FakeTradingClient plus a scripted ``get_clock`` (the base fake has none)."""

    def __init__(self) -> None:
        super().__init__()
        self.clock_payload: Any = None
        self.clock_calls = 0

    def get_clock(self) -> Any:
        self.clock_calls += 1
        if isinstance(self.clock_payload, Exception):
            raise self.clock_payload
        return self.clock_payload


def _broker(tmp_path: Path, client: FakeTradingClient, owned: dict[str, float]) -> Any:
    return make_broker(
        tmp_path,
        client=client,
        peak_state_path=tmp_path / "peak.json",
        portfolio_owned_quantities=lambda: owned,
        portfolio_open_orders=lambda: [],
    )


# --- Task 2: broker clock mapping ---


def test_broker_clock_maps_alpaca_fields_to_dataclass(tmp_path: Path) -> None:
    client = _ClockClient()
    now = datetime(2026, 10, 27, 15, 0, tzinfo=UTC)
    client.clock_payload = SimpleNamespace(
        timestamp=now,
        is_open=True,
        next_open=datetime(2026, 10, 28, 13, 30, tzinfo=UTC),
        next_close=datetime(2026, 10, 27, 20, 0, tzinfo=UTC),
    )
    broker = make_broker(tmp_path, client=client)

    clock = broker.clock()

    assert clock == BrokerClock(
        timestamp=now,
        is_open=True,
        next_open=datetime(2026, 10, 28, 13, 30, tzinfo=UTC),
        next_close=datetime(2026, 10, 27, 20, 0, tzinfo=UTC),
    )
    assert client.clock_calls == 1


def test_broker_clock_rejects_naive_timestamp(tmp_path: Path) -> None:
    client = _ClockClient()
    naive = datetime(2026, 10, 27, 15, 0)
    client.clock_payload = SimpleNamespace(
        timestamp=naive,
        is_open=True,
        next_open=datetime(2026, 10, 28, 13, 30, tzinfo=UTC),
        next_close=datetime(2026, 10, 27, 20, 0, tzinfo=UTC),
    )
    broker = make_broker(tmp_path, client=client)

    with pytest.raises(ValueError, match="timezone-aware"):
        broker.clock()


# --- Task 3: premarket probe ---

NOW = datetime(2026, 10, 27, 15, 0, tzinfo=UTC)  # 11:00 ET, regular session open
SESSIONS = (
    (date(2026, 10, 27), datetime(2026, 10, 27, 13, 30, tzinfo=UTC), datetime(2026, 10, 27, 20, 0, tzinfo=UTC)),
    (date(2026, 10, 28), datetime(2026, 10, 28, 13, 30, tzinfo=UTC), datetime(2026, 10, 28, 20, 0, tzinfo=UTC)),
)


class _FakeProbeBroker:
    """Minimal broker surface for the probe: ``clock()`` and ``working_orders()``."""

    def __init__(self, *, clock: BrokerClock | Exception, orders: list[str]) -> None:
        self._clock = clock
        self._orders = orders

    def clock(self) -> BrokerClock:
        if isinstance(self._clock, Exception):
            raise self._clock
        return self._clock

    def working_orders(self) -> tuple[Any, ...]:
        return tuple(SimpleNamespace(client_order_id=order_id) for order_id in self._orders)


def _clean_clock(**overrides: Any) -> BrokerClock:
    values: dict[str, Any] = {
        "timestamp": NOW,
        "is_open": True,
        "next_open": datetime(2026, 10, 28, 13, 30, tzinfo=UTC),
        "next_close": datetime(2026, 10, 27, 20, 0, tzinfo=UTC),
    }
    values.update(overrides)
    return BrokerClock(**values)


def _probe(broker: Any, *, local: list[str] | None = None, max_skew: float = 2.0) -> Any:
    return premarket_probe(
        broker,
        now=NOW,
        calendar_sessions=SESSIONS,
        open_orders_local=local if local is not None else ["cid-1"],
        max_clock_skew_seconds=max_skew,
    )


def test_probe_passes_on_clean_setup() -> None:
    broker = _FakeProbeBroker(clock=_clean_clock(), orders=["cid-1"])

    result = _probe(broker)

    assert result.ok is True
    assert result.reasons == []


def test_probe_tolerates_skew_within_limit() -> None:
    broker = _FakeProbeBroker(clock=_clean_clock(timestamp=NOW + timedelta(seconds=1)), orders=["cid-1"])

    assert _probe(broker).ok is True


def test_probe_fails_on_clock_skew() -> None:
    broker = _FakeProbeBroker(clock=_clean_clock(timestamp=NOW + timedelta(seconds=5)), orders=["cid-1"])

    result = _probe(broker)

    assert result.ok is False
    assert any("clock skew" in reason for reason in result.reasons)


def test_probe_fails_on_calendar_mismatch() -> None:
    early_close = datetime(2026, 10, 27, 18, 0, tzinfo=UTC)
    broker = _FakeProbeBroker(clock=_clean_clock(next_close=early_close), orders=["cid-1"])

    result = _probe(broker)

    assert result.ok is False
    assert any("next_close" in reason and "disagrees" in reason for reason in result.reasons)


def test_probe_fails_on_unknown_broker_order() -> None:
    broker = _FakeProbeBroker(clock=_clean_clock(), orders=["cid-1", "cid-unknown"])

    result = _probe(broker)

    assert result.ok is False
    assert result.reasons == ["unknown broker open order cid-unknown"]


def test_probe_fails_on_local_order_missing_at_broker() -> None:
    broker = _FakeProbeBroker(clock=_clean_clock(), orders=[])

    result = _probe(broker, local=["cid-1"])

    assert result.ok is False
    assert result.reasons == ["local non-terminal order cid-1 missing at broker"]


def test_probe_reports_unreachable_broker_clock_as_failure() -> None:
    broker = _FakeProbeBroker(clock=ConnectionError("timeout"), orders=[])

    result = _probe(broker)

    assert result.ok is False
    assert any("broker clock unavailable" in reason for reason in result.reasons)


def test_probe_rejects_naive_now() -> None:
    broker = _FakeProbeBroker(clock=_clean_clock(), orders=["cid-1"])

    with pytest.raises(ValueError, match="timezone-aware"):
        premarket_probe(
            broker,
            now=datetime(2026, 10, 27, 11, 0),
            calendar_sessions=SESSIONS,
            open_orders_local=["cid-1"],
        )
