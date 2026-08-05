#!/usr/bin/env python3
"""Portföy stratejisi araştırması: indirme -> baseline -> nested validation.

Üç yeni portföy stratejisini gerçek veriyle doğrular:

  * equity_etf_dual_momentum_v1          (1d, aylık rebalance)
  * crypto_cross_sectional_momentum_1h_v1 (1h, spot, PIT evren)
  * equity_time_series_momentum_1d_v1    (1d, aylık/haftalık)

Akış:
  1) Alpaca'dan veri indirilir ve versiyonlu yerel depoya (data/research/)
     yazılır; manifest veri hash'i, feed, adjustment ve kapsamı kaydeder.
  2) Her strateji için base maliyetle baseline koşusu yapılır.
  3) Base/stress maliyet karşılaştırması üretilir.
  4) Kilitli holdout ile nested validation koşulur; DSR/PBO ve production
     gate karar tablosu yazılır.

Raporlar reports/portfolio_research/ altındadır. Holdout yalnız
--unlock-holdout ile ve yalnız bir kez açılabilir.
"""

from __future__ import annotations

import argparse
import json
import os
import time
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path

from dotenv import load_dotenv

from parayabuck.backtest.experiments import (
    CostScenario,
    ExperimentManifest,
    hash_bars,
    new_manifest,
)
from parayabuck.backtest.portfolio_engine import (
    PortfolioBacktestEngine,
    PortfolioBacktestResult,
)
from parayabuck.backtest.portfolio_experiments import (
    PortfolioExperimentSpec,
    PortfolioGate,
    PortfolioTrialResult,
    run_portfolio_experiment,
)
from parayabuck.backtest.portfolio_report import build_portfolio_report
from parayabuck.backtest.universe import build_crypto_point_in_time_universe
from parayabuck.config import Config, load_config
from parayabuck.core.models import Bar, Market
from parayabuck.data.alpaca import AlpacaHistoricalData
from parayabuck.data.research_store import VersionedResearchStore
from parayabuck.portfolio import rebalance_policy_for
from parayabuck.portfolio.base import PortfolioStrategy
from parayabuck.portfolio.crypto_cross_sectional import CryptoCrossSectionalMomentum1hV1
from parayabuck.portfolio.equity_dual_momentum import EquityEtfDualMomentumV1
from parayabuck.portfolio.equity_tsm import EquityTimeSeriesMomentum1dV1
from parayabuck.portfolio.universes import (
    CRYPTO_UNIVERSE_VERSION,
    EQUITY_ETF_UNIVERSE,
    EQUITY_UNIVERSE_VERSION,
    EXECUTION_SUPPORTED_CRYPTO,
)

ROOT = Path(__file__).resolve().parents[1]
REPORTS_ROOT = ROOT / "reports" / "portfolio_research"
DATA_ROOT = ROOT / "data" / "research"
EQUITY_DATASET = "equity_etf_1d_v1"
CRYPTO_DATASET = "crypto_spot_1h_v1"
EQUITY_EXPERIMENT = ROOT / "config" / "experiments" / "equity_dual_momentum_v1.yaml"
TSM_EXPERIMENT = ROOT / "config" / "experiments" / "equity_tsm_1d_v1.yaml"
CRYPTO_EXPERIMENT = ROOT / "config" / "experiments" / "crypto_cross_sectional_1h_v1.yaml"
STARTING_EQUITY = 100_000.0
HOLDOUT_REGISTRY_PATH = ROOT / "data" / "runtime" / "portfolio_holdout_registry.json"

BASELINE_PARAMETERS: dict[str, dict[str, object]] = {
    "equity_etf_dual_momentum_v1": {
        "momentum_scheme": "12_1",
        "absolute_filter": "positive_momentum",
        "volatility_window": 20,
        "top_n": 3,
    },
    "crypto_cross_sectional_momentum_1h_v1": {
        "momentum_scheme": "7d",
        "rebalance_hours": 24,
        "top_n": 3,
        "volatility_window": 72,
    },
    "equity_time_series_momentum_1d_v1": {
        "momentum_scheme": "12m",
        "rebalance_frequency": "monthly",
        "volatility_window": 20,
    },
}


