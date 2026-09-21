from __future__ import annotations

from datetime import UTC, datetime, timedelta
from importlib import import_module

import pytest

verifier = import_module("research.52week_high_economic_v1.verify_economic")


def test_closed_pnl_reconstruction_preserves_partial_exit_and_terminal_fee() -> None:
    epoch = datetime(2024, 1, 1, tzinfo=UTC)
    evidence = {
        "fills": [
            {
                "timestamp": epoch.isoformat(),
                "symbol": "AAA",
                "side": "buy",
                "qty": 10.0,
                "fill_price": 100.0,
                "fee": 1.0,
            },
            {
                "timestamp": (epoch + timedelta(days=1)).isoformat(),
                "symbol": "AAA",
                "side": "sell",
                "qty": 4.0,
                "fill_price": 110.0,
                "fee": 0.5,
            },
        ],
        "terminal_events": [
            {
                "timestamp": (epoch + timedelta(days=2)).isoformat(),
                "symbol": "AAA",
                "quantity": 6.0,
                "fill_price": 120.0,
                "fee": 0.6,
            }
        ],
    }

    closed = verifier._closed_pnls_from_ledger(evidence, epoch, epoch + timedelta(days=3))

    assert closed == pytest.approx([157.9])
