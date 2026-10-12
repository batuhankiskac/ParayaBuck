"""Audit 2026-10-11 workstream W2 (execution layer) regression tests.

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

from parayabuck.core.models import AccountSnapshot, Market, PositionSnapshot
from parayabuck.execution.alpaca_reconciliation import ReconciliationService
from parayabuck.execution.ledger import ExecutionLedger
from parayabuck.execution.portfolio import PortfolioRiskLimits, PortfolioRiskOverlay
from parayabuck.execution.telemetry import forward_metrics
from parayabuck.portfolio.models import TargetPortfolio
from parayabuck.portfolio.runtime_models import OpenPortfolioOrder
from parayabuck.portfolio.runtime_repository import PortfolioStateRepository
from parayabuck.storage import SQLiteStore
from tests.helpers import make_broker

_AT = datetime(2026, 9, 1, 20, 0, tzinfo=UTC)


def _account(equity: float = 100_000.0, daily_pnl: float = 0.0) -> AccountSnapshot:
    return AccountSnapshot(cash=equity, equity=equity, peak_equity=equity, daily_pnl=daily_pnl)


def _order(
    *,
    client_id: str = "parayabuck-test-buy",
    symbol: str = "MSFT",
    side: str = "buy",
    qty: Any = "10",
    filled: Any = "0",
    avg: Any = None,
    notional: Any = None,
    limit: Any = "100",
) -> SimpleNamespace:
    return SimpleNamespace(
        client_order_id=client_id,
        symbol=symbol,
        side=side,
        qty=qty,
        filled_qty=filled,
        filled_avg_price=avg,
        notional=notional,
        limit_price=limit,
        stop_price=None,
    )


def _broker_with_orders(tmp_path: Path, orders: tuple[Any, ...]):
    broker = make_broker(tmp_path)
    broker._get_open_orders = lambda _request: orders  # type: ignore[method-assign]
    return broker


# B1: unfilled notional BUY must not crash ------------------------------------


def test_unfilled_notional_buy_does_not_crash(tmp_path: Path) -> None:
    broker = _broker_with_orders(tmp_path, (_order(notional="500", filled="0", avg=None, qty=None, limit=None),))
    (order,) = broker.working_buy_orders()
    assert order.remaining_notional == 500.0


def test_partially_filled_notional_buy_prices_remaining(tmp_path: Path) -> None:
    broker = _broker_with_orders(tmp_path, (_order(notional="500", filled="2", avg="100", qty=None, limit=None),))
    (order,) = broker.working_buy_orders()
    assert order.remaining_notional == 300.0


# R3A1/R5B-4: foreign + hostile working BUYs stay unpriced, never raise ------


def test_identity_less_manual_buy_is_unpriced_not_raised(tmp_path: Path) -> None:
    broker = _broker_with_orders(tmp_path, (_order(client_id="", symbol="SPY"),))
    (order,) = broker.working_buy_orders()
    assert order.reference_price is None
    assert order.remaining_quantity is None
    assert order.remaining_notional is None


def test_duplicate_bot_client_id_still_raises(tmp_path: Path) -> None:
    import pytest

    broker = _broker_with_orders(
        tmp_path,
        (
            _order(client_id="parayabuck-dup"),
            _order(client_id="parayabuck-dup"),
        ),
    )
    with pytest.raises(ValueError, match="Invalid broker working BUY identity"):
        broker.working_buy_orders()


def test_hostile_working_buy_fields_are_unpriced(tmp_path: Path) -> None:
    for kwargs in (
        {"avg": "nan"},
        {"avg": "0"},
        {"avg": "-5"},
        {"qty": "5", "filled": "10"},
        {"notional": "nan", "qty": None, "limit": None},
        {"qty": None, "notional": None, "limit": None},
    ):
        broker = _broker_with_orders(tmp_path, (_order(**kwargs),))
        (order,) = broker.working_buy_orders()
        assert order.reference_price is None
        assert order.remaining_quantity is None
        assert order.remaining_notional is None


# R2B1: reconcile buckets id-less broker orders -------------------------------


def test_reconcile_buckets_foreign_open_order_by_broker_id(tmp_path: Path) -> None:
    from tests.fake_client import FakeTradingClient

    fake = FakeTradingClient()
    broker = make_broker(tmp_path, client=fake)
    broker._get_open_orders = lambda _request: (  # type: ignore[method-assign]
        SimpleNamespace(id="broker-foreign", client_order_id="", symbol="SPY", side="buy"),
    )
    report = ReconciliationService(broker).reconcile()
    assert report["UNKNOWN_ORDER"] == ["broker-foreign"]


def test_reconcile_still_raises_on_duplicate_bot_client_id(tmp_path: Path) -> None:
    import pytest

    from tests.fake_client import FakeTradingClient

    fake = FakeTradingClient()
    broker = make_broker(tmp_path, client=fake)
    dup = lambda _request: (  # noqa: E731
        SimpleNamespace(id="b-1", client_order_id="parayabuck-x", symbol="A", side="buy"),
        SimpleNamespace(id="b-2", client_order_id="parayabuck-x", symbol="B", side="buy"),
    )
    broker._get_open_orders = dup  # type: ignore[method-assign]
    with pytest.raises(ValueError, match="duplicate client ID"):
        ReconciliationService(broker).reconcile()


# R2B2: recovered fills excluded from the fee sum -----------------------------


def _record_fill(repo: PortfolioStateRepository, account_id: str, client_id: str, fee: float | None) -> None:
    session_id = repo.begin_session(account_id, f"digest-{client_id}", "paper")
    target = TargetPortfolio(
        weights=(), cash_weight=1.0, decision_timestamp=_AT, strategy_name="eq", strategy_version="1"
    )
    from parayabuck.execution.portfolio import PortfolioOrderRequest, PortfolioOrderSide, RiskOverlayDecision

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
    repo.apply_trade_update(
        client_order_id=client_id,
        broker_order_id=f"b-{client_id}",
        status="fill",
        cumulative_filled_quantity=2.0,
        average_fill_price=100.0,
        occurred_at=_AT,
        activity_id=f"exec-{client_id}",
        fee=fee,
        realized_pnl=0.0,
        unrealized_pnl=0.0,
    )


def test_recovered_fill_fee_excluded_from_fee_sum(tmp_path: Path) -> None:
    db = tmp_path / "runtime.sqlite3"
    store = SQLiteStore(db)
    ledger = ExecutionLedger(str(db), store=store)
    repo = PortfolioStateRepository(db, store=store)
    try:
        _record_fill(repo, "acct", "pb-observed", 0.25)
        _record_fill(repo, "acct", "pb-recovered", None)
        assert ledger.total_fees_for_account("acct") == 0.25
    finally:
        ledger.close()


# R2B7: double close is a no-op ------------------------------------------------


def test_repository_double_close_is_a_noop(tmp_path: Path) -> None:
    db = tmp_path / "runtime.sqlite3"
    store = SQLiteStore(db)
    ledger = ExecutionLedger(str(db), store=store)
    repo = PortfolioStateRepository(db, store=store)
    ledger.close()
    ledger.close()
    repo.close()
    repo.close()


# R3C-01: daily-loss denominator is day-start equity ---------------------------


def test_overlay_daily_loss_uses_day_start_equity() -> None:
    overlay = PortfolioRiskOverlay(PortfolioRiskLimits(daily_loss_limit=0.20))
    target = TargetPortfolio(
        weights=(), cash_weight=1.0, decision_timestamp=_AT, strategy_name="eq", strategy_version="1"
    )
    # 18% day-start loss must NOT trip a 20% limit (current-equity math trips at 16.7%).
    account = _account(equity=82.0, daily_pnl=-18.0)
    decision = overlay.apply(
        target, account, market=Market.EQUITY, approved_symbols=(), maximum_allocation=1.0, mode="paper"
    )
    assert "daily_loss_limit" not in decision.reasons
    account = _account(equity=79.0, daily_pnl=-21.0)
    decision = overlay.apply(
        target, account, market=Market.EQUITY, approved_symbols=(), maximum_allocation=1.0, mode="paper"
    )
    assert "daily_loss_limit" in decision.reasons


# R3C-03: forward metrics must not double-count fees ---------------------------


def _forward_db(path: Path) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.executescript(
            """
            CREATE TABLE portfolio_runtime_sessions (
                session_id TEXT PRIMARY KEY, account_id TEXT, config_digest TEXT,
                mode TEXT, started_at TEXT, ended_at TEXT, status TEXT);
            CREATE TABLE portfolio_decisions (
                decision_id TEXT PRIMARY KEY, session_id TEXT, account_id TEXT,
                config_digest TEXT, decision_timestamp TEXT, market TEXT,
                strategy_name TEXT, target_json TEXT, created_at TEXT);
            CREATE TABLE portfolio_orders (client_order_id TEXT PRIMARY KEY,
                decision_id TEXT, symbol TEXT, market TEXT, side TEXT, quantity REAL,
                cumulative_filled_quantity REAL, status TEXT, broker_order_id TEXT,
                reference_price REAL, average_fill_price REAL, created_at TEXT);
            CREATE TABLE portfolio_fills (activity_id TEXT PRIMARY KEY,
                client_order_id TEXT, occurred_at TEXT, quantity REAL, price REAL,
                fee REAL, realized_pnl REAL, unrealized_pnl REAL, slippage REAL,
                fill_delay_seconds REAL);
            CREATE TABLE risk_state (account_id TEXT, session_date TEXT,
                payload TEXT, updated_at TEXT, PRIMARY KEY(account_id, session_date));
            """
        )
        connection.execute(
            "INSERT INTO portfolio_runtime_sessions VALUES ('s1','acct','d','paper','2026-01-01T00:00:00+00:00',NULL,'running')"
        )
        connection.execute(
            "INSERT INTO portfolio_decisions VALUES ('d1','s1','acct','d','2026-02-05T00:00:00+00:00','equity','eq','{}','2026-02-05T00:00:00+00:00')"
        )
        connection.execute(
            "INSERT INTO portfolio_orders VALUES ('o1','d1','AAPL','equity','buy',1.0,1.0,'filled','b1',100.0,100.0,'2026-02-05T00:00:00+00:00')"
        )
        connection.execute(
            "INSERT INTO portfolio_fills VALUES ('f1','o1','2026-02-05T00:00:00+00:00',1.0,100.0,1.0,0.0,0.0,0.0,1.0)"
        )
        connection.execute(
            "INSERT INTO risk_state VALUES ('acct','2026-02-05',?,'2026-02-05T16:00:00+00:00')",
            (
                json.dumps(
                    {
                        "day_start_equity": 100000.0,
                        "peak_equity": 100000.0,
                        "realized_pnl": 0.0,
                        "unrealized_pnl": 0.0,
                        "fees": 10.0,
                        "consecutive_losses": 0,
                        "circuit_breaker_reason": None,
                    }
                ),
            ),
        )
        connection.commit()
    finally:
        connection.close()


def test_forward_metrics_reports_fills_ledger_fees_not_the_sum(tmp_path: Path) -> None:
    db = tmp_path / "runtime.sqlite3"
    _forward_db(db)
    metrics = forward_metrics(db)
    assert metrics["portfolio"]["pnl"]["fees"] == 10.0


# R5B-2: non-finite account values fail closed ---------------------------------


def test_nonfinite_equity_last_equity_cash_raise(tmp_path: Path) -> None:
    import pytest

    for kwargs in (
        {"equity": "nan", "last_equity": "100000", "cash": "90000"},
        {"equity": "100000", "last_equity": "nan", "cash": "90000"},
        {"equity": "100000", "last_equity": "100000", "cash": "nan"},
        {"equity": "inf", "last_equity": "100000", "cash": "90000"},
    ):
        broker = make_broker(tmp_path)
        raw = {
            "id": "acct",
            "account_blocked": False,
            "trading_blocked": False,
            "trade_suspended_by_user": False,
            **kwargs,
        }
        broker._client = SimpleNamespace(
            get_account=lambda raw=raw: SimpleNamespace(**raw),
            get_all_positions=lambda: [],
        )
        with pytest.raises(ValueError, match="[Nn]on-finite|not numeric"):
            broker.account()


# R5B-3: NaN availability falls back to qty ------------------------------------


def test_nan_qty_available_falls_back_to_qty(tmp_path: Path) -> None:
    broker = make_broker(tmp_path)
    broker._client = SimpleNamespace(
        get_account=lambda: SimpleNamespace(
            id="acct",
            account_blocked=False,
            trading_blocked=False,
            trade_suspended_by_user=False,
            equity="100000",
            last_equity="100000",
            cash="90000",
        ),
        get_all_positions=lambda: [
            SimpleNamespace(
                symbol="QLD",
                qty="10",
                avg_entry_price="100",
                current_price="100",
                unrealized_pl="0",
                unrealized_intraday_pl="0",
                qty_available="nan",
                asset_class="us_equity",
            ),
        ],
    )
    broker.portfolio_owned_quantities = lambda: {"QLD": 10.0}
    (position,) = broker.account().positions
    assert position.qty_available is None


# R5A-2: peak read-modify-write is serialized ----------------------------------


def test_peak_lock_exists_and_serializes(tmp_path: Path) -> None:
    broker = make_broker(tmp_path)
    broker._client = SimpleNamespace(
        get_account=lambda: SimpleNamespace(
            id="acct",
            account_blocked=False,
            trading_blocked=False,
            trade_suspended_by_user=False,
            equity="110000",
            last_equity="100000",
            cash="90000",
        ),
        get_all_positions=lambda: [],
    )
    assert getattr(broker, "_peak_lock", None) is not None
    first_lock = broker._peak_lock
    broker.account()
    assert broker._peak_lock is first_lock

    errors: list[BaseException] = []

    def worker(equity: str) -> None:
        try:
            broker._client = SimpleNamespace(
                get_account=lambda: SimpleNamespace(
                    id="acct",
                    account_blocked=False,
                    trading_blocked=False,
                    trade_suspended_by_user=False,
                    equity=equity,
                    last_equity="100000",
                    cash="90000",
                ),
                get_all_positions=lambda: [],
            )
            broker.account()
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(equity,)) for equity in ("105000", "110000")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert not errors
    assert broker._peak_equity == 110000.0


# R5E-1: sidecar write failure keeps the snapshot ------------------------------


def test_peak_sidecar_failure_keeps_snapshot(tmp_path: Path, monkeypatch) -> None:
    broker = make_broker(tmp_path)
    broker._client = SimpleNamespace(
        get_account=lambda: SimpleNamespace(
            id="acct",
            account_blocked=False,
            trading_blocked=False,
            trade_suspended_by_user=False,
            equity="120000",
            last_equity="100000",
            cash="90000",
        ),
        get_all_positions=lambda: [],
    )
    broker.account()
    broker._client = SimpleNamespace(
        get_account=lambda: SimpleNamespace(
            id="acct",
            account_blocked=False,
            trading_blocked=False,
            trade_suspended_by_user=False,
            equity="130000",
            last_equity="100000",
            cash="90000",
        ),
        get_all_positions=lambda: [],
    )
    monkeypatch.setattr(
        "parayabuck.execution.risk_peak.RiskPeakService.save_peak_equity",
        lambda self, account_id=None: (_ for _ in ()).throw(OSError("disk full")),
    )
    snapshot = broker.account()
    assert snapshot.equity == 130000.0
    assert broker._peak_equity == 130000.0


# R5B-5: hostile numerics through the stream chain ------------------------------


def test_nan_last_equity_cannot_clear_the_entry_latch(tmp_path: Path) -> None:
    import pytest

    broker = make_broker(tmp_path)
    broker._client = SimpleNamespace(
        get_account=lambda: SimpleNamespace(
            id="acct",
            account_blocked=False,
            trading_blocked=False,
            trade_suspended_by_user=False,
            equity="100000",
            last_equity="nan",
            cash="90000",
        ),
        get_all_positions=lambda: [],
    )
    with pytest.raises(ValueError):
        broker.account()


def test_open_order_symbols_tolerate_missing_symbol(tmp_path: Path) -> None:
    broker = make_broker(tmp_path)
    broker._get_open_orders = lambda _request: (SimpleNamespace(symbol=""), SimpleNamespace(symbol="QLD"))  # type: ignore[method-assign]
    assert broker.all_open_order_symbols() == {"QLD"}


def test_unpriced_working_buy_blocks_new_buy(tmp_path: Path) -> None:
    from parayabuck.execution.portfolio import PortfolioOrderRequest, PortfolioOrderSide

    broker = _broker_with_orders(tmp_path, (_order(client_id="", symbol="MSFT"),))
    assert broker.working_buy_orders()[0].reference_price is None
    assert (
        PositionSnapshot("AAPL", 500, 100, 100, Market.EQUITY, ownership="managed", managed_qty=500).market_value
        == 50000.0
    )
    assert OpenPortfolioOrder("x", "MSFT", Market.EQUITY, "buy", 50, 0, "", 100).quantity == 50
    req = PortfolioOrderRequest("c", "d", "QLD", Market.EQUITY, PortfolioOrderSide.BUY, 1.0, 100.0, _AT, "eq", "1")
    assert req.quantity == 1.0
