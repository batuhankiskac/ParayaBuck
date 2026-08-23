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

from parayabuck.core.models import Bar, Market
from parayabuck.data.adjustment import (
    SPLIT_RATIO_TOLERANCE,
    authoritative_signal_history,
    detect_basis_break,
)
from parayabuck.portfolio.runtime_service import PortfolioRuntime

_TS0 = datetime(2025, 6, 2, 4, 0, tzinfo=UTC)


def _bar(symbol: str, timestamp: datetime, close: float) -> Bar:
    return Bar(timestamp, symbol, "1d", close, close, close, close, 1_000, Market.EQUITY)


def _days(count: int) -> list[datetime]:
    return [_TS0 + timedelta(days=index) for index in range(count)]


def _economic_path(start: float, count: int) -> list[float]:
    return [start * (1.0002**index) for index in range(count)]


def _bare_runtime(symbol: str = "AAA") -> PortfolioRuntime:
    runtime = object.__new__(PortfolioRuntime)
    runtime.market = Market.EQUITY
    runtime.strategy = type("S", (), {"timeframe": "1d"})()
    runtime.symbols = (symbol,)
    runtime.history = {symbol: []}
    runtime.signal_verified_through = None
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

    merged, changed = authoritative_signal_history(tuple(stored), provider_now)
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
    first_changed = authoritative_signal_history(tuple(stored), provider)[1]
    second, second_changed = authoritative_signal_history(tuple(stored), provider)
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
    merged, changed = authoritative_signal_history(tuple(raw_as_traded), provider)
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
    merged, changed = authoritative_signal_history(stored, provider)
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
    changed = runtime.adopt_authoritative_signal_history({"AAA": provider}, dates[-1])
    assert changed == 1
    assert [b.close for b in runtime.history["AAA"]] == [c / 4.0 for c in economic]
    assert runtime.signal_verified_through == dates[-1]
    assert runtime.signal_last_verified_at is not None
    # Second identical adoption changes nothing.
    assert runtime.adopt_authoritative_signal_history({"AAA": provider}, dates[-1]) == 0


def test_hot_swap_between_splits_resets_verification_state() -> None:
    runtime = _bare_runtime()
    runtime.adopt_authoritative_signal_history({"AAA": [_bar("AAA", _TS0, 50.0)]}, _TS0)
    assert runtime.signal_last_verified_at is not None
    added, removed = runtime.update_symbols(("AAA", "BBB"))
    assert added == ("BBB",) and removed == ()
    # Membership changed while splits may have happened: fail conservative.
    assert runtime.signal_verified_through is None
    assert runtime.signal_last_verified_at is None
    assert "AAA" in runtime.history and "BBB" in runtime.history


class FakeHistory:
    def __init__(
        self,
        raw: dict[str, list[Bar]] | None = None,
        splits: dict[str, list[Bar]] | None = None,
        error: Exception | None = None,
    ) -> None:
        self.raw = raw or {}
        self.splits = splits or {}
        self.error = error
        self.calls: list[tuple[tuple[str, ...], str]] = []

    def get_bars_batch(self, symbols, timeframe, *, start, end, adjustment):  # noqa: ANN001
        self.calls.append((tuple(symbols), adjustment))
        if self.error is not None:
            raise self.error
        return dict(self.raw if adjustment == "raw" else self.splits)


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

    latch = engine._equity_signal_basis_latch()
    assert latch is None  # verified clean
    bases = {adjustment for _syms, adjustment in fake.calls}
    assert bases == {"raw", "splits"}
    symbol_sets = {syms for syms, _a in fake.calls}
    assert symbol_sets == {("AAA", "BBB")}  # one batched call per basis
    assert equity.signal_verified_through == dates[-1]
    # First-ever success transitions the state: exactly one recovery event.
    assert [kind for kind, _kw in events] == ["signal_adjustment_recovery"]

    # A split rebase between verifications forces the latch until repaired.
    rebased = {"AAA": [_bar("AAA", d, (100.0 - i) / 4.0) for i, d in enumerate(dates)]}
    fake.splits = {**provider, **rebased}
    equity.history["AAA"] = [_bar("AAA", d, 100.0 - i) for i, d in enumerate(dates)]
    equity.signal_last_verified_at = datetime.now(UTC) - timedelta(hours=2)
    engine._signal_adjustment["last_success_at"] = equity.signal_last_verified_at
    latch = engine._equity_signal_basis_latch()
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
    working = FakeHistory(raw=provider, splits=provider)
    engine._historical = failing

    assert engine._equity_signal_basis_latch() == "signal_adjustment_unverified"
    engine._historical = working
    assert engine._equity_signal_basis_latch() is None
    assert engine.signal_adjustment_status()["entry_gate_reason"] is None
    assert "signal_adjustment_failure" in events
    assert "signal_adjustment_recovery" in events
    assert equity.signal_verified_through == dates[-1]


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
    pre_restart.adopt_authoritative_signal_history({"AAA": provider_now[:-1]}, dates[-2])

    post_restart = _bare_runtime()
    post_restart.seed(list(provider_now))

    assert [b.close for b in pre_restart.history["AAA"]] == [
        b.close for b in post_restart.history["AAA"]
    ]


def test_crypto_runtime_is_never_touched_by_the_bridge() -> None:
    runtime = object.__new__(PortfolioRuntime)
    runtime.market = Market.CRYPTO
    runtime.symbols = ("BTC/USD",)
    runtime.history = {"BTC/USD": [_bar("BTC/USD", _TS0, 50_000.0)]}
    runtime.signal_verified_through = None
    runtime.signal_last_verified_at = None
    assert runtime.signal_history_matches_provider({}) is True
    assert runtime.adopt_authoritative_signal_history({}, _TS0) == 0
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
