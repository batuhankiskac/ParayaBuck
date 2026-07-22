from __future__ import annotations

import argparse
import asyncio
import math
import os
import threading
from dataclasses import replace
from datetime import UTC, datetime, timedelta

from dotenv import load_dotenv

from parayabuck.app_config import load_config
from parayabuck.data.historical import AlpacaDataSource
from parayabuck.utils import is_crypto
from parayabuck.config import load_engine_config
from parayabuck.core.models import Bar, Market
from parayabuck.data import AlpacaBarStream, TimeframeAggregator
from parayabuck.engine import TradingEngine
from parayabuck.execution.alpaca import AlpacaExecutionBroker
from parayabuck.execution.shadow import ShadowBroker
from parayabuck.monitoring import DecisionJournal


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="ParayaBuck trading engine")
    parser.add_argument("--mode", choices=("shadow", "paper", "live"), default="shadow")
    parser.add_argument("--symbols", nargs="*", help="Override configured symbols")
    parser.add_argument("--confirm-paper", default="NO")
    parser.add_argument("--confirm-live", default="NO")
    return parser


def _convert_bar(bar: object, market: Market) -> Bar:
    return Bar(
        timestamp=getattr(bar, "timestamp").astimezone(UTC),
        symbol=str(getattr(bar, "symbol")),
        timeframe=str(getattr(bar, "timeframe")),
        open=float(getattr(bar, "open")),
        high=float(getattr(bar, "high")),
        low=float(getattr(bar, "low")),
        close=float(getattr(bar, "close")),
        volume=float(getattr(bar, "volume")),
        market=market,
    )


def _warmup_start(symbol: str, timeframe: str, bars: int) -> str:
    minutes = {"1m": 1, "5m": 5, "15m": 15, "30m": 30, "1h": 60, "1d": 1440}[timeframe]
    bars_per_day = (1440 if is_crypto(symbol) else 390) / minutes
    trading_days = bars * 1.5 / max(1, bars_per_day)
    calendar_days = trading_days if is_crypto(symbol) else trading_days * 1.5
    days = max(2, min(180, math.ceil(calendar_days)))
    return (datetime.now(UTC) - timedelta(days=days)).strftime("%Y-%m-%d")


def main() -> None:
    args = _parser().parse_args()
    if args.mode == "paper" and args.confirm_paper != "YES":
        raise SystemExit("Paper mode requires --confirm-paper YES")
    if args.mode == "live" and (
        args.confirm_live != "YES" or os.getenv("PARAYABUCK_ENABLE_LIVE") != "YES"
    ):
        raise SystemExit("Live mode requires --confirm-live YES and PARAYABUCK_ENABLE_LIVE=YES")
    load_dotenv()
    api_key = os.getenv("ALPACA_API_KEY")
    secret_key = os.getenv("ALPACA_SECRET_KEY")
    if not api_key or not secret_key:
        raise SystemExit("ALPACA_API_KEY and ALPACA_SECRET_KEY are required")

    app_config = load_config()
    symbols = args.symbols or list(app_config.symbols)
    config_path = os.getenv("PARAYABUCK_ENGINE_CONFIG", "config/engine.yaml")
    config = replace(
        load_engine_config(config_path),
        timeframe=app_config.app.timeframe,
        shadow_mode=args.mode == "shadow",
    )
    live_broker = AlpacaExecutionBroker(api_key, secret_key, paper=args.mode != "live")
    broker = ShadowBroker(live_broker.account()) if args.mode == "shadow" else live_broker
    journal = DecisionJournal(config.decision_db_path)
    engine = TradingEngine(broker, config, journal=journal)

    # REST is used once for warmup; all subsequent bars arrive over WebSocket.
    history = AlpacaDataSource(api_key, secret_key, stock_feed=app_config.alpaca.stock_feed)
    for symbol in symbols:
        try:
            market = Market.CRYPTO if is_crypto(symbol) else Market.EQUITY
            for timeframe in {config.timeframe, config.higher_timeframe}:
                raw = history.get_bars(
                    symbol,
                    timeframe,
                    start=_warmup_start(symbol, timeframe, config.warmup_bars),
                    use_cache=False,
                )
                engine.seed([_convert_bar(bar, market) for bar in raw[-config.warmup_bars :]])
        except Exception as exc:
            print(f"Warmup skipped for {symbol}: {exc}")

    aggregator = TimeframeAggregator(config.timeframe)
    higher_aggregator = TimeframeAggregator(config.higher_timeframe)
    lock = threading.Lock()

    async def on_minute_bar(bar: Bar) -> None:
        closed = aggregator.update(bar)
        higher_closed = higher_aggregator.update(bar)
        if higher_closed is not None:
            with lock:
                engine.seed([higher_closed])
        if closed is None:
            return
        with lock:
            if isinstance(broker, ShadowBroker):
                broker.set_account(live_broker.account())
            orders = engine.process_cycle([closed])
        for order in orders:
            print(
                f"{closed.timestamp.isoformat()} {args.mode.upper()} "
                f"{order.strategy} {order.symbol} qty={order.qty} "
                f"entry={order.entry_price:.4f} stop={order.stop_price:.4f} "
                f"target={order.target_price:.4f}"
            )

    stream = AlpacaBarStream(api_key, secret_key, app_config.alpaca.stock_feed)
    stocks = [symbol for symbol in symbols if not is_crypto(symbol)]
    crypto = [symbol for symbol in symbols if is_crypto(symbol)]
    stream.subscribe(stocks, crypto, on_minute_bar)
    workers: list[threading.Thread] = []
    if stocks:
        workers.append(threading.Thread(target=stream.run_stock, daemon=True))
    if crypto:
        workers.append(threading.Thread(target=stream.run_crypto, daemon=True))
    for worker in workers:
        worker.start()
    print(f"ParayaBuck {args.mode} mode running for {len(symbols)} symbols")
    try:
        while any(worker.is_alive() for worker in workers):
            asyncio.run(asyncio.sleep(1))
    except KeyboardInterrupt:
        stream.stop()
    finally:
        journal.close()


if __name__ == "__main__":
    main()
