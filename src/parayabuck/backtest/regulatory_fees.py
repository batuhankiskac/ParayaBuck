"""Effective-dated US equity regulatory fee schedule for backtests.

Rates verified against primary sources as of 2026-08:

* FINRA TAF (per share sold, per-trade cap): SR-FINRA-2020-032 (the phased
  2022-2024 schedule, effective January 1 each year) and the published
  five-year fee schedule (SR-FINRA-2024-019): $0.000119/$5.95 through 2021
  (rate unchanged since 2013), $0.000130/$6.49 from 2022-01-01,
  $0.000145/$7.27 from 2023-01-01, $0.000166/$8.30 from 2024-01-01 (2025
  unchanged), $0.000195/$9.79 from 2026-01-01.
* SEC Section 31 (per $1 of sell proceeds): SEC fee-rate advisories and
  FINRA Information Notices — $20.70/$1M before 2020-02-18 (FY2019 rate),
  $22.10/$1M from 2020-02-18 (FY2020), $5.10/$1M from 2021-02-25 (FY2021),
  $22.90/$1M from 2022-05-14 (FY2022; unchanged for FY2023 from 2023-02-27),
  $8.00/$1M from 2024-02-16 (FY2024), $27.80/$1M from 2024-05-22 (FY2024
  adjustment), $0.00/$1M from 2025-05-14 (FY2025), $20.60/$1M from
  2026-04-04 (FY2026).
* CAT NMS Plan (per executed equivalent share), keyed by TRANSACTION month
  (never by invoice month): CAT Fee Alert 2024-1 — first invoice Oct 2024
  for Sep 2024 transactions, $0.000035; CAT Fee Alert 2024-2 (Historical
  Assessment 1) — first invoice Nov 2024 for Oct 2024 transactions,
  $0.000013; CAT Fee 2025-1 — $0.000022 for Jan through Jun 2025
  transactions (first invoice Feb 2025); CAT Fee 2025-2 — $0.000009 for Jul
  through Nov 2025 transactions (first invoice Aug 2025); CAT Fee Alert
  2025-4 — both 2025 fees cease after the Dec 2025 invoice, which covered
  Nov 2025 transactions; the 2026 funding model — CAT Fee 2026-1 ($0.000001)
  + Historical Assessment 1A ($0.000002), first invoice Jun 2026 for May
  2026 transactions. CAT invoicing began in 2024, so earlier years carry
  zero CAT.

The whole schedule is an approximation — the backtest report says so
explicitly and never claims broker-exact historical fees (Alpaca/FINRA/SEC/
CAT rounding and billing details can still differ).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

# FINRA TAF equity rate: per share sold, capped per trade. Effective dates
# are the January 1 implementation dates from SR-FINRA-2020-032 (2022-2024
# phase-in), the five-year schedule SR-FINRA-2024-019 (2025 unchanged) and
# the 2026 adjustment. The pre-2022 entry is the rate in effect since 2013.
_TAF_SCHEDULE: tuple[tuple[date, float, float], ...] = (
    (date(2013, 1, 1), 0.000119, 5.95),
    (date(2022, 1, 1), 0.000130, 6.49),
    (date(2023, 1, 1), 0.000145, 7.27),
    (date(2024, 1, 1), 0.000166, 8.30),
    (date(2026, 1, 1), 0.000195, 9.79),
)

# SEC Section 31: rate per $1 of sell proceeds, keyed by effective date.
_SEC_SCHEDULE: tuple[tuple[date, float], ...] = (
    (date(2020, 2, 18), 0.0000221),  # FY2020
    (date(2021, 2, 25), 0.0000051),  # FY2021
    (date(2022, 5, 14), 0.0000229),  # FY2022 (FY2023 unchanged, 2023-02-27)
    (date(2024, 2, 16), 0.0000080),  # FY2024
    (date(2024, 5, 22), 0.0000278),  # FY2024 adjustment
    (date(2025, 5, 14), 0.0000000),  # FY2025
    (date(2026, 4, 4), 0.0000206),  # FY2026
)
# Fixed approximation for dates before the first anchored segment: the FY2019
# rate ($20.70/$1M) in effect before the FY2020 change.
_SEC_PRE_SCHEDULE = 0.0000207

# CAT NMS Plan: per executed equivalent share, keyed by TRANSACTION month.
_CAT_SCHEDULE: tuple[tuple[date, float], ...] = (
    (date(2024, 9, 1), 0.000035),  # CAT Fee 2024-1 (Sep 2024 transactions)
    (date(2024, 10, 1), 0.000048),  # + Historical Assessment 1 (Oct 2024)
    (date(2025, 1, 1), 0.000035),  # CAT Fee 2025-1 replaces 2024-1 (Jan 2025)
    (date(2025, 7, 1), 0.000022),  # CAT Fee 2025-2 replaces 2025-1 (Jul 2025)
    (date(2025, 12, 1), 0.0),  # 2025 fees cease after Nov 2025 transactions
    (date(2026, 5, 1), 0.000003),  # 2026-1 + Historical Assessment 1A (May 2026)
)
_CAT_PRE_SCHEDULE = 0.0


@dataclass(frozen=True)
class RegulatoryFeeRates:
    taf_per_share: float
    taf_cap: float
    cat_per_share: float
    sec_fee_rate: float


def rates_for_trade_date(trade_date: date) -> RegulatoryFeeRates:
    """Effective regulatory rates for one equity trade date."""
    taf, cap = _TAF_SCHEDULE[0][1], _TAF_SCHEDULE[0][2]
    for effective, rate, maximum in _TAF_SCHEDULE:
        if trade_date >= effective:
            taf, cap = rate, maximum
    sec = _SEC_PRE_SCHEDULE
    for effective, rate in _SEC_SCHEDULE:
        if trade_date >= effective:
            sec = rate
    cat = _CAT_PRE_SCHEDULE
    for effective, rate in _CAT_SCHEDULE:
        if trade_date >= effective:
            cat = rate
    return RegulatoryFeeRates(
        taf_per_share=taf,
        taf_cap=cap,
        cat_per_share=cat,
        sec_fee_rate=sec,
    )


def crypto_taker_fee_bps_for_tier(thirty_day_volume_usd: float) -> int:
    """Alpaca crypto taker fee in basis points for a 30-day executed volume.

    Verified from Alpaca's published volume-tiered fee schedule. Market orders
    always pay the taker rate; a low-volume account sits in tier 1 (25 bps).
    """
    if thirty_day_volume_usd < 100_000:
        return 25
    if thirty_day_volume_usd < 500_000:
        return 22
    if thirty_day_volume_usd < 1_000_000:
        return 20
    if thirty_day_volume_usd < 10_000_000:
        return 18
    if thirty_day_volume_usd < 25_000_000:
        return 15
    if thirty_day_volume_usd < 50_000_000:
        return 13
    if thirty_day_volume_usd < 100_000_000:
        return 12
    return 10


__all__ = [
    "RegulatoryFeeRates",
    "crypto_taker_fee_bps_for_tier",
    "rates_for_trade_date",
]
