from __future__ import annotations

import os
import sys
from typing import Any

import click

from parayabuck.config import load_config
from parayabuck.data.base import DataSource
from parayabuck.logger import setup_logging
from parayabuck.models import Bar
from parayabuck.strategy.base import Strategy


def _load_strategy(name: str, params: dict[str, Any]) -> Strategy:
    if name == "ema_crossover":
        from parayabuck.strategy.ema_crossover import EMACrossover

        return EMACrossover(**params)
    elif name == "mean_reversion":
        from parayabuck.strategy.mean_reversion import MeanReversion

        return MeanReversion(**params)
    elif name == "pairs_trading":
        from parayabuck.strategy.pairs_trading import PairsTrading

        return PairsTrading(**params)
    elif name == "multi_factor":
        from parayabuck.strategy.multi_factor import MultiFactorMomentum

        return MultiFactorMomentum(**params)
    elif name == "vwap_reversion":
        from parayabuck.strategy.vwap import VWAPReversion

        return VWAPReversion(**params)
    else:
        raise click.ClickException(f"Unknown strategy: {name}")


def _get_alpaca_credentials() -> tuple[str, str]:
    key = os.getenv("ALPACA_API_KEY")
    secret = os.getenv("ALPACA_SECRET_KEY")
    if not key or not secret:
        raise click.ClickException(
            "Alpaca API credentials not found. Set ALPACA_API_KEY and ALPACA_SECRET_KEY in .env"
        )
    return key, secret


@click.group()
@click.option("--config", "-c", default="config/settings.yaml", help="Config file path")
@click.option("--log-level", default="INFO", help="Log level (DEBUG/INFO/WARNING/ERROR)")
@click.pass_context
def cli(ctx: click.Context, config: str, log_level: str) -> None:
    """ParayaBuck — Python trading bot (Alpaca, US stocks)."""
    setup_logging(level=log_level)
    ctx.ensure_object(dict)
    ctx.obj["config"] = load_config(config)


@cli.command()
@click.option("--strategy", "-s", default=None, help="Strategy name (overrides config)")
@click.option("--symbol", "-y", default=None, help="Symbol (overrides config)")
@click.option("--timeframe", "-t", default=None, help="Timeframe (overrides config)")
@click.option("--start", default=None, help="Start date (YYYY-MM-DD)")
@click.option("--end", default=None, help="End date (YYYY-MM-DD)")
@click.option(
    "--source",
    default="alpaca",
    type=click.Choice(["alpaca", "csv"]),
    help="Data source: alpaca (API) or csv (local file)",
)
@click.option("--metrics/--no-metrics", default=True, help="Show detailed performance metrics")
@click.option("--plot/--no-plot", default=False, help="Generate and save plots to output/")
@click.pass_context
def backtest(
    ctx: click.Context,
    strategy: str | None,
    symbol: str | None,
    timeframe: str | None,
    start: str | None,
    end: str | None,
    source: str,
    metrics: bool,
    plot: bool,
) -> None:
    """Run a backtest on historical data."""
    from parayabuck.backtest.engine import BacktestEngine
    from parayabuck.execution.paper import PaperBroker
    from parayabuck.risk.manager import RiskManager

    cfg = ctx.obj["config"]

    sym = symbol or cfg.backtest.symbol
    tf = timeframe or cfg.backtest.timeframe
    strat_name = strategy or cfg.strategy.name
    strat_params = cfg.strategy.params
    start_date = start or cfg.backtest.start
    end_date = end or cfg.backtest.end

    # Data source
    if source == "alpaca":
        from parayabuck.data.alpaca import AlpacaDataSource

        key, secret = _get_alpaca_credentials()
        data_source: DataSource = AlpacaDataSource(key, secret)
    else:
        from parayabuck.data.historical import HistoricalCSVSource

        data_source = HistoricalCSVSource()

    broker = PaperBroker(cfg)
    risk_mgr = RiskManager(cfg)
    strat = _load_strategy(strat_name, strat_params)

    engine = BacktestEngine(cfg, data_source, broker, risk_mgr)
    result = engine.run(strat, sym, tf, start_date, end_date)

    click.echo(result.summary())
    if result.trades:
        click.echo("\nLast trades:")
        click.echo(result.trade_table())

    if metrics:
        from parayabuck.analytics.metrics import calculate_metrics
        from parayabuck.models import Trade as TradeModel

        trade_models = [
            TradeModel(
                timestamp=t["timestamp"],
                symbol=result.symbol,
                side=t["side"],
                qty=t["qty"],
                price=t["price"],
                realized_pnl=t["pnl"],
            )
            for t in result.trades
        ]
        bars_per_year = _bars_per_year(tf, sym)
        m = calculate_metrics(
            equity_curve=result.equity_curve or [cfg.account.starting_balance],
            trades=trade_models,
            starting_balance=cfg.account.starting_balance,
            bars_per_year=bars_per_year,
        )
        click.echo(m.summary())

    if plot:
        from parayabuck.analytics.plots import save_all_plots

        trades_pnl = [t["pnl"] for t in result.trades]
        save_all_plots(result.equity_curve, trades_pnl)
        click.echo("\nPlots saved to output/")


