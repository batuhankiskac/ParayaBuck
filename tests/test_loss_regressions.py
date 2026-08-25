from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from parayabuck.config import Config, EquityUniverseConfig
from parayabuck.core.models import (
    AccountSnapshot,
    Direction,
    ExecutionEvent,
    Fill,
    Market,
    OrderIntent,
    OrderRole,
    OrderState,
)
from parayabuck.execution.alpaca import AlpacaBroker
from parayabuck.execution.alpaca_updates import AlpacaTradingUpdateStream
from parayabuck.execution.ledger import ExecutionLedger
from parayabuck.runtime import EngineRuntime
from parayabuck.universe import EquityCandidate, EquityUniverseScanner
from tests.helpers import apply_entry_fill

pytestmark = pytest.mark.paper_chaos


def test_parentless_bracket_children_keep_their_exit_roles() -> None:
    updates = []
    stream = AlpacaTradingUpdateStream.__new__(AlpacaTradingUpdateStream)

    async def handler(update: object) -> None:
        updates.append(update)

    async def scenario() -> None:
        stream._handler = handler
        common = {
            "asset_class": "us_equity",
            "symbol": "AAPL",
            "order_class": "bracket",
            "qty": "10",
            "filled_qty": "0",
            "updated_at": datetime.now(UTC),
        }
        await stream._on_update(
            {
                "event": "new",
                "order": {**common, "id": "stop", "type": "stop", "stop_price": "95"},
            }
        )
        await stream._on_update({"event": "new", "order": {**common, "id": "target", "type": "limit"}})

    asyncio.run(scenario())
    assert [item.role for item in updates] == [
        OrderRole.PROTECTIVE_STOP,
        OrderRole.TAKE_PROFIT,
    ]


def test_uuid_exit_without_parent_links_to_unique_managed_lot(tmp_path: Path) -> None:
    ledger = ExecutionLedger(str(tmp_path / "ledger.sqlite3"))
    order = OrderIntent(
        "signal",
        "GE",
        Market.EQUITY,
        Direction.LONG,
        10,
        100,
        95,
        110,
        "test",
        client_order_id="parayabuck-entry",
    )
    ledger.register_entry_order("acct", order, "broker-entry")
    apply_entry_fill(
        ledger,
        "acct",
        order,
        Fill(order.client_order_id, "GE", 10, 100, Direction.LONG, datetime.now(UTC)),
        "broker-entry",
    )

    transition = ledger.apply_execution_event(
        ExecutionEvent(
            "acct",
            "broker-generated-uuid",
            "broker-stop",
            None,
            OrderRole.MANUAL_EXIT,
            "GE",
            Market.EQUITY,
            OrderState.FILLED,
            10,
            10,
            95,
            datetime.now(UTC),
        )
    )

    row = ledger.connection.execute(
        """
        SELECT entry_client_order_id, realized_pnl, status
        FROM managed_positions WHERE entry_client_order_id=?
        """,
        (order.client_order_id,),
    ).fetchone()
    assert transition.entry_client_order_id == order.client_order_id
    assert transition.realized_pnl_delta == -50
    assert row == (order.client_order_id, -50.0, "closed")
    ledger.close()


def test_account_poll_does_not_restore_stale_consecutive_loss_count(tmp_path: Path) -> None:
    class RiskStub:
        consecutive_losses = 2

        def record_realized_pnl(self, pnl: float) -> None:
            self.consecutive_losses = self.consecutive_losses + 1 if pnl < 0 else 0

    ledger = ExecutionLedger(str(tmp_path / "ledger.sqlite3"))
    runtime = EngineRuntime.__new__(EngineRuntime)
    runtime.ledger = ledger
    runtime.broker = SimpleNamespace(account_id="acct")
    runtime.app_config = Config()
    runtime._entry_risk_latch = {Market.EQUITY: None, Market.CRYPTO: None}
    runtime._consecutive_losses = 2
    runtime._circuit_breaker_reason = None
    account = AccountSnapshot(
        cash=10_000,
        equity=10_000,
        peak_equity=10_000,
        daily_pnl=0,
        positions=(),
    )

    runtime._persist_risk_state(account)
    runtime._consecutive_losses = 3
    runtime._persist_risk_state(account)
    recovered_loss = AccountSnapshot(
        cash=9_950,
        equity=9_950,
        peak_equity=10_000,
        daily_pnl=-50,
        positions=(),
        realized_pnl=-50,
    )
    runtime._persist_risk_state(recovered_loss)

    saved = ledger.load_risk_state("acct", datetime.now(UTC).date())
    assert saved is not None
    assert saved.consecutive_losses == 4
    ledger.close()


