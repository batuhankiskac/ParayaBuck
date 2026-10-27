"""Contract tests for the R2 early re-entry path of ``equity_leveraged_trend_v1``.

``early_reentry_weight`` (``docs/research/fast_reentry_integration_v1.md``,
research ``docs/research/fast_reentry_v1.md``/``fast_reentry_v2.md``): while E2's
trend vote is off, hold that fraction of the production weight in the hold
symbol when the signal index's completed closes are above their 50-session
average, their 20-session annualized volatility is below that series'
252-session median and the close is above the close 20 sessions earlier; the
path ends below the 50-session average and E2's vote overrides it. Production
is stateless, so ``decide`` replays the state machine over the last 126
sessions. Each test pins one invariant: the absent parameter keeps the E2
(1.1.0) contract byte-identical, the parameter is validated, the minimum
history becomes 397 sessions, the replay equals a full-history reference on a
crash/recovery fixture, the weight is a fraction of the production weight, E2
wins, and the metadata round-trips through JSON.
"""

from __future__ import annotations

import json
import math
import random
import statistics
from datetime import UTC, datetime, timedelta
from functools import lru_cache

import pytest

from parayabuck.core.models import Bar, Market
from parayabuck.monitoring.daily_summary import build_equity_daily_summary
from parayabuck.portfolio import portfolio_strategy_from
from parayabuck.portfolio.base import PortfolioContext
from parayabuck.portfolio.equity_leveraged_trend import EquityLeveragedTrendV1

START = datetime(2024, 1, 1, 21, 0, tzinfo=UTC)
# The E2 vote, without the early path: SMA(200), 252-session momentum (needs
# 253 closes) and the 126-close channel.
E2_PARAMETERS = {"trend_rule": "majority_sma_tsm_don"}
EARLY = {**E2_PARAMETERS, "early_reentry_weight": 0.5}
E2_METADATA_KEYS = {
    "trend_state",
    "signal_symbol",
    "hold_symbol",
    "signal_close",
    "signal_sma",
    "sma_bars",
    "realized_volatility",
    "vol_target",
    "trend_rule",
    "trend_signals",
}

SESSIONS = 700
# The first decidable session: minimum_history_bars - 1 with the early path.
FIRST_DECIDABLE = 396


def _fixture_closes(sessions: int = SESSIONS) -> list[float]:
    """A deterministic crash/recovery path with early episodes, SMA50 exits and E2 takeovers."""
    rng = random.Random(20261014)
    closes = [100.0]
    for index in range(1, sessions):
        if index < 300:  # pre-crash uptrend
            drift, sigma = 0.0009, 0.004
        elif index < 340:  # crash
            drift, sigma = -0.012, 0.006
        elif index < 430:  # calm recovery below the channel midpoint (E2 still off)
            drift, sigma = 0.0025, 0.003
        elif index < 445:  # dip through SMA50 -> early exit
            drift, sigma = -0.005, 0.003
        elif index < 561:  # rally -> E2 takeover
            drift, sigma = 0.0035, 0.003
        elif index < 621:  # second decline -> E2 off again
            drift, sigma = -0.0055, 0.004
        else:  # calm recovery -> second early episode and takeover
            drift, sigma = 0.0025, 0.002
        closes.append(closes[-1] * (1.0 + drift + rng.gauss(0.0, sigma)))
    return closes


class _Reference:
    """The R2 state machine over the whole history, without any replay window."""

    def __init__(self, closes: list[float]) -> None:
        self.closes = closes
        self.vol20 = [self._vol20(index) for index in range(len(closes))]
        self.median252 = [self._median(index) for index in range(len(closes))]
        self.sma50_up = [self._sma_up(index, 50) for index in range(len(closes))]
        self.e2 = [self._e2(index) for index in range(len(closes))]
        self.states = self._states()

    def _vol20(self, index: int) -> float | None:
        if index < 20:
            return None
        returns = [self.closes[i] / self.closes[i - 1] - 1 for i in range(index - 19, index + 1)]
        return statistics.stdev(returns) * math.sqrt(252)

    def _median(self, index: int) -> float | None:
        if index < 251:
            return None
        window = self.vol20[index - 251 : index + 1]
        return None if any(value is None for value in window) else statistics.median(window)

    def _sma_up(self, index: int, bars: int) -> bool:
        return self.closes[index] > sum(self.closes[index - bars + 1 : index + 1]) / bars

    def _e2(self, index: int) -> bool | None:
        if index < 252:
            return None
        votes = (
            self._sma_up(index, 200),
            self.closes[index] > self.closes[index - 252],
            self.closes[index]
            > (max(self.closes[index - 125 : index + 1]) + min(self.closes[index - 125 : index + 1])) / 2,
        )
        return sum(1 for vote in votes if vote) >= 2

    def _states(self) -> list[bool]:
        active = False
        states: list[bool] = []
        for index in range(len(self.closes)):
            if self.e2[index]:
                active = False
            else:
                if active and not self.sma50_up[index]:
                    active = False
                if (
                    not active
                    and self.median252[index] is not None
                    and self.sma50_up[index]
                    and self.vol20[index] < self.median252[index]
                    and self.closes[index] > self.closes[index - 20]
                ):
                    active = True
            states.append(active)
        return states


