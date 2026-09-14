from __future__ import annotations

import inspect
from datetime import datetime

from scripts.research import diagnose_r46_incumbent_decision_pipeline_2026_09_14 as diagnosis


def timestamp(value: str) -> datetime:
    return diagnosis.aware(value)


def complete_observation() -> diagnosis.ObservationMetadata:
    current = frozenset({"SPY", "AAA"})
    latest = {symbol: timestamp("2017-02-06T05:00:00Z") for symbol in current}
    return diagnosis.ObservationMetadata(
        expected_symbols=("SPY", "AAA"),
        current_symbols=current,
        latest_by_symbol=latest,
        source="EXPLICIT_OBSERVATION_SET",
    )


def cycle(
    *,
    previous: str | None,
    current: str,
    event: bool = False,
    supports: bool | None = True,
    product_guard: bool | None = True,
    observation: diagnosis.ObservationMetadata | None = None,
) -> dict[str, object]:
    return diagnosis.trace_cycle(
        diagnosis.CycleContext(
            timestamp=timestamp(current),
            previous_timestamp=timestamp(previous) if previous is not None else None,
            event_boundary=event,
            event_type="ENTRY" if event else None,
            supports=supports,
            product_guard=product_guard,
            observation=observation or complete_observation(),
        )
    )


def test_weekly_boundary_detected_from_iso_week_transition() -> None:
    result = cycle(previous="2017-02-03T14:30:00Z", current="2017-02-06T05:00:00Z")

    assert result["native_weekly_scheduled"] is True
    assert result["scheduled"] is True


def test_skipped_completeness_cannot_materialize_target() -> None:
    observation = diagnosis.ObservationMetadata(
        expected_symbols=("SPY", "AAA"),
        current_symbols=frozenset({"SPY"}),
        latest_by_symbol={"SPY": timestamp("2017-02-06T05:00:00Z")},
        source="EXPLICIT_OBSERVATION_SET",
    )
    result = cycle(
        previous="2017-02-03T14:30:00Z",
        current="2017-02-06T05:00:00Z",
        observation=observation,
    )

    assert result["completeness_gate"] == "INCOMPLETE"
    assert result["final_decision_enum"] == "SKIPPED_OBSERVATION_COMPLETENESS"
    assert result["target_could_materialize"] is False
    assert result["strategy_decide_invoked_by_diagnostic"] is False


def test_previous_timestamp_advances_even_when_completeness_skips() -> None:
    observation = diagnosis.ObservationMetadata(
        expected_symbols=("SPY", "AAA"),
        current_symbols=frozenset({"SPY"}),
        latest_by_symbol={"SPY": timestamp("2017-02-06T05:00:00Z")},
        source="EXPLICIT_OBSERVATION_SET",
    )
    result = cycle(
        previous="2017-02-03T14:30:00Z",
        current="2017-02-06T05:00:00Z",
        observation=observation,
    )

    assert result["engine_previous_timestamp_after_cycle"] == "2017-02-06T05:00:00Z"


def test_next_same_week_timestamp_is_not_native_weekly() -> None:
    result = cycle(previous="2017-02-06T14:30:00Z", current="2017-02-07T05:00:00Z")

    assert result["native_weekly_scheduled"] is False
    assert result["scheduled"] is False
    assert result["final_decision_enum"] == "SKIPPED_OTHER_PRE_DECIDE_GATE"


def test_r46_extra_boundary_schedules_without_native_week_transition() -> None:
    result = cycle(
        previous="2017-02-09T14:30:00Z",
        current="2017-02-10T05:00:00Z",
        event=True,
        observation=diagnosis.ObservationMetadata(
            expected_symbols=None,
            current_symbols=frozenset(),
            latest_by_symbol={},
            source="R46_EVENT_BOUNDARY_OVERRIDE",
        ),
    )

    assert result["native_weekly_scheduled"] is False
    assert result["r46_extra_boundary_scheduled"] is True
    assert result["scheduled"] is True
    assert result["completeness_gate"] == "BYPASSED_R46_EVENT_BOUNDARY_OVERRIDE"
    assert result["would_reach_strategy_decide"] is True
    assert result["target_could_materialize"] is False


def test_incomplete_observation_reports_exact_missing_symbols() -> None:
    metadata = diagnosis.ObservationMetadata(
        expected_symbols=("SPY", "AAA", "BBB"),
        current_symbols=frozenset({"SPY", "AAA"}),
        latest_by_symbol={
            "SPY": timestamp("2017-02-06T05:00:00Z"),
            "AAA": timestamp("2017-02-06T05:00:00Z"),
        },
        source="EXPLICIT_OBSERVATION_SET",
    )

    result = diagnosis.observation_gate(metadata, timestamp("2017-02-06T05:00:00Z"))

    assert result["missing_count"] == 1
    assert result["missing_symbols"] == ["BBB"]
    assert result["stale_count"] == 0


def test_stale_observation_is_not_classified_as_missing() -> None:
    metadata = diagnosis.ObservationMetadata(
        expected_symbols=("SPY",),
        current_symbols=frozenset(),
        latest_by_symbol={"SPY": timestamp("2017-02-03T05:00:00Z")},
        source="EXPLICIT_OBSERVATION_SET",
    )

    result = diagnosis.observation_gate(metadata, timestamp("2017-02-06T05:00:00Z"))

    assert result["missing_count"] == 0
    assert result["stale_count"] == 1
    assert result["stale_symbols"] == ["SPY"]


def test_diagnostic_has_no_engine_or_strategy_decision_dependency() -> None:
    source = inspect.getsource(diagnosis)

    assert "from parayabuck.backtest" not in source
    assert "strategy.decide" not in source
    assert not hasattr(diagnosis, "PortfolioBacktestEngine")


def test_gate_output_has_no_economic_result_fields() -> None:
    result = cycle(previous="2017-02-03T14:30:00Z", current="2017-02-06T05:00:00Z")
    forbidden_keys = {
        "returns",
        "pnl",
        "sharpe",
        "sortino",
        "drawdown",
        "price",
        "volume",
        "weights",
        "orders",
        "fills",
    }

    assert not forbidden_keys.intersection(str(key).lower() for key in result)
