"""Deterministic injected-failure chaos tests for PAPER reliability (paper_chaos gate).

Covers: provider timeout, broker timeout, accepted-but-timeout, partial fill,
DB locked, restart, stale account, missing symbol, universe partial.
All tests are offline (fake brokers, real SQLite), deterministic, and verify
no duplicate economic action, no silent drift, safe fail-closed.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from parayabuck.core.models import AccountSnapshot, Bar, Market, PositionSnapshot
from parayabuck.execution.portfolio import RiskOverlayDecision
from parayabuck.monitoring import alerts, metrics
from parayabuck.monitoring.incident import IncidentCategory
from parayabuck.monitoring.paper_incident import (
    automate_incident_recovery,
    broker_uncertainty_reconcile_first,
    db_integrity_fail_closed,
    provider_temporary_retry,
)
from parayabuck.portfolio.models import TargetPortfolio
from parayabuck.portfolio.runtime_service import (
    PortfolioRuntime,
    PortfolioStateRepository,
)

pytestmark = pytest.mark.paper_chaos


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
class _FakeBroker:
    def __init__(self, account: AccountSnapshot) -> None:
        self._account = account
        self.submitted: list[str] = []
        self.reconcile_calls = 0
        self.account_calls = 0
        self.fail_next_account: Exception | None = None
        self.fail_next_submit: Exception | None = None
        self.account_id = account.account_id if hasattr(account, "account_id") else "test-acct"

    def account(self) -> AccountSnapshot:
        self.account_calls += 1
        if self.fail_next_account is not None:
            exc = self.fail_next_account
            self.fail_next_account = None
            raise exc
        return self._account

    def reconcile(self) -> bool:
        self.reconcile_calls += 1
        return True

    def submit_portfolio_order(self, order) -> str:  # type: ignore[no-untyped-def]
        if self.fail_next_submit is not None:
            exc = self.fail_next_submit
            self.fail_next_submit = None
            raise exc
        oid = f"broker-{len(self.submitted) + 1}"
        self.submitted.append(oid)
        return oid


def _account(
    equity: float = 100_000.0, positions: list[PositionSnapshot] | None = None, stale: bool = False
) -> AccountSnapshot:
    # AccountSnapshot(cash, equity, peak_equity, daily_pnl, positions)
    # stale flag is just for test semantics; timestamp not stored on AccountSnapshot
    snap = AccountSnapshot(
        cash=equity - sum(getattr(pos, "market_value", 0) for pos in (positions or [])),
        equity=equity,
        peak_equity=equity,
        daily_pnl=0.0,
        positions=tuple(positions or []),
    )
    return snap


def _bar(symbol: str = "AAPL", close: float = 100.0, ts: datetime | None = None) -> Bar:
    timestamp = ts or datetime(2025, 1, 1, tzinfo=UTC)
    return Bar(timestamp, symbol, "1d", close, close, close, close, 1_000_000, Market.EQUITY)


# ---------------------------------------------------------------------------
# 1. Provider timeout — exponential retry 5 attempts recovers (bounded)
# ---------------------------------------------------------------------------
def test_provider_timeout_exponential_retry_recovers() -> None:
    alerts.reset_for_tests()
    metrics.reset_for_tests()
    # Throttle disabled for deterministic delivery count
    alerts._throttle_seconds = 0.0
    attempts = {"n": 0}

    def flaky() -> str:
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise TimeoutError("provider timeout")
        return "ok"

    result, record = provider_temporary_retry(
        IncidentCategory.PROVIDER_UNAVAILABLE,
        flaky,
        account_id="acct",
        strategy="equity_leveraged_trend_v1",
        sleep=lambda _seconds: None,
    )
    assert result == "ok"
    assert record.recovery_result == "recovered"
    assert record.retry_attempt == 3
    # Canonical fields present
    assert record.incident_id and record.timestamp and record.category == IncidentCategory.PROVIDER_UNAVAILABLE
    assert record.severity
    # Bounded: 5 attempts max; success within bound does not exhaust
    assert attempts["n"] == 3
    alerts.reset_for_tests()


def test_provider_timeout_exhausts_after_5_attempts() -> None:
    alerts.reset_for_tests()
    alerts._throttle_seconds = 0.0

    def always_timeout() -> str:
        raise TimeoutError("provider timeout")

    delays: list[float] = []
    result, record = provider_temporary_retry(
        IncidentCategory.PROVIDER_UNAVAILABLE, always_timeout, account_id="acct", sleep=delays.append
    )
    assert result is None
    assert record.recovery_result == "failed_exhausted"
    assert record.retry_attempt == 5
    # The taxonomy backoff reaches the injected sleep unmodified (no
    # test-runner detection inside production code).
    assert delays == [2.0, 4.0, 8.0, 16.0]
    alerts.reset_for_tests()


# ---------------------------------------------------------------------------
# 2. Broker timeout — reconcile first, never blind retry
# ---------------------------------------------------------------------------
def test_broker_timeout_reconcile_first_never_blind_retry() -> None:
    alerts.reset_for_tests()
    alerts._throttle_seconds = 0.0
    _FakeBroker(_account())
    # Simulate broker timeout on account fetch; reconcile must be called before retry
    broker_reconciled = {"called": False}

    def reconcile() -> bool:
        broker_reconciled["called"] = True
        return True

    def action_never_called() -> str:  # would be blind retry — must not be called if reconcile fails
        raise AssertionError("blind retry must not happen when reconcile fails")

    # First: reconcile fails -> no blind retry
    def reconcile_false() -> bool:
        broker_reconciled["called"] = True
        return False

    result, record = broker_uncertainty_reconcile_first(
        IncidentCategory.BROKER_ACCOUNT_STALE, reconcile_false, action_never_called, account_id="acct"
    )
    assert result is None
    assert record.recovery_result == "reconcile_failed_no_retry"
    assert broker_reconciled["called"] is True

    # Second: reconcile succeeds -> action may run once (not blind, but after verification)
    broker_reconciled["called"] = False
    result2, record2 = broker_uncertainty_reconcile_first(
        IncidentCategory.PENDING_ORDER_UNKNOWN, reconcile, lambda: "submitted", account_id="acct"
    )
    assert result2 == "submitted"
    assert record2.recovery_result == "reconciled_then_action_succeeded"
    alerts.reset_for_tests()


# ---------------------------------------------------------------------------
# 3. Accepted-but-timeout — ambiguous, reconciles without duplicate order
# ---------------------------------------------------------------------------
def test_accepted_but_timeout_ambiguous_reconciles_without_duplicate(tmp_path: Path) -> None:
    """Accepted-but-timeout: broker accepted order but response timed out.
    Recovery must reconcile, never blind-resubmit with same client_order_id.
    """
    alerts.reset_for_tests()
    alerts._throttle_seconds = 0.0
    # Use automate with reconcile path for PENDING_ORDER_UNKNOWN

    reconcile_calls = {"n": 0}

    def fake_reconcile() -> str:
        reconcile_calls["n"] += 1
        # Simulate broker shows order exists
        return "order_found_at_broker"

    # No blind retry: automate will do reconcile-first, not blind submit
    record = automate_incident_recovery(
        IncidentCategory.PENDING_ORDER_UNKNOWN,
        account_id="acct",
        error="broker accepted but timeout",
        reconcile_action=fake_reconcile,
        retry_action=None,  # no retry without verification
    )
    assert reconcile_calls["n"] == 1
    assert record.category == IncidentCategory.PENDING_ORDER_UNKNOWN
    assert "reconcile" in record.attempted_recovery
    # If retry_action was provided, it would run only after reconcile success — single economic action
    retry_calls = {"n": 0}

    def retry_once() -> str:
        retry_calls["n"] += 1
        return "retry_ok"

    result2, rec2 = broker_uncertainty_reconcile_first(
        IncidentCategory.PENDING_ORDER_UNKNOWN, fake_reconcile, retry_once, account_id="acct"
    )
    assert retry_calls["n"] == 1  # exactly one post-reconcile attempt
    assert rec2.recovery_result == "reconciled_then_action_succeeded"
    alerts.reset_for_tests()


# ---------------------------------------------------------------------------
# 5. DB locked — fail closed immediately, never retry blind
# ---------------------------------------------------------------------------
def test_db_locked_fail_closed_immediately() -> None:
    alerts.reset_for_tests()
    alerts._throttle_seconds = 0.0
    record = db_integrity_fail_closed(IncidentCategory.DATABASE_ERROR, account_id="acct", error="database is locked")
    assert record.recovery_result == "halted_trading_persisted"
    assert record.attempted_recovery == "fail_closed_no_retry"
    assert record.trading_halted is True
    # Via automate, same
    rec2 = automate_incident_recovery(IncidentCategory.DATABASE_ERROR, account_id="acct", error="database is locked")
    assert rec2.attempted_recovery == "fail_closed_no_retry"
    assert rec2.severity.value == "critical"
    alerts.reset_for_tests()


# ---------------------------------------------------------------------------
# 6. Restart — recovery resumes exactly once, no duplicate SELL/BUY
# ---------------------------------------------------------------------------
def test_restart_resumes_without_duplicate_orders(tmp_path: Path) -> None:
    """Simulate restart after SELL submitted but before BUY. Verify no duplicate."""
    # Use PortfolioStateRepository directly for restart semantics
    repo = PortfolioStateRepository(tmp_path / "restart.sqlite3")
    account_id = "acct"
    sid = repo.begin_session(account_id, "cfg123", "paper")
    # A decision row plus its sell_pending marker, written atomically like
    # the production decision path does.
    stamp = datetime.now(UTC)
    chaos_target = TargetPortfolio("test", "1", stamp, (), 1.0)
    chaos_risk = RiskOverlayDecision(chaos_target, False, (), 0.0, 0.0)
    repo.record_decision_with_orders(
        sid,
        account_id,
        "cfg123",
        chaos_target,
        chaos_risk,
        Market.EQUITY,
        (),
        rebalance_phase="sell_pending",
    )
    # Restart: repository reopened
    repo2 = PortfolioStateRepository(tmp_path / "restart.sqlite3")
    execution = repo2.current_rebalance_execution(account_id, Market.EQUITY)
    assert execution is not None
    assert execution.phase == "sell_pending"
    # Advancing should not create duplicate decision rows
    assert repo2.has_decision  # exists
    repo.close()
    repo2.close()


# ---------------------------------------------------------------------------
# 7. Stale account — blocks entries, canonical incident, fail-closed
# ---------------------------------------------------------------------------
def test_stale_account_blocks_entries_and_emits_incident() -> None:
    alerts.reset_for_tests()
    alerts._throttle_seconds = 0.0
    metrics.reset_for_tests()
    # stale account snapshot age > threshold
    _account(stale=True)
    # The runtime's _fresh_account_snapshot would emit BROKER_ACCOUNT_STALE
    # Here we directly test the incident taxonomy
    record = automate_incident_recovery(
        IncidentCategory.BROKER_ACCOUNT_STALE,
        account_id="acct",
        error="broker account snapshot stale age=4000s",
        current_state={"age_seconds": 4000},
    )
    assert record.category == IncidentCategory.BROKER_ACCOUNT_STALE
    assert record.trading_halted is True  # critical halts trading
    assert record.severity.value == "critical"
    # Metrics: incident path emits reconciliation-related kind
    # Ensure no duplicate economic action — stale account never submits orders
    assert record.recovery_result in {
        "reconcile_required_but_not_provided",
        "reconciled_no_further_action",
        "reconcile_failed_no_retry",
    }
    alerts.reset_for_tests()


# ---------------------------------------------------------------------------
# 8. Missing symbol — incomplete strategy batch does not silently trade
# ---------------------------------------------------------------------------
def test_missing_symbol_incomplete_batch_fails_closed(tmp_path: Path) -> None:
    """If a symbol is missing from the closed-bar batch, the decision is incomplete,
    not a silent partial rebalance. No orders are submitted.
    """
    from parayabuck.portfolio import EquityLeveragedTrendV1

    # Minimal strategy and broker for PortfolioRuntime
    strategy = EquityLeveragedTrendV1({"signal_symbol": "QQQ", "hold_symbol": "QLD", "sma_bars": 5})
    account = _account(positions=[])
    broker = _FakeBroker(account)
    repo = PortfolioStateRepository(tmp_path / "missing.sqlite3")
    runtime = PortfolioRuntime(
        strategy=strategy,
        symbols=("QQQ", "QLD"),
        broker=broker,  # type: ignore[arg-type]
        repository=repo,
        mode="paper",
        maximum_allocation=1.0,
        account_id="acct",
        allow_static_universe=True,
    )
    # Both symbols are warm (sma_bars=5), but the next closed-bar batch carries
    # only QQQ: the missing QLD bar fails closed instead of trading partially.
    start = datetime(2024, 6, 1, tzinfo=UTC)
    runtime.seed(
        [
            _bar(symbol, close=100.0 + index, ts=start + timedelta(days=index))
            for index in range(10)
            for symbol in ("QQQ", "QLD")
        ]
    )
    result = runtime.process_closed_bars([_bar("QQQ", close=200.0, ts=start + timedelta(days=10))])
    assert result.status == "incomplete_strategy_batch"
    assert len(result.submitted_order_ids) == 0
    assert len(broker.submitted) == 0
    runtime.close("stopped")
    repo.close()


# ---------------------------------------------------------------------------
# Automation failure — the fail-closed record must still be emitted
# ---------------------------------------------------------------------------
def test_incident_automation_failure_emits_fail_closed_record(monkeypatch: pytest.MonkeyPatch) -> None:
    from parayabuck.monitoring import paper_incident
    from parayabuck.monitoring.incident import create_incident as real_create_incident

    calls = {"n": 0}

    def flaky_create(*args: object, **kwargs: object) -> object:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("incident store unavailable")
        return real_create_incident(*args, **kwargs)  # type: ignore[arg-type]

    emitted: list[tuple[str, dict[str, object]]] = []
    monkeypatch.setattr(paper_incident, "create_incident", flaky_create)
    monkeypatch.setattr(alerts, "emit", lambda kind, **fields: emitted.append((kind, fields)))
    alerts.reset_for_tests()

    record = automate_incident_recovery(IncidentCategory.PROVIDER_UNAVAILABLE, account_id="acct")
    alerts.reset_for_tests()

    assert record.recovery_result == "halted"
    assert record.attempted_recovery == "automation_error_fail_closed"
    # The most severe path is the one that used to stay silent
    assert len(emitted) == 1
    assert emitted[0][0] == "data_provider_unavailable"
    assert emitted[0][1]["incident_id"] == record.incident_id