def _git_revision() -> str:
    try:
        import subprocess

        return (
            subprocess.run(
                ["git", "rev-parse", "HEAD"],
                check=True,
                capture_output=True,
                text=True,
                cwd=ROOT,
            ).stdout.strip()
            or "unknown"
        )
    except Exception:
        return "unknown"


def _holdout_registry() -> dict[str, object]:
    if not HOLDOUT_REGISTRY_PATH.exists():
        return {}
    raw = json.loads(HOLDOUT_REGISTRY_PATH.read_text(encoding="utf-8"))
    return raw if isinstance(raw, dict) else {}


def _record_holdout_evaluation(experiment_id: str, data_hash: str) -> None:
    registry = _holdout_registry()
    if experiment_id in registry:
        raise SystemExit(
            f"Holdout daha önce değerlendirildi: {experiment_id}. "
            "Aynı holdout üzerinde yeniden değerlendirme yasaktır."
        )
    registry[experiment_id] = {
        "evaluated_at": datetime.now(UTC).isoformat(),
        "data_hash": data_hash,
    }
    HOLDOUT_REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = HOLDOUT_REGISTRY_PATH.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(registry, indent=2, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )
    temporary.replace(HOLDOUT_REGISTRY_PATH)


# ---------------------------------------------------------------------------
# Download + research store
# ---------------------------------------------------------------------------


def download_and_persist(
    history: AlpacaHistoricalData,
    store: VersionedResearchStore,
    config: Config,
    *,
    equity_years: int,
    crypto_days: int,
) -> tuple[dict[str, list[Bar]], dict[str, list[Bar]], list[Bar]]:
    now = datetime.now(UTC)
    equity_start = now - timedelta(days=equity_years * 365)
    equity_bars: list[Bar] = []
    for symbol in (*EQUITY_ETF_UNIVERSE, "SPY"):
        fetched = history.get_bars(
            symbol, "1d", start=equity_start, end=now, adjustment="splits"
        )
        equity_bars.extend(fetched)
        print(f"  equity {symbol:<5} 1d: {len(fetched):>5} bar")
        time.sleep(0.2)
    equity_by_symbol: dict[str, list[Bar]] = {
        symbol: [] for symbol in (*EQUITY_ETF_UNIVERSE, "SPY")
    }
    for bar in equity_bars:
        equity_by_symbol.setdefault(bar.symbol, []).append(bar)

    crypto_start = now - timedelta(days=crypto_days)
    crypto_bars: list[Bar] = []
    for symbol in EXECUTION_SUPPORTED_CRYPTO:
        fetched = history.get_bars(
            symbol, "1h", start=crypto_start, end=now, adjustment="splits"
        )
        crypto_bars.extend(fetched)
        print(f"  crypto {symbol:<8} 1h: {len(fetched):>5} bar")
        time.sleep(0.2)
    crypto_by_symbol: dict[str, list[Bar]] = {
        symbol: [] for symbol in EXECUTION_SUPPORTED_CRYPTO
    }
    for bar in crypto_bars:
        crypto_by_symbol.setdefault(bar.symbol, []).append(bar)

    store.save_dataset(
        EQUITY_DATASET,
        equity_bars,
        timeframe="1d",
        source_feed=config.alpaca.stock_feed,
        adjustment="splits",
        universe_version=EQUITY_UNIVERSE_VERSION,
        notes=f"permanent ETF core; requested {equity_years}y from {equity_start.date()}",
    )
    store.save_dataset(
        CRYPTO_DATASET,
        crypto_bars,
        timeframe="1h",
        source_feed="crypto",
        adjustment="splits",
        universe_version=CRYPTO_UNIVERSE_VERSION,
        notes=(
            "execution-supported spot pairs; "
            f"requested {crypto_days}d from {crypto_start.date()}"
        ),
    )
    manifest = store.manifest(EQUITY_DATASET)
    assert manifest is not None
    print(
        f"\nEquity dataset  : {EQUITY_DATASET}  {manifest.start.date()} -> {manifest.end.date()}"
        f"  hash={manifest.data_hash[:12]}..."
    )
    crypto_manifest = store.manifest(CRYPTO_DATASET)
    assert crypto_manifest is not None
    print(
        f"Crypto dataset  : {CRYPTO_DATASET}  {crypto_manifest.start.date()} -> "
        f"{crypto_manifest.end.date()}  hash={crypto_manifest.data_hash[:12]}..."
    )
    return equity_by_symbol, crypto_by_symbol, equity_bars


