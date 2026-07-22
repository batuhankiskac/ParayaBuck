from __future__ import annotations

from collections import deque
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from parayabuck.logger import get_logger
from parayabuck.models import Bar, Order, OrderType, Position, Side
from parayabuck.strategy.base import Strategy

log = get_logger("parayabuck.strategy.orb")

_ET = ZoneInfo("America/New_York")


class OpeningRangeBreakout(Strategy):
    """Opening Range Breakout (ORB) — intraday momentum strategy.

    Defines the "opening range" as the first N minutes after the session open
    (default 15 min). After the opening range closes:

      - If price breaks ABOVE the opening-range high -> BUY (long breakout).
      - If price breaks BELOW the opening-range low -> SELL (short, if allowed).

    Exit:
      - Stop-loss: opposite end of the opening range (a tight, structurally
        meaningful stop — the level being broken).
      - Take-profit: `risk_reward` x stop distance, OR end-of-day forced close.
      - Optional trailing: once trade moves favorably by `trail_activate_pct`,
        trail at `trail_pct` to capture trend days.

    The opening-range high/low are psychologically important levels — institutional
    "opening prints" accumulate around them, so a clean break often trends into
    the close. This is one of the oldest intraday strategies and still produces
    profitable signals on liquid US equities (especially large-cap tech) when
    filtered by ATR / pre-market range.

    Params:
        opening_minutes: Length of the opening range in minutes (default 15).
            Common choices: 5, 15, 30, 60.
        risk_reward: Take-profit ratio (default 2.0).
        stop_at_range_end: If True, stop is placed at the opposite end of the
            opening range (default True). If False, uses a fixed % stop.
        fixed_stop_pct: Stop-loss % from entry when stop_at_range_end=False
            (default 1.0).
        trail_pct: Trailing stop distance once trade is in profit (default 1.0%).
        trail_activate_pct: Min % gain before trailing activates (default 1.0).
        allow_short: Allow short entries (default False).
        max_position: Max shares (default 100).
        force_eod_close: Close any open position near the close (default True).
        eod_minutes_before_close: Minutes before 16:00 ET to force-close
            (default 10).
    """

    name = "orb"

    def __init__(self, **params: Any) -> None:
        super().__init__(
            opening_minutes=params.get("opening_minutes", 15),
            risk_reward=params.get("risk_reward", 2.0),
            stop_at_range_end=params.get("stop_at_range_end", True),
            fixed_stop_pct=params.get("fixed_stop_pct", 1.0),
            trail_pct=params.get("trail_pct", 1.0),
            trail_activate_pct=params.get("trail_activate_pct", 1.0),
            allow_short=params.get("allow_short", False),
            max_position=params.get("max_position", 100),
            force_eod_close=params.get("force_eod_close", True),
            eod_minutes_before_close=params.get("eod_minutes_before_close", 10),
        )
        self.opening_minutes = int(self.params["opening_minutes"])
        self.risk_reward = float(self.params["risk_reward"])
        self.stop_at_range_end = bool(self.params["stop_at_range_end"])
        self.fixed_stop_pct = float(self.params["fixed_stop_pct"]) / 100
        self.trail_pct = float(self.params["trail_pct"]) / 100
        self.trail_activate_pct = float(self.params["trail_activate_pct"]) / 100
        self.allow_short = bool(self.params["allow_short"])
        self.max_position = int(self.params["max_position"])
        self.force_eod_close = bool(self.params["force_eod_close"])
        self.eod_minutes_before_close = int(self.params["eod_minutes_before_close"])

        self._session_date: datetime | None = None
        self._range_high: float | None = None
        self._range_low: float | None = None
        self._range_closed: bool = False
        self._bars_in_range: int = 0
        # Track the bar timeframe (minutes) so we know when the opening range
        # has elapsed. Default to 15-min bars -> 1 bar = 15 minutes.
        self._bar_minutes: int = 15
        self._closes: deque[float] = deque(maxlen=200)

        # Position management state.
        self._entry_price: float = 0.0
        self._stop_price: float = 0.0
        self._target_price: float = 0.0
        self._trail_high: float = 0.0  # highest price since entry (for longs)
        self._trail_low: float = 0.0  # lowest price since entry (for shorts)
        self._trailing_active: bool = False

    def _et(self, ts: datetime) -> datetime:
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=ZoneInfo("UTC"))
        return ts.astimezone(_ET)

    def _reset_session(self, date: datetime) -> None:
        self._session_date = date
        self._range_high = None
        self._range_low = None
        self._range_closed = False
        self._bars_in_range = 0

    def _minutes_from_open(self, bar: Bar) -> int:
        t = self._et(bar.timestamp)
        return (t.hour - 9) * 60 + (t.minute - 30)

    def _minutes_to_close(self, bar: Bar) -> int:
        t = self._et(bar.timestamp)
        return (16 * 60) - (t.hour * 60 + t.minute)

    def _infer_bar_minutes(self, bar: Bar, prev_bar: Bar | None) -> int:
        if prev_bar is None:
            # Default to 15 if we can't infer yet; will correct on next bar.
            return self._bar_minutes
        delta = (bar.timestamp - prev_bar.timestamp).total_seconds() / 60
        if delta > 0:
            return int(delta)
        return self._bar_minutes

    def on_bar(self, bar: Bar, position: Position) -> Order | None:
        self._bars_seen += 1
        self._closes.append(bar.close)

        bar_date = self._et(bar.timestamp).replace(hour=0, minute=0, second=0, microsecond=0)
        if self._session_date != bar_date:
            self._reset_session(bar_date)

        # Track opening range
        if not self._range_closed:
            if self._range_high is None:
                self._range_high = bar.high
                self._range_low = bar.low
            else:
                assert self._range_high is not None
                assert self._range_low is not None
                self._range_high = max(self._range_high, bar.high)
                self._range_low = min(self._range_low, bar.low)
            self._bars_in_range += 1
            # Close the range once we've covered at least `opening_minutes`.
            if self._minutes_from_open(bar) >= self.opening_minutes:
                self._range_closed = True

        # Force EOD close.
        if (
            self.force_eod_close
            and self._minutes_to_close(bar) <= self.eod_minutes_before_close
        ):
            if position.is_long:
                log.info(f"ORB EOD close long {bar.symbol} @ {bar.close:.2f}")
                return Order(
                    symbol=bar.symbol,
                    side=Side.SELL,
                    qty=position.qty,
                    order_type=OrderType.MARKET,
                )
            if position.is_short:
                log.info(f"ORB EOD close short {bar.symbol} @ {bar.close:.2f}")
                return Order(
                    symbol=bar.symbol,
                    side=Side.BUY,
                    qty=abs(position.qty),
                    order_type=OrderType.MARKET,
                )

        # Manage existing position.
        if position.is_long:
            # Update trailing high-water.
            if self._trail_high == 0.0:
                self._trail_high = bar.close
            else:
                self._trail_high = max(self._trail_high, bar.close)

            # Check hard stop and target first.
            if bar.close <= self._stop_price:
                log.info(
                    f"ORB stop long {bar.symbol}: close={bar.close:.2f} "
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
                    f"ORB target long {bar.symbol}: close={bar.close:.2f} "
                    f"target={self._target_price:.2f}"
                )
                return Order(
                    symbol=bar.symbol,
                    side=Side.SELL,
                    qty=position.qty,
                    order_type=OrderType.MARKET,
                )

            # Trailing stop: once we've achieved `trail_activate_pct` gain,
            # trail at `trail_pct` below the high-water.
            if not self._trailing_active and self._entry_price > 0:
                gain = (self._trail_high - self._entry_price) / self._entry_price
                if gain >= self.trail_activate_pct:
                    self._trailing_active = True
            if self._trailing_active:
                trail_stop = self._trail_high * (1 - self.trail_pct)
                # Trail stop only tightens; never relax below the original stop.
                if trail_stop > self._stop_price:
                    self._stop_price = trail_stop
                    log.debug(
                        f"ORB trail long {bar.symbol}: stop -> {trail_stop:.2f} "
                        f"(high={self._trail_high:.2f})"
                    )
            return None

        if position.is_short:
            if self._trail_low == 0.0:
                self._trail_low = bar.close
            else:
                self._trail_low = min(self._trail_low, bar.close)

            if bar.close >= self._stop_price:
                log.info(
                    f"ORB stop short {bar.symbol}: close={bar.close:.2f} "
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
                    f"ORB target short {bar.symbol}: close={bar.close:.2f} "
                    f"target={self._target_price:.2f}"
                )
                return Order(
                    symbol=bar.symbol,
                    side=Side.BUY,
                    qty=abs(position.qty),
                    order_type=OrderType.MARKET,
                )

            if not self._trailing_active and self._entry_price > 0:
                gain = (self._entry_price - self._trail_low) / self._entry_price
                if gain >= self.trail_activate_pct:
                    self._trailing_active = True
            if self._trailing_active:
                trail_stop = self._trail_low * (1 + self.trail_pct)
                if trail_stop < self._stop_price:
                    self._stop_price = trail_stop
                    log.debug(
                        f"ORB trail short {bar.symbol}: stop -> {trail_stop:.2f} "
                        f"(low={self._trail_low:.2f})"
                    )
            return None

        # No position — look for breakout entry (only after range has closed).
        if not self._range_closed or self._range_high is None or self._range_low is None:
            return None

        # Long breakout: close above opening-range high.
        if bar.close > self._range_high:
            self._entry_price = bar.close
            if self.stop_at_range_end:
                stop_dist = bar.close - self._range_low
            else:
                stop_dist = bar.close * self.fixed_stop_pct
            self._stop_price = bar.close - max(stop_dist, bar.close * 0.005)
            self._target_price = bar.close + stop_dist * self.risk_reward
            self._trail_high = bar.close
            self._trailing_active = False
            qty = min(self.max_position, 1)
            log.info(
                f"ORB ENTRY long {bar.symbol}: close={bar.close:.2f} "
                f"> range_high={self._range_high:.2f} "
                f"stop={self._stop_price:.2f} target={self._target_price:.2f}"
            )
            return Order(
                symbol=bar.symbol,
                side=Side.BUY,
                qty=qty,
                order_type=OrderType.MARKET,
            )

        # Short breakout: close below opening-range low.
        if self.allow_short and bar.close < self._range_low:
            self._entry_price = bar.close
            if self.stop_at_range_end:
                stop_dist = self._range_high - bar.close
            else:
                stop_dist = bar.close * self.fixed_stop_pct
            self._stop_price = bar.close + max(stop_dist, bar.close * 0.005)
            self._target_price = bar.close - stop_dist * self.risk_reward
            self._trail_low = bar.close
            self._trailing_active = False
            qty = min(self.max_position, 1)
            log.info(
                f"ORB ENTRY short {bar.symbol}: close={bar.close:.2f} "
                f"< range_low={self._range_low:.2f} "
                f"stop={self._stop_price:.2f} target={self._target_price:.2f}"
            )
            return Order(
                symbol=bar.symbol,
                side=Side.SELL,
                qty=qty,
                order_type=OrderType.MARKET,
            )

        return None
