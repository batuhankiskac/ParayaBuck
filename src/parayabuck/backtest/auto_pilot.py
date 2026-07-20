from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from tabulate import tabulate

from parayabuck.backtest.engine import BacktestEngine, BacktestResult
from parayabuck.config import Config
from parayabuck.data.base import DataSource
from parayabuck.execution.paper import PaperBroker
from parayabuck.logger import get_logger
from parayabuck.models import Bar
from parayabuck.portfolio.diversification import DiversificationEngine
from parayabuck.risk.manager import RiskManager
from parayabuck.risk.sizer import PositionSizer
from parayabuck.strategy.base import Strategy
from parayabuck.strategy.regime import REGIME_TO_STRATEGY, MarketRegime, RegimeDetector
from parayabuck.utils import is_crypto

log = get_logger("parayabuck.auto")


@dataclass
class SymbolPlan:
    symbol: str
    sector: str
    regime: MarketRegime
    strategy_name: str
    strategy: Strategy | None = None


@dataclass
class AutoPilotResult:
    starting_balance: float
    ending_balance: float
    total_pnl: float
    total_trades: int
    max_drawdown: float
    win_rate: float
    profit_factor: float
    per_symbol: list[dict[str, Any]] = field(default_factory=list)
    equity_curve: list[float] = field(default_factory=list)
    plans: list[SymbolPlan] = field(default_factory=list)

    def summary(self) -> str:
        lines = [
            "",
            "=" * 70,
            "  AUTO-PILOT BACKTEST RESULTS",
            "=" * 70,
            f"  Symbols traded:    {len(self.per_symbol)}",
            f"  Starting balance:  ${self.starting_balance:,.2f}",
            f"  Ending balance:    ${self.ending_balance:,.2f}",
            f"  Total PnL:         ${self.total_pnl:,.2f}",
            f"  Max drawdown:      ${self.max_drawdown:,.2f}",
            f"  Total trades:      {self.total_trades}",
            f"  Win rate:          {self.win_rate:.1%}",
            f"  Profit factor:     {self.profit_factor:.2f}",
            "",
            "  Per-Symbol Breakdown:",
            "  " + "-" * 64,
        ]

        rows = []
        for s in self.per_symbol:
            rows.append(
                [
                    s["symbol"],
                    s["strategy"],
                    s["regime"],
                    s["trades"],
                    f"${s['pnl']:+.2f}",
                    f"{s['win_rate']:.0%}",
                    f"{s['profit_factor']:.2f}",
                ]
            )

        lines.append(
            tabulate(
                rows,
                headers=["Symbol", "Strategy", "Regime", "Trades", "PnL", "Win%", "PF"],
                tablefmt="simple",
            )
        )
        lines.append("=" * 70)
        return "\n".join(lines)


