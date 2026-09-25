"""Explanation-metadata, alerting and runtime-metric contract tests.

Worker D acceptance suite:

* the crypto low-variance identity carries concise per-holding decision
  reasons in ``TargetPortfolio.metadata["explanations"]``, derived EXACTLY
  from the production ``_score_candidates`` output (no second scoring path);
* ``ObservationSetResult`` carries ``strategy_name`` / ``ranked_pool`` /
  ``alpha_scored_count`` for strategy-alpha observation-set selection;
* ``parayabuck.monitoring.alerts.emit`` never raises, delivers to the log
  sink, POSTs to a configured webhook, and throttles repeat failures;
* ``parayabuck.monitoring.metrics.runtime_metrics`` reads an empty or
  temp runtime ledger without raising and aggregates persisted state plus
  process-local counters.
"""

from __future__ import annotations

import json
import queue
import threading
from datetime import UTC, date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from parayabuck.core.models import Bar, Market
from parayabuck.execution.ledger import ExecutionLedger
from parayabuck.monitoring import alerts, metrics
from parayabuck.portfolio.base import PortfolioContext
from parayabuck.portfolio.crypto_low_variance import CryptoLowVariance1dV1
from parayabuck.risk.state import RiskState
from parayabuck.universe.observation_set import build_observation_set

# ---------------------------------------------------------------------------
# Shared bar/context builders (same synthetic-history pattern as
# tests/test_crypto_low_variance.py)
# ---------------------------------------------------------------------------

VARIANCE_LOOKBACK = 22
MIN_HISTORY_BARS = VARIANCE_LOOKBACK + 1
CRYPTO_SYMBOLS = ("LOVOL/USD", "MIDVOL/USD", "HIVOL/USD")


def _bar(symbol: str, timestamp: datetime, close: float, market: Market) -> Bar:
    return Bar(
        timestamp=timestamp,
        symbol=symbol,
        timeframe="1d",
        open=close,
        high=close,
        low=close,
        close=close,
        volume=1_000_000.0,
        market=market,
    )


def _crypto_series(symbol: str, amplitude: float, days: int = 40) -> tuple[Bar, ...]:
    start = datetime(2024, 1, 1, tzinfo=UTC)
    return tuple(
        _bar(
            symbol,
            start + timedelta(days=index),
            100.0 + amplitude if index % 2 else 100.0,
            Market.CRYPTO,
        )
        for index in range(days)
    )


def _crypto_history() -> dict[str, tuple[Bar, ...]]:
    return {
        "LOVOL/USD": _crypto_series("LOVOL/USD", 0.0),
        "MIDVOL/USD": _crypto_series("MIDVOL/USD", 1.0),
        "HIVOL/USD": _crypto_series("HIVOL/USD", 10.0),
    }


def _crypto_context(holdings: dict[str, float] | None = None) -> PortfolioContext:
    history = _crypto_history()
    last_bar = max(bar.timestamp for series in history.values() for bar in series)
    return PortfolioContext(
        decision_timestamp=last_bar,
        market=Market.CRYPTO,
        timeframe="1d",
        history=history,
        universe=None,
        universe_version="test",
        equity=100_000.0,
        holdings=holdings or {},
        prices={},
        benchmark_symbol="BTC/USD",
    )


# ---------------------------------------------------------------------------
# Crypto explanation metadata
# ---------------------------------------------------------------------------


def test_crypto_explanations_carry_required_keys() -> None:
    strategy = CryptoLowVariance1dV1()
    target = strategy.decide(_crypto_context(holdings={"LOVOL/USD": 1.5}))
    explanations = target.metadata["explanations"]
    assert set(explanations) == {weight.symbol for weight in target.weights}
    for symbol, explanation in explanations.items():
        assert set(explanation) == {
            "alpha_rank",
            "realized_vol_22d",
            "target_weight",
            "current_weight",
        }
        assert explanation["alpha_rank"] >= 1
        assert explanation["realized_vol_22d"] >= 0.0
        assert explanation["target_weight"] == pytest.approx(target.weight_for(symbol, Market.CRYPTO))
    assert explanations["LOVOL/USD"]["realized_vol_22d"] == 0.0  # flat series
    assert explanations["LOVOL/USD"]["current_weight"] == 1.5
    assert explanations["MIDVOL/USD"]["current_weight"] == 0.0


def test_crypto_explanations_match_production_ranking() -> None:
    strategy = CryptoLowVariance1dV1()
    context = _crypto_context()
    ranked = strategy._score_candidates(context)
    target = strategy.decide(context)
    explanations = target.metadata["explanations"]
    for rank, (sigma, symbol) in enumerate(ranked, start=1):
        if symbol not in explanations:
            continue
        assert explanations[symbol]["alpha_rank"] == rank
        assert explanations[symbol]["realized_vol_22d"] == pytest.approx(sigma)
    # Lowest-variance names first, exactly the rank_candidates order.
    ranked_symbols = strategy.rank_candidates(context)
    assert ranked_symbols[:2] == ("LOVOL/USD", "MIDVOL/USD")
    assert [symbol for symbol in ranked_symbols if symbol in explanations] == [
        symbol
        for symbol, _ in sorted(
            ((symbol, exp["alpha_rank"]) for symbol, exp in explanations.items()),
            key=lambda item: item[1],
        )
    ]


