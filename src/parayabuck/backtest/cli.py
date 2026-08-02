"""ParayaBuck backtest CLI — tek komutla CSV verisi veya 10+ yıl borsa + kripto.

Botu günlerce açık tutmak yerine strateji performansını geçmiş veri üzerinde
ölçer ve detaylı rapor üretir. Alpaca modunda veriler ilk çalıştırmada
indirilir ve data/cache/ altında saklanır; sonraki çalıştırmalar önbellekten
anında tamamlanır.

Çalıştırma yolları (hepsi aynı CLI):
    python scripts/run_backtest.py ...          # script
    python -m parayabuck.backtest ...           # modül
    parayabuck-backtest ...                     # kurulu komut

Örnekler:
    # Hiçbir argüman vermeden çalıştırın: kullanım rehberi gösterir.
    # Örnek veriyle hızlı test (offline, indirme yok):
    python scripts/run_backtest.py --generate-sample
    # Kendi CSV'n:
    python scripts/run_backtest.py data/historical/DEMO_5m_2024.csv --timeframe 5m
    # Canlıda doğrulanmış 5m + 1h profiliyle (pencere <= ~120 işlem günü):
    python scripts/run_backtest.py --market both --days 90 --profile live
    # Alpaca, 10 yıl, her iki piyasa (1d, deep profil):
    python scripts/run_backtest.py --market both --years 10
    python scripts/run_backtest.py --market crypto --years 7 --timeframe 1d
    python scripts/run_backtest.py --market equity --years 2 --timeframe 1h --max-symbols 50
    python scripts/run_backtest.py --symbols AAPL,MSFT,GOOG,BTC/USD --years 10
    python scripts/run_backtest.py --market equity --timeframe 5m --days 90 --max-symbols 15
    python scripts/run_backtest.py --market both --years 10 --walk-forward
    python scripts/run_backtest.py --market both --years 10 --walk-forward-optimize
    python scripts/run_backtest.py --market both --years 10 --sweep
"""

from __future__ import annotations

import argparse
import json
import os
import random
import subprocess
import sys
from dataclasses import asdict, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv

from parayabuck.backtest import BacktestEngine
from parayabuck.backtest.engine import BacktestResult
from parayabuck.backtest.history import (
    DEEP_BACKTEST_TIMEFRAMES,
    EQUITY_MAX_DAYS,
    SymbolCoverage,
    coverage_for,
    coverage_table,
    download_history,
    validate_range,
)
from parayabuck.backtest.report import build_report
from parayabuck.backtest.sweep import (
    DEFAULT_POSITIONS,
    DEFAULT_RISKS,
    DEFAULT_SCORES,
    render_sweep_text,
    run_sweep,
    write_sweep_csv,
)
from parayabuck.backtest.validation import (
    monte_carlo_trades,
    rolling_out_of_sample,
    walk_forward_optimized,
)
from parayabuck.config import Config, EngineConfig, load_config
from parayabuck.core.models import Bar, Market
from parayabuck.data.alpaca import AlpacaHistoricalData
from parayabuck.markets.timeframes import TIMEFRAME_MINUTES
from parayabuck.universe import load_active_crypto_symbols, load_active_symbols
from parayabuck.utils import is_crypto

EQUITY_BENCHMARK = "SPY"
CRYPTO_BENCHMARK = "BTC/USD"

USAGE = """\
ParayaBuck Backtest
===================

Tek komutla backtest çalıştırır ve detaylı rapor üretir (konsol + reports/).

  # 1) Örnek veriyle hızlı test (indirme yok, tamamen offline):
  python scripts/run_backtest.py --generate-sample

  # 2) Kendi CSV'nle:
  python scripts/run_backtest.py <veri.csv> [--symbol SYM] [--timeframe 5m]

  # 3) Alpaca'dan gerçek veriyle (anahtarlar .env dosyasında):
  python scripts/run_backtest.py --market both --years 10
  python scripts/run_backtest.py --market crypto --years 7
  python scripts/run_backtest.py --market equity --timeframe 5m --days 90

  # 4) Canlıda doğrulanmış 5m + 1h profil (pencere <= ~120 işlem günü):
  python scripts/run_backtest.py --market both --days 90 --profile live

  # 5) Gelişmiş: rolling out-of-sample dilimleri ve parametre seçimli
  #    nested walk-forward (train'de seç, test'te ölç):
  python scripts/run_backtest.py --market both --years 10 --walk-forward
  python scripts/run_backtest.py --market both --years 10 --walk-forward-optimize

  # 6) Tek tıkla parametre taraması (in-sample en uygun kombinasyonları bul):
  python scripts/run_backtest.py --market both --years 10 --sweep
  python scripts/run_backtest.py --market both --years 10 --sweep --sweep-scores "55,65,75"

Rapor çıktıları (varsayılan reports/ dizininde):
  report.json          tüm metriklerin tam JSON'u
  run_manifest.json    koşu manifesti (parametreler, veri, sürüm, benchmark)
  summary.csv          özet metrikler
  trades.csv           işlem bazında detay (giriş/çıkış, MAE/MFE, çıkış nedeni)
  yearly_returns.csv   yıl yıl performans (benchmark karşılaştırmalı)
  monthly_returns.csv  aylık getiri matrisi
  by_symbol.csv        sembol bazında dağılım
  equity_curve.csv     equity eğrisi
  drawdowns.csv        drawdown serisi

Tüm seçenekler için: python scripts/run_backtest.py --help
"""


