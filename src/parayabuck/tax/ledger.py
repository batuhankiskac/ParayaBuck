"""FIFO lot matching of the bot's own fills into TL disposal records.

Fills are read with the same join as ``PortfolioStateRepository._owned_events``
(portfolio_fills -> orders -> decisions -> runtime sessions), read-only.

Per disposal (decision record rules 1-4):

* USD cost = lot price x quantity + the buy fee share; USD proceeds = sale price x
  quantity - the sell fee share (fees are per fill, split pro rata by quantity).
* TL cost = USD cost x USD/TRY rate of the previous business day before the
  acquisition; TL proceeds = USD proceeds x rate of the previous business day
  before the sale. The FX movement therefore stays in the gain.
* Indexation: index(month before sale) / index(month before acquisition). When
  ratio - 1 >= 0.10 the TL cost is multiplied by the full ratio, else the nominal
  TL cost is used.

Trade dates are Türkiye calendar dates (UTC+03, no DST).
"""

from __future__ import annotations

import sqlite3
from collections import deque
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta, timezone
from typing import Any

from parayabuck.core.models import Market
from parayabuck.tax.errors import LedgerError
from parayabuck.tax.rates import RateBook, prior_month_key
from parayabuck.tax.rules import INDEXATION_MIN_RISE

TURKEY_TIME = timezone(timedelta(hours=3), "UTC+03")
_EPS = 1e-9


@dataclass(frozen=True)
class Fill:
    """One bot fill of an equity symbol (quantities and prices in USD)."""

    fill_id: str
    symbol: str
    side: str
    occurred_at: datetime
    quantity: float
    price: float
    fee: float


@dataclass(frozen=True)
class LotDisposal:
    """A quantity of one FIFO lot sold by one sale fill, with every TL input kept."""

    symbol: str
    quantity: float
    buy_fill_id: str
    sell_fill_id: str
    acquired_on: date
    disposed_on: date
    buy_price_usd: float
    sell_price_usd: float
    buy_fee_usd: float
    sell_fee_usd: float
    cost_usd: float
    proceeds_usd: float
    buy_rate: float
    buy_rate_published_on: date
    sell_rate: float
    sell_rate_published_on: date
    cost_tl_nominal: float
    index_buy_month: str
    index_buy: float
    index_sell_month: str
    index_sell: float
    index_ratio: float
    indexation_applied: bool
    cost_tl_used: float
    proceeds_tl: float
    gain_tl: float


def turkey_date(instant: datetime) -> date:
    """The Türkiye calendar date of an aware instant."""
    return instant.astimezone(TURKEY_TIME).date()


def parse_instant(text: str) -> datetime:
    """An ISO timestamp as an aware datetime (naive values are read as UTC)."""
    value = datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value


def read_fills(connection: sqlite3.Connection, account_id: str) -> list[Fill]:
    """Every bot equity fill of ``account_id``, oldest first (read-only query)."""
    rows = connection.execute(
        """SELECT f.activity_id, o.symbol, o.side, f.occurred_at, f.quantity, f.price, f.fee
           FROM portfolio_fills f
           JOIN portfolio_orders o ON o.client_order_id = f.client_order_id
           JOIN portfolio_decisions d ON d.decision_id = o.decision_id
           JOIN portfolio_runtime_sessions s ON s.session_id = d.session_id
           WHERE s.account_id = ? AND o.market = ? AND f.quantity > 0""",
        (account_id, Market.EQUITY.value),
    ).fetchall()
    fills: list[Fill] = []
    for fill_id, symbol, side, occurred_at, quantity, price, fee in rows:
        normalized = str(side).lower()
        if normalized not in {"buy", "sell"}:
            raise LedgerError(f"fill {fill_id} has unknown side {side!r}")
        fills.append(
            Fill(
                fill_id=str(fill_id),
                symbol=str(symbol),
                side=normalized,
                occurred_at=parse_instant(str(occurred_at)),
                quantity=float(quantity),
                price=float(price),
                fee=float(fee or 0.0),
            )
        )
    fills.sort(key=lambda fill: (fill.occurred_at, fill.fill_id))
    return fills


def assert_no_ownership_adjustments(
    connection: sqlite3.Connection,
    account_id: str,
    symbols: Collection[str] | None = None,
) -> None:
    """Fail closed when a symbol's broker book was changed outside fills (splits, rebases).

    The fill-based FIFO book cannot reproduce those adjustments, so a tax figure
    computed from fills alone would be silently wrong. The adjustment rows carry no
    effective date, so the scope is the symbol: ``symbols`` limits the check to the
    symbols whose disposals are reported (``None`` checks every symbol). Other
    symbols keep their own lot book and are not affected.
    """
    present = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'portfolio_ownership_adjustments'"
    ).fetchone()
    if present is None:
        return
    rows = connection.execute(
        "SELECT symbol, COUNT(*), MIN(created_at), MAX(created_at) FROM portfolio_ownership_adjustments "
        "WHERE account_id = ? AND market = ? GROUP BY symbol",
        (account_id, Market.EQUITY.value),
    ).fetchall()
    adjusted = {str(symbol): int(count) for symbol, count, *_ in rows if symbols is None or symbol in symbols}
    if adjusted:
        # The rows carry only created_at, not the effective date of the split or rebase, so a
        # later-recorded row still blocks an earlier year. Narrowing by created_at could let an
        # adjustment recorded after year end slip through, so the block stays and the dates are shown.
        recorded = ", ".join(
            f"{symbol} {first[:10]}..{last[:10]}" for symbol, _count, first, last in rows if str(symbol) in adjusted
        )
        raise LedgerError(
            f"ownership adjustment(s) recorded for {', '.join(sorted(adjusted))} in account {account_id} "
            f"({sum(adjusted.values())} row(s), created {recorded}); the fill-based TL ledger does not model "
            "splits or rebases, so no figure is produced for those symbols. Reconcile the lots manually first."
        )


