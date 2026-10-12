"""Database baseline: portfolio-state repository hot operations.

Seeds a fresh runtime DB and times the canonical write/read operations on the
decision path persistence operations. Uses production classes against a
tmp DB so absolute numbers reflect real schema/index behavior.
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from benchmarks.harness import Report, timeit
from parayabuck.core.models import Market
from parayabuck.execution.portfolio import (
    PortfolioOrderRequest,
    PortfolioOrderSide,
    RiskOverlayDecision,
)
from parayabuck.portfolio.models import TargetPortfolio, TargetWeight
from parayabuck.portfolio.runtime_service import PortfolioStateRepository
from parayabuck.storage import SQLiteStore

_SYMBOLS = ("SPY", "AAPL", "MSFT", "NVDA", "TSLA", "AMZN", "META", "GOOGL")


def _target(timestamp: datetime) -> TargetPortfolio:
    return TargetPortfolio(
        strategy_name="equity_leveraged_trend_v1",
        strategy_version="2.0.0",
        decision_timestamp=timestamp,
        weights=tuple(TargetWeight(symbol, 0.1, reason="trend_up") for symbol in _SYMBOLS),
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
        strategy_name="equity_leveraged_trend_v1",
        strategy_version="2.0.0",
    )


def run(report: Report) -> None:
    # A fresh throwaway directory per run: never under the repository.
    db = Path(tempfile.mkdtemp(prefix="bench_db_")) / "bench_db.sqlite3"
    store = SQLiteStore(str(db))
    repo = PortfolioStateRepository(str(db), store=store)

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
        nonlocal store, repo, session_id
        db.unlink(missing_ok=True)
        store = SQLiteStore(str(db))
        repo = PortfolioStateRepository(str(db), store=store)
        session_id = repo.begin_session("account-1", "digest-bench", "paper")

    def record_decision() -> None:
        repo.record_decision_with_orders(
            session_id, "account-1", "digest-bench", _target(timestamp), overlay, Market.EQUITY, ()
        )

    timing, _ = timeit(
        record_decision,
        label="portfolio decision insert",
        warmup=3,
        runs=10,
        before_each=fresh_db,
    )
    report.add(timing)

    def insert_order() -> None:
        decision_id = repo.record_decision_with_orders(
            session_id, "account-1", "digest-bench", _target(timestamp), overlay, Market.EQUITY, ()
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
        decision_id = repo.record_decision_with_orders(
            session_id, "account-1", "digest-bench", _target(timestamp), overlay, Market.EQUITY, ()
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

    def queries() -> None:
        repo.record_decision_with_orders(
            session_id, "account-1", "digest-bench", _target(timestamp), overlay, Market.EQUITY, ()
        )
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
    store.release()


if __name__ == "__main__":
    argparse.ArgumentParser(
        description="Portfolio-state repository hot-operation timings (temp SQLite DB)."
    ).parse_args()
    report = Report()
    run(report)
    report.print_table()
