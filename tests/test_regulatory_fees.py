from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from parayabuck.backtest.portfolio_engine import (
    OrderSide,
    PortfolioBacktestEngine,
    equity_regulatory_fee,
)
from parayabuck.backtest.regulatory_fees import (
    crypto_taker_fee_bps_for_tier,
    rates_for_trade_date,
)
from parayabuck.core.models import Bar, Market
from parayabuck.portfolio.base import PortfolioContext, PortfolioStrategy
from parayabuck.portfolio.models import RebalancePolicy, TargetPortfolio, TargetWeight


def test_rates_for_trade_date_follows_official_anchors() -> None:
    # 2023: TAF $0.000145/$7.27 (phased schedule, SR-FINRA-2020-032),
    # SEC $22.90/$1M (FY2023), no CAT invoicing yet.
    r2023 = rates_for_trade_date(date(2023, 6, 1))
    assert r2023.taf_per_share == pytest.approx(0.000145)
    assert r2023.taf_cap == pytest.approx(7.27)
    assert r2023.cat_per_share == 0.0
    assert r2023.sec_fee_rate == pytest.approx(0.0000229)
    # Mid-2024: SEC $27.80/$1M (from 2024-05-22). CAT invoicing begins with
    # Sep 2024 transactions, so June 2024 trades still pay zero CAT.
    r2024 = rates_for_trade_date(date(2024, 6, 1))
    assert r2024.sec_fee_rate == pytest.approx(0.0000278)
    assert r2024.cat_per_share == 0.0
    # Late 2025: SEC $0.00 (from 2025-05-14), CAT 2025-2 + Hist 1.
    r2025 = rates_for_trade_date(date(2025, 9, 1))
    assert r2025.sec_fee_rate == 0.0
    assert r2025.cat_per_share == pytest.approx(0.000022)
    # 2026: TAF $0.000195/cap 9.79, SEC $20.60/$1M from 2026-04-04,
    # CAT suspended Dec 2025-Apr 2026, CAT 2026-1 + Hist 1A from May 2026
    # transactions (first invoice Jun 2026).
    r2026a = rates_for_trade_date(date(2026, 3, 1))
    assert r2026a.taf_per_share == pytest.approx(0.000195)
    assert r2026a.taf_cap == pytest.approx(9.79)
    assert r2026a.sec_fee_rate == 0.0  # $0.00 rate still in force
    assert r2026a.cat_per_share == 0.0  # suspended period
    r2026b = rates_for_trade_date(date(2026, 8, 1))
    assert r2026b.sec_fee_rate == pytest.approx(0.0000206)
    assert r2026b.cat_per_share == pytest.approx(0.000003)


def test_finra_taf_annual_boundaries() -> None:
    """One day before/on/after every FINRA TAF effective date."""
    assert rates_for_trade_date(date(2021, 12, 31)).taf_per_share == pytest.approx(0.000119)
    assert rates_for_trade_date(date(2021, 12, 31)).taf_cap == pytest.approx(5.95)
    assert rates_for_trade_date(date(2022, 1, 1)).taf_per_share == pytest.approx(0.000130)
    assert rates_for_trade_date(date(2022, 1, 1)).taf_cap == pytest.approx(6.49)
    assert rates_for_trade_date(date(2023, 1, 1)).taf_per_share == pytest.approx(0.000145)
    assert rates_for_trade_date(date(2023, 1, 1)).taf_cap == pytest.approx(7.27)
    assert rates_for_trade_date(date(2024, 1, 1)).taf_per_share == pytest.approx(0.000166)
    assert rates_for_trade_date(date(2024, 1, 1)).taf_cap == pytest.approx(8.30)
    # 2025: no change from 2024.
    assert rates_for_trade_date(date(2025, 7, 1)).taf_per_share == pytest.approx(0.000166)
    assert rates_for_trade_date(date(2025, 7, 1)).taf_cap == pytest.approx(8.30)
    assert rates_for_trade_date(date(2026, 1, 1)).taf_per_share == pytest.approx(0.000195)
    assert rates_for_trade_date(date(2026, 1, 1)).taf_cap == pytest.approx(9.79)


