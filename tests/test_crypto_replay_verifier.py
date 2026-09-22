"""Synthetic, offline mutation tests for the independent replay audit."""

from __future__ import annotations

import copy
import csv
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from maintenance.crypto_replay_parity_v1.verify import (
    check_daily_bars,
    file_sha256,
    independently_rank,
    independently_select,
    scheduled,
    verify_payload,
)


def fixture_payload(tmp_path: Path) -> dict:
    start = datetime.fromtimestamp(5000 * 96 * 3600, UTC)
    symbol = "AAA/USD"
    daily = [
        {
            "timestamp": (start + timedelta(days=offset)).isoformat(),
            "symbol": symbol,
            "open": 10.0,
            "high": 10.0,
            "low": 10.0,
            "close": 10.0,
            "volume": 24000.0,
            "market": "crypto",
            "timeframe": "1d",
        }
        for offset in range(-22, 2)
    ]
    source = tmp_path / "hourly.csv"
    with source.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["timestamp", "symbol", "open", "high", "low", "close", "volume"])
        writer.writeheader()
        for bar in daily:
            for hour in range(24):
                writer.writerow(
                    {
                        "timestamp": (datetime.fromisoformat(bar["timestamp"]) + timedelta(hours=hour)).isoformat(),
                        "symbol": symbol,
                        "open": 10,
                        "high": 10,
                        "low": 10,
                        "close": 10,
                        "volume": 1000,
                    }
                )
    limits = {
        "daily_loss_limit": 0.005,
        "drawdown_halt": 0.05,
        "maximum_crypto_asset": 0.05,
        "maximum_positions": 6,
        "maximum_crypto_positions": 2,
        "maximum_crypto": 0.1,
        "maximum_gross": 0.6,
        "minimum_cash": 0.4,
    }
    engine = {
        "starting_cash": 1000,
        "maximum_allocation": 0.05,
        "risk_limits": limits,
        "enforce_drawdown_halt": False,
        "liquidity_lookback_bars": 20,
        "max_participation_pct": 0.1,
        "commission": 0,
        "slippage_bps": 2,
        "spread_bps": 2,
        "crypto_taker_fee_bps": 25,
        "turnover_band": 0.005,
        "minimum_order_notional": 10,
        "liquidate_at_end": False,
    }
    price = 10 * 1.0003
    fee = 5 * price * 0.0025
    cash = 1000 - 5 * price - fee
    ts, next_ts = start.isoformat(), (start + timedelta(days=1)).isoformat()
    order = {"symbol": symbol, "side": "buy", "qty": 5.0, "created_at": ts, "decision_timestamp": ts}
    return {
        "data": {"path": str(source), "sha256": file_sha256(source)},
        "daily_bars": daily,
        "engine": engine,
        "config": {
            "strategy": {
                "crypto": {
                    "name": "crypto_low_variance_hysteresis_v1",
                    "timeframe": "1d",
                    "maximum_allocation": 0.05,
                    "parameters": {"rebalance_hours": 96, "top_n": 2, "variance_lookback_days": 22},
                }
            },
            "backtest": {
                "commission": 0,
                "slippage_bps": 2,
                "spread_bps": 2,
                "crypto_taker_fee_bps": 25,
                "initial_cash": 1000,
            },
        },
        "window": {"start": ts, "end": next_ts},
        "universe": {
            "snapshots": [
                {
                    "effective_at": ts,
                    "symbols": [symbol],
                    "observation_set": [symbol],
                    "maximum_stream_symbols": 2,
                    "pinned_symbols": [],
                }
            ]
        },
        "decisions": [
            {
                "timestamp": ts,
                "history": {symbol: daily[:23]},
                "eligible_symbols": [symbol],
                "holdings": {},
                "equity": 1000.0,
                "scores": [[0.0, symbol]],
                "alpha_target": {
                    "metadata": {"selected_symbols": [symbol], "retained": []},
                    "weights": [{"symbol": symbol, "weight": 1.0}],
                },
                "target": {"weights": [{"symbol": symbol, "weight": 0.05}], "cash_weight": 0.95},
                "orders": [order],
            }
        ],
        "snapshots": [
            {"timestamp": ts, "cash": 1000.0, "nav": 1000.0, "positions": {}},
            {
                "timestamp": next_ts,
                "cash": cash,
                "nav": cash + 50,
                "positions": {symbol: {"qty": 5.0, "avg_price": price, "entry_fee": fee, "mark": 10.0}},
            },
        ],
        "fills": [
            {
                "timestamp": next_ts,
                "symbol": symbol,
                "side": "buy",
                "qty": 5.0,
                "reference_price": 10.0,
                "fill_price": price,
                "fee": fee,
                "slippage_cost": 0.01,
                "spread_cost": 0.005,
                "decision_timestamp": ts,
            }
        ],
        "terminal_events": [],
        "metrics": {"ending_equity": cash + 50},
        "final_state": {
            "cash": cash,
            "holdings": {symbol: 5.0},
            "avg_prices": {symbol: price},
            "entry_fees": {symbol: fee},
            "realized_pnl": 0.0,
        },
    }


