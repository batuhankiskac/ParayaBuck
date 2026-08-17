from __future__ import annotations

import math
from datetime import datetime

from parayabuck.core.models import Market
from parayabuck.portfolio.base import PortfolioContext, PortfolioStrategy
from parayabuck.portfolio.models import (
    RebalanceReason,
    TargetPortfolio,
    TargetWeight,
)
from parayabuck.portfolio.momentum import (
    annualize_period_volatility,
    composite_momentum,
    inverse_volatility_weights,
    momentum_at,
    realized_volatility,
)
from parayabuck.portfolio.universes import CRYPTO_UNIVERSE_VERSION

BARS_PER_YEAR = 365 * 24


def _crypto_momentum(closes: list[float], scheme: str) -> float:
    if scheme == "24h":
        return momentum_at(closes, 24, skip_bars=1)
    if scheme == "7d":
        return momentum_at(closes, 168, skip_bars=1)
    if scheme == "30d":
        return momentum_at(closes, 720, skip_bars=1)
    if scheme == "norm_24_7_30":
        return composite_momentum(closes, (24, 168, 720), skip_bars=1)
    raise ValueError(f"Unsupported momentum scheme: {scheme}")


class CryptoCrossSectionalMomentum1hV1(PortfolioStrategy):
    """1h long-only spot momentum: rank execution-supported pairs by relative
    momentum, hold the strongest names inverse-vol weighted, and go to cash
    when the BTC regime turns negative.

    Universe membership is point-in-time (trailing dollar-volume snapshots);
    volume is never used as an alpha input. Rebalances are limited to 6/12/24h.
    """

    name = "crypto_cross_sectional_momentum_1h_v1"
    version = "1.0.0"
    market = Market.CRYPTO
    timeframe = "1h"
    minimum_history_bars = 730
    default_rebalance_hours = 24
    rebalance_policy_declared = "hours:24"

    def _validate_parameters(self) -> None:
        allowed_schemes = {"24h", "7d", "30d", "norm_24_7_30"}
        scheme = self.param_str("momentum_scheme", "7d")
        if scheme not in allowed_schemes:
            raise ValueError(f"Unsupported crypto momentum scheme: {scheme}")
        rebalance_hours = self.parameters.get("rebalance_hours", 24)
        if not isinstance(rebalance_hours, int) or rebalance_hours not in {6, 12, 24}:
            raise ValueError("rebalance_hours must be one of 6, 12, 24")
        top_n = self.parameters.get("top_n", 3)
        if not isinstance(top_n, int) or top_n not in {2, 3}:
            raise ValueError("top_n must be 2 or 3")
        volatility_window = self.parameters.get("volatility_window", 72)
        if not isinstance(volatility_window, int) or volatility_window < 24:
            raise ValueError("volatility_window must be an integer of at least 24 bars")
        btc_lookback = self.parameters.get("btc_regime_lookback_hours", 168)
        if not isinstance(btc_lookback, int) or btc_lookback <= 0:
            raise ValueError("btc_regime_lookback_hours must be positive")
        max_volatility = self.parameters.get("max_annualized_volatility_pct", 250.0)
        if not isinstance(max_volatility, (int, float)) or float(max_volatility) <= 0:
            raise ValueError("max_annualized_volatility_pct must be positive")
        max_single = self.parameters.get("max_single_weight", 0.5)
        if not isinstance(max_single, (int, float)) or not 0 < float(max_single) <= 1:
            raise ValueError("max_single_weight must be in (0, 1]")

    def decide(self, context: PortfolioContext) -> TargetPortfolio:
        scheme = self.param_str("momentum_scheme", "7d")
        top_n = self.param_int("top_n", 3)
        volatility_window = self.param_int("volatility_window", 72)
        btc_lookback = self.param_int("btc_regime_lookback_hours", 168)
        max_annualized_volatility_pct = self.param_float("max_annualized_volatility_pct", 250.0)
        max_single_weight = self.param_float("max_single_weight", 0.5)

        btc_closes = context.closes("BTC/USD")
        btc_regime = momentum_at(btc_closes, btc_lookback, skip_bars=1)
        if not math.isfinite(btc_regime) or btc_regime <= 0:
            return self._cash_portfolio(context, RebalanceReason.RISK_OFF, "btc_regime_negative")

        scored: list[tuple[float, str]] = []
        volatilities: dict[str, float] = {}
        for symbol in context.history:
            if not context.in_universe(symbol):
                continue
            closes = context.closes(symbol)
            if len(closes) < self.minimum_history_bars:
                # Match the declared contract and every sibling strategy:
                # short-history symbols never qualify, even when the momentum
                # scheme alone has enough bars.
                continue
            score = _crypto_momentum(closes, scheme)
            if not math.isfinite(score) or score <= 0:
                continue
            volatility = annualize_period_volatility(
                realized_volatility(closes, volatility_window), BARS_PER_YEAR
            )
            if volatility > max_annualized_volatility_pct / 100:
                continue
            scored.append((score, symbol))
            volatilities[symbol] = volatility
        scored.sort(key=lambda item: (-item[0], item[1]))
        selected = tuple(symbol for _, symbol in scored[:top_n])
        if not selected:
            return self._cash_portfolio(context, RebalanceReason.SCHEDULED, "no_candidates")

        weights = dict(
            inverse_volatility_weights(selected, volatilities, maximum_weight=max_single_weight)
        )
        if not weights:
            return self._cash_portfolio(context, RebalanceReason.SCHEDULED, "no_weights")
        targets = tuple(
            TargetWeight(symbol, weight, reason="cross_sectional_momentum")
            for symbol, weight in weights.items()
        )
        return TargetPortfolio(
            strategy_name=self.name,
            strategy_version=self.version,
            decision_timestamp=context.decision_timestamp,
            data_timestamp=context.decision_timestamp,
            weights=targets,
            cash_weight=max(0.0, 1.0 - sum(weights.values())),
            rebalance_reason=RebalanceReason.SCHEDULED,
            universe_version=context.universe_version or CRYPTO_UNIVERSE_VERSION,
            universe_snapshot_id=self._snapshot_id(context),
            parameters=self.parameter_tuple(),
            metadata={"btc_regime_return": btc_regime},
        )

    def _cash_portfolio(
        self, context: PortfolioContext, reason: RebalanceReason, detail: str
    ) -> TargetPortfolio:
        return TargetPortfolio(
            strategy_name=self.name,
            strategy_version=self.version,
            decision_timestamp=context.decision_timestamp,
            data_timestamp=context.decision_timestamp,
            weights=(),
            cash_weight=1.0,
            rebalance_reason=reason,
            universe_version=context.universe_version or CRYPTO_UNIVERSE_VERSION,
            universe_snapshot_id=self._snapshot_id(context),
            parameters=self.parameter_tuple(),
            metadata={"risk_off_detail": detail},
        )

    @staticmethod
    def _snapshot_id(context: PortfolioContext) -> str:
        snapshots = getattr(context.universe, "snapshots", ())
        if not isinstance(snapshots, tuple):
            return ""
        candidates: list[tuple[datetime, str]] = []
        for item in snapshots:
            effective_at = getattr(item, "effective_at", None)
            if effective_at is None or not isinstance(effective_at, datetime):
                continue
            if effective_at <= context.decision_timestamp:
                candidates.append((effective_at, effective_at.isoformat()))
        return candidates[-1][1] if candidates else ""


__all__ = ["CryptoCrossSectionalMomentum1hV1"]
