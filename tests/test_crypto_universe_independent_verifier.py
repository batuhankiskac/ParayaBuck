"""Small mutation checks for the standalone daily universe verifier."""

from copy import deepcopy

from maintenance.crypto_replay_parity_v1.universe_verify import verify


def _payload() -> dict:
    bars = [
        {
            "symbol": "BTC/USD",
            "timestamp": f"2024-01-0{day}T00:00:00+00:00",
            "timeframe": "1d",
            "market": "crypto",
            "open": 100,
            "close": 100,
            "high": 101,
            "low": 99,
            "volume": 1000,
        }
        for day in (1, 2)
    ]
    return {
        "universe_timeframe": "1d",
        "daily_bars": bars,
        "config": {
            "universe": {
                "crypto": {
                    "trade_symbols": ["BTC/USD"],
                    "allowlist_symbols": [],
                    "denylist_symbols": [],
                    "pinned_symbols": ["BTC/USD"],
                    "maximum_stream_symbols": 12,
                    "scanner": {
                        "lookback_days": 14,
                        "minimum_data_completeness": 0.9,
                        "minimum_median_dollar_volume": 1000,
                        "minimum_atr_pct": 0.004,
                        "maximum_atr_pct": 0.2,
                    },
                }
            }
        },
        "universe": {
            "snapshots": [
                {
                    "effective_at": "2024-01-02T00:00:00+00:00",
                    "symbols": ["BTC/USD"],
                    "candidate_count": 1,
                    "eliminated": {},
                    "pinned_symbols": ["BTC/USD"],
                    "maximum_stream_symbols": 12,
                    "score_breakdown": {"BTC/USD": {"spread_source": "unavailable"}},
                }
            ]
        },
    }


def test_independent_verifier_detects_membership_mutation() -> None:
    payload = _payload()
    assert verify(payload)["status"] == "PASS"
    changed = deepcopy(payload)
    changed["universe"]["snapshots"][0]["symbols"].append("ETH/USD")
    assert verify(changed)["status"] == "FAIL"


def test_current_boundary_bar_does_not_change_prior_eligibility() -> None:
    payload = _payload()
    before = verify(payload)
    payload["daily_bars"][-1]["volume"] = 0
    payload["daily_bars"][-1]["close"] = 10000
    after = verify(payload)
    assert after["status"] == "PASS"
    assert before["metric_receipts_sha256"] == after["metric_receipts_sha256"]