def test_sec_section31_boundaries() -> None:
    """One day before/on/after every SEC Section 31 effective date."""
    assert rates_for_trade_date(date(2020, 2, 17)).sec_fee_rate == pytest.approx(0.0000207)
    assert rates_for_trade_date(date(2020, 2, 18)).sec_fee_rate == pytest.approx(0.0000221)
    assert rates_for_trade_date(date(2021, 2, 24)).sec_fee_rate == pytest.approx(0.0000221)
    assert rates_for_trade_date(date(2021, 2, 25)).sec_fee_rate == pytest.approx(0.0000051)
    assert rates_for_trade_date(date(2022, 5, 13)).sec_fee_rate == pytest.approx(0.0000051)
    assert rates_for_trade_date(date(2022, 5, 14)).sec_fee_rate == pytest.approx(0.0000229)
    # FY2023 keeps $22.90/$1M from 2023-02-27 (no schedule change).
    assert rates_for_trade_date(date(2023, 2, 27)).sec_fee_rate == pytest.approx(0.0000229)
    assert rates_for_trade_date(date(2024, 2, 15)).sec_fee_rate == pytest.approx(0.0000229)
    assert rates_for_trade_date(date(2024, 2, 16)).sec_fee_rate == pytest.approx(0.0000080)
    assert rates_for_trade_date(date(2024, 5, 21)).sec_fee_rate == pytest.approx(0.0000080)
    assert rates_for_trade_date(date(2024, 5, 22)).sec_fee_rate == pytest.approx(0.0000278)
    assert rates_for_trade_date(date(2025, 5, 13)).sec_fee_rate == pytest.approx(0.0000278)
    assert rates_for_trade_date(date(2025, 5, 14)).sec_fee_rate == 0.0
    assert rates_for_trade_date(date(2026, 4, 3)).sec_fee_rate == 0.0
    assert rates_for_trade_date(date(2026, 4, 4)).sec_fee_rate == pytest.approx(0.0000206)


def test_cat_transaction_month_boundaries() -> None:
    """CAT fees key by TRANSACTION month; invoice month must never leak in."""
    # 2024-1 starts with Sep 2024 transactions (first invoice Oct 2024).
    assert rates_for_trade_date(date(2024, 8, 31)).cat_per_share == 0.0
    assert rates_for_trade_date(date(2024, 9, 1)).cat_per_share == pytest.approx(0.000035)
    assert rates_for_trade_date(date(2024, 9, 30)).cat_per_share == pytest.approx(0.000035)
    # Historical Assessment 1 starts with Oct 2024 transactions (first
    # invoice Nov 2024): invoice month (Oct) != transaction month.
    assert rates_for_trade_date(date(2024, 10, 1)).cat_per_share == pytest.approx(0.000048)
    assert rates_for_trade_date(date(2024, 12, 31)).cat_per_share == pytest.approx(0.000048)
    # CAT Fee 2025-1 applies to Jan-Jun 2025 transactions (invoice Feb 2025).
    assert rates_for_trade_date(date(2025, 1, 1)).cat_per_share == pytest.approx(0.000035)
    assert rates_for_trade_date(date(2025, 6, 30)).cat_per_share == pytest.approx(0.000035)
    # CAT Fee 2025-2 applies to Jul-Nov 2025 transactions (invoice Aug 2025).
    assert rates_for_trade_date(date(2025, 7, 1)).cat_per_share == pytest.approx(0.000022)
    assert rates_for_trade_date(date(2025, 11, 30)).cat_per_share == pytest.approx(0.000022)
    # Both 2025 fees cease after the Dec 2025 invoice (Nov 2025 transactions):
    # December 2025 transactions onward pay no CAT.
    assert rates_for_trade_date(date(2025, 12, 1)).cat_per_share == 0.0
    assert rates_for_trade_date(date(2026, 4, 30)).cat_per_share == 0.0
    # 2026-1 + Historical Assessment 1A apply to May 2026 transactions (the
    # first payable invoice under the 2026 funding model was issued in June
    # 2026, based on May 2026 transactions).
    assert rates_for_trade_date(date(2026, 5, 1)).cat_per_share == pytest.approx(0.000003)
    assert rates_for_trade_date(date(2026, 5, 31)).cat_per_share == pytest.approx(0.000003)


