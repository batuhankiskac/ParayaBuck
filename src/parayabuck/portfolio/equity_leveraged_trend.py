"""Trend-filtered leveraged index ETF: hold one ETF while an index is in an uptrend.

Each completed session the strategy compares the signal index's close with its
simple moving average over ``sma_bars`` completed sessions. Above the average
it targets the whole managed allocation in ``hold_symbol``; otherwise it
targets cash. ``sma_bars: 0`` holds ``hold_symbol`` unconditionally (the
buy-and-hold control used by research). With ``vol_target > 0`` the uptrend
weight is ``min(1, vol_target / realized volatility)`` of ``hold_symbol`` over
the last ``vol_window`` completed sessions (annualized), the rest cash, so the
position shrinks when the leveraged ETF is turbulent.

Research: ``docs/research/profit_max_v1.md``. The rule is the long-standing
200-day moving-average filter on a leveraged index; the leverage, not the
filter, drives the return, and the filter mainly shortens drawdowns.

``trend_rule`` selects the in-trend test (``docs/research/e2_integration_v1.md``):

* ``sma`` (the default, VT35): close > SMA(``sma_bars``).
* ``majority_sma_tsm_don`` (E2): in trend when at least two of three votes
  hold on the signal index's completed closes. SMA as above; TSM, the
  ``tsm_bars``-session price return is above zero; DON, the close is above
  the midpoint of the max/min of the last ``donchian_bars`` closes (current
  close included). Research E2 measured TSM on total return against
  cumulative 3-month T-bills and DON on the total-return level; production
  has split-adjusted price closes only and no T-bill series, so TSM uses a
  zero hurdle on price. Sizing is unchanged. Any vote without enough history
  makes the decision ``insufficient_history`` (cash), never a 2-of-2 vote.

The two rules report different strategy versions so decision receipts and
idempotency keys distinguish them.
"""

from __future__ import annotations

import math

from parayabuck.core.models import Market
from parayabuck.markets.symbols import canonical_symbol
from parayabuck.portfolio.base import PortfolioContext, PortfolioStrategy
from parayabuck.portfolio.models import RebalanceReason, TargetPortfolio, TargetWeight

# The only parameters the rule reads. Anything else is a typo whose intended
# change would never apply while it still perturbed the configuration digest.
_PARAMETER_KEYS = frozenset(
    {
        "signal_symbol",
        "hold_symbol",
        "sma_bars",
        "vol_target",
        "vol_window",
        "rebalance_frequency",
        "trend_rule",
        "tsm_bars",
        "donchian_bars",
    }
)
TREND_RULE_SMA = "sma"
TREND_RULE_MAJORITY = "majority_sma_tsm_don"
_TREND_RULES = (TREND_RULE_SMA, TREND_RULE_MAJORITY)
# Parameters only the majority rule reads; under the SMA rule they would be
# silent no-op knobs that still perturb the configuration digest.
_MAJORITY_ONLY_KEYS = ("tsm_bars", "donchian_bars")
_VERSION_BY_RULE = {TREND_RULE_SMA: "1.0.0", TREND_RULE_MAJORITY: "1.1.0"}
# Sane ranges: an SMA longer than ~4 years of sessions is permanent cash on
# any realistic history, an annualized vol target above 100% pins the weight
# at 1.0 (volatility targeting silently off), and a vol window beyond one
# trading year no longer measures current turbulence. A momentum lookback or
# channel beyond ~4 years is permanent cash on any realistic warm-up too.
_MAX_SMA_BARS = 1000
_MAX_VOL_TARGET = 1.0
_MAX_VOL_WINDOW = 252
_MAX_TSM_BARS = 1000
_MAX_DONCHIAN_BARS = 1000


