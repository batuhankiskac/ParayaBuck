"""Regression tests for the audit wave 2 precision and FIFO findings (worker I2).

T2-4-1 cash-flow peak, T2-4-2 realized P&L on the matched shares, T2-4-3 one
share-count band, T2-4-5 unpriced BUY, T2-4-6 non-finite order facts,
T2-4-7 fill completeness band, F2-4-2 duplicate fill activity,
F2-4-4 buy-date pairing with acquired lots. Synthetic fixtures and tmp_path
only; no network, no credentials, no production paths.
"""

from __future__ import annotations

import math
from datetime import UTC, date, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from parayabuck.core.models import Market
from parayabuck.execution import alpaca_account
from parayabuck.execution.alpaca_models import DefiniteOrderRejection
from parayabuck.execution.alpaca_orders import OrderService
from parayabuck.execution.portfolio import PortfolioOrderRequest, PortfolioOrderSide, RiskOverlayDecision
from parayabuck.execution.telemetry import expected_vs_actual
from parayabuck.portfolio.models import TargetPortfolio
from parayabuck.portfolio.ownership_rebase import (
    ACQUIRE,
    BUY,
    MATCH,
    LotBookInconsistency,
    _replay_lots,
    classify_share_change,
)
from parayabuck.portfolio.runtime_repository import PortfolioStateRepository
from parayabuck.portfolio.runtime_service import PortfolioRuntime
from tests.fake_client import FakeTradingClient
from tests.helpers import make_broker

PRIOR_DAY = date(2026, 10, 26)
TODAY = date(2026, 10, 27)
_AT = datetime(2026, 10, 27, 15, tzinfo=UTC)


# --- shared fixtures (T2-4-1) ------------------------------------------------


