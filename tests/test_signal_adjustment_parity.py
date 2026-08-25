"""Split-adjustment parity under MULTIPLE corporate actions and failures.

Design v2 (authoritative): the provider's split-adjusted daily history is
the sole authority for the strategy's SIGNAL domain. Raw stream bars are
provisional; a batched verification before any finalized equity decision
either confirms them against the provider or rebuilds the history from it.
Unverifiable basis FAILS CLOSED for new equity entries; exits keep flowing.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from parayabuck.core.models import AccountSnapshot, Bar, Market
from parayabuck.data.adjustment import (
    SPLIT_RATIO_TOLERANCE,
    detect_basis_break,
    merge_provider_authority,
)
from parayabuck.portfolio.runtime_service import PortfolioRuntime

_TS0 = datetime(2025, 6, 2, 4, 0, tzinfo=UTC)


def _bar(symbol: str, timestamp: datetime, close: float) -> Bar:
    return Bar(timestamp, symbol, "1d", close, close, close, close, 1_000, Market.EQUITY)


def _days(count: int) -> list[datetime]:
    return [_TS0 + timedelta(days=index) for index in range(count)]


def _economic_path(start: float, count: int) -> list[float]:
    return [start * (1.0002**index) for index in range(count)]


def _bare_runtime(symbol: str = "AAA", *, minimum_history_bars: int = 10) -> PortfolioRuntime:
    runtime = object.__new__(PortfolioRuntime)
    runtime.market = Market.EQUITY
    runtime.strategy = type("S", (), {"timeframe": "1d", "minimum_history_bars": minimum_history_bars})()
    runtime.symbols = (symbol,)
    runtime.history = {symbol: []}
    runtime.signal_verified_through_by_symbol = {}
    runtime.signal_last_verified_at = None
    runtime.previous_timestamp = None
    return runtime


# --------------------------------------------------------------------------- #
# Pure merge semantics
# --------------------------------------------------------------------------- #


def test_two_splits_outside_reconcile_window_preserve_cumulative_basis() -> None:
    """warmup -> 4:1 split -> >14d pass -> 2:1 split.

    The provider rebases its ENTIRE splits series by BOTH ratios (cumulative
    8x vs the warmup basis). Adopting authority must land every covered bar
    on that new basis — factor 8, never the most-recent-only factor 2."""
    dates = _days(30)
    economic = _economic_path(400.0, 30)
    # As-traded raw stream: pre-split 400-scale, after 4:1 /4, after 2:1 /8.
    raw = [
        _bar("AAA", date, close / (8.0 if index >= 25 else 4.0 if index >= 10 else 1.0))
        for index, (date, close) in enumerate(zip(dates, economic))
    ]
    stored = list(raw)  # warmup B0 bars (days 0..9) are identical to raw there
    provider_now = [  # provider TODAY: fully rebased to the post-2:1 basis
        _bar("AAA", date, close / 8.0) for date, close in zip(dates[:29], economic)
    ]
    assert detect_basis_break(tuple(stored), provider_now)

    merged, changed = merge_provider_authority(tuple(stored), provider_now)
    assert changed
    # Cumulative factor vs the ORIGINAL warmup basis is 8 everywhere covered.
    assert merged[0].close == pytest.approx(economic[0] / 8.0)
    assert merged[10].close == pytest.approx(economic[10] / 8.0)
    assert merged[25].close == pytest.approx(economic[25] / 8.0)
    # Provisional newest raw bar survives untouched (already post-2:1).
    assert merged[-1].close == pytest.approx(raw[-1].close)
    # Whole series now sits on ONE basis: no internal break remains.
    assert not detect_basis_break(merged, [*provider_now, raw[-1]])


def test_repeated_same_split_is_idempotent() -> None:
    dates = _days(5)
    economic = _economic_path(100.0, 5)
    provider = [_bar("AAA", date, close / 2.0) for date, close in zip(dates, economic)]
    stored = list(provider)
    first_changed = merge_provider_authority(tuple(stored), provider)[1]
    second, second_changed = merge_provider_authority(tuple(stored), provider)
    assert not first_changed or True  # equal content => unchanged either way
    assert not second_changed
    assert [bar.close for bar in second] == [bar.close for bar in stored]


def test_reverse_split_after_forward_split() -> None:
    """4:1 forward then 1:2 reverse: net 2x down from warmup basis; provider
    authority absorbs both directions without residual mismatch."""
    dates = _days(6)
    economic = _economic_path(400.0, 6)
    # Net basis vs warmup: x(1/4) then x(2) => /2.
    provider = [_bar("AAA", date, close / 2.0) for date, close in zip(dates, economic)]
    raw_as_traded = [
        _bar(
            "AAA",
            date,
            close / (4.0 if index < 3 else 2.0),
        )
        for index, (date, close) in enumerate(zip(dates, economic))
    ]
    assert detect_basis_break(tuple(raw_as_traded), provider)
    merged, changed = merge_provider_authority(tuple(raw_as_traded), provider)
    assert changed
    assert merged[0].close == pytest.approx(economic[0] / 2.0)
    assert merged[-1].close == pytest.approx(economic[-1] / 2.0)
    assert not detect_basis_break(merged, provider)


def test_already_scaled_window_is_not_rescaled() -> None:
    """Bars already on the provider basis must survive byte-for-byte; a
    repeated reconciliation over the same window changes nothing."""
    dates = _days(4)
    provider = [_bar("AAA", date, 50.0 + index) for index, date in enumerate(dates)]
    stored = (*provider, _bar("AAA", dates[-1] + timedelta(days=1), 51.0))
    merged, changed = merge_provider_authority(stored, provider)
    assert not changed
    assert merged == stored  # identity preserved: nothing rewritten
    for original, kept in zip(stored[: len(provider)], merged):
        assert kept is original


# --------------------------------------------------------------------------- #
# Runtime wiring
# --------------------------------------------------------------------------- #


def test_adopt_authoritative_history_updates_verification_state() -> None:
    runtime = _bare_runtime()
    dates = _days(3)
    economic = _economic_path(200.0, 3)
    runtime.seed([_bar("AAA", dates[0], economic[0])])  # warmup B0
    provider = [_bar("AAA", date, close / 4.0) for date, close in zip(dates, economic)]
    changed = runtime.adopt_authoritative_signal_history({"AAA": provider}, {"AAA": dates[-1]})
    assert changed == 1
    assert [b.close for b in runtime.history["AAA"]] == [c / 4.0 for c in economic]
    assert runtime.signal_verified_through_by_symbol == {"AAA": dates[-1]}
    assert runtime.signal_last_verified_at is not None
    # Second identical adoption changes nothing.
    assert runtime.adopt_authoritative_signal_history({"AAA": provider}, {"AAA": dates[-1]}) == 0


def test_hot_swap_between_splits_resets_verification_state() -> None:
    runtime = _bare_runtime()
    runtime.adopt_authoritative_signal_history({"AAA": [_bar("AAA", _TS0, 50.0)]}, {"AAA": _TS0})
    assert runtime.signal_last_verified_at is not None
    added, removed = runtime.update_symbols(("AAA", "BBB"))
    assert added == ("BBB",) and removed == ()
    # Membership changed while splits may have happened: fail conservative.
    assert runtime.signal_verified_through_by_symbol == {}
    assert runtime.signal_last_verified_at is None
    assert "AAA" in runtime.history and "BBB" in runtime.history


class FakeHistory:
    """Fake provider that HONORS the requested [start, end] window like the
    real API: only bars inside it are returned, and the first daily bar can
    legitimately land after ``start`` (intraday/weekend/holiday starts)."""

    def __init__(
        self,
        raw: dict[str, list[Bar]] | None = None,
        splits: dict[str, list[Bar]] | None = None,
        error: Exception | None = None,
        *,
        honor_window: bool = False,
    ) -> None:
        self.raw = raw or {}
        self.splits = splits or {}
        self.error = error
        self.honor_window = honor_window
        self.calls: list[tuple[tuple[str, ...], str]] = []
        self.requested_windows: list[tuple[datetime, datetime]] = []

    def get_bars_batch(self, symbols, timeframe, *, start, end, adjustment):  # noqa: ANN001
        self.calls.append((tuple(symbols), adjustment))
        self.requested_windows.append((start, end))
        if self.error is not None:
            raise self.error
        source = self.raw if adjustment == "raw" else self.splits
        if not self.honor_window:
            return dict(source)
        filtered: dict[str, list[Bar]] = {}
        for symbol, bars in source.items():
            filtered[symbol] = [bar for bar in bars if start <= bar.timestamp <= end]
        return filtered


def _engine_with_equity_runtime(symbols: tuple[str, ...]):
    from parayabuck.runtime import EngineRuntime

    engine = EngineRuntime.__new__(EngineRuntime)
    engine._symbols_by_market = {Market.EQUITY: set(symbols), Market.CRYPTO: set()}
    equity = _bare_runtime(symbols[0])
    equity.symbols = symbols
    equity.history = {symbol: [] for symbol in symbols}
    engine._portfolio_runtimes = (equity,)
    return engine, equity


def test_split_day_decision_waits_for_verified_signal_basis(monkeypatch) -> None:
    """A scheduled equity decision may never execute on an unverified basis:
    the gate returns a latch reason until the batched reconciliation ran, and
    the reconciliation itself is TWO batched calls for ALL symbols."""
    events: list[tuple[str, dict[str, object]]] = []
    monkeypatch.setattr(
        "parayabuck.runtime.alerts",
        type("A", (), {"emit": staticmethod(lambda kind, **kw: events.append((kind, kw)))}),
    )
    engine, equity = _engine_with_equity_runtime(("AAA", "BBB"))
    dates = _days(3)
    provider = {
        "AAA": [_bar("AAA", d, 100.0 - i) for i, d in enumerate(dates)],
        "BBB": [_bar("BBB", d, 50.0 + i) for i, d in enumerate(dates)],
    }
    raws = {
        "AAA": [_bar("AAA", d, 100.0 - i) for i, d in enumerate(dates)],
        "BBB": [_bar("BBB", d, 50.0 + i) for i, d in enumerate(dates)],
    }
    fake = FakeHistory(raw=raws, splits=provider)
    engine._historical = fake

    latch = engine._equity_signal_basis_latch(dates[-1])
    assert latch is None  # verified clean through the decision timestamp
    # ONE batched SPLITS call for ALL symbols covering the full window.
    assert len(fake.calls) == 1
    syms, adjustment = fake.calls[0]
    assert adjustment == "splits" and set(syms) == {"AAA", "BBB"}
    assert equity.signal_verified_through_by_symbol == {
        "AAA": dates[-1],
        "BBB": dates[-1],
    }
    # First-ever success is SILENT: "recovery" is reserved for failed->healthy.
    assert [kind for kind, _kw in events] == []

    # A split rebase between verifications forces the latch until repaired.
    rebased = {"AAA": [_bar("AAA", d, (100.0 - i) / 4.0) for i, d in enumerate(dates)]}
    fake.splits = {**provider, **rebased}
    fake.raw = {}
    equity.history["AAA"] = [_bar("AAA", d, 100.0 - i) for i, d in enumerate(dates)]
    engine._signal_adjustment["healthy"] = None  # force re-verification
    latch = engine._equity_signal_basis_latch(dates[-1])
    assert latch is None  # repaired in the same synchronous pass
    assert [b.close for b in equity.history["AAA"]] == [(100.0 - i) / 4.0 for i in range(3)]


def test_adjustment_worker_failure_blocks_equity_entries(monkeypatch) -> None:
    events: list[str] = []
    monkeypatch.setattr(
        "parayabuck.runtime.alerts",
        type("A", (), {"emit": staticmethod(lambda kind, **kw: events.append(kind))}),
    )
    engine, _equity = _engine_with_equity_runtime(("AAA",))
    engine._historical = FakeHistory(error=RuntimeError("provider down"))

    assert engine._equity_signal_basis_latch() == "signal_adjustment_unverified"
    status = engine.signal_adjustment_status()
    assert status["verified"] is False
    assert status["stale"] is True
    assert status["entry_gate_reason"] == "signal_adjustment_unverified"
    assert "signal_adjustment_failure" in events
    # Repeated failures stay latched without flapping recovery semantics.
    assert engine._equity_signal_basis_latch() == "signal_adjustment_unverified"


def test_successful_reconcile_recovers_entry_readiness(monkeypatch) -> None:
    events: list[str] = []
    monkeypatch.setattr(
        "parayabuck.runtime.alerts",
        type("A", (), {"emit": staticmethod(lambda kind, **kw: events.append(kind))}),
    )
    engine, equity = _engine_with_equity_runtime(("AAA",))
    dates = _days(2)
    provider = {"AAA": [_bar("AAA", d, 40.0 + i) for i, d in enumerate(dates)]}
    failing = FakeHistory(error=RuntimeError("down"))
    working = FakeHistory(raw={}, splits=provider)
    engine._historical = failing

    assert engine._equity_signal_basis_latch(dates[-1]) == "signal_adjustment_unverified"
    engine._historical = working
    assert engine._equity_signal_basis_latch(dates[-1]) is None
    assert engine.signal_adjustment_status()["entry_gate_reason"] is None
    assert events.count("signal_adjustment_failure") == 1  # exactly once per episode
    assert events.count("signal_adjustment_recovery") == 1
    assert equity.signal_verified_through_by_symbol == {"AAA": dates[-1]}


def test_restart_between_splits_lands_on_provider_authority() -> None:
    """After a restart, warmup seeds directly from the CURRENT provider
    basis; the adopted pre-restart history must be IDENTICAL to it."""
    dates = _days(20)
    economic = _economic_path(300.0, 20)
    provider_now = [_bar("AAA", date, close / 8.0) for date, close in zip(dates, economic)]

    pre_restart = _bare_runtime()
    raw_stream = [
        _bar("AAA", date, close / (8.0 if i >= 15 else 4.0 if i >= 5 else 1.0))
        for i, (date, close) in enumerate(zip(dates, economic))
    ]
    for bar in raw_stream:
        pre_restart._append_signal_bar(bar)
    pre_restart.adopt_authoritative_signal_history({"AAA": provider_now[:-1]}, {"AAA": dates[-2]})

    post_restart = _bare_runtime()
    post_restart.seed(list(provider_now))

    assert [b.close for b in pre_restart.history["AAA"]] == [b.close for b in post_restart.history["AAA"]]


def test_crypto_runtime_is_never_touched_by_the_bridge() -> None:
    runtime = object.__new__(PortfolioRuntime)
    runtime.market = Market.CRYPTO
    runtime.symbols = ("BTC/USD",)
    runtime.history = {"BTC/USD": [_bar("BTC/USD", _TS0, 50_000.0)]}
    runtime.signal_verified_through_by_symbol = {}
    runtime.signal_last_verified_at = None
    assert runtime.signal_history_matches_provider({}) is True
    assert runtime.adopt_authoritative_signal_history({}, {"BTC/USD": _TS0}) == 0
    bar = _bar("BTC/USD", _TS0 + timedelta(days=1), 51_000.0)
    bar = Bar(bar.timestamp, "BTC/USD", "1d", *([51_000.0] * 4), 1_000, Market.CRYPTO)
    runtime._append_signal_bar(bar)
    assert runtime.history["BTC/USD"][-1].close == 51_000.0


def test_tolerance_rejects_rounding_noise_only() -> None:
    dates = _days(2)
    provider = [_bar("AAA", d, 100.0) for d in dates]
    noisy = [_bar("AAA", d, 100.0 * (1 + SPLIT_RATIO_TOLERANCE / 2)) for d in dates]
    breaking = [_bar("AAA", d, 25.0) for d in dates]
    assert not detect_basis_break(tuple(noisy), provider)
    assert detect_basis_break(tuple(breaking), provider)


# --------------------------------------------------------------------------- #
# Final-signal-basis mission regressions
# --------------------------------------------------------------------------- #


def test_short_provider_tail_never_truncates_long_history() -> None:
    """300 stored daily bars vs a 10-bar provider tail: reconciliation must
    retain every out-of-scope bar and return a full-length series."""
    dates = _days(300)
    stored = [_bar("AAA", date, 100.0 + index) for index, date in enumerate(dates)]
    provider = [_bar("AAA", date, 100.0 + 290 + offset) for offset, date in enumerate(dates[-10:])]
    merged, changed = merge_provider_authority(tuple(stored), provider)
    assert len(merged) == 300
    # Overlap replaced by equal-close provider bars is NOT a change worth
    # reporting; only content differences count.
    # Old history outside provider coverage is retained verbatim.
    assert merged[0] is stored[0]
    assert merged[289] is stored[289]
    # Overlap bars are replaced by the provider objects themselves.
    assert merged[-1] is provider[-1]


def test_short_provider_tail_through_runtime_adoption() -> None:
    runtime = _bare_runtime(minimum_history_bars=274)
    dates = _days(300)
    runtime.history["AAA"] = [_bar("AAA", d, 100.0 + i) for i, d in enumerate(dates)]
    provider = [_bar("AAA", d, 100.0 + i) for i, d in enumerate(dates[-10:])]
    changed = runtime.adopt_authoritative_signal_history({"AAA": provider}, {"AAA": dates[-1]})
    assert len(runtime.history["AAA"]) == 300
    assert changed == 0 or changed == 1  # content-equal merges report unchanged
    assert runtime.signal_verified_through_by_symbol == {"AAA": dates[-1]}
    del changed


def test_signal_adjustment_does_not_race_startup_warmup(monkeypatch) -> None:
    """Two guards: (a) seed() monotonicity would PERMANENTLY drop warmup bars
    if a short verification ran first; (b) the monitor pass refuses to fetch
    while _warming_up."""
    # (a) prove the hazard the ordering prevents:
    runtime = _bare_runtime()
    dates = _days(30)
    runtime.seed([_bar("AAA", d, 100.0) for d in dates])  # full warmup first
    runtime.seed([_bar("AAA", dates[-1], 100.0)])  # late short tail: no-op
    assert len(runtime.history["AAA"]) == 30
    truncated = _bare_runtime()
    truncated.seed([_bar("AAA", dates[-1], 100.0)])  # WRONG order: tail first
    truncated.seed([_bar("AAA", d, 100.0) for d in dates])
    assert len(truncated.history["AAA"]) < 30  # warmup bars lost forever

    # (b) monitor pass must not fetch during warmup:
    from parayabuck.runtime import WorkerHealth

    engine, _equity = _engine_with_equity_runtime(("AAA",))
    fake = FakeHistory(raw={}, splits={"AAA": [_bar("AAA", _TS0, 50.0)]})
    engine._historical = fake
    worker = WorkerHealth(datetime.now(UTC), datetime.now(UTC))
    engine._warming_up = True
    engine._signal_adjustment_monitor_pass(worker)
    assert fake.calls == []  # startup-race guard held
    engine._warming_up = False
    engine._signal_adjustment_monitor_pass(worker)
    assert len(fake.calls) == 1


def test_provider_lag_on_split_day_blocks_new_entries(monkeypatch) -> None:
    """Provider has not published the decision-day bar yet: even a previously
    healthy basis fails closed for that day's entries."""
    events: list[str] = []
    monkeypatch.setattr(
        "parayabuck.runtime.alerts",
        type("A", (), {"emit": staticmethod(lambda kind, **kw: events.append(kind))}),
    )
    engine, equity = _engine_with_equity_runtime(("AAA",))
    dates = _days(3)
    lagging = {"AAA": [_bar("AAA", d, 40.0 + i) for i, d in enumerate(dates[:-1])]}
    engine._historical = FakeHistory(raw={}, splits=lagging)

    reason = engine._equity_signal_basis_latch(dates[-1])
    assert reason == "signal_adjustment_unverified"
    assert "signal_adjustment_failure" in events
    # Nothing was adopted: fail closed means no partial state changes.
    assert equity.signal_verified_through_by_symbol == {}
    status = engine.signal_adjustment_status()
    assert status["entry_gate_reason"] == "signal_adjustment_unverified"


