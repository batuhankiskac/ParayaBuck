"""Runtime trading-state derivation, stale-account sell-only path and the
daily-summary tax line (2026-10-27). All stubs; no broker, network or disk
state outside pytest's tmp_path."""

from __future__ import annotations

import itertools
import sqlite3
import threading
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import parayabuck.runtime as runtime_module
from parayabuck.config import RuntimeConfig
from parayabuck.core.models import AccountSnapshot, Bar, Market, PositionSnapshot
from parayabuck.runtime import (
    STALE_ACCOUNT_SELL_ONLY_LATCH,
    EngineRuntime,
    TradingState,
)

NOW = datetime(2026, 10, 27, 15, 0, tzinfo=UTC)
STALE_REASON = "account_snapshot_stale"


def _base_runtime() -> EngineRuntime:
    """A bare runtime with every permission flag at its neutral value."""
    runtime = EngineRuntime.__new__(EngineRuntime)
    runtime._circuit_breaker_reason = None
    runtime._warming_up = False
    runtime._reconcile_failed = False
    runtime._reconcile_pending_reason = None
    runtime._reconcile_pending_seq = 0
    runtime._entry_risk_latch = {Market.EQUITY: None}
    runtime._market_entry_halts = {Market.EQUITY: None}
    runtime._state = "running"
    runtime._running = True
    return runtime


def _legacy_submit_buys(runtime: EngineRuntime, market: Market) -> bool:
    """The pre-refactor conjunction of permission flags (buys only).

    Kept here verbatim so the test proves the TradingState replacement gives
    the same answer on every flag combination. The equity signal-basis latch
    is a separate term that the call site still evaluates itself.
    """
    return (
        getattr(runtime, "_circuit_breaker_reason", None) is None
        and not getattr(runtime, "_reconcile_failed", False)
        and not runtime._reconcile_pending()
        and not getattr(runtime, "_warming_up", False)
        and getattr(runtime, "_entry_risk_latch", {}).get(market) is None
        and getattr(runtime, "_market_entry_halts", {}).get(market) is None
    )


# --- Task 1: TradingState derivation -----------------------------------------

FLAG_COMBOS = list(
    itertools.product(
        (None, "ownership_drift"),  # _circuit_breaker_reason
        (False, True),  # _warming_up
        (False, True),  # _reconcile_failed
        (None, "reconciliation_failed"),  # _reconcile_pending_reason
        (None, "portfolio_drawdown_halt"),  # EQUITY entry risk latch
        (None, "rebalance_reconciliation_required"),  # EQUITY market entry halt
    )
)


def _expected_state(breaker, warming, failed, pending, latch, halt, scoped_latched: bool) -> TradingState:
    if breaker is not None or warming:
        return TradingState.HALTED
    if failed or pending is not None or scoped_latched:
        return TradingState.REDUCING
    return TradingState.ACTIVE


@pytest.mark.parametrize(("breaker", "warming", "failed", "pending", "latch", "halt"), FLAG_COMBOS)
def test_trading_state_table_over_flag_combinations(breaker, warming, failed, pending, latch, halt) -> None:
    runtime = _base_runtime()
    runtime._circuit_breaker_reason = breaker
    runtime._warming_up = warming
    runtime._reconcile_failed = failed
    runtime._reconcile_pending_reason = pending
    runtime._entry_risk_latch = {Market.EQUITY: latch}
    runtime._market_entry_halts = {Market.EQUITY: halt}
    entry_latched = latch is not None or halt is not None

    # EQUITY carries the latch/halt, so both the market-scoped and the
    # any-market views see it.
    assert runtime.trading_state(Market.EQUITY) is _expected_state(
        breaker, warming, failed, pending, latch, halt, entry_latched
    )
    assert runtime.trading_state() is _expected_state(breaker, warming, failed, pending, latch, halt, entry_latched)
    # A market with no latch of its own is not restricted by EQUITY's latch.
    assert runtime.trading_state(Market.CRYPTO) is _expected_state(
        breaker, warming, failed, pending, latch, halt, False
    )


