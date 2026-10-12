"""Contract tests for the E2 trend vote of ``equity_leveraged_trend_v1``.

``trend_rule: majority_sma_tsm_don`` (docs/research/e2_integration_v1.md):
in trend when at least two of SMA (close > SMA), TSM (``tsm_bars``-session
price return > 0) and DON (close > midpoint of the last ``donchian_bars``
closes) hold. Each test pins one invariant on small synthetic series; the
SMA rule (VT35) stays the class default and is pinned for parity.
"""

from __future__ import annotations

import importlib.util
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import yaml

from parayabuck.config import load_config
from parayabuck.core.models import Bar, Market
from parayabuck.monitoring.daily_summary import build_equity_daily_summary
from parayabuck.portfolio import portfolio_strategy_from
from parayabuck.portfolio.base import PortfolioContext
from parayabuck.portfolio.equity_leveraged_trend import EquityLeveragedTrendV1
from parayabuck.portfolio.signal_cross_check import (
    STATUS_TREND_MISMATCH,
    compare_sources,
    cross_check_symbols,
)

ROOT = Path(__file__).resolve().parents[1]
START = datetime(2024, 1, 1, 21, 0, tzinfo=UTC)
# Small windows: SMA(3), 3-session momentum (needs 4 closes), 4-close channel.
SMALL = {"trend_rule": "majority_sma_tsm_don", "sma_bars": 3, "tsm_bars": 3, "donchian_bars": 4}


def _bars(symbol: str, closes: list[float]) -> tuple[Bar, ...]:
    return tuple(
        Bar(START + timedelta(days=index), symbol, "1d", close, close, close, close, 1_000_000, Market.EQUITY)
        for index, close in enumerate(closes)
    )


class _Universe:
    def __init__(self, excluded: set[str] | None = None) -> None:
        self.excluded = excluded or set()

    def contains(self, symbol: str, market: Market, timestamp: datetime) -> bool:
        return symbol not in self.excluded


def _context(qqq: list[float], qld: list[float] | None = None, universe: _Universe | None = None) -> PortfolioContext:
    qld = qld if qld is not None else [50.0] * len(qqq)
    return PortfolioContext(
        decision_timestamp=START + timedelta(days=len(qqq) - 1),
        market=Market.EQUITY,
        timeframe="1d",
        history={"QQQ": _bars("QQQ", qqq), "QLD": _bars("QLD", qld)},
        universe=universe or _Universe(),
        universe_version="test",
        equity=100_000.0,
        holdings={},
        prices={},
        benchmark_symbol="SPY",
    )


def _e2(**parameters: object) -> EquityLeveragedTrendV1:
    strategy = portfolio_strategy_from("equity_leveraged_trend_v1", {**SMALL, **parameters})
    assert isinstance(strategy, EquityLeveragedTrendV1)
    return strategy


def _votes(target) -> tuple[object, object, object, object]:  # type: ignore[no-untyped-def]
    signals = target.metadata["trend_signals"]
    return signals["sma"]["up"], signals["tsm"]["up"], signals["don"]["up"], signals["votes_up"]


# --------------------------------------------------------------------------- #
# Individual votes
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("closes", "expected"),
    [
        ([100, 50, 50, 101], True),  # 3-session return +1%
        ([100, 150, 150, 100], False),  # exactly zero is not above the hurdle
        ([100, 150, 150, 99], False),
    ],
)
def test_tsm_vote_is_the_tsm_bars_price_return_above_zero(closes: list[float], expected: bool) -> None:
    up, momentum = _e2().tsm_vote([float(value) for value in closes])
    assert up is expected
    assert momentum == pytest.approx(closes[-1] / closes[0] - 1)


def test_tsm_vote_reads_the_close_exactly_tsm_bars_sessions_back() -> None:
    # The oldest close (10) is outside a 3-session lookback; the base is 200.
    up, momentum = _e2().tsm_vote([10.0, 200.0, 1.0, 1.0, 150.0])
    assert up is False
    assert momentum == pytest.approx(150 / 200 - 1)


