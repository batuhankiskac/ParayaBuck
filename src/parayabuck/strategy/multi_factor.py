from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Any

from parayabuck.logger import get_logger
from parayabuck.models import Bar, Order, OrderType, Position, Side
from parayabuck.strategy.base import Strategy
from parayabuck.strategy.indicators import rsi

log = get_logger("parayabuck.strategy.multi_factor")


@dataclass
class FactorScore:
    symbol: str
    momentum_3m: float
    momentum_6m: float
    volatility: float
    rsi_val: float
    rel_strength: float
    volume_trend: float
    total_score: float = 0.0
    rank: int = 0


class MultiFactorMomentum(Strategy):
    """Multi-factor momentum scoring strategy.

    Scores each stock on multiple factors, then trades the highest-scoring ones.

    Factors (each normalized 0-100):
      - 3-month momentum (price return)
      - 6-month momentum
      - Volatility (lower = better score)
      - RSI (50-70 sweet spot for healthy momentum)
      - Relative strength vs benchmark
      - Volume trend (increasing = better)

    Signal:
      - Score > buy_threshold → BUY (if flat)
      - Score < sell_threshold → SELL (if long)

    Params:
        benchmark: Benchmark symbol (default "SPY")
        mom_3m_period: Bars for 3-month momentum (default 63 ~ 3 months of trading days)
        mom_6m_period: Bars for 6-month momentum (default 126)
        vol_period: Volatility lookback (default 20)
        rsi_period: RSI period (default 14)
        buy_score: Min score to buy (default 65)
        sell_score: Score below which to sell (default 40)
        max_position: Max shares per stock (default 50)
        allow_short: Allow shorting low-score stocks (default False)
    """

    name = "multi_factor"

    def __init__(self, **params: Any) -> None:
        super().__init__(
            benchmark=params.get("benchmark", "SPY"),
            mom_3m_period=params.get("mom_3m_period", 63),
            mom_6m_period=params.get("mom_6m_period", 126),
            vol_period=params.get("vol_period", 20),
            rsi_period=params.get("rsi_period", 14),
            buy_score=params.get("buy_score", 65),
            sell_score=params.get("sell_score", 40),
            max_position=params.get("max_position", 50),
            allow_short=params.get("allow_short", False),
        )
        self.benchmark = str(self.params["benchmark"])
        self.symbols = None  # set dynamically when benchmark feed is wired
        self.mom_3m_period = int(self.params["mom_3m_period"])
        self.mom_6m_period = int(self.params["mom_6m_period"])
        self.vol_period = int(self.params["vol_period"])
        self.rsi_period = int(self.params["rsi_period"])
        self.buy_score = float(self.params["buy_score"])
        self.sell_score = float(self.params["sell_score"])
        self.max_position = int(self.params["max_position"])
        self.allow_short = bool(self.params["allow_short"])

        self._symbol_bars: dict[str, deque[Bar]] = {}
        self._scores: dict[str, FactorScore] = {}

    def _ensure_buffer(self, symbol: str) -> deque[Bar]:
        if symbol not in self._symbol_bars:
            self._symbol_bars[symbol] = deque(maxlen=300)
        return self._symbol_bars[symbol]

    def _calc_score(self, symbol: str, benchmark_return: float) -> FactorScore | None:
        bars = list(self._symbol_bars.get(symbol, []))
        if len(bars) < self.mom_6m_period:
            return None

        closes = [b.close for b in bars]
        volumes = [b.volume for b in bars]

        mom_3m = (closes[-1] / closes[-self.mom_3m_period] - 1) * 100
        mom_6m = (closes[-1] / closes[-self.mom_6m_period] - 1) * 100

        recent_closes = closes[-self.vol_period :]
        vol_returns = [
            abs(recent_closes[i] / recent_closes[i - 1] - 1) for i in range(1, len(recent_closes))
        ]
        volatility = (sum(vol_returns) / len(vol_returns) * 100) if vol_returns else 0

        rsi_val = rsi(closes, self.rsi_period) or 50

        symbol_return = (closes[-1] / closes[-self.mom_3m_period] - 1) * 100
        rel_strength = symbol_return - benchmark_return

        recent_vol = sum(volumes[-10:]) / max(len(volumes[-10:]), 1)
        older_vol = sum(volumes[-20:-10]) / max(len(volumes[-20:-10]), 1)
        volume_trend = (recent_vol / older_vol - 1) * 100 if older_vol > 0 else 0

        s_mom = self._normalize(mom_3m + mom_6m, -20, 40)
        s_vol = self._normalize(-volatility, -5, 5)
        s_rsi = self._normalize(100 - abs(rsi_val - 60) * 2, 0, 100)
        s_rs = self._normalize(rel_strength, -15, 15)
        s_vol_trend = self._normalize(volume_trend, -50, 50)

        total = s_mom * 0.30 + s_vol * 0.15 + s_rsi * 0.15 + s_rs * 0.25 + s_vol_trend * 0.15

        return FactorScore(
            symbol=symbol,
            momentum_3m=mom_3m,
            momentum_6m=mom_6m,
            volatility=volatility,
            rsi_val=rsi_val,
            rel_strength=rel_strength,
            volume_trend=volume_trend,
            total_score=total,
        )

    @staticmethod
    def _normalize(value: float, low: float, high: float) -> float:
        if high == low:
            return 50.0
        return max(0.0, min(100.0, (value - low) / (high - low) * 100))

    def update_benchmark_bar(self, bar: Bar) -> None:
        """Feed a benchmark bar so relative-strength scoring has a reference.

        MultiFactorMomentum is a single-symbol strategy that scores against a
        benchmark (e.g. SPY). The benchmark is not traded, but its bars must be
        supplied separately via this method (the backtest engine and live engine
        do this when `strategy.benchmark` is set).
        """
        self._ensure_buffer(bar.symbol).append(bar)

    def on_bar(self, bar: Bar, position: Position) -> Order | None:
        self._bars_seen += 1
        buf = self._ensure_buffer(bar.symbol)
        buf.append(bar)

        if len(buf) < self.mom_6m_period:
            return None

        benchmark_bars = list(self._symbol_bars.get(self.benchmark, []))
        benchmark_return = 0.0
        if len(benchmark_bars) >= self.mom_3m_period:
            bc = [b.close for b in benchmark_bars]
            benchmark_return = (bc[-1] / bc[-self.mom_3m_period] - 1) * 100

        if bar.symbol == self.benchmark:
            return None

        score = self._calc_score(bar.symbol, benchmark_return)
        if score is None:
            return None
        self._scores[bar.symbol] = score

        # Long entry
        if position.is_flat and score.total_score > self.buy_score:
            qty = min(self.max_position, 1)
            log.info(
                f"MF ENTRY long {bar.symbol}: score={score.total_score:.1f} "
                f"mom3m={score.momentum_3m:.1f}% rs={score.rel_strength:.1f}%"
            )
            return Order(
                symbol=bar.symbol,
                side=Side.BUY,
                qty=qty,
                order_type=OrderType.MARKET,
            )

        # Exit long
        if position.is_long and score.total_score < self.sell_score:
            log.info(
                f"MF EXIT long {bar.symbol}: score={score.total_score:.1f} "
                f"fell below {self.sell_score}"
            )
            return Order(
                symbol=bar.symbol,
                side=Side.SELL,
                qty=position.qty,
                order_type=OrderType.MARKET,
            )

        # Short entry (optional)
        if self.allow_short and position.is_flat and score.total_score < 15:
            qty = min(self.max_position, 1)
            log.info(f"MF ENTRY short {bar.symbol}: score={score.total_score:.1f} (weak momentum)")
            return Order(
                symbol=bar.symbol,
                side=Side.SELL,
                qty=qty,
                order_type=OrderType.MARKET,
            )

        # Exit short
        if position.is_short and score.total_score > 60:
            log.info(f"MF EXIT short {bar.symbol}: score={score.total_score:.1f} recovered")
            return Order(
                symbol=bar.symbol,
                side=Side.BUY,
                qty=abs(position.qty),
                order_type=OrderType.MARKET,
            )

        return None

    def get_scores(self) -> dict[str, FactorScore]:
        return self._scores
