"""Dividend, non-resident withholding and interest records from Alpaca activities.

Alpaca activity types used: ``DIV`` (dividend, positive), ``DIVNRA`` (dividend
withheld from a non-resident, negative), ``INT`` (interest). Activities are
stored idempotently by Alpaca's activity id in the ``tax_activities`` table,
which this package creates in the runtime SQLite database.

Each USD amount is converted at the rate published on the previous business day
before its payment date (rule 4, the same rule as the trade legs). The US
withholding is creditable against the Turkish tax on the dividends (rule 5),
capped there by :mod:`parayabuck.tax.export`.
"""

from __future__ import annotations

import json
import math
import sqlite3
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

from parayabuck.tax.errors import TaxDataError
from parayabuck.tax.rates import RateBook

TAX_ACTIVITY_TYPES = ("DIV", "DIVNRA", "INT")
# Alpaca income types this package does not import (capital-gain distributions and
# return of capital). The decision record has no rule for them, and a return of
# capital also changes the cost basis, so they are reported as missing, not guessed.
NOT_IMPORTED_INCOME_TYPES = ("DIVCGL", "DIVCGS", "DIVROC")
ACTIVITIES_PATH = "/account/activities"
ACCOUNT_PATH = "/account"
_PAGE_SIZE = 100

# (activity type) -> record kind
_KIND_BY_TYPE = {"DIV": "dividend", "DIVNRA": "withholding", "INT": "interest"}

Get = Callable[[str, Mapping[str, Any]], Any]

_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS tax_activities (
    activity_id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL,
    activity_type TEXT NOT NULL,
    activity_date TEXT NOT NULL,
    symbol TEXT NOT NULL DEFAULT '',
    quantity REAL,
    per_share_amount REAL,
    net_amount REAL NOT NULL,
    raw_json TEXT NOT NULL DEFAULT '',
    imported_at TEXT NOT NULL
)
"""


@dataclass(frozen=True)
class ActivityRow:
    """One normalized Alpaca activity; ``net_amount`` is signed USD."""

    activity_id: str
    account_id: str
    activity_type: str
    activity_date: date
    symbol: str
    quantity: float | None
    per_share_amount: float | None
    net_amount: float


@dataclass(frozen=True)
class IncomeRecord:
    """One dividend, withholding or interest amount in USD and TL."""

    kind: str
    activity_id: str
    activity_type: str
    symbol: str
    paid_on: date
    usd_amount: float
    rate: float
    rate_published_on: date
    tl_amount: float


def _number(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise TaxDataError(f"activity amount is not a number: {value!r}") from exc
    if not math.isfinite(parsed):
        raise TaxDataError(f"activity amount is not a finite number: {value!r}")
    return parsed


def fetch_account_id(get: Get) -> str:
    """The Alpaca account id (``GET /account``) that the activities belong to."""
    account = get(ACCOUNT_PATH, {})
    account_id = str(account.get("id") or "") if isinstance(account, Mapping) else ""
    if not account_id:
        raise TaxDataError("Alpaca account response has no id")
    return account_id


def fetch_activities(get: Get, since: date, *, page_size: int = _PAGE_SIZE) -> list[Mapping[str, Any]]:
    """Every tax-relevant activity on or after ``since``, oldest first.

    Pages through the activities endpoint with ``page_token`` (the id of the last
    row). A page that does not advance raises instead of looping.
    """
    rows: list[Mapping[str, Any]] = []
    seen_tokens: set[str] = set()
    page_token: str | None = None
    while True:
        query: dict[str, Any] = {
            "activity_types": ",".join(TAX_ACTIVITY_TYPES),
            "after": since.isoformat(),
            "direction": "asc",
            "page_size": page_size,
        }
        if page_token is not None:
            query["page_token"] = page_token
        page = get(ACTIVITIES_PATH, query)
        if not isinstance(page, list):
            raise TaxDataError("Alpaca activities response is not a list")
        rows.extend(item for item in page if isinstance(item, Mapping))
        if len(page) < page_size:
            return rows
        token = str(page[-1].get("id") or "") if isinstance(page[-1], Mapping) else ""
        if not token or token in seen_tokens:
            raise TaxDataError("Alpaca activity pagination did not advance")
        seen_tokens.add(token)
        page_token = token


def parse_activity(raw: Mapping[str, Any], account_id: str) -> ActivityRow:
    """Normalize one raw activity; unknown types and missing ids raise."""
    activity_id = str(raw.get("id") or "").strip()
    if not activity_id:
        raise TaxDataError("activity without an id")
    activity_type = str(raw.get("activity_type") or "").upper()
    if activity_type not in TAX_ACTIVITY_TYPES:
        raise TaxDataError(f"activity {activity_id} has unexpected type {activity_type!r}")
    raw_date = str(raw.get("date") or "")[:10]
    try:
        activity_date = date.fromisoformat(raw_date)
    except ValueError as exc:
        raise TaxDataError(f"activity {activity_id} has no valid date: {raw.get('date')!r}") from exc
    net_amount = _number(raw.get("net_amount"))
    if net_amount is None:
        raise TaxDataError(f"activity {activity_id} has no net_amount")
    return ActivityRow(
        activity_id=activity_id,
        account_id=account_id,
        activity_type=activity_type,
        activity_date=activity_date,
        symbol=str(raw.get("symbol") or ""),
        quantity=_number(raw.get("qty")),
        per_share_amount=_number(raw.get("per_share_amount")),
        net_amount=net_amount,
    )


def ensure_table(connection: sqlite3.Connection) -> None:
    """Create ``tax_activities`` if it does not exist (idempotent)."""
    connection.execute(_CREATE_SQL)


def store_activities(
    connection: sqlite3.Connection,
    rows: Sequence[ActivityRow],
    *,
    raw_by_id: Mapping[str, Mapping[str, Any]] | None = None,
    imported_at: datetime | None = None,
) -> tuple[int, int]:
    """Insert activities keyed by Alpaca id; returns ``(inserted, already_present)``.

    Re-importing the same window is a no-op: an existing id is never rewritten.
    """
    ensure_table(connection)
    stamp = (imported_at or datetime.now(UTC)).isoformat()
    inserted = 0
    for row in rows:
        raw = raw_by_id.get(row.activity_id, {}) if raw_by_id is not None else {}
        cursor = connection.execute(
            """INSERT OR IGNORE INTO tax_activities
               (activity_id, account_id, activity_type, activity_date, symbol, quantity,
                per_share_amount, net_amount, raw_json, imported_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                row.activity_id,
                row.account_id,
                row.activity_type,
                row.activity_date.isoformat(),
                row.symbol,
                row.quantity,
                row.per_share_amount,
                row.net_amount,
                json.dumps(dict(raw), default=str, sort_keys=True),
                stamp,
            ),
        )
        inserted += cursor.rowcount
    return inserted, len(rows) - inserted


