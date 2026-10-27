"""W3 audit-2 regression tests (2026-10-11): portfolio, data, markets.

Deterministic, tmp_path-only, no network. Each test pins one finding.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml

from parayabuck import load_config
from parayabuck.core.models import Bar, Market
from parayabuck.data.alpaca import AlpacaHistoricalData
from parayabuck.data.cache import WarmupCache
from parayabuck.execution.portfolio import (
    BrokerWorkingBuyOrder,
    PortfolioOrderRequest,
    PortfolioOrderSide,
    PortfolioRiskLimits,
)
from parayabuck.portfolio import EquityLeveragedTrendV1
from parayabuck.portfolio.base import PortfolioContext
from parayabuck.portfolio.runtime_service import PortfolioStateRepository
from tests.test_audit_20261011_w3 import _order
from tests.test_signal_adjustment_parity import _bare_runtime
from tests.test_two_phase_rebalance import _bars, _fill, _position, _runtime, _SimBroker

SHIPPED = Path(__file__).resolve().parents[1] / "config" / "settings.yaml"
ACCOUNT = "acct"
SYMBOL = "QLD"
T0 = datetime(2026, 9, 1, tzinfo=UTC)


# --------------------------------------------------------------------------- #
# Historical fetch helpers (fake provider, real WarmupCache under tmp_path)
# --------------------------------------------------------------------------- #


class _FrameProvider:
    """Returns exactly the given rows, whatever the request window."""

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows

    def get_stock_bars(self, _request: Any) -> Any:
        import pandas as pd

        return SimpleNamespace(df=pd.DataFrame(self.rows))


def _row(symbol: str, timestamp: datetime, close: float = 100.0, **overrides: float) -> dict[str, Any]:
    row: dict[str, Any] = {
        "symbol": symbol,
        "timestamp": timestamp,
        "open": close,
        "high": close + 1.0,
        "low": close - 1.0,
        "close": close,
        "volume": 1000.0,
    }
    row.update(overrides)
    return row


def _history(cache: WarmupCache, rows: list[dict[str, Any]]) -> AlpacaHistoricalData:
    history = AlpacaHistoricalData.__new__(AlpacaHistoricalData)
    history._stock_feed = "sip"  # type: ignore[assignment]
    history._configured_stock_feed = "sip"
    history._sip_fallback_active = False
    history._sip_fallback_since = None
    history._sip_embargo = timedelta(0)
    history._stock_client = _FrameProvider(rows)  # type: ignore[assignment]
    history._cache = cache
    history._provider_fallbacks = 0
    history._last_batch_degraded = False
    history.provider_calls = 0
    history.provider_symbols_requested = 0
    history.provider_bars_returned = 0
    return history


# Thanksgiving week 2025: Mon 24, Tue 25, Wed 26, (Thu 27 holiday), Fri 28
# (half day), Mon Dec 1. Daily bars are labelled 00:00 New York (05:00 UTC).
_THANKSGIVING_SESSIONS = [datetime(2025, 11, day, 5, tzinfo=UTC) for day in (24, 25, 26, 28)] + [
    datetime(2025, 12, 1, 5, tzinfo=UTC)
]
_TG_START = datetime(2025, 11, 24, tzinfo=UTC)
_TG_END = datetime(2025, 12, 2, tzinfo=UTC)


def _daily(symbol: str, timestamp: datetime, close: float = 100.0) -> Bar:
    return Bar(timestamp, symbol, "1d", close, close + 1.0, close - 1.0, close, 1000.0, Market.EQUITY)


def test_holiday_spanning_daily_window_is_complete() -> None:
    """Q1C-2/Q1E-1: honest bars for every NYSE session complete a window
    spanning a holiday and a midnight-aligned start/end."""
    bars = [_daily("QLD", stamp) for stamp in _THANKSGIVING_SESSIONS]
    assert AlpacaHistoricalData._window_is_complete(bars, _TG_START, _TG_END)


def test_daily_window_with_a_session_hole_is_not_complete() -> None:
    """Q1C-2: a missing session (not a holiday) keeps the write an upsert."""
    bars = [_daily("QLD", stamp) for stamp in _THANKSGIVING_SESSIONS if stamp.day != 26]
    assert not AlpacaHistoricalData._window_is_complete(bars, _TG_START, _TG_END)


def test_complete_holiday_window_replaces_and_hole_upserts(tmp_path: Path, caplog: Any) -> None:
    """Q1C-4: a complete fetch calls replace_window without the "partial"
    warning; a provider hole falls back to upsert and warns."""
    cache = WarmupCache(str(tmp_path / "warmup.sqlite3"))
    try:
        calls: list[int] = []
        original = cache.replace_window

        def _spy(bars: list[Bar], **kwargs: Any) -> bool:
            calls.append(len(bars))
            return original(bars, **kwargs)

        cache.replace_window = _spy  # type: ignore[method-assign]
        rows = [_row("QLD", stamp) for stamp in _THANKSGIVING_SESSIONS]
        with caplog.at_level("WARNING"):
            answer = _history(cache, rows)._fetch_window(["QLD"], "1d", _TG_START, _TG_END, "raw")
        assert len(answer["QLD"]) == 5
        assert calls == [5]
        assert "partial" not in caplog.text

        caplog.clear()
        holed = [row for row in rows if row["timestamp"].day != 26]
        with caplog.at_level("WARNING"):
            _history(cache, holed)._fetch_window(["QLD"], "1d", _TG_START, _TG_END, "raw")
        assert calls == [5]
        assert "partial 1d window" in caplog.text
    finally:
        cache.close()


def test_one_invalid_row_does_not_abort_other_symbols(tmp_path: Path) -> None:
    """Q3C-1: a NaN close for one symbol is skipped; clean symbols are served."""
    cache = WarmupCache(str(tmp_path / "warmup.sqlite3"))
    try:
        rows = [_row(symbol, stamp) for stamp in _THANKSGIVING_SESSIONS for symbol in ("QQQ", "SPY")]
        rows += [_row("QLD", stamp) for stamp in _THANKSGIVING_SESSIONS[:-1]]
        rows.append(_row("QLD", _THANKSGIVING_SESSIONS[-1], close=float("nan")))
        rows.append(_row("QLD", _THANKSGIVING_SESSIONS[0] - timedelta(days=3), high=90.0, low=110.0))
        answer = _history(cache, rows)._fetch_window(["QLD", "QQQ", "SPY"], "1d", _TG_START, _TG_END, "raw")
        assert len(answer["QQQ"]) == len(answer["SPY"]) == 5
        assert [bar.timestamp for bar in answer["QLD"]] == _THANKSGIVING_SESSIONS[:-1]
        assert not AlpacaHistoricalData._window_is_complete(answer["QLD"], _TG_START, _TG_END)
    finally:
        cache.close()


def test_duplicate_instant_keeps_one_bar_in_memory_and_cache(tmp_path: Path) -> None:
    """Q3C-3/Q3C-6: two rows for one instant -> last wins everywhere."""
    cache = WarmupCache(str(tmp_path / "warmup.sqlite3"))
    try:
        session = _THANKSGIVING_SESSIONS[0]
        rows = [_row("QLD", session, close=50.5), _row("QLD", session, close=50.9)]
        answer = _history(cache, rows)._fetch_window(["QLD"], "1d", _TG_START, _TG_END, "raw")
        assert [bar.close for bar in answer["QLD"]] == [50.9]
        cached = cache.get(
            "QLD", "1d", market=Market.EQUITY, feed="sip", start=_TG_START, end=_TG_END, adjustment="raw"
        )
        assert [bar.close for bar in cached] == [50.9]
        runtime = _bare_runtime("QLD")
        runtime.seed(answer["QLD"])
        assert [bar.close for bar in runtime.history["QLD"]] == [50.9]
    finally:
        cache.close()


def test_unrequested_symbol_rows_are_dropped(tmp_path: Path) -> None:
    """Q3C-4: a successor ticker row never leaks into the answer, and the
    chunked merge no longer raises KeyError on it."""
    cache = WarmupCache(str(tmp_path / "warmup.sqlite3"))
    try:
        rows = [_row("QLD", stamp) for stamp in _THANKSGIVING_SESSIONS]
        rows.append(_row("QLD_NEW", _THANKSGIVING_SESSIONS[0]))
        history = _history(cache, rows)
        answer = history._fetch_window(["QLD"], "1d", _TG_START, _TG_END, "raw")
        assert set(answer) == {"QLD"}
        chunked = history._fetch_chunked_windows(["QLD"], "1d", _TG_START, _TG_END, 1440, "raw")
        assert set(chunked) == {"QLD"}
        assert len(chunked["QLD"]) == 5
    finally:
        cache.close()


def test_stale_schema_cache_is_rebuilt_instead_of_crashing_upsert(tmp_path: Path) -> None:
    """Q3C-5: a cache whose primary key lacks ``market`` is dropped and
    rebuilt on open, so upsert works instead of raising OperationalError."""
    path = tmp_path / "warmup.sqlite3"
    connection = sqlite3.connect(path)
    connection.execute(
        """CREATE TABLE bars (
            feed TEXT NOT NULL, symbol TEXT NOT NULL, market TEXT NOT NULL,
            timeframe TEXT NOT NULL, adjustment TEXT NOT NULL, timestamp TEXT NOT NULL,
            open REAL NOT NULL, high REAL NOT NULL, low REAL NOT NULL,
            close REAL NOT NULL, volume REAL NOT NULL, retrieved_at TEXT NOT NULL,
            PRIMARY KEY(feed, symbol, timeframe, adjustment, timestamp)
        )"""
    )
    connection.commit()
    connection.close()
    cache = WarmupCache(str(path))
    try:
        bar = _daily("QLD", _THANKSGIVING_SESSIONS[0])
        cache.upsert([bar], feed="sip", adjustment="raw")
        cache.upsert([bar], feed="sip", adjustment="raw")
        assert (
            len(
                cache.get("QLD", "1d", market=Market.EQUITY, feed="sip", start=_TG_START, end=_TG_END, adjustment="raw")
            )
            == 1
        )
    finally:
        cache.close()


def test_current_schema_cache_is_kept(tmp_path: Path) -> None:
    """Q3C-5: a current cache survives a reopen untouched."""
    path = tmp_path / "warmup.sqlite3"
    cache = WarmupCache(str(path))
    cache.upsert([_daily("QLD", _THANKSGIVING_SESSIONS[0])], feed="sip", adjustment="raw")
    cache.close()
    reopened = WarmupCache(str(path))
    try:
        assert (
            len(
                reopened.get(
                    "QLD", "1d", market=Market.EQUITY, feed="sip", start=_TG_START, end=_TG_END, adjustment="raw"
                )
            )
            == 1
        )
    finally:
        reopened.close()


# --------------------------------------------------------------------------- #
# Signal history: stream tail vs adopted provider bar
# --------------------------------------------------------------------------- #


def test_stream_bar_never_overwrites_adopted_provider_final_bar() -> None:
    """Q3C-2/Q3C-6: the provider-final (rebased) bar adopted for session D
    keeps its close when the RAW stream bar for D is appended."""
    runtime = _bare_runtime("QQQ")
    provider_day = datetime(2026, 9, 28, 4, tzinfo=UTC)
    provider = [_daily("QQQ", provider_day - timedelta(days=offset), 100.0) for offset in (3, 2, 1)]
    provider.append(_daily("QQQ", provider_day, 102.0))
    runtime.adopt_authoritative_signal_history({"QQQ": provider}, {"QQQ": provider_day})
    stream = _daily("QQQ", provider_day.replace(hour=13, minute=30), 400.0)
    runtime._append_signal_bar(stream)
    tail = runtime.history["QQQ"][-1]
    assert tail.timestamp == stream.timestamp
    assert tail.close == 102.0
    assert len(runtime.history["QQQ"]) == 4


def test_stream_bar_still_replaces_an_unadopted_same_session_tail() -> None:
    """Q3C-2: a same-session provider tail that was never adopted as final
    (verification only reached D-1) is still replaced by the stream bar."""
    runtime = _bare_runtime("QQQ")
    day = datetime(2026, 9, 28, 4, tzinfo=UTC)
    runtime.adopt_authoritative_signal_history(
        {"QQQ": [_daily("QQQ", day - timedelta(days=1), 100.0)]}, {"QQQ": day - timedelta(days=1)}
    )
    runtime.history["QQQ"].append(_daily("QQQ", day, 101.0))
    runtime._append_signal_bar(_daily("QQQ", day.replace(hour=13, minute=30), 103.0))
    assert runtime.history["QQQ"][-1].close == 103.0


# --------------------------------------------------------------------------- #
# Strategy parameter validation and config merging
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "parameters",
    [
        {"sma_bars": 1},
        {"sma_bars": 1_000_000},
        {"vol_target": 5.0},
        {"vol_target": float("nan")},
        {"vol_window": 10_000},
        {"sma_barz": 200},
    ],
)
def test_strategy_rejects_meaningless_parameters(parameters: dict[str, object]) -> None:
    """Q2C-2/Q3E-7/Q3E-8: always-cash SMA(1), absurd bounds and typo keys fail."""
    with pytest.raises(ValueError):
        EquityLeveragedTrendV1(parameters)


def test_strategy_accepts_buy_and_hold_and_shipped_parameters() -> None:
    """Q2C-2: sma_bars=0 (buy-and-hold) and the shipped VT35 values stay valid."""
    EquityLeveragedTrendV1({"sma_bars": 0})
    strategy = EquityLeveragedTrendV1({"sma_bars": 200, "vol_target": 0.35, "vol_window": 20})
    assert strategy.vol_target == 0.35


def test_flat_history_with_vol_target_holds_full_weight() -> None:
    """Q5E-4: zero realized volatility takes the guard (weight 1.0), never
    a ZeroDivisionError out of decide()."""
    strategy = EquityLeveragedTrendV1({"sma_bars": 0, "vol_target": 0.35, "vol_window": 5})
    stamps = [T0 + timedelta(days=index) for index in range(10)]
    history = {symbol: tuple(_daily(symbol, stamp, 50.0) for stamp in stamps) for symbol in ("QQQ", "QLD")}
    context = PortfolioContext(
        decision_timestamp=stamps[-1],
        market=Market.EQUITY,
        timeframe="1d",
        history=history,
        universe=None,
        universe_version="digest",
        equity=1000.0,
        holdings={},
        prices={},
        benchmark_symbol="SPY",
    )
    target = strategy.decide(context)
    assert target.metadata["realized_volatility"] == 0.0
    assert [(weight.symbol, weight.weight) for weight in target.weights] == [("QLD", 1.0)]


def _config_with_parameters(tmp_path: Path, parameters: dict[str, object]) -> Path:
    raw = yaml.safe_load(SHIPPED.read_text(encoding="utf-8"))
    raw["strategy"]["equity"]["parameters"] = parameters
    path = tmp_path / "settings.yaml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    return path


def test_partial_parameters_block_keeps_packaged_values(tmp_path: Path) -> None:
    """Q3E-6: setting only sma_bars must not silently disable vol targeting."""
    config = load_config(str(_config_with_parameters(tmp_path, {"sma_bars": 150})))
    parameters = config.strategy.equity.parameters
    assert parameters["sma_bars"] == 150
    assert parameters["vol_target"] == 0.35
    assert parameters["vol_window"] == 20


def test_unknown_parameter_key_fails_config_loading(tmp_path: Path) -> None:
    """Q3E-8: a typo'd key is a load error, not a silent digest change."""
    with pytest.raises(ValueError, match="sma_barz"):
        load_config(str(_config_with_parameters(tmp_path, {"sma_barz": 200})))


