"""Trend-filtered leveraged index ETF: hold one ETF while an index is in an uptrend.

Each completed session the strategy compares the signal index's close with its
simple moving average over ``sma_bars`` completed sessions. Above the average
it targets the whole managed allocation in ``hold_symbol``; otherwise it
targets cash. ``sma_bars: 0`` holds ``hold_symbol`` unconditionally (the
buy-and-hold control used by research). With ``vol_target > 0`` the uptrend
weight is ``min(1, vol_target / realized volatility)`` of ``hold_symbol`` over
the last ``vol_window`` completed sessions (annualized), the rest cash, so the
position shrinks when the leveraged ETF is turbulent.

Optional idle-cash sweep (``cash_sweep_symbol``, e.g. ``BIL``; off unless
set): the part of the managed allocation not targeted to ``hold_symbol`` (the
whole allocation in a downtrend, ``1 - weight`` when volatility targeting
shrinks the position) is targeted to the sweep ETF instead of cash. Orders go
through the same rebalance band as ``hold_symbol``. The account-level cash
buffer (``risk.cash_buffer_pct``) stays cash because it lies outside the
managed allocation. When unset, the parameter is absent from
``parameter_tuple()``, so the configuration digest of a sweep-less
configuration is unchanged; when set, it enters the digest.

Research: ``docs/research/profit_max_v1.md``. The rule is the long-standing
200-day moving-average filter on a leveraged index; the leverage, not the
filter, drives the return, and the filter mainly shortens drawdowns.
"""

from __future__ import annotations

import math

from parayabuck.core.models import Market
from parayabuck.markets.symbols import canonical_symbol
from parayabuck.portfolio.base import PortfolioContext, PortfolioStrategy
from parayabuck.portfolio.models import RebalanceReason, TargetPortfolio, TargetWeight


