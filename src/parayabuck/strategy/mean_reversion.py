from __future__ import annotations

from collections import deque
from typing import Any

from parayabuck.logger import get_logger
from parayabuck.models import Bar, Order, OrderType, Position, Side
from parayabuck.strategy.base import Strategy
from parayabuck.strategy.indicators import atr, bollinger, rsi

log = get_logger("parayabuck.strategy.mean_reversion")


class MeanReversion(Strategy):
    """Bollinger Bands + RSI mean reversion strategy.

    Entry Long:
      - Price closes below lower Bollinger Band
      - RSI < oversold_threshold (confirms oversold)
      - Volume > average volume (confirms participation)
      - Optional volume climax: volume > avg * climax_factor (e.g. 2x) signals
        capitulation — a flush-out that often marks the day's low. When
        require_climax_exit is set, we additionally wait for the NEXT bar to
        close back above the lower band before triggering, confirming buyers
        have stepped in rather than catching a falling knife.

    Entry Short:
      - Price closes above upper Bollinger Band
      - RSI > overbought_threshold
      - allow_short = True

    Exit:
      - Price returns to middle band (SMA)
      - OR stop-loss hit (ATR-based)

    Best in ranging / low-volatility markets.

    Params:
        bb_period: Bollinger period (default 20)
        bb_std: Number of std deviations (default 2.0)
        rsi_period: RSI period (default 14)
        oversold: RSI threshold for buy (default 30)
        overbought: RSI threshold for sell (default 70)
        volume_factor: Min volume / avg volume (default 1.0)
        climax_factor: Volume spike ratio that qualifies as a capitulation
            climax (default 2.0). Set <= volume_factor to disable.
        require_climax_exit: If True, after a climax touch below the lower
            band, wait for a close back above the lower band before buying.
            This converts "touch lower band" into "capitulation + reclaim",
            which is a much higher-quality mean-reversion trigger (default True).
        allow_short: Allow short selling (default False)
        risk_reward: Take-profit ratio (default 2.0)
        atr_multiplier: Stop-loss = ATR * multiplier (default 2.0)
        max_position: Max shares (default 100)
        use_trend_filter: Skip longs when price below long SMA (default True).
            Without this, mean reversion in a downtrend buys falling knives —
            oversold dips keep making lower lows, stop keeps triggering.
        trend_filter_period: SMA period for the trend filter (default 50)
        min_stop_pct: Min stop distance as fraction of entry (default 0.02).
            ATR*multiplier can be tiny on low timeframes, giving an
            ineffective stop that fires immediately on noise.
    """

    name = "mean_reversion"

    def __init__(self, **params: Any) -> None:
        super().__init__(
            bb_period=params.get("bb_period", 20),
            bb_std=params.get("bb_std", 2.0),
            rsi_period=params.get("rsi_period", 14),
            oversold=params.get("oversold", 30),
            overbought=params.get("overbought", 70),
            volume_factor=params.get("volume_factor", 1.0),
            climax_factor=params.get("climax_factor", 2.0),
            require_climax_exit=params.get("require_climax_exit", True),
            allow_short=params.get("allow_short", False),
            risk_reward=params.get("risk_reward", 2.0),
            atr_multiplier=params.get("atr_multiplier", 2.0),
            max_position=params.get("max_position", 100),
            use_trend_filter=params.get("use_trend_filter", True),
            trend_filter_period=params.get("trend_filter_period", 50),
            min_stop_pct=params.get("min_stop_pct", 0.02),
        )
        self.bb_period = int(self.params["bb_period"])
        self.bb_std = float(self.params["bb_std"])
        self.rsi_period = int(self.params["rsi_period"])
        self.oversold = float(self.params["oversold"])
        self.overbought = float(self.params["overbought"])
        self.volume_factor = float(self.params["volume_factor"])
        self.climax_factor = float(self.params["climax_factor"])
        self.require_climax_exit = bool(self.params["require_climax_exit"])
        self.allow_short = bool(self.params["allow_short"])
        self.risk_reward = float(self.params["risk_reward"])
        self.atr_multiplier = float(self.params["atr_multiplier"])
        self.max_position = int(self.params["max_position"])
        self.use_trend_filter = bool(self.params["use_trend_filter"])
        self.trend_filter_period = int(self.params["trend_filter_period"])
        self.min_stop_pct = float(self.params["min_stop_pct"])

        self._closes: deque[float] = deque(maxlen=200)
        self._highs: deque[float] = deque(maxlen=200)
        self._lows: deque[float] = deque(maxlen=200)
        self._volumes: deque[int] = deque(maxlen=50)
        self._entry_price: float = 0.0
        self._stop_price: float = 0.0
        self._target_price: float = 0.0
        # Hysteresis for climax-confirmation entries: after a climax touch
        # below the lower band, we arm a "buy on next close back above lower"
        # flag instead of buying the climax bar itself. This avoids buying the
        # falling knife leg and waits for buyers to actually step in.
        self._climax_armed_long: bool = False
        self._climax_armed_short: bool = False

    def _avg_volume(self) -> float:
        if not self._volumes:
            return 0.0
        return sum(self._volumes) / len(self._volumes)

    def on_bar(self, bar: Bar, position: Position) -> Order | None:
        self._bars_seen += 1
        self._closes.append(bar.close)
        self._highs.append(bar.high)
        self._lows.append(bar.low)
        self._volumes.append(bar.volume)

        warmup = max(self.bb_period, self.rsi_period, 14)
        if len(self._closes) < warmup:
            return None

        closes_list = list(self._closes)
        highs_list = list(self._highs)
        lows_list = list(self._lows)

        bb = bollinger(closes_list, self.bb_period, self.bb_std)
        if bb is None:
            return None

        rsi_val = rsi(closes_list, self.rsi_period)
        if rsi_val is None:
            return None

        avg_vol = self._avg_volume()
        vol_ok = bar.volume >= avg_vol * self.volume_factor if avg_vol > 0 else True

        # Manage existing position — check stop / target
        if position.is_long:
            if bar.close <= self._stop_price or bar.close >= self._target_price:
                log.info(
                    f"MR EXIT long {bar.symbol}: close={bar.close:.2f} "
                    f"stop={self._stop_price:.2f} target={self._target_price:.2f}"
                )
                return Order(
                    symbol=bar.symbol,
                    side=Side.SELL,
                    qty=position.qty,
                    order_type=OrderType.MARKET,
                )
            return None

        if position.is_short:
            if bar.close >= self._stop_price or bar.close <= self._target_price:
                log.info(
                    f"MR EXIT short {bar.symbol}: close={bar.close:.2f} "
                    f"stop={self._stop_price:.2f} target={self._target_price:.2f}"
                )
                return Order(
                    symbol=bar.symbol,
                    side=Side.BUY,
                    qty=abs(position.qty),
                    order_type=OrderType.MARKET,
                )
            return None

        # No position — look for entry
        atr_val = atr(highs_list, lows_list, closes_list, 14)
        if atr_val is None:
            return None

        # Trend filter: in a downtrend, oversold dips are falling knives —
        # they keep making lower lows and the stop keeps triggering. Skip
        # longs when price is below its long SMA. This single filter removes
        # the bulk of mean-reversion losses in trending-down markets.
        if self.use_trend_filter and self.trend_filter_period > 0:
            tf_period = min(self.trend_filter_period, len(closes_list))
            if tf_period > 0:
                trend_sma = sum(closes_list[-tf_period:]) / tf_period
                if bar.close < trend_sma:
                    return None

        # Min stop floor: ATR*multiplier can be a few cents on low timeframes,
        # giving a stop that fires on the first noisy tick. Floor it at a
        # small fraction of entry price so the stop has breathing room. Also
        # lift the take-profit target by the same ratio so R:R is preserved.
        atr_stop = atr_val * self.atr_multiplier
        min_stop_dist = bar.close * self.min_stop_pct
        stop_dist = max(atr_stop, min_stop_dist)
        tp_dist = stop_dist * self.risk_reward

        # Long entry: price below lower band + RSI oversold + volume confirmation
        # Two modes:
        #   1. require_climax_exit=True: arm on climax touch (vol > avg*climax_factor
        #      AND close < lower band AND RSI < oversold), then trigger on the
        #      NEXT bar that closes back ABOVE the lower band. This is the
        #      "capitulation then reclaim" pattern — much higher quality than
        #      blindly buying a single-bar touch.
        #   2. require_climax_exit=False: trigger immediately on the climax
        #      touch (legacy behaviour, but still requires climax_factor vol).
        is_volume_climax = avg_vol > 0 and bar.volume >= avg_vol * self.climax_factor
        is_climax_long = (
            bar.close <= bb.lower
            and rsi_val < self.oversold
            and vol_ok
            and is_volume_climax
        )

        if self.require_climax_exit:
            if is_climax_long:
                self._climax_armed_long = True
                log.debug(
                    f"MR climax arm long {bar.symbol}: close={bar.close:.2f} "
                    f"BB_lower={bb.lower:.2f} RSI={rsi_val:.1f} vol={bar.volume:.0f}"
                )
            # Trigger only on a subsequent bar that reclaims the lower band.
            if self._climax_armed_long and bar.close > bb.lower and rsi_val < 45:
                self._climax_armed_long = False
                self._entry_price = bar.close
                self._stop_price = bar.close - stop_dist
                self._target_price = min(bb.middle, bar.close + tp_dist)
                qty = min(self.max_position, 1)
                log.info(
                    f"MR ENTRY long {bar.symbol} (reclaim): close={bar.close:.2f} "
                    f"BB_lower={bb.lower:.2f} RSI={rsi_val:.1f} "
                    f"stop={self._stop_price:.2f} target={self._target_price:.2f}"
                )
                return Order(
                    symbol=bar.symbol,
                    side=Side.BUY,
                    qty=qty,
                    order_type=OrderType.MARKET,
                )
        else:
            if is_climax_long:
                self._entry_price = bar.close
                self._stop_price = bar.close - stop_dist
                self._target_price = min(bb.middle, bar.close + tp_dist)
                qty = min(self.max_position, 1)
                log.info(
                    f"MR ENTRY long {bar.symbol}: close={bar.close:.2f} "
                    f"BB_lower={bb.lower:.2f} RSI={rsi_val:.1f} "
                    f"stop={self._stop_price:.2f} target={self._target_price:.2f}"
                )
                return Order(
                    symbol=bar.symbol,
                    side=Side.BUY,
                    qty=qty,
                    order_type=OrderType.MARKET,
                )

        # Short entry: price above upper band + RSI overbought + climax volume
        is_climax_short = (
            self.allow_short
            and bar.close >= bb.upper
            and rsi_val > self.overbought
            and vol_ok
            and is_volume_climax
        )

        if self.require_climax_exit:
            if is_climax_short:
                self._climax_armed_short = True
            if self._climax_armed_short and bar.close < bb.upper and rsi_val > 55:
                self._climax_armed_short = False
                self._entry_price = bar.close
                self._stop_price = bar.close + stop_dist
                self._target_price = max(bb.middle, bar.close - tp_dist)
                qty = min(self.max_position, 1)
                log.info(
                    f"MR ENTRY short {bar.symbol} (reclaim): close={bar.close:.2f} "
                    f"BB_upper={bb.upper:.2f} RSI={rsi_val:.1f} "
                    f"stop={self._stop_price:.2f} target={self._target_price:.2f}"
                )
                return Order(
                    symbol=bar.symbol,
                    side=Side.SELL,
                    qty=qty,
                    order_type=OrderType.MARKET,
                )
        else:
            if is_climax_short:
                self._entry_price = bar.close
                self._stop_price = bar.close + stop_dist
                self._target_price = max(bb.middle, bar.close - tp_dist)
                qty = min(self.max_position, 1)
                log.info(
                    f"MR ENTRY short {bar.symbol}: close={bar.close:.2f} "
                    f"BB_upper={bb.upper:.2f} RSI={rsi_val:.1f} "
                    f"stop={self._stop_price:.2f} target={self._target_price:.2f}"
                )
                return Order(
                    symbol=bar.symbol,
                    side=Side.SELL,
                    qty=qty,
                    order_type=OrderType.MARKET,
                )

        return None