def _bars_per_year(timeframe: str, symbol: str | None = None) -> int:
    """Estimate bars per year for a given timeframe.

    Crypto trades 24/7 (365 days), US stocks ~6.5h sessions (252 days).
    """
    from parayabuck.utils import is_crypto

    days = 365 if (symbol and is_crypto(symbol)) else 252
    minutes_per_bar = {
        "1m": 1,
        "5m": 5,
        "15m": 15,
        "30m": 30,
        "1h": 60,
        "1d": 1440,
    }.get(timeframe, 60)
    if minutes_per_bar >= 1440:
        return days
    mins_per_day = 1440 if (symbol and is_crypto(symbol)) else 390
    return int(days * mins_per_day / minutes_per_bar)


@cli.command()
@click.option("--strategy", "-s", default=None, help="Strategy name")
@click.option("--symbol", "-y", default=None, help="Symbol")
@click.option("--timeframe", "-t", default=None, help="Timeframe")
@click.pass_context
def paper(
    ctx: click.Context, strategy: str | None, symbol: str | None, timeframe: str | None
) -> None:
    """Run paper trading via Alpaca (real paper account, simulated data feed)."""
    from parayabuck.data.alpaca import AlpacaDataSource
    from parayabuck.execution.alpaca import AlpacaBroker
    from parayabuck.risk.manager import RiskManager

    cfg = ctx.obj["config"]

    sym = symbol or cfg.backtest.symbol
    tf = timeframe or cfg.backtest.timeframe
    strat_name = strategy or cfg.strategy.name
    strat_params = cfg.strategy.params

    key, secret = _get_alpaca_credentials()
    broker = AlpacaBroker(key, secret, paper=cfg.alpaca.paper)
    risk_mgr = RiskManager(cfg)
    strat = _load_strategy(strat_name, strat_params)
    data_source = AlpacaDataSource(key, secret)

    click.echo(f"Paper trading: {strat_name} on {sym} {tf}")
    click.echo("Connecting to Alpaca paper account...")

    account = broker.get_account()
    click.echo(f"Account equity: ${account.equity:,.2f} | Cash: ${account.balance:,.2f}")

    import time
    from datetime import UTC, datetime, timedelta

    # Warm up the strategy with enough history so indicators are primed and the
    # first live bar can actually produce a signal (previously only the latest
    # bar was fed, so indicators never had enough data to fire).
    strat.on_init()
    warmup_days = 5
    warmup_start = datetime.now(UTC) - timedelta(days=warmup_days)
    warmup_bars = data_source.get_bars(sym, tf, warmup_start, datetime.now(UTC))
    if warmup_bars:
        pos = broker.get_position(sym)
        for wb in warmup_bars:
            broker.update_market_price(sym, wb.close, wb.timestamp)
            strat.on_bar(wb, pos if pos.is_flat else broker.get_position(sym))
        click.echo(f"Warmed up with {len(warmup_bars)} bars.")

    click.echo("Strategy started. Press Ctrl+C to stop.\n")

    # Poll roughly at the timeframe cadence (default 60s for 1m).
    tf_minutes = {"1m": 1, "5m": 5, "15m": 15, "30m": 30, "1h": 60, "1d": 1440}.get(tf, 1)
    poll_seconds = max(tf_minutes * 60, 15)

    try:
        while True:
            end_dt = datetime.now(UTC)
            start_dt = end_dt - timedelta(days=2)
            bars = data_source.get_bars(sym, tf, start_dt, end_dt)

            if bars:
                latest_bar = bars[-1]
                broker.update_market_price(sym, latest_bar.close, latest_bar.timestamp)
                position = broker.get_position(sym)

                order = strat.on_bar(latest_bar, position)
                pending = strat.pop_pending_orders() if hasattr(strat, "pop_pending_orders") else []
                for od in ([order] if order is not None else []) + list(pending):
                    checked = risk_mgr.check_order(od, broker.get_account())
                    if checked is not None:
                        broker.submit_order(checked)

                acct = broker.get_account()
                pos = acct.get_position(sym)
                click.echo(
                    f"{latest_bar.timestamp.strftime('%H:%M')} | "
                    f"{sym} ${latest_bar.close:.2f} | "
                    f"Pos: {pos.qty} @ ${pos.avg_price:.2f} | "
                    f"Equity: ${acct.equity:,.2f}"
                )

                risk_mgr.tick_cooldown()

            time.sleep(poll_seconds)
    except KeyboardInterrupt:
        click.echo("\nStopping...")
        strat.on_stop()