def load_research_data(
    store: VersionedResearchStore,
) -> tuple[dict[str, list[Bar]], dict[str, list[Bar]]]:
    if store.manifest(EQUITY_DATASET) is None or store.manifest(CRYPTO_DATASET) is None:
        raise SystemExit(
            "Araştırma verisi yok. Önce 'python scripts/run_portfolio_research.py --download' "
            "çalıştırın."
        )
    equity = store.load_dataset(EQUITY_DATASET)
    crypto = store.load_dataset(CRYPTO_DATASET)
    return equity, crypto


def _write_dataset_manifest(
    store: VersionedResearchStore,
    dataset_id: str,
    bars: list[Bar],
    code_revision: str,
) -> ExperimentManifest:
    manifest = store.manifest(dataset_id)
    assert manifest is not None
    return new_manifest(
        experiment_id=dataset_id,
        spec_dict={
            "dataset": dataset_id,
            "format": manifest.format,
            "source_feed": manifest.source_feed,
            "adjustment": manifest.adjustment,
            "start": manifest.start.isoformat(),
            "end": manifest.end.isoformat(),
            "bars_per_symbol": manifest.bars_per_symbol,
        },
        universe_version=manifest.universe_version,
        seed=7,
        bars=bars,
        code_revision=code_revision,
        metadata={"coverage": manifest.bars_per_symbol},
    )


# ---------------------------------------------------------------------------
# Engines and baselines
# ---------------------------------------------------------------------------


def make_engine(
    strategy: PortfolioStrategy,
    cost: CostScenario,
    universe: object | None = None,
    universe_version: str = "",
) -> PortfolioBacktestEngine:
    return PortfolioBacktestEngine(
        strategy,
        rebalance_policy_for(strategy),
        starting_equity=STARTING_EQUITY,
        slippage_bps=cost.slippage_bps,
        spread_bps=cost.spread_bps,
        crypto_taker_fee_bps=cost.taker_fee_bps,
        benchmark_symbol="SPY" if strategy.market == Market.EQUITY else "BTC/USD",
        max_participation_pct=0.10,
        turnover_band=0.005,
        minimum_order_notional=10.0,
        universe=universe,
        universe_version=universe_version,
    )


def run_baseline(
    strategy: PortfolioStrategy,
    bars: list[Bar],
    cost: CostScenario,
    universe: object | None = None,
    universe_version: str = "",
) -> tuple[PortfolioBacktestResult, PortfolioBacktestEngine]:
    engine = make_engine(strategy, cost, universe, universe_version)
    result = engine.run(bars)
    return result, engine