def load_activities(connection: sqlite3.Connection, account_id: str, year: int | None = None) -> list[ActivityRow]:
    """Stored activities of ``account_id`` (optionally one calendar year), by date."""
    present = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'tax_activities'"
    ).fetchone()
    if present is None:
        return []
    query = (
        "SELECT activity_id, account_id, activity_type, activity_date, symbol, quantity, "
        "per_share_amount, net_amount FROM tax_activities WHERE account_id = ?"
    )
    parameters: list[Any] = [account_id]
    if year is not None:
        query += " AND activity_date >= ? AND activity_date <= ?"
        parameters += [f"{year:04d}-01-01", f"{year:04d}-12-31"]
    query += " ORDER BY activity_date, activity_id"
    return [
        ActivityRow(
            activity_id=str(row[0]),
            account_id=str(row[1]),
            activity_type=str(row[2]),
            activity_date=date.fromisoformat(str(row[3])),
            symbol=str(row[4] or ""),
            quantity=None if row[5] is None else float(row[5]),
            per_share_amount=None if row[6] is None else float(row[6]),
            net_amount=float(row[7]),
        )
        for row in connection.execute(query, parameters).fetchall()
    ]


def income_records(activities: Sequence[ActivityRow], rates: RateBook) -> list[IncomeRecord]:
    """Convert stored activities to TL at the rate published on the previous business day
    before each payment date (rule 4; :meth:`RateBook.usd_try_for_trade_date`).

    ``DIVNRA`` is recorded as a positive withheld amount (the negative Alpaca
    net is sign-flipped), so the withholding totals read as "tax withheld".
    """
    records: list[IncomeRecord] = []
    for row in activities:
        kind = _KIND_BY_TYPE[row.activity_type]
        usd = -row.net_amount if kind == "withholding" else row.net_amount
        quote = rates.usd_try_for_trade_date(row.activity_date)
        records.append(
            IncomeRecord(
                kind=kind,
                activity_id=row.activity_id,
                activity_type=row.activity_type,
                symbol=row.symbol,
                paid_on=row.activity_date,
                usd_amount=usd,
                rate=quote.rate,
                rate_published_on=quote.published_on,
                tl_amount=usd * quote.rate,
            )
        )
    return records


__all__ = [
    "ACCOUNT_PATH",
    "ACTIVITIES_PATH",
    "ActivityRow",
    "IncomeRecord",
    "NOT_IMPORTED_INCOME_TYPES",
    "TAX_ACTIVITY_TYPES",
    "ensure_table",
    "fetch_account_id",
    "fetch_activities",
    "income_records",
    "load_activities",
    "parse_activity",
    "store_activities",
]
