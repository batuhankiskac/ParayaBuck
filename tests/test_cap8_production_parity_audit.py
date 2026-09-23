"""Audit-only observations of current PAPER/LIVE behavior, using no broker network."""

from __future__ import annotations

from datetime import UTC, datetime

from parayabuck.core.models import AccountSnapshot, Market, PositionSnapshot
from parayabuck.execution.portfolio import (
    PortfolioOrderRequest,
    PortfolioOrderSide,
    PortfolioRiskLimits,
    PortfolioRiskOverlay,
    reserve_opening_order_slots,
)
from parayabuck.portfolio.models import TargetPortfolio, TargetWeight
from tests.test_two_phase_rebalance import _bars, _position, _runtime, _SimBroker

NAMES = ("AAPL", "MSFT", "NVDA", "AMZN", "META", "GOOG")


def _over_limit_broker() -> _SimBroker:
    # Six managed equities at 45% plus a manual crypto lot at 15.5%.
    # Equity = 100,000 and cash = 39.5% in the synthetic broker.
    positions = [_position(name, 75) for name in NAMES]
    positions.append(
        PositionSnapshot(
            "BTC/USD",
            155,
            100,
            100,
            Market.CRYPTO,
            qty_available=155,
            ownership="external",
            managed_qty=0,
        )
    )
    return _SimBroker(tuple(positions))


def _decide(tmp_path, targets: dict[str, float], cap: int = 8):
    broker = _over_limit_broker()
    limits = PortfolioRiskLimits(
        maximum_gross=0.60,
        maximum_equity=0.60,
        maximum_crypto=0.10,
        minimum_cash=0.40,
        maximum_positions=cap,
        maximum_crypto_positions=2,
    )
    runtime, repository = _runtime(tmp_path, broker, targets, limits=limits)
    runtime.maximum_allocation = 0.55  # production equity sleeve setting
    result = runtime.process_closed_bars(_bars(tuple(targets)))
    return broker, runtime, repository, result


def test_case1_over_limit_new_symbol_buy_is_submitted(tmp_path) -> None:
    targets = {**{name: 0.071 for name in NAMES}, "TSLA": 0.019}
    broker, _, _, result = _decide(tmp_path, targets)
    assert result.risk is not None and result.risk.gross_before == 0.605
    assert result.risk.gross_after <= 0.60 + 1e-12
    assert [(o.side, o.symbol) for o in broker.submitted] == [(PortfolioOrderSide.BUY, "TSLA")]
    assert broker.submitted[0].quantity * 100 / 100_000 == 0.019


def test_case2_over_limit_add_to_held_buy_is_submitted(tmp_path) -> None:
    targets = {**{name: 0.071 for name in NAMES[1:]}, NAMES[0]: 0.09}
    broker, _, _, result = _decide(tmp_path, targets)
    assert result.status == "submitted"
    assert [(o.side, o.symbol) for o in broker.submitted] == [(PortfolioOrderSide.BUY, "AAPL")]


def test_case3_over_limit_sell_is_submitted(tmp_path) -> None:
    targets = {**{name: 0.071 for name in NAMES[1:]}, NAMES[0]: 0.04}
    broker, _, _, result = _decide(tmp_path, targets)
    assert result.status == "submitted"
    assert [(o.side, o.symbol) for o in broker.submitted] == [(PortfolioOrderSide.SELL, "AAPL")]


def test_case4_replacement_defers_buy_until_sell_terminal(tmp_path) -> None:
    targets = {**{name: 0.071 for name in NAMES[1:]}, NAMES[0]: 0, "TSLA": 0.09}
    broker, _, _, result = _decide(tmp_path, targets)
    assert result.status == "sell_phase_submitted"
    assert [(o.side, o.symbol) for o in broker.submitted] == [(PortfolioOrderSide.SELL, "AAPL")]
    assert any(o.side == PortfolioOrderSide.BUY for o in result.orders)


def test_case5_external_position_consumes_slot_and_gross(tmp_path) -> None:
    targets = {**{name: 0.071 for name in NAMES}, "TSLA": 0.019}
    broker, _, _, result = _decide(tmp_path, targets, cap=6)
    assert result.risk is not None and result.risk.gross_before == 0.605
    assert "position_count_limit" in result.risk.reasons
    assert all(o.side != PortfolioOrderSide.BUY for o in broker.submitted)


def test_eight_claims_include_manual_and_working_orders() -> None:
    broker = _over_limit_broker()
    account = broker.account()
    opening = PortfolioOrderRequest(
        client_order_id="audit-new",
        decision_id="audit",
        symbol="TSLA",
        market=Market.EQUITY,
        side=PortfolioOrderSide.BUY,
        quantity=1,
        reference_price=100,
        decision_timestamp=datetime(2025, 1, 1, tzinfo=UTC),
        strategy_name="audit",
        strategy_version="1",
        risk_reducing=False,
    )
    # Seven positions plus a working new-name reservation occupy all eight.
    assert (
        reserve_opening_order_slots(
            (opening,),
            account,
            maximum_positions=8,
            reserved_position_keys=((Market.EQUITY, "PENDING"),),
        )
        == ()
    )
    assert reserve_opening_order_slots((opening,), account, maximum_positions=8) == (opening,)


def test_cap8_does_not_raise_crypto_count_or_target_gross() -> None:
    equity = tuple(_position(name, 550 / 6) for name in NAMES)
    account = AccountSnapshot(
        cash=45_000,
        equity=100_000,
        peak_equity=100_000,
        daily_pnl=0,
        positions=equity,
    )
    target = TargetPortfolio(
        strategy_name="audit_crypto",
        strategy_version="1",
        decision_timestamp=datetime(2025, 1, 1, tzinfo=UTC),
        weights=tuple(TargetWeight(name, 0.05) for name in ("BTC/USD", "ETH/USD", "LTC/USD")),
        cash_weight=0.85,
    )
    limits = PortfolioRiskLimits(
        maximum_gross=0.60,
        maximum_equity=0.60,
        maximum_crypto=0.10,
        maximum_crypto_asset=0.05,
        minimum_cash=0.40,
        maximum_positions=8,
        maximum_crypto_positions=2,
    )
    result = PortfolioRiskOverlay(limits).apply(
        target,
        account,
        market=Market.CRYPTO,
        approved_symbols=("BTC/USD", "ETH/USD", "LTC/USD"),
        maximum_allocation=0.05,
        mode="paper",
    )
    assert len(result.target.weights) == 2
    assert all(weight.weight <= 0.05 for weight in result.target.weights)
    assert result.gross_after <= 0.60 + 1e-12
