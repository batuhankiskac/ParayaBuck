"""Execute the frozen A/B/C SPY defensive-overlay campaign offline."""

from __future__ import annotations

import argparse
import gc
import json
import math
import statistics
import subprocess
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from parayabuck.backtest.numeric_history import build_numeric_history
from parayabuck.backtest.portfolio_engine import PortfolioBacktestEngine, PortfolioBacktestResult
from parayabuck.backtest.product import build_product_universe
from parayabuck.backtest.universe import PointInTimeUniverse, hash_point_in_time_universe
from parayabuck.config import load_config
from parayabuck.core.models import Bar, Market
from parayabuck.execution.portfolio import PortfolioExecutionPolicy, portfolio_risk_limits_from_config
from parayabuck.portfolio import rebalance_policy_for
from parayabuck.portfolio.equity_residual_momentum_v3 import EquityResidualMomentum1dV3
from research.spy_defensive_overlay_v1.candidate import EquitySpyDefensiveOverlayV1, OverlayArm
from research.spy_defensive_overlay_v1.data import FrozenData, load_frozen_data, sha256_file

ROOT = Path(__file__).resolve().parents[2]
OUTPUT_DIR = Path(__file__).resolve().parent
PROTOCOL = OUTPUT_DIR / "FROZEN_PROTOCOL.md"
FULL_START = datetime(2017, 1, 3, tzinfo=UTC)
DEVELOPMENT_END = datetime(2021, 12, 31, 23, 59, 59, tzinfo=UTC)
VALIDATION_START = datetime(2022, 1, 3, tzinfo=UTC)
FULL_END = datetime(2026, 7, 31, 23, 59, 59, tzinfo=UTC)
WINDOWS = {
    "full": (FULL_START, FULL_END),
    "development": (FULL_START, DEVELOPMENT_END),
    "validation": (VALIDATION_START, FULL_END),
}


@dataclass(frozen=True)
class CampaignRun:
    arm: OverlayArm
    cost_multiplier: int
    strategy: EquitySpyDefensiveOverlayV1
    result: PortfolioBacktestResult


