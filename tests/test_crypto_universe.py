from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml

from parayabuck.config import EquityUniverseConfig, load_config
from parayabuck.core.models import (
    Direction,
    Fill,
    Market,
    OrderIntent,
    OrderState,
)
from parayabuck.execution.alpaca import (
    AlpacaBroker,
    AmbiguousOrderState,
    CryptoPriceObservation,
)
from parayabuck.execution.ledger import ExecutionLedger
from parayabuck.universe import (
    EquityCandidate,
    EquityUniverseScanner,
    load_active_symbols,
    validate_active_universe,
)

pytestmark = pytest.mark.paper_chaos


def _write_config(path: Path, raw: dict[str, Any]) -> None:
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")


def test_new_universe_is_canonical_and_crypto_does_not_count_toward_iex(
    tmp_path: Path,
) -> None:
    path = tmp_path / "settings.yaml"
    equities = [f"S{index}" for index in range(29)]
    _write_config(
        path,
        {
            "alpaca": {"paper": True, "stock_feed": "iex"},
            "universe": {
                "equity": {
                    "reference_symbols": ["spy"],
                    "fallback_trade_symbols": equities,
                    "dynamic_selection_enabled": False,
                },
                "crypto": {
                    "enabled": True,
                    "trade_symbols": ["btcusd", "ETH/USD"],
                },
            },
            "engine": {"crypto_trading_enabled": False},
        },
    )
    config = load_config(str(path))
    assert config.universe.equity.reference_symbols == ("SPY",)
    assert config.universe.crypto.trade_symbols == ("BTC/USD", "ETH/USD")
    assert len(config.universe.equity.configured_stream_symbols) == 30


def test_iex_rejects_reference_plus_trade_symbols_over_limit(tmp_path: Path) -> None:
    path = tmp_path / "settings.yaml"
    _write_config(
        path,
        {
            "alpaca": {"paper": True, "stock_feed": "iex"},
            "universe": {
                "equity": {
                    "reference_symbols": ["SPY"],
                    "fallback_trade_symbols": [f"S{index}" for index in range(30)],
                    "dynamic_selection_enabled": False,
                },
                "crypto": {"enabled": False},
            },
        },
    )
    with pytest.raises(ValueError, match="IEX"):
        load_config(str(path))


def test_disabled_market_needs_no_symbols(tmp_path: Path) -> None:
    path = tmp_path / "settings.yaml"
    _write_config(
        path,
        {
            "universe": {
                "equity": {
                    "enabled": False,
                    "reference_symbols": [],
                    "fallback_trade_symbols": [],
                },
                "crypto": {"enabled": True, "trade_symbols": ["BTC/USD"]},
            },
            "engine": {
                "equity_trading_enabled": False,
                "crypto_trading_enabled": True,
            },
        },
    )
    assert load_config(str(path)).symbols == ("BTC/USD",)


def test_scanner_is_deterministic_and_atomic(tmp_path: Path) -> None:
    active = tmp_path / "active.json"
    config = EquityUniverseConfig(
        maximum_stream_symbols=3,
        reference_symbols=("SPY",),
        fallback_trade_symbols=("AAPL",),
        active_universe_path=str(active),
    )
    scanner = EquityUniverseScanner(config, maximum_per_sector=1)
    candidates = [
        EquityCandidate("MSFT", 400, 500_000_000, 0.03, 1.0, 2, "technology"),
        EquityCandidate("AAPL", 200, 600_000_000, 0.03, 1.0, 2, "technology"),
        EquityCandidate("JPM", 220, 300_000_000, 0.025, 0.99, 3, "financial"),
    ]
    timestamp = datetime(2026, 7, 28, 12, tzinfo=UTC)
    first = scanner.scan(candidates, data_timestamp=timestamp, selection_timestamp=timestamp)
    second = scanner.scan(
        list(reversed(candidates)), data_timestamp=timestamp, selection_timestamp=timestamp
    )
    assert first == second
    assert first.selected_symbols == ("SPY", "AAPL", "JPM")
    assert first.eliminated["MSFT"] == "sector_concentration"
    scanner.persist(first)
    assert load_active_symbols(config) == first.selected_symbols
    assert not active.with_suffix(".json.tmp").exists()


