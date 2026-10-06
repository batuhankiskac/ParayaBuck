"""Ownership rebases on splits and foreign sells (audit wave 1c).

Ownership is net fills, so a stock split or another consumer of the shared
Alpaca key left the repository claiming a share count the broker disagrees
with: every decision (exits included) was dropped, and no reset could recover
because a clean reconcile was impossible. Each test pins one consumer-visible
invariant and is deterministic — synthetic fixtures, ``tmp_path`` state, no
network and no production paths.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from parayabuck.cli import main
from parayabuck.core.models import Market
from parayabuck.execution.alpaca_account import AccountService
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
    when: datetime,
    client_order_id: str,
) -> str:
    """Record one of our own orders (no fill yet)."""
    target = TargetPortfolio("vt35", "1", when, (), 1.0)
    risk = RiskOverlayDecision(target, False, (), 0.0, 0.0)
    session = repo.begin_session(ACCOUNT, "digest", "paper")
    decision_id = repo.record_decision(session, ACCOUNT, "digest", target, risk, Market.EQUITY)
    order = PortfolioOrderRequest(
        client_order_id=client_order_id,
        decision_id=decision_id,
        symbol=SYMBOL,
        market=Market.EQUITY,
        side=PortfolioOrderSide(side),
        quantity=quantity,
        reference_price=price,
        decision_timestamp=when,
        strategy_name="vt35",
        strategy_version="1",
        risk_reducing=False,
    )
    repo.record_order(order, "submitted")
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
    when: datetime,
    *,
    client_order_id: str | None = None,
) -> str:
    """Record one of our own orders and its real fill at ``price``."""
    client_id = client_order_id or f"cid-{side}-{when.isoformat()}"
    _order(repo, side, quantity, price, when, client_id)
    _apply_fill(repo, client_id, quantity, price, when)
    return client_id


def _owned(repo: PortfolioStateRepository) -> float:
    return repo.owned_quantities(ACCOUNT, Market.EQUITY).get(SYMBOL, 0.0)


def _outstanding(repo: PortfolioStateRepository) -> list[tuple[str, float, float]]:
    return [
        (str(row["kind"]), float(row["sign"]), float(row["outstanding_quantity"]))
        for row in repo.ownership_adjustments(ACCOUNT)
    ]


# ---------------------------------------------------------------------------
# Split detection (F07)
# ---------------------------------------------------------------------------


def test_forward_split_rebases_ownership_instead_of_halting(tmp_path: Path) -> None:
    """A 2:1 ex-date doubles the broker's shares; ownership follows, so the
    next decision (an exit included) is still made."""
    repo = _repo(tmp_path)
    _fill(repo, "buy", 100.0, ENTRY_PRICE, T0)
    assert _owned(repo) == 100.0

    assert repo.reconcile_broker_quantity(ACCOUNT, SYMBOL, Market.EQUITY, 200.0, ENTRY_PRICE / 2) == 200.0

    assert _owned(repo) == 200.0
    assert _outstanding(repo) == [("split_rebase", 1.0, 100.0)]


def test_reverse_split_reduces_ownership_to_the_broker_count(tmp_path: Path) -> None:
    """A 1:2 reverse split quarters the broker's shares and doubles its cost
    basis; ownership is reduced, never raised."""
    repo = _repo(tmp_path)
    _fill(repo, "buy", 100.0, ENTRY_PRICE, T0)

    assert repo.reconcile_broker_quantity(ACCOUNT, SYMBOL, Market.EQUITY, 50.0, ENTRY_PRICE * 2) == 50.0

    assert _owned(repo) == 50.0
    assert _outstanding(repo) == [("split_rebase", -1.0, 50.0)]


def test_a_foreign_buy_is_never_mistaken_for_a_split(tmp_path: Path) -> None:
    """Another consumer of the shared key bought our exact share count: the
    quantity doubles but our cost basis does not, so nothing is claimed."""
    repo = _repo(tmp_path)
    _fill(repo, "buy", 100.0, ENTRY_PRICE, T0)

    assert repo.reconcile_broker_quantity(ACCOUNT, SYMBOL, Market.EQUITY, 200.0, ENTRY_PRICE) is None

    assert _owned(repo) == 100.0
    assert _outstanding(repo) == []


def test_a_non_standard_ratio_is_left_to_the_operator(tmp_path: Path) -> None:
    """No standard split ratio explains 1.5x, so the counts keep disagreeing
    and reconciliation still reports the drift instead of guessing."""
    repo = _repo(tmp_path)
    _fill(repo, "buy", 100.0, ENTRY_PRICE, T0)

    assert repo.reconcile_broker_quantity(ACCOUNT, SYMBOL, Market.EQUITY, 150.0, ENTRY_PRICE) is None

    assert _owned(repo) == 100.0


def test_classify_share_change_labels_a_foreign_liquidation(tmp_path: Path) -> None:
    """The pure classifier: a shortfall with an unchanged cost basis is a
    foreign sell, and adopting it can never over-claim a share."""
    change = classify_share_change(100.0, 60.0, owned_average_cost=50.0, broker_average_cost=50.0)

    assert change.kind == "foreign_sell"
    assert change.rebasable is True
    assert change.adjustment_quantity == pytest.approx(-40.0)


# ---------------------------------------------------------------------------
# Foreign partial sell (G1.2)
# ---------------------------------------------------------------------------


def test_foreign_partial_sell_reduces_ownership_with_an_audit_row(tmp_path: Path) -> None:
    """Another consumer of the shared key sold 40 of our 100 shares: ownership
    is reduced to the broker's count, with a durable anomaly row."""
    repo = _repo(tmp_path)
    _fill(repo, "buy", 100.0, ENTRY_PRICE, T0)

    assert repo.reconcile_broker_quantity(ACCOUNT, SYMBOL, Market.EQUITY, 60.0, ENTRY_PRICE) == 60.0

    assert _owned(repo) == 60.0
    assert _outstanding(repo) == [("foreign_sell_reduction", -1.0, 40.0)]