def _git(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()


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
        return value.value
    return value


def write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(_safe(payload), indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")


def _prepared_window(bars: list[Bar]) -> tuple[list[Bar], list[Bar], tuple[list[Bar], list[Bar]]]:
    available = [bar for bar in bars if bar.timestamp <= FULL_END]
    warmup = [bar for bar in available if bar.timestamp < FULL_START]
    filtered = [bar for bar in available if bar.timestamp >= FULL_START]
    ordered = sorted(PortfolioBacktestEngine._deduplicate(filtered), key=lambda bar: (bar.timestamp, bar.symbol))
    warmup = sorted(PortfolioBacktestEngine._deduplicate(warmup), key=lambda bar: (bar.timestamp, bar.symbol))
    return filtered, warmup, (ordered, ordered)


def _curve_points(
    result: PortfolioBacktestResult, start: datetime, end: datetime
) -> tuple[float, list[datetime], list[float]]:
    selected = [
        (timestamp, value)
        for timestamp, value in zip(result.curve_timestamps, result.equity_curve, strict=True)
        if start <= timestamp <= end
    ]
    if not selected:
        raise ValueError(f"No curve points in {start.isoformat()}..{end.isoformat()}")
    first_index = next(index for index, timestamp in enumerate(result.curve_timestamps) if timestamp == selected[0][0])
    base = result.starting_equity if first_index == 0 else result.equity_curve[first_index - 1]
    return base, [row[0] for row in selected], [row[1] for row in selected]


def _monthly_returns(timestamps: list[datetime], values: list[float], base: float) -> list[float]:
    month_end: dict[tuple[int, int], float] = {}
    for timestamp, value in zip(timestamps, values, strict=True):
        month_end[(timestamp.year, timestamp.month)] = value
    previous = base
    returns: list[float] = []
    for key in sorted(month_end):
        value = month_end[key]
        returns.append(value / previous - 1.0 if previous > 0 else 0.0)
        previous = value
    return returns


def period_metrics(result: PortfolioBacktestResult, start: datetime, end: datetime) -> dict[str, Any]:
    base, timestamps, values = _curve_points(result, start, end)
    path = [base, *values]
    returns = [path[index] / path[index - 1] - 1.0 for index in range(1, len(path)) if path[index - 1] > 0]
    downside = [value for value in returns if value < 0]
    duration_days = max(1, int((timestamps[-1] - timestamps[0]).total_seconds() / 86_400))
    years = duration_days / 365.0
    mean = statistics.fmean(returns) if returns else 0.0
    std = statistics.stdev(returns) if len(returns) > 1 else 0.0
    downside_std = statistics.stdev(downside) if len(downside) > 1 else 0.0
    annualizer = math.sqrt(len(returns) / years) if returns and years > 0 else 1.0
    peak = base
    max_drawdown = 0.0
    for value in path:
        peak = max(peak, value)
        max_drawdown = max(max_drawdown, (peak - value) / peak if peak > 0 else 0.0)
    ending = values[-1]
    total_return = ending / base - 1.0 if base > 0 else 0.0
    cagr = (ending / base) ** (1.0 / years) - 1.0 if base > 0 and ending > 0 and years > 0 else 0.0
    fills = [fill for fill in result.fills if start <= fill.timestamp <= end]
    terminal = [
        event for event in result.terminal_events if start <= datetime.fromisoformat(str(event["timestamp"])) <= end
    ]
    rebalances = [record for record in result.rebalances if start <= record.decision_timestamp <= end]
    costs = sum(fill.slippage_cost + fill.spread_cost + fill.fee for fill in fills) + sum(
        float(event["slippage_cost"]) + float(event["spread_cost"]) + float(event["fee"]) for event in terminal
    )
    traded = sum(fill.qty * fill.reference_price for fill in fills) + sum(
        float(event["quantity"]) * float(event["last_close"]) for event in terminal
    )
    average_equity = statistics.fmean(path)
    exposures = [sum(weight for _symbol, weight in record.target_weights) for record in rebalances]
    concentration = [max((weight for _symbol, weight in record.target_weights), default=0.0) for record in rebalances]
    monthly = _monthly_returns(timestamps, values, base)
    return {
        "start": timestamps[0].isoformat(),
        "end": timestamps[-1].isoformat(),
        "starting_equity": base,
        "ending_equity": ending,
        "net_return": total_return,
        "cagr": cagr,
        "annualized_volatility": std * annualizer if std > 0 else 0.0,
        "sharpe": mean / std * annualizer if std > 0 else 0.0,
        "sortino": mean / downside_std * annualizer if downside_std > 0 else 0.0,
        "max_drawdown": max_drawdown,
        "calmar": cagr / max_drawdown if max_drawdown > 0 else 0.0,
        "worst_month": min(monthly, default=0.0),
        "turnover": traded / average_equity if average_equity > 0 else 0.0,
        "transaction_costs": costs,
        "average_exposure": statistics.fmean(exposures) if exposures else 0.0,
        "max_concentration": max(concentration, default=0.0),
        "fills": len(fills),
        "terminal_liquidations": len(terminal),
        "rebalances": len(rebalances),
        "duration_days": duration_days,
    }


def scheduled_weekly_timestamps(bars: list[Bar]) -> list[datetime]:
    spy = sorted({bar.timestamp for bar in bars if bar.symbol == "SPY" and FULL_START <= bar.timestamp <= FULL_END})
    scheduled: list[datetime] = []
    previous: datetime | None = None
    for timestamp in spy:
        if previous is None or previous.isocalendar()[:2] != timestamp.isocalendar()[:2]:
            scheduled.append(timestamp)
        previous = timestamp
    return scheduled


def coverage_payload(run: CampaignRun, engine_bars: list[Bar]) -> dict[str, Any]:
    scheduled = scheduled_weekly_timestamps(engine_bars)
    recorded = sorted(record.decision_timestamp for record in run.result.rebalances)
    recorded_set = set(recorded)
    sh_timestamps = {bar.timestamp for bar in engine_bars if bar.symbol == "SH"}
    skips = []
    for timestamp in scheduled:
        if timestamp in recorded_set:
            continue
        reason = (
            "missing_current_sh_bar"
            if run.arm == OverlayArm.DEFENSIVE_SH and timestamp not in sh_timestamps
            else "engine_universe_or_completeness_gate"
        )
        skips.append({"timestamp": timestamp.isoformat(), "reason": reason})
    gaps = [(right - left).total_seconds() / 86_400 for left, right in zip(recorded, recorded[1:], strict=False)]
    validation_decisions = sum(timestamp >= VALIDATION_START for timestamp in recorded)
    development_decisions = sum(timestamp <= DEVELOPMENT_END for timestamp in recorded)
    return {
        "scheduled_opportunities": len(scheduled),
        "recorded_decisions": len(recorded),
        "skipped_decisions": len(skips),
        "accounting_complete": len(scheduled) == len(recorded) + len(skips),
        "skips": skips,
        "first_decision": recorded[0].isoformat() if recorded else None,
        "last_decision": recorded[-1].isoformat() if recorded else None,
        "longest_decision_gap_days": max(gaps, default=0.0),
        "development_decisions": development_decisions,
        "validation_decisions": validation_decisions,
    }


def capacity_rows(run: CampaignRun) -> list[dict[str, Any]]:
    records = {record.decision_timestamp: record for record in run.result.rebalances}
    rows: list[dict[str, Any]] = []
    for timestamp, diagnostic in sorted(run.strategy.decision_diagnostics.items()):
        if diagnostic["regime"] != "DEFENSIVE":
            continue
        record = records.get(timestamp)
        if record is None:
            continue
        weights = dict(record.target_weights)
        total_sh = weights.get("SH", 0.0)
        v3_owned_sh = float(diagnostic["v3_owned_sh"])
        realized_overlay = max(0.0, total_sh - min(total_sh, v3_owned_sh))
        realized_total = sum(weights.values())
        realized_v3 = realized_total - realized_overlay
        reasons = list(diagnostic["capacity_loss_reasons"])
        intended_v3 = float(diagnostic["intended_v3_exposure"])
        source_v3_total = sum(float(row["weight"]) for row in diagnostic["source_v3_weights"])
        if realized_v3 + 1e-12 < min(intended_v3, source_v3_total) and "position_slot_limit" not in reasons:
            reasons.append("position_slot_limit")
        intended_overlay = float(diagnostic["intended_overlay_sh_exposure"])
        if realized_overlay + 1e-12 < intended_overlay and "overlay_not_fully_realized" not in reasons:
            reasons.append("overlay_not_fully_realized")
        capacity_loss_unused = max(0.0, intended_v3 + intended_overlay - realized_total) if reasons else 0.0
        rows.append(
            {
                "timestamp": timestamp.isoformat(),
                "arm": run.arm.value,
                "trend_return": diagnostic["trend_return"],
                "intended_v3_exposure": intended_v3,
                "realized_v3_exposure": realized_v3,
                "intended_sh_exposure": intended_overlay,
                "realized_overlay_sh_exposure": realized_overlay,
                "total_sh_exposure": total_sh,
                "v3_owned_sh": v3_owned_sh,
                "number_of_v3_names": sum(symbol != "SH" for symbol in weights),
                "total_positions": len(weights),
                "unused_equity_allocation": max(0.0, 0.55 - realized_total),
                "capacity_loss_unused_equity_allocation": capacity_loss_unused,
                "capacity_loss_reasons": reasons,
                "target_weights": sorted(weights.items()),
            }
        )
    return rows


def regime_payload(run: CampaignRun) -> dict[str, Any]:
    decisions = sorted((timestamp, str(row["regime"])) for timestamp, row in run.strategy.decision_diagnostics.items())
    counts = {name: sum(regime == name for _timestamp, regime in decisions) for name in ("NORMAL", "DEFENSIVE")}
    episodes: list[list[datetime]] = []
    current: list[datetime] = []
    previous_regime: str | None = None
    transitions = 0
    for timestamp, regime in decisions:
        if previous_regime is not None and regime != previous_regime:
            transitions += 1
        if regime == "DEFENSIVE":
            if previous_regime != "DEFENSIVE":
                current = []
                episodes.append(current)
            current.append(timestamp)
        previous_regime = regime
    total = len(decisions)
    decision_regimes = {timestamp: regime for timestamp, regime in decisions}
    ordered_decisions = sorted(decision_regimes)
    conditional: dict[str, list[float]] = defaultdict(list)
    previous_equity = run.result.starting_equity
    decision_index = -1
    active_regime: str | None = None
    for timestamp, equity in zip(run.result.curve_timestamps, run.result.equity_curve, strict=True):
        while decision_index + 1 < len(ordered_decisions) and ordered_decisions[decision_index + 1] <= timestamp:
            decision_index += 1
            active_regime = decision_regimes[ordered_decisions[decision_index]]
        daily_return = equity / previous_equity - 1.0 if previous_equity > 0 else 0.0
        if active_regime is not None:
            conditional[active_regime].append(daily_return)
        previous_equity = equity
    performance: dict[str, Any] = {}
    for regime in ("NORMAL", "DEFENSIVE"):
        values = conditional[regime]
        mean = statistics.fmean(values) if values else 0.0
        std = statistics.stdev(values) if len(values) > 1 else 0.0
        performance[regime] = {
            "observations": len(values),
            "compounded_return": math.prod(1.0 + value for value in values) - 1.0,
            "annualized_volatility": std * math.sqrt(252) if std > 0 else 0.0,
            "sharpe": mean / std * math.sqrt(252) if std > 0 else 0.0,
        }
    return {
        "normal_decisions": counts["NORMAL"],
        "defensive_decisions": counts["DEFENSIVE"],
        "normal_pct": counts["NORMAL"] / total if total else 0.0,
        "defensive_pct": counts["DEFENSIVE"] / total if total else 0.0,
        "distinct_defensive_episodes": len(episodes),
        "average_defensive_episode_decisions": statistics.fmean(map(len, episodes)) if episodes else 0.0,
        "regime_transitions": transitions,
        "conditional_performance": performance,
    }


def _make_engine(
    strategy: EquitySpyDefensiveOverlayV1,
    config: Any,
    universe: PointInTimeUniverse,
    cost_multiplier: int,
    data: FrozenData,
    stream_symbols: set[str],
) -> PortfolioBacktestEngine:
    return PortfolioBacktestEngine(
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
        risk_limits=portfolio_risk_limits_from_config(config.risk),
        maximum_allocation=config.strategy.equity.maximum_allocation,
        execution_policy=PortfolioExecutionPolicy(
            rebalance_band_pct=config.execution.rebalance_band_pct,
            minimum_order_notional=config.execution.minimum_order_notional,
        ),
        terminal_lifecycles={
            symbol: lifecycle for symbol, lifecycle in data.terminal_lifecycles.items() if symbol in stream_symbols
        },
    )


def _fill_row(fill: Any) -> dict[str, Any]:
    return {
        "timestamp": fill.timestamp.isoformat(),
        "decision_timestamp": fill.decision_timestamp.isoformat(),
        "symbol": fill.symbol,
        "side": fill.side.value,
        "qty": fill.qty,
        "reference_price": fill.reference_price,
        "fill_price": fill.fill_price,
        "slippage_cost": fill.slippage_cost,
        "spread_cost": fill.spread_cost,
        "fee": fill.fee,
    }


def _audit(run: CampaignRun) -> dict[str, Any]:
    return {
        "arm": run.arm.value,
        "cost_multiplier": run.cost_multiplier,
        "starting_equity": run.result.starting_equity,
        "ending_equity": run.result.ending_equity,
        "curve": list(zip(run.result.curve_timestamps, run.result.equity_curve, strict=True)),
        "fills": [_fill_row(fill) for fill in run.result.fills],
        "terminal_events": list(run.result.terminal_events),
        "rebalances": [
            {
                "decision_timestamp": record.decision_timestamp.isoformat(),
                "target_weights": list(record.target_weights),
                "target_cash_weight": record.target_cash_weight,
                "cost": record.cost,
                "realized_turnover": record.realized_turnover,
                "risk_overlay_halted": record.risk_overlay_halted,
                "risk_overlay_reasons": list(record.risk_overlay_reasons),
            }
            for record in run.result.rebalances
        ],
        "decision_diagnostics": run.strategy.decision_diagnostics,
        "dividend_total": run.result.dividend_total,
        "slippage_cost_total": run.result.slippage_cost_total,
        "spread_cost_total": run.result.spread_cost_total,
        "fee_total": run.result.fee_total,
        "state_cash": run.result.state.cash,
        "state_holdings": dict(run.result.state.holdings),
        "state_pending_orders": list(run.result.state.pending_orders),
    }


def whole_account_capacity_scenarios() -> list[dict[str, Any]]:
    rows = []
    for crypto_positions in (0, 1, 2):
        available_equity_slots = 6 - crypto_positions
        treatment_v3_slots = max(0, available_equity_slots - 1)
        maximum_equity_exposure = min(0.55, available_equity_slots * 0.10)
        rows.append(
            {
                "crypto_positions": crypto_positions,
                "available_equity_slots": available_equity_slots,
                "defensive_sh_slots": 1,
                "maximum_non_sh_v3_slots": treatment_v3_slots,
                "maximum_equity_exposure_at_0p10_cap": maximum_equity_exposure,
                "equity_under_utilization": max(0.0, 0.55 - maximum_equity_exposure),
            }
        )
    return rows


def run_campaign(repo_root: Path = ROOT) -> dict[str, Any]:
    started = time.time()
    config = load_config(repo_root / "config/settings.yaml")
    data = load_frozen_data(repo_root)
    v3_parameters = dict(config.strategy.equity.parameters)
    v3_observation = EquityResidualMomentum1dV3(v3_parameters)
    universe = build_product_universe(
        config,
        data.bars,
        market=Market.EQUITY,
        end=FULL_END,
        timeframe="1d",
        observation_strategy=v3_observation,
    )
    universe_hash = hash_point_in_time_universe(universe)
    stream_symbols = {"SPY", "SH"}
    for snapshot in universe.snapshots:
        stream_symbols.update(snapshot.observation_set or ())
        stream_symbols.update(snapshot.pinned_symbols)
    engine_bars = sorted(
        (bar for bar in data.bars if bar.symbol in stream_symbols and bar.timestamp <= FULL_END),
        key=lambda bar: (bar.timestamp, bar.symbol),
    )
    data.bars.clear()
    gc.collect()
    filtered, warmup, prepared = _prepared_window(engine_bars)
    numeric_history = build_numeric_history(engine_bars, market=Market.EQUITY, timeframe="1d")
    actions = [
        action for action in data.corporate_actions if action.symbol in stream_symbols and action.timestamp <= FULL_END
    ]
    runs: dict[str, CampaignRun] = {}
    for arm in OverlayArm:
        for cost in (1, 2, 3):
            strategy = EquitySpyDefensiveOverlayV1(arm, EquityResidualMomentum1dV3(v3_parameters))
            engine = _make_engine(strategy, config, universe, cost, data, stream_symbols)
            result = engine.run(
                filtered,
                warmup=warmup,
                corporate_actions=actions,
                prepared_inputs=prepared,
                numeric_history=numeric_history,
            )
            key = f"{arm.value}_{cost}x"
            runs[key] = CampaignRun(arm, cost, strategy, result)
            print(f"{key}: cagr={result.cagr:.6f} maxdd={result.max_drawdown:.6f}", flush=True)
    results: dict[str, Any] = {
        "campaign": "equity_spy_defensive_overlay_v1",
        "windows": {name: {"start": start, "end": end} for name, (start, end) in WINDOWS.items()},
        "data_hashes": data.hashes,
        "universe": {
            "hash": universe_hash,
            "snapshot_count": len(universe.snapshots),
            "stream_symbol_count": len(stream_symbols),
            "engine_bar_count": len(engine_bars),
            "observation_strategy": v3_observation.spec().to_dict(),
        },
        "whole_account_capacity_scenarios": whole_account_capacity_scenarios(),
        "runs": {},
    }
    audits: dict[str, Any] = {}
    for key, run in runs.items():
        rows = capacity_rows(run)
        results["runs"][key] = {
            "arm": run.arm.value,
            "cost_multiplier": run.cost_multiplier,
            "periods": {name: period_metrics(run.result, start, end) for name, (start, end) in WINDOWS.items()},
            "coverage": coverage_payload(run, engine_bars),
            "regime": regime_payload(run),
            "capacity": {
                "defensive_decisions": len(rows),
                "rows": rows,
                "average_unused_equity_allocation": statistics.fmean(
                    [float(row["unused_equity_allocation"]) for row in rows]
                )
                if rows
                else 0.0,
            },
        }
        audits[key] = _audit(run)
    manifest = {
        "campaign": "equity_spy_defensive_overlay_v1",
        "cleaned_main_sha": _git("rev-parse", "main"),
        "research_head_at_run": _git("rev-parse", "HEAD"),
        "branch": _git("branch", "--show-current"),
        "protocol_sha256": sha256_file(PROTOCOL),
        "data_hashes": data.hashes,
        "universe_hash": universe_hash,
        "v3_source_sha256": sha256_file(repo_root / "src/parayabuck/portfolio/equity_residual_momentum_v3.py"),
        "v3_parameters": v3_parameters,
        "paper_config_unchanged": True,
        "live_unchanged_disabled": True,
        "elapsed_seconds": time.time() - started,
    }
    write_json(OUTPUT_DIR / "campaign_results.json", results)
    write_json(OUTPUT_DIR / "execution_audit.json", audits)
    write_json(OUTPUT_DIR / "run_manifest.json", manifest)
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args(argv)
    run_campaign()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
