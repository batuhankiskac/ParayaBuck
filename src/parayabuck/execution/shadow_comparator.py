"""Offline, read-only execution-schedule comparator.

This module deliberately mirrors the current portfolio backtest's *dynamic*
residual capacity rule without importing or changing its hot path.  It is an
observation tool: it never opens a writable database connection, downloads
bars, or changes a cost assumption.
"""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
import statistics
from collections.abc import Iterable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from parayabuck.backtest.portfolio_engine import OrderSide, PortfolioBacktestEngine, equity_regulatory_fee
from parayabuck.core.models import Bar, Market

_EPSILON = 1e-9
_BPS = 10_000.0


@dataclass(frozen=True)
class ShadowOrder:
    client_order_id: str
    decision_id: str
    decision_timestamp: datetime
    symbol: str
    market: Market
    side: OrderSide
    quantity: float
    reference_price: float | None
    status: str
    origin: str = "scheduled"
    execution_role: str = "immediate"
    superseded_at: datetime | None = None
    capacity_timestamp: datetime | None = None


@dataclass(frozen=True)
class ScheduledSlice:
    timestamp: datetime
    quantity: float
    open_price: float
    known_average_volume: float
    capacity_timestamp: datetime


def _scheduling_bars(order: ShadowOrder, bars: Iterable[Bar]) -> tuple[Bar, ...]:
    """Return the one backtest-comparable bar series for ``order``.

    A runtime order has no independent timeframe column.  Consequently a
    local comparison may proceed only when its symbol/market input resolves
    to exactly one timeframe.  Silently interleaving 1d and intraday bars
    would invent a capacity series that the engine never saw.
    """
    canonical = PortfolioBacktestEngine._deduplicate(list(bars))
    relevant = tuple(bar for bar in canonical if bar.symbol == order.symbol and bar.market == order.market)
    timeframes = {bar.timeframe for bar in relevant}
    if len(timeframes) > 1:
        raise ValueError("noncomparable_mixed_timeframes")
    return tuple(sorted(relevant, key=lambda bar: bar.timestamp))


def schedule_zero_cost_fills(
    order: ShadowOrder,
    bars: Iterable[Bar],
    *,
    liquidity_lookback_bars: int = 20,
    max_participation_pct: float = 0.10,
) -> tuple[ScheduledSlice, ...]:
    """Mirror the engine's current zero-cost schedule for one order.

    The first slice uses positive volume known at ``decision_timestamp``.  On
    every partial slice the engine recreates the residual order with
    ``created_at`` set to that fill timestamp; later slices therefore refresh
    their 20-positive-bar average through the previous fill, while never using
    the current fill bar's completed volume.  ``superseded_at`` models the
    next decision clearing pending orders before it creates a replacement.
    """
    if liquidity_lookback_bars <= 0 or not 0 < max_participation_pct <= 1:
        raise ValueError("invalid scheduling parameters")
    # `_deduplicate` deliberately raises on conflicting same-timeframe rows,
    # which is the exact engine input contract.  A mixed timeframe input is
    # separately rejected by `_scheduling_bars` because orders do not retain
    # enough metadata to select one safely.
    all_bars = _scheduling_bars(order, bars)
    remaining = order.quantity
    capacity_timestamp = order.capacity_timestamp or order.decision_timestamp
    slices: list[ScheduledSlice] = []
    for bar in all_bars:
        if remaining <= _EPSILON:
            break
        # The engine fills the timestamp's cycle *before* `_apply_target`
        # clears stale pending orders for a new decision at that timestamp.
        # A later decision therefore stops only later bars, not this one.
        if order.superseded_at is not None and bar.timestamp > order.superseded_at:
            break
        if bar.timestamp <= capacity_timestamp:
            continue
        known = [item.volume for item in all_bars if item.timestamp <= capacity_timestamp and item.volume > 0][
            -liquidity_lookback_bars:
        ]
        if not known:
            continue
        average = sum(known) / len(known)
        quantity = min(remaining, average * max_participation_pct)
        if quantity <= _EPSILON:
            continue
        slices.append(
            ScheduledSlice(
                timestamp=bar.timestamp,
                quantity=quantity,
                open_price=bar.open,
                known_average_volume=average,
                capacity_timestamp=capacity_timestamp,
            )
        )
        remaining -= quantity
        # Exact current engine residual behavior; see PortfolioBacktestEngine
        # _fill_side, which rebuilds the pending order at the fill timestamp.
        capacity_timestamp = bar.timestamp
    return tuple(slices)


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def dataset_metadata(path: str | Path, bars: Iterable[Bar], *, feed_identity: str | None = None) -> dict[str, object]:
    rows = tuple(bars)
    timestamps = sorted(bar.timestamp for bar in rows)
    return {
        "path": str(Path(path).resolve()),
        "sha256": sha256_file(path),
        "feed_identity": feed_identity or "unknown",
        "coverage_start": timestamps[0].isoformat() if timestamps else None,
        "coverage_end": timestamps[-1].isoformat() if timestamps else None,
        "bar_count": len(rows),
        "markets": sorted({bar.market.value for bar in rows}),
    }