def test_shipped_parameters_are_unchanged() -> None:
    """Q3E-6: the merge leaves the shipped parameters exactly as written."""
    assert load_config(str(SHIPPED)).strategy.equity.parameters == {
        "signal_symbol": "QQQ",
        "hold_symbol": "QLD",
        "sma_bars": 200,
        "vol_target": 0.35,
        "vol_window": 20,
        "rebalance_frequency": "hours:24",
        "trend_rule": "majority_sma_tsm_don",
        "tsm_bars": 252,
        "donchian_bars": 126,
        # 2026-10-14 (fast_reentry_integration_v1): R2 early re-entry path.
        "early_reentry_weight": 0.5,
    }


# --------------------------------------------------------------------------- #
# Ownership: operator reverse-split rebase and stale fill averages
# --------------------------------------------------------------------------- #


def _buy_100_at_10(repo: PortfolioStateRepository) -> None:
    cid = _order(repo, "buy", 100.0, 10.0, T0, "cid-buy")
    repo.apply_trade_update(
        client_order_id=cid,
        broker_order_id="b-buy",
        status="fill",
        cumulative_filled_quantity=100.0,
        average_fill_price=10.0,
        occurred_at=T0,
        activity_id="a-buy",
        fee=0.0,
        realized_pnl=0.0,
        unrealized_pnl=0.0,
    )