# ---------------------------------------------------------------------------
# Stale-adjustment consumption (G1.1)
# ---------------------------------------------------------------------------


def _force_order_created_at(repo: PortfolioStateRepository, client_order_id: str, when: datetime) -> None:
    with repo.store.lock, repo.store.connection:
        repo.store.connection.execute(
            "UPDATE portfolio_orders SET created_at = ? WHERE client_order_id = ?",
            (when.isoformat(), client_order_id),
        )


def test_only_a_sell_older_than_the_adjustment_consumes_it(tmp_path: Path) -> None:
    """The race the adjustment exists for: the broker filled our exit while
    the stream was delayed, and reconcile saw a flat position first."""
    repo = _repo(tmp_path)
    _fill(repo, "buy", 100.0, ENTRY_PRICE, T0)
    early_exit = _order(repo, "sell", 100.0, ENTRY_PRICE, T0 + timedelta(minutes=1), "cid-early-exit")
    _force_order_created_at(repo, early_exit, T0)
    assert repo.clear_stale_ownership(ACCOUNT, SYMBOL, Market.EQUITY) == 100.0

    _apply_fill(repo, early_exit, 100.0, ENTRY_PRICE, T0 + timedelta(minutes=2))

    assert repo.ownership_adjustments(ACCOUNT) == ()
    assert _owned(repo) == pytest.approx(0.0)


def test_a_newer_sell_does_not_consume_a_stale_adjustment(tmp_path: Path) -> None:
    """The reported defect: an exit submitted after the adjustment is a
    different trade. Cancelling the adjustment against it left phantom owned
    shares and then halted the runtime on ownership drift."""
    repo = _repo(tmp_path)
    _fill(repo, "buy", 100.0, ENTRY_PRICE, T0)
    assert repo.clear_stale_ownership(ACCOUNT, SYMBOL, Market.EQUITY) == 100.0

    _fill(repo, "sell", 30.0, ENTRY_PRICE, T0 + timedelta(minutes=5), client_order_id="cid-later-exit")

    assert [
        (row["kind"], row["consumed_quantity"], row["outstanding_quantity"])
        for row in repo.ownership_adjustments(ACCOUNT)
    ] == [("stale_ownership_clear", 0.0, 100.0)]


def test_foreign_liquidation_rebuy_and_partial_exit_stay_consistent(tmp_path: Path) -> None:
    """The reported sequence end to end: our 100 shares are sold from outside
    the bot, we re-buy 80, a partial de-risk sells 30, and the broker holds
    50. Ownership must be 50 — never a negative net and never a drift halt."""
    repo = _repo(tmp_path)
    _fill(repo, "buy", 100.0, ENTRY_PRICE, T0)
    assert repo.clear_stale_ownership(ACCOUNT, SYMBOL, Market.EQUITY) == 100.0
    _fill(repo, "buy", 80.0, ENTRY_PRICE, T0 + timedelta(minutes=1))
    _fill(repo, "sell", 30.0, ENTRY_PRICE, T0 + timedelta(minutes=2))

    assert _owned(repo) == pytest.approx(50.0)
    assert [(row["kind"], row["outstanding_quantity"]) for row in repo.ownership_adjustments(ACCOUNT)] == [
        ("stale_ownership_clear", 100.0)
    ]


