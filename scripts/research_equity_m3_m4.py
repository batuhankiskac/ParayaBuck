#!/usr/bin/env python3
"""Research-only M3/M4 equity candidates with a same-run production V3 control.

This file is deliberately outside the portfolio strategy registry.  It never
changes PAPER/LIVE configuration and never promotes a result.  The runner
fails M3 closed unless SPY, QQQ and IWM all cover the requested window, and it
labels every M4 result with the historical/live feed mismatch.

Examples::

    python scripts/research_equity_m3_m4.py --scope audit
    python scripts/research_equity_m3_m4.py --scope recent
    python scripts/research_equity_m3_m4.py --scope full
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import statistics
import subprocess
import sys
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import yaml

_REPO_BOOTSTRAP = Path(__file__).resolve().parents[1]
for _path in (_REPO_BOOTSTRAP / "src", Path(__file__).parent):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from m3_data_gate_helpers import (  # noqa: E402
    CANONICAL_TIMEFRAME,
    EXCHANGE_TIMEZONE,
    NORMALIZER_VERSION,
    RAW_SOURCE_TIMEFRAME,
    SOURCE_ADJUSTMENT,
    SOURCE_API_FAMILY_VERSION,
    SOURCE_ENDPOINT,
    SOURCE_FEED,
    SOURCE_PROVIDER,
    SOURCE_SORT,
    GateError,
    calendar_dependency_identity,
    canonical_digest,
    composite_dataset_identity,
    config_semantic_digest,
    deterministic_candidate_population,
    forbidden_receipt_keys,
    load_csv_reference_rows,
    merge_normalized_rows,
    reference_panel_digest,
    semantic_rows_digest,
    validate_pit_reference_boundary,
)
from research_helpers import git_head, sha256_of_file  # noqa: E402
from run_symmetric_v5_validation import load_corporate_actions_all, load_lifetimes  # noqa: E402

from parayabuck.backtest.numeric_history import build_numeric_history
from parayabuck.backtest.portfolio_engine import PortfolioBacktestEngine
from parayabuck.backtest.product import build_product_universe
from parayabuck.backtest.terminal_aware_engine import TerminalAwareResearchEngine
from parayabuck.backtest.universe import hash_point_in_time_universe
from parayabuck.config import Config, load_config
from parayabuck.core.models import Bar, Market
from parayabuck.data.market_dataset import CorporateAction
from parayabuck.execution.portfolio import PortfolioExecutionPolicy, portfolio_risk_limits_from_config
from parayabuck.portfolio import EquityResidualMomentum1dV3, rebalance_policy_for
from parayabuck.portfolio.base import PortfolioContext
from parayabuck.portfolio.equity_residual_momentum import _volatility
from parayabuck.portfolio.equity_residual_momentum_v3 import percentile_ranks
from parayabuck.portfolio.momentum import annualize_period_volatility

REPO = Path(__file__).resolve().parents[1]
DATASET = REPO / "data/historical/equity_pit_symmetric_2016_2026_v5.csv"
DATASET_MANIFEST = DATASET.with_suffix(".manifest.json")
CA_PATH = REPO / "data/historical/equity_survivorship_full_corporate_actions_v5.csv"
MANIFEST_PATH = REPO / "data/research_manifests/equity_m3_m4_2026_08.json"
OUT_DIR = REPO / "data/research/equity_m3_m4_2026_08"
M3_DATA_GATE_R2_RECEIPT = REPO / "data/research_receipts/equity_m3_data_gate_2026_09_r2.json"
M3_DATA_GATE_R2_MANIFEST = REPO / "data/research_manifests/equity_m3_data_gate_2026_09_r2.json"
M3_DATA_GATE_R2_MANIFEST_PATH = "data/research_manifests/equity_m3_data_gate_2026_09_r2.json"
M3_DATA_GATE_R2_VERIFIER_PATH = "scripts/research_equity_m3_data_gate.py"
M3_DATA_GATE_R2_NORMALIZER_PATH = "scripts/m3_data_gate_helpers.py"
M3_DATA_GATE_R2_CRITICAL_PATHS = (
    M3_DATA_GATE_R2_NORMALIZER_PATH,
    M3_DATA_GATE_R2_VERIFIER_PATH,
    "tests/test_research_equity_m3_data_gate.py",
    ".github/workflows/ci.yml",
)
M3_DATA_GATE_R3_RECEIPT = REPO / "data/research_receipts/equity_m3_data_gate_2026_09_r3.json"
M3_DATA_GATE_R3_MANIFEST = REPO / "data/research_manifests/equity_m3_data_gate_2026_09_r3.json"
M3_DATA_GATE_R3_MANIFEST_PATH = "data/research_manifests/equity_m3_data_gate_2026_09_r3.json"
M3_DATA_GATE_R3_VERIFIER_PATH = "scripts/research_equity_m3_data_gate.py"
M3_DATA_GATE_R3_NORMALIZER_PATH = "scripts/m3_data_gate_helpers.py"
M3_DATA_GATE_R3_BASE_COMMIT = "99fa950a3223a8b6cc78c8a92f8fa20aff52b350"
M3_DATA_GATE_R3_CRITICAL_PATHS = (
    ".github/workflows/ci.yml",
    M3_DATA_GATE_R3_NORMALIZER_PATH,
    M3_DATA_GATE_R3_VERIFIER_PATH,
    "scripts/research_equity_m3_m4.py",
    "scripts/acquire_m3_iwm_raw_bundle.py",
    "scripts/build_m3_pit_scanner_universe.py",
    "tests/test_research_equity_m3_data_gate.py",
    "tests/test_m3_iwm_acquisition.py",
    "tests/test_m3_pit_scanner_universe.py",
)
M3_DATA_GATE_R3_MANDATORY_GATES = (
    "commit_topology",
    "implementation_blob_identity",
    "manifest_only_commit",
    "clean_worktree_at_start",
    "configuration_identity",
    "dependency_identity",
    "acquisition_script_identity",
    "canonical_artifact_identity",
    "lifetime_identity",
    "corporate_action_identity",
    "iwm_raw_bundle",
    "iwm_provider_contract",
    "iwm_pagination_chain",
    "iwm_normalization",
    "iwm_source_completeness",
    "reference_calendar",
    "reference_row_quality",
    "reference_common_sessions",
    "full_weekly_post_warmup_coverage",
    "recent_weekly_coverage",
    "composite_dataset_identity",
    "deterministic_numerical_population",
    "pit_reference_boundary",
    "design_matrix_feasibility",
    "reference_role_contract",
    "receipt_forbidden_fields",
)
# The production research consumer is deliberately R3-only.  R2 remains named
# separately so its immutable historical artifacts can still be audited.
M3_DATA_GATE_RECEIPT = M3_DATA_GATE_R3_RECEIPT

EVAL_END = datetime(2026, 7, 30, tzinfo=UTC)
RECENT_START = datetime(2022, 1, 1, tzinfo=UTC)
REFERENCE_SYMBOLS = ("SPY", "IWM", "QQQ")
HISTORY_DAYS = 400
FROZEN_PARAMETERS: dict[str, object] = {
    "formation_bars": 252,
    "skip_bars": 21,
    "beta_window_bars": 273,
    "residual_vol_window": 63,
    "top_n": 8,
    "max_single_weight": 0.10,
    "ivol_reject_quantile": 0.70,
    "exclude_symbols": ["SPY"],
    "rebalance_frequency": "weekly",
}


class ProxyMultifactorResidualMomentum(EquityResidualMomentum1dV3):
    """M3: V3 pipeline with SPY, IWM-SPY and QQQ-SPY residualization."""

    name = "equity_proxy_multifactor_residual_momentum_v1"
    version = "1.0.0"
    display_name = "Proxy Multifactor Residual Momentum v1 (research only)"

    def __init__(self, parameters: Mapping[str, object]) -> None:
        self.reference_outages = 0
        self.coefficient_samples: list[tuple[float, float, float]] = []
        super().__init__(dict(parameters))

    @staticmethod
    def _close_map(bars: Sequence[Bar], decision: datetime) -> dict[datetime, float]:
        return {bar.timestamp: bar.close for bar in bars if bar.timestamp <= decision and bar.close > 0}

    def _score_candidates(self, context: PortfolioContext):  # type: ignore[no-untyped-def]
        formation = self.param_int("formation_bars", 252)
        skip = self.param_int("skip_bars", 21)
        beta_window = self.param_int("beta_window_bars", 273)
        vol_window = self.param_int("residual_vol_window", 63)
        ivol_quantile = self.param_float("ivol_reject_quantile", 0.70)
        reference_maps = {
            symbol: self._close_map(context.history.get(symbol, ()), context.decision_timestamp)
            for symbol in REFERENCE_SYMBOLS
        }
        if any(len(reference_maps[symbol]) < beta_window + 1 for symbol in REFERENCE_SYMBOLS):
            self.reference_outages += 1
            return (), {}

        excluded = {
            str(symbol).strip().upper() for symbol in (*self.parameters.get("exclude_symbols", []), *REFERENCE_SYMBOLS)
        }
        scored: list[tuple[float, str]] = []
        residual_vols: dict[str, float] = {}
        residuals_by_symbol: dict[str, list[float]] = {}
        minimum_aligned = max(formation + skip, vol_window + 1, 60)
        for symbol in sorted(context.history):
            if symbol in excluded or not context.in_universe(symbol):
                continue
            stock_bars = context.history[symbol]
            if len(stock_bars) < self.minimum_history_bars:
                continue
            stock_map = self._close_map(stock_bars[-(beta_window + 1) :], context.decision_timestamp)
            common = sorted(set(stock_map).intersection(*(set(reference_maps[item]) for item in REFERENCE_SYMBOLS)))
            if len(common) < beta_window + 1:
                continue
            common = common[-(beta_window + 1) :]
            closes = {
                "stock": np.asarray([stock_map[ts] for ts in common], dtype=np.float64),
                **{
                    ref: np.asarray([reference_maps[ref][ts] for ts in common], dtype=np.float64)
                    for ref in REFERENCE_SYMBOLS
                },
            }
            stock_returns = closes["stock"][1:] / closes["stock"][:-1] - 1.0
            spy_returns = closes["SPY"][1:] / closes["SPY"][:-1] - 1.0
            size_returns = closes["IWM"][1:] / closes["IWM"][:-1] - 1.0 - spy_returns
            growth_returns = closes["QQQ"][1:] / closes["QQQ"][:-1] - 1.0 - spy_returns
            if len(stock_returns) < minimum_aligned:
                continue
            factors = np.column_stack((spy_returns, size_returns, growth_returns))
            demeaned_factors = factors - np.mean(factors, axis=0)
            demeaned_stock = stock_returns - np.mean(stock_returns)
            try:
                coefficients, _residual_sum, _rank, _singular = np.linalg.lstsq(
                    demeaned_factors, demeaned_stock, rcond=None
                )
            except np.linalg.LinAlgError:
                continue
            if not np.all(np.isfinite(coefficients)):
                continue
            residuals_array = stock_returns - factors @ coefficients
            residuals = [float(value) for value in residuals_array]
            score = self._candidate_score(residuals, formation=formation, skip=skip)
            residual_vol = _volatility(residuals[-vol_window:])
            if (
                not math.isfinite(score)
                or not math.isfinite(residual_vol)
                or residual_vol <= 0
                or not self._candidate_passes_gate(residuals, score, formation=formation, skip=skip)
            ):
                continue
            scored.append((score, symbol))
            residual_vols[symbol] = annualize_period_volatility(residual_vol, 252)
            residuals_by_symbol[symbol] = residuals
            if len(self.coefficient_samples) < 20_000:
                self.coefficient_samples.append(tuple(float(value) for value in coefficients))

        scored = self._transform_cross_sectional_scores_with_context(
            context,
            scored,
            residuals_by_symbol,
            residual_vols,
            formation=formation,
            skip=skip,
        )
        scored = self._apply_ivol_filter(scored, residual_vols, ivol_quantile)
        scored.sort(key=lambda item: (-item[0], item[1]))
        return tuple(scored), residual_vols


class VolumePersistenceResidualMomentum(EquityResidualMomentum1dV3):
    """M4: 80% V3 consensus + 20% low-attention volume persistence rank."""

    name = "equity_residual_momentum_volume_persistence_v1"
    version = "1.0.0"
    display_name = "Residual Momentum Volume Persistence v1 (research only)"

    def __init__(self, parameters: Mapping[str, object]) -> None:
        self.volume_evaluated = 0
        self.volume_valid = 0
        self.volume_missing_or_zero = 0
        self.volume_ratios: list[float] = []
        super().__init__(dict(parameters))

    def _transform_cross_sectional_scores_with_context(
        self,
        context: PortfolioContext,
        scored: list[tuple[float, str]],
        residuals_by_symbol: dict[str, list[float]],
        residual_vols: dict[str, float],
        *,
        formation: int,
        skip: int,
    ) -> list[tuple[float, str]]:
        v3_scores = super()._transform_cross_sectional_scores_with_context(
            context,
            scored,
            residuals_by_symbol,
            residual_vols,
            formation=formation,
            skip=skip,
        )
        attention: dict[str, float] = {}
        for _score, symbol in v3_scores:
            self.volume_evaluated += 1
            bars = tuple(bar for bar in context.history.get(symbol, ()) if bar.timestamp <= context.decision_timestamp)
            if len(bars) < 273:
                self.volume_missing_or_zero += 1
                continue
            recent = [float(bar.volume) for bar in bars[-84:-21] if float(bar.volume) > 0]
            prior = [float(bar.volume) for bar in bars[-273:-84] if float(bar.volume) > 0]
            if len(recent) != 63 or len(prior) != 189:
                self.volume_missing_or_zero += 1
                continue
            prior_median = statistics.median(prior)
            recent_median = statistics.median(recent)
            if prior_median <= 0 or recent_median <= 0:
                self.volume_missing_or_zero += 1
                continue
            ratio = recent_median / prior_median
            if not math.isfinite(ratio) or ratio <= 0:
                self.volume_missing_or_zero += 1
                continue
            attention[symbol] = -math.log(ratio)
            self.volume_valid += 1
            if len(self.volume_ratios) < 50_000:
                self.volume_ratios.append(ratio)
        persistence_rank = percentile_ranks(attention)
        return [
            (0.80 * score + 0.20 * persistence_rank[symbol], symbol)
            for score, symbol in v3_scores
            if symbol in persistence_rank
        ]


def _load_window(
    start: datetime | None,
    end: datetime,
    *,
    collect_bars: bool,
) -> tuple[list[Bar], dict[str, dict[str, object]]]:
    earliest = None if start is None else start - timedelta(days=HISTORY_DAYS)
    bars: list[Bar] = []
    coverage = {symbol: {"count": 0, "first": None, "last": None, "zero_volume": 0} for symbol in REFERENCE_SYMBOLS}
    with DATASET.open(newline="") as handle:
        for row in csv.DictReader(handle):
            timestamp = datetime.fromisoformat(row["timestamp"].replace("Z", "+00:00"))
            symbol = row["symbol"]
            if symbol in coverage:
                item = coverage[symbol]
                item["count"] = int(item["count"]) + 1
                item["first"] = item["first"] or timestamp.isoformat()
                item["last"] = timestamp.isoformat()
                if float(row.get("volume") or 0.0) <= 0:
                    item["zero_volume"] = int(item["zero_volume"]) + 1
            if not collect_bars or timestamp > end:
                continue
            if earliest is not None and timestamp < earliest:
                continue
            bars.append(
                Bar(
                    timestamp=timestamp,
                    symbol=symbol,
                    timeframe=row.get("timeframe", "1d") or "1d",
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                    volume=float(row.get("volume") or 0.0),
                    market=Market.EQUITY,
                )
            )
    bars.sort(key=lambda bar: (bar.timestamp, bar.symbol))
    return bars, coverage


def _make_engine(config: Config, strategy: EquityResidualMomentum1dV3, universe, terminal_map):  # type: ignore[no-untyped-def]
    return TerminalAwareResearchEngine(
        strategy=strategy,
        rebalance_policy=rebalance_policy_for(strategy),
        terminal_map=dict(terminal_map),
        policy="T1",
        starting_equity=config.backtest.initial_cash,
        slippage_bps=config.backtest.slippage_bps,
        spread_bps=config.backtest.spread_bps,
        commission=config.backtest.commission,
        equity_taf_per_share=config.backtest.equity_taf_per_share,
        equity_taf_cap=config.backtest.equity_taf_cap,
        equity_cat_per_share=config.backtest.equity_cat_per_share,
        equity_sec_fee_rate=config.backtest.equity_sec_fee_rate,
        equity_use_effective_dated_fees=config.backtest.equity_use_effective_dated_fees,
        benchmark_symbol="SPY",
        risk_limits=portfolio_risk_limits_from_config(config.risk),
        maximum_allocation=config.strategy.equity.maximum_allocation,
        execution_policy=PortfolioExecutionPolicy(
            rebalance_band_pct=config.execution.rebalance_band_pct,
            minimum_order_notional=config.execution.minimum_order_notional,
        ),
        enforce_drawdown_halt=False,
        universe=universe,
        universe_version=hash_point_in_time_universe(universe),
    )


def _stats(result) -> dict[str, object]:  # type: ignore[no-untyped-def]
    return {
        "return": round(result.net_return, 6),
        "cagr": round(result.cagr, 6),
        "sharpe": round(result.sharpe, 6),
        "sortino": round(result.sortino, 6),
        "calmar": round(result.calmar, 6),
        "max_drawdown": round(result.max_drawdown, 6),
        "turnover": round(result.turnover, 6),
        "average_cash_ratio": round(result.average_cash_ratio, 6),
        "rebalance_count": result.rebalance_count,
        "fill_count": len(result.fills),
        "ending_equity": round(result.ending_equity, 2),
    }


def _prepare(config: Config, bars: list[Bar], start: datetime | None, end: datetime):  # type: ignore[no-untyped-def]
    available = [bar for bar in bars if bar.timestamp <= end]
    filtered = [bar for bar in available if start is None or bar.timestamp >= start]
    warmup = [bar for bar in available if start is not None and bar.timestamp < start]
    ordered = sorted(PortfolioBacktestEngine._deduplicate(filtered), key=lambda bar: (bar.timestamp, bar.symbol))
    warmup = sorted(PortfolioBacktestEngine._deduplicate(warmup), key=lambda bar: (bar.timestamp, bar.symbol))
    universe = build_product_universe(config, [*warmup, *ordered], market=Market.EQUITY, end=end, timeframe="1d")
    numeric = build_numeric_history([*warmup, *ordered], market=Market.EQUITY, timeframe="1d")
    return ordered, warmup, universe, numeric


def _run(
    config: Config,
    strategy: EquityResidualMomentum1dV3,
    prepared,
    terminal_map,
    actions: list[CorporateAction],
    start: datetime | None,
    end: datetime,
):  # type: ignore[no-untyped-def]
    ordered, warmup, universe, numeric = prepared
    engine = _make_engine(config, strategy, universe, terminal_map)
    relevant_actions = [
        action for action in actions if (start is None or action.timestamp >= start) and action.timestamp <= end
    ]
    result = engine.run(
        ordered,
        warmup=warmup,
        corporate_actions=relevant_actions,
        prepared_inputs=(ordered, ordered),
        numeric_history=numeric,
    )
    return result, universe


def _feed_evidence(config: Config, coverage: Mapping[str, Mapping[str, object]]) -> dict[str, object]:
    manifest = json.loads(DATASET_MANIFEST.read_text())
    historical_feed = str(manifest.get("feed", "unknown")).lower()
    live_feed = str(config.alpaca.stock_stream_feed).lower()
    return {
        "historical_feed": historical_feed,
        "live_stream_feed": live_feed,
        "feed_match": historical_feed == live_feed,
        "canonical_historical_iex_panel_available": historical_feed == "iex",
        "sip_iex_rank_overlap": None,
        "promotion_blocked": historical_feed != live_feed,
        "reason": (
            "Canonical volume is SIP while PAPER/LIVE stock streaming is IEX; no historical IEX panel exists."
            if historical_feed != live_feed
            else "Historical and live feed labels match."
        ),
        "reference_coverage": coverage,
    }


def _r2_git_blob(commit: str, path: str) -> tuple[str, str, bytes]:
    """Read one Git blob and return its object and content identities."""
    blob = subprocess.run(
        ["git", "cat-file", "blob", f"{commit}:{path}"],
        cwd=REPO,
        check=True,
        capture_output=True,
    ).stdout
    object_id = subprocess.run(
        ["git", "rev-parse", f"{commit}:{path}"],
        cwd=REPO,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    return object_id, hashlib.sha256(blob).hexdigest(), blob


def _r2_git_text(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=REPO, check=True, capture_output=True, text=True).stdout.strip()


def _r2_relative_path(value: object) -> Path | None:
    if not isinstance(value, str) or not value or Path(value).is_absolute():
        return None
    candidate = (REPO / value).resolve()
    try:
        candidate.relative_to(REPO.resolve())
    except ValueError:
        return None
    return candidate


def _valid_m3_data_gate_r2_receipt() -> bool:
    """Legacy audit helper for the immutable R2 receipt."""
    required_gate_order = (
        "commit_topology",
        "implementation_blob_identity",
        "clean_worktree",
        "configuration_identity",
        "dependency_identity",
        "canonical_artifact_identity",
        "lifetime_identity",
        "corporate_action_identity",
        "iwm_raw_bundle",
        "iwm_provider_contract",
        "iwm_pagination_chain",
        "iwm_normalization",
        "reference_calendar",
        "reference_row_quality",
        "reference_common_sessions",
        "weekly_coverage",
        "composite_dataset_identity",
        "deterministic_numerical_population",
        "design_matrix_feasibility",
        "reference_role_contract",
        "receipt_forbidden_fields",
    )
    required_gates = set(required_gate_order)
    source_identity = {
        "provider": SOURCE_PROVIDER,
        "api_family_version": SOURCE_API_FAMILY_VERSION,
        "endpoint_identity": SOURCE_ENDPOINT,
        "feed": SOURCE_FEED,
        "adjustment": SOURCE_ADJUSTMENT,
        "source_timeframe": CANONICAL_TIMEFRAME,
        "sort": SOURCE_SORT,
    }
    try:
        payload = json.loads(M3_DATA_GATE_R2_RECEIPT.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            return False
        if forbidden_receipt_keys(payload):
            return False
        required = {
            "schema_version": "2.0.0",
            "mission": "equity_m3_data_gate_2026_09_r2",
            "data_gate_verdict": "M3_DATA_GATE_PASS",
            "corrective_infrastructure_verdict": "PASS",
            "research_only": True,
            "automatic_promotion": False,
            "human_promotion_only": True,
            "candidate_economics_run": False,
            "economic_metrics_present": False,
            "economic_mission_ready": False,
            "next_required_gate": "M3_CANDIDATE_OCP_GATE",
            "m3_candidate_ocp_status": "NOT_RUN_OUT_OF_SCOPE",
            "diagnostic_only": False,
        }
        if any(payload.get(key) != expected for key, expected in required.items()):
            return False
        recorded_digest = payload.get("self_digest_sha256")
        if not isinstance(recorded_digest, str):
            return False
        without_digest = dict(payload)
        without_digest.pop("self_digest_sha256", None)
        if recorded_digest != canonical_digest(without_digest):
            return False

        gate_results = payload.get("mandatory_gate_results")
        if not isinstance(gate_results, Mapping) or set(gate_results) != required_gates:
            return False
        if any(
            not isinstance(gate_results[name], Mapping) or gate_results[name].get("status") != "PASS"
            for name in required_gates
        ):
            return False

        lineage = payload.get("base_lineage")
        if not isinstance(lineage, Mapping):
            return False
        base = str(lineage.get("corrective_base_commit", ""))
        implementation = str(lineage.get("implementation_a2_commit", ""))
        run_commit = str(lineage.get("run_b2_commit", ""))
        if not all(len(value) == 40 for value in (base, implementation, run_commit)):
            return False
        if _r2_git_text("rev-parse", f"{implementation}^") != base:
            return False
        if _r2_git_text("rev-parse", f"{run_commit}^") != implementation:
            return False
        changed_paths = _r2_git_text("diff-tree", "--no-commit-id", "--name-only", "-r", run_commit).splitlines()
        if changed_paths != [M3_DATA_GATE_R2_MANIFEST_PATH]:
            return False

        manifest_identity = payload.get("manifest_identity")
        if not isinstance(manifest_identity, Mapping):
            return False
        if manifest_identity.get("path") != M3_DATA_GATE_R2_MANIFEST_PATH:
            return False
        manifest_blob, manifest_sha, manifest_bytes = _r2_git_blob(run_commit, M3_DATA_GATE_R2_MANIFEST_PATH)
        if manifest_identity.get("git_blob_id") != manifest_blob:
            return False
        if manifest_identity.get("raw_sha256") != manifest_sha:
            return False
        if M3_DATA_GATE_R2_MANIFEST.read_bytes() != manifest_bytes:
            return False
        manifest = json.loads(manifest_bytes.decode("utf-8"))
        if not isinstance(manifest, Mapping):
            return False
        if manifest.get("schema_version") != "2.0.0" or manifest.get("mission") != "equity_m3_data_gate_2026_09_r2":
            return False
        if manifest.get("mandatory_data_pass_gates") != list(required_gate_order):
            return False
        manifest_ocp = manifest.get("ocp_boundary")
        if not isinstance(manifest_ocp, Mapping):
            return False
        if manifest_ocp.get("observer_execution_strategy_identity_must_match") is not True:
            return False
        if manifest_ocp.get("observer_execution_object_instance_must_differ") is not True:
            return False
        if manifest.get("implementation_commit") != implementation:
            return False
        implementation_tree = _r2_git_text("rev-parse", f"{implementation}^{{tree}}")
        if manifest.get("implementation_tree") != implementation_tree:
            return False
        if payload.get("implementation_tree") != implementation_tree:
            return False

        critical = payload.get("critical_git_blobs")
        manifest_critical = manifest.get("critical_git_blobs")
        if not isinstance(critical, Mapping) or not isinstance(manifest_critical, Mapping):
            return False
        if set(critical) != set(M3_DATA_GATE_R2_CRITICAL_PATHS):
            return False
        if set(manifest_critical) != set(critical):
            return False
        current_head = _r2_git_text("rev-parse", "HEAD")
        for path in M3_DATA_GATE_R2_CRITICAL_PATHS:
            declaration = critical.get(path)
            manifest_declaration = manifest_critical.get(path)
            if not isinstance(declaration, Mapping) or declaration != manifest_declaration:
                return False
            for commit in (implementation, run_commit, current_head):
                blob_id, raw_sha, _blob = _r2_git_blob(commit, path)
                if declaration.get("git_blob_id") != blob_id or declaration.get("raw_sha256") != raw_sha:
                    return False
        for identity_key, path in (
            ("verifier_identity", M3_DATA_GATE_R2_VERIFIER_PATH),
            ("normalizer_identity", M3_DATA_GATE_R2_NORMALIZER_PATH),
        ):
            identity = payload.get(identity_key)
            if not isinstance(identity, Mapping) or identity.get("path") != path:
                return False
            if identity.get("git_blob") != critical.get(path):
                return False

        config_digest = payload.get("config_semantic_digest_sha256")
        if not isinstance(config_digest, str):
            return False
        if manifest.get("config_semantic_digest_sha256") != config_digest:
            return False
        config_data = yaml.safe_load((REPO / "config/settings.yaml").read_text(encoding="utf-8"))
        if not isinstance(config_data, Mapping) or config_semantic_digest(config_data) != config_digest:
            return False
        calendar_identity = payload.get("calendar_identity")
        if not isinstance(calendar_identity, Mapping) or dict(calendar_identity) != calendar_dependency_identity():
            return False
        if manifest.get("calendar_identity") != calendar_identity:
            return False

        input_provenance = payload.get("input_provenance")
        canonical_provenance = input_provenance.get("canonical_bars") if isinstance(input_provenance, Mapping) else None
        if not isinstance(canonical_provenance, Mapping):
            return False
        canonical_raw_sha = canonical_provenance.get("raw_sha256")
        if not isinstance(canonical_raw_sha, str) or sha256_of_file(DATASET) != canonical_raw_sha:
            return False
        canonical_manifest_blob = canonical_provenance.get("manifest_git_blob")
        if not isinstance(canonical_manifest_blob, Mapping):
            return False
        canonical_manifest_path = DATASET_MANIFEST.relative_to(REPO).as_posix()
        actual_manifest_blob, actual_manifest_sha, actual_manifest_bytes = _r2_git_blob(
            current_head, canonical_manifest_path
        )
        if canonical_manifest_blob.get("git_blob_id") != actual_manifest_blob:
            return False
        if canonical_manifest_blob.get("git_blob_raw_sha256") != actual_manifest_sha:
            return False
        canonical_manifest = json.loads(actual_manifest_bytes.decode("utf-8"))
        if not isinstance(canonical_manifest, Mapping) or canonical_manifest.get("dataset_sha256") != canonical_raw_sha:
            return False

        composite = payload.get("composite_dataset_identity")
        if not isinstance(composite, Mapping) or composite.get("status") != "PASS":
            return False
        identity = composite.get("identity")
        sidecar = composite.get("sidecar")
        if not isinstance(identity, Mapping) or not isinstance(sidecar, Mapping):
            return False
        sidecar_path = _r2_relative_path(sidecar.get("path"))
        if sidecar_path is None or sidecar_path.is_symlink() or not sidecar_path.is_file():
            return False
        sidecar_raw_sha = sha256_of_file(sidecar_path)
        if sidecar.get("raw_sha256") != sidecar_raw_sha:
            return False
        if identity.get("iwm_normalized_raw_sha256") != sidecar_raw_sha:
            return False
        sidecar_rows, sidecar_quality = load_csv_reference_rows(sidecar_path, reference_symbols=("IWM",))
        if sidecar_quality.get("IWM", {}).get("rejected_row_count", 0) != 0:
            return False
        if sidecar.get("normalized_semantic_row_sha256") != semantic_rows_digest(sidecar_rows["IWM"]):
            return False
        if identity.get("iwm_normalized_semantic_row_sha256") != sidecar.get("normalized_semantic_row_sha256"):
            return False
        if identity.get("canonical_parent_raw_sha256") != canonical_raw_sha:
            return False
        if identity.get("canonical_parent_normalized_sha256") != canonical_provenance.get("crlf_normalized_sha256"):
            return False
        if identity.get("canonical_manifest_git_blob") != actual_manifest_blob:
            return False
        normalizer = critical[M3_DATA_GATE_R2_NORMALIZER_PATH]
        if not isinstance(normalizer, Mapping):
            return False
        if identity.get("normalizer_git_blob") != normalizer.get("git_blob_id"):
            return False
        if identity.get("normalizer_sha256") != normalizer.get("raw_sha256"):
            return False
        if identity.get("schema_version") != NORMALIZER_VERSION:
            return False
        if identity.get("canonical_timeframe") != CANONICAL_TIMEFRAME:
            return False
        if identity.get("source_feed") != SOURCE_FEED or identity.get("adjustment_mode") != SOURCE_ADJUSTMENT:
            return False
        if identity.get("exchange_timezone") != EXCHANGE_TIMEZONE:
            return False
        if identity.get("calendar_identity") != calendar_identity:
            return False
        if identity.get("source_range") != {"start": "2016-01-01", "end": "2026-07-31"}:
            return False
        if identity.get("evaluation_range") != {"end_inclusive": "2026-07-30"}:
            return False
        recomputed_identity = composite_dataset_identity(
            canonical_parent_raw_sha256=str(identity["canonical_parent_raw_sha256"]),
            canonical_parent_normalized_sha256=str(identity["canonical_parent_normalized_sha256"]),
            canonical_manifest_git_blob=str(identity["canonical_manifest_git_blob"]),
            iwm_raw_bundle_sha256=str(identity["iwm_raw_bundle_sha256"]),
            iwm_normalized_raw_sha256=str(identity["iwm_normalized_raw_sha256"]),
            iwm_normalized_semantic_row_sha256=str(identity["iwm_normalized_semantic_row_sha256"]),
            normalizer_git_blob=str(identity["normalizer_git_blob"]),
            normalizer_sha256=str(identity["normalizer_sha256"]),
            schema_version=str(identity["schema_version"]),
            canonical_timeframe=str(identity["canonical_timeframe"]),
            source_feed=str(identity["source_feed"]),
            adjustment_mode=str(identity["adjustment_mode"]),
            exchange_timezone=str(identity["exchange_timezone"]),
            calendar_identity=identity["calendar_identity"],  # type: ignore[arg-type]
            source_range=identity["source_range"],  # type: ignore[arg-type]
            evaluation_range=identity["evaluation_range"],  # type: ignore[arg-type]
        )
        if dict(recomputed_identity) != dict(identity):
            return False
        iwm_source = payload.get("iwm_raw_source")
        if not isinstance(iwm_source, Mapping) or iwm_source.get("status") != "PASS":
            return False
        if iwm_source.get("raw_bundle_sha256") != identity.get("iwm_raw_bundle_sha256"):
            return False

        canonical_rows, canonical_quality = load_csv_reference_rows(DATASET, reference_symbols=REFERENCE_SYMBOLS)
        if any(item.get("rejected_row_count", 0) != 0 for item in canonical_quality.values()):
            return False
        merged_iwm, _merge_hashes = merge_normalized_rows(canonical_rows["IWM"], sidecar_rows["IWM"])
        panel_rows = {
            "SPY": canonical_rows["SPY"],
            "IWM": merged_iwm,
            "QQQ": canonical_rows["QQQ"],
        }
        panel = payload.get("reference_panel")
        if not isinstance(panel, Mapping):
            return False
        actual_panel_digest = reference_panel_digest(
            [row for symbol in REFERENCE_SYMBOLS for row in panel_rows[symbol]],
            source_identity=source_identity,
        )
        actual_evaluation_digest = reference_panel_digest(
            [row for symbol in REFERENCE_SYMBOLS for row in panel_rows[symbol] if row.session_date <= EVAL_END.date()],
            source_identity=source_identity,
        )
        if panel.get("normalized_reference_panel_sha256") != actual_panel_digest:
            return False
        if panel.get("evaluation_reference_panel_sha256") != actual_evaluation_digest:
            return False

        matrix = payload.get("design_matrix_summary")
        if not isinstance(matrix, Mapping):
            return False
        total = matrix.get("total_matrix_count")
        passed = matrix.get("pass_count")
        failed = matrix.get("fail_count")
        if not isinstance(total, int) or total <= 0 or passed != total or failed != 0:
            return False
        if payload.get("forbidden_economic_fields") != []:
            return False
        return True
    except (
        OSError,
        UnicodeDecodeError,
        json.JSONDecodeError,
        GateError,
        KeyError,
        TypeError,
        ValueError,
        subprocess.CalledProcessError,
    ):
        return False


def _valid_m3_data_gate_r3_receipt(payload: Mapping[str, object]) -> bool:
    """Accept only a Git-, data-, PIT-, and matrix-bound R3 data PASS."""
    required_gate_order = M3_DATA_GATE_R3_MANDATORY_GATES
    source_identity = {
        "provider": SOURCE_PROVIDER,
        "api_family_version": SOURCE_API_FAMILY_VERSION,
        "endpoint_identity": SOURCE_ENDPOINT,
        "feed": SOURCE_FEED,
        "adjustment": SOURCE_ADJUSTMENT,
        "source_timeframe": CANONICAL_TIMEFRAME,
        "sort": SOURCE_SORT,
    }
    try:
        if forbidden_receipt_keys(payload):
            return False
        required = {
            "schema_version": "3.0.0",
            "mission": "equity_m3_data_gate_2026_09_r3",
            "data_gate_verdict": "M3_DATA_GATE_PASS",
            "corrective_infrastructure_verdict": "PASS",
            "research_only": True,
            "automatic_promotion": False,
            "human_promotion_only": True,
            "candidate_economics_run": False,
            "economic_metrics_present": False,
            "economic_mission_ready": False,
            "next_required_gate": "M3_CANDIDATE_OCP_GATE",
            "m3_candidate_ocp_status": "NOT_RUN_OUT_OF_SCOPE",
            "diagnostic_only": False,
        }
        if any(payload.get(key) != expected for key, expected in required.items()):
            return False
        recorded_digest = payload.get("self_digest_sha256")
        if not isinstance(recorded_digest, str):
            return False
        without_digest = dict(payload)
        without_digest.pop("self_digest_sha256", None)
        if recorded_digest != canonical_digest(without_digest):
            return False

        gate_results = payload.get("mandatory_gate_results")
        if not isinstance(gate_results, Mapping) or set(gate_results) != set(required_gate_order):
            return False
        if any(
            not isinstance(gate_results[name], Mapping) or gate_results[name].get("status") != "PASS"
            for name in required_gate_order
        ):
            return False

        lineage = payload.get("base_lineage")
        if not isinstance(lineage, Mapping):
            return False
        base = str(lineage.get("corrective_base_commit", ""))
        implementation = str(lineage.get("implementation_a3_commit", ""))
        run_commit = str(lineage.get("run_b3_commit", ""))
        if lineage.get("c3_commit") is not None or not all(
            len(value) == 40 for value in (base, implementation, run_commit)
        ):
            return False
        if base != M3_DATA_GATE_R3_BASE_COMMIT:
            return False
        if _r2_git_text("rev-parse", f"{implementation}^") != base:
            return False
        if _r2_git_text("rev-parse", f"{run_commit}^") != implementation:
            return False
        if _r2_git_text("diff-tree", "--no-commit-id", "--name-only", "-r", run_commit).splitlines() != [
            M3_DATA_GATE_R3_MANIFEST_PATH
        ]:
            return False

        manifest_identity = payload.get("manifest_identity")
        if not isinstance(manifest_identity, Mapping) or manifest_identity.get("path") != M3_DATA_GATE_R3_MANIFEST_PATH:
            return False
        manifest_blob, manifest_sha, manifest_bytes = _r2_git_blob(run_commit, M3_DATA_GATE_R3_MANIFEST_PATH)
        if manifest_identity.get("git_blob_id") != manifest_blob or manifest_identity.get("raw_sha256") != manifest_sha:
            return False
        if not M3_DATA_GATE_R3_MANIFEST.is_file() or M3_DATA_GATE_R3_MANIFEST.read_bytes() != manifest_bytes:
            return False
        manifest = json.loads(manifest_bytes.decode("utf-8"))
        if not isinstance(manifest, Mapping):
            return False
        if (
            manifest.get("schema_version") != "3.0.0"
            or manifest.get("mission") != "equity_m3_data_gate_2026_09_r3"
            or manifest.get("declared_before_results") is not True
            or manifest.get("mandatory_data_pass_gates") != list(required_gate_order)
            or manifest.get("implementation_commit") != implementation
        ):
            return False
        implementation_tree = _r2_git_text("rev-parse", f"{implementation}^{{tree}}")
        if (
            manifest.get("implementation_tree") != implementation_tree
            or payload.get("implementation_tree") != implementation_tree
        ):
            return False

        critical = payload.get("critical_git_blobs")
        manifest_critical = manifest.get("critical_git_blobs")
        if (
            not isinstance(critical, Mapping)
            or not isinstance(manifest_critical, Mapping)
            or set(critical) != set(M3_DATA_GATE_R3_CRITICAL_PATHS)
            or dict(manifest_critical) != dict(critical)
        ):
            return False
        current_head = _r2_git_text("rev-parse", "HEAD")
        for path in M3_DATA_GATE_R3_CRITICAL_PATHS:
            declaration = critical.get(path)
            if not isinstance(declaration, Mapping):
                return False
            for commit in (implementation, run_commit, current_head):
                blob_id, raw_sha, _blob = _r2_git_blob(commit, path)
                if declaration.get("git_blob_id") != blob_id or declaration.get("raw_sha256") != raw_sha:
                    return False
        for identity_key, path in (
            ("verifier_identity", M3_DATA_GATE_R3_VERIFIER_PATH),
            ("normalizer_identity", M3_DATA_GATE_R3_NORMALIZER_PATH),
            ("acquisition_script_identity", "scripts/acquire_m3_iwm_raw_bundle.py"),
            ("pit_builder_identity", "scripts/build_m3_pit_scanner_universe.py"),
        ):
            identity = payload.get(identity_key)
            if not isinstance(identity, Mapping) or dict(identity) != dict(critical[path]):
                return False

        config_digest = payload.get("config_semantic_digest_sha256")
        if not isinstance(config_digest, str) or manifest.get("config_semantic_digest_sha256") != config_digest:
            return False
        config_data = yaml.safe_load((REPO / "config/settings.yaml").read_text(encoding="utf-8"))
        if not isinstance(config_data, Mapping) or config_semantic_digest(config_data) != config_digest:
            return False
        calendar_identity = payload.get("calendar_identity")
        if not isinstance(calendar_identity, Mapping) or dict(calendar_identity) != calendar_dependency_identity():
            return False
        if manifest.get("calendar_identity") != calendar_identity:
            return False

        input_provenance = payload.get("input_provenance")
        canonical_provenance = input_provenance.get("canonical_bars") if isinstance(input_provenance, Mapping) else None
        if not isinstance(canonical_provenance, Mapping):
            return False
        canonical_raw_sha = canonical_provenance.get("raw_sha256")
        if not isinstance(canonical_raw_sha, str) or sha256_of_file(DATASET) != canonical_raw_sha:
            return False
        canonical_manifest_path = DATASET_MANIFEST.relative_to(REPO).as_posix()
        actual_manifest_blob, actual_manifest_sha, actual_manifest_bytes = _r2_git_blob(
            current_head, canonical_manifest_path
        )
        declared_manifest = canonical_provenance.get("manifest_git_blob")
        if (
            not isinstance(declared_manifest, Mapping)
            or declared_manifest.get("git_blob_id") != actual_manifest_blob
            or declared_manifest.get("git_blob_raw_sha256") != actual_manifest_sha
        ):
            return False
        canonical_manifest = json.loads(actual_manifest_bytes.decode("utf-8"))
        if not isinstance(canonical_manifest, Mapping) or canonical_manifest.get("dataset_sha256") != canonical_raw_sha:
            return False

        iwm_source = payload.get("iwm_raw_source")
        if not isinstance(iwm_source, Mapping) or iwm_source.get("status") != "PASS":
            return False
        source = iwm_source.get("source_identity")
        if not isinstance(source, Mapping):
            return False
        for key, expected in (
            ("provider", SOURCE_PROVIDER),
            ("api_family_version", SOURCE_API_FAMILY_VERSION),
            ("endpoint_identity", SOURCE_ENDPOINT),
            ("feed", SOURCE_FEED),
            ("adjustment", SOURCE_ADJUSTMENT),
            ("source_timeframe", RAW_SOURCE_TIMEFRAME),
            ("sort", SOURCE_SORT),
        ):
            if source.get(key) != expected:
                return False

        composite = payload.get("composite_dataset_identity")
        if not isinstance(composite, Mapping) or composite.get("status") != "PASS":
            return False
        identity = composite.get("identity")
        sidecar = composite.get("sidecar")
        if not isinstance(identity, Mapping) or not isinstance(sidecar, Mapping):
            return False
        composite_path = _r2_relative_path(composite.get("manifest_path"))
        if composite_path is None or composite_path.is_symlink() or not composite_path.is_file():
            return False
        if composite.get("manifest_sha256") != sha256_of_file(composite_path):
            return False
        sidecar_path = _r2_relative_path(sidecar.get("path"))
        if sidecar_path is None or sidecar_path.is_symlink() or not sidecar_path.is_file():
            return False
        sidecar_raw_sha = sha256_of_file(sidecar_path)
        if sidecar.get("raw_sha256") != sidecar_raw_sha:
            return False
        sidecar_rows, sidecar_quality = load_csv_reference_rows(sidecar_path, reference_symbols=("IWM",))
        iwm_sidecar_rows = sidecar_rows.get("IWM", [])
        if sidecar_quality.get("IWM", {}).get("rejected_row_count", 0) != 0:
            return False
        sidecar_semantic = semantic_rows_digest(iwm_sidecar_rows)
        if sidecar.get("normalized_semantic_row_sha256") != sidecar_semantic:
            return False
        if iwm_source.get("normalized_semantic_row_sha256") != sidecar_semantic:
            return False
        if iwm_source.get("normalized_row_count") != len(iwm_sidecar_rows):
            return False
        if identity.get("iwm_raw_bundle_sha256") != iwm_source.get("raw_bundle_sha256"):
            return False
        normalizer = critical[M3_DATA_GATE_R3_NORMALIZER_PATH]
        if not isinstance(normalizer, Mapping):
            return False
        recomputed_identity = composite_dataset_identity(
            canonical_parent_raw_sha256=str(identity["canonical_parent_raw_sha256"]),
            canonical_parent_normalized_sha256=str(identity["canonical_parent_normalized_sha256"]),
            canonical_manifest_git_blob=str(identity["canonical_manifest_git_blob"]),
            iwm_raw_bundle_sha256=str(identity["iwm_raw_bundle_sha256"]),
            iwm_normalized_raw_sha256=str(identity["iwm_normalized_raw_sha256"]),
            iwm_normalized_semantic_row_sha256=str(identity["iwm_normalized_semantic_row_sha256"]),
            normalizer_git_blob=str(identity["normalizer_git_blob"]),
            normalizer_sha256=str(identity["normalizer_sha256"]),
            schema_version=str(identity["schema_version"]),
            canonical_timeframe=str(identity["canonical_timeframe"]),
            source_feed=str(identity["source_feed"]),
            adjustment_mode=str(identity["adjustment_mode"]),
            exchange_timezone=str(identity["exchange_timezone"]),
            calendar_identity=identity["calendar_identity"],  # type: ignore[arg-type]
            source_range=identity["source_range"],  # type: ignore[arg-type]
            evaluation_range=identity["evaluation_range"],  # type: ignore[arg-type]
        )
        if dict(recomputed_identity) != dict(identity):
            return False
        if (
            identity.get("canonical_parent_raw_sha256") != canonical_raw_sha
            or identity.get("canonical_parent_normalized_sha256") != canonical_provenance.get("crlf_normalized_sha256")
            or identity.get("canonical_manifest_git_blob") != actual_manifest_blob
            or identity.get("normalizer_git_blob") != normalizer.get("git_blob_id")
            or identity.get("normalizer_sha256") != normalizer.get("raw_sha256")
            or identity.get("schema_version") != NORMALIZER_VERSION
            or identity.get("canonical_timeframe") != CANONICAL_TIMEFRAME
            or identity.get("source_feed") != SOURCE_FEED
            or identity.get("adjustment_mode") != SOURCE_ADJUSTMENT
            or identity.get("exchange_timezone") != EXCHANGE_TIMEZONE
            or identity.get("calendar_identity") != calendar_identity
            or identity.get("source_range") != {"start": "2016-01-01", "end": "2026-07-31"}
            or identity.get("evaluation_range") != {"end_inclusive": "2026-07-30"}
        ):
            return False
        composite_payload = json.loads(composite_path.read_text(encoding="utf-8"))
        if (
            not isinstance(composite_payload, Mapping)
            or composite_payload.get("mission") != "equity_m3_composite_dataset_2026_09"
            or composite_payload.get("identity") != dict(identity)
            or composite_payload.get("iwm_sidecar") != dict(sidecar)
        ):
            return False

        pit_info = payload.get("deterministic_numerical_population")
        if not isinstance(pit_info, Mapping):
            return False
        pit_artifact = pit_info.get("input_artifact")
        pit_manifest_info = pit_info.get("compact_manifest")
        if not isinstance(pit_artifact, Mapping) or not isinstance(pit_manifest_info, Mapping):
            return False
        pit_path = _r2_relative_path(pit_artifact.get("path"))
        pit_manifest_path = _r2_relative_path(pit_manifest_info.get("path"))
        if (
            pit_path is None
            or pit_manifest_path is None
            or pit_path.is_symlink()
            or pit_manifest_path.is_symlink()
            or not pit_path.is_file()
            or not pit_manifest_path.is_file()
        ):
            return False
        pit_raw_sha = sha256_of_file(pit_path)
        pit_manifest_raw_sha = sha256_of_file(pit_manifest_path)
        if pit_artifact.get("raw_sha256") != pit_raw_sha or pit_manifest_info.get("raw_sha256") != pit_manifest_raw_sha:
            return False
        pit_payload = json.loads(pit_path.read_text(encoding="utf-8"))
        if not isinstance(pit_payload, Mapping):
            return False
        if (
            pit_payload.get("schema_version") != "m3-pit-scanner-population-v1"
            or pit_payload.get("mission") != "equity_m3_pit_scanner_population_2026_09_r3"
            or pit_payload.get("market") != "EQUITY"
            or pit_payload.get("timeframe") != CANONICAL_TIMEFRAME
            or pit_payload.get("observation_strategy") is not None
            or pit_payload.get("alpha_ranking_used") is not False
            or pit_payload.get("reference_symbols") != sorted(REFERENCE_SYMBOLS)
            or pit_payload.get("scanner_rebalance") != "daily"
        ):
            return False
        no_alpha = pit_payload.get("no_alpha_assertion")
        if not isinstance(no_alpha, Mapping) or any(
            no_alpha.get(key) is not expected
            for key, expected in (
                ("observation_strategy_is_none", True),
                ("candidate_rankings_present", False),
                ("economic_ranking_present", False),
            )
        ):
            return False
        snapshots = pit_payload.get("snapshots")
        if not isinstance(snapshots, list) or not snapshots:
            return False
        for snapshot in snapshots:
            if not isinstance(snapshot, Mapping) or snapshot.get("strictly_prior_data") is not True:
                return False
            try:
                effective = datetime.fromisoformat(str(snapshot["effective_at"]).replace("Z", "+00:00"))
                latest = datetime.fromisoformat(str(snapshot["latest_input_timestamp"]).replace("Z", "+00:00"))
            except (KeyError, TypeError, ValueError):
                return False
            if effective.tzinfo is None or latest.tzinfo is None or latest >= effective:
                return False
            if snapshot.get("scanner_version") != "equity-scanner-v3" or not isinstance(snapshot.get("symbols"), list):
                return False
        pit_population = deterministic_candidate_population(snapshots)
        if validate_pit_reference_boundary(pit_population) != pit_payload["population"]["reference_boundary"]:
            return False
        for key in (
            "candidate_universe_digest_sha256",
            "candidate_decision_eligibility_digest_sha256",
            "snapshot_count",
            "unique_candidate_count",
        ):
            if pit_info.get(key) != pit_population.get(key) or pit_payload["population"].get(key) != pit_population.get(
                key
            ):
                return False
        if int(pit_population["unique_candidate_count"]) <= 0:
            return False
        pit_manifest = json.loads(pit_manifest_path.read_text(encoding="utf-8"))
        if not isinstance(pit_manifest, Mapping):
            return False
        if (
            pit_manifest.get("mission") != "equity_m3_pit_scanner_population_2026_09_r3"
            or pit_manifest.get("raw_pit_sha256") != pit_raw_sha
            or pit_manifest.get("raw_pit_path") != pit_artifact.get("path")
            or pit_manifest.get("semantic_population_digest_sha256")
            != pit_population.get("candidate_universe_digest_sha256")
            or pit_manifest.get("candidate_decision_eligibility_digest_sha256")
            != pit_population.get("candidate_decision_eligibility_digest_sha256")
            or pit_manifest.get("snapshot_count") != pit_population.get("snapshot_count")
            or pit_manifest.get("unique_candidate_count") != pit_population.get("unique_candidate_count")
            or pit_manifest.get("no_alpha_ranking") is not True
            or pit_manifest.get("candidate_rankings_present") is not False
        ):
            return False

        canonical_rows, canonical_quality = load_csv_reference_rows(DATASET, reference_symbols=REFERENCE_SYMBOLS)
        if any(item.get("rejected_row_count", 0) != 0 for item in canonical_quality.values()):
            return False
        merged_iwm, _merge_hashes = merge_normalized_rows(canonical_rows["IWM"], iwm_sidecar_rows)
        panel_rows = {"SPY": canonical_rows["SPY"], "IWM": merged_iwm, "QQQ": canonical_rows["QQQ"]}
        panel = payload.get("reference_panel")
        if not isinstance(panel, Mapping):
            return False
        if panel.get("normalized_reference_panel_sha256") != reference_panel_digest(
            [row for symbol in REFERENCE_SYMBOLS for row in panel_rows[symbol]], source_identity=source_identity
        ):
            return False
        if panel.get("evaluation_reference_panel_sha256") != reference_panel_digest(
            [row for symbol in REFERENCE_SYMBOLS for row in panel_rows[symbol] if row.session_date <= EVAL_END.date()],
            source_identity=source_identity,
        ):
            return False

        matrix = payload.get("design_matrix_summary")
        if not isinstance(matrix, Mapping):
            return False
        total = matrix.get("total_matrix_count")
        if (
            not isinstance(total, int)
            or total <= 0
            or matrix.get("pass_count") != total
            or matrix.get("fail_count") != 0
        ):
            return False
        role = payload.get("reference_role_separation")
        if not isinstance(role, Mapping) or role.get("reference_observation_symbols") != sorted(REFERENCE_SYMBOLS):
            return False
        return payload.get("forbidden_economic_fields") == []
    except (
        OSError,
        UnicodeDecodeError,
        json.JSONDecodeError,
        GateError,
        KeyError,
        TypeError,
        ValueError,
        subprocess.CalledProcessError,
    ):
        return False


def _valid_m3_data_gate_receipt() -> bool:
    """Accept only the immutable R3 data-gate PASS; R2 is not a valid input."""
    try:
        payload = json.loads(M3_DATA_GATE_RECEIPT.read_text(encoding="utf-8"))
        if not isinstance(payload, Mapping) or payload.get("mission") != "equity_m3_data_gate_2026_09_r3":
            return False
        return _valid_m3_data_gate_r3_receipt(payload)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scope", choices=("audit", "recent", "full"), default="audit")
    args = parser.parse_args()
    manifest = json.loads(MANIFEST_PATH.read_text())
    if manifest["frozen_parameters"] != FROZEN_PARAMETERS:
        raise SystemExit("M3/M4 frozen parameter manifest drifted")
    if sha256_of_file(DATASET) != manifest["dataset_sha256"]:
        raise SystemExit("M3/M4 canonical dataset hash drifted")
    config = load_config(str(REPO / "config/settings.yaml"))
    start = None if args.scope == "full" else RECENT_START
    bars, coverage = _load_window(start, EVAL_END, collect_bars=args.scope != "audit")
    feed = _feed_evidence(config, coverage)
    m3_ready = _valid_m3_data_gate_receipt()
    output: dict[str, object] = {
        "mission": "equity_m3_m4_2026_08",
        "research_only": True,
        "automatic_promotion": False,
        "git_head": git_head(),
        "scope": args.scope,
        "window": {"start": start.isoformat() if start else None, "end": EVAL_END.isoformat()},
        "dataset": {
            "path": str(DATASET.relative_to(REPO)),
            "sha256": manifest["dataset_sha256"],
            "feed": feed,
        },
        "m3": {
            "strategy_name": ProxyMultifactorResidualMomentum.name,
            "status": "data_gate_pass_m3_ocp_required" if m3_ready else "blocked_data_gate",
            "required_references": list(REFERENCE_SYMBOLS),
            "missing_references": [symbol for symbol in REFERENCE_SYMBOLS if int(coverage[symbol]["count"]) < 274],
            "configured_reference_symbols": list(config.universe.equity.reference_symbols),
            "unreserved_reference_symbols": [
                symbol for symbol in REFERENCE_SYMBOLS if symbol not in config.universe.equity.reference_symbols
            ],
            "headline": None,
        },
        "m4": {
            "strategy_name": VolumePersistenceResidualMomentum.name,
            "status": "audit_only" if args.scope == "audit" else "running",
            "headline": None,
            "promotion_blocked_by_feed_mismatch": bool(feed["promotion_blocked"]),
        },
    }
    if args.scope != "audit":
        terminal_map, _lifetimes = load_lifetimes()
        actions = load_corporate_actions_all()
        prepared = _prepare(config, bars, start, EVAL_END)
        control, universe = _run(
            config,
            EquityResidualMomentum1dV3(dict(FROZEN_PARAMETERS)),
            prepared,
            terminal_map,
            actions,
            start,
            EVAL_END,
        )
        volume_strategy = VolumePersistenceResidualMomentum(dict(FROZEN_PARAMETERS))
        volume, volume_universe = _run(
            config,
            volume_strategy,
            prepared,
            terminal_map,
            actions,
            start,
            EVAL_END,
        )
        control_stats = _stats(control)
        volume_stats = _stats(volume)
        output["control"] = {
            "strategy_name": EquityResidualMomentum1dV3.name,
            "headline": control_stats,
            "universe_hash": hash_point_in_time_universe(universe),
        }
        output["m4"] = {
            **output["m4"],  # type: ignore[dict-item]
            "status": "headline_complete_promotion_blocked",
            "headline": volume_stats,
            "same_run_delta": {
                key: round(float(volume_stats[key]) - float(control_stats[key]), 6)
                for key in ("return", "cagr", "sharpe", "max_drawdown", "turnover")
            },
            "universe_hash": hash_point_in_time_universe(volume_universe),
            "volume_diagnostics": {
                "evaluated": volume_strategy.volume_evaluated,
                "valid": volume_strategy.volume_valid,
                "missing_or_zero": volume_strategy.volume_missing_or_zero,
                "median_ratio": round(statistics.median(volume_strategy.volume_ratios), 6)
                if volume_strategy.volume_ratios
                else None,
            },
        }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    destination = OUT_DIR / f"{args.scope}.json"
    destination.write_text(json.dumps(output, indent=2, sort_keys=True) + "\n")
    print(json.dumps(output, indent=2, sort_keys=True))
    print(f"wrote {destination}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