@pytest.fixture
def alerts(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    captured: list[dict[str, Any]] = []

    def record(kind: str, *, message: str, **fields: object) -> None:
        captured.append({"kind": kind, "message": message, **fields})

    monkeypatch.setattr(alpaca_account, "emit_execution_alert", record)
    return captured


@pytest.fixture
def trading_day(monkeypatch: pytest.MonkeyPatch) -> dict[str, date]:
    """The New York trading day the account snapshot belongs to; mutable."""
    day = {"value": PRIOR_DAY}
    monkeypatch.setattr(alpaca_account, "_trading_day", lambda now: day["value"])
    return day


def _account(equity: float, last_equity: float) -> SimpleNamespace:
    return SimpleNamespace(
        id="fake-acct-1234",
        account_number="PA-TEST-0001",
        account_blocked=False,
        trading_blocked=False,
        trade_suspended_by_user=False,
        equity=str(equity),
        last_equity=str(last_equity),
        cash="90000",
        status="ACTIVE",
        non_marginable_buying_power=None,
    )


class _ActivityClient(FakeTradingClient):
    """Fake client that serves a scripted activities list and can fail it."""

    def __init__(self) -> None:
        super().__init__()
        self.rows: list[dict[str, Any]] = []
        self.activity_error: Exception | None = None

    def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        if path == "/account/activities":
            if self.activity_error is not None:
                raise self.activity_error
            return list(self.rows)
        return super().get(path, params)


def _broker(tmp_path: Path, client: Any) -> Any:
    broker = make_broker(
        tmp_path,
        client=client,
        portfolio_owned_quantities=lambda: {},
        portfolio_open_orders=list,
    )
    broker.bot_tradable_symbols = lambda: {"QLD"}
    broker.adjust_for_cash_flows = True
    return broker


def _deposit(day: str, amount: str, activity_id: str = "d1") -> dict[str, str]:
    return {"id": activity_id, "activity_type": "CSD", "date": day, "net_amount": amount}


# --- T2-4-1: the peak is kept on equity net of every external flow ----------


def test_flow_totals_split_the_history_before_today_from_today(tmp_path: Path, alerts: list) -> None:
    client = _ActivityClient()
    client.rows = [
        _deposit("2026-10-26", "5000", "a1"),
        {"id": "a2", "activity_type": "CSW", "date": "2026-10-27", "net_amount": "-1200"},
        {"id": "a3", "activity_type": "JNLC", "date": "2026-10-27", "net_amount": "100"},
        _deposit("2026-10-28", "9999", "a4"),  # after the day: in neither total
    ]
    before, on_day = alpaca_account._read_external_flow_totals(client, TODAY)
    assert before == pytest.approx(5000.0)
    assert on_day == pytest.approx(-1100.0)


def test_a_malformed_flow_fails_the_whole_totals_read(tmp_path: Path, alerts: list) -> None:
    client = _ActivityClient()
    client.rows = [_deposit("2026-10-26", "5000", "a1"), _deposit("2026-10-27", "nan", "a2")]
    with pytest.raises(ValueError, match="non-finite"):
        alpaca_account._read_external_flow_totals(client, TODAY)


def test_deposit_settled_on_an_earlier_day_is_not_a_gain(
    tmp_path: Path, alerts: list, trading_day: dict[str, date]
) -> None:
    client = _ActivityClient()
    client.script_get_account = lambda: _account(100000, 100000)
    broker = _broker(tmp_path, client)
    broker.account()  # prior day: peak 100000, no flows
    assert broker._peak_equity == pytest.approx(100000.0)

    # Next day: the deposit was made yesterday and is in last_equity.
    trading_day["value"] = TODAY
    client.rows = [_deposit("2026-10-26", "5000")]
    client.script_get_account = lambda: _account(105000, 105000)
    snapshot = broker.account()

    assert broker._peak_equity == pytest.approx(100000.0)  # the transfer never raises the peak
    assert snapshot.drawdown_pct == pytest.approx(0.0)
    assert snapshot.daily_pnl == pytest.approx(0.0)


def test_unknown_flows_never_raise_the_peak(tmp_path: Path, alerts: list, trading_day: dict[str, date]) -> None:
    client = _ActivityClient()
    client.script_get_account = lambda: _account(100000, 100000)
    broker = _broker(tmp_path, client)
    broker.account()  # peak 100000, no flows

    # Equity rises above the peak, but the history can no longer be read.
    client.activity_error = RuntimeError("activities endpoint down")
    client.script_get_account = lambda: _account(110000, 100000)
    broker._account_service._cash_flow_cache = None  # force a read on this poll
    snapshot = broker.account()

    assert broker._peak_equity == pytest.approx(100000.0)  # a possible deposit is not a gain
    assert snapshot.daily_pnl == pytest.approx(10000.0)  # the unadjusted day P&L, as before


def test_deposit_posted_inside_the_cache_window_does_not_raise_the_peak(
    tmp_path: Path, alerts: list, trading_day: dict[str, date]
) -> None:
    trading_day["value"] = TODAY
    client = _ActivityClient()
    client.script_get_account = lambda: _account(100000, 100000)
    broker = _broker(tmp_path, client)
    broker.account()  # cached: no flows yet today

    # The deposit posts inside the cache window and equity jumps above the peak.
    client.rows = [_deposit("2026-10-27", "10000", "d-today")]
    client.script_get_account = lambda: _account(110000, 100000)
    snapshot = broker.account()

    assert broker._peak_equity == pytest.approx(100000.0)  # confirmed fresh before any raise
    assert snapshot.daily_pnl == pytest.approx(0.0)
    assert snapshot.drawdown_pct == pytest.approx(0.0)


# --- T2-4-2: realized P&L costs only the matched shares ----------------------


class _ReplayRepo:
    """Stands in for the repository's replay source; only the events matter."""

    def __init__(self, events: list[tuple[str, float, float | None]]) -> None:
        self._events = events

    def _fifo_replay_events(self, *args: Any, **kwargs: Any) -> list[tuple[str, float, float | None]]:
        return list(self._events)


def test_noise_excess_past_the_lots_is_left_out_of_the_sale() -> None:
    repo = _ReplayRepo([(BUY, 1000.0, 100.0)])
    # 1e-4 shares past the lots: inside the relative band, so it is noise.
    pnl = PortfolioStateRepository.fifo_realized_pnl(repo, "acct", "QLD", Market.EQUITY, 1000.0001, 110.0, 1.0)
    # 1000 matched shares: 110000 - 100000 - the fee on the matched fraction only.
    assert pnl == pytest.approx(9999.0, abs=1e-4)


def test_excess_past_the_band_still_books_zero() -> None:
    repo = _ReplayRepo([(BUY, 1000.0, 100.0)])
    pnl = PortfolioStateRepository.fifo_realized_pnl(repo, "acct", "QLD", Market.EQUITY, 1001.0, 110.0, 1.0)
    assert pnl == pytest.approx(0.0)


# --- T2-4-3: one share-count band in the classifier --------------------------


def test_dust_difference_inside_the_shared_floor_is_a_match() -> None:
    # A 1e-9 difference is 9-decimal broker noise on a dust holding; the shared
    # floor (1e-8) classifies it as the same share count.
    assert classify_share_change(1e-9, 2e-9).kind == MATCH


# --- T2-4-5: an unpriced BUY refuses instead of dropping its lots -----------


@pytest.mark.parametrize("price", [0.0, -1.0, math.nan, math.inf, None])
def test_buy_without_a_usable_price_raises(price: float | None) -> None:
    with pytest.raises(LotBookInconsistency, match="buy needs a finite positive unit cost"):
        _replay_lots([(BUY, 5.0, price)])


def test_buy_with_no_shares_is_skipped_without_a_price() -> None:
    lots, quantity, cost = _replay_lots([(BUY, 0.0, None)])
    assert lots == [] and quantity == 0.0 and cost == 0.0


# --- T2-4-6: non-finite order facts are refused before any floor -------------


def _portfolio_order(quantity: float, reference_price: float) -> SimpleNamespace:
    return SimpleNamespace(
        client_order_id="i2-buy",
        symbol="QLD",
        market=Market.EQUITY,
        side="buy",
        quantity=quantity,
        reference_price=reference_price,
    )


def _order_service() -> OrderService:
    service = OrderService.__new__(OrderService)
    service.asset_trading_rules = lambda symbol, market: SimpleNamespace(  # type: ignore[method-assign]
        quantity_increment=1e-9,
        minimum_order_size=0.0,
        minimum_order_notional=10.0,
    )
    return service


@pytest.mark.parametrize(("quantity", "reference_price"), [(math.nan, 100.0), (1.0, math.inf), (1.0, math.nan)])
def test_non_finite_order_facts_are_refused_before_submit(quantity: float, reference_price: float) -> None:
    with pytest.raises(DefiniteOrderRejection, match="non-finite"):
        _order_service().submit_portfolio_order(_portfolio_order(quantity, reference_price))  # type: ignore[arg-type]


def test_finite_order_below_the_notional_floor_is_still_refused_by_the_floor() -> None:
    with pytest.raises(DefiniteOrderRejection, match="below minimum notional"):
        _order_service().submit_portfolio_order(_portfolio_order(1.0, 5.0))  # type: ignore[arg-type]


# --- T2-4-7: the fill completeness band -------------------------------------


def _filled_order(quantity: float) -> dict[str, object]:
    return {
        "client_order_id": "i2-fill",
        "symbol": "QLD",
        "market": "equity",
        "side": "buy",
        "quantity": quantity,
        "reference_price": 100.0,
        "created_at": "2026-10-27T14:00:00+00:00",
        "status": "filled",
    }


def _fill(quantity: float) -> dict[str, object]:
    return {"quantity": quantity, "price": 100.0, "fee": 0.0, "occurred_at": "2026-10-27T14:00:01+00:00"}


def test_fill_within_the_broker_band_is_complete() -> None:
    result = expected_vs_actual(_filled_order(1.0), (_fill(1.0 - 5e-10),))
    assert result["complete"] is True


def test_fill_short_of_the_band_is_incomplete() -> None:
    result = expected_vs_actual(_filled_order(1.0), (_fill(1.0 - 1e-3),))
    assert result["complete"] is False


# --- F2-4-2: a fill activity already booked refuses the update --------------


def test_duplicate_fill_activity_refuses_the_whole_update(tmp_path: Path) -> None:
    repo = PortfolioStateRepository(tmp_path / "runtime.sqlite3")
    target = TargetPortfolio("i2", "1", _AT, (), 1.0)
    session = repo.begin_session("acct", "digest", "paper")
    decision_id = repo.record_decision_with_orders(
        session, "acct", "digest", target, RiskOverlayDecision(target, False, (), 0.0, 0.0), Market.EQUITY, ()
    )
    order = PortfolioOrderRequest(
        client_order_id="i2-buy",
        decision_id=decision_id,
        symbol="QLD",
        market=Market.EQUITY,
        side=PortfolioOrderSide.BUY,
        quantity=10.0,
        reference_price=100.0,
        decision_timestamp=_AT,
        strategy_name="i2",
        strategy_version="1",
    )
    repo.record_order(order, "submitting")

    def update(status: str, quantity: float, activity: str) -> None:
        repo.apply_trade_update(
            client_order_id="i2-buy",
            broker_order_id="broker-1",
            status=status,
            cumulative_filled_quantity=quantity,
            average_fill_price=100.0,
            occurred_at=_AT,
            activity_id=activity,
            fee=None,
            realized_pnl=0.0,
            unrealized_pnl=0.0,
        )

    update("partially_filled", 5.0, "act-1")
    with pytest.raises(RuntimeError, match="already booked"):
        update("filled", 10.0, "act-1")  # same activity id, a new 5-share delta

    cumulative = repo.store.connection.execute(
        "SELECT cumulative_filled_quantity FROM portfolio_orders WHERE client_order_id = 'i2-buy'"
    ).fetchone()[0]
    assert cumulative == pytest.approx(5.0)  # rolled back with the refused fill


# --- F2-4-4: buy-date pairing counts acquired lots --------------------------


class _OwnedEventsRepo:
    def __init__(self, events: list[tuple[datetime, str, float, float | None, float]]) -> None:
        self._events = events

    def _owned_events(self, account_id: str, symbol: str, market: Market) -> list[tuple[Any, ...]]:
        return list(self._events)


def test_acquired_lot_takes_its_place_in_the_buy_date_pairing() -> None:
    repo = _OwnedEventsRepo([(datetime(2026, 10, 20, 14, tzinfo=UTC), ACQUIRE, 5.0, 100.0, 0.0)])
    runtime = SimpleNamespace(repository=repo, account_id="acct", market=Market.EQUITY)
    assert PortfolioRuntime._managed_buy_dates(runtime, "QLD", 1) == [date(2026, 10, 20)]  # type: ignore[arg-type]


def test_pairing_still_returns_none_when_buys_are_fewer_than_lots() -> None:
    repo = _OwnedEventsRepo([(datetime(2026, 10, 20, 14, tzinfo=UTC), ACQUIRE, 5.0, 100.0, 0.0)])
    runtime = SimpleNamespace(repository=repo, account_id="acct", market=Market.EQUITY)
    assert PortfolioRuntime._managed_buy_dates(runtime, "QLD", 2) is None  # type: ignore[arg-type]