def _load_strategy_by_name(name: str, params: dict[str, Any] | None = None) -> Strategy:
    params = params or {}
    if name == "mean_reversion":
        from parayabuck.strategy.mean_reversion import MeanReversion

        return MeanReversion(**params)
    elif name == "multi_factor":
        from parayabuck.strategy.multi_factor import MultiFactorMomentum

        return MultiFactorMomentum(**params)
    elif name == "vwap_reversion":
        from parayabuck.strategy.vwap import VWAPReversion

        return VWAPReversion(**params)
    elif name == "pairs_trading":
        from parayabuck.strategy.pairs_trading import PairsTrading

        return PairsTrading(**params)
    elif name == "ema_crossover":
        from parayabuck.strategy.ema_crossover import EMACrossover

        return EMACrossover(**params)
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
    else:
        raise ValueError(f"Unknown strategy: {name}")


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
    "multi_factor": {
        "mom_3m_period": 63,
        "mom_6m_period": 126,
        "buy_score": 65,
        "sell_score": 40,
        "max_position": 500,
    },
    "vwap_reversion": {
        "deviation_threshold": 1.0,
        "allow_short": False,
        "max_position": 500,
    },
    "ema_crossover": {
        "fast_ema": 9,
        "slow_ema": 21,
        "allow_short": False,
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


# Auto-pilot uses the canonical map from the regime module so it stays in
# sync with the live engine. Previously auto-pilot routed trending_down to
# mean_reversion while the live engine routed it to "none" — a long-only bot
# buying oversold dips in a downtrend is exactly the falling-knife case we
# want to avoid.
REGIME_TO_STRATEGY = REGIME_TO_STRATEGY  # re-export for backward import compat


class AutoPilotRunner:
    """Auto-pilot: selects symbols, detects regime, picks strategy, runs backtest.

    Workflow:
      1. Fetch daily bars for all configured symbols
      2. Run diversification engine → select best basket
      3. For each selected symbol, fetch intraday bars
      4. Detect market regime from recent history
      5. Map regime → appropriate strategy
      6. Run backtest per symbol with allocated capital
      7. Aggregate results into a portfolio report
    """

    def __init__(
        self,
        config: Config,
        data_source: DataSource,
        timeframe: str = "15m",
        max_symbols: int = 8,
    ) -> None:
        self.config = config
        self.data = data_source
        self.timeframe = timeframe
        self.max_symbols = max_symbols
        self.regime_detector = RegimeDetector(sma_period=50)
        self.diversification = DiversificationEngine(max_total=max_symbols)

    def plan(self, start: str, end: str) -> list[SymbolPlan]:
        """Step 1-4: fetch data, diversify, detect regime, select strategy."""
        log.info("AUTO-PILOT: Planning phase")

        # Step 1: Fetch daily bars for all symbols
        all_symbols = list(self.config.symbols.keys())
        bars_by_symbol: dict[str, list[Bar]] = {}

        for sym in all_symbols:
            try:
                bars = self.data.get_bars(sym, "1d", start, end)
                bars_by_symbol[sym] = bars
                log.info(f"  {sym}: {len(bars)} daily bars")
            except Exception as e:
                log.warning(f"  {sym}: fetch failed — {e}")

        # Step 2: Diversify
        result = self.diversification.select(bars_by_symbol=bars_by_symbol)
        selected = result.selected[: self.max_symbols]
        log.info(f"  Diversification selected: {selected}")

        # Step 3-4: Detect regime per symbol and pick strategy
        plans: list[SymbolPlan] = []
        for sym in selected:
            bars = bars_by_symbol.get(sym, [])
            if len(bars) < 50:
                log.warning(f"  {sym}: not enough bars for regime detection")
                continue

            regime_state = self.regime_detector.detect(bars)
            strategy_name = REGIME_TO_STRATEGY.get(regime_state.regime.value, "none")

            from parayabuck.risk.portfolio import get_sector

            plan = SymbolPlan(
                symbol=sym,
                sector=get_sector(sym),
                regime=regime_state.regime,
                strategy_name=strategy_name,
            )
            plans.append(plan)

            log.info(
                f"  {sym}: regime={regime_state.regime.value} "
                f"ADX={regime_state.trend_strength:.1f} "
                f"→ strategy={strategy_name}"
            )

        return plans

    def run(
        self,
        start: str,
        end: str,
        starting_balance: float | None = None,
    ) -> AutoPilotResult:
        """Full auto-pilot backtest: plan → execute → aggregate."""
        total_balance = starting_balance or self.config.account.starting_balance
        plans = self.plan(start, end)

        # Filter out symbols with no viable strategy
        active_plans = [p for p in plans if p.strategy_name != "none"]
        if not active_plans:
            log.warning("AUTO-PILOT: No viable strategies found. Market may be in crisis/high-vol.")
            return AutoPilotResult(
                starting_balance=total_balance,
                ending_balance=total_balance,
                total_pnl=0.0,
                total_trades=0,
                max_drawdown=0.0,
                win_rate=0.0,
                profit_factor=0.0,
                plans=plans,
            )

        # Allocate capital equally among active symbols
        per_symbol_capital = total_balance / len(active_plans)
        log.info(f"AUTO-PILOT: {len(active_plans)} symbols, ${per_symbol_capital:,.2f} each")

        # Run backtest per symbol
        per_symbol_results: list[dict[str, Any]] = []
        per_symbol_pnl: list[tuple[list[float], float]] = []
        total_pnl = 0.0
        total_trades = 0
        total_wins = 0
        total_closing = 0
        total_gross_profit = 0.0
        total_gross_loss = 0.0
        max_dd = 0.0

        for plan in active_plans:
            try:
                result = self._run_single(plan, per_symbol_capital, start, end)
            except Exception as e:
                log.error(f"  {plan.symbol}: backtest failed — {e}")
                continue

            per_symbol_results.append(
                {
                    "symbol": plan.symbol,
                    "strategy": plan.strategy_name,
                    "regime": plan.regime.value,
                    "trades": result.total_trades,
                    "pnl": result.total_pnl,
                    "win_rate": result.win_rate,
                    "profit_factor": result.profit_factor,
                    "max_drawdown": result.max_drawdown,
                }
            )

            total_pnl += result.total_pnl
            total_trades += result.total_trades
            max_dd = max(max_dd, result.max_drawdown)

            # Approximate win/loss aggregation
            closing = [t for t in result.trades if t["pnl"] != 0]
            wins = [t for t in closing if t["pnl"] > 0]
            losses = [t for t in closing if t["pnl"] < 0]
            total_wins += len(wins)
            total_closing += len(closing)
            total_gross_profit += sum(t["pnl"] for t in wins)
            total_gross_loss += sum(abs(t["pnl"]) for t in losses)

            # Collect per-symbol PnL curves (equity - allocated capital) for
            # later timestamp-aligned aggregation. Indexing by bar number across
            # symbols with different trading hours (crypto 24/7 vs stocks 6.5h)
            # is meaningless, so we align by wall-clock timestamp instead.
            per_symbol_pnl.append((result.equity_curve, per_symbol_capital))

        # Build a timestamp-aligned portfolio equity curve. Each per-symbol
        # backtest ran independently, so we sum PnL contributions; when a symbol
        # has no point at a given timestamp we carry its last known PnL forward.
        all_equity = self._aggregate_equity(per_symbol_pnl, total_balance)

        win_rate = total_wins / total_closing if total_closing > 0 else 0.0
        profit_factor = (
            total_gross_profit / total_gross_loss
            if total_gross_loss > 0
            else float("inf")
            if total_gross_profit > 0
            else 0.0
        )

        return AutoPilotResult(
            starting_balance=total_balance,
            ending_balance=total_balance + total_pnl,
            total_pnl=total_pnl,
            total_trades=total_trades,
            max_drawdown=max_dd,
            win_rate=win_rate,
            profit_factor=profit_factor,
            per_symbol=per_symbol_results,
            equity_curve=all_equity,
            plans=plans,
        )

    def _run_single(
        self,
        plan: SymbolPlan,
        capital: float,
        start: str,
        end: str,
    ) -> BacktestResult:
        """Run backtest for a single symbol with the planned strategy."""
        # Create a sub-config with adjusted starting balance
        from copy import deepcopy

        sub_config = deepcopy(self.config)
        sub_config.account.starting_balance = capital

        # Load strategy
        params = dict(STRATEGY_PARAMS.get(plan.strategy_name, {}))
        # multi_factor's 3m/6m windows are daily-bar defaults; rescale them
        # to the actual backtest timeframe so "6-month momentum" still means
        # six months on 15m / 1h bars too. Without this, on 15m bars the
        # strategy waits for 126 bars (~2 days) and treats that as half a year.
        if plan.strategy_name == "multi_factor":
            minutes_per_bar = {
                "1m": 1,
                "5m": 5,
                "15m": 15,
                "30m": 30,
                "1h": 60,
                "1d": 1440,
            }.get(self.timeframe, 1440)
            if minutes_per_bar >= 1440:
                bpd = 252.0
            elif is_crypto(plan.symbol):
                bpd = 1440.0 / minutes_per_bar
            else:
                bpd = 390.0 / minutes_per_bar
            params["bars_per_day"] = bpd
        strategy = _load_strategy_by_name(plan.strategy_name, params)
        plan.strategy = strategy

        # Create engine components
        broker = PaperBroker(sub_config)
        risk_mgr = RiskManager(sub_config)
        sizer = PositionSizer(
            risk_pct=sub_config.risk.risk_per_trade / capital,
            max_qty=sub_config.risk.max_position_size,
        )

        engine = BacktestEngine(
            config=sub_config,
            data_source=self.data,
            broker=broker,
            risk_manager=risk_mgr,
            position_sizer=sizer,
        )

        log.info(f"  Backtest: {plan.symbol} with {plan.strategy_name} (${capital:,.0f})")
        return engine.run(strategy, plan.symbol, self.timeframe, start, end)

    @staticmethod
    def _aggregate_equity(
        per_symbol_pnl: list[tuple[list[float], float]], total_balance: float
    ) -> list[float]:
        """Aggregate per-symbol equity curves into a portfolio equity curve.

        Each entry is (equity_curve, allocated_capital). The PnL contribution of
        a symbol at step i is `equity_curve[i] - allocated_capital`. Symbols have
        different bar counts (crypto trades 24/7, stocks 6.5h), so we sum by
        step index and carry each symbol's last PnL forward once its curve ends.
        """
        if not per_symbol_pnl:
            return []
        max_len = max(len(ec) for ec, _ in per_symbol_pnl) if per_symbol_pnl else 0
        last_pnl = [0.0] * len(per_symbol_pnl)
        out: list[float] = []
        for i in range(max_len):
            total_pnl_at_i = 0.0
            for s, (ec, cap) in enumerate(per_symbol_pnl):
                if i < len(ec):
                    last_pnl[s] = ec[i] - cap
                total_pnl_at_i += last_pnl[s]
            out.append(total_balance + total_pnl_at_i)
        return out
