"""Audit wave 2 regressions for the warmup, seed and bar-validation paths (H1)."""

from __future__ import annotations

import csv
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest
from alpaca.data.enums import DataFeed

from parayabuck.backtest.product import load_csv_bars
from parayabuck.core.models import Bar, Market
from parayabuck.data.aggregator import TimeframeAggregator
from parayabuck.data.alpaca import AlpacaHistoricalData, BatchOutcome
from parayabuck.data.cache import WarmupCache
from parayabuck.markets.timeframes import equity_day_bar_is_closed
from parayabuck.portfolio.runtime_service import PortfolioRuntime
from parayabuck.runtime import EngineRuntime

WINDOW_START = datetime(2026, 10, 19, tzinfo=UTC)
WINDOW_END = datetime(2026, 10, 24, tzinfo=UTC)
SESSIONS = [datetime(2026, 10, day, 4, 0, tzinfo=UTC) for day in (19, 20, 21, 22, 23)]


def _daily_bar(symbol: str, stamp: datetime, close: float = 100.0) -> Bar:
    return Bar(stamp, symbol, "1d", close, close, close, close, 1000.0, Market.EQUITY)


def _frame(rows: list[tuple[str, datetime, float]]) -> pd.DataFrame:
    index = pd.MultiIndex.from_tuples([(s, pd.Timestamp(t)) for s, t, _ in rows], names=["symbol", "timestamp"])
    closes = [close for _, _, close in rows]
    return pd.DataFrame(
        {"open": closes, "high": closes, "low": closes, "close": closes, "volume": [1000.0] * len(rows)},
        index=index,
    )


def _provider(frame: pd.DataFrame | None) -> SimpleNamespace:
    empty = pd.DataFrame()
    return SimpleNamespace(get_stock_bars=lambda request: SimpleNamespace(df=empty if frame is None else frame))


def _history(tmp_path: Path, provider: SimpleNamespace) -> AlpacaHistoricalData:
    history = AlpacaHistoricalData.__new__(AlpacaHistoricalData)
    history._configured_stock_feed = "sip"
    history._stock_feed = DataFeed("sip")
    history._sip_fallback_active = False
    history._sip_fallback_since = None
    history._sip_embargo = timedelta(0)
    history._provider_fallbacks = 0
    history._cache = WarmupCache(str(tmp_path / "warmup.sqlite3"))
    history._stock_client = provider
    return history


def _full_week(symbol: str = "QLD") -> list[tuple[str, datetime, float]]:
    return [(symbol, stamp, 100.0 + index) for index, stamp in enumerate(SESSIONS)]


def test_invalid_cached_row_is_skipped_and_the_valid_rows_survive(tmp_path: Path) -> None:
    cache = WarmupCache(str(tmp_path / "warmup.sqlite3"))
    cache.upsert([_daily_bar("QLD", SESSIONS[0])], feed="sip", adjustment="splits")
    # A pre-W17 row with a non-positive price: Bar validation rejects it on read.
    cache.connection.execute(
        "INSERT INTO bars (feed, symbol, market, timeframe, adjustment, timestamp, open, high, low, close, volume, retrieved_at)"
        " VALUES ('sip', 'QLD', 'equity', '1d', 'splits', ?, 0, 0, 0, 0, 1000, '2026-10-20T00:00:00+00:00')",
        (SESSIONS[1].isoformat(),),
    )
    cache.connection.commit()

    bars = cache.get(
        "QLD", "1d", market=Market.EQUITY, feed="sip", start=WINDOW_START, end=WINDOW_END, adjustment="splits"
    )

    assert [bar.timestamp for bar in bars] == [SESSIONS[0]]
    cache.close()


