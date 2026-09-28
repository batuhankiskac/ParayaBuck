"""A crypto exit leaving sub-precision fee-net dust is flat, not owned drift.

Numbers from the 2026-09-25 PAPER incident: fills are booked fee-net at full
float precision while the protective stops sold the broker's 9-decimal
position, leaving 4.375e-10 ETH and 1.66e-9 BTC of phantom ownership.
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest

from parayabuck.core.models import AccountSnapshot, Market
from parayabuck.execution.alpaca_reconciliation import ReconciliationService
from parayabuck.execution.portfolio import PortfolioOrderRequest, PortfolioOrderSide, RiskOverlayDecision
from parayabuck.portfolio.models import TargetPortfolio, TargetWeight
from parayabuck.portfolio.runtime_ownership import (
    CRYPTO_DUST_CLEAR_KIND,
    CRYPTO_DUST_CREDIT_KIND,
    CRYPTO_OWNERSHIP_DUST,
    net_owned_quantities,
)
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


def _cycle(
    repository: PortfolioStateRepository,
    session: str,
    cycle: int,
    symbols: tuple[str, ...],
    stops: dict[str, float] | None = None,
) -> None:
    """One incident-shaped round trip per symbol: fee-net BUY, stop sells the broker's 9 decimals."""
    timestamp = _NOW + timedelta(days=cycle)
    target = TargetPortfolio(
        "fixed", "1", timestamp, tuple(TargetWeight(symbol, 0.1) for symbol in symbols), 0.8, universe_version="test"
    )
    buys = tuple(
        PortfolioOrderRequest(
            client_order_id=f"buy-{cycle}-{symbol}",
            decision_id="",
            symbol=symbol,
            market=Market.CRYPTO,
            side=PortfolioOrderSide.BUY,
            quantity=_FILLS[symbol][0],
            reference_price=100.0,
            decision_timestamp=timestamp,
            strategy_name="fixed",
            strategy_version="1",
            risk_reducing=False,
        )
        for symbol in symbols
    )
    risk = RiskOverlayDecision(target, False, (), 0.2, 0.2)
    repository.record_decision_with_orders(session, "acct", "digest", target, risk, Market.CRYPTO, buys)
    for symbol in symbols:
        ordered, _booked, stop = _FILLS[symbol]
        stop = (stops or {}).get(symbol, stop)
        _fill(repository, f"buy-{cycle}-{symbol}", ordered)
        stop_id = f"parayabuck-stop-{cycle}-{symbol}"
        repository.record_protective_order("acct", stop_id, symbol, Market.CRYPTO, stop, 95.0)
        _fill(repository, stop_id, stop)


def _stale_after_each_cycle(
    repository: PortfolioStateRepository, session: str, cycles: range, symbols: tuple[str, ...] = tuple(_FILLS)
) -> list[list[str]]:
    stale = []
    for cycle in cycles:
        _cycle(repository, session, cycle, symbols)
        stale.append(ReconciliationService(_flat_broker(repository)).reconcile()["LOCAL_STALE"])
    return stale


def _raw_net(repository: PortfolioStateRepository, symbol: str) -> float:
    rows = repository.store.connection.execute(
        "SELECT side, cumulative_filled_quantity FROM portfolio_orders WHERE symbol = ?", (symbol,)
    ).fetchall()
    adjusted = repository.store.connection.execute(
        "SELECT COALESCE(SUM(CASE WHEN kind = ? THEN consumed_quantity - quantity"
        " ELSE quantity - consumed_quantity END), 0) FROM portfolio_ownership_adjustments WHERE symbol = ?",
        (CRYPTO_DUST_CREDIT_KIND, symbol),
    ).fetchone()[0]
    return sum(qty if side == "buy" else -qty for side, qty in rows) - adjusted


def _dust_clears(repository: PortfolioStateRepository, kind: str = CRYPTO_DUST_CLEAR_KIND) -> int:
    return repository.store.connection.execute(
        "SELECT COUNT(*) FROM portfolio_ownership_adjustments WHERE kind = ?", (kind,)
    ).fetchone()[0]


def test_thirty_crypto_round_trips_never_accumulate_drift(tmp_path) -> None:
    repository = PortfolioStateRepository(tmp_path / "runtime.sqlite3")
    session = repository.begin_session("acct", "digest", "paper")

    stale = _stale_after_each_cycle(repository, session, range(30))

    assert stale == [[]] * 30
    assert repository.owned_quantities("acct") == {}
    for symbol in _FILLS:
        assert abs(_raw_net(repository, symbol)) < 1e-15
    assert _dust_clears(repository) == 60
    # Dust clears are bookkeeping, not operator-facing anomalies.
    assert repository.ownership_adjustments("acct") == ()