# ---------------------------------------------------------------------------
# Observation-set metadata (ObservationSetResult)
# ---------------------------------------------------------------------------


def test_crypto_observation_set_metadata() -> None:
    strategy = CryptoLowVariance1dV1()
    history = _crypto_history()
    result = build_observation_set(
        strategy,
        history,
        pool_symbols=CRYPTO_SYMBOLS,
        market=Market.CRYPTO,
        timeframe="1d",
        decision_timestamp=max(bar.timestamp for series in history.values() for bar in series),
        maximum_stream_symbols=3,
    )
    assert result.strategy_name == "crypto_low_variance_v1"
    assert result.ranked_pool == ("LOVOL/USD", "MIDVOL/USD", "HIVOL/USD")
    assert result.alpha_scored_count == 3
    assert result.observation_set == result.ranked_pool
    assert result.source == "strategy_alpha"


def test_observation_set_without_ranker_is_empty_state_safe() -> None:
    result = build_observation_set(
        None,
        {},
        pool_symbols=("AAA",),
        market=Market.EQUITY,
        timeframe="1d",
        decision_timestamp=datetime(2024, 1, 1, tzinfo=UTC),
        maximum_stream_symbols=1,
    )
    assert result.strategy_name == ""
    assert result.ranked_pool == ()
    assert result.alpha_scored_count == 0
    assert result.source == "no_ranker"


# ---------------------------------------------------------------------------
# Alerts module
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_observability() -> None:
    alerts.reset_for_tests()
    metrics.reset_for_tests()
    yield
    alerts.reset_for_tests()
    metrics.reset_for_tests()


def test_alerts_emit_never_raises_and_accepts_all_kinds() -> None:
    for kind in sorted(alerts.ALERT_KINDS):
        alerts.emit(kind, message=f"test {kind}", extra=1)
    # Unknown kinds are rejected without raising.
    alerts.emit("not_a_real_kind", message="ignored")
    assert alerts.alert_stats()["suppressed"] == 0


