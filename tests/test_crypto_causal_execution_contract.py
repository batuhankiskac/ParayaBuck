"""Regression checks for the frozen offline crypto execution clock."""

from __future__ import annotations

import csv
import gzip
import json
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from maintenance.crypto_canonical_causal_execution_v1.contract import (
    complete_daily_available_at,
    first_causal_hour,
)
from parayabuck.core.models import Bar, Market


def bar(symbol: str, timestamp: datetime, timeframe: str, price: float = 100.0) -> Bar:
    return Bar(timestamp, symbol, timeframe, price, price, price, price, 1.0, Market.CRYPTO)


def test_start_labeled_hours_complete_only_at_next_midnight() -> None:
    label = datetime(2021, 4, 1, tzinfo=UTC)
    daily = bar("BTC/USD", label, "1d")
    hours = [bar("BTC/USD", label + timedelta(hours=hour), "1h") for hour in range(24)]
    assert complete_daily_available_at(daily, hours) == datetime(2021, 4, 2, tzinfo=UTC)
    with pytest.raises(ValueError, match="Incomplete"):
        complete_daily_available_at(daily, hours[:-1])


def test_same_boundary_open_is_excluded_and_missing_hour_uses_next_source_open() -> None:
    decision = datetime(2021, 4, 2, tzinfo=UTC)
    zero = bar("BTC/USD", decision, "1h", 58714.99)
    one = bar("BTC/USD", decision + timedelta(hours=1), "1h", 58625.59)
    two = bar("BTC/USD", decision + timedelta(hours=2), "1h", 58300.0)
    assert first_causal_hour([zero, one, two], boundary=decision, order_created_at=decision) == one
    assert first_causal_hour([zero, two], boundary=decision, order_created_at=decision) == two
    assert first_causal_hour([zero], boundary=decision, order_created_at=decision) is None


def test_pinned_2021_btc_eth_regression_uses_actual_source_hours() -> None:
    source = Path(__file__).resolve().parents[1] / "data/historical/crypto_expanded_1h.csv"
    expected = {"BTC/USD": (58714.99, 58625.59), "ETH/USD": (1968.4, 1958.13)}
    midnight = datetime(2021, 4, 2, tzinfo=UTC)
    found: dict[str, dict[datetime, float]] = {symbol: {} for symbol in expected}
    with source.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            symbol = row["symbol"]
            if symbol not in found:
                continue
            timestamp = datetime.fromisoformat(row["timestamp"].replace("Z", "+00:00"))
            if midnight <= timestamp <= midnight + timedelta(hours=1):
                found[symbol][timestamp] = float(row["open"])
    for symbol, (old_same_boundary, causal_open) in expected.items():
        assert found[symbol][midnight] == old_same_boundary
        assert found[symbol][midnight + timedelta(hours=1)] == causal_open
        hours = [bar(symbol, timestamp, "1h", price) for timestamp, price in found[symbol].items()]
        selected = first_causal_hour(hours, boundary=midnight, order_created_at=midnight)
        assert selected is not None and selected.timestamp > midnight
        assert selected.open == causal_open


def test_corrected_control_repeats_and_preserves_partial_fill_and_terminal_invariants() -> None:
    base = Path(__file__).resolve().parents[1] / "maintenance/crypto_canonical_causal_execution_v1"
    a = (base / "CORRECTED_REPLAY_A.json.gz").read_bytes()
    b = (base / "CORRECTED_REPLAY_B.json.gz").read_bytes()
    assert a == b
    payload = json.loads(gzip.decompress(a))
    assert payload["contract_git_sha"] == "2b483b1e5022a23a0c34cc3ab15145da342c89d6"
    assert payload["counts"]["decisions"] == 454
    assert payload["counts"]["fills"] == 406
    assert any(count > 1 for count in Counter(fill["client_order_id"] for fill in payload["fills"]).values())
    assert payload["final_state"]["holdings"] == {}
    assert len(payload["terminal_events"]) == 2
    for event in payload["price_events"]:
        assert datetime.fromisoformat(event["source_price_event_time"]) > datetime.fromisoformat(
            event["order_created_at"]
        )
    labels = [datetime.fromisoformat(row["label_time"]) for row in payload["decisions"]]
    assert len({int(label.timestamp()) // (96 * 3600) for label in labels}) == len(labels)
