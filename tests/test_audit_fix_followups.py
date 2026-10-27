"""Orchestrator follow-ups that join two workers' fixes (audit 2026-10-27)."""

from __future__ import annotations

from parayabuck.monitoring.daily_summary import format_tax_ytd


def test_tax_line_names_missing_tables() -> None:
    assert format_tax_ytd({"status": "tables_missing"}) == "tax: ledger tables missing"


def test_tax_line_flags_missing_income_rows() -> None:
    line = format_tax_ytd(
        {
            "status": "ok",
            "year": 2026,
            "net_gain_tl": 100.0,
            "estimated_tax_tl": 15.0,
            "income_declaration_status": "veri eksik",
        }
    )
    assert line.endswith(", income data missing")


def test_tax_line_without_income_flag_is_unchanged() -> None:
    line = format_tax_ytd({"status": "ok", "year": 2026, "net_gain_tl": 100.0, "estimated_tax_tl": 15.0})
    assert line == "tax YTD 2026: net gain +100.00 TL, est. tax 15.00 TL"
