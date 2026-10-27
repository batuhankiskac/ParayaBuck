"""Regression tests for audit wave 2 fixes J2 (R2-2, F2-2, F2-3-8, F2-5, R2-10).

Every test uses tmp_path SQLite and synthetic rows. No network, no credentials,
no production paths.
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from parayabuck.backtest import regulatory_fees
from parayabuck.backtest.regulatory_fees import rates_for_trade_date
from parayabuck.core.models import Bar, Market
from parayabuck.data.cache import WarmupCache
from parayabuck.execution.ledger import ExecutionLedger
from parayabuck.monitoring.forensics import find_stuck_rebalances
from parayabuck.monitoring.metrics import _risk_state_metrics
from parayabuck.portfolio.runtime_repository import PortfolioStateRepository
from parayabuck.storage import RUNTIME_SCHEMA_VERSION, SQLiteStore
from parayabuck.tax.errors import LedgerError
from parayabuck.tax.ledger import (
    Fill,
    assert_fees_observed,
    build_disposals,
    match_disposals,
    read_fills,
    unobserved_fee_symbols,
)
from parayabuck.tax.rates import RateBook

ACCOUNT = "ACC-J2"
T0 = datetime(2026, 1, 5, 14, 0, tzinfo=UTC)


def _user_version(path: Path) -> int:
    with closing(sqlite3.connect(path)) as connection:
        return int(connection.execute("PRAGMA user_version").fetchone()[0])


def _seed_session(repo: PortfolioStateRepository) -> None:
    connection = repo.store.connection
    connection.execute(
        "INSERT INTO portfolio_runtime_sessions VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("SESSION-J2", ACCOUNT, "cfg-digest", "paper", T0.isoformat(), None, "running"),
    )
    connection.commit()


def _seed_order(
    repo: PortfolioStateRepository,
    client_order_id: str,
    *,
    side: str = "buy",
    quantity: float = 10.0,
    reference_price: float = 100.0,
    created_at: datetime = T0,
) -> None:
    """One decision plus one order in the runtime schema, created at ``created_at``."""
    connection = repo.store.connection
    decision_id = f"DEC-{client_order_id}"
    connection.execute(
        "INSERT INTO portfolio_decisions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            decision_id,
            "SESSION-J2",
            ACCOUNT,
            "cfg-digest",
            created_at.isoformat(),
            "equity",
            "test",
            "{}",
            "{}",
        ),
    )
    connection.execute(
        """INSERT INTO portfolio_orders (client_order_id, decision_id, symbol, market, side, quantity,
               reference_price, status, broker_order_id, cumulative_filled_quantity, average_fill_price,
               created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            client_order_id,
            decision_id,
            "QLD",
            "equity",
            side,
            quantity,
            reference_price,
            "new",
            "",
            0.0,
            None,
            created_at.isoformat(),
        ),
    )
    connection.commit()


def _apply(repo: PortfolioStateRepository, client_order_id: str, **kwargs: object) -> bool:
    arguments: dict[str, object] = {
        "client_order_id": client_order_id,
        "broker_order_id": "B-" + client_order_id,
        "status": "filled",
        "cumulative_filled_quantity": 10.0,
        "average_fill_price": 100.0,
        "occurred_at": T0 + timedelta(minutes=1),
        "activity_id": f"{client_order_id}-{kwargs.get('status', 'filled')}-"
        f"{kwargs.get('cumulative_filled_quantity', 10.0)}",
        "fee": None,
        "realized_pnl": 0.0,
        "unrealized_pnl": 0.0,
        "source": "stream",
    }
    arguments.update(kwargs)
    return repo.apply_trade_update(**arguments)  # type: ignore[arg-type]


def _order_status(repo: PortfolioStateRepository, client_order_id: str) -> str:
    row = repo.store.connection.execute(
        "SELECT status FROM portfolio_orders WHERE client_order_id = ?", (client_order_id,)
    ).fetchone()
    return str(row[0])


# -- F2-2-3: a filled row is not relabeled by a late terminal event ----------


def test_filled_order_keeps_filled_status_against_a_late_cancel(tmp_path: Path) -> None:
    repo = PortfolioStateRepository(tmp_path / "runtime.sqlite3")
    _seed_session(repo)
    _seed_order(repo, "O-FILL")
    assert _apply(repo, "O-FILL", status="filled")

    assert _apply(repo, "O-FILL", status="canceled", activity_id="O-FILL-late-cancel")

    assert _order_status(repo, "O-FILL") == "filled"
    repo.close()


