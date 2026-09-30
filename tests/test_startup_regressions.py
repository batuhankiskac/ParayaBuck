"""Engine startup regression tests (warmup + ledger recovery).

Two real startup crashes found during release closure:

1. ``_warmup_symbols`` reduced ``max(2, *[])`` to ``max(2)`` — a single
   argument, which Python treats as the iterable — crashing every engine
   start whose operational timeframes include one no strategy uses.
2. Fill-activity recovery inserted a second ledger row for a broker order
   already booked under a different client id, violating the unique
   broker-order index and bricking engine startup.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

from parayabuck.core.models import Bar, Market
from parayabuck.data.aggregator import TimeframeAggregator
from parayabuck.execution.alpaca_reconciliation import ReconciliationService
from parayabuck.execution.ledger import ExecutionLedger
from parayabuck.runtime import EngineRuntime
from tests.helpers import make_broker


def _equity_runtime() -> EngineRuntime:
    runtime = EngineRuntime.__new__(EngineRuntime)
    runtime.app_config = SimpleNamespace(
        runtime=SimpleNamespace(
            execution_timeframe="5m",
            market_state_timeframe="5m",
        )
    )
    runtime._portfolio_runtimes = (
        SimpleNamespace(
            market=Market.EQUITY,
            strategy=SimpleNamespace(
                timeframe="1d",
                minimum_history_bars=150,
            ),
        ),
    )
    return runtime


def test_warmup_with_operational_timeframe_no_strategy_does_not_crash() -> None:
    """An operational 5m timeframe that no strategy uses must warm up with
    the baseline window instead of crashing on max(2, *[])."""
    runtime = _equity_runtime()
    runtime.aggregators = {
        "5m": TimeframeAggregator("5m", finalization_grace_seconds=0.0),
        "1d": TimeframeAggregator("1d", finalization_grace_seconds=0.0),
    }
    fetched: list[tuple[str, str]] = []

    class _History:
        def get_bars_batch(self, symbols, timeframe, *, start=None, end=None, adjustment="raw"):
            fetched.append((timeframe, str(symbols[0])))
            return {
                symbol: [
                    Bar(
                        datetime(2025, 1, 1, tzinfo=UTC),
                        symbol,
                        timeframe,
                        1,
                        1,
                        1,
                        1,
                        1000,
                        Market.EQUITY,
                    )
                ]
                for symbol in symbols
            }

    runtime._warmup_symbols(_History(), ["AAPL", "MSFT"], retry=False)
    # Both timeframes were fetched (no crash), including the operational-only
    # 5m window that no portfolio strategy declares.
    assert any(timeframe == "5m" for timeframe, _symbol in fetched)
    assert any(timeframe == "1d" for timeframe, _symbol in fetched)


def test_fill_activity_recovery_does_not_rebook_existing_broker_order(
    tmp_path,
) -> None:
    """A sell activity whose broker order is already in the ledger (under a
    different client id) must not crash startup with a unique-index error."""
    ledger = ExecutionLedger(str(tmp_path / "ledger.sqlite3"))
    # Book the exit first (e.g. a portfolio SELL row or an earlier recovery).
    from parayabuck.core.models import ExecutionEvent, OrderRole, OrderState

    ledger.apply_execution_event(
        ExecutionEvent(
            account_id="acct",
            client_order_id="portfolio-sell-1",
            broker_order_id="broker-exit",
            parent_order_id=None,
            role=OrderRole.MANUAL_EXIT,
            symbol="AAPL",
            market=Market.EQUITY,
            state=OrderState.FILLED,
            requested_qty=5.0,
            cumulative_filled_qty=5.0,
            average_fill_price=101.0,
            occurred_at=datetime(2026, 1, 2, tzinfo=UTC),
        )
    )
    activities = [
        {
            "id": "act-1",
            "order_id": "broker-exit",
            "side": "sell",
            "symbol": "AAPL",
            "qty": "5",
            "price": "101",
            "commission": "0.5",
            "transaction_time": "2026-01-02T15:00:00+00:00",
        }
    ]
    broker = make_broker(
        tmp_path,
        ledger=ledger,
        client=SimpleNamespace(
            _request=lambda *_args, **kwargs: [] if kwargs.get("data", {}).get("page_token") else activities
        ),
    )
    broker._account_id = "acct"

    assert ReconciliationService(broker)._reconcile_fill_activity_fees() is True
    # Still exactly one row for the broker order; the fee was recorded.
    rows = ledger.connection.execute(
        "SELECT client_order_id FROM orders WHERE broker_order_id='broker-exit'"
    ).fetchall()
    assert rows == [("portfolio-sell-1",)]
    fee_rows = ledger.connection.execute("SELECT COUNT(*) FROM fee_activities").fetchone()[0]
    assert fee_rows == 1
    ledger.close()