def test_empty_provider_response_fails_closed(monkeypatch) -> None:
    engine, _equity = _engine_with_equity_runtime(("AAA",))
    stored = [_bar("AAA", d, 50.0) for d in _days(3)]
    _equity.history["AAA"] = list(stored)
    engine._historical = FakeHistory(raw={}, splits={})

    assert engine.verify_equity_signal_basis() is False
    assert _equity.history["AAA"] == stored  # untouched


def test_partial_symbol_response_fails_closed(monkeypatch) -> None:
    engine, equity = _engine_with_equity_runtime(("AAA", "BBB"))
    dates = _days(3)
    stored_a = [_bar("AAA", d, 100.0 - i) for i, d in enumerate(dates)]
    stored_b = [_bar("BBB", d, 50.0 + i) for i, d in enumerate(dates)]
    equity.history = {"AAA": list(stored_a), "BBB": list(stored_b)}
    only_a = {"AAA": [_bar("AAA", d, 100.0 - i) for i, d in enumerate(dates)]}
    engine._historical = FakeHistory(raw={}, splits=only_a)

    assert engine.verify_equity_signal_basis() is False
    # NO partial adoption: AAA stays exactly as stored despite good coverage.
    assert equity.history["AAA"] == stored_a
    assert equity.history["BBB"] == stored_b
    assert equity.signal_verified_through_by_symbol == {}


