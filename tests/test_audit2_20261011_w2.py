"""Audit 2 (2026-10-11) workstream W2 (execution layer) regression tests.

Deterministic, focused on one invariant each, tmp_path state only.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from parayabuck.core.models import Market
from parayabuck.execution import alpaca_reconciliation
from parayabuck.execution.alpaca_models import AmbiguousOrderState
from parayabuck.execution.alpaca_reconciliation import ReconciliationResultError, ReconciliationService
from parayabuck.execution.ledger import ExecutionLedger
from parayabuck.execution.portfolio import PortfolioOrderRequest, PortfolioOrderSide, RiskOverlayDecision
from parayabuck.execution.telemetry import _open_readonly, execution_metrics, fill_delay_seconds, forward_metrics
from parayabuck.portfolio.models import TargetPortfolio
from parayabuck.portfolio.runtime_repository import PortfolioStateRepository
from parayabuck.storage import SQLiteStore
from tests.execution.test_reconciliation_repair import _State
from tests.fake_client import FakeTradingClient
from tests.helpers import make_broker
from tests.test_audit_20261011_w2 import _record_fill

_AT = datetime(2026, 9, 1, 20, 0, tzinfo=UTC)


def _record_order(repo: PortfolioStateRepository, account_id: str, client_id: str) -> None:
    session_id = repo.begin_session(account_id, f"digest-{client_id}", "paper")
    target = TargetPortfolio(
        weights=(), cash_weight=1.0, decision_timestamp=_AT, strategy_name="eq", strategy_version="1"
    )
    decision_id = repo.record_decision_with_orders(
        session_id,
        account_id,
        f"digest-{client_id}",
        target,
        RiskOverlayDecision(target, False, (), 0.0, 0.0),
        Market.EQUITY,
        (),
    )
    repo.record_order(
        PortfolioOrderRequest(
            client_order_id=client_id,
            decision_id=decision_id,
            symbol="QLD",
            market=Market.EQUITY,
            side=PortfolioOrderSide.BUY,
            quantity=2.0,
            reference_price=100.0,
            decision_timestamp=_AT,
            strategy_name="eq",
            strategy_version="1",
        ),
        "submitted",
    )


def _trade(repo: PortfolioStateRepository, client_id: str, cumulative: float, activity_id: str, **kwargs: Any) -> None:
    assert repo.apply_trade_update(
        client_order_id=client_id,
        broker_order_id=f"b-{client_id}",
        status=kwargs.pop("status", "fill"),
        cumulative_filled_quantity=cumulative,
        average_fill_price=100.0,
        occurred_at=_AT,
        activity_id=activity_id,
        realized_pnl=0.0,
        unrealized_pnl=0.0,
        **kwargs,
    )


# Q1B-1 / Q1B-T1: the fee exclusion is per fill, not per order ---------------


def test_observed_fee_survives_unobserved_recovery_event_on_same_order(tmp_path: Path) -> None:
    db = tmp_path / "runtime.sqlite3"
    store = SQLiteStore(db)
    ledger = ExecutionLedger(str(db), store=store)
    repo = PortfolioStateRepository(db, store=store)
    try:
        _record_order(repo, "acct", "c1")
        _trade(repo, "c1", 1.0, "exec-c1-1", fee=5.0, source="stream")
        assert ledger.total_fees_for_account("acct") == 5.0
        # A reconcile pass journals the same order with an unobserved fee and
        # books the remaining share as a fee-unknown recovery fill.
        _trade(
            repo,
            "c1",
            2.0,
            "c1:recovered:2.0:filled",
            fee=None,
            fee_observed=False,
            source="recovered_submission",
            status="filled",
        )
        assert ledger.total_fees_for_account("acct") == 5.0
    finally:
        ledger.close()


# Q1B-2 / Q1B-T1: duplicate identity-less BUYs fail closed, never halt -------


def _manual_buy(broker_id: str, symbol: str = "SPY") -> SimpleNamespace:
    return SimpleNamespace(
        id=broker_id,
        client_order_id="",
        symbol=symbol,
        side="buy",
        qty="5",
        filled_qty="0",
        filled_avg_price=None,
        notional=None,
        limit_price="100",
        stop_price=None,
    )


def test_identity_less_working_buys_get_unique_ids(tmp_path: Path) -> None:
    broker = make_broker(tmp_path)
    broker._get_open_orders = lambda _request: (_manual_buy("b-1"), _manual_buy("b-2"), _manual_buy(""))  # type: ignore[method-assign]
    ids = [order.client_order_id for order in broker.working_buy_orders()]
    assert len(ids) == len(set(ids)) == 3
    assert ids[:2] == ["foreign:SPY:b-1", "foreign:SPY:b-2"]


def test_duplicate_foreign_spy_buys_block_admission_as_unpriced(tmp_path: Path) -> None:
    from tests.test_buy_admission_correctness import _admission_runtime, _buy

    alpaca = make_broker(tmp_path)
    alpaca._get_open_orders = lambda _request: (_manual_buy("b-1"), _manual_buy("b-2"))  # type: ignore[method-assign]
    sim, runtime = _admission_runtime(tmp_path)
    sim.working_buy_orders = alpaca.working_buy_orders  # type: ignore[attr-defined]
    admission = runtime._size_buys_to_safe_capacity((_buy(),))
    assert admission.orders == ()
    assert admission.reason == "committed_buy_unpriced"


# Q1B-3 / Q2B-1 / Q2B-2: hostile position facts at the snapshot source -------


def _account_broker(tmp_path: Path, fake: FakeTradingClient, owned: dict[str, float]) -> Any:
    broker = make_broker(tmp_path, client=fake, portfolio_owned_quantities=lambda: owned, portfolio_open_orders=list)
    broker.bot_tradable_symbols = lambda: {"QLD"}
    return broker


@pytest.mark.parametrize("field", ["unrealized_pl", "unrealized_intraday_pl"])
def test_nonfinite_position_pnl_fails_closed(tmp_path: Path, field: str) -> None:
    fake = FakeTradingClient()
    fake.set_position("QLD", qty=10)
    setattr(fake.positions[0], field, "nan")
    with pytest.raises(ValueError, match="non-finite"):
        _account_broker(tmp_path, fake, {"QLD": 10.0}).account()


@pytest.mark.parametrize("value", ["nan", "inf", "abc"])
def test_nonfinite_bot_position_mark_fails_closed(tmp_path: Path, value: str) -> None:
    fake = FakeTradingClient()
    fake.set_position("QLD", qty=10)
    fake.positions[0].current_price = value
    with pytest.raises(ValueError, match="non-finite price"):
        _account_broker(tmp_path, fake, {"QLD": 10.0}).account()


def test_nonfinite_foreign_position_mark_reads_zero(tmp_path: Path) -> None:
    fake = FakeTradingClient()
    fake.set_position("XYZ", qty=3)
    fake.positions[0].current_price = "nan"
    (position,) = _account_broker(tmp_path, fake, {}).account().positions
    assert position.current_price == 0.0


def test_negative_qty_available_falls_back_to_full_quantity(tmp_path: Path) -> None:
    fake = FakeTradingClient()
    fake.set_position("QLD", qty=10, qty_available=-3.0)
    (position,) = _account_broker(tmp_path, fake, {"QLD": 10.0}).account().positions
    assert position.qty_available is None


# Q1B-4: an operator peak reset waits for the account() peak lock ------------


def test_reset_peak_equity_takes_the_peak_lock(tmp_path: Path) -> None:
    broker = make_broker(tmp_path)
    broker._account_id = "acct"
    worker = threading.Thread(target=broker.reset_peak_equity, args=(90_000.0,))
    with broker._peak_lock:
        worker.start()
        worker.join(timeout=0.2)
        assert worker.is_alive()
    worker.join(timeout=5)
    assert not worker.is_alive()
    assert broker._peak_equity == 90_000.0


# Q3B-3 / Q3B-4: unparseable REST fill numerics fail closed -------------------


@pytest.mark.parametrize(("field", "value"), [("filled_qty", "abc"), ("filled_avg_price", "xyz")])
def test_unparseable_fill_facts_block_reconciliation(field: str, value: str) -> None:
    broker = _State().broker()
    setattr(broker._fetch_broker_order("intent-1", "broker-1"), field, value)
    with pytest.raises(ReconciliationResultError, match="Unparseable broker fill facts"):
        ReconciliationService(broker).reconcile()


@pytest.mark.parametrize(
    ("field", "value"), [("filled_qty", "abc"), ("filled_avg_price", "xyz"), ("filled_qty", "nan")]
)
def test_unparseable_fill_facts_extract_no_fill(tmp_path: Path, field: str, value: str) -> None:
    order = SimpleNamespace(id="b-1", filled_qty="2", filled_avg_price="100", filled_at=_AT)
    setattr(order, field, value)
    assert make_broker(tmp_path)._fill_from_broker_order(order, "c1", "b-1") is None


def test_unparseable_fill_on_dead_recovered_order_is_ambiguous(tmp_path: Path) -> None:
    def submit(_request: Any) -> Any:
        raise TimeoutError("submit timed out")

    recovered = SimpleNamespace(id="b-1", client_order_id="c1", status="canceled", filled_qty="abc")
    broker = make_broker(
        tmp_path, client=SimpleNamespace(submit_order=submit, get_order_by_client_id=lambda _cid: recovered)
    )
    with pytest.raises(AmbiguousOrderState, match="unreadable filled_qty"):
        broker._submit_with_recovery(object(), "c1")


# Q5E-2: an unknown broker status skips that order, the pass completes -------


def test_unrecognized_broker_status_flags_order_without_aborting_pass(monkeypatch: pytest.MonkeyPatch) -> None:
    alerts: list[dict[str, Any]] = []
    monkeypatch.setattr(
        alpaca_reconciliation,
        "emit_execution_alert",
        lambda kind, **fields: alerts.append({"kind": kind, **fields}),
    )
    state = _State(broker_status="brand_new_state", broker_qty=0.0)
    report = ReconciliationService(state.broker()).reconcile()
    assert state.recoveries == 0
    assert state.local_status == "submitted"
    # The untouched row still surfaces as drift, so nothing trades on it.
    assert report["MISSING_BROKER_ORDER"] == ["intent-1"]
    assert [alert["category"] for alert in alerts] == ["unrecognized_broker_order_status"]


# Q5B-1: reports scoped to one account ----------------------------------------


def test_forward_and_execution_metrics_scope_to_one_account(tmp_path: Path) -> None:
    db = tmp_path / "runtime.sqlite3"
    store = SQLiteStore(db)
    ledger = ExecutionLedger(str(db), store=store)
    repo = PortfolioStateRepository(db, store=store)
    try:
        _record_fill(repo, "ACCT_A", "pb-a", 5.0)
        _record_fill(repo, "ACCT_B", "pb-b", 7.0)
        with store.lock, store.connection:
            for account_id, day_start in (("ACCT_A", 10_000.0), ("ACCT_B", 20_000.0)):
                store.connection.execute(
                    "INSERT INTO risk_state(account_id, session_date, payload, updated_at) VALUES (?, ?, ?, ?)",
                    (account_id, "2026-09-01", json.dumps({"day_start_equity": day_start}), _AT.isoformat()),
                )
    finally:
        ledger.close()
    assert forward_metrics(db)["portfolio"]["pnl"]["fees"] == 12.0
    scoped = forward_metrics(db, account_id="ACCT_A")
    assert scoped["portfolio"]["pnl"]["fees"] == 5.0
    assert scoped["portfolio"]["equity_estimate"] == 10_000.0 - 5.0
    assert scoped["strategy"]["decisions"]["count"] == 1
    assert scoped["strategy"]["rebalances"]["orders_planned"] == 1
    assert execution_metrics(db, account_id="ACCT_B")["fees"]["total"] == 7.0


# Q5C-1 / Q5C-2: mixed naive/aware stamps never crash the reports ------------


def test_fill_delay_seconds_mixed_naive_and_aware() -> None:
    assert fill_delay_seconds("2026-09-01T20:00:05+00:00", "2026-09-01T20:00:00") == 5.0
    assert fill_delay_seconds("2026-09-01T20:00:05", "2026-09-01T20:00:00+00:00") == 5.0


def test_execution_metrics_over_naive_order_created_at(tmp_path: Path) -> None:
    db = tmp_path / "runtime.sqlite3"
    store = SQLiteStore(db)
    ledger = ExecutionLedger(str(db), store=store)
    repo = PortfolioStateRepository(db, store=store)
    try:
        _record_fill(repo, "acct", "pb-naive", 0.25)
        with store.lock, store.connection:
            store.connection.execute("UPDATE portfolio_orders SET created_at = '2026-09-01T19:59:50'")
            store.connection.execute("UPDATE portfolio_fills SET fill_delay_seconds = NULL")
    finally:
        ledger.close()
    assert execution_metrics(db)["fill_latency"]["mean"] == 10.0


# Q4B-5: one shared read-only opener ------------------------------------------


def test_open_readonly_missing_contract(tmp_path: Path) -> None:
    missing = tmp_path / "absent.sqlite3"
    assert _open_readonly(missing) is None
    with pytest.raises(ValueError, match="not a regular file"):
        _open_readonly(missing, missing_ok=False)
    sqlite3.connect(tmp_path / "present.sqlite3").close()
    connection = _open_readonly(tmp_path / "present.sqlite3", missing_ok=False)
    connection.close()