def _open_readonly(path: str | Path) -> sqlite3.Connection:
    source = Path(path).resolve()
    if not source.is_file():
        raise ValueError("runtime database path is not a regular file")
    connection = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    return connection


def _table_exists(connection: sqlite3.Connection, table: str) -> bool:
    return (
        connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is not None
    )


def _columns(connection: sqlite3.Connection, table: str) -> set[str]:
    if not _table_exists(connection, table):
        return set()
    return {str(row[1]) for row in connection.execute(f'PRAGMA table_info("{table}")')}


def _parse_timestamp(value: object) -> datetime | None:
    try:
        result = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return result if result.tzinfo is not None else result.replace(tzinfo=UTC)


def _float(value: object) -> float | None:
    try:
        result = float(str(value))
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _json_object(value: object) -> dict[str, object]:
    try:
        result = json.loads(str(value))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return result if isinstance(result, dict) else {}


def _metadata_value(target_json: object, key: str, default: str) -> str:
    payload = _json_object(target_json)
    metadata = payload.get("metadata")
    if isinstance(metadata, dict) and metadata.get(key) is not None:
        return str(metadata[key])
    return default


def _load_orders(connection: sqlite3.Connection, *, as_of: datetime | None = None) -> tuple[ShadowOrder, ...]:
    required = {"portfolio_runtime_sessions", "portfolio_decisions", "portfolio_orders"}
    if not all(_table_exists(connection, table) for table in required):
        return ()
    decision_columns = _columns(connection, "portfolio_decisions")
    order_columns = _columns(connection, "portfolio_orders")
    origin_sql = "d.origin" if "origin" in decision_columns else "NULL"
    role_sql = "o.execution_role" if "execution_role" in order_columns else "NULL"
    rows = connection.execute(
        f"""
        SELECT o.client_order_id, o.decision_id, d.decision_timestamp, o.symbol,
               o.market, o.side, o.quantity, o.reference_price, o.status,
               d.target_json, {origin_sql} AS origin, {role_sql} AS execution_role
        FROM portfolio_orders o
        JOIN portfolio_decisions d ON d.decision_id = o.decision_id
        JOIN portfolio_runtime_sessions s ON s.session_id = d.session_id
        WHERE lower(s.mode) = 'paper'
        ORDER BY d.decision_timestamp, o.client_order_id
        """
    ).fetchall()
    result: list[ShadowOrder] = []
    for row in rows:
        timestamp = _parse_timestamp(row[2])
        quantity = _float(row[6])
        try:
            market = Market(str(row[4]).lower())
            side = OrderSide(str(row[5]).lower())
        except ValueError:
            continue
        if timestamp is None or quantity is None or quantity <= 0:
            continue
        origin = str(row[10]) if row[10] is not None else _metadata_value(row[9], "decision_origin", "scheduled")
        role = str(row[11]) if row[11] is not None else _metadata_value(row[9], "execution_role", "immediate")
        if as_of is not None and timestamp > as_of:
            continue
        result.append(
            ShadowOrder(
                client_order_id=str(row[0]),
                decision_id=str(row[1]),
                decision_timestamp=timestamp,
                symbol=str(row[3]),
                market=market,
                side=side,
                quantity=quantity,
                reference_price=_float(row[7]),
                status=str(row[8]),
                origin=origin,
                execution_role=role,
            )
        )
    # The engine clears all pending orders when a later decision is applied.
    # That later decision may deliberately create no orders, so it cannot be
    # inferred from the orders query above.
    decision_rows = connection.execute(
        """
        SELECT d.market, d.decision_timestamp
        FROM portfolio_decisions d
        JOIN portfolio_runtime_sessions s ON s.session_id = d.session_id
        WHERE lower(s.mode) = 'paper'
        """
    ).fetchall()
    by_market: dict[Market, set[datetime]] = {}
    for market_value, timestamp_value in decision_rows:
        timestamp = _parse_timestamp(timestamp_value)
        try:
            market = Market(str(market_value).lower())
        except ValueError:
            continue
        if timestamp is not None and (as_of is None or timestamp <= as_of):
            by_market.setdefault(market, set()).add(timestamp)
    superseded: dict[str, datetime] = {}
    for order in result:
        later = next(
            (item for item in sorted(by_market.get(order.market, set())) if item > order.decision_timestamp),
            None,
        )
        if later is not None:
            superseded[order.client_order_id] = later
    return tuple(replace(order, superseded_at=superseded.get(order.client_order_id)) for order in result)