@cli.command()
@click.option("--strategy", "-s", default=None, help="Strategy name")
@click.option("--symbol", "-y", default=None, help="Symbol")
@click.pass_context
def live(ctx: click.Context, strategy: str | None, symbol: str | None) -> None:
    """Run live trading with real money — NOT YET IMPLEMENTED."""
    click.echo("Live trading is not yet implemented.")
    click.echo("Complete paper trading validation first.")
    sys.exit(1)


@cli.command()
@click.option(
    "--timeframe",
    "-t",
    default="15m",
    help="Bar timeframe (1m/5m/15m/30m/1h). 15m+ recommended — on 1m bars ATR is "
    "tiny and the stop floor dominates, inverting risk:reward.",
)
@click.option("--poll", default=60, help="Poll interval in seconds")
@click.option("--max-positions", default=5, help="Max concurrent positions")
@click.option("--symbols", "-y", default=None, help="Comma-separated symbols (overrides config)")
@click.option("--risk-pct", default=0.02, help="Risk per trade as fraction of equity")
@click.option("--stop-loss", default=0.04, help="Hard stop-loss percentage")
@click.option("--take-profit", default=0.06, help="Take-profit percentage")
@click.option("--trail", default=0.03, help="Trailing stop percentage")
@click.option("--paper/--real", default=True, help="Paper or real account (default: paper)")
@click.pass_context
def run(
    ctx: click.Context,
    timeframe: str,
    poll: int,
    max_positions: int,
    symbols: str | None,
    risk_pct: float,
    stop_loss: float,
    take_profit: float,
    trail: float,
    paper: bool,
) -> None:
    """Start the live trading bot — fire and forget mode.

    The bot will:
      1. Connect to Alpaca paper account
      2. Fetch data for all configured symbols
      3. Detect market regime per symbol → pick best strategy
      4. Continuously poll for new data
      5. Auto-buy when strategy signals
      6. Auto-sell on stop-loss, take-profit, trailing stop, or exit signal
      7. Re-check regime every 30 min, rotate strategies if market changes

    Just run it and let it work. Press Ctrl+C to stop.
    """
    from parayabuck.data.alpaca import AlpacaDataSource
    from parayabuck.execution.alpaca import AlpacaBroker
    from parayabuck.execution.live_engine import LiveTradingEngine

    cfg = ctx.obj["config"]
    key, secret = _get_alpaca_credentials()

    if symbols:
        sym_list = [s.strip().upper() for s in symbols.split(",")]
    else:
        sym_list = list(cfg.symbols.keys())

    broker = AlpacaBroker(key, secret, paper=paper)
    data_source = AlpacaDataSource(key, secret)

    engine = LiveTradingEngine(
        broker=broker,
        data_source=data_source,
        symbols=sym_list,
        timeframe=timeframe,
        poll_interval=poll,
        max_positions=max_positions,
        risk_pct=risk_pct,
        stop_loss_pct=stop_loss,
        take_profit_pct=take_profit,
        trail_pct=trail,
    )

    engine.run()


