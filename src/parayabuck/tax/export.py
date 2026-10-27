"""Calendar-year tax report, CSV and summary JSON export, and the YTD status panel.

Rules applied (decision record):

* Rule 3: gains and losses net within the calendar year of the sale; a net loss
  is not carried forward and no tax is due on it.
* Rule 6: the estimated income tax applies the year's bracket table to the net
  gain only. Other income is ignored, so the figure is an estimate.
* Dividends and interest: the Turkish tax on them is the progressive tax on the
  gain base plus that income minus the tax on the gain base (a marginal figure).
  The US withholding is credited up to that amount (rule 5).
* Rule 6: dividends plus interest above the GVK 86 threshold must be declared.
"""

from __future__ import annotations

import csv
import io
import json
import os
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

from parayabuck.tax.dividends import IncomeRecord, income_records, load_activities
from parayabuck.tax.errors import LedgerError, MissingIndexError, MissingRateError, TaxDataError
from parayabuck.tax.ledger import TURKEY_TIME, LotDisposal, build_disposals
from parayabuck.tax.rates import DEFAULT_USDTRY_PATH, DEFAULT_YIUFE_PATH, RateBook, load_rate_book
from parayabuck.tax.rules import US_TREATY_WITHHOLDING_CAP, declaration_threshold_tl, progressive_tax

COLUMNS: tuple[str, ...] = (
    "section",
    "symbol",
    "quantity",
    "acquired_on",
    "disposed_on",
    "buy_fill_id",
    "sell_fill_id",
    "buy_price_usd",
    "sell_price_usd",
    "buy_fee_usd",
    "sell_fee_usd",
    "cost_usd",
    "proceeds_usd",
    "buy_rate",
    "buy_rate_published_on",
    "sell_rate",
    "sell_rate_published_on",
    "cost_tl_nominal",
    "index_buy_month",
    "index_buy",
    "index_sell_month",
    "index_sell",
    "index_ratio",
    "indexation_applied",
    "cost_tl_used",
    "proceeds_tl",
    "gain_tl",
    "paid_on",
    "activity_type",
    "activity_id",
    "usd_amount",
    "rate",
    "rate_published_on",
    "tl_amount",
)


@dataclass(frozen=True)
class YearReport:
    """Every figure of one tax year for one account (estimates are labelled)."""

    year: int
    account_id: str
    disposals: tuple[LotDisposal, ...]
    income: tuple[IncomeRecord, ...]
    gains_tl: float
    losses_tl: float
    net_gain_tl: float
    gain_tax_base_tl: float
    estimated_tax_tl: float
    dividend_gross_usd: float
    dividend_gross_tl: float
    withholding_usd: float
    withholding_tl: float
    dividend_net_tl: float
    interest_tl: float
    income_threshold_tl: float
    income_declaration_required: bool
    dividend_tax_estimate_tl: float
    foreign_tax_credit_tl: float
    dividend_tax_due_tl: float
    notes: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        """Summary mapping written as JSON (disposal rows are in the CSV)."""
        return {
            "year": self.year,
            "account_id": self.account_id,
            "disposal_count": len(self.disposals),
            "gains_tl": _r(self.gains_tl),
            "losses_tl": _r(self.losses_tl),
            "net_gain_tl": _r(self.net_gain_tl),
            "estimated_tax_tl": _r(self.estimated_tax_tl),
            "estimated_tax_basis": "2026 bracket table on the year's net gain only; other income ignored",
            "dividends": {
                "gross_usd": _r(self.dividend_gross_usd),
                "gross_tl": _r(self.dividend_gross_tl),
                "withheld_usd": _r(self.withholding_usd),
                "withheld_tl": _r(self.withholding_tl),
                "net_tl": _r(self.dividend_net_tl),
            },
            "interest_tl": _r(self.interest_tl),
            "income_threshold_tl": self.income_threshold_tl,
            "income_declaration_required": self.income_declaration_required,
            "dividend_tax_estimate_tl": _r(self.dividend_tax_estimate_tl),
            "foreign_tax_credit_tl": _r(self.foreign_tax_credit_tl),
            "dividend_tax_due_tl": _r(self.dividend_tax_due_tl),
            "notes": list(self.notes),
        }


def _r(value: float) -> float:
    return round(float(value), 2)


