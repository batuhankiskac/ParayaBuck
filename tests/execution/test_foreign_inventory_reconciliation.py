"""Foreign inventory is never drift; owned share-count changes are rebased.

The Alpaca PAPER account is shared with the operator and with the deleted
crypto sleeve, so a real reconcile can meet positions ParayaBuck never sized:
crypto, and equity the strategy does not trade. These are classified and
alerted once at startup by the runtime and must never raise the drift that
halts it. Activity on a symbol the bot DOES trade (QLD) keeps today's
fail-closed semantics, and so does every position while the broker cannot
state the bot's tradable universe.

The last two tests pin the boundary between that exclusion and the audited
ownership rebase: on QLD the bot is the only legitimate owner, so an unowned
holding stays drift while a share count the bot owns is rebased instead.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from parayabuck.core.models import Market
from parayabuck.execution.alpaca import AlpacaBroker
from parayabuck.execution.fake_client import FakeTradingClient
from parayabuck.execution.portfolio import PortfolioOrderRequest, PortfolioOrderSide, RiskOverlayDecision
from parayabuck.portfolio.models import TargetPortfolio
from parayabuck.portfolio.runtime_repository import PortfolioStateRepository


def _broker(fake: FakeTradingClient, **kwargs: Any) -> AlpacaBroker:
    """The production composition: real broker, account and reconciliation."""
    return AlpacaBroker("k", "s", paper=True, client=fake, **kwargs)


def test_crypto_and_out_of_universe_equity_are_never_drift() -> None:
    fake = FakeTradingClient()
    fake.set_position("QLD", 10.0)
    fake.set_position("AAPL", 3.0)
    fake.set_position("BTC/USD", 0.5, market="crypto")
    broker = _broker(
        fake,
        manage_external_positions=False,
        portfolio_owned_quantities=lambda: {"QLD": 10.0},
        portfolio_open_orders=lambda: (),
        bot_tradable_symbols=lambda: {"QLD"},
    )

    report = broker.reconcile()

    # The bot's own holding still reconciles; the two foreign positions are
    # excluded from every bucket instead of halting the engine.
    assert report["MATCH"] == ["QLD"]
    assert {symbol for values in report.values() for symbol in values} == {"QLD"}
    # Foreign inventory is never protected or traded.
    assert fake.submitted_stops == 0
    assert fake.submitted_market_orders == 0


def test_foreign_position_on_a_traded_symbol_still_reports_drift() -> None:
    """QLD is the strategy's own symbol: an unowned QLD holding is drift."""
    fake = FakeTradingClient()
    fake.set_position("QLD", 10.0)
    fake.set_position("BTC/USD", 0.5, market="crypto")
    broker = _broker(
        fake,
        manage_external_positions=False,
        portfolio_owned_quantities=lambda: {},
        portfolio_open_orders=lambda: (),
        bot_tradable_symbols=lambda: {"QLD"},
    )

    report = broker.reconcile()

    assert report["BROKER_STALE"] == ["QLD"]
    assert report["MISSING_LOCAL_ORDER"] == ["QLD"]
    assert "BTC/USD" not in {symbol for values in report.values() for symbol in values}


def test_unowned_equity_stays_drift_without_a_stated_universe() -> None:
    """Fail closed: with no tradable universe the bot cannot claim the
    position is someone else's, so it remains ownership drift."""
    fake = FakeTradingClient()
    fake.set_position("AAPL", 3.0)
    broker = _broker(
        fake,
        manage_external_positions=False,
        portfolio_owned_quantities=lambda: {},
        portfolio_open_orders=lambda: (),
    )

    report = broker.reconcile()

    assert report["BROKER_STALE"] == ["AAPL"]


def test_managed_position_outside_the_universe_is_still_reconciled() -> None:
    """A position the bot owns is never foreign, even once it leaves the
    configured universe: the quantity must keep being compared."""
    fake = FakeTradingClient()
    fake.set_position("MSFT", 4.0)
    broker = _broker(
        fake,
        manage_external_positions=False,
        portfolio_owned_quantities=lambda: {"MSFT": 4.0},
        portfolio_open_orders=lambda: (),
        bot_tradable_symbols=lambda: {"QLD"},
    )

    report = broker.reconcile()

    assert report["MATCH"] == ["MSFT"]


