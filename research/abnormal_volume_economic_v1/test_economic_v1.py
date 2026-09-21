from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from parayabuck.core.models import Bar, Market
from parayabuck.data.market_dataset import CorporateAction, CorporateActionType
from research.abnormal_volume_economic_v1.economic_models import (
    AuditedPortfolioBacktestEngine,
    ExactDecisionPolicy,
    FrozenTargetStrategy,
    dividend_events,
    one_way_turnover,
    period_metrics,
    targets_from_readiness,
)
from research.abnormal_volume_economic_v1.run_economic_v1 import _raw_action_ids


def _bar(day: int, *, open_price: float = 10.0, close: float = 10.0) -> Bar:
    return Bar(
        timestamp=datetime(2022, 1 if day <= 31 else 2, day if day <= 31 else day - 31, 21, tzinfo=UTC),
        symbol="AAA",
        timeframe="1d",
        open=open_price,
        high=max(open_price, close),
        low=min(open_price, close),
        close=close,
        volume=1_000_000.0,
        market=Market.EQUITY,
    )


def test_targets_from_readiness_uses_only_first_open_available_rows() -> None:
    timestamp = "2022-01-31T21:00:00+00:00"
    payload = {
        "candidate": "AV49_D1_NORMALRET_M1_LONG / DESIGN_V2",
        "decisions": [
            {
                "decision_timestamp": timestamp,
                "decision_session": "2022-01-31",
                "targets": [
                    {"symbol": "AAA", "target_weight": "0.10", "fill_available": True},
                    {"symbol": "BBB", "target_weight": "0.10", "fill_available": False},
                ],
            }
        ],
    }
    targets = targets_from_readiness(payload)
    assert targets[datetime.fromisoformat(timestamp)] == (("AAA", 0.1),)


def test_readiness_contract_timestamp_maps_to_engine_session_marker() -> None:
    payload = {
        "candidate": "AV49_D1_NORMALRET_M1_LONG / DESIGN_V2",
        "decisions": [
            {
                "decision_session": "2022-01-31",
                "decision_timestamp": "2022-01-31T21:00:00+00:00",
                "targets": [{"symbol": "AAA", "target_weight": "0.10", "fill_available": True}],
            }
        ],
    }
    marker = datetime(2022, 1, 31, 5, tzinfo=UTC)
    targets = targets_from_readiness(payload, {marker.date(): marker})
    assert targets == {marker: (("AAA", 0.1),)}


def test_exact_policy_and_frozen_strategy_fill_next_open() -> None:
    decision = datetime(2022, 1, 31, 21, tzinfo=UTC)
    fill = datetime(2022, 2, 1, 21, tzinfo=UTC)
    strategy = FrozenTargetStrategy({decision: (("AAA", 0.1),)})
    engine = AuditedPortfolioBacktestEngine(
        strategy,
        ExactDecisionPolicy((decision,)),
        starting_equity=100_000.0,
        slippage_bps=2.0,
        spread_bps=2.0,
        allow_static_universe=True,
        contract_fill_timestamps={decision: fill},
    )
    result = engine.run([_bar(31), _bar(32, open_price=11.0, close=11.25)])
    assert len(result.rebalances) == 1
    assert len(result.fills) == 1
    fill = result.fills[0]
    assert fill.decision_timestamp == decision
    assert fill.timestamp == datetime(2022, 2, 1, 21, tzinfo=UTC)
    assert fill.reference_price == 11.0
    assert fill.timestamp > fill.decision_timestamp
    assert engine.audit_snapshots[-1]["nav"] == pytest.approx(result.ending_equity)