@pytest.mark.parametrize(("breaker", "warming", "failed", "pending", "latch", "halt"), FLAG_COMBOS)
def test_active_state_matches_legacy_buy_gate(breaker, warming, failed, pending, latch, halt) -> None:
    """The submit_buys replacement is identical to the legacy conjunction."""
    runtime = _base_runtime()
    runtime._circuit_breaker_reason = breaker
    runtime._warming_up = warming
    runtime._reconcile_failed = failed
    runtime._reconcile_pending_reason = pending
    runtime._entry_risk_latch = {Market.EQUITY: latch}
    runtime._market_entry_halts = {Market.EQUITY: halt}
    for market in (Market.EQUITY, Market.CRYPTO):
        assert (runtime.trading_state(market) is TradingState.ACTIVE) == _legacy_submit_buys(runtime, market)


def test_lifecycle_state_is_not_part_of_the_derivation() -> None:
    """_state and _running are gated at their own sites, not folded in here."""
    runtime = _base_runtime()
    runtime._state = "stopping"
    runtime._running = False
    assert runtime.trading_state() is TradingState.ACTIVE
    runtime._state = "degraded"
    assert runtime.trading_state() is TradingState.ACTIVE


def test_trading_state_enum_values_are_stable() -> None:
    assert {member.name: member.value for member in TradingState} == {
        "ACTIVE": "active",
        "REDUCING": "reducing",
        "HALTED": "halted",
    }


# --- Task 2: stale-account sell-only path ------------------------------------


def test_stale_sell_only_is_default_off() -> None:
    assert RuntimeConfig().stale_account_sell_only is False


@pytest.mark.parametrize(
    ("enabled", "reasons", "breaker", "expected"),
    [
        (True, (STALE_REASON,), None, True),
        (True, [STALE_REASON], None, True),
        (False, (STALE_REASON,), None, False),
        (True, (STALE_REASON, "event_queue_stale"), None, False),
        (True, ("event_queue_stale",), None, False),
        (True, (STALE_REASON,), "ownership_drift", False),
    ],
)
def test_sell_only_applies_only_when_freshness_is_the_sole_blocker(enabled, reasons, breaker, expected) -> None:
    runtime = _base_runtime()
    runtime._circuit_breaker_reason = breaker
    runtime.app_config = SimpleNamespace(runtime=SimpleNamespace(stale_account_sell_only=enabled))
    assert runtime._stale_account_sell_only_applies(reasons) is expected


def test_sell_only_flag_must_be_literal_true() -> None:
    runtime = _base_runtime()
    runtime.app_config = SimpleNamespace(runtime=MagicMock())  # truthy mock, not literal True
    assert runtime._stale_account_sell_only_applies((STALE_REASON,)) is False


def _position(symbol: str, qty: float, market: Market = Market.EQUITY, managed: float | None = None):
    return PositionSnapshot(
        symbol=symbol,
        qty=qty,
        avg_price=10.0,
        current_price=10.0,
        market=market,
        managed_qty=abs(qty) if managed is None else managed,
    )


def _snapshot(*positions: PositionSnapshot) -> AccountSnapshot:
    return AccountSnapshot(cash=1_000.0, equity=10_000.0, peak_equity=10_000.0, daily_pnl=0.0, positions=positions)


def test_sell_only_snapshot_caps_long_positions_at_owned_quantity() -> None:
    runtime = _base_runtime()
    snapshot = _snapshot(
        _position("AAA", 100),  # owns 40 of 100 -> capped to 40
        _position("BBB", 50),  # owns nothing -> dropped
        _position("CCC", -5),  # short -> dropped
        _position("DDD", 10),  # owns 50, holds 10 -> stays 10
        _position("EEE", 7, market=Market.CRYPTO),  # other market -> untouched
    )
    runtime._account_snapshot_cache_state = lambda: (snapshot, NOW, 0)
    owned = {"AAA": 40.0, "DDD": 50.0, "CCC": 9.0}
    runtime._portfolio_repository = SimpleNamespace(owned_quantities=lambda account_id, market=None: dict(owned))
    runtime.broker = SimpleNamespace(account_id="acct")

    capped = runtime._stale_account_sell_only_snapshot(Market.EQUITY)

    assert capped is not None
    by_symbol = {position.symbol: position for position in capped.positions}
    assert set(by_symbol) == {"AAA", "DDD", "EEE"}
    assert by_symbol["AAA"].qty == pytest.approx(40.0)
    assert by_symbol["AAA"].managed_qty == pytest.approx(40.0)
    assert by_symbol["DDD"].qty == pytest.approx(10.0)
    assert by_symbol["EEE"].qty == pytest.approx(7.0)
    # Equity stays the last known figure: the snapshot is stale by definition.
    assert capped.equity == snapshot.equity


