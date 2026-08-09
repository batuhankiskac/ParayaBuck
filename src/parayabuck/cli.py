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

from parayabuck.backtest.product import load_csv_bars, optimize_grid, run_product_backtest
from parayabuck.config import Config, load_config
from parayabuck.core.models import Bar, Market
from parayabuck.data import AlpacaHistoricalData
from parayabuck.execution.alpaca import AlpacaBroker
from parayabuck.live_safety import LiveOperatorApproval
from parayabuck.portfolio import portfolio_strategy_from
from parayabuck.portfolio.runtime_service import configuration_digest
from parayabuck.universe import (
    ensure_active_universes,
    load_active_crypto_symbols,
    load_active_symbols,
)
from parayabuck.universe.crypto_scanner import crypto_scanner_config_digest
from parayabuck.universe.scanner import scanner_config_digest
from parayabuck.utils.io import atomic_write


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="parayabuck", description="ParayaBuck trading bot")
    parser.add_argument("--config", default=None, help="configuration YAML path")
    commands = parser.add_subparsers(dest="command")
    commands.add_parser("web", help="start the local operations console")
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
    parser.add_argument("--symbols", help="comma-separated download candidates")
    parser.add_argument("--candidate-limit", type=int, default=300)
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
    market = Market(args.market)
    start = _date(args.start) if args.start else None
    end = _date(args.end, end_of_day=True) if args.end else None
    bars = _load_bars(config, args, market, start, end)
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
    broker = AlpacaBroker(key, secret, paper=False)
    broker.account()  # Read-only identity verification against the live endpoint.
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
        strategy = portfolio_strategy_from(selection.name, selection.parameters)
        digests.append(
            configuration_digest(
                strategy,
                tuple(symbols),
                "live",
                selection.maximum_allocation,
                universe_version,
            )
        )
    now = datetime.now(UTC)
    approval = LiveOperatorApproval(
        operator_id=args.operator,
        account_id=broker.account_id or "",
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
        f"Live approval written for account ...{broker.account_suffix}; "
        f"expires {approval.expires_at.isoformat()}"
    )
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
    start_at = start or end_at - timedelta(days=5 * 365 if market == Market.EQUITY else 2 * 365)
    expected_per_symbol = max(
        1,
        int((end_at - start_at).total_seconds() / 86_400)
        * (24 if selection.timeframe == "1h" else 1),
    )
    batch_size = max(1, min(100, 9_000 // expected_per_symbol))
    provider = AlpacaHistoricalData(
        key,
        secret,
        stock_feed=config.alpaca.stock_feed,
        data_delayed=config.alpaca.data_delayed,
    )
    bars: list[Bar] = []
    for offset in range(0, len(symbols), batch_size):
        batch = symbols[offset : offset + batch_size]
        downloaded = provider.get_bars_batch(
            batch,
            selection.timeframe,
            start=start_at,
            end=end_at,
            adjustment="splits" if market == Market.EQUITY else "raw",
        )
        bars.extend(bar for symbol in batch for bar in downloaded.get(symbol, ()))
    return bars


def _candidate_symbols(
    config: Config,
    market: Market,
    explicit: str | None,
    limit: int,
) -> list[str]:
    if explicit:
        return list(
            dict.fromkeys(item.strip().upper() for item in explicit.split(",") if item.strip())
        )
    if market == Market.EQUITY:
        source = Path(config.universe.equity.candidate_universe_path)
        payload = json.loads(source.read_text(encoding="utf-8")) if source.exists() else {}
        candidates = [str(symbol) for symbol in payload.get("symbols", ())]
        fallback = config.universe.equity.fallback_trade_symbols
        symbols = candidates or list(fallback)
        required = config.universe.equity.reference_symbols
    else:
        source = Path(config.universe.crypto.active_universe_path)
        payload = json.loads(source.read_text(encoding="utf-8")) if source.exists() else {}
        candidates = [str(symbol) for symbol in payload.get("candidate_symbols", ())]
        symbols = candidates or list(config.universe.crypto.trade_symbols)
        required = config.universe.crypto.pinned_symbols
    ordered = list(dict.fromkeys((*required, *sorted(symbols))))
    return ordered[: max(len(required), limit)]


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
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
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