@cli.command()
@click.option("--timeframe", "-t", default="15m", help="Timeframe for backtest")
@click.option("--start", default="2025-01-01", help="Start date (YYYY-MM-DD)")
@click.option("--end", default="2025-06-30", help="End date (YYYY-MM-DD)")
@click.option("--max-symbols", default=8, help="Max symbols to trade")
@click.option("--plot/--no-plot", default=False, help="Save equity curve plot")
@click.pass_context
def auto(
    ctx: click.Context,
    timeframe: str,
    start: str,
    end: str,
    max_symbols: int,
    plot: bool,
) -> None:
    """Auto-pilot: selects symbols, detects regime, picks strategy, runs backtest.

    This is the fully automated mode — you don't need to choose strategies
    or symbols manually. The bot:

      1. Fetches data for all configured symbols
      2. Runs diversification analysis (sector + correlation filter)
      3. Detects market regime per symbol (trend / range / crisis)
      4. Selects the best strategy per regime
      5. Allocates capital equally across selected symbols
      6. Runs backtest per symbol with ATR/Kelly position sizing
      7. Reports combined portfolio results
    """
    from parayabuck.backtest.auto_pilot import AutoPilotRunner
    from parayabuck.data.alpaca import AlpacaDataSource

    cfg = ctx.obj["config"]
    key, secret = _get_alpaca_credentials()
    data_source = AlpacaDataSource(key, secret)

    click.echo("AUTO-PILOT MODE")
    click.echo("=" * 60)
    click.echo(f"  Timeframe:     {timeframe}")
    click.echo(f"  Period:        {start} → {end}")
    click.echo(f"  Max symbols:   {max_symbols}")
    click.echo(f"  Starting cash: ${cfg.account.starting_balance:,.2f}")
    click.echo("=" * 60)
    click.echo("")

    runner = AutoPilotRunner(
        config=cfg,
        data_source=data_source,
        timeframe=timeframe,
        max_symbols=max_symbols,
    )

    result = runner.run(start, end, cfg.account.starting_balance)

    click.echo(result.summary())

    if plot and result.equity_curve:
        from parayabuck.analytics.plots import save_all_plots

        all_pnls = [s["pnl"] for s in result.per_symbol]
        save_all_plots(result.equity_curve, all_pnls)
        click.echo("\nPlots saved to output/")


@cli.command()
@click.pass_context
def account(ctx: click.Context) -> None:
    """Show Alpaca account info."""
    from parayabuck.execution.alpaca import AlpacaBroker

    key, secret = _get_alpaca_credentials()
    broker = AlpacaBroker(key, secret, paper=ctx.obj["config"].alpaca.paper)
    acct = broker.get_account()

    click.echo(f"Equity:       ${acct.equity:,.2f}")
    click.echo(f"Cash:         ${acct.balance:,.2f}")
    click.echo(f"Realized PnL: ${acct.realized_pnl:,.2f}")
    click.echo(f"Positions:    {len(acct.positions)}")
    for sym, pos in acct.positions.items():
        click.echo(f"  {sym}: {pos.qty} @ ${pos.avg_price:.2f}")


@cli.command()
@click.pass_context
def strategies(ctx: click.Context) -> None:
    """List available strategies."""
    click.echo("Available strategies:")
    click.echo("")
    click.echo("  ema_crossover     EMA 9/21 crossover (basic, for testing)")
    click.echo("  mean_reversion    Bollinger Bands + RSI mean reversion (low vol)")
    click.echo("  pairs_trading     Statistical arbitrage / pairs (market neutral)")
    click.echo("  multi_factor      Multi-factor momentum scoring (trending markets)")
    click.echo("  vwap_reversion    VWAP intraday reversion (intraday)")
    click.echo("")
    click.echo("Usage: parayabuck backtest -s <strategy_name> ...")
    click.echo("      parayabuck paper   -s <strategy_name> ...")


@cli.command()
@click.pass_context
def diversify(ctx: click.Context) -> None:
    """Run diversification analysis on the symbol universe."""
    from parayabuck.data.alpaca import AlpacaDataSource
    from parayabuck.portfolio.diversification import DiversificationEngine

    cfg = ctx.obj["config"]
    key, secret = _get_alpaca_credentials()
    data = AlpacaDataSource(key, secret)

    click.echo("Fetching recent data for all symbols...")
    bars_by_symbol: dict[str, list[Bar]] = {}
    for sym in cfg.symbols:
        try:
            bars = data.get_bars(sym, "1d", "2025-01-01", "2025-06-30")
            bars_by_symbol[sym] = bars
            click.echo(f"  {sym}: {len(bars)} bars")
        except Exception as e:
            click.echo(f"  {sym}: ERROR — {e}")

    engine = DiversificationEngine()
    result = engine.select(bars_by_symbol=bars_by_symbol)

    click.echo("")
    click.echo(f"Selected ({len(result.selected)}): {', '.join(result.selected)}")
    click.echo("")
    click.echo("Sector breakdown:")
    for sector, count in sorted(result.sector_counts.items()):
        click.echo(f"  {sector}: {count}")
    click.echo("")
    if result.rejected:
        click.echo(f"Rejected ({len(result.rejected)}):")
        for sym, reason in result.rejected:
            click.echo(f"  {sym}: {reason}")

    click.echo("")
    click.echo(engine.get_correlation_report())


def main() -> None:
    cli()


if __name__ == "__main__":
    main()
