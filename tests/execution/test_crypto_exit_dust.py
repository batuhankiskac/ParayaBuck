"""A crypto exit leaving sub-precision fee-net dust is flat, not owned drift.

Numbers from the 2026-09-25 PAPER incident: fills are booked fee-net at full
float precision while the protective stops sold the broker's 9-decimal
position, leaving 4.375e-10 ETH and 1.66e-9 BTC of phantom ownership.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

from parayabuck.core.models import AccountSnapshot, Market
from parayabuck.execution.alpaca_reconciliation import ReconciliationService
from parayabuck.execution.portfolio import PortfolioOrderRequest, PortfolioOrderSide, RiskOverlayDecision
from parayabuck.portfolio.models import TargetPortfolio, TargetWeight
from parayabuck.portfolio.runtime_ownership import net_owned_quantities
from parayabuck.portfolio.runtime_service import PortfolioStateRepository

_NOW = datetime(2026, 9, 25, 19, 52, tzinfo=UTC)
_FILLS = {
    # symbol: (ordered/broker-filled, booked fee-net quantity, stop sell fill)
    "ETH/USD": (0.914782625, 0.9124956684375, 0.912495668),
    "BTC/USD": (0.029134135, 0.0290612996625, 0.029061298),
}


def _fill(repository: PortfolioStateRepository, client_order_id: str, quantity: float) -> None:
    assert repository.apply_trade_update(
        client_order_id=client_order_id,
        broker_order_id="broker-" + client_order_id,
        status="filled",
        cumulative_filled_quantity=quantity,
        average_fill_price=100.0,
        occurred_at=_NOW,
        activity_id=f"{client_order_id}:fill",
        fee=0.0,
        realized_pnl=0.0,
        unrealized_pnl=0.0,
    )


def _crypto_round_trip(tmp_path: Any, exits: dict[str, float]) -> PortfolioStateRepository:
    repository = PortfolioStateRepository(tmp_path / "runtime.sqlite3")
    session = repository.begin_session("acct", "digest", "paper")
    target = TargetPortfolio(
        "fixed",
        "1",
        _NOW,
        tuple(TargetWeight(symbol, 0.1) for symbol in _FILLS),
        0.8,
        universe_version="test",
    )
    risk = RiskOverlayDecision(target, False, (), 0.2, 0.2)
    buys = tuple(
        PortfolioOrderRequest(
            client_order_id=f"buy-{symbol}",
            decision_id="",
            symbol=symbol,
            market=Market.CRYPTO,
            side=PortfolioOrderSide.BUY,
            quantity=ordered,
            reference_price=100.0,
            decision_timestamp=_NOW,
            strategy_name="fixed",
            strategy_version="1",
            risk_reducing=False,
        )
        for symbol, (ordered, _filled, _stop) in _FILLS.items()
    )
    repository.record_decision_with_orders(session, "acct", "digest", target, risk, Market.CRYPTO, buys)
    for symbol, (ordered, booked, _stop) in _FILLS.items():
        _fill(repository, f"buy-{symbol}", ordered)
        # The repository books a crypto BUY net of the taker-fee reserve.
        assert abs(repository.owned_quantities("acct")[symbol] - booked) < 1e-15
    for symbol, quantity in exits.items():
        stop_id = f"parayabuck-stop-{symbol}"
        repository.record_protective_order("acct", stop_id, symbol, Market.CRYPTO, quantity, 95.0)
        _fill(repository, stop_id, quantity)
    return repository


def _flat_broker(repository: PortfolioStateRepository) -> Any:
    """Reconciliation-side broker holding no position and no open order."""
    return SimpleNamespace(
        account=lambda: AccountSnapshot(cash=100_000, equity=100_000, peak_equity=100_000, daily_pnl=0, positions=()),
        account_id="acct",
        _client=SimpleNamespace(get_account=lambda: SimpleNamespace(id="acct")),
        _client_adapter=SimpleNamespace(request=lambda *_args, **_kwargs: []),
        _account_id="acct",
        _get_open_orders=lambda _request: (),
        _reconcile_stale_portfolio_ownership=lambda _symbol, _market: False,
        portfolio_open_orders=lambda: repository.open_orders("acct"),
        portfolio_owned_quantities=lambda: repository.owned_quantities("acct"),
        portfolio_flat_reconciled=lambda symbol, market: repository.clear_stale_ownership("acct", symbol, market),
        manage_external_positions=False,
        ledger=None,
    )


def test_sub_precision_crypto_residue_is_not_owned() -> None:
    rows = [
        (symbol, side, qty)
        for symbol, (_o, filled, stop) in _FILLS.items()
        for side, qty in (("buy", filled), ("sell", stop))
    ]
    assert net_owned_quantities(rows, [], crypto_symbols=set(_FILLS)) == {}
    # Equity keeps the existing near-zero threshold unchanged.
    assert set(net_owned_quantities(rows, [])) == set(_FILLS)


def test_crypto_exit_dust_reconciles_clean_without_stale_adjustment(tmp_path) -> None:
    repository = _crypto_round_trip(tmp_path, {symbol: stop for symbol, (_o, _f, stop) in _FILLS.items()})
    assert repository.owned_quantities("acct") == {}

    report = ReconciliationService(_flat_broker(repository)).reconcile()

    assert not any(report.values()), report
    assert repository.ownership_adjustments("acct") == ()


def test_real_crypto_remainder_still_drifts(tmp_path) -> None:
    # Exit sells all but 0.0125 ETH (and a remainder of just over the floor for BTC).
    exits = {"ETH/USD": 0.9, "BTC/USD": 0.0290612996625 - 2e-8}
    repository = _crypto_round_trip(tmp_path, exits)
    owned = repository.owned_quantities("acct")
    assert set(owned) == {"ETH/USD", "BTC/USD"}

    report = ReconciliationService(_flat_broker(repository)).reconcile()

    assert report["LOCAL_STALE"] == ["BTC/USD", "ETH/USD"]
    assert report["MISSING_BROKER_ORDER"] == ["BTC/USD", "ETH/USD"]
