"""Engine startup regression tests (warmup).

``_warmup_symbols`` once reduced ``max(2, *[])`` to ``max(2)`` on a timeframe
no strategy used and crashed startup. Warmup now fetches only the strategy
signal cadences.
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime
from types import SimpleNamespace

from parayabuck.core.models import Bar, Market
from parayabuck.runtime import EngineRuntime


def _equity_runtime(seeded: list[list[Bar]]) -> EngineRuntime:
    runtime = EngineRuntime.__new__(EngineRuntime)
    runtime._strategy_timeframes = {"1d"}
    runtime.aggregators = {}
    runtime._portfolio_lock = threading.RLock()
    runtime._portfolio_runtimes = (
        SimpleNamespace(
            market=Market.EQUITY,
            strategy=SimpleNamespace(
                timeframe="1d",
                minimum_history_bars=150,
            ),
            seed=seeded.append,
        ),
    )
    return runtime


def test_warmup_fetches_only_the_strategy_signal_cadence_on_the_splits_basis() -> None:
    """Warmup seeds the strategy history and nothing else: one split-adjusted
    request per symbol batch for the signal cadence, no raw or intraday fetch."""
    seeded: list[list[Bar]] = []
    runtime = _equity_runtime(seeded)
    fetched: list[tuple[str, str]] = []

    class _History:
        def get_bars_batch(self, symbols, timeframe, *, start=None, end=None, adjustment="raw"):
            fetched.append((timeframe, adjustment))
            return {
                symbol: [Bar(datetime(2025, 1, 1, tzinfo=UTC), symbol, timeframe, 1, 1, 1, 1, 1000, Market.EQUITY)]
                for symbol in symbols
            }

    runtime._warmup_symbols(_History(), ["AAPL", "MSFT"], retry=False)

    assert fetched == [("1d", "splits")]
    assert [[bar.symbol for bar in batch] for batch in seeded] == [["AAPL"], ["MSFT"]]
