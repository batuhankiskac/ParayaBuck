"""Realized P&L booked FIFO on the ledger, audit parity, and empty-position guard."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from parayabuck.core.models import Market
from parayabuck.execution.alpaca_account import flat_broker_reconcile_scope
from parayabuck.execution.alpaca_models import OwnershipDriftError
from parayabuck.execution.ledger import ExecutionLedger
from parayabuck.execution.portfolio import PortfolioOrderRequest, PortfolioOrderSide, RiskOverlayDecision
from parayabuck.monitoring.paper_incident import BrokerFillRecord, _fill_accounting
from parayabuck.portfolio import runtime_repository
from parayabuck.portfolio.models import TargetPortfolio
from parayabuck.portfolio.runtime_repository import PortfolioStateRepository
from tests.fake_client import FakeTradingClient
from tests.helpers import make_broker

T0 = datetime(2025, 1, 1, tzinfo=UTC)


def _at(minutes: int) -> datetime:
    return T0 + timedelta(minutes=minutes)


def _target(at: datetime) -> TargetPortfolio:
    return TargetPortfolio(
        weights=(),
        cash_weight=1.0,
        decision_timestamp=at,
        strategy_name="eq",
        strategy_version="1",
    )


def _book(
    repo: PortfolioStateRepository,
    session_id: str,
    *,
    client_order_id: str,
    side: str,
    quantity: float,
    price: float,
    fee: float,
    at: datetime,
    symbol: str = "AAPL",
) -> bool:
    """Record one fully filled order and book its fill through the repository."""
    decision_id = repo.record_decision_with_orders(
        session_id,
        "acct",
        "digest",
        _target(at),
        RiskOverlayDecision(_target(at), False, (), 0.0, 0.0),
        Market.EQUITY,
        (),
    )
    repo.record_order(
        PortfolioOrderRequest(
            client_order_id=client_order_id,
            decision_id=decision_id,
            symbol=symbol,
            market=Market.EQUITY,
            side=PortfolioOrderSide(side),
            quantity=quantity,
            reference_price=price,
            decision_timestamp=at,
            strategy_name="eq",
            strategy_version="1",
        ),
        "submitting",
    )
    return repo.apply_trade_update(
        client_order_id=client_order_id,
        broker_order_id=f"broker-{client_order_id}",
        status="fill",
        cumulative_filled_quantity=quantity,
        average_fill_price=price,
        occurred_at=at,
        activity_id=f"activity-{client_order_id}",
        fee=fee,
        realized_pnl=0.0,
        unrealized_pnl=0.0,
    )


def _booked_realized(repo: PortfolioStateRepository, client_order_id: str) -> float:
    row = repo.store.connection.execute(
        "SELECT realized_pnl FROM portfolio_fills WHERE activity_id = ?",
        (f"activity-{client_order_id}",),
    ).fetchone()
    assert row is not None
    return float(row[0])


class _LogRecorder:
    def __init__(self) -> None:
        self.warnings: list[str] = []

    def warning(self, message: str, *args: object) -> None:
        self.warnings.append(message % args)


# --- FIFO realized P&L on the ledger ----------------------------------------


def test_fifo_realized_pnl_buy_buy_sell_with_fees(tmp_path: Path) -> None:
    repo = PortfolioStateRepository(tmp_path / "runtime.sqlite3")
    session = repo.begin_session("acct", "digest", "paper")
    assert _book(repo, session, client_order_id="b1", side="buy", quantity=10, price=10, fee=1, at=_at(1))
    assert _book(repo, session, client_order_id="b2", side="buy", quantity=10, price=20, fee=2, at=_at(2))

    # Lots are 10 @ 10.1 (fee-inclusive) and 10 @ 20.2. Sale of 15 @ 30, fee 3:
    # proceeds 450 - 3 = 447; consumed cost 10 * 10.1 + 5 * 20.2 = 202.
    assert repo.fifo_realized_pnl("acct", "AAPL", Market.EQUITY, 15, 30, 3) == pytest.approx(245.0)

    assert _book(repo, session, client_order_id="s1", side="sell", quantity=15, price=30, fee=3, at=_at(3))
    assert _booked_realized(repo, "s1") == pytest.approx(245.0)
    # Buys are never booked with a realized value.
    assert _booked_realized(repo, "b1") == 0.0
    assert _booked_realized(repo, "b2") == 0.0


def test_fifo_realized_pnl_partial_lot_consumption(tmp_path: Path) -> None:
    repo = PortfolioStateRepository(tmp_path / "runtime.sqlite3")
    session = repo.begin_session("acct", "digest", "paper")
    assert _book(repo, session, client_order_id="b1", side="buy", quantity=10, price=10, fee=0, at=_at(1))
    assert _book(repo, session, client_order_id="b2", side="buy", quantity=10, price=20, fee=0, at=_at(2))

    # Four shares leave the oldest lot only: 4 * 30 - 4 * 10 = 80.
    assert repo.fifo_realized_pnl("acct", "AAPL", Market.EQUITY, 4, 30, 0) == pytest.approx(80.0)

    assert _book(repo, session, client_order_id="s1", side="sell", quantity=4, price=30, fee=0, at=_at(3))
    assert repo.owned_fifo_lots("acct", "AAPL", Market.EQUITY) == ((6.0, 10.0), (10.0, 20.0))


def test_fifo_realized_pnl_is_zero_for_non_sale_inputs(tmp_path: Path) -> None:
    repo = PortfolioStateRepository(tmp_path / "runtime.sqlite3")
    session = repo.begin_session("acct", "digest", "paper")
    assert _book(repo, session, client_order_id="b1", side="buy", quantity=10, price=10, fee=0, at=_at(1))

    assert repo.fifo_realized_pnl("acct", "AAPL", Market.EQUITY, 0, 30, 0) == 0.0
    assert repo.fifo_realized_pnl("acct", "AAPL", Market.EQUITY, 5, float("nan"), 0) == 0.0


def test_inconsistent_lot_book_returns_zero_without_raising(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = PortfolioStateRepository(tmp_path / "runtime.sqlite3")
    session = repo.begin_session("acct", "digest", "paper")
    assert _book(repo, session, client_order_id="b1", side="buy", quantity=10, price=10, fee=0, at=_at(1))
    logs = _LogRecorder()
    monkeypatch.setattr(runtime_repository, "log", logs)

    # Selling 12 against 10 open shares cannot come from a consistent book.
    assert repo.fifo_realized_pnl("acct", "AAPL", Market.EQUITY, 12, 30, 0) == 0.0
    assert len(logs.warnings) == 1
    assert "booked as 0.0" in logs.warnings[0]


def test_oversold_fill_is_still_booked_with_zero_realized(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = PortfolioStateRepository(tmp_path / "runtime.sqlite3")
    session = repo.begin_session("acct", "digest", "paper")
    assert _book(repo, session, client_order_id="b1", side="buy", quantity=10, price=10, fee=0, at=_at(1))
    monkeypatch.setattr(runtime_repository, "log", _LogRecorder())

    assert _book(repo, session, client_order_id="s1", side="sell", quantity=12, price=30, fee=0, at=_at(2))
    assert _booked_realized(repo, "s1") == 0.0


def test_booking_site_stores_fifo_value_for_sells(tmp_path: Path) -> None:
    repo = PortfolioStateRepository(tmp_path / "runtime.sqlite3")
    session = repo.begin_session("acct", "digest", "paper")
    assert _book(repo, session, client_order_id="b1", side="buy", quantity=10, price=100, fee=0, at=_at(1))
    # Booking sites pass realized_pnl=0.0; the repository stores the FIFO value.
    assert _book(repo, session, client_order_id="s1", side="sell", quantity=10, price=110, fee=1, at=_at(2))
    assert _booked_realized(repo, "s1") == pytest.approx(99.0)


# --- paper_incident audit agrees with the ledger -----------------------------


def test_paper_incident_fill_accounting_is_fifo(tmp_path: Path) -> None:
    fills = (
        BrokerFillRecord("a1", "o1", "AAPL", "buy", 10.0, 10.0, 0.0, _at(1)),
        BrokerFillRecord("a2", "o2", "AAPL", "buy", 10.0, 20.0, 0.0, _at(2)),
        BrokerFillRecord("a3", "o3", "AAPL", "sell", 15.0, 30.0, 0.0, _at(3)),
    )

    realized, _ = _fill_accounting(fills, {}, {}, (), None)

    # FIFO: 10 * (30 - 10) + 5 * (30 - 20) = 250. Average cost would give 225.
    assert realized == pytest.approx(250.0)


def test_paper_incident_fill_accounting_matches_ledger_fifo(tmp_path: Path) -> None:
    repo = PortfolioStateRepository(tmp_path / "runtime.sqlite3")
    session = repo.begin_session("acct", "digest", "paper")
    steps = [
        ("b1", "buy", 10.0, 10.0, _at(1)),
        ("b2", "buy", 10.0, 20.0, _at(2)),
        ("s1", "sell", 15.0, 30.0, _at(3)),
    ]
    for client_order_id, side, quantity, price, at in steps:
        assert _book(
            repo, session, client_order_id=client_order_id, side=side, quantity=quantity, price=price, fee=0, at=at
        )
    ledger_realized = sum(
        float(row[0]) for row in repo.store.connection.execute("SELECT realized_pnl FROM portfolio_fills").fetchall()
    )

    audit_fills = tuple(
        BrokerFillRecord(f"activity-{cid}", cid, "AAPL", side, quantity, price, 0.0, at)
        for cid, side, quantity, price, at in steps
    )
    audit_realized, _ = _fill_accounting(audit_fills, {}, {}, (), None)

    assert audit_realized == pytest.approx(ledger_realized)


# --- empty broker positions are not flat -------------------------------------


def _owned_broker(tmp_path: Path, client: FakeTradingClient, owned: dict[str, float]) -> Any:
    return make_broker(
        tmp_path,
        client=client,
        peak_state_path=tmp_path / "peak.json",
        portfolio_owned_quantities=lambda: owned,
        portfolio_open_orders=lambda: [],
    )


def test_empty_positions_with_owned_quantity_fail_closed_after_one_retry(tmp_path: Path) -> None:
    fake = FakeTradingClient()
    reads: list[int] = []

    def empty_positions() -> list[Any]:
        reads.append(1)
        return []

    fake.script_get_all_positions = empty_positions
    broker = _owned_broker(tmp_path, fake, {"AAPL": 5.0})

    with pytest.raises(OwnershipDriftError, match="AAPL"):
        broker.account()
    assert len(reads) == 2, "exactly one re-read before failing closed"


def test_empty_positions_recovered_on_retry_are_accepted(tmp_path: Path) -> None:
    fake = FakeTradingClient()
    held = SimpleNamespace(
        symbol="AAPL",
        qty="5",
        avg_entry_price="100",
        current_price="101",
        unrealized_pl="5",
        unrealized_intraday_pl="1",
        asset_class="us_equity",
        qty_available="5",
    )
    responses: list[Any] = [None, [held]]
    reads: list[int] = []

    def flaky_positions() -> Any:
        reads.append(1)
        return responses[len(reads) - 1]

    fake.script_get_all_positions = flaky_positions
    broker = _owned_broker(tmp_path, fake, {"AAPL": 5.0})

    snapshot = broker.account()
    assert len(reads) == 2
    assert [position.symbol for position in snapshot.positions] == ["AAPL"]


def test_empty_positions_with_nothing_owned_succeed(tmp_path: Path) -> None:
    fake = FakeTradingClient()
    reads: list[int] = []

    def empty_positions() -> list[Any]:
        reads.append(1)
        return []

    fake.script_get_all_positions = empty_positions
    broker = _owned_broker(tmp_path, fake, {})

    assert broker.account().positions == ()
    assert len(reads) == 1, "a genuinely flat account is not re-read"


def test_none_positions_with_nothing_owned_succeed(tmp_path: Path) -> None:
    fake = FakeTradingClient()
    fake.script_get_all_positions = lambda: None
    broker = _owned_broker(tmp_path, fake, {"AAPL": 0.0})

    assert broker.account().positions == ()


def test_reconcile_scope_accepts_empty_positions_while_owning(tmp_path: Path) -> None:
    fake = FakeTradingClient()
    reads: list[int] = []

    def empty_positions() -> list[Any]:
        reads.append(1)
        return []

    fake.script_get_all_positions = empty_positions
    broker = _owned_broker(tmp_path, fake, {"AAPL": 5.0})

    with flat_broker_reconcile_scope():
        assert broker.account().positions == ()
    assert len(reads) == 1
    with pytest.raises(OwnershipDriftError):
        broker.account()  # the scope ends with the reconcile call


def test_reconcile_still_clears_stale_ownership_on_flat_broker(tmp_path: Path) -> None:
    repo = PortfolioStateRepository(tmp_path / "runtime.sqlite3")
    session = repo.begin_session("acct", "digest", "paper")
    assert _book(repo, session, client_order_id="b1", side="buy", quantity=0.5, price=100, fee=0, at=_at(1))
    assert repo.owned_quantities("acct") == {"AAPL": 0.5}

    client = FakeTradingClient()  # flat broker: no positions, no open orders
    broker = make_broker(
        tmp_path,
        client=client,
        ledger=ExecutionLedger(str(tmp_path / "execution.sqlite3")),
        peak_state_path=tmp_path / "peak.json",
        portfolio_owned_quantities=lambda: repo.owned_quantities("acct"),
        portfolio_open_orders=lambda: repo.open_orders("acct"),
    )
    broker.portfolio_flat_reconciled = lambda symbol, market: repo.clear_stale_ownership("acct", symbol, market)

    # A normal snapshot refuses the flat read while the ledger still owns shares.
    with pytest.raises(OwnershipDriftError):
        broker.account()

    broker.reconcile()

    assert repo.owned_quantities("acct") == {}
    with flat_broker_reconcile_scope():
        assert broker.account().positions == ()