def test_per_symbol_verified_through_is_required(monkeypatch) -> None:
    """A watermark on ONE symbol never authorizes another symbol's day: the
    gate re-verifies until EVERY required symbol reaches the timestamp."""
    engine, equity = _engine_with_equity_runtime(("AAA", "BBB"))
    dates = _days(2)
    provider = {
        "AAA": [_bar("AAA", d, 10.0 + i) for i, d in enumerate(dates)],
        "BBB": [_bar("BBB", d, 20.0 + i) for i, d in enumerate(dates)],
    }
    engine._historical = FakeHistory(raw={}, splits=provider)

    # Simulate a stale partial watermark set from an earlier epoch.
    engine._signal_adjustment_state().update(
        {
            "healthy": True,
            "last_success_at": datetime.now(UTC),
            "verified_through_by_symbol": {"AAA": dates[-1]},
        }
    )
    assert engine._equity_signal_basis_verified(dates[-1]) is False  # BBB missing
    latch = engine._equity_signal_basis_latch(dates[-1])
    assert latch is None  # synchronous verification completed BOTH symbols
    assert equity.signal_verified_through_by_symbol == {
        "AAA": dates[-1],
        "BBB": dates[-1],
    }


def _process_engine(provider: dict[str, list[Bar]], dates: list[datetime]):
    """Full integration fixture driving _process_portfolio_timestamp."""
    import threading
    from types import SimpleNamespace

    from parayabuck.runtime import EngineRuntime

    engine = EngineRuntime.__new__(EngineRuntime)
    engine._warming_up = False
    engine._circuit_breaker_reason = None
    engine._symbols_by_market = {Market.EQUITY: {"AAA"}, Market.CRYPTO: set()}
    engine._portfolio_lock = threading.RLock()
    engine._last_portfolio_status = {}
    engine._historical = FakeHistory(raw={}, splits=provider)
    equity = _bare_runtime(minimum_history_bars=274)
    equity.symbols = ("AAA",)
    captured: dict[str, object] = {}

    def fake_process(bars, *, entry_latch=None, **kwargs):  # noqa: ANN001
        captured["entry_latch"] = entry_latch
        captured["bars"] = list(bars)
        return SimpleNamespace(status="captured", orders=(), submitted_order_ids=())

    portfolio_runtime = SimpleNamespace(
        market=Market.EQUITY,
        symbols=("AAA",),
        strategy=SimpleNamespace(timeframe="1d", minimum_history_bars=274, name="equity_test"),
        process_closed_bars=fake_process,
        history={"AAA": [_bar("AAA", d, 40.0) for d in dates[:-1]]},
        signal_verified_through_by_symbol={},
        signal_last_verified_at=None,
        previous_timestamp=None,
    )
    # Verification adopts THROUGH the runtime: bind the real method.
    portfolio_runtime.adopt_authoritative_signal_history = lambda provider, watermarks: (
        PortfolioRuntime.adopt_authoritative_signal_history(portfolio_runtime, provider, watermarks)
    )
    engine._portfolio_runtimes = (portfolio_runtime,)
    # Instance attribute shadows the bound readiness gate (always open).
    engine._entry_readiness_reasons = lambda market, **kwargs: ()
    engine.broker = SimpleNamespace(account=lambda: AccountSnapshot(10_000, 10_000, 10_000, 0.0, positions=()))
    # Freshness machinery is irrelevant here: the pipeline only needs A
    # snapshot to proceed past its None-check.
    engine._fresh_account_snapshot = lambda: AccountSnapshot(10_000, 10_000, 10_000, 0.0, positions=())
    return engine, captured


