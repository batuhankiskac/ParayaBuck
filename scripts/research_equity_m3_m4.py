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
import json
import math
import statistics
import sys
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np

_REPO_BOOTSTRAP = Path(__file__).resolve().parents[1]
for _path in (_REPO_BOOTSTRAP / "src", Path(__file__).parent):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from m3_data_gate_helpers import canonical_digest  # noqa: E402
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
M3_DATA_GATE_RECEIPT = REPO / "data/research_receipts/equity_m3_data_gate_2026_09.json"

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


def _valid_m3_data_gate_receipt() -> bool:
    """Return true only for a self-consistent research-only PASS receipt."""
    try:
        payload = json.loads(M3_DATA_GATE_RECEIPT.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return False
    if not isinstance(payload, dict):
        return False
    required = {
        "verdict": "M3_DATA_GATE_PASS",
        "research_only": True,
        "automatic_promotion": False,
        "human_promotion_only": True,
        "candidate_economics_run": False,
        "economic_metrics_present": False,
    }
    if any(payload.get(key) != expected for key, expected in required.items()):
        return False
    recorded_digest = payload.get("self_digest_sha256")
    if not isinstance(recorded_digest, str):
        return False
    without_digest = dict(payload)
    without_digest.pop("self_digest_sha256", None)
    return recorded_digest == canonical_digest(without_digest)


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
            "status": "ready" if m3_ready else "blocked_unverified_data_gate",
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
