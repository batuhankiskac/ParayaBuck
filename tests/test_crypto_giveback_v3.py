"""Focused reconstruction checks for the corrected lifecycle audit."""

from __future__ import annotations

import gzip
import json
from pathlib import Path

from research.crypto_exit_giveback_audit_v3.episodes import reconstruct
from research.crypto_exit_giveback_audit_v3.verify import verify

ROOT = Path(__file__).resolve().parents[1]


def test_partial_sale_stays_in_one_episode() -> None:
    def fill(ts: str, side: str, qty: float, price: float, fee: float) -> dict[str, object]:
        return {
            "timestamp": ts,
            "symbol": "BTC/USD",
            "side": side,
            "qty": qty,
            "fill_price": price,
            "fee": fee,
            "decision_timestamp": ts,
        }

    raw = {
        "final_state": {"strategy_name": "synthetic"},
        "metrics": {"starting_equity": 100, "ending_equity": 113},
        "fills": [
            fill("2021-01-01T01:00:00+00:00", "buy", 2, 10, 1),
            fill("2021-01-02T01:00:00+00:00", "sell", 1, 15, 0.5),
            fill("2021-01-03T01:00:00+00:00", "sell", 1, 20, 0.5),
        ],
        "terminal_at": "2021-01-04T00:00:00+00:00",
        "terminal_events": [],
        "snapshots": [
            {"available_at": "2021-01-01T00:00:00+00:00", "nav": 100, "positions": {}},
            {"available_at": "2021-01-02T00:00:00+00:00", "nav": 103, "positions": {"BTC/USD": {"qty": 2, "mark": 12}}},
            {
                "available_at": "2021-01-03T00:00:00+00:00",
                "nav": 111.5,
                "positions": {"BTC/USD": {"qty": 1, "mark": 18}},
            },
            {"available_at": "2021-01-04T00:00:00+00:00", "nav": 113, "positions": {}},
        ],
    }
    result = reconstruct(raw)
    episode = result["episodes"][0]
    assert len(result["episodes"]) == 1
    assert episode["fill_count"] == 3
    assert episode["capital"] == 21
    assert episode["final_pnl"] == 13
    assert episode["mfe"] == 11.5
    assert episode["retention"] > 1
    assert verify(raw, result)["status"] == "PASS"


def test_complete_causal_replay_episode_reconstruction() -> None:
    path = ROOT / "maintenance/crypto_canonical_causal_execution_v1/CORRECTED_REPLAY_A.json.gz"
    with gzip.open(path, "rt") as stream:
        raw = json.load(stream)
    result = reconstruct(raw)
    assert len(result["episodes"]) == 56
    assert sum(not e["censored"] for e in result["episodes"]) == 54
    assert sum(e["censored"] for e in result["episodes"]) == 2
    assert all(e["path"][-1]["timestamp"] < raw["terminal_at"] for e in result["episodes"] if e["censored"])
    assert verify(raw, result)["status"] == "PASS"
    assert abs(result["reconciliation"]["final_residual"]) < 1e-7
