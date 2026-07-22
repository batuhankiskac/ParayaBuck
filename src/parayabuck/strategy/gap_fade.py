from __future__ import annotations

from collections import deque
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from parayabuck.logger import get_logger
from parayabuck.models import Bar, Order, OrderType, Position, Side
from parayabuck.strategy.base import Strategy
from parayabuck.strategy.indicators import atr, rsi

log = get_logger("parayabuck.strategy.gap_fade")


class GapFade(Strategy):
    """Gap-fade mean-reversion strategy (intraday).

    A "gap" is an opening print that's meaningfully away from the previous
    close. Gaps that exhaust on the open tend to fade back toward the prior
    close as the day goes on — especially when RSI is at an extreme.

    Entry Long (gap down):
      - Open gaps DOWN by at least `min_gap_pct` from the previous close.
      - RSI <= `rsi_extreme` (oversold capitulation).
      - After entry, target is the previous close ("fade the gap back").

    Entry Short (gap up, allow_short=True):
      - Open gaps UP by at least `min_gap_pct`.
      - RSI >= (100 - rsi_extreme).
      - Target is the previous close.

    Exit:
      - Price reaches the previous close (gap fully faded).
      - OR stop-loss hit (ATR * multiplier).
      - OR end-of-day forced close (intraday strategy — never hold gaps overnight).

    Best on liquid large-caps where overnight news-driven gaps overreact and
    then revert. Avoid on earnings days unless you have explicit event handling.

    Params:
        min_gap_pct: Min gap size as % of previous close (default 1.5).
        rsi_extreme: RSI threshold (default 25 for long / 75 for short).
        rsi_period: RSI lookback (default 14).
        atr_period: ATR lookback for the stop (default 14).
        atr_stop_mult: Stop = ATR * this (default 1.5 — tighter than trend
            strategies because the gap already defines a natural risk level).
        min_stop_pct: Min stop distance as fraction of entry (default 0.01).
        allow_short: Allow short entries on gap-ups (default False).
        max_position: Max shares (default 100).
        force_eod_close: Always close before the session end (default True).
        eod_minutes_before_close: Minutes before 16:00 ET to force-close
            (default 10).
    """

    name = "gap_fade"

    def __init__(self, **params: Any) -> None:
        super().__init__(
            min_gap_pct=params.get("min_gap_pct", 1.5),
            rsi_extreme=params.get("rsi_extreme", 25),
            rsi_period=params.get("rsi_period", 14),
            atr_period=params.get("atr_period", 14),
            atr_stop_mult=params.get("atr_stop_mult", 1.5),
            min_stop_pct=params.get("min_stop_pct", 0.01),
            allow_short=params.get("allow_short", False),
            max_position=params.get("max_position", 100),
            force_eod_close=params.get("force_eod_close", True),
            eod_minutes_before_close=params.get("eod_minutes_before_close", 10),
        )
        self.min_gap_pct = float(self.params["min_gap_pct"]) / 100
        self.rsi_extreme = float(self.params["rsi_extreme"])
        self.rsi_period = int(self.params["rsi_period"])
        self.atr_period = int(self.params["atr_period"])
        self.atr_stop_mult = float(self.params["atr_stop_mult"])
        self.min_stop_pct = float(self.params["min_stop_pct"])
        self.allow_short = bool(self.params["allow_short"])
        self.max_position = int(self.params["max_position"])
        self.force_eod_close = bool(self.params["force_eod_close"])
        self.eod_minutes_before_close = int(self.params["eod_minutes_before_close"])

        self._closes: deque[float] = deque(maxlen=200)
        self._highs: deque[float] = deque(maxlen=200)
        self._lows: deque[float] = deque(maxlen=200)
        # Per-session state: we only allow ONE gap-fade entry per session —
        # if it stops out, the gap has proven not to fade and we wait for the
        # next session rather than re-attacking the same move.
        self._session_date: datetime | None = None
        self._traded_this_session: bool = False
        self._prev_close: float = 0.0
        self._session_open: float = 0.0
        self._entry_price: float = 0.0
        self._stop_price: float = 0.0
        self._target_price: float = 0.0

    def _et(self, ts: datetime) -> datetime:
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=ZoneInfo("UTC"))
        return ts.astimezone(ZoneInfo("America/New_York"))

    def _reset_session(self, date: datetime) -> None:
        self._session_date = date
        self._traded_this_session = False
        self._prev_close = self._closes[-1] if self._closes else 0.0
        self._session_open = 0.0

    def _minutes_to_close(self, bar: Bar) -> int:
        t = self._et(bar.timestamp)
        return int((16 * 60) - (t.hour * 60 + t.minute))

    def _minutes_from_open(self, bar: Bar) -> int:
        t = self._et(bar.timestamp)
        return int((t.hour - 9) * 60 + (t.minute - 30))

    def on_bar(self, bar: Bar, position: Position) -> Order | None:
        self._bars_seen += 1
        self._closes.append(bar.close)
        self._highs.append(bar.high)
        self._lows.append(bar.low)

        bar_date = self._et(bar.timestamp).replace(hour=0, minute=0, second=0, microsecond=0)
        if self._session_date != bar_date:
            self._reset_session(bar_date)

        # Capture the session's opening print.
        if self._session_open == 0.0 and self._minutes_from_open(bar) < 30:
            self._session_open = bar.open

        # Force EOD close (intraday strategy — never hold gaps overnight).
        if (
            self.force_eod_close
            and self._minutes_to_close(bar) <= self.eod_minutes_before_close
        ):
            if position.is_long:
                log.info(f"GAP EOD close long {bar.symbol} @ {bar.close:.2f}")
                return Order(
                    symbol=bar.symbol,
                    side=Side.SELL,
                    qty=position.qty,
                    order_type=OrderType.MARKET,
                )
            if position.is_short:
                log.info(f"GAP EOD close short {bar.symbol} @ {bar.close:.2f}")
                return Order(
                    symbol=bar.symbol,
                    side=Side.BUY,
                    qty=abs(position.qty),
                    order_type=OrderType.MARKET,
                )

        # Manage existing position — stop / target.
        if position.is_long:
            if bar.close <= self._stop_price:
                log.info(
                    f"GAP stop long {bar.symbol}: close={bar.close:.2f} "
                    f"stop={self._stop_price:.2f}"
                )
                return Order(
                    symbol=bar.symbol,
                    side=Side.SELL,
                    qty=position.qty,
                    order_type=OrderType.MARKET,
                )
            if bar.close >= self._target_price:
                log.info(
                    f"GAP target long {bar.symbol}: faded to prev_close "
                    f"{self._target_price:.2f}"
                )
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
                    f"GAP stop short {bar.symbol}: close={bar.close:.2f} "
                    f"stop={self._stop_price:.2f}"
                )
                return Order(
                    symbol=bar.symbol,
                    side=Side.BUY,
                    qty=abs(position.qty),
                    order_type=OrderType.MARKET,
                )
            if bar.close <= self._target_price:
                log.info(
                    f"GAP target short {bar.symbol}: faded to prev_close "
                    f"{self._target_price:.2f}"
                )
                return Order(
                    symbol=bar.symbol,
                    side=Side.BUY,
                    qty=abs(position.qty),
                    order_type=OrderType.MARKET,
                )
            return None

        # No position — look for entry. Only trade within the first 60 minutes
        # of the session; later entries are no longer "gap fades" but random
        # intraday reversals.
        if self._traded_this_session:
            return None
        if self._minutes_from_open(bar) > 60:
            return None
        if self._prev_close <= 0 or self._session_open <= 0:
            return None

        gap_pct = (self._session_open - self._prev_close) / self._prev_close

        closes_list = list(self._closes)
        rsi_val = rsi(closes_list, self.rsi_period)
        if rsi_val is None:
            return None
        atr_val = atr(list(self._highs), list(self._lows), closes_list, self.atr_period)
        if atr_val is None:
            return None

        stop_dist = max(atr_val * self.atr_stop_mult, bar.close * self.min_stop_pct)

        # Gap down + oversold -> fade up to previous close.
        if gap_pct <= -self.min_gap_pct and rsi_val <= self.rsi_extreme:
            self._entry_price = bar.close
            self._stop_price = bar.close - stop_dist
            self._target_price = self._prev_close  # fade the gap back
            # Don't set a target below the stop; if the gap is large, the
            # previous close may be far above and the trade has great R:R.
            self._traded_this_session = True
            qty = min(self.max_position, 1)
            log.info(
                f"GAP ENTRY long {bar.symbol}: gap={gap_pct*100:+.2f}% "
                f"open={self._session_open:.2f} prev_close={self._prev_close:.2f} "
                f"RSI={rsi_val:.1f} stop={self._stop_price:.2f} "
                f"target={self._target_price:.2f}"
            )
            return Order(
                symbol=bar.symbol,
                side=Side.BUY,
                qty=qty,
                order_type=OrderType.MARKET,
            )

        # Gap up + overbought -> fade down to previous close.
        if (
            self.allow_short
            and gap_pct >= self.min_gap_pct
            and rsi_val >= (100 - self.rsi_extreme)
        ):
            self._entry_price = bar.close
            self._stop_price = bar.close + stop_dist
            self._target_price = self._prev_close
            self._traded_this_session = True
            qty = min(self.max_position, 1)
            log.info(
                f"GAP ENTRY short {bar.symbol}: gap={gap_pct*100:+.2f}% "
                f"open={self._session_open:.2f} prev_close={self._prev_close:.2f} "
                f"RSI={rsi_val:.1f} stop={self._stop_price:.2f} "
                f"target={self._target_price:.2f}"
            )
            return Order(
                symbol=bar.symbol,
                side=Side.SELL,
                qty=qty,
                order_type=OrderType.MARKET,
            )

        return None