def _actual_from_events(
    connection: sqlite3.Connection, client_order_id: str, *, as_of: datetime | None = None
) -> dict[str, object] | None:
    if not _table_exists(connection, "portfolio_execution_events"):
        return None
    cols = _columns(connection, "portfolio_execution_events")
    required = {"client_order_id", "raw_cumulative_filled_quantity", "raw_cumulative_average_fill_price"}
    if not required.issubset(cols):
        return None
    event_time = "broker_event_at" if "broker_event_at" in cols else "received_at"
    fee_sql = "raw_fee" if "raw_fee" in cols else "NULL"
    observed_sql = "fee_observed" if "fee_observed" in cols else "0"
    type_sql = "event_type" if "event_type" in cols else "''"
    quality_sql = "measurement_quality" if "measurement_quality" in cols else "NULL"
    rows = connection.execute(
        f"""SELECT {event_time} AS event_at,
                   raw_cumulative_filled_quantity, raw_cumulative_average_fill_price,
                   {fee_sql} AS raw_fee, {observed_sql} AS fee_observed, {type_sql} AS event_type,
                   {quality_sql} AS measurement_quality
            FROM portfolio_execution_events
            WHERE client_order_id = ?
            ORDER BY {event_time}, id""",
        (client_order_id,),
    ).fetchall()
    valid_fill_qualities = {"first_fill", "valid_incremental"}
    benign_nonfill_qualities = {"status_only", "duplicate_or_zero_delta"}
    if as_of is not None:
        rows = [row for row in rows if (event_at := _parse_timestamp(row[0])) is not None and event_at <= as_of]
    observed_qualities = [str(row[6]) if row[6] is not None else "missing_measurement_quality" for row in rows]
    blocking = sorted(
        {quality for quality in observed_qualities if quality not in valid_fill_qualities | benign_nonfill_qualities}
    )
    best_qty = 0.0
    best_price: float | None = None
    for row, quality in zip(rows, observed_qualities, strict=True):
        quantity, price = _float(row[1]), _float(row[2])
        if (
            quality in valid_fill_qualities
            and quantity is not None
            and quantity >= best_qty
            and price is not None
            and price > 0
        ):
            best_qty, best_price = quantity, price
    exclusion_reason = f"raw_execution_quality_excluded:{','.join(blocking)}" if blocking else None
    if best_price is None:
        exclusion_reason = exclusion_reason or "raw_execution_quality_excluded:no_valid_cumulative_vwap"
    price_comparison_eligible = best_price is not None and not blocking
    return {
        "quantity": best_qty,
        "vwap": best_price if price_comparison_eligible else None,
        # ``raw_fee`` is event-local and the Phase-A uniqueness constraint is
        # source-scoped.  It does not establish cross-source precedence, so a
        # comparator must not turn it into a summed total-cost observation.
        "fee_known": False,
        "fee": None,
        "quality": "raw_execution_events_fee_unknown" if exclusion_reason is None else exclusion_reason,
        "quality_states": observed_qualities,
        "final_gross_quantity_eligible": price_comparison_eligible,
        "final_vwap_eligible": price_comparison_eligible,
        "price_comparison_eligible": price_comparison_eligible,
        # Event-local fee observations never establish cross-source
        # precedence, so total-cost comparison remains unavailable.
        "total_cost_comparison_eligible": False,
    }


