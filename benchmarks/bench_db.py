"""Database baseline: portfolio-state repository and ledger hot operations.

Seeds a fresh runtime DB and times the canonical write/read operations on the
decision path persistence operations. Uses production classes against a
tmp DB so absolute numbers reflect real schema/index behavior.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from benchmarks.harness import Report, timeit
from parayabuck.core.models import Market
from parayabuck.execution.ledger import ExecutionLedger
from parayabuck.execution.portfolio import (
    PortfolioOrderRequest,
    PortfolioOrderSide,
    RiskOverlayDecision,
)
from parayabuck.portfolio.models import TargetPortfolio, TargetWeight
from parayabuck.portfolio.runtime_service import PortfolioStateRepository
from parayabuck.storage import SQLiteStore

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "smoke" / "bench_db.sqlite3"

_SYMBOLS = ("SPY", "AAPL", "MSFT", "NVDA", "TSLA", "AMZN", "META", "GOOGL")


def _target(timestamp: datetime) -> TargetPortfolio:
    return TargetPortfolio(
        strategy_name="equity_residual_momentum_v2",
        strategy_version="2.0.0",
        decision_timestamp=timestamp,
        weights=tuple(TargetWeight(symbol, 0.1, reason="residual_momentum") for symbol in _SYMBOLS),
        cash_weight=0.2,
        universe_version="bench",
        universe_snapshot_id="bench",
        parameters=(("top_n", 8),),
    )


def _order(timestamp: datetime, symbol: str, decision_id: str) -> PortfolioOrderRequest:
    return PortfolioOrderRequest(
        client_order_id=f"pbp-bench-{symbol}",
        decision_id=decision_id,
        symbol=symbol,
        market=Market.EQUITY,
        side=PortfolioOrderSide.BUY,
        quantity=10.0,
        reference_price=100.0,
        decision_timestamp=timestamp,
        strategy_name="equity_residual_momentum_v2",
        strategy_version="2.0.0",
        risk_reducing=False,
    )


def run(report: Report) -> None:
    DB.unlink(missing_ok=True)
    store = SQLiteStore(str(DB))
    repo = PortfolioStateRepository(str(DB), store=store)
    ledger = ExecutionLedger(str(DB), store=store)

    session_id = repo.begin_session("account-1", "digest-bench", "paper")
    timestamp = datetime(2026, 8, 1, tzinfo=UTC)
    overlay = RiskOverlayDecision(
        TargetPortfolio(
            strategy_name="x",
            strategy_version="1",
            decision_timestamp=timestamp,
            weights=(),
            cash_weight=1.0,
        ),
        False,
        (),
        0.0,
        0.0,
    )

    def fresh_db() -> None:
        nonlocal store, repo, ledger, session_id
        DB.unlink(missing_ok=True)
        store = SQLiteStore(str(DB))
        repo = PortfolioStateRepository(str(DB), store=store)
        ledger = ExecutionLedger(str(DB), store=store)
        session_id = repo.begin_session("account-1", "digest-bench", "paper")

    def record_decision() -> None:
        repo.record_decision(session_id, "account-1", "digest-bench", _target(timestamp), overlay, Market.EQUITY)

    timing, _ = timeit(
        record_decision,
        label="portfolio decision insert",
        warmup=3,
        runs=10,
        before_each=fresh_db,
    )
    report.add(timing)

    def insert_order() -> None:
        decision_id = repo.record_decision(
            session_id, "account-1", "digest-bench", _target(timestamp), overlay, Market.EQUITY
        )
        repo.record_order(_order(timestamp, "AAPL", decision_id), "submitted")

    timing, _ = timeit(
        insert_order,
        label="decision insert + order insert",
        warmup=3,
        runs=10,
        before_each=fresh_db,
    )
    report.add(timing)

    def order_update() -> None:
        decision_id = repo.record_decision(
            session_id, "account-1", "digest-bench", _target(timestamp), overlay, Market.EQUITY
        )
        repo.record_order(_order(timestamp, "AAPL", decision_id), "submitted")
        repo.apply_trade_update(
            client_order_id="pbp-bench-AAPL",
            broker_order_id="broker-1",
            status="fill",
            cumulative_filled_quantity=10.0,
            average_fill_price=100.5,
            occurred_at=timestamp + timedelta(minutes=1),
            activity_id="activity-1",
            fee=0.5,
            realized_pnl=0.0,
            unrealized_pnl=0.0,
        )

    timing, _ = timeit(
        order_update,
        label="order insert + fill update",
        warmup=3,
        runs=10,
        before_each=fresh_db,
    )
    report.add(timing)

    def ledger_fill() -> None:
        from parayabuck.core.models import (
            Direction,
            ExecutionEvent,
            ExitPolicy,
            OrderIntent,
            OrderRole,
            OrderState,
        )

        intent = OrderIntent(
            signal_id="signal-1",
            symbol="AAPL",
            market=Market.EQUITY,
            direction=Direction.LONG,
            qty=10.0,
            entry_price=100.0,
            stop_price=95.0,
            target_price=120.0,
            strategy="equity_residual_momentum_v2",
            client_order_id="pbp-ledger-1",
            strategy_version="2.0.0",
            exit_policy=ExitPolicy(),
        )
        ledger.register_entry_order(
            account_id="account-1",
            order=intent,
            broker_order_id="broker-ledger-1",
            decision_timestamp=timestamp,
            reference_price=100.0,
        )
        event = ExecutionEvent(
            account_id="account-1",
            client_order_id="pbp-ledger-1",
            broker_order_id="broker-ledger-1",
            parent_order_id=None,
            role=OrderRole.ENTRY,
            symbol="AAPL",
            market=Market.EQUITY,
            state=OrderState.FILLED,
            requested_qty=10.0,
            cumulative_filled_qty=10.0,
            average_fill_price=100.5,
            occurred_at=timestamp + timedelta(minutes=1),
            fee=0.5,
            activity_id="activity-ledger-1",
        )
        ledger.apply_execution_event(event)

    timing, _ = timeit(
        ledger_fill,
        label="ledger entry order + execution event",
        warmup=3,
        runs=10,
        before_each=fresh_db,
    )
    report.add(timing)

    def queries() -> None:
        repo.record_decision(session_id, "account-1", "digest-bench", _target(timestamp), overlay, Market.EQUITY)
        repo.high_water("account-1")
        repo.update_high_water("account-1", 100_000.0)
        repo.operations_snapshot("account-1")
        repo.ownership_adjustments("account-1")
        repo.open_orders("account-1")
        repo.owned_quantities("account-1")

    timing, _ = timeit(
        queries,
        label="high-water + snapshot + ownership queries",
        warmup=3,
        runs=10,
        before_each=fresh_db,
    )
    report.add(timing)

    repo.close()
    ledger.close()
    store.release()


if __name__ == "__main__":
    report = Report()
    run(report)
    report.print_table()