def test_operator_reverse_split_rebase_rescales_cost_basis(tmp_path: Path) -> None:
    """Q1C-1/Q1C-4: an operator 1:2 reverse-split rebase rescales the lots.

    The corroborating broker average cost is the only accepted proof: an
    explicit ``kind="split_rebase"`` without it no longer rescales (F10-2), so
    that parametrised case was removed."""
    repo = PortfolioStateRepository(tmp_path / "runtime.sqlite3")
    try:
        _buy_100_at_10(repo)
        assert (
            repo.rebase_ownership_to_broker_quantity(ACCOUNT, SYMBOL, Market.EQUITY, 50.0, broker_average_cost=20.0)
            == 50.0
        )
        assert repo.owned_quantities(ACCOUNT, Market.EQUITY)[SYMBOL] == pytest.approx(50.0)
        assert repo.owned_cost_bases(ACCOUNT, SYMBOL, Market.EQUITY) == pytest.approx((20.0, 20.0))
    finally:
        repo.close()


def test_operator_decrease_with_unchanged_basis_depletes_like_a_sale(tmp_path: Path) -> None:
    """Q1C-1: an unchanged broker basis proves no split; lots deplete."""
    repo = PortfolioStateRepository(tmp_path / "runtime.sqlite3")
    try:
        _buy_100_at_10(repo)
        repo.rebase_ownership_to_broker_quantity(ACCOUNT, SYMBOL, Market.EQUITY, 50.0, broker_average_cost=10.0)
        assert repo.owned_cost_bases(ACCOUNT, SYMBOL, Market.EQUITY) == pytest.approx((10.0, 10.0))
    finally:
        repo.close()