def test_late_fill_on_a_canceled_order_still_moves_it_to_filled(tmp_path: Path) -> None:
    repo = PortfolioStateRepository(tmp_path / "runtime.sqlite3")
    _seed_session(repo)
    _seed_order(repo, "O-CANCEL")
    assert _apply(repo, "O-CANCEL", status="canceled", cumulative_filled_quantity=0.0, average_fill_price=None)

    assert _apply(repo, "O-CANCEL", status="filled", cumulative_filled_quantity=10.0, average_fill_price=100.0)

    assert _order_status(repo, "O-CANCEL") == "filled"
    repo.close()


# -- F2-2-2: the frame's execution price prices a matching delta -------------


def _fill_price(repo: PortfolioStateRepository, activity_id: str) -> float:
    row = repo.store.connection.execute(
        "SELECT price FROM portfolio_fills WHERE activity_id = ?", (activity_id,)
    ).fetchone()
    assert row is not None
    return float(row[0])


def test_exact_execution_price_prices_a_matching_delta(tmp_path: Path) -> None:
    repo = PortfolioStateRepository(tmp_path / "runtime.sqlite3")
    _seed_session(repo)
    _seed_order(repo, "O-EXACT")
    assert _apply(
        repo,
        "O-EXACT",
        status="partial_fill",
        cumulative_filled_quantity=4.0,
        average_fill_price=100.0,
        activity_id="E-1",
        execution_price=100.0,
        execution_quantity=4.0,
    )

    # The cumulative average 110.0 rounds the second delta to a blended figure;
    # the frame's own price 121.0 for the 6 new shares is exact.
    assert _apply(
        repo,
        "O-EXACT",
        status="filled",
        cumulative_filled_quantity=10.0,
        average_fill_price=110.0,
        activity_id="E-2",
        execution_price=121.0,
        execution_quantity=6.0,
    )

    assert _fill_price(repo, "E-2") == pytest.approx(121.0)
    repo.close()


def test_mismatched_execution_quantity_falls_back_to_the_derived_price(tmp_path: Path) -> None:
    repo = PortfolioStateRepository(tmp_path / "runtime.sqlite3")
    _seed_session(repo)
    _seed_order(repo, "O-MISMATCH")
    assert _apply(
        repo,
        "O-MISMATCH",
        status="partial_fill",
        cumulative_filled_quantity=4.0,
        average_fill_price=100.0,
        activity_id="M-1",
        execution_price=100.0,
        execution_quantity=4.0,
    )

    # The frame reports 5 shares, not the 6 this delta books: its price is not used.
    assert _apply(
        repo,
        "O-MISMATCH",
        status="filled",
        cumulative_filled_quantity=10.0,
        average_fill_price=110.0,
        activity_id="M-2",
        execution_price=121.0,
        execution_quantity=5.0,
    )

    assert _fill_price(repo, "M-2") == pytest.approx((110.0 * 10.0 - 100.0 * 4.0) / 6.0)
    repo.close()


# -- F2-3-8: an unobserved fee is recorded as unobserved, not as a free fill --


def test_unobserved_fee_is_flagged_and_an_observed_zero_fee_is_not(tmp_path: Path) -> None:
    repo = PortfolioStateRepository(tmp_path / "runtime.sqlite3")
    _seed_session(repo)
    _seed_order(repo, "O-FEE")
    assert _apply(repo, "O-FEE", status="partial_fill", cumulative_filled_quantity=4.0, activity_id="F-1")
    assert _apply(
        repo,
        "O-FEE",
        status="filled",
        cumulative_filled_quantity=10.0,
        activity_id="F-2",
        fee=0.0,
        fee_observed=True,
    )

    flags = dict(repo.store.connection.execute("SELECT activity_id, fee_observed FROM portfolio_fills").fetchall())
    assert flags == {"F-1": 0, "F-2": 1}
    repo.close()


def test_migration_backfills_fee_observed_from_the_execution_journal(tmp_path: Path) -> None:
    path = tmp_path / "runtime.sqlite3"
    repo = PortfolioStateRepository(path)
    _seed_session(repo)
    _seed_order(repo, "O-LEGACY")
    assert _apply(repo, "O-LEGACY", status="partial_fill", cumulative_filled_quantity=4.0, activity_id="L-1")
    assert _apply(repo, "O-LEGACY", status="filled", activity_id="L-2", fee=0.5, fee_observed=True)
    # Make the database look like one written before the column existed.
    repo.store.connection.execute("ALTER TABLE portfolio_fills DROP COLUMN fee_observed")
    repo.store.connection.commit()
    repo.close()

    reopened = PortfolioStateRepository(path)
    flags = dict(reopened.store.connection.execute("SELECT activity_id, fee_observed FROM portfolio_fills").fetchall())
    reopened.close()

    assert flags == {"L-1": 0, "L-2": 1}


