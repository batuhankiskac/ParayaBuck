#!/usr/bin/env python3
"""Portföy stratejisi araştırması: indirme -> baseline -> nested validation.

Üç yeni portföy stratejisini gerçek veriyle doğrular:

  * equity_etf_dual_momentum_v1          (1d, aylık rebalance)
  * crypto_cross_sectional_momentum_1h_v1 (1h, spot, PIT evren)
  * equity_time_series_momentum_1d_v1    (1d, aylık/haftalık)

Akış:
  1) Veri ResearchDataProvider üzerinden indirilir ve versiyonlu yerel depoya
     (data/research/) yazılır; manifest veri hash'i, feed, adjustment,
     provider metadata ve duplicate audit sayılarını kaydeder. İndirme
     sonrası dataset kalite kapısı (data/dataset_quality.py) fail-closed
     çalışır.
  2) Her strateji için base maliyetle baseline koşusu yapılır. Baseline
     YALNIZCA kilitli holdout başlangıcından önceki barlarla çalışır; tam
     örneklem baseline üretilmez (contamination koruması).
  3) Base/stress maliyet karşılaştırması üretilir.
  4) Kilitli holdout ile nested validation koşulur; DSR/PBO ve production
     gate karar tablosu yazılır. Holdout registry preflight'ı holdout
     barlarına erişimden ÖNCE yapılır ve aynı holdout bir kez açılabilir.

Raporlar reports/portfolio_research/ altındadır. Holdout yalnız
--unlock-holdout ile ve yalnız bir kez açılabilir.

--rerun: yalnız kod doğruluğu etkisini ölçmek için mevcut v1 dataseti ve 36
aday üzerinde equity_dual_momentum_v1_correctness_rerun üretir; holdout
kilitli kalır ve baseline holdout öncesi kesilir.
"""

from __future__ import annotations

import argparse
import json
import os
import time
from dataclasses import asdict, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import yaml
from dotenv import load_dotenv

from parayabuck.backtest.experiments import (
    CostScenario,
    new_manifest,
    outer_holdout_start,
)
from parayabuck.backtest.holdout_registry import HoldoutRegistry
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
from parayabuck.backtest.universe import (
    build_crypto_point_in_time_universe,
    hash_point_in_time_universe,
    verify_persisted_universe,
)
from parayabuck.config import load_config
from parayabuck.core.models import Bar, Market
from parayabuck.data.alpaca import AlpacaHistoricalData
from parayabuck.data.dataset_quality import dataset_ready_for_production
from parayabuck.data.providers import AlpacaResearchDataProvider, ResearchDataProvider
from parayabuck.data.research_store import (
    ProviderMetadata,
    UniverseSnapshotRecord,
    VersionedResearchStore,
)
from parayabuck.markets.symbols import canonical_symbol
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
# Audit dataset: the original split-adjusted, SPY-duplicated dataset. It is
# never modified; the corrected pipeline writes a new dataset id.
EQUITY_DATASET = "equity_etf_1d_v1"
EQUITY_DATASET_CORRECTED = "equity_etf_1d_v1_corrected"
EQUITY_DATASET_V2 = "equity_etf_1d_v2"
CRYPTO_DATASET = "crypto_spot_1h_v1"
EQUITY_EXPERIMENT = ROOT / "config" / "experiments" / "equity_dual_momentum_v1.yaml"
TSM_EXPERIMENT = ROOT / "config" / "experiments" / "equity_tsm_1d_v1.yaml"
V2_EQUITY_EXPERIMENT = ROOT / "config" / "experiments" / "equity_dual_momentum_v2.yaml"
CRYPTO_EXPERIMENT = ROOT / "config" / "experiments" / "crypto_cross_sectional_1h_v1.yaml"
CORRECTNESS_RERUN_ID = "equity_dual_momentum_v1_correctness_rerun"
STARTING_EQUITY = 100_000.0
HOLDOUT_REGISTRY_PATH = ROOT / "data" / "runtime" / "portfolio_holdout_registry.json"

# Research signal price adjustment: equity momentum uses dividend+split
# adjusted (total-return) bars so distributions do not distort signals.
EQUITY_DOWNLOAD_ADJUSTMENT = "all"
CRYPTO_DOWNLOAD_ADJUSTMENT = "splits"

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


