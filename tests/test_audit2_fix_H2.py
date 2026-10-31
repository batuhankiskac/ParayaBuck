"""Audit wave 2 regressions for the equity signal basis, data feed and cross-check (H2).

Synthetic fixtures only: pinned timestamps, fake provider clients, and no
network, credentials or production paths.
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from alpaca.data.enums import DataFeed

from parayabuck.core.models import Bar, Market
from parayabuck.core.split_ratios import KNOWN_SPLIT_RATIOS
from parayabuck.data.adjustment import split_continuity_breach
from parayabuck.data.alpaca import AlpacaHistoricalData
from parayabuck.portfolio import ownership_rebase
from parayabuck.portfolio.runtime_service import PortfolioRuntime
from parayabuck.portfolio.signal_cross_check import (
    STATUS_SECONDARY_UNAVAILABLE,
    SignalCrossCheckCoordinator,
    SourceDecision,
    compare_sources,
)
from parayabuck.runtime import EngineRuntime

D0 = datetime(2026, 10, 19, 4, 0, tzinfo=UTC)
D1 = datetime(2026, 10, 20, 4, 0, tzinfo=UTC)
D2 = datetime(2026, 10, 21, 4, 0, tzinfo=UTC)
D3 = datetime(2026, 10, 22, 4, 0, tzinfo=UTC)


def _bar(symbol: str, stamp: datetime, close: float = 100.0) -> Bar:
    return Bar(stamp, symbol, "1d", close, close, close, close, 1000.0, Market.EQUITY)


def _engine() -> EngineRuntime:
    engine = EngineRuntime.__new__(EngineRuntime)
    engine._warming_up = False
    engine._circuit_breaker_reason = None
    engine._reconcile_failed = False
    engine._state_lock = threading.RLock()
    engine._portfolio_lock = threading.RLock()
    engine._entry_risk_latch = {Market.EQUITY: None}
    engine._market_entry_halts = {Market.EQUITY: None}
    engine._portfolio_runtimes = ()
    return engine


def _equity_runtime(symbol: str, history: dict[str, list[Bar]], minimum_history_bars: int = 2) -> PortfolioRuntime:
    runtime = object.__new__(PortfolioRuntime)
    runtime.market = Market.EQUITY
    runtime.strategy = SimpleNamespace(timeframe="1d", minimum_history_bars=minimum_history_bars)
    runtime.symbols = tuple(history)
    runtime.history = {key: list(series) for key, series in history.items()}
    runtime.signal_verified_through_by_symbol = {}
    runtime.previous_timestamp = None
    return runtime


class _Provider:
    """Batched splits answer with a fixed window per symbol."""

    def __init__(self, windows: dict[str, list[Bar]]) -> None:
        self.windows = windows

    def get_bars_batch(self, symbols, timeframe, *, start, end, adjustment, outcome=None):  # noqa: ANN001
        return {symbol: list(self.windows[symbol]) for symbol in symbols if symbol in self.windows}


@pytest.fixture
def alert_events(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    events: list[str] = []
    monkeypatch.setattr(
        "parayabuck.runtime.alerts",
        type("A", (), {"emit": staticmethod(lambda kind, **kw: events.append(kind))}),
    )
    return events


# --- T2-8-1: one shared split-ratio tuple -----------------------------------


def test_books_and_signal_side_share_one_split_ratio_tuple() -> None:
    assert ownership_rebase.KNOWN_SPLIT_RATIOS is KNOWN_SPLIT_RATIOS


# --- T2-7-5: a 401 is a credential failure, never an entitlement downgrade ---


def _status_error(status: int, body: str) -> Exception:
    error = RuntimeError(body)
    error.status_code = status  # type: ignore[attr-defined]
    error.response = SimpleNamespace(text=body)  # type: ignore[attr-defined]
    return error


def test_unauthorized_key_is_not_classified_as_a_missing_sip_entitlement() -> None:
    assert AlpacaHistoricalData._is_entitlement_error(_status_error(401, '{"message": "unauthorized."}')) is False


def test_forbidden_sip_request_is_still_an_entitlement_error() -> None:
    assert AlpacaHistoricalData._is_entitlement_error(_status_error(403, '{"message": "forbidden"}')) is True


# --- T2-7-2: the cache label and the request name the same feed --------------


class _FeedFlip:
    """A cache whose read happens between the feed label and the request: the
    concurrent downgrade lands in that window."""

    def __init__(self, history: AlpacaHistoricalData) -> None:
        self.history = history
        self.labels: list[str] = []

    def get(self, symbol: str, timeframe: str, **kwargs: Any) -> list[Bar]:
        self.labels.append(str(kwargs.get("feed")))
        self.history._stock_feed = DataFeed("iex")
        self.history._sip_fallback_active = True
        return []


def test_cache_label_and_request_use_the_same_feed_across_a_downgrade() -> None:
    requests: list[Any] = []

    def _get_stock_bars(request: Any) -> SimpleNamespace:
        requests.append(request)
        return SimpleNamespace(df=SimpleNamespace(empty=True))

    history = AlpacaHistoricalData.__new__(AlpacaHistoricalData)
    history._configured_stock_feed = "sip"
    history._stock_feed = DataFeed("sip")
    history._sip_fallback_active = False
    history._sip_fallback_since = None
    history._sip_embargo = timedelta(0)
    history._provider_fallbacks = 0
    history._stock_client = SimpleNamespace(get_stock_bars=_get_stock_bars)
    cache = _FeedFlip(history)
    history._cache = cache  # type: ignore[assignment]

    history.get_bars_batch(["QLD"], "1d", start=D0, end=D2, adjustment="splits")

    assert requests, "the provider request was not made"
    label = cache.labels[0]
    requested = str(requests[0].feed).lower().split(".")[-1]
    assert label == requested == "sip"


# --- T2-8-1: split continuity -------------------------------------------------


def test_split_continuity_flags_a_post_split_close_over_unrebased_priors() -> None:
    stored = [_bar("AAA", D0, 100.0), _bar("AAA", D1, 100.0), _bar("AAA", D2, 100.0)]
    provider = [*stored, _bar("AAA", D3, 50.0)]  # 2-for-1 on D3, priors not rebased
    assert split_continuity_breach(provider, stored) is True


def test_split_continuity_accepts_a_rebased_window() -> None:
    stored = [_bar("AAA", D0, 100.0), _bar("AAA", D1, 100.0), _bar("AAA", D2, 100.0)]
    provider = [_bar("AAA", D0, 50.0), _bar("AAA", D1, 50.0), _bar("AAA", D2, 50.0), _bar("AAA", D3, 50.0)]
    assert split_continuity_breach(provider, stored) is False


def test_split_continuity_ignores_an_ordinary_overnight_move() -> None:
    stored = [_bar("AAA", D0, 100.0), _bar("AAA", D1, 100.0), _bar("AAA", D2, 100.0)]
    provider = [*stored, _bar("AAA", D3, 101.5)]
    assert split_continuity_breach(provider, stored) is False


def test_split_continuity_needs_stored_history() -> None:
    assert split_continuity_breach([_bar("AAA", D3, 50.0)], []) is False


def test_verifier_refuses_an_unrebased_split_and_keeps_the_watermark(alert_events: list[str]) -> None:
    stored = [_bar("AAA", D0, 100.0), _bar("AAA", D1, 100.0), _bar("AAA", D2, 100.0)]
    provider = [*stored, _bar("AAA", D3, 50.0)]
    runtime = _equity_runtime("AAA", {"AAA": stored})
    engine = _engine()
    engine._symbols_by_market = {Market.EQUITY: {"AAA"}}
    engine._portfolio_runtimes = (runtime,)
    engine._historical = _Provider({"AAA": provider})

    assert engine.verify_equity_signal_basis(required_through=D3) is False
    assert runtime.signal_verified_through_by_symbol == {}
    assert "signal_adjustment_failure" in alert_events


def test_verifier_adopts_a_provider_window_that_rebased_its_priors(alert_events: list[str]) -> None:
    stored = [_bar("AAA", D0, 100.0), _bar("AAA", D1, 100.0), _bar("AAA", D2, 100.0)]
    rebased = [_bar("AAA", D0, 50.0), _bar("AAA", D1, 50.0), _bar("AAA", D2, 50.0), _bar("AAA", D3, 50.0)]
    runtime = _equity_runtime("AAA", {"AAA": stored})
    engine = _engine()
    engine._symbols_by_market = {Market.EQUITY: {"AAA"}}
    engine._portfolio_runtimes = (runtime,)
    engine._historical = _Provider({"AAA": rebased})

    assert engine.verify_equity_signal_basis(required_through=D3) is True
    assert runtime.signal_verified_through_by_symbol == {"AAA": D3}


# --- T2-8-2: the watermark counts final bars only -----------------------------


def test_partial_newest_provider_bar_is_neither_coverage_nor_watermark(alert_events: list[str]) -> None:
    stored = [_bar("AAA", D0, 100.0), _bar("AAA", D1, 100.0), _bar("AAA", D2, 100.0)]
    future_partial = datetime.now(UTC) + timedelta(days=3)
    provider = [*stored, _bar("AAA", future_partial, 100.0)]
    runtime = _equity_runtime("AAA", {"AAA": stored})
    engine = _engine()
    engine._symbols_by_market = {Market.EQUITY: {"AAA"}}
    engine._portfolio_runtimes = (runtime,)
    engine._historical = _Provider({"AAA": provider})

    assert engine.verify_equity_signal_basis(required_through=D2) is True
    assert runtime.signal_verified_through_by_symbol == {"AAA": D2}


def test_decision_session_newer_than_the_last_final_adoption_forces_a_verify(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = _engine()
    engine._symbols_by_market = {Market.EQUITY: {"AAA"}}
    engine._signal_adjustment_state().update(
        {"healthy": True, "last_success_at": datetime.now(UTC), "verified_through_by_symbol": {"AAA": D1}}
    )
    calls: list[datetime | None] = []

    def _verify(required_through: datetime | None = None) -> bool:
        calls.append(required_through)
        return False

    monkeypatch.setattr(engine, "verify_equity_signal_basis", _verify)
    assert engine._equity_signal_basis_verified(D2) is False
    assert engine._equity_signal_basis_check(D2) == "signal_adjustment_unverified"
    assert calls == [D2]


# --- T2-7-1: the trade-update path reads the cached basis only ---------------


class _AdvancePortfolio:
    market = Market.EQUITY
    history: dict[str, list[Bar]] = {}

    def __init__(self) -> None:
        self.seen: dict[str, Any] = {}

    def advance_rebalance(self, **kwargs: Any) -> SimpleNamespace:
        self.seen.update(kwargs)
        return SimpleNamespace(status="buy_ready", decision_id="dec-1")


def test_cached_basis_latch_never_calls_the_provider() -> None:
    engine = _engine()
    engine._symbols_by_market = {Market.EQUITY: {"QLD"}}

    def _no_provider(*_args: Any, **_kwargs: Any) -> bool:
        raise AssertionError("the trade-update path must not call the provider")

    engine.verify_equity_signal_basis = _no_provider  # type: ignore[method-assign]
    assert engine._equity_signal_basis_cached_latch() == "signal_adjustment_unverified"
    engine._signal_adjustment_state().update({"healthy": True, "last_success_at": datetime.now(UTC)})
    assert engine._equity_signal_basis_cached_latch() is None


def test_trade_update_advance_withholds_buys_without_a_provider_call() -> None:
    engine = _engine()
    engine._symbols_by_market = {Market.EQUITY: {"QLD"}}
    portfolio = _AdvancePortfolio()
    engine._portfolio_runtimes = (portfolio,)

    def _no_provider(*_args: Any, **_kwargs: Any) -> bool:
        raise AssertionError("the trade-update path must not call the provider")

    engine.verify_equity_signal_basis = _no_provider  # type: ignore[method-assign]
    engine._advance_portfolio_rebalance(Market.EQUITY, cached_basis_only=True)
    assert portfolio.seen["submit_deferred_buys"] is False
    assert engine._basis_buy_deferred is True


def test_monitor_resumes_only_buys_the_fill_path_deferred(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = _engine()
    engine._startup_committed = True  # the deferred BUY resumes only after the startup commit (C3-2-4)
    engine._symbols_by_market = {Market.EQUITY: {"QLD"}}
    resumed: list[Market] = []
    monkeypatch.setattr(engine, "verify_equity_signal_basis", lambda *a, **k: True)
    monkeypatch.setattr(engine, "_check_market_data_feed", lambda: None)
    monkeypatch.setattr(engine, "_advance_portfolio_rebalance", lambda market, **_kw: resumed.append(market))
    worker = SimpleNamespace(last_error=None)

    # A startup pass with no deferred BUY must not advance the rebalance.
    engine._signal_adjustment_monitor_pass(worker)  # type: ignore[arg-type]
    assert resumed == []

    engine._basis_buy_deferred = True  # set by the trade-update path when it defers a BUY
    engine._signal_adjustment_monitor_pass(worker)  # type: ignore[arg-type]

    assert resumed == [Market.EQUITY]
    assert engine._basis_buy_deferred is False
    assert worker.last_error is None


# --- T2-9-1: the cross-check compares target weights -------------------------


def _strategy_stub() -> Any:
    return SimpleNamespace(timeframe="1d")


def _history_stub() -> dict[str, list[Bar]]:
    return {}


def test_weight_gap_beyond_the_rebalance_band_is_flagged_as_an_alert(monkeypatch: pytest.MonkeyPatch) -> None:
    from parayabuck.portfolio import signal_cross_check as scc

    weights = iter((0.30, 0.45))

    def _decision(strategy: Any, history: Any, session_bars: Any, decision_timestamp: Any) -> SourceDecision:
        return SourceDecision("uptrend", 100.0, 99.0, next(weights), {})

    monkeypatch.setattr(scc, "_source_decision", _decision)
    monkeypatch.setattr(scc, "cross_check_symbols", lambda strategy: ("QLD",))
    primary = {"QLD": _bar("QLD", D3, 100.0)}
    secondary = {"QLD": _bar("QLD", D3, 100.0)}

    outcome = compare_sources(
        _strategy_stub(),
        _history_stub(),
        primary,
        secondary,
        D3,
        close_divergence_bps=50.0,
        rebalance_band=0.005,
    )

    assert outcome.status == "agree" and not outcome.hold
    assert outcome.weight_divergence is True
    assert outcome.weight_gap == pytest.approx(0.15)
    assert outcome.to_metadata()["weight_divergence"] is True


def test_weight_gap_inside_the_band_is_not_flagged(monkeypatch: pytest.MonkeyPatch) -> None:
    from parayabuck.portfolio import signal_cross_check as scc

    weights = iter((0.300, 0.302))
    monkeypatch.setattr(
        scc,
        "_source_decision",
        lambda *_args: SourceDecision("uptrend", 100.0, 99.0, next(weights), {}),
    )
    monkeypatch.setattr(scc, "cross_check_symbols", lambda strategy: ("QLD",))
    bars = {"QLD": _bar("QLD", D3, 100.0)}

    outcome = compare_sources(
        _strategy_stub(), _history_stub(), bars, bars, D3, close_divergence_bps=50.0, rebalance_band=0.005
    )

    assert outcome.weight_divergence is False


# --- T2-3-2: the cross-check never outlives the engine's decision window -----

_CLOSE = datetime(2026, 9, 29, 20, 0, tzinfo=UTC)


def test_cross_check_resolves_by_the_engine_deadline_when_the_gate_clears_late(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from parayabuck.portfolio import signal_cross_check as scc

    monkeypatch.setattr(scc, "cross_check_symbols", lambda _strategy: ("QQQ", "QLD"))
    hard = _CLOSE + timedelta(minutes=40)
    coordinator = SignalCrossCheckCoordinator(
        sip_embargo=timedelta(minutes=15),
        retry_window=timedelta(minutes=10),
        retry_interval=timedelta(seconds=60),
        close_divergence_bps=50.0,
        session_close=lambda _session: _CLOSE,
        session_deadline=lambda _session: hard,
    )
    strategy = SimpleNamespace(timeframe="1d")
    bars = [_bar("QQQ", D3, 440.0), _bar("QLD", D3, 100.0)]

    def _failing_fetch(symbols: list[str], start: datetime, end: datetime) -> dict[str, Bar]:
        return {}

    first = coordinator.step(strategy, {}, bars, _failing_fetch, _CLOSE + timedelta(minutes=35))
    assert first.action == "defer"
    assert first.retry_at is not None and first.retry_at <= hard
    assert max(state.deadline for state in coordinator._pending.values()) <= hard

    last = coordinator.step(strategy, {}, bars, _failing_fetch, hard - timedelta(minutes=1))

    assert last.action == "proceed"
    assert last.outcome is not None and last.outcome.status == STATUS_SECONDARY_UNAVAILABLE


def test_cross_check_never_decides_a_batch_already_past_the_engine_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from parayabuck.portfolio import signal_cross_check as scc

    monkeypatch.setattr(scc, "cross_check_symbols", lambda _strategy: ("QQQ", "QLD"))
    hard = _CLOSE + timedelta(minutes=40)
    coordinator = SignalCrossCheckCoordinator(
        sip_embargo=timedelta(minutes=15),
        retry_window=timedelta(minutes=10),
        retry_interval=timedelta(seconds=60),
        close_divergence_bps=50.0,
        session_close=lambda _session: _CLOSE,
        session_deadline=lambda _session: hard,
    )
    strategy = SimpleNamespace(timeframe="1d")
    bars = [_bar("QQQ", D3, 440.0), _bar("QLD", D3, 100.0)]

    step = coordinator.step(strategy, {}, bars, lambda symbols, start, end: {}, hard + timedelta(hours=1))

    # Past the engine window the batch waits for the engine's expiry, exactly as before.
    assert step.action == "defer"


# --- T2-5-2: a managed pin with no provider history does not hold readiness --


def _readiness_engine(history: dict[str, list[Bar]], pins: set[str]) -> EngineRuntime:
    engine = _engine()
    engine._running = False
    engine._state = "warming_up"
    engine._not_ready_symbols = set(pins)
    engine._managed_pin_symbols = set(pins)
    engine.app_config = SimpleNamespace(universe=SimpleNamespace(equity=SimpleNamespace(symbols=("QLD",))))
    runtime = _equity_runtime("QLD", history)
    runtime.symbols = tuple(dict.fromkeys((*history, *pins)))
    engine._portfolio_runtimes = (runtime,)
    return engine


def test_pin_without_provider_history_is_not_held_as_not_ready() -> None:
    bars = [_bar("QLD", D0), _bar("QLD", D1), _bar("QLD", D2)]
    engine = _readiness_engine({"QLD": bars}, {"ZZZ"})

    engine._refresh_readiness()

    assert "ZZZ" not in engine._not_ready_symbols


def test_pin_with_history_that_is_still_short_stays_not_ready() -> None:
    bars = [_bar("QLD", D0), _bar("QLD", D1), _bar("QLD", D2)]
    engine = _readiness_engine({"QLD": bars, "ZZZ": [_bar("ZZZ", D2)]}, {"ZZZ"})

    engine._refresh_readiness()

    assert "ZZZ" in engine._not_ready_symbols


def test_strategy_universe_symbol_without_history_still_holds_readiness() -> None:
    bars = [_bar("QLD", D0), _bar("QLD", D1), _bar("QLD", D2)]
    engine = _readiness_engine({"QLD": bars}, set())
    engine._not_ready_symbols = {"QLD"}
    engine._managed_pin_symbols = set()
    engine._portfolio_runtimes[0].history["QLD"] = []  # type: ignore[index]

    engine._refresh_readiness()

    assert "QLD" in engine._not_ready_symbols