def test_tsm_vote_is_unknown_on_a_non_positive_base_close() -> None:
    assert _e2().tsm_vote([0.0, 1.0, 1.0, 2.0]) == (None, None)


@pytest.mark.parametrize(
    ("closes", "expected"),
    [
        ([80, 100, 90, 95], True),  # midpoint 90
        ([80, 100, 90, 90], False),  # at the midpoint is not above it
        ([80, 100, 90, 85], False),
    ],
)
def test_don_vote_is_close_above_the_channel_midpoint(closes: list[float], expected: bool) -> None:
    up, midpoint = _e2().donchian_vote([float(value) for value in closes])
    assert up is expected
    assert midpoint == pytest.approx(90.0)


def test_don_channel_includes_the_current_close_and_ignores_older_extremes() -> None:
    # 1000 is outside the 4-close window; a new high is its own channel top.
    up, midpoint = _e2().donchian_vote([1000.0, 10.0, 12.0, 14.0, 20.0])
    assert midpoint == pytest.approx((20.0 + 10.0) / 2)
    assert up is True


# --------------------------------------------------------------------------- #
# The 2-of-3 vote
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("qqq", "votes", "state"),
    [
        ([100, 101, 102, 103], (True, True, True, 3), "uptrend"),
        ([100, 90, 80, 95], (True, False, True, 2), "uptrend"),  # TSM outvoted
        ([90, 100, 120, 106], (False, True, True, 2), "uptrend"),  # SMA outvoted
        ([100, 110, 120, 105], (False, True, False, 1), "downtrend"),
        ([103, 102, 101, 100], (False, False, False, 0), "downtrend"),
    ],
)
def test_two_of_three_votes_decide_the_trend(qqq: list[float], votes: tuple, state: str) -> None:
    target = _e2().decide(_context([float(value) for value in qqq]))
    assert _votes(target) == votes
    assert target.metadata["trend_state"] == state
    if state == "uptrend":
        assert [(weight.symbol, weight.weight) for weight in target.weights] == [("QLD", 1.0)]
    else:
        assert target.weights == ()
        assert target.cash_weight == 1.0


def test_sizing_is_the_vt35_vol_target_weight() -> None:
    qld = [100.0 * (1.02 if index % 2 else 0.98) ** index for index in range(25)]
    qqq = [100.0 + index for index in range(25)]
    e2 = _e2(vol_target=0.35).decide(_context(qqq, qld))
    vt35 = portfolio_strategy_from("equity_leveraged_trend_v1", {"sma_bars": 3, "vol_target": 0.35}).decide(
        _context(qqq, qld)
    )
    assert e2.metadata["trend_state"] == vt35.metadata["trend_state"] == "uptrend"
    assert 0 < e2.weights[0].weight < 1
    assert e2.weights[0].weight == pytest.approx(vt35.weights[0].weight)


# --------------------------------------------------------------------------- #
# History edge cases
# --------------------------------------------------------------------------- #


def test_short_momentum_history_is_insufficient_never_a_two_of_two_vote() -> None:
    # 3 closes: SMA(3) and a 2-close channel both vote "up", but the
    # 3-session momentum needs 4 closes. Two known votes are not a majority.
    strategy = _e2(donchian_bars=2)
    target = strategy.decide(_context([100.0, 101.0, 102.0]))
    signals = target.metadata["trend_signals"]
    assert signals["sma"]["up"] is True and signals["don"]["up"] is True
    assert signals["tsm"]["up"] is None
    assert signals["votes_up"] is None
    assert target.metadata["trend_state"] == "insufficient_history"
    assert target.weights == ()


def test_short_channel_history_is_insufficient() -> None:
    strategy = _e2(tsm_bars=2, donchian_bars=5)
    target = strategy.decide(_context([100.0, 101.0, 102.0, 103.0]))
    assert target.metadata["trend_signals"]["don"]["up"] is None
    assert target.metadata["trend_state"] == "insufficient_history"
    assert target.weights == ()