def test_equity_regulatory_fee_uses_2026_rates() -> None:
    sell = equity_regulatory_fee(
        OrderSide.SELL,
        1000,
        100,
        taf_per_share=0.000195,
        taf_cap=9.79,
        cat_per_share=0.000003,
        sec_fee_rate=0.0000206,
    )
    assert sell == pytest.approx(0.195 + 0.003 + 1000 * 100 * 0.0000206)
    buy = equity_regulatory_fee(
        OrderSide.BUY,
        1000,
        100,
        taf_per_share=0.000195,
        taf_cap=9.79,
        cat_per_share=0.000003,
        sec_fee_rate=0.0000206,
    )
    # TAF and SEC apply only to sells; CAT applies to both sides.
    assert buy == pytest.approx(0.003)


def test_crypto_taker_fee_uses_market_order_tier() -> None:
    # A low-volume account pays tier-1 taker: 25 bps.
    assert crypto_taker_fee_bps_for_tier(50_000) == 25
    assert crypto_taker_fee_bps_for_tier(250_000) == 22
    assert crypto_taker_fee_bps_for_tier(9_000_000) == 18
    assert crypto_taker_fee_bps_for_tier(150_000_000) == 10


class _FixedTargetStrategy(PortfolioStrategy):
    name = "fixed_target"
    version = "1.0.0"
    market = Market.EQUITY
    timeframe = "1d"
    minimum_history_bars = 1

    def __init__(self) -> None:
        super().__init__()

    def _validate_parameters(self) -> None:
        return None

    def decide(self, context: PortfolioContext) -> TargetPortfolio:
        return TargetPortfolio(
            strategy_name=self.name,
            strategy_version=self.version,
            decision_timestamp=context.decision_timestamp,
            weights=(TargetWeight("SPY", 0.5),),
            cash_weight=0.5,
            universe_version="test",
        )


def _bar(timestamp: datetime, close: float) -> Bar:
    return Bar(
        timestamp,
        "SPY",
        "1d",
        close * 0.999,
        close * 1.001,
        close * 0.998,
        close,
        5_000_000,
        Market.EQUITY,
    )


def test_effective_dated_fees_apply_per_trade_date() -> None:
    """A 2025 fill pays zero SEC Section 31; a 2026 fill pays 20.60/$1M."""
    epoch = datetime(2025, 6, 2, tzinfo=UTC)
    bars = [
        _bar(epoch + timedelta(days=0), 100.0),
        _bar(epoch + timedelta(days=1), 100.0),
    ]
    engine = PortfolioBacktestEngine(
        _FixedTargetStrategy(),
        RebalancePolicy(Market.EQUITY, "monthly"),
        starting_equity=100_000.0,
        slippage_bps=2.0,
        spread_bps=4.0,
        benchmark_symbol="SPY",
        max_participation_pct=0.10,
        turnover_band=0.005,
        minimum_order_notional=10.0,
        equity_use_effective_dated_fees=True,
    )
    result = engine.run(bars, liquidate_at_end=False)
    buy_fill = result.fills[0]
    # TAF applies only to sells; SEC is $0.00 in June 2025; CAT Fee 2025-1
    # (0.000022) + Historical Assessment 1 (0.000013) = 0.000035/share.
    assert buy_fill.fee == pytest.approx(buy_fill.qty * 0.000035, abs=1e-6)
