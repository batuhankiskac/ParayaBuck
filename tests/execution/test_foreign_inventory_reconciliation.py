"""Foreign inventory in the shared account is observed, never ownership drift.

The Alpaca PAPER account is shared with the operator and with the deleted
crypto sleeve, so a real reconcile can meet positions ParayaBuck never sized:
crypto, and equity the strategy does not trade. These are classified and
alerted once at startup by the runtime and must never raise the drift that
halts it. Activity on a symbol the bot DOES trade (QLD) keeps today's
fail-closed semantics, and so does every position while the broker cannot
state the bot's tradable universe.
"""

from __future__ import annotations

from typing import Any

from parayabuck.execution.alpaca import AlpacaBroker
from parayabuck.execution.fake_client import FakeTradingClient


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