def test_dividend_event_replay_matches_engine_apply_once() -> None:
    decision = datetime(2022, 1, 31, 21, tzinfo=UTC)
    strategy = FrozenTargetStrategy({decision: (("AAA", 0.1),)})
    fill = datetime(2022, 2, 1, 21, tzinfo=UTC)
    engine = AuditedPortfolioBacktestEngine(
        strategy,
        ExactDecisionPolicy((decision,)),
        starting_equity=100_000.0,
        allow_static_universe=True,
        contract_fill_timestamps={decision: fill},
    )
    action = CorporateAction(
        timestamp=datetime(2022, 2, 2, tzinfo=UTC),
        symbol="AAA",
        action_type=CorporateActionType.DIVIDEND,
        value=0.25,
    )
    result = engine.run([_bar(31), _bar(32), _bar(33)], corporate_actions=[action])
    rows = dividend_events(result, [action], {("AAA", "2022-02-02", 0.25): "raw-1"})
    assert len(rows) == 1
    assert rows[0]["cash_credit"] == pytest.approx(result.dividend_total)
    assert rows[0]["raw_id"] == "raw-1"


def test_period_metrics_use_preceding_nav_anchor_and_inclusive_bounds() -> None:
    result = SimpleNamespace(
        curve_timestamps=(
            datetime(2021, 12, 31, tzinfo=UTC),
            datetime(2022, 1, 3, tzinfo=UTC),
            datetime(2022, 1, 4, tzinfo=UTC),
        ),
        equity_curve=(100.0, 101.0, 102.0),
        fills=(),
        terminal_events=(),
        rebalances=(),
        closed_position_events=(),
    )
    metrics = period_metrics(
        result,
        datetime(2022, 1, 3, tzinfo=UTC),
        datetime(2022, 1, 4, 23, 59, tzinfo=UTC),
    )
    assert metrics["curve_point_count"] == 3
    assert metrics["net_total_return"] == pytest.approx(0.02)


def test_one_way_turnover_matches_repository_half_absolute_delta_convention() -> None:
    assert one_way_turnover((("AAA", 0.10), ("BBB", 0.10)), ()) == pytest.approx(0.10)
    assert one_way_turnover((("AAA", 0.05), ("CCC", 0.10)), (("AAA", 0.10), ("BBB", 0.10))) == pytest.approx(0.125)


def test_partial_order_expires_after_contracted_first_open() -> None:
    decision = datetime(2022, 1, 31, 21, tzinfo=UTC)
    first_open = datetime(2022, 2, 1, 21, tzinfo=UTC)
    strategy = FrozenTargetStrategy({decision: (("AAA", 0.1),)})
    engine = AuditedPortfolioBacktestEngine(
        strategy,
        ExactDecisionPolicy((decision,)),
        starting_equity=100_000.0,
        max_participation_pct=0.000001,
        allow_static_universe=True,
        contract_fill_timestamps={decision: first_open},
    )
    result = engine.run([_bar(31), _bar(32), _bar(33)])
    assert len(result.fills) == 1
    assert result.fills[0].timestamp == first_open
    assert len(engine.first_open_expirations) == 1
    assert engine.first_open_expirations[0]["expiry_cycle_timestamp"] == first_open.isoformat()
    assert engine.first_open_expirations[0]["expired_qty"] > 0


def test_duplicate_dividend_identity_uses_first_canonical_occurrence(tmp_path: Path) -> None:
    path = tmp_path / "actions.csv"
    path.write_text(
        "action_type,symbol,ex_date,rate,raw_id\n"
        "cash_dividends,AAA,2022-02-02,0.25,first\n"
        "cash_dividends,AAA,2022-02-02,0.25,second\n",
        encoding="utf-8",
    )
    assert _raw_action_ids(path)[("AAA", "2022-02-02", 0.25)] == "first"


@pytest.mark.parametrize(
    "targets",
    [
        (("SPY", 0.1),),
        tuple((f"A{index}", 0.09) for index in range(7)),
        (("AAA", 0.11),),
    ],
)
def test_frozen_strategy_rejects_contract_violations(targets: tuple[tuple[str, float], ...]) -> None:
    with pytest.raises(ValueError):
        FrozenTargetStrategy({datetime(2022, 1, 31, tzinfo=UTC): targets})
