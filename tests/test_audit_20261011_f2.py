"""F2 audit follow-ups (2026-10-11): integration remainders.

Covers the six F2 tasks: live-arm digest keeps cap/approval, planner
decision_id is required, consecutive_losses is unread, journal reads never
create the file, the degraded-reason helper is shared, and ManagedPosition
is trimmed to the fields the startup guard reads.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from parayabuck.core.models import AccountSnapshot, Market
from parayabuck.execution.ledger import ExecutionLedger, ManagedPosition
from parayabuck.execution.portfolio import AssetTradingRules, TargetToOrderPlanner
from parayabuck.monitoring import diagnostic as diagnostic_module
from parayabuck.monitoring import forensics as forensics_module
from parayabuck.monitoring.intent_watchdog import degraded_reasons
from parayabuck.monitoring.journal import EventJournal
from parayabuck.portfolio.models import TargetPortfolio, TargetWeight
from parayabuck.web.managers.engine import EngineManager
from parayabuck.web.routers.health import _intent_degraded_reasons
from parayabuck.web.services.dashboard import _dashboard_degraded_reasons

_NOW = datetime(2026, 8, 8, tzinfo=UTC)


def _manager(tmp_path: Path) -> EngineManager:
    manager = EngineManager()
    manager.config_path = tmp_path / "settings.yaml"
    manager.config_path.write_text(
        yaml.safe_dump(
            {
                "app": {"host": "127.0.0.1", "port": 8000},
                "runtime": {"mode": "paper", "decision_db_path": str(tmp_path / "runtime.sqlite3")},
                "risk": {"daily_loss_limit_pct": 0.015, "max_drawdown_pct": 0.10},
                "observability": {"journal_path": str(tmp_path / "journal.sqlite3")},
                "live": {"enabled": False, "hard_dollar_cap": 0.0, "operator_approval_path": ""},
                "universe": {"equity": {"enabled": True}},
                "strategy": {"equity": {"enabled": True, "name": "equity_leveraged_trend_v1"}},
            }
        ),
        encoding="utf-8",
    )
    manager.env_path = tmp_path / ".env"
    manager.engine_state = "stopped"
    return manager


# --- Task 1: cap/approval changes invalidate the arm ---


def test_f2_cap_change_invalidates_live_arm(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    raw = yaml.safe_load(manager.config_path.read_text(encoding="utf-8"))
    raw["live"]["hard_dollar_cap"] = 5000.0
    raw["live"]["operator_approval_path"] = str(tmp_path / "approval.json")
    armed = manager._digest_config(raw, paper=False)
    raw["runtime"]["mode"] = "live"
    raw["live"]["enabled"] = True
    assert manager._digest_config(raw, paper=False) == armed
    raw["live"]["hard_dollar_cap"] = 9999.0
    assert manager._digest_config(raw, paper=False) != armed


def test_f2_approval_path_change_invalidates_live_arm(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    raw = yaml.safe_load(manager.config_path.read_text(encoding="utf-8"))
    raw["live"]["hard_dollar_cap"] = 5000.0
    raw["live"]["operator_approval_path"] = str(tmp_path / "approval.json")
    armed = manager._digest_config(raw, paper=False)
    raw["live"]["operator_approval_path"] = str(tmp_path / "swapped.json")
    assert manager._digest_config(raw, paper=False) != armed


# --- Task 2: decision_id required ---


def _target() -> TargetPortfolio:
    return TargetPortfolio(
        strategy_name="approved",
        strategy_version="1.0.0",
        decision_timestamp=_NOW,
        weights=(TargetWeight("SPY", 0.10),),
        cash_weight=0.90,
    )


def test_f2_plan_requires_decision_id() -> None:
    account = AccountSnapshot(cash=90_000.0, equity=100_000.0, peak_equity=100_000.0, daily_pnl=0.0, positions=())
    with pytest.raises(TypeError):
        TargetToOrderPlanner().plan(  # type: ignore[call-arg]
            _target(),
            account,
            market=Market.EQUITY,
            prices={"SPY": 100.0},
            rules={"SPY": AssetTradingRules("SPY", Market.EQUITY, 0.01, 0.01, 0.01, 1.0)},
            approved_symbols=("SPY",),
        )


def test_f2_plan_with_explicit_decision_id() -> None:
    account = AccountSnapshot(cash=90_000.0, equity=100_000.0, peak_equity=100_000.0, daily_pnl=0.0, positions=())
    orders = TargetToOrderPlanner().plan(
        _target(),
        account,
        market=Market.EQUITY,
        prices={"SPY": 100.0},
        rules={"SPY": AssetTradingRules("SPY", Market.EQUITY, 0.01, 0.01, 0.01, 1.0)},
        approved_symbols=("SPY",),
        decision_id="f2-decision",
    )
    assert [order.symbol for order in orders] == ["SPY"]
    assert all(order.decision_id == "f2-decision" for order in orders)


# --- Task 3: consecutive_losses unread ---


def test_f2_old_payload_with_consecutive_losses_still_loads(tmp_path: Path) -> None:
    ledger = ExecutionLedger(str(tmp_path / "ledger.sqlite3"))
    try:
        today = datetime.now(UTC).date()
        payload = json.dumps(
            {
                "day_start_equity": 10_000.0,
                "peak_equity": 10_000.0,
                "realized_pnl": 0.0,
                "unrealized_pnl": 0.0,
                "fees": 0.0,
                "consecutive_losses": 6,
                "circuit_breaker_reason": None,
            },
            sort_keys=True,
        )
        with ledger._lock:
            ledger.connection.execute(
                "INSERT INTO risk_state(account_id, session_date, payload, updated_at) VALUES (?, ?, ?, ?)",
                ("acct", today.isoformat(), payload, datetime.now(UTC).isoformat()),
            )
            ledger.connection.commit()
        loaded = ledger.load_risk_state("acct", today)
        assert loaded is not None
        assert not hasattr(loaded, "consecutive_losses")
        # A fresh save drops the dead key from the payload.
        ledger.save_risk_state(loaded)
        with ledger._lock:
            row = ledger.connection.execute(
                "SELECT payload FROM risk_state WHERE account_id=? AND session_date=?",
                ("acct", today.isoformat()),
            ).fetchone()
        assert "consecutive_losses" not in json.loads(row[0])
    finally:
        ledger.close()


# --- Task 4: read paths never create the journal ---


def test_f2_read_only_journal_open_creates_nothing(tmp_path: Path) -> None:
    missing = tmp_path / "nope" / "journal.sqlite3"
    EventJournal.open_readonly(str(missing))
    assert not missing.exists()
    assert not (tmp_path / "nope").exists()


def test_f2_forensics_and_diagnostic_reads_create_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import parayabuck.monitoring.journal as journal_module
    from parayabuck.monitoring.operational_event import OperationalEvent

    journal_path = tmp_path / "journal.sqlite3"
    writer = EventJournal(str(journal_path))
    assert writer.emit(
        OperationalEvent(
            event_id="evt-1",
            occurred_at=datetime.now(UTC),
            kind="test_event",
            severity="info",
            message="hello",
            decision_id="d",
        )
    )
    writer.stop()
    before = journal_path.stat().st_mtime_ns
    created: list[Path] = []
    real_open = journal_module.EventJournal.open_readonly

    def _spy(cls: type[EventJournal], path: str | Path) -> EventJournal:
        created.append(Path(str(path)))
        return real_open(path)

    monkeypatch.setattr(journal_module.EventJournal, "open_readonly", classmethod(_spy))
    # A plain constructor must never be used on the read path: it would
    # ensure the schema (a write) against a file readers only query.
    real_init = journal_module.EventJournal.__init__

    def _refuse_init(self: EventJournal, *args: object, **kwargs: object) -> None:
        raise AssertionError("read path constructed a writing EventJournal")

    monkeypatch.setattr(journal_module.EventJournal, "__init__", _refuse_init)
    try:
        empty_db = tmp_path / "empty.sqlite3"
        sqlite3.connect(empty_db).close()
        result = forensics_module.decision_reconstruction(db_path=empty_db, decision_id="d", journal_path=journal_path)
        assert [event["event_id"] for event in result["operational_events"]] == ["evt-1"]
        assert [str(path) for path in created] == [str(journal_path)]
    finally:
        monkeypatch.setattr(journal_module.EventJournal, "__init__", real_init)
    data = diagnostic_module.collect_bundle_data(
        config_path=tmp_path / "nope.yaml",
        snapshot_path=tmp_path / "nope.json",
        journal_path=journal_path,
        decision_db_path=None,
    )
    assert [event["event_id"] for event in data["recent_events"]] == ["evt-1"]
    assert journal_path.stat().st_mtime_ns == before
    missing = tmp_path / "missing-journal.sqlite3"
    assert (
        diagnostic_module.collect_bundle_data(
            config_path=tmp_path / "nope.yaml",
            snapshot_path=tmp_path / "nope.json",
            journal_path=missing,
            decision_db_path=None,
        )["recent_events"]
        == []
    )
    assert not missing.exists()


def test_f2_legacy_guard_still_reads_symbol_status(tmp_path: Path) -> None:
    from parayabuck.execution.ledger import LegacyLedgerStateError

    db = tmp_path / "runtime.sqlite3"
    ExecutionLedger(str(db)).close()
    with sqlite3.connect(db) as connection:
        connection.execute(
            "INSERT INTO managed_positions(account_id, canonical_symbol, market, strategy, source_signal_id,"
            " entry_client_order_id, owned_qty, filled_avg_price, original_stop, original_target,"
            " status, opened_at, remaining_owned_qty)"
            " VALUES ('a', 'QLD', 'equity', 's', 'sig', 'e1', 10, 100, 90, 120, 'open', '2026-01-01', 10)"
        )
        connection.commit()
    # The startup guard still fires on the legacy open row; the trimmed row
    # mapper keeps the symbol/market/status it reports.
    with pytest.raises(LegacyLedgerStateError, match="QLD"):
        ExecutionLedger(str(db))
    item = ExecutionLedger._managed_from_row(
        (
            "a",
            "QLD",
            "equity",
            "s",
            "sig",
            "e1",
            None,
            10,
            10,
            0,
            100,
            90,
            120,
            None,
            0,
            0,
            0,
            "unprotected",
            "open",
            None,
            None,
            "pending",
            "watching",
            None,
            None,
            None,
        )
    )
    assert (item.canonical_symbol, item.market, item.status) == ("QLD", Market.EQUITY, "open")


def test_f2_degraded_helpers_agree() -> None:
    health_obj = SimpleNamespace(intent_watchdog={"degraded_reasons": ["equity_decision_missing"]})
    health_dict = {"intent_watchdog": {"degraded_reasons": ["equity_decision_missing"]}}
    assert degraded_reasons(health_obj) == ["equity_decision_missing"]
    assert _intent_degraded_reasons(health_obj) == ["equity_decision_missing"]
    assert _dashboard_degraded_reasons(health_dict) == ["equity_decision_missing"]
    assert degraded_reasons(None) == []
    assert _intent_degraded_reasons(None) == []
    assert _dashboard_degraded_reasons(None) == []


# --- Task 6: ManagedPosition trimmed ---


def test_f2_managed_position_holds_only_guard_fields() -> None:
    import dataclasses

    assert [field.name for field in dataclasses.fields(ManagedPosition)] == [
        "canonical_symbol",
        "market",
        "status",
    ]
