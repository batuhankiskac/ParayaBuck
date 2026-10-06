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
from parayabuck.runtime import EngineRuntime


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