def test_stale_fill_event_does_not_corrupt_next_incremental_price(tmp_path: Path) -> None:
    """Q3B-1: cum=10@102, stale cum=5@100, then cum=12@101 -> delta at 96."""
    repo = PortfolioStateRepository(tmp_path / "runtime.sqlite3")
    try:
        cid = _order(repo, "buy", 20.0, 100.0, T0, "cid-stale")
        for index, (cumulative, average) in enumerate(((10.0, 102.0), (5.0, 100.0), (12.0, 101.0))):
            repo.apply_trade_update(
                client_order_id=cid,
                broker_order_id="b-stale",
                status="partial_fill",
                cumulative_filled_quantity=cumulative,
                average_fill_price=average,
                occurred_at=T0 + timedelta(minutes=index),
                activity_id=f"a-{index}",
                fee=0.0,
                realized_pnl=0.0,
                unrealized_pnl=0.0,
            )
        with repo.store.lock:
            fills = repo.store.connection.execute(
                "SELECT quantity, price FROM portfolio_fills WHERE client_order_id=? ORDER BY occurred_at", (cid,)
            ).fetchall()
            average = repo.store.connection.execute(
                "SELECT average_fill_price FROM portfolio_orders WHERE client_order_id=?", (cid,)
            ).fetchone()[0]
        assert [(quantity, round(price, 9)) for quantity, price in fills] == [(10.0, 102.0), (2.0, 96.0)]
        assert average == 101.0
    finally:
        repo.close()


