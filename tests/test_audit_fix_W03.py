"""Regression tests for audit fix wave W03 (Alpaca account and reconciliation).

Covers G-1/D8-1, G-2, E-1, D8-5/G-4, D8-6, D8-10, D8-12, N6-2, C7-2, BK-4 and
F9-2 (behind ``risk.adjust_for_cash_flows``). Fake client and synthetic
accounts only: no network, no credentials, peak sidecar confined to tmp_path,
alerts captured instead of emitted.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from parayabuck.config import RiskLimits
from parayabuck.core.models import Market, PositionSnapshot
from parayabuck.execution import alpaca_account, alpaca_reconciliation
from parayabuck.execution.alpaca_account import (
    _read_same_day_external_flows,
    flat_broker_reconcile_scope,
    quantities_agree,
)
from parayabuck.execution.alpaca_models import EmptyPositionsReadError, OwnershipDriftError
from parayabuck.execution.alpaca_reconciliation import ReconciliationResultError, ReconciliationService
from tests.fake_client import FakeTradingClient
from tests.helpers import make_broker

TODAY = date(2026, 10, 27)


@pytest.fixture
def alerts(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Capture execution alerts instead of emitting them."""
    captured: list[dict[str, Any]] = []

    def record(kind: str, *, message: str, **fields: object) -> None:
        captured.append({"kind": kind, "message": message, **fields})

    monkeypatch.setattr(alpaca_account, "emit_execution_alert", record)
    monkeypatch.setattr(alpaca_reconciliation, "emit_execution_alert", record)
    return captured


