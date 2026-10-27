"""W3 audit-wave regression tests (2026-10-11): portfolio, data, markets.

Deterministic, tmp_path-only, no network. Each test pins one finding.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from parayabuck.core.models import AccountSnapshot, Bar, Market, PositionSnapshot
from parayabuck.data.adjustment import adequate_coverage, merge_provider_authority
from parayabuck.execution.portfolio import (
    AssetTradingRules,
    PortfolioOrderRequest,
    PortfolioOrderSide,
    RiskOverlayDecision,
)
from parayabuck.markets import nyse_calendar
from parayabuck.markets.nyse_calendar import sessions_around
from parayabuck.monitoring.intent_watchdog import evaluate_equity, last_due_equity_session
from parayabuck.portfolio.base import PortfolioContext, PortfolioStrategy
from parayabuck.portfolio.models import TargetPortfolio, TargetWeight
from parayabuck.portfolio.ownership_rebase import classify_share_change, open_position_cost_bases
from parayabuck.portfolio.runtime_service import (
    REBALANCE_PHASE_BUY_READY,
    PortfolioRuntime,
    PortfolioStateRepository,
)

ACCOUNT = "acct"
SYMBOL = "QLD"
ENTRY_PRICE = 50.0
T0 = datetime(2026, 9, 1, tzinfo=UTC)


class _Broker:
    def __init__(self, positions: tuple[PositionSnapshot, ...] = ()) -> None:
        self.positions = list(positions)
        self.submitted: list[PortfolioOrderRequest] = []

    def account(self) -> AccountSnapshot:
        equity = 100_000.0
        value = sum(position.market_value for position in self.positions)
        return AccountSnapshot(
            cash=equity - value,
            equity=equity,
            peak_equity=equity,
            daily_pnl=0.0,
            positions=tuple(self.positions),
        )

    def asset_trading_rules(self, symbol: str, market: Market) -> AssetTradingRules:
        return AssetTradingRules(1.0, 1.0, 10.0)

    def all_open_order_symbols(self) -> set[str]:
        return set()

    def working_buy_orders(self) -> tuple[()]:
        return ()

    def submit_portfolio_order(self, order: PortfolioOrderRequest) -> str:
        self.submitted.append(order)
        return "broker-" + order.client_order_id


def _position(symbol: str, qty: float) -> PositionSnapshot:
    return PositionSnapshot(
        symbol, qty, 100.0, 100.0, Market.EQUITY, qty_available=qty, ownership="managed", managed_qty=qty
    )


class _TestUniverse:
    def contains(self, symbol: str, market: Market, timestamp: datetime) -> bool:
        return True


class _FixedTargetStrategy(PortfolioStrategy):
    name = "fixed_target"
    version = "1.0.0"
    market = Market.EQUITY
    timeframe = "1d"
    minimum_history_bars = 1

    def __init__(self, targets: dict[str, float]) -> None:
        self._targets = targets
        super().__init__()

    def _validate_parameters(self) -> None:
        return None

    def decide(self, context: PortfolioContext) -> TargetPortfolio:
        weights = tuple(TargetWeight(symbol, w) for symbol, w in self._targets.items())
        return TargetPortfolio(
            strategy_name=self.name,
            strategy_version=self.version,
            decision_timestamp=context.decision_timestamp,
            weights=weights,
            cash_weight=1.0 - sum(self._targets.values()),
            universe_version="test",
        )

    def requires_intermediate_risk_rebalance(self, context: PortfolioContext) -> bool:
        return True


def _runtime(tmp_path: Path, broker: _Broker, targets: dict[str, float]) -> PortfolioRuntime:
    return PortfolioRuntime(
        _FixedTargetStrategy(targets),
        tuple(targets),
        broker,
        PortfolioStateRepository(tmp_path / "runtime.sqlite3"),
        mode="paper",
        maximum_allocation=0.50,
        allow_static_universe=True,
        universe=_TestUniverse(),
        universe_version="test-universe-v1",
    )


def _bars(symbols: tuple[str, ...], day: datetime) -> list[Bar]:
    return [Bar(day, symbol, "1d", 100.0, 100.0, 100.0, 100.0, 1_000_000, Market.EQUITY) for symbol in symbols]


def _sell_to_buy_ready(tmp_path: Path) -> tuple[PortfolioRuntime, _Broker, str, datetime]:
    """One two-phase decision driven to buy_ready: SELL submitted then filled."""
    broker = _Broker((_position("AAPL", 400),))
    runtime = _runtime(tmp_path, broker, {"AAPL": 0.15, "MSFT": 0.20})
    day = datetime(2025, 2, 3, tzinfo=UTC)
    first = runtime.process_closed_bars(_bars(("AAPL", "MSFT"), day), force_scheduled=True)
    assert first.status == "sell_phase_submitted", first.status
    sell = next(order for order in first.orders if order.side == PortfolioOrderSide.SELL)
    runtime.repository.apply_trade_update(
        client_order_id=sell.client_order_id,
        broker_order_id=f"broker-{sell.client_order_id}",
        status="fill",
        cumulative_filled_quantity=sell.quantity,
        average_fill_price=sell.reference_price,
        occurred_at=sell.decision_timestamp,
        activity_id=f"activity-{sell.client_order_id}",
        fee=0.0,
        realized_pnl=0.0,
        unrealized_pnl=0.0,
    )
    broker.positions = [_position("AAPL", 150)]
    # Promote to buy_ready without submitting (unlatched advance would submit).
    runtime.advance_rebalance(submit_deferred_buys=False)
    state = runtime.repository.current_rebalance_execution(runtime.account_id, Market.EQUITY)
    assert state is not None and state.phase == REBALANCE_PHASE_BUY_READY
    assert len(broker.submitted) == 1  # only the SELL so far
    next_day = day + timedelta(days=1)
    return runtime, broker, state.decision_id, next_day


def test_w3_latched_decision_path_withholds_persisted_deferred_buy(tmp_path: Path) -> None:
    """A1/R5D-1 (decision path): process_closed_bars with a latch keeps buy_ready."""
    runtime, broker, decision_id, next_day = _sell_to_buy_ready(tmp_path)
    result = runtime.process_closed_bars(
        _bars(("AAPL", "MSFT"), next_day), entry_latch="portfolio_drawdown_halt", force_scheduled=True
    )
    assert len(broker.submitted) == 1  # no BUY submitted
    state = runtime.repository.current_rebalance_execution(runtime.account_id, Market.EQUITY)
    assert state is not None and state.phase == REBALANCE_PHASE_BUY_READY
    assert state.decision_id == decision_id
    assert result.status in {"rebalance_in_progress", "rebalance_blocked"}


def test_w3_latched_catch_up_path_withholds_persisted_deferred_buy(tmp_path: Path) -> None:
    """A1/R5D-1 (catch-up path): startup_catch_up with a latch keeps buy_ready."""
    runtime, broker, decision_id, next_day = _sell_to_buy_ready(tmp_path)
    # A new closed session past a monthly boundary, so the catch-up treats
    # it as a recovering session and reaches its rebalance-advance gate
    # instead of exiting superseded.
    later = next_day + timedelta(days=40)
    runtime.seed(_bars(("AAPL", "MSFT"), later))
    result = runtime.startup_catch_up(entry_latch="signal_adjustment_unverified")
    assert result.status in {"rebalance_in_progress", "rebalance_blocked"}
    assert len(broker.submitted) == 1  # no BUY submitted
    state = runtime.repository.current_rebalance_execution(runtime.account_id, Market.EQUITY)
    assert state is not None and state.phase == REBALANCE_PHASE_BUY_READY
    assert state.decision_id == decision_id


def test_w3_unlatched_advance_still_submits_deferred_buy(tmp_path: Path) -> None:
    """The latch gate withholds only while latched: unlatched advance submits."""
    runtime, broker, _decision_id, _next_day = _sell_to_buy_ready(tmp_path)
    result = runtime.advance_rebalance()
    assert result is not None and result.status == "buy_phase_submitted"
    assert len(broker.submitted) == 2
    assert broker.submitted[-1].side == PortfolioOrderSide.BUY


def test_w3_foreign_sell_at_split_ratio_is_not_a_split() -> None:
    """C-01: an unchanged broker basis at a 1/2 ratio is a foreign sell."""
    change = classify_share_change(100.0, 50.0, owned_average_cost=80.0, broker_average_cost=80.0)
    assert change.kind == "foreign_sell"
    assert change.rebasable is True


def test_w3_corroborated_reverse_split_stays_a_split() -> None:
    """C-01: a halved basis at a 1/2 ratio is still a reverse split."""
    change = classify_share_change(100.0, 50.0, owned_average_cost=80.0, broker_average_cost=160.0)
    assert change.kind == "split"
    assert change.rebasable is True


def test_w3_reverse_split_rescales_cost_basis() -> None:
    """R2C1/R2C2: a split-driven decrease doubles the lot price."""
    assert open_position_cost_bases([("buy", 100.0, 10.0), ("split_adjust", -50.0, None)]) == pytest.approx(
        (20.0, 20.0)
    )


def test_w3_plain_adjust_decrease_still_depletes_like_a_sale() -> None:
    """R2C1: a foreign-sale decrease keeps FIFO depletion, not a rescale."""
    fifo, _average = open_position_cost_bases([("buy", 10.0, 10.0), ("buy", 10.0, 20.0), ("adjust", -10.0, None)])
    assert fifo == pytest.approx(20.0)


def _order(
    repo: PortfolioStateRepository,
    side: str,
    quantity: float,
    price: float,
    when: datetime,
    client_order_id: str,
) -> str:
    target = TargetPortfolio("vt35", "1", when, (), 1.0)
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
        decision_timestamp=when,
        strategy_name="vt35",
        strategy_version="1",
    )
    repo.record_order(order, "submitted")
    return client_order_id


def test_w3_apply_trade_update_rejects_infinite_cumulative(tmp_path: Path) -> None:
    """R5B-1: inf cumulative books nothing and stays healable."""
    from parayabuck.monitoring import alerts as alerts_module

    repo = PortfolioStateRepository(tmp_path / "runtime.sqlite3")
    cid = _order(repo, "buy", 10.0, 100.0, T0, "cid-inf")
    emitted: list[dict[str, object]] = []
    orig = alerts_module.emit
    alerts_module.emit = lambda kind, **fields: emitted.append({"kind": kind, **fields})  # type: ignore[method-assign]
    try:
        assert (
            repo.apply_trade_update(
                client_order_id=cid,
                broker_order_id="b-1",
                status="partial_fill",
                cumulative_filled_quantity=float("inf"),
                average_fill_price=100.0,
                occurred_at=T0,
                activity_id="a-1",
                fee=0.0,
                realized_pnl=0.0,
                unrealized_pnl=0.0,
            )
            is False
        )
    finally:
        alerts_module.emit = orig  # type: ignore[method-assign]
    assert repo.owned_quantities(ACCOUNT, Market.EQUITY).get(SYMBOL, 0.0) == 0.0
    # A later legit fill heals: ownership becomes real, never inf.
    assert (
        repo.apply_trade_update(
            client_order_id=cid,
            broker_order_id="b-1",
            status="fill",
            cumulative_filled_quantity=10.0,
            average_fill_price=100.0,
            occurred_at=T0,
            activity_id="a-2",
            fee=0.0,
            realized_pnl=0.0,
            unrealized_pnl=0.0,
        )
        is True
    )
    assert repo.owned_quantities(ACCOUNT, Market.EQUITY).get(SYMBOL, 0.0) == pytest.approx(10.0)


def test_w3_apply_trade_update_rejects_bad_price_and_fee(tmp_path: Path) -> None:
    """R5B-1: zero/negative average and NaN fee are rejected before any write."""
    repo = PortfolioStateRepository(tmp_path / "runtime.sqlite3")
    cid = _order(repo, "buy", 10.0, 100.0, T0, "cid-bad")
    for average, fee in ((0.0, 0.0), (-5.0, 0.0), (100.0, float("nan"))):
        assert (
            repo.apply_trade_update(
                client_order_id=cid,
                broker_order_id="b-1",
                status="partial_fill",
                cumulative_filled_quantity=5.0,
                average_fill_price=average,
                occurred_at=T0,
                activity_id=f"a-{average}-{fee}",
                fee=fee,
                realized_pnl=0.0,
                unrealized_pnl=0.0,
            )
            is False
        )
    assert repo.owned_quantities(ACCOUNT, Market.EQUITY).get(SYMBOL, 0.0) == 0.0


def test_w3_repository_close_is_idempotent(tmp_path: Path) -> None:
    """R2B3: double close (and close after store release) never raises."""
    repo = PortfolioStateRepository(tmp_path / "runtime.sqlite3")
    repo.close()
    repo.close()
    repo2 = PortfolioStateRepository(tmp_path / "runtime2.sqlite3")
    repo2.store.acquire()
    repo2.store.release()
    repo2.close()  # store already closed: no-op


def _daily_bar(symbol: str, day: datetime, close: float) -> Bar:
    return Bar(day, symbol, "1d", close, close, close, close, 1_000_000, Market.EQUITY)


def test_w3_mid_window_provider_hole_fails_closed() -> None:
    """R3E1/R3E4: a provider window missing an interior session is inadequate."""
    base = datetime(2026, 9, 1, tzinfo=UTC)
    stored = tuple(_daily_bar("QLD", base + timedelta(days=i), 100.0 + i) for i in range(5))
    provider = (stored[0], stored[1], stored[3], stored[4])  # session 3 omitted
    assert (
        adequate_coverage(
            provider,
            required_oldest=stored[0].timestamp,
            required_newest=stored[-1].timestamp,
        )
        is True
    )
    assert (
        adequate_coverage(
            provider,
            required_oldest=stored[0].timestamp,
            required_newest=stored[-1].timestamp,
            stored=stored,
        )
        is False
    )
    merged, _changed = merge_provider_authority(stored, provider)
    assert len(merged) == 4  # documents the drop the adequacy gate now prevents


def test_w3_partial_window_preserves_cached_bars(tmp_path: Path) -> None:
    """R3E2/R3E4: a holed provider window upserts instead of replacing."""
    from types import SimpleNamespace

    import parayabuck.data.alpaca as alpaca_module
    from parayabuck.data.cache import WarmupCache

    cache = WarmupCache(str(tmp_path / "warmup.sqlite3"))
    try:
        feed = "sip"
        start = datetime(2026, 9, 1, tzinfo=UTC)
        stamps = [start + timedelta(days=i) for i in range(6)]
        cache.upsert(
            [Bar(stamp, "QLD", "1d", 100.0, 101.0, 99.0, 100.0, 1000.0, Market.EQUITY) for stamp in stamps],
            feed=feed,
            adjustment="raw",
        )
        rows = [
            {"timestamp": stamp, "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 1000.0}
            for stamp in (stamps[0], stamps[1], stamps[3], stamps[4], stamps[5])
        ]
        import pandas as pd

        history = alpaca_module.AlpacaHistoricalData.__new__(alpaca_module.AlpacaHistoricalData)
        from alpaca.data.enums import DataFeed

        history._stock_feed = DataFeed("sip")
        history._configured_stock_feed = "sip"
        history._sip_fallback_active = False
        history._sip_fallback_since = None
        history._sip_embargo = timedelta(0)
        history._provider_fallbacks = 0
        history._last_batch_degraded = False
        history.provider_calls = 0
        history.provider_symbols_requested = 0
        history.provider_bars_returned = 0
        history._cache = cache
        frame = pd.DataFrame(rows).set_index("timestamp")

        class _Resp:
            df = frame

        history._stock_client = SimpleNamespace(get_stock_bars=lambda request: _Resp())
        result = history._fetch_window(["QLD"], "1d", stamps[0], stamps[5], "raw")
        assert [bar.timestamp for bar in result["QLD"]] == [stamps[0], stamps[1], stamps[3], stamps[4], stamps[5]]
        kept = cache.get("QLD", "1d", market=Market.EQUITY, feed=feed, start=stamps[0], end=stamps[5], adjustment="raw")
        assert [bar.timestamp for bar in kept] == stamps  # holed session survives
    finally:
        cache.close()


def test_w3_sessions_around_degrades_instead_of_raising(monkeypatch: pytest.MonkeyPatch) -> None:
    """R5C-1/R5C-3: a broken calendar still yields weekday sessions."""
    now = datetime(2026, 11, 27, 12, 0, tzinfo=UTC)  # Friday
    assert sessions_around(now)

    def _boom(start: object, end: object) -> object:
        raise RuntimeError("boom")

    monkeypatch.setattr(nyse_calendar, "nyse_sessions", _boom)
    try:
        degraded = sessions_around(now)
        assert degraded
        assert all(day.weekday() < 5 for day, _open, _close in degraded)
        assert last_due_equity_session(now, timedelta(minutes=45)) is not None
        from parayabuck.monitoring.intent_watchdog import IntentWatchdogSettings

        assert evaluate_equity(now, None, None, running_since=None, settings=IntentWatchdogSettings()) is not None
    finally:
        # The degraded flag is module-global with a 5-minute retry window:
        # restore health so later tests see the real calendar again.
        nyse_calendar._degraded_reason = None
        nyse_calendar._degraded_retry_at = 0.0


def test_w3_recovering_session_tags_startup_origin(tmp_path: Path) -> None:
    """R3A2: a non-forced startup_catch_up decision carries its origin."""
    broker = _Broker()
    runtime = _runtime(tmp_path, broker, {"AAPL": 0.10})
    seed_day = datetime(2025, 2, 3, tzinfo=UTC)
    runtime.seed(
        [
            Bar(seed_day - timedelta(days=i), s, "1d", 100.0, 100.0, 100.0, 100.0, 1_000, Market.EQUITY)
            for s in ("AAPL",)
            for i in range(1, 60)
        ]
    )
    first = runtime.process_closed_bars(_bars(("AAPL",), seed_day), force_scheduled=True)
    assert first.target is not None
    assert first.target.metadata.get("decision_origin", "scheduled") == "scheduled"
    # A recovering session (restart across the close) decides the missed
    # boundary without forcing: the startup_catch_up origin must still tag
    # the persisted target.
    result = runtime.process_closed_bars(
        _bars(("AAPL",), seed_day + timedelta(days=30)), decision_origin="startup_catch_up"
    )
    assert result.target is not None, result.status
    assert result.target.metadata.get("decision_origin") == "startup_catch_up"


def test_w3_zero_owned_operator_rebase_adopts_surplus(tmp_path: Path) -> None:
    """R3D2: an explicit rebase from zero adopts the broker quantity.

    F10-3: the adoption must now state the unit cost (an acquisition lot);
    without it the zero-owned increase is refused, see tests/test_audit_fix_W11.py."""
    repo = PortfolioStateRepository(tmp_path / "runtime.sqlite3")
    try:
        assert repo.rebase_ownership_to_broker_quantity(ACCOUNT, SYMBOL, Market.EQUITY, 50.0, unit_cost=10.0) == 50.0
        assert repo.owned_quantities(ACCOUNT, Market.EQUITY).get(SYMBOL, 0.0) == pytest.approx(50.0)
    finally:
        repo.close()
