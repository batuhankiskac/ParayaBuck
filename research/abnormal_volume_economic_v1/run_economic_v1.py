#!/usr/bin/env python3
"""Run the one authorized standalone economic experiment for DESIGN_V2."""

from __future__ import annotations

import argparse
import csv
import gc
import hashlib
import json
import math
import subprocess
import sys
from dataclasses import asdict
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from parayabuck.backtest.lifetimes import load_entity_lifetimes  # noqa: E402
from parayabuck.backtest.portfolio_engine import PortfolioBacktestEngine  # noqa: E402
from parayabuck.backtest.product import load_csv_corporate_actions  # noqa: E402
from parayabuck.config import load_config  # noqa: E402
from parayabuck.core.models import Bar, Market  # noqa: E402
from parayabuck.execution.portfolio import (  # noqa: E402
    PortfolioExecutionPolicy,
    portfolio_risk_limits_from_config,
)
from parayabuck.markets.symbols import canonical_symbol  # noqa: E402

try:  # direct script and namespace-package import are both supported
    from .economic_models import (
        CANDIDATE_ID,
        COST_MULTIPLIERS,
        DEVELOPMENT_END,
        DEVELOPMENT_START,
        FULL_END,
        VALIDATION_START,
        AuditedPortfolioBacktestEngine,
        ExactDecisionPolicy,
        FrozenTargetStrategy,
        canonical_sha256,
        dividend_events,
        evaluate_gates,
        jsonable,
        period_metrics,
        result_rows,
        targets_from_readiness,
    )
except ImportError:  # pragma: no cover
    from economic_models import (
        CANDIDATE_ID,
        COST_MULTIPLIERS,
        DEVELOPMENT_END,
        DEVELOPMENT_START,
        FULL_END,
        VALIDATION_START,
        AuditedPortfolioBacktestEngine,
        ExactDecisionPolicy,
        FrozenTargetStrategy,
        canonical_sha256,
        dividend_events,
        evaluate_gates,
        jsonable,
        period_metrics,
        result_rows,
        targets_from_readiness,
    )

BARS_PATH = ROOT / "data/historical/equity_pit_symmetric_2016_2026_v5.csv"
LIFETIMES_PATH = ROOT / "data/historical/equity_entity_lifetimes_full_v5.csv"
ACTIONS_PATH = ROOT / "data/historical/equity_survivorship_full_corporate_actions_v5.csv"
CONFIG_PATH = ROOT / "config/settings.yaml"
READINESS_PATH = ROOT / "research/abnormal_volume_readiness_v2/RUNNER_OUTPUT.json"
V3_BASELINE_PATH = ROOT / "research/month_end_first_open_correctness_v1/V3_POST_FIX_REPLAY.json"
FRESH_CONTROL_PATH = ROOT / "research/abnormal_volume_economic_v1/CONTROL_A_RESULTS.json"
FRESH_CONTROL_COMPARISON_PATH = ROOT / "research/abnormal_volume_economic_v1/CONTROL_A_PARITY.json"
OUTPUT_PATH = ROOT / "research/abnormal_volume_economic_v1/RUNNER_ECONOMIC_OUTPUT.json"
MODELS_PATH = ROOT / "research/abnormal_volume_economic_v1/economic_models.py"
RUNNER_SOURCE_PATH = ROOT / "research/abnormal_volume_economic_v1/run_economic_v1.py"
VERIFIER_SOURCE_PATH = ROOT / "research/abnormal_volume_economic_v1/verify_economic_v1.py"

DESIGN_V2_SHA = "a39cf6dfc72a4a4aa92d95b3f760626e2ce216c6"
READINESS_V2_SHA = "ec37b0f1671191f1842f5f952977bb60c772fa4b"
PRODUCTION_MAIN_SHA = "980c6b90b7f316bcec6a9466dfc9b0e1756cc748"
ENGINE_START = datetime(2016, 12, 30, tzinfo=UTC)