def test_corrupt_risk_state_payload_is_treated_as_absent(tmp_path: Path) -> None:
    """A truncated/corrupt risk_state row must not fail startup: it is
    treated as absent and rebuilt from the account."""
    ledger = ExecutionLedger(str(tmp_path / "ledger.sqlite3"))
    ledger.connection.execute(
        """
        INSERT INTO risk_state(account_id, session_date, payload, updated_at)
        VALUES (?, ?, ?, ?)
        """,
        (
            "acct",
            datetime.now(UTC).date().isoformat(),
            "{truncated json",
            "2026-01-01T00:00:00+00:00",
        ),
    )
    ledger.connection.commit()
    assert ledger.load_risk_state("acct", datetime.now(UTC).date()) is None
    ledger.close()


def test_fill_activity_recovers_exit_pnl_when_stream_was_offline(tmp_path: Path) -> None:
    ledger = ExecutionLedger(str(tmp_path / "ledger.sqlite3"))
    created = datetime.now(UTC) - timedelta(minutes=5)
    order = OrderIntent(
        "signal",
        "GE",
        Market.EQUITY,
        Direction.LONG,
        10,
        100,
        95,
        110,
        "test",
        created_at=created,
        client_order_id="parayabuck-offline-entry",
    )
    ledger.register_entry_order("acct", order, "broker-entry")
    apply_entry_fill(
        ledger,
        "acct",
        order,
        Fill(order.client_order_id, "GE", 10, 100, Direction.LONG, created),
        "broker-entry",
    )
    activity = {
        "id": "activity-1",
        "order_id": "broker-stop-child",
        "symbol": "GE",
        "side": "sell",
        "qty": "10",
        "price": "95",
        "commission": "1",
        "transaction_time": datetime.now(UTC).isoformat(),
    }
    broker = AlpacaBroker.__new__(AlpacaBroker)
    broker._account_id = "acct"
    broker.ledger = ledger
    broker._client = SimpleNamespace(
        _request=lambda *_args, **_kwargs: [activity],
    )

    broker._reconcile_fill_activity_fees()
    broker._reconcile_fill_activity_fees()

    row = ledger.connection.execute(
        """
        SELECT exit_filled_qty, realized_pnl, exit_fees, status
        FROM managed_positions WHERE entry_client_order_id=?
        """,
        (order.client_order_id,),
    ).fetchone()
    assert row == (10.0, -51.0, 1.0, "closed")
    ledger.close()


def test_fill_activity_recovers_entry_fill_idempotently(tmp_path: Path) -> None:
    ledger = ExecutionLedger(str(tmp_path / "entry-recovery.sqlite3"))
    created = datetime.now(UTC) - timedelta(minutes=5)
    order = OrderIntent(
        "signal",
        "BTC/USD",
        Market.CRYPTO,
        Direction.LONG,
        0.1,
        100_000,
        95_000,
        110_000,
        "test",
        created_at=created,
        client_order_id="parayabuck-offline-crypto-entry",
    )
    ledger.register_entry_order("acct", order, "broker-entry")
    activities = [
        {
            "id": f"buy-activity-{index}",
            "order_id": "broker-entry",
            "symbol": "BTCUSD",
            "side": "buy",
            "qty": "0.05",
            "price": str(price),
            "commission": "0",
            "transaction_time": (created + timedelta(seconds=index)).isoformat(),
        }
        for index, price in enumerate((99_000, 101_000), 1)
    ]
    broker = AlpacaBroker.__new__(AlpacaBroker)
    broker._account_id = "acct"
    broker.ledger = ledger
    broker._client = SimpleNamespace(_request=lambda *_args, **_kwargs: activities)

    broker._reconcile_fill_activity_fees()
    broker._reconcile_fill_activity_fees()

    position = ledger.managed_for_account("acct")["BTC/USD"]
    assert position.owned_qty == 0.1
    assert position.filled_avg_price == 100_000
    ledger.close()


def test_scanner_keeps_only_one_alphabet_share_class(tmp_path: Path) -> None:
    scanner = EquityUniverseScanner(
        EquityUniverseConfig(
            maximum_stream_symbols=3,
            reference_symbols=("SPY",),
            active_universe_path=str(tmp_path / "active.json"),
        )
    )
    candidates = [
        EquityCandidate("GOOG", 200, 500_000_000, 0.03, 1.0, 2, "communications"),
        EquityCandidate("GOOGL", 200, 400_000_000, 0.03, 1.0, 2, "communications"),
    ]
    timestamp = datetime.now(UTC)

    result = scanner.scan(
        candidates,
        data_timestamp=timestamp,
        selection_timestamp=timestamp,
    )

    # The eligible POOL keeps both share classes; the streamed OBSERVATION
    # SET deduplicates economically identical issuers.
    alphabet = set(result.selected_symbols) & {"GOOG", "GOOGL"}
    assert len(alphabet) == 2
    observation_alphabet = set(result.observation_set) & {"GOOG", "GOOGL"}
    assert len(observation_alphabet) == 1
    rejected = ({"GOOG", "GOOGL"} - observation_alphabet).pop()
    assert result.observation_eliminated[rejected] == "duplicate_issuer"