@lru_cache(maxsize=1)
def _fixture() -> tuple[list[float], _Reference]:
    closes = _fixture_closes()
    return closes, _Reference(closes)


def _early_indexes() -> list[int]:
    return [index for index in range(FIRST_DECIDABLE, SESSIONS) if _fixture()[1].states[index]]


def _bars(symbol: str, closes: list[float]) -> tuple[Bar, ...]:
    return tuple(
        Bar(START + timedelta(days=index), symbol, "1d", close, close, close, close, 1_000_000, Market.EQUITY)
        for index, close in enumerate(closes)
    )


def _context(signal_closes: list[float], hold_closes: list[float] | None = None) -> PortfolioContext:
    hold_closes = hold_closes if hold_closes is not None else [50.0] * len(signal_closes)
    return PortfolioContext(
        decision_timestamp=START + timedelta(days=len(signal_closes) - 1),
        market=Market.EQUITY,
        timeframe="1d",
        history={"QQQ": _bars("QQQ", signal_closes), "QLD": _bars("QLD", hold_closes)},
        universe=None,
        universe_version="test",
        equity=100_000.0,
        holdings={},
        prices={},
        benchmark_symbol="SPY",
    )


def _strategy(**parameters: object) -> EquityLeveragedTrendV1:
    strategy = portfolio_strategy_from("equity_leveraged_trend_v1", {**EARLY, **parameters})
    assert isinstance(strategy, EquityLeveragedTrendV1)
    return strategy


# --------------------------------------------------------------------------- #
# The absent parameter keeps the E2 contract byte-identical
# --------------------------------------------------------------------------- #


def test_absent_parameter_is_the_e2_contract() -> None:
    plain = portfolio_strategy_from("equity_leveraged_trend_v1", E2_PARAMETERS)
    assert plain.early_reentry_weight is None
    assert plain.version == "1.1.0"
    assert plain.minimum_history_bars == 253
    assert "early_reentry_weight" not in dict(plain.parameter_tuple())
    target = plain.decide(_context([100.0 + index for index in range(253)]))
    assert target.strategy_version == "1.1.0"
    assert set(target.metadata) == E2_METADATA_KEYS

    early = _strategy()
    assert early.early_reentry_weight == 0.5
    assert early.version == "1.2.0"
    assert early.spec().version == "1.2.0"
    assert dict(early.parameter_tuple())["early_reentry_weight"] == 0.5


def test_absent_parameter_never_takes_the_early_path() -> None:
    closes = _fixture()[0]
    plain = portfolio_strategy_from("equity_leveraged_trend_v1", E2_PARAMETERS)
    states = {
        plain.decide(_context(closes[: index + 1])).metadata["trend_state"]
        for index in range(FIRST_DECIDABLE, SESSIONS, 16)
    }
    assert states == {"uptrend", "downtrend"}


# --------------------------------------------------------------------------- #
# Validation and minimum history
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("weight", [0.0, -0.5, 1.5, 2, True, False, "0.5", None, [0.5]])
def test_invalid_early_weights_are_rejected(weight: object) -> None:
    with pytest.raises(ValueError):
        portfolio_strategy_from("equity_leveraged_trend_v1", {**E2_PARAMETERS, "early_reentry_weight": weight})


@pytest.mark.parametrize("weight", [0.001, 0.5, 1, 1.0])
def test_the_early_weight_range_is_open_at_zero_and_closed_at_one(weight: object) -> None:
    strategy = portfolio_strategy_from("equity_leveraged_trend_v1", {**E2_PARAMETERS, "early_reentry_weight": weight})
    assert isinstance(strategy, EquityLeveragedTrendV1)
    assert strategy.early_reentry_weight == float(weight)  # type: ignore[arg-type]


def test_the_sma_rule_rejects_the_early_weight() -> None:
    with pytest.raises(ValueError, match="only apply to trend_rule"):
        portfolio_strategy_from("equity_leveraged_trend_v1", {"trend_rule": "sma", "early_reentry_weight": 0.5})