def test_alerts_webhook_delivery() -> None:
    received: queue.Queue[dict[str, object]] = queue.Queue()

    class _Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802 (http.server convention)
            length = int(self.headers.get("Content-Length", "0"))
            received.put(json.loads(self.rfile.read(length)))
            self.send_response(200)
            self.end_headers()

        def log_message(self, *args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        alerts.configure_alerting(
            webhook_url=f"http://127.0.0.1:{server.server_address[1]}/alerts",
            throttle_seconds=0.0,
        )
        alerts.emit(
            "scanner_refresh_failure",
            message="scanner down",
            market="equity",
            attempt=1,
        )
        payload = received.get(timeout=5.0)
        assert payload["kind"] == "scanner_refresh_failure"
        assert payload["message"] == "scanner down"
        assert payload["fields"] == {"market": "equity", "attempt": 1}
        assert payload["occurred_at"]
        assert alerts.alert_stats()["webhook_sent"] == 1
    finally:
        server.shutdown()
        server.server_close()


def test_alerts_throttle_coalesces_repeat_failures() -> None:
    hits: list[dict[str, object]] = []
    sent = queue.Queue()

    class _Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length))
            sent.put(payload)
            hits.append(payload)
            self.send_response(200)
            self.end_headers()

        def log_message(self, *args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        alerts.configure_alerting(
            webhook_url=f"http://127.0.0.1:{server.server_address[1]}/alerts",
            throttle_seconds=60.0,
        )
        alerts.emit("reconciliation_failure", message="first")
        alerts.emit("reconciliation_failure", message="second, throttled")
        delivered = sent.get(timeout=5.0)
        assert delivered["kind"] == "reconciliation_failure"
        assert delivered["message"] == "first"
        assert delivered["fields"] == {}
        assert len(hits) == 1
        assert alerts.alert_stats()["suppressed"] == 1
        assert alerts.alert_stats()["webhook_sent"] == 1
        # A different kind is NOT throttled by the same window.
        alerts.emit("scanner_refresh_failure", message="other kind")
        assert sent.get(timeout=5.0)["kind"] == "scanner_refresh_failure"
    finally:
        server.shutdown()
        server.server_close()


def test_alerts_webhook_event_filtering_and_disable() -> None:
    hits: list[str] = []

    class _Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("Content-Length", "0"))
            hits.append(json.loads(self.rfile.read(length))["kind"])
            self.send_response(200)
            self.end_headers()

        def log_message(self, *args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    def _await_hits(count: int, timeout: float = 5.0) -> None:
        deadline = datetime.now(UTC) + timedelta(seconds=timeout)
        while len(hits) < count and datetime.now(UTC) < deadline:
            threading.Event().wait(0.05)
        assert len(hits) >= count

    try:
        url = f"http://127.0.0.1:{server.server_address[1]}/alerts"
        alerts.configure_alerting(webhook_url=url, throttle_seconds=0.0)
        alerts.emit("runtime_started", message="started")
        _await_hits(1)
        assert hits == ["runtime_started"]
        # Restrict the webhook event set; console/log sink still receives all.
        alerts.configure_alerting(webhook_url=url, throttle_seconds=0.0, events=("runtime_started",))
        alerts.emit("scanner_refresh_failure", message="not delivered to webhook")
        assert hits == ["runtime_started"]
        # Disable the webhook entirely.
        alerts.configure_alerting(webhook_url=url, throttle_seconds=0.0, enabled=False)
        alerts.emit("runtime_started", message="started again")
        assert hits == ["runtime_started"]
    finally:
        server.shutdown()
        server.server_close()


# ---------------------------------------------------------------------------
# Runtime metrics
# ---------------------------------------------------------------------------


def test_runtime_metrics_empty_state_is_safe(tmp_path) -> None:
    missing = tmp_path / "does-not-exist.sqlite3"
    snapshot = metrics.runtime_metrics(missing)
    assert snapshot["sessions"]["total"] == 0
    assert snapshot["restart_count"] == 0
    assert snapshot["incidents"]["count"] == 0
    assert snapshot["risk_state"]["latest"] == []
    for key in (
        "scanner_failures",
        "reconciliation_failures",
        "data_gap_events",
        "daily_loss_incidents",
        "drawdown_incidents",
        "circuit_breaker_events",
    ):
        assert snapshot[key] == 0
    assert snapshot["entry_block_duration_seconds"] is None


def test_runtime_metrics_reads_persisted_state(tmp_path) -> None:
    ledger = ExecutionLedger(str(tmp_path / "runtime.sqlite3"))
    try:
        session_a = ledger.record_runtime_session("account-1", "paper")
        session_b = ledger.record_runtime_session("account-1", "paper")
        ledger.end_runtime_session(session_a, "stopped")
        ledger.save_safety_incident("account-1", "portfolio_drawdown_halt")
        ledger.save_safety_incident("account-2", "ownership_drift")
        now = datetime.now(UTC)
        ledger.save_risk_state(
            RiskState(
                account_id="account-1",
                session_date=date(2026, 8, 12),
                day_start_equity=100_000.0,
                peak_equity=101_000.0,
                realized_pnl=-900.0,
                unrealized_pnl=-100.0,
                fees=3.5,
                consecutive_losses=2,
                circuit_breaker_reason=None,
                updated_at=now,
            )
        )
        ledger_path = str(tmp_path / "runtime.sqlite3")
        snapshot = metrics.runtime_metrics(ledger_path)
        assert snapshot["sessions"]["total"] == 2
        assert snapshot["sessions"]["completed"] == 1
        assert snapshot["sessions"]["unended"] == 1
        assert snapshot["restart_count"] == 1  # second start is the first restart
        assert snapshot["incidents"]["count"] == 2
        reasons = {item["reason"] for item in snapshot["incidents"]["active"]}
        assert reasons == {"portfolio_drawdown_halt", "ownership_drift"}
        assert snapshot["risk_state"]["latest"][0]["account_id"] == "account-1"
        assert snapshot["risk_state"]["latest"][0]["realized_pnl"] == -900.0
        assert snapshot["entry_block_duration_seconds"] is not None
        assert snapshot["entry_block_duration_seconds"] >= 0.0
        # account_id narrows the persisted queries.
        scoped = metrics.runtime_metrics(ledger_path, account_id="account-1")
        assert scoped["incidents"]["count"] == 1
        assert [item["reason"] for item in scoped["incidents"]["active"]] == ["portfolio_drawdown_halt"]
        assert session_b  # keep the second session id referenced
    finally:
        ledger.close()


def test_runtime_metrics_merges_process_local_counters(tmp_path) -> None:
    metrics.record_event("scanner_refresh_failure")
    metrics.record_event("scanner_refresh_failure")
    metrics.record_event("scanner_recovered")
    metrics.record_event("data_gap")
    metrics.record_event("circuit_breaker")
    metrics.record_event("not_a_counter")  # ignored
    snapshot = metrics.runtime_metrics(tmp_path / "empty.sqlite3")
    counters = snapshot["counters"]
    assert counters["scanner_refresh_failure"] == 2
    assert counters["scanner_recovered"] == 1
    assert counters["data_gap"] == 1
    assert counters["circuit_breaker"] == 1
    assert counters["runtime_started"] == 0  # zero-filled, never missing
    assert snapshot["scanner_failures"] == 2
    assert snapshot["data_gap_events"] == 1
    assert snapshot["circuit_breaker_events"] == 1