def test_crypto_dust_clear_is_idempotent_on_fill_redelivery(tmp_path) -> None:
    repository = PortfolioStateRepository(tmp_path / "runtime.sqlite3")
    session = repository.begin_session("acct", "digest", "paper")
    _cycle(repository, session, 0, ("BTC/USD",))
    assert _dust_clears(repository) == 1

    _fill(repository, "parayabuck-stop-0-BTC/USD", _FILLS["BTC/USD"][2])

    assert _dust_clears(repository) == 1
    assert abs(_raw_net(repository, "BTC/USD")) < 1e-15
    assert repository.owned_quantities("acct") == {}


def test_legacy_residue_at_or_below_floor_is_cleared_by_next_sell(tmp_path, monkeypatch) -> None:
    repository = PortfolioStateRepository(tmp_path / "runtime.sqlite3")
    session = repository.begin_session("acct", "digest", "paper")
    # Pre-fix ledger: five BTC trips accumulate ~8.3e-9 of hidden residue.
    monkeypatch.setattr(repository, "_clear_crypto_exit_dust_tx", lambda *_args: None)
    assert _stale_after_each_cycle(repository, session, range(5), ("BTC/USD",)) == [[]] * 5
    assert 0 < _raw_net(repository, "BTC/USD") < CRYPTO_OWNERSHIP_DUST
    monkeypatch.undo()

    assert _stale_after_each_cycle(repository, session, range(5, 35), ("BTC/USD",)) == [[]] * 30
    assert abs(_raw_net(repository, "BTC/USD")) < 1e-15


def test_legacy_residue_above_floor_stops_cascading_after_one_clear(tmp_path, monkeypatch) -> None:
    repository = PortfolioStateRepository(tmp_path / "runtime.sqlite3")
    session = repository.begin_session("acct", "digest", "paper")
    # Pre-fix ledger: residue crossed the floor and every exit re-drifted,
    # each time leaving a consumable stale-clear adjustment behind.
    monkeypatch.setattr(repository, "_clear_crypto_exit_dust_tx", lambda *_args: None)
    monkeypatch.setattr("parayabuck.portfolio.runtime_repository.CRYPTO_STALE_DUST_BAND", 0.0)
    legacy = _stale_after_each_cycle(repository, session, range(8), ("BTC/USD",))
    assert legacy[-2:] == [["BTC/USD"], ["BTC/USD"]]
    monkeypatch.undo()

    stale = _stale_after_each_cycle(repository, session, range(8, 38), ("BTC/USD",))

    # The legacy consumable clear is consumed by the next exit once more; the
    # re-clear is then non-consumable and every later exit is clean.
    assert stale == [["BTC/USD"]] + [[]] * 29
    assert repository.owned_quantities("acct") == {}
    assert repository.ownership_adjustments("acct") == ()


def test_equity_residue_keeps_existing_ownership_semantics(tmp_path) -> None:
    repository = PortfolioStateRepository(tmp_path / "runtime.sqlite3")
    session = repository.begin_session("acct", "digest", "paper")
    target = TargetPortfolio("fixed", "1", _NOW, (TargetWeight("SPY", 0.1),), 0.8, universe_version="test")
    buy = PortfolioOrderRequest(
        client_order_id="buy-SPY",
        decision_id="",
        symbol="SPY",
        market=Market.EQUITY,
        side=PortfolioOrderSide.BUY,
        quantity=1.5,
        reference_price=100.0,
        decision_timestamp=_NOW,
        strategy_name="fixed",
        strategy_version="1",
        risk_reducing=False,
    )
    repository.record_decision_with_orders(
        session, "acct", "digest", target, RiskOverlayDecision(target, False, (), 0.2, 0.2), Market.EQUITY, (buy,)
    )
    _fill(repository, "buy-SPY", 1.5)
    repository.record_protective_order("acct", "parayabuck-stop-SPY", "SPY", Market.EQUITY, 1.5 - 5e-9, 95.0)
    _fill(repository, "parayabuck-stop-SPY", 1.5 - 5e-9)

    assert _dust_clears(repository) == 0
    assert set(repository.owned_quantities("acct")) == {"SPY"}
    assert repository.clear_stale_ownership("acct", "SPY", Market.EQUITY) > 0
    assert [row["kind"] for row in repository.ownership_adjustments("acct")] == ["stale_ownership_clear"]