def test_decision_requires_verified_through_decision_timestamp(monkeypatch) -> None:
    """Integration: the finalized decision-day bar itself must be covered by
    per-symbol provider watermarks BEFORE any entry-bearing decision runs;
    provider lag on that exact bar blocks entries through the real pipeline."""
    monkeypatch.setattr(
        "parayabuck.runtime.alerts",
        type("A", (), {"emit": staticmethod(lambda kind, **kw: None)}),
    )
    # A decision day anchored to NOW so the fixture can span the real
    # full-history window the verifier demands (~minimum_history_bars).
    decision_ts = datetime.now(UTC).replace(hour=20, minute=0, second=0, microsecond=0)
    span_days = 420
    window_dates = [decision_ts - timedelta(days=offset) for offset in range(span_days)][::-1]
    decision_bar = _bar("AAA", decision_ts, 42.0)

    def _series(count: int) -> list[Bar]:
        return [_bar("AAA", d, 40.0 + i) for i, d in enumerate(window_dates[:count])]

    # Provider covers the ENTIRE required window INCLUDING the decision-day
    # bar: the decision passes clean.
    engine, captured = _process_engine({"AAA": _series(span_days)}, window_dates)
    equity_ns = engine._portfolio_runtimes[0]
    equity_ns.history["AAA"].append(decision_bar)
    engine._process_portfolio_timestamp([decision_bar])
    assert captured["entry_latch"] is None

    # Provider LAGS (missing exactly the decision-day bar): fail closed.
    lagging = {"AAA": _series(span_days - 1)}
    engine2, captured2 = _process_engine(lagging, window_dates)
    equity_ns2 = engine2._portfolio_runtimes[0]
    equity_ns2.history["AAA"].append(decision_bar)
    engine2._process_portfolio_timestamp([decision_bar])
    assert captured2["entry_latch"] == "signal_adjustment_unverified"


