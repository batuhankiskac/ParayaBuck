#!/usr/bin/env python3
"""Alpaca'dan gerçek geçmiş veri çekip BacktestEngine ile çalıştırır.

Örnek kullanım:
    python scripts/run_historical_backtest.py --days 90 --max-symbols 15
    python scripts/run_historical_backtest.py --symbols AAPL,MSFT,GOOG --days 60
    python scripts/run_historical_backtest.py --crypto --symbols BTC/USD,ETH/USD --days 45
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict, replace
from datetime import UTC, datetime, timedelta

from dotenv import load_dotenv

from parayabuck.backtest import BacktestEngine
from parayabuck.config import load_config
from parayabuck.core.models import Bar
from parayabuck.data.alpaca import AlpacaHistoricalData
from parayabuck.markets.timeframes import TIMEFRAME_MINUTES

BENCHMARK = "SPY"
DEFAULT_UNIVERSE = "data/runtime/active_equity_universe.json"
EQUITY_DAYS_MAX = 120
_CHUNK_DAYS = {"5m": 60, "1h": 90, "1d": 180}


def _parse_date(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _load_symbols(args: argparse.Namespace) -> list[str]:
    if args.symbols:
        return [symbol.strip().upper() for symbol in args.symbols.split(",") if symbol.strip()]
    path = args.universe or DEFAULT_UNIVERSE
    if not os.path.exists(path):
        raise SystemExit(f"Universe file not found: {path} (use --symbols to pass symbols)")
    with open(path) as handle:
        raw = json.load(handle)
    candidates = raw.get("candidate_symbols", []) if isinstance(raw, dict) else raw
    if not candidates:
        raise SystemExit(f"No candidate symbols in {path}")
    return [str(symbol).upper() for symbol in candidates[: args.max_symbols]]


def _fetch_symbol_bars(
    history: AlpacaHistoricalData,
    symbol: str,
    timeframe: str,
    start: datetime,
    end: datetime,
) -> list[Bar]:
    """Alpaca tek istekte ~10k bar sınırlar; sembolü zaman dilimlerine bölerek çeker."""
    chunk_days = _CHUNK_DAYS.get(timeframe, 30)
    collected: list[Bar] = []
    cursor = start
    while cursor < end:
        segment_end = min(cursor + timedelta(days=chunk_days), end)
        collected.extend(history.get_bars(symbol, timeframe, start=cursor, end=segment_end))
        cursor = segment_end
    return collected


def main() -> None:
    parser = argparse.ArgumentParser(description="Alpaca geçmiş verisiyle gerçek backtest")
    parser.add_argument(
        "--symbols", help="virgülle ayrılmış semboller (SPY benchmark otomatik eklenir)"
    )
    parser.add_argument("--universe", default=DEFAULT_UNIVERSE)
    parser.add_argument("--max-symbols", type=int, default=15)
    parser.add_argument("--start", help="YYYY-MM-DD (varsayılan: bugün - --days)")
    parser.add_argument("--days", type=int, default=90)
    parser.add_argument("--crypto", action="store_true", help="kripto sembollerini işleme al")
    parser.add_argument("--starting-equity", type=float, default=100_000.0)
    parser.add_argument("--slippage-bps", type=float, default=2.0)
    parser.add_argument("--spread-bps", type=float, default=0.0)
    parser.add_argument("--commission", type=float, default=0.0)
    parser.add_argument("--crypto-taker-fee-bps", type=float, default=0.0)
    parser.add_argument("--output", help="sonucu JSON olarak bu dosyaya yaz")
    args = parser.parse_args()

    load_dotenv()
    api_key = os.getenv("ALPACA_API_KEY")
    secret_key = os.getenv("ALPACA_SECRET_KEY")
    if not api_key or not secret_key:
        raise SystemExit("ALPACA_API_KEY / ALPACA_SECRET_KEY environment variables are required")

    config = load_config()
    timeframe = config.engine.timeframe
    higher_timeframe = config.engine.higher_timeframe
    if TIMEFRAME_MINUTES[higher_timeframe] <= TIMEFRAME_MINUTES[timeframe]:
        higher_timeframe = "1d"

    if args.start:
        end_at = _parse_date(args.start) + timedelta(days=args.days)
        start_at = _parse_date(args.start)
    else:
        end_at = datetime.now(UTC)
        start_at = end_at - timedelta(days=args.days)
    has_equity = not args.crypto
    if has_equity and args.days > EQUITY_DAYS_MAX:
        raise SystemExit(
            f"Equity 5m verisinde Alpaca istek limiti ~{EQUITY_DAYS_MAX} gün; "
            f"--days {EQUITY_DAYS_MAX} veya altını kullanın"
        )

    symbols = _load_symbols(args)
    if has_equity:
        symbols = list(dict.fromkeys(symbols + [BENCHMARK]))
    print(
        f"Veri çekiliyor: {len(symbols)} sembol, {timeframe}/{higher_timeframe}, "
        f"{start_at.date()} -> {end_at.date()}"
    )

    history = AlpacaHistoricalData(
        api_key,
        secret_key,
        stock_feed=config.alpaca.stock_feed,
        data_delayed=config.alpaca.data_delayed,
    )
    bars_by_timeframe: dict[str, dict[str, object]] = {}
    for tf in {timeframe, higher_timeframe}:
        combined: dict[str, object] = {}
        for symbol in symbols:
            combined[symbol] = _fetch_symbol_bars(history, symbol, tf, start_at, end_at)
        bars_by_timeframe[tf] = combined

    all_bars = [bar for tf in bars_by_timeframe.values() for bars in tf.values() for bar in bars]
    per_symbol = {symbol: len(bars_by_timeframe[timeframe].get(symbol, [])) for symbol in symbols}
    print("Sembol başına 5m bar sayısı:", json.dumps(per_symbol))
    empty = [symbol for symbol, count in per_symbol.items() if count == 0]
    if empty:
        print(f"Uyarı: veri gelmeyen semboller: {empty}")
    if not all_bars:
        raise SystemExit("Hiç veri çekilemedi")

    engine_config = replace(
        config.engine,
        trading_enabled=True,
        crypto_trading_enabled=args.crypto,
    )
    tradeable = {symbol for symbol in symbols if symbol != BENCHMARK}
    engine = BacktestEngine(
        engine_config,
        starting_equity=args.starting_equity,
        slippage_bps=args.slippage_bps,
        spread_bps=args.spread_bps,
        commission=args.commission,
        crypto_taker_fee_bps=args.crypto_taker_fee_bps,
        allowed_trade_symbols=tradeable,
    )
    result = engine.run(all_bars)

    payload = asdict(result)
    payload.pop("equity_curve")
    payload.pop("realized_pnls")
    payload["ai_log_dir"] = str(engine.ai_journal.root) if engine.ai_journal is not None else None
    payload["symbols"] = symbols
    payload["period"] = {
        "start": start_at.isoformat(),
        "end": end_at.isoformat(),
        "timeframe": timeframe,
        "higher_timeframe": higher_timeframe,
    }
    print(json.dumps(payload, indent=2, allow_nan=False))
    if args.output:
        with open(args.output, "w") as handle:
            json.dump(payload, handle, indent=2, allow_nan=False)
        print(f"Sonuç yazıldı: {args.output}")


if __name__ == "__main__":
    sys.exit(main())