def write_baseline_report(
    strategy_name: str,
    result: PortfolioBacktestResult,
    bars: list[Bar],
    cost: CostScenario,
    code_revision: str,
) -> None:
    directory = REPORTS_ROOT / "baseline" / strategy_name
    directory.mkdir(parents=True, exist_ok=True)
    report = build_portfolio_report(
        result, strategy_name=strategy_name, strategy_version="1.0.0"
    )
    (directory / "report.json").write_text(
        json.dumps(report.to_dict(), indent=2, allow_nan=False, ensure_ascii=False),
        encoding="utf-8",
    )
    report.write_csvs(str(directory))
    manifest = new_manifest(
        experiment_id=f"baseline_{strategy_name}",
        spec_dict={
            "cost_scenario": cost.name,
            "slippage_bps": cost.slippage_bps,
            "spread_bps": cost.spread_bps,
            "taker_fee_bps": cost.taker_fee_bps,
        },
        universe_version=(
            EQUITY_UNIVERSE_VERSION if result.benchmark_symbol == "SPY" else CRYPTO_UNIVERSE_VERSION
        ),
        seed=7,
        bars=bars,
        code_revision=code_revision,
    )
    (directory / "manifest.json").write_text(
        json.dumps(
            {**asdict(manifest), "created_at": manifest.created_at.isoformat()},
            indent=2,
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        ),
        encoding="utf-8",
    )
    print(f"Baseline raporu: {directory}")


def write_cost_comparison(
    base: PortfolioBacktestResult,
    stress: PortfolioBacktestResult,
    cost: CostScenario,
    strategy_name: str,
) -> None:
    directory = REPORTS_ROOT / "cost_comparison"
    directory.mkdir(parents=True, exist_ok=True)
    payload = {
        "strategy": strategy_name,
        "base_cost": {
            "name": cost.name,
            "slippage_bps": cost.slippage_bps,
            "spread_bps": cost.spread_bps,
            "taker_fee_bps": cost.taker_fee_bps,
        },
        "base": {
            "total_return": base.total_return,
            "cagr": base.cagr,
            "sharpe": base.sharpe,
            "max_drawdown": base.max_drawdown,
            "turnover": base.turnover,
            "slippage_cost": base.slippage_cost_total,
            "spread_cost": base.spread_cost_total,
            "fee_cost": base.fee_total,
        },
        "stress": {
            "total_return": stress.total_return,
            "cagr": stress.cagr,
            "sharpe": stress.sharpe,
            "max_drawdown": stress.max_drawdown,
            "turnover": stress.turnover,
            "slippage_cost": stress.slippage_cost_total,
            "spread_cost": stress.spread_cost_total,
            "fee_cost": stress.fee_total,
        },
        "cost_sensitivity": {
            "return_drag_pp": (base.total_return - stress.total_return) * 100,
            "sharpe_drag": base.sharpe - stress.sharpe,
        },
    }
    path = directory / f"{strategy_name}.json"
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Maliyet karşılaştırması: {path}")


# ---------------------------------------------------------------------------
# Nested validation + gate table
# ---------------------------------------------------------------------------


def run_nested(
    experiment_path: Path,
    bars: list[Bar],
    *,
    universe: object | None,
    unlock_holdout: bool,
) -> tuple[PortfolioExperimentSpec, tuple[PortfolioTrialResult, ...]]:
    spec = PortfolioExperimentSpec.from_file(experiment_path)
    market = spec.candidate_spaces[0].market
    universe_version = (
        EQUITY_UNIVERSE_VERSION if market == Market.EQUITY else CRYPTO_UNIVERSE_VERSION
    )

    def factory(
        strategy: PortfolioStrategy, cost: CostScenario
    ) -> PortfolioBacktestEngine:
        return make_engine(strategy, cost, universe, universe_version)

    trials = run_portfolio_experiment(
        bars, spec, factory, unlock_holdout=unlock_holdout
    )
    if unlock_holdout:
        _record_holdout_evaluation(spec.experiment_id, hash_bars(bars))
    return spec, trials