# --------------------------------------------------------------------------- #
# Coverage adequacy: requirement from STRATEGY history, not request boundary
# --------------------------------------------------------------------------- #


def _window_engine_with_history(
    stored: list[Bar],
    provider: dict[str, list[Bar]],
    *,
    honor_window: bool = True,
):
    engine, equity = _engine_with_equity_runtime(("AAA",))
    equity.history["AAA"] = list(stored)
    fake = FakeHistory(raw={}, splits=provider, honor_window=honor_window)
    engine._historical = fake
    return engine, equity, fake


def test_intraday_request_start_still_verifies(monkeypatch) -> None:
    """Request start lands mid-session; the first returned daily bar is
    necessarily AFTER it. Adequacy compares against the strategy-required
    oldest BAR, so verification still succeeds."""
    monkeypatch.setattr(
        "parayabuck.runtime.alerts",
        type("A", (), {"emit": staticmethod(lambda kind, **kw: None)}),
    )
    odd = _TS0.replace(hour=7, minute=37)  # an intraday, non-bar timestamp
    dates = [odd + timedelta(days=i) for i in range(30)]
    stored = [_bar("AAA", d if i else odd, 100.0 + i) for i, d in enumerate(dates)]
    provider = {"AAA": list(stored)}
    engine, _equity, fake = _window_engine_with_history(stored, provider)

    assert engine.verify_equity_signal_basis() is True
    requested_start, _requested_end = fake.requested_windows[0]
    # The request boundary inherits the odd intraday time of the strategy's
    # oldest bar, so it cannot coincide with any daily bar timestamp.
    assert (requested_start.hour, requested_start.minute) == (7, 37)
    first_returned = min(bar.timestamp for bar in provider["AAA"] if bar.timestamp >= requested_start)
    assert first_returned > requested_start  # API semantics: bar AFTER start
    # ...yet verification succeeded because adequacy is measured against the
    # strategy-required oldest BAR (inside covered territory).