def _disposal(symbol: str, buy: Fill, quantity: float, sell: Fill, rates: RateBook) -> LotDisposal:
    buy_fee = buy.fee * quantity / buy.quantity
    sell_fee = sell.fee * quantity / sell.quantity
    cost_usd = quantity * buy.price + buy_fee
    proceeds_usd = quantity * sell.price - sell_fee
    acquired = turkey_date(buy.occurred_at)
    disposed = turkey_date(sell.occurred_at)
    buy_quote = rates.usd_try_for_trade_date(acquired)
    sell_quote = rates.usd_try_for_trade_date(disposed)
    cost_tl_nominal = cost_usd * buy_quote.rate
    proceeds_tl = proceeds_usd * sell_quote.rate
    buy_month = prior_month_key(acquired)
    sell_month = prior_month_key(disposed)
    index_buy = rates.yiufe_for_month(buy_month)
    index_sell = rates.yiufe_for_month(sell_month)
    ratio = index_sell / index_buy
    applied = (ratio - 1.0) >= INDEXATION_MIN_RISE - 1e-12
    cost_tl_used = cost_tl_nominal * ratio if applied else cost_tl_nominal
    return LotDisposal(
        symbol=symbol,
        quantity=quantity,
        buy_fill_id=buy.fill_id,
        sell_fill_id=sell.fill_id,
        acquired_on=acquired,
        disposed_on=disposed,
        buy_price_usd=buy.price,
        sell_price_usd=sell.price,
        buy_fee_usd=buy_fee,
        sell_fee_usd=sell_fee,
        cost_usd=cost_usd,
        proceeds_usd=proceeds_usd,
        buy_rate=buy_quote.rate,
        buy_rate_published_on=buy_quote.published_on,
        sell_rate=sell_quote.rate,
        sell_rate_published_on=sell_quote.published_on,
        cost_tl_nominal=cost_tl_nominal,
        index_buy_month=buy_month,
        index_buy=index_buy,
        index_sell_month=sell_month,
        index_sell=index_sell,
        index_ratio=ratio,
        indexation_applied=applied,
        cost_tl_used=cost_tl_used,
        proceeds_tl=proceeds_tl,
        gain_tl=proceeds_tl - cost_tl_used,
    )


def match_disposals(fills: Sequence[Fill], rates: RateBook) -> list[LotDisposal]:
    """FIFO-match every sale against the earliest open bot lots of its symbol.

    A sale larger than the open bot lots raises :class:`LedgerError`: the bot
    cannot account for shares it did not buy, so nothing is guessed.
    """
    by_symbol: dict[str, list[Fill]] = {}
    for fill in fills:
        by_symbol.setdefault(fill.symbol, []).append(fill)
    disposals: list[LotDisposal] = []
    for symbol in sorted(by_symbol):
        lots: deque[list[Any]] = deque()  # [buy Fill, open quantity]
        for fill in sorted(by_symbol[symbol], key=lambda item: (item.occurred_at, item.fill_id)):
            if fill.side == "buy":
                lots.append([fill, fill.quantity])
                continue
            remaining = fill.quantity
            while remaining > _EPS:
                if not lots:
                    raise LedgerError(
                        f"sell fill {fill.fill_id} of {symbol} on {turkey_date(fill.occurred_at).isoformat()} "
                        "exceeds the open bot lots; the fill book does not cover this position"
                    )
                lot_fill, open_quantity = lots[0]
                taken = min(open_quantity, remaining)
                disposals.append(_disposal(symbol, lot_fill, taken, fill, rates))
                remaining -= taken
                if open_quantity - taken <= _EPS:
                    lots.popleft()
                else:
                    lots[0][1] = open_quantity - taken
    disposals.sort(key=lambda item: (item.disposed_on, item.sell_fill_id, item.symbol, item.buy_fill_id))
    return disposals


def build_disposals(
    connection: sqlite3.Connection,
    account_id: str,
    rates: RateBook,
    year: int | None = None,
) -> list[LotDisposal]:
    """Read the account's fills and match every disposal (FIFO from the start).

    Without ``year`` every disposal is returned and the adjustment guard covers all
    symbols. With ``year`` only that year's disposals are returned, and the guard
    covers only the symbols sold in that year.
    """
    disposals = match_disposals(read_fills(connection, account_id), rates)
    if year is None:
        assert_no_ownership_adjustments(connection, account_id)
        return disposals
    selected = [item for item in disposals if item.disposed_on.year == year]
    assert_no_ownership_adjustments(connection, account_id, {item.symbol for item in selected})
    return selected


__all__ = [
    "Fill",
    "LotDisposal",
    "TURKEY_TIME",
    "assert_no_ownership_adjustments",
    "build_disposals",
    "match_disposals",
    "parse_instant",
    "read_fills",
    "turkey_date",
]
