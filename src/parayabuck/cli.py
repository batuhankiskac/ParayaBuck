from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import yaml
from dotenv import load_dotenv

from parayabuck.backtest.product import (
    load_csv_bars,
    load_csv_corporate_actions,
    optimize_grid,
    run_product_backtest,
)
from parayabuck.config import Config, load_config
from parayabuck.core.models import Bar, Market
from parayabuck.data import AlpacaHistoricalData
from parayabuck.execution.alpaca import AlpacaBroker
from parayabuck.execution.portfolio import portfolio_risk_limits_from_config
from parayabuck.live_safety import LiveOperatorApproval
from parayabuck.logger import get_logger
from parayabuck.portfolio import portfolio_strategy_from
from parayabuck.portfolio.runtime_service import configuration_digest
from parayabuck.universe import (
    ensure_active_universes,
    load_active_crypto_symbols,
    load_active_symbols,
    validate_active_crypto_universe,
    validate_active_universe,
)
from parayabuck.universe.crypto_scanner import crypto_scanner_config_digest
from parayabuck.universe.scanner import scanner_config_digest
from parayabuck.utils.io import atomic_write

log = get_logger("parayabuck.cli")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="parayabuck", description="ParayaBuck trading bot")
    parser.add_argument("--config", default=None, help="configuration YAML path")
    commands = parser.add_subparsers(dest="command")
    commands.add_parser("web", help="start the local operations console")
    backup = commands.add_parser(
        "backup", help="consistent SQLite backup of the runtime decision database"
    )
    backup.add_argument(
        "--retention-days",
        type=float,
        default=None,
        help="override the configured backup retention (days)",
    )
    authorize = commands.add_parser(
        "authorize-live", help="create a short-lived account/config-bound live approval"
    )
    authorize.add_argument("--operator", required=True)
    authorize.add_argument("--expires-minutes", type=int, default=15)
    backtest = commands.add_parser("backtest", help="run a production-strategy backtest")
    _add_backtest_arguments(backtest, include_cost_stress=True)
    optimize = commands.add_parser("optimize", help="grid search on TRAIN and optional OOS data")
    _add_backtest_arguments(optimize, include_cost_stress=False)
    optimize.add_argument(
        "--grid",
        action="append",
        required=True,
        metavar="NAME=V1,V2",
        help="repeat for each parameter",
    )
    optimize.add_argument("--train-start")
    optimize.add_argument("--train-end")
    optimize.add_argument("--oos-start")
    optimize.add_argument("--oos-end")
    return parser


