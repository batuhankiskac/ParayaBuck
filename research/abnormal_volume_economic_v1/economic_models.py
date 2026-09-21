"""Frozen standalone economic-run helpers for abnormal-volume DESIGN_V2.

This module deliberately contains no signal discovery code.  Candidate
targets are consumed from the independently verified readiness-v2 artifact;
the production portfolio engine remains authoritative for orders, fills,
costs, dividends, terminal lifecycles, cash and NAV.
"""

from __future__ import annotations

import hashlib
import json
import math
import statistics
from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime
from enum import Enum
from typing import Any

from parayabuck.backtest.portfolio_engine import PortfolioBacktestEngine, PortfolioBacktestResult
from parayabuck.core.models import Market
from parayabuck.data.market_dataset import CorporateAction
from parayabuck.markets.symbols import canonical_symbol
from parayabuck.portfolio.base import PortfolioContext, PortfolioStrategy
from parayabuck.portfolio.models import TargetPortfolio, TargetWeight

CANDIDATE_ID = "AV49_D1_NORMALRET_M1_LONG / DESIGN_V2"
COST_MULTIPLIERS = (1.0, 2.0, 3.0)
RESERVED_ROLES = frozenset({"SPY", "SH", "PSQ", "DOG"})

DEVELOPMENT_START = datetime(2017, 1, 3, tzinfo=UTC)
DEVELOPMENT_END = datetime(2021, 12, 31, 23, 59, 59, 999999, tzinfo=UTC)
VALIDATION_START = datetime(2022, 1, 3, tzinfo=UTC)
FULL_END = datetime(2026, 7, 30, 23, 59, 59, 999999, tzinfo=UTC)