# -- R2-2-1: the dry run refuses an unmigrated schema instead of crashing ----


def test_dry_run_refuses_a_schema_without_unit_cost_with_the_remedy(tmp_path: Path) -> None:
    path = tmp_path / "runtime.sqlite3"
    PortfolioStateRepository(path).close()
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("ALTER TABLE portfolio_ownership_adjustments DROP COLUMN unit_cost")
        connection.commit()

    repository = PortfolioStateRepository.open_read_only(path)
    try:
        plan = repository.plan_ownership_rebase(ACCOUNT, "QLD", Market.EQUITY, 10.0)
    finally:
        repository.close()

    assert plan.refused
    assert "start the engine" in plan.reason


# -- R2-2-3: the ledger no longer stamps the schema before the repository ---


def test_ledger_alone_does_not_stamp_the_schema_version(tmp_path: Path) -> None:
    path = tmp_path / "runtime.sqlite3"
    ExecutionLedger(str(path)).close()
    assert _user_version(path) == 0

    PortfolioStateRepository(path).close()
    assert _user_version(path) == RUNTIME_SCHEMA_VERSION


# -- R2-10-1: the latest-decision query has its index -----------------------


def test_latest_decision_index_exists_after_migration(tmp_path: Path) -> None:
    path = tmp_path / "runtime.sqlite3"
    PortfolioStateRepository(path).close()
    with closing(sqlite3.connect(path)) as connection:
        names = {row[1] for row in connection.execute("PRAGMA index_list(portfolio_decisions)")}
        columns = [row[2] for row in connection.execute("PRAGMA index_info(idx_portfolio_decisions_account_market_ts)")]

    assert "idx_portfolio_decisions_account_market_ts" in names
    assert columns == ["account_id", "market", "decision_timestamp"]


# -- R2-10-6: float residue does not keep an ownership adjustment open ------


def _stale_clear(repo: PortfolioStateRepository, quantity: float) -> None:
    repo.record_ownership_adjustment(
        ACCOUNT,
        "QLD",
        Market.EQUITY,
        quantity,
        kind="stale_ownership_clear",
        note="test",
    )


def test_consume_closes_a_row_left_with_float_residue(tmp_path: Path) -> None:
    repo = PortfolioStateRepository(tmp_path / "runtime.sqlite3")
    _stale_clear(repo, 2.0)
    early = datetime(2000, 1, 1, tzinfo=UTC)

    with repo.store.lock, repo.store.connection:
        # A sell for 2 shares minus a residue below the tolerance closes the row.
        repo._consume_ownership_adjustments_tx(ACCOUNT, "QLD", Market.EQUITY, 2.0 - 5e-10, early)

    consumed = repo.store.connection.execute(
        "SELECT consumed_quantity FROM portfolio_ownership_adjustments WHERE symbol = 'QLD'"
    ).fetchone()[0]
    assert float(consumed) == pytest.approx(2.0)
    repo.close()


def test_consume_skips_a_row_whose_only_residue_is_below_tolerance(tmp_path: Path) -> None:
    repo = PortfolioStateRepository(tmp_path / "runtime.sqlite3")
    _stale_clear(repo, 2.0)
    early = datetime(2000, 1, 1, tzinfo=UTC)
    repo.store.connection.execute("UPDATE portfolio_ownership_adjustments SET consumed_quantity = 2.0 - 1e-16")
    repo.store.connection.commit()

    with repo.store.lock, repo.store.connection:
        repo._consume_ownership_adjustments_tx(ACCOUNT, "QLD", Market.EQUITY, 1.0, early)

    consumed = repo.store.connection.execute("SELECT consumed_quantity FROM portfolio_ownership_adjustments").fetchone()
    assert float(consumed[0]) == pytest.approx(2.0 - 1e-16)
    repo.close()


# -- F2-5-2: a date past the verified schedule is flagged, not silently used -


