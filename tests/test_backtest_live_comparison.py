"""Backtest-vs-live comparison report on synthetic VT35 sessions (read-only DB)."""

from __future__ import annotations

import importlib.util
import json
import math
import sqlite3
import statistics
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

import pytest

from parayabuck.config import load_config
from parayabuck.core.models import Bar, Market
from parayabuck.execution.portfolio import PortfolioRiskLimits, PortfolioRiskOverlay, portfolio_risk_limits_from_config
from parayabuck.markets.timeframes import NEW_YORK
from parayabuck.portfolio.equity_leveraged_trend import EquityLeveragedTrendV1

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("backtest_live_comparison", ROOT / "scripts/backtest_live_comparison.py")
assert spec and spec.loader
report = importlib.util.module_from_spec(spec)
spec.loader.exec_module(report)

ALLOCATION = 0.98
BAND = 0.005


def _sessions(count: int) -> list[date]:
    day, out = date(2025, 1, 2), []
    while len(out) < count:
        if day.weekday() < 5:
            out.append(day)
        day += timedelta(days=1)
    return out


def _at(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime.combine(day, time(hour, minute), tzinfo=NEW_YORK).astimezone(UTC)


def _bars(sessions: list[date], move: float = 0.03) -> dict[str, list[Bar]]:
    """Provider-style bars labeled 00:00 ET: QQQ rising (uptrend), QLD alternating +/-``move``."""
    qqq, qld = [], []
    price = 50.0
    for index, day in enumerate(sessions):
        label = _at(day, 0)
        close = 100.0 + index * 0.1
        qqq.append(Bar(label, "QQQ", "1d", close, close, close, close, 1e6, Market.EQUITY))
        opened = price
        price *= 1 + move if index % 2 == 0 else 1 - move
        qld.append(Bar(label, "QLD", "1d", opened, max(opened, price), min(opened, price), price, 1e6, Market.EQUITY))
    return {"QQQ": qqq, "QLD": qld}


def _strategy_weight(bars: dict[str, list[Bar]], index: int) -> float:
    closes = [bar.close for bar in bars["QLD"][index - 20 : index + 1]]
    returns = [closes[k] / closes[k - 1] - 1 for k in range(1, len(closes))]
    return min(1.0, 0.35 / (statistics.stdev(returns) * math.sqrt(252)))


def _expected_weight(bars: dict[str, list[Bar]], index: int) -> float:
    """Production sizing: the risk overlay CAPS the strategy weight, it never multiplies it."""
    return min(_strategy_weight(bars, index), ALLOCATION)


def _limits() -> PortfolioRiskLimits:
    config = load_config(str(ROOT / "config" / "settings.yaml"))
    assert config.strategy.equity.maximum_allocation == ALLOCATION
    return portfolio_risk_limits_from_config(config.risk)


def _db(
    tmp_path: Path, decisions: list[tuple[str, datetime, dict]], fills: list[tuple[str, str, datetime, float, float]]
) -> Path:
    path = tmp_path / "runtime.sqlite3"
    with sqlite3.connect(path) as con:
        con.executescript(
            """
            CREATE TABLE portfolio_decisions(decision_id TEXT, decision_timestamp TEXT, market TEXT,
                strategy_name TEXT, target_json TEXT);
            CREATE TABLE portfolio_orders(client_order_id TEXT, decision_id TEXT, symbol TEXT, side TEXT,
                quantity REAL, reference_price REAL);
            CREATE TABLE portfolio_fills(activity_id TEXT, client_order_id TEXT, occurred_at TEXT,
                quantity REAL, price REAL);
            """
        )
        for decision_id, stamp, target in decisions:
            con.execute(
                "INSERT INTO portfolio_decisions VALUES (?, ?, 'equity', 'equity_leveraged_trend_v1', ?)",
                (decision_id, stamp.isoformat(), json.dumps(target)),
            )
        for number, (decision_id, side, occurred, quantity, price) in enumerate(fills):
            order = f"order-{number}"
            con.execute(
                "INSERT INTO portfolio_orders VALUES (?, ?, 'QLD', ?, ?, 0)", (order, decision_id, side, quantity)
            )
            con.execute(
                "INSERT INTO portfolio_fills VALUES (?, ?, ?, ?, ?)",
                (f"a{number}", order, occurred.isoformat(), quantity, price),
            )
    return path


def _target(weight: float, state: str, **extra: object) -> dict:
    return {
        "weights": [{"symbol": "QLD", "weight": weight}] if weight else [],
        "metadata": {"trend_state": state, **extra},
    }


@pytest.fixture
def fixture(tmp_path: Path):  # type: ignore[no-untyped-def]
    sessions = _sessions(215)
    bars = _bars(sessions)
    return tmp_path, sessions, bars


def test_matching_session_has_no_flags_and_measures_fill_cost_against_next_open(fixture) -> None:  # type: ignore[no-untyped-def]
    tmp_path, sessions, bars = fixture
    index = 205
    weight = _expected_weight(bars, index)
    next_open = bars["QLD"][index + 1].open
    db = _db(
        tmp_path,
        [("d1", _at(sessions[index], 9, 30), _target(weight, "uptrend"))],
        [("d1", "buy", _at(sessions[index + 1], 9, 30), 10.0, next_open * 1.0005)],
    )
    result = report.compare(
        report.load_live(db), bars, EquityLeveragedTrendV1({"vol_target": 0.35}), _limits(), ALLOCATION, BAND
    )
    row = result["sessions"][0]
    assert row["session"] == sessions[index].isoformat()
    assert row["flags"] == []
    assert row["reference"]["target_weight"] == pytest.approx(weight)
    assert row["reference"]["fill_session"] == sessions[index + 1].isoformat()
    assert row["fill"]["cost_vs_open_bps"] == pytest.approx(5.0)
    assert result["sessions_without_live_decision"] == []


def test_mismatch_latch_late_fill_and_missed_sessions_are_flagged(fixture) -> None:  # type: ignore[no-untyped-def]
    tmp_path, sessions, bars = fixture
    db = _db(
        tmp_path,
        [
            (
                "d1",
                _at(sessions[205], 9, 30),
                _target(0.0, "downtrend", entry_risk_latch="signal_adjustment_unverified"),
            ),
            ("d2", _at(sessions[208], 9, 30), _target(0.5, "uptrend")),
        ],
        [("d2", "buy", _at(sessions[210], 10, 0), 5.0, bars["QLD"][210].open)],
    )
    result = report.compare(
        report.load_live(db), bars, EquityLeveragedTrendV1({"vol_target": 0.35}), _limits(), ALLOCATION, BAND
    )
    first, second = result["sessions"]
    assert first["flags"] == ["entry_latched", "trend_state_mismatch", "target_weight_gap"]
    assert "fill_not_next_session" in second["flags"]
    assert result["sessions_without_live_decision"] == [sessions[206].isoformat(), sessions[207].isoformat()]
    assert result["flag_counts"]["target_weight_gap"] == 2


def test_session_beyond_reference_bars_is_reported_not_guessed(fixture) -> None:  # type: ignore[no-untyped-def]
    tmp_path, sessions, bars = fixture
    beyond = sessions[-1] + timedelta(days=3)
    db = _db(tmp_path, [("d1", _at(beyond, 9, 30), _target(0.5, "uptrend"))], [])
    result = report.compare(
        report.load_live(db), bars, EquityLeveragedTrendV1({"vol_target": 0.35}), _limits(), ALLOCATION, BAND
    )
    assert result["sessions"][0]["flags"] == ["missing_reference_bar"]
    assert "weight_gap" not in result["sessions"][0]


def test_vol_scaled_weight_below_allocation_is_never_multiplied(fixture) -> None:  # type: ignore[no-untyped-def]
    """The overlay CAPS the strategy weight at the allocation; it never multiplies by it."""
    tmp_path, sessions, bars = fixture
    index = 205
    strategy = EquityLeveragedTrendV1({"vol_target": 0.35})
    overlay = PortfolioRiskOverlay(_limits())
    vol_scaled = report.reference_decision(strategy, bars, sessions[index], overlay, ALLOCATION)["strategy_weight"]
    assert 0.0 < vol_scaled < ALLOCATION
    db = _db(tmp_path, [("d1", _at(sessions[index], 9, 30), _target(vol_scaled, "uptrend"))], [])
    result = report.compare(report.load_live(db), bars, strategy, _limits(), ALLOCATION, BAND)
    row = result["sessions"][0]
    assert row["reference"]["target_weight"] == pytest.approx(vol_scaled)
    assert row["reference"]["target_weight"] != pytest.approx(vol_scaled * ALLOCATION)
    assert "target_weight_gap" not in row["flags"]


def test_full_strength_weight_is_capped_at_the_allocation(fixture) -> None:  # type: ignore[no-untyped-def]
    """A calm uptrend emits weight 1.0; the overlay caps it at maximum_allocation (0.98)."""
    tmp_path, sessions, _ = fixture
    bars = _bars(sessions, move=0.0005)
    index = 205
    strategy = EquityLeveragedTrendV1({"vol_target": 0.35})
    overlay = PortfolioRiskOverlay(_limits())
    ref = report.reference_decision(strategy, bars, sessions[index], overlay, ALLOCATION)
    assert ref["strategy_weight"] == pytest.approx(1.0)
    db = _db(tmp_path, [("d1", _at(sessions[index], 9, 30), _target(ALLOCATION, "uptrend"))], [])
    result = report.compare(report.load_live(db), bars, strategy, _limits(), ALLOCATION, BAND)
    row = result["sessions"][0]
    assert row["reference"]["target_weight"] == pytest.approx(ALLOCATION)
    assert "target_weight_gap" not in row["flags"]