def test_a_rebuy_does_not_resurrect_cleared_ownership(tmp_path: Path) -> None:
    """A BUY fill after a stale clear adds the newly bought shares and nothing
    else: the shares the broker no longer had stay cleared, and the anomaly
    row stays visible for the operator."""
    repo = _repo(tmp_path)
    _fill(repo, "buy", 100.0, ENTRY_PRICE, T0)
    assert repo.clear_stale_ownership(ACCOUNT, SYMBOL, Market.EQUITY) == 100.0

    _fill(repo, "buy", 80.0, ENTRY_PRICE, T0 + timedelta(minutes=1))

    assert _owned(repo) == pytest.approx(80.0)
    assert [row["kind"] for row in repo.ownership_adjustments(ACCOUNT)] == ["stale_ownership_clear"]


# ---------------------------------------------------------------------------
# Account snapshot behaviour
# ---------------------------------------------------------------------------


def _account_broker(repo: PortfolioStateRepository, *, quantity: float, average: float, resolver: object) -> object:
    position = SimpleNamespace(
        symbol=SYMBOL,
        qty=str(quantity),
        qty_available=str(quantity),
        avg_entry_price=str(average),
        current_price=str(average),
        market_value="0",
        cost_basis="0",
        unrealized_pl="0",
        unrealized_intraday_pl="0",
        asset_class="us_equity",
    )
    client = SimpleNamespace(
        get_account=lambda: SimpleNamespace(id=ACCOUNT, equity="100000", last_equity="100000", cash="50000"),
        get_all_positions=lambda: [position],
    )
    return SimpleNamespace(
        _client=client,
        _account_id=ACCOUNT,
        ledger=None,
        portfolio_owned_quantities=lambda: repo.owned_quantities(ACCOUNT, Market.EQUITY),
        portfolio_open_orders=lambda: (),
        portfolio_ownership_resolved=resolver,
        _ensure_peak_reconciled=lambda account_id, equity: None,
        _peak_equity=0.0,
        _save_peak_equity=lambda: None,
        _snapshot_cache=None,
    )


def _resolver(repo: PortfolioStateRepository):
    def resolve(symbol: str, market: Market, broker_qty: float, broker_average: float | None) -> float | None:
        return repo.reconcile_broker_quantity(ACCOUNT, symbol, market, broker_qty, broker_average)

    return resolve


def test_account_absorbs_a_foreign_partial_sell_and_alerts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """G1.2 end to end: an unexplained shortfall is an audited reduction plus
    an alert, not a permanent halt with no supported recovery."""
    repo = _repo(tmp_path)
    _fill(repo, "buy", 100.0, ENTRY_PRICE, T0)
    emitted: list[dict[str, object]] = []
    monkeypatch.setattr(alerts_module, "emit", lambda kind, **fields: emitted.append({"kind": kind, **fields}))
    broker = _account_broker(repo, quantity=60.0, average=ENTRY_PRICE, resolver=_resolver(repo))

    snapshot = AccountService(broker).account()

    assert [(p.symbol, p.managed_qty, p.ownership) for p in snapshot.positions] == [(SYMBOL, 60.0, "managed")]
    assert _owned(repo) == pytest.approx(60.0)
    assert [row["kind"] for row in repo.ownership_adjustments(ACCOUNT)] == ["foreign_sell_reduction"]
    assert emitted and emitted[0]["category"] == "ownership_rebased"


def test_account_rebases_a_forward_split_without_halting(tmp_path: Path) -> None:
    """F07 end to end: the ex-date snapshot must come back managed, with the
    decision path still able to exit."""
    repo = _repo(tmp_path)
    _fill(repo, "buy", 100.0, ENTRY_PRICE, T0)
    broker = _account_broker(repo, quantity=200.0, average=ENTRY_PRICE / 2, resolver=_resolver(repo))

    snapshot = AccountService(broker).account()

    assert [(p.symbol, p.managed_qty, p.ownership) for p in snapshot.positions] == [(SYMBOL, 200.0, "managed")]
    assert _owned(repo) == pytest.approx(200.0)


def test_account_still_fails_closed_without_a_rebase_authority(tmp_path: Path) -> None:
    """Without the runtime's resolver the shortfall must raise as before: the
    runtime owns the adjustment, the account service never invents one."""
    repo = _repo(tmp_path)
    _fill(repo, "buy", 100.0, ENTRY_PRICE, T0)
    broker = _account_broker(repo, quantity=60.0, average=ENTRY_PRICE, resolver=None)

    with pytest.raises(RuntimeError, match="Ownership drift for QLD"):
        AccountService(broker).account()

    assert repo.ownership_adjustments(ACCOUNT) == ()


