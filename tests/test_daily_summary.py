"""Equity session-close daily summary notification (offline, no credentials)."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

import parayabuck.monitoring.alerts as alerts
from parayabuck.core.models import AccountSnapshot, Market, PositionSnapshot
from parayabuck.monitoring.daily_summary import build_equity_daily_summary
from parayabuck.portfolio.models import RebalanceReason, TargetPortfolio, TargetWeight
from parayabuck.runtime import EngineRuntime

_DECISION_TS = datetime(2026, 9, 29, 13, 30, tzinfo=UTC)  # stream label of 2026-09-29


def _target(weight: float = 0.8) -> TargetPortfolio:
    weights = (TargetWeight("QLD", weight, reason="trend_up", rank=1),) if weight else ()
    return TargetPortfolio(
        strategy_name="equity_leveraged_trend_v1",
        strategy_version="1.0.0",
        decision_timestamp=_DECISION_TS,
        weights=weights,
        cash_weight=1.0 - weight,
        rebalance_reason=RebalanceReason.SCHEDULED if weight else RebalanceReason.RISK_OFF,
        metadata={
            "trend_state": "uptrend" if weight else "downtrend",
            "signal_symbol": "QQQ",
            "hold_symbol": "QLD",
            "signal_close": 550.0,
            "signal_sma": 500.0,
            "sma_bars": 200,
        },
    )


def _account() -> AccountSnapshot:
    return AccountSnapshot(
        cash=30_000.0,
        equity=101_000.0,
        peak_equity=101_000.0,
        daily_pnl=1_000.0,
        positions=(
            PositionSnapshot("QLD", 700.0, 90.0, 100.0, Market.EQUITY, managed_qty=700.0),
            PositionSnapshot("BTCUSD", 0.05, 60_000.0, 62_000.0, Market.CRYPTO, managed_qty=0.05),
        ),
    )


@pytest.fixture(autouse=True)
def _alerts_reset():
    alerts.reset_for_tests()
    yield
    alerts.reset_for_tests()


def test_summary_line_carries_trend_weights_positions_and_equity() -> None:
    message, fields = build_equity_daily_summary(
        _target(0.8), _account(), status="submitted", decision_id="d1", orders=1, submitted=1
    )
    assert "\n" not in message
    assert "2026-09-29" in message
    assert "QQQ 550.00 vs SMA200 500.00 (+10.00%) uptrend" in message
    assert "QLD target 80.0% held 69.3%" in message
    assert "pos BTC/USD 0.05, QLD 700" in message
    assert "equity $101,000.00 (+1,000.00, +1.00% d/d)" in message
    assert fields["target_weight"] == pytest.approx(0.8)
    assert fields["held_weight"] == pytest.approx(70_000.0 / 101_000.0)
    assert fields["signal_sma_distance_pct"] == pytest.approx(10.0)
    assert fields["daily_change_pct"] == pytest.approx(1.0)
    assert fields["positions"] == {"QLD": 700.0, "BTC/USD": 0.05}


def test_summary_tolerates_missing_account_and_history() -> None:
    target = _target(0.0)
    target = TargetPortfolio(**{**target.__dict__, "metadata": {"trend_state": "insufficient_history"}})
    message, fields = build_equity_daily_summary(target, None, status="submitted", decision_id=None)
    assert "equity n/a" in message
    assert fields["held_weight"] is None
    assert fields["account_equity"] is None


def _shell(daily_summary: bool = True) -> EngineRuntime:
    runtime = EngineRuntime.__new__(EngineRuntime)
    runtime.app_config = SimpleNamespace(alerting=SimpleNamespace(daily_summary=daily_summary))
    return runtime


def _result(target: TargetPortfolio | None, decision_id: str | None = "d1") -> SimpleNamespace:
    return SimpleNamespace(
        decision_id=decision_id, target=target, status="submitted", orders=(object(),), submitted_order_ids=("x",)
    )


def test_runtime_summary_is_logged_but_not_posted_without_webhook_url(monkeypatch) -> None:
    posted: list[dict[str, object]] = []
    monkeypatch.setattr(alerts, "_enqueue_webhook", lambda url, payload: posted.append(payload))
    alerts.configure_alerting(enabled=True, webhook_url="", throttle_seconds=0)
    _shell()._emit_equity_daily_summary(SimpleNamespace(market=Market.EQUITY), _result(_target()), _account())
    stats = alerts.alert_stats()
    assert stats["delivered"] == {"daily_summary": 1}
    assert stats["webhook_attempted"] == 0
    assert posted == []


def test_runtime_summary_is_posted_once_when_webhook_configured(monkeypatch) -> None:
    posted: list[tuple[str, dict[str, object]]] = []
    monkeypatch.setattr(alerts, "_enqueue_webhook", lambda url, payload: posted.append((url, payload)))
    alerts.configure_alerting(enabled=True, webhook_url="http://hooks.invalid/abc", throttle_seconds=0)
    _shell()._emit_equity_daily_summary(SimpleNamespace(market=Market.EQUITY), _result(_target()), _account())
    assert len(posted) == 1
    url, payload = posted[0]
    assert url == "http://hooks.invalid/abc"
    assert payload["kind"] == "daily_summary"
    assert payload["text"] == payload["message"] == payload["content"]
    assert "QLD target 80.0%" in str(payload["message"])
    assert payload["decision_id"] == "d1"


@pytest.mark.parametrize(
    ("runtime", "market", "result"),
    [
        (_shell(daily_summary=False), Market.EQUITY, _result(_target())),
        (_shell(), Market.CRYPTO, _result(_target())),
        (_shell(), Market.EQUITY, _result(None, decision_id="d1")),  # rebalance still in flight
        (_shell(), Market.EQUITY, _result(_target(), decision_id=None)),
    ],
)
def test_runtime_summary_skips_disabled_non_equity_and_non_decisions(monkeypatch, runtime, market, result) -> None:
    alerts.configure_alerting(enabled=True, webhook_url="", throttle_seconds=0)
    runtime._emit_equity_daily_summary(SimpleNamespace(market=market), result, _account())
    assert alerts.alert_stats()["delivered"] == {}


def test_daily_summary_is_a_default_webhook_event() -> None:
    from parayabuck.config import AlertingConfig
    from parayabuck.monitoring.events import DEFAULT_ALERT_EVENTS

    assert "daily_summary" in DEFAULT_ALERT_EVENTS
    assert AlertingConfig().daily_summary is True
