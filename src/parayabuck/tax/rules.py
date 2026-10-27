"""Turkish tax constants used by the TL ledger.

Every value here is taken from the decision record ``docs/tax/turkish_tax_rules.md``;
change that record first. This module is arithmetic on the record's rules, not tax advice.
"""

from __future__ import annotations

from parayabuck.tax.errors import TaxDataError

# Rule 1-2: Yİ-ÜFE indexation applies when the index rise is at least 10%.
INDEXATION_MIN_RISE = 0.10

# Rule 5: US treaty withholding cap for an individual (Alpaca with W-8BEN).
US_TREATY_WITHHOLDING_CAP = 0.20

# Rule 6: non-wage brackets as (upper bound in TL or None, rate). The last
# bound is open-ended.
_BRACKETS_BY_YEAR: dict[int, tuple[tuple[float | None, float], ...]] = {
    2026: (
        (190_000.0, 0.15),
        (400_000.0, 0.20),
        (1_000_000.0, 0.27),
        (5_300_000.0, 0.35),
        (None, 0.40),
    ),
}

# Rule 6: GVK 86 limit for dividends and interest not withheld in Türkiye.
_DECLARATION_THRESHOLD_BY_YEAR: dict[int, float] = {2026: 22_000.0}


def progressive_tax(base_tl: float, year: int) -> float:
    """Progressive income tax on ``base_tl`` with the year's bracket table.

    A non-positive base has no tax; a net loss is never carried forward.
    """
    if year not in _BRACKETS_BY_YEAR:
        raise TaxDataError(f"no bracket table for tax year {year}; add it to docs/tax/turkish_tax_rules.md first")
    if base_tl <= 0:
        return 0.0
    tax = 0.0
    lower = 0.0
    for upper, rate in _BRACKETS_BY_YEAR[year]:
        top = base_tl if upper is None else min(base_tl, upper)
        if top > lower:
            tax += (top - lower) * rate
        if upper is None or base_tl <= upper:
            break
        lower = upper
    return tax


def declaration_threshold_tl(year: int) -> float:
    """GVK 86 threshold above which dividends and interest must be declared."""
    if year not in _DECLARATION_THRESHOLD_BY_YEAR:
        raise TaxDataError(f"no declaration threshold for tax year {year}; add it to the decision record first")
    return _DECLARATION_THRESHOLD_BY_YEAR[year]


__all__ = [
    "INDEXATION_MIN_RISE",
    "US_TREATY_WITHHOLDING_CAP",
    "declaration_threshold_tl",
    "progressive_tax",
]
