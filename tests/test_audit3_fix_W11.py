"""Audit wave 3 W11 regressions: stale-clear proof, rebase drift, operator hints,
ownership tolerance, recovered fill ordering, peak bootstrap alert, surplus alert
dedupe, and the in-flight submission resolver (F3-3-1..4, D3-5-1, F3-4-2, F3-4-3,
C3-1-2, C3-1-3, P3-4-1).

Each test pins one invariant. Synthetic fixtures, ``tmp_path`` state, fixed
timestamps, no network, no credentials and no production paths.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from parayabuck.core.models import Market
from parayabuck.execution.alpaca_account import AccountService
from parayabuck.execution.portfolio import PortfolioOrderRequest, PortfolioOrderSide, RiskOverlayDecision
from parayabuck.execution.risk_peak import RiskPeakService
from parayabuck.portfolio.models import TargetPortfolio
from parayabuck.portfolio.runtime_ownership import net_owned_quantities
from parayabuck.portfolio.runtime_repository import PortfolioStateRepository
from parayabuck.portfolio.runtime_service import PortfolioRuntime

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


def _buy_filled(repo: PortfolioStateRepository, quantity: float, price: float, when: datetime, cid: str) -> None:
    _order(repo, "buy", quantity, price, when, cid)
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


def _owned(repo: PortfolioStateRepository) -> float:
    return repo.owned_quantities(ACCOUNT, Market.EQUITY).get(SYMBOL, 0.0)


# ---------------------------------------------------------------------------
# F3-3-1 / F3-3-2: a stale clear needs a live local SELL whose unbooked quantity
# covers the residue.
# ---------------------------------------------------------------------------


def test_stale_clear_refused_when_a_partial_exit_does_not_explain_the_residue(tmp_path: Path) -> None:
    """Bought 100, a live 10-share exit, broker flat: the 90-share residue is not explained."""
    repo = _repo(tmp_path)
    _buy_filled(repo, 100.0, 50.0, T0, "buy-1")
    _order(repo, "sell", 10.0, 55.0, T0 + timedelta(hours=1), "sell-1")

    assert repo.clear_stale_ownership(ACCOUNT, SYMBOL, Market.EQUITY) == 0.0
    assert _owned(repo) == pytest.approx(100.0)


def test_stale_clear_allowed_when_a_live_exit_covers_the_residue(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _buy_filled(repo, 100.0, 50.0, T0, "buy-1")
    _order(repo, "sell", 100.0, 55.0, T0 + timedelta(hours=1), "sell-1")

    assert repo.clear_stale_ownership(ACCOUNT, SYMBOL, Market.EQUITY) == pytest.approx(100.0)
    assert _owned(repo) == pytest.approx(0.0)


def test_dead_sell_never_explains_a_flat_broker(tmp_path: Path) -> None:
    """A SELL canceled before any fill (for example a 404 resolved to canceled) is no proof."""
    repo = _repo(tmp_path)
    _buy_filled(repo, 100.0, 50.0, T0, "buy-1")
    _order(repo, "sell", 100.0, 55.0, T0 + timedelta(hours=1), "sell-1")
    repo.update_order("sell-1", "canceled")

    assert repo.clear_stale_ownership(ACCOUNT, SYMBOL, Market.EQUITY) == 0.0
    assert _owned(repo) == pytest.approx(100.0)


def test_booked_fills_of_an_earlier_exit_do_not_explain_a_later_residue(tmp_path: Path) -> None:
    """A fully booked sale is already out of ownership; its quantity cannot cover a new residue."""
    repo = _repo(tmp_path)
    _buy_filled(repo, 100.0, 50.0, T0, "buy-1")
    _order(repo, "sell", 50.0, 55.0, T0 + timedelta(hours=1), "sell-1")
    repo.apply_trade_update(
        client_order_id="sell-1",
        broker_order_id="broker-sell-1",
        status="filled",
        cumulative_filled_quantity=50.0,
        average_fill_price=55.0,
        occurred_at=T0 + timedelta(hours=1),
        activity_id="activity-sell-1",
        fee=0.0,
        realized_pnl=0.0,
        unrealized_pnl=0.0,
    )
    assert _owned(repo) == pytest.approx(50.0)

    assert repo.clear_stale_ownership(ACCOUNT, SYMBOL, Market.EQUITY) == 0.0
    assert _owned(repo) == pytest.approx(50.0)


# ---------------------------------------------------------------------------
# F3-3-3: several open exits together explain a broker shortfall.
# ---------------------------------------------------------------------------


def _open_sell(quantity: float, filled: float = 0.0) -> SimpleNamespace:
    return SimpleNamespace(market=Market.EQUITY, quantity=quantity, cumulative_filled_quantity=filled)


def test_several_open_exits_together_explain_the_shortfall() -> None:
    orders = {SYMBOL: [_open_sell(30.0), _open_sell(30.0)]}
    assert AccountService._open_order_explains_drift(SYMBOL, 100.0, 50.0, Market.EQUITY, orders) is True


def test_open_exits_that_do_not_cover_the_shortfall_do_not_explain_it() -> None:
    orders = {SYMBOL: [_open_sell(20.0), _open_sell(20.0, filled=5.0)]}
    assert AccountService._open_order_explains_drift(SYMBOL, 100.0, 50.0, Market.EQUITY, orders) is False


# ---------------------------------------------------------------------------
# D3-5-1 / F3-3-4: operator hints print the two-step flow with --plan-digest.
# ---------------------------------------------------------------------------


def _hint_commands(text: str) -> list[str]:
    return re.findall(r"rebase-ownership[^`\n]*", text)


def test_surplus_alert_hint_includes_plan_digest(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _repo(tmp_path)
    emitted: list[dict[str, Any]] = []

    def fake_emit(kind: str, **fields: Any) -> None:
        emitted.append(fields)

    monkeypatch.setattr("parayabuck.monitoring.alerts.emit", fake_emit)
    repo._alert_unconfirmed_surplus(ACCOUNT, SYMBOL, Market.EQUITY, 100.0, 150.0, "no split")

    message = str(emitted[0]["message"])
    assert _hint_commands(message), "the surplus alert must name the rebase command"
    assert "--confirm" in message
    assert "--plan-digest" in message


def test_hint_placeholder_free_confirm_form_is_not_printed_without_digest() -> None:
    """The ledger, halt and reconciliation hints must not print a bare ``--confirm``."""
    sources = [
        Path(__file__).resolve().parents[1] / "src/parayabuck/execution/alpaca_account.py",
        Path(__file__).resolve().parents[1] / "src/parayabuck/execution/alpaca_reconciliation.py",
        Path(__file__).resolve().parents[1] / "src/parayabuck/portfolio/runtime_repository.py",
    ]
    for path in sources:
        for line in path.read_text(encoding="utf-8").splitlines():
            if "rebase-ownership" in line and "--confirm" in line:
                assert "--plan-digest" in line, f"{path.name}: bare --confirm hint: {line.strip()}"


# ---------------------------------------------------------------------------
# F3-4-2: one tolerance for the lot band and the net guard.
# ---------------------------------------------------------------------------


def test_float_noise_inside_the_band_does_not_trip_the_net_guard() -> None:
    # Gross 1000 shares: the band absorbs 1e-6 relative noise (1e-3 shares).
    net = net_owned_quantities([(SYMBOL, "buy", 1000.0), (SYMBOL, "sell", 1000.0005)], [], allow_negative=False)
    assert SYMBOL not in net


def test_a_real_negative_residue_still_halts() -> None:
    with pytest.raises(RuntimeError, match="Negative net portfolio ownership"):
        net_owned_quantities([(SYMBOL, "buy", 1.0), (SYMBOL, "sell", 1.01)], [], allow_negative=False)


# ---------------------------------------------------------------------------
# F3-4-3: a recovered remainder is clamped to the last booked fill of its order.
# ---------------------------------------------------------------------------


def test_recovered_fill_is_not_dated_before_a_booked_fill(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    cid = _order(repo, "buy", 100.0, 50.0, T0, "buy-partial")
    repo.apply_trade_update(
        client_order_id=cid,
        broker_order_id="broker-buy-partial",
        status="partial_fill",
        cumulative_filled_quantity=40.0,
        average_fill_price=50.0,
        occurred_at=T0 + timedelta(hours=2),
        activity_id="stream-buy-partial-40",
        fee=0.0,
        realized_pnl=0.0,
        unrealized_pnl=0.0,
    )
    # The recovered remainder is dated at submission (T0), before the stream fill.
    repo.apply_trade_update(
        client_order_id=cid,
        broker_order_id="broker-buy-partial",
        status="canceled",
        cumulative_filled_quantity=100.0,
        average_fill_price=50.0,
        occurred_at=T0,
        activity_id=f"{cid}:recovered:100.0",
        fee=0.0,
        realized_pnl=0.0,
        unrealized_pnl=0.0,
    )
    rows = dict(
        repo.store.connection.execute(
            "SELECT activity_id, occurred_at FROM portfolio_fills WHERE client_order_id = ?", (cid,)
        ).fetchall()
    )
    recovered_at = datetime.fromisoformat(rows[f"{cid}:recovered:100.0"])
    booked_at = datetime.fromisoformat(rows["stream-buy-partial-40"])
    assert recovered_at >= booked_at


# ---------------------------------------------------------------------------
# C3-1-2: the peak bootstrap is announced by the caller, after the peak lock.
# ---------------------------------------------------------------------------


def test_peak_save_returns_the_bootstrap_announcement_instead_of_emitting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    emitted: list[object] = []
    monkeypatch.setattr("parayabuck.execution.alerting.emit_execution_alert", lambda *a, **k: emitted.append(a))
    service = RiskPeakService(peak_state_path=tmp_path / "peak.json")
    service._bootstrap_pending = True
    service._peak_equity = 100_000.0
    service._peak_account_id = ACCOUNT

    announcement = service.save_peak_equity(ACCOUNT)

    assert announcement == (ACCOUNT, 100_000.0)
    assert emitted == []
    assert service._bootstrap_pending is False
    assert service.save_peak_equity(ACCOUNT) is None


# ---------------------------------------------------------------------------
# C3-1-3: the surplus alert episode is claimed under the store lock, and a failed
# emission releases it.
# ---------------------------------------------------------------------------


def test_surplus_episode_is_claimed_once_and_released_after_a_failed_emit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path)
    assert repo._reserve_surplus_alert(ACCOUNT, SYMBOL, Market.EQUITY, 100.0, 150.0) is True
    assert repo._reserve_surplus_alert(ACCOUNT, SYMBOL, Market.EQUITY, 100.0, 150.0) is False

    def failing_emit(kind: str, **fields: Any) -> None:
        raise RuntimeError("journal unavailable")

    monkeypatch.setattr("parayabuck.monitoring.alerts.emit", failing_emit)
    repo._alert_unconfirmed_surplus(ACCOUNT, SYMBOL, Market.EQUITY, 100.0, 150.0, "no split")

    # The failed emission released the claim, so the next pass alerts again.
    assert repo._reserve_surplus_alert(ACCOUNT, SYMBOL, Market.EQUITY, 100.0, 150.0) is True


def test_successful_surplus_emit_keeps_the_episode_claimed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _repo(tmp_path)
    monkeypatch.setattr("parayabuck.monitoring.alerts.emit", lambda kind, **fields: None)
    assert repo._reserve_surplus_alert(ACCOUNT, SYMBOL, Market.EQUITY, 100.0, 150.0) is True
    repo._alert_unconfirmed_surplus(ACCOUNT, SYMBOL, Market.EQUITY, 100.0, 150.0, "no split")
    assert repo._reserve_surplus_alert(ACCOUNT, SYMBOL, Market.EQUITY, 100.0, 150.0) is False


# ---------------------------------------------------------------------------
# P3-4-1: a submit POST that started and never returned is not booked canceled
# on a single 404; it is held for reconciliation.
# ---------------------------------------------------------------------------


class _ResolverBroker:
    def __init__(self, outcome: object) -> None:
        self._outcome = outcome

    def resolve_portfolio_submission(self, client_order_id: str, broker_order_id: str = "") -> object:
        return self._outcome


def _stub_runtime(repo: PortfolioStateRepository, outcome: object) -> SimpleNamespace:
    return SimpleNamespace(
        broker=_ResolverBroker(outcome),
        repository=repo,
        _submission_lookup_failures=lambda: {},
        _escalate_unresolved_submission=lambda cid: False,
        _raise_operator_alert_once=lambda key, **fields: None,
    )


def _submitting_order(repo: PortfolioStateRepository, cid: str, *, started: bool, returned: bool) -> None:
    _order(repo, "buy", 10.0, 50.0, T0, cid)
    repo.update_order(cid, "submitting")
    if started:
        repo.record_submit_call_started(cid, T0)
    if returned:
        repo.record_submit_call_returned(cid, T0)


def _status(repo: PortfolioStateRepository, cid: str) -> str:
    row = repo.store.connection.execute("SELECT status FROM portfolio_orders WHERE client_order_id = ?", (cid,))
    return str(row.fetchone()[0])


def test_in_flight_submit_with_a_404_is_held_for_reconciliation(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _submitting_order(repo, "buy-inflight", started=True, returned=False)
    rows = repo.submitting_orders(ACCOUNT, Market.EQUITY)
    assert [row["submit_call_in_flight"] for row in rows] == [True]

    PortfolioRuntime._resolve_submitting_rows(_stub_runtime(repo, "canceled"), rows)

    assert _status(repo, "buy-inflight") == "reconciliation_required"


def test_submit_that_returned_before_the_404_is_still_canceled(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _submitting_order(repo, "buy-returned", started=True, returned=True)
    rows = repo.submitting_orders(ACCOUNT, Market.EQUITY)
    assert [row["submit_call_in_flight"] for row in rows] == [False]

    PortfolioRuntime._resolve_submitting_rows(_stub_runtime(repo, "canceled"), rows)

    assert _status(repo, "buy-returned") == "canceled"


def test_row_without_a_started_submit_call_is_still_canceled(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _submitting_order(repo, "buy-unstarted", started=False, returned=False)
    rows = repo.submitting_orders(ACCOUNT, Market.EQUITY)

    PortfolioRuntime._resolve_submitting_rows(_stub_runtime(repo, "canceled"), rows)

    assert _status(repo, "buy-unstarted") == "canceled"
