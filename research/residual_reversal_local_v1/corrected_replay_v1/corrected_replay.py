"""Run the one-time implementation-corrected frozen replay.

The runner refuses to start unless the pre-outcome mechanics specification is
present and hash-pinned. It writes only inside ``corrected_replay_v1`` and
never calls the original campaign runner or overwrites its artifacts.
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
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
RESEARCH_DIR = Path(__file__).resolve().parents[1]
OUTPUT_DIR = Path(__file__).resolve().parent
SPEC_PATH = OUTPUT_DIR / "CORRECTED_REPLAY_SPEC.md"
ORIGINAL_DIR = RESEARCH_DIR
EXPECTED_MAIN_SHA = "8123694a71d8fbb0e520f13b4e1692e932754e97"
EXPECTED_PRICE_SHA = "bb7aaae86bc1909c6b0a1094a59430f5ed9900bb3ec63891bbdcda8cda055139"
EXPECTED_ACTION_SHA = "a0a60755a3f6adfc06a5ef0e6ed82852e1584c20fdb62cefed92b765dc9adc23"
EXPECTED_BRANCH = "research/residual-reversal-local-v1"
PARENT_FORENSIC_HEAD = "633c11448387f291d20a8a1aa0ed1286e2e94b34"
FULL_START = datetime(2017, 1, 3, tzinfo=UTC)
DEVELOPMENT_START = datetime(2017, 1, 3, tzinfo=UTC)
DEVELOPMENT_END = datetime(2021, 12, 31, 23, 59, 59, tzinfo=UTC)
VALIDATION_START = datetime(2022, 1, 3, tzinfo=UTC)
REQUESTED_END = datetime(2026, 7, 31, 23, 59, 59, tzinfo=UTC)
MAX_COST_MULTIPLIER = 3
MIN_DECISION_COVERAGE = 0.90
MAX_UNEXPLAINED_GAP_WEEKS = 8
MAX_FINAL_DECISION_LAG_WEEKS = 8
REQUIRED_ORIGINAL_FILES = (
    "FINAL_REPORT.md",
    "decision.json",
    "gate_table.json",
    "standalone_reversal.json",
    "v3_control.json",
    "treatment.json",
    "cost_stress.json",
    "execution_audit.json",
    "independent_verification.json",
    "FROZEN_PROTOCOL.md",
    "FORENSIC_AUDIT.md",
    "FORENSIC_AUDIT.json",
)

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from parayabuck.backtest.numeric_history import build_numeric_history
from parayabuck.backtest.universe import (
    PointInTimeUniverse,
    build_trailing_point_in_time_universe,
    hash_point_in_time_universe,
)
from parayabuck.config import load_config
from parayabuck.core.models import Bar, Market
from parayabuck.execution.portfolio import PortfolioExecutionPolicy, portfolio_risk_limits_from_config
from parayabuck.portfolio import rebalance_policy_for
from parayabuck.portfolio.base import PortfolioStrategy
from parayabuck.portfolio.equity_residual_momentum_v3 import EquityResidualMomentum1dV3

try:
    from .corrected_mechanics import (
        FROZEN_MAX_POSITIONS,
        FROZEN_MAX_SINGLE_WEIGHT,
        FROZEN_REVERSAL_ALLOCATION,
        FROZEN_V3_ALLOCATION,
        TREATMENT_OBSERVATION_NAME,
        CorrectedReplayEngine,
        CorrectedTreatmentStrategy,
        TreatmentObservationRanker,
    )
except ImportError:  # pragma: no cover - direct script execution
    from corrected_mechanics import (
        FROZEN_MAX_POSITIONS,
        FROZEN_MAX_SINGLE_WEIGHT,
        FROZEN_REVERSAL_ALLOCATION,
        FROZEN_V3_ALLOCATION,
        TREATMENT_OBSERVATION_NAME,
        CorrectedReplayEngine,
        CorrectedTreatmentStrategy,
        TreatmentObservationRanker,
    )

try:
    from ..candidate import EquityShortTermResidualReversalV1, frozen_reversal_parameters
except ImportError:  # pragma: no cover - direct script execution
    from research.residual_reversal_local_v1.candidate import (
        EquityShortTermResidualReversalV1,
        frozen_reversal_parameters,
    )

from research.residual_reversal_local_v1 import run_campaign as original


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _timestamp(raw: object) -> datetime:
    value = str(raw).replace("Z", "+00:00")
    result = datetime.fromisoformat(value)
    if result.tzinfo is None:
        result = result.replace(tzinfo=UTC)
    return result.astimezone(UTC)


def _safe(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _safe(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_safe(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if hasattr(value, "value") and not isinstance(value, (str, bytes)):
        try:
            return value.value
        except Exception:
            pass
    return value


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(_safe(payload), indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")


def _git(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()


def _load_terminal_map(path: Path) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    terminal_map: dict[str, dict[str, Any]] = {}
    rows = 0
    inactive = 0
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            rows += 1
            if str(row.get("current_status", "")).strip().lower() != "inactive":
                continue
            symbol = str(row.get("canonical_symbol", "")).strip().upper()
            last_raw = str(row.get("last_observed_bar", "")).strip()
            if not symbol or not last_raw:
                raise ValueError(f"Inactive lifetime row lacks terminal data: {row}")
            inactive += 1
            terminal_map[symbol] = {
                "last_bar": _timestamp(last_raw),
                "valid_from": row.get("valid_from"),
                "valid_to": row.get("valid_to"),
                "last_observed_bar": last_raw,
                "current_status": row.get("current_status"),
                "terminal_event_class": row.get("terminal_event_class") or "UNKNOWN",
                "entity_id": row.get("entity_id"),
                "provider_asset_id": row.get("provider_asset_id"),
            }
    return terminal_map, {
        "source_path": str(path),
        "source_sha256": _hash_file(path),
        "row_count": rows,
        "inactive_terminal_entries": inactive,
        "active_symbols_without_terminal_entry": rows - inactive,
    }


def _strategy_universe(config: Any, bars: list[Bar], strategy: PortfolioStrategy) -> PointInTimeUniverse:
    """Build the exact scanner policy with a caller-owned alpha ranker."""
    equity = config.universe.equity
    available = [
        bar
        for bar in bars
        if bar.market == Market.EQUITY and bar.timeframe == strategy.timeframe and bar.timestamp <= REQUESTED_END
    ]
    pinned = (*equity.reference_symbols, *equity.inverse_trade_symbols)
    return build_trailing_point_in_time_universe(
        available,
        market=Market.EQUITY,
        maximum_symbols=equity.maximum_stream_symbols,
        rebalance_frequency=config.backtest.scanner_rebalance,
        lookback_days=60,
        minimum_history_ratio=equity.scanner.minimum_data_completeness,
        minimum_price=equity.scanner.minimum_price,
        minimum_dollar_volume=equity.scanner.minimum_median_dollar_volume,
        pinned_symbols=pinned,
        target_shortlist=equity.target_shortlist,
        equity_config=equity,
        observation_strategy=strategy,
    )


def _compact_universe(universe: PointInTimeUniverse) -> dict[str, Any]:
    snapshots = []
    for snapshot in universe.snapshots:
        snapshots.append(
            {
                "effective_at": snapshot.effective_at.isoformat(),
                "symbols": sorted(snapshot.symbols),
                "observation_set": list(snapshot.observation_set),
                "maximum_stream_symbols": snapshot.maximum_stream_symbols,
                "pinned_symbols": list(snapshot.pinned_symbols),
                "observation_strategy_name": snapshot.observation_strategy_name,
                "observation_strategy_version": snapshot.observation_strategy_version,
                "scanner_version": snapshot.scanner_version,
                "config_digest": snapshot.config_digest,
                "candidate_count": snapshot.candidate_count,
                "sector_metadata_coverage": snapshot.sector_metadata_coverage,
            }
        )
    pool_sizes = [len(snapshot["symbols"]) for snapshot in snapshots]
    observation_sizes = [len(snapshot["observation_set"]) for snapshot in snapshots]
    return {
        "market": universe.market.value,
        "methodology": universe.methodology,
        "hash": hash_point_in_time_universe(universe),
        "snapshot_count": len(snapshots),
        "average_pool_size": statistics.mean(pool_sizes) if pool_sizes else 0.0,
        "minimum_pool_size": min(pool_sizes, default=0),
        "maximum_pool_size": max(pool_sizes, default=0),
        "average_observation_size": statistics.mean(observation_sizes) if observation_sizes else 0.0,
        "minimum_observation_size": min(observation_sizes, default=0),
        "maximum_observation_size": max(observation_sizes, default=0),
        "snapshots": snapshots,
    }


def _validate_universes(
    v3_universe: PointInTimeUniverse,
    reversal_universe: PointInTimeUniverse,
    treatment_universe: PointInTimeUniverse,
) -> dict[str, Any]:
    universes = {"v3": v3_universe, "reversal": reversal_universe, "treatment": treatment_universe}
    compact = {name: _compact_universe(universe) for name, universe in universes.items()}
    if not v3_universe.snapshots or not reversal_universe.snapshots or not treatment_universe.snapshots:
        raise ValueError("Corrected replay requires non-empty V3, reversal, and treatment PIT universes")
    baseline = [(item.effective_at, item.symbols) for item in v3_universe.snapshots]
    for name, universe in (("reversal", reversal_universe), ("treatment", treatment_universe)):
        candidate = [(item.effective_at, item.symbols) for item in universe.snapshots]
        if candidate != baseline:
            raise ValueError(f"Scanner eligible pool changed across corrected {name} observation universe")

    treatment_reversal_coverage: list[dict[str, Any]] = []
    for v3_snapshot, reversal_snapshot, treatment_snapshot in zip(
        v3_universe.snapshots,
        reversal_universe.snapshots,
        treatment_universe.snapshots,
        strict=True,
    ):
        reversal_pins = set(reversal_snapshot.pinned_symbols)
        reversal_top6 = [symbol for symbol in reversal_snapshot.observation_set if symbol not in reversal_pins][:6]
        treatment_set = set(treatment_snapshot.observation_set)
        missing = [symbol for symbol in reversal_top6 if symbol not in treatment_set]
        treatment_reversal_coverage.append(
            {
                "effective_at": treatment_snapshot.effective_at.isoformat(),
                "reversal_top6": reversal_top6,
                "missing_from_treatment": missing,
                "treatment_observation_size": len(treatment_snapshot.observation_set),
                "v3_observation_strategy_name": v3_snapshot.observation_strategy_name,
                "reversal_observation_strategy_name": reversal_snapshot.observation_strategy_name,
                "treatment_observation_strategy_name": treatment_snapshot.observation_strategy_name,
            }
        )
        if missing:
            raise ValueError(
                f"Treatment stream lost reversal top-six access at {treatment_snapshot.effective_at.isoformat()}: {missing}"
            )
    strategy_names = {
        name: sorted({item["observation_strategy_name"] for item in data["snapshots"]})
        for name, data in compact.items()
    }
    expected_names = {
        "v3": ["equity_residual_momentum_v3"],
        "reversal": ["equity_short_term_residual_reversal_v1"],
        "treatment": [TREATMENT_OBSERVATION_NAME],
    }
    if strategy_names != expected_names:
        raise ValueError(f"Unexpected corrected observation strategy identities: {strategy_names}")
    return {
        "policy": "V3 and reversal use separate alpha-ranked streams; treatment uses balanced round-robin union",
        "treatment_policy_name": TREATMENT_OBSERVATION_NAME,
        "treatment_policy_version": "1.0.0-corrected-replay",
        "strategy_names": strategy_names,
        "strategy_versions": {
            name: sorted({item["observation_strategy_version"] for item in data["snapshots"]})
            for name, data in compact.items()
        },
        "universe_hashes": {name: data["hash"] for name, data in compact.items()},
        "scanner_pool_same_across_strategies": True,
        "treatment_reversal_top6_coverage": {
            "snapshot_count": len(treatment_reversal_coverage),
            "snapshots_with_missing_reversal_top6": sum(
                bool(item["missing_from_treatment"]) for item in treatment_reversal_coverage
            ),
            "minimum_treatment_observation_size": min(
                (item["treatment_observation_size"] for item in treatment_reversal_coverage), default=0
            ),
        },
        "universes": compact,
    }


def _make_strategy(name: str, config: Any) -> PortfolioStrategy:
    v3 = EquityResidualMomentum1dV3(dict(config.strategy.equity.parameters))
    reversal = EquityShortTermResidualReversalV1(frozen_reversal_parameters())
    if name == "v3":
        return v3
    if name == "reversal":
        return reversal
    if name == "treatment":
        return CorrectedTreatmentStrategy(v3, reversal)
    raise ValueError(f"Unknown corrected replay strategy: {name}")


def _make_engine(
    strategy: PortfolioStrategy,
    config: Any,
    universe: PointInTimeUniverse,
    cost_multiplier: int,
    terminal_map: dict[str, dict[str, Any]],
) -> CorrectedReplayEngine:
    risk = portfolio_risk_limits_from_config(config.risk)
    return CorrectedReplayEngine(
        strategy,
        rebalance_policy_for(strategy),
        starting_equity=config.backtest.initial_cash,
        slippage_bps=config.backtest.slippage_bps * cost_multiplier,
        spread_bps=config.backtest.spread_bps * cost_multiplier,
        commission=config.backtest.commission,
        crypto_taker_fee_bps=config.backtest.crypto_taker_fee_bps,
        equity_taf_per_share=config.backtest.equity_taf_per_share,
        equity_taf_cap=config.backtest.equity_taf_cap,
        equity_cat_per_share=config.backtest.equity_cat_per_share,
        equity_sec_fee_rate=config.backtest.equity_sec_fee_rate,
        equity_use_effective_dated_fees=config.backtest.equity_use_effective_dated_fees,
        benchmark_symbol="SPY",
        universe=universe,
        universe_version=hash_point_in_time_universe(universe),
        risk_limits=risk,
        maximum_allocation=config.strategy.equity.maximum_allocation,
        execution_policy=PortfolioExecutionPolicy(
            rebalance_band_pct=config.execution.rebalance_band_pct,
            minimum_order_notional=config.execution.minimum_order_notional,
        ),
        terminal_map=terminal_map,
    )


def _stream_symbols(universes: tuple[PointInTimeUniverse, ...]) -> set[str]:
    symbols: set[str] = {"SPY"}
    for universe in universes:
        for snapshot in universe.snapshots:
            symbols.update(snapshot.observation_set or tuple(sorted(snapshot.symbols))[: snapshot.maximum_stream_symbols or 30])
            symbols.update(snapshot.pinned_symbols)
    return symbols


def _run_one(
    name: str,
    cost_multiplier: int,
    config: Any,
    engine_bars: list[Bar],
    numeric_history: Any,
    universe: PointInTimeUniverse,
    corporate_actions: list[Any],
    terminal_map: dict[str, dict[str, Any]],
) -> Any:
    strategy = _make_strategy(name, config)
    filtered, warmup, prepared = original._prepared_window(engine_bars, FULL_START, REQUESTED_END)
    engine = _make_engine(strategy, config, universe, cost_multiplier, terminal_map)
    result = engine.run(
        filtered,
        warmup=warmup,
        corporate_actions=corporate_actions,
        prepared_inputs=prepared,
        numeric_history=numeric_history,
    )
    summary = original._summary(result, strategy, cost_multiplier)
    audit = original._audit_payload(result, name, cost_multiplier)
    audit["decision_coverage_attempts"] = list(engine.coverage_attempts)
    audit["treatment_decision_attribution"] = list(engine.decision_attributions)
    audit["terminal_map_loaded"] = True
    audit["terminal_bar_event_count"] = sum(
        1 for event in result.terminal_events if event.get("policy") == "TERMINAL_BAR"
    )
    return original.RunRecord(name, cost_multiplier, strategy, result, summary, audit)


def _period_coverage(
    record: Any,
    start: datetime,
    end: datetime,
) -> dict[str, Any]:
    attempts = [
        row
        for row in record.audit["decision_coverage_attempts"]
        if start <= _timestamp(row["timestamp"]) <= end and row["eligible"]
    ]
    attempts.sort(key=lambda row: row["timestamp"])
    decisions = [
        rebalance.decision_timestamp
        for rebalance in record.result.rebalances
        if start <= rebalance.decision_timestamp <= end
    ]
    fills = [fill for fill in record.result.fills if start <= fill.timestamp <= end]
    skipped_by_reason = Counter(row["skip_reason"] for row in attempts if not row["decision_recorded"])
    longest_gap = 0
    current_gap = 0
    for row in attempts:
        if row["decision_recorded"]:
            current_gap = 0
        else:
            current_gap += 1
            longest_gap = max(longest_gap, current_gap)
    decision_timestamps = sorted(decisions)
    recorded_gap_days = 0
    for previous, current in zip(decision_timestamps, decision_timestamps[1:], strict=False):
        recorded_gap_days = max(recorded_gap_days, (current - previous).days)
    last_decision = decision_timestamps[-1].isoformat() if decision_timestamps else None
    recency = sum(_timestamp(row["timestamp"]) > decision_timestamps[-1] for row in attempts) if decision_timestamps else len(attempts)
    return {
        "scheduled_opportunities": len(attempts),
        "decisions": sum(bool(row["decision_recorded"]) for row in attempts),
        "coverage_pct": (100.0 * sum(bool(row["decision_recorded"]) for row in attempts) / len(attempts)) if attempts else 0.0,
        "fills": len(fills),
        "first_decision": decision_timestamps[0].isoformat() if decision_timestamps else None,
        "last_decision": last_decision,
        "last_fill": fills[-1].timestamp.isoformat() if fills else None,
        "longest_unexplained_gap_weeks": longest_gap,
        "longest_recorded_decision_gap_days": recorded_gap_days,
        "decision_recency_lag_weeks": recency,
        "skipped_by_reason": dict(sorted(skipped_by_reason.items())),
        "attempts": attempts,
        "eligible_schedule_timestamps": [row["timestamp"] for row in attempts],
    }


def _coverage_payload(records: dict[tuple[str, int], Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "thresholds": {
            "minimum_decision_coverage_pct": MIN_DECISION_COVERAGE * 100.0,
            "maximum_unexplained_gap_weeks": MAX_UNEXPLAINED_GAP_WEEKS,
            "maximum_final_decision_lag_weeks": MAX_FINAL_DECISION_LAG_WEEKS,
            "validation_requires_decision": True,
        },
        "runs": {},
    }
    for (name, cost), record in sorted(records.items()):
        key = f"{name}_{cost}x"
        periods = {
            "development": _period_coverage(record, DEVELOPMENT_START, DEVELOPMENT_END),
            "validation": _period_coverage(record, VALIDATION_START, REQUESTED_END),
        }
        attempts = record.audit["decision_coverage_attempts"]
        annual: list[dict[str, Any]] = []
        for year in range(2017, 2027):
            start = datetime(year, 1, 1, tzinfo=UTC)
            end = datetime(year, 12, 31, 23, 59, 59, tzinfo=UTC)
            period = _period_coverage(record, start, end)
            if period["scheduled_opportunities"] or year <= 2026:
                annual.append({"year": year, **{key: value for key, value in period.items() if key not in {"attempts", "eligible_schedule_timestamps"}}})
        payload["runs"][key] = {
            "strategy": name,
            "cost_multiplier": cost,
            "attempts": attempts,
            "development": periods["development"],
            "validation": periods["validation"],
            "annual": annual,
        }
    return payload


def _summarize_sleeve_attribution(records: dict[tuple[str, int], Any]) -> dict[str, Any]:
    by_cost: dict[str, Any] = {}
    for cost in (1, 2, 3):
        attrs = records[("treatment", cost)].audit["treatment_decision_attribution"]
        v3_realized = [float(row["post_overlay_v3_exposure"]) for row in attrs]
        reversal_realized = [float(row["post_overlay_reversal_exposure"]) for row in attrs]
        unused_v3 = [float(row["post_overlay_unused_v3_allocation"]) for row in attrs]
        unused_reversal = [float(row["post_overlay_unused_reversal_allocation"]) for row in attrs]
        shortfalls = [row for row in attrs if row["allocation_capacity_shortfall"]]
        zero_reversal = Counter(
            str(row["zero_reversal_exposure_reason"])
            for row in attrs
            if row["zero_reversal_exposure_reason"] is not None
        )
        overlap = sum(bool(row["overlap_symbols"]) for row in attrs)
        by_cost[f"{cost}x"] = {
            "decision_count": len(attrs),
            "frozen_intended_v3_allocation": FROZEN_V3_ALLOCATION,
            "frozen_intended_reversal_allocation": FROZEN_REVERSAL_ALLOCATION,
            "mean_realized_v3_target_exposure": statistics.fmean(v3_realized) if v3_realized else 0.0,
            "mean_realized_reversal_target_exposure": statistics.fmean(reversal_realized) if reversal_realized else 0.0,
            "mean_unused_v3_allocation": statistics.fmean(unused_v3) if unused_v3 else FROZEN_V3_ALLOCATION,
            "mean_unused_reversal_allocation": statistics.fmean(unused_reversal) if unused_reversal else FROZEN_REVERSAL_ALLOCATION,
            "allocation_capacity_shortfall_decisions": len(shortfalls),
            "allocation_capacity_shortfall_pct": 100.0 * len(shortfalls) / len(attrs) if attrs else 0.0,
            "zero_reversal_exposure_decisions": sum(zero_reversal.values()),
            "zero_reversal_exposure_reasons": dict(sorted(zero_reversal.items())),
            "overlap_decisions": overlap,
            "per_decision": attrs,
        }
    return {
        "policy": "sleeve_aware_quota_round_robin_v1",
        "frozen_intended_allocation": {"v3": FROZEN_V3_ALLOCATION, "reversal": FROZEN_REVERSAL_ALLOCATION, "total": 0.55},
        "by_cost": by_cost,
    }


def _lifecycle_payload(records: dict[tuple[str, int], Any], terminal_meta: dict[str, Any]) -> dict[str, Any]:
    runs: dict[str, Any] = {}
    for (name, cost), record in sorted(records.items()):
        events = record.result.terminal_events
        runs[f"{name}_{cost}x"] = {
            "terminal_map_loaded": record.audit["terminal_map_loaded"],
            "terminal_bar_events": [event for event in events if event.get("policy") == "TERMINAL_BAR"],
            "final_bar_events": [event for event in events if event.get("policy") == "FINAL_BAR"],
            "inactive_holdings_remaining": sorted(
                symbol for symbol in record.result.state.holdings if symbol in terminal_meta
            ),
        }
    return {
        "terminal_metadata": terminal_meta,
        "semantics": "inactive managed symbols are active through last_bar, closed on real terminal bar, then removed from completeness",
        "runs": runs,
    }


def _period_bundle(record: Any) -> dict[str, Any]:
    return {
        "full": record.summary,
        "development": original._period_metrics(record.result, DEVELOPMENT_START, DEVELOPMENT_END),
        "validation": original._period_metrics(record.result, VALIDATION_START, REQUESTED_END),
    }


def _coverage_table_rows(coverage: dict[str, Any]) -> list[list[Any]]:
    rows: list[list[Any]] = []
    for name, label in (("v3", "V3"), ("reversal", "Reversal"), ("treatment", "Treatment")):
        item = coverage["runs"][f"{name}_1x"]
        for period in ("development", "validation"):
            data = item[period]
            rows.append(
                [
                    label,
                    period,
                    data["scheduled_opportunities"],
                    data["decisions"],
                    f"{data['coverage_pct']:.2f}%",
                    data["fills"],
                    data["last_decision"] or "n/a",
                    data["longest_unexplained_gap_weeks"],
                ]
            )
    return rows


def _build_final_report(
    *,
    decision: str,
    replay_validity: str,
    start_head: str,
    final_sha: str,
    manifest: dict[str, Any],
    coverage: dict[str, Any],
    observation: dict[str, Any],
    sleeve: dict[str, Any],
    lifecycle: dict[str, Any],
    v3: dict[str, Any],
    reversal: dict[str, Any],
    treatment: dict[str, Any],
    cost_stress: dict[str, Any],
    diversification: dict[str, Any],
    robustness: dict[str, Any],
    gates: list[dict[str, Any]],
    verification: dict[str, Any],
) -> str:
    def fmt(value: Any, digits: int = 4) -> str:
        if value is None:
            return "n/a"
        return f"{value:.{digits}f}" if isinstance(value, float) else str(value)

    coverage_rows = _coverage_table_rows(coverage)
    coverage_table = original._markdown_table(
        ["Strategy", "Period", "Scheduled", "Decisions", "Coverage", "Fills", "Last decision", "Longest gap"],
        coverage_rows,
    )
    reversal_rows = [
        [cost, fmt(reversal[cost]["full"]["net_return"]), fmt(reversal[cost]["full"]["cagr"]), fmt(reversal[cost]["full"]["annualized_volatility"]), fmt(reversal[cost]["full"]["sharpe"]), fmt(reversal[cost]["full"]["sortino"]), fmt(reversal[cost]["full"]["max_drawdown"]), fmt(reversal[cost]["full"]["calmar"]), fmt(reversal[cost]["full"]["turnover"]), reversal[cost]["full"]["fill_count"]]
        for cost in (1, 2, 3)
    ]
    attr = sleeve["by_cost"]["1x"]
    gate_rows = [[row.get("name"), row.get("status"), json.dumps(row.get("evidence", {}), sort_keys=True)] for row in gates]
    return "\n".join(
        [
            "# Final corrected replay report: equity short-term residual reversal v1",
            "",
            "## Decision",
            "",
            f"`{decision}`",
            "",
            "## Replay validity",
            "",
            f"`{replay_validity}`",
            "",
            "## Starting state",
            "",
            f"Branch: `{manifest['branch']}`. Replay-start HEAD: `{start_head}`. Main: `{manifest['main_sha']}`. Forensic parent: `{manifest['forensic_parent_commit']}`.",
            "",
            "## Frozen economics confirmation",
            "",
            "The candidate, 21-session formation, zero skip, 273 aligned-return beta window, 63 residual-volatility window, negative-first ranking, no positive gate, top_n=6, ivol q=0.70, inverse-vol sizing, weekly cadence, 0.10 position cap, V3 control, 75/25 treatment allocation, chronology, costs, risk limits, and frozen acceptance gates were unchanged. No economic parameter was changed after any outcome was observed.",
            "",
            "## Corrections applied",
            "",
            "### Lifecycle repair",
            "",
            "The v5 inactive-entity terminal map was loaded into a research-only engine subclass. Held inactive symbols close on their real final bar with the existing accounting path, pending orders are removed, and the symbol is excluded from later completeness requirements without fake prices.",
            "",
            "### Decision coverage repair",
            "",
            f"Every scheduled attempt is persisted separately from fills. Coverage thresholds were frozen before outcomes: at least {MIN_DECISION_COVERAGE:.0%} eligible weekly decision coverage, no more than {MAX_UNEXPLAINED_GAP_WEEKS} consecutive skipped eligible weeks, validation decisions required, and no more than {MAX_FINAL_DECISION_LAG_WEEKS} eligible weeks of final lag.",
            "",
            "### Strategy-specific observation sets",
            "",
            "V3 was ranked by V3 alpha, reversal by the frozen reversal alpha, and treatment by the predeclared balanced V3/reversal round-robin union. The scanner eligible pool remained shared.",
            "",
            "### Sleeve-aware position-cap handling",
            "",
            "Treatment uses a one-account ledger with source-preserving contributions, 4/2 six-slot sleeve quotas, overlap-aware one-slot selection, proportional 0.10 per-symbol capping, explicit dropped-symbol reasons, and cash-held infeasible remainder. No capital is transferred between sleeves.",
            "",
            "### Verifier strengthening",
            "",
            "The separate verifier checks identity, candidate semantics, observation identity, lifecycle fixtures, coverage, sleeve attribution, risk caps, fills, costs, NAV, and cash before the frozen economic gates are interpreted.",
            "",
            "## Data identity",
            "",
            original._markdown_table(
                ["Item", "SHA / value"],
                [
                    ["Price panel", manifest["price_data_sha256"]],
                    ["Corporate actions", manifest["corporate_actions_sha256"]],
                    ["Entity lifetimes", manifest["lifetime_table_sha256"]],
                    ["Frozen protocol", manifest["protocol_sha256"]],
                    ["Corrected replay spec", manifest["corrected_replay_spec_sha256"]],
                ],
            ),
            "",
            "## Decision coverage",
            "",
            coverage_table,
            "",
            "Validation decisions, fills, first/last decision, skipped-attempt reasons, and longest gaps are independently persisted in `decision_coverage.json` and verified before this decision.",
            "",
            "## Observation-set identity",
            "",
            f"V3: `{observation['strategy_names']['v3']}`; reversal: `{observation['strategy_names']['reversal']}`; treatment: `{observation['strategy_names']['treatment']}`. Treatment snapshots with missing reversal top-six access: `{observation['treatment_reversal_top6_coverage']['snapshots_with_missing_reversal_top6']}`.",
            "",
            "## Standalone reversal corrected results",
            "",
            original._markdown_table(["Cost", "Net return", "CAGR", "Volatility", "Sharpe", "Sortino", "Max DD", "Calmar", "Turnover", "Fills"], reversal_rows),
            "",
            "## V3 control corrected results",
            "",
            f"At 1x: net return `{fmt(v3['full']['net_return'])}`, CAGR `{fmt(v3['full']['cagr'])}`, volatility `{fmt(v3['full']['annualized_volatility'])}`, Sharpe `{fmt(v3['full']['sharpe'])}`, Sortino `{fmt(v3['full']['sortino'])}`, max drawdown `{fmt(v3['full']['max_drawdown'])}`, Calmar `{fmt(v3['full']['calmar'])}`, turnover `{fmt(v3['full']['turnover'])}`. Full 1x/2x/3x control outputs are in `v3_control.json`.",
            "",
            "## Diversification",
            "",
            f"Full-period daily return correlation: `{fmt(diversification.get('daily_return_correlation'))}` over `{diversification.get('daily_observations')}` observations; monthly correlation: `{fmt(diversification.get('monthly_return_correlation'))}`. Holdings overlap and drawdown overlap are in `diversification.json`.",
            "",
            "## Corrected capital-neutral treatment",
            "",
            f"Intended sleeves were V3 `{FROZEN_V3_ALLOCATION}` and reversal `{FROZEN_REVERSAL_ALLOCATION}`. At 1x, mean realized target exposure was V3 `{fmt(attr['mean_realized_v3_target_exposure'])}` and reversal `{fmt(attr['mean_realized_reversal_target_exposure'])}`; mean unused allocation was V3 `{fmt(attr['mean_unused_v3_allocation'])}` and reversal `{fmt(attr['mean_unused_reversal_allocation'])}`.",
            "",
            "## Sleeve attribution",
            "",
            original._markdown_table(
                ["Metric", "V3", "Reversal"],
                [
                    ["Intended allocation", FROZEN_V3_ALLOCATION, FROZEN_REVERSAL_ALLOCATION],
                    ["Mean realized target exposure", fmt(attr["mean_realized_v3_target_exposure"]), fmt(attr["mean_realized_reversal_target_exposure"])],
                    ["Mean unused allocation", fmt(attr["mean_unused_v3_allocation"]), fmt(attr["mean_unused_reversal_allocation"])],
                    ["Zero-exposure decisions", 0, attr["zero_reversal_exposure_decisions"]],
                ],
            ),
            "",
            "## Capacity constraints",
            "",
            f"The six-position policy produced allocation-capacity shortfall on `{attr['allocation_capacity_shortfall_decisions']}` of `{attr['decision_count']}` 1x treatment decisions (`{fmt(attr['allocation_capacity_shortfall_pct'], 2)}%`). Overlap decisions: `{attr['overlap_decisions']}`. Reversal zero-exposure reasons: `{json.dumps(attr['zero_reversal_exposure_reasons'], sort_keys=True)}`. Infeasible remainder stayed cash.",
            "",
            "## Development vs validation",
            "",
            original._markdown_table(
                ["Run", "Window", "Net return", "CAGR", "Sharpe", "Sortino", "Max DD", "Turnover"],
                [
                    [name, period, fmt(bundle[period]["total_return"]), fmt(bundle[period]["cagr"]), fmt(bundle[period]["sharpe"]), fmt(bundle[period]["sortino"]), fmt(bundle[period]["max_drawdown"]), fmt(bundle[period]["turnover"])]
                    for name, bundle in (("V3", v3), ("Reversal 1x", reversal[1]), ("Treatment 1x", treatment[1]))
                    for period in ("development", "validation")
                ],
            ),
            "",
            "## Cost stress",
            "",
            "The unchanged 1x/2x/3x equity cost model is persisted in `cost_stress.json`; no cost was altered after observing results.",
            "",
            "## Concentration and robustness",
            "",
            f"Treatment robustness and leave-one-symbol-out diagnostics are in `robustness.json`. The 1x maximum realized concentration was `{fmt(treatment[1]['full']['maximum_concentration'])}`.",
            "",
            "## Independent verification",
            "",
            f"Verifier status: `{verification.get('status')}`. Checks passed: `{verification.get('passed_checks')}`; failures: `{verification.get('failure_count')}`. Lifecycle terminal-bar events, coverage, observation identities, source attribution, cap/cash invariants, fills, costs, NAV, and cash were checked before economic interpretation.",
            "",
            "## Frozen gate table",
            "",
            original._markdown_table(["Gate", "Status", "Evidence"], gate_rows),
            "",
            "## Comparison with invalid original campaign",
            "",
            "This is structural only. The original result JSON and report were not used as economic evidence. The corrected run removes the original zero-validation-decision interpretation, wires lifecycle handling, gives reversal its own observation ranker, and preserves treatment source attribution. Any corrected economic decision below is based only on the valid corrected replay and the frozen gates.",
            "",
            "## Final reasoning",
            "",
            f"Replay validity is `{replay_validity}` and the final economic decision is `{decision}`. The result is research-only; it does not enable PAPER/LIVE, register the reversal strategy in production, or place broker orders.",
            "",
            "## Files created",
            "",
            "- `CORRECTED_REPLAY_SPEC.md`",
            "- `replay_manifest.json`, `corrected_replay.py`, `corrected_mechanics.py`, `verify_corrected_replay.py`",
            "- `test_corrected_mechanics.py`, `test_corrected_replay.py`",
            "- `observation_sets.json`, `lifecycle_audit.json`, `decision_coverage.json`, `sleeve_attribution.json`",
            "- `v3_control.json`, `standalone_reversal.json`, `treatment.json`, `cost_stress.json`, `diversification.json`, `robustness.json`, `execution_audit.json`, `corrected_independent_verification.json`, `decision.json`, `gate_table.json`",
            "- `FINAL_CORRECTED_REPLAY_REPORT.md`",
            "",
            "## Final branch commit SHA",
            "",
            f"`{final_sha}`",
        ]
    ) + "\n"


def _assert_starting_state() -> tuple[str, str, str]:
    branch = _git("branch", "--show-current")
    head = _git("rev-parse", "HEAD")
    main = _git("rev-parse", "main")
    status = _git("status", "--porcelain")
    if branch != EXPECTED_BRANCH:
        raise RuntimeError(f"Corrected replay must run on {EXPECTED_BRANCH}, got {branch}")
    if main != EXPECTED_MAIN_SHA:
        raise RuntimeError(f"Production main moved: expected {EXPECTED_MAIN_SHA}, got {main}")
    if status:
        raise RuntimeError(f"Corrected replay requires a clean mechanics commit; uncommitted paths: {status}")
    if not SPEC_PATH.exists():
        raise RuntimeError("CORRECTED_REPLAY_SPEC.md must exist before economic outcomes")
    return branch, head, main


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=ROOT / "data/historical/equity_pit_symmetric_2016_2026_v5.csv")
    parser.add_argument("--lifetimes", type=Path, default=ROOT / "data/historical/equity_entity_lifetimes_full_v5.csv")
    parser.add_argument("--corporate-actions", type=Path, default=ROOT / "data/historical/equity_survivorship_full_corporate_actions_v5.csv")
    args = parser.parse_args(argv)
    if (OUTPUT_DIR / "replay_manifest.json").exists():
        raise RuntimeError("Corrected replay output already exists; the authorized replay is one-shot")
    campaign_start = time.time()
    branch, start_head, main_sha = _assert_starting_state()
    spec_sha = _hash_file(SPEC_PATH)
    config = load_config(ROOT / "config/settings.yaml")
    loaded = original.load_research_bars(args.data)
    actions, action_receipt = original.load_dividend_actions(args.corporate_actions, {bar.symbol for bar in loaded.bars}, REQUESTED_END)
    terminal_map, terminal_meta = _load_terminal_map(args.lifetimes)
    if loaded.receipt["source_sha256"] != EXPECTED_PRICE_SHA:
        raise RuntimeError("Price panel SHA does not match the frozen replay data")
    if action_receipt["source_sha256"] != EXPECTED_ACTION_SHA:
        raise RuntimeError("Corporate-action SHA does not match the frozen replay data")
    protocol_sha = _hash_file(RESEARCH_DIR / "FROZEN_PROTOCOL.md")
    v3_source_path = ROOT / "src/parayabuck/portfolio/equity_residual_momentum_v3.py"
    original_hashes = {name: _hash_file(ORIGINAL_DIR / name) for name in REQUIRED_ORIGINAL_FILES}
    manifest: dict[str, Any] = {
        "campaign": "equity_short_term_residual_reversal_v1",
        "replay": "corrected_replay_v1",
        "branch": branch,
        "main_sha": main_sha,
        "forensic_parent_commit": PARENT_FORENSIC_HEAD,
        "head_sha_at_replay_start": start_head,
        "protocol_sha256": protocol_sha,
        "corrected_replay_spec_sha256": spec_sha,
        "price_data_path": str(args.data),
        "lifetime_table_path": str(args.lifetimes),
        "corporate_actions_path": str(args.corporate_actions),
        "v3_source_path": str(v3_source_path),
        "v3_source_sha256": _hash_file(v3_source_path),
        "price_data_sha256": loaded.receipt["source_sha256"],
        "lifetime_table_sha256": terminal_meta["source_sha256"],
        "corporate_actions_sha256": action_receipt["source_sha256"],
        "windows": {
            "full_start": FULL_START.isoformat(),
            "full_end": REQUESTED_END.isoformat(),
            "development_start": DEVELOPMENT_START.isoformat(),
            "development_end": DEVELOPMENT_END.isoformat(),
            "validation_start": VALIDATION_START.isoformat(),
            "validation_end": REQUESTED_END.isoformat(),
        },
        "candidate_parameters": frozen_reversal_parameters(),
        "v3_parameters": dict(config.strategy.equity.parameters),
        "maximum_allocation": config.strategy.equity.maximum_allocation,
        "treatment_allocation": {"v3": FROZEN_V3_ALLOCATION, "reversal": FROZEN_REVERSAL_ALLOCATION, "total": 0.55, "cash_remainder": 0.45},
        "risk_limits": {
            "maximum_positions": FROZEN_MAX_POSITIONS,
            "maximum_single_weight": FROZEN_MAX_SINGLE_WEIGHT,
            "max_position_pct": config.risk.max_position_pct,
            "cash_buffer_pct": config.risk.cash_buffer_pct,
            "max_gross_exposure_pct": config.risk.max_gross_exposure_pct,
            "daily_loss_limit_pct": config.risk.daily_loss_limit_pct,
            "max_drawdown_pct": config.risk.max_drawdown_pct,
        },
        "execution_policy": {
            "slippage_bps": config.backtest.slippage_bps,
            "spread_bps": config.backtest.spread_bps,
            "commission": config.backtest.commission,
            "rebalance_band_pct": config.execution.rebalance_band_pct,
            "minimum_order_notional": config.execution.minimum_order_notional,
        },
        "cost_multipliers": [1, 2, 3],
        "mechanics_frozen_before_outcomes": True,
        "economic_results_generated": False,
        "original_artifact_sha256": original_hashes,
    }
    _write_json(OUTPUT_DIR / "replay_manifest.json", manifest)

    reversal_for_observation = EquityShortTermResidualReversalV1(frozen_reversal_parameters())
    v3_for_observation = EquityResidualMomentum1dV3(dict(config.strategy.equity.parameters))
    treatment_observation = TreatmentObservationRanker(
        EquityResidualMomentum1dV3(dict(config.strategy.equity.parameters)),
        EquityShortTermResidualReversalV1(frozen_reversal_parameters()),
    )
    v3_universe = _strategy_universe(config, loaded.bars, v3_for_observation)
    reversal_universe = _strategy_universe(config, loaded.bars, reversal_for_observation)
    treatment_universe = _strategy_universe(config, loaded.bars, treatment_observation)
    observation = _validate_universes(v3_universe, reversal_universe, treatment_universe)
    _write_json(OUTPUT_DIR / "observation_sets.json", observation)
    stream_symbols = _stream_symbols((v3_universe, reversal_universe, treatment_universe))
    engine_bars = sorted((bar for bar in loaded.bars if bar.symbol in stream_symbols), key=lambda bar: (bar.timestamp, bar.symbol))
    numeric_history = build_numeric_history(engine_bars, market=Market.EQUITY, timeframe="1d")
    records: dict[tuple[str, int], Any] = {}
    universes = {"v3": v3_universe, "reversal": reversal_universe, "treatment": treatment_universe}
    for name in ("v3", "reversal", "treatment"):
        for cost in (1, 2, 3):
            print(f"Running corrected {name} at {cost}x costs...")
            records[(name, cost)] = _run_one(
                name,
                cost,
                config,
                engine_bars,
                numeric_history,
                universes[name],
                actions,
                terminal_map,
            )

    v3_records = {cost: _period_bundle(records[("v3", cost)]) for cost in (1, 2, 3)}
    reversal_records = {cost: _period_bundle(records[("reversal", cost)]) for cost in (1, 2, 3)}
    treatment_records = {cost: _period_bundle(records[("treatment", cost)]) for cost in (1, 2, 3)}
    coverage = _coverage_payload(records)
    sleeve = _summarize_sleeve_attribution(records)
    lifecycle = _lifecycle_payload(records, terminal_meta)
    diversification = original.diversification_payload(records[("v3", 1)], records[("reversal", 1)])
    robustness = {name: original._robustness_record(records[(name, 1)]) for name in ("v3", "reversal", "treatment")}
    cost_stress = {"v3": v3_records, "reversal": reversal_records, "treatment": treatment_records}
    execution_audit = {f"{name}_{cost}x": records[(name, cost)].audit for name in ("v3", "reversal", "treatment") for cost in (1, 2, 3)}
    _write_json(OUTPUT_DIR / "v3_control.json", {"strategy": "equity_residual_momentum_v3", "costs": v3_records})
    _write_json(OUTPUT_DIR / "standalone_reversal.json", {"strategy": "equity_short_term_residual_reversal_v1", "costs": reversal_records})
    _write_json(OUTPUT_DIR / "treatment.json", {"strategy": "equity_v3_plus_residual_reversal_treatment", "costs": treatment_records})
    _write_json(OUTPUT_DIR / "cost_stress.json", cost_stress)
    _write_json(OUTPUT_DIR / "diversification.json", diversification)
    _write_json(OUTPUT_DIR / "robustness.json", robustness)
    _write_json(OUTPUT_DIR / "execution_audit.json", execution_audit)
    _write_json(OUTPUT_DIR / "decision_coverage.json", coverage)
    _write_json(OUTPUT_DIR / "sleeve_attribution.json", sleeve)
    _write_json(OUTPUT_DIR / "lifecycle_audit.json", lifecycle)

    manifest.update(
        {
            "economic_results_generated": True,
            "universe_hashes": observation["universe_hashes"],
            "stream_symbol_count": len(stream_symbols),
            "engine_bar_count": len(engine_bars),
            "loaded_bar_count": len(loaded.bars),
            "elapsed_seconds_before_verification": time.time() - campaign_start,
        }
    )
    _write_json(OUTPUT_DIR / "replay_manifest.json", manifest)

    verifier_path = OUTPUT_DIR / "verify_corrected_replay.py"
    subprocess.run([sys.executable, str(verifier_path), "--output", str(OUTPUT_DIR)], cwd=ROOT, check=True)
    verification = json.loads((OUTPUT_DIR / "corrected_independent_verification.json").read_text(encoding="utf-8"))
    replay_validity = "CORRECTED_REPLAY_VALID" if verification.get("status") == "CORRECTED_REPLAY_VALID" else "CORRECTED_REPLAY_INVALID"
    if replay_validity == "CORRECTED_REPLAY_VALID":
        gates = original._gate_table(reversal_records, v3_records[1], treatment_records, diversification, robustness)
        decision = original._verdict(gates, loaded.receipt)
    else:
        gates = [{"gate": index, "name": f"frozen gate {index}", "status": "NOT_EVALUATED", "evidence": {"reason": "corrected replay invalid"}} for index in range(1, 9)]
        decision = "RESEARCH_INCONCLUSIVE_DATA_OR_VALIDATION_LIMIT"
    _write_json(OUTPUT_DIR / "gate_table.json", gates)
    _write_json(OUTPUT_DIR / "decision.json", {"decision": decision, "replay_validity": replay_validity, "gates": gates})
    manifest.update(
        {
            "replay_validity": replay_validity,
            "decision": decision,
            "verification_status": verification.get("status"),
            "elapsed_seconds": time.time() - campaign_start,
        }
    )
    _write_json(OUTPUT_DIR / "replay_manifest.json", manifest)
    (OUTPUT_DIR / "FINAL_CORRECTED_REPLAY_REPORT.md").write_text(
        _build_final_report(
            decision=decision,
            replay_validity=replay_validity,
            start_head=start_head,
            final_sha="pending-uncommitted-research-branch",
            manifest=manifest,
            coverage=coverage,
            observation=observation,
            sleeve=sleeve,
            lifecycle=lifecycle,
            v3=v3_records[1],
            reversal=reversal_records,
            treatment=treatment_records,
            cost_stress=cost_stress,
            diversification=diversification,
            robustness=robustness,
            gates=gates,
            verification=verification,
        ),
        encoding="utf-8",
    )
    print(f"Corrected replay validity: {replay_validity}")
    print(f"Economic decision: {decision}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