# ---------------------------------------------------------------------------
# Interaction with the audited ownership rebase (wave 1c)
# ---------------------------------------------------------------------------

ACCOUNT = "fake-acct-1234"  # the fake client's account id
SYMBOL = "QLD"
T0 = datetime(2026, 9, 1, tzinfo=UTC)


def _owned_fill(repo: PortfolioStateRepository, quantity: float, price: float) -> None:
    """Record our own BUY and its real fill: ownership is net fills."""
    target = TargetPortfolio("vt35", "1", T0, (), 1.0)
    risk = RiskOverlayDecision(target, False, (), 0.0, 0.0)
    session = repo.begin_session(ACCOUNT, "digest", "paper")
    decision_id = repo.record_decision(session, ACCOUNT, "digest", target, risk, Market.EQUITY)
    client_order_id = "cid-buy"
    repo.record_order(
        PortfolioOrderRequest(
            client_order_id=client_order_id,
            decision_id=decision_id,
            symbol=SYMBOL,
            market=Market.EQUITY,
            side=PortfolioOrderSide("buy"),
            quantity=quantity,
            reference_price=price,
            decision_timestamp=T0,
            strategy_name="vt35",
            strategy_version="1",
            risk_reducing=False,
        ),
        "submitted",
    )
    repo.apply_trade_update(
        client_order_id=client_order_id,
        broker_order_id="broker-cid-buy",
        status="filled",
        cumulative_filled_quantity=quantity,
        average_fill_price=price,
        occurred_at=T0,
        activity_id="activity-cid-buy",
        fee=0.0,
        realized_pnl=0.0,
        unrealized_pnl=0.0,
    )


def _rebase_broker(fake: FakeTradingClient, repo: PortfolioStateRepository) -> tuple[AlpacaBroker, list[str]]:
    """The runtime's composition: the repository is the rebase authority."""
    asked: list[str] = []

    def resolve(symbol: str, market: Market, broker_qty: float, broker_average: float | None) -> float | None:
        asked.append(symbol)
        return repo.reconcile_broker_quantity(ACCOUNT, symbol, market, broker_qty, broker_average)

    broker = _broker(
        fake,
        manage_external_positions=False,
        portfolio_owned_quantities=lambda: repo.owned_quantities(ACCOUNT, Market.EQUITY),
        portfolio_open_orders=lambda: (),
        portfolio_ownership_resolved=resolve,
        bot_tradable_symbols=lambda: {"QLD"},
    )
    return broker, asked


def test_owned_qld_shortfall_is_rebased_while_foreign_equity_stays_excluded(tmp_path: Path) -> None:
    """Our 100 QLD shares, 40 of them sold from outside: QLD is a symbol the
    bot trades, so the foreign-inventory exclusion must not swallow it — the
    audited reduction resolves it and AAPL stays out of every bucket."""
    repo = PortfolioStateRepository(tmp_path / "runtime.sqlite3")
    _owned_fill(repo, 100.0, 100.0)
    fake = FakeTradingClient()
    fake.set_position("QLD", 60.0)
    fake.set_position("AAPL", 3.0)
    broker, asked = _rebase_broker(fake, repo)

    report = broker.reconcile()

    assert asked == [SYMBOL]
    assert report["MATCH"] == [SYMBOL]
    assert repo.owned_quantities(ACCOUNT, Market.EQUITY)[SYMBOL] == pytest.approx(60.0)
    assert [(row["kind"], row["outstanding_quantity"]) for row in repo.ownership_adjustments(ACCOUNT)] == [
        ("foreign_sell_reduction", 40.0)
    ]
    repo.close()


def test_unowned_qld_stays_drift_and_is_never_rebased(tmp_path: Path) -> None:
    """Nobody's QLD: the bot owns zero shares, so there is no ownership to
    rebase onto the broker's count and the holding must keep halting as
    drift until an operator resolves it."""
    repo = PortfolioStateRepository(tmp_path / "runtime.sqlite3")
    fake = FakeTradingClient()
    fake.set_position("QLD", 10.0)
    fake.set_position("AAPL", 3.0)
    broker, asked = _rebase_broker(fake, repo)

    report = broker.reconcile()

    assert asked == []
    assert report["BROKER_STALE"] == [SYMBOL]
    assert report["MISSING_LOCAL_ORDER"] == [SYMBOL]
    assert repo.ownership_adjustments(ACCOUNT) == ()
    repo.close()
