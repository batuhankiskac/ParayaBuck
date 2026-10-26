"""Lot-book integrity: strict FIFO replay, gain band fallback, cost-basis drift."""

from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from parayabuck.core.models import Market
from parayabuck.execution import alerting
from parayabuck.execution import alpaca_reconciliation as reconciliation
from parayabuck.execution.alpaca_reconciliation import (
    COST_BASIS_DRIFT_KEY,
    ReconciliationReport,
    ReconciliationResultError,
    ReconciliationService,
)
from parayabuck.execution.portfolio import downsize_gain_band_verdict
from parayabuck.portfolio import ownership_rebase
from parayabuck.portfolio.ownership_rebase import (
    LotBookInconsistency,
    open_position_cost_bases,
    open_position_lots,
)


@dataclass(frozen=True)
class FakePosition:
    symbol: str
    avg_price: float
    qty: float = 10.0
    market: Market = Market.EQUITY

    @property
    def is_flat(self) -> bool:
        return self.qty == 0


class AlertRecorder:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    def __call__(self, kind: str, *, message: str, **fields: object) -> None:
        self.calls.append((kind, {"message": message, **fields}))


class LogRecorder:
    def __init__(self) -> None:
        self.errors: list[str] = []

    def error(self, message: str, *args: object) -> None:
        self.errors.append(message % args)


def _service(provider=None) -> ReconciliationService:
    broker = SimpleNamespace()
    if provider is not None:
        broker.portfolio_owned_cost_bases = provider
    return ReconciliationService(broker)


# -- TASK 1: strict replay ----------------------------------------------------


def test_consistent_replay_is_unchanged() -> None:
    events = [("buy", 10.0, 10.0), ("buy", 10.0, 20.0), ("sell", 15.0, 30.0)]

    assert open_position_lots(events) == ((5.0, 20.0),)
    fifo, average = open_position_cost_bases(events)
    assert fifo == pytest.approx(20.0)
    assert average == pytest.approx(15.0)


def test_sell_within_tolerance_of_open_quantity_does_not_raise() -> None:
    events = [("buy", 10.0, 10.0), ("sell", 10.0 + 1e-6, 12.0)]

    assert open_position_cost_bases(events) == ()


def test_oversell_beyond_tolerance_raises_with_quantities() -> None:
    events = [("buy", 10.0, 10.0), ("sell", 10.5, 12.0)]

    with pytest.raises(LotBookInconsistency) as caught:
        open_position_cost_bases(events)

    assert caught.value.sell_quantity == pytest.approx(10.5)
    assert caught.value.open_quantity == pytest.approx(10.0)
    assert isinstance(caught.value, ValueError)


def test_reducing_adjustment_beyond_open_quantity_raises() -> None:
    with pytest.raises(LotBookInconsistency):
        open_position_cost_bases([("buy", 5.0, 10.0), ("adjust", -6.0, None)])


# -- TASK 1: gain band caller falls back with a recorded reason ---------------


def test_gain_band_lot_input_falls_back_and_records_reason(monkeypatch: pytest.MonkeyPatch) -> None:
    alerts = AlertRecorder()
    logs = LogRecorder()
    monkeypatch.setattr(alerting, "emit_execution_alert", alerts)
    monkeypatch.setattr(ownership_rebase, "log", logs)

    lots = open_position_lots([("buy", 10.0, 10.0), ("sell", 11.0, 12.0)])

    assert lots == ()
    assert len(logs.errors) == 1
    assert "keeping the constant band" in logs.errors[0]
    assert [kind for kind, _ in alerts.calls] == ["paper_incident"]
    assert alerts.calls[0][1]["category"] == "lot_book_inconsistency"
    assert alerts.calls[0][1]["sell_quantity"] == pytest.approx(11.0)
    assert alerts.calls[0][1]["open_quantity"] == pytest.approx(10.0)

    verdict = downsize_gain_band_verdict(
        object(),
        target_weight=0.2,
        current_weight=0.4,
        units=5.0,
        price=20.0,
        lots=lots,
        managed_qty=10.0,
        base_band=0.05,
    )
    assert verdict.applied is False
    assert verdict.band == pytest.approx(0.05)
    assert verdict.fallback == "lots_unavailable"


