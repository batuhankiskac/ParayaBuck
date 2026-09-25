from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml

from parayabuck.config import load_config
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
from tests.helpers import apply_entry_fill

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
            "alpaca": {"paper": True, "stock_stream_feed": "iex", "stock_historical_feed": "sip"},
            "universe": {
                "equity": {"symbols": ["spy", *equities], "maximum_stream_symbols": 30},
                "crypto": {
                    "enabled": True,
                    "trade_symbols": ["btcusd", "ETH/USD"],
                },
            },
            "strategy": {"equity": {"name": "equity_leveraged_trend_v1"}, "crypto": {"enabled": False}},
        },
    )
    config = load_config(str(path))
    assert config.universe.equity.symbols == ("SPY", *equities)
    assert config.universe.crypto.trade_symbols == ("BTC/USD", "ETH/USD")
    # 30 equity symbols fill the IEX stream budget exactly; the two crypto
    # symbols are a separate market and must not count toward it.
    assert len(config.universe.equity.symbols) == 30


def test_iex_rejects_equity_symbols_over_limit(tmp_path: Path) -> None:
    path = tmp_path / "settings.yaml"
    _write_config(
        path,
        {
            "alpaca": {"paper": True, "stock_stream_feed": "iex", "stock_historical_feed": "sip"},
            "universe": {
                "equity": {
                    "symbols": ["SPY", *[f"S{index}" for index in range(30)]],
                    "maximum_stream_symbols": 31,
                },
                "crypto": {"enabled": False},
            },
            "strategy": {
                "equity": {"name": "equity_leveraged_trend_v1"},
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
                "equity": {"enabled": False, "symbols": []},
                "crypto": {"enabled": True, "trade_symbols": ["BTC/USD"]},
            },
            "strategy": {
                "equity": {"enabled": False},
                "crypto": {"name": "crypto_low_variance_v2"},
            },
        },
    )
    assert load_config(str(path)).symbols == ("BTC/USD",)


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
    apply_entry_fill(
        ledger,
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
    ledger.register_protective_stop("acct", order.client_order_id, "parayabuck-entry-stop", "stop-id")
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


def _broker(
    ledger: ExecutionLedger,
    client: Any,
    *,
    stop_cancel_timeout_seconds: float | None = None,
) -> AlpacaBroker:
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
    if stop_cancel_timeout_seconds is not None:
        broker.crypto_stop_cancel_timeout_seconds = stop_cancel_timeout_seconds
    return broker


def test_target_cancels_stop_then_exits_only_managed_quantity(tmp_path: Path) -> None:
    ledger, _order = _managed_crypto_ledger(tmp_path)
    client = _ExitClient()
    broker = _broker(ledger, client)
    result = broker.process_crypto_price(
        CryptoPriceObservation("BTC/USD", 111, 109, "one_minute_bar", datetime.now(UTC))
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
    broker.process_crypto_price(CryptoPriceObservation("BTC/USD", 111, 94, "one_minute_bar", datetime.now(UTC)))
    assert client.submitted == []
    assert ledger.managed_for_account("acct") == {}
    ledger.close()


def test_unconfirmed_stop_cancel_halts_without_target_order(tmp_path: Path) -> None:
    ledger, _order = _managed_crypto_ledger(tmp_path)
    client = _ExitClient(stop_status="accepted")
    broker = _broker(ledger, client, stop_cancel_timeout_seconds=0.03)
    with pytest.raises(AmbiguousOrderState, match="unconfirmed"):
        broker.process_crypto_price(CryptoPriceObservation("BTC/USD", 111, 109, "one_minute_bar", datetime.now(UTC)))
    assert client.submitted == []
    position = ledger.managed_for_account("acct")["BTC/USD"]
    assert position.status == "manual_intervention_required"
    ledger.close()


def test_partial_crypto_exit_is_cancelled_and_remaining_qty_reprotected(
    tmp_path: Path,
) -> None:
    ledger, order = _managed_crypto_ledger(tmp_path)
    client = _ExitClient()
    broker = _broker(ledger, client)
    partial = Fill(
        order.client_order_id,
        order.symbol,
        0.04,
        111,
        Direction.LONG,
        datetime.now(UTC),
    )
    responses = iter(
        (
            (OrderState.PARTIALLY_FILLED, partial),
            (OrderState.CANCELLED, partial),
        )
    )
    broker._wait_for_fill = lambda *_args, **_kwargs: next(responses)

    result = broker.process_crypto_price(
        CryptoPriceObservation("BTC/USD", 111, 109, "one_minute_bar", datetime.now(UTC))
    )

    assert result == "target"
    assert len(client.submitted) == 2
    assert float(client.submitted[1].qty) == pytest.approx(0.06)
    remaining = ledger.managed_for_account("acct")["BTC/USD"]
    assert remaining.owned_qty == pytest.approx(0.06)
    assert remaining.stop_state == "open"
    assert remaining.status == "open"
    ledger.close()
