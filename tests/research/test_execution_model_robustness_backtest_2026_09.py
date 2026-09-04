from __future__ import annotations

from datetime import UTC, datetime, timedelta

from parayabuck.backtest.portfolio_engine import PortfolioBacktestEngine
from parayabuck.core.models import Bar, Market
from parayabuck.portfolio.base import PortfolioContext, PortfolioStrategy
from parayabuck.portfolio.models import RebalancePolicy, TargetPortfolio, TargetWeight
from scripts.research.execution_model_robustness_backtest_2026_09 import ExecutionStressEngine, result_components


class _Strategy(PortfolioStrategy):
    name = "execution_robustness_fixture"
    version = "1"
    market = Market.EQUITY
    timeframe = "1d"
    minimum_history_bars = 1

    def _validate_parameters(self) -> None:
        return None

    def decide(self, context: PortfolioContext) -> TargetPortfolio:
        return TargetPortfolio(self.name, self.version, context.decision_timestamp, (TargetWeight("AAA", 0.5),), 0.5)


def _bars(start: int, end: int, *, volume: float = 100.0) -> list[Bar]:
    epoch = datetime(2026, 1, 5, tzinfo=UTC)
    return [
        Bar(epoch + timedelta(days=index), "AAA", "1d", 100.0, 101.0, 99.0, 100.0, volume, Market.EQUITY)
        for index in range(start, end + 1)
    ]


def _engine(cls=ExecutionStressEngine, **kwargs):  # type: ignore[no-untyped-def]
    return cls(
        _Strategy(), RebalancePolicy(Market.EQUITY, "monthly"), starting_equity=100_000.0,
        slippage_bps=0.0, spread_bps=0.0, turnover_band=0.0, minimum_order_notional=0.0,
        max_participation_pct=0.1, liquidity_lookback_bars=20, allow_static_universe=True, **kwargs,
    )


class _Universe:
    def to_dict(self):  # type: ignore[no-untyped-def]
        return {"fixture": True}


def test_c0_adapter_is_exact_base_engine_parity():
    warmup = _bars(-20, -1)
    execution = _bars(0, 4)
    base = _engine(PortfolioBacktestEngine)
    adapter = _engine()
    base_result = base.run(execution, warmup=warmup, liquidate_at_end=False)
    adapter_result = adapter.run(execution, warmup=warmup, liquidate_at_end=False)
    assert result_components(base_result, base, _Universe()) == result_components(adapter_result, adapter, _Universe())


def test_latency_consumes_eligible_bars_including_zero_volume_without_changing_capacity_rule():
    warmup = _bars(-20, -1)
    execution = _bars(0, 3)
    execution[1] = Bar(execution[1].timestamp, "AAA", "1d", 100, 101, 99, 100, 0, Market.EQUITY)
    engine = _engine(additional_eligible_bar_delay=1)
    result = engine.run(execution, warmup=warmup, liquidate_at_end=False)
    assert result.fills[0].timestamp == execution[2].timestamp
    assert result.fills[0].qty == 10.0


def test_frozen_decision_liquidity_never_includes_post_decision_volume():
    warmup = _bars(-20, -1, volume=100.0)
    execution = _bars(0, 3, volume=1_000.0)
    dynamic = _engine()
    frozen = _engine(frozen_decision_liquidity=True)
    dynamic_result = dynamic.run(execution, warmup=warmup, liquidate_at_end=False)
    frozen_result = frozen.run(execution, warmup=warmup, liquidate_at_end=False)
    assert dynamic_result.fills[1].qty > frozen_result.fills[1].qty
    assert frozen_result.fills[0].qty == frozen_result.fills[1].qty


def test_adapter_is_not_imported_by_production_runtime():
    from pathlib import Path

    production = Path("src/parayabuck")
    assert all("execution_model_robustness_backtest_2026_09" not in path.read_text() for path in production.rglob("*.py"))