# -- TASK 1: reconcile path fails closed --------------------------------------


def test_reconcile_cost_basis_path_fails_closed_on_inconsistent_lot_book() -> None:
    def provider(symbol: str, market: Market) -> tuple[float, ...]:
        raise LotBookInconsistency(symbol=symbol, sell_quantity=11.0, open_quantity=10.0)

    service = _service(provider)
    positions = [FakePosition("AAA", avg_price=10.0)]

    with pytest.raises(ReconciliationResultError, match="AAA"):
        service._cost_basis_drift(positions, ["AAA"], {})


# -- TASK 2: cost-basis drift check -------------------------------------------


def test_cost_basis_drift_alerts_once_above_threshold(monkeypatch: pytest.MonkeyPatch) -> None:
    alerts = AlertRecorder()
    monkeypatch.setattr(reconciliation, "emit_execution_alert", alerts)
    service = _service(lambda symbol, market: (100.0, 100.0))
    positions = [FakePosition("AAA", avg_price=103.0)]

    first = service._cost_basis_drift(positions, ["AAA"], {})
    second = service._cost_basis_drift(positions, ["AAA"], {})

    assert set(first) == {"AAA"}
    assert first["AAA"]["bot_average_cost"] == pytest.approx(100.0)
    assert first["AAA"]["tolerance"] == pytest.approx(0.515)
    assert set(second) == {"AAA"}
    assert len(alerts.calls) == 1
    kind, fields = alerts.calls[0]
    assert kind == "paper_incident"
    assert fields["category"] == "cost_basis_drift"


def test_cost_basis_drift_silent_below_threshold(monkeypatch: pytest.MonkeyPatch) -> None:
    alerts = AlertRecorder()
    monkeypatch.setattr(reconciliation, "emit_execution_alert", alerts)
    service = _service(lambda symbol, market: (100.0, 100.4))

    # 0.4 is inside max(0.5% of 100, $0.02) = 0.5.
    assert service._cost_basis_drift([FakePosition("AAA", avg_price=100.0)], ["AAA"], {}) == {}
    assert alerts.calls == []


def test_cost_basis_drift_dollar_floor_applies_to_cheap_shares(monkeypatch: pytest.MonkeyPatch) -> None:
    alerts = AlertRecorder()
    monkeypatch.setattr(reconciliation, "emit_execution_alert", alerts)
    service = _service(lambda symbol, market: (2.0, 2.0))

    assert service._cost_basis_drift([FakePosition("AAA", avg_price=2.01)], ["AAA"], {}) == {}
    assert set(service._cost_basis_drift([FakePosition("AAA", avg_price=2.03)], ["AAA"], {})) == {"AAA"}


def test_cost_basis_drift_skipped_with_external_units(monkeypatch: pytest.MonkeyPatch) -> None:
    alerts = AlertRecorder()
    monkeypatch.setattr(reconciliation, "emit_execution_alert", alerts)
    service = _service(lambda symbol, market: (100.0, 100.0))
    positions = [FakePosition("AAA", avg_price=150.0, qty=15.0)]

    # Broker holds more than the bot owns: not MATCH, so the check never runs.
    assert service._cost_basis_drift(positions, [], {}) == {}
    # Foreign inventory on the same symbol also excludes it.
    assert service._cost_basis_drift(positions, ["AAA"], {"AAA": Market.EQUITY.value}) == {}
    assert alerts.calls == []


def test_cost_basis_drift_is_absent_without_provider() -> None:
    service = _service()

    assert service._cost_basis_drift([FakePosition("AAA", avg_price=150.0)], ["AAA"], {}) == {}


def test_drift_is_recorded_without_becoming_a_drift_category() -> None:
    result = {category: [] for category in reconciliation.RECONCILIATION_CATEGORIES}
    result[COST_BASIS_DRIFT_KEY] = ["AAA"]

    report = ReconciliationReport.from_result(result)

    assert report.cost_basis_drift == ("AAA",)
    assert report.has_drift is False
    assert COST_BASIS_DRIFT_KEY not in report.as_dict()
