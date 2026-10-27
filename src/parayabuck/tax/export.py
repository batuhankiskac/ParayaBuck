"""Calendar-year tax report, CSV and summary JSON export, and the YTD status panel.

Rules applied (decision record):

* Rule 3: gains and losses net within the calendar year of the sale; a net loss
  is not carried forward and no tax is due on it.
* Rule 6: the estimated income tax applies the year's bracket table to the net
  gain only. Other income is ignored, so the figure is an estimate.
* Dividends: the Turkish tax on them is the progressive tax on the gain base plus
  the dividends minus the tax on the gain base (a marginal figure). The US
  withholding is credited up to that amount (rule 5).
* Interest: its Turkish tax is the progressive tax on the gain base plus the
  dividends and the interest, minus the tax on the gain base plus the dividends.
  It is reported separately and is not creditable, since no US tax is withheld.
* Rule 6: dividends plus interest above the GVK 86 threshold must be declared.
"""

from __future__ import annotations

import csv
import io
import json
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

from parayabuck.monitoring.operational_event import mask_account_number
from parayabuck.tax.dividends import (
    NOT_IMPORTED_INCOME_TYPES,
    OTHER_TYPES_NOTE,
    IncomeRecord,
    coverage_spans_year,
    income_records,
    load_activities,
)
from parayabuck.tax.errors import LedgerError, MissingIndexError, MissingRateError, MissingTableError, TaxDataError
from parayabuck.tax.ledger import TURKEY_TIME, LotDisposal, build_disposals, read_fills, unobserved_fee_symbols
from parayabuck.tax.rates import DEFAULT_USDTRY_PATH, DEFAULT_YIUFE_PATH, RateBook, load_rate_book
from parayabuck.tax.rules import US_TREATY_WITHHOLDING_CAP, declaration_threshold_tl, progressive_tax
from parayabuck.utils.io import atomic_write

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
    interest_tax_estimate_tl: float
    foreign_tax_credit_tl: float
    dividend_tax_due_tl: float
    notes: tuple[str, ...]
    income_coverage_complete: bool

    @property
    def income_import_status(self) -> str:
        """``complete`` when one import spans the year; ``stale`` when rows exist but no
        import reaches 31 December; ``missing`` when nothing covers the year (F2-7-7)."""
        if self.income_coverage_complete:
            return "complete"
        return "stale" if self.income else "missing"

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
            "interest_tax_estimate_tl": _r(self.interest_tax_estimate_tl),
            "foreign_tax_credit_tl": _r(self.foreign_tax_credit_tl),
            "dividend_tax_due_tl": _r(self.dividend_tax_due_tl),
            "income_coverage_complete": self.income_coverage_complete,
            "income_import_status": self.income_import_status,
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
    disposals = tuple(build_disposals(connection, account_id, rates, year=year))
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
    # Dividends are stacked on the gain base first; interest sits on top of them. The
    # US withholding can only be credited against the dividend part (rule 5).
    dividend_base = gain_base + max(dividend_gross_tl, 0.0)
    dividend_tax = max(progressive_tax(dividend_base, year) - estimated_tax, 0.0)
    interest_tax = max(
        progressive_tax(dividend_base + max(interest_tl, 0.0), year) - progressive_tax(dividend_base, year), 0.0
    )
    credit = min(max(withholding_tl, 0.0), dividend_tax)
    declaration_required = income_total_tl > threshold
    dividend_tax_due = dividend_tax - credit
    coverage_complete = coverage_spans_year(connection, account_id, year)

    notes: list[str] = []
    if not income:
        notes.append(f"no dividend or interest activities stored for {year}; run `tax import-activities`")
    if not coverage_complete:
        notes.append(
            f"{year} yılı için temettü ve faiz içe aktarma kapsamı yılı tamamen kapsamıyor; yıl sonundan sonra "
            f"`tax import-activities --since {year}-01-01` komutunu çalıştırın. Rakamlar eksik olabilir."
        )
    if dividend_gross_usd > 0 and withholding_usd > dividend_gross_usd * US_TREATY_WITHHOLDING_CAP + 1e-6:
        excess_usd = withholding_usd - dividend_gross_usd * US_TREATY_WITHHOLDING_CAP
        notes.append(
            f"US stopajı brüt temettünün %20 anlaşma tavanını (treaty cap) {excess_usd:,.2f} USD aşıyor. "
            "Tavanın üstündeki kısım da temettü vergisi sınırına kadar mahsup tutarına dahil edildi; "
            "yasal dayanağı doğrulanmadı. W-8BEN kaydını aracı kurumdan kontrol edin."
        )
    if withholding_usd > 0 and dividend_gross_usd <= 0:
        notes.append(
            "stopaj kaydı var ama brüt temettü içe aktarılmamış; net temettü negatif görünür. "
            "Eksik temettü kayıtlarını kontrol edin."
        )
    if dividend_tax_due > 0 and not declaration_required:
        notes.append(
            "temettü ve faiz toplamı beyan eşiğinin altında; beyan zorunluluğu görünmüyor. "
            "dividend_tax_due_tl yine de ihtiyatlı olarak gösteriliyor."
        )
    fee_gaps = unobserved_fee_symbols(disposals, read_fills(connection, account_id)) if disposals else []
    if fee_gaps:
        notes.append(
            f"{', '.join(fee_gaps)}: bazı dolumların broker komisyonu gözlenmedi ve 0 sayıldı; "
            "kazanç ve vergi bu yüzden olduğundan biraz yüksek (ihtiyatlı) olabilir."
        )
    if net_gain < 0:
        notes.append("net loss for the year; not carried forward and no tax is due on it (rule 3)")
    if not disposals:
        notes.append(f"no disposals in {year}")
    notes.append(
        f"income types {', '.join(NOT_IMPORTED_INCOME_TYPES)} (capital-gain distributions, return of capital) "
        "are not imported; any such income is missing from these figures"
    )
    notes.append(OTHER_TYPES_NOTE)

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
        income_declaration_required=declaration_required,
        dividend_tax_estimate_tl=dividend_tax,
        interest_tax_estimate_tl=interest_tax,
        foreign_tax_credit_tl=credit,
        dividend_tax_due_tl=dividend_tax_due,
        notes=tuple(notes),
        income_coverage_complete=coverage_complete,
    )