def _parse_date(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _parse_float_list(value: str, option: str) -> tuple[float, ...]:
    items = tuple(float(item) for item in value.split(",") if item.strip())
    if not items:
        raise SystemExit(f"--{option} boş olamaz")
    return items


def _auto_profile(requested: str | None, start_at: datetime, end_at: datetime) -> str:
    """Pick the bar timeframe for Alpaca mode when --timeframe is omitted.

    ``live`` maps to the exact validated 5m + 1h profile; ``deep`` to 1d for
    multi-year windows. Without an explicit profile, the window decides: the
    live profile is only meaningful within the equity 5m data horizon
    (``EQUITY_MAX_DAYS``), otherwise the deep 1d profile is used.
    """
    if requested == "live":
        return "5m"
    if requested == "deep":
        return "1d"
    requested_days = max(0, (end_at - start_at).days)
    return "5m" if requested_days <= EQUITY_MAX_DAYS["5m"] else "1d"


def write_run_manifest(
    output_dir: str,
    *,
    result: BacktestResult,
    profile: str,
    timeframe: str,
    higher_timeframe: str,
    benchmark_symbol: str,
    adjustment: str,
    coverage: list[SymbolCoverage],
    symbols: list[str],
    starting_equity: float,
    participation: tuple[float, float],
    extra: dict[str, object] | None = None,
) -> str:
    """Write run_manifest.json describing how this backtest was produced.

    The manifest makes every report reproducible: parameters, data coverage,
    benchmark, adjustment basis and the producing code revision.
    """
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    revision = "unknown"
    try:
        revision = (
            subprocess.run(
                ["git", "rev-parse", "HEAD"],
                check=True,
                capture_output=True,
                text=True,
                cwd=Path(__file__).resolve().parents[3],
            ).stdout.strip()
            or "unknown"
        )
    except (OSError, subprocess.SubprocessError):
        pass
    started_at = datetime.now(UTC).isoformat()
    result_dict = asdict(result)
    manifest = {
        "schema_version": 1,
        "started_at": started_at,
        "profile": profile,
        "timeframe": timeframe,
        "higher_timeframe": higher_timeframe,
        "benchmark_symbol": benchmark_symbol,
        "adjustment": adjustment,
        "starting_equity": starting_equity,
        "max_entry_participation_pct": participation[0],
        "max_exit_participation_pct": participation[1],
        "symbols": sorted(symbols),
        "coverage": [item.to_dict() for item in coverage],
        "code_revision": revision,
        "summary": {
            key: result_dict.get(key)
            for key in (
                "start_date",
                "end_date",
                "duration_days",
                "total_return",
                "benchmark_return",
                "excess_return",
                "max_drawdown",
                "total_trades",
                "sharpe",
                "cagr",
                "open_positions",
                "unrealized_pnl",
                "liquidated_at_end",
            )
        },
    }
    if extra:
        manifest.update(extra)
    path = directory / "run_manifest.json"
    with path.open("w", encoding="utf-8") as handle:
        json.dump(manifest, handle, indent=2, allow_nan=False, ensure_ascii=False)
    return str(path)


def _resolve_symbols(args: argparse.Namespace, config: Config) -> dict[str, list[str]]:
    universe = config.universe
    equity_symbols: list[str] = []
    crypto_symbols: list[str] = []
    if args.symbols:
        for symbol in args.symbols.split(","):
            symbol = symbol.strip().upper()
            if not symbol:
                continue
            if is_crypto(symbol):
                crypto_symbols.append(symbol)
            else:
                equity_symbols.append(symbol)
    else:
        if args.market in {"both", "equity"}:
            equity_symbols = _equity_universe(args, config)
        if args.market in {"both", "crypto"}:
            # Mirror the live runtime: the validated scanner selection (or the
            # configured fallback), capacity-capped exactly like the engine.
            crypto_symbols = [
                str(symbol).upper()
                for symbol in load_active_crypto_symbols(universe.crypto, log_rejection=True)
            ]
            if args.max_symbols:
                crypto_symbols = crypto_symbols[: args.max_symbols]
    if args.market == "equity":
        crypto_symbols = []
    elif args.market == "crypto":
        equity_symbols = []
    return {"equity": equity_symbols, "crypto": crypto_symbols}


def _equity_universe(args: argparse.Namespace, config: Config) -> list[str]:
    if args.universe and os.path.exists(args.universe):
        path = args.universe
        with open(path) as handle:
            raw = json.load(handle)
        candidates = raw.get("candidate_symbols", []) if isinstance(raw, dict) else raw
        symbols = (
            [str(symbol).upper() for symbol in candidates]
            if candidates
            else list(config.universe.equity.fallback_trade_symbols)
        )
    else:
        # Same source the live engine streams: validated scanner selection
        # (fallback_trade_symbols when rejected), references and pins, capped
        # at maximum_stream_symbols. Reference-only symbols are not tradable
        # (matching the runtime's allowed_trade_symbols) but stay in the
        # download list for benchmark/regime context.
        symbols = list(load_active_symbols(config.universe.equity, log_rejection=True))
        if not config.universe.equity.trade_reference_symbols:
            references = set(config.universe.equity.reference_symbols)
            symbols = [symbol for symbol in symbols if symbol not in references]
    return list(dict.fromkeys(symbols))[: args.max_symbols]


def _validate_data_quality(
    coverage: list[SymbolCoverage],
    args: argparse.Namespace,
    start_at: datetime,
    end_at: datetime,
    timeframe: str,
) -> None:
    """Fail fast when downloaded data is too incomplete to trust.

    Coverage below the minimum or a gap far beyond the timeframe's regular
    interval means the simulation would run on unreliable data and produce
    confident-looking but invalid results. Late-listed symbols (which by
    definition only cover part of the window) are exempt.
    """
    interval_minutes = TIMEFRAME_MINUTES.get(timeframe, 0)
    min_coverage = args.min_coverage_pct / 100.0
    offenders: list[str] = []
    for item in coverage:
        if item.expected_bars <= 0 or item.bars == 0:
            continue
        if item.start > start_at + timedelta(days=7):
            continue  # late-listed: legitimately partial window
        reasons: list[str] = []
        if item.coverage_pct < min_coverage:
            reasons.append(f"kapsama %{item.coverage_pct * 100:.1f} < %{args.min_coverage_pct:.0f}")
        if (
            interval_minutes > 0
            and item.max_gap_minutes > interval_minutes * args.max_gap_multiplier
        ):
            reasons.append(
                f"boşluk {item.max_gap_minutes} dk > "
                f"{interval_minutes * args.max_gap_multiplier:.0f} dk "
                f"({args.max_gap_multiplier:.0f}x {timeframe})"
            )
        if reasons:
            offenders.append(f"{item.symbol}: " + "; ".join(reasons))
    if offenders:
        raise SystemExit(
            "Veri kalitesi eşikleri karşılanmıyor; koşu durduruldu:\n  "
            + "\n  ".join(offenders)
            + "\n--min-coverage-pct / --max-gap-multiplier ile gevşetilebilir."
        )


def _download_market(
    history: AlpacaHistoricalData,
    symbols: list[str],
    timeframe: str,
    higher_timeframe: str,
    start_at: datetime,
    end_at: datetime,
    adjustment: str,
) -> tuple[dict[str, list[Bar]], list[SymbolCoverage]]:
    primary: dict[str, list[Bar]] = {}
    for symbol in symbols:
        primary[symbol] = download_history(
            history, [symbol], timeframe, start_at, end_at, adjustment=adjustment
        ).get(symbol, [])
    coverage = [
        coverage_for(
            symbol, bars, timeframe=timeframe, expected_start=start_at, expected_end=end_at
        )
        for symbol, bars in primary.items()
    ]
    if higher_timeframe and higher_timeframe != timeframe:
        for symbol in symbols:
            primary[symbol].extend(
                download_history(
                    history, [symbol], higher_timeframe, start_at, end_at, adjustment=adjustment
                ).get(symbol, [])
            )
    return primary, coverage


def _load_csv_bars(
    args: argparse.Namespace, parser: argparse.ArgumentParser, default_timeframe: str
) -> tuple[dict[str, list[Bar]], list[SymbolCoverage]]:
    frame = pd.read_csv(args.csv)
    timestamps = pd.to_datetime(frame["timestamp"], utc=True)
    if "symbol" not in frame.columns and not args.symbol:
        parser.error("--symbol is required when the dataset has no symbol column")
    default_market = "equity" if args.market == "both" else args.market
    bars_by_symbol: dict[str, list[Bar]] = {}
    for timestamp, row in zip(timestamps, frame.itertuples(index=False), strict=True):
        symbol = str(getattr(row, "symbol", args.symbol))
        bar = Bar(
            timestamp=timestamp.to_pydatetime().astimezone(UTC),
            symbol=symbol,
            timeframe=str(getattr(row, "timeframe", default_timeframe)),
            open=float(row.open),
            high=float(row.high),
            low=float(row.low),
            close=float(row.close),
            volume=float(row.volume),
            market=Market(str(getattr(row, "market", default_market))),
        )
        bars_by_symbol.setdefault(symbol, []).append(bar)
    coverage = [coverage_for(symbol, bars) for symbol, bars in bars_by_symbol.items()]
    return bars_by_symbol, coverage


def generate_sample_bars(
    symbol: str = "DEMO",
    timeframe: str = "5m",
    n_bars: int = 5000,
    start_price: float = 5000.0,
    drift: float = 0.0001,
    volatility: float = 0.003,
    start_date: str = "2024-01-01",
) -> pd.DataFrame:
    """Sentetik OHLCV üretir; --generate-sample modunun veri kaynağı.

    Varsayılan sembol DEMO'dur: veri sentetiktir ve hiçbir piyasa enstrümanını
    (futures dahil) temsil etmez. Sonuçlar yalnızca akışın doğru çalıştığını
    gösterir, strateji performansı olarak yorumlanmamalıdır.
    """
    try:
        minutes = TIMEFRAME_MINUTES[timeframe]
    except KeyError as exc:
        raise ValueError(f"Unsupported timeframe: {timeframe}") from exc
    start_dt = datetime.strptime(start_date, "%Y-%m-%d")
    timestamps: list[datetime] = []
    t = start_dt
    while len(timestamps) < n_bars:
        if t.weekday() < 5:  # Mon-Fri
            hour = t.hour
            if not (hour == 17 and t.minute < 60):
                timestamps.append(t)
        t += timedelta(minutes=minutes)
    timestamps = timestamps[:n_bars]
    rng = random.Random(42)
    prices: list[float] = [start_price]
    for _ in range(n_bars - 1):
        prices.append(prices[-1] * (1 + rng.gauss(drift, volatility)))
    data: list[dict[str, object]] = []
    for i in range(n_bars):
        close = round(prices[i], 2)
        bar_range = abs(prices[i] * volatility * rng.uniform(0.5, 1.5))
        high = round(close + bar_range * rng.uniform(0.3, 1.0), 2)
        low = round(close - bar_range * rng.uniform(0.3, 1.0), 2)
        open_p = round(low + (high - low) * rng.uniform(0.2, 0.8), 2)
        data.append(
            {
                "timestamp": timestamps[i].isoformat(),
                "symbol": symbol,
                "open": open_p,
                "high": max(high, open_p, close),
                "low": min(low, open_p, close),
                "close": close,
                "volume": int(rng.uniform(100, 5000)),
            }
        )
    return pd.DataFrame(data)


def main(argv: list[str] | None = None) -> int:
    effective_argv = sys.argv[1:] if argv is None else argv
    parser = argparse.ArgumentParser(
        description="ParayaBuck backtest: CSV veya 10+ yıl borsa + kripto, detaylı rapor"
    )
    parser.add_argument(
        "csv",
        nargs="?",
        help="Alpaca yerine bu CSV dosyasını kullan "
        "(offline; symbol/timeframe/market sütunları opsiyonel)",
    )
    parser.add_argument(
        "--generate-sample",
        action="store_true",
        help="önce sentetik örnek veri üret (indirme yok), ardından backtest'i çalıştır",
    )
    parser.add_argument(
        "--market",
        choices=("both", "equity", "crypto"),
        default="both",
        help="hangi piyasalar (CSV'de sütun yoksa varsayılan: equity)",
    )
    parser.add_argument(
        "--years", type=int, default=10, help="kaç yıl geriye gidilecek (varsayılan: 10)"
    )
    parser.add_argument(
        "--days",
        type=int,
        default=0,
        help="--years yerine gün bazlı pencere (örn. 90); --years'i ezer",
    )
    parser.add_argument("--start", help="başlangıç tarihi YYYY-MM-DD (varsayılan: bugün - --years)")
    parser.add_argument("--end", help="bitiş tarihi YYYY-MM-DD (varsayılan: şimdi)")
    parser.add_argument(
        "--timeframe",
        choices=DEEP_BACKTEST_TIMEFRAMES,
        default=None,
        help="bar periyodu (CSV: varsayılan 5m; Alpaca: profil veya pencereye göre, "
        "çok yıllık testler için 1d önerilir)",
    )
    parser.add_argument(
        "--profile",
        choices=("live", "deep"),
        default=None,
        help="Alpaca profili: live = canlıda doğrulanmış 5m + 1h (pencere "
        "<= ~120 işlem günü); deep = 1d (10 yıllık testler). Belirtilmezse "
        "pencereye göre otomatik seçilir.",
    )
    parser.add_argument(
        "--adjustment",
        choices=("raw", "splits", "all"),
        default="splits",
        help="hisse fiyat düzeltmesi (corporate actions): splits = bölünmeler "
        "düzeltilmiş (varsayılan, uzun testlerde önerilir), raw = ham fiyat, "
        "all = bölünme + temettü",
    )
    parser.add_argument(
        "--entry-participation-pct",
        type=float,
        default=0.10,
        help="giriş emrinin tek barda hacmin yüzde kaçını doldurabileceği "
        "(varsayılan: 0.10, yani yüzde 10; kısmi dolum simülasyonu)",
    )
    parser.add_argument(
        "--exit-participation-pct",
        type=float,
        default=0.10,
        help="stop/hedef çıkışının tek barda hacmin yüzde kaçını doldurabileceği "
        "(varsayılan: 0.10, yani yüzde 10; kısmi çıkış simülasyonu)",
    )
    parser.add_argument(
        "--symbols", help="virgülle ayrılmış semboller (borsa + kripto karışabilir)"
    )
    parser.add_argument(
        "--symbol",
        default=None,
        help="CSV'de symbol sütunu yoksa kullanılacak sembol; "
        "örnek verideki sembol (varsayılan: DEMO, sentetik)",
    )
    parser.add_argument("--universe", help="özel borsa universe dosyası")
    parser.add_argument("--max-symbols", type=int, default=40)
    parser.add_argument("--bars", type=int, default=5000, help="--generate-sample bar sayısı")
    parser.add_argument(
        "--start-price", type=float, default=5000.0, help="--generate-sample başlangıç fiyatı"
    )
    parser.add_argument(
        "--start-date",
        default="2024-01-01",
        help="--generate-sample başlangıç tarihi (YYYY-MM-DD)",
    )
    parser.add_argument(
        "--sample-output",
        help="--generate-sample CSV çıktı yolu "
        "(varsayılan: data/historical/<sembol>_<tf>_sample.csv)",
    )
    parser.add_argument("--starting-equity", type=float, default=100_000.0)
    parser.add_argument("--slippage-bps", type=float, default=2.0)
    parser.add_argument("--spread-bps", type=float, default=0.0)
    parser.add_argument("--commission", type=float, default=0.0)
    parser.add_argument("--crypto-taker-fee-bps", type=float, default=5.0)
    parser.add_argument(
        "--output", help="rapor JSON çıktı yolu (varsayılan: <output-dir>/report.json)"
    )
    parser.add_argument(
        "--output-dir", default="reports", help="CSV ve rapor dizini (varsayılan: reports/)"
    )
    parser.add_argument(
        "--no-monte-carlo", action="store_true", help="Monte Carlo simülasyonunu atla"
    )
    parser.add_argument(
        "--walk-forward",
        action="store_true",
        help="rolling out-of-sample dilimlerini de çalıştır (yavaşlatır)",
    )
    parser.add_argument(
        "--walk-forward-optimize",
        action="store_true",
        help="nested walk-forward: her train diliminde parametre seç (sweep), "
        "seçilen parametrelerle bir sonraki test diliminde ölç (yavaşlatır)",
    )
    parser.add_argument("--wf-train", type=int, default=1250, help="walk-forward eğitim bar sayısı")
    parser.add_argument("--wf-test", type=int, default=250, help="walk-forward test bar sayısı")
    parser.add_argument(
        "--sweep",
        action="store_true",
        help="aynı veri üzerinde parametre taraması yap, en iyi kombinasyonları raporla",
    )
    parser.add_argument(
        "--sweep-scores",
        default="",
        help="sweep skor eşikleri, virgülle ayrılmış (örn. 55,65,75; varsayılan: 55-85)",
    )
    parser.add_argument(
        "--sweep-risks",
        default="",
        help="sweep risk oranları, virgülle ayrılmış (örn. 0.0025,0.005; varsayılan: 0.0025-0.01)",
    )
    parser.add_argument(
        "--sweep-positions",
        default="",
        help="sweep pozisyon yüzdeleri, virgülle ayrılmış (örn. 0.05,0.15; varsayılan: 0.05-0.15)",
    )
    parser.add_argument(
        "--sweep-min-trades",
        type=int,
        default=10,
        help="sweep'te dikkate alınacak minimum işlem sayısı (varsayılan: 10)",
    )
    parser.add_argument(
        "--min-coverage-pct",
        type=float,
        default=50.0,
        help="Alpaca modunda sembol başına minimum veri kapsama yüzdesi; altında "
        "kalan koşu veri hatasıyla durdurulur (varsayılan: 50)",
    )
    parser.add_argument(
        "--max-gap-multiplier",
        type=float,
        default=12.0,
        help="Alpaca modunda izin verilen en büyük bar boşluğu; timeframe "
        "aralığının katı olarak (varsayılan: 12)",
    )
    args = parser.parse_args(effective_argv)

    if not effective_argv:
        print(USAGE)
        return 0

    if args.generate_sample:
        sample_timeframe = args.timeframe or "5m"
        sample_symbol = args.symbol or "DEMO"
        sample_path = args.sample_output or (
            f"data/historical/{sample_symbol}_{sample_timeframe}_sample.csv"
        )
        frame = generate_sample_bars(
            symbol=sample_symbol,
            timeframe=sample_timeframe,
            n_bars=args.bars,
            start_price=args.start_price,
            start_date=args.start_date,
        )
        os.makedirs(os.path.dirname(sample_path) or ".", exist_ok=True)
        frame.to_csv(sample_path, index=False)
        print(
            f"Örnek veri üretildi: {sample_path} "
            f"({len(frame)} bar, {sample_symbol} {sample_timeframe})"
        )
        print(
            f"Periyot: {frame['timestamp'].iloc[0]} -> {frame['timestamp'].iloc[-1]}  "
            f"Fiyat: {frame['low'].min():.2f} -> {frame['high'].max():.2f}"
        )
        args.csv = sample_path

    config = load_config()
    if args.csv:
        timeframe = args.timeframe or "5m"
    else:
        load_dotenv()
        api_key = os.getenv("ALPACA_API_KEY")
        secret_key = os.getenv("ALPACA_SECRET_KEY")
        if api_key is None or secret_key is None or not api_key or not secret_key:
            raise SystemExit(
                "ALPACA_API_KEY / ALPACA_SECRET_KEY environment variables are required"
            )
        assert api_key is not None and secret_key is not None
        alpaca_api_key: str = api_key
        alpaca_secret_key: str = secret_key
        end_at = _parse_date(args.end) if args.end else datetime.now(UTC)
        if args.start:
            start_at = _parse_date(args.start)
        elif args.days > 0:
            start_at = end_at - timedelta(days=args.days)
        else:
            start_at = end_at - timedelta(days=args.years * 365)
        if args.timeframe:
            timeframe = args.timeframe
        else:
            timeframe = _auto_profile(args.profile, start_at, end_at)
            if args.profile is None:
                profile_label = "live (5m + 1h)" if timeframe == "5m" else "deep (1d)"
                print(
                    f"Profil otomatik seçildi: {profile_label} "
                    f"(--profile live/deep ile geçersiz kılınabilir)"
                )
    higher_timeframe = config.engine.higher_timeframe
    if TIMEFRAME_MINUTES[higher_timeframe] <= TIMEFRAME_MINUTES[timeframe]:
        higher_timeframe = "1d"

    bars_by_symbol: dict[str, list[Bar]]
    coverage: list[SymbolCoverage]
    if args.csv:
        bars_by_symbol, coverage = _load_csv_bars(args, parser, timeframe)
        requested_symbols = set(bars_by_symbol)
        all_bars = [bar for bars in bars_by_symbol.values() for bar in bars]
        if not all_bars:
            raise SystemExit("CSV'de bar bulunamadı")
        print(
            f"CSV yüklendi: {len(all_bars)} bar, {len(bars_by_symbol)} sembol, "
            f"{timeframe}/{higher_timeframe}"
        )
    else:
        symbols_by_market = _resolve_symbols(args, config)
        requested_symbols = set(symbols_by_market["equity"]) | set(symbols_by_market["crypto"])
        if symbols_by_market["equity"]:
            symbols_by_market["equity"].append(EQUITY_BENCHMARK)
        if symbols_by_market["crypto"]:
            symbols_by_market["crypto"].append(CRYPTO_BENCHMARK)
        all_symbols = symbols_by_market["equity"] + symbols_by_market["crypto"]
        if not all_symbols:
            raise SystemExit(
                "Sembol bulunamadı: --symbols verin veya universe dosyalarını kontrol edin"
            )
        try:
            validate_range(all_symbols, timeframe, start_at, end_at)
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc

        print(
            f"Veri indiriliyor: {len(all_symbols)} sembol, {timeframe}"
            f"{'/' + higher_timeframe if higher_timeframe != timeframe else ''}, "
            f"{start_at.date()} -> {end_at.date()}"
        )

        history = AlpacaHistoricalData(
            alpaca_api_key,
            alpaca_secret_key,
            stock_feed=config.alpaca.stock_feed,
            data_delayed=config.alpaca.data_delayed,
        )
        bars_by_symbol = {}
        coverage = []
        for market_name, symbols in symbols_by_market.items():
            if not symbols:
                continue
            fetched, market_coverage = _download_market(
                history,
                list(dict.fromkeys(symbols)),
                timeframe,
                higher_timeframe,
                start_at,
                end_at,
                args.adjustment,
            )
            bars_by_symbol.update(fetched)
            coverage.extend(market_coverage)
        print(coverage_table(coverage))

        late_listings = [
            item for item in coverage if item.bars > 0 and item.start > start_at + timedelta(days=7)
        ]
        for item in late_listings:
            print(
                f"Uyarı: {item.symbol} verisi {item.start.date()} itibarıyla başlıyor; "
                "öncesinde işlem görmedi (geç listelenmiş veya verisi kısa bir sembol). "
                "Bu, point-in-time olarak doğru kabul edilir."
            )

        empty = [item.symbol for item in coverage if item.bars == 0]
        if empty:
            print(f"Uyarı: veri gelmeyen semboller: {empty}")
        all_bars = [bar for bars in bars_by_symbol.values() for bar in bars]
        if not all_bars:
            raise SystemExit("Hiç veri çekilemedi")
        _validate_data_quality(coverage, args, start_at, end_at, timeframe)

    non_empty = [symbol for symbol, bars in bars_by_symbol.items() if bars]
    has_equity = any(not is_crypto(symbol) for symbol in non_empty)
    has_crypto = any(is_crypto(symbol) for symbol in non_empty)
    benchmark_symbol = EQUITY_BENCHMARK if has_equity else CRYPTO_BENCHMARK
    explicit_symbols = (
        {item.strip().upper() for item in args.symbols.split(",") if item.strip()}
        if args.symbols
        else set()
    )
    # The benchmark is a comparison reference, but when it also belongs to the
    # configured trade universe (e.g. BTC/USD in a default crypto run) it must
    # stay tradeable — otherwise the live strategy on that symbol is never
    # tested. Only exclude it when it was added purely for benchmarking.
    tradeable = [
        symbol
        for symbol in non_empty
        if symbol != benchmark_symbol or symbol in explicit_symbols or symbol in requested_symbols
    ]

    engine_config = replace(
        config.engine,
        timeframe=timeframe,
        higher_timeframe=higher_timeframe,
        trading_enabled=True,
        crypto_trading_enabled=has_crypto,
        live_trading_enabled=False,
        ai_logging_enabled=False,
    )

    def make_engine(engine_config: EngineConfig) -> BacktestEngine:
        return BacktestEngine(
            engine_config,
            starting_equity=args.starting_equity,
            slippage_bps=args.slippage_bps,
            spread_bps=args.spread_bps,
            commission=args.commission,
            crypto_taker_fee_bps=args.crypto_taker_fee_bps,
            allowed_trade_symbols=set(tradeable) if tradeable else None,
            benchmark_symbol=benchmark_symbol,
            max_entry_participation_pct=args.entry_participation_pct,
            max_exit_participation_pct=args.exit_participation_pct,
        )

    engine = make_engine(engine_config)
    print(f"\nBacktest çalıştırılıyor: {timeframe}, {benchmark_symbol} benchmark...")
    result = engine.run(all_bars)

    actual_benchmark = getattr(engine, "used_benchmark_symbol", benchmark_symbol)
    benchmark_bars = list(getattr(engine, "benchmark_bars", ()))
    if actual_benchmark != benchmark_symbol:
        print(
            f"Uyarı: {benchmark_symbol} verisi bulunamadı; benchmark olarak "
            f"{actual_benchmark} kullanıldı."
        )
    trades = tuple(engine.broker.trades)
    monte_carlo = None
    if not args.no_monte_carlo and result.realized_pnls:
        monte_carlo_result = monte_carlo_trades(
            result.realized_pnls, args.starting_equity, simulations=2_000
        )
        monte_carlo = asdict(monte_carlo_result)
    walk_forward_folds: list[dict[str, object]] = []
    if args.walk_forward and timeframe != "1m":
        print("Walk-forward (rolling out-of-sample) çalıştırılıyor...")
        folds = rolling_out_of_sample(
            all_bars,
            lambda: make_engine(engine_config),
            train_size=args.wf_train,
            test_size=args.wf_test,
        )
        for fold in folds:
            fold_dict = asdict(fold.result)
            fold_dict.pop("equity_curve")
            fold_dict.pop("realized_pnls")
            walk_forward_folds.append(fold_dict)
    if args.walk_forward_optimize and timeframe != "1m":
        print("Nested walk-forward (her train diliminde parametre seçimi) çalıştırılıyor...")
        optimized_folds = walk_forward_optimized(
            all_bars,
            engine_config,
            make_engine,
            train_size=args.wf_train,
            test_size=args.wf_test,
            scores=_parse_float_list(args.sweep_scores, "sweep-scores")
            if args.sweep_scores
            else DEFAULT_SCORES,
            risks=_parse_float_list(args.sweep_risks, "sweep-risks")
            if args.sweep_risks
            else DEFAULT_RISKS,
            positions=_parse_float_list(args.sweep_positions, "sweep-positions")
            if args.sweep_positions
            else DEFAULT_POSITIONS,
            min_trades=args.sweep_min_trades,
        )
        for opt_fold in optimized_folds:
            fold_dict = asdict(opt_fold.result)
            fold_dict.pop("equity_curve")
            fold_dict.pop("realized_pnls")
            fold_dict["train_params"] = {
                "minimum_signal_score": opt_fold.minimum_signal_score,
                "risk_per_trade_pct": opt_fold.risk_per_trade_pct,
                "max_position_pct": opt_fold.max_position_pct,
            }
            walk_forward_folds.append(fold_dict)

    report = build_report(
        result,
        curve_timestamps=engine.curve_timestamps,
        trades=trades,
        benchmark_bars=benchmark_bars,
        timeframe=timeframe,
        symbols=tradeable,
        monte_carlo=monte_carlo,
        walk_forward=tuple(walk_forward_folds),
    )

    os.makedirs(args.output_dir, exist_ok=True)
    output_path = args.output or os.path.join(args.output_dir, "report.json")
    with open(output_path, "w", encoding="utf-8") as handle:
        json.dump(report.to_dict(), handle, indent=2, allow_nan=False, ensure_ascii=False)
    csv_paths = report.write_csvs(args.output_dir)
    manifest_path = write_run_manifest(
        args.output_dir,
        result=result,
        profile=args.profile or ("live" if timeframe == "5m" else "deep"),
        timeframe=timeframe,
        higher_timeframe=higher_timeframe,
        benchmark_symbol=actual_benchmark,
        adjustment=args.adjustment,
        coverage=coverage,
        symbols=tradeable,
        starting_equity=args.starting_equity,
        participation=(args.entry_participation_pct, args.exit_participation_pct),
        extra={
            "profile_source": "cli",
            "argv": effective_argv,
            "walk_forward_folds": len(walk_forward_folds),
        },
    )

    print(report.render_text())
    print(f"JSON rapor   : {output_path}")
    print(f"CSV dosyaları: {', '.join(csv_paths)}")
    print(f"Manifest     : {manifest_path}")

    if args.sweep:
        print("\nParametre taraması çalıştırılıyor (aynı veri, tüm kombinasyonlar)...")
        points = run_sweep(
            all_bars,
            engine_config,
            make_engine,
            scores=_parse_float_list(args.sweep_scores, "sweep-scores")
            if args.sweep_scores
            else DEFAULT_SCORES,
            risks=_parse_float_list(args.sweep_risks, "sweep-risks")
            if args.sweep_risks
            else DEFAULT_RISKS,
            positions=_parse_float_list(args.sweep_positions, "sweep-positions")
            if args.sweep_positions
            else DEFAULT_POSITIONS,
            min_trades=args.sweep_min_trades,
        )
        if points:
            sweep_path = write_sweep_csv(points, args.output_dir)
            print(f"\nEn iyi {min(5, len(points))} kombinasyon (Calmar'a göre):")
            print(render_sweep_text(points))
            print(f"Sweep CSV   : {sweep_path}")
        else:
            print(
                f"Sweep sonuçlandı: minimum {args.sweep_min_trades} işleme ulaşan "
                "kombinasyon yok; --sweep-min-trades düşürmeyi deneyin."
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