def test_equity_universe_rejects_stale_data_and_non_equity_symbols(tmp_path: Path) -> None:
    active = tmp_path / "active.json"
    config = EquityUniverseConfig(
        maximum_stream_symbols=3,
        reference_symbols=("SPY",),
        active_universe_path=str(active),
        active_universe_max_age_hours=1,
    )
    scanner = EquityUniverseScanner(config)
    now = datetime.now(UTC)
    scanner.persist(
        scanner.scan(
            [EquityCandidate("AAPL", 200, 600_000_000, 0.03, 1.0)],
            data_timestamp=now - timedelta(hours=2),
            selection_timestamp=now,
        )
    )
    assert validate_active_universe(config)[1] == "stale"

    payload = json.loads(active.read_text())
    payload["data_timestamp"] = now.isoformat()
    payload["selected_symbols"].append("BTC/USD")
    payload["candidate_symbols"].append("BTC/USD")
    payload["score_breakdown"]["BTC/USD"] = {}
    active.write_text(json.dumps(payload))
    assert validate_active_universe(config)[1] == "invalid"


def test_scanner_reserves_approved_inverse_etf_and_rejects_leveraged_inverse(
    tmp_path: Path,
) -> None:
    config = EquityUniverseConfig(
        maximum_stream_symbols=4,
        reference_symbols=("SPY",),
        fallback_trade_symbols=("AAPL", "SH"),
        inverse_trade_symbols=("SH",),
        active_universe_path=str(tmp_path / "active.json"),
    )
    scanner = EquityUniverseScanner(config)
    candidates = [
        EquityCandidate("AAPL", 200, 600_000_000, 0.03, 1.0, 2, "technology"),
        EquityCandidate(
            "SH",
            40,
            100_000_000,
            0.02,
            1.0,
            3,
            "inverse_market",
            leveraged_or_inverse=True,
        ),
        EquityCandidate(
            "SQQQ",
            10,
            500_000_000,
            0.05,
            1.0,
            3,
            "inverse_market",
            leveraged_or_inverse=True,
        ),
    ]

    result = scanner.scan(candidates, data_timestamp=datetime.now(UTC))

    assert result.selected_symbols[:2] == ("SPY", "SH")
    assert result.score_breakdown["SH"]["selection_role"] == "inverse_hedge"
    assert result.eliminated["SQQQ"] == "leveraged_or_inverse"


def test_inverse_universe_only_accepts_approved_minus_one_etfs() -> None:
    with pytest.raises(ValueError, match="approved -1x"):
        EquityUniverseConfig(
            fallback_trade_symbols=("SQQQ",),
            inverse_trade_symbols=("SQQQ",),
        )


def _managed_crypto_ledger(tmp_path: Path) -> tuple[ExecutionLedger, OrderIntent]:
    ledger = ExecutionLedger(str(tmp_path / "ledger.sqlite3"))
    order = OrderIntent(
        "signal",
        "BTC/USD",
        Market.CRYPTO,
        Direction.LONG,
        0.1,
        100,
        95,
        110,
        "test",
        client_order_id="parayabuck-entry",
    )
    ledger.register_entry_order("acct", order, "entry-id")
    ledger.record_entry_fill(
        "acct",
        order,
        Fill(
            order.client_order_id,
            order.symbol,
            order.qty,
            100,
            Direction.LONG,
            datetime.now(UTC),
        ),
        "entry-id",
    )
    ledger.register_protective_stop(
        "acct", order.client_order_id, "parayabuck-entry-stop", "stop-id"
    )
    return ledger, order