def test_sell_only_snapshot_is_none_without_snapshot_or_owned_quantities() -> None:
    runtime = _base_runtime()
    runtime._account_snapshot_cache_state = lambda: (None, None, 0)
    assert runtime._stale_account_sell_only_snapshot(Market.EQUITY) is None

    runtime._account_snapshot_cache_state = lambda: (_snapshot(_position("AAA", 5)), NOW, 0)

    def _broken(account_id, market=None):
        raise sqlite3.OperationalError("ledger locked")

    runtime._portfolio_repository = SimpleNamespace(owned_quantities=_broken)
    runtime.broker = SimpleNamespace(account_id="acct")
    assert runtime._stale_account_sell_only_snapshot(Market.EQUITY) is None


class _RecordingPortfolioRuntime:
    def __init__(self) -> None:
        self.market = Market.EQUITY
        self.strategy = SimpleNamespace(timeframe="1Day", name="stub")
        self.symbols = frozenset({"AAA", "BBB"})
        self.calls: list[dict] = []

    def process_closed_bars(self, bars, **kwargs):
        self.calls.append({"bars": list(bars), **kwargs})
        return SimpleNamespace(
            status="not_scheduled",
            orders=(),
            submitted_order_ids=(),
            target=None,
            decision_id=None,
        )


def _batch_runtime(*, enabled: bool, reasons: tuple[str, ...]):
    runtime = _base_runtime()
    snapshot = _snapshot(_position("AAA", 100), _position("BBB", 20))
    runtime.app_config = SimpleNamespace(runtime=SimpleNamespace(stale_account_sell_only=enabled))
    runtime.broker = SimpleNamespace(account_id="acct")
    runtime._portfolio_repository = SimpleNamespace(
        owned_quantities=lambda account_id, market=None: {"AAA": 60.0, "BBB": 20.0}
    )
    runtime._account_snapshot_cache_state = lambda: (snapshot, NOW, 0)
    portfolio = _RecordingPortfolioRuntime()
    runtime._portfolio_runtimes = (portfolio,)
    runtime._portfolio_lock = threading.RLock()
    runtime._state_lock = threading.RLock()
    runtime._last_portfolio_status = {}
    runtime._entry_readiness_reasons = lambda market, **_kwargs: reasons
    runtime._equity_signal_basis_latch = lambda *_args, **_kwargs: None
    runtime._signal_cross_check_step = lambda _runtime, _relevant: SimpleNamespace()
    deferred: list[tuple[str, str]] = []
    runtime._defer_portfolio_batch = lambda bars, market, reason: deferred.append((market.value, reason)) or True
    runtime._record_paper_decision_receipt = lambda *_args, **_kwargs: None
    runtime._emit_equity_daily_summary = lambda *_args, **_kwargs: None
    return runtime, portfolio, deferred


def _bars() -> list[Bar]:
    return [
        Bar(timestamp=NOW, symbol="AAA", timeframe="1Day", open=10, high=10, low=10, close=10, volume=1),
        Bar(timestamp=NOW, symbol="BBB", timeframe="1Day", open=10, high=10, low=10, close=10, volume=1),
    ]


def test_stale_batch_runs_sell_only_decision_when_enabled() -> None:
    runtime, portfolio, deferred = _batch_runtime(enabled=True, reasons=(STALE_REASON,))

    decided = runtime._process_portfolio_timestamp(_bars())

    assert decided is True
    assert deferred == []
    assert len(portfolio.calls) == 1
    call = portfolio.calls[0]
    assert call["entry_latch"] == STALE_ACCOUNT_SELL_ONLY_LATCH
    capped = {position.symbol: position.qty for position in call["account"].positions}
    assert capped == {"AAA": pytest.approx(60.0), "BBB": pytest.approx(20.0)}


def test_stale_batch_is_held_when_disabled() -> None:
    runtime, portfolio, deferred = _batch_runtime(enabled=False, reasons=(STALE_REASON,))

    decided = runtime._process_portfolio_timestamp(_bars())

    assert decided is False
    assert portfolio.calls == []
    assert deferred and deferred[0][1] == f"readiness:{STALE_REASON}"


def test_stale_batch_is_held_when_another_blocker_is_present() -> None:
    runtime, portfolio, deferred = _batch_runtime(enabled=True, reasons=(STALE_REASON, "event_queue_stale"))

    runtime._process_portfolio_timestamp(_bars())

    assert portfolio.calls == []
    assert deferred and deferred[0][1] == f"readiness:{STALE_REASON}"