def test_minimum_history_bars_is_397_with_the_early_path() -> None:
    # u = t - 125 needs its 252-session vol median (defined from index 271), so
    # the first decidable session is index 396 = 397 completed closes.
    assert _strategy().minimum_history_bars == 397
    assert portfolio_strategy_from("equity_leveraged_trend_v1", E2_PARAMETERS).minimum_history_bars == 253
    assert _strategy(vol_target=0.0).minimum_history_bars == 397


def test_the_early_path_is_unavailable_on_a_shorter_history() -> None:
    # 396 closes: E2 can decide (its 253-session vote is available) but the
    # replay's first vol median is not, so the path stays inactive.
    closes = [100.0 - 0.1 * index for index in range(FIRST_DECIDABLE)]
    target = _strategy(vol_target=0.0).decide(_context(closes))
    assert target.metadata["trend_state"] == "downtrend"
    assert target.weights == ()
    early = target.metadata["early_reentry"]
    assert early["active"] is False
    assert early["sma50"] is None and early["vol20"] is None and early["vol20_median252"] is None
    assert early["momentum20"] is None
    assert early["replay_bars"] == 126 and early["weight"] == 0.5


def test_zero_base_close_reports_no_momentum_instead_of_dividing(monkeypatch) -> None:
    # A zero close 20 sessions back has no return to report, so momentum20 is
    # None and decide() must not divide by it.
    # Bar now refuses non-positive prices; this pins the strategy's own guard
    # (defence in depth for bars built elsewhere), so the model check is bypassed.
    monkeypatch.setattr(Bar, "__post_init__", lambda self: None)
    closes = _fixture_closes()
    closes[-21] = 0.0
    target = _strategy(vol_target=0.0).decide(_context(closes))
    assert target.metadata["early_reentry"]["momentum20"] is None


# --------------------------------------------------------------------------- #
# The 126-session replay equals a full-history reference
# --------------------------------------------------------------------------- #


def test_replay_matches_the_full_history_state_machine() -> None:
    closes, reference = _fixture()
    active = reference.states
    entering = [index for index in range(1, SESSIONS) if active[index] and not active[index - 1]]
    leaving = [index for index in range(1, SESSIONS) if active[index - 1] and not active[index]]
    takeovers = [index for index in leaving if reference.e2[index]]
    sma50_exits = [index for index in leaving if not reference.e2[index]]
    assert (len(entering), len(takeovers), len(sma50_exits)) == (3, 2, 1), (
        "the fixture must exercise entries, E2 takeovers and SMA50 exits"
    )

    strategy = _strategy()
    assert strategy.minimum_history_bars == FIRST_DECIDABLE + 1
    early_sessions = 0
    for index in range(FIRST_DECIDABLE, SESSIONS):
        context = _context(closes[: index + 1])
        target = strategy.decide(context)
        volatility = strategy.realized_volatility(context)
        production_weight = 0.0 if strategy.vol_target and volatility is None else 1.0
        if strategy.vol_target and volatility is not None:
            production_weight = 1.0 if volatility <= 0 else min(1.0, strategy.vol_target / volatility)
        if reference.e2[index]:
            expected_state, expected_weight = "uptrend", production_weight
        elif active[index]:
            expected_state, expected_weight = "early_reentry", production_weight * 0.5
        else:
            expected_state, expected_weight = "downtrend", 0.0
        assert target.metadata["trend_state"] == expected_state, index
        assert sum(item.weight for item in target.weights) == pytest.approx(expected_weight, abs=1e-12), index
        if expected_state == "early_reentry":
            early_sessions += 1
            assert target.weights[0].reason == "early_reentry"
            assert target.metadata["early_reentry"]["active"] is True
            assert target.rebalance_reason.value == "scheduled"
        elif expected_state == "uptrend":
            assert target.weights[0].reason == "trend_up"
            assert target.metadata["early_reentry"]["active"] is False
        else:
            assert target.weights == ()
            assert target.rebalance_reason.value == "risk_off"
    assert early_sessions == sum(1 for index in range(FIRST_DECIDABLE, SESSIONS) if active[index]) > 0


def test_early_episodes_are_shorter_than_the_replay_window() -> None:
    # The replay can only reproduce the full-history state while no episode
    # outlives the 126-session window it starts from.
    active = _fixture()[1].states
    run = 0
    longest = 0
    for state in active[FIRST_DECIDABLE:]:
        run = run + 1 if state else 0
        longest = max(longest, run)
    assert 0 < longest < 126