def test_session_daily_bars_drop_a_symbol_whose_newest_row_is_invalid(tmp_path: Path) -> None:
    rows = [
        ("SPY", SESSIONS[0], 100.0),
        ("SPY", SESSIONS[1], 0.0),  # newest SPY row fails validation
        ("QLD", SESSIONS[0], 50.0),
        ("QLD", SESSIONS[1], 60.0),
    ]
    history = _history(tmp_path, _provider(_frame(rows)))

    result = history.get_session_daily_bars(["SPY", "QLD"], WINDOW_START, WINDOW_END)

    assert set(result) == {"QLD"}
    assert result["QLD"].timestamp == SESSIONS[1]
    assert result["QLD"].close == 60.0
    history.close()


def test_outcome_is_per_call_and_never_shared_on_the_client(tmp_path: Path) -> None:
    holed = [row for row in _full_week() if row[1] != SESSIONS[2]]
    provider = _provider(_frame(holed))
    history = _history(tmp_path, provider)

    first = BatchOutcome()
    history.get_bars_batch(["QLD"], "1d", start=WINDOW_START, end=WINDOW_END, adjustment="splits", outcome=first)
    provider.get_stock_bars = lambda request: SimpleNamespace(df=_frame(_full_week()))
    second = BatchOutcome()
    history.get_bars_batch(["QLD"], "1d", start=WINDOW_START, end=WINDOW_END, adjustment="splits", outcome=second)

    assert first.incomplete == {"QLD"}
    assert second.incomplete == set()
    assert not hasattr(history, "last_incomplete_symbols")
    history.close()


def test_empty_provider_answer_marks_the_expected_window_incomplete(tmp_path: Path) -> None:
    history = _history(tmp_path, _provider(None))
    outcome = BatchOutcome()

    bars = history.get_bars_batch(
        ["QLD", "SPY"], "1d", start=WINDOW_START, end=WINDOW_END, adjustment="splits", outcome=outcome
    )

    assert bars == {"QLD": [], "SPY": []}
    assert outcome.incomplete == {"QLD", "SPY"}
    assert outcome.degraded is True
    history.close()


def test_partial_daily_bar_is_answered_but_never_cached(tmp_path: Path) -> None:
    future = datetime(2099, 1, 5, 5, 0, tzinfo=UTC)  # a session that cannot be final yet
    history = _history(tmp_path, _provider(_frame([("QLD", future, 100.0)])))

    bars = history.get_bars_batch(["QLD"], "1d", start=WINDOW_START, end=WINDOW_END, adjustment="splits")

    assert [bar.timestamp for bar in bars["QLD"]] == [future]
    cached = history._cache.get(
        "QLD",
        "1d",
        market=Market.EQUITY,
        feed="sip",
        start=datetime(2099, 1, 1, tzinfo=UTC),
        end=datetime(2099, 12, 31, tzinfo=UTC),
        adjustment="splits",
    )
    assert cached == []
    history.close()


def test_untracked_correction_is_logged_instead_of_dropped_silently(caplog: pytest.LogCaptureFixture) -> None:
    aggregator = TimeframeAggregator("5m")
    stray = Bar(datetime(2026, 10, 23, 14, 31, tzinfo=UTC), "SPY", "1m", 1, 1, 1, 1, 10, Market.EQUITY)

    with caplog.at_level("WARNING"):
        assert aggregator.update_correction(stray) is None

    assert "Correction for an untracked minute dropped" in caplog.text


def test_non_session_date_is_not_closed_while_the_calendar_is_healthy() -> None:
    saturday_bar = datetime(2026, 10, 24, 4, 0, tzinfo=UTC)  # Saturday 00:00 New York
    saturday_evening = datetime(2026, 10, 24, 21, 0, tzinfo=UTC)  # Saturday 17:00 New York

    assert equity_day_bar_is_closed(saturday_bar, saturday_evening) is False


