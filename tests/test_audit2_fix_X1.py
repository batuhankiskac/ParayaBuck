"""Audit wave 2 leftover fixes X1: held-day records, submit hold alerts, stop gate, re-advance.

Covers the decision-path remainders of D2-3-1 (the held advance reason on a
scheduled decision), D2-3-2 (the submit_blocked hold alert), the operator-facing
no-trade translations, C2-9-2 (the submit gate refuses after stop), and P2-2-1
(the account loop re-advances a BUY side that a stale snapshot or a full slot held).
Offline: fake brokers, fake repositories and tmp_path ledgers.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from parayabuck.core.models import Market
from parayabuck.execution.portfolio import PortfolioOrderSide
from parayabuck.monitoring import alerts as alerts_module
from parayabuck.portfolio import runtime_service
from parayabuck.portfolio.runtime_models import PortfolioRuntimeResult
from parayabuck.runtime import EngineRuntime
from tests.test_audit_fix_W06 import _NEXT_MONTH, _bars, _buy_ready, _sell_submitted


@pytest.fixture
def journal(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Capture no-trade journal rows; a fresh per-day throttle so every emission is seen."""
    rows: list[dict[str, Any]] = []
    monkeypatch.setattr(runtime_service, "_emit_no_trade_safe", lambda **fields: rows.append(fields))
    monkeypatch.setattr(runtime_service, "_no_trade_last", {})
    return rows