def test_exactly_minimum_history_bars_decides() -> None:
    strategy = _e2()
    assert strategy.minimum_history_bars == 4
    target = strategy.decide(_context([100.0, 101.0, 102.0, 103.0]))
    assert target.metadata["trend_state"] == "uptrend"


def test_shipped_windows_need_253_sessions() -> None:
    strategy = portfolio_strategy_from("equity_leveraged_trend_v1", {"trend_rule": "majority_sma_tsm_don"})
    assert (strategy.sma_bars, strategy.tsm_bars, strategy.donchian_bars) == (200, 252, 126)
    assert strategy.minimum_history_bars == 253
    rising = [100.0 + index for index in range(253)]
    assert strategy.decide(_context(rising)).metadata["trend_state"] == "uptrend"
    assert strategy.decide(_context(rising[1:])).metadata["trend_state"] == "insufficient_history"


def test_degenerate_momentum_base_is_insufficient_not_long() -> None:
    target = _e2().decide(_context([0.0, 101.0, 102.0, 103.0]))
    assert target.metadata["trend_state"] == "insufficient_history"
    assert target.weights == ()


def test_unavailable_hold_etf_targets_cash_under_the_vote() -> None:
    target = _e2().decide(_context([100.0, 101.0, 102.0, 103.0], universe=_Universe({"QLD"})))
    assert target.metadata["trend_state"] == "hold_symbol_unavailable"
    assert target.weights == ()


# --------------------------------------------------------------------------- #
# Identity, metadata and VT35 parity
# --------------------------------------------------------------------------- #


def test_metadata_records_every_vote_with_its_inputs() -> None:
    target = _e2().decide(_context([100.0, 90.0, 80.0, 95.0]))
    meta = target.metadata
    assert meta["trend_rule"] == "majority_sma_tsm_don"
    assert meta["signal_close"] == 95.0
    assert meta["signal_sma"] == pytest.approx((90 + 80 + 95) / 3)
    assert meta["trend_signals"] == {
        "sma": {"up": True, "close": 95.0, "sma": pytest.approx((90 + 80 + 95) / 3), "bars": 3},
        "tsm": {"up": False, "return": pytest.approx(-0.05), "hurdle": 0.0, "bars": 3},
        "don": {"up": True, "close": 95.0, "midpoint": pytest.approx(90.0), "bars": 4},
        "votes_up": 2,
    }
    json.dumps(target.to_dict())  # persisted decision targets stay JSON


def test_the_vote_reports_its_own_strategy_version() -> None:
    e2 = _e2()
    vt35 = portfolio_strategy_from("equity_leveraged_trend_v1", {"sma_bars": 3})
    assert vt35.version == "1.0.0" == EquityLeveragedTrendV1.version
    assert e2.version == "1.1.0"
    assert e2.spec().version == "1.1.0"
    assert e2.decide(_context([100.0, 101.0, 102.0, 103.0])).strategy_version == "1.1.0"


def test_default_rule_is_vt35_with_unchanged_parameters_and_metadata() -> None:
    strategy = portfolio_strategy_from("equity_leveraged_trend_v1", {"sma_bars": 3})
    assert strategy.trend_rule == "sma"
    assert dict(strategy.parameter_tuple()) == {
        "signal_symbol": "QQQ",
        "hold_symbol": "QLD",
        "sma_bars": 3,
        "vol_target": 0.0,
        "vol_window": 20,
        "rebalance_frequency": "hours:24",
    }
    target = strategy.decide(_context([100.0, 90.0, 80.0, 95.0]))
    assert set(target.metadata) == {
        "trend_state",
        "signal_symbol",
        "hold_symbol",
        "signal_close",
        "signal_sma",
        "sma_bars",
        "realized_volatility",
        "vol_target",
    }
    assert strategy.minimum_history_bars == 3


