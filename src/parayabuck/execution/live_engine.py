from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from parayabuck.data.alpaca import AlpacaDataSource
from parayabuck.execution.alpaca import AlpacaBroker
from parayabuck.execution.bracket import TrailingStopManager
from parayabuck.logger import get_logger
from parayabuck.models import Bar, Order, OrderType, Position, Side
from parayabuck.risk.portfolio import PortfolioRiskManager
from parayabuck.risk.sizer import PositionSizer
from parayabuck.strategy.base import Strategy
from parayabuck.strategy.indicators import atr
from parayabuck.strategy.regime import MarketRegime, RegimeDetector
from parayabuck.utils import is_crypto

log = get_logger("parayabuck.live")


REGIME_TO_STRATEGY: dict[str, str] = {
    "trending_up": "ema_crossover",
    "trending_down": "mean_reversion",
    "ranging": "mean_reversion",
    "low_volatility": "mean_reversion",
    "high_volatility": "mean_reversion",
    "crisis": "none",
    "unknown": "none",
}


STRATEGY_PARAMS: dict[str, dict[str, Any]] = {
    "mean_reversion": {
        "bb_period": 20,
        "bb_std": 2.0,
        "rsi_period": 14,
        "oversold": 30,
        "overbought": 70,
        "allow_short": False,
        "max_position": 500,
    },
    "ema_crossover": {
        "fast_ema": 9,
        "slow_ema": 21,
        "allow_short": False,
    },
    "vwap_reversion": {
        "vwap_period": 30,
        "deviation_threshold": 1.0,
        "allow_short": False,
        "max_position": 500,
    },
}


def _load_strategy(name: str, params: dict[str, Any] | None = None) -> Strategy:
    params = params or {}
    if name == "mean_reversion":
        from parayabuck.strategy.mean_reversion import MeanReversion

        return MeanReversion(**params)
    elif name == "ema_crossover":
        from parayabuck.strategy.ema_crossover import EMACrossover

        return EMACrossover(**params)
    elif name == "vwap_reversion":
        from parayabuck.strategy.vwap import VWAPReversion

        return VWAPReversion(**params)
    elif name == "multi_factor":
        from parayabuck.strategy.multi_factor import MultiFactorMomentum

        return MultiFactorMomentum(**params)
    raise ValueError(f"Unknown strategy: {name}")


@dataclass
class SymbolRuntime:
    """Per-symbol state for live trading."""
    symbol: str
    strategy: Strategy | None = None
    strategy_name: str = "none"
    regime: MarketRegime = MarketRegime.UNKNOWN
    last_bar_time: datetime | None = None
    warmup_done: bool = False
    bars_buffer: list[Bar] = field(default_factory=list)