def jsonable(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Mapping):
        return {str(key): jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [jsonable(item) for item in value]
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("economic artifact cannot serialize a non-finite float")
        return value
    if value is None or isinstance(value, (str, int, bool)):
        return value
    raise TypeError(f"unsupported economic artifact value: {type(value).__name__}")


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        jsonable(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


class ExactDecisionPolicy:
    """Schedule only the readiness-approved month-end close timestamps."""

    frequency = "frozen-month-end-close-v2"

    def __init__(self, timestamps: Sequence[datetime]) -> None:
        self.timestamps = frozenset(timestamps)
        if not self.timestamps or any(timestamp.tzinfo is None for timestamp in self.timestamps):
            raise ValueError("exact decision policy requires aware timestamps")

    def is_period_start(self, previous: datetime | None, current: datetime) -> bool:
        del previous
        return current in self.timestamps


class AuditedPortfolioBacktestEngine(PortfolioBacktestEngine):
    """Canonical engine with additive audit evidence and first-open expiry.

    The production engine intentionally carries partial orders forward. The
    frozen candidate contract is narrower: a monthly rebalance may execute
    only at its explicitly contracted first tradable open. Residual quantity
    is expired after that single cycle and remains visible as unfilled
    notional in the canonical rebalance enrichment.
    """

    _pending: list[Any]

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        contract_fill_timestamps = kwargs.pop("contract_fill_timestamps", {})
        super().__init__(*args, **kwargs)
        self.contract_fill_timestamps: dict[datetime, datetime] = dict(contract_fill_timestamps)
        self.audit_snapshots: list[dict[str, Any]] = []
        self.audit_orders: list[dict[str, Any]] = []
        self.first_open_expirations: list[dict[str, Any]] = []

    def _apply_target(self, target: TargetPortfolio) -> None:
        super()._apply_target(target)
        contracted = self.contract_fill_timestamps.get(target.decision_timestamp)
        if contracted is None:
            raise ValueError(f"missing contracted fill timestamp for {target.decision_timestamp.isoformat()}")
        for order in self._pending:
            if order.decision_timestamp != target.decision_timestamp:
                continue
            self.audit_orders.append(
                {
                    "client_order_id": order.client_order_id,
                    "symbol": order.symbol,
                    "side": order.side.value,
                    "requested_qty": float(order.qty),
                    "decision_reference_price": float(self._last_price[order.symbol]),
                    "created_at": order.created_at.isoformat(),
                    "decision_timestamp": order.decision_timestamp.isoformat(),
                    "contract_fill_cycle_timestamp": contracted.isoformat(),
                }
            )

    def _fill_cycle(self, cycle: list[Any]) -> None:
        cycle_timestamps = {bar.timestamp for bar in cycle}
        if len(cycle_timestamps) != 1:
            raise ValueError("economic audit requires one canonical timestamp per fill cycle")
        cycle_timestamp = next(iter(cycle_timestamps))
        due = {
            order.client_order_id: order
            for order in self._pending
            if self.contract_fill_timestamps.get(order.decision_timestamp) is not None
            and self.contract_fill_timestamps[order.decision_timestamp] <= cycle_timestamp
        }
        super()._fill_cycle(cycle)
        residuals = {order.client_order_id: order for order in self._pending if order.client_order_id in due}
        if residuals:
            self._pending = [order for order in self._pending if order.client_order_id not in residuals]
        for client_order_id, original in sorted(due.items()):
            residual = residuals.get(client_order_id)
            if residual is None:
                continue
            contracted = self.contract_fill_timestamps[original.decision_timestamp]
            self.first_open_expirations.append(
                {
                    "client_order_id": client_order_id,
                    "symbol": original.symbol,
                    "side": original.side.value,
                    "decision_timestamp": original.decision_timestamp.isoformat(),
                    "contract_fill_cycle_timestamp": contracted.isoformat(),
                    "expiry_cycle_timestamp": cycle_timestamp.isoformat(),
                    "requested_qty": float(original.qty),
                    "expired_qty": float(residual.qty),
                    "filled_qty": float(original.qty - residual.qty),
                    "reason": "FIRST_OPEN_PARTIAL_EXPIRED"
                    if residual.qty < original.qty
                    else "FIRST_OPEN_UNFILLED_EXPIRED",
                }
            )

    def _audit_row(self, timestamp: datetime) -> dict[str, Any]:
        positions = []
        for symbol, position in sorted(self._positions.items()):
            current_price = float(self._last_price.get(symbol, position.avg_price))
            positions.append(
                {
                    "symbol": symbol,
                    "qty": float(position.qty),
                    "avg_price": float(position.avg_price),
                    "entry_fee": float(position.entry_fee),
                    "current_price": current_price,
                    "market_value": float(position.qty) * current_price,
                }
            )
        return {
            "timestamp": timestamp.isoformat(),
            "cash": float(self._cash),
            "positions": positions,
            "nav": float(self._equity()),
        }

    def _record_holdings_snapshot(self, timestamp: datetime) -> None:
        super()._record_holdings_snapshot(timestamp)
        self.audit_snapshots.append(self._audit_row(timestamp))

    def run(self, *args: Any, **kwargs: Any) -> PortfolioBacktestResult:
        result = super().run(*args, **kwargs)
        # Canonical final liquidation occurs after the last ordinary holdings
        # snapshot and updates that timestamp's NAV in place. Mirror that
        # replacement so the persisted ledger snapshot agrees with the curve.
        if self.audit_snapshots and result.curve_timestamps:
            self.audit_snapshots[-1] = self._audit_row(result.curve_timestamps[-1])
        return result


class FrozenTargetStrategy(PortfolioStrategy):
    """Research-only adapter from persisted readiness targets to the engine."""

    name = "abnormal_volume_design_v2_research_only"
    version = "2.0.0"
    display_name = CANDIDATE_ID
    market = Market.EQUITY
    timeframe = "1d"
    rebalance_policy_declared = "frozen-month-end-close-v2"

    def __init__(
        self,
        targets_by_timestamp: Mapping[datetime, Sequence[tuple[str, float]]],
        *,
        omitted_symbols: Sequence[str] = (),
    ) -> None:
        self._targets = {
            timestamp: tuple((canonical_symbol(symbol, Market.EQUITY), float(weight)) for symbol, weight in targets)
            for timestamp, targets in targets_by_timestamp.items()
        }
        self._omitted_symbols = frozenset(canonical_symbol(symbol, Market.EQUITY) for symbol in omitted_symbols)
        super().__init__({})

    @property
    def minimum_history_bars(self) -> int:
        # Signal history was independently reconstructed and persisted at G1.
        # One completed bar is still required so the engine can price targets.
        return 1

    def _validate_parameters(self) -> None:
        for timestamp, targets in self._targets.items():
            if timestamp.tzinfo is None:
                raise ValueError("frozen target timestamp must be timezone-aware")
            symbols = [symbol for symbol, _weight in targets]
            if len(symbols) != len(set(symbols)):
                raise ValueError(f"duplicate frozen target at {timestamp.isoformat()}")
            if len(targets) > 6:
                raise ValueError("frozen target exceeds six names")
            if any(symbol in RESERVED_ROLES for symbol in symbols):
                raise ValueError("reserved role present in frozen target")
            if any(weight <= 0 or weight > 0.10 + 1e-12 for _symbol, weight in targets):
                raise ValueError("frozen target violates per-name cap")
            if sum(weight for _symbol, weight in targets) > 0.55 + 1e-12:
                raise ValueError("frozen target violates allocation cap")

    def decide(self, context: PortfolioContext) -> TargetPortfolio:
        source = self._targets.get(context.decision_timestamp, ())
        selected = tuple((symbol, weight) for symbol, weight in source if symbol not in self._omitted_symbols)
        weights = tuple(
            TargetWeight(symbol, weight, "frozen_readiness_v2_target", rank=rank)
            for rank, (symbol, weight) in enumerate(selected, start=1)
        )
        return TargetPortfolio(
            strategy_name=self.name,
            strategy_version=self.version,
            decision_timestamp=context.decision_timestamp,
            data_timestamp=context.decision_timestamp,
            weights=weights,
            cash_weight=1.0 - sum(weight.weight for weight in weights),
            universe_version="abnormal-volume-readiness-v2",
            parameters=(("candidate", CANDIDATE_ID),),
            metadata={
                "target_source": "research/abnormal_volume_readiness_v2/RUNNER_OUTPUT.json",
                "omitted_symbols": tuple(sorted(self._omitted_symbols)),
            },
        )


def targets_from_readiness(
    payload: Mapping[str, Any],
    session_timestamps: Mapping[date, datetime] | None = None,
) -> dict[datetime, tuple[tuple[str, float], ...]]:
    if payload.get("candidate") != CANDIDATE_ID:
        raise ValueError("unexpected readiness candidate identity")
    targets: dict[datetime, tuple[tuple[str, float], ...]] = {}
    for decision in payload.get("decisions", []):
        contract_timestamp = datetime.fromisoformat(str(decision["decision_timestamp"]))
        decision_session = date.fromisoformat(str(decision["decision_session"]))
        timestamp = (
            session_timestamps.get(decision_session, contract_timestamp) if session_timestamps else contract_timestamp
        )
        rows = []
        for target in decision.get("targets", []):
            if not bool(target.get("fill_available")):
                continue
            rows.append((str(target["symbol"]), float(str(target["target_weight"]))))
        targets[timestamp] = tuple(rows)
    if not targets:
        raise ValueError("readiness payload contains no scheduled decisions")
    return targets


def _sample_std(values: Sequence[float]) -> float:
    return statistics.stdev(values) if len(values) >= 2 else 0.0


def _month_returns(points: Sequence[tuple[datetime, float]]) -> list[float]:
    month_ends: dict[tuple[int, int], float] = {}
    for timestamp, value in points:
        month_ends[(timestamp.year, timestamp.month)] = value
    values = list(month_ends.values())
    return [values[index] / values[index - 1] - 1.0 for index in range(1, len(values))]


def one_way_turnover(target_weights: Sequence[Sequence[Any]], pre_trade_weights: Sequence[Sequence[Any]]) -> float:
    """Repository-canonical one-way turnover: half absolute weight change."""

    target = {str(symbol): float(weight) for symbol, weight in target_weights}
    pre_trade = {str(symbol): float(weight) for symbol, weight in pre_trade_weights}
    symbols = set(target) | set(pre_trade)
    return 0.5 * sum(abs(target.get(symbol, 0.0) - pre_trade.get(symbol, 0.0)) for symbol in symbols)


def period_metrics(
    result: PortfolioBacktestResult,
    start: datetime,
    end: datetime,
    snapshots: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Metrics over [start,end], anchored by the immediately preceding NAV."""

    all_points = list(zip(result.curve_timestamps, result.equity_curve, strict=True))
    selected = [index for index, (timestamp, _value) in enumerate(all_points) if start <= timestamp <= end]
    if not selected:
        raise ValueError(f"no NAV points in {start.isoformat()} -> {end.isoformat()}")
    first = selected[0]
    indices = ([first - 1] if first > 0 else []) + selected
    points = [all_points[index] for index in indices]
    timestamps = [timestamp for timestamp, _value in points]
    values = [float(value) for _timestamp, value in points]
    returns = [values[index] / values[index - 1] - 1.0 for index in range(1, len(values)) if values[index - 1] > 0]
    duration_days = (timestamps[-1] - timestamps[0]).total_seconds() / 86_400
    years = duration_days / 365.0
    total_return = values[-1] / values[0] - 1.0 if values[0] > 0 else -1.0
    cagr = (values[-1] / values[0]) ** (1.0 / years) - 1.0 if years > 0 and values[-1] > 0 else 0.0
    std = _sample_std(returns)
    downside = _sample_std([value for value in returns if value < 0])
    annualizer = math.sqrt(len(returns) / years) if returns and years > 0 else 1.0
    peak = values[0]
    max_drawdown = 0.0
    for value in values:
        peak = max(peak, value)
        if peak > 0:
            max_drawdown = max(max_drawdown, (peak - value) / peak)
    fills = [fill for fill in result.fills if start <= fill.timestamp <= end]
    terminals = [
        event for event in result.terminal_events if start <= datetime.fromisoformat(str(event["timestamp"])) <= end
    ]
    rebalances = [record for record in result.rebalances if start <= record.decision_timestamp <= end]
    realized = [pnl for timestamp, _symbol, pnl in result.closed_position_events if start <= timestamp <= end]
    gross_profit = sum(value for value in realized if value > 0)
    gross_loss = abs(sum(value for value in realized if value < 0))
    slippage = sum(float(fill.slippage_cost) for fill in fills) + sum(
        float(event.get("slippage_cost", 0.0)) for event in terminals
    )
    spread = sum(float(fill.spread_cost) for fill in fills) + sum(
        float(event.get("spread_cost", 0.0)) for event in terminals
    )
    fees = sum(float(fill.fee) for fill in fills) + sum(float(event.get("fee", 0.0)) for event in terminals)
    average_nav = sum(values) / len(values)
    realized_notional = sum(float(fill.qty) * float(fill.reference_price) for fill in fills)
    years_for_turnover = max(years, 1 / 365.0)
    monthly = _month_returns(points)
    target_turnover = sum(one_way_turnover(record.target_weights, record.pre_trade_weights) for record in rebalances)
    realized_turnover = sum(float(record.realized_turnover) for record in rebalances)
    period_snapshots = [row for row in snapshots if start <= datetime.fromisoformat(str(row["timestamp"])) <= end]
    exposures = [
        sum(float(position["market_value"]) for position in row["positions"]) / float(row["nav"])
        if float(row["nav"]) > 0
        else 0.0
        for row in period_snapshots
    ]
    position_counts = [len(row["positions"]) for row in period_snapshots]
    realized_concentrations = [
        max((float(position["market_value"]) / float(row["nav"]) for position in row["positions"]), default=0.0)
        if float(row["nav"]) > 0
        else 0.0
        for row in period_snapshots
    ]
    return {
        "start_timestamp": timestamps[0].isoformat(),
        "end_timestamp": timestamps[-1].isoformat(),
        "curve_point_count": len(values),
        "net_total_return": total_return,
        "cagr": cagr,
        "annualized_volatility": std * annualizer if std > 0 else 0.0,
        "sharpe": (statistics.fmean(returns) / std * annualizer if returns and std > 0 else 0.0),
        "sortino": (statistics.fmean(returns) / downside * annualizer if returns and downside > 0 else 0.0),
        "max_drawdown": max_drawdown,
        "calmar": cagr / max_drawdown if max_drawdown > 0 else 0.0,
        "worst_month": min(monthly, default=0.0),
        "positive_month_ratio": sum(value > 0 for value in monthly) / len(monthly) if monthly else 0.0,
        "profit_factor": gross_profit / gross_loss if gross_loss > 0 else None,
        "gross_profit": gross_profit,
        "gross_loss": gross_loss,
        "annualized_target_turnover": target_turnover / years_for_turnover,
        "annualized_realized_turnover": realized_turnover / years_for_turnover,
        "realized_notional_turnover": realized_notional / average_nav / years_for_turnover if average_nav > 0 else 0.0,
        "transaction_costs": slippage + spread + fees,
        "slippage_cost": slippage,
        "spread_cost": spread,
        "fee_cost": fees,
        "cost_to_positive_gross_profit": (slippage + spread + fees) / gross_profit if gross_profit > 0 else None,
        "scheduled_decision_count": len(rebalances),
        "actual_decision_count": len(rebalances),
        "fill_count": len(fills),
        "terminal_event_count": len(terminals),
        "average_exposure": statistics.fmean(exposures) if exposures else 0.0,
        "median_exposure": statistics.median(exposures) if exposures else 0.0,
        "average_positions": statistics.fmean(position_counts) if position_counts else 0.0,
        "maximum_target_concentration": max(
            (max((weight for _symbol, weight in record.target_weights), default=0.0) for record in rebalances),
            default=0.0,
        ),
        "maximum_realized_concentration": max(realized_concentrations, default=0.0),
    }


def result_rows(result: PortfolioBacktestResult) -> dict[str, Any]:
    decisions = [
        {
            "decision_timestamp": row.decision_timestamp.isoformat(),
            "target_weights": [[symbol, float(weight)] for symbol, weight in row.target_weights],
            "target_cash_weight": float(row.target_cash_weight),
            "pre_trade_weights": [[symbol, float(weight)] for symbol, weight in row.pre_trade_weights],
            "signed_trade_weights": [[symbol, float(weight)] for symbol, weight in row.signed_trade_weights],
            "post_fill_actual_weights": [[symbol, float(weight)] for symbol, weight in row.post_fill_actual_weights],
            "executed_weights": [[symbol, float(weight)] for symbol, weight in row.executed_weights],
            "expected_turnover": float(row.expected_turnover),
            "one_way_turnover": one_way_turnover(row.target_weights, row.pre_trade_weights),
            "realized_turnover": float(row.realized_turnover),
            "cost": float(row.cost),
            "unfilled_notional": float(row.unfilled_notional),
            "rejected_notional": float(row.rejected_notional),
            "equity_at_decision": float(row.equity_at_decision),
            "risk_overlay_halted": bool(row.risk_overlay_halted),
            "risk_overlay_reasons": list(row.risk_overlay_reasons),
        }
        for row in result.rebalances
    ]
    fills = [
        {
            "timestamp": fill.timestamp.isoformat(),
            "decision_timestamp": fill.decision_timestamp.isoformat(),
            "client_order_id": fill.client_order_id,
            "symbol": fill.symbol,
            "side": fill.side.value,
            "qty": float(fill.qty),
            "reference_price": float(fill.reference_price),
            "fill_price": float(fill.fill_price),
            "slippage_cost": float(fill.slippage_cost),
            "spread_cost": float(fill.spread_cost),
            "fee": float(fill.fee),
        }
        for fill in result.fills
    ]
    terminal_events = [jsonable(dict(event)) for event in result.terminal_events]
    nav_curve = [
        {"timestamp": timestamp.isoformat(), "nav": float(nav)}
        for timestamp, nav in zip(result.curve_timestamps, result.equity_curve, strict=True)
    ]
    return {
        "decisions": decisions,
        "fills": fills,
        "terminal_events": terminal_events,
        "realized_pnl_events": [
            {"timestamp": timestamp.isoformat(), "symbol": symbol, "pnl": float(pnl)}
            for timestamp, symbol, pnl in result.realized_pnl_events
        ],
        "closed_position_events": [
            {"timestamp": timestamp.isoformat(), "symbol": symbol, "pnl": float(pnl)}
            for timestamp, symbol, pnl in result.closed_position_events
        ],
        "nav_curve": nav_curve,
        "hashes": {
            "decisions_sha256": canonical_sha256(decisions),
            "fills_sha256": canonical_sha256(fills),
            "terminal_events_sha256": canonical_sha256(terminal_events),
            "nav_curve_sha256": canonical_sha256(nav_curve),
        },
    }


def dividend_events(
    result: PortfolioBacktestResult,
    actions: Sequence[CorporateAction],
    raw_ids: Mapping[tuple[str, str, float], str],
) -> list[dict[str, Any]]:
    """Replay quantities to expose the engine's otherwise aggregate dividends."""

    events: list[tuple[datetime, int, str, Any]] = []
    for fill in result.fills:
        events.append((fill.timestamp, 1, "fill", fill))
    for terminal in result.terminal_events:
        events.append((datetime.fromisoformat(str(terminal["timestamp"])), 2, "terminal", terminal))
    for action in actions:
        events.append((action.timestamp, 0, "dividend", action))
    quantities: dict[str, float] = defaultdict(float)
    dividends: list[dict[str, Any]] = []
    for timestamp, _priority, kind, payload in sorted(events, key=lambda item: (item[0], item[1])):
        if kind == "fill":
            symbol = canonical_symbol(payload.symbol, Market.EQUITY)
            quantities[symbol] += payload.qty if payload.side.value == "buy" else -payload.qty
            quantities[symbol] = max(0.0, quantities[symbol])
        elif kind == "terminal":
            symbol = canonical_symbol(str(payload["symbol"]), Market.EQUITY)
            quantities[symbol] = max(0.0, quantities[symbol] - float(payload["quantity"]))
        else:
            symbol = canonical_symbol(payload.symbol, Market.EQUITY)
            qty = quantities.get(symbol, 0.0)
            if qty <= 0:
                continue
            cash = qty * float(payload.value)
            key = (symbol, payload.timestamp.date().isoformat(), float(payload.value))
            dividends.append(
                {
                    "timestamp": payload.timestamp.isoformat(),
                    "ex_date": payload.timestamp.date().isoformat(),
                    "symbol": symbol,
                    "qty": qty,
                    "rate": float(payload.value),
                    "cash_credit": cash,
                    "raw_id": raw_ids.get(key),
                }
            )
    total = sum(float(row["cash_credit"]) for row in dividends)
    if not math.isclose(total, result.dividend_total, rel_tol=1e-10, abs_tol=1e-8):
        raise ValueError(f"dividend replay mismatch: events={total} engine={result.dividend_total}")
    return dividends


def evaluate_gates(
    cost_runs: Mapping[str, Mapping[str, Any]],
    leave_one_out: Mapping[str, Any],
    architecture_parity: Mapping[str, Any],
) -> dict[str, Any]:
    one = cost_runs["1.0"]
    two = cost_runs["2.0"]
    three = cost_runs["3.0"]
    full = one["metrics"]["full"]
    development = one["metrics"]["development"]
    validation = one["metrics"]["validation"]
    decisions = one["decisions"]
    target_counts = [len(row["target_weights"]) for row in decisions]
    contributions = one["summary"]["per_entity"]
    absolute_gross = sum(abs(float(row["gross_pnl"])) for row in contributions)
    max_share = max((abs(float(row["gross_pnl"])) / absolute_gross for row in contributions), default=0.0)
    target_notional = sum(float(row["full_target_notional"]) for row in decisions)
    unfilled = sum(float(row["unfilled_target_notional"]) for row in decisions)
    filled_ratio = 1.0 - unfilled / target_notional if target_notional > 0 else 0.0
    loo_metrics = leave_one_out.get("metrics", {})
    loo_full = loo_metrics.get("full", {})
    loo_validation = loo_metrics.get("validation", {})
    gates = {
        "G2": full["net_total_return"] > 0 and full["cagr"] > 0,
        "G3": development["net_total_return"] > 0 and development["cagr"] > 0,
        "G4": validation["net_total_return"] > 0
        and validation["cagr"] > 0
        and validation["sharpe"] > 0
        and validation["max_drawdown"] <= 0.30,
        "G5": full["sharpe"] >= 0.35
        and full["sortino"] >= 0.45
        and full["calmar"] >= 0.20
        and full["max_drawdown"] <= 0.30,
        "G6": two["metrics"]["full"]["net_total_return"] > 0
        and two["metrics"]["full"]["cagr"] > 0
        and two["metrics"]["validation"]["net_total_return"] > 0
        and two["metrics"]["validation"]["cagr"] > 0
        and three["metrics"]["full"]["net_total_return"] > 0
        and three["metrics"]["full"]["cagr"] > 0
        and three["metrics"]["full"]["sharpe"] > 0,
        "G7": full["annualized_target_turnover"] <= 12.0
        and filled_ratio >= 0.95
        and (1.0 - filled_ratio) <= 0.05
        and one["summary"]["positive_gross_profit"] > 0
        and one["summary"]["total_cost"] <= 0.35 * one["summary"]["positive_gross_profit"],
        "G8": statistics.median(target_counts) >= 5
        and (sum(count >= 4 for count in target_counts) / len(target_counts) if target_counts else 0.0) >= 0.90
        and max(
            (max((weight for _symbol, weight in row["target_weights"]), default=0.0) for row in decisions), default=0.0
        )
        <= 0.10 + 1e-12
        and max_share <= 0.35
        and float(loo_full.get("net_total_return", -1.0)) > 0
        and float(loo_full.get("cagr", -1.0)) > 0
        and float(loo_validation.get("net_total_return", -1.0)) > 0
        and float(loo_validation.get("cagr", -1.0)) > 0,
        "G9": one["summary"]["maximum_target_exposure"] <= 0.55 + 1e-12
        and one["summary"]["maximum_realized_exposure"] <= 0.60 + 1e-12
        and one["summary"]["minimum_realized_cash"] >= 0.40 - 1e-12
        and one["summary"]["maximum_actual_positions"] <= 6
        and bool(architecture_parity.get("all_pass")),
    }
    return {
        "gates": {gate: {"passed": passed} for gate, passed in gates.items()},
        "all_g2_g9_pass": all(gates.values()),
        "implementation_diagnostics": {
            "filled_target_notional_ratio": filled_ratio,
            "unfilled_partial_target_notional_ratio": 1.0 - filled_ratio,
            "largest_absolute_contribution_share": max_share,
            "median_target_holdings": statistics.median(target_counts) if target_counts else 0.0,
            "decisions_with_at_least_four_ratio": (
                sum(count >= 4 for count in target_counts) / len(target_counts) if target_counts else 0.0
            ),
        },
    }


__all__ = [
    "CANDIDATE_ID",
    "COST_MULTIPLIERS",
    "AuditedPortfolioBacktestEngine",
    "DEVELOPMENT_END",
    "DEVELOPMENT_START",
    "ExactDecisionPolicy",
    "FULL_END",
    "FrozenTargetStrategy",
    "VALIDATION_START",
    "canonical_sha256",
    "dividend_events",
    "evaluate_gates",
    "jsonable",
    "period_metrics",
    "result_rows",
    "targets_from_readiness",
]