# --------------------------------------------------------------------------- #
# Sizing, precedence and metadata
# --------------------------------------------------------------------------- #


def test_early_weight_is_half_the_production_weight_and_the_cap_is_unchanged() -> None:
    closes, _ = _fixture()
    index = _early_indexes()[0]
    signals = closes[: index + 1]
    rng = random.Random(7)
    hold = [100.0]
    for _ in range(len(signals) - 1):
        hold.append(hold[-1] * (1.0 + rng.gauss(0.0, 0.04)))
    target = _strategy(vol_target=0.35).decide(_context(signals, hold))
    assert target.metadata["trend_state"] == "early_reentry"
    # The production weight is the 20-session realized volatility of the hold
    # symbol (``vol_window + 1`` closes), recomputed here independently.
    window = hold[-(20 + 1) :]
    returns = [window[position] / window[position - 1] - 1 for position in range(1, len(window))]
    production_weight = min(1.0, 0.35 / (statistics.stdev(returns) * math.sqrt(252)))
    assert 0 < production_weight < 1
    assert sum(item.weight for item in target.weights) == pytest.approx(production_weight * 0.5, rel=1e-12)

    # A calm hold symbol pins the production weight at its 1.0 cap, so the early
    # position is exactly the parameter (half the cap).
    calm = _strategy(vol_target=0.35).decide(
        _context(signals, [50.0 + 0.001 * (position % 5) for position in range(len(signals))])
    )
    assert calm.metadata["trend_state"] == "early_reentry"
    assert sum(item.weight for item in calm.weights) == pytest.approx(0.5)


def test_e2_on_overrides_the_early_path() -> None:
    closes, reference = _fixture()
    # The session right after an early episode that E2 ends: the replayed state
    # is reset by the vote, so the decision is the plain in-trend target.
    leaving = [
        index
        for index in range(FIRST_DECIDABLE, SESSIONS)
        if reference.states[index - 1] and not reference.states[index] and reference.e2[index]
    ]
    index = leaving[0]
    target = _strategy().decide(_context(closes[: index + 1]))
    assert target.metadata["trend_state"] == "uptrend"
    assert target.metadata["early_reentry"]["active"] is False
    assert target.weights[0].reason == "trend_up"


def test_e2_without_history_stays_insufficient_history() -> None:
    closes = _fixture()[0][:252]
    target = _strategy().decide(_context(closes))
    assert target.metadata["trend_state"] == "insufficient_history"
    assert target.weights == ()
    assert target.metadata["early_reentry"]["active"] is False


def test_daily_summary_shows_the_early_re_entry_state() -> None:
    closes, _ = _fixture()
    index = _early_indexes()[0]
    target = _strategy(vol_target=0.35).decide(_context(closes[: index + 1]))
    message, fields = build_equity_daily_summary(target, None, status="submitted", decision_id="d1")
    assert "early re-entry 0.5x" in message
    assert "early_reentry" not in message  # the phrase replaces the raw state token
    assert fields["early_reentry"] == "early re-entry 0.5x"
    assert fields["trend_state"] == "early_reentry"
    downtrend = _strategy(vol_target=0.35).decide(_context([100.0 - 0.1 * step for step in range(FIRST_DECIDABLE)]))
    message, fields = build_equity_daily_summary(downtrend, None, status="submitted", decision_id="d2")
    assert "early re-entry" not in message and "downtrend" in message
    assert fields["early_reentry"] is None


def test_metadata_round_trips_through_json() -> None:
    closes, _ = _fixture()
    index = _early_indexes()[0]
    target = _strategy().decide(_context(closes[: index + 1]))
    early = target.metadata["early_reentry"]
    assert set(early) == {
        "weight",
        "active",
        "sma50",
        "above_sma50",
        "vol20",
        "vol20_median252",
        "momentum20",
        "replay_bars",
    }
    assert early["active"] is True
    assert early["above_sma50"] is True
    assert early["vol20"] == pytest.approx(
        statistics.stdev([closes[i] / closes[i - 1] - 1 for i in range(index - 19, index + 1)]) * math.sqrt(252)
    )
    assert early["vol20_median252"] == pytest.approx(
        statistics.median(
            [
                statistics.stdev([closes[i] / closes[i - 1] - 1 for i in range(step - 19, step + 1)]) * math.sqrt(252)
                for step in range(index - 251, index + 1)
            ]
        )
    )
    assert early["momentum20"] == pytest.approx(closes[index] / closes[index - 20] - 1)
    restored = json.loads(json.dumps(target.to_dict()))
    assert restored["metadata"]["early_reentry"] == early
    assert restored["parameters"]["early_reentry_weight"] == 0.5