def write_nested_reports(
    spec: PortfolioExperimentSpec,
    trials: tuple[PortfolioTrialResult, ...],
    bars: list[Bar],
    code_revision: str,
) -> None:
    directory = REPORTS_ROOT / "nested" / spec.experiment_id
    directory.mkdir(parents=True, exist_ok=True)
    gate = PortfolioGate.for_market(spec.candidate_spaces[0].market)
    payload: list[dict[str, object]] = []
    for trial in trials:
        decision = gate.evaluate(trial) if trial.holdout_evaluations else None
        payload.append(
            {
                "trial_id": trial.trial_id,
                "strategy": trial.strategy_name,
                "strategy_version": trial.strategy_version,
                "parameters": dict(trial.parameters),
                "folds": len(trial.fold_results),
                "fold_results": [
                    {
                        "start_date": fold.start_date,
                        "end_date": fold.end_date,
                        "total_return": fold.total_return,
                        "max_drawdown": fold.max_drawdown,
                        "sharpe": fold.sharpe,
                        "rebalance_count": fold.rebalance_count,
                        "closed_positions": fold.closed_positions,
                    }
                    for fold in trial.fold_results
                ],
                "oos_rebalance_months": trial.total_rebalance_months,
                "oos_closed_positions": trial.closed_positions,
                "total_return": sum(fold.total_return for fold in trial.fold_results),
                "profit_factor": (
                    trial.profit_factor
                    if trial.profit_factor is not None
                    and trial.profit_factor != float("inf")
                    else None
                ),
                "sharpe": trial.sharpe,
                "median_calmar": trial.median_fold_calmar,
                "expectancy": trial.expectancy,
                "max_drawdown": trial.max_drawdown,
                "deflated_sharpe_probability": trial.deflated_sharpe_probability,
                "pbo": trial.probability_backtest_overfitting,
                "positive_fold_ratio": trial.positive_fold_ratio,
                "positive_month_ratio": trial.positive_month_ratio,
                "stress_profit_factor": (
                    trial.stress_profit_factor
                    if trial.stress_profit_factor != float("inf")
                    else None
                ),
                "stress_expectancy": trial.stress_expectancy,
                "cost_sensitivity": dict(trial.cost_sensitivity),
                "gross_profit_by_symbol": dict(trial.gross_profit_by_symbol),
                "max_concentration": trial.maximum_concentration,
                "regime_diverse": trial.regime_diverse,
                "parameter_plateau": trial.neighborhood_robust,
                "holdout_evaluations": trial.holdout_evaluations,
                "production_gate_passed": decision.passed if decision else None,
                "production_gate_failures": list(decision.failures) if decision else [],
            }
        )
    (directory / "trial_results.json").write_text(
        json.dumps(payload, indent=2, allow_nan=False, ensure_ascii=False), encoding="utf-8"
    )
    manifest = new_manifest(
        experiment_id=spec.experiment_id,
        spec_dict=spec.to_dict(),
        universe_version=spec.universe_version,
        seed=spec.seed,
        bars=bars,
        code_revision=code_revision,
        metadata={"trial_count": len(trials)},
    )
    (directory / "manifest.json").write_text(
        json.dumps(
            {**asdict(manifest), "created_at": manifest.created_at.isoformat()},
            indent=2,
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        ),
        encoding="utf-8",
    )
    print(f"Nested raporları: {directory}")
    return