def test_date_past_the_verified_horizon_uses_newest_rates_and_flags_once(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(regulatory_fees, "_beyond_horizon_flagged", False)

    late = rates_for_trade_date(date(2027, 3, 1))

    assert regulatory_fees._beyond_horizon_flagged is True
    assert late == rates_for_trade_date(date(2026, 12, 31))
    assert late.taf_per_share == pytest.approx(0.000195)


def test_date_inside_the_horizon_does_not_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(regulatory_fees, "_beyond_horizon_flagged", False)

    rates_for_trade_date(date(2026, 8, 1))

    assert regulatory_fees._beyond_horizon_flagged is False


# -- F2-5-3 / R2-10-4: WAL size cap is set on every store --------------------


def test_sqlite_store_caps_the_wal_size_after_checkpoint(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "runtime.sqlite3")
    try:
        limit = store.connection.execute("PRAGMA journal_size_limit").fetchone()[0]
    finally:
        store.connection.close()

    assert limit == 64 * 1024 * 1024


# -- R2-10-5: warmup cache retention --------------------------------------------


def test_warmup_cache_prunes_bars_before_the_cutoff_only(tmp_path: Path) -> None:
    cache = WarmupCache(str(tmp_path / "warmup.sqlite3"))
    try:
        bars = [
            Bar(
                timestamp=datetime(2026, 1, day, tzinfo=UTC),
                symbol="QLD",
                timeframe="1Day",
                open=1.0,
                high=1.0,
                low=1.0,
                close=1.0,
                volume=1.0,
                market=Market.EQUITY,
            )
            for day in (1, 2, 3)
        ]
        cache.upsert(bars, feed="iex")

        removed = cache.prune_before(datetime(2026, 1, 3, tzinfo=UTC))

        remaining = [row[0] for row in cache.connection.execute("SELECT timestamp FROM bars ORDER BY timestamp")]
    finally:
        cache.close()

    assert removed == 2
    assert len(remaining) == 1
    assert remaining[0].startswith("2026-01-03")


# -- R2-10-3: bounded monitoring reads --------------------------------------


def test_risk_state_metrics_takes_the_newest_readable_payload_per_account(tmp_path: Path) -> None:
    path = tmp_path / "risk.sqlite3"
    with closing(sqlite3.connect(path)) as connection:
        connection.execute(
            "CREATE TABLE risk_state (account_id TEXT NOT NULL, session_date TEXT NOT NULL, "
            "payload TEXT NOT NULL, updated_at TEXT NOT NULL, PRIMARY KEY(account_id, session_date))"
        )
        connection.executemany(
            "INSERT INTO risk_state VALUES (?, ?, ?, ?)",
            [
                ("ACC-A", "2026-10-01", '{"halted": false}', "2026-10-01T21:00:00+00:00"),
                ("ACC-A", "2026-10-02", "not json", "2026-10-02T21:00:00+00:00"),
                ("ACC-B", "2026-10-02", '{"halted": true}', "2026-10-02T21:00:00+00:00"),
            ],
        )
        connection.commit()
        with closing(sqlite3.connect(f"file:{path}?mode=ro", uri=True)) as readonly:
            everything = _risk_state_metrics(readonly, None)
            only_a = _risk_state_metrics(readonly, "ACC-A")

    latest = {row["account_id"]: row for row in everything["latest"]}  # type: ignore[index]
    assert set(latest) == {"ACC-A", "ACC-B"}
    assert latest["ACC-A"]["session_date"] == "2026-10-01"
    assert latest["ACC-A"]["halted"] is False
    assert latest["ACC-B"]["halted"] is True
    assert [row["account_id"] for row in only_a["latest"]] == ["ACC-A"]  # type: ignore[index]


def test_forensics_stuck_rebalance_reads_only_open_equity_rows(tmp_path: Path) -> None:
    path = tmp_path / "runtime.sqlite3"
    repo = PortfolioStateRepository(path)
    _seed_session(repo)
    stale = (datetime.now(UTC) - timedelta(hours=3)).isoformat()
    rows = [
        ("R-OPEN", "equity", "sell_pending", stale),
        ("R-DONE", "equity", "complete", stale),
        ("R-CRYPTO", "crypto", "sell_pending", stale),
    ]
    for index, (decision_id, market, phase, updated_at) in enumerate(rows):
        repo.store.connection.execute(
            "INSERT INTO portfolio_decisions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                f"DEC-{decision_id}",
                "SESSION-J2",
                ACCOUNT,
                "cfg-digest",
                (T0 + timedelta(minutes=index)).isoformat(),
                market,
                "t",
                "{}",
                "{}",
            ),
        )
        repo.store.connection.execute(
            "INSERT INTO portfolio_rebalance_executions(decision_id, account_id, market, phase, status, "
            "buy_orders_json, updated_at) VALUES (?, ?, ?, ?, 'active', '[]', ?)",
            (f"DEC-{decision_id}", ACCOUNT, market, phase, updated_at),
        )
    repo.store.connection.commit()
    repo.close()

    stuck = find_stuck_rebalances(path, threshold_seconds=60)

    assert [row["decision_id"] for row in stuck] == ["DEC-R-OPEN"]


