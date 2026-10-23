"""Drawdown context: the current drawdown against K's backtest episodes.

Display-only. These tests pin the static episode table's shape and the
counting rules (threshold inclusive, completed episodes only, empty above the
deepest episode, unavailable for missing or tiny drawdowns).
"""

from __future__ import annotations

import math

import pytest

from parayabuck.web.services.drawdown_context import (
    BACKTEST_END,
    BACKTEST_START,
    K_EPISODES,
    drawdown_context,
)
from parayabuck.web.services.trend_status import trend_status_from_metadata


def test_episode_table_is_ordered_and_consistent() -> None:
    assert len(K_EPISODES) == 53
    peaks = [row[0] for row in K_EPISODES]
    assert peaks == sorted(peaks)
    for peak, trough, recovery, depth, to_trough, to_recover in K_EPISODES:
        assert BACKTEST_START <= peak <= trough < recovery <= BACKTEST_END
        assert 5.0 <= depth < 50.0
        assert 0 < to_trough < to_recover
    # The table's deepest episode is the backtest's max drawdown (2000-03 -> 2003-03).
    assert max(row[3] for row in K_EPISODES) == pytest.approx(46.67)


def test_counts_are_inclusive_of_the_threshold() -> None:
    depth = K_EPISODES[0][3]  # 9.13 %
    expected = sum(1 for row in K_EPISODES if row[3] >= depth)
    context = drawdown_context(depth)

    assert context["available"] is True
    assert context["deeper_count"] == expected
    assert context["episodes_total"] == len(K_EPISODES)
    assert context["deeper_per_year"] == pytest.approx(expected / 26.56)


def test_october_2026_scale_drawdown() -> None:
    context = drawdown_context(14.0)

    assert context["deeper_count"] == 27
    assert context["median_sessions_to_recover"] == 103
    assert context["median_sessions_to_trough"] == 41
    assert context["last_deeper_peak"] == "2025-10-29"


def test_deeper_than_any_backtest_episode() -> None:
    context = drawdown_context(55.0)

    assert context["available"] is True
    assert context["deeper_count"] == 0
    assert context["median_sessions_to_recover"] is None
    assert context["last_deeper_peak"] is None
    assert context["deepest_pct"] == pytest.approx(46.67)


@pytest.mark.parametrize("value", [None, 0.0, 0.5, math.nan, math.inf, True, "14"])
def test_unavailable_for_missing_or_tiny_drawdowns(value: object) -> None:
    assert drawdown_context(value) == {"available": False}  # type: ignore[arg-type]


def test_trend_status_carries_the_context() -> None:
    status = trend_status_from_metadata({}, {"equity": 86_000.0, "drawdown": 14_000.0})

    assert status["drawdown_pct"] == pytest.approx(14.0)
    assert status["drawdown_context"]["deeper_count"] == 27
    assert trend_status_from_metadata({}, None)["drawdown_context"] == {"available": False}