def _actual_from_legacy_fills(connection: sqlite3.Connection, client_order_id: str) -> dict[str, object]:
    if not _table_exists(connection, "portfolio_fills"):
        return {"quantity": 0.0, "vwap": None, "fee_known": False, "fee": None, "quality": "no_execution_events"}
    rows = connection.execute(
        "SELECT quantity, price FROM portfolio_fills WHERE client_order_id = ? ORDER BY occurred_at, activity_id",
        (client_order_id,),
    ).fetchall()
    quantity = sum(value for row in rows if (value := _float(row[0])) is not None and value > 0)
    notional = sum(
        qty * price
        for row in rows
        if (qty := _float(row[0])) is not None and qty > 0 and (price := _float(row[1])) is not None and price > 0
    )
    return {
        "quantity": quantity,
        "vwap": notional / quantity if quantity > 0 else None,
        # Legacy fills lack fee-observation completeness and crypto quantities
        # can be net ownership. Never turn a NULL/zero-looking legacy fee into
        # a total-cost claim.
        "fee_known": False,
        "fee": None,
        "quality": "legacy_fills_fee_unknown",
        "quality_states": ["legacy_fill_rows_without_raw_event_quality"],
        "final_gross_quantity_eligible": True,
        "final_vwap_eligible": True,
        "price_comparison_eligible": True,
        "total_cost_comparison_eligible": False,
    }


def _modeled_fee(order: ShadowOrder, slice_: ScheduledSlice, config: Any) -> float:
    backtest = getattr(config, "backtest")
    adverse = float(backtest.slippage_bps) + float(backtest.spread_bps) / 2.0
    direction = 1.0 if order.side == OrderSide.BUY else -1.0
    price = slice_.open_price * (1.0 + direction * adverse / _BPS)
    fee = float(backtest.commission)
    if order.market == Market.CRYPTO:
        return fee + slice_.quantity * price * float(backtest.crypto_taker_fee_bps) / _BPS
    if bool(backtest.equity_use_effective_dated_fees):
        from parayabuck.backtest.regulatory_fees import rates_for_trade_date

        rates = rates_for_trade_date(slice_.timestamp.date())
        return fee + equity_regulatory_fee(
            order.side,
            slice_.quantity,
            price,
            taf_per_share=rates.taf_per_share,
            taf_cap=rates.taf_cap,
            cat_per_share=rates.cat_per_share,
            sec_fee_rate=rates.sec_fee_rate,
        )
    return fee + equity_regulatory_fee(
        order.side,
        slice_.quantity,
        price,
        taf_per_share=float(backtest.equity_taf_per_share),
        taf_cap=float(backtest.equity_taf_cap),
        cat_per_share=float(backtest.equity_cat_per_share),
        sec_fee_rate=float(backtest.equity_sec_fee_rate),
    )


def _matched_schedule(slices: tuple[ScheduledSlice, ...], quantity: float) -> tuple[tuple[ScheduledSlice, float], ...]:
    remaining = quantity
    matched: list[tuple[ScheduledSlice, float]] = []
    for slice_ in slices:
        if remaining <= _EPSILON:
            break
        amount = min(remaining, slice_.quantity)
        matched.append((slice_, amount))
        remaining -= amount
    return tuple(matched)


def _summary(values: list[float]) -> dict[str, float | None]:
    if not values:
        return {"mean": None, "median": None, "p75": None, "p95": None}
    ordered = sorted(values)

    def percentile(pct: float) -> float:
        rank = (len(ordered) - 1) * pct
        low, high = int(rank), min(int(rank) + 1, len(ordered) - 1)
        return ordered[low] * (1 - (rank - low)) + ordered[high] * (rank - low)

    return {
        "mean": statistics.fmean(values),
        "median": statistics.median(values),
        "p75": percentile(0.75),
        "p95": percentile(0.95),
    }