@pytest.mark.parametrize("explicit", [False, True])
def test_sma_rule_decides_exactly_like_vt35_on_a_long_series(explicit: bool) -> None:
    # The SMA verdict on every prefix of a zig-zag series equals close > SMA.
    closes = [100.0 + 10.0 * ((index * 7) % 11 - 5) + index for index in range(60)]
    parameters: dict[str, object] = {"sma_bars": 5}
    if explicit:
        parameters["trend_rule"] = "sma"
    strategy = portfolio_strategy_from("equity_leveraged_trend_v1", parameters)
    for end in range(5, len(closes) + 1):
        window = closes[:end]
        expected = window[-1] > sum(window[-5:]) / 5
        state = strategy.decide(_context(window)).metadata["trend_state"]
        assert state == ("uptrend" if expected else "downtrend")


# --------------------------------------------------------------------------- #
# Parameter validation
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "parameters",
    [
        {"trend_rule": "vote"},
        {"trend_rule": ""},
        {"tsm_bars": 252},  # majority-only key under the default SMA rule
        {"trend_rule": "sma", "donchian_bars": 126},
        {"trend_rule": "majority_sma_tsm_don", "sma_bars": 0},
        {"trend_rule": "majority_sma_tsm_don", "tsm_bars": 1},
        {"trend_rule": "majority_sma_tsm_don", "tsm_bars": 1001},
        {"trend_rule": "majority_sma_tsm_don", "tsm_bars": 252.0},
        {"trend_rule": "majority_sma_tsm_don", "tsm_bars": True},
        {"trend_rule": "majority_sma_tsm_don", "donchian_bars": 1},
        {"trend_rule": "majority_sma_tsm_don", "donchian_bars": 1001},
        {"trend_rule": "majority_sma_tsm_don", "donchian_bars": "126"},
    ],
)
def test_invalid_vote_parameters_are_rejected(parameters: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        portfolio_strategy_from("equity_leveraged_trend_v1", parameters)


def _write_config(tmp_path: Path, parameters: dict[str, object] | None) -> Path:
    raw = yaml.safe_load((ROOT / "config" / "settings.yaml").read_text())
    if parameters is None:
        raw["strategy"]["equity"].pop("parameters")
    else:
        raw["strategy"]["equity"]["parameters"] = parameters
    path = tmp_path / "settings.yaml"
    path.write_text(yaml.safe_dump(raw))
    return path


def test_shipped_config_selects_the_vote() -> None:
    selection = load_config(str(ROOT / "config" / "settings.yaml")).strategy.equity
    strategy = portfolio_strategy_from(selection.name, selection.parameters)
    assert strategy.trend_rule == "majority_sma_tsm_don"
    assert (strategy.tsm_bars, strategy.donchian_bars) == (252, 126)
    assert strategy.minimum_history_bars == 253
    assert strategy.version == "1.1.0"


def test_config_with_the_sma_rule_restores_vt35(tmp_path: Path) -> None:
    path = _write_config(
        tmp_path,
        {"signal_symbol": "QQQ", "hold_symbol": "QLD", "sma_bars": 200, "vol_target": 0.35, "trend_rule": "sma"},
    )
    selection = load_config(str(path)).strategy.equity
    assert "tsm_bars" not in selection.parameters and "donchian_bars" not in selection.parameters
    strategy = portfolio_strategy_from(selection.name, selection.parameters)
    assert (strategy.trend_rule, strategy.version, strategy.minimum_history_bars) == ("sma", "1.0.0", 200)


def test_config_without_parameters_keeps_the_shipped_vote(tmp_path: Path) -> None:
    selection = load_config(str(_write_config(tmp_path, None))).strategy.equity
    strategy = portfolio_strategy_from(selection.name, selection.parameters)
    assert strategy.trend_rule == "majority_sma_tsm_don"
    assert strategy.minimum_history_bars == 253


def test_config_rejects_a_majority_key_under_the_sma_rule(tmp_path: Path) -> None:
    path = _write_config(tmp_path, {"trend_rule": "sma", "tsm_bars": 252})
    with pytest.raises(ValueError, match="only apply to trend_rule"):
        load_config(str(path))


# --------------------------------------------------------------------------- #
# Only the hold ETF is ever traded; the cross-check covers the vote
# --------------------------------------------------------------------------- #


def test_the_vote_reads_no_extra_symbol_and_only_weights_the_hold_etf() -> None:
    strategy = _e2()
    assert strategy.decision_symbols == ("QQQ", "QLD")
    assert cross_check_symbols(strategy) == ("QQQ", "QLD")
    series = [100.0, 101.0, 99.0, 103.0, 97.0, 104.0, 90.0, 110.0, 80.0, 120.0]
    for end in range(1, len(series) + 1):
        target = strategy.decide(_context(series[:end]))
        assert {weight.symbol for weight in target.weights} <= {"QLD"}


def test_cross_check_holds_when_the_sources_disagree_only_on_the_vote() -> None:
    strategy = _e2()
    history = {"QQQ": _bars("QQQ", [100.0, 90.0, 80.0]), "QLD": _bars("QLD", [50.0, 50.0, 50.0])}
    session = START + timedelta(days=3)

    def bars(close: float) -> dict[str, Bar]:
        return {
            "QQQ": Bar(session, "QQQ", "1d", close, close, close, close, 1e6, Market.EQUITY),
            "QLD": Bar(session, "QLD", "1d", 50.0, 50.0, 50.0, 50.0, 1e6, Market.EQUITY),
        }

    # IEX 95: SMA up, TSM down, DON up (midpoint 90). SIP 89: DON flips down.
    outcome = compare_sources(strategy, history, bars(95.0), bars(89.0), session, close_divergence_bps=1e9)
    assert outcome.status == STATUS_TREND_MISMATCH
    assert (outcome.primary.trend_state, outcome.secondary.trend_state) == ("uptrend", "downtrend")
    assert outcome.hold


# --------------------------------------------------------------------------- #
# Backtest-vs-live comparison re-decides with the recorded rule
# --------------------------------------------------------------------------- #


def _comparison_module():  # type: ignore[no-untyped-def]
    path = ROOT / "scripts" / "backtest_live_comparison.py"
    spec = importlib.util.spec_from_file_location("backtest_live_comparison", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_comparison_uses_each_decisions_recorded_rule() -> None:
    module = _comparison_module()
    configured = _e2()
    vt35_target = {"parameters": {"sma_bars": 3, "vol_target": 0.0}}
    assert module.decision_strategy(vt35_target, configured).trend_rule == "sma"
    assert module.decision_strategy({"parameters": dict(configured.parameters)}, configured) is configured
    assert module.decision_strategy({}, configured) is configured
    assert module.decision_strategy({"parameters": {"bogus": 1}}, configured) is configured


# --------------------------------------------------------------------------- #
# Session-close daily summary shows the votes
# --------------------------------------------------------------------------- #


def test_daily_summary_shows_the_votes_only_for_the_vote_rule() -> None:
    e2_target = _e2().decide(_context([100.0, 90.0, 80.0, 95.0]))
    message, fields = build_equity_daily_summary(e2_target, None, status="submitted", decision_id="d1")
    assert "votes 2/3 (SMA+ TSM- DON+) uptrend" in message
    assert fields["trend_votes"] == "2/3 (SMA+ TSM- DON+)"
    vt35 = portfolio_strategy_from("equity_leveraged_trend_v1", {"sma_bars": 3})
    message, fields = build_equity_daily_summary(
        vt35.decide(_context([100.0, 90.0, 80.0, 95.0])), None, status="submitted", decision_id="d1"
    )
    assert "votes" not in message
    assert fields["trend_votes"] is None
