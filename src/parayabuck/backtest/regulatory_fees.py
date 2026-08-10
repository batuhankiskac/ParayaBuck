"""Effective-dated US equity regulatory fee schedule for backtests.

Rates verified against primary sources as of 2026-08:

* SEC Section 31 (per $1 of sell proceeds): SEC fee-rate advisories and
  Federal Register orders — FY2022 order effective 2022-05-14 ($22.90/$1M),
  FY2023 order effective 2023-02-27 ($22.90/$1M), FY2024 order effective
  2024-02-16 ($8.00/$1M), May 2024 adjustment effective 2024-05-22
  ($27.80/$1M), FY2025 advisory effective 2025-05-14 ($0.00/$1M), FY2026
  order effective 2026-04-04 ($20.60/$1M).
* FINRA TAF (per share sold, per-trade cap): FINRA Schedule A notification —
  effective 2026-01-01 the equity rate moved from $0.000166 (cap $8.30) to
  $0.000195 (cap $9.79).
* CAT NMS Plan (per executed equivalent share): CAT fee alerts — CAT Fee
  2024-1 ($0.000035) + Historical Assessment 1 ($0.000013) from 2024; CAT Fee
  2025-1 ($0.000022) from the Feb 2025 invoice; CAT Fee 2025-2 ($0.000009)
  from the Aug 2025 invoice; CAT Fee 2026-1 ($0.000001, prospective) +
  Historical Assessment 1A ($0.000002) proposed for 2026.

Rates before 2022-05-14 use a fixed approximation ($22.10/$1M, the FY2020-22
rate); CAT invoicing began in 2024, so earlier years carry zero CAT. The
whole schedule is an approximation — the backtest report says so explicitly
and never claims exact historical fees.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

# FINRA TAF equity rate: per share sold, capped per trade.
_TAF_PRE_2026 = (0.000166, 8.30)
_TAF_2026 = (0.000195, 9.79)

# SEC Section 31: rate per $1 of sell proceeds, keyed by effective date.
_SEC_SCHEDULE: tuple[tuple[date, float], ...] = (
    (date(2022, 5, 14), 0.0000229),
    (date(2024, 2, 16), 0.0000080),
    (date(2024, 5, 22), 0.0000278),
    (date(2025, 5, 14), 0.0000000),
    (date(2026, 4, 4), 0.0000206),
)
# Fixed approximation for dates before the first anchored segment.
_SEC_PRE_SCHEDULE = 0.0000221

# CAT NMS Plan: per executed equivalent share, keyed by invoice month basis.
_CAT_SCHEDULE: tuple[tuple[date, float], ...] = (
    (date(2024, 1, 1), 0.000048),  # 2024-1 (0.000035) + Hist 1 (0.000013)
    (date(2025, 2, 1), 0.000035),  # 2025-1 (0.000022) + Hist 1 (0.000013)
    (date(2025, 8, 1), 0.000022),  # 2025-2 (0.000009) + Hist 1 (0.000013)
    (date(2026, 1, 1), 0.000003),  # 2026-1 (0.000001) + Hist 1A (0.000002)
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
    if trade_date < date(2026, 1, 1):
        taf, cap = _TAF_PRE_2026
    else:
        taf, cap = _TAF_2026
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
