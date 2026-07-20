from __future__ import annotations

from collections import deque
from typing import Any

from parayabuck.logger import get_logger
from parayabuck.models import Bar, Order, OrderType, Position, Side
from parayabuck.strategy.base import Strategy
from parayabuck.strategy.indicators import atr

log = get_logger("parayabuck.strategy.donchian")


class DonchianBreakout(Strategy):
    """Donchian Channel Breakout (Turtle-style momentum).

    Entry Long:
      - Close breaks ABOVE the upper Donchian channel (highest high of the
        last `entry_period` bars). The breakout must be a CLOSE, not an
        intrabar touch — close-only filters out wicks that snap back.
      - ATR filter: ATR >= atr_filter_mult * price * 0.01 to confirm there's
        enough volatility to fuel a follow-through. (Optional; disable with
        atr_filter_mult=0.)
      - allow_short=False => only longs.

    Entry Short:
      - Close breaks BELOW the lower channel.
      - allow_short=True.

    Exit:
      - Close breaks the opposite `exit_period` channel (typically shorter —
        e.g. 20-day entry, 10-day exit, the classic Turtle ratio).
      - Stop-loss: ATR * atr_stop_mult from entry (handled by the engine's
        protective stop machinery; the strategy only emits the entry).

    Why Donchian over EMA crossover:
      - Donchian breakouts are a *price-level* event (a new N-bar high) rather
        than a derivative crossover — they fire less often and at moments when
        momentum is genuinely expanding. Combined with the ATR filter, this
        produces fewer whipsaws than EMA(9)/EMA(21) in choppy markets.

    Params:
        entry_period: Donchian lookback for entries (default 20).
        exit_period: Donchian lookback for exits (default 10). Shorter than
            entry so we exit fast when momentum fades.
        atr_period: ATR lookback (default 14).
        atr_filter_mult: Min ATR as a fraction of price (default 0.5%).
            Set to 0 to disable the volatility filter.
        atr_stop_mult: Stop-loss = ATR * this (default 2.0). Recorded on the
            strategy for the engine to read; the engine itself enforces stops.
        allow_short: Allow short entries (default False).
        max_position: Max shares (default 100).
    """

    name = "donchian_breakout"

    def __init__(self, **params: Any) -> None:
        super().__init__(
            entry_period=params.get("entry_period", 20),
            exit_period=params.get("exit_period", 10),
            atr_period=params.get("atr_period", 14),
            atr_filter_mult=params.get("atr_filter_mult", 0.5),
            atr_stop_mult=params.get("atr_stop_mult", 2.0),
            allow_short=params.get("allow_short", False),
            max_position=params.get("max_position", 100),
        )
        self.entry_period = int(self.params["entry_period"])
        self.exit_period = int(self.params["exit_period"])
        self.atr_period = int(self.params["atr_period"])
        self.atr_filter_mult = float(self.params["atr_filter_mult"])
        self.atr_stop_mult = float(self.params["atr_stop_mult"])
        self.allow_short = bool(self.params["allow_short"])
        self.max_position = int(self.params["max_position"])

        buf_len = max(self.entry_period * 4, 200)
        self._highs: deque[float] = deque(maxlen=buf_len)
        self._lows: deque[float] = deque(maxlen=buf_len)
        self._closes: deque[float] = deque(maxlen=buf_len)

    def _donchian_upper(self, period: int) -> float | None:
        if len(self._highs) < period + 1:
            # +1 because we exclude the current bar (a close must break the
            # PREVIOUS N bars' high — using the current bar's high would make
            # the condition trivially true on every new bar).
            return None
        return max(list(self._highs)[-period - 1 : -1])

    def _donchian_lower(self, period: int) -> float | None:
        if len(self._lows) < period + 1:
            return None
        return min(list(self._lows)[-period - 1 : -1])

    def on_bar(self, bar: Bar, position: Position) -> Order | None:
        self._bars_seen += 1
        self._highs.append(bar.high)
        self._lows.append(bar.low)
        self._closes.append(bar.close)

        if len(self._closes) < self.entry_period + 1:
            return None

        # Exit checks come first — exits are unconditional on opposite-channel
        # break, regardless of ATR filter (we must always be able to flatten).
        if position.is_long:
            exit_low = self._donchian_lower(self.exit_period)
            if exit_low is not None and bar.close < exit_low:
                log.info(
                    f"DON EXIT long {bar.symbol}: close={bar.close:.2f} "
                    f"< exit_channel_low={exit_low:.2f} ({self.exit_period}-bar)"
                )
                return Order(
                    symbol=bar.symbol,
                    side=Side.SELL,
                    qty=position.qty,
                    order_type=OrderType.MARKET,
                )
            return None

        if position.is_short:
            exit_high = self._donchian_upper(self.exit_period)
            if exit_high is not None and bar.close > exit_high:
                log.info(
                    f"DON EXIT short {bar.symbol}: close={bar.close:.2f} "
                    f"> exit_channel_high={exit_high:.2f} ({self.exit_period}-bar)"
                )
                return Order(
                    symbol=bar.symbol,
                    side=Side.BUY,
                    qty=abs(position.qty),
                    order_type=OrderType.MARKET,
                )
            return None

        # No position — look for breakout entry.
        entry_high = self._donchian_upper(self.entry_period)
        entry_low = self._donchian_lower(self.entry_period)
        if entry_high is None or entry_low is None:
            return None

        # ATR filter: confirm enough volatility to fuel follow-through.
        atr_val = atr(list(self._highs), list(self._lows), list(self._closes), self.atr_period)
        atr_ok = True
        if self.atr_filter_mult > 0:
            if atr_val is None or atr_val <= 0:
                atr_ok = False
            else:
                atr_ok = atr_val >= self.atr_filter_mult * 0.01 * bar.close

        # Long breakout: close above the upper channel.
        if bar.close > entry_high and atr_ok:
            qty = min(self.max_position, 1)
            atr_str = f"{atr_val:.2f}" if atr_val else "n/a"
            log.info(
                f"DON ENTRY long {bar.symbol}: close={bar.close:.2f} "
                f"> channel_high={entry_high:.2f} ({self.entry_period}-bar) "
                f"ATR={atr_str}"
            )
            return Order(
                symbol=bar.symbol,
                side=Side.BUY,
                qty=qty,
                order_type=OrderType.MARKET,
            )

        # Short breakout: close below the lower channel.
        if self.allow_short and bar.close < entry_low and atr_ok:
            qty = min(self.max_position, 1)
            log.info(
                f"DON ENTRY short {bar.symbol}: close={bar.close:.2f} "
                f"< channel_low={entry_low:.2f} ({self.entry_period}-bar)"
            )
            return Order(
                symbol=bar.symbol,
                side=Side.SELL,
                qty=qty,
                order_type=OrderType.MARKET,
            )

        return None