# --------------------------------------------------------------------------- #
# Deferred BUY phase crash window
# --------------------------------------------------------------------------- #


def test_accepted_deferred_buy_with_unflipped_phase_does_not_complete(tmp_path: Path) -> None:
    """Q3A-1/Q3A-3: the broker accepted the deferred BUY but the crash hit
    before the buy_pending flip; the retry must track it, not complete."""
    broker = _SimBroker((_position("AAPL", 400),))
    runtime, repository = _runtime(tmp_path, broker, {"AAPL": 0.15, "MSFT": 0.20})
    first = runtime.process_closed_bars(_bars(("AAPL", "MSFT")))
    assert first.status == "sell_phase_submitted" and first.decision_id is not None
    sell = next(order for order in first.orders if order.side == PortfolioOrderSide.SELL)
    _fill(repository, sell)
    broker.positions = [_position("AAPL", 400 - sell.quantity)]
    broker.open_symbols.discard("AAPL")
    assert runtime.advance_rebalance().status == "buy_phase_submitted"  # type: ignore[union-attr]
    submits = len(broker.submitted)
    # Simulated SIGKILL between the BUY submit and the phase flip.
    repository.update_rebalance_execution(first.decision_id, "buy_ready")

    retried = runtime.advance_rebalance()

    assert retried is not None and retried.status != "rebalance_complete"
    assert len(broker.submitted) == submits
    state = repository.current_rebalance_execution("acct", Market.EQUITY)
    assert state is not None and state.phase == "buy_pending"