class EquityLeveragedTrendV1(PortfolioStrategy):
    """Hold ``hold_symbol`` while ``signal_symbol`` closes above its SMA, else cash."""

    name = "equity_leveraged_trend_v1"
    version = "1.0.0"
    market = Market.EQUITY
    timeframe = "1d"
    display_name = "Leveraged index trend v1"
    supported_timeframes = ("1d",)
    rebalance_policy_declared = "hours:24"
    cadence_label = "daily"

    def __init__(self, parameters: dict[str, object] | None = None) -> None:
        merged = dict(parameters or {})
        merged.setdefault("signal_symbol", "QQQ")
        merged.setdefault("hold_symbol", "QLD")
        merged.setdefault("sma_bars", 200)
        merged.setdefault("vol_target", 0.0)
        merged.setdefault("vol_window", 20)
        merged.setdefault("rebalance_frequency", "hours:24")
        # Off unless set; an empty value is dropped so it stays out of the digest.
        if merged.get("cash_sweep_symbol") in (None, ""):
            merged.pop("cash_sweep_symbol", None)
        super().__init__(merged)

    def _validate_parameters(self) -> None:
        if self.parameters.get("rebalance_frequency") != "hours:24":
            raise ValueError("equity_leveraged_trend_v1 decides every session: rebalance_frequency must be hours:24")
        for key in ("signal_symbol", "hold_symbol"):
            value = self.parameters.get(key)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{key} must be a symbol")
            self.parameters[key] = canonical_symbol(value, Market.EQUITY)
        sma = self.parameters.get("sma_bars")
        if isinstance(sma, bool) or not isinstance(sma, int) or sma < 0:
            raise ValueError("sma_bars must be a non-negative integer")
        target = self.parameters.get("vol_target")
        if isinstance(target, bool) or not isinstance(target, (int, float)) or not 0 <= target <= 5:
            raise ValueError("vol_target must be a number in [0, 5] (0 disables volatility targeting)")
        self.parameters["vol_target"] = float(target)
        window = self.parameters.get("vol_window")
        if isinstance(window, bool) or not isinstance(window, int) or window < 2:
            raise ValueError("vol_window must be an integer of at least 2")
        if "cash_sweep_symbol" in self.parameters:
            sweep = self.parameters["cash_sweep_symbol"]
            if not isinstance(sweep, str) or not sweep.strip():
                raise ValueError("cash_sweep_symbol must be a symbol (omit it to disable the sweep)")
            sweep = canonical_symbol(sweep, Market.EQUITY)
            if sweep in (self.parameters["signal_symbol"], self.parameters["hold_symbol"]):
                raise ValueError("cash_sweep_symbol must differ from signal_symbol and hold_symbol")
            self.parameters["cash_sweep_symbol"] = sweep

    @property
    def signal_symbol(self) -> str:
        return str(self.parameters["signal_symbol"])

    @property
    def hold_symbol(self) -> str:
        return str(self.parameters["hold_symbol"])

    @property
    def cash_sweep_symbol(self) -> str | None:
        """Idle-cash sweep ETF, or ``None`` when the sweep is off."""
        value = self.parameters.get("cash_sweep_symbol")
        return str(value) if value else None

    @property
    def sma_bars(self) -> int:
        return int(self.parameters["sma_bars"])  # type: ignore[call-overload, no-any-return]

    @property
    def vol_target(self) -> float:
        return float(self.parameters["vol_target"])  # type: ignore[arg-type]

    @property
    def vol_window(self) -> int:
        return int(self.parameters["vol_window"])  # type: ignore[call-overload, no-any-return]

    @property
    def minimum_history_bars(self) -> int:
        return max(self.sma_bars, self.vol_window + 1 if self.vol_target else 1)

    def realized_volatility(self, context: PortfolioContext) -> float | None:
        """Annualized sample volatility of ``hold_symbol`` daily returns over ``vol_window``."""
        closes = context.closes(self.hold_symbol)[-(self.vol_window + 1) :]
        if len(closes) < self.vol_window + 1:
            return None
        # A degenerate bar with a non-positive close has no defined return;
        # skip it (as the crypto twin does) instead of raising out of decide().
        returns = [
            closes[index] / closes[index - 1] - 1.0 for index in range(1, len(closes)) if closes[index - 1] > 0
        ]
        if len(returns) < 2:
            return None
        mean = sum(returns) / len(returns)
        variance = sum((value - mean) ** 2 for value in returns) / (len(returns) - 1)
        return math.sqrt(variance * 252.0)

    def trend_state(self, context: PortfolioContext) -> tuple[bool | None, float | None, float | None]:
        """(in_uptrend, last close, SMA); ``None`` trend when history is too short."""
        if self.sma_bars == 0:
            return True, None, None
        closes = context.closes(self.signal_symbol)
        if len(closes) < self.sma_bars:
            return None, closes[-1] if closes else None, None
        sma = sum(closes[-self.sma_bars :]) / self.sma_bars
        return closes[-1] > sma, closes[-1], sma

    def decide(self, context: PortfolioContext) -> TargetPortfolio:
        uptrend, close, sma = self.trend_state(context)
        tradable = bool(context.history.get(self.hold_symbol)) and context.in_universe(self.hold_symbol)
        volatility = self.realized_volatility(context) if self.vol_target else None
        if uptrend is None:
            state = "insufficient_history"
        elif not tradable:
            state = "hold_symbol_unavailable"
        elif self.vol_target and volatility is None:
            state = "insufficient_history"
        else:
            state = "uptrend" if uptrend else "downtrend"
        weight = 1.0
        if self.vol_target and volatility is not None:
            weight = 1.0 if volatility <= 0 else min(1.0, self.vol_target / volatility)
        weights: tuple[TargetWeight, ...] = (
            (TargetWeight(self.hold_symbol, weight, reason="trend_up", rank=1),) if state == "uptrend" else ()
        )
        holds_trend = bool(weights)
        sweep = self.cash_sweep_symbol
        sweep_weight = 0.0
        if sweep is not None and bool(context.history.get(sweep)) and context.in_universe(sweep):
            sweep_weight = max(0.0, 1.0 - sum(item.weight for item in weights))
            if sweep_weight > 0.0:
                weights = (*weights, TargetWeight(sweep, sweep_weight, reason="cash_sweep", rank=2))
        return TargetPortfolio(
            strategy_name=self.name,
            strategy_version=self.version,
            decision_timestamp=context.decision_timestamp,
            data_timestamp=context.decision_timestamp,
            weights=weights,
            cash_weight=max(0.0, 1.0 - sum(item.weight for item in weights)),
            rebalance_reason=RebalanceReason.SCHEDULED if holds_trend else RebalanceReason.RISK_OFF,
            universe_version=context.universe_version,
            universe_snapshot_id=context.universe_version,
            parameters=self.parameter_tuple(),
            metadata={
                **({"cash_sweep_symbol": sweep, "cash_sweep_weight": sweep_weight} if sweep is not None else {}),
                "trend_state": state,
                "signal_symbol": self.signal_symbol,
                "hold_symbol": self.hold_symbol,
                "signal_close": close,
                "signal_sma": sma,
                "sma_bars": self.sma_bars,
                "realized_volatility": volatility,
                "vol_target": self.vol_target,
            },
        )


__all__ = ["EquityLeveragedTrendV1"]