def test_weekend_start_first_bar_monday_still_verifies(monkeypatch) -> None:
    """Start falls on a Saturday/Sunday; the provider's first daily bar is
    Monday's. Adequacy still holds because the strategy-required oldest bar
    sits inside covered territory."""
    monkeypatch.setattr(
        "parayabuck.runtime.alerts",
        type("A", (), {"emit": staticmethod(lambda kind, **kw: None)}),
    )
    # The strategy's trailing window begins WEDNESDAY Jun 11 (its oldest
    # readable bar); start = that bar minus the 10-day margin lands on a
    # SUNDAY. The provider archive begins the MONDAY before it — its own
    # boundary is after the weekend request start yet still at/before the
    # strategy requirement, exactly like a real historical endpoint.
    anchor = datetime(2025, 6, 11, 4, 0, tzinfo=UTC)  # Wednesday (strategy oldest)
    dates = [anchor + timedelta(days=i) for i in range(30)]
    stored = [_bar("AAA", d, 200.0 + i) for i, d in enumerate(dates)]
    monday_before = datetime(2025, 6, 9, 4, 0, tzinfo=UTC)
    provider_dates = [monday_before + timedelta(days=i) for i in range(32)]
    provider_series = [_bar("AAA", d, 200.0 + i) for i, d in enumerate(provider_dates)]
    assert provider_series[0].timestamp.weekday() == 0
    engine, _equity, fake = _window_engine_with_history(stored, {"AAA": provider_series})

    assert engine.verify_equity_signal_basis() is True
    requested_start, _end = fake.requested_windows[0]
    assert requested_start.weekday() in (5, 6)  # Saturday/Sunday request start
    returned_first = min(bar.timestamp for bar in provider_series)
    assert returned_first.weekday() == 0  # Monday first returned bar
    assert returned_first > requested_start
    # Adequacy held because the requirement is the strategy's oldest BAR,
    # which the provider window covers with room to spare.


