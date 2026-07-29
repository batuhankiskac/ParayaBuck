from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from parayabuck.core.models import (
    Bar,
    Direction,
    Fill,
    Market,
    OrderIntent,
    OrderState,
    Regime,
    Signal,
)
from parayabuck.markets.regime import RegimeAssessment
from parayabuck.monitoring import AIDecisionJournal
from parayabuck.strategies.base import (
    ConditionEvaluation,
    StrategyEvaluation,
)


def _bar(
    minute: int,
    close: float = 100,
    low: float | None = None,
    high: float | None = None,
) -> Bar:
    return Bar(
        datetime(2025, 1, 1, tzinfo=UTC) + timedelta(minutes=5 * minute),
        "TEST",
        "5m",
        close,
        high if high is not None else close + 0.5,
        low if low is not None else close - 0.5,
        close,
        1_000,
        Market.EQUITY,
    )


def _signal(timestamp: datetime) -> Signal:
    return Signal(
        "TEST",
        Market.EQUITY,
        Direction.LONG,
        "test_strategy",
        80,
        0.8,
        100,
        98,
        104,
        timestamp,
        ("test_reason",),
        Regime.RANGE,
        "fixed-signal",
    )


def _evaluation(signal: Signal) -> StrategyEvaluation:
    return StrategyEvaluation(
        "test_strategy",
        True,
        "The test setup fired.",
        (
            ConditionEvaluation(
                "test_gate",
                True,
                10,
                ">= 5",
                "The test threshold passed.",
            ),
        ),
        {"indicator": 10},
        signal,
    )


def _record(journal: AIDecisionJournal) -> None:
    bar = _bar(0)
    signal = _signal(bar.timestamp)
    assessment = RegimeAssessment(Regime.RANGE, 0.8, 12, 0.01, ("weak_trend",))
    journal.record_signal(
        bar,
        signal,
        _evaluation(signal),
        assessment,
        Regime.UNKNOWN.value,
        Regime.UNKNOWN.value,
        [
            {"gate": "strategy", "status": "passed", "reason": "setup fired"},
            {"gate": "risk", "status": "failed", "reason": "test rejection"},
        ],
        False,
    )


def test_same_bar_target_and_stop_resolves_as_stop(tmp_path: Path) -> None:
    journal = AIDecisionJournal(str(tmp_path), "paper", "5m", horizon_bars=48)
    _record(journal)

    assert journal.observe_bar(_bar(0, low=97, high=105)) == ()
    outcomes = journal.observe_bar(_bar(1, low=97, high=105))

    assert len(outcomes) == 1
    assert outcomes[0].outcome == "stop_hit"
    assert outcomes[0].verdict == "wrong"
    assert outcomes[0].mae_pct == -3
    decision = next((tmp_path / "paper" / "2025-01-01").glob("*test_strategy*.md"))
    text = decision.read_text()
    assert "## Prediction vs. Actual" in text
    assert "WRONG (stop_hit)" in text

    events = [
        json.loads(line)
        for line in (tmp_path / "paper" / "2025-01-01" / "events.jsonl").read_text().splitlines()
    ]
    assert [event["event_type"] for event in events] == ["decision", "outcome"]
    stats = json.loads((tmp_path / "paper" / "strategy_stats.json").read_text())
    assert stats["test_strategy"]["virtual_resolved"] == 1
    assert stats["test_strategy"]["accuracy"] == 0


def test_open_prediction_survives_restart_and_times_out(tmp_path: Path) -> None:
    first = AIDecisionJournal(str(tmp_path), "paper", "5m", horizon_bars=2)
    _record(first)

    second = AIDecisionJournal(str(tmp_path), "paper", "5m", horizon_bars=2)
    assert second.observe_bar(_bar(1, close=101)) == ()
    outcomes = second.observe_bar(_bar(2, close=102))

    assert len(outcomes) == 1
    assert outcomes[0].outcome == "timeout_correct"
    assert outcomes[0].return_pct == 2
    assert json.loads((tmp_path / "paper" / "open_predictions.json").read_text()) == []


def test_async_entry_fill_marks_prediction_as_traded(tmp_path: Path) -> None:
    journal = AIDecisionJournal(str(tmp_path), "paper", "5m", horizon_bars=2)
    bar = _bar(0)
    signal = _signal(bar.timestamp)
    order = OrderIntent(
        signal.signal_id,
        signal.symbol,
        signal.market,
        signal.direction,
        1,
        signal.entry_price,
        signal.invalidation_price,
        signal.target_price,
        signal.strategy,
        client_order_id="parayabuck-async-entry",
    )
    journal.record_signal(
        bar,
        signal,
        _evaluation(signal),
        RegimeAssessment(Regime.RANGE, 0.8, 12, 0.01, ("weak_trend",)),
        Regime.UNKNOWN.value,
        Regime.UNKNOWN.value,
        [],
        False,
        order=order,
        order_state=OrderState.ACCEPTED,
    )

    journal.record_broker_fill(
        Fill(order.client_order_id, order.symbol, 1, 100, Direction.LONG, _bar(1).timestamp)
    )

    assert journal._open[signal.signal_id].traded is True
    persisted = json.loads((tmp_path / "paper" / "open_predictions.json").read_text())
    assert persisted[0]["traded"] is True


def test_scan_digest_records_first_strategy_reason(tmp_path: Path) -> None:
    journal = AIDecisionJournal(str(tmp_path), "paper", "5m")
    bar = _bar(0)
    assessment = RegimeAssessment(Regime.UNKNOWN, 0, 0, 0, ("insufficient_history",))
    evaluation = StrategyEvaluation(
        "test_strategy",
        False,
        "Insufficient history.",
        (
            ConditionEvaluation(
                "strategy_support",
                False,
                {"bars": 1},
                "bars>=50",
                "Insufficient history.",
            ),
        ),
    )

    journal.record_scan(bar, assessment, "none", (evaluation,))

    digest = tmp_path / "paper" / "2025-01-01" / "scan-digest.md"
    assert "test_strategy: skipped (Insufficient history.)" in digest.read_text()