@pytest.fixture
def summaries(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    monkeypatch.setattr(runtime_service, "_emit_decision_summary_safe", lambda **fields: rows.append(fields))
    return rows


@pytest.fixture
def alerts(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Operator alerts, captured instead of emitted."""
    rows: list[dict[str, Any]] = []
    monkeypatch.setattr(alerts_module, "emit", lambda kind, *, message, **fields: rows.append({"kind": kind, **fields}))
    return rows


# ---------------------------------------------------------------- D2-3-1 remainder


@pytest.mark.parametrize(
    ("held", "reason", "summary_reason"),
    [
        ("replan_snapshot_stale", "replan_snapshot_stale", "ACCOUNT_STALE"),
        ("rebalance_waiting_for_slot", "rebalance_waiting_for_slot", "REBALANCE_WAITING_FOR_SLOT"),
        ("submit_blocked", "submit_blocked", "SUBMIT_BLOCKED"),
    ],
)
def test_held_day_records_the_hold_as_its_no_trade_reason(
    tmp_path, monkeypatch: pytest.MonkeyPatch, journal, summaries, held: str, reason: str, summary_reason: str
) -> None:
    """D2-3-1: a scheduled decision held by an open rebalance records that hold, not the generic token."""
    runtime, _repository, _broker, first, _sell = _sell_submitted(tmp_path)
    journal.clear()
    summaries.clear()
    monkeypatch.setattr(
        runtime,
        "advance_rebalance",
        lambda **_kwargs: PortfolioRuntimeResult(first.decision_id, None, None, (), (), held),
    )
    monkeypatch.setattr(runtime, "_stale_rebalance_decision", lambda _timestamp: None)

    result = runtime.process_closed_bars(_bars(_NEXT_MONTH), force_scheduled=True)

    assert result.status == held
    assert [row["reason"] for row in journal] == [reason]
    assert [row["no_trade_reason"] for row in summaries] == [summary_reason]


def test_sell_phase_hold_keeps_the_generic_token_and_summary(
    tmp_path, monkeypatch: pytest.MonkeyPatch, journal, summaries
) -> None:
    """D2-3-1: a SELL-phase hold is not relabelled as a BUY-side hold."""
    runtime, _repository, _broker, first, _sell = _sell_submitted(tmp_path)
    journal.clear()
    summaries.clear()
    monkeypatch.setattr(
        runtime,
        "advance_rebalance",
        lambda **_kwargs: PortfolioRuntimeResult(first.decision_id, None, None, (), (), "replan_sell_submitted"),
    )
    monkeypatch.setattr(runtime, "_stale_rebalance_decision", lambda _timestamp: None)

    runtime.process_closed_bars(_bars(_NEXT_MONTH), force_scheduled=True)

    assert [row["reason"] for row in journal] == ["rebalance_in_progress"]
    assert [row["no_trade_reason"] for row in summaries] == ["SELL_PHASE_PENDING"]


# ---------------------------------------------------------------- D2-3-2 remainder


def test_submit_blocked_hold_raises_one_alert_per_decision(tmp_path, monkeypatch: pytest.MonkeyPatch, journal) -> None:
    """D2-3-2: a BUY side the submit gate holds is alerted and journaled once per decision."""
    raised: list[tuple[str, str]] = []
    monkeypatch.setattr(
        runtime_service.PortfolioRuntime,
        "_alert_buy_side_held",
        lambda self, decision_id, status: raised.append((decision_id, status)),
    )
    runtime, repository, _broker, first = _buy_ready(tmp_path)

    held = runtime.advance_rebalance(submit_gate=lambda _order: "operator_halt")

    assert held is not None and held.status == "submit_blocked"
    assert raised == [(first.decision_id, "submit_blocked")]


def test_submit_blocked_hold_is_alerted_and_journaled_with_its_status(
    tmp_path, journal, alerts: list[dict[str, Any]]
) -> None:
    """D2-3-2: the real alert helper raises the operator alert and journals the hold under its reason."""
    runtime, _repository, _broker, first = _buy_ready(tmp_path)

    held = runtime.advance_rebalance(submit_gate=lambda _order: "operator_halt")

    assert held is not None and held.status == "submit_blocked"
    assert [row["category"] for row in alerts] == ["submit_blocked"]
    assert [row["decision_id"] for row in alerts] == [first.decision_id]
    assert [row["reason"] for row in journal] == ["submit_blocked"]


# ---------------------------------------------------------------- no-trade vocabulary


@pytest.mark.parametrize(
    "reason",
    [
        "replan_snapshot_stale",
        "rebalance_waiting_for_slot",
        "stale_decision_buy_held",
        "closed_unresolved",
        "submit_blocked",
        "replan_order_in_flight",
        "signal_cross_check_pending",
        "rebalance_held:replan_snapshot_stale",
        "rebalance_held:submit_blocked",
    ],
)
def test_no_trade_message_never_shows_a_raw_token(monkeypatch: pytest.MonkeyPatch, reason: str) -> None:
    """Summaries name the hold in Turkish; the raw engine token never reaches the owner."""
    messages: list[str] = []
    from parayabuck.monitoring import journal as journal_module
    from parayabuck.monitoring import operational_event

    def _create_event(**kwargs: Any) -> SimpleNamespace:
        messages.append(kwargs["message"])
        return SimpleNamespace()

    monkeypatch.setattr(operational_event, "create_event", _create_event)
    monkeypatch.setattr(journal_module, "emit_journal", lambda _event: True)
    monkeypatch.setattr(runtime_service, "_no_trade_last", {})

    runtime_service._emit_no_trade_safe(market="equity", strategy="qld", reason=reason)

    assert len(messages) == 1
    message = messages[0]
    raw = reason.partition(":")[2] or reason
    assert raw not in message
    assert message.startswith("Hisse alınmadı: ")


# ---------------------------------------------------------------- C2-9-2


@pytest.mark.parametrize("side", [PortfolioOrderSide.BUY, PortfolioOrderSide.SELL])
def test_decision_path_submit_gate_refuses_every_order_after_stop(side: PortfolioOrderSide) -> None:
    """C2-9-2: once a stop is requested the gate holds each order of a decision batch."""
    runtime = EngineRuntime.__new__(EngineRuntime)
    runtime._running = False

    assert runtime._advance_submit_block_reason(Market.EQUITY, SimpleNamespace(side=side)) == "engine_not_running"


# ---------------------------------------------------------------- P2-2-1


class _FakeRepository:
    def __init__(self, phase: str | None) -> None:
        self.phase = phase

    def current_rebalance_execution(self, _account_id: str, _market: Market) -> SimpleNamespace | None:
        return None if self.phase is None else SimpleNamespace(phase=self.phase)


def _loop_runtime(phase: str | None, calls: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch) -> EngineRuntime:
    runtime = EngineRuntime.__new__(EngineRuntime)
    runtime._running = True
    runtime._warming_up = False
    runtime._startup_committed = True
    runtime._portfolio_runtimes = [
        SimpleNamespace(market=Market.EQUITY, account_id="acct", repository=_FakeRepository(phase))
    ]
    monkeypatch.setattr(
        runtime,
        "_advance_portfolio_rebalance",
        lambda market, **kwargs: calls.append({"market": market, **kwargs}),
        raising=False,
    )
    return runtime


def test_account_loop_readvances_a_buy_ready_side_once_per_window(monkeypatch: pytest.MonkeyPatch) -> None:
    """P2-2-1: a BUY side held with nothing in flight is re-advanced from the account loop, rate limited."""
    calls: list[dict[str, Any]] = []
    runtime = _loop_runtime("buy_ready", calls, monkeypatch)
    account = SimpleNamespace(equity=1.0)

    runtime._maybe_readvance_held_buy_side(account)
    runtime._maybe_readvance_held_buy_side(account)

    assert len(calls) == 1
    assert calls[0]["market"] == Market.EQUITY
    assert calls[0]["account"] is account
    assert calls[0]["cached_basis_only"] is True


@pytest.mark.parametrize("phase", ["sell_pending", "buy_pending", "complete", None])
def test_account_loop_leaves_other_phases_alone(monkeypatch: pytest.MonkeyPatch, phase: str | None) -> None:
    calls: list[dict[str, Any]] = []
    runtime = _loop_runtime(phase, calls, monkeypatch)

    runtime._maybe_readvance_held_buy_side(SimpleNamespace(equity=1.0))

    assert calls == []


def test_account_loop_readvance_skips_while_not_running_or_warming(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, Any]] = []
    runtime = _loop_runtime("buy_ready", calls, monkeypatch)
    runtime._warming_up = True

    runtime._maybe_readvance_held_buy_side(SimpleNamespace(equity=1.0))
    runtime._warming_up = False
    runtime._running = False
    runtime._maybe_readvance_held_buy_side(SimpleNamespace(equity=1.0))

    assert calls == []


def test_account_loop_readvance_failure_never_escapes(monkeypatch: pytest.MonkeyPatch) -> None:
    runtime = _loop_runtime("buy_ready", [], monkeypatch)

    def _boom(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("advance failed")

    monkeypatch.setattr(runtime, "_advance_portfolio_rebalance", _boom)

    runtime._maybe_readvance_held_buy_side(SimpleNamespace(equity=1.0))


def test_superseded_buy_ready_side_is_discarded_with_an_operator_alert(
    tmp_path, journal, alerts: list[dict[str, Any]]
) -> None:
    """P2-2-1: a withheld BUY side discarded by a newer session is alerted once, not only logged."""
    runtime, repository, _broker, first = _buy_ready(tmp_path)

    runtime._close_superseded_rebalance(first.decision_id)

    closed = [row for row in alerts if row["category"] == "closed_unresolved"]
    assert [row["decision_id"] for row in closed] == [first.decision_id]
    assert [row["reason"] for row in journal] == ["closed_unresolved"]
    state = repository.current_rebalance_execution("acct", Market.EQUITY)
    assert state is not None and state.phase == "complete"