class EquityLeveragedTrendV1(PortfolioStrategy):
    """Hold ``hold_symbol`` while ``signal_symbol`` is in trend (SMA or 2-of-3 vote), else cash."""

    name = "equity_leveraged_trend_v1"
    version = _VERSION_BY_RULE[TREND_RULE_SMA]
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
        # ``trend_rule`` is never defaulted into the parameters: an SMA-rule
        # configuration keeps VT35's exact parameter tuple (and digest).
        if merged.get("trend_rule") == TREND_RULE_MAJORITY:
            merged.setdefault("tsm_bars", 252)
            merged.setdefault("donchian_bars", 126)
        super().__init__(merged)
        self.version = _VERSION_BY_RULE[self.trend_rule]

    def _validate_parameters(self) -> None:
        unknown = sorted(set(self.parameters) - _PARAMETER_KEYS)
        if unknown:
            raise ValueError(
                f"equity_leveraged_trend_v1 does not accept parameter(s) {', '.join(unknown)}; "
                f"supported: {', '.join(sorted(_PARAMETER_KEYS))}"
            )
        if self.parameters.get("rebalance_frequency") != "hours:24":
            raise ValueError("equity_leveraged_trend_v1 decides every session: rebalance_frequency must be hours:24")
        for key in ("signal_symbol", "hold_symbol"):
            value = self.parameters.get(key)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{key} must be a symbol")
            self.parameters[key] = canonical_symbol(value, Market.EQUITY)
        sma = self.parameters.get("sma_bars")
        # SMA(1) equals the last close, so the strict ``close > sma`` test could
        # never signal an uptrend: 1 would silently mean always-cash.
        if isinstance(sma, bool) or not isinstance(sma, int) or sma < 0 or sma == 1 or sma > _MAX_SMA_BARS:
            raise ValueError(f"sma_bars must be a non-negative integer: 0 (buy-and-hold) or 2..{_MAX_SMA_BARS}")
        target = self.parameters.get("vol_target")
        if isinstance(target, bool) or not isinstance(target, (int, float)) or not 0 <= target <= _MAX_VOL_TARGET:
            raise ValueError(
                f"vol_target must be a number in [0, {_MAX_VOL_TARGET:g}] (0 disables volatility targeting)"
            )
        self.parameters["vol_target"] = float(target)
        window = self.parameters.get("vol_window")
        if isinstance(window, bool) or not isinstance(window, int) or not 2 <= window <= _MAX_VOL_WINDOW:
            raise ValueError(f"vol_window must be an integer in [2, {_MAX_VOL_WINDOW}]")
        rule = self.parameters.get("trend_rule", TREND_RULE_SMA)
        if rule not in _TREND_RULES:
            raise ValueError(f"trend_rule must be one of {', '.join(_TREND_RULES)}")
        if rule == TREND_RULE_SMA:
            stray = [key for key in _MAJORITY_ONLY_KEYS if key in self.parameters]
            if stray:
                raise ValueError(f"{', '.join(stray)} only apply to trend_rule {TREND_RULE_MAJORITY}")
            return
        if sma == 0:
            raise ValueError(f"trend_rule {TREND_RULE_MAJORITY} needs an SMA vote: sma_bars must be 2..{_MAX_SMA_BARS}")
        tsm = self.parameters.get("tsm_bars")
        if isinstance(tsm, bool) or not isinstance(tsm, int) or not 2 <= tsm <= _MAX_TSM_BARS:
            raise ValueError(f"tsm_bars must be an integer in [2, {_MAX_TSM_BARS}]")
        channel = self.parameters.get("donchian_bars")
        # A one-close channel's midpoint is the close itself: never "above".
        if isinstance(channel, bool) or not isinstance(channel, int) or not 2 <= channel <= _MAX_DONCHIAN_BARS:
            raise ValueError(f"donchian_bars must be an integer in [2, {_MAX_DONCHIAN_BARS}]")

    @property
    def signal_symbol(self) -> str:
        return str(self.parameters["signal_symbol"])

    @property
    def hold_symbol(self) -> str:
        return str(self.parameters["hold_symbol"])

    @property
    def decision_symbols(self) -> tuple[str, ...]:
        """The trend input and the held ETF; the benchmark is never read."""
        return tuple(dict.fromkeys((self.signal_symbol, self.hold_symbol)))

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
    def trend_rule(self) -> str:
        return str(self.parameters.get("trend_rule", TREND_RULE_SMA))

    @property
    def tsm_bars(self) -> int | None:
        value = self.parameters.get("tsm_bars")
        return value if isinstance(value, int) and not isinstance(value, bool) else None

    @property
    def donchian_bars(self) -> int | None:
        value = self.parameters.get("donchian_bars")
        return value if isinstance(value, int) and not isinstance(value, bool) else None

    @property
    def minimum_history_bars(self) -> int:
        bars = max(self.sma_bars, self.vol_window + 1 if self.vol_target else 1)
        if self.trend_rule == TREND_RULE_MAJORITY:
            # TSM compares the last close with the close ``tsm_bars`` sessions earlier.
            bars = max(bars, (self.tsm_bars or 0) + 1, self.donchian_bars or 0)
        return bars

    def realized_volatility(self, context: PortfolioContext) -> float | None:
        """Annualized sample volatility of ``hold_symbol`` daily returns over ``vol_window``."""
        closes = context.closes(self.hold_symbol)[-(self.vol_window + 1) :]
        if len(closes) < self.vol_window + 1:
            return None
        # A degenerate bar with a non-positive close has no defined return;
        # skip it (as the crypto twin does) instead of raising out of decide().
        returns = [closes[index] / closes[index - 1] - 1.0 for index in range(1, len(closes)) if closes[index - 1] > 0]
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

    def tsm_vote(self, closes: list[float]) -> tuple[bool | None, float | None]:
        """(``tsm_bars``-session price return > 0, that return); ``None`` vote when short or degenerate."""
        lookback = self.tsm_bars or 0
        if lookback < 1 or len(closes) < lookback + 1:
            return None, None
        base = closes[-(lookback + 1)]
        if base <= 0:
            return None, None
        momentum = closes[-1] / base - 1.0
        return momentum > 0.0, momentum

    def donchian_vote(self, closes: list[float]) -> tuple[bool | None, float | None]:
        """(close > midpoint of the last ``donchian_bars`` closes' max/min, midpoint); ``None`` when short."""
        window = self.donchian_bars or 0
        if window < 2 or len(closes) < window:
            return None, None
        tail = closes[-window:]
        midpoint = (max(tail) + min(tail)) / 2.0
        return closes[-1] > midpoint, midpoint

    def majority_state(
        self, context: PortfolioContext
    ) -> tuple[bool | None, float | None, float | None, dict[str, object]]:
        """(in trend by 2-of-3 vote, last close, SMA, per-vote detail); ``None`` trend when any vote is short."""
        sma_up, close, sma = self.trend_state(context)
        closes = context.closes(self.signal_symbol)
        tsm_up, momentum = self.tsm_vote(closes)
        don_up, midpoint = self.donchian_vote(closes)
        detail: dict[str, object] = {
            "sma": {"up": sma_up, "close": close, "sma": sma, "bars": self.sma_bars},
            "tsm": {"up": tsm_up, "return": momentum, "hurdle": 0.0, "bars": self.tsm_bars},
            "don": {"up": don_up, "close": close, "midpoint": midpoint, "bars": self.donchian_bars},
        }
        votes = (sma_up, tsm_up, don_up)
        if any(vote is None for vote in votes):
            return None, close, sma, {**detail, "votes_up": None}
        up = sum(1 for vote in votes if vote)
        return up >= 2, close, sma, {**detail, "votes_up": up}

    def decide(self, context: PortfolioContext) -> TargetPortfolio:
        signals: dict[str, object] | None = None
        if self.trend_rule == TREND_RULE_MAJORITY:
            uptrend, close, sma, signals = self.majority_state(context)
        else:
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
        weights = (TargetWeight(self.hold_symbol, weight, reason="trend_up", rank=1),) if state == "uptrend" else ()
        metadata: dict[str, object] = {
            "trend_state": state,
            "signal_symbol": self.signal_symbol,
            "hold_symbol": self.hold_symbol,
            "signal_close": close,
            "signal_sma": sma,
            "sma_bars": self.sma_bars,
            "realized_volatility": volatility,
            "vol_target": self.vol_target,
        }
        if signals is not None:
            # Only the majority rule records its votes: VT35's metadata (and
            # every receipt or hash built from it) stays byte-identical.
            metadata["trend_rule"] = self.trend_rule
            metadata["trend_signals"] = signals
        return TargetPortfolio(
            strategy_name=self.name,
            strategy_version=self.version,
            decision_timestamp=context.decision_timestamp,
            data_timestamp=context.decision_timestamp,
            weights=weights,
            cash_weight=1.0 - sum(item.weight for item in weights),
            rebalance_reason=RebalanceReason.SCHEDULED if weights else RebalanceReason.RISK_OFF,
            universe_version=context.universe_version,
            universe_snapshot_id=context.universe_version,
            parameters=self.parameter_tuple(),
            metadata=metadata,
        )


__all__ = ["TREND_RULE_MAJORITY", "TREND_RULE_SMA", "EquityLeveragedTrendV1"]
