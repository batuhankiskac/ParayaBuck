"""Audit wave W11 regressions: split boundaries, acquisitions, refusals and the
operator rebase CLI (F10-1..F10-4, F10-7, N6-1, F8-2, F8-3, F8-6, M-1, C7-3, D-1, P-F3).

Each test pins one invariant. Synthetic fixtures, ``tmp_path`` state, no network,
no credentials and no production paths.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from parayabuck.cli import main
from parayabuck.core.models import Market
from parayabuck.execution.portfolio import PortfolioOrderRequest, PortfolioOrderSide, RiskOverlayDecision
from parayabuck.monitoring import alerts as alerts_module
from parayabuck.portfolio.models import TargetPortfolio
from parayabuck.portfolio.ownership_rebase import classify_share_change, format_quantity
from parayabuck.portfolio.runtime_ownership import OwnershipInconsistency
from parayabuck.portfolio.runtime_repository import OwnershipRebaseRefused, PortfolioStateRepository

ACCOUNT = "acct"
SYMBOL = "QLD"
T0 = datetime(2026, 9, 1, 13, 30, tzinfo=UTC)


def _repo(tmp_path: Path) -> PortfolioStateRepository:
    return PortfolioStateRepository(tmp_path / "runtime.sqlite3")


def _order(repo: PortfolioStateRepository, side: str, quantity: float, price: float, when: datetime, cid: str) -> str:
    target = TargetPortfolio("vt35", "1", when, (), 1.0)
    risk = RiskOverlayDecision(target, False, (), 0.0, 0.0)
    session = repo.begin_session(ACCOUNT, "digest", "paper")
    decision_id = repo.record_decision_with_orders(session, ACCOUNT, "digest", target, risk, Market.EQUITY, ())
    repo.record_order(
        PortfolioOrderRequest(
            client_order_id=cid,
            decision_id=decision_id,
            symbol=SYMBOL,
            market=Market.EQUITY,
            side=PortfolioOrderSide(side),
            quantity=quantity,
            reference_price=price,
            decision_timestamp=when,
            strategy_name="vt35",
            strategy_version="1",
        ),
        "submitted",
    )
    return cid


def _apply_fill(repo: PortfolioStateRepository, cid: str, quantity: float, price: float, when: datetime) -> None:
    repo.apply_trade_update(
        client_order_id=cid,
        broker_order_id=f"broker-{cid}",
        status="filled",
        cumulative_filled_quantity=quantity,
        average_fill_price=price,
        occurred_at=when,
        activity_id=f"activity-{cid}",
        fee=0.0,
        realized_pnl=0.0,
        unrealized_pnl=0.0,
    )


def _fill(repo: PortfolioStateRepository, side: str, quantity: float, price: float, when: datetime) -> str:
    cid = f"cid-{side}-{when.isoformat()}-{quantity}"
    _order(repo, side, quantity, price, when, cid)
    _apply_fill(repo, cid, quantity, price, when)
    return cid


def _owned(repo: PortfolioStateRepository) -> float:
    return repo.owned_quantities(ACCOUNT, Market.EQUITY).get(SYMBOL, 0.0)


def _adjustments(repo: PortfolioStateRepository) -> list[tuple[str, float, float]]:
    return [
        (str(row["kind"]), float(row["sign"]), float(row["outstanding_quantity"]))
        for row in repo.ownership_adjustments(ACCOUNT)
    ]


def _cli(*argv: str) -> int:
    return main(["ops", "rebase-ownership", *argv])


# ---------------------------------------------------------------------------
# F10-1: a split is still auto-rebased when a fill booked after it is in the count
# ---------------------------------------------------------------------------


def test_split_is_rebased_when_a_fill_after_the_split_is_already_booked(tmp_path: Path) -> None:
    """A 2:1 split and a 100-share DAY BUY booked before the first account pass.
    The whole-position ratio is 1.8, so the old logic missed the split. The split
    is now located at the BUY's instant: the pre-split 400 shares carry the ratio,
    and the post-split buy keeps its own cost."""
    repo = _repo(tmp_path)
    _fill(repo, "buy", 400.0, 50.0, T0)
    _fill(repo, "buy", 100.0, 30.0, T0 + timedelta(days=1))
    broker_average = (400 * 50.0 + 100 * 30.0) / 900.0

    assert repo.reconcile_broker_quantity(ACCOUNT, SYMBOL, Market.EQUITY, 900.0, broker_average) == 900.0

    assert _owned(repo) == pytest.approx(900.0)
    assert _adjustments(repo) == [("split_rebase", 1.0, 400.0)]
    fifo, average = repo.owned_cost_bases(ACCOUNT, SYMBOL, Market.EQUITY)
    assert fifo == pytest.approx(23000.0 / 900.0)
    assert average == pytest.approx(23000.0 / 900.0)


def test_pure_classifier_holds_post_boundary_quantity_out_of_the_ratio() -> None:
    """Whole-position ratio 1.8 is no split; holding the 100 post-split shares out gives 2."""
    assert classify_share_change(500.0, 900.0, owned_average_cost=46.0, broker_average_cost=23000 / 900).kind == (
        "unknown"
    )
    change = classify_share_change(
        500.0,
        900.0,
        owned_average_cost=46.0,
        broker_average_cost=23000 / 900,
        post_boundary_quantity=100.0,
    )
    assert change.kind == "split"
    assert change.ratio == 2.0


# ---------------------------------------------------------------------------
# F10-2: only a corroborated split rescales; an increase states its proof
# ---------------------------------------------------------------------------


def test_operator_increase_without_a_split_proof_is_refused(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _fill(repo, "buy", 100.0, 50.0, T0)

    with pytest.raises(OwnershipRebaseRefused, match="acquisition"):
        repo.rebase_ownership_to_broker_quantity(ACCOUNT, SYMBOL, Market.EQUITY, 200.0)

    assert _owned(repo) == pytest.approx(100.0)
    assert _adjustments(repo) == []
    assert repo.store.connection.in_transaction is False


def test_corroborated_operator_increase_is_booked_as_a_split(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _fill(repo, "buy", 100.0, 50.0, T0)

    assert repo.rebase_ownership_to_broker_quantity(ACCOUNT, SYMBOL, Market.EQUITY, 200.0, broker_average_cost=25.0)

    assert _adjustments(repo) == [("split_rebase", 1.0, 100.0)]
    assert repo.owned_cost_bases(ACCOUNT, SYMBOL, Market.EQUITY) == pytest.approx((25.0, 25.0))


def test_operator_acquisition_seeds_a_lot_at_the_stated_unit_cost(tmp_path: Path) -> None:
    """A foreign purchase must not rescale the bot's own lot: 100@50 plus an
    acquired 100@60 averages 55, not the 25 a split rescale would give."""
    repo = _repo(tmp_path)
    _fill(repo, "buy", 100.0, 50.0, T0)

    repo.rebase_ownership_to_broker_quantity(ACCOUNT, SYMBOL, Market.EQUITY, 200.0, unit_cost=60.0)

    assert _owned(repo) == pytest.approx(200.0)
    assert _adjustments(repo) == [("operator_acquisition", 1.0, 100.0)]
    assert repo.owned_cost_bases(ACCOUNT, SYMBOL, Market.EQUITY) == pytest.approx((55.0, 55.0))


def test_an_increase_cannot_claim_both_a_split_and_an_acquisition(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _fill(repo, "buy", 100.0, 50.0, T0)

    with pytest.raises(OwnershipRebaseRefused, match="corroborates a split"):
        repo.rebase_ownership_to_broker_quantity(
            ACCOUNT, SYMBOL, Market.EQUITY, 200.0, broker_average_cost=25.0, unit_cost=60.0
        )
    assert _owned(repo) == pytest.approx(100.0)


def test_an_acquisition_cost_is_refused_for_a_decrease(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _fill(repo, "buy", 100.0, 50.0, T0)

    with pytest.raises(OwnershipRebaseRefused, match="books an increase"):
        repo.rebase_ownership_to_broker_quantity(ACCOUNT, SYMBOL, Market.EQUITY, 50.0, unit_cost=10.0)
    assert _owned(repo) == pytest.approx(100.0)


def test_an_unpriced_increase_fails_closed_for_the_lot_book(tmp_path: Path) -> None:
    """A legacy positive operator_rebase row has no cost: the replay refuses to
    price it, so cost bases are unknown and a sale is booked at 0.0 P&L (warned)."""
    repo = _repo(tmp_path)
    _fill(repo, "buy", 100.0, 50.0, T0)
    repo.record_ownership_adjustment(ACCOUNT, SYMBOL, Market.EQUITY, 100.0, kind="operator_rebase", sign=1.0)

    assert _owned(repo) == pytest.approx(200.0)
    assert repo.owned_cost_bases(ACCOUNT, SYMBOL, Market.EQUITY) == ()
    assert repo.fifo_realized_pnl(ACCOUNT, SYMBOL, Market.EQUITY, 10.0, 60.0, 0.0) == 0.0


# ---------------------------------------------------------------------------
# F10-3: a zero-owned increase seeds a lot at the unit cost, or is refused
# ---------------------------------------------------------------------------


def test_zero_owned_increase_is_refused_without_a_unit_cost(tmp_path: Path) -> None:
    repo = _repo(tmp_path)

    with pytest.raises(OwnershipRebaseRefused):
        repo.rebase_ownership_to_broker_quantity(ACCOUNT, SYMBOL, Market.EQUITY, 50.0)
    assert _adjustments(repo) == []


def test_zero_owned_adoption_with_a_unit_cost_seeds_a_lot(tmp_path: Path) -> None:
    """Before the fix the adoption added nothing to the lot book, so the later sale
    booked 0.0 P&L. With the lot seeded at 10, selling 50 at 12 realizes 100."""
    repo = _repo(tmp_path)
    repo.rebase_ownership_to_broker_quantity(ACCOUNT, SYMBOL, Market.EQUITY, 50.0, unit_cost=10.0)

    assert _owned(repo) == pytest.approx(50.0)
    assert repo.fifo_realized_pnl(ACCOUNT, SYMBOL, Market.EQUITY, 50.0, 12.0, 0.0) == pytest.approx(100.0)


# ---------------------------------------------------------------------------
# F10-4: fractional-share tolerance on reverse splits; halt on near-standard reductions
# ---------------------------------------------------------------------------


def test_reverse_split_with_cash_in_lieu_is_a_split_when_the_basis_corroborates(tmp_path: Path) -> None:
    """197.6 shares to 98 is a 1:2 reverse split with 0.8 share paid in cash."""
    repo = _repo(tmp_path)
    _fill(repo, "buy", 197.6, 50.0, T0)
    broker_average = 50.0 * 197.6 / 98.0

    assert repo.reconcile_broker_quantity(ACCOUNT, SYMBOL, Market.EQUITY, 98.0, broker_average) == 98.0

    assert _owned(repo) == pytest.approx(98.0)
    assert _adjustments(repo) == [("split_rebase", -1.0, pytest.approx(99.6))]
    assert repo.owned_cost_bases(ACCOUNT, SYMBOL, Market.EQUITY) == pytest.approx(
        (50.0 * 197.6 / 98.0, 50.0 * 197.6 / 98.0)
    )


def test_fractional_tolerance_applies_only_to_reductions() -> None:
    """A forward split is not rounded down, so a 1-share gap on an increase is not a split."""
    change = classify_share_change(100.0, 201.0, owned_average_cost=50.0, broker_average_cost=50 * 100 / 201)
    assert change.kind == "unknown"


def test_near_standard_inexact_reduction_halts_instead_of_a_foreign_sale(tmp_path: Path) -> None:
    """504 of 1000 is 0.504: near the 1:2 ratio but four shares off it, so it halts."""
    repo = _repo(tmp_path)
    _fill(repo, "buy", 1000.0, 50.0, T0)

    assert repo.reconcile_broker_quantity(ACCOUNT, SYMBOL, Market.EQUITY, 504.0) is None

    assert _owned(repo) == pytest.approx(1000.0)
    assert _adjustments(repo) == []


def test_near_standard_inexact_reduction_is_classified_unknown() -> None:
    change = classify_share_change(1000.0, 504.0, owned_average_cost=50.0)
    assert change.kind == "unknown"
    assert change.rebasable is False


# ---------------------------------------------------------------------------
# F10-7: negative net is a typed inconsistency; the operator path still reads it
# ---------------------------------------------------------------------------


def test_negative_net_is_a_typed_inconsistency_and_the_plan_explains_it(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _fill(repo, "buy", 100.0, 50.0, T0)
    repo.record_ownership_adjustment(ACCOUNT, SYMBOL, Market.EQUITY, 150.0, kind="stale_ownership_clear")

    with pytest.raises(OwnershipInconsistency) as caught:
        repo.owned_quantities(ACCOUNT, Market.EQUITY)
    assert isinstance(caught.value, RuntimeError)
    assert caught.value.net == {SYMBOL: pytest.approx(-50.0)}

    plan = repo.plan_ownership_rebase(ACCOUNT, SYMBOL, Market.EQUITY, 50.0)
    assert plan.refused
    assert "negative" in plan.reason


# ---------------------------------------------------------------------------
# Perf 8: the owned read sums per symbol and side
# ---------------------------------------------------------------------------


def test_owned_quantities_nets_every_fill_of_a_symbol(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _fill(repo, "buy", 100.0, 50.0, T0)
    _fill(repo, "buy", 50.0, 52.0, T0 + timedelta(days=1))
    _fill(repo, "sell", 30.0, 55.0, T0 + timedelta(days=2))

    assert _owned(repo) == pytest.approx(120.0)


# ---------------------------------------------------------------------------
# N6-1: quantities are never rounded to six significant digits
# ---------------------------------------------------------------------------


def test_format_quantity_is_exact_to_nine_decimals() -> None:
    assert format_quantity(1034.567891) == "1034.567891"
    assert format_quantity(200.0) == "200"
    assert format_quantity(-40.5) == "-40.5"
    assert format_quantity(-0.0) == "0"


def test_surplus_alert_quotes_the_exact_broker_quantity(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _repo(tmp_path)
    _fill(repo, "buy", 100.0, 50.0, T0)
    emitted: list[dict[str, object]] = []
    monkeypatch.setattr(alerts_module, "emit", lambda kind, **fields: emitted.append({"kind": kind, **fields}))

    repo.reconcile_broker_quantity(ACCOUNT, SYMBOL, Market.EQUITY, 1034.567891)

    assert emitted and "1034.567891" in str(emitted[0]["message"])
    assert "--broker-qty 1034.567891" in str(emitted[0]["message"])
    assert "1034.57 " not in str(emitted[0]["message"])


# ---------------------------------------------------------------------------
# C7-3: the surplus alert key is discarded once the symbol reconciles
# ---------------------------------------------------------------------------


def test_surplus_alert_fires_again_in_a_new_episode_after_reconciling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path)
    _fill(repo, "buy", 100.0, 50.0, T0)
    emitted: list[dict[str, object]] = []
    monkeypatch.setattr(alerts_module, "emit", lambda kind, **fields: emitted.append({"kind": kind, **fields}))

    repo.reconcile_broker_quantity(ACCOUNT, SYMBOL, Market.EQUITY, 200.0)
    repo.reconcile_broker_quantity(ACCOUNT, SYMBOL, Market.EQUITY, 200.0)
    assert len(emitted) == 1  # once per episode

    repo.reconcile_broker_quantity(ACCOUNT, SYMBOL, Market.EQUITY, 100.0)  # reconciled
    repo.reconcile_broker_quantity(ACCOUNT, SYMBOL, Market.EQUITY, 200.0)  # a new episode

    assert len(emitted) == 2


# ---------------------------------------------------------------------------
# D-1: a late SELL realizes its P&L against the lots as they stood before the clear
# ---------------------------------------------------------------------------


def test_late_sell_books_its_realized_pnl_before_the_stale_clear(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _fill(repo, "buy", 100.0, 50.0, T0)
    early_exit = _order(repo, "sell", 100.0, 50.0, T0 + timedelta(minutes=1), "cid-early-exit")
    with repo.store.lock, repo.store.connection:
        repo.store.connection.execute(
            "UPDATE portfolio_orders SET created_at = ? WHERE client_order_id = ?",
            (T0.isoformat(), early_exit),
        )
    assert repo.clear_stale_ownership(ACCOUNT, SYMBOL, Market.EQUITY) == 100.0

    _apply_fill(repo, early_exit, 100.0, 60.0, T0 + timedelta(minutes=2))

    row = repo.store.connection.execute(
        "SELECT realized_pnl FROM portfolio_fills WHERE activity_id = ?", (f"activity-{early_exit}",)
    ).fetchone()
    assert row[0] == pytest.approx((60.0 - 50.0) * 100.0)
    assert repo.ownership_adjustments(ACCOUNT) == ()
    assert _owned(repo) == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# M-1: the dry run never creates or migrates the database
# ---------------------------------------------------------------------------


def test_read_only_view_cannot_write_and_never_creates_a_file(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _fill(repo, "buy", 100.0, 50.0, T0)
    db = tmp_path / "runtime.sqlite3"
    view = PortfolioStateRepository.open_read_only(db)
    try:
        assert view.owned_quantities(ACCOUNT, Market.EQUITY)[SYMBOL] == pytest.approx(100.0)
        with pytest.raises(sqlite3.OperationalError):
            view.store.connection.execute(
                "INSERT INTO portfolio_high_water(account_id, equity, updated_at) VALUES ('x', 1, 'now')"
            )
    finally:
        view.close()

    missing = tmp_path / "absent" / "runtime.sqlite3"
    with pytest.raises(sqlite3.OperationalError):
        PortfolioStateRepository.open_read_only(missing).owned_quantities(ACCOUNT, Market.EQUITY)
    assert not missing.parent.exists()


def test_dry_run_on_a_missing_database_creates_nothing(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    missing = tmp_path / "absent" / "runtime.sqlite3"

    exit_code = _cli(SYMBOL, "--broker-qty", "200", "--db", str(missing), "--account-id", ACCOUNT)

    assert exit_code == 1
    assert "Nothing was created" in capsys.readouterr().err
    assert not missing.parent.exists()


# ---------------------------------------------------------------------------
# F8-2 / F8-3 / F8-6: the CLI shows the booking, checks the expected owned quantity
# and validates the symbol and account
# ---------------------------------------------------------------------------


def test_dry_run_shows_the_booking_kind_and_writes_nothing(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo = _repo(tmp_path)
    _fill(repo, "buy", 100.0, 50.0, T0)
    db = str(tmp_path / "runtime.sqlite3")

    exit_code = _cli(SYMBOL, "--broker-qty", "200", "--broker-average-cost", "25", "--db", db, "--account-id", ACCOUNT)

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "split rescale" in output
    assert "Re-run with --confirm 100" in output
    assert _adjustments(repo) == []


def test_confirm_refuses_when_the_owned_quantity_changed(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo = _repo(tmp_path)
    _fill(repo, "buy", 100.0, 50.0, T0)
    db = str(tmp_path / "runtime.sqlite3")

    exit_code = _cli(
        SYMBOL,
        "--broker-qty",
        "200",
        "--broker-average-cost",
        "25",
        "--db",
        db,
        "--account-id",
        ACCOUNT,
        "--confirm",
        "99",
    )

    assert exit_code == 1
    assert "not the 99" in capsys.readouterr().err
    assert _adjustments(repo) == []
    assert _owned(repo) == pytest.approx(100.0)


def test_confirm_needs_the_owned_quantity_value(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo = _repo(tmp_path)
    _fill(repo, "buy", 100.0, 50.0, T0)

    exit_code = _cli(SYMBOL, "--broker-qty", "200", "--db", str(tmp_path / "runtime.sqlite3"), "--confirm")

    assert exit_code == 2
    assert "needs the owned quantity" in capsys.readouterr().err
    assert _owned(repo) == pytest.approx(100.0)


def test_cli_refuses_an_unproven_increase_and_names_the_two_ways_out(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repo(tmp_path)
    _fill(repo, "buy", 100.0, 50.0, T0)

    exit_code = _cli(SYMBOL, "--broker-qty", "200", "--db", str(tmp_path / "runtime.sqlite3"), "--account-id", ACCOUNT)

    err = capsys.readouterr().err
    assert exit_code == 1
    assert "--broker-average-cost" in err
    assert "--acquisition-unit-cost" in err


def test_cli_acquisition_flag_books_the_extra_shares(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo = _repo(tmp_path)
    _fill(repo, "buy", 100.0, 50.0, T0)
    db = str(tmp_path / "runtime.sqlite3")

    exit_code = _cli(
        SYMBOL,
        "--broker-qty",
        "200",
        "--acquisition-unit-cost",
        "60",
        "--db",
        db,
        "--account-id",
        ACCOUNT,
        "--confirm",
        "100",
    )

    assert exit_code == 0
    assert "Ownership rebased" in capsys.readouterr().out
    assert _adjustments(repo) == [("operator_acquisition", 1.0, 100.0)]
    assert repo.owned_cost_bases(ACCOUNT, SYMBOL, Market.EQUITY) == pytest.approx((55.0, 55.0))


def _fake_config(db: str, *, universe: list[str], expected_account: str | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        runtime=SimpleNamespace(decision_db_path=db),
        broker=SimpleNamespace(expected_account_id=expected_account),
        universe=SimpleNamespace(equity=SimpleNamespace(symbols=universe)),
    )


def test_symbol_outside_the_universe_is_refused_when_not_owned(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path)
    _fill(repo, "buy", 100.0, 50.0, T0)
    db = str(tmp_path / "runtime.sqlite3")
    monkeypatch.setattr("parayabuck.cli.load_config", lambda _path=None: _fake_config(db, universe=["QQQ"]))

    exit_code = _cli("QLX", "--broker-qty", "50", "--db", db, "--account-id", ACCOUNT)

    assert exit_code == 1
    assert "not in the configured equity universe" in capsys.readouterr().err


def test_symbol_the_bot_holds_is_allowed_even_outside_the_universe(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path)
    _fill(repo, "buy", 100.0, 50.0, T0)
    db = str(tmp_path / "runtime.sqlite3")
    monkeypatch.setattr("parayabuck.cli.load_config", lambda _path=None: _fake_config(db, universe=["QQQ"]))

    exit_code = _cli(SYMBOL, "--broker-qty", "50", "--db", db, "--account-id", ACCOUNT)

    assert exit_code == 0
    assert "Dry run" in capsys.readouterr().out


def test_account_must_match_the_configured_expected_account(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path)
    _fill(repo, "buy", 100.0, 50.0, T0)
    db = str(tmp_path / "runtime.sqlite3")
    monkeypatch.setattr(
        "parayabuck.cli.load_config",
        lambda _path=None: _fake_config(db, universe=[SYMBOL], expected_account="PA-PINNED"),
    )

    exit_code = _cli(SYMBOL, "--broker-qty", "50", "--db", db, "--account-id", ACCOUNT, "--confirm", "100")

    assert exit_code == 1
    assert "broker.expected_account_id" in capsys.readouterr().err
    assert _owned(repo) == pytest.approx(100.0)