def _order_record(order: ShadowOrder, actual: dict[str, object], bars: Iterable[Bar], config: Any) -> dict[str, object]:
    schedule_reason: str | None = None
    try:
        slices = schedule_zero_cost_fills(order, bars)
    except ValueError as exc:
        if str(exc) != "noncomparable_mixed_timeframes":
            raise
        slices = ()
        schedule_reason = str(exc)
    actual_qty = _float(actual.get("quantity")) or 0.0
    price_comparison_eligible = bool(actual.get("price_comparison_eligible", True))
    comparison_qty = actual_qty if price_comparison_eligible else 0.0
    matched = _matched_schedule(slices, comparison_qty)
    matched_qty = sum(quantity for _slice, quantity in matched)
    zero_notional = sum(slice_.open_price * quantity for slice_, quantity in matched)
    zero_vwap = zero_notional / matched_qty if matched_qty > 0 else None
    actual_vwap = _float(actual.get("vwap"))
    direction = 1.0 if order.side == OrderSide.BUY else -1.0
    price_cost = (
        direction * matched_qty * (actual_vwap - zero_vwap)
        if actual_vwap is not None and zero_vwap is not None and matched_qty > 0
        else None
    )
    price_bps = price_cost / (matched_qty * zero_vwap) * _BPS if price_cost is not None and zero_vwap else None
    modeled_adverse_bps = float(config.backtest.slippage_bps) + float(config.backtest.spread_bps) / 2.0
    modeled_price_cost = sum(quantity * slice_.open_price * modeled_adverse_bps / _BPS for slice_, quantity in matched)
    modeled_fee = sum(
        _modeled_fee(
            order,
            ScheduledSlice(
                slice_.timestamp, quantity, slice_.open_price, slice_.known_average_volume, slice_.capacity_timestamp
            ),
            config,
        )
        for slice_, quantity in matched
    )
    actual_fee = _float(actual.get("fee")) if bool(actual.get("fee_known")) else None
    actual_total = price_cost + actual_fee if price_cost is not None and actual_fee is not None else None
    modeled_total = modeled_price_cost + modeled_fee
    close_bps = None
    if actual_vwap is not None and order.reference_price and order.reference_price > 0:
        close_bps = direction * (actual_vwap - order.reference_price) / order.reference_price * _BPS
    expected_residual = max(0.0, order.quantity - sum(item.quantity for item in slices))
    actual_residual = max(0.0, order.quantity - actual_qty)
    initial_adv = slices[0].known_average_volume if slices else None
    reasons: list[str] = []
    if not slices:
        reasons.append("no_eligible_executable_bar_or_known_positive_volume")
    if actual_vwap is None:
        reasons.append("no_actual_execution_price")
    if matched_qty <= _EPSILON and actual_vwap is not None:
        reasons.append("no_matched_scheduled_quantity")
    if actual_fee is None:
        reasons.append("fee_unknown_price_only")
    if schedule_reason is not None:
        reasons.append(schedule_reason)
    if not price_comparison_eligible:
        reasons.append(str(actual.get("quality") or "raw_execution_quality_excluded"))
    return {
        "client_order_id": order.client_order_id,
        "decision_id": order.decision_id,
        "decision_timestamp": order.decision_timestamp.isoformat(),
        "symbol": order.symbol,
        "market": order.market.value,
        "side": order.side.value,
        "status": order.status,
        "decision_origin": order.origin,
        "execution_role": order.execution_role,
        "expected_quantity": order.quantity,
        "actual_gross_quantity": actual_qty,
        "expected_notional": order.quantity * order.reference_price if order.reference_price is not None else None,
        "lagged_adv": initial_adv,
        "order_quantity_over_lagged_adv": order.quantity / initial_adv if initial_adv and initial_adv > 0 else None,
        "gross_fill_ratio": actual_qty / order.quantity if order.quantity > 0 else None,
        "scheduled_quantity": sum(item.quantity for item in slices),
        "matched_quantity": matched_qty,
        "scheduled_residual_quantity": expected_residual,
        "actual_residual_quantity": actual_residual,
        "complete": actual_residual <= _EPSILON and expected_residual <= _EPSILON,
        "decision_close_shortfall_bps": close_bps,
        "scheduled_zero_cost_open_vwap": zero_vwap,
        "paper_vwap": actual_vwap,
        "paper_vs_zero_cost_open_bps": price_bps,
        "actual_price_cost": price_cost,
        "modeled_price_cost": modeled_price_cost,
        "modeled_fee": modeled_fee,
        "actual_fee": actual_fee,
        "actual_fee_known": bool(actual.get("fee_known")),
        "actual_total_cost": actual_total,
        "modeled_total_cost": modeled_total,
        "underestimation_residual": actual_total - modeled_total if actual_total is not None else None,
        "measurement_quality": actual.get("quality"),
        "quality_states": actual.get("quality_states", []),
        "comparison_eligibility": {
            "final_gross_execution_quantity": bool(actual.get("final_gross_quantity_eligible", True)),
            "final_vwap": bool(actual.get("final_vwap_eligible", True)),
            "price_only": price_comparison_eligible,
            "total_cost": bool(actual.get("total_cost_comparison_eligible", False)),
        },
        "excluded_reasons": reasons,
        "schedule": [
            {
                "timestamp": item.timestamp.isoformat(),
                "quantity": item.quantity,
                "open": item.open_price,
                "known_average_volume": item.known_average_volume,
                "capacity_timestamp": item.capacity_timestamp.isoformat(),
                "modeled_price": item.open_price
                * (
                    1.0
                    + (1.0 if order.side == OrderSide.BUY else -1.0)
                    * (float(config.backtest.slippage_bps) + float(config.backtest.spread_bps) / 2.0)
                    / _BPS
                ),
                "modeled_fee": _modeled_fee(order, item, config),
            }
            for item in slices
        ],
    }