def _experiment_end(path: Path) -> datetime:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    parsed = datetime.fromisoformat(str(raw["end"]).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def _locked_holdout_start(experiment_path: Path) -> datetime:
    end: datetime = _experiment_end(experiment_path)
    locked: datetime = outer_holdout_start(end, 6)
    return locked


# ---------------------------------------------------------------------------
# Download + research store
# ---------------------------------------------------------------------------


def download_and_persist(
    equity_provider: ResearchDataProvider,
    crypto_provider: ResearchDataProvider,
    store: VersionedResearchStore,
    *,
    equity_years: int,
    crypto_days: int,
) -> None:
    now = datetime.now(UTC)
    equity_start = now - timedelta(days=equity_years * 365)
    # Canonical, unique download list: SPY is already part of the permanent
    # universe and must never be requested twice.
    equity_symbols = tuple(dict.fromkeys(canonical_symbol(s) for s in EQUITY_ETF_UNIVERSE))
    if len(equity_symbols) != len(set(equity_symbols)):
        raise SystemExit("Duplicate symbols in equity download list")
    assert equity_symbols.count("SPY") == 1
    equity_bars: list[Bar] = []
    for symbol in equity_symbols:
        fetched = equity_provider.fetch_bars(
            symbol,
            "1d",
            start=equity_start,
            end=now,
            adjustment=EQUITY_DOWNLOAD_ADJUSTMENT,
        )
        equity_bars.extend(fetched)
        print(f"  equity {symbol:<5} 1d: {len(fetched):>5} bar")
        time.sleep(0.2)

    crypto_symbols = tuple(
        dict.fromkeys(canonical_symbol(s, Market.CRYPTO) for s in EXECUTION_SUPPORTED_CRYPTO)
    )
    crypto_bars: list[Bar] = []
    crypto_start = now - timedelta(days=crypto_days)
    for symbol in crypto_symbols:
        fetched = crypto_provider.fetch_bars(
            symbol,
            "1h",
            start=crypto_start,
            end=now,
            adjustment=CRYPTO_DOWNLOAD_ADJUSTMENT,
        )
        crypto_bars.extend(fetched)
        print(f"  crypto {symbol:<8} 1h: {len(fetched):>5} bar")
        time.sleep(0.2)

    equity_provider_metadata = equity_provider.metadata("SPY")
    equity_manifest = store.save_dataset(
        EQUITY_DATASET_CORRECTED,
        equity_bars,
        timeframe="1d",
        source_feed=equity_provider_metadata.source_feed,
        adjustment=EQUITY_DOWNLOAD_ADJUSTMENT,
        universe_version=EQUITY_UNIVERSE_VERSION,
        notes=(
            "corrected pipeline: canonical unique symbols, dividend+split "
            f"adjusted; requested {equity_years}y from {equity_start.date()}"
        ),
        provider_metadata=equity_provider_metadata,
    )
    crypto_provider_metadata = crypto_provider.metadata("BTC/USD")
    store.save_dataset(
        CRYPTO_DATASET,
        crypto_bars,
        timeframe="1h",
        source_feed=crypto_provider_metadata.source_feed,
        adjustment=CRYPTO_DOWNLOAD_ADJUSTMENT,
        universe_version=CRYPTO_UNIVERSE_VERSION,
        notes=(
            "execution-supported spot pairs; "
            f"requested {crypto_days}d from {crypto_start.date()}"
        ),
        provider_metadata=crypto_provider_metadata,
    )
    print(
        f"\nEquity dataset  : {EQUITY_DATASET_CORRECTED}  "
        f"{equity_manifest.start.date()} -> {equity_manifest.end.date()}  "
        f"hash={equity_manifest.data_hash[:12]}..."
    )

    equity_by_symbol = store.load_dataset(EQUITY_DATASET_CORRECTED)
    ready, failures = dataset_ready_for_production(
        equity_manifest, equity_by_symbol, benchmark_symbol="SPY", market=Market.EQUITY
    )
    if not ready:
        raise SystemExit(
            "Equity dataset production quality gate FAILED: " + ", ".join(failures)
        )
    print("Equity dataset quality gate: PASSED")


def download_and_persist_v2(
    sip_provider: ResearchDataProvider,
    iex_provider: ResearchDataProvider,
    store: VersionedResearchStore,
    *,
    equity_years: int,
    benchmark_symbol: str = "SPY",
) -> None:
    """Build equity_etf_1d_v2: dividend+split adjusted, quality-gated.

    Feed policy (recorded in the manifest):
    * Alpaca SIP, adjustment=all, 2016-01-01 -> 2025-12-31 (subscription does
      not permit recent SIP data),
    * Alpaca IEX, adjustment=all, 2025-12-01 -> now (overlap month serves as
      the splice reconciliation window).

    Both feeds belong to the same broker and use the same dividend+split
    adjustment policy, so the hybrid is a single total-return series. The
    splice is verified per symbol: Dec-2025 returns must agree between feeds
    within tolerance, otherwise the dataset is rejected.
    """
    from parayabuck.data.providers import reconcile_providers

    now = datetime.now(UTC)
    sip_end = datetime(2025, 12, 31, tzinfo=UTC)
    overlap_start = datetime(2025, 12, 1, tzinfo=UTC)
    equity_symbols = tuple(dict.fromkeys(canonical_symbol(s) for s in EQUITY_ETF_UNIVERSE))
    if len(equity_symbols) != len(set(equity_symbols)):
        raise SystemExit("Duplicate symbols in equity download list")
    assert equity_symbols.count("SPY") == 1
    sip_bars: dict[str, list[Bar]] = {}
    iex_bars: dict[str, list[Bar]] = {}
    for symbol in equity_symbols:
        historical = sip_provider.fetch_bars(
            symbol, "1d", start=datetime(2016, 1, 1, tzinfo=UTC), end=sip_end,
            adjustment=EQUITY_DOWNLOAD_ADJUSTMENT,
        )
        recent = iex_provider.fetch_bars(
            symbol, "1d", start=overlap_start, end=now,
            adjustment=EQUITY_DOWNLOAD_ADJUSTMENT,
        )
        sip_bars[symbol] = historical
        iex_bars[symbol] = recent
        print(
            f"  equity {symbol:<5} 1d: sip={len(historical):>5} "
            f"iex={len(recent):>5} bar"
        )
        time.sleep(0.2)

    # Splice reconciliation: Dec-2025 returns must agree between the two feeds.
    splice_failures: list[str] = []
    for symbol in equity_symbols:
        report = reconcile_providers(
            symbol,
            sip_bars[symbol],
            iex_bars[symbol],
            provider_a="alpaca_sip_all",
            provider_b="alpaca_iex_all",
        )
        if report.overlapping_days < 20:
            splice_failures.append(f"{symbol}: insufficient overlap")
        elif not report.passed:
            splice_failures.append(
                f"{symbol}: max abs return diff {report.max_abs_return_diff:.4f}"
            )
    if splice_failures:
        raise SystemExit(
            "Equity v2 splice reconciliation FAILED: " + "; ".join(splice_failures)
        )
    print("Splice reconciliation (Dec-2025 SIP vs IEX): PASSED")

    merged: list[Bar] = []
    for symbol in equity_symbols:
        merged.extend(bar for bar in sip_bars[symbol] if bar.timestamp <= sip_end)
        merged.extend(bar for bar in iex_bars[symbol] if bar.timestamp > sip_end)
    base_metadata = sip_provider.metadata(benchmark_symbol)
    provider_metadata = ProviderMetadata(
        provider_name=base_metadata.provider_name,
        source_feed="sip+iex_hybrid",
        vendor_symbol=base_metadata.vendor_symbol,
        adjustment_method=base_metadata.adjustment_method,
        currency=base_metadata.currency,
        timezone=base_metadata.timezone,
        exchange_calendar=base_metadata.exchange_calendar,
        downloaded_at=datetime.now(UTC),
        provider_version=base_metadata.provider_version,
        research_provider_differs_from_execution=False,
        notes=(
            "hybrid feed: Alpaca SIP (all) 2016-01-01..2025-12-31 + Alpaca IEX "
            "(all) 2026-01-01..; same broker, same dividend+split adjustment "
            "policy; Dec-2025 feed-overlap reconciliation passed"
        ),
    )
    manifest = store.save_dataset(
        EQUITY_DATASET_V2,
        merged,
        timeframe="1d",
        source_feed=provider_metadata.source_feed,
        adjustment=EQUITY_DOWNLOAD_ADJUSTMENT,
        universe_version=EQUITY_UNIVERSE_VERSION,
        notes=(
            f"equity_etf_1d_v2: total-return (all) hybrid SIP+IEX; "
            f"requested {equity_years}y from 2016-01-01"
        ),
        provider_metadata=provider_metadata,
    )
    print(
        f"\nEquity dataset v2: {manifest.dataset_id}  {manifest.start.date()} -> "
        f"{manifest.end.date()}  hash={manifest.data_hash[:12]}..."
    )
    by_symbol = store.load_dataset(EQUITY_DATASET_V2)
    ready, failures = dataset_ready_for_production(
        manifest, by_symbol, benchmark_symbol=benchmark_symbol, market=Market.EQUITY
    )
    if not ready:
        raise SystemExit(
            "Equity v2 dataset production quality gate FAILED: " + ", ".join(failures)
        )
    print("Equity v2 dataset quality gate: PASSED")
    _write_v2_reconciliation_report(store, by_symbol, manifest)
    return None


def _write_v2_reconciliation_report(
    store: VersionedResearchStore,
    v2_by_symbol: dict[str, list[Bar]],
    v2_manifest: object,
) -> None:
    """Reconcile the new dividend-adjusted dataset against the legacy v1 feed.

    The legacy v1 dataset is split-only, so dividend days are expected to show
    return differences; the report flags them and records the policy delta.
    """
    from parayabuck.data.providers import reconcile_providers

    directory = REPORTS_ROOT / "reconciliation"
    directory.mkdir(parents=True, exist_ok=True)
    if store.manifest(EQUITY_DATASET) is None:
        return
    v1_by_symbol = store.load_dataset(EQUITY_DATASET)
    per_symbol: list[dict[str, object]] = []
    for symbol in sorted(v2_by_symbol):
        reference = v1_by_symbol.get(symbol, [])
        if not reference:
            continue
        report = reconcile_providers(
            symbol,
            v2_by_symbol[symbol],
            reference,
            provider_a="alpaca_sip_all",
            provider_b="alpaca_iex_splits",
        )
        per_symbol.append(
            {
                "symbol": symbol,
                "overlapping_days": report.overlapping_days,
                "mean_abs_return_diff": report.mean_abs_return_diff,
                "max_abs_return_diff": report.max_abs_return_diff,
                "abnormal_day_count": len(report.abnormal_days),
                "likely_corporate_action_days": [
                    item for item in report.abnormal_days if item[3]
                ],
                "unexplained_days": [item for item in report.abnormal_days if not item[3]],
            }
        )
    payload = {
        "policy": (
            "v2 = Alpaca SIP, adjustment=all (dividend+split); "
            "v1 = Alpaca IEX, adjustment=splits. Return differences on "
            "dividend days are EXPECTED and confirm the adjustment change."
        ),
        "per_symbol": per_symbol,
    }
    (directory / "equity_v2_vs_v1.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8"
    )
    print(f"Reconciliation raporu: {directory / 'equity_v2_vs_v1.json'}")


def _verify_persisted_snapshot_hash(
    store: VersionedResearchStore, dataset_id: str, expected_hash: str
) -> None:
    """Hash the PERSISTED snapshot file so a corrupt or missing file fails
    closed instead of silently re-verifying the in-memory universe."""
    if not expected_hash:
        return
    records, methodology, _metadata = store.load_universe_snapshots(dataset_id)
    from parayabuck.backtest.universe import PointInTimeUniverse, UniverseSnapshot

    universe = PointInTimeUniverse(
        Market.CRYPTO,
        tuple(
            UniverseSnapshot(record.effective_at, frozenset(record.symbols))
            for record in records
        ),
        methodology,
    )
    verify_persisted_universe(universe, expected_hash)


def load_research_data(
    store: VersionedResearchStore,
    equity_dataset: str,
) -> tuple[dict[str, list[Bar]], dict[str, list[Bar]]]:
    if store.manifest(equity_dataset) is None or store.manifest(CRYPTO_DATASET) is None:
        raise SystemExit(
            "Araştırma verisi yok. Önce 'python scripts/run_portfolio_research.py --download' "
            "çalıştırın."
        )
    equity = store.load_dataset(equity_dataset)
    crypto = store.load_dataset(CRYPTO_DATASET)
    return equity, crypto


def persist_crypto_universe_snapshots(
    store: VersionedResearchStore,
    crypto_bars: list[Bar],
    maximum_symbols: int,
) -> tuple[object, str]:
    """Build, persist and hash the point-in-time crypto universe."""
    crypto_primary = [bar for bar in crypto_bars if bar.timeframe == "1h"]
    universe = build_crypto_point_in_time_universe(
        crypto_primary,
        maximum_symbols=maximum_symbols,
    )
    records = tuple(
        UniverseSnapshotRecord(snapshot.effective_at, tuple(sorted(snapshot.symbols)))
        for snapshot in universe.snapshots
    )
    first = min((bar.timestamp for bar in crypto_primary), default=datetime.now(UTC))
    metadata = {
        "listing_start": first.isoformat(),
        "trailing_lookback_days": 30,
        "maximum_symbols": maximum_symbols,
        "btc_regime_lag": "n/a",
    }
    store.save_universe_snapshots(
        CRYPTO_DATASET,
        records,
        methodology=universe.methodology,
        metadata=metadata,
    )
    universe_hash = hash_point_in_time_universe(universe)
    print(f"Crypto PIT universe snapshots: {len(records)} persisted, hash={universe_hash[:12]}...")
    return universe, universe_hash


def _dataset_adjustment_and_provider(
    store: VersionedResearchStore, dataset_id: str
) -> tuple[str, str]:
    manifest = store.manifest(dataset_id)
    if manifest is None:
        raise SystemExit(f"Unknown dataset: {dataset_id}")
    if manifest.provider_metadata is not None:
        provider = manifest.provider_metadata.provider_name
    else:
        provider = manifest.source_feed
    return manifest.adjustment, provider


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
    locked_holdout_start: datetime,
    dataset_id: str,
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
            "dataset_id": dataset_id,
            "locked_holdout_start": locked_holdout_start.isoformat(),
            "baseline_cutoff": locked_holdout_start.isoformat(),
            "full_sample_descriptive": False,
            "selection_use_allowed": False,
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
    store: VersionedResearchStore,
    universe: object | None,
    unlock_holdout: bool,
    dataset_id: str,
    adjustment: str,
    source_provider: str,
    universe_snapshot_hash: str = "",
) -> tuple[PortfolioExperimentSpec, tuple[PortfolioTrialResult, ...]]:
    spec = PortfolioExperimentSpec.from_file(experiment_path)
    spec = replace(
        spec,
        source_provider=source_provider,
        adjustment_policy=adjustment,
        universe_snapshot_hash=universe_snapshot_hash or spec.universe_snapshot_hash,
    )
    market = spec.candidate_spaces[0].market
    universe_version = (
        EQUITY_UNIVERSE_VERSION if market == Market.EQUITY else CRYPTO_UNIVERSE_VERSION
    )

    def factory(
        strategy: PortfolioStrategy, cost: CostScenario
    ) -> PortfolioBacktestEngine:
        return make_engine(strategy, cost, universe, universe_version)

    def verifier() -> None:
        if universe is not None and spec.universe_snapshot_hash:
            verify_persisted_universe(universe, spec.universe_snapshot_hash)
            if spec.candidate_spaces[0].market == Market.CRYPTO:
                _verify_persisted_snapshot_hash(
                    store, CRYPTO_DATASET, spec.universe_snapshot_hash
                )

    registry = HoldoutRegistry(HOLDOUT_REGISTRY_PATH)
    trials = run_portfolio_experiment(
        bars,
        spec,
        factory,
        unlock_holdout=unlock_holdout,
        universe_hash_verifier=verifier,
        holdout_registry=registry,
    )
    return spec, trials