def test_trailing_sell_after_dust_clears_consumes_exact_shortfall(tmp_path) -> None:
    repository = PortfolioStateRepository(tmp_path / "runtime.sqlite3")
    session = repository.begin_session("acct", "digest", "paper")
    # Four BTC exits book ~6.65e-9 of dust clears.
    assert _stale_after_each_cycle(repository, session, range(4), ("BTC/USD",)) == [[]] * 4
    stop = _FILLS["BTC/USD"][2]

    # A trailing 5e-9 SELL delta on the last stop would otherwise go negative.
    _fill(repository, "parayabuck-stop-3-BTC/USD", stop + 5e-9)

    assert repository.owned_quantities("acct") == {}
    assert abs(_raw_net(repository, "BTC/USD")) < 1e-15
    assert ReconciliationService(_flat_broker(repository)).reconcile()["LOCAL_STALE"] == []


def test_late_sell_after_band_stale_clear_nets_to_zero(tmp_path) -> None:
    exits = {"ETH/USD": _FILLS["ETH/USD"][2], "BTC/USD": 0.0290612996625 - 5e-7}
    repository = _crypto_round_trip(tmp_path, exits)
    assert ReconciliationService(_flat_broker(repository)).reconcile()["LOCAL_STALE"] == ["BTC/USD"]
    assert _dust_clears(repository) == 2  # ETH exit dust + the 5e-7 BTC band stale-clear

    _fill(repository, "parayabuck-stop-BTC/USD", 0.0290612996625)

    assert repository.owned_quantities("acct") == {}
    assert abs(_raw_net(repository, "BTC/USD")) < 1e-15


@pytest.mark.parametrize("remainder", [5e-7, 0.01], ids=["band-dust-clear", "stale-clear"])
def test_late_sell_racing_the_stale_clear_never_goes_negative(tmp_path, remainder) -> None:
    booked = _FILLS["BTC/USD"][1]
    repository = _crypto_round_trip(tmp_path, {"ETH/USD": _FILLS["ETH/USD"][2], "BTC/USD": booked - remainder})
    original_owned = repository.owned_quantities
    late_fill = threading.Thread(target=_fill, args=(repository, "parayabuck-stop-BTC/USD", booked))

    def owned_then_late_fill(*args: Any, **kwargs: Any) -> dict[str, float]:
        owned = original_owned(*args, **kwargs)
        if not late_fill.is_alive() and late_fill.ident is None:
            # The trade-update thread books the late final fill right after
            # the reconcile thread read the pre-fill ownership.
            late_fill.start()
            late_fill.join(timeout=0.3)
        return owned

    repository.owned_quantities = owned_then_late_fill  # type: ignore[method-assign]
    assert repository.clear_stale_ownership("acct", "BTC/USD", Market.CRYPTO) > 0
    late_fill.join(timeout=10)
    assert not late_fill.is_alive()
    repository.owned_quantities = original_owned  # type: ignore[method-assign]

    assert repository.owned_quantities("acct") == {}
    assert abs(_raw_net(repository, "BTC/USD")) < 1e-15


def _overshoot_stops(overshoot: dict[str, float]) -> dict[str, float]:
    """Stops that sell ``overshoot`` above the fee-net booked quantity."""
    return {symbol: _FILLS[symbol][1] + delta for symbol, delta in overshoot.items()}


def _stale_with_stops(
    repository: PortfolioStateRepository, session: str, stops_per_cycle: list[dict[str, float]]
) -> list[list[str]]:
    stale = []
    for cycle, stops in enumerate(stops_per_cycle):
        _cycle(repository, session, cycle, tuple(stops), stops)
        stale.append(ReconciliationService(_flat_broker(repository)).reconcile()["LOCAL_STALE"])
    return stale


def test_crypto_exit_selling_above_booked_quantity_never_accumulates(tmp_path) -> None:
    repository = PortfolioStateRepository(tmp_path / "runtime.sqlite3")
    session = repository.begin_session("acct", "digest", "paper")

    stale = _stale_with_stops(repository, session, [_overshoot_stops({"BTC/USD": 6e-10})] * 30)

    assert stale == [[]] * 30
    assert repository.owned_quantities("acct") == {}
    assert abs(_raw_net(repository, "BTC/USD")) < 1e-15
    assert _dust_clears(repository, CRYPTO_DUST_CREDIT_KIND) == 30
    assert repository.ownership_adjustments("acct") == ()


def test_mixed_sign_crypto_residues_stay_flat(tmp_path) -> None:
    repository = PortfolioStateRepository(tmp_path / "runtime.sqlite3")
    session = repository.begin_session("acct", "digest", "paper")
    deltas = (-1.7e-9, 6e-10, -4.4e-10, 9e-10, 3e-9, -9e-9, 8e-9)
    plan = [
        _overshoot_stops({"BTC/USD": deltas[cycle % len(deltas)], "ETH/USD": deltas[(cycle + 3) % len(deltas)]})
        for cycle in range(30)
    ]

    stale = _stale_with_stops(repository, session, plan)

    assert stale == [[]] * 30
    assert repository.owned_quantities("acct") == {}
    for symbol in _FILLS:
        assert abs(_raw_net(repository, symbol)) < 1e-15
    assert _dust_clears(repository, CRYPTO_DUST_CREDIT_KIND) > 0
    assert repository.ownership_adjustments("acct") == ()