# -- F2-3-8 (tax): unobserved fees refuse the disposals that use them --------


def _rates() -> RateBook:
    days = {}
    day = date(2024, 11, 1)
    while day <= date(2027, 1, 31):
        days[day] = 40.0
        day += timedelta(days=1)
    months = {f"{year}-{month:02d}": 3000.0 for year in (2024, 2025, 2026, 2027) for month in range(1, 13)}
    return RateBook(usd_try=days, yiufe=months)


def _fill(fill_id: str, side: str, when: datetime, fee: float, observed: bool) -> Fill:
    return Fill(
        fill_id=fill_id,
        symbol="QLD",
        side=side,
        occurred_at=when,
        quantity=10.0,
        price=100.0,
        fee=fee,
        fee_observed=observed,
    )


def test_assert_fees_observed_refuses_only_disposals_that_use_an_unobserved_fill() -> None:
    buy = _fill("B1", "buy", datetime(2025, 3, 10, 12, tzinfo=UTC), 0.0, observed=False)
    sell = _fill("S1", "sell", datetime(2026, 2, 12, 12, tzinfo=UTC), 1.0, observed=True)
    other_buy = _fill("B2", "buy", datetime(2025, 4, 10, 12, tzinfo=UTC), 0.5, observed=True)
    other_sell = _fill("S2", "sell", datetime(2026, 5, 12, 12, tzinfo=UTC), 0.5, observed=True)
    fills = [buy, other_buy, sell, other_sell]
    # FIFO: S1 sells the unobserved lot B1; S2 sells the clean lot B2.
    disposals = match_disposals(fills, _rates())

    with pytest.raises(LedgerError, match="komisyon"):
        assert_fees_observed(disposals, fills)
    clean = [item for item in disposals if item.buy_fill_id == "B2"]
    assert clean
    assert_fees_observed(clean, fills)


def test_build_disposals_keeps_and_flags_an_unobserved_fee_in_the_reported_year(tmp_path: Path) -> None:
    path = tmp_path / "runtime.sqlite3"
    repo = PortfolioStateRepository(path)
    _seed_session(repo)
    _seed_order(repo, "T-BUY", side="buy", created_at=datetime(2025, 3, 10, 12, tzinfo=UTC))
    _seed_order(repo, "T-SELL", side="sell", created_at=datetime(2026, 2, 12, 12, tzinfo=UTC))
    assert _apply(
        repo,
        "T-BUY",
        status="filled",
        cumulative_filled_quantity=10.0,
        average_fill_price=80.0,
        occurred_at=datetime(2025, 3, 10, 12, tzinfo=UTC),
        activity_id="TB-1",
        fee=None,
    )
    assert _apply(
        repo,
        "T-SELL",
        status="filled",
        cumulative_filled_quantity=10.0,
        average_fill_price=100.0,
        occurred_at=datetime(2026, 2, 12, 12, tzinfo=UTC),
        activity_id="TS-1",
        fee=1.0,
        fee_observed=True,
    )
    repo.close()

    with closing(sqlite3.connect(f"file:{path}?mode=ro", uri=True)) as connection:
        assert [fill.fee_observed for fill in read_fills(connection, ACCOUNT)] == [False, True]
        # A zero unobserved fee can only overstate the gain, so the disposal is
        # kept (conservative) and the symbol is reported for a note.
        disposals = build_disposals(connection, ACCOUNT, _rates(), year=2026)
        assert [item.symbol for item in disposals] == ["QLD"]
        assert unobserved_fee_symbols(disposals, read_fills(connection, ACCOUNT)) == ["QLD"]
        assert build_disposals(connection, ACCOUNT, _rates(), year=2025) == []


def test_read_fills_refuses_a_database_without_the_fee_flag(tmp_path: Path) -> None:
    path = tmp_path / "runtime.sqlite3"
    PortfolioStateRepository(path).close()
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("ALTER TABLE portfolio_fills DROP COLUMN fee_observed")
        connection.commit()

    with closing(sqlite3.connect(f"file:{path}?mode=ro", uri=True)) as connection:
        with pytest.raises(LedgerError, match="fee_observed"):
            read_fills(connection, ACCOUNT)
