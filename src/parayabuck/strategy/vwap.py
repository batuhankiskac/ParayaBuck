from __future__ import annotations

from collections import deque
from datetime import datetime
from typing import Any

from parayabuck.logger import get_logger
from parayabuck.models import Bar, Order, OrderType, Position, Side
from parayabuck.strategy.base import Strategy
from parayabuck.strategy.indicators import vwap

log = get_logger("parayabuck.strategy.vwap")


class VWAPReversion(Strategy):
    """VWAP reversion strategy (intraday).

    Trades when price deviates significantly from VWAP, expecting reversion.

    Entry Long:
      - Price below VWAP by more than deviation_threshold (e.g. 1%)
      - RSI < 40 (mildly oversold, not crashing)
      - Within trading window (first 2 hours or last hour)

    Entry Short:
      - Price above VWAP by more than deviation_threshold
      - RSI > 60
      - allow_short = True

    Exit:
      - Price crosses back above/below VWAP
      - OR end-of-day forced close
      - OR stop-loss hit

    Best for liquid stocks with mean-reverting intraday behavior.

    Params:
        vwap_period: Bars for VWAP calculation (default 30)
        deviation_threshold: Min % deviation from VWAP (default 1.0)
        rsi_period: RSI period (default 14)
        rsi_buy_max: Max RSI for buy (default 40)
        rsi_sell_min: Min RSI for sell (default 60)
        stop_pct: Stop-loss % from entry (default 1.5)
        allow_short: Allow short (default False)
        max_position: Max shares (default 100)
        trading_start_minutes: Skip first N minutes after open (default 15)
        trading_end_minutes_offset: Stop N minutes before close (default 30)
        force_eod_close: Close all positions near end of day (default True)
    """

    name = "vwap_reversion"

    def __init__(self, **params: Any) -> None:
        super().__init__(
            vwap_period=params.get("vwap_period", 30),
            deviation_threshold=params.get("deviation_threshold", 1.0),
            rsi_period=params.get("rsi_period", 14),
            rsi_buy_max=params.get("rsi_buy_max", 40),
            rsi_sell_min=params.get("rsi_sell_min", 60),
            stop_pct=params.get("stop_pct", 1.5),
            allow_short=params.get("allow_short", False),
            max_position=params.get("max_position", 100),
            trading_start_minutes=params.get("trading_start_minutes", 15),
            trading_end_minutes_offset=params.get("trading_end_minutes_offset", 30),
            force_eod_close=params.get("force_eod_close", True),
        )
        self.vwap_period = int(self.params["vwap_period"])
        self.deviation_threshold = float(self.params["deviation_threshold"])
        self.rsi_period = int(self.params["rsi_period"])
        self.rsi_buy_max = float(self.params["rsi_buy_max"])
        self.rsi_sell_min = float(self.params["rsi_sell_min"])
        self.stop_pct = float(self.params["stop_pct"]) / 100
        self.allow_short = bool(self.params["allow_short"])
        self.max_position = int(self.params["max_position"])
        self.trading_start_minutes = int(self.params["trading_start_minutes"])
        self.trading_end_minutes_offset = int(self.params["trading_end_minutes_offset"])
        self.force_eod_close = bool(self.params["force_eod_close"])

        self._highs: deque[float] = deque(maxlen=self.vwap_period)
        self._lows: deque[float] = deque(maxlen=self.vwap_period)
        self._volumes: deque[int] = deque(maxlen=self.vwap_period)
        self._closes: deque[float] = deque(maxlen=200)
        self._entry_price: float = 0.0
        self._stop_price: float = 0.0
        self._current_vwap: float = 0.0
        self._session_date: datetime | None = None

    def _reset_session(self, date: datetime) -> None:
        self._session_date = date
        self._highs.clear()
        self._lows.clear()
        self._volumes.clear()

    def _is_trading_window(self, bar: Bar) -> bool:
        t = bar.timestamp
        minutes_from_open = (t.hour - 9) * 60 + (t.minute - 30)
        minutes_to_close = (16 * 60) - (t.hour * 60 + t.minute)
        if minutes_from_open < self.trading_start_minutes:
            return False
        if minutes_to_close < self.trading_end_minutes_offset:
            return False
        return True

    def _is_near_eod(self, bar: Bar) -> bool:
        t = bar.timestamp
        minutes_to_close = (16 * 60) - (t.hour * 60 + t.minute)
        return minutes_to_close <= self.trading_end_minutes_offset

    def on_bar(self, bar: Bar, position: Position) -> Order | None:
        self._bars_seen += 1

        bar_date = bar.timestamp.replace(hour=0, minute=0, second=0, microsecond=0)
        if self._session_date != bar_date:
            self._reset_session(bar_date)

        self._highs.append(bar.high)
        self._lows.append(bar.low)
        self._volumes.append(bar.volume)
        self._closes.append(bar.close)

        if len(self._highs) < self.vwap_period:
            return None

        from parayabuck.strategy.indicators import rsi as calc_rsi

        self._current_vwap = vwap(
            list(self._highs), list(self._lows), list(self._volumes)
        )
        if self._current_vwap <= 0:
            return None

        deviation_pct = ((bar.close - self._current_vwap) / self._current_vwap) * 100

        # Force EOD close
        if self.force_eod_close and self._is_near_eod(bar):
            if position.is_long:
                log.info(f"VWAP EOD close long {bar.symbol} @ {bar.close:.2f}")
                return Order(
                    symbol=bar.symbol,
                    side=Side.SELL,
                    qty=position.qty,
                    order_type=OrderType.MARKET,
                )
            if position.is_short:
                log.info(f"VWAP EOD close short {bar.symbol} @ {bar.close:.2f}")
                return Order(
                    symbol=bar.symbol,
                    side=Side.BUY,
                    qty=abs(position.qty),
                    order_type=OrderType.MARKET,
                )

        # Manage existing position — stop loss
        if position.is_long:
            if bar.close <= self._stop_price:
                log.info(
                    f"VWAP stop long {bar.symbol}: "
                    f"close={bar.close:.2f} stop={self._stop_price:.2f}"
                )
                return Order(
                    symbol=bar.symbol,
                    side=Side.SELL,
                    qty=position.qty,
                    order_type=OrderType.MARKET,
                )
            if abs(deviation_pct) < 0.2:
                log.info(f"VWAP target long {bar.symbol}: reverted to VWAP")
                return Order(
                    symbol=bar.symbol,
                    side=Side.SELL,
                    qty=position.qty,
                    order_type=OrderType.MARKET,
                )
            return None

        if position.is_short:
            if bar.close >= self._stop_price:
                log.info(
                    f"VWAP stop short {bar.symbol}: "
                    f"close={bar.close:.2f} stop={self._stop_price:.2f}"
                )
                return Order(
                    symbol=bar.symbol,
                    side=Side.BUY,
                    qty=abs(position.qty),
                    order_type=OrderType.MARKET,
                )
            if abs(deviation_pct) < 0.2:
                log.info(f"VWAP target short {bar.symbol}: reverted to VWAP")
                return Order(
                    symbol=bar.symbol,
                    side=Side.BUY,
                    qty=abs(position.qty),
                    order_type=OrderType.MARKET,
                )
            return None

        # No position — look for entry
        if not self._is_trading_window(bar):
            return None

        rsi_val = calc_rsi(list(self._closes), self.rsi_period)
        if rsi_val is None:
            return None

        # Long entry: price below VWAP
        if deviation_pct < -self.deviation_threshold and rsi_val < self.rsi_buy_max:
            self._entry_price = bar.close
            self._stop_price = bar.close * (1 - self.stop_pct)
            qty = min(self.max_position, 1)
            log.info(
                f"VWAP ENTRY long {bar.symbol}: close={bar.close:.2f} "
                f"vwap={self._current_vwap:.2f} dev={deviation_pct:.2f}% RSI={rsi_val:.1f}"
            )
            return Order(
                symbol=bar.symbol,
                side=Side.BUY,
                qty=qty,
                order_type=OrderType.MARKET,
            )

        # Short entry: price above VWAP
        if (
            self.allow_short
            and deviation_pct > self.deviation_threshold
            and rsi_val > self.rsi_sell_min
        ):
            self._entry_price = bar.close
            self._stop_price = bar.close * (1 + self.stop_pct)
            qty = min(self.max_position, 1)
            log.info(
                f"VWAP ENTRY short {bar.symbol}: close={bar.close:.2f} "
                f"vwap={self._current_vwap:.2f} dev={deviation_pct:.2f}% RSI={rsi_val:.1f}"
            )
            return Order(
                symbol=bar.symbol,
                side=Side.SELL,
                qty=qty,
                order_type=OrderType.MARKET,
            )

        return None