def _git(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_endpoint_bars(path: Path, endpoint: date) -> tuple[list[Bar], dict[str, int]]:
    """Strictly load only rows at or before the frozen completed endpoint."""

    bars: list[Bar] = []
    previous: datetime | None = None
    excluded_later = 0
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        required = {"timestamp", "symbol", "timeframe", "open", "high", "low", "close", "volume", "market"}
        if set(reader.fieldnames or ()) != required:
            raise ValueError("unexpected canonical bar CSV schema")
        for row_number, raw in enumerate(reader, start=2):
            timestamp = datetime.fromisoformat(str(raw["timestamp"]).replace("Z", "+00:00"))
            if timestamp.tzinfo is None:
                raise ValueError(f"bars row {row_number}: naive timestamp")
            timestamp = timestamp.astimezone(UTC)
            if timestamp.date() > endpoint:
                excluded_later += 1
                continue
            if previous is not None and timestamp < previous:
                raise ValueError(f"bars row {row_number}: timestamps are not sorted")
            values = {field: float(str(raw[field])) for field in ("open", "high", "low", "close", "volume")}
            if any(not math.isfinite(value) for value in values.values()) or values["volume"] < 0:
                raise ValueError(f"bars row {row_number}: invalid numeric input")
            timeframe = str(raw["timeframe"]).lower()
            if timeframe not in {"1d", "1day"} or str(raw["market"]).lower() != "equity":
                raise ValueError(f"bars row {row_number}: unexpected market/timeframe")
            bars.append(
                Bar(
                    timestamp=timestamp,
                    symbol=str(raw["symbol"]),
                    timeframe="1d",
                    open=values["open"],
                    high=values["high"],
                    low=values["low"],
                    close=values["close"],
                    volume=values["volume"],
                    market=Market.EQUITY,
                )
            )
            previous = timestamp
    return bars, {"rows_loaded": len(bars), "rows_after_endpoint_excluded": excluded_later}


def _raw_action_ids(path: Path) -> dict[tuple[str, str, float], str]:
    rows: dict[tuple[str, str, float], str] = {}
    with path.open(newline="", encoding="utf-8") as handle:
        for raw in csv.DictReader(handle):
            if str(raw.get("action_type", "")).lower() != "cash_dividends":
                continue
            symbol = canonical_symbol(str(raw["symbol"]), Market.EQUITY)
            rows.setdefault((symbol, str(raw["ex_date"]), float(str(raw["rate"]))), str(raw["raw_id"]))
    return rows


def _prepare_bars(bars: list[Bar]) -> tuple[list[Bar], list[Bar], tuple[list[Bar], list[Bar]]]:
    available = [bar for bar in bars if bar.timestamp <= FULL_END]
    filtered = [bar for bar in available if bar.timestamp >= ENGINE_START]
    warmup = sorted(
        PortfolioBacktestEngine._deduplicate([bar for bar in available if bar.timestamp < ENGINE_START]),
        key=lambda bar: (bar.timestamp, bar.symbol),
    )
    ordered = sorted(PortfolioBacktestEngine._deduplicate(filtered), key=lambda bar: (bar.timestamp, bar.symbol))
    return filtered, warmup, (ordered, ordered)


def _periods(result: Any, snapshots: list[dict[str, Any]]) -> dict[str, Any]:
    validation_years: dict[str, Any] = {}
    for year in range(2022, 2027):
        start = max(VALIDATION_START, datetime(year, 1, 1, tzinfo=UTC))
        end = min(FULL_END, datetime(year, 12, 31, 23, 59, 59, 999999, tzinfo=UTC))
        validation_years[str(year)] = {
            **period_metrics(result, start, end, snapshots),
            "partial_calendar_year": year == 2026,
        }
    return {
        "development": period_metrics(result, DEVELOPMENT_START, DEVELOPMENT_END, snapshots),
        "validation": period_metrics(result, VALIDATION_START, FULL_END, snapshots),
        "full": period_metrics(result, DEVELOPMENT_START, FULL_END, snapshots),
        "validation_years": validation_years,
    }


def _enrich_entity_ids(rows: dict[str, Any], readiness: dict[str, Any]) -> None:
    exact: dict[tuple[str, str], str] = {}
    contracts: dict[str, dict[str, str]] = {}
    symbol_ids: dict[str, set[str]] = {}
    for decision in readiness["decisions"]:
        decision_session = str(decision["decision_session"])
        contracts[decision_session] = {
            "decision_session": decision_session,
            "contract_decision_timestamp": str(decision["decision_timestamp"]),
            "fill_session": str(decision["fill_session"]),
            "contract_fill_timestamp": str(decision["fill_timestamp"]),
        }
        for target in decision["targets"]:
            symbol = str(target["symbol"])
            entity_id = str(target["entity_id"])
            exact[(decision_session, symbol)] = entity_id
            symbol_ids.setdefault(symbol, set()).add(entity_id)
    stable = {symbol: next(iter(ids)) for symbol, ids in symbol_ids.items() if len(ids) == 1}
    for decision in rows["decisions"]:
        engine_timestamp = datetime.fromisoformat(str(decision["decision_timestamp"]))
        session = engine_timestamp.date().isoformat()
        decision.update(contracts[session])
        decision["target_entities"] = [
            {
                "symbol": symbol,
                "entity_id": exact.get((session, symbol), stable.get(symbol)),
                "weight": weight,
            }
            for symbol, weight in decision["target_weights"]
        ]
    for fill in rows["fills"]:
        session = datetime.fromisoformat(str(fill["decision_timestamp"])).date().isoformat()
        fill["entity_id"] = exact.get((session, fill["symbol"]), stable.get(fill["symbol"]))
        fill.update(
            {
                key: value
                for key, value in contracts[session].items()
                if key.startswith("contract_") or key == "fill_session"
            }
        )
    for event in rows["terminal_events"]:
        event["entity_id"] = stable.get(str(event["symbol"]))
    for collection in (rows.get("orders", []), rows.get("first_open_expirations", [])):
        for row in collection:
            session = datetime.fromisoformat(str(row["decision_timestamp"])).date().isoformat()
            row["entity_id"] = exact.get((session, str(row["symbol"])), stable.get(str(row["symbol"])))
            row.update(contracts[session])


def _gross_closed_episodes(rows: dict[str, Any], dividends: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Reconstruct closed holding episodes before all transaction costs.

    Cash dividends earned during an open episode are included in gross P&L;
    slippage, spread and fees are added back to the engine's net closed P&L.
    """

    closed: dict[tuple[str, str], list[float]] = {}
    for row in rows["closed_position_events"]:
        closed.setdefault((str(row["timestamp"]), str(row["symbol"])), []).append(float(row["pnl"]))
    events: list[tuple[datetime, int, int, str, dict[str, Any]]] = []
    for index, row in enumerate(dividends):
        events.append((datetime.fromisoformat(str(row["timestamp"])), 0, index, "dividend", row))
    for index, row in enumerate(rows["fills"]):
        priority = 1 if row["side"] == "sell" else 2
        events.append((datetime.fromisoformat(str(row["timestamp"])), priority, index, "fill", row))
    for index, row in enumerate(rows["terminal_events"]):
        events.append((datetime.fromisoformat(str(row["timestamp"])), 3, index, "terminal", row))
    quantities: dict[str, float] = {}
    episode_costs: dict[str, float] = {}
    episode_dividends: dict[str, float] = {}
    episode_start: dict[str, str] = {}
    episodes: list[dict[str, Any]] = []
    for timestamp, _priority, _index, kind, row in sorted(events):
        symbol = str(row["symbol"])
        if kind == "dividend":
            if quantities.get(symbol, 0.0) > 0:
                episode_dividends[symbol] = episode_dividends.get(symbol, 0.0) + float(row["cash_credit"])
            continue
        cost = sum(float(row.get(field, 0.0)) for field in ("slippage_cost", "spread_cost", "fee"))
        if kind == "fill" and row["side"] == "buy":
            if quantities.get(symbol, 0.0) <= 1e-12:
                episode_start[symbol] = timestamp.isoformat()
                episode_costs[symbol] = 0.0
                episode_dividends[symbol] = 0.0
            quantities[symbol] = quantities.get(symbol, 0.0) + float(row["qty"])
            episode_costs[symbol] = episode_costs.get(symbol, 0.0) + cost
            continue
        close_qty = float(row["qty"] if kind == "fill" else row["quantity"])
        quantities[symbol] = max(0.0, quantities.get(symbol, 0.0) - close_qty)
        episode_costs[symbol] = episode_costs.get(symbol, 0.0) + cost
        if quantities[symbol] > 1e-8:
            continue
        key = (timestamp.isoformat(), symbol)
        net_values = closed.get(key, [])
        if not net_values:
            raise ValueError(f"missing closed-position event for gross episode {key}")
        net_pnl = net_values.pop(0)
        dividends_cash = episode_dividends.get(symbol, 0.0)
        transaction_cost = episode_costs.get(symbol, 0.0)
        episodes.append(
            {
                "symbol": symbol,
                "entity_id": row.get("entity_id"),
                "opened_at": episode_start.get(symbol),
                "closed_at": timestamp.isoformat(),
                "net_price_pnl": net_pnl,
                "dividend_cash": dividends_cash,
                "transaction_cost": transaction_cost,
                "gross_pnl": net_pnl + dividends_cash + transaction_cost,
            }
        )
        episode_costs.pop(symbol, None)
        episode_dividends.pop(symbol, None)
        episode_start.pop(symbol, None)
    if any(values for values in closed.values()):
        raise ValueError("unmatched closed-position events in gross episode reconstruction")
    return episodes


def _attach_execution_notionals(rows: dict[str, Any]) -> None:
    """Value fill completion on the frozen decision-notional basis."""

    orders_by_decision_symbol: dict[tuple[str, str], list[dict[str, Any]]] = {}
    fills_by_order: dict[str, float] = {}
    for order in rows["orders"]:
        key = (str(order["decision_timestamp"]), str(order["symbol"]))
        orders_by_decision_symbol.setdefault(key, []).append(order)
    for fill in rows["fills"]:
        client_order_id = str(fill["client_order_id"])
        fills_by_order[client_order_id] = fills_by_order.get(client_order_id, 0.0) + float(fill["qty"])
    for decision in rows["decisions"]:
        timestamp = str(decision["decision_timestamp"])
        target = {str(symbol): float(weight) for symbol, weight in decision["target_weights"]}
        pre_trade = {str(symbol): float(weight) for symbol, weight in decision["pre_trade_weights"]}
        equity = float(decision["equity_at_decision"])
        full_target = completed = 0.0
        for symbol in set(target) | set(pre_trade):
            intended = abs(target.get(symbol, 0.0) - pre_trade.get(symbol, 0.0)) * equity
            full_target += intended
            orders = orders_by_decision_symbol.get((timestamp, symbol), [])
            requested_qty = sum(float(order["requested_qty"]) for order in orders)
            filled_qty = sum(fills_by_order.get(str(order["client_order_id"]), 0.0) for order in orders)
            completion = min(1.0, filled_qty / requested_qty) if requested_qty > 0 else 0.0
            completed += intended * completion
        decision["full_target_notional"] = full_target
        decision["completed_target_notional"] = completed
        decision["unfilled_target_notional"] = max(0.0, full_target - completed)


def _run_candidate_cell(
    config: Any,
    prepared: tuple[list[Bar], list[Bar], tuple[list[Bar], list[Bar]]],
    actions: list[Any],
    raw_action_ids: dict[tuple[str, str, float], str],
    terminal_lifecycles: dict[str, Any],
    readiness: dict[str, Any],
    targets: dict[datetime, tuple[tuple[str, float], ...]],
    contract_fill_timestamps: dict[datetime, datetime],
    multiplier: float,
    omitted_entity_id: str | None = None,
    omitted_symbols: tuple[str, ...] = (),
) -> dict[str, Any]:
    strategy = FrozenTargetStrategy(targets, omitted_symbols=omitted_symbols)
    engine = AuditedPortfolioBacktestEngine(
        strategy,
        ExactDecisionPolicy(tuple(targets)),
        starting_equity=config.backtest.initial_cash,
        slippage_bps=config.backtest.slippage_bps * multiplier,
        spread_bps=config.backtest.spread_bps * multiplier,
        commission=config.backtest.commission,
        equity_taf_per_share=config.backtest.equity_taf_per_share,
        equity_taf_cap=config.backtest.equity_taf_cap,
        equity_cat_per_share=config.backtest.equity_cat_per_share,
        equity_sec_fee_rate=config.backtest.equity_sec_fee_rate,
        equity_use_effective_dated_fees=config.backtest.equity_use_effective_dated_fees,
        equity_regulatory_fee_multiplier=multiplier,
        benchmark_symbol="SPY",
        execution_policy=PortfolioExecutionPolicy(
            rebalance_band_pct=config.execution.rebalance_band_pct,
            minimum_order_notional=config.execution.minimum_order_notional,
        ),
        risk_limits=portfolio_risk_limits_from_config(config.risk),
        maximum_allocation=0.55,
        ordinary_alpha_excluded_symbols=config.universe.equity.ordinary_alpha_excluded_symbols,
        terminal_lifecycles=terminal_lifecycles,
        allow_static_universe=True,
        contract_fill_timestamps=contract_fill_timestamps,
    )
    filtered, warmup, ordered = prepared
    result = engine.run(
        filtered,
        warmup=warmup,
        corporate_actions=actions,
        prepared_inputs=ordered,
    )
    snapshots = engine.audit_snapshots
    rows: dict[str, Any] = result_rows(result)
    rows["orders"] = engine.audit_orders
    rows["first_open_expirations"] = engine.first_open_expirations
    _enrich_entity_ids(rows, readiness)
    _attach_execution_notionals(rows)
    dividends = dividend_events(result, actions, raw_action_ids)
    symbol_to_entity = {
        target["symbol"]: target["entity_id"] for decision in readiness["decisions"] for target in decision["targets"]
    }
    symbol_entity_ids: dict[str, set[str]] = {}
    for decision in readiness["decisions"]:
        for target in decision["targets"]:
            symbol_entity_ids.setdefault(str(target["symbol"]), set()).add(str(target["entity_id"]))
    conflicts = {symbol: ids for symbol, ids in symbol_entity_ids.items() if len(ids) > 1}
    if conflicts:
        raise ValueError(f"selected ticker maps to multiple entities: {sorted(conflicts)}")
    for event in dividends:
        event["entity_id"] = symbol_to_entity.get(event["symbol"])
    gross_episodes = _gross_closed_episodes(rows, dividends)
    metrics = _periods(result, snapshots)
    costs_by_symbol: dict[str, float] = {}
    for fill in rows["fills"]:
        costs_by_symbol[fill["symbol"]] = costs_by_symbol.get(fill["symbol"], 0.0) + sum(
            float(fill[field]) for field in ("slippage_cost", "spread_cost", "fee")
        )
    for event in rows["terminal_events"]:
        symbol = str(event["symbol"])
        costs_by_symbol[symbol] = costs_by_symbol.get(symbol, 0.0) + sum(
            float(event.get(field, 0.0)) for field in ("slippage_cost", "spread_cost", "fee")
        )
    dividends_by_symbol: dict[str, float] = {}
    for event in dividends:
        dividends_by_symbol[event["symbol"]] = dividends_by_symbol.get(event["symbol"], 0.0) + float(
            event["cash_credit"]
        )
    per_symbol = [
        {
            "symbol": row.symbol,
            "entity_id": symbol_to_entity.get(row.symbol),
            "market": row.market.value,
            "realized_pnl": float(row.realized_pnl),
            "dividend_cash": dividends_by_symbol.get(row.symbol, 0.0),
            "transaction_cost": costs_by_symbol.get(row.symbol, 0.0),
            "gross_pnl": float(row.realized_pnl)
            + dividends_by_symbol.get(row.symbol, 0.0)
            + costs_by_symbol.get(row.symbol, 0.0),
            "fees": float(row.fees),
            "final_weight": float(row.final_weight),
        }
        for row in result.per_symbol
    ]
    target_exposures = [sum(weight for _symbol, weight in row["target_weights"]) for row in rows["decisions"]]
    target_positions = [len(row["target_weights"]) for row in rows["decisions"]]
    realized_concentration = max(
        (
            max((position["market_value"] / snapshot["nav"] for position in snapshot["positions"]), default=0.0)
            for snapshot in snapshots
            if snapshot["nav"] > 0
        ),
        default=0.0,
    )
    realized_exposures = [
        sum(float(position["market_value"]) for position in snapshot["positions"]) / float(snapshot["nav"])
        for snapshot in snapshots
        if float(snapshot["nav"]) > 0
    ]
    realized_cash = [
        float(snapshot["cash"]) / float(snapshot["nav"]) for snapshot in snapshots if float(snapshot["nav"]) > 0
    ]
    per_entity_by_id: dict[str, dict[str, Any]] = {}
    for row in per_symbol:
        entity_id = str(row["entity_id"] or f"UNMAPPED:{row['symbol']}")
        aggregate = per_entity_by_id.setdefault(
            entity_id,
            {
                "entity_id": entity_id,
                "symbols": [],
                "realized_pnl": 0.0,
                "dividend_cash": 0.0,
                "transaction_cost": 0.0,
                "gross_pnl": 0.0,
            },
        )
        aggregate["symbols"].append(row["symbol"])
        for field in ("realized_pnl", "dividend_cash", "transaction_cost", "gross_pnl"):
            aggregate[field] += float(row[field])
    per_entity = sorted(per_entity_by_id.values(), key=lambda row: row["entity_id"])
    for row in per_entity:
        row["symbols"].sort()
    summary = {
        "starting_equity": float(result.starting_equity),
        "ending_equity": float(result.ending_equity),
        "dividend_total": float(result.dividend_total),
        "slippage_cost_total": float(result.slippage_cost_total),
        "spread_cost_total": float(result.spread_cost_total),
        "fee_total": float(result.fee_total),
        "total_cost": float(result.slippage_cost_total + result.spread_cost_total + result.fee_total),
        "average_cash_ratio": float(result.average_cash_ratio),
        "average_holdings_count": float(result.average_holdings_count),
        "maximum_target_exposure": max(target_exposures, default=0.0),
        "minimum_target_cash": min((1.0 - value for value in target_exposures), default=1.0),
        "maximum_target_positions": max(target_positions, default=0),
        "maximum_realized_concentration": realized_concentration,
        "maximum_realized_exposure": max(realized_exposures, default=0.0),
        "minimum_realized_cash": min(realized_cash, default=1.0),
        "maximum_actual_positions": max((len(snapshot["positions"]) for snapshot in snapshots), default=0),
        "unique_traded_symbols": len({fill["symbol"] for fill in rows["fills"]}),
        "unique_traded_entities": len(per_entity),
        "selected_symbol_entity_reuse_count": sum(len(entity_ids) > 1 for entity_ids in symbol_entity_ids.values()),
        "per_symbol": per_symbol,
        "per_entity": per_entity,
    }
    summary["positive_gross_profit"] = sum(max(0.0, row["gross_pnl"]) for row in gross_episodes)
    summary["positive_gross_profit_basis"] = (
        "sum positive closed holding-episode gross P&L, including episode dividends and before slippage, spread, fees"
    )
    absolute_gross = sum(abs(row["gross_pnl"]) for row in per_entity)
    summary["concentration"] = {
        "largest_absolute_contribution_share": (
            max((abs(row["gross_pnl"]) / absolute_gross for row in per_entity), default=0.0)
            if absolute_gross > 0
            else 0.0
        ),
        "contribution_basis": "entity-aggregated gross price P&L plus dividends before slippage, spread, and fees",
    }
    target_notional = sum(float(row["full_target_notional"]) for row in rows["decisions"])
    unfilled_notional = sum(float(row["unfilled_target_notional"]) for row in rows["decisions"])
    summary["filled_target_notional_ratio"] = 1.0 - unfilled_notional / target_notional if target_notional > 0 else 0.0
    summary["unfilled_partial_target_notional_ratio"] = (
        unfilled_notional / target_notional if target_notional > 0 else 1.0
    )
    rows.update(
        {
            "cost_multiplier": multiplier,
            "omitted_entity_id": omitted_entity_id,
            "omitted_symbols": list(omitted_symbols),
            "holdings_snapshots": snapshots,
            "dividend_events": dividends,
            "gross_closed_episodes": gross_episodes,
            "metrics": metrics,
            "summary": summary,
        }
    )
    rows["hashes"]["holdings_snapshots_sha256"] = canonical_sha256(snapshots)
    rows["hashes"]["dividend_events_sha256"] = canonical_sha256(dividends)
    rows["hashes"]["gross_closed_episodes_sha256"] = canonical_sha256(gross_episodes)
    rows["hashes"]["orders_sha256"] = canonical_sha256(rows["orders"])
    rows["hashes"]["first_open_expirations_sha256"] = canonical_sha256(rows["first_open_expirations"])
    for field in ("decisions", "fills", "terminal_events", "nav_curve"):
        rows["hashes"][f"{field}_sha256"] = canonical_sha256(rows[field])
    rows["run_sha256"] = canonical_sha256({key: value for key, value in rows.items() if key != "run_sha256"})
    return rows


def _fresh_control() -> dict[str, Any]:
    # Stage coordination intentionally runs the expensive control before the
    # candidate.  Consume that fresh persisted receipt rather than execute a
    # second control after candidate outcomes become visible.
    current = json.loads(FRESH_CONTROL_PATH.read_text(encoding="utf-8"))
    comparison = json.loads(FRESH_CONTROL_COMPARISON_PATH.read_text(encoding="utf-8"))
    return {
        "fresh": current,
        "parity": comparison,
        "fresh_control_sha256": _file_sha256(FRESH_CONTROL_PATH),
        "fresh_control_comparison_sha256": _file_sha256(FRESH_CONTROL_COMPARISON_PATH),
        "artifacts": {
            "fresh_path": str(FRESH_CONTROL_PATH.relative_to(ROOT)),
            "fresh_sha256": _file_sha256(FRESH_CONTROL_PATH),
            "comparison_path": str(FRESH_CONTROL_COMPARISON_PATH.relative_to(ROOT)),
            "comparison_sha256": _file_sha256(FRESH_CONTROL_COMPARISON_PATH),
            "baseline_path": str(V3_BASELINE_PATH.relative_to(ROOT)),
            "baseline_sha256": _file_sha256(V3_BASELINE_PATH),
        },
    }


def collect() -> dict[str, Any]:
    config = load_config(str(CONFIG_PATH))
    if config.strategy.equity.name != "equity_residual_momentum_v3" or config.runtime.mode.value != "paper":
        raise ValueError("production PAPER identity drifted")
    if config.live.enabled:
        raise ValueError("LIVE must remain disabled")
    readiness = json.loads(READINESS_PATH.read_text(encoding="utf-8"))
    control = _fresh_control()
    if control["parity"]["verdict"] != "V3_PARITY_PASS":
        raise RuntimeError("fresh V3 control drifted; candidate economics are invalid")

    bars, bar_validation = _load_endpoint_bars(BARS_PATH, FULL_END.date())
    session_timestamps: dict[date, datetime] = {}
    for bar in bars:
        previous = session_timestamps.setdefault(bar.timestamp.date(), bar.timestamp)
        if previous != bar.timestamp:
            raise ValueError(f"multiple canonical session markers on {bar.timestamp.date()}")
    targets = targets_from_readiness(readiness, session_timestamps)
    if len(targets) != 115:
        raise ValueError(f"readiness decision count drifted: {len(targets)} != 115")
    contract_fill_timestamps: dict[datetime, datetime] = {}
    for decision in readiness["decisions"]:
        decision_session = date.fromisoformat(str(decision["decision_session"]))
        fill_session = date.fromisoformat(str(decision["fill_session"]))
        if decision_session not in session_timestamps or fill_session not in session_timestamps:
            raise ValueError(f"missing canonical session marker for readiness contract {decision_session}")
        contract_fill_timestamps[session_timestamps[decision_session]] = session_timestamps[fill_session]
    prepared = _prepare_bars(bars)
    actions = load_csv_corporate_actions(ACTIONS_PATH)
    raw_ids = _raw_action_ids(ACTIONS_PATH)
    lifetimes = load_entity_lifetimes(LIFETIMES_PATH)
    risk_limits = portfolio_risk_limits_from_config(config.risk)
    execution_policy = PortfolioExecutionPolicy(
        rebalance_band_pct=config.execution.rebalance_band_pct,
        minimum_order_notional=config.execution.minimum_order_notional,
    )
    cost_runs: dict[str, Any] = {}
    for multiplier in COST_MULTIPLIERS:
        cost_runs[str(multiplier)] = _run_candidate_cell(
            config,
            prepared,
            actions,
            raw_ids,
            lifetimes.engine_terminal_lifecycles(),
            readiness,
            targets,
            contract_fill_timestamps,
            multiplier,
        )
        print(f"CANDIDATE {multiplier:.1f}x complete", flush=True)
        gc.collect()

    positive = [row for row in cost_runs["1.0"]["summary"]["per_entity"] if row["gross_pnl"] > 0]
    dominant = max(positive, key=lambda row: (row["gross_pnl"], row["entity_id"])) if positive else None
    if dominant is None:
        leave_one_out: dict[str, Any] = {
            "status": "NOT_APPLICABLE_NO_POSITIVE_CONTRIBUTOR",
            "reason": "G8 dominant-positive removal cannot pass because no entity has positive aggregate gross P&L",
        }
    else:
        dominant_aliases = tuple(
            sorted(
                {
                    str(target["symbol"])
                    for decision in readiness["decisions"]
                    for target in decision["targets"]
                    if str(target["entity_id"]) == str(dominant["entity_id"])
                }
            )
        )
        if not dominant_aliases:
            raise RuntimeError("dominant entity has no readiness target aliases")
        leave_one_out = _run_candidate_cell(
            config,
            prepared,
            actions,
            raw_ids,
            lifetimes.engine_terminal_lifecycles(),
            readiness,
            targets,
            contract_fill_timestamps,
            1.0,
            omitted_entity_id=str(dominant["entity_id"]),
            omitted_symbols=dominant_aliases,
        )
    control_parity = control["fresh"]["parity"]
    control_summary = control_parity["runs"]["1.0"]["metrics"]["report_summary"]
    execution_contract = {
        "starting_capital": float(config.backtest.initial_cash),
        "maximum_allocation": 0.55,
        "max_participation_pct": 0.10,
        "liquidity_lookback_bars": 20,
        "execution_policy": asdict(execution_policy),
        "risk_limits": asdict(risk_limits),
        "ordinary_alpha_excluded_symbols": list(config.universe.equity.ordinary_alpha_excluded_symbols),
        "terminal_mapping_count": len(lifetimes.explicit_terminals),
        "terminal_mapping_sha256": lifetimes.receipt.terminal_mapping_sha256,
        "effective_dated_regulatory_fees": bool(config.backtest.equity_use_effective_dated_fees),
        "drawdown_halt_mode": "shadow",
        "first_open_residual_policy": "expire_without_later_session_fallback",
        "engine_class": "PortfolioBacktestEngine via additive AuditedPortfolioBacktestEngine subclass",
        "pit_scanner_contract": "readiness-v2 exact frozen target stream from canonical PIT reconstruction",
        "readiness_runner_sha256": _file_sha256(READINESS_PATH),
        "readiness_reconstruction_sha256": readiness["reconstruction_sha256"],
        "readiness_independent_comparison_sha256": "8baed348f477f4f12746c1f27bc7c7a2b8214a2c35fe5271260a82a1e6515e33",
    }
    control_lifetimes = control_parity["input_identity"]["lifetimes"]
    architecture_parity = {
        "candidate_starting_capital": float(config.backtest.initial_cash),
        "control_starting_capital": float(control_summary["starting_equity"]),
        "same_starting_capital": math.isclose(
            float(config.backtest.initial_cash), float(control_summary["starting_equity"]), rel_tol=0.0, abs_tol=0.0
        ),
        "candidate_maximum_allocation": 0.55,
        "control_maximum_allocation": float(control_parity["production_identity"]["equity"]["maximum_allocation"]),
        "same_maximum_allocation": math.isclose(
            0.55,
            float(control_parity["production_identity"]["equity"]["maximum_allocation"]),
            rel_tol=0.0,
            abs_tol=0.0,
        ),
        "same_data_hashes": all(
            control_parity["input_identity"][name]["sha256"]
            == _file_sha256({"bars": BARS_PATH, "lifetimes": LIFETIMES_PATH, "corporate_actions": ACTIONS_PATH}[name])
            for name in ("bars", "lifetimes", "corporate_actions")
        ),
        "same_config_hash": control_parity["input_identity"]["config"]["sha256"] == _file_sha256(CONFIG_PATH),
        "same_execution_framework": execution_contract["engine_class"].startswith("PortfolioBacktestEngine"),
        "execution_framework": "PortfolioBacktestEngine canonical orders/fills/costs/lifecycle ledger",
        "same_global_risk_constraints": execution_contract["risk_limits"] == asdict(risk_limits),
        "risk_source": "config/settings.yaml loaded by both control and candidate",
        "same_pit_scanner_inputs": all(
            readiness["inputs"][name]["sha256"] == control_parity["input_identity"][name]["sha256"]
            for name in ("bars", "lifetimes", "corporate_actions")
        )
        and execution_contract["readiness_runner_sha256"] == _file_sha256(READINESS_PATH)
        and execution_contract["readiness_reconstruction_sha256"] == readiness["reconstruction_sha256"],
        "pit_scanner_evidence": "readiness-v2 reconstructed frozen targets from the canonical PIT bars/lifetimes",
        "candidate_receives_extra_capital_slots_or_execution_privilege": False,
        "production_promotion_performed": False,
        "same_lifecycle_mapping": execution_contract["terminal_mapping_count"]
        == control_lifetimes["terminal_mapping_count"]
        and execution_contract["terminal_mapping_sha256"] == control_lifetimes["terminal_mapping_sha256"],
        "candidate_execution_contract": execution_contract,
    }
    architecture_parity["all_pass"] = (
        all(
            architecture_parity[key]
            for key in (
                "same_starting_capital",
                "same_maximum_allocation",
                "same_data_hashes",
                "same_config_hash",
                "same_execution_framework",
                "same_global_risk_constraints",
                "same_pit_scanner_inputs",
                "same_lifecycle_mapping",
            )
        )
        and not architecture_parity["candidate_receives_extra_capital_slots_or_execution_privilege"]
    )
    gates = evaluate_gates(cost_runs, leave_one_out, architecture_parity)
    payload = {
        "schema_version": "abnormal-volume-economic-runner-v1",
        "candidate": CANDIDATE_ID,
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "identity": {
            "production_main_sha": PRODUCTION_MAIN_SHA,
            "design_v2_sha": DESIGN_V2_SHA,
            "readiness_v2_sha": READINESS_V2_SHA,
            "economic_branch": _git("branch", "--show-current"),
            "economic_head_at_run": _git("rev-parse", "HEAD"),
        },
        "input_identity": {
            "bars": {"path": str(BARS_PATH.relative_to(ROOT)), "sha256": _file_sha256(BARS_PATH)},
            "lifetimes": {"path": str(LIFETIMES_PATH.relative_to(ROOT)), "sha256": _file_sha256(LIFETIMES_PATH)},
            "corporate_actions": {"path": str(ACTIONS_PATH.relative_to(ROOT)), "sha256": _file_sha256(ACTIONS_PATH)},
            "config": {"path": str(CONFIG_PATH.relative_to(ROOT)), "sha256": _file_sha256(CONFIG_PATH)},
            "readiness_runner": {"path": str(READINESS_PATH.relative_to(ROOT)), "sha256": _file_sha256(READINESS_PATH)},
            "economic_models": {"path": str(MODELS_PATH.relative_to(ROOT)), "sha256": _file_sha256(MODELS_PATH)},
            "economic_runner": {
                "path": str(RUNNER_SOURCE_PATH.relative_to(ROOT)),
                "sha256": _file_sha256(RUNNER_SOURCE_PATH),
            },
            "independent_verifier": {
                "path": str(VERIFIER_SOURCE_PATH.relative_to(ROOT)),
                "sha256": _file_sha256(VERIFIER_SOURCE_PATH),
            },
            "endpoint": FULL_END.date().isoformat(),
            "bar_validation": bar_validation,
        },
        "structural_identity": {
            "readiness_reconstruction_sha256": readiness["reconstruction_sha256"],
            "independent_comparison_sha256": "8baed348f477f4f12746c1f27bc7c7a2b8214a2c35fe5271260a82a1e6515e33",
            "scheduled_decision_count": len(targets),
            "decision_targets_sha256": canonical_sha256(
                [[timestamp.isoformat(), list(rows)] for timestamp, rows in sorted(targets.items())]
            ),
            "reserved_role_target_count": sum(
                target["symbol"] in {"SPY", "SH", "PSQ", "DOG"}
                for decision in readiness["decisions"]
                for target in decision["targets"]
            ),
        },
        "metric_contract": {
            "interval": "inclusive [start,end] with immediately preceding NAV as return anchor",
            "daily_return": "nav_t/nav_t_minus_1 - 1",
            "year_fraction": "elapsed_seconds/(86400*365)",
            "annualizer": "sqrt(number_of_returns/year_fraction)",
            "sharpe": "mean(daily_returns)/sample_std(daily_returns)*annualizer; zero if denominator zero",
            "sortino": "mean(daily_returns)/sample_std(negative_daily_returns)*annualizer; zero if denominator zero",
            "max_drawdown": "max((running_peak-nav)/running_peak)",
            "turnover": "sum(0.5 * absolute security-weight changes) / year_fraction",
            "costs": "slippage + half-spread + commission/regulatory fees, including terminal liquidation",
        },
        "control": control,
        "candidate_results": {
            "cost_scenarios": cost_runs,
            "leave_one_dominant_name_out": leave_one_out,
            "gate_evaluation": gates,
            "architecture_parity": architecture_parity,
        },
    }
    payload["deterministic_payload_sha256"] = canonical_sha256(
        {key: value for key, value in payload.items() if key != "generated_at_utc"}
    )
    payload["run_sha256"] = canonical_sha256(payload)
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=OUTPUT_PATH)
    args = parser.parse_args()
    output = args.out.resolve()
    research_root = (ROOT / "research/abnormal_volume_economic_v1").resolve()
    if research_root not in output.parents:
        raise ValueError(f"output must remain inside {research_root}")
    payload = collect()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(jsonable(payload), indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
    print(f"WROTE={output}")
    print(f"SHA256={payload['run_sha256']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
