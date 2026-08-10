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
    # 2023: TAF pre-2026, SEC $22.90/$1M (FY2023), no CAT invoicing yet.
    r2023 = rates_for_trade_date(date(2023, 6, 1))
    assert r2023.taf_per_share == pytest.approx(0.000166)
    assert r2023.taf_cap == pytest.approx(8.30)
    assert r2023.cat_per_share == 0.0
    assert r2023.sec_fee_rate == pytest.approx(0.0000229)
    # Mid-2024: SEC $27.80/$1M (from 2024-05-22), CAT 2024-1 + Hist 1.
    r2024 = rates_for_trade_date(date(2024, 6, 1))
    assert r2024.sec_fee_rate == pytest.approx(0.0000278)
    assert r2024.cat_per_share == pytest.approx(0.000048)
    # Late 2025: SEC $0.00 (from 2025-05-14), CAT 2025-2 + Hist 1.
    r2025 = rates_for_trade_date(date(2025, 9, 1))
    assert r2025.sec_fee_rate == 0.0
    assert r2025.cat_per_share == pytest.approx(0.000022)
    # 2026: TAF $0.000195/cap 9.79, SEC $20.60/$1M from 2026-04-04,
    # CAT 2026-1 + Historical Assessment 1A.
    r2026a = rates_for_trade_date(date(2026, 3, 1))
    assert r2026a.taf_per_share == pytest.approx(0.000195)
    assert r2026a.taf_cap == pytest.approx(9.79)
    assert r2026a.sec_fee_rate == 0.0  # $0.00 rate still in force
    assert r2026a.cat_per_share == pytest.approx(0.000003)
    r2026b = rates_for_trade_date(date(2026, 8, 1))
    assert r2026b.sec_fee_rate == pytest.approx(0.0000206)
    assert r2026b.cat_per_share == pytest.approx(0.000003)


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