@pytest.fixture
def fixed_day(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(alpaca_account, "_trading_day", lambda now: TODAY)


def _account(
    equity: float,
    last_equity: float | None,
    *,
    status: Any = "ACTIVE",
    account_id: str | None = "fake-acct-1234",
) -> SimpleNamespace:
    return SimpleNamespace(
        id=account_id,
        account_number="PA-TEST-0001",
        account_blocked=False,
        trading_blocked=False,
        trade_suspended_by_user=False,
        equity=str(equity),
        last_equity=None if last_equity is None else str(last_equity),
        cash="90000",
        status=status,
        non_marginable_buying_power=None,
    )


class _ActivityClient(FakeTradingClient):
    """Fake client that also serves a scripted activities list."""

    def __init__(self) -> None:
        super().__init__()
        self.rows: list[dict[str, Any]] = []
        self.activity_error: Exception | None = None

    def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        if path == "/account/activities":
            if self.activity_error is not None:
                raise self.activity_error
            return list(self.rows)
        return super().get(path, params)


def _broker(tmp_path: Path, client: Any, owned: dict[str, float] | None = None, **kwargs: Any) -> Any:
    options: dict[str, Any] = {
        "portfolio_owned_quantities": lambda: dict(owned or {}),
        "portfolio_open_orders": list,
    }
    options.update(kwargs)
    broker = make_broker(tmp_path, client=client, **options)
    broker.bot_tradable_symbols = lambda: {"QLD"}
    return broker


# --- G-1 / D8-1: empty positions read inside the reconcile scope -------------


def test_single_empty_read_inside_scope_is_not_a_flat_broker(tmp_path: Path, alerts: list) -> None:
    fake = FakeTradingClient()
    fake.set_position("QLD", qty=10)
    reads: list[list[Any]] = [[], fake.positions]  # first read empty, second lists the position
    fake.script_get_all_positions = lambda: reads.pop(0)
    broker = _broker(tmp_path, fake, {"QLD": 10.0})
    with flat_broker_reconcile_scope():
        snapshot = broker.account()
    assert [p.symbol for p in snapshot.positions] == ["QLD"]
    assert not reads  # both reads happened


def test_two_empty_reads_inside_scope_accept_flat_for_reconcile(tmp_path: Path, alerts: list) -> None:
    fake = FakeTradingClient()
    reads: list[int] = []

    def empty() -> list[Any]:
        reads.append(1)
        return []

    fake.script_get_all_positions = empty
    broker = _broker(tmp_path, fake, {"QLD": 10.0})
    with flat_broker_reconcile_scope():
        assert broker.account().positions == ()
    assert len(reads) == 2


def test_empty_read_without_booked_inventory_is_not_reread(tmp_path: Path, alerts: list) -> None:
    fake = FakeTradingClient()
    reads: list[int] = []

    def empty() -> list[Any]:
        reads.append(1)
        return []

    fake.script_get_all_positions = empty
    assert _broker(tmp_path, fake, {}).account().positions == ()
    assert len(reads) == 1


def test_empty_read_outside_scope_still_fails_closed(tmp_path: Path, alerts: list) -> None:
    fake = FakeTradingClient()
    fake.script_get_all_positions = lambda: []
    with pytest.raises(EmptyPositionsReadError):
        _broker(tmp_path, fake, {"QLD": 10.0}).account()


# --- G-2: owned symbol missing from a non-empty broker list ------------------


def test_owned_symbol_missing_from_nonempty_broker_list_is_drift(tmp_path: Path, alerts: list) -> None:
    fake = FakeTradingClient()
    fake.set_position("IWM", qty=4)  # QLD is owned by the bot but absent
    broker = _broker(tmp_path, fake, {"QLD": 10.0, "IWM": 4.0})
    with pytest.raises(OwnershipDriftError) as info:
        broker.account()
    assert info.value.symbols == ("QLD",)


def test_owned_symbol_missing_is_explained_by_our_working_sell(tmp_path: Path, alerts: list) -> None:
    fake = FakeTradingClient()
    fake.set_position("IWM", qty=4)
    sell = SimpleNamespace(
        symbol="QLD",
        side="sell",
        market=Market.EQUITY,
        quantity=10.0,
        cumulative_filled_quantity=0.0,
        client_order_id="exit-1",
        broker_order_id="b-1",
    )
    broker = _broker(tmp_path, fake, {"QLD": 10.0, "IWM": 4.0}, portfolio_open_orders=lambda: [sell])
    snapshot = broker.account()
    assert [p.symbol for p in snapshot.positions] == ["IWM"]


def test_owned_symbol_missing_locks_only_that_symbol_with_the_flag(tmp_path: Path, alerts: list) -> None:
    fake = FakeTradingClient()
    fake.set_position("IWM", qty=4)
    broker = _broker(tmp_path, fake, {"QLD": 10.0, "IWM": 4.0})
    broker.per_symbol_drift_lock = True
    broker.account()
    assert broker.drift_locked_symbols == frozenset({"QLD"})


def test_owned_symbol_missing_from_a_flat_reconcile_read_is_left_to_reconcile(tmp_path: Path, alerts: list) -> None:
    fake = FakeTradingClient()
    fake.set_position("IWM", qty=4)
    broker = _broker(tmp_path, fake, {"QLD": 10.0, "IWM": 4.0})
    with flat_broker_reconcile_scope():
        snapshot = broker.account()
    assert [p.symbol for p in snapshot.positions] == ["IWM"]


# --- E-1: failed peak sidecar write is retried on the next poll -------------


def test_failed_peak_sidecar_write_is_retried_on_next_poll(tmp_path: Path, alerts: list, fixed_day: None) -> None:
    fake = FakeTradingClient()
    fake.script_get_account = lambda: _account(100000, 100000)
    broker = _broker(tmp_path, fake)
    attempts: list[float] = []

    def flaky_save() -> None:
        attempts.append(broker._peak_equity)
        if len(attempts) == 1:
            raise OSError("disk full")

    broker._save_peak_equity = flaky_save
    snapshot = broker.account()
    assert snapshot.equity == 100000.0  # the fetched snapshot is kept
    assert broker._account_service._peak_save_pending is True
    broker.account()  # the peak did not move, yet the sidecar is retried
    assert len(attempts) == 2
    assert broker._account_service._peak_save_pending is False
    broker.account()  # nothing pending and no new peak: no write
    assert len(attempts) == 2
    assert any(a["category"] == "peak_sidecar_write_failed" for a in alerts)


# --- D8-5 / G-4: missing last_equity must not read as flat -------------------


def test_missing_last_equity_without_a_baseline_refuses_the_snapshot(
    tmp_path: Path, alerts: list, fixed_day: None
) -> None:
    fake = FakeTradingClient()
    fake.script_get_account = lambda: _account(99000, None)
    with pytest.raises(ValueError, match="last_equity is missing"):
        _broker(tmp_path, fake).account()
    assert any(a["category"] == "last_equity_missing" for a in alerts)


def test_missing_last_equity_reuses_todays_baseline(tmp_path: Path, alerts: list, fixed_day: None) -> None:
    fake = FakeTradingClient()
    broker = _broker(tmp_path, fake)
    fake.script_get_account = lambda: _account(100000, 100000)
    broker.account()
    fake.script_get_account = lambda: _account(99000, None)
    snapshot = broker.account()
    assert snapshot.daily_pnl == pytest.approx(-1000.0)


def test_missing_last_equity_does_not_reuse_yesterdays_baseline(
    tmp_path: Path, alerts: list, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = FakeTradingClient()
    broker = _broker(tmp_path, fake)
    monkeypatch.setattr(alpaca_account, "_trading_day", lambda now: date(2026, 10, 26))
    fake.script_get_account = lambda: _account(100000, 100000)
    broker.account()
    monkeypatch.setattr(alpaca_account, "_trading_day", lambda now: TODAY)
    fake.script_get_account = lambda: _account(99000, None)
    with pytest.raises(ValueError, match="last_equity is missing"):
        broker.account()


# --- D8-6: account status must be ACTIVE -------------------------------------


@pytest.mark.parametrize("status", ["DISABLED", "ACTION_REQUIRED", "ACCOUNT_CLOSED", ""])
def test_non_active_account_status_refuses_the_snapshot(tmp_path: Path, alerts: list, status: str) -> None:
    fake = FakeTradingClient()
    fake.script_get_account = lambda: _account(100000, 100000, status=status)
    with pytest.raises(RuntimeError, match="status"):
        _broker(tmp_path, fake).account()


def test_active_account_status_is_accepted_enum_or_string(tmp_path: Path, alerts: list, fixed_day: None) -> None:
    fake = FakeTradingClient()
    fake.script_get_account = lambda: _account(100000, 100000, status=SimpleNamespace(value="ACTIVE"))
    assert _broker(tmp_path, fake).account().equity == 100000.0
    fake.script_get_account = lambda: _account(100000, 100000, status="ACTIVE")
    assert _broker(tmp_path, fake).account().equity == 100000.0


# --- BK-4: no account_number fallback for the peak key -----------------------


def test_missing_account_id_refuses_without_account_number_fallback(tmp_path: Path, alerts: list) -> None:
    fake = FakeTradingClient()
    fake.script_get_account = lambda: _account(100000, 100000, account_id=None)
    broker = _broker(tmp_path, fake)
    with pytest.raises(ValueError, match="no id"):
        broker.account()
    assert broker._account_id == ""


def test_reconcile_refuses_missing_account_id_without_fallback() -> None:
    broker = SimpleNamespace(
        _account_id="",
        _client=SimpleNamespace(get_account=lambda: SimpleNamespace(account_number="PA-TEST-0001")),
    )
    with pytest.raises(ReconciliationResultError, match="no identity"):
        ReconciliationService(broker).reconcile()
    assert broker._account_id == ""


# --- D8-10: foreign position with a missing mark is valued at cost basis ----


def test_foreign_position_with_missing_mark_is_valued_at_cost_basis(tmp_path: Path, alerts: list) -> None:
    fake = FakeTradingClient()
    fake.set_position("XYZ", qty=3)
    fake.positions[0].current_price = None
    fake.positions[0].avg_entry_price = "42.5"
    (position,) = _broker(tmp_path, fake).account().positions
    assert position.current_price == 42.5
    assert position.market_value == pytest.approx(3 * 42.5)


def test_bot_position_with_missing_mark_still_fails_closed(tmp_path: Path, alerts: list) -> None:
    fake = FakeTradingClient()
    fake.set_position("QLD", qty=3)
    fake.positions[0].current_price = None
    with pytest.raises(ValueError, match="no price"):
        _broker(tmp_path, fake, {"QLD": 3.0}).account()


# --- D8-12: foreign-position parse errors are isolated -----------------------


def test_unparseable_foreign_pnl_does_not_refuse_the_snapshot(tmp_path: Path, alerts: list, fixed_day: None) -> None:
    fake = FakeTradingClient()
    fake.set_position("XYZ", qty=3)
    fake.set_position("QLD", qty=2)
    fake.positions[0].unrealized_pl = "N/A"
    fake.positions[0].unrealized_intraday_pl = "N/A"
    fake.script_get_account = lambda: _account(100000, 100000)
    snapshot = _broker(tmp_path, fake, {"QLD": 2.0}).account()
    assert {p.symbol for p in snapshot.positions} == {"XYZ", "QLD"}
    assert snapshot.unrealized_pnl == 0.0


def test_unparseable_bot_pnl_stays_strict(tmp_path: Path, alerts: list) -> None:
    fake = FakeTradingClient()
    fake.set_position("QLD", qty=2)
    fake.positions[0].unrealized_pl = "N/A"
    with pytest.raises(ValueError, match="not numeric"):
        _broker(tmp_path, fake, {"QLD": 2.0}).account()


# --- N6-2: one tolerance for drift detection and classification --------------


def test_quantities_agree_uses_the_resolver_band() -> None:
    assert quantities_agree(1000.0, 1000.0 - 5e-4)  # 5e-7 relative: inside the 1e-6 band
    assert not quantities_agree(1000.0, 1000.0 - 2e-3)  # 2e-6 relative: outside
    assert quantities_agree(1e-3, 1e-3 + 5e-9)  # absolute floor absorbs 9-decimal noise


def test_drift_detection_does_not_halt_inside_the_shared_band(tmp_path: Path, alerts: list, fixed_day: None) -> None:
    fake = FakeTradingClient()
    fake.set_position("QLD", qty=999.9995)  # 5e-7 relative short of the owned 1000
    fake.script_get_account = lambda: _account(100000, 100000)
    snapshot = _broker(tmp_path, fake, {"QLD": 1000.0}).account()
    assert snapshot.positions[0].symbol == "QLD"


def test_drift_detection_halts_outside_the_shared_band(tmp_path: Path, alerts: list) -> None:
    fake = FakeTradingClient()
    fake.set_position("QLD", qty=999.998)  # 2e-6 relative short
    with pytest.raises(OwnershipDriftError):
        _broker(tmp_path, fake, {"QLD": 1000.0}).account()


# --- C7-2: cost-basis alert re-arms after the symbol returns to band --------


def test_cost_basis_alert_rearms_after_the_symbol_returns_to_band(alerts: list) -> None:
    bot_average = {"value": 120.0}
    broker = SimpleNamespace(portfolio_owned_cost_bases=lambda symbol, market: (0.0, bot_average["value"]))
    service = ReconciliationService(broker)
    positions = [
        PositionSnapshot(symbol="QLD", qty=10, avg_price=100.0, current_price=100.0, market=Market.EQUITY),
    ]

    def drift() -> dict[str, dict[str, float]]:
        return service._cost_basis_drift(positions, ["QLD"], {})

    def cost_alerts() -> int:
        return sum(1 for a in alerts if a.get("category") == "cost_basis_drift")

    assert "QLD" in drift()
    assert cost_alerts() == 1
    assert "QLD" in drift()  # same episode: no repeat alert
    assert cost_alerts() == 1
    bot_average["value"] = 100.0
    assert drift() == {}  # back in tolerance
    bot_average["value"] = 120.0
    assert "QLD" in drift()
    assert cost_alerts() == 2  # a new episode alerts again


# --- F9-2: same-day external cash flows (flag risk.adjust_for_cash_flows) ---


def test_risk_flag_defaults_off_and_must_be_boolean() -> None:
    assert RiskLimits().adjust_for_cash_flows is False
    with pytest.raises(ValueError, match="adjust_for_cash_flows must be a boolean"):
        RiskLimits(adjust_for_cash_flows="yes")  # type: ignore[arg-type]


def test_flow_sum_counts_deposits_withdrawals_and_journals_on_the_ny_day() -> None:
    client = _ActivityClient()
    client.rows = [
        {"id": "a1", "activity_type": "CSD", "date": "2026-10-27", "net_amount": "5000.00"},
        {"id": "a2", "activity_type": "CSW", "date": "2026-10-27", "net_amount": "-1200.00"},
        {"id": "a3", "activity_type": "JNLC", "date": "2026-10-27", "net_amount": "100"},
        {"id": "a4", "activity_type": "CSD", "date": "2026-10-26", "net_amount": "9999"},
    ]
    assert _read_same_day_external_flows(client, TODAY) == pytest.approx(3900.0)


def test_flag_off_keeps_a_deposit_in_daily_pnl(tmp_path: Path, alerts: list, fixed_day: None) -> None:
    client = _ActivityClient()
    client.rows = [{"id": "a1", "activity_type": "CSD", "date": "2026-10-27", "net_amount": "5000"}]
    client.script_get_account = lambda: _account(105000, 100000)
    snapshot = _broker(tmp_path, client).account()
    assert snapshot.daily_pnl == pytest.approx(5000.0)


def test_flag_on_nets_a_same_day_deposit_out_of_pnl_and_drawdown(tmp_path: Path, alerts: list, fixed_day: None) -> None:
    client = _ActivityClient()
    client.rows = [{"id": "a1", "activity_type": "CSD", "date": "2026-10-27", "net_amount": "5000"}]
    client.script_get_account = lambda: _account(105000, 100000)
    broker = _broker(tmp_path, client)
    broker.adjust_for_cash_flows = True
    snapshot = broker.account()
    assert snapshot.daily_pnl == pytest.approx(0.0)
    assert snapshot.drawdown_pct == pytest.approx(0.0)
    assert broker._peak_equity == pytest.approx(100000.0)  # the transfer never raises the high-water mark


def test_flag_on_nets_a_same_day_withdrawal_out_of_drawdown(tmp_path: Path, alerts: list, fixed_day: None) -> None:
    client = _ActivityClient()
    client.script_get_account = lambda: _account(100000, 100000)
    broker = _broker(tmp_path, client)
    broker.adjust_for_cash_flows = True
    broker.account()  # peak 100000, no flow
    client.rows = [{"id": "w1", "activity_type": "CSW", "date": "2026-10-27", "net_amount": "-25000"}]
    broker._account_service._cash_flow_cache = None
    client.script_get_account = lambda: _account(75000, 100000)
    snapshot = broker.account()
    assert snapshot.daily_pnl == pytest.approx(0.0)
    assert snapshot.drawdown_pct == pytest.approx(0.0)  # a 25 % withdrawal is not a 25 % loss
    assert broker._peak_equity == pytest.approx(100000.0)


def test_flag_on_failed_activities_read_keeps_the_current_behaviour(
    tmp_path: Path, alerts: list, fixed_day: None
) -> None:
    client = _ActivityClient()
    client.activity_error = RuntimeError("activities endpoint down")
    client.script_get_account = lambda: _account(75000, 100000)
    broker = _broker(tmp_path, client)
    broker.adjust_for_cash_flows = True
    snapshot = broker.account()
    assert snapshot.daily_pnl == pytest.approx(-25000.0)  # unadjusted, as before the flag


def test_flag_on_does_not_read_activities_when_disabled(tmp_path: Path, alerts: list, fixed_day: None) -> None:
    client = _ActivityClient()
    calls: list[str] = []
    original_get = client.get

    def tracking_get(path: str, params: dict[str, Any] | None = None) -> Any:
        calls.append(path)
        return original_get(path, params)

    client.get = tracking_get  # type: ignore[method-assign]
    client.script_get_account = lambda: _account(100000, 100000)
    _broker(tmp_path, client).account()
    assert "/account/activities" not in calls


# --- BF-3: fill timestamp falls back filled_at -> submitted_at (never updated_at) ---


def _recovery_service(recorded: list[Any]) -> ReconciliationService:
    local = SimpleNamespace(
        client_order_id="exit-ts-1",
        broker_order_id="b-ts-1",
        symbol="QLD",
        market=Market.EQUITY,
        side="sell",
        quantity=10.0,
        status="submitted",
    )
    broker = SimpleNamespace(
        _field=lambda value, name, default=None: getattr(value, name, default),
        _normalized_field=lambda value, name: str(getattr(value, name, "") or ""),
        portfolio_open_orders=lambda: [local],
        portfolio_fill_recovered=lambda *args, **kwargs: recorded.append(args[4]),
    )
    return ReconciliationService(broker)


def _filled_broker_order(**timestamps: Any) -> SimpleNamespace:
    base: dict[str, Any] = {
        "client_order_id": "exit-ts-1",
        "id": "b-ts-1",
        "symbol": "QLD",
        "side": "sell",
        "status": "filled",
        "filled_qty": "10",
        "filled_avg_price": "100",
        "qty": "10",
        "filled_at": None,
        "submitted_at": None,
        "updated_at": None,
    }
    base.update(timestamps)
    return SimpleNamespace(**base)


def test_fill_timestamp_prefers_filled_at() -> None:
    recorded: list[Any] = []
    service = _recovery_service(recorded)
    order = _filled_broker_order(
        filled_at=datetime(2026, 10, 27, 14, 0, tzinfo=UTC),
        submitted_at=datetime(2026, 10, 27, 13, 0, tzinfo=UTC),
        updated_at=datetime(2026, 10, 27, 15, 0, tzinfo=UTC),
    )
    service._recover_portfolio_orders([order])
    assert recorded == [datetime(2026, 10, 27, 14, 0, tzinfo=UTC)]


def test_fill_timestamp_falls_back_to_submitted_at_not_updated_at() -> None:
    recorded: list[Any] = []
    service = _recovery_service(recorded)
    order = _filled_broker_order(
        filled_at=None,
        submitted_at=datetime(2026, 10, 27, 13, 0, tzinfo=UTC),
        updated_at=datetime(2026, 10, 27, 15, 0, tzinfo=UTC),
    )
    service._recover_portfolio_orders([order])
    assert recorded == [datetime(2026, 10, 27, 13, 0, tzinfo=UTC)]