def _add_backtest_arguments(
    parser: argparse.ArgumentParser,
    *,
    include_cost_stress: bool,
) -> None:
    parser.add_argument("--market", choices=("equity", "crypto"), default="equity")
    parser.add_argument("--strategy", default=None)
    parser.add_argument("--data", help="offline CSV containing OHLCV bars")
    parser.add_argument(
        "--corporate-actions",
        help="optional CSV of explicit equity dividends (split-adjusted prices are expected)",
    )
    parser.add_argument("--symbols", help="comma-separated download candidates")
    parser.add_argument("--candidate-limit", type=int, default=None)
    parser.add_argument("--start")
    parser.add_argument("--end")
    parser.add_argument("--initial-cash", type=float, default=None)
    parser.add_argument("--output-dir", default=None)
    parser.add_argument(
        "--param", action="append", default=[], metavar="NAME=VALUE", help="strategy override"
    )
    if include_cost_stress:
        parser.add_argument(
            "--cost-stress",
            action="store_true",
            help="also run 2x and 3x slippage/spread/crypto-fee assumptions",
        )


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if not arguments:
        from parayabuck.web.app import main as web_main

        web_main()
        return 0
    args = _parser().parse_args(arguments)
    if args.command in {None, "web"}:
        from parayabuck.web.app import main as web_main

        web_main()
        return 0
    config = load_config(args.config)
    if args.command == "authorize-live":
        return _authorize_live(config, args)
    if args.command == "backup":
        return _run_backup(config, args)
    market = Market(args.market)
    start = _date(args.start) if args.start else None
    end = _date(args.end, end_of_day=True) if args.end else None
    bars = _load_bars(config, args, market, start, end)
    corporate_actions = (
        load_csv_corporate_actions(args.corporate_actions) if args.corporate_actions else None
    )
    if args.command == "optimize":
        grid = _parse_grid(args.grid)
        parameters = _strategy_parameters(config, market, args.param)
        payload = optimize_grid(
            config,
            bars,
            market=market,
            grid=grid,
            strategy_name=args.strategy,
            base_parameters=parameters,
            initial_cash=args.initial_cash,
            train_start=_date(args.train_start) if args.train_start else start,
            train_end=_date(args.train_end, end_of_day=True) if args.train_end else end,
            oos_start=_date(args.oos_start) if args.oos_start else None,
            oos_end=_date(args.oos_end, end_of_day=True) if args.oos_end else None,
            output_dir=args.output_dir,
            corporate_actions=corporate_actions,
        )
        print(json.dumps(payload, indent=2, default=str))
        return 0
    parameters = _strategy_parameters(config, market, args.param)
    multipliers = (1.0, 2.0, 3.0) if args.cost_stress else (1.0,)
    for multiplier in multipliers:
        run = run_product_backtest(
            config,
            bars,
            market=market,
            strategy_name=args.strategy,
            parameters=parameters,
            start=start,
            end=end,
            initial_cash=args.initial_cash,
            output_dir=args.output_dir,
            cost_multiplier=multiplier,
            corporate_actions=corporate_actions,
            data_feed=config.alpaca.stock_historical_feed,
        )
        _print_summary(run.report.summary, run.output_dir)
    return 0


def backtest_main(argv: list[str] | None = None) -> int:
    return main(["backtest", *(sys.argv[1:] if argv is None else argv)])


def _authorize_live(config: Config, args: argparse.Namespace) -> int:
    if not config.live.enabled or config.live.hard_dollar_cap <= 0:
        raise SystemExit("live.enabled=true and a positive live.hard_dollar_cap are required")
    if not config.live.operator_approval_path:
        raise SystemExit("live.operator_approval_path is required")
    if not 1 <= args.expires_minutes <= 1440:
        raise SystemExit("--expires-minutes must be between 1 and 1440")
    load_dotenv()
    key = os.getenv("ALPACA_API_KEY", "")
    secret = os.getenv("ALPACA_SECRET_KEY", "")
    hmac_secret = os.getenv(config.live.operator_approval_key_env, "")
    if not key or not secret:
        raise SystemExit("Live Alpaca credentials are required")
    equity_universe = config.universe.equity
    if (
        config.strategy.equity.enabled
        and equity_universe.enabled
        and equity_universe.dynamic_selection_enabled
    ):
        _symbols, status = validate_active_universe(equity_universe)
        if status != "success" and not equity_universe.allow_static_fallback_trading:
            raise SystemExit(
                f"Live authorization requires a valid dynamic equity universe; status={status}"
            )
    crypto_universe = config.universe.crypto
    if (
        config.strategy.crypto.enabled
        and crypto_universe.enabled
        and crypto_universe.dynamic_selection_enabled
    ):
        _symbols, status = validate_active_crypto_universe(crypto_universe)
        if status != "success" and not crypto_universe.allow_static_fallback_trading:
            raise SystemExit(
                f"Live authorization requires a valid dynamic crypto universe; status={status}"
            )
    broker = AlpacaBroker(key, secret, paper=False)
    try:
        broker.account()  # Read-only identity verification against the live endpoint.
        account_suffix = broker.account_suffix
        account_id = broker.account_id or ""
    finally:
        # Temporary live-credential verification sessions must be closed.
        broker.close()
    digests: list[str] = []
    for market, selection, symbols, universe_version in (
        (
            Market.EQUITY,
            config.strategy.equity,
            load_active_symbols(config.universe.equity, log_rejection=False),
            scanner_config_digest(config.universe.equity),
        ),
        (
            Market.CRYPTO,
            config.strategy.crypto,
            load_active_crypto_symbols(config.universe.crypto, log_rejection=False),
            crypto_scanner_config_digest(config.universe.crypto),
        ),
    ):
        if not selection.enabled:
            continue
        if not symbols:
            raise SystemExit(f"No active {market.value} universe is available")
        strategy = portfolio_strategy_from(
            selection.name, selection.parameters, timeframe=selection.timeframe
        )
        digests.append(
            configuration_digest(
                strategy,
                "live",
                selection.maximum_allocation,
                universe_version,
                portfolio_risk_limits_from_config(config.risk),
            )
        )
    now = datetime.now(UTC)
    approval = LiveOperatorApproval(
        operator_id=args.operator,
        account_id=account_id,
        configuration_digests=tuple(digests),
        maximum_dollar_exposure=config.live.hard_dollar_cap,
        approved_at=now,
        expires_at=now + timedelta(minutes=args.expires_minutes),
        nonce=secrets.token_hex(16),
    ).signed(hmac_secret)
    atomic_write(
        Path(config.live.operator_approval_path),
        json.dumps(approval.to_dict(), indent=2, sort_keys=True),
        mode=0o600,
    )
    print(
        f"Live approval written for account ...{account_suffix}; "
        f"expires {approval.expires_at.isoformat()}"
    )
    return 0


