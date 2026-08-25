from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta

import scripts.reconcile_paper_incident as incident_cli
from parayabuck.monitoring.paper_incident import (
    BrokerCashActivityRecord,
    BrokerFillRecord,
    BrokerOrderRecord,
    BrokerPositionRecord,
    PaperIncidentReconciler,
    PaperIncidentSnapshot,
)


def test_paper_incident_reconciliation_is_read_only_and_anonymized(tmp_path) -> None:
    database = tmp_path / "ledger.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.executescript(
            """
            CREATE TABLE orders (
                client_order_id TEXT, broker_order_id TEXT, entry_price REAL
            );
            CREATE TABLE managed_positions (
                canonical_symbol TEXT, status TEXT, realized_pnl REAL,
                entry_fees REAL, exit_fees REAL
            );
            INSERT INTO orders VALUES ('bot-buy', 'broker-buy', 10);
            INSERT INTO managed_positions VALUES ('AAPL', 'closed', 0, 0, 0);
            """
        )
    before = database.read_bytes()
    now = datetime(2026, 1, 1, tzinfo=UTC)
    snapshot = PaperIncidentSnapshot(
        orders=(
            BrokerOrderRecord("broker-buy", "bot-buy", "AAPL", "buy", 5, 5, "filled", now),
            BrokerOrderRecord("manual-sell", "manual", "AAPL", "sell", 5, 5, "filled", now),
        ),
        fills=(
            BrokerFillRecord("fill-buy", "broker-buy", "AAPL", "buy", 5, 10, 0.1, now),
            BrokerFillRecord(
                "fill-sell",
                "manual-sell",
                "AAPL",
                "sell",
                5,
                12,
                0.1,
                now + timedelta(hours=1),
            ),
        ),
        positions=(BrokerPositionRecord("BTC/USD", 0.1, 50_000, 49_000),),
        cash_activities=(BrokerCashActivityRecord("deposit", "CSD", 200, now),),
        starting_equity=10_000,
        ending_equity=10_109.8,
    )
    reconciler = PaperIncidentReconciler()
    report = reconciler.reconcile(snapshot, database)
    assert report.realized_pnl == 10.0
    assert report.unrealized_pnl == -100
    assert report.unrealized_pnl_change == -100
    assert report.net_transfers == 200
    assert report.expected_ending_equity == 10_109.8
    assert report.actual_ending_equity == 10_109.8
    assert report.equity_residual == 0
    assert report.manual_exit_order_ids == ("manual-sell",)
    assert report.external_position_symbols == ("BTC/USD",)
    assert report.local_closed_positions_with_zero_pnl == 1
    assert report.local_closed_positions_with_zero_fees == 1
    assert database.read_bytes() == before

    fixture = reconciler.anonymized_replay_fixture(snapshot, report)
    serialized = json.dumps(fixture)
    assert "manual-sell" not in serialized
    assert "fill-buy" not in serialized


def test_incident_download_paginates_orders_and_fills(monkeypatch) -> None:
    order_time = "2026-01-02T00:00:00Z"
    older_time = "2026-01-01T00:00:00Z"
    first_orders = [{"id": f"order-{index}", "created_at": order_time} for index in range(500)]
    second_orders = [{"id": "order-older", "created_at": older_time}]
    first_fills = [
        {
            "id": f"fill-{index}",
            "transaction_time": order_time,
        }
        for index in range(100)
    ]
    second_fills = [{"id": "fill-older", "transaction_time": older_time}]
    calls: list[tuple[str, dict[str, object]]] = []

    def fake_get(
        path: str,
        *,
        key: str,
        secret: str,
        params: dict[str, object] | None = None,
    ) -> object:
        del key, secret
        actual = params or {}
        calls.append((path, actual))
        if path == "/orders":
            return second_orders if "until" in actual else first_orders
        return second_fills if "page_token" in actual else first_fills

    monkeypatch.setattr(incident_cli, "_get", fake_get)
    assert len(incident_cli._all_orders("key", "secret", None)) == 501
    assert len(incident_cli._all_fills("key", "secret", "2026-01-01T00:00:00Z")) == 101
    assert any("until" in params for path, params in calls if path == "/orders")
    assert any("page_token" in params for path, params in calls if path == "/account/activities/FILL")
    assert all(
        params["after"] == "2026-01-01T00:00:00Z" for path, params in calls if path == "/account/activities/FILL"
    )


def test_incident_equity_bridge_uses_unrealized_pnl_change(tmp_path) -> None:
    database = tmp_path / "empty.sqlite3"
    with sqlite3.connect(database):
        pass
    snapshot = PaperIncidentSnapshot(
        orders=(),
        fills=(),
        positions=(BrokerPositionRecord("AAPL", 10, 100, 102),),
        starting_equity=10_000,
        ending_equity=10_005,
        starting_unrealized_pnl=15,
    )
    report = PaperIncidentReconciler().reconcile(snapshot, database)
    assert report.unrealized_pnl == 20
    assert report.starting_unrealized_pnl == 15
    assert report.unrealized_pnl_change == 5
    assert report.expected_ending_equity == 10_005
    assert report.equity_residual == 0


def test_incident_download_paginates_relevant_cash_activities(monkeypatch) -> None:
    first_page = [{"id": f"cash-{index}", "date": "2026-01-02", "net_amount": "1"} for index in range(100)]
    second_page = [{"id": "cash-older", "date": "2026-01-01", "net_amount": "-2"}]
    calls: list[dict[str, object]] = []

    def fake_get(
        path: str,
        *,
        key: str,
        secret: str,
        params: dict[str, object] | None = None,
    ) -> object:
        del key, secret
        assert path == "/account/activities"
        actual = params or {}
        calls.append(actual)
        return second_page if "page_token" in actual else first_page

    monkeypatch.setattr(incident_cli, "_get", fake_get)
    activities = incident_cli._all_cash_activities("key", "secret", "2026-01-01T00:00:00Z")
    assert len(activities) == 101
    assert calls[0]["activity_types"] == "CSD,CSW,ACATC,JNLC"
    assert calls[0]["after"] == "2026-01-01T00:00:00Z"
    assert "page_token" in calls[1]