def run_shadow_comparator(
    db_path: str | Path,
    bars: Iterable[Bar],
    config: Any,
    *,
    dataset: dict[str, object],
    as_of: datetime | None = None,
) -> dict[str, object]:
    """Build an offline comparison report from a read-only PAPER ledger.

    ``as_of`` makes the result historical: later decisions and raw broker
    events are invisible, while the default preserves the existing latest
    snapshot behavior.
    """
    # Use the engine's existing duplicate/canonical-symbol contract before the
    # comparator applies its independent, golden-locked schedule loop.
    local_bars = tuple(PortfolioBacktestEngine._deduplicate(list(bars)))
    connection = _open_readonly(db_path)
    try:
        orders = _load_orders(connection, as_of=as_of)
        records: list[dict[str, object]] = []
        for order in orders:
            actual = _actual_from_events(connection, order.client_order_id, as_of=as_of) or _actual_from_legacy_fills(
                connection, order.client_order_id
            )
            records.append(_order_record(order, actual, local_bars, config))
    finally:
        connection.close()
    decisions: dict[str, list[dict[str, object]]] = {}
    for record in records:
        decisions.setdefault(str(record["decision_id"]), []).append(record)
    aggregates: dict[str, dict[str, object]] = {}
    for market in (Market.EQUITY.value, Market.CRYPTO.value):
        rows = [item for item in records if item["market"] == market]
        price_values = [
            value for item in rows if (value := _float(item.get("paper_vs_zero_cost_open_bps"))) is not None
        ]
        residuals = [value for item in rows if (value := _float(item.get("underestimation_residual"))) is not None]
        excluded: list[str] = []
        for item in rows:
            reasons = item.get("excluded_reasons")
            if isinstance(reasons, list):
                excluded.extend(str(reason) for reason in reasons)
        strata: dict[str, dict[str, int]] = {}
        for field in ("side", "status", "decision_origin", "execution_role", "actual_fee_known"):
            values = [str(item[field]) for item in rows]
            strata[field] = {value: values.count(value) for value in sorted(set(values))}
        aggregates[market] = {
            "eligible_count": len(price_values),
            "excluded_count": len(rows) - len(price_values),
            "excluded_reasons": {reason: excluded.count(reason) for reason in sorted(set(excluded))},
            "paper_vs_zero_cost_open_bps": _summary(price_values),
            "underestimation_residual": _summary(residuals),
            "aggregate_modeled_cost": sum(_float(item.get("modeled_total_cost")) or 0.0 for item in rows),
            "aggregate_actual_known_cost": sum(
                _float(item.get("actual_total_cost")) or 0.0
                for item in rows
                if item.get("actual_total_cost") is not None
            ),
            "strata": strata,
            "transferability_verdict": "UNKNOWN_COST_TRANSFERABILITY",
        }
    return {
        "available": True,
        "read_only": True,
        "dataset": dataset,
        "schedule_contract": "current_backtest_dynamic_residual_capacity",
        "orders": records,
        "decisions": [{"decision_id": key, "orders": value} for key, value in sorted(decisions.items())],
        "aggregates_by_market": aggregates,
        "transferability_verdict": "UNKNOWN_COST_TRANSFERABILITY",
    }
