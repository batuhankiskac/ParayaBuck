"""F1 audit follow-up regression tests (2026-10-11 findings).

Deterministic, single-invariant, tmp_path-only. Each test fails before its
fix and passes after.
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

from parayabuck.core.models import Bar, Market

_TS0 = datetime(2025, 6, 2, 4, 0, tzinfo=UTC)


def _bar(symbol: str, ts: datetime, close: float = 100.0) -> Bar:
    return Bar(ts, symbol, "1d", close, close, close, close, 1000, Market.EQUITY)


def _days(n: int) -> list[datetime]:
    return [_TS0 + timedelta(days=i) for i in range(n)]


class _FakeHistory:
    def __init__(self, provider_by_symbol: dict[str, list[Bar]]) -> None:
        self.provider_by_symbol = provider_by_symbol
        self.calls = 0

    def get_bars_batch(self, symbols, timeframe, start=None, end=None, adjustment="raw"):  # noqa: ANN001
        self.calls += 1
        return {symbol: list(self.provider_by_symbol.get(symbol, [])) for symbol in symbols}


def _verifier_engine(symbols: tuple[str, ...], stored: dict[str, list[Bar]]):
    from parayabuck.portfolio.runtime_service import PortfolioRuntime
    from parayabuck.runtime import EngineRuntime

    class _Strat:
        name = "test"
        version = "1"
        timeframe = "1d"
        minimum_history_bars = 10

    runtime = object.__new__(PortfolioRuntime)
    runtime.market = Market.EQUITY
    runtime.strategy = _Strat()
    runtime.history = {symbol: list(bars) for symbol, bars in stored.items()}
    runtime.symbols = tuple(symbols)
    runtime.signal_verified_through_by_symbol = {}
    runtime.adopt_authoritative_signal_history = lambda *_a, **_k: 0  # type: ignore[attr-defined]

    engine = EngineRuntime.__new__(EngineRuntime)
    engine._portfolio_runtimes = (runtime,)
    engine._symbols_by_market = {Market.EQUITY: set(symbols)}
    engine._signal_adjustment = {
        "healthy": None,
        "last_success_at": None,
        "last_failure_at": None,
        "last_error": None,
        "verified_through_by_symbol": {},
    }
    engine._signal_verify_dedup = None
    return engine


def test_mid_window_provider_hole_fails_verification_without_advancing_watermark() -> None:
    """R3E1 wiring: a provider hole inside the stored window fails closed."""
    dates = _days(20)
    stored = {"AAA": [_bar("AAA", day, 100 + i) for i, day in enumerate(dates)]}
    # Provider spans the required window but omits the middle session.
    holed = [bar for i, bar in enumerate(stored["AAA"]) if i != 10]
    engine = _verifier_engine(("AAA",), stored)
    engine._historical = _FakeHistory({"AAA": holed})

    assert engine.verify_equity_signal_basis() is False
    assert engine._signal_adjustment["healthy"] is False
    assert engine._signal_adjustment["verified_through_by_symbol"] == {}


def test_matching_provider_window_still_verifies() -> None:
    dates = _days(20)
    stored = {"AAA": [_bar("AAA", day, 100 + i) for i, day in enumerate(dates)]}
    engine = _verifier_engine(("AAA",), stored)
    engine._historical = _FakeHistory({"AAA": list(stored["AAA"])})

    assert engine.verify_equity_signal_basis() is True
    assert engine._signal_adjustment["verified_through_by_symbol"]["AAA"] == dates[-1]


def test_batched_intraday_filter_matches_per_row_session_open() -> None:
    """T3: the batched NYSE range filter keeps exactly the per-row rows."""
    import parayabuck.data.alpaca as alpaca_module
    from parayabuck.markets.nyse_calendar import is_session_open
    from tests.test_historical_data import _hourly_frame

    start = datetime(2024, 11, 25, tzinfo=UTC)
    end = datetime(2024, 12, 2, tzinfo=UTC)
    frame = _hourly_frame(start, end, ["AAPL"])
    stamps = [
        alpaca_module._normalize_row_timestamp(row.timestamp) for row in frame.reset_index().itertuples(index=False)
    ]
    bounds = alpaca_module._session_bounds_for([stamp.astimezone(alpaca_module.NEW_YORK).date() for stamp in stamps])

    def _kept(stamp: datetime) -> bool:
        window = bounds.get(stamp.astimezone(alpaca_module.NEW_YORK).date())
        return window is not None and window[0] <= stamp < window[1]

    assert [stamp for stamp in stamps if _kept(stamp)] == [stamp for stamp in stamps if is_session_open(stamp)]


def test_none_last_equity_refuses_snapshot_without_a_baseline(tmp_path) -> None:
    """D8-5 / G-4: a missing last_equity must not read as flat on the day.

    With no same-day baseline seen by this process the snapshot is refused
    (entries cannot be sized against an unmeasured day P&L).
    """
    import pytest

    from tests.helpers import make_broker

    broker = make_broker(tmp_path)
    broker._client = SimpleNamespace(
        get_account=lambda: SimpleNamespace(
            id="acct",
            account_blocked=False,
            trading_blocked=False,
            trade_suspended_by_user=False,
            equity="100000",
            last_equity=None,
            cash="90000",
        ),
        get_all_positions=lambda: [],
    )
    with pytest.raises(ValueError, match="last_equity is missing"):
        broker.account()


def test_missing_last_equity_attribute_refuses_snapshot_without_a_baseline(tmp_path) -> None:
    import pytest

    from tests.helpers import make_broker

    broker = make_broker(tmp_path)
    broker._client = SimpleNamespace(
        get_account=lambda: SimpleNamespace(
            id="acct",
            account_blocked=False,
            trading_blocked=False,
            trade_suspended_by_user=False,
            equity="100000",
            cash="90000",
        ),
        get_all_positions=lambda: [],
    )
    with pytest.raises(ValueError, match="last_equity is missing"):
        broker.account()


def test_non_numeric_or_nonfinite_account_values_still_raise(tmp_path) -> None:
    import pytest

    from tests.helpers import make_broker

    cases = (
        {"equity": None, "last_equity": "100000", "cash": "90000"},
        {"equity": "bogus", "last_equity": "100000", "cash": "90000"},
        {"equity": "100000", "last_equity": "bogus", "cash": "90000"},
        {"equity": "100000", "last_equity": "nan", "cash": "90000"},
        {"equity": "100000", "last_equity": "100000", "cash": None},
        {"equity": "100000", "last_equity": "100000", "cash": "nan"},
    )
    for kwargs in cases:
        broker = make_broker(tmp_path)
        broker._client = SimpleNamespace(
            get_account=lambda kwargs=kwargs: SimpleNamespace(
                id="acct",
                account_blocked=False,
                trading_blocked=False,
                trade_suspended_by_user=False,
                **kwargs,
            ),
            get_all_positions=lambda: [],
        )
        with pytest.raises(ValueError, match="[Nn]on-finite|not numeric|no cash|has no"):
            broker.account()


def test_peak_lock_exists_before_first_account_call(tmp_path) -> None:
    """R5A-2 follow-up: the peak lock is created in __init__, not lazily."""
    import threading

    from tests.helpers import make_broker

    broker = make_broker(tmp_path)
    assert isinstance(broker._peak_lock, type(threading.Lock()))

    seen: list[int] = []
    original = broker._ensure_peak_reconciled

    def _spy(account_id: str) -> None:
        seen.append(id(broker._peak_lock))
        return original(account_id)

    broker._ensure_peak_reconciled = _spy  # type: ignore[method-assign]
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
    broker.account()
    assert len(seen) == 1


def test_concurrent_account_calls_share_one_peak_lock(tmp_path) -> None:
    from tests.helpers import make_broker

    broker = make_broker(tmp_path)
    first = broker._peak_lock
    errors: list[BaseException] = []

    def _worker(equity: str) -> None:
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

    threads = [threading.Thread(target=_worker, args=(equity,)) for equity in ("105000", "110000")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    assert broker._peak_lock is first
    assert broker._peak_equity == 110000.0


def test_warmup_double_uses_the_plain_batch_shape() -> None:
    from parayabuck.runtime import EngineRuntime

    runtime = EngineRuntime.__new__(EngineRuntime)
    runtime._strategy_timeframes = {"1d"}
    runtime.aggregators = {}
    runtime._portfolio_lock = threading.RLock()
    seeded: list[Any] = []
    runtime._portfolio_runtimes = (
        SimpleNamespace(
            market=Market.EQUITY,
            strategy=SimpleNamespace(timeframe="1d", minimum_history_bars=150),
            seed=seeded.append,
        ),
    )
    fetched: list[tuple[str, str]] = []

    class _History:
        def get_bars_batch(self, symbols, timeframe, *, start=None, end=None, adjustment="raw"):
            fetched.append((timeframe, adjustment))
            return {symbol: [_bar(symbol, datetime(2025, 1, 1, tzinfo=UTC))] for symbol in symbols}

    runtime._warmup_symbols(_History(), ["AAPL"], retry=False)
    assert fetched == [("1d", "splits")]