def _run_backup(config: Config, args: argparse.Namespace) -> int:
    """Snapshot the runtime decision database with the sqlite3 online backup
    API (safe while the runtime keeps the WAL database open), then prune
    backups older than the retention window."""
    from parayabuck.backup import backup_database, verify_backup

    retention_days = (
        args.retention_days if args.retention_days is not None else config.backup.retention_days
    )
    destination = backup_database(
        config.runtime.decision_db_path,
        config.backup.path,
        retention_days=retention_days,
    )
    if not verify_backup(destination):
        raise SystemExit(f"Backup failed integrity check: {destination}")
    print(f"Backup written: {destination}")
    print(f"Retention: {retention_days:g} day(s); older backups pruned")
    return 0


def _load_bars(
    config: Config,
    args: argparse.Namespace,
    market: Market,
    start: datetime | None,
    end: datetime | None,
) -> list[Bar]:
    if args.data:
        return load_csv_bars(args.data, default_market=market)
    load_dotenv()
    key = os.getenv("ALPACA_API_KEY", "")
    secret = os.getenv("ALPACA_SECRET_KEY", "")
    if not key or not secret:
        raise SystemExit("ALPACA_API_KEY and ALPACA_SECRET_KEY are required for downloads")
    ensure_active_universes(config, key, secret)
    symbols = _candidate_symbols(config, market, args.symbols, args.candidate_limit)
    if not symbols:
        raise SystemExit("No candidate symbols are available")
    selection = config.strategy.equity if market == Market.EQUITY else config.strategy.crypto
    end_at = end or datetime.now(UTC)
    if start is None:
        start_at = end_at - timedelta(days=5 * 365 if market == Market.EQUITY else 2 * 365)
    else:
        # Download enough history before the requested evaluation start so the
        # strategy warmup and the trailing scanner lookback have real bars.
        # run_product_backtest splits warmup from the evaluation window. The
        # session density is anchored at the evaluation start (a 2020 backtest
        # must size its warmup with the 2020 holiday calendar, not today's).
        warmup_days = _warmup_window_days(config, market, end_date=start)
        start_at = start - timedelta(days=warmup_days)
    provider = AlpacaHistoricalData(
        key,
        secret,
        stock_feed=config.alpaca.stock_historical_feed,
        data_delayed=config.alpaca.data_delayed,
    )
    strategy = portfolio_strategy_from(
        selection.name, selection.parameters, timeframe=selection.timeframe
    )
    minimum_bars = strategy.minimum_history_bars
    # Bounded warmup extension: a symbol with fewer pre-start bars than the
    # strategy declares would silently drop out of the early evaluation
    # window. Re-fetch deficient symbols with an extended window (at most
    # three attempts), then surface any residual shortfall loudly instead of
    # letting the backtest run with truncated history.
    by_symbol: dict[str, list[Bar]] = {symbol: [] for symbol in symbols}
    attempts = 0
    try:
        while attempts < 3:
            attempts += 1
            expected_per_symbol = max(
                1,
                int((end_at - start_at).total_seconds() / 86_400)
                * (24 if selection.timeframe == "1h" else 1),
            )
            batch_size = max(1, min(100, 9_000 // expected_per_symbol))
            for offset in range(0, len(symbols), batch_size):
                batch = symbols[offset : offset + batch_size]
                downloaded = provider.get_bars_batch(
                    batch,
                    strategy.timeframe,
                    start=start_at,
                    end=end_at,
                    adjustment="splits" if market == Market.EQUITY else "raw",
                )
                for symbol in batch:
                    by_symbol[symbol] = [
                        bar for bar in downloaded.get(symbol, ()) if bar.timestamp >= start_at
                    ]
            deficient = [
                symbol
                for symbol in symbols
                if start is not None
                and sum(1 for bar in by_symbol[symbol] if bar.timestamp < start) < minimum_bars
            ]
            if not deficient:
                break
            if attempts >= 3:
                log.warning(
                    "Warmup still short of %d pre-start bars for %d symbol(s) after "
                    "3 extension attempts: %s",
                    minimum_bars,
                    len(deficient),
                    ", ".join(sorted(deficient)[:20]),
                )
                continue
            # Extend the window backward for the deficient symbols only. The
            # shortfall is converted to calendar days anchored at the evaluation
            # start, plus a growth buffer so the next attempt makes real progress.
            from parayabuck.markets.timeframes import bars_to_calendar_days

            extra_days = bars_to_calendar_days(
                "SPY" if market == Market.EQUITY else "BTC/USD",
                strategy.timeframe,
                minimum_bars,
                end_date=start,
            )
            symbols_to_retry = sorted(set(deficient))
            symbols = [s for s in symbols if s not in symbols_to_retry] + symbols_to_retry
            start_at = min(
                start_at,
                start - timedelta(days=extra_days) if start is not None else start_at,
            )
    finally:
        provider.close()
    return [bar for symbol in symbols for bar in by_symbol.get(symbol, ())]


def _warmup_window_days(config: Config, market: Market, end_date: datetime | None = None) -> int:
    """Calendar days of pre-start history for strategy and scanner warmup.

    ``end_date`` anchors the strategy-warmup span at the evaluation start so
    a historical backtest sizes its warmup with that era's session density.
    """
    selection = config.strategy.equity if market == Market.EQUITY else config.strategy.crypto
    strategy = portfolio_strategy_from(
        selection.name, selection.parameters, timeframe=selection.timeframe
    )
    lookback_days = 60 if market == Market.EQUITY else config.universe.crypto.scanner.lookback_days
    from parayabuck.markets.timeframes import bars_to_calendar_days

    # Strategy warmup is expressed in bars of the strategy timeframe (e.g. 150
    # daily bars); the scanner lookback is already calendar days and must not
    # be scaled by the bar duration. Bars are converted to a calendar span
    # through the number of trading sessions, not 1 bar == 1 calendar day.
    strategy_days = bars_to_calendar_days(
        "SPY" if market == Market.EQUITY else "BTC/USD",
        strategy.timeframe,
        strategy.minimum_history_bars,
        end_date=end_date,
    )
    return max(strategy_days, lookback_days)


def _candidate_symbols(
    config: Config,
    market: Market,
    explicit: str | None,
    limit: int | None,
) -> list[str]:
    if explicit:
        return list(
            dict.fromkeys(item.strip().upper() for item in explicit.split(",") if item.strip())
        )
    if market == Market.EQUITY:
        # Always download the broad eligible universe. The historical Stage-A
        # shortlist is applied per date inside the backtest universe builder,
        # so 2022 liquidity cannot be hidden behind today's shortlist.
        from alpaca.trading.client import TradingClient

        from parayabuck.universe.preflight import eligible_equity_symbols

        load_dotenv()
        trading = TradingClient(
            os.getenv("ALPACA_API_KEY", ""),
            os.getenv("ALPACA_SECRET_KEY", ""),
            paper=config.runtime.mode.value != "live",
        )
        if (session := getattr(trading, "_session", None)) is not None:
            from parayabuck.utils.http import patch_session_timeout

            patch_session_timeout(session, timeout=15.0)
        eligible = eligible_equity_symbols(config, trading, limit=limit)
        # The scanner reserves references, approved inverse ETFs and pinned
        # symbols in every snapshot: those must be present even when an
        # explicit --candidate-limit excludes them alphabetically, otherwise
        # the backtest gate cannot evaluate a period at all.
        equity_config = config.universe.equity
        required_order = list(
            dict.fromkeys(
                (
                    *equity_config.reference_symbols,
                    *equity_config.inverse_trade_symbols,
                    *equity_config.fallback_trade_symbols,
                )
            )
        )
        required_set = set(required_order)
        candidates = [symbol for symbol in dict.fromkeys(eligible) if symbol not in required_set]
        if limit is not None:
            candidates = candidates[: max(0, limit - len(required_order))]
        return [*required_order, *candidates]
    source = Path(config.universe.crypto.active_universe_path)
    payload = json.loads(source.read_text(encoding="utf-8")) if source.exists() else {}
    candidates = [str(symbol) for symbol in payload.get("candidate_symbols", ())]
    symbols = candidates or list(config.universe.crypto.trade_symbols)
    required = config.universe.crypto.pinned_symbols
    required_order = list(dict.fromkeys(required))
    required_set = set(required_order)
    candidates = [symbol for symbol in dict.fromkeys(symbols) if symbol not in required_set]
    return [*required_order, *candidates[: max(0, limit or len(candidates))]]


def _strategy_parameters(
    config: Config, market: Market, raw_overrides: list[str]
) -> dict[str, object]:
    selection = config.strategy.equity if market == Market.EQUITY else config.strategy.crypto
    parameters = dict(selection.parameters)
    for item in raw_overrides:
        if "=" not in item:
            raise SystemExit(f"Invalid --param {item!r}; expected NAME=VALUE")
        name, value = item.split("=", 1)
        parameters[name] = yaml.safe_load(value)
    return parameters


def _parse_grid(items: list[str]) -> dict[str, list[object]]:
    grid: dict[str, list[object]] = {}
    for item in items:
        if "=" not in item:
            raise SystemExit(f"Invalid --grid {item!r}; expected NAME=V1,V2")
        name, values = item.split("=", 1)
        parsed = [yaml.safe_load(value) for value in values.split(",")]
        if not name or not parsed:
            raise SystemExit(f"Invalid --grid {item!r}")
        grid[name] = parsed
    return grid


def _date(value: str, *, end_of_day: bool = False) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    if end_of_day and len(value) == 10:
        parsed = parsed.replace(hour=23, minute=59, second=59, microsecond=999999)
    return parsed


def _print_summary(summary: dict[str, object], output_dir: str) -> None:
    def number(name: str) -> float:
        value = summary[name]
        if not isinstance(value, (int, float)):
            raise TypeError(f"Backtest metric {name} must be numeric")
        return float(value)

    print(
        f"{summary['strategy_name']} | {summary['start_date']} -> {summary['end_date']} | "
        f"return={number('total_return'):.2%} "
        f"sharpe={number('sharpe'):.2f} "
        f"max_dd={number('max_drawdown'):.2%} "
        f"trades={summary['number_of_trades']}"
    )
    print(f"Results: {output_dir}")


__all__ = ["backtest_main", "main"]


if __name__ == "__main__":
    main()
