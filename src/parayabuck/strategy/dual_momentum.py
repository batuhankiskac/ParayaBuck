from __future__ import annotations

from collections import deque
from typing import Any

from parayabuck.logger import get_logger
from parayabuck.models import Bar, Order, OrderType, Position, Side
from parayabuck.strategy.base import Strategy

log = get_logger("parayabuck.strategy.dual_momentum")


class DualMomentum(Strategy):
    """Dual Momentum (Antonacci) — absolute + relative momentum.

    The strategy alternates between a risky asset (default SPY) and a safe
    asset proxy (default BIL — 1-3 month T-bills). Each rebalance:

      1. Look back `momentum_period` bars (default 252 = 12 months on daily).
      2. Absolute momentum: if SPY's return over the lookback is NEGATIVE,
         go to cash (BIL or just flatten). This is the crisis filter — it
         gets the bot out of equities before drawdowns compound.
      3. Relative momentum: if SPY's return is positive AND greater than
         BIL's, hold SPY. Otherwise hold BIL.

    This is one of the most documented tactical-allocation strategies and
    integrates naturally with the existing 200-SMA regime filter (the
    absolute-momentum leg IS a market-wide filter, just measured as return
    rather than price-vs-MA — the two tend to agree but absolute momentum
    is smoother and harder to whipsaw).

    Note: BIL is a proxy for the risk-free rate; if you can't trade BIL on
    your Alpaca plan, set `risk_free_symbol` to None and the strategy will
    simply flatten instead of buying BIL when relative momentum favours
    bonds. SPY is the default risky symbol but any broad index ETF works.

    Params:
        risky_symbol: The risky asset (default "SPY"). When running on a
            single-symbol backtest, this must equal the symbol being traded.
        risk_free_symbol: The safe-asset proxy (default "BIL"). Set to None
            to flatten instead of buying the safe asset.
        momentum_period: Lookback for momentum (default 252, ~12 months of
            daily bars). On intraday timeframes, pass `bars_per_day` so the
            period is rescaled.
        bars_per_day: Bars per day for the running timeframe. When set, the
            momentum_period is rescaled to calendar months.
        allow_short: Allow shorting the risky asset when momentum is strongly
            negative (default False — Dual Momentum is long/flat, not short).
        max_position: Max shares (default 100).
    """

    name = "dual_momentum"

    def __init__(self, **params: Any) -> None:
        super().__init__(
            risky_symbol=params.get("risky_symbol", "SPY"),
            risk_free_symbol=params.get("risk_free_symbol", "BIL"),
            momentum_period=params.get("momentum_period", 252),
            bars_per_day=params.get("bars_per_day"),
            allow_short=params.get("allow_short", False),
            max_position=params.get("max_position", 100),
        )
        self.risky_symbol = str(self.params["risky_symbol"])
        rf = self.params.get("risk_free_symbol")
        self.risk_free_symbol = str(rf) if rf else None
        # Rescale momentum period for intraday timeframes so "12 months" still
        # means 12 months on 15m / 1h bars, not 12 days.
        daily_bpd = 252.0
        bpd = float(self.params.get("bars_per_day") or daily_bpd)
        self.bars_per_day = bpd
        scale = bpd / daily_bpd if daily_bpd > 0 else 1.0
        self.momentum_period = max(int(round(int(self.params["momentum_period"]) * scale)), 20)
        self.allow_short = bool(self.params["allow_short"])
        self.max_position = int(self.params["max_position"])

        # The strategy consumes both the risky and the risk-free symbols.
        # If risk_free_symbol is None, only the risky symbol is consumed.
        self.symbols = [self.risky_symbol]
        if self.risk_free_symbol:
            self.symbols.append(self.risk_free_symbol)

        buf_len = max(self.momentum_period * 2, 600)
        self._risky_bars: deque[Bar] = deque(maxlen=buf_len)
        self._rf_bars: deque[Bar] = deque(maxlen=buf_len)
        # Last rebalance bar count — we only re-evaluate momentum every
        # `rebalance_interval` bars to avoid churning. Default: re-evaluate
        # every 21 bars (monthly on daily, ~daily on 15m).
        self._rebalance_interval = max(21, self.momentum_period // 12)
        self._bars_since_rebalance: int = 0
        # Current "decision": "risky", "risk_free", or "flat".
        self._current_decision: str = "flat"

    def _momentum(self, bars: deque[Bar]) -> float | None:
        if len(bars) < self.momentum_period + 1:
            return None
        closes = [b.close for b in bars]
        past = closes[-self.momentum_period - 1]
        now = closes[-1]
        if past <= 0:
            return None
        return (now - past) / past

    def on_bar(self, bar: Bar, position: Position) -> Order | None:
        self._bars_seen += 1

        if bar.symbol == self.risky_symbol:
            self._risky_bars.append(bar)
        elif self.risk_free_symbol and bar.symbol == self.risk_free_symbol:
            self._rf_bars.append(bar)
        else:
            return None

        # Only act on risky-symbol bars — that's the asset we're rotating
        # in/out of. Risk-free bars just update the relative-momentum input.
        if bar.symbol != self.risky_symbol:
            return None

        # Rebalance gate: re-evaluate every N bars, not every bar.
        self._bars_since_rebalance += 1
        if self._bars_since_rebalance < self._rebalance_interval:
            return None
        self._bars_since_rebalance = 0

        risky_mom = self._momentum(self._risky_bars)
        if risky_mom is None:
            return None
        rf_mom: float | None = None
        if self.risk_free_symbol:
            rf_mom = self._momentum(self._rf_bars)
        if rf_mom is None:
            rf_mom = 0.0

        # Decision logic:
        #   - risky_mom < 0            -> absolute momentum says cash.
        #   - risky_mom >= 0 and
        #     risky_mom >= rf_mom      -> hold risky.
        #   - risky_mom >= 0 and
        #     risky_mom <  rf_mom      -> hold risk_free (bonds beat stocks).
        #   - allow_short and
        #     risky_mom < -short_thresh-> short risky (rare; DM is long/flat).
        decision = "flat"
        if risky_mom < 0:
            if self.allow_short and risky_mom < -0.08:
                decision = "short_risky"
            else:
                decision = "risk_free" if self.risk_free_symbol else "flat"
        elif risky_mom >= rf_mom:
            decision = "risky"
        else:
            decision = "risk_free" if self.risk_free_symbol else "flat"

        if decision == self._current_decision:
            return None
        self._current_decision = decision

        # Translate decision to order(s), considering current position.
        # The strategy emits orders on the risky_symbol only (the engine
        # executes them one at a time); when going to "risk_free", we also
        # queue a BUY for the risk-free symbol.
        orders: list[Order] = []

        # First, exit current risky position if we're not staying in it.
        if position.is_long and decision != "risky":
            orders.append(
                Order(
                    symbol=self.risky_symbol,
                    side=Side.SELL,
                    qty=position.qty,
                    order_type=OrderType.MARKET,
                )
            )

        # Then, take the new action.
        if decision == "risky" and position.is_flat:
            qty = min(self.max_position, 1)
            orders.append(
                Order(
                    symbol=self.risky_symbol,
                    side=Side.BUY,
                    qty=qty,
                    order_type=OrderType.MARKET,
                )
            )
        elif decision == "risk_free" and self.risk_free_symbol:
            # Buy the risk-free asset on its own leg. The engine will execute
            # this when it processes pending orders.
            qty = min(self.max_position, 1)
            self._pending_orders.append(
                Order(
                    symbol=self.risk_free_symbol,
                    side=Side.BUY,
                    qty=qty,
                    order_type=OrderType.MARKET,
                )
            )
        elif decision == "short_risky" and position.is_flat:
            qty = min(self.max_position, 1)
            orders.append(
                Order(
                    symbol=self.risky_symbol,
                    side=Side.SELL,
                    qty=qty,
                    order_type=OrderType.MARKET,
                )
            )

        # Return the first order for the risky symbol; the rest were queued.
        return orders[0] if orders else None