# Text that a spreadsheet reads as a formula when the CSV is opened (F2-7-9).
_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def _cell(value: Any) -> Any:
    if isinstance(value, bool) or value is None:
        return "" if value is None else str(value).lower()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, float):
        return round(value, 6)
    if isinstance(value, str) and value.startswith(_FORMULA_PREFIXES):
        # Broker-supplied ids and symbols must not run as formulas: a leading apostrophe
        # keeps the cell text.
        return "'" + value
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
    atomic_write(out, buffer.getvalue())
    atomic_write(summary_path, json.dumps(report.to_dict(), ensure_ascii=False, indent=2) + "\n")
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

    ``status`` is one of ``ok``, ``rates_missing`` (rate or index file missing,
    unreadable or short), ``tables_missing`` (no bracket table for the year),
    ``ledger_error``, ``unavailable`` (no database), or ``error`` (a bad value).
    An ``ok`` status with ``income_row_count`` 0 means the dividend and interest
    activities were never imported; ``income_declaration_status`` then reads
    ``veri eksik`` (data missing) instead of a threshold verdict.
    """
    # The account id is masked on every status: the dashboard and the daily summary show it (S2-2-1).
    base: dict[str, Any] = {"year": year, "account_id": mask_account_number(account_id)}
    if connection is None:
        return {**base, "status": "unavailable", "error": "ledger database not found"}
    if not account_id or account_id == "unknown":
        # The runtime falls back to "unknown" when the broker reports no account id; zero
        # figures for that placeholder would read as a real, empty account.
        return {**base, "status": "unavailable", "error": "broker account id unknown; tax figures not attributed"}
    try:
        rates = load_rate_book(Path(usdtry_path), Path(yiufe_path))
        report = build_year_report(connection, account_id, year, rates)
    except (MissingRateError, MissingIndexError) as exc:
        return {**base, "status": "rates_missing", "error": str(exc)}
    except MissingTableError as exc:
        return {**base, "status": "tables_missing", "error": str(exc)}
    except LedgerError as exc:
        return {**base, "status": "ledger_error", "error": str(exc)}
    except TaxDataError as exc:
        return {**base, "status": "rates_missing", "error": str(exc)}
    except OSError as exc:
        return {**base, "status": "rates_missing", "error": str(exc)}
    except sqlite3.Error as exc:
        return {**base, "status": "unavailable", "error": str(exc)}
    except (ValueError, TypeError) as exc:
        return {**base, "status": "error", "error": str(exc)}
    if not report.income or not report.income_coverage_complete:
        income_status = "veri eksik"
    elif report.income_declaration_required:
        income_status = "aşıldı, beyan gerekir"
    else:
        income_status = "aşılmadı"
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
        "income_declaration_status": income_status,
        "income_row_count": len(report.income),
        "income_coverage_complete": report.income_coverage_complete,
        "dividend_tax_due_tl": _r(report.dividend_tax_due_tl),
        "interest_tax_estimate_tl": _r(report.interest_tax_estimate_tl),
        "notes": list(report.notes),
    }


__all__ = [
    "COLUMNS",
    "YearReport",
    "build_year_report",
    "export_year",
    "ytd_status",
]