def test_stale_batch_keeps_existing_latch_and_appends_sell_only_reason() -> None:
    runtime, portfolio, _deferred = _batch_runtime(enabled=True, reasons=(STALE_REASON,))
    runtime._equity_signal_basis_latch = lambda *_args, **_kwargs: "basis_unverified"

    runtime._process_portfolio_timestamp(_bars())

    assert portfolio.calls[0]["entry_latch"] == f"basis_unverified|{STALE_ACCOUNT_SELL_ONLY_LATCH}"


# --- Task 3: daily-summary tax line ------------------------------------------


def _summary_runtime(tmp_path, *, database_exists: bool = True):
    runtime = _base_runtime()
    database = tmp_path / "runtime.sqlite3"
    if database_exists:
        sqlite3.connect(database).close()
    runtime.app_config = SimpleNamespace(
        alerting=None,
        runtime=SimpleNamespace(decision_db_path=str(database)),
    )
    runtime.broker = SimpleNamespace(account_id="acct")
    return runtime


def test_tax_line_formats_ok_status(tmp_path, monkeypatch) -> None:
    runtime = _summary_runtime(tmp_path)
    seen: dict = {}

    def _stub_ytd(connection, account_id, year, **_kwargs):
        seen.update(connection=connection, account_id=account_id, year=year)
        return {"status": "ok", "year": 2026, "net_gain_tl": 1234.5, "estimated_tax_tl": 100.0}

    monkeypatch.setattr("parayabuck.tax.export.ytd_status", _stub_ytd)

    line = runtime._daily_summary_tax_line()

    assert line == "tax YTD 2026: net gain +1,234.50 TL, est. tax 100.00 TL"
    assert seen["account_id"] == "acct"
    assert seen["year"] == 2026


def test_tax_line_is_unavailable_text_when_the_helper_raises(tmp_path, monkeypatch) -> None:
    runtime = _summary_runtime(tmp_path)

    def _boom(*_args, **_kwargs):
        raise RuntimeError("rates file corrupt")

    monkeypatch.setattr("parayabuck.tax.export.ytd_status", _boom)
    assert runtime._daily_summary_tax_line() == "tax: unavailable"


def test_tax_line_is_unavailable_text_without_a_database(tmp_path) -> None:
    runtime = _summary_runtime(tmp_path, database_exists=False)
    assert runtime._daily_summary_tax_line() == "tax: unavailable"


def test_tax_line_reports_missing_rates_from_the_formatter(tmp_path, monkeypatch) -> None:
    runtime = _summary_runtime(tmp_path)
    monkeypatch.setattr(
        "parayabuck.tax.export.ytd_status",
        lambda *_args, **_kwargs: {"status": "rates_missing", "year": 2026},
    )
    assert runtime._daily_summary_tax_line() == "tax: rates missing"


def _summary_result():
    return SimpleNamespace(
        decision_id="dec-1",
        target=SimpleNamespace(),
        status="ok",
        orders=(),
        submitted_order_ids=(),
    )


@pytest.mark.parametrize("tax_fails", [False, True])
def test_daily_summary_is_emitted_with_tax_segment(tmp_path, monkeypatch, tax_fails) -> None:
    runtime = _summary_runtime(tmp_path)
    captured: dict = {}

    def _stub_summary(target, account, **kwargs):
        captured.update(kwargs)
        return "summary message", {"decision_id": kwargs["decision_id"]}

    emitted: list[tuple] = []
    monkeypatch.setattr("parayabuck.monitoring.daily_summary.build_equity_daily_summary", _stub_summary)
    monkeypatch.setattr(runtime_module.alerts, "emit", lambda kind, **fields: emitted.append((kind, fields)))
    if tax_fails:
        monkeypatch.setattr("parayabuck.tax.export.ytd_status", MagicMock(side_effect=RuntimeError("no ledger")))
    else:
        monkeypatch.setattr(
            "parayabuck.tax.export.ytd_status",
            lambda *_args, **_kwargs: {"status": "ok", "year": 2026, "net_gain_tl": 0.0, "estimated_tax_tl": 0.0},
        )

    portfolio = SimpleNamespace(market=Market.EQUITY)
    runtime._emit_equity_daily_summary(portfolio, _summary_result(), None)

    expected = "tax: unavailable" if tax_fails else "tax YTD 2026: net gain +0.00 TL, est. tax 0.00 TL"
    assert captured["tax_ytd"] == expected
    assert emitted and emitted[0][0] == "daily_summary"