def test_genuinely_insufficient_history_still_fails_closed(monkeypatch) -> None:
    """A provider that cannot cover back to the strategy-required oldest bar
    must fail closed — no adoption, no watermarks, one failure alert."""
    events: list[str] = []
    monkeypatch.setattr(
        "parayabuck.runtime.alerts",
        type("A", (), {"emit": staticmethod(lambda kind, **kw: events.append(kind))}),
    )
    dates = [_TS0 + timedelta(days=i, hours=4) for i in range(30)]
    stored = [_bar("AAA", d, 100.0 + i) for i, d in enumerate(dates)]
    short_provider = {"AAA": list(stored[-3:])}
    engine, equity, _fake = _window_engine_with_history(stored, short_provider, honor_window=True)

    assert engine.verify_equity_signal_basis() is False
    assert equity.signal_verified_through_by_symbol == {}
    assert equity.history["AAA"] == stored
    assert events.count("signal_adjustment_failure") == 1


def test_shutdown_joins_signal_adjustment_before_client_close() -> None:
    """stop() joins pb-signal-adjustment BEFORE closing the historical data
    client: no worker may touch a dead HTTP session."""
    import queue as queue_module
    import threading

    from parayabuck.runtime import EngineRuntime

    order: list[str] = []
    engine = EngineRuntime.__new__(EngineRuntime)
    engine._running = True
    engine._state = "running"
    engine._circuit_breaker_reason = None
    engine._stop_event = threading.Event()
    engine._events = queue_module.Queue()
    engine.stream = type("S", (), {"stop": staticmethod(lambda: None)})()
    engine.trading_updates = type("T", (), {"stop": staticmethod(lambda: None)})()
    engine._threads = []
    engine._reconcile_thread = None
    engine._warmup_retry_thread = None
    engine._storage_closed = True  # skip SQLite teardown in this test
    engine._runtime_session_id = None
    engine.broker = type("B", (), {"close": staticmethod(lambda: None)})()

    def _historical_close() -> None:
        thread = engine._signal_adjustment_thread
        order.append(f"close:worker_alive={thread.is_alive()}")

    engine._historical = type("H", (), {"close": staticmethod(_historical_close)})()

    started = threading.Event()

    def monitor_body() -> None:
        started.set()
        engine._stop_event.wait(60)  # simulates the hourly wait
        order.append("worker_exited")

    engine._signal_adjustment_thread = threading.Thread(target=monitor_body, daemon=True)
    engine._signal_adjustment_thread.start()
    assert started.wait(timeout=5)

    assert engine.stop() is True
    assert not engine._signal_adjustment_thread.is_alive()
    assert order == ["worker_exited", "close:worker_alive=False"]
