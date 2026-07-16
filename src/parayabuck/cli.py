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
@click.option("--source", default="alpaca", type=click.Choice(["alpaca", "csv"]),
              help="Data source: alpaca (API) or csv (local file)")
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
        bars_per_year = _bars_per_year(tf)
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


def _bars_per_year(timeframe: str) -> int:
    """Estimate bars per year for a given timeframe."""
    trading_days = 252
    mapping = {
        "1m": trading_days * 390,
        "5m": trading_days * 78,
        "15m": trading_days * 26,
        "30m": trading_days * 13,
        "1h": trading_days * 7,
        "1d": trading_days,
    }
    return mapping.get(timeframe, trading_days)


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

    # Run: poll latest bars, feed to strategy, execute orders
    import time
    from datetime import UTC, datetime, timedelta

    strat.on_init()
    click.echo("Strategy started. Press Ctrl+C to stop.\n")

    try:
        while True:
            # Get latest bars (last 50 for EMA warmup)
            end_dt = datetime.now(UTC)
            start_dt = end_dt - timedelta(days=5)
            bars = data_source.get_bars(sym, tf, start_dt, end_dt)

            if bars:
                latest_bar = bars[-1]
                broker.update_market_price(sym, latest_bar.close, latest_bar.timestamp)
                position = broker.get_position(sym)

                order = strat.on_bar(latest_bar, position)
                if order is not None:
                    order = risk_mgr.check_order(order, broker.get_account())
                    if order is not None:
                        broker.submit_order(order)

                # Print status
                acct = broker.get_account()
                pos = acct.get_position(sym) if sym in acct.positions else broker.get_position(sym)
                click.echo(
                    f"{latest_bar.timestamp.strftime('%H:%M')} | "
                    f"{sym} ${latest_bar.close:.2f} | "
                    f"Pos: {pos.qty} @ ${pos.avg_price:.2f} | "
                    f"Equity: ${acct.equity:,.2f}"
                )

            time.sleep(60)  # poll every minute
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