def test_independent_complete_execution_and_cost_accounting(tmp_path: Path) -> None:
    result = verify_payload(fixture_payload(tmp_path))
    assert result["status"] == "PASS"
    assert result["decisions"] == result["fills"] == result["vacancy_acquisitions"] == 1


@pytest.mark.parametrize(
    "stage", ["input", "bar", "config", "score", "selection", "target", "order", "fill", "fee", "nav", "state"]
)
def test_verifier_rejects_upstream_and_downstream_tampering(tmp_path: Path, stage: str) -> None:
    payload = fixture_payload(tmp_path)
    if stage == "input":
        payload["data"]["sha256"] = "0" * 64
    elif stage == "bar":
        payload["daily_bars"][0]["close"] = 11.0
    elif stage == "config":
        payload["config"]["strategy"]["crypto"]["parameters"]["top_n"] = 3
    elif stage == "score":
        payload["decisions"][0]["scores"][0][0] = 0.1
    elif stage == "selection":
        payload["decisions"][0]["alpha_target"]["metadata"]["selected_symbols"] = []
    elif stage == "target":
        payload["decisions"][0]["target"]["weights"][0]["weight"] = 0.04
    elif stage == "order":
        payload["decisions"][0]["orders"][0]["qty"] = 6.0
    elif stage == "fill":
        payload["fills"][0]["qty"] = 6.0
    elif stage == "fee":
        payload["fills"][0]["fee"] = 0.0
    elif stage == "nav":
        payload["snapshots"][1]["nav"] = 1000.0
    else:
        payload["final_state"]["holdings"] = {}
    with pytest.raises(AssertionError):
        verify_payload(payload)


def test_rank_ties_gaps_and_retention_are_independent(tmp_path: Path) -> None:
    history = fixture_payload(tmp_path)["decisions"][0]["history"]
    window = history["AAA/USD"]
    history["BBB/USD"] = copy.deepcopy(window)
    assert independently_rank(history, ["BBB/USD", "AAA/USD"]) == [(0.0, "AAA/USD"), (0.0, "BBB/USD")]
    history["BBB/USD"][0]["timestamp"] = (
        datetime.fromisoformat(window[0]["timestamp"]) - timedelta(days=1)
    ).isoformat()
    assert independently_rank(history, ["BBB/USD"]) == []
    assert independently_select(["A", "B", "C", "D", "E"], {"D": 1, "E": 1}) == (["D", "A"], ["D"])
    assert independently_select(["A", "B"], {"C": 1}) == (["A", "B"], [])


def test_epoch_clock_and_boundary_validation() -> None:
    anchor = datetime.fromtimestamp(5000 * 96 * 3600, UTC)
    assert scheduled(None, anchor)
    for hours in (24, 48, 72):
        assert not scheduled(anchor, anchor + timedelta(hours=hours))
    assert scheduled(anchor, anchor + timedelta(hours=96))
    assert scheduled(anchor, anchor + timedelta(hours=97))
    with pytest.raises(AssertionError, match="Non-UTC-midnight"):
        check_daily_bars([{"symbol": "A", "timestamp": (anchor + timedelta(hours=1)).isoformat()}])
