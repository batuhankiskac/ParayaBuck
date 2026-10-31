"""Signal cross-check guard (IEX stream bar vs SIP daily bar), offline.

Synthetic fixtures only: pinned clocks, fake provider fetches, tmp_path DBs.
Rules: (a) agree -> trade as usual; (b) trend disagreement -> hold the
current position, flag, alert, watchdog reason; (c) no second source within
the retry window -> decide on the primary with an alert; (d) close
divergence above the threshold -> alert even when the trend agrees.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest

from parayabuck.config import Config, IntentWatchdogConfig, SignalCrossCheckConfig
from parayabuck.core.models import AccountSnapshot, Bar, Market, PositionSnapshot
from parayabuck.execution.portfolio import AssetTradingRules, PortfolioOrderRequest
from parayabuck.monitoring.intent_watchdog import IntentWatchdogSettings, LatestDecision, evaluate_equity
from parayabuck.portfolio import runtime_service
from parayabuck.portfolio.equity_leveraged_trend import EquityLeveragedTrendV1
from parayabuck.portfolio.runtime_service import PortfolioRuntime, PortfolioStateRepository
from parayabuck.portfolio.signal_cross_check import (
    STATUS_AGREE,
    STATUS_SECONDARY_UNAVAILABLE,
    STATUS_TREND_MISMATCH,
    SignalCrossCheckCoordinator,
    compare_sources,
    cross_check_symbols,
)
from parayabuck.runtime import EngineRuntime

_SESSION = date(2026, 9, 29)
_PROVIDER_LABEL = datetime(2026, 9, 29, 4, 0, tzinfo=UTC)  # 00:00 ET
_STREAM_LABEL = datetime(2026, 9, 29, 13, 30, tzinfo=UTC)  # 09:30 ET
_CLOSE = datetime(2026, 9, 29, 20, 0, tzinfo=UTC)  # 16:00 ET
_AFTER_CLOSE = datetime(2026, 9, 29, 20, 30, tzinfo=UTC)


def _bar(symbol: str, timestamp: datetime, close: float) -> Bar:
    return Bar(timestamp, symbol, "1d", close, close, close, close, 1_000_000, Market.EQUITY)


def _strategy() -> EquityLeveragedTrendV1:
    return EquityLeveragedTrendV1(
        {"signal_symbol": "QQQ", "hold_symbol": "QLD", "sma_bars": 5, "vol_target": 0.35, "vol_window": 3}
    )


def _history(sessions: int = 30) -> dict[str, list[Bar]]:
    days: list[datetime] = []
    day = _PROVIDER_LABEL - timedelta(days=1)
    while len(days) < sessions:
        if day.weekday() < 5:
            days.append(day)
        day -= timedelta(days=1)
    days.reverse()
    # QQQ rises 1/session (SMA5 of the last closes ~ 427); QLD alternates so
    # its realized volatility is well defined.
    return {
        "QQQ": [_bar("QQQ", d, 400.0 + i) for i, d in enumerate(days)],
        "QLD": [_bar("QLD", d, 100.0 + (1.0 if i % 2 else -1.0)) for i, d in enumerate(days)],
        "SPY": [_bar("SPY", d, 500.0 + i) for i, d in enumerate(days)],
    }


def _stream(qqq: float, qld: float = 100.5) -> list[Bar]:
    return [_bar("QQQ", _STREAM_LABEL, qqq), _bar("QLD", _STREAM_LABEL, qld), _bar("SPY", _STREAM_LABEL, 530.0)]


def _sip(qqq: float, qld: float = 100.5) -> dict[str, Bar]:
    return {"QQQ": _bar("QQQ", _PROVIDER_LABEL, qqq), "QLD": _bar("QLD", _PROVIDER_LABEL, qld)}


def _primary(bars: list[Bar]) -> dict[str, Bar]:
    return {bar.symbol: bar for bar in bars}


# --------------------------------------------------------------------------- #
# Pure comparison
# --------------------------------------------------------------------------- #


def test_cross_check_symbols_are_the_signal_and_hold_inputs() -> None:
    assert cross_check_symbols(_strategy()) == ("QQQ", "QLD")
    assert cross_check_symbols(SimpleNamespace()) == ()  # type: ignore[arg-type]


def test_a_sources_agree_on_trend_and_weight() -> None:
    outcome = compare_sources(
        _strategy(), _history(), _primary(_stream(440.0)), _sip(440.2), _STREAM_LABEL, close_divergence_bps=50
    )
    assert outcome.status == STATUS_AGREE
    assert not outcome.hold and not outcome.close_divergence
    assert outcome.primary is not None and outcome.secondary is not None
    assert outcome.primary.trend_state == outcome.secondary.trend_state == "uptrend"
    assert outcome.primary.weight == pytest.approx(outcome.secondary.weight)
    assert outcome.close_gap_bps["QQQ"] == pytest.approx((440.0 / 440.2 - 1) * 1e4, abs=1e-3)


def test_b_trend_disagreement_is_a_hold() -> None:
    outcome = compare_sources(
        _strategy(), _history(), _primary(_stream(440.0)), _sip(300.0), _STREAM_LABEL, close_divergence_bps=50
    )
    assert outcome.status == STATUS_TREND_MISMATCH
    assert outcome.hold
    assert (outcome.primary.trend_state, outcome.secondary.trend_state) == ("uptrend", "downtrend")  # type: ignore[union-attr]
    metadata = outcome.to_metadata()
    assert metadata["hold"] is True and metadata["secondary"]["trend_state"] == "downtrend"


def test_d_close_divergence_is_flagged_even_when_the_trend_agrees() -> None:
    # QQQ 440 vs 441 (-22.7 bps) is fine; QLD 100.5 vs 99.5 (+100 bps) diverges.
    outcome = compare_sources(
        _strategy(),
        _history(),
        _primary(_stream(440.0, 100.5)),
        _sip(441.0, 99.5),
        _STREAM_LABEL,
        close_divergence_bps=50,
    )
    assert outcome.status == STATUS_AGREE and not outcome.hold
    assert outcome.close_divergence_symbols == ("QLD",)


def test_same_session_provider_tail_is_replaced_not_duplicated() -> None:
    history = _history()
    history["QQQ"].append(_bar("QQQ", _PROVIDER_LABEL, 250.0))  # forming SIP tail adopted earlier
    outcome = compare_sources(
        _strategy(), history, _primary(_stream(440.0)), _sip(440.0), _STREAM_LABEL, close_divergence_bps=50
    )
    assert outcome.status == STATUS_AGREE
    assert outcome.primary.signal_close == outcome.secondary.signal_close == 440.0  # type: ignore[union-attr]


# --------------------------------------------------------------------------- #
# Coordinator timing: embargo, bounded retry, (c) unavailable
# --------------------------------------------------------------------------- #


def _coordinator(**overrides: Any) -> SignalCrossCheckCoordinator:
    settings: dict[str, Any] = {
        "sip_embargo": timedelta(minutes=15),
        "retry_window": timedelta(minutes=10),
        "retry_interval": timedelta(seconds=60),
        "close_divergence_bps": 50.0,
        "session_close": lambda _session: _CLOSE,
    }
    settings.update(overrides)
    return SignalCrossCheckCoordinator(**settings)


def test_first_attempt_waits_for_the_sip_embargo() -> None:
    calls: list[tuple[list[str], datetime, datetime]] = []

    def fetch(symbols: list[str], start: datetime, end: datetime) -> dict[str, Bar]:
        calls.append((symbols, start, end))
        return _sip(440.0)

    coordinator = _coordinator()
    at_close = _CLOSE + timedelta(seconds=2)
    step = coordinator.step(_strategy(), _history(), _stream(440.0), fetch, at_close)
    assert step.action == "defer" and step.retry_at == _CLOSE + timedelta(minutes=15)
    assert calls == []
    step = coordinator.step(_strategy(), _history(), _stream(440.0), fetch, _CLOSE + timedelta(minutes=15))
    assert step.action == "proceed" and step.outcome is not None and step.outcome.status == STATUS_AGREE
    # The request never reaches past the session close (Basic-plan SIP embargo).
    assert calls == [(["QQQ", "QLD"], datetime(2026, 9, 29, 4, 0, tzinfo=UTC), _CLOSE)]


def test_c_unavailable_second_source_proceeds_on_the_primary_after_the_window() -> None:
    def fetch(symbols: list[str], start: datetime, end: datetime) -> dict[str, Bar]:
        raise ConnectionError("provider down")

    coordinator = _coordinator()
    now = _CLOSE + timedelta(minutes=15)
    step = coordinator.step(_strategy(), _history(), _stream(440.0), fetch, now)
    assert step.action == "defer" and step.retry_at == now + timedelta(seconds=60)
    assert coordinator.pending_status()["2026-09-29"]["last_error"] == "ConnectionError: provider down"
    steps = 1
    while step.action == "defer":
        assert step.retry_at is not None
        now = step.retry_at
        step = coordinator.step(_strategy(), _history(), _stream(440.0), fetch, now)
        steps += 1
    assert now == _CLOSE + timedelta(minutes=25)  # bounded: embargo + window
    assert step.outcome is not None and step.outcome.status == STATUS_SECONDARY_UNAVAILABLE
    assert not step.outcome.hold
    assert step.outcome.attempts == steps == 11
    assert coordinator.pending_status() == {}


def test_missing_sip_bar_is_retried_then_compared() -> None:
    responses = [{}, _sip(300.0)]

    def fetch(symbols: list[str], start: datetime, end: datetime) -> dict[str, Bar]:
        return responses.pop(0)

    coordinator = _coordinator(sip_embargo=timedelta(0))
    first = coordinator.step(_strategy(), _history(), _stream(440.0), fetch, _AFTER_CLOSE)
    assert first.action == "defer"
    second = coordinator.step(_strategy(), _history(), _stream(440.0), fetch, first.retry_at)  # type: ignore[arg-type]
    assert second.outcome is not None and second.outcome.status == STATUS_TREND_MISMATCH
    assert second.outcome.attempts == 2


def test_disabled_or_sourceless_checks_are_a_no_op() -> None:
    assert _coordinator(enabled=False).step(_strategy(), _history(), _stream(440.0), None, _AFTER_CLOSE).outcome is None
    step = _coordinator().step(_strategy(), _history(), _stream(440.0), None, _AFTER_CLOSE)
    assert step.action == "proceed" and step.outcome is None


def test_config_defaults_and_validation() -> None:
    config = SignalCrossCheckConfig()
    assert (config.enabled, config.sip_embargo_minutes, config.retry_window_minutes) == (True, 15.0, 10.0)
    assert (config.retry_interval_seconds, config.close_divergence_bps) == (60.0, 50.0)
    with pytest.raises(ValueError):
        SignalCrossCheckConfig(retry_interval_seconds=0)
    with pytest.raises(ValueError, match="equity_decision_grace_minutes"):
        Config(
            signal_cross_check=SignalCrossCheckConfig(sip_embargo_minutes=30, retry_window_minutes=15),
            intent_watchdog=IntentWatchdogConfig(equity_decision_grace_minutes=45),
        )


# --------------------------------------------------------------------------- #
# Decision path: (a) unchanged, (b) hold with no orders and a flagged decision
# --------------------------------------------------------------------------- #


class _Broker:
    def __init__(self, positions: tuple[PositionSnapshot, ...] = ()) -> None:
        self.positions = positions
        self.submitted: list[PortfolioOrderRequest] = []

    def account(self) -> AccountSnapshot:
        return AccountSnapshot(100_000, 100_000, 100_000, 0.0, self.positions)

    def asset_trading_rules(self, symbol: str, market: Market) -> AssetTradingRules:
        return AssetTradingRules(0.01, 0.01, 1.0)

    def all_open_order_symbols(self) -> set[str]:
        return set()

    def working_buy_orders(self) -> tuple[Any, ...]:
        return ()

    def submit_portfolio_order(self, order: PortfolioOrderRequest) -> str:
        self.submitted.append(order)
        return "broker-" + order.client_order_id


class _Universe:
    def contains(self, symbol: str, market: Market, timestamp: datetime) -> bool:
        return True


def _runtime(tmp_path, broker: _Broker) -> PortfolioRuntime:
    runtime = PortfolioRuntime(
        _strategy(),
        ("QQQ", "QLD", "SPY"),
        broker,
        PortfolioStateRepository(tmp_path / "runtime.sqlite3"),
        mode="paper",
        maximum_allocation=0.5,
        account_id="acct",
        universe=_Universe(),
        universe_version="test-universe-v1",
    )
    runtime.history = _history()
    return runtime


def _check(qqq_sip: float) -> Any:
    return compare_sources(
        _strategy(), _history(), _primary(_stream(440.0)), _sip(qqq_sip), _STREAM_LABEL, close_divergence_bps=50
    )


def test_a_agreement_trades_exactly_like_no_cross_check(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(runtime_service, "_utcnow", lambda: _AFTER_CLOSE)
    baseline_broker, checked_broker = _Broker(), _Broker()
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    baseline = _runtime(tmp_path / "a", baseline_broker).process_closed_bars(_stream(440.0))
    checked = _runtime(tmp_path / "b", checked_broker).process_closed_bars(
        _stream(440.0), signal_cross_check=_check(440.0)
    )
    assert baseline.status == checked.status == "submitted"
    assert [(o.symbol, o.side, o.quantity) for o in baseline_broker.submitted] == [
        (o.symbol, o.side, o.quantity) for o in checked_broker.submitted
    ]
    assert checked_broker.submitted and checked.target is not None
    assert checked.target.metadata["signal_cross_check"]["status"] == STATUS_AGREE
    assert "signal_source_hold" not in checked.target.metadata


def test_b_mismatch_blocks_an_entry_and_records_the_flag(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(runtime_service, "_utcnow", lambda: _AFTER_CLOSE)
    broker = _Broker()
    runtime = _runtime(tmp_path, broker)
    result = runtime.process_closed_bars(_stream(440.0), signal_cross_check=_check(300.0))
    assert result.status == "signal_source_hold"
    assert broker.submitted == [] and result.orders == ()
    assert result.target is not None and result.target.weights == ()
    assert result.target.metadata["signal_source_hold"] is True
    assert result.target.metadata["trend_state"] == "uptrend"  # primary decision kept for the record
    assert result.target.metadata["paper_observability"]["blocked_or_skipped_reason"] == "signal_source_mismatch"
    # The decision is persisted: no "decision missing" alarm, and the flag is readable.
    repository = runtime.repository
    assert repository.has_decision("acct", runtime.configuration_digest, _STREAM_LABEL, Market.EQUITY)
    check = repository.latest_decision_cross_check("acct", Market.EQUITY)
    assert check is not None and check["hold"] is True and check["status"] == STATUS_TREND_MISMATCH


def test_b_mismatch_keeps_an_existing_position_without_orders(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(runtime_service, "_utcnow", lambda: _AFTER_CLOSE)
    held = PositionSnapshot("QLD", 400.0, 100.0, 100.5, Market.EQUITY, managed_qty=400.0, ownership="portfolio")
    broker = _Broker((held,))
    runtime = _runtime(tmp_path, broker)
    # Primary (IEX) says downtrend -> would sell; SIP says uptrend -> hold.
    history = _history()
    down_check = compare_sources(
        _strategy(), history, _primary(_stream(300.0)), _sip(440.0), _STREAM_LABEL, close_divergence_bps=50
    )
    assert down_check.hold
    result = runtime.process_closed_bars(_stream(300.0), signal_cross_check=down_check)
    assert result.status == "signal_source_hold"
    assert broker.submitted == []
    assert result.target is not None
    # The target mirrors the held position (capped by the test's 10% per-asset
    # risk limit); even that cap produces no trim order during a hold.
    assert [w.symbol for w in result.target.weights] == ["QLD"]
    assert 0 < result.target.weights[0].weight <= 400 * 100.5 / 1e5
    assert result.orders == ()


# --------------------------------------------------------------------------- #
# Intent watchdog: the hold is reported as the true reason
# --------------------------------------------------------------------------- #


def test_watchdog_reports_the_hold_instead_of_under_target() -> None:
    settings = IntentWatchdogSettings()
    now = datetime(2026, 9, 30, 14, 5, tzinfo=UTC)  # after next open + grace
    flat = AccountSnapshot(100_000, 100_000, 100_000, 0.0)
    metadata = _check(300.0).to_metadata()
    # A normal decision targeting QLD but not filled: under target.
    normal = LatestDecision(_STREAM_LABEL, {"QLD": 0.5})
    assert [a.code for a in evaluate_equity(now, normal, flat, running_since=None, settings=settings)] == [
        "equity_position_under_target"
    ]
    # The hold decision (target = held = flat): the true reason, no false alarm.
    hold = LatestDecision(_STREAM_LABEL, {}, metadata)
    alarms = evaluate_equity(now, hold, flat, running_since=None, settings=settings)
    assert [a.code for a in alarms] == ["equity_signal_source_hold"]
    assert "uptrend" in alarms[0].message and "downtrend" in alarms[0].message
    # A defensive case: even a stale positive weight on a hold never alarms under-target.
    odd = LatestDecision(_STREAM_LABEL, {"QLD": 0.5}, metadata)
    assert [a.code for a in evaluate_equity(now, odd, flat, running_since=None, settings=settings)] == [
        "equity_signal_source_hold"
    ]
    # An agreeing cross-check changes nothing.
    agree = LatestDecision(_STREAM_LABEL, {"QLD": 0.5}, _check(440.0).to_metadata())
    assert [a.code for a in evaluate_equity(now, agree, flat, running_since=None, settings=settings)] == [
        "equity_position_under_target"
    ]


def test_runtime_watchdog_pass_reads_the_hold_flag(monkeypatch) -> None:
    events: list[tuple[str, dict[str, object]]] = []
    monkeypatch.setattr(
        "parayabuck.runtime.alerts", SimpleNamespace(emit=lambda kind, **fields: events.append((kind, fields)))
    )
    import threading

    from parayabuck.portfolio.models import RebalancePolicy

    metadata = _check(300.0).to_metadata()
    repo = SimpleNamespace(
        latest_decision_target=lambda account_id, market: (_STREAM_LABEL, {}),
        latest_decision_cross_check=lambda account_id, market: metadata,
    )
    engine = EngineRuntime.__new__(EngineRuntime)
    engine.app_config = SimpleNamespace(intent_watchdog=IntentWatchdogConfig())
    engine._startup_committed = True
    engine._intent_watchdog_running_since = datetime(2026, 9, 20, tzinfo=UTC)
    engine._account_snapshot_lock = threading.Lock()
    engine._last_account_snapshot = AccountSnapshot(100_000, 100_000, 100_000, 0.0)
    engine._last_account_snapshot_at = datetime(2026, 9, 29, 20, 59, tzinfo=UTC)
    engine._portfolio_runtimes = (
        SimpleNamespace(
            market=Market.EQUITY,
            account_id="acct",
            repository=repo,
            policy=RebalancePolicy(Market.EQUITY, "hours:24"),
            strategy=SimpleNamespace(timeframe="1d"),
        ),
    )
    engine._state = "running"
    engine._intent_watchdog_pass(datetime(2026, 9, 29, 21, 0, tzinfo=UTC))
    assert engine.intent_watchdog_status()["degraded_reasons"] == ["equity_signal_source_hold"]
    assert [(kind, fields["category"]) for kind, fields in events] == [("intent_alarm", "equity_signal_source_hold")]


# --------------------------------------------------------------------------- #
# Runtime wiring: deferral, alerts, never raising
# --------------------------------------------------------------------------- #


class _Historical:
    def __init__(self, response: dict[str, Bar] | Exception) -> None:
        self.response = response
        self.calls: list[dict[str, Any]] = []

    def get_session_daily_bars(self, symbols, start, end, *, feed="sip", adjustment="splits"):  # type: ignore[no-untyped-def]
        self.calls.append({"symbols": symbols, "feed": feed, "adjustment": adjustment})
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def _engine(historical: Any, events: list[tuple[str, dict[str, object]]], monkeypatch) -> EngineRuntime:
    monkeypatch.setattr(
        "parayabuck.runtime.alerts", SimpleNamespace(emit=lambda kind, **fields: events.append((kind, fields)))
    )
    engine = EngineRuntime.__new__(EngineRuntime)
    engine.app_config = SimpleNamespace(signal_cross_check=SignalCrossCheckConfig(sip_embargo_minutes=0))
    engine._historical = historical
    return engine


def _equity_runtime() -> Any:
    return SimpleNamespace(market=Market.EQUITY, strategy=_strategy(), history=_history())


def test_runtime_step_alerts_a_mismatch_with_the_explicit_sip_feed(monkeypatch) -> None:
    events: list[tuple[str, dict[str, object]]] = []
    historical = _Historical(_sip(300.0))
    engine = _engine(historical, events, monkeypatch)
    outcome = engine._signal_cross_check_step(_equity_runtime(), _stream(440.0))
    assert outcome is not None and outcome is not False and outcome.hold  # type: ignore[union-attr]
    assert historical.calls == [{"symbols": ["QQQ", "QLD"], "feed": "sip", "adjustment": "splits"}]
    assert [(kind, fields["category"]) for kind, fields in events] == [("signal_source_mismatch", "trend_mismatch")]
    assert engine.signal_cross_check_status()["last"]["status"] == STATUS_TREND_MISMATCH


def test_runtime_step_alerts_close_divergence_and_stays_quiet_on_agreement(monkeypatch) -> None:
    events: list[tuple[str, dict[str, object]]] = []
    engine = _engine(_Historical(_sip(441.0, 99.5)), events, monkeypatch)
    outcome = engine._signal_cross_check_step(_equity_runtime(), _stream(440.0, 100.5))
    assert outcome is not None and not outcome.hold  # type: ignore[union-attr]
    assert [(kind, fields["category"]) for kind, fields in events] == [("signal_source_mismatch", "close_divergence")]
    quiet: list[tuple[str, dict[str, object]]] = []
    engine = _engine(_Historical(_sip(440.0)), quiet, monkeypatch)
    assert engine._signal_cross_check_step(_equity_runtime(), _stream(440.0)).status == STATUS_AGREE  # type: ignore[union-attr]
    assert quiet == []


class _Clock:
    """Pins ``parayabuck.runtime.datetime.now`` (same pattern as test_runtime_decision_retry)."""

    def __init__(self, now: datetime) -> None:
        self.now = now

    def freeze(self, moment: datetime) -> None:
        self.now = moment

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        clock = self

        class _Frozen(datetime):
            @classmethod
            def now(cls, tz: Any = None) -> datetime:
                return clock.now.astimezone(tz) if tz is not None else clock.now

        monkeypatch.setattr("parayabuck.runtime.datetime", _Frozen)


def test_runtime_defers_the_batch_while_the_sip_bar_is_missing_then_reruns_it(monkeypatch) -> None:
    events: list[tuple[str, dict[str, object]]] = []
    engine = _engine(_Historical(ConnectionError("down")), events, monkeypatch)
    bars = _stream(440.0)
    # Freeze the clock at the session close (+2 s) so the coordinator's retry
    # horizon sits on the fixed bar date, not on the day the suite runs.
    _Clock(_CLOSE + timedelta(seconds=2)).install(monkeypatch)
    assert engine._signal_cross_check_step(_equity_runtime(), bars) is None
    key = (Market.EQUITY, _STREAM_LABEL)
    deferred_bars, retry_at = engine._deferred_portfolio_batches[key]
    assert deferred_bars == bars and retry_at is not None
    # The cross-check's retry horizon sits inside the decision window: the
    # due-runner must re-run it, never expire it as a stale parked batch.
    engine._decision_retry_settings = lambda: (timedelta(seconds=60), timedelta(days=30))  # type: ignore[method-assign]
    rerun: list[list[Bar]] = []

    def _handled(batch: list[Bar], **_kwargs: object) -> bool:
        # True = the batch was handled, so the runner pops the deferred key.
        rerun.append(batch)
        return True

    monkeypatch.setattr(engine, "_process_portfolio_timestamp", _handled)
    engine._run_due_deferred_portfolio_batches(now=retry_at - timedelta(seconds=1))
    assert rerun == []
    engine._run_due_deferred_portfolio_batches(now=retry_at)
    assert rerun == [bars] and engine._deferred_portfolio_batches == {}
    assert events == []  # no alert until the window is exhausted


def test_c_runtime_proceeds_on_the_primary_with_a_warning_when_the_window_is_exhausted(monkeypatch) -> None:
    events: list[tuple[str, dict[str, object]]] = []
    engine = _engine(_Historical(ConnectionError("down")), events, monkeypatch)
    engine.app_config = SimpleNamespace(
        signal_cross_check=SignalCrossCheckConfig(sip_embargo_minutes=0, retry_window_minutes=0)
    )
    outcome = engine._signal_cross_check_step(_equity_runtime(), _stream(440.0))
    assert outcome is not None and outcome is not False
    assert outcome.status == STATUS_SECONDARY_UNAVAILABLE and not outcome.hold  # type: ignore[union-attr]
    assert [(kind, fields["category"]) for kind, fields in events] == [
        ("signal_source_mismatch", "secondary_unavailable")
    ]
    assert "ConnectionError: down" in str(events[0][1]["reason"])


def test_runtime_without_a_second_source_client_is_a_no_op(monkeypatch) -> None:
    events: list[tuple[str, dict[str, object]]] = []
    engine = _engine(None, events, monkeypatch)
    assert engine._signal_cross_check_step(_equity_runtime(), _stream(440.0)) is False
    assert events == []


def test_alert_kind_is_known_and_a_warning() -> None:
    from parayabuck.monitoring.events import ALERT_KINDS, WARNING_KINDS

    assert "signal_source_mismatch" in ALERT_KINDS
    assert "signal_source_mismatch" in WARNING_KINDS


def test_alpaca_session_daily_bars_uses_the_explicit_feed_without_fallback() -> None:
    import pandas as pd
    from alpaca.data.enums import DataFeed

    from parayabuck.data.alpaca import AlpacaHistoricalData

    requests: list[Any] = []
    frame = pd.DataFrame(
        {
            "symbol": ["QQQ", "QLD"],
            "timestamp": [pd.Timestamp(_PROVIDER_LABEL), pd.Timestamp(_PROVIDER_LABEL)],
            "open": [439.0, 100.0],
            "high": [441.0, 101.0],
            "low": [438.0, 99.0],
            "close": [440.0, 100.5],
            "volume": [1e6, 2e6],
        }
    ).set_index(["symbol", "timestamp"])

    class _Client:
        def get_stock_bars(self, request: Any) -> Any:
            requests.append(request)
            return SimpleNamespace(df=frame)

    client = AlpacaHistoricalData.__new__(AlpacaHistoricalData)
    client._stock_client = _Client()
    client.provider_calls = client.provider_symbols_requested = 0
    client._sip_fallback_active = False
    bars = client.get_session_daily_bars(["QQQ", "QLD"], _PROVIDER_LABEL, _CLOSE)
    assert {symbol: bar.close for symbol, bar in bars.items()} == {"QQQ": 440.0, "QLD": 100.5}
    assert requests[0].feed == DataFeed.SIP
    assert client._sip_fallback_active is False
