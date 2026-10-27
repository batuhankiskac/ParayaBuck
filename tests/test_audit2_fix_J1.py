"""Audit wave 2 fixes J1: stale-ownership clears, same-instant replay, reverse splits.

Each test pins one invariant with synthetic fixtures in ``tmp_path``. No network,
no credentials and no production paths. Order timestamps are forced so the
ordering rules are deterministic.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from parayabuck.core.models import Market
from parayabuck.execution.portfolio import PortfolioOrderRequest, PortfolioOrderSide, RiskOverlayDecision
from parayabuck.monitoring import alerts as alerts_module
from parayabuck.portfolio.models import TargetPortfolio
from parayabuck.portfolio.ownership_rebase import classify_share_change
from parayabuck.portfolio.runtime_repository import PortfolioStateRepository

ACCOUNT = "acct"
SYMBOL = "QLD"
ENTRY_PRICE = 50.0
T0 = datetime(2026, 9, 1, tzinfo=UTC)


def _repo(tmp_path: Path) -> PortfolioStateRepository:
    return PortfolioStateRepository(tmp_path / "runtime.sqlite3")


def _order(
    repo: PortfolioStateRepository,
    side: str,
    quantity: float,
    price: float,
    client_order_id: str,
    created_at: datetime,
) -> str:
    """Record one of our own orders (no fill yet) and pin its created_at."""
    target = TargetPortfolio("vt35", "1", created_at, (), 1.0)
    risk = RiskOverlayDecision(target, False, (), 0.0, 0.0)
    session = repo.begin_session(ACCOUNT, "digest", "paper")
    decision_id = repo.record_decision_with_orders(session, ACCOUNT, "digest", target, risk, Market.EQUITY, ())
    order = PortfolioOrderRequest(
        client_order_id=client_order_id,
        decision_id=decision_id,
        symbol=SYMBOL,
        market=Market.EQUITY,
        side=PortfolioOrderSide(side),
        quantity=quantity,
        reference_price=price,
        decision_timestamp=created_at,
        strategy_name="vt35",
        strategy_version="1",
    )
    repo.record_order(order, "submitted")
    with repo.store.lock, repo.store.connection:
        repo.store.connection.execute(
            "UPDATE portfolio_orders SET created_at = ? WHERE client_order_id = ?",
            (created_at.isoformat(), client_order_id),
        )
    return client_order_id


def _apply_fill(
    repo: PortfolioStateRepository,
    client_order_id: str,
    quantity: float,
    price: float,
    when: datetime,
) -> None:
    repo.apply_trade_update(
        client_order_id=client_order_id,
        broker_order_id=f"broker-{client_order_id}",
        status="filled",
        cumulative_filled_quantity=quantity,
        average_fill_price=price,
        occurred_at=when,
        activity_id=f"activity-{client_order_id}",
        fee=0.0,
        realized_pnl=0.0,
        unrealized_pnl=0.0,
    )


def _fill(
    repo: PortfolioStateRepository,
    side: str,
    quantity: float,
    price: float,
    created_at: datetime,
    client_order_id: str,
) -> None:
    """Record one of our own orders and its real fill at ``price``."""
    _order(repo, side, quantity, price, client_order_id, created_at)
    _apply_fill(repo, client_order_id, quantity, price, created_at)


def _owned(repo: PortfolioStateRepository) -> float:
    return repo.owned_quantities(ACCOUNT, Market.EQUITY).get(SYMBOL, 0.0)


def _adjustments(repo: PortfolioStateRepository) -> list[dict[str, object]]:
    return [dict(row) for row in repo.ownership_adjustments(ACCOUNT)]


@pytest.fixture
def emitted(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, object]]:
    alerts: list[dict[str, object]] = []
    monkeypatch.setattr(alerts_module, "emit", lambda kind, **fields: alerts.append({"kind": kind, **fields}))
    return alerts


# ---------------------------------------------------------------------------
# R2-7-1: a flat broker clears only dust or a residue a local exit explains
# ---------------------------------------------------------------------------


def test_dust_residue_still_clears_without_a_local_exit(tmp_path: Path, emitted: list[dict[str, object]]) -> None:
    repo = _repo(tmp_path)
    _fill(repo, "buy", 1e-7, ENTRY_PRICE, T0, "cid-dust-entry")

    assert repo.clear_stale_ownership(ACCOUNT, SYMBOL, Market.EQUITY) == pytest.approx(1e-7)

    assert _owned(repo) == pytest.approx(0.0)
    assert [row["kind"] for row in _adjustments(repo)] == ["stale_ownership_clear"]
    assert emitted == []


def test_uncorroborated_residue_is_refused_and_alerted_once(tmp_path: Path, emitted: list[dict[str, object]]) -> None:
    """The R2-7-1 case: a restored ledger shows 100 QLD, the broker is flat and
    no local SELL exists. The clear is refused, the lots stay, and one alert
    names the operator rebase instead of a silent write-off."""
    repo = _repo(tmp_path)
    _fill(repo, "buy", 100.0, ENTRY_PRICE, T0, "cid-entry")

    assert repo.clear_stale_ownership(ACCOUNT, SYMBOL, Market.EQUITY) == 0.0
    assert repo.clear_stale_ownership(ACCOUNT, SYMBOL, Market.EQUITY) == 0.0

    assert _owned(repo) == pytest.approx(100.0)
    assert _adjustments(repo) == []
    assert len(emitted) == 1
    assert emitted[0]["category"] == "ownership_stale_uncorroborated"
    assert "--broker-qty 0 --confirm 100" in str(emitted[0]["message"])


def test_local_exit_after_the_last_buy_clears_with_an_honest_note(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _fill(repo, "buy", 100.0, ENTRY_PRICE, T0, "cid-entry")
    _order(repo, "sell", 100.0, ENTRY_PRICE, "cid-exit", T0 + timedelta(minutes=1))

    assert repo.clear_stale_ownership(ACCOUNT, SYMBOL, Market.EQUITY) == 100.0

    (row,) = _adjustments(repo)
    assert row["kind"] == "stale_ownership_clear"
    note = str(row["note"])
    assert "activity history" not in note
    assert "local SELL order" in note


def test_an_exit_older_than_the_last_buy_does_not_explain_a_flat_broker(
    tmp_path: Path, emitted: list[dict[str, object]]
) -> None:
    """The exit belonged to an earlier lot; the 80 shares bought after it have
    no exit of their own, so the clear is refused."""
    repo = _repo(tmp_path)
    _fill(repo, "buy", 100.0, ENTRY_PRICE, T0, "cid-entry")
    _fill(repo, "sell", 100.0, ENTRY_PRICE, T0 + timedelta(minutes=1), "cid-old-exit")
    _fill(repo, "buy", 80.0, ENTRY_PRICE, T0 + timedelta(minutes=2), "cid-rebuy")

    assert repo.clear_stale_ownership(ACCOUNT, SYMBOL, Market.EQUITY) == 0.0

    assert _owned(repo) == pytest.approx(80.0)
    assert _adjustments(repo) == []
    assert len(emitted) == 1


# ---------------------------------------------------------------------------
# F2-1-4: same-instant fills replay buys first, in booking order
# ---------------------------------------------------------------------------


def test_same_instant_sell_booked_first_still_replays_after_its_buy(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _order(repo, "buy", 100.0, ENTRY_PRICE, "cid-buy", T0)
    _order(repo, "sell", 100.0, 60.0, "cid-sell", T0)
    # The sell's fill is booked first; the replay must still see the buy first.
    _apply_fill(repo, "cid-sell", 100.0, 60.0, T0)
    _apply_fill(repo, "cid-buy", 100.0, ENTRY_PRICE, T0)

    # Each event is (occurred_at, kind, quantity, price, fee).
    kinds = [event[1] for event in repo._owned_events(ACCOUNT, SYMBOL, Market.EQUITY)]

    assert kinds == ["buy", "sell"]
    assert repo.owned_cost_bases(ACCOUNT, SYMBOL, Market.EQUITY) == ()


# ---------------------------------------------------------------------------
# F2-1-2 / F2-8-2: an uncorroborated reverse split halts, it is never a sale
# ---------------------------------------------------------------------------


def test_reverse_ratio_with_a_moved_basis_is_unknown_not_a_sale() -> None:
    change = classify_share_change(100.0, 50.0, owned_average_cost=80.0, broker_average_cost=120.0)

    assert change.kind == "unknown"
    assert change.rebasable is False


def test_reverse_ratio_without_a_broker_basis_is_unknown_not_a_sale() -> None:
    change = classify_share_change(100.0, 50.0, owned_average_cost=80.0, broker_average_cost=None)

    assert change.kind == "unknown"
    assert change.rebasable is False


def test_reverse_ratio_with_an_unchanged_basis_is_still_a_foreign_sell() -> None:
    change = classify_share_change(100.0, 50.0, owned_average_cost=80.0, broker_average_cost=80.0)

    assert change.kind == "foreign_sell"
    assert change.rebasable is True


def test_non_standard_decrease_is_still_a_foreign_sell_without_a_basis() -> None:
    change = classify_share_change(100.0, 60.0, owned_average_cost=50.0)

    assert change.kind == "foreign_sell"


def test_repository_halts_an_uncorroborated_reverse_split_instead_of_booking_it(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _fill(repo, "buy", 100.0, ENTRY_PRICE, T0, "cid-entry")

    assert repo.reconcile_broker_quantity(ACCOUNT, SYMBOL, Market.EQUITY, 50.0, 120.0) is None

    assert _owned(repo) == pytest.approx(100.0)
    assert _adjustments(repo) == []