def test_real_crypto_oversell_still_trips_the_invariant(tmp_path) -> None:
    repository = PortfolioStateRepository(tmp_path / "runtime.sqlite3")
    session = repository.begin_session("acct", "digest", "paper")

    _cycle(repository, session, 0, ("BTC/USD",), _overshoot_stops({"BTC/USD": 1e-6}))

    assert _dust_clears(repository, CRYPTO_DUST_CREDIT_KIND) == 0
    with pytest.raises(RuntimeError, match="Negative net portfolio ownership"):
        repository.owned_quantities("acct")


def test_crypto_dust_credit_is_idempotent_on_fill_redelivery(tmp_path) -> None:
    repository = PortfolioStateRepository(tmp_path / "runtime.sqlite3")
    session = repository.begin_session("acct", "digest", "paper")
    stops = _overshoot_stops({"BTC/USD": 6e-10})
    _cycle(repository, session, 0, ("BTC/USD",), stops)
    assert _dust_clears(repository, CRYPTO_DUST_CREDIT_KIND) == 1

    _fill(repository, "parayabuck-stop-0-BTC/USD", stops["BTC/USD"])

    assert _dust_clears(repository, CRYPTO_DUST_CREDIT_KIND) == 1
    assert abs(_raw_net(repository, "BTC/USD")) < 1e-15
    assert repository.owned_quantities("acct") == {}


def test_dust_credit_does_not_distort_a_later_position(tmp_path) -> None:
    repository = PortfolioStateRepository(tmp_path / "runtime.sqlite3")
    session = repository.begin_session("acct", "digest", "paper")
    _cycle(repository, session, 0, ("BTC/USD",), _overshoot_stops({"BTC/USD": 6e-10}))
    target = TargetPortfolio("fixed", "1", _NOW, (TargetWeight("BTC/USD", 0.1),), 0.8, universe_version="test")
    buy = PortfolioOrderRequest(
        client_order_id="buy-later-BTC/USD",
        decision_id="",
        symbol="BTC/USD",
        market=Market.CRYPTO,
        side=PortfolioOrderSide.BUY,
        quantity=_FILLS["BTC/USD"][0],
        reference_price=100.0,
        decision_timestamp=_NOW + timedelta(days=5),
        strategy_name="fixed",
        strategy_version="1",
        risk_reducing=False,
    )
    repository.record_decision_with_orders(
        session, "acct", "digest", target, RiskOverlayDecision(target, False, (), 0.2, 0.2), Market.CRYPTO, (buy,)
    )
    _fill(repository, "buy-later-BTC/USD", _FILLS["BTC/USD"][0])

    assert abs(repository.owned_quantities("acct")["BTC/USD"] - _FILLS["BTC/USD"][1]) < 1e-15


def test_equity_oversell_books_no_dust_credit(tmp_path) -> None:
    repository = PortfolioStateRepository(tmp_path / "runtime.sqlite3")
    session = repository.begin_session("acct", "digest", "paper")
    target = TargetPortfolio("fixed", "1", _NOW, (TargetWeight("SPY", 0.1),), 0.8, universe_version="test")
    buy = PortfolioOrderRequest(
        client_order_id="buy-SPY",
        decision_id="",
        symbol="SPY",
        market=Market.EQUITY,
        side=PortfolioOrderSide.BUY,
        quantity=1.5,
        reference_price=100.0,
        decision_timestamp=_NOW,
        strategy_name="fixed",
        strategy_version="1",
        risk_reducing=False,
    )
    repository.record_decision_with_orders(
        session, "acct", "digest", target, RiskOverlayDecision(target, False, (), 0.2, 0.2), Market.EQUITY, (buy,)
    )
    _fill(repository, "buy-SPY", 1.5)
    repository.record_protective_order("acct", "parayabuck-stop-SPY", "SPY", Market.EQUITY, 1.5 + 5e-9, 95.0)
    _fill(repository, "parayabuck-stop-SPY", 1.5 + 5e-9)

    assert _dust_clears(repository, CRYPTO_DUST_CREDIT_KIND) == 0
    with pytest.raises(RuntimeError, match="Negative net portfolio ownership"):
        repository.owned_quantities("acct")