class LiveTradingEngine:
    """Fully automated live trading engine — fire and forget.

    On startup:
      1. Connects to Alpaca paper account
      2. Fetches warmup data for all symbols
      3. Detects regime per symbol, picks strategy

    On each poll cycle (every N seconds):
      1. Fetches latest bars for all symbols
      2. Feeds new bars to each symbol's strategy
      3. Checks exit conditions (stop-loss, trailing stop, strategy signal)
      4. Checks entry conditions (strategy signal → open position)
      5. Updates trailing stops
      6. Prints status

    Runs forever until Ctrl+C.
    """

    def __init__(
        self,
        broker: AlpacaBroker,
        data_source: AlpacaDataSource,
        symbols: list[str],
        timeframe: str = "1m",
        poll_interval: int = 15,
        warmup_bars: int = 300,
        max_positions: int = 5,
        risk_pct: float = 0.02,
        trail_pct: float = 0.03,
        stop_loss_pct: float = 0.04,
        take_profit_pct: float = 0.06,
        regime_recheck_minutes: int = 30,
        crypto_stop_loss_pct: float = 0.025,
        crypto_take_profit_pct: float = 0.04,
        atr_stop_multiplier: float = 2.0,
        atr_tp_multiplier: float = 4.0,
    ) -> None:
        self.broker = broker
        self.data = data_source
        self.symbols = symbols
        self.timeframe = timeframe
        self.poll_interval = poll_interval
        self.warmup_bars = warmup_bars
        self.max_positions = max_positions
        self.risk_pct = risk_pct
        self.trail_pct = trail_pct
        self.stop_loss_pct = stop_loss_pct
        self.take_profit_pct = take_profit_pct
        self.crypto_stop_loss_pct = crypto_stop_loss_pct
        self.crypto_take_profit_pct = crypto_take_profit_pct
        self.atr_stop_multiplier = atr_stop_multiplier
        self.atr_tp_multiplier = atr_tp_multiplier
        self.regime_recheck_minutes = regime_recheck_minutes

        self.regime_detector = RegimeDetector(sma_period=50)
        self.trailing_stops = TrailingStopManager(
            trail_pct=trail_pct, min_profit_to_activate=0.005
        )
        self.sizer = PositionSizer(
            risk_pct=risk_pct, max_qty=500, crypto_risk_pct=0.01
        )
        self.sizer = PositionSizer(risk_pct=risk_pct, max_qty=500)
        self.portfolio_risk = PortfolioRiskManager(
            max_pct_per_position=0.15,
            max_pct_per_sector=0.35,
            max_positions=max_positions,
        )

        self._runtimes: dict[str, SymbolRuntime] = {
            sym: SymbolRuntime(symbol=sym) for sym in symbols
        }
        self._last_regime_check: datetime | None = None
        self._last_status_print: datetime | None = None
        self._running = False
        self._cycle_count = 0
        self._entry_prices: dict[str, float] = {}

    def run(self) -> None:
        """Main loop — runs forever until Ctrl+C."""
        self._running = True
        log.info("=" * 60)
        log.info("  ParayaBuck LIVE TRADING ENGINE — STARTING")
        log.info("=" * 60)
        log.info(f"  Symbols:    {', '.join(self.symbols)}")
        log.info(f"  Timeframe:  {self.timeframe}")
        log.info(f"  Poll:       every {self.poll_interval}s")
        log.info(f"  Max positions: {self.max_positions}")
        log.info(f"  Risk/trade: {self.risk_pct:.1%}")
        log.info(f"  Stop loss:  {self.stop_loss_pct:.1%}")
        log.info(f"  Trailing:   {self.trail_pct:.1%}")
        log.info(f"  Take profit:{self.take_profit_pct:.1%}")
        log.info("=" * 60)

        account = self.broker.get_account()
        log.info(f"  Account equity: ${account.equity:,.2f}")
        log.info(f"  Cash:           ${account.balance:,.2f}")
        log.info(f"  Open positions: {len(account.positions)}")
        log.info("")

        self._warmup()

        log.info("Engine running. Press Ctrl+C to stop.\n")

        try:
            while self._running:
                self._cycle()
                time.sleep(self.poll_interval)
        except KeyboardInterrupt:
            log.info("\nStopping engine...")
        finally:
            self._shutdown()

    def stop(self) -> None:
        self._running = False

    def _warmup(self) -> None:
        """Fetch initial data and prime all strategies."""
        log.info("Warming up — fetching historical data...")

        days_back = max(self.warmup_bars // 10, 30)
        start = (datetime.now(UTC) - timedelta(days=days_back)).strftime("%Y-%m-%d")

        for sym in self.symbols:
            try:
                bars = self.data.get_bars(sym, self.timeframe, start, use_cache=False)
                if not bars:
                    log.warning(f"  {sym}: no data received")
                    continue

                rt = self._runtimes[sym]
                rt.bars_buffer = bars[-self.warmup_bars :]
                rt.last_bar_time = bars[-1].timestamp

                self._detect_regime_and_assign_strategy(sym)

                if rt.strategy is not None:
                    rt.strategy.on_init()
                    for bar in rt.bars_buffer:
                        rt.strategy.on_bar(bar, Position(symbol=sym))
                    rt.warmup_done = True
                    log.info(
                        f"  {sym}: warmed up with {len(rt.bars_buffer)} bars, "
                        f"regime={rt.regime.value}, strategy={rt.strategy_name}"
                    )

                self.data.clear_cache_for_symbol(sym)

            except Exception as e:
                log.error(f"  {sym}: warmup failed — {e}")

        self._last_regime_check = datetime.now(UTC)
        log.info("Warmup complete.\n")

    def _detect_regime_and_assign_strategy(self, symbol: str) -> None:
        """Detect market regime and assign appropriate strategy."""
        rt = self._runtimes[symbol]
        if len(rt.bars_buffer) < 50:
            rt.strategy_name = "none"
            rt.strategy = None
            return

        state = self.regime_detector.detect(rt.bars_buffer)
        rt.regime = state.regime

        new_strategy_name = REGIME_TO_STRATEGY.get(state.regime.value, "none")

        if new_strategy_name == rt.strategy_name and rt.strategy is not None:
            return

        if new_strategy_name == "none":
            log.info(
                f"  {symbol}: regime={state.regime.value} → no trading "
                f"(ADX={state.trend_strength:.1f})"
            )
            rt.strategy = None
            rt.strategy_name = "none"
            return

        params = STRATEGY_PARAMS.get(new_strategy_name, {})
        rt.strategy = _load_strategy(new_strategy_name, params)
        rt.strategy_name = new_strategy_name
        rt.warmup_done = False

        log.info(
            f"  {symbol}: regime={state.regime.value} "
            f"(ADX={state.trend_strength:.1f}, RSI={state.momentum:.0f}) "
            f"→ strategy={new_strategy_name}"
        )

    def _cycle(self) -> None:
        """One poll cycle: fetch data, check exits, check entries, report."""
        self._cycle_count += 1

        if not self._is_market_open():
            if self._cycle_count % 10 == 1:
                now = datetime.now(UTC)
                log.info(f"Market closed ({now.strftime('%H:%M UTC')}). Waiting...")
            return

        try:
            self._fetch_new_bars()
            self._check_regime_rotation()
            self._manage_positions()
            self._check_new_entries()
            self._print_status()
        except Exception as e:
            log.error(f"Cycle error: {e}", exc_info=True)

    def _fetch_new_bars(self) -> None:
        """Fetch latest bars for all symbols."""
        start = (datetime.now(UTC) - timedelta(days=5)).strftime("%Y-%m-%d")

        for sym in self.symbols:
            try:
                bars = self.data.get_bars(sym, self.timeframe, start, use_cache=False)
                if not bars:
                    continue

                rt = self._runtimes[sym]
                new_bars: list[Bar] = []

                for bar in bars:
                    if rt.last_bar_time is None or bar.timestamp > rt.last_bar_time:
                        new_bars.append(bar)

                if new_bars:
                    rt.bars_buffer = (rt.bars_buffer + new_bars)[-self.warmup_bars :]
                    rt.last_bar_time = new_bars[-1].timestamp

                    if rt.strategy is not None and not rt.warmup_done:
                        for bar in rt.bars_buffer:
                            rt.strategy.on_bar(bar, Position(symbol=sym))
                        rt.warmup_done = True
                    elif rt.strategy is not None:
                        for bar in new_bars:
                            self._process_bar(sym, bar)

                self.data.clear_cache_for_symbol(sym)

            except Exception as e:
                log.error(f"Fetch {sym}: {e}")

    def _process_bar(self, symbol: str, bar: Bar) -> None:
        """Feed a new bar to the strategy and handle any signal."""
        rt = self._runtimes[symbol]
        if rt.strategy is None:
            return

        self.sizer.update_history(symbol, bar.high, bar.low, bar.close)

        position = self.broker.get_position(symbol)
        order = rt.strategy.on_bar(bar, position)

        if order is not None:
            if self._is_exit_signal(order, position):
                self._execute_exit(order, position, bar)
            else:
                self._execute_entry(order, position, bar)

    def _is_exit_signal(self, order: Order, position: Position) -> bool:
        """Check if this order is closing an existing position."""
        if position.is_flat:
            return False
        if order.side == Side.SELL and position.is_long:
            return True
        if order.side == Side.BUY and position.is_short:
            return True
        return False

    def _execute_exit(self, order: Order, position: Position, bar: Bar) -> None:
        """Close a position."""
        order = Order(
            symbol=order.symbol,
            side=order.side,
            qty=abs(position.qty),
            order_type=OrderType.MARKET,
        )
        log.info(
            f"EXIT SIGNAL: {order.side.value} {order.qty} {order.symbol} "
            f"@ {bar.close:.2f} (strategy={self._runtimes[order.symbol].strategy_name})"
        )
        result = self.broker.submit_order(order)
        if result.status.value == "FILLED":
            self.trailing_stops.remove(order.symbol)
            self._entry_prices.pop(order.symbol, None)
            pnl = self._calc_pnl(order.symbol, result.filled_price or bar.close, position)
            self.sizer.record_trade(order.symbol, pnl)
            log.info(f"EXIT FILLED: {order.symbol} PnL=${pnl:+.2f}")

    def _execute_entry(self, order: Order, position: Position, bar: Bar) -> None:
        """Open a new position with sizing and risk checks."""
        if not position.is_flat:
            return

        account = self.broker.get_account()
        active = sum(1 for p in account.positions.values() if not p.is_flat)
        if active >= self.max_positions:
            return

        sized = self.sizer.size_order(order, account)
        if sized.qty <= 0:
            return

        # In high-volatility regimes, halve the position size for safety.
        rt = self._runtimes.get(order.symbol)
        if rt is not None and rt.regime == MarketRegime.HIGH_VOLATILITY:
            half_qty = sized.qty / 2
            crypto = is_crypto(order.symbol)
            min_qty = 0.0001 if crypto else 1
            if half_qty >= min_qty:
                sized = Order(
                    symbol=sized.symbol,
                    side=sized.side,
                    qty=half_qty,
                    order_type=sized.order_type,
                    limit_price=sized.limit_price,
                    stop_price=sized.stop_price,
                    tif=sized.tif,
                )
                log.info(
                    f"  {order.symbol}: high-volatility regime → halved qty to {half_qty}"
                )

        checked = self.portfolio_risk.check_order(sized, account, bar.close)
        if checked is None:
            return

        log.info(
            f"ENTRY SIGNAL: {checked.side.value} {checked.qty} {checked.symbol} "
            f"@ {bar.close:.2f} (strategy={self._runtimes[checked.symbol].strategy_name})"
        )
        result = self.broker.submit_order(checked)
        if result.status.value == "FILLED":
            fill_price = result.filled_price or bar.close
            self.trailing_stops.set_entry(checked.symbol, fill_price, checked.side)
            self._entry_prices[checked.symbol] = fill_price
            log.info(
                f"ENTRY FILLED: {checked.symbol} {checked.qty} shares @ ${fill_price:.2f}"
            )

    def _manage_positions(self) -> None:
        """Check all open positions for exit conditions (ATR-adaptive stops)."""
        account = self.broker.get_account()

        for sym, pos in account.positions.items():
            if pos.is_flat:
                continue

            rt = self._runtimes.get(sym)
            if rt is None or not rt.bars_buffer:
                continue

            current_price = rt.bars_buffer[-1].close
            entry = self._entry_prices.get(sym, 0)
            crypto = is_crypto(sym)

            # Compute ATR-based stop/take; fall back to fixed % if ATR unavailable.
            stop_price, take_profit_price = self._calc_exit_levels(
                sym, entry, pos, crypto
            )

            if pos.is_long:
                self.trailing_stops.update(sym, current_price, Side.BUY)

                if stop_price > 0 and current_price <= stop_price:
                    log.warning(
                        f"HARD STOP-LOSS: {sym} @ {current_price:.2f} "
                        f"(stop={stop_price:.2f})"
                    )
                    self._force_close(sym, pos, current_price)
                    continue

                if take_profit_price > 0 and current_price >= take_profit_price:
                    log.info(
                        f"TAKE PROFIT: {sym} @ {current_price:.2f} "
                        f"(target={take_profit_price:.2f})"
                    )
                    self._force_close(sym, pos, current_price)
                    continue

                trail_stop = self.trailing_stops.get_stop(sym)
                if trail_stop and current_price <= trail_stop:
                    log.info(
                        f"TRAILING STOP: {sym} @ {current_price:.2f} "
                        f"(trail={trail_stop:.2f})"
                    )
                    self._force_close(sym, pos, current_price)
            else:
                self.trailing_stops.update(sym, current_price, Side.SELL)

                if stop_price > 0 and current_price >= stop_price:
                    log.warning(
                        f"HARD STOP-LOSS (short): {sym} @ {current_price:.2f} "
                        f"(stop={stop_price:.2f})"
                    )
                    self._force_close(sym, pos, current_price)
                    continue

                if take_profit_price > 0 and current_price <= take_profit_price:
                    log.info(
                        f"TAKE PROFIT (short): {sym} @ {current_price:.2f} "
                        f"(target={take_profit_price:.2f})"
                    )
                    self._force_close(sym, pos, current_price)
                    continue

                trail_stop = self.trailing_stops.get_stop(sym)
                if trail_stop and current_price >= trail_stop:
                    log.info(
                        f"TRAILING STOP (short): {sym} @ {current_price:.2f} "
                        f"(trail={trail_stop:.2f})"
                    )
                    self._force_close(sym, pos, current_price)

    def _calc_exit_levels(
        self, symbol: str, entry: float, position: Position, crypto: bool
    ) -> tuple[float, float]:
        """Calculate ATR-adaptive stop-loss and take-profit prices.

        Returns (stop_price, take_profit_price). Falls back to fixed % if ATR
        is unavailable or produces an implausibly small distance.
        """
        if entry <= 0:
            return 0.0, 0.0

        rt = self._runtimes.get(symbol)
        if rt is None or len(rt.bars_buffer) < 15:
            stop_pct = self.crypto_stop_loss_pct if crypto else self.stop_loss_pct
            tp_pct = self.crypto_take_profit_pct if crypto else self.take_profit_pct
            if position.is_long:
                return entry * (1 - stop_pct), entry * (1 + tp_pct)
            return entry * (1 + stop_pct), entry * (1 - tp_pct)

        highs = [b.high for b in rt.bars_buffer[-50:]]
        lows = [b.low for b in rt.bars_buffer[-50:]]
        closes = [b.close for b in rt.bars_buffer[-50:]]
        atr_val = atr(highs, lows, closes, 14)

        stop_pct_fb = self.crypto_stop_loss_pct if crypto else self.stop_loss_pct
        tp_pct_fb = self.crypto_take_profit_pct if crypto else self.take_profit_pct

        if atr_val is None or atr_val <= 0:
            if position.is_long:
                return entry * (1 - stop_pct_fb), entry * (1 + tp_pct_fb)
            return entry * (1 + stop_pct_fb), entry * (1 - tp_pct_fb)

        stop_dist = atr_val * self.atr_stop_multiplier
        tp_dist = atr_val * self.atr_tp_multiplier

        # Guard against implausibly tight ATR stops (whipsaw protection).
        min_stop_dist = entry * stop_pct_fb
        stop_dist = max(stop_dist, min_stop_dist)

        if position.is_long:
            return entry - stop_dist, entry + tp_dist
        return entry + stop_dist, entry - tp_dist

    def _force_close(self, symbol: str, position: Position, price: float) -> None:
        """Force-close a position immediately."""
        side = Side.SELL if position.is_long else Side.BUY
        order = Order(
            symbol=symbol,
            side=side,
            qty=abs(position.qty),
            order_type=OrderType.MARKET,
        )
        result = self.broker.submit_order(order)
        if result.status.value == "FILLED":
            self.trailing_stops.remove(symbol)
            pnl = self._calc_pnl(symbol, result.filled_price or price, position)
            self.sizer.record_trade(symbol, pnl)
            self._entry_prices.pop(symbol, None)
            log.info(f"CLOSED: {symbol} PnL=${pnl:+.2f}")

    def _check_new_entries(self) -> None:
        """Check if regime allows new entries for symbols without positions."""
        now = datetime.now(UTC)
        if self._last_regime_check is None:
            self._last_regime_check = now

    def _check_regime_rotation(self) -> None:
        """Periodically re-check regime and rotate strategies if needed."""
        now = datetime.now(UTC)
        if self._last_regime_check is None:
            self._last_regime_check = now
            return

        if (now - self._last_regime_check).total_seconds() < self.regime_recheck_minutes * 60:
            return

        log.info("Re-checking market regimes...")
        for sym in self.symbols:
            rt = self._runtimes[sym]
            if len(rt.bars_buffer) < 50:
                continue
            self._detect_regime_and_assign_strategy(sym)
            if rt.strategy is not None and not rt.warmup_done:
                rt.strategy.on_init()
                for bar in rt.bars_buffer:
                    rt.strategy.on_bar(bar, Position(symbol=sym))
                rt.warmup_done = True

        self._last_regime_check = now

    def _print_status(self) -> None:
        """Print portfolio status every few cycles."""
        now = datetime.now(UTC)
        if self._last_status_print is not None:
            elapsed = (now - self._last_status_print).total_seconds()
            if elapsed < 300:
                return

        self._last_status_print = now
        account = self.broker.get_account()

        lines = [
            "",
            "=" * 60,
            f"  STATUS — {now.strftime('%Y-%m-%d %H:%M UTC')} (cycle #{self._cycle_count})",
            "=" * 60,
            f"  Equity:    ${account.equity:,.2f}",
            f"  Cash:      ${account.balance:,.2f}",
            f"  Daily PnL: ${account.daily_pnl:+.2f}",
            f"  Positions: {len(account.positions)}",
        ]

        for sym, pos in account.positions.items():
            if pos.is_flat:
                continue
            direction = "LONG" if pos.is_long else "SHORT"
            rt = self._runtimes.get(sym)
            price = rt.bars_buffer[-1].close if rt and rt.bars_buffer else pos.avg_price
            pnl = (price - pos.avg_price) * pos.qty
            trail = self.trailing_stops.get_stop(sym)
            lines.append(
                f"    {sym:6s} {direction:5s} {abs(pos.qty):4d} @ ${pos.avg_price:.2f} "
                f"→ ${price:.2f}  PnL=${pnl:+.2f}  "
                f"trail={'$'+f'{trail:.2f}' if trail else 'N/A'}"
            )

        active_strats = [
            f"{sym}={rt.strategy_name}"
            for sym, rt in self._runtimes.items()
            if rt.strategy is not None
        ]
        lines.append(f"  Active strategies: {', '.join(active_strats) or 'none'}")
        lines.append("=" * 60)
        log.info("\n".join(lines))

    def _calc_pnl(self, symbol: str, exit_price: float, position: Position) -> float:
        """Calculate realized PnL for a closing trade."""
        if position.is_flat:
            return 0.0
        return (exit_price - position.avg_price) * position.qty

    def _is_market_open(self) -> bool:
        """Check if any tradable market is open.

        Crypto trades 24/7, so if any configured symbol is a crypto pair,
        the engine stays active. For stock-only setups, US market hours
        (9:30-16:00 ET, Mon-Fri) apply.
        """
        if any(is_crypto(s) for s in self.symbols):
            return True

        from zoneinfo import ZoneInfo

        et = datetime.now(ZoneInfo("America/New_York"))
        if et.weekday() >= 5:
            return False
        market_open = et.replace(hour=9, minute=30, second=0, microsecond=0)
        market_close = et.replace(hour=16, minute=0, second=0, microsecond=0)
        return market_open <= et <= market_close

    def _shutdown(self) -> None:
        """Clean up on shutdown."""
        log.info("\n" + "=" * 60)
        log.info("  ENGINE STOPPED")
        log.info("=" * 60)
        account = self.broker.get_account()
        log.info(f"  Final equity:   ${account.equity:,.2f}")
        log.info(f"  Cash:           ${account.balance:,.2f}")
        log.info(f"  Open positions: {len(account.positions)}")
        for sym, pos in account.positions.items():
            if not pos.is_flat:
                direction = "LONG" if pos.is_long else "SHORT"
                log.info(f"    {sym}: {direction} {abs(pos.qty)} @ ${pos.avg_price:.2f}")
        log.info("=" * 60)
        log.info("NOTE: Open positions remain on Alpaca. They will NOT be auto-closed.")
