"""Focused regressions for the merged portfolio/universe audit findings.

Each test pins one consumer-visible invariant and is deterministic: synthetic
fixtures, ``tmp_path`` state, no network and no production paths.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta

import pytest

from parayabuck.core.models import Market
from parayabuck.execution.portfolio import (
    PortfolioOrderRequest,
    PortfolioOrderSide,
    RiskOverlayDecision,
)
from parayabuck.monitoring import journal as journal_module
from parayabuck.portfolio import runtime_service
from parayabuck.portfolio.base import PortfolioContext
from parayabuck.portfolio.equity_leveraged_trend import EquityLeveragedTrendV1
from parayabuck.portfolio.models import TargetPortfolio
from parayabuck.portfolio.runtime_repository import PortfolioStateRepository


def _fill(repo: PortfolioStateRepository, client_order_id: str, quantity: float) -> None:
    with repo.store.lock, repo.store.connection:
        repo.store.connection.execute(
            "UPDATE portfolio_orders SET cumulative_filled_quantity = ? WHERE client_order_id = ?",
            (quantity, client_order_id),
        )


# ---------------------------------------------------------------------------
# PB-01: legacy crypto ledger rows never leak into equity ownership
# ---------------------------------------------------------------------------


def _target(timestamp: datetime) -> TargetPortfolio:
    return TargetPortfolio(
        strategy_name="test",
        strategy_version="1",
        decision_timestamp=timestamp,
        weights=(),
        cash_weight=1.0,
        metadata={},
    )


def _risk(timestamp: datetime) -> RiskOverlayDecision:
    return RiskOverlayDecision(
        target=_target(timestamp),
        halted=False,
        reasons=(),
        gross_before=1.0,
        gross_after=1.0,
    )


def _order(decision_id: str, market: Market, symbol: str, quantity: float) -> PortfolioOrderRequest:
    return PortfolioOrderRequest(
        client_order_id=f"cid-{symbol}",
        decision_id=decision_id,
        symbol=symbol,
        market=market,
        side=PortfolioOrderSide.BUY,
        quantity=quantity,
        reference_price=100.0,
        decision_timestamp=datetime(2025, 1, 1, tzinfo=UTC),
        strategy_name="test",
        strategy_version="1",
        risk_reducing=False,
    )


def test_owned_quantities_ignores_legacy_crypto_rows(tmp_path):
    """Legacy crypto-sleeve ledger rows load but never count as ownership.

    The crypto adjustment exceeds the crypto fill: were those rows netted,
    the ownership invariant would trip and the equity runtime would crash."""
    repo = PortfolioStateRepository(tmp_path / "ownership.sqlite3")
    equity_ts = datetime(2025, 1, 1, tzinfo=UTC)
    crypto_ts = datetime(2025, 1, 2, tzinfo=UTC)

    equity_session = repo.begin_session("acct", "digest-equity", "paper")
    equity_decision = repo.record_decision(
        equity_session, "acct", "digest-equity", _target(equity_ts), _risk(equity_ts), Market.EQUITY
    )
    repo.record_order(_order(equity_decision, Market.EQUITY, "QLD", 5.0), "filled")
    _fill(repo, "cid-QLD", 5.0)

    crypto_session = repo.begin_session("acct", "digest-crypto", "paper")
    crypto_decision = repo.record_decision(
        crypto_session, "acct", "digest-crypto", _target(crypto_ts), _risk(crypto_ts), Market.CRYPTO
    )
    repo.record_order(_order(crypto_decision, Market.CRYPTO, "BTC/USD", 0.5), "filled")
    _fill(repo, "cid-BTC/USD", 0.5)

    repo.record_order(_order(crypto_decision, Market.CRYPTO, "ETH/USD", 2.0), "filled")
    _fill(repo, "cid-ETH/USD", 2.0)
    repo.record_ownership_adjustment("acct", "BTC/USD", Market.CRYPTO, 0.9, kind="stale_ownership_clear", note="legacy")

    assert repo.owned_quantities("acct", Market.EQUITY) == {"QLD": 5.0}
    assert repo.owned_symbols("acct", Market.EQUITY) == {"QLD"}
    assert repo.owned_quantities("acct", Market.CRYPTO) == {}
    assert repo.owned_quantities("acct") == {"QLD": 5.0}
    assert repo.owned_symbols("acct") == {"QLD"}
    assert repo.ownership_adjustments("acct") == ()


# ---------------------------------------------------------------------------
# PB-02: portfolio_no_trade dedup is day-scoped, not process-scoped
# ---------------------------------------------------------------------------


def _no_trade_rows(journal, expected: int) -> list[tuple[str, str, str]]:
    """Wait for ``expected`` durable rows; return (event_id, day, reason)."""
    deadline = time.monotonic() + 5.0
    while True:
        rows = journal.query(kind="portfolio_no_trade", order="asc")
        if len(rows) >= expected or time.monotonic() >= deadline:
            return [(row.event_id, str(row.fields["decision_timestamp"])[:10], row.fields["reason"]) for row in rows]
        time.sleep(0.02)


def test_no_trade_event_re_emits_on_the_next_decision_day(monkeypatch):
    """Every decision day must leave its own row in the real journal.

    The dashboard banner is simply the newest ``portfolio_no_trade`` row
    (``web/server_legacy.py``), and the journal inserts on the event id primary
    key, so a day-stable id would silently keep only the first day's row and
    freeze the banner on that reason forever.
    """
    monkeypatch.setattr(runtime_service, "_no_trade_last", {})
    journal = journal_module.get_journal()

    day_one = datetime(2025, 1, 1, 12, tzinfo=UTC)
    day_two = day_one + timedelta(days=1)
    common = {"market": "equity", "strategy": "equity_leveraged_trend_v1", "reason": "warming_up"}

    runtime_service._emit_no_trade_safe(decision_timestamp=day_one, **common)
    runtime_service._emit_no_trade_safe(decision_timestamp=day_one + timedelta(hours=1), **common)
    assert [(day, reason) for _, day, reason in _no_trade_rows(journal, 1)] == [("2025-01-01", "warming_up")], (
        "repeat of the same reason on the same day stays suppressed"
    )

    runtime_service._emit_no_trade_safe(decision_timestamp=day_two, **common)
    rows = _no_trade_rows(journal, 2)
    assert [(day, reason) for _, day, reason in rows] == [
        ("2025-01-01", "warming_up"),
        ("2025-01-02", "warming_up"),
    ], "a new decision day must persist its own row; otherwise the banner is stale forever"
    assert len({event_id for event_id, _, _ in rows}) == 2, "one row per day means one id per day"

    # A changed reason is a different key and emits immediately.
    runtime_service._emit_no_trade_safe(decision_timestamp=day_two, **{**common, "reason": "not_scheduled"})
    rows = _no_trade_rows(journal, 3)
    assert [(day, reason) for _, day, reason in rows] == [
        ("2025-01-01", "warming_up"),
        ("2025-01-02", "warming_up"),
        ("2025-01-02", "not_scheduled"),
    ]
    assert len({event_id for event_id, _, _ in rows}) == 3


# ---------------------------------------------------------------------------
# PB-10: bool is not an acceptable numeric strategy parameter
# ---------------------------------------------------------------------------


def test_param_helpers_reject_boolean_parameters():
    # A YAML ``sma_bars: true`` must fail closed at construction instead of
    # silently becoming 1 through ``isinstance(True, int)``.
    with pytest.raises(ValueError, match="sma_bars must be a non-negative integer"):
        EquityLeveragedTrendV1({"sma_bars": True})

    strategy = EquityLeveragedTrendV1()
    strategy.parameters["sma_bars"] = True
    with pytest.raises(ValueError, match="sma_bars must be numeric"):
        strategy.param_int("sma_bars", 200)
    strategy.parameters["vol_target"] = True
    with pytest.raises(ValueError, match="vol_target must be numeric"):
        strategy.param_float("vol_target", 0.0)


# ---------------------------------------------------------------------------
# PB-12: a degenerate (non-positive) close must not raise out of decide()
# ---------------------------------------------------------------------------


def _bars(symbol: str, closes: list[float], start: datetime) -> tuple:
    from parayabuck.core.models import Bar

    return tuple(
        Bar(
            symbol=symbol,
            market=Market.EQUITY,
            timeframe="1d",
            timestamp=start + timedelta(days=offset),
            open=close,
            high=close,
            low=close,
            close=close,
            volume=1.0,
        )
        for offset, close in enumerate(closes)
    )


def test_equity_volatility_treats_zero_close_as_insufficient_history():
    strategy = EquityLeveragedTrendV1({"vol_target": 0.35, "vol_window": 2, "sma_bars": 0})
    closes = [0.0, 100.0, 101.0]
    context = PortfolioContext(
        decision_timestamp=datetime(2025, 1, 4, tzinfo=UTC),
        market=Market.EQUITY,
        timeframe="1d",
        history={
            "QQQ": _bars("QQQ", closes, datetime(2025, 1, 1, tzinfo=UTC)),
            "QLD": _bars("QLD", closes, datetime(2025, 1, 1, tzinfo=UTC)),
        },
        universe=None,
        universe_version="digest",
        equity=1000.0,
        holdings={},
        prices={},
        benchmark_symbol="SPY",
    )

    assert strategy.realized_volatility(context) is None

    target = strategy.decide(context)
    assert target.weights == ()
    assert target.metadata["trend_state"] == "insufficient_history"