def test_a_working_exit_keeps_ownership_until_it_completes(tmp_path: Path) -> None:
    """Our own unfilled SELL explains the shortfall; the resolver must not
    absorb it as a foreign sale while the exit is still working."""
    repo = _repo(tmp_path)
    _fill(repo, "buy", 100.0, ENTRY_PRICE, T0)
    broker = _account_broker(repo, quantity=60.0, average=ENTRY_PRICE, resolver=_resolver(repo))
    broker.portfolio_open_orders = lambda: (
        SimpleNamespace(
            symbol=SYMBOL, market=Market.EQUITY, side="sell", quantity=40.0, cumulative_filled_quantity=0.0
        ),
    )

    snapshot = AccountService(broker).account()

    assert snapshot.positions[0].managed_qty == pytest.approx(60.0)
    assert repo.ownership_adjustments(ACCOUNT) == ()


# ---------------------------------------------------------------------------
# Operator CLI
# ---------------------------------------------------------------------------


def _cli(tmp_path: Path, *argv: str) -> int:
    return main(["ops", "rebase-ownership", *argv])


def test_rebase_cli_is_a_dry_run_by_default(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo = _repo(tmp_path)
    _fill(repo, "buy", 100.0, ENTRY_PRICE, T0)
    db = str(tmp_path / "runtime.sqlite3")

    assert _cli(tmp_path, SYMBOL, "--broker-qty", "200", "--db", db, "--account-id", ACCOUNT) == 0

    output = capsys.readouterr().out
    assert "Dry run" in output
    assert "200" in output
    assert _owned(repo) == pytest.approx(100.0)


def test_rebase_cli_confirm_writes_the_audited_adjustment(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo = _repo(tmp_path)
    _fill(repo, "buy", 100.0, ENTRY_PRICE, T0)
    db = str(tmp_path / "runtime.sqlite3")

    exit_code = _cli(
        tmp_path,
        SYMBOL,
        "--broker-qty",
        "200",
        "--db",
        db,
        "--account-id",
        ACCOUNT,
        "--note",
        "QLD 2:1 split verified on the broker",
        "--confirm",
    )

    assert exit_code == 0
    assert "Ownership rebased" in capsys.readouterr().out
    assert _owned(repo) == pytest.approx(200.0)
    rows = repo.ownership_adjustments(ACCOUNT)
    assert [(row["kind"], row["sign"], row["note"]) for row in rows] == [
        ("operator_rebase", 1.0, "QLD 2:1 split verified on the broker")
    ]


def test_rebase_cli_refuses_while_orders_are_open(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo = _repo(tmp_path)
    decision_target = TargetPortfolio("vt35", "1", T0, (), 1.0)
    risk = RiskOverlayDecision(decision_target, False, (), 0.0, 0.0)
    session = repo.begin_session(ACCOUNT, "digest", "paper")
    decision_id = repo.record_decision(session, ACCOUNT, "digest", decision_target, risk, Market.EQUITY)
    _fill(repo, "buy", 100.0, ENTRY_PRICE, T0)
    repo.record_order(
        PortfolioOrderRequest(
            client_order_id="cid-working-exit",
            decision_id=decision_id,
            symbol=SYMBOL,
            market=Market.EQUITY,
            side=PortfolioOrderSide.SELL,
            quantity=100.0,
            reference_price=ENTRY_PRICE,
            decision_timestamp=T0,
            strategy_name="vt35",
            strategy_version="1",
            risk_reducing=True,
        ),
        "submitted",
    )

    exit_code = _cli(
        tmp_path,
        SYMBOL,
        "--broker-qty",
        "200",
        "--db",
        str(tmp_path / "runtime.sqlite3"),
        "--account-id",
        ACCOUNT,
        "--confirm",
    )

    assert exit_code == 1
    assert "Refusing" in capsys.readouterr().err
    assert _owned(repo) == pytest.approx(100.0)


def test_rebase_cli_reports_nothing_to_rebase(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo = _repo(tmp_path)
    _fill(repo, "buy", 100.0, ENTRY_PRICE, T0)

    exit_code = _cli(
        tmp_path,
        SYMBOL,
        "--broker-qty",
        "100",
        "--db",
        str(tmp_path / "runtime.sqlite3"),
        "--account-id",
        ACCOUNT,
        "--confirm",
    )

    assert exit_code == 0
    assert "Nothing to do" in capsys.readouterr().out
    assert repo.ownership_adjustments(ACCOUNT) == ()


def test_rebase_cli_rejects_a_negative_broker_quantity(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo = _repo(tmp_path)
    _fill(repo, "buy", 100.0, ENTRY_PRICE, T0)

    exit_code = _cli(
        tmp_path,
        SYMBOL,
        "--broker-qty",
        "-1",
        "--db",
        str(tmp_path / "runtime.sqlite3"),
        "--account-id",
        ACCOUNT,
        "--confirm",
    )

    assert exit_code == 2
    assert "Invalid --broker-qty" in capsys.readouterr().err
    assert _owned(repo) == pytest.approx(100.0)
