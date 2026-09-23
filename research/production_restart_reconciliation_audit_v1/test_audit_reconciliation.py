"""Offline characterization of production reconciliation; no broker connection."""

from types import SimpleNamespace

from parayabuck.core.models import AccountSnapshot, Market, PositionSnapshot
from parayabuck.execution.alpaca_reconciliation import ReconciliationService


def test_local_nonterminal_order_missing_at_broker_is_startup_drift() -> None:
    local_order = SimpleNamespace(client_order_id="intent-1")
    broker = SimpleNamespace(
        account=lambda: AccountSnapshot(cash=100, equity=100, peak_equity=100, daily_pnl=0),
        manage_external_positions=False,
        portfolio_owned_quantities=lambda: {},
        portfolio_open_orders=lambda: (local_order,),
        ledger=SimpleNamespace(
            managed_for_account=lambda _account: {},
            managed_entries_for_account=lambda _account: (),
        ),
        _account_id="acct",
        _get_open_orders=lambda _request: (),
    )
    result = ReconciliationService(broker).reconcile()
    assert result["MISSING_BROKER_ORDER"] == ["intent-1"]
    assert result["MISSING_LOCAL_ORDER"] == ["intent-1"]


def test_filled_buy_without_trade_update_halts_before_phase_recovery() -> None:
    broker_position = PositionSnapshot(symbol="AAPL", qty=1, avg_price=100, current_price=100, market=Market.EQUITY)
    broker = SimpleNamespace(
        account=lambda: AccountSnapshot(cash=0, equity=100, peak_equity=100, daily_pnl=0, positions=(broker_position,)),
        manage_external_positions=False,
        portfolio_owned_quantities=lambda: {},
        portfolio_open_orders=lambda: (SimpleNamespace(client_order_id="intent-buy-1"),),
        ledger=SimpleNamespace(
            managed_for_account=lambda _account: {},
            managed_entries_for_account=lambda _account: (),
        ),
        _account_id="acct",
        _get_open_orders=lambda _request: (),
    )
    result = ReconciliationService(broker).reconcile()
    assert result["BROKER_STALE"] == ["AAPL"]
    assert result["MISSING_BROKER_ORDER"] == ["intent-buy-1"]


def test_nonadvancing_full_fill_page_reports_success_and_advances_watermark() -> None:
    first_page = [
        {
            "id": f"fill-{index:03d}",
            "order_id": f"order-{index:03d}",
            "side": "buy",
            "symbol": "AAPL",
            "qty": "1",
            "price": "1",
            "transaction_time": "2026-01-01T00:00:00+00:00",
        }
        for index in range(100)
    ]
    calls = []
    watermark = []

    def request(_method: str, _path: str, data: dict[str, object]) -> object:
        calls.append(data)
        return first_page

    ledger = SimpleNamespace(
        get_reconcile_watermark=lambda _account, _kind: None,
        set_reconcile_watermark=lambda _account, _kind, value: watermark.append(value),
        apply_fee_activity=lambda *args, **kwargs: False,
        entry_order_for_broker_id=lambda *_args: None,
    )
    broker = SimpleNamespace(
        _client_adapter=SimpleNamespace(request=request),
        ledger=ledger,
        _account_id="acct",
    )
    assert ReconciliationService(broker)._reconcile_fill_activity_fees() is True
    assert len(calls) == 2
    assert calls[1]["page_token"] == "fill-099"
    assert len(watermark) == 1


def test_malformed_fill_response_reports_success() -> None:
    broker = SimpleNamespace(
        _client_adapter=SimpleNamespace(request=lambda *_args, **_kwargs: {"error": "not a page"}),
        ledger=SimpleNamespace(get_reconcile_watermark=lambda *_args: None),
        _account_id="acct",
    )
    assert ReconciliationService(broker)._reconcile_fill_activity_fees() is True