def build_year_report(
    connection: sqlite3.Connection,
    account_id: str,
    year: int,
    rates: RateBook,
) -> YearReport:
    """Compute one tax year. Missing rates, indices, tariffs or a bad book raise."""
    threshold = declaration_threshold_tl(year)
    progressive_tax(0.0, year)  # fails early when the year has no bracket table
    all_disposals = build_disposals(connection, account_id, rates)
    disposals = tuple(item for item in all_disposals if item.disposed_on.year == year)
    income = tuple(income_records(load_activities(connection, account_id, year), rates))

    gains = sum(item.gain_tl for item in disposals if item.gain_tl > 0)
    losses = sum(item.gain_tl for item in disposals if item.gain_tl < 0)
    net_gain = gains + losses
    gain_base = max(net_gain, 0.0)
    estimated_tax = progressive_tax(gain_base, year)

    dividend_gross_usd = sum(item.usd_amount for item in income if item.kind == "dividend")
    dividend_gross_tl = sum(item.tl_amount for item in income if item.kind == "dividend")
    withholding_usd = sum(item.usd_amount for item in income if item.kind == "withholding")
    withholding_tl = sum(item.tl_amount for item in income if item.kind == "withholding")
    interest_tl = sum(item.tl_amount for item in income if item.kind == "interest")

    income_total_tl = dividend_gross_tl + interest_tl
    dividend_tax = progressive_tax(gain_base + max(income_total_tl, 0.0), year) - estimated_tax
    dividend_tax = max(dividend_tax, 0.0)
    credit = min(max(withholding_tl, 0.0), dividend_tax)

    notes: list[str] = []
    if not income:
        notes.append(f"no dividend or interest activities stored for {year}; run `tax import-activities`")
    if dividend_gross_usd > 0 and withholding_usd > dividend_gross_usd * US_TREATY_WITHHOLDING_CAP + 1e-6:
        notes.append("US withholding exceeds the 20% treaty cap; check the W-8BEN on file with the broker")
    if net_gain < 0:
        notes.append("net loss for the year; not carried forward and no tax is due on it (rule 3)")
    if not disposals:
        notes.append(f"no disposals in {year}")

    return YearReport(
        year=year,
        account_id=account_id,
        disposals=disposals,
        income=income,
        gains_tl=gains,
        losses_tl=losses,
        net_gain_tl=net_gain,
        gain_tax_base_tl=gain_base,
        estimated_tax_tl=estimated_tax,
        dividend_gross_usd=dividend_gross_usd,
        dividend_gross_tl=dividend_gross_tl,
        withholding_usd=withholding_usd,
        withholding_tl=withholding_tl,
        dividend_net_tl=dividend_gross_tl - withholding_tl,
        interest_tl=interest_tl,
        income_threshold_tl=threshold,
        income_declaration_required=income_total_tl > threshold,
        dividend_tax_estimate_tl=dividend_tax,
        foreign_tax_credit_tl=credit,
        dividend_tax_due_tl=dividend_tax - credit,
        notes=tuple(notes),
    )


def _cell(value: Any) -> Any:
    if isinstance(value, bool) or value is None:
        return "" if value is None else str(value).lower()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, float):
        return round(value, 6)
    return value


def _disposal_row(item: LotDisposal) -> dict[str, Any]:
    row: dict[str, Any] = {"section": "disposal"}
    row.update({key: getattr(item, key) for key in LotDisposal.__dataclass_fields__ if key in COLUMNS})
    return {key: _cell(value) for key, value in row.items()}


def _income_row(item: IncomeRecord) -> dict[str, Any]:
    row: dict[str, Any] = {
        "section": item.kind,
        "symbol": item.symbol,
        "paid_on": item.paid_on,
        "activity_type": item.activity_type,
        "activity_id": item.activity_id,
        "usd_amount": item.usd_amount,
        "rate": item.rate,
        "rate_published_on": item.rate_published_on,
        "tl_amount": item.tl_amount,
    }
    return {key: _cell(value) for key, value in row.items()}


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / (path.name + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, path)


def export_year(report: YearReport, out: Path) -> tuple[Path, Path]:
    """Write one CSV row per disposal, then dividend/interest rows, and the summary JSON.

    Returns ``(csv_path, summary_path)``; the summary sits next to the CSV as
    ``<stem>.summary.json``.
    """
    out = Path(out)
    rows = [_disposal_row(item) for item in report.disposals] + [_income_row(item) for item in report.income]
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=list(COLUMNS), extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow({key: row.get(key, "") for key in COLUMNS})
    summary_path = out.parent / f"{out.stem}.summary.json"
    _atomic_write(out, buffer.getvalue())
    _atomic_write(summary_path, json.dumps(report.to_dict(), ensure_ascii=False, indent=2) + "\n")
    return out, summary_path


def ytd_status(
    connection: sqlite3.Connection | None,
    account_id: str,
    year: int,
    *,
    usdtry_path: Path = DEFAULT_USDTRY_PATH,
    yiufe_path: Path = DEFAULT_YIUFE_PATH,
) -> dict[str, Any]:
    """The YTD figures for dashboards and the daily summary; never raises.

    ``status`` is one of ``ok``, ``rates_missing``, ``ledger_error``,
    ``unavailable`` (no database) or ``error``.
    """
    base: dict[str, Any] = {"year": year, "account_id": account_id}
    if connection is None:
        return {**base, "status": "unavailable", "error": "ledger database not found"}
    try:
        rates = load_rate_book(Path(usdtry_path), Path(yiufe_path))
        report = build_year_report(connection, account_id, year, rates)
    except (MissingRateError, MissingIndexError) as exc:
        return {**base, "status": "rates_missing", "error": str(exc)}
    except TaxDataError as exc:
        if isinstance(exc, LedgerError):
            return {**base, "status": "ledger_error", "error": str(exc)}
        return {**base, "status": "rates_missing", "error": str(exc)}
    except sqlite3.Error as exc:
        return {**base, "status": "unavailable", "error": str(exc)}
    return {
        **base,
        "status": "ok",
        "as_of_day": datetime.now(TURKEY_TIME).date().isoformat(),
        "net_gain_tl": _r(report.net_gain_tl),
        "estimated_tax_tl": _r(report.estimated_tax_tl),
        "disposal_count": len(report.disposals),
        "dividend_gross_tl": _r(report.dividend_gross_tl),
        "dividend_withheld_tl": _r(report.withholding_tl),
        "dividend_net_tl": _r(report.dividend_net_tl),
        "interest_tl": _r(report.interest_tl),
        "income_declaration_required": report.income_declaration_required,
        "dividend_tax_due_tl": _r(report.dividend_tax_due_tl),
    }


__all__ = [
    "COLUMNS",
    "YearReport",
    "build_year_report",
    "export_year",
    "ytd_status",
]