# --------------------------------------------------------------------------- #
# BUY admission: live hard-dollar cap with working BUYs, hostile remainders
# --------------------------------------------------------------------------- #

_WIDE_LIMITS = PortfolioRiskLimits(
    daily_loss_limit=0.20,
    drawdown_halt=0.75,
    maximum_gross=0.95,
    maximum_equity=0.95,
    maximum_equity_asset=0.95,
    minimum_cash=0.02,
    maximum_positions=8,
)


def _live_decision(tmp_path: Path, working_remaining: float) -> _SimBroker:
    broker = _SimBroker((_position("AAPL", 200),))  # $20k managed
    broker.working_buy_orders = lambda: (  # type: ignore[attr-defined]
        BrokerWorkingBuyOrder("working-nvda", "NVDA", Market.EQUITY, working_remaining, 100, None),
    )
    runtime, _repository = _runtime(
        tmp_path, broker, {"AAPL": 0.20, "MSFT": 0.08}, mode="live", cap=30_000.0, limits=_WIDE_LIMITS
    )
    runtime.process_closed_bars(_bars(("AAPL", "MSFT")))
    return broker


def test_live_cap_counts_working_buy_commitments(tmp_path: Path) -> None:
    """Q5E-1: $20k managed + $5k working BUY under a $30k cap leaves $5k:
    the $8k MSFT BUY is scaled to 50 shares, not admitted at 80."""
    broker = _live_decision(tmp_path, 50)
    buys = [(order.symbol, order.quantity) for order in broker.submitted if order.side == PortfolioOrderSide.BUY]
    assert buys == [("MSFT", 50.0)]


def test_live_cap_exhausted_by_working_buy_suppresses_new_buy(tmp_path: Path) -> None:
    """Q5E-1: a $10k working BUY fills the cap; no new BUY is submitted."""
    broker = _live_decision(tmp_path, 100)
    assert [order for order in broker.submitted if order.side == PortfolioOrderSide.BUY] == []


def test_hostile_durable_buy_remainder_halts_the_decision(tmp_path: Path) -> None:
    """Q5E-5: a persisted BUY whose fill exceeds its quantity raises out of
    the decision path (halt) instead of sizing or silently admitting."""
    broker = _SimBroker()
    runtime, repository = _runtime(tmp_path, broker, {"MSFT": 0.10}, limits=_WIDE_LIMITS)
    session = repository.begin_session("acct", "digest", "paper")
    from parayabuck.execution.portfolio import RiskOverlayDecision
    from parayabuck.portfolio.models import TargetPortfolio

    target = TargetPortfolio("other", "1", T0, (), 1.0)
    decision_id = repository.record_decision_with_orders(
        session, "acct", "digest", target, RiskOverlayDecision(target, False, (), 0.0, 0.0), Market.EQUITY, ()
    )
    hostile = PortfolioOrderRequest(
        client_order_id="hostile-buy",
        decision_id=decision_id,
        symbol="NVDA",
        market=Market.EQUITY,
        side=PortfolioOrderSide.BUY,
        quantity=10.0,
        reference_price=100.0,
        decision_timestamp=T0,
        strategy_name="other",
        strategy_version="1",
    )
    repository.record_order(hostile, "submitted")
    with repository.store.lock, repository.store.connection:
        repository.store.connection.execute(
            "UPDATE portfolio_orders SET cumulative_filled_quantity=25 WHERE client_order_id='hostile-buy'"
        )
    with pytest.raises(ValueError, match="Invalid durable BUY remainder"):
        runtime.process_closed_bars(_bars(("MSFT",)))
    assert broker.submitted == []
