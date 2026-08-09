from __future__ import annotations

from parayabuck.core.models import Market
from parayabuck.portfolio.base import PortfolioContext, PortfolioStrategy
from parayabuck.portfolio.models import RebalanceReason, TargetPortfolio, TargetWeight
from parayabuck.portfolio.momentum import simple_moving_average
from parayabuck.portfolio.risk_budgeting import covariance_budget_weights

CRYPTO_CORE_UNIVERSE = ("BTC/USD", "ETH/USD")


class CryptoVolatilityTrend1dV1(PortfolioStrategy):
    """Independent BTC/ETH absolute trend with mandatory volatility scaling."""

    name = "crypto_volatility_trend_1d_v1"
    version = "1.0.0"
    market = Market.CRYPTO
    timeframe = "1d"
    default_rebalance_hours = 168

    @property
    def minimum_history_bars(self) -> int:
        return max(
            self.param_int("trend_window", 200),
            self.param_int("covariance_window", 60) + 1,
        )

    def _validate_parameters(self) -> None:
        if self.param_int("trend_window", 200) < 100:
            raise ValueError("trend_window must be at least 100")
        if self.param_int("covariance_window", 60) < 20:
            raise ValueError("covariance_window must be at least 20")
        if self.param_int("rebalance_hours", 168) != 168:
            raise ValueError("Crypto v1 is preregistered for weekly rebalancing")
        if not 0 < self.param_float("annual_volatility_target", 0.08) <= 0.10:
            raise ValueError("annual_volatility_target must be in (0, 0.10]")
        if not 0 < self.param_float("max_single_weight", 0.05) <= 0.05:
            raise ValueError("max_single_weight must be in (0, 0.05]")
        if self.param_float("max_crypto_weight", 0.10) != 0.10:
            raise ValueError("max_crypto_weight is fixed at 0.10")

    def decide(self, context: PortfolioContext) -> TargetPortfolio:
        trend_window = self.param_int("trend_window", 200)
        closes_by_symbol: dict[str, list[float]] = {}
        for symbol in CRYPTO_CORE_UNIVERSE:
            closes = context.closes(symbol)
            average = simple_moving_average(closes, trend_window)
            if average is not None and closes[-1] > average:
                closes_by_symbol[symbol] = closes
        weights, scale = covariance_budget_weights(
            closes_by_symbol,
            window=self.param_int("covariance_window", 60),
            max_weight=self.param_float("max_single_weight", 0.05),
            annual_volatility_target=self.param_float("annual_volatility_target", 0.08),
            periods_per_year=365,
            allocation_cap=self.param_float("max_crypto_weight", 0.10),
        )
        targets = tuple(
            TargetWeight(symbol, weight, "independent_absolute_trend_vol_target")
            for symbol, weight in sorted(weights.items())
        )
        return TargetPortfolio(
            strategy_name=self.name,
            strategy_version=self.version,
            decision_timestamp=context.decision_timestamp,
            data_timestamp=context.decision_timestamp,
            weights=targets,
            cash_weight=max(0.0, 1.0 - sum(weights.values())),
            rebalance_reason=(RebalanceReason.SCHEDULED if targets else RebalanceReason.RISK_OFF),
            universe_version="crypto_btc_eth_v1",
            universe_snapshot_id="btc_eth_only_v1",
            parameters=self.parameter_tuple(),
            risk_multiplier=scale,
            metadata={"eligible_assets": len(targets), "max_crypto_weight": 0.10},
        )


__all__ = ["CRYPTO_CORE_UNIVERSE", "CryptoVolatilityTrend1dV1"]