class _ExitClient:
    def __init__(self, stop_status: str = "canceled") -> None:
        self.stop_status = stop_status
        self.submitted: list[Any] = []

    def cancel_order_by_id(self, _order_id: str) -> None:
        return

    def get_order_by_id(self, order_id: str) -> Any:
        if order_id == "stop-id":
            return SimpleNamespace(
                id=order_id,
                status=self.stop_status,
                qty="0.1",
                filled_qty="0.1" if self.stop_status == "filled" else "0",
                filled_avg_price="94" if self.stop_status == "filled" else None,
            )
        return SimpleNamespace(
            id=order_id,
            status="filled",
            qty="0.1",
            filled_qty="0.1",
            filled_avg_price="111",
        )

    def get_open_position(self, _symbol: str) -> Any:
        # Account also owns 0.5 BTC externally; only 0.1 is managed.
        return SimpleNamespace(qty="0.6", qty_available="0.6")

    def submit_order(self, request: Any) -> Any:
        self.submitted.append(request)
        return SimpleNamespace(id="target-id")


def _broker(ledger: ExecutionLedger, client: Any) -> AlpacaBroker:
    broker = AlpacaBroker.__new__(AlpacaBroker)
    broker.ledger = ledger
    broker._account_id = "acct"
    broker._client = client
    broker._asset_cache = {
        "BTC/USD": SimpleNamespace(
            min_trade_increment="0.000000001",
            price_increment="0.01",
        )
    }
    broker._symbol_exit_locks = {}
    broker.crypto_emergency_exit_bps = 75
    return broker


def test_target_cancels_stop_then_exits_only_managed_quantity(tmp_path: Path) -> None:
    ledger, _order = _managed_crypto_ledger(tmp_path)
    client = _ExitClient()
    broker = _broker(ledger, client)
    result = broker.process_crypto_price(
        CryptoPriceObservation("BTC/USD", 111, 109, 110, "one_minute_bar", datetime.now(UTC))
    )
    assert result == "target"
    assert len(client.submitted) == 1
    assert float(client.submitted[0].qty) == pytest.approx(0.1)
    assert ledger.managed_for_account("acct") == {}
    ledger.close()


def test_stop_fill_during_target_cancel_never_submits_duplicate_sell(
    tmp_path: Path,
) -> None:
    ledger, _order = _managed_crypto_ledger(tmp_path)
    client = _ExitClient(stop_status="filled")
    broker = _broker(ledger, client)
    broker.process_crypto_price(
        CryptoPriceObservation("BTC/USD", 111, 94, 100, "one_minute_bar", datetime.now(UTC))
    )
    assert client.submitted == []
    assert ledger.managed_for_account("acct") == {}
    ledger.close()


def test_unconfirmed_stop_cancel_halts_without_target_order(tmp_path: Path) -> None:
    ledger, _order = _managed_crypto_ledger(tmp_path)
    client = _ExitClient(stop_status="accepted")
    broker = _broker(ledger, client)
    with pytest.raises(AmbiguousOrderState, match="unconfirmed"):
        broker.process_crypto_price(
            CryptoPriceObservation("BTC/USD", 111, 109, 110, "one_minute_bar", datetime.now(UTC))
        )
    assert client.submitted == []
    position = ledger.managed_for_account("acct")["BTC/USD"]
    assert position.status == "manual_intervention_required"
    ledger.close()


def test_crypto_risk_rejects_inactive_account() -> None:
    # The direct broker lifecycle must preserve a broker-confirmed inactive state.
    broker = AlpacaBroker.__new__(AlpacaBroker)
    broker._validate_pre_submit = lambda _intent: None
    broker._validated_values = lambda _intent: (_intent.qty, 95.0, 110.0)
    broker._client = SimpleNamespace()
    intent = OrderIntent(
        "signal",
        "BTC/USD",
        Market.CRYPTO,
        Direction.SHORT,
        0.1,
        100,
        105,
        90,
        "test",
    )
    # Asset validation is the final broker boundary even when the strategy/risk
    # layers have already rejected crypto shorts.
    broker._asset_cache = {
        "BTC/USD": SimpleNamespace(
            tradable=True,
            min_trade_increment="0.0001",
            min_order_size="0.0001",
            price_increment="0.01",
        )
    }
    with pytest.raises(ValueError, match="short"):
        AlpacaBroker._validated_values(broker, intent)
    assert OrderState.REJECTED.value == "rejected"