def test_csv_bar_error_names_the_row_number(tmp_path: Path) -> None:
    path = tmp_path / "bars.csv"
    header = ("timestamp", "symbol", "timeframe", "open", "high", "low", "close", "volume", "market")
    good = ("2025-01-06T05:00:00+00:00", "SPY", "1d", "100", "101", "99", "100.5", "1000", "equity")
    bad = ("2025-01-07T05:00:00+00:00", "SPY", "1d", "oops", "101", "99", "100.5", "1000", "equity")
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        writer.writerow(good)
        writer.writerow(bad)

    with pytest.raises(ValueError, match=r"CSV row 3 is not a valid bar"):
        load_csv_bars(path, default_market=Market.EQUITY)


def _bare_seed_runtime(symbol: str = "AAA") -> PortfolioRuntime:
    runtime = object.__new__(PortfolioRuntime)
    runtime.market = Market.EQUITY
    runtime.strategy = type("S", (), {"timeframe": "1d", "minimum_history_bars": 10})()
    runtime.symbols = (symbol,)
    runtime.history = {symbol: []}
    runtime.signal_verified_through_by_symbol = {}
    runtime.previous_timestamp = None
    return runtime


def test_seed_backfills_older_provider_bars_once_per_session() -> None:
    runtime = _bare_seed_runtime()
    live_close = datetime(2025, 1, 10, 21, 0, tzinfo=UTC)  # stream label of the newest session
    runtime.seed([Bar(live_close, "AAA", "1d", 100, 100, 100, 100, 1000, Market.EQUITY)])
    provider = [
        Bar(datetime(2025, 1, day, 5, 0, tzinfo=UTC), "AAA", "1d", 90, 90, 90, 90, 1000, Market.EQUITY)
        for day in range(1, 11)
    ]

    runtime.seed(provider)

    stamps = [bar.timestamp for bar in runtime.history["AAA"]]
    assert stamps == sorted(stamps)
    assert len(stamps) == 10
    assert stamps[-1] == live_close  # the session already held keeps its stream bar


def test_verifier_refuses_a_window_warmup_marked_incomplete(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[str] = []
    monkeypatch.setattr(
        "parayabuck.runtime.alerts",
        type("A", (), {"emit": staticmethod(lambda kind, **kw: events.append(kind))}),
    )
    engine = EngineRuntime.__new__(EngineRuntime)
    engine._symbols_by_market = {Market.EQUITY: {"AAA"}}
    equity = _bare_seed_runtime("AAA")
    engine._portfolio_runtimes = (equity,)
    dates = [datetime(2026, 10, day, 4, 0, tzinfo=UTC) for day in (19, 20, 21)]

    class _Holed:
        def get_bars_batch(self, symbols, timeframe, *, start, end, adjustment, outcome=None):
            outcome.incomplete.update(symbols)
            return {"AAA": [_daily_bar("AAA", stamp) for stamp in dates]}

    engine._historical = _Holed()
    engine._signal_adjustment_state().update({"healthy": True, "last_success_at": None})

    assert engine.verify_equity_signal_basis() is False
    assert equity.signal_verified_through_by_symbol == {}
    assert "signal_adjustment_failure" in events


def test_cache_fallback_with_a_hole_is_marked_incomplete(tmp_path: Path) -> None:
    holed = [stamp for stamp in SESSIONS if stamp != SESSIONS[2]]
    history = _history(tmp_path, _provider(None))
    history._cache.upsert([_daily_bar("QLD", stamp) for stamp in holed], feed="sip", adjustment="splits")

    def _provider_down(request: object) -> None:
        raise RuntimeError("provider down")

    history._stock_client = SimpleNamespace(get_stock_bars=_provider_down)
    outcome = BatchOutcome()

    bars = history.get_bars_batch(
        ["QLD"], "1d", start=WINDOW_START, end=WINDOW_END, adjustment="splits", outcome=outcome
    )

    assert [bar.timestamp for bar in bars["QLD"]] == holed
    assert outcome.degraded is True
    assert outcome.incomplete == {"QLD"}
    history.close()