def write_nested_reports(
    spec: PortfolioExperimentSpec,
    trials: tuple[PortfolioTrialResult, ...],
    bars: list[Bar],
    code_revision: str,
    dataset_id: str,
) -> None:
    directory = REPORTS_ROOT / "nested" / spec.experiment_id
    directory.mkdir(parents=True, exist_ok=True)
    gate = PortfolioGate.for_market(spec.candidate_spaces[0].market)
    finalists = [
        trial for trial in trials if trial.holdout_evaluations and not trial.holdout_missing
    ]
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
                "unique_oos_calendar_months": trial.unique_oos_calendar_months,
                "oos_closed_positions": trial.closed_positions,
                "total_return": trial.aggregate_oos_return,
                "profit_factor": (
                    trial.profit_factor
                    if trial.profit_factor is not None
                    and trial.profit_factor != float("inf")
                    else None
                ),
                "aggregate_oos_sharpe": trial.aggregate_oos_sharpe,
                "aggregate_oos_sortino": trial.aggregate_oos_sortino,
                "aggregate_oos_calmar": trial.aggregate_oos_calmar,
                "aggregate_oos_max_drawdown": trial.aggregate_oos_max_drawdown,
                "median_fold_sharpe": trial.median_fold_sharpe,
                "median_fold_calmar": trial.median_fold_calmar,
                "expectancy": trial.expectancy,
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
                "neighborhood_report": list(trial.neighborhood_report),
                "duplicate_series_trial_ids": list(trial.duplicate_series_trial_ids),
                "holdout_evaluations": trial.holdout_evaluations,
                "holdout_missing": trial.holdout_missing,
                "holdout_gate_decision": (
                    {
                        "passed": trial.holdout_gate_decision.passed,
                        "failures": list(trial.holdout_gate_decision.failures),
                    }
                    if trial.holdout_gate_decision is not None
                    else None
                ),
                "production_gate_passed": decision.passed if decision else None,
                "production_gate_failures": list(decision.failures) if decision else [],
            }
        )
    (directory / "trial_results.json").write_text(
        json.dumps(payload, indent=2, allow_nan=False, ensure_ascii=False), encoding="utf-8"
    )
    finalist = finalists[0] if finalists else None
    metadata: dict[str, object] = {"trial_count": len(trials), "dataset_id": dataset_id}
    if finalist is not None:
        metadata["holdout_finalist_trial_id"] = finalist.trial_id
        metadata["holdout_finalist_parameters"] = dict(finalist.parameters)
        metadata["max_holdout_finalists"] = spec.max_holdout_finalists
    manifest = new_manifest(
        experiment_id=spec.experiment_id,
        spec_dict=spec.to_dict(),
        universe_version=spec.universe_version,
        seed=spec.seed,
        bars=bars,
        code_revision=code_revision,
        metadata=metadata,
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
        "| # | parametreler | OOS ay | OOS poz. | agg Sharpe | MaxDD | DSR | PBO "
        "| stress PF | stress Exp | pozitif ay | plato | rejim | kons. | holdout | GATE |",
        "|---|--------------|--------|----------|------------|-------|-----|-----"
        "|-----------|------------|-----------|-------|-------|-------|---------|------|",
    ]
    for index, trial in enumerate(trials, start=1):
        decision = gate.evaluate(trial) if trial.holdout_evaluations else None
        params = ", ".join(f"{k}={v}" for k, v in trial.parameters)
        lines.append(
            f"| {index} | {params} | {trial.unique_oos_calendar_months} | "
            f"{trial.closed_positions} | {trial.aggregate_oos_sharpe:.2f} | "
            f"{trial.aggregate_oos_max_drawdown:.2%} | "
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
        help="Alpaca'dan veri indir ve araştırma deposuna yaz (düzeltilmiş pipeline)",
    )
    parser.add_argument(
        "--download-v2",
        action="store_true",
        help=(
            "equity_etf_1d_v2 üret: SIP feed, dividend+split ayarlı, "
            "kalite kapısı + reconciliation raporu"
        ),
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
        help="üç baseline koşusunu çalıştır (yalnız holdout öncesi barlar)",
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
        "--rerun",
        action="store_true",
        help=(
            "mevcut v1 dataseti + 36 aday ile correctness rerun üretir "
            "(equity_dual_momentum_v1_correctness_rerun); holdout kilitli"
        ),
    )
    parser.add_argument(
        "--equity-dataset",
        default="auto",
        help=(
            "equity dataset id "
            "(auto|equity_etf_1d_v1|equity_etf_1d_v1_corrected|equity_etf_1d_v2)"
        ),
    )
    parser.add_argument(
        "--v2",
        action="store_true",
        help=(
            "equity dual momentum'u equity_etf_1d_v2 verisi ve "
            "equity_dual_momentum_v2.yaml (frozen aday + diagnostic ablasyon) ile "
            "çalıştır"
        ),
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

    if not any((args.download, args.download_v2, args.baseline, args.nested, args.rerun)):
        parser.print_help()
        return 0

    code_revision = _git_revision()
    store = VersionedResearchStore(DATA_ROOT)
    config = load_config(str(ROOT / "config" / "settings.yaml"))

    if args.download_v2:
        api_key = os.getenv("ALPACA_API_KEY")
        secret_key = os.getenv("ALPACA_SECRET_KEY")
        if not api_key or not secret_key:
            raise SystemExit(
                "ALPACA_API_KEY / ALPACA_SECRET_KEY environment variables are required"
            )
        sip_history = AlpacaHistoricalData(
            api_key,
            secret_key,
            stock_feed="sip",
            data_delayed=False,
        )
        iex_history = AlpacaHistoricalData(
            api_key,
            secret_key,
            stock_feed="iex",
            data_delayed=False,
        )
        v2_sip_provider = AlpacaResearchDataProvider(
            sip_history,
            feed="sip",
            data_delayed=False,
            research_differs_from_execution=False,
        )
        v2_iex_provider = AlpacaResearchDataProvider(
            iex_history,
            feed="iex",
            data_delayed=False,
            research_differs_from_execution=False,
        )
        download_and_persist_v2(
            v2_sip_provider,
            v2_iex_provider,
            store,
            equity_years=args.equity_years,
        )

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
        equity_provider = AlpacaResearchDataProvider(
            history,
            feed=config.alpaca.stock_feed,
            data_delayed=config.alpaca.data_delayed,
            research_differs_from_execution=False,
        )
        download_and_persist(
            equity_provider,
            equity_provider,
            store,
            equity_years=args.equity_years,
            crypto_days=args.crypto_days,
        )

    equity_dataset = args.equity_dataset
    if equity_dataset == "auto":
        if store.manifest(EQUITY_DATASET_V2) is not None:
            equity_dataset = EQUITY_DATASET_V2
        elif store.manifest(EQUITY_DATASET_CORRECTED) is not None:
            equity_dataset = EQUITY_DATASET_CORRECTED
        elif store.manifest(EQUITY_DATASET) is not None:
            equity_dataset = EQUITY_DATASET
            print(
                f"Uyarı: düzeltilmiş dataset yok; audit dataseti {EQUITY_DATASET} "
                "kullanılıyor (production kalite kapısı uygulanmaz)"
            )
        else:
            raise SystemExit("Equity dataset bulunamadı")

    equity_by_symbol, crypto_by_symbol = load_research_data(store, equity_dataset)
    equity_bars = [bar for bars in equity_by_symbol.values() for bar in bars]
    crypto_bars = [bar for bars in crypto_by_symbol.values() for bar in bars]
    if not equity_bars:
        raise SystemExit("Equity araştırma verisi boş")
    if not crypto_bars:
        raise SystemExit("Crypto araştırma verisi boş")

    crypto_universe, universe_snapshot_hash = persist_crypto_universe_snapshots(
        store,
        crypto_bars,
        maximum_symbols=config.universe.crypto.maximum_stream_symbols,
    )
    equity_adjustment, equity_provider_name = _dataset_adjustment_and_provider(
        store, equity_dataset
    )
    crypto_adjustment, crypto_provider_name = _dataset_adjustment_and_provider(
        store, CRYPTO_DATASET
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
        equity_locked = _locked_holdout_start(EQUITY_EXPERIMENT)
        crypto_locked = _locked_holdout_start(CRYPTO_EXPERIMENT)
        baseline_specs: list[
            tuple[str, PortfolioStrategy, list[Bar], object | None, str, datetime, str]
        ] = []
        if not args.skip_equity:
            pre_holdout_equity = [
                bar for bar in equity_bars if bar.timestamp < equity_locked
            ]
            if len(pre_holdout_equity) != len(equity_bars):
                print(
                    f"Baseline: {len(equity_bars) - len(pre_holdout_equity)} bar "
                    f"holdout sonrasında kesildi ({equity_locked.date()})"
                )
            baseline_specs.append(
                (
                    "equity_etf_dual_momentum_v1",
                    EquityEtfDualMomentumV1(
                        dict(BASELINE_PARAMETERS["equity_etf_dual_momentum_v1"])
                    ),
                    pre_holdout_equity,
                    None,
                    EQUITY_UNIVERSE_VERSION,
                    equity_locked,
                    equity_dataset,
                )
            )
            baseline_specs.append(
                (
                    "equity_time_series_momentum_1d_v1",
                    EquityTimeSeriesMomentum1dV1(
                        dict(BASELINE_PARAMETERS["equity_time_series_momentum_1d_v1"])
                    ),
                    pre_holdout_equity,
                    None,
                    EQUITY_UNIVERSE_VERSION,
                    equity_locked,
                    equity_dataset,
                )
            )
        if not args.skip_crypto:
            pre_holdout_crypto = [bar for bar in crypto_bars if bar.timestamp < crypto_locked]
            baseline_specs.append(
                (
                    "crypto_cross_sectional_momentum_1h_v1",
                    CryptoCrossSectionalMomentum1hV1(
                        dict(BASELINE_PARAMETERS["crypto_cross_sectional_momentum_1h_v1"])
                    ),
                    pre_holdout_crypto,
                    crypto_universe,
                    CRYPTO_UNIVERSE_VERSION,
                    crypto_locked,
                    CRYPTO_DATASET,
                )
            )
        for name, strategy, bars, universe, universe_version, locked, dataset_id in baseline_specs:
            market = Market.CRYPTO if strategy.market == Market.CRYPTO else Market.EQUITY
            base_cost, stress_cost = scenarios[market]
            print(f"\n=== BASELINE: {name} ({base_cost.name}, pre-holdout) ===")
            result, _engine = run_baseline(strategy, bars, base_cost, universe, universe_version)
            write_baseline_report(
                name, result, bars, base_cost, code_revision, locked, dataset_id
            )
            stress_result, _stress_engine = run_baseline(
                strategy, bars, stress_cost, universe, universe_version
            )
            write_cost_comparison(result, stress_result, base_cost, name)
            report = build_portfolio_report(result, strategy_name=name, strategy_version="1.0.0")
            print(report.render_text())

    if args.rerun:
        if args.unlock_holdout:
            raise SystemExit("--rerun holdout'u asla açmaz; --unlock-holdout yasaktır")
        spec = PortfolioExperimentSpec.from_file(EQUITY_EXPERIMENT)
        spec = replace(
            spec,
            experiment_id=CORRECTNESS_RERUN_ID,
            source_provider=equity_provider_name,
            adjustment_policy=equity_adjustment,
            metadata={
                **spec.metadata,
                "purpose": "correctness_rerun",
                "dataset_id": equity_dataset,
                "holdout_locked": True,
            },
        )
        market = spec.candidate_spaces[0].market

        def factory(
            strategy: PortfolioStrategy, cost: CostScenario
        ) -> PortfolioBacktestEngine:
            return make_engine(strategy, cost, None, EQUITY_UNIVERSE_VERSION)

        print(f"\n=== CORRECTNESS RERUN: {CORRECTNESS_RERUN_ID} ===")
        trials = run_portfolio_experiment(
            equity_bars,
            spec,
            factory,
            unlock_holdout=False,
        )
        write_nested_reports(spec, trials, equity_bars, code_revision, equity_dataset)
        gate = PortfolioGate.for_market(market)
        table = render_gate_table(spec, trials, gate)
        output_dir = REPORTS_ROOT / "nested" / spec.experiment_id
        (output_dir / "gate_table.md").write_text(table, encoding="utf-8")
        print(table)

    if args.nested:
        equity_experiment_path = V2_EQUITY_EXPERIMENT if args.v2 else EQUITY_EXPERIMENT
        if args.v2 and equity_dataset != EQUITY_DATASET_V2:
            raise SystemExit(
                "--v2, equity_etf_1d_v2 datasetiyle kullanılmalı "
                "(--equity-dataset equity_etf_1d_v2)"
            )
        experiments = [
            (equity_experiment_path, equity_bars, None, equity_adjustment, equity_provider_name),
            (TSM_EXPERIMENT, equity_bars, None, equity_adjustment, equity_provider_name),
            (
                CRYPTO_EXPERIMENT,
                crypto_bars,
                crypto_universe,
                crypto_adjustment,
                crypto_provider_name,
            ),
        ]
        if args.skip_equity:
            experiments = [item for item in experiments if "equity" not in item[0].name]
        if args.skip_crypto or args.v2:
            # Crypto XSM stays suspended (27 aylık veriyle parametre taraması yok);
            # --v2 koşusu yalnız equity'dir.
            experiments = [item for item in experiments if "crypto" not in item[0].name]
        for experiment_path, bars, universe, adjustment, provider_name in experiments:
            print(f"\n=== NESTED: {experiment_path.name} ===")
            is_crypto_experiment = "crypto" in experiment_path.name
            spec, trials = run_nested(
                experiment_path,
                bars,
                store=store,
                universe=universe,
                unlock_holdout=args.unlock_holdout,
                dataset_id=equity_dataset if not is_crypto_experiment else CRYPTO_DATASET,
                adjustment=adjustment,
                source_provider=provider_name,
                universe_snapshot_hash=universe_snapshot_hash if is_crypto_experiment else "",
            )
            report_dataset_id = CRYPTO_DATASET if is_crypto_experiment else equity_dataset
            write_nested_reports(spec, trials, bars, code_revision, report_dataset_id)
            gate = PortfolioGate.for_market(spec.candidate_spaces[0].market)
            table = render_gate_table(spec, trials, gate)
            output_dir = REPORTS_ROOT / "nested" / spec.experiment_id
            (output_dir / "gate_table.md").write_text(table, encoding="utf-8")
            print(table)

    print("\nTamamlandı. Raporlar:", REPORTS_ROOT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
