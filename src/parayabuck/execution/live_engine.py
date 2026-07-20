from __future__ import annotations

import math
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from parayabuck.data.alpaca import AlpacaDataSource
from parayabuck.execution.alpaca import AlpacaBroker
from parayabuck.execution.bracket import TrailingStopManager
from parayabuck.logger import get_logger
from parayabuck.models import Account, Bar, Order, OrderType, Position, Side
from parayabuck.risk.portfolio import PortfolioRiskManager
from parayabuck.risk.sizer import PositionSizer
from parayabuck.strategy.base import Strategy
from parayabuck.strategy.indicators import atr
from parayabuck.strategy.regime import (
    REGIME_TO_STRATEGY as _CANONICAL_REGIME_MAP,
)
from parayabuck.strategy.regime import (
    MarketRegime,
    RegimeDetector,
)
from parayabuck.utils import is_crypto

log = get_logger("parayabuck.live")


# Single source of truth lives in parayabuck.strategy.regime; mirror it here
# for fast lookup and to make the live engine's intent explicit. Both maps
# MUST stay in sync — the regime module is authoritative, this is a cached
# copy. Auto-pilot imports the same constant.
REGIME_TO_STRATEGY: dict[str, str] = dict(_CANONICAL_REGIME_MAP)

# Strategies that ride trends benefit from a trailing stop (let winners run).
# Mean-reversion / intraday-reversion strategies are better served by a fixed
# ATR stop + take-profit target — using BOTH a trailing stop and an ATR hard
# stop on the same position leads to premature exits (trailing fires before the
# wider ATR stop, locking in small losses before the mean reversion plays out).
TRAILING_STRATEGIES: frozenset[str] = frozenset(
    {"ema_crossover", "multi_factor", "donchian_breakout", "dual_momentum"}
)


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
        "deviation_threshold": 1.0,
        "allow_short": False,
        "max_position": 500,
    },
    "donchian_breakout": {
        "entry_period": 20,
        "exit_period": 10,
        "atr_period": 14,
        "atr_filter_mult": 0.5,
        "allow_short": False,
        "max_position": 500,
    },
    "orb": {
        "opening_minutes": 15,
        "allow_short": False,
        "max_position": 500,
    },
    "gap_fade": {
        "min_gap_pct": 1.5,
        "rsi_extreme": 25,
        "allow_short": False,
        "max_position": 500,
    },
    "dual_momentum": {
        "momentum_period": 252,
        "risk_free_symbol": "BIL",
        "allow_short": False,
        "max_position": 500,
    },
    "sector_rotation": {
        "lookback": 63,
        "top_n": 3,
        "rebalance_bars": 21,
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
    elif name == "donchian_breakout":
        from parayabuck.strategy.donchian import DonchianBreakout

        return DonchianBreakout(**params)
    elif name == "orb":
        from parayabuck.strategy.orb import OpeningRangeBreakout

        return OpeningRangeBreakout(**params)
    elif name == "gap_fade":
        from parayabuck.strategy.gap_fade import GapFade

        return GapFade(**params)
    elif name == "dual_momentum":
        from parayabuck.strategy.dual_momentum import DualMomentum

        return DualMomentum(**params)
    elif name == "sector_rotation":
        from parayabuck.strategy.sector_rotation import SectorRotation

        return SectorRotation(**params)
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
    order_pending: bool = False
    # Regime hysteresis state. Without this, ADX oscillating around its
    # threshold flips the strategy every recheck and resets strategy state
    # (entry/stop/target), churning commissions and abandoning the original
    # exit plan. We track the candidate regime and how many consecutive
    # rechecks have agreed on it; only rotate after `regime_min_stable_cycles`.
    candidate_regime: MarketRegime = MarketRegime.UNKNOWN
    candidate_strategy: str = "none"
    stable_cycles: int = 0
    # Time-based exit: bars since the strategy opened its current position.
    # Engines increment this and force-close if `max_holding_bars` is exceeded
    # (set on the engine; strategy reads via `bars_in_trade`).
    bars_in_trade: int = 0
    last_managed_bar_time: datetime | None = None
    restored_position: bool = False


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
        timeframe: str = "15m",
        poll_interval: int = 60,
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
        max_daily_loss: float = 300.0,
        max_drawdown: float = 1500.0,
        # Global market filter: SPY 200-SMA + VIX proxy. When SPY is below its
        # 200-SMA or VIX is spiked, new long entries are blocked market-wide.
        # This catches broad-market crisis regimes that per-symbol ATR/vol
        # ratios miss — single-name price action lags VIX futures.
        use_global_market_filter: bool = True,
        benchmark_symbol: str = "SPY",
        vix_proxy_symbol: str = "VIXY",
        benchmark_sma_period: int = 200,
        vix_proxy_sma_period: int = 50,
        vix_high_ratio: float = 1.5,
        # Regime hysteresis: a regime must be stable for at least this many
        # re-check cycles before we rotate strategies. Without hysteresis, ADX
        # oscillating around its threshold flaps the strategy on every recheck,
        # resetting strategy state (entry/stop/target) and churning commissions.
        regime_min_stable_cycles: int = 2,
        # Native Alpaca bracket orders: when True, the engine submits entry
        # orders with attached stop-loss + take-profit legs so protective
        # orders live on Alpaca's servers and fire even if the bot crashes.
        # Set to False to fall back to software-only stops (the engine polls
        # and force-closes on stop hit).
        use_native_bracket: bool = True,
        # Scaling out: when > 0, close `scale_out_pct` of the position at TP1
        # and let the rest ride with the trailing stop. Significantly improves
        # expectancy on trend strategies — locks in some profit while keeping
        # upside exposure. 0.0 disables scaling out (all-or-nothing exit).
        scale_out_pct: float = 0.5,
        # Time-based exit: force-close a position after N bars. Mean-reversion
        # strategies especially benefit — "dead money" sitting in a flat
        # position ties up capital that could be deployed elsewhere. 0 = off.
        max_holding_bars: int = 0,
        # Per-strategy max-position cap from settings.yaml's strategy_allocations.
        # When provided, the engine caps the number of concurrent positions per
        # strategy proportional to its allocation. Strategies not in the map
        # get an equal share of the remaining slots. None/empty disables
        # per-strategy caps and falls back to a flat max_positions.
        strategy_allocations: dict[str, float] | None = None,
        max_position_size: float = 500,
        risk_per_trade_amount: float | None = None,
        max_pct_per_position: float = 0.10,
        max_pct_per_sector: float = 0.30,
        min_cash_pct: float = 0.05,
        fractional_shares: bool = True,
        min_stock_order_notional: float = 5.0,
        min_crypto_order_notional: float = 5.0,
        strategy_params_by_name: dict[str, dict[str, Any]] | None = None,
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
        self.max_daily_loss = max_daily_loss
        self.max_drawdown = max_drawdown
        self.regime_recheck_minutes = regime_recheck_minutes

        self.regime_detector = RegimeDetector(sma_period=50)
        self.trailing_stops = TrailingStopManager(trail_pct=trail_pct, min_profit_to_activate=0.005)
        self.sizer = PositionSizer(
            risk_pct=risk_pct,
            max_qty=max_position_size,
            crypto_risk_pct=0.01,
            # Match the engine's stop floor so the sizer's notion of "risk per
            # trade" matches the actual stop distance the engine will enforce.
            # Without this, on low-ATR timeframes (e.g. 1m) the sizer sizes for
            # an ATR*2 stop (~$0.2) while the engine enforces a 4% floor stop
            # (~$9) → real risk is 20-50x larger than intended.
            min_stop_pct=stop_loss_pct,
            min_stop_pct_crypto=crypto_stop_loss_pct,
            atr_multiplier=atr_stop_multiplier,
            atr_tp_multiplier=atr_tp_multiplier,
            # Fractional shares: when the broker supports it (Alpaca does for
            # liquid US equities on market orders), keep fractional qty so
            # small accounts can still size to a $50 risk target on $500+
            # stocks instead of being int-truncated to 0 shares.
            fractional_shares=fractional_shares,
            fixed_risk_amount=risk_per_trade_amount,
            min_stock_order_notional=min_stock_order_notional,
            min_crypto_order_notional=min_crypto_order_notional,
        )
        self.portfolio_risk = PortfolioRiskManager(
            max_pct_per_position=max_pct_per_position,
            max_pct_per_sector=max_pct_per_sector,
            max_positions=max_positions,
            min_cash_pct=min_cash_pct,
            max_daily_loss=max_daily_loss,
            max_drawdown=max_drawdown,
            fractional_shares=fractional_shares,
        )

        # Global market filter (SPY 200-SMA + VIX proxy). Catches broad-market
        # crisis regimes that per-symbol ATR/vol ratios miss.
        from parayabuck.strategy.market_filter import GlobalMarketFilter, MarketFilterState

        self.market_filter = GlobalMarketFilter(
            benchmark_symbol=benchmark_symbol,
            benchmark_sma_period=benchmark_sma_period,
            vix_proxy_symbol=vix_proxy_symbol,
            vix_proxy_sma_period=vix_proxy_sma_period,
            vix_high_ratio=vix_high_ratio,
            enabled=use_global_market_filter,
        )
        self._market_filter_state: MarketFilterState = MarketFilterState(
            spy_above_200sma=True, vix_high=False, vix_value=0.0, allowed=True, reason="warmup"
        )
        self._last_market_filter_check: datetime | None = None

        # Regime hysteresis: track how many consecutive cycles each symbol's
        # regime has been stable for, and what the "candidate" new regime is.
        # Rotate only after `regime_min_stable_cycles` stable readings.
        self.regime_min_stable_cycles = max(1, int(regime_min_stable_cycles))

        # Native bracket orders + scaling out + time-based exit.
        self.use_native_bracket = bool(use_native_bracket)
        self.scale_out_pct = max(0.0, min(1.0, float(scale_out_pct)))
        self.max_holding_bars = max(0, int(max_holding_bars))
        # Per-strategy max-position caps derived from strategy_allocations.
        # Strategies with a higher allocation get more concurrent slots so a
        # 40%-allocated strategy can hold more positions than a 15%-allocated
        # one. When allocations are missing or empty, all strategies share
        # max_positions equally (the legacy behaviour).
        self.strategy_allocations = strategy_allocations or {}
        self.strategy_params_by_name = strategy_params_by_name or {}
        self._strategy_position_caps: dict[str, int] = self._compute_strategy_caps(max_positions)

        # Per-symbol scale-out state: tracks whether we've already scaled out
        # of TP1 for the current position (avoid re-scaling on every bar).
        self._scaled_out: set[str] = set()
        # Per-symbol bracket order tracking: when a native bracket is in
        # flight, the engine doesn't apply its own software stop (the broker
        # handles it). We track which symbols have native brackets so
        # _manage_positions can skip them.
        self._native_bracket_active: set[str] = set()
        self._broker_stop_active: set[str] = set()

        self._runtimes: dict[str, SymbolRuntime] = {
            sym: SymbolRuntime(symbol=sym) for sym in symbols
        }
        self._last_regime_check: datetime | None = None
        self._last_status_print: datetime | None = None
        self._running = False
        self._stop_event = threading.Event()
        self._cycle_count = 0
        # Observability counters accumulated between the five-minute status
        # reports. A poll cycle is not a strategy evaluation unless a new
        # timeframe candle has closed; exposing that distinction prevents a
        # healthy, selective engine from looking stuck.
        self._closed_bars_since_status = 0
        self._signals_since_status = 0
        self._entry_prices: dict[str, float] = {}
        # Daily-loss / drawdown kill-switch state. Once tripped, the engine
        # stops opening NEW entries but keeps managing/exiting existing
        # positions so capital is protected. Resets only on engine restart.
        self._halted: bool = False
        self._halt_reason: str | None = None
        self._last_kill_switch_check: datetime | None = None

    def run(self) -> None:
        """Main loop — runs forever until Ctrl+C."""
        self._running = True
        self._stop_event.clear()
        log.info("=" * 60)
        log.info("  ParayaBuck LIVE TRADING ENGINE — STARTING")
        log.info("=" * 60)
        log.info(f"  Symbols:    {', '.join(self.symbols)}")
        log.info(f"  Timeframe:  {self.timeframe}")
        log.info(f"  Poll:       every {self.poll_interval}s")
        log.info(f"  Max positions: {self.max_positions}")
        log.info(f"  Risk/trade: {self.risk_pct:.1%}")
        log.info(f"  Stop loss:  {self.stop_loss_pct:.1%}")
        log.info(f"  Trailing:   {self.trail_pct:.1%} (trend strategies only)")
        log.info(f"  Take profit:{self.take_profit_pct:.1%}")
        log.info(f"  Daily loss kill-switch: ${self.max_daily_loss:.0f}")
        log.info(f"  Drawdown kill-switch:   ${self.max_drawdown:.0f}")
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
                self._stop_event.wait(self.poll_interval)
        except KeyboardInterrupt:
            log.info("\nStopping engine...")
        finally:
            self._shutdown()

    def stop(self) -> None:
        self._running = False
        self._stop_event.set()

    @staticmethod
    def _bars_per_day(symbol: str, timeframe: str) -> float:
        """Estimate bars produced per calendar day for a symbol/timeframe.

        Used to size warmup lookback windows. Crypto trades 24/7 (1440 min/day),
        US stocks ~6.5h sessions (390 min/day).
        """
        minutes_per_bar = {
            "1m": 1,
            "5m": 5,
            "15m": 15,
            "30m": 30,
            "1h": 60,
            "1d": 1440,
        }.get(timeframe, 60)
        if minutes_per_bar >= 1440:
            return 1.0
        if is_crypto(symbol):
            return 1440.0 / minutes_per_bar
        return 390.0 / minutes_per_bar

    def _fetch_lookback_days(self) -> int:
        """Days of history to fetch per poll cycle.

        We need at least warmup_bars of history to keep the indicator buffer
        full, plus a margin for the weekend gap (US stocks). Fetching 3x the
        warmup window in calendar days gives enough overlap to never lose bars
        between polls while staying well under Alpaca's 10000-bar page limit.
        """
        # Use the stock (non-crypto) bars-per-day as a conservative lower bound;
        # crypto symbols will simply receive more bars than they need, which is
        # cheap and harmless.
        bpd = 390.0 / {
            "1m": 1,
            "5m": 5,
            "15m": 15,
            "30m": 30,
            "1h": 60,
            "1d": 1440,
        }.get(self.timeframe, 60)
        days = max(int(math.ceil(self.warmup_bars / bpd * 3)), 2)
        # Cap: no reason to ever pull more than ~10 days per poll.
        return min(days, 10)

    def _warmup(self) -> None:
        """Fetch initial data and prime all strategies."""
        log.info("Warming up — fetching historical data...")

        for sym in self.symbols:
            try:
                # Size the lookback window so we fetch RECENT bars without
                # hitting the Alpaca 10000-bar limit. The API returns bars
                # ascending from `start`, so an oversized window yields the
                # OLDEST bars and leaves last_bar_time days in the past —
                # which then makes the first live cycle treat thousands of
                # recent bars as "new". Target ~2x warmup_bars with margin.
                bpd = self._bars_per_day(sym, self.timeframe)
                target = self.warmup_bars * 2
                days_back = max(int(math.ceil(target / bpd * 1.2)), 2)
                start = (datetime.now(UTC) - timedelta(days=days_back)).strftime("%Y-%m-%d")

                bars = self.data.get_bars(sym, self.timeframe, start, use_cache=False)
                bars = [bar for bar in bars if self._bar_is_closed(bar)]
                if not bars:
                    log.warning(f"  {sym}: no data received")
                    continue

                rt = self._runtimes[sym]
                rt.bars_buffer = bars[-self.warmup_bars :]
                rt.last_bar_time = bars[-1].timestamp

                self._detect_regime_and_assign_strategy(sym)

                if rt.strategy is not None:
                    rt.strategy.on_init()
                    # Warm indicators with a flat synthetic position. A restored
                    # live position is managed by the engine's broker-resident
                    # protection until it becomes flat; strategy-local entry
                    # state cannot be reconstructed from historical bars.
                    real_pos = self.broker.get_position(sym)
                    flat = Position(symbol=sym)
                    for bar in rt.bars_buffer:
                        rt.strategy.on_bar(bar, flat)
                        rt.strategy.pop_pending_orders()
                    rt.warmup_done = True
                    # If a real position exists, seed trailing stop + entry price
                    # so protective logic works immediately after a restart.
                    if not real_pos.is_flat:
                        self._entry_prices[sym] = real_pos.avg_price
                        self.trailing_stops.set_entry(
                            sym,
                            real_pos.avg_price,
                            Side.BUY if real_pos.is_long else Side.SELL,
                        )
                        rt.restored_position = True
                        self._restore_position_protection(sym, real_pos)
                        log.info(
                            f"  {sym}: resumed open position "
                            f"({'LONG' if real_pos.is_long else 'SHORT'} "
                            f"{abs(real_pos.qty)} @ {real_pos.avg_price:.2f}), "
                            f"stops seeded"
                        )
                    log.info(
                        f"  {sym}: warmed up with {len(rt.bars_buffer)} bars, "
                        f"regime={rt.regime.value}, strategy={rt.strategy_name}"
                    )

                # Protection is independent of whether the current regime has
                # an active strategy. An existing position must never be left
                # unprotected merely because the regime maps to "none".
                if sym not in self._entry_prices:
                    restored = self.broker.get_position(sym)
                    if not restored.is_flat:
                        self._entry_prices[sym] = restored.avg_price
                        self.trailing_stops.set_entry(
                            sym,
                            restored.avg_price,
                            Side.BUY if restored.is_long else Side.SELL,
                        )
                        rt.restored_position = True
                        self._restore_position_protection(sym, restored)

                self.data.clear_cache_for_symbol(sym)

            except Exception as e:
                log.error(f"  {sym}: warmup failed — {e}")

        self._last_regime_check = datetime.now(UTC)
        log.info("Warmup complete.\n")

    def _detect_regime_and_assign_strategy(self, symbol: str) -> None:
        """Detect market regime and assign appropriate strategy.

        Includes hysteresis: a regime change must be observed for at least
        `regime_min_stable_cycles` consecutive rechecks before we rotate.
        This prevents ADX flapping around its threshold from resetting
        strategy state on every recheck.
        """
        rt = self._runtimes[symbol]
        if len(rt.bars_buffer) < 50:
            rt.strategy_name = "none"
            rt.strategy = None
            return

        state = self.regime_detector.detect(rt.bars_buffer)
        new_strategy_name = REGIME_TO_STRATEGY.get(state.regime.value, "none")

        # First-ever detection: just assign without hysteresis so the engine
        # has a working strategy during initial warmup.
        if rt.regime == MarketRegime.UNKNOWN and rt.strategy is None:
            rt.regime = state.regime
            rt.candidate_regime = state.regime
            rt.candidate_strategy = new_strategy_name
            rt.stable_cycles = self.regime_min_stable_cycles
        else:
            # Hysteresis: track how many consecutive cycles the candidate
            # regime has been stable. Only rotate when it exceeds the
            # configured threshold. Same-regime readings reset the counter.
            if state.regime == rt.candidate_regime:
                rt.stable_cycles += 1
            else:
                rt.candidate_regime = state.regime
                rt.candidate_strategy = new_strategy_name
                rt.stable_cycles = 1
            # Update the visible regime only after we've committed to rotate.
            # Until then, `rt.regime` reflects the currently-active strategy's
            # regime so logs don't flap.
            if rt.stable_cycles < self.regime_min_stable_cycles:
                # Not yet stable — keep the current strategy.
                if rt.strategy is not None:
                    return
                # No strategy yet and not stable — wait.
                if new_strategy_name != "none":
                    return
                # regime is "none" — that's a safe no-op, allow it immediately.
            rt.regime = state.regime

        # Same strategy -> no rotation needed.
        if new_strategy_name == rt.strategy_name and rt.strategy is not None:
            return

        # If we'd rotate but a position is open, keep the current strategy
        # until flat — rotating mid-trade resets strategy state and abandons
        # the original exit plan (entry/stop/target).
        if (
            new_strategy_name != rt.strategy_name
            and rt.strategy is not None
            and not self._is_flat(symbol)
        ):
            log.info(
                f"  {symbol}: regime flipped {rt.strategy_name} -> {new_strategy_name} "
                f"but position open; keeping {rt.strategy_name} until flat"
            )
            return

        if new_strategy_name == "none":
            log.info(
                f"  {symbol}: regime={state.regime.value} -> no trading "
                f"(ADX={state.trend_strength:.1f})"
            )
            rt.strategy = None
            rt.strategy_name = "none"
            return

        params = dict(STRATEGY_PARAMS.get(new_strategy_name, {}))
        params.update(self.strategy_params_by_name.get(new_strategy_name, {}))
        # Pass the timeframe's bars-per-day to multi_factor so its 3m/6m
        # momentum windows are rescaled to calendar months rather than being
        # raw bar counts (which on 15m bars made "6m momentum" mean ~2 days).
        if new_strategy_name == "multi_factor":
            params["bars_per_day"] = self._bars_per_day(symbol, self.timeframe)
        rt.strategy = _load_strategy(new_strategy_name, params)
        rt.strategy_name = new_strategy_name
        rt.warmup_done = False

        log.info(
            f"  {symbol}: regime={state.regime.value} "
            f"(ADX={state.trend_strength:.1f}, RSI={state.momentum:.0f}) "
            f"-> strategy={new_strategy_name}"
        )

    def _check_market_filter(self) -> None:
        """Evaluate the global market filter (SPY 200-SMA + VIX proxy).

        Throttled to once per `regime_recheck_minutes` since SPY/VIX bars
        don't move meaningfully between 15s polls and the historical-bar
        fetch is expensive.
        """
        if not self.market_filter.enabled:
            return
        now = datetime.now(UTC)
        if (
            self._last_market_filter_check is not None
            and (now - self._last_market_filter_check).total_seconds()
            < self.regime_recheck_minutes * 60
        ):
            return
        self._last_market_filter_check = now

        try:
            # Daily bars are the natural timeframe for a 200-SMA filter.
            # Fetch ~1.5 years of history to keep the SMA warm across polls.
            start = (now - timedelta(days=540)).strftime("%Y-%m-%d")
            bench_bars: list[Bar] = []
            vix_bars: list[Bar] = []
            try:
                bench_bars = self.data.get_bars(
                    self.market_filter.benchmark_symbol, "1d", start, use_cache=False
                )
            except Exception as e:
                log.warning(f"Market filter: benchmark fetch failed — {e}")
            try:
                vix_bars = self.data.get_bars(
                    self.market_filter.vix_proxy_symbol, "1d", start, use_cache=False
                )
            except Exception as e:
                # VIXY may not be tradable on the account's plan; degrade
                # gracefully to a SPY-only filter.
                log.debug(f"Market filter: VIX proxy fetch failed — {e}")

            bench_bars = [bar for bar in bench_bars if self._bar_is_closed(bar)]
            vix_bars = [bar for bar in vix_bars if self._bar_is_closed(bar)]

            new_state = self.market_filter.evaluate(bench_bars, vix_bars)
            prev_allowed = self._market_filter_state.allowed
            self._market_filter_state = new_state

            if new_state.allowed != prev_allowed:
                if new_state.allowed:
                    log.info(f"Market filter: RISK-ON ({new_state.reason})")
                else:
                    log.warning(f"Market filter: RISK-OFF — {new_state.reason}")
            elif not new_state.allowed:
                log.debug(f"Market filter: still RISK-OFF — {new_state.reason}")
        except Exception as e:
            log.error(f"Market filter check failed: {e}", exc_info=True)

    def _compute_strategy_caps(self, max_positions: int) -> dict[str, int]:
        """Convert `strategy_allocations` weights to per-strategy position caps.

        Each strategy's cap = max(1, round(max_positions * allocation_weight)).
        Strategies not in the allocation map get an equal share of the
        remaining slots. Returns {strategy_name: cap}. When no allocations
        are configured, returns an empty dict (engine falls back to flat
        max_positions).
        """
        if not self.strategy_allocations:
            return {}
        caps: dict[str, int] = {}
        active_strategy_names = set(REGIME_TO_STRATEGY.values()) - {"none"}
        active_allocations = {
            name: weight
            for name, weight in self.strategy_allocations.items()
            if name in active_strategy_names and weight > 0
        }
        total_weight = sum(active_allocations.values()) or 1.0
        for name, weight in active_allocations.items():
            cap = max(1, round(max_positions * weight / total_weight))
            caps[name] = cap
        return caps

    def _strategy_positions_used(self, strategy_name: str, account: Account) -> int:
        """Count how many open positions were opened by `strategy_name`."""
        if not self._strategy_position_caps or strategy_name == "none":
            return 0
        used = 0
        for sym, pos in account.positions.items():
            if pos.is_flat:
                continue
            rt = self._runtimes.get(sym)
            if rt is not None and rt.strategy_name == strategy_name:
                used += 1
        return used

    def _is_flat(self, symbol: str) -> bool:
        """True if we currently hold no position in `symbol`."""
        try:
            return self.broker.get_position(symbol).is_flat
        except Exception as exc:
            log.warning(f"Could not verify position state for {symbol}: {exc}")
            return False

    def _bar_is_closed(self, bar: Bar) -> bool:
        """Never trade on a still-forming REST bar."""
        minutes = {"1m": 1, "5m": 5, "15m": 15, "30m": 30, "1h": 60, "1d": 1440}
        duration = timedelta(minutes=minutes.get(bar.timeframe, 1))
        now = datetime.now(UTC)
        timestamp = bar.timestamp
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=UTC)
        return now >= timestamp.astimezone(UTC) + duration

    def _cycle(self) -> None:
        """One poll cycle: fetch data, check exits, check entries, report."""
        self._cycle_count += 1

        if not self._is_market_open():
            if self._cycle_count % 10 == 1:
                now = datetime.now(UTC)
                log.info(f"Market closed ({now.strftime('%H:%M UTC')}). Waiting...")
            return

        try:
            # Kill-switch check first: if daily-loss / drawdown limit is hit,
            # block NEW entries but still manage existing positions so we can
            # exit them defensively.
            self._check_kill_switch()
            # Global market filter (SPY 200-SMA + VIX). Throttled internally;
            # blocks new longs market-wide when equities are in a downtrend
            # or vol is spiked. Existing positions are still managed.
            self._check_market_filter()

            self._fetch_new_bars()
            self._check_regime_rotation()
            self._manage_positions()
            self._print_status()
        except Exception as e:
            log.error(f"Cycle error: {e}", exc_info=True)

    def _check_kill_switch(self) -> None:
        """Halt new entries if daily loss or drawdown limits are breached.

        Existing positions are still managed (exits allowed) so the engine
        never abandons open risk. This was missing previously — the live engine
        used PortfolioRiskManager which only enforces sector/position limits,
        so the bot kept trading through losses indefinitely.
        """
        # Throttle: account fetch is a network call, no need to do it every
        # 15s poll. Check at most once per minute.
        now = datetime.now(UTC)
        if (
            self._last_kill_switch_check is not None
            and (now - self._last_kill_switch_check).total_seconds() < 60
            and not self._halted
        ):
            return
        self._last_kill_switch_check = now

        try:
            account = self.broker.get_account()
        except Exception as e:
            log.warning(f"Kill-switch check skipped (account fetch failed): {e}")
            return

        if account.daily_pnl <= -self.max_daily_loss:
            reason = (
                f"Daily loss limit reached: daily PnL ${account.daily_pnl:+.2f} "
                f"<= -${self.max_daily_loss:.2f}"
            )
            if not self._halted:
                log.error(f"TRADING HALTED: {reason}")
            self._halted = True
            self._halt_reason = reason
            return

        drawdown = account.peak_equity - account.equity if account.peak_equity > 0 else 0.0
        if drawdown >= self.max_drawdown:
            reason = f"Max drawdown reached: ${drawdown:.2f} >= ${self.max_drawdown:.2f}"
            if not self._halted:
                log.error(f"TRADING HALTED: {reason}")
            self._halted = True
            self._halt_reason = reason
            return

    def _fetch_new_bars(self) -> None:
        """Fetch latest bars for all symbols.

        The lookback window is sized to the timeframe so we don't pull 5 days
        of 1m bars (thousands of rows) every 15s poll — that hammering burns
        through the Alpaca rate limit and provides no benefit since we only
        keep the last ``warmup_bars`` anyway. We fetch roughly 3x the warmup
        window in calendar days, clamped to sane bounds.
        """
        days_back = self._fetch_lookback_days()
        start = (datetime.now(UTC) - timedelta(days=days_back)).strftime("%Y-%m-%d")

        for sym in self.symbols:
            try:
                if not self._is_symbol_tradable(sym):
                    continue

                bars = self.data.get_bars(sym, self.timeframe, start, use_cache=False)
                if not bars:
                    continue

                rt = self._runtimes[sym]
                new_bars: list[Bar] = []

                for bar in bars:
                    if not self._bar_is_closed(bar):
                        continue
                    if rt.last_bar_time is None or bar.timestamp > rt.last_bar_time:
                        new_bars.append(bar)

                if new_bars:
                    self._closed_bars_since_status += len(new_bars)
                    # Update buffer with all new bars so indicators see full history
                    rt.bars_buffer = (rt.bars_buffer + new_bars)[-self.warmup_bars :]
                    rt.last_bar_time = new_bars[-1].timestamp

                    if rt.strategy is not None and not rt.warmup_done:
                        flat = Position(symbol=sym)
                        for bar in rt.bars_buffer:
                            rt.strategy.on_bar(bar, flat)
                            rt.strategy.pop_pending_orders()
                        rt.warmup_done = True
                    elif rt.strategy is not None:
                        # Feed all new bars to strategy for state updates, but only
                        # act on the LATEST bar's signal. This keeps strategy state
                        # (EMA, prev_above, etc.) in sync without spamming orders.
                        # Fetch position ONCE: it cannot change between bars in this
                        # batch (no orders are submitted here), and calling the
                        # broker per bar would make thousands of synchronous API
                        # calls and hang the engine when new_bars is large.
                        position = self.broker.get_position(sym)
                        for bar in new_bars[:-1]:
                            rt.strategy.on_bar(bar, position)

                        # Process only the last bar for potential order execution
                        if not rt.order_pending:
                            self._process_bar(sym, new_bars[-1])

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
        if position.is_flat:
            rt.restored_position = False
        order = rt.strategy.on_bar(bar, position)

        # Collect the primary order (if any) plus multi-leg pending orders.
        orders: list[Order] = []
        if order is not None:
            orders.append(order)
        orders.extend(rt.strategy.pop_pending_orders())

        if not orders:
            return

        self._signals_since_status += len(orders)

        rt.order_pending = True
        try:
            for od in orders:
                current_position = self.broker.get_position(od.symbol)
                if self._is_exit_signal(od, current_position) and rt.restored_position:
                    log.debug(
                        f"Ignoring strategy exit for restored {od.symbol}; "
                        "engine protection remains authoritative until flat"
                    )
                    continue
                if self._is_exit_signal(od, current_position):
                    self._execute_exit(od, current_position, bar)
                else:
                    self._execute_entry(od, current_position, bar)
        finally:
            rt.order_pending = False

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
        if not self._cancel_protective_orders(order.symbol):
            log.error(
                f"Exit signal ignored for {order.symbol}: protective orders remain active"
            )
            return
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
            self._scaled_out.discard(order.symbol)
            self._native_bracket_active.discard(order.symbol)
            self._broker_stop_active.discard(order.symbol)
            rt = self._runtimes.get(order.symbol)
            if rt is not None:
                rt.bars_in_trade = 0
                rt.last_managed_bar_time = None
                rt.restored_position = False
            pnl = self._calc_pnl(order.symbol, result.filled_price or bar.close, position)
            self.sizer.record_trade(order.symbol, pnl)
            log.info(f"EXIT FILLED: {order.symbol} PnL=${pnl:+.2f}")

    def _execute_entry(self, order: Order, position: Position, bar: Bar) -> None:
        """Open a new position with sizing and risk checks."""
        if not position.is_flat:
            return
        self._native_bracket_active.discard(order.symbol)
        self._broker_stop_active.discard(order.symbol)

        if hasattr(self.broker, "has_open_order") and self.broker.has_open_order(order.symbol):
            log.warning(f"Entry skipped ({order.symbol}): broker has an open order")
            return

        # Kill-switch: no new entries once daily-loss / drawdown limit is hit.
        # Existing positions are still managed in _manage_positions so we can
        # exit defensively, but we never add risk while halted.
        if self._halted:
            log.debug(f"Entry skipped ({order.symbol}): trading halted — {self._halt_reason}")
            return

        # Global market filter: when SPY is below its 200-SMA or VIX is
        # spiked, block new long entries market-wide. Shorts are still
        # allowed (the filter is asymmetric by design — long-only bot).
        if (
            not self._market_filter_state.allowed
            and order.side == Side.BUY
            and not is_crypto(order.symbol)
        ):
            log.debug(
                f"Entry skipped ({order.symbol}): market filter RISK-OFF — "
                f"{self._market_filter_state.reason}"
            )
            return

        account = self.broker.get_account()
        active = sum(1 for p in account.positions.values() if not p.is_flat)
        if active >= self.max_positions:
            return

        # Per-strategy cap: a strategy with a 40% allocation shouldn't be
        # allowed to monopolise all max_positions slots. Each strategy gets
        # a sub-cap proportional to its configured allocation.
        rt = self._runtimes.get(order.symbol)
        strategy_name = rt.strategy_name if rt is not None else "none"
        if strategy_name in self._strategy_position_caps:
            cap = self._strategy_position_caps[strategy_name]
            used = self._strategy_positions_used(strategy_name, account)
            if used >= cap:
                log.debug(
                    f"Entry skipped ({order.symbol}): strategy {strategy_name} "
                    f"at cap {used}/{cap}"
                )
                return

        sized = self.sizer.size_order(order, account)
        if sized.qty <= 0:
            return

        # In high-volatility regimes, halve the position size for safety.
        rt = self._runtimes.get(order.symbol)
        if rt is not None and rt.regime == MarketRegime.HIGH_VOLATILITY:
            half_qty = sized.qty / 2
            min_qty = self.sizer.minimum_entry_qty(order.symbol, bar.close)
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
                log.info(f"  {order.symbol}: high-volatility regime → halved qty to {half_qty}")

        checked = self.portfolio_risk.check_order(sized, account, bar.close)
        if checked is None:
            return

        min_entry_qty = self.sizer.minimum_entry_qty(checked.symbol, bar.close)
        if checked.qty < min_entry_qty:
            log.warning(
                f"Entry skipped ({checked.symbol}): quantity {checked.qty} is below "
                f"minimum {min_entry_qty:.8f} (${checked.qty * bar.close:.2f} notional)"
            )
            return

        log.info(
            f"ENTRY SIGNAL: {checked.side.value} {checked.qty} {checked.symbol} "
            f"@ {bar.close:.2f} (strategy={self._runtimes[checked.symbol].strategy_name})"
        )

        # Native bracket: attach stop-loss + take-profit legs so the protective
        # orders live on Alpaca's servers and fire even if the bot crashes.
        # The engine falls back to software stops when native brackets are
        # disabled or when the broker doesn't support them (paper broker).
        crypto = is_crypto(checked.symbol)
        # Compute the protective levels for the assumed entry price (bar.close).
        # _calc_exit_levels needs a Position, but at entry time we don't have
        # one yet; use a thin helper that takes the entry price + side directly.
        if checked.side == Side.BUY:
            stop_price, tp_price = self._calc_exit_levels_for_entry(
                bar.close, crypto, is_long=True
            )
        else:
            stop_price, tp_price = self._calc_exit_levels_for_entry(
                bar.close, crypto, is_long=False
            )

        submit_order = checked
        if (
            self.use_native_bracket
            and hasattr(self.broker, "submit_order")
            and stop_price > 0
            and tp_price > 0
            and not crypto
        ):
            # Augment the order with stop_price + limit_price so the Alpaca
            # broker recognises it as a bracket entry. Paper broker ignores
            # these fields (no native bracket support) and the engine's
            # software stops handle protection.
            submit_order = Order(
                symbol=checked.symbol,
                side=checked.side,
                qty=checked.qty,
                order_type=OrderType.MARKET,
                stop_price=stop_price,
                limit_price=tp_price,
                tif=checked.tif,
            )

        result = self.broker.submit_order(submit_order)
        if result.status.value == "FILLED":
            fill_price = result.filled_price or bar.close
            self.trailing_stops.set_entry(checked.symbol, fill_price, checked.side)
            self._entry_prices[checked.symbol] = fill_price
            if submit_order.stop_price is not None and submit_order.limit_price is not None:
                self._native_bracket_active.add(checked.symbol)
            elif crypto:
                protective = self.broker.submit_crypto_protective_stop(
                    checked.symbol,
                    result.filled_qty or checked.qty,
                    stop_price,
                    position_is_long=checked.side == Side.BUY,
                )
                if protective.status.value in {"REJECTED", "CANCELLED"}:
                    log.error(
                        f"Crypto protection failed for {checked.symbol}; closing entry"
                    )
                    live_position = self.broker.get_position(checked.symbol)
                    self._force_close(checked.symbol, live_position, fill_price)
                    return
                self._broker_stop_active.add(checked.symbol)
            log.info(f"ENTRY FILLED: {checked.symbol} {checked.qty} shares @ ${fill_price:.2f}")
        elif result.filled_qty > 0:
            fill_price = result.filled_price or bar.close
            self._entry_prices[checked.symbol] = fill_price
            self.trailing_stops.set_entry(checked.symbol, fill_price, checked.side)
            if submit_order.stop_price is not None and submit_order.limit_price is not None:
                self._native_bracket_active.add(checked.symbol)
            log.warning(
                f"ENTRY PARTIALLY FILLED: {checked.symbol} {result.filled_qty}/{checked.qty}"
            )

    def _calc_exit_levels_for_entry(
        self, entry_price: float, crypto: bool, is_long: bool
    ) -> tuple[float, float]:
        """Compute stop/TP for an entry that hasn't filled yet.

        Mirrors `_calc_exit_levels` but uses the assumed entry price and
        side, since at entry time we don't yet have a position to inspect.
        """
        if entry_price <= 0:
            return 0.0, 0.0
        stop_pct = self.crypto_stop_loss_pct if crypto else self.stop_loss_pct
        tp_pct = self.crypto_take_profit_pct if crypto else self.take_profit_pct
        # Use ATR if we have bars; fall back to the % floors otherwise. We
        # don't have a symbol context here so we just use the floors — the
        # bracket order's stop/TP are conservative initial levels; the
        # engine's _manage_positions will tighten them via trailing once
        # the position is open.
        if is_long:
            stop, target = entry_price * (1 - stop_pct), entry_price * (1 + tp_pct)
        else:
            stop, target = entry_price * (1 + stop_pct), entry_price * (1 - tp_pct)
        precision = 4 if entry_price < 1 else 2
        return round(stop, precision), round(target, precision)

    def _manage_positions(self) -> None:
        """Check all open positions for exit conditions (ATR-adaptive stops).

        Trailing stops are only applied to trend strategies (see
        ``TRAILING_STRATEGIES``). Mean-reversion / intraday-reversion
        strategies are better served by a fixed ATR stop + take-profit target
        — running BOTH a trailing stop and an ATR hard stop on the same
        position leads to premature exits (the trailing fires before the
        wider ATR stop, locking in small losses before the reversion plays
        out).

        Additional exit layers (in priority order):
          1. Time-based exit: force-close after `max_holding_bars` bars.
          2. Hard ATR stop / TP target (engine-managed when no native bracket).
          3. Trailing stop (trend strategies only).
          4. Scaling out: at TP1, close `scale_out_pct` of the position and
             let the rest ride with the trailing stop. This noticeably
             improves expectancy on trend strategies by locking in some
             profit while keeping upside exposure.
        """
        account = self.broker.get_account()

        for sym, pos in account.positions.items():
            if pos.is_flat:
                continue

            if not self._is_symbol_tradable(sym):
                continue

            rt = self._runtimes.get(sym)
            if rt is None or not rt.bars_buffer:
                continue

            # Track bars-in-trade for time-based exit. The counter resets
            # when the position goes flat (handled in _execute_exit / _force_close).
            current_bar_time = rt.bars_buffer[-1].timestamp
            if rt.last_managed_bar_time != current_bar_time:
                rt.bars_in_trade += 1
                rt.last_managed_bar_time = current_bar_time

            # Time-based exit: force-close if the position has been open
            # longer than `max_holding_bars`. Useful for mean-reversion
            # where "dead money" in a flat position ties up capital.
            if self.max_holding_bars > 0 and rt.bars_in_trade >= self.max_holding_bars:
                log.info(
                    f"TIME EXIT: {sym} held {rt.bars_in_trade} bars "
                    f">= {self.max_holding_bars}"
                )
                current_price = rt.bars_buffer[-1].close
                self._force_close(sym, pos, current_price)
                continue

            # Native bracket legs live at Alpaca. Do not race them with local
            # hard stops, take-profit or scale-out orders.
            if sym in self._native_bracket_active:
                continue

            current_price = rt.bars_buffer[-1].close
            entry = self._entry_prices.get(sym, 0)
            crypto = is_crypto(sym)
            use_trailing = rt.strategy_name in TRAILING_STRATEGIES

            # Compute ATR-based stop/take; fall back to fixed % if ATR unavailable.
            stop_price, take_profit_price = self._calc_exit_levels(sym, entry, pos, crypto)

            # Scaling out: when the price reaches TP1 (the take_profit target)
            # and we haven't scaled out yet, close `scale_out_pct` of the
            # position. The remainder rides with the trailing stop. This
            # converts an all-or-nothing exit into "lock in half, let the
            # rest run" — a meaningful expectancy boost on trend strategies.
            if (
                self.scale_out_pct > 0
                and sym not in self._scaled_out
                and take_profit_price > 0
            ):
                tp_hit = (
                    (pos.is_long and current_price >= take_profit_price)
                    or (pos.is_short and current_price <= take_profit_price)
                )
                if tp_hit and use_trailing:
                    self._scale_out(sym, pos, current_price)
                    # After scaling out, the trailing stop handles the rest.
                    # Don't fully close — let the remainder ride.

            if pos.is_long:
                if use_trailing:
                    self.trailing_stops.update(sym, current_price, Side.BUY)

                if (
                    stop_price > 0
                    and current_price <= stop_price
                    and sym not in self._broker_stop_active
                ):
                    log.warning(
                        f"HARD STOP-LOSS: {sym} @ {current_price:.2f} (stop={stop_price:.2f})"
                    )
                    self._force_close(sym, pos, current_price)
                    continue

                if take_profit_price > 0 and current_price >= take_profit_price:
                    # If we already scaled out, the trailing stop handles the
                    # remainder. Otherwise, the TP closes the whole position.
                    if sym in self._scaled_out or not use_trailing:
                        log.info(
                            f"TAKE PROFIT: {sym} @ {current_price:.2f} "
                            f"(target={take_profit_price:.2f})"
                        )
                        self._force_close(sym, pos, current_price)
                        continue

                if use_trailing:
                    trail_stop = self.trailing_stops.get_stop(sym)
                    if trail_stop and current_price <= trail_stop:
                        log.info(
                            f"TRAILING STOP: {sym} @ {current_price:.2f} (trail={trail_stop:.2f})"
                        )
                        self._force_close(sym, pos, current_price)
            else:
                if use_trailing:
                    self.trailing_stops.update(sym, current_price, Side.SELL)

                if (
                    stop_price > 0
                    and current_price >= stop_price
                    and sym not in self._broker_stop_active
                ):
                    log.warning(
                        f"HARD STOP-LOSS (short): {sym} @ {current_price:.2f} "
                        f"(stop={stop_price:.2f})"
                    )
                    self._force_close(sym, pos, current_price)
                    continue

                if take_profit_price > 0 and current_price <= take_profit_price:
                    if sym in self._scaled_out or not use_trailing:
                        log.info(
                            f"TAKE PROFIT (short): {sym} @ {current_price:.2f} "
                            f"(target={take_profit_price:.2f})"
                        )
                        self._force_close(sym, pos, current_price)
                        continue

                if use_trailing:
                    trail_stop = self.trailing_stops.get_stop(sym)
                    if trail_stop and current_price >= trail_stop:
                        log.info(
                            f"TRAILING STOP (short): {sym} @ {current_price:.2f} "
                            f"(trail={trail_stop:.2f})"
                        )
                        self._force_close(sym, pos, current_price)

    def _scale_out(self, symbol: str, position: Position, price: float) -> None:
        """Close `scale_out_pct` of the position at TP1; let the rest ride.

        Marks the symbol as scaled-out so we don't repeatedly trim on every
        bar that's above TP1. The marker is cleared in _force_close when the
        position fully closes.
        """
        if self.scale_out_pct <= 0 or self.scale_out_pct >= 1:
            return  # config disabled or invalid
        if symbol in self._native_bracket_active or symbol in self._broker_stop_active:
            return
        crypto = is_crypto(symbol)
        remaining_qty = abs(position.qty) * (1 - self.scale_out_pct)
        # Round stocks to whole shares; keep crypto fractional.
        if not crypto:
            remaining_qty = float(int(remaining_qty))
        close_qty = abs(position.qty) - remaining_qty
        min_qty = 0.0001 if crypto else 1
        if close_qty < min_qty:
            return
        side = Side.SELL if position.is_long else Side.BUY
        order = Order(
            symbol=symbol,
            side=side,
            qty=close_qty,
            order_type=OrderType.MARKET,
        )
        result = self.broker.submit_order(order)
        if result.status.value == "FILLED":
            self._scaled_out.add(symbol)
            scale_pnl = (
                self._calc_pnl(symbol, result.filled_price or price, position)
                * self.scale_out_pct
            )
            self.sizer.record_trade(symbol, scale_pnl)
            log.info(
                f"SCALED OUT {self.scale_out_pct:.0%} of {symbol}: "
                f"closed {close_qty} @ ${result.filled_price or price:.2f}, "
                f"remaining {remaining_qty:.4f}"
            )

    def _calc_exit_levels(
        self, symbol: str, entry: float, position: Position, crypto: bool
    ) -> tuple[float, float]:
        """Calculate ATR-adaptive stop-loss and take-profit prices.

        Returns (stop_price, take_profit_price). Falls back to fixed % if ATR
        is unavailable or produces an implausibly small distance.

        CRITICAL: both stop and take-profit get the same min-floor treatment.
        Previously only the stop had a floor (``max(atr*mult, entry*stop_pct)``)
        while the TP was left at ``atr*tp_mult``. On low-ATR timeframes (1m, 5m)
        ATR is a few cents while the stop floor is ~4% away — so the stop was
        40-100x further than the TP, inverting risk:reward to ~1:40. Each stop
        hit then wiped out dozens of small TP wins, producing the steady bleed
        the bot was showing. With matched floors, R:R stays near the intended
        ``atr_tp_multiplier / atr_stop_multiplier`` ratio (default 2:1).
        """
        if entry <= 0:
            return 0.0, 0.0

        stop_pct_fb = self.crypto_stop_loss_pct if crypto else self.stop_loss_pct
        tp_pct_fb = self.crypto_take_profit_pct if crypto else self.take_profit_pct

        rt = self._runtimes.get(symbol)
        if rt is None or len(rt.bars_buffer) < 15:
            if position.is_long:
                return entry * (1 - stop_pct_fb), entry * (1 + tp_pct_fb)
            return entry * (1 + stop_pct_fb), entry * (1 - tp_pct_fb)

        highs = [b.high for b in rt.bars_buffer[-50:]]
        lows = [b.low for b in rt.bars_buffer[-50:]]
        closes = [b.close for b in rt.bars_buffer[-50:]]
        atr_val = atr(highs, lows, closes, 14)

        if atr_val is None or atr_val <= 0:
            if position.is_long:
                return entry * (1 - stop_pct_fb), entry * (1 + tp_pct_fb)
            return entry * (1 + stop_pct_fb), entry * (1 - tp_pct_fb)

        stop_dist = atr_val * self.atr_stop_multiplier
        tp_dist = atr_val * self.atr_tp_multiplier

        # Matched floors: guard against implausibly tight ATR levels on both
        # sides so whipsaw protection doesn't invert R:R. Without a TP floor,
        # a tiny ATR gives a near-zero target while the stop sits at its floor.
        min_stop_dist = entry * stop_pct_fb
        min_tp_dist = entry * tp_pct_fb
        stop_dist = max(stop_dist, min_stop_dist)
        tp_dist = max(tp_dist, min_tp_dist)

        if position.is_long:
            return entry - stop_dist, entry + tp_dist
        return entry + stop_dist, entry - tp_dist

    def _force_close(self, symbol: str, position: Position, price: float) -> None:
        """Force-close a position immediately."""
        if not self._cancel_protective_orders(symbol):
            log.error(f"Close aborted for {symbol}: protective orders could not be cancelled")
            return
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
            self._scaled_out.discard(symbol)
            self._native_bracket_active.discard(symbol)
            self._broker_stop_active.discard(symbol)
            # Reset the bars-in-trade counter so the next position starts fresh.
            rt = self._runtimes.get(symbol)
            if rt is not None:
                rt.bars_in_trade = 0
                rt.last_managed_bar_time = None
                rt.restored_position = False
            log.info(f"CLOSED: {symbol} PnL=${pnl:+.2f}")

    def _cancel_protective_orders(self, symbol: str) -> bool:
        """Cancel broker-side children before a separate market exit."""
        if symbol not in self._native_bracket_active and symbol not in self._broker_stop_active:
            return True
        cancel = getattr(self.broker, "cancel_open_orders", None)
        if cancel is None:
            return False
        try:
            cancelled = bool(cancel(symbol))
        except Exception as exc:
            log.error(f"Protective-order cancellation failed for {symbol}: {exc}")
            return False
        if cancelled:
            self._native_bracket_active.discard(symbol)
            self._broker_stop_active.discard(symbol)
        return cancelled

    def _restore_position_protection(self, symbol: str, position: Position) -> None:
        """Reconcile or recreate broker-side protection after restart."""
        try:
            if self.broker.has_open_order(symbol):
                self._broker_stop_active.add(symbol)
                return
            stop_price, _ = self._calc_exit_levels(
                symbol, position.avg_price, position, is_crypto(symbol)
            )
            protective = self.broker.submit_protective_stop(
                symbol,
                abs(position.qty),
                stop_price,
                position_is_long=position.is_long,
            )
            if protective.status.value in {"REJECTED", "CANCELLED"}:
                raise RuntimeError("broker rejected restored-position stop")
            self._broker_stop_active.add(symbol)
        except Exception as exc:
            log.error(f"Could not protect restored position {symbol}: {exc}; closing it")
            self._force_close(symbol, position, position.avg_price)

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
                # Multi-factor needs bars_per_day to scale its lookback windows.
                # The attribute exists on MultiFactorMomentum but not on the
                # Strategy base class, so we set it via setattr to keep mypy
                # happy without narrowing the type.
                if rt.strategy_name == "multi_factor":
                    setattr(
                        rt.strategy,
                        "bars_per_day",
                        self._bars_per_day(sym, self.timeframe),
                    )
                rt.strategy.on_init()
                flat = Position(symbol=sym)
                for bar in rt.bars_buffer:
                    rt.strategy.on_bar(bar, flat)
                    rt.strategy.pop_pending_orders()
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
            (
                f"  Last 5m scan: {self._closed_bars_since_status} new closed "
                f"{self.timeframe} bars, {self._signals_since_status} strategy signals"
            ),
        ]

        if self._closed_bars_since_status == 0:
            lines.append(
                f"  Waiting: poll cycles only check data; signals are evaluated "
                f"when a new {self.timeframe} candle closes"
            )
        elif self._signals_since_status == 0:
            lines.append("  No entry/exit setup met the active strategy rules in those bars")

        if self._halted:
            lines.append(f"  ⛔ HALTED: {self._halt_reason}")
        else:
            lines.append(
                f"  Kill-switch: daily ${account.daily_pnl:+.2f} / "
                f"-${self.max_daily_loss:.0f}, drawdown ${account.drawdown:.2f} / "
                f"${self.max_drawdown:.0f}"
            )
        mf = self._market_filter_state
        if mf.allowed:
            lines.append("  Market filter: RISK-ON")
        else:
            lines.append(f"  Market filter: ⛔ RISK-OFF — {mf.reason}")

        for sym, pos in account.positions.items():
            if pos.is_flat:
                continue
            direction = "LONG" if pos.is_long else "SHORT"
            rt = self._runtimes.get(sym)
            price = rt.bars_buffer[-1].close if rt and rt.bars_buffer else pos.avg_price
            pnl = (price - pos.avg_price) * pos.qty
            trail = self.trailing_stops.get_stop(sym)
            qty_fmt = (
                f"{abs(pos.qty):.4f}"
                if is_crypto(sym) or not float(abs(pos.qty)).is_integer()
                else f"{abs(pos.qty):.0f}"
            )
            lines.append(
                f"    {sym:6s} {direction:5s} {qty_fmt} @ ${pos.avg_price:.2f} "
                f"→ ${price:.2f}  PnL=${pnl:+.2f}  "
                f"trail={'$' + f'{trail:.2f}' if trail else 'N/A'}"
            )

        active_strats = [
            f"{sym}={rt.strategy_name}"
            for sym, rt in self._runtimes.items()
            if rt.strategy is not None
        ]
        lines.append(f"  Active strategies: {', '.join(active_strats) or 'none'}")
        lines.append("=" * 60)
        log.info("\n".join(lines))
        self._closed_bars_since_status = 0
        self._signals_since_status = 0

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
        try:
            return self.broker.is_market_open()
        except Exception as exc:
            log.warning(f"Could not verify Alpaca market clock: {exc}")
            return False

    def _is_symbol_tradable(self, symbol: str) -> bool:
        """Check if a specific symbol's market is open for trading."""
        if is_crypto(symbol):
            return True
        try:
            return self.broker.is_market_open()
        except Exception as exc:
            log.warning(f"Could not verify market clock for {symbol}: {exc}")
            return False

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
                qty_fmt = (
                    f"{abs(pos.qty):.4f}"
                    if is_crypto(sym) or not float(abs(pos.qty)).is_integer()
                    else f"{abs(pos.qty):.0f}"
                )
                log.info(f"    {sym}: {direction} {qty_fmt} @ ${pos.avg_price:.2f}")
        log.info("=" * 60)
        log.info("NOTE: Open positions remain on Alpaca. They will NOT be auto-closed.")