def render_gate_table(
    spec: PortfolioExperimentSpec,
    trials: tuple[PortfolioTrialResult, ...],
    gate: PortfolioGate,
) -> str:
    lines = [
        f"# Production Gate Karar Tablosu — {spec.experiment_id}",
        "",
        "Strateji için gate eşikleri (validation fold'ları, base/stress maliyet):",
        "",
        "| # | parametreler | OOS ay | OOS poz. | Sharpe | MaxDD | DSR | PBO "
        "| stress PF | stress Exp | pozitif ay | plato | rejim | kons. | holdout | GATE |",
        "|---|--------------|--------|----------|--------|-------|-----|-----"
        "|-----------|------------|-----------|-------|-------|-------|---------|------|",
    ]
    for index, trial in enumerate(trials, start=1):
        decision = gate.evaluate(trial) if trial.holdout_evaluations else None
        params = ", ".join(f"{k}={v}" for k, v in trial.parameters)
        lines.append(
            f"| {index} | {params} | {trial.total_rebalance_months} | "
            f"{trial.closed_positions} | {trial.sharpe:.2f} | {trial.max_drawdown:.2%} | "
            f"{trial.deflated_sharpe_probability:.2%} | "
            f"{trial.probability_backtest_overfitting:.2%} | "
            f"{trial.stress_profit_factor:.2f} | {trial.stress_expectancy:.2f} | "
            f"{trial.positive_month_ratio:.2%} | {'E' if trial.neighborhood_robust else 'H'} | "
            f"{'E' if trial.regime_diverse else 'H'} | {trial.maximum_concentration:.2%} | "
            f"{'1 kez' if trial.holdout_evaluations else '0'} | "
            f"{'GEÇTİ' if decision and decision.passed else 'KALDI' if decision else '—'} |"
        )
    lines.append("")
    lines.append(
        "Hüküm: holdout açılmadan gate sonucu verilmez; "
        "`--unlock-holdout` yalnız bir kez."
    )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    load_dotenv(ROOT / ".env")
    parser = argparse.ArgumentParser(description="Portföy stratejisi araştırma hattı")
    parser.add_argument(
        "--download",
        action="store_true",
        help="Alpaca'dan veri indir ve araştırma deposuna yaz",
    )
    parser.add_argument(
        "--equity-years",
        type=int,
        default=10,
        help="equity 1d için istenen geçmiş (varsayılan: 10 yıl)",
    )
    parser.add_argument(
        "--crypto-days",
        type=int,
        default=800,
        help="crypto 1h için istenen geçmiş (varsayılan: 800 gün)",
    )
    parser.add_argument(
        "--baseline",
        action="store_true",
        help="üç baseline koşusunu çalıştır (base + stress maliyet karşılaştırması dahil)",
    )
    parser.add_argument(
        "--nested",
        action="store_true",
        help="üç nested validation koşusunu çalıştır (holdout kilitli)",
    )
    parser.add_argument(
        "--unlock-holdout",
        action="store_true",
        help="nested'ta kilitli holdout'u finalistler için bir kez aç",
    )
    parser.add_argument(
        "--skip-equity",
        action="store_true",
        help="equity adımlarını atla (yalnızca kripto)",
    )
    parser.add_argument(
        "--skip-crypto",
        action="store_true",
        help="kripto adımlarını atla (yalnızca equity)",
    )
    args = parser.parse_args(argv)

    if not any((args.download, args.baseline, args.nested)):
        parser.print_help()
        return 0

    code_revision = _git_revision()
    store = VersionedResearchStore(DATA_ROOT)
    config = load_config(str(ROOT / "config" / "settings.yaml"))

    if args.download:
        api_key = os.getenv("ALPACA_API_KEY")
        secret_key = os.getenv("ALPACA_SECRET_KEY")
        if not api_key or not secret_key:
            raise SystemExit(
                "ALPACA_API_KEY / ALPACA_SECRET_KEY environment variables are required"
            )
        history = AlpacaHistoricalData(
            api_key,
            secret_key,
            stock_feed=config.alpaca.stock_feed,
            data_delayed=config.alpaca.data_delayed,
        )
        download_and_persist(
            history,
            store,
            config,
            equity_years=args.equity_years,
            crypto_days=args.crypto_days,
        )

    equity_by_symbol, crypto_by_symbol = load_research_data(store)
    equity_bars = [bar for bars in equity_by_symbol.values() for bar in bars]
    crypto_bars = [bar for bars in crypto_by_symbol.values() for bar in bars]
    if not equity_bars:
        raise SystemExit("Equity araştırma verisi boş")
    if not crypto_bars:
        raise SystemExit("Crypto araştırma verisi boş")

    crypto_primary = [bar for bar in crypto_bars if bar.timeframe == "1h"]
    crypto_universe = build_crypto_point_in_time_universe(
        crypto_primary,
        maximum_symbols=config.universe.crypto.maximum_stream_symbols,
    )

    if args.baseline:
        scenarios = {
            Market.EQUITY: (
                CostScenario("equity_base", Market.EQUITY, 2.0, 4.0),
                CostScenario("equity_stress", Market.EQUITY, 5.0, 10.0),
            ),
            Market.CRYPTO: (
                CostScenario("crypto_base", Market.CRYPTO, 2.0, 6.0, 5.0),
                CostScenario("crypto_stress", Market.CRYPTO, 5.0, 15.0, 10.0),
            ),
        }
        baseline_specs: list[tuple[str, PortfolioStrategy, list[Bar], object | None, str]] = []
        if not args.skip_equity:
            baseline_specs.append(
                (
                    "equity_etf_dual_momentum_v1",
                    EquityEtfDualMomentumV1(
                        dict(BASELINE_PARAMETERS["equity_etf_dual_momentum_v1"])
                    ),
                    equity_bars,
                    None,
                    EQUITY_UNIVERSE_VERSION,
                )
            )
            baseline_specs.append(
                (
                    "equity_time_series_momentum_1d_v1",
                    EquityTimeSeriesMomentum1dV1(
                        dict(BASELINE_PARAMETERS["equity_time_series_momentum_1d_v1"])
                    ),
                    equity_bars,
                    None,
                    EQUITY_UNIVERSE_VERSION,
                )
            )
        if not args.skip_crypto:
            baseline_specs.append(
                (
                    "crypto_cross_sectional_momentum_1h_v1",
                    CryptoCrossSectionalMomentum1hV1(
                        dict(BASELINE_PARAMETERS["crypto_cross_sectional_momentum_1h_v1"])
                    ),
                    crypto_bars,
                    crypto_universe,
                    CRYPTO_UNIVERSE_VERSION,
                )
            )
        for name, strategy, bars, universe, universe_version in baseline_specs:
            market = Market.CRYPTO if strategy.market == Market.CRYPTO else Market.EQUITY
            base_cost, stress_cost = scenarios[market]
            print(f"\n=== BASELINE: {name} ({base_cost.name}) ===")
            result, _engine = run_baseline(
                strategy, bars, base_cost, universe, universe_version
            )
            write_baseline_report(name, result, bars, base_cost, code_revision)
            stress_result, _stress_engine = run_baseline(
                strategy, bars, stress_cost, universe, universe_version
            )
            write_cost_comparison(result, stress_result, base_cost, name)
            report = build_portfolio_report(result, strategy_name=name, strategy_version="1.0.0")
            print(report.render_text())

    if args.nested:
        experiments = [
            (EQUITY_EXPERIMENT, equity_bars, None),
            (TSM_EXPERIMENT, equity_bars, None),
            (CRYPTO_EXPERIMENT, crypto_bars, crypto_universe),
        ]
        if args.skip_equity:
            experiments = [item for item in experiments if "equity" not in item[0].name]
        if args.skip_crypto:
            experiments = [item for item in experiments if "crypto" not in item[0].name]
        for experiment_path, bars, universe in experiments:
            print(f"\n=== NESTED: {experiment_path.name} ===")
            spec, trials = run_nested(
                experiment_path,
                bars,
                universe=universe,
                unlock_holdout=args.unlock_holdout,
            )
            write_nested_reports(spec, trials, bars, code_revision)
            gate = PortfolioGate.for_market(spec.candidate_spaces[0].market)
            table = render_gate_table(spec, trials, gate)
            output_dir = REPORTS_ROOT / "nested" / spec.experiment_id
            (output_dir / "gate_table.md").write_text(table, encoding="utf-8")
            print(table)

    print("\nTamamlandı. Raporlar:", REPORTS_ROOT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
