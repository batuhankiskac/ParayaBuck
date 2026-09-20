from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from parayabuck.backtest.portfolio_engine import (
    OrderSide,
    PortfolioBacktestEngine,
    _prepare_corporate_actions,
)
from parayabuck.core.models import Bar, Market
from parayabuck.data.market_dataset import CorporateAction, CorporateActionType
from parayabuck.portfolio.base import PortfolioContext, PortfolioStrategy
from parayabuck.portfolio.models import RebalancePolicy, TargetPortfolio, TargetWeight


class _FixedTargetStrategy(PortfolioStrategy):
    name = "regulatory_fee_multiplier_fixture"
    version = "1.0.0"
    market = Market.EQUITY
    timeframe = "1d"
    minimum_history_bars = 1

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


def _engine(
    multiplier: float,
    *,
    effective_dated: bool,
) -> PortfolioBacktestEngine:
    return PortfolioBacktestEngine(
        _FixedTargetStrategy(),
        RebalancePolicy(Market.EQUITY, "monthly"),
        starting_equity=100_000.0,
        slippage_bps=0.0,
        spread_bps=0.0,
        commission=0.0,
        equity_taf_per_share=0.01,
        equity_taf_cap=2.0,
        equity_cat_per_share=0.001,
        equity_sec_fee_rate=0.0001,
        equity_use_effective_dated_fees=effective_dated,
        equity_regulatory_fee_multiplier=multiplier,
        benchmark_symbol="SPY",
    )


@pytest.mark.parametrize("multiplier", (1.0, 2.0, 3.0))
def test_fixed_regulatory_fee_multiplier_scales_buy_sell_and_taf_cap(multiplier: float) -> None:
    engine = _engine(multiplier, effective_dated=False)

    assert engine._equity_fee(OrderSide.BUY, 1_000, 100.0, date(2025, 1, 2)) == pytest.approx(1.0 * multiplier)
    # CAT $1 + capped TAF $2 + SEC $10.
    assert engine._equity_fee(OrderSide.SELL, 1_000, 100.0, date(2025, 1, 2)) == pytest.approx(13.0 * multiplier)


@pytest.mark.parametrize(
    ("trade_date", "side", "unscaled_fee"),
    (
        (date(2024, 5, 21), OrderSide.SELL, 0.166 + 0.80),
        (date(2024, 5, 22), OrderSide.SELL, 0.166 + 2.78),
        (date(2024, 8, 31), OrderSide.BUY, 0.0),
        (date(2024, 9, 1), OrderSide.BUY, 0.035),
        (date(2025, 11, 30), OrderSide.BUY, 0.022),
        (date(2025, 12, 1), OrderSide.BUY, 0.0),
        (date(2026, 4, 3), OrderSide.SELL, 0.195),
        (date(2026, 4, 4), OrderSide.SELL, 0.195 + 2.06),
    ),
)
def test_effective_dated_multiplier_preserves_representative_boundaries(
    trade_date: date,
    side: OrderSide,
    unscaled_fee: float,
) -> None:
    engine = _engine(3.0, effective_dated=True)

    assert engine._equity_fee(side, 1_000, 100.0, trade_date) == pytest.approx(unscaled_fee * 3.0)


def _bar(timestamp: datetime) -> Bar:
    return Bar(timestamp, "SPY", "1d", 100.0, 101.0, 99.0, 100.0, 1_000_000.0, Market.EQUITY)


@pytest.mark.parametrize("multiplier", (1.0, 2.0, 3.0))
def test_multiplier_applies_to_ordinary_and_terminal_fill_fees(multiplier: float) -> None:
    epoch = datetime(2024, 9, 3, tzinfo=UTC)
    engine = _engine(multiplier, effective_dated=True)

    result = engine.run([_bar(epoch), _bar(epoch + timedelta(days=1))], liquidate_at_end=True)

    assert len(result.fills) == 1
    assert len(result.terminal_events) == 1
    fill = result.fills[0]
    terminal = result.terminal_events[0]
    expected_buy = fill.qty * 0.000035 * multiplier
    expected_sell = (fill.qty * 0.000035 + min(fill.qty * 0.000166, 8.30) + fill.qty * 100.0 * 0.0000278) * multiplier
    assert fill.fee == pytest.approx(expected_buy)
    assert terminal["fee"] == pytest.approx(expected_sell)
    assert result.fee_total == pytest.approx(expected_buy + expected_sell)


def test_multiplier_does_not_change_gross_fill_geometry() -> None:
    epoch = datetime(2024, 9, 3, tzinfo=UTC)
    runs = [
        _engine(multiplier, effective_dated=True).run(
            [_bar(epoch), _bar(epoch + timedelta(days=1))],
            liquidate_at_end=True,
        )
        for multiplier in (1.0, 2.0, 3.0)
    ]

    gross_fills = [[(fill.qty, fill.reference_price, fill.fill_price) for fill in result.fills] for result in runs]
    terminal_geometry = [
        [(event["quantity"], event["last_close"], event["fill_price"]) for event in result.terminal_events]
        for result in runs
    ]
    assert gross_fills[0] == gross_fills[1] == gross_fills[2]
    assert terminal_geometry[0] == terminal_geometry[1] == terminal_geometry[2]
    assert [result.fee_total for result in runs] == pytest.approx(
        [runs[0].fee_total, runs[0].fee_total * 2.0, runs[0].fee_total * 3.0]
    )


@pytest.mark.parametrize("multiplier", (0.0, -1.0, float("nan"), float("inf")))
def test_regulatory_fee_multiplier_must_be_finite_and_positive(multiplier: float) -> None:
    with pytest.raises(ValueError, match="finite and positive"):
        _engine(multiplier, effective_dated=True)


def test_corporate_actions_are_economically_deduplicated_and_watermark_filtered() -> None:
    before = datetime(2025, 1, 2, tzinfo=UTC)
    after = datetime(2025, 1, 3, tzinfo=UTC)
    stale = CorporateAction(before, "brk.b", CorporateActionType.DIVIDEND, 1.0)
    first = CorporateAction(after, "BRK.B", CorporateActionType.DIVIDEND, 2.0)
    duplicate_alias = CorporateAction(after, "brk.b", CorporateActionType.DIVIDEND, 2.0)
    distinct_value = CorporateAction(after, "BRK.B", CorporateActionType.DIVIDEND, 3.0)

    prepared = _prepare_corporate_actions(
        [distinct_value, stale, first, duplicate_alias],
        cutoff=before,
    )

    assert prepared == [distinct_value, first]
