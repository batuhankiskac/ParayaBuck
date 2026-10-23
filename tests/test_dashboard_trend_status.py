"""Trend status card: vote thresholds computed from persisted decision metadata.

The card is display-only. These tests pin the pure threshold math (uptrend,
downtrend, ties, missing/old metadata) and the overview payload key, using
synthetic metadata and a stub manager under ``tmp_path`` — no ledger, network
or runtime.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from parayabuck.web.services.trend_status import trend_status_from_metadata

# The 2026-10-21 production decision: all three votes up, close 725.25.
_METADATA: dict[str, Any] = {
    "trend_state": "uptrend",
    "trend_rule": "majority_sma_tsm_don",
    "signal_symbol": "QQQ",
    "signal_close": 725.25,
    "signal_sma": 676.488,
    "realized_volatility": 0.316,
    "vol_target": 0.35,
    "trend_signals": {
        "sma": {"bars": 200, "close": 725.25, "sma": 676.488, "up": True},
        "tsm": {"bars": 252, "hurdle": 0.0, "return": 0.18594, "up": True},
        "don": {"bars": 126, "close": 725.25, "midpoint": 705.54, "up": True},
        "votes_up": 3,
    },
}


def _metadata(**overrides: Any) -> dict[str, Any]:
    metadata = dict(_METADATA)
    metadata.update(overrides)
    return metadata


# --------------------------------------------------------------------------- #
# Threshold math
# --------------------------------------------------------------------------- #


def test_uptrend_exit_is_the_second_highest_threshold() -> None:
    status = trend_status_from_metadata(_metadata())

    assert status["available"] is True
    assert status["in_trend"] is True
    assert status["votes_up"] == 3
    assert status["votes_total"] == 3
    assert status["thresholds"] == pytest.approx(
        {"sma": 676.488, "tsm": 725.25 / 1.18594, "don": 705.54}
    )
    # Sorted [TSM base, SMA, DON]: the middle level is where the second vote flips.
    assert status["exit_price"] == pytest.approx(676.488)
    assert status["exit_distance_pct"] == pytest.approx(-6.724, abs=0.01)
    assert status["reentry_price"] == pytest.approx(676.488)
    assert status["volatility"] == pytest.approx(0.316)
    assert status["vol_trim"] is False


def test_downtrend_reentry_is_the_second_lowest_threshold() -> None:
    metadata = _metadata(
        trend_state="downtrend",
        signal_close=660.0,
        trend_signals={
            "sma": {"bars": 200, "close": 660.0, "sma": 676.488, "up": False},
            "tsm": {"bars": 252, "hurdle": 0.0, "return": 0.18594, "up": True},
            "don": {"bars": 126, "close": 660.0, "midpoint": 705.54, "up": False},
            "votes_up": 1,
        },
    )

    status = trend_status_from_metadata(metadata)

    assert status["in_trend"] is False
    assert status["votes_up"] == 1
    assert status["reentry_price"] == pytest.approx(676.488)
    assert status["reentry_distance_pct"] == pytest.approx(2.498, abs=0.01)


def test_tied_thresholds_still_flip_a_majority() -> None:
    close = 710.0
    metadata = _metadata(
        signal_close=close,
        trend_signals={
            "sma": {"bars": 200, "close": close, "sma": 700.0, "up": True},
            "tsm": {"bars": 252, "hurdle": 0.0, "return": close / 700.0 - 1.0, "up": True},
            "don": {"bars": 126, "close": close, "midpoint": 700.0, "up": True},
            "votes_up": 3,
        },
    )

    status = trend_status_from_metadata(metadata)

    assert status["thresholds"] == pytest.approx({"sma": 700.0, "tsm": 700.0, "don": 700.0})
    assert status["exit_price"] == pytest.approx(700.0)
    assert status["exit_distance_pct"] == pytest.approx(-1.408, abs=0.001)


def test_missing_metadata_is_none_safe() -> None:
    for metadata in (None, {}):
        status = trend_status_from_metadata(metadata)

        assert status["available"] is False
        assert status["in_trend"] is None
        assert status["signal_close"] is None
        assert status["thresholds"] is None
        assert status["exit_price"] is None
        assert status["reentry_price"] is None
        assert status["vol_trim"] is None
        assert status["drawdown_pct"] is None


def test_old_sma_only_decision_uses_the_single_sma_threshold() -> None:
    metadata = {
        "trend_state": "uptrend",
        "signal_symbol": "QQQ",
        "signal_close": 100.0,
        "signal_sma": 90.0,
        "sma_bars": 200,
    }

    status = trend_status_from_metadata(metadata)

    assert status["available"] is True
    assert status["rule"] is None
    assert status["votes_up"] == 1
    assert status["votes_total"] == 1
    assert status["thresholds"] == pytest.approx({"sma": 90.0})
    assert status["exit_price"] == pytest.approx(90.0)
    assert status["reentry_price"] == pytest.approx(90.0)
    assert status["exit_distance_pct"] == pytest.approx(-10.0)


def test_non_numeric_metadata_degrades_to_no_levels() -> None:
    metadata = _metadata(signal_close="725.25", signal_sma=None, trend_signals={})

    status = trend_status_from_metadata(metadata)

    assert status["available"] is True  # the state is still known
    assert status["signal_close"] is None
    assert status["thresholds"] is None
    assert status["exit_price"] is None


# --------------------------------------------------------------------------- #
# Volatility and early re-entry
# --------------------------------------------------------------------------- #


def test_volatility_above_target_marks_trimming() -> None:
    trimmed = trend_status_from_metadata(_metadata(realized_volatility=0.40))
    quiet = trend_status_from_metadata(_metadata(realized_volatility=0.30))

    assert trimmed["vol_trim"] is True
    assert trimmed["vol_trim_at"] == pytest.approx(0.35)
    assert quiet["vol_trim"] is False
    assert quiet["vol_trim_at"] == pytest.approx(0.35)


def test_vol_target_zero_is_volatility_targeting_off() -> None:
    status = trend_status_from_metadata(_metadata(vol_target=0.0))

    assert status["vol_trim"] is None
    assert status["vol_trim_at"] is None


def test_early_reentry_is_not_a_full_trend() -> None:
    metadata = _metadata(
        trend_state="early_reentry",
        early_reentry={"active": True, "weight": 0.5, "above_sma50": True},
        trend_signals={
            "sma": {"bars": 200, "close": 725.25, "sma": 676.488, "up": False},
            "tsm": {"bars": 252, "hurdle": 0.0, "return": 0.18594, "up": True},
            "don": {"bars": 126, "close": 725.25, "midpoint": 705.54, "up": False},
            "votes_up": 1,
        },
    )

    status = trend_status_from_metadata(metadata)

    assert status["in_trend"] is False
    assert status["state"] == "early_reentry"
    assert status["early_reentry_active"] is True
    assert status["early_reentry_weight"] == pytest.approx(0.5)
    assert status["reentry_price"] == pytest.approx(676.488)


def test_drawdown_from_peak_is_reported_when_the_account_has_it() -> None:
    status = trend_status_from_metadata(_metadata(), {"equity": 95_000.0, "drawdown": 5_000.0})

    assert status["drawdown_pct"] == pytest.approx(5.0)
    assert trend_status_from_metadata(_metadata(), {"equity": 0.0, "drawdown": 0.0})["drawdown_pct"] is None


# --------------------------------------------------------------------------- #
# Overview payload
# --------------------------------------------------------------------------- #


class _StubManager:
    runtime = None
    broker = None

    def __init__(self, config_path: Path) -> None:
        self.config_path = config_path

    def current_engine_state(self) -> str:
        return "stopped"

    def fetch_account_snapshot(self) -> dict[str, Any]:
        return {
            "equity": 100_000.0,
            "cash": 60_000.0,
            "daily_pnl": 0.0,
            "realized_pnl": 0.0,
            "unrealized_pnl": 0.0,
            "drawdown": 5_000.0,
            "gross_exposure": 0.0,
            "positions": [],
            "account_suffix": "",
            "snapshot_at": None,
            "age_seconds": 0.0,
            "stale": False,
            "source": "broker",
        }


class _State:
    backtest_manager = None

    def __init__(self, manager: _StubManager) -> None:
        self.engine_manager = manager


def _decision(metadata: dict[str, Any]) -> dict[str, Any]:
    return {
        "timestamp": "2026-10-21T00:00:00+00:00",
        "strategy_name": "equity_leveraged_trend_v1",
        "target": {
            "weights": [{"symbol": "QLD", "weight": 0.98, "reason": "trend_up", "rank": 1}],
            "metadata": metadata,
        },
        "risk": {},
    }


def test_overview_payload_carries_trend_status(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from parayabuck.web.services import dashboard as svc

    manager = _StubManager(tmp_path / "settings.yaml")
    monkeypatch.setattr(svc, "_dashboard_db_path", lambda: tmp_path / "absent.sqlite3")
    monkeypatch.setattr(svc, "_dashboard_account_id", lambda: "test-account")
    monkeypatch.setattr(
        svc,
        "_dashboard_market_decisions",
        lambda _account_id: {"equity": _decision(_metadata())},
    )
    svc._dashboard_snapshot_reset()

    payload = svc.get_overview_payload(_State(manager))
    trend = payload["trend_status"]

    assert trend["available"] is True
    assert trend["in_trend"] is True
    assert trend["exit_price"] == pytest.approx(676.488)
    # drawdown is the dollar gap below peak, so peak = equity + drawdown.
    assert trend["drawdown_pct"] == pytest.approx(5000.0 / 105000.0 * 100.0)
    assert trend["drawdown_context"]["available"] is True
    assert trend["drawdown_context"]["drawdown_pct"] == pytest.approx(5000.0 / 105000.0 * 100.0)
