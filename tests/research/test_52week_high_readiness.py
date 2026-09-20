from __future__ import annotations

from datetime import UTC, datetime, timedelta
from importlib import import_module

import pytest

candidate = import_module("research.52week_high_readiness_v1.candidate")
month_end = import_module("research.52week_high_readiness_v1.month_end")
MAX_ALLOCATION = candidate.MAX_ALLOCATION
MAX_WEIGHT = candidate.MAX_WEIGHT
RESERVED_ROLES = candidate.RESERVED_ROLES
CloseObservation = candidate.CloseObservation
rank_broad_pool_through_t = candidate.rank_broad_pool_through_t
score_history_through_t = candidate.score_history_through_t
NyseLastCompletedSessionPolicy = month_end.NyseLastCompletedSessionPolicy


def _sessions(count: int = 252, *, end: datetime = datetime(2026, 1, 30, tzinfo=UTC)) -> tuple[datetime, ...]:
    return tuple(end - timedelta(days=count - index - 1) for index in range(count))


def _history(sessions: tuple[datetime, ...], *, last: float = 99.0, peak: float = 100.0):
    closes = [50.0 + index / 10 for index in range(len(sessions))]
    closes[-2] = peak
    closes[-1] = last
    return tuple(CloseObservation(timestamp, close) for timestamp, close in zip(sessions, closes, strict=True))


def test_exact_formula_includes_t_and_split_adjusted_continuity() -> None:
    sessions = _sessions()
    history = _history(sessions, last=99.0, peak=100.0)
    assert score_history_through_t(
        history,
        decision_timestamp=sessions[-1],
        expected_session_timestamps=sessions,
        identity_resolved=True,
    ) == pytest.approx(0.99)


@pytest.mark.parametrize("defect", ("short", "missing", "ambiguous", "future"))
def test_score_fails_closed(defect: str) -> None:
    sessions = _sessions()
    history = list(_history(sessions))
    expected = sessions
    identity = True
    decision = sessions[-1]
    if defect == "short":
        history = history[1:]
    elif defect == "missing":
        history[100] = CloseObservation(history[100].timestamp + timedelta(hours=1), history[100].adjusted_close)
    elif defect == "ambiguous":
        identity = False
    else:
        history[-1] = CloseObservation(decision + timedelta(days=1), history[-1].adjusted_close)
        expected = (*sessions[:-1], history[-1].timestamp)
    assert (
        score_history_through_t(
            history,
            decision_timestamp=decision,
            expected_session_timestamps=expected,
            identity_resolved=identity,
        )
        is None
    )


def test_broad_pool_rank_tie_break_roles_capacity_and_targets() -> None:
    sessions = _sessions()
    ordinary = tuple(f"S{index}" for index in range(10))
    pool = (*ordinary, *RESERVED_ROLES)
    histories = {symbol: _history(sessions, last=100 - index, peak=100) for index, symbol in enumerate(ordinary)}
    histories.update({symbol: _history(sessions, last=100, peak=100) for symbol in RESERVED_ROLES})
    identities = {symbol: True for symbol in pool}
    result = rank_broad_pool_through_t(
        pool,
        histories,
        decision_timestamp=sessions[-1],
        expected_session_timestamps=sessions,
        identity_resolved=identities,
        observation_capacity=8,
    )
    assert result.eligible_pool == tuple(sorted(pool))
    assert result.ranked[0][1] > result.ranked[-1][1]
    assert result.selected == tuple(f"S{index}" for index in range(6))
    assert not set(result.selected) & RESERVED_ROLES
    assert len(result.observation_set) == 8
    assert len(result.selected) == 6
    assert len({weight for _symbol, weight in result.target_weights}) == 1
    assert max(weight for _symbol, weight in result.target_weights) <= MAX_WEIGHT
    assert sum(weight for _symbol, weight in result.target_weights) <= MAX_ALLOCATION


def test_exact_ties_break_by_symbol() -> None:
    sessions = _sessions()
    histories = {symbol: _history(sessions, last=99, peak=100) for symbol in ("BBB", "AAA")}
    result = rank_broad_pool_through_t(
        ("BBB", "AAA"),
        histories,
        decision_timestamp=sessions[-1],
        expected_session_timestamps=sessions,
        identity_resolved={"AAA": True, "BBB": True},
        observation_capacity=6,
    )
    assert result.selected == ("AAA", "BBB")


@pytest.mark.parametrize(
    ("completed", "expected"),
    (
        (datetime(2026, 4, 30, 20, tzinfo=UTC), True),
        (datetime(2026, 1, 30, 21, tzinfo=UTC), True),
        (datetime(2024, 11, 27, 21, tzinfo=UTC), False),
        (datetime(2024, 11, 29, 18, tzinfo=UTC), True),
        (datetime(2025, 12, 31, 21, tzinfo=UTC), True),
        (datetime(2026, 2, 2, 21, tzinfo=UTC), False),
    ),
)
def test_month_end_uses_exchange_sessions(completed: datetime, expected: bool) -> None:
    assert NyseLastCompletedSessionPolicy().should_decide(completed) is expected


def test_missing_symbol_bar_cannot_redefine_month_end_and_next_open_is_later() -> None:
    policy = NyseLastCompletedSessionPolicy()
    completed = datetime(2026, 1, 30, 21, tzinfo=UTC)
    assert policy.should_decide(completed)
    assert policy.next_tradable_open(completed) > completed


def test_candidate_is_not_registered_in_production() -> None:
    from parayabuck.portfolio import portfolio_strategy_from

    with pytest.raises(ValueError):
        portfolio_strategy_from("equity_52week_high_close_v1", {})
