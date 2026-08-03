#!/usr/bin/env python3
"""Borsa ve kripto stratejilerini ayrı ayrı, 5m/15m/1h zaman dilimlerinde backtest eder.

Her (piyasa, timeframe) çifti için:
  - Alpaca'dan geçmiş veri indirilir (data/cache/warmup.sqlite3 önbelleğiyle birleşir),
  - yalnızca o piyasanın stratejileri çalıştırılır (borsa: StockTrendPullback +
    StockMeanReversion; kripto: CryptoTrendBreakout + CryptoRangeReversion),
  - rapor reports/backtests/<piyasa>_<timeframe>/ altına yazılır,
  - sonunda strateji bazında karşılaştırma tablosu basılır.

Rapor dosyası mevcutsa koşu atlanır (resume); indirme hataları retry ile
yeniden denenir.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

from dotenv import load_dotenv

from parayabuck.backtest import BacktestEngine, build_report
from parayabuck.backtest.cli import (
    CRYPTO_BENCHMARK,
    EQUITY_BENCHMARK,
    write_run_manifest,
)
from parayabuck.backtest.experiments import hash_bars
from parayabuck.backtest.history import (
    EQUITY_MAX_DAYS,
    SymbolCoverage,
    coverage_for,
    coverage_table,
    download_history,
)
from parayabuck.backtest.universe import build_crypto_point_in_time_universe
from parayabuck.config import Config, EngineConfig, load_config
from parayabuck.core.models import Bar, Market
from parayabuck.data.aggregator import aggregate_historical_bars
from parayabuck.data.alpaca import AlpacaHistoricalData
from parayabuck.markets.timeframes import TIMEFRAME_MINUTES
from parayabuck.strategies import (
    CryptoRangeReversion,
    CryptoTrendBreakout,
    StockMeanReversion,
    StockTrendPullback,
)
from parayabuck.universe import (
    load_active_crypto_symbols,
)

TIMEFRAMES = ("5m", "15m", "1h")
STARTING_EQUITY = 100_000.0
MAX_DOWNLOAD_ATTEMPTS = 5
COMPARISON_DAYS = 120
EQUITY_ETF_CORE = (
    "SPY",
    "QQQ",
    "IWM",
    "XLB",
    "XLC",
    "XLE",
    "XLF",
    "XLI",
    "XLK",
    "XLP",
    "XLRE",
    "XLU",
    "XLV",
    "XLY",
)


def _download_with_retry(
    history: AlpacaHistoricalData,
    symbols: list[str],
    timeframe: str,
    start_at: datetime,
    end_at: datetime,
) -> dict[str, list[Bar]]:
    last_error: Exception | None = None
    for attempt in range(1, MAX_DOWNLOAD_ATTEMPTS + 1):
        try:
            return download_history(
                history, symbols, timeframe, start_at, end_at, adjustment="splits"
            )
        except Exception as exc:  # noqa: BLE001 - retry transient API failures
            last_error = exc
            if attempt < MAX_DOWNLOAD_ATTEMPTS:
                delay = 5 * attempt
                print(f"  İndirme hatası ({timeframe} {symbols}): {exc}; {delay}s sonra yeniden...")
                time.sleep(delay)
    assert last_error is not None
    raise last_error


def resolve_symbols(config: Config) -> dict[str, list[str]]:
    # Current scanner membership must not be projected backwards. Until a
    # point-in-time constituent source is available, equity research uses a
    # permanent broad/sector ETF core; individual stocks remain research-only.
    equity = list(EQUITY_ETF_CORE)
    crypto = [
        str(symbol).upper()
        for symbol in load_active_crypto_symbols(config.universe.crypto, log_rejection=True)
    ]
    return {"equity": equity, "crypto": crypto}


def make_engine_config(config: Config, timeframe: str, market: Market) -> EngineConfig:
    higher_timeframe = config.engine.higher_timeframe
    if TIMEFRAME_MINUTES[higher_timeframe] <= TIMEFRAME_MINUTES[timeframe]:
        higher_timeframe = "1d"
    return replace(
        config.engine,
        timeframe=timeframe,
        higher_timeframe=higher_timeframe,
        trading_enabled=True,
        equity_trading_enabled=market == Market.EQUITY,
        crypto_trading_enabled=market == Market.CRYPTO,
        live_trading_enabled=False,
        ai_logging_enabled=False,
        risk=replace(config.engine.risk, risk_per_trade_pct=0.0025),
    )


def strategies_for(market: Market, config: Config):
    ratio = config.engine.crypto_min_volume_bar_ratio
    if market == Market.EQUITY:
        return (StockTrendPullback(), StockMeanReversion())
    return (
        CryptoTrendBreakout(min_volume_bar_ratio=ratio),
        CryptoRangeReversion(min_volume_bar_ratio=ratio),
    )


def run_one(
    history: AlpacaHistoricalData,
    config: Config,
    market: Market,
    timeframe: str,
    output_dir: Path,
    *,
    days: int,
    source_timeframe: str | None = None,
) -> None:
    end_at = datetime.now(UTC)
    start_at = end_at - timedelta(days=days)
    engine_config = make_engine_config(config, timeframe, market)
    higher_timeframe = engine_config.higher_timeframe
    benchmark = EQUITY_BENCHMARK if market == Market.EQUITY else CRYPTO_BENCHMARK

    symbols = resolve_symbols(config)[market.value]
    symbols = [symbol for symbol in symbols if symbol != benchmark]
    symbols = list(dict.fromkeys([*symbols, benchmark]))

    bars_by_symbol: dict[str, list[Bar]] = {}
    coverage: list[SymbolCoverage] = []
    source = source_timeframe or timeframe
    for symbol in symbols:
        source_bars = _download_with_retry(
            history, [symbol], source, start_at, end_at
        ).get(symbol, [])
        primary = (
            aggregate_historical_bars(source_bars, timeframe)
            if source != timeframe
            else source_bars
        )
        bars_by_symbol[symbol] = primary
        coverage.append(
            coverage_for(
                symbol,
                primary,
                timeframe=timeframe,
                expected_start=start_at,
                expected_end=end_at,
            )
        )
    if higher_timeframe != timeframe:
        for symbol in symbols:
            if source == "5m" and TIMEFRAME_MINUTES[higher_timeframe] < 1440:
                raw = _download_with_retry(history, [symbol], source, start_at, end_at).get(
                    symbol, []
                )
                context_bars = aggregate_historical_bars(raw, higher_timeframe)
            else:
                context_bars = _download_with_retry(
                    history, [symbol], higher_timeframe, start_at, end_at
                ).get(symbol, [])
            bars_by_symbol[symbol].extend(context_bars)

    tradeable = [symbol for symbol in symbols if bars_by_symbol[symbol]]
    all_bars = [bar for bars in bars_by_symbol.values() for bar in bars]
    print(f"\n[{market.value} / {timeframe}] {days} gün, {len(tradeable)} sembol indirildi")
    print(coverage_table(coverage))

    failures = [
        item.symbol
        for item in coverage
        if item.bars == 0
        or item.coverage_pct < 0.50
        or (
            item.max_gap_minutes
            > TIMEFRAME_MINUTES[timeframe] * 12
            and item.market == Market.CRYPTO
        )
    ]
    if benchmark in failures or len(failures) == len(coverage):
        raise RuntimeError(f"Insufficient research coverage for: {', '.join(failures)}")

    point_in_time = None
    if market == Market.CRYPTO:
        primary_only = [bar for bar in all_bars if bar.timeframe == timeframe]
        point_in_time = build_crypto_point_in_time_universe(
            primary_only,
            maximum_symbols=config.universe.crypto.maximum_stream_symbols,
        )

    engine = BacktestEngine(
        engine_config,
        starting_equity=STARTING_EQUITY,
        slippage_bps=2.0,
        spread_bps=4.0 if market == Market.EQUITY else 6.0,
        crypto_taker_fee_bps=5.0,
        strategies=strategies_for(market, config),
        allowed_trade_symbols=set(tradeable) if tradeable else None,
        benchmark_symbol=benchmark,
        historical_universe_filter=point_in_time.contains if point_in_time else None,
    )
    result = engine.run(all_bars)
    trades = tuple(engine.broker.trades)

    output_dir.mkdir(parents=True, exist_ok=True)
    report = build_report(
        result,
        curve_timestamps=engine.curve_timestamps,
        trades=trades,
        benchmark_bars=engine.benchmark_bars,
        timeframe=timeframe,
        symbols=tradeable,
    )
    (output_dir / "report.json").write_text(
        json.dumps(report.to_dict(), indent=2, allow_nan=False, ensure_ascii=False),
        encoding="utf-8",
    )
    report.write_csvs(str(output_dir))
    write_run_manifest(
        str(output_dir),
        result=result,
        profile="strategy-backtest",
        timeframe=timeframe,
        higher_timeframe=higher_timeframe,
        benchmark_symbol=engine.used_benchmark_symbol,
        adjustment="splits",
        coverage=coverage,
        symbols=tradeable,
        starting_equity=STARTING_EQUITY,
        participation=(0.10, 0.10),
        extra={
            "market": market.value,
            "window_days": days,
            "source_timeframe": source,
            "data_hash": hash_bars(all_bars),
            "universe_version": (
                "permanent_etf_core_v1"
                if market == Market.EQUITY
                else "crypto_trailing_liquidity_v1"
            ),
            "point_in_time_universe": point_in_time.to_dict() if point_in_time else None,
            "cost_scenario": "equity_base" if market == Market.EQUITY else "crypto_base",
            "strategy_specs": [
                {
                    "name": strategy.name,
                    "version": strategy.version,
                    "parameters": strategy.params.__dict__,
                    "exit_policy": strategy.exit_policy.__dict__,
                }
                for strategy in strategies_for(market, config)
            ],
            "random_seed": 7,
        },
    )
    print(f"Rapor: {output_dir}")

    # Portfolio capacity can hide a viable strategy behind a higher-ranked
    # sibling. Run every strategy alone with identical data and costs as well.
    for strategy in strategies_for(market, config):
        isolated_dir = output_dir / "isolated" / strategy.name
        isolated_engine = BacktestEngine(
            engine_config,
            starting_equity=STARTING_EQUITY,
            slippage_bps=2.0,
            spread_bps=4.0 if market == Market.EQUITY else 6.0,
            crypto_taker_fee_bps=5.0,
            strategies=(strategy,),
            allowed_trade_symbols=set(tradeable) if tradeable else None,
            benchmark_symbol=benchmark,
            historical_universe_filter=point_in_time.contains if point_in_time else None,
        )
        isolated_result = isolated_engine.run(all_bars)
        isolated_report = build_report(
            isolated_result,
            curve_timestamps=isolated_engine.curve_timestamps,
            trades=tuple(isolated_engine.broker.trades),
            benchmark_bars=isolated_engine.benchmark_bars,
            timeframe=timeframe,
            symbols=tradeable,
        )
        isolated_dir.mkdir(parents=True, exist_ok=True)
        (isolated_dir / "report.json").write_text(
            json.dumps(
                isolated_report.to_dict(), indent=2, allow_nan=False, ensure_ascii=False
            ),
            encoding="utf-8",
        )
        isolated_report.write_csvs(str(isolated_dir))
        write_run_manifest(
            str(isolated_dir),
            result=isolated_result,
            profile="isolated-strategy-research",
            timeframe=timeframe,
            higher_timeframe=higher_timeframe,
            benchmark_symbol=isolated_engine.used_benchmark_symbol,
            adjustment="splits",
            coverage=coverage,
            symbols=tradeable,
            starting_equity=STARTING_EQUITY,
            participation=(0.10, 0.10),
            extra={
                "market": market.value,
                "strategy": strategy.name,
                "strategy_version": strategy.version,
                "exit_policy_id": strategy.exit_policy.policy_id,
                "data_hash": hash_bars(all_bars),
                "cost_scenario": (
                    "equity_base" if market == Market.EQUITY else "crypto_base"
                ),
            },
        )


def summarize(output_dir: Path) -> dict[str, object]:
    """Rapor dosyasından koşu özeti + strateji bazında işlem istatistiklerini okur."""
    payload = json.loads((output_dir / "report.json").read_text(encoding="utf-8"))
    summary = payload["summary"]
    stats: dict[str, dict[str, float | int]] = {}
    for trade in payload["trades"]:
        entry = stats.setdefault(
            trade["strategy"], {"trades": 0, "wins": 0, "net_pnl": 0.0, "total_return": 0.0}
        )
        entry["trades"] += 1
        entry["net_pnl"] += trade["pnl"]
        entry["total_return"] += trade["return_pct"]
        if trade["pnl"] > 0:
            entry["wins"] += 1
    for entry in stats.values():
        entry["win_rate"] = entry["wins"] / entry["trades"] if entry["trades"] else 0.0
    return {"summary": summary, "by_strategy": stats}


def main() -> int:
    load_dotenv()
    api_key = os.getenv("ALPACA_API_KEY")
    secret_key = os.getenv("ALPACA_SECRET_KEY")
    if not api_key or not secret_key:
        raise SystemExit("ALPACA_API_KEY / ALPACA_SECRET_KEY environment variables are required")
    config = load_config()
    history = AlpacaHistoricalData(
        api_key,
        secret_key,
        stock_feed=config.alpaca.stock_feed,
        data_delayed=config.alpaca.data_delayed,
    )
    base = Path("reports/backtests")
    summaries: dict[tuple[str, str, str], dict[str, object]] = {}
    for market in (Market.EQUITY, Market.CRYPTO):
        for timeframe in TIMEFRAMES:
            output_dir = base / "common_120d" / f"{market.value}_{timeframe}"
            if (output_dir / "report.json").exists():
                print(f"[{market.value} / {timeframe}] mevcut rapordan yüklendi: {output_dir}")
            else:
                run_one(
                    history,
                    config,
                    market,
                    timeframe,
                    output_dir,
                    days=COMPARISON_DAYS,
                    source_timeframe="5m",
                )
            summaries[("common120d", market.value, timeframe)] = summarize(output_dir)

        timeframe = "1h"
        output_dir = base / "research_1h" / market.value
        if (output_dir / "report.json").exists():
            print(f"[{market.value} / long 1h] mevcut rapordan yüklendi: {output_dir}")
        else:
            run_one(
                history,
                config,
                market,
                timeframe,
                output_dir,
                days=EQUITY_MAX_DAYS[timeframe],
            )
        summaries[("research", market.value, timeframe)] = summarize(output_dir)

    print("\n" + "=" * 100)
    print("STRATEJİ PERFORMANS KARŞILAŞTIRMASI")
    print("=" * 100)
    header = (
        f"{'Koşu':<11}{'Piyasa':<8}{'TF':<6}{'Pencere':<9}{'Getiri%':>10}"
        f"{'CAGR%':>9}{'MaxDD%':>9}"
        f"{'Sharpe':>8}{'İşlem':>7}{'Kazanma%':>9}{'PF':>7}{'Bench%':>9}{'Excess%':>10}"
    )
    print(header)
    print("-" * 100)
    for (scope, market, timeframe), entry in summaries.items():
        result = entry["summary"]
        pf = result.get("profit_factor")
        pf_text = f"{pf:.2f}" if pf is not None else "-"
        print(
            f"{scope:<11}{market:<8}{timeframe:<6}{result['duration_days']:>7}g"
            f"{result['total_return'] * 100:>9.2f}%"
            f"{result['cagr'] * 100:>8.2f}%"
            f"{result['max_drawdown'] * 100:>8.2f}%"
            f"{result['sharpe']:>8.2f}{result['total_trades']:>7}"
            f"{result['win_rate'] * 100:>8.2f}%"
            f"{pf_text:>7}{result['benchmark_return'] * 100:>8.2f}%"
            f"{result['excess_return'] * 100:>9.2f}%"
        )
    print("-" * 100)
    print("\nStrateji bazında (trade payları):")
    for (scope, market, timeframe), entry in summaries.items():
        print(f"\n  {scope} {market} {timeframe}:")
        for strategy, stats in sorted(entry["by_strategy"].items()):
            if not stats["trades"]:
                print(f"    {strategy:<24} işlem=0")
                continue
            print(
                f"    {strategy:<24} işlem={stats['trades']:>4}  "
                f"kazanma=%{stats['win_rate'] * 100:.1f}  "
                f"net PnL={stats['net_pnl']:>12,.2f}  "
                f"ort. işlem getirisi=%{stats['total_return'] / stats['trades'] * 100:.2f}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
