"""Execution-quality telemetry over the runtime ledger.

Pure, JSON-serializable, read-only queries against the single runtime
SQLite database (``config.runtime.decision_db_path``). The runtime database
holds BOTH authoritative record families in one file:

* the execution ledger (``orders``, ``order_events``, ``executions``,
  ``fee_activities``, ``managed_positions``, ``safety_incidents``,
  ``runtime_sessions``) written by ``parayabuck.execution.ledger``, and
* the portfolio repository (``portfolio_decisions``, ``portfolio_orders``,
  ``portfolio_fills``, ``portfolio_runtime_sessions``,
  ``portfolio_rebalance_executions``, ``portfolio_high_water``) written by
  ``parayabuck.portfolio.runtime_service``.

Every function here is REPORT ONLY: no broker calls, no writes, no
auto-calibration of any cost assumption. Empty or partial databases (fresh
file, missing tables, no data yet) yield explicit zeros / ``None`` and never
raise.
"""

from __future__ import annotations

import json
import math
import sqlite3
from datetime import UTC, datetime
from itertools import pairwise
from pathlib import Path
from typing import Any

# Direction of the adverse-slippage sign convention: for a BUY a fill above
# the reference price is adverse; for a SELL a fill below the reference is
# adverse. Positive reported slippage bps always means "worse than reference".
_ADVERSE_SIGN: dict[str, int] = {"buy": 1, "long": 1, "sell": -1, "short": -1}

_BPS = 10_000.0


def _iso_now() -> str:
    return datetime.now(UTC).isoformat()


def _to_float(value: object, default: float = 0.0) -> float:
    """Coerce a JSON/sqlite value (str, int, float, None) to float."""
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return default
    return default


def _open_readonly(db_path: str | Path) -> sqlite3.Connection | None:
    """Open the runtime database read-only, or ``None`` when it is absent."""
    path = Path(db_path)
    # No exists() TOCTOU — try connect directly, handle missing file via OperationalError
    try:
        # Encode path for URI: escape ? and # which would be parsed as query/fragment
        safe = str(path).replace("?", "%3F").replace("#", "%23")
        connection = sqlite3.connect(f"file:{safe}?mode=ro", uri=True)
        connection.execute("PRAGMA query_only=ON")
        return connection
    except sqlite3.OperationalError as exc:
        # Only suppress "no such file/table" missing-DB case; re-raise corruption/I/O errors
        msg = str(exc).lower()
        if "unable to open" in msg or "no such file" in msg or "no such table" in msg:
            return None
        raise


def _query(
    connection: sqlite3.Connection,
    sql: str,
    params: tuple[object, ...] = (),
) -> list[tuple[Any, ...]]:
    try:
        return list(connection.execute(sql, params).fetchall())
    except sqlite3.OperationalError as exc:
        msg = str(exc).lower()
        if "no such table" in msg or "no such column" in msg:
            return []
        raise


def _table_exists(connection: sqlite3.Connection, table: str) -> bool:
    row = connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
    return row is not None


def _columns(connection: sqlite3.Connection, table: str) -> set[str]:
    if not _table_exists(connection, table):
        return set()
    # Validate identifier to prevent injection if table name ever flows from external input
    if not table.replace("_", "").isalnum() or not table[0].isalpha():
        return set()
    quoted = '"' + table.replace('"', '""') + '"'
    return {str(row[1]) for row in connection.execute(f"PRAGMA table_info({quoted})")}


def _percentile(sorted_values: list[float], percentile: float) -> float | None:
    """Linear-interpolation percentile (numpy-default-like) of sorted values."""
    if not 0 <= percentile <= 100:
        raise ValueError(f"percentile must be 0..100, got {percentile!r}")
    if not sorted_values:
        return None
    if len(sorted_values) == 1:
        return sorted_values[0]
    # Filter non-finite before ranking to avoid NaN poisoning
    finite = [v for v in sorted_values if math.isfinite(v)]
    if not finite:
        return None
    ordered = sorted(finite)
    rank = (percentile / 100.0) * (len(ordered) - 1)
    lower = math.floor(rank)
    upper = min(lower + 1, len(ordered) - 1)
    weight = rank - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def _stat_summary(values: list[float]) -> dict[str, float | None]:
    if not values:
        return {"count": 0, "mean": None, "median": None, "p75": None, "p95": None}
    # Drop non-finite to prevent NaN poisoning of mean/median/p95
    finite = [v for v in values if math.isfinite(v)]
    if not finite:
        return {"count": 0, "mean": None, "median": None, "p75": None, "p95": None}
    ordered = sorted(finite)
    return {
        "count": len(finite),
        "mean": sum(finite) / len(finite),
        "median": _percentile(ordered, 50),
        "p75": _percentile(ordered, 75),
        "p95": _percentile(ordered, 95),
    }


def slippage_bps(fill_price: float, reference_price: float, side: str) -> float | None:
    """Adverse slippage of one fill in basis points.

    Positive = adverse (worse than the reference): a BUY filled above the
    reference or a SELL filled below it. ``side`` accepts ``buy``/``sell``
    (portfolio orders) or ``long``/``short`` (legacy entry orders).
    """
    if not reference_price or reference_price <= 0 or not fill_price or fill_price <= 0:
        return None
    sign = _ADVERSE_SIGN.get(str(side).lower())
    if sign is None:
        return None
    return (fill_price - reference_price) / reference_price * _BPS * sign


def fill_delay_seconds(occurred_at: str, submitted_at: str | None) -> float | None:
    """Seconds between an order submission and a fill, or ``None`` when either
    timestamp is missing/unparseable or the fill predates the submission."""
    if not submitted_at:
        return None
    try:
        occurred = datetime.fromisoformat(occurred_at)
        submitted = datetime.fromisoformat(submitted_at)
    except (TypeError, ValueError):
        return None
    delay = (occurred - submitted).total_seconds()
    return delay if delay >= 0 else None


def _empty_state() -> dict[str, object]:
    return {
        "available": False,
        "reason": "runtime database not available",
        "asof": _iso_now(),
    }


# --------------------------------------------------------------------------
# execution_metrics
# --------------------------------------------------------------------------


def execution_metrics(
    db_path: str | Path,
    *,
    _connection: sqlite3.Connection | None = None,
) -> dict[str, object]:
    """Orders / fills / rejects / fill latency / slippage / fees over the
    runtime ledger, with per-market breakdowns. Pure, read-only, empty-safe.

    ``_connection`` (private) lets a caller share one already-open read-only
    connection across several reports; the caller owns its lifecycle.
    """
    owns = _connection is None
    connection = _connection if _connection is not None else _open_readonly(db_path)
    if connection is None:
        return _empty_state()
    try:
        orders_cols = _columns(connection, "orders")
        by_market: dict[str, dict[str, int]] = {}
        by_strategy: dict[str, int] = {}
        by_terminal: dict[str, int] = {}
        total_orders = 0
        open_orders = 0
        for row in _query(
            connection,
            """
            SELECT market, strategy, terminal_state FROM orders
            WHERE client_order_id IS NOT NULL
            """,
        ):
            market = str(row[0] or "unknown")
            strategy = str(row[1] or "unknown")
            terminal = str(row[2] or "open")
            total_orders += 1
            bucket = by_market.setdefault(market, {"total": 0, "filled": 0, "rejected": 0})
            bucket["total"] += 1
            if terminal in {"filled", "fill", "partially_filled"}:
                bucket["filled"] += 1
            elif terminal in {"rejected", "canceled", "cancelled", "expired"}:
                bucket["rejected"] += 1
            by_strategy[strategy] = by_strategy.get(strategy, 0) + 1
            by_terminal[terminal] = by_terminal.get(terminal, 0) + 1
            if terminal == "open":
                open_orders += 1
        for row in _query(
            connection,
            """
            SELECT market, status FROM portfolio_orders
            """,
        ):
            market = str(row[0] or "unknown")
            status = str(row[1] or "unknown")
            total_orders += 1
            bucket = by_market.setdefault(market, {"total": 0, "filled": 0, "rejected": 0})
            bucket["total"] += 1
            terminal = "filled" if status in {"filled", "fill"} else status
            if terminal == "filled":
                bucket["filled"] += 1
            elif terminal in {"rejected", "canceled", "cancelled", "expired"}:
                bucket["rejected"] += 1
            by_terminal[terminal] = by_terminal.get(terminal, 0) + 1
            if status not in {"filled", "fill", "canceled", "cancelled", "rejected", "expired"}:
                open_orders += 1

        fills_by_market: dict[str, int] = {}
        orders_with_fills_by_market: dict[str, int] = {}
        latency_values: list[float] = []
        slippage_values: list[float] = []
        slippage_by_market: dict[str, list[float]] = {}
        fee_values: list[float] = []
        fees_by_market: dict[str, float] = {}

        # Portfolio fills (side-aware reference slippage, fill delay).
        if _table_exists(connection, "portfolio_fills") and _table_exists(connection, "portfolio_orders"):
            delay_cols = _columns(connection, "portfolio_fills")
            delay_expr = "f.fill_delay_seconds" if "fill_delay_seconds" in delay_cols else "NULL"
            for row in _query(
                connection,
                f"""
                SELECT f.price, f.fee, f.occurred_at, o.reference_price, o.side, o.market,
                       o.created_at, {delay_expr}
                FROM portfolio_fills f
                JOIN portfolio_orders o ON o.client_order_id = f.client_order_id
                """,
            ):
                price, fee, occurred_at, reference, side, market, created_at, stored_delay = row
                market_key = str(market or "unknown")
                fills_by_market[market_key] = fills_by_market.get(market_key, 0) + 1
                if price is not None and reference:
                    bps = slippage_bps(float(price), float(reference), str(side))
                    if bps is not None:
                        slippage_values.append(bps)
                        slippage_by_market.setdefault(market_key, []).append(bps)
                if fee is not None and fee > 0:
                    fee_values.append(float(fee))
                    fees_by_market[market_key] = fees_by_market.get(market_key, 0.0) + float(fee)
                delay: float | None = None
                if stored_delay is not None:
                    delay = float(stored_delay)
                elif created_at:
                    delay = fill_delay_seconds(str(occurred_at), str(created_at))
                if delay is not None:
                    latency_values.append(delay)

        # Legacy ledger fills (stored slippage bps / fill delay columns, and
        # any order with a positive cumulative fill).
        if "filled_at" in orders_cols and _table_exists(connection, "orders"):
            for row in _query(
                connection,
                """
                SELECT slippage_bps, filled_at, submitted_at, market,
                       cumulative_filled_qty
                FROM orders
                WHERE slippage_bps IS NOT NULL
                   OR filled_at IS NOT NULL
                   OR cumulative_filled_qty > 0
                """,
            ):
                stored_bps, filled_at, submitted_at, market, cumulative = row
                market_key = str(market or "unknown")
                if cumulative is not None and float(cumulative) > 0:
                    # Legacy rows: ONE row per order, however many partial
                    # fills it had — count orders-with-fills, not fill events
                    # (portfolio_fills rows above count each fill exactly once).
                    orders_with_fills_by_market[market_key] = orders_with_fills_by_market.get(market_key, 0) + 1
                if stored_bps is not None:
                    slippage_values.append(float(stored_bps))
                    slippage_by_market.setdefault(market_key, []).append(float(stored_bps))
                if filled_at:
                    delay = fill_delay_seconds(str(filled_at), str(submitted_at))
                    if delay is not None:
                        latency_values.append(delay)

        # Fees from the legacy fee activities ledger.
        if _table_exists(connection, "fee_activities"):
            for fee in _query(connection, "SELECT fee FROM fee_activities WHERE fee > 0"):
                fee_values.append(float(fee[0]))
                fees_by_market["unknown"] = fees_by_market.get("unknown", 0.0) + float(fee[0])

        rejected = int(by_terminal.get("rejected", 0))
        cancelled = int(by_terminal.get("cancelled", 0) + by_terminal.get("canceled", 0))
        expired = int(by_terminal.get("expired", 0))
        return {
            "available": True,
            "orders": {
                "total": total_orders,
                "open": open_orders,
                "by_market": by_market,
                "by_strategy": by_strategy,
                "by_terminal_state": by_terminal,
            },
            "rejects": {
                "count": rejected + cancelled + expired,
                "rejected": rejected,
                "cancelled": cancelled,
                "expired": expired,
            },
            "fills": {
                "count": sum(fills_by_market.values()),
                "by_market": fills_by_market,
            },
            "orders_with_fills": {
                "count": sum(orders_with_fills_by_market.values()),
                "by_market": orders_with_fills_by_market,
            },
            "fill_latency": {
                **_stat_summary(latency_values),
                "unit": "seconds",
            },
            "slippage": {
                **_stat_summary(slippage_values),
                "by_market": {market: _stat_summary(values) for market, values in sorted(slippage_by_market.items())},
                "unit": "bps",
                "sign_convention": "positive = adverse (fill worse than reference)",
            },
            "fees": {
                "total": round(sum(fee_values), 6),
                "by_market": {key: round(value, 6) for key, value in fees_by_market.items()},
                **_stat_summary(fee_values),
                "unit": "account currency",
            },
            "asof": _iso_now(),
        }
    finally:
        if owns:
            connection.close()


# --------------------------------------------------------------------------
# forward_metrics
# --------------------------------------------------------------------------


def _turnover_and_churn_by_market(
    decisions_by_market: dict[str, list[tuple[str, str, str, str]]],
) -> tuple[dict[str, list[float]], dict[str, list[float]], dict[str, int]]:
    """Per-market decision turnover, candidate-pool sizes and universe churn.

    Derived purely from persisted ``portfolio_decisions.target_json`` rows:
    turnover is half the L1 weight distance between consecutive decisions,
    churn counts consecutive pairs whose universe_version changed.
    """
    turnover_by_market: dict[str, list[float]] = {}
    candidate_pool_by_market: dict[str, list[float]] = {}
    universe_versions_by_market: dict[str, list[tuple[str, str]]] = {}
    for market, decisions in decisions_by_market.items():
        weights_by_decision: list[tuple[str, dict[str, float], int]] = []
        for _decision_id, timestamp, _strategy, target_json in decisions:
            try:
                raw = json.loads(target_json)
            except (TypeError, ValueError):
                continue
            weights = {
                str(item.get("symbol", "")): float(item.get("weight", 0.0))
                for item in raw.get("weights", ())
                if isinstance(item, dict)
            }
            metadata = raw.get("metadata") or {}
            qualified = metadata.get("qualified_symbols")
            pool_size: int | None = None
            if isinstance(qualified, (int, float)):
                pool_size = int(qualified)
            elif weights:
                pool_size = len(weights)
            if pool_size is not None:
                candidate_pool_by_market.setdefault(market, []).append(float(pool_size))
            weights_by_decision.append((str(timestamp), weights, pool_size or 0))
            universe_versions_by_market.setdefault(market, []).append(
                (str(timestamp), str(raw.get("universe_version", "")))
            )
        for (_previous_ts, previous_w, _), (_current_ts, current_w, _) in pairwise(weights_by_decision):
            symbols = set(previous_w) | set(current_w)
            turnover = sum(abs(previous_w.get(symbol, 0.0) - current_w.get(symbol, 0.0)) for symbol in symbols) / 2.0
            turnover_by_market.setdefault(market, []).append(turnover)

    churn_by_market: dict[str, int] = {}
    for market, entries in universe_versions_by_market.items():
        churn_by_market[market] = sum(
            previous_version != current_version
            for (_previous_ts, previous_version), (_current_ts, current_version) in pairwise(entries)
        )
    return turnover_by_market, candidate_pool_by_market, churn_by_market


def forward_metrics(
    db_path: str | Path,
    *,
    _connection: sqlite3.Connection | None = None,
) -> dict[str, object]:
    """Strategy / execution / portfolio forward-looking quality metrics over
    the persisted decisions, orders, fills and risk state. Read-only.

    ``_connection`` (private) lets a caller share one already-open read-only
    connection; the caller owns its lifecycle.
    """
    owns = _connection is None
    connection = _connection if _connection is not None else _open_readonly(db_path)
    if connection is None:
        return _empty_state()
    try:
        decisions_by_market: dict[str, list[tuple[str, str, str, str]]] = {}
        decision_count = 0
        rebalance_orders = 0
        if _table_exists(connection, "portfolio_decisions"):
            for row in _query(
                connection,
                """
                SELECT decision_id, decision_timestamp, market, strategy_name, target_json
                FROM portfolio_decisions
                ORDER BY decision_timestamp
                """,
            ):
                decision_id, timestamp, market, strategy, target_json = row
                decisions_by_market.setdefault(str(market), []).append(
                    (str(decision_id), str(timestamp), str(strategy), str(target_json))
                )
                decision_count += 1
        if _table_exists(connection, "portfolio_orders"):
            rows = _query(connection, "SELECT COUNT(*) FROM portfolio_orders")
            rebalance_orders = int(rows[0][0]) if rows else 0

        turnover_by_market, candidate_pool_by_market, churn_by_market = _turnover_and_churn_by_market(
            decisions_by_market
        )

        execution: dict[str, int] = {}
        if _table_exists(connection, "portfolio_orders"):
            status_counts: dict[str, int] = {}
            for (status,) in _query(connection, "SELECT status FROM portfolio_orders"):
                status_counts[str(status)] = status_counts.get(str(status), 0) + 1
            execution = status_counts
        submitted = int(execution.get("submitted", 0) + execution.get("submitting", 0))
        filled = int(execution.get("filled", 0) + execution.get("fill", 0))
        rejected = int(
            execution.get("rejected", 0)
            + execution.get("cancelled", 0)
            + execution.get("canceled", 0)
            + execution.get("expired", 0)
        )
        open_count = int(
            execution.get("submitted", 0)
            + execution.get("submitting", 0)
            + execution.get("accepted", 0)
            + execution.get("partially_filled", 0)
        )

        realized = 0.0
        unrealized = 0.0
        fee_total = 0.0
        if _table_exists(connection, "portfolio_fills"):
            for row in _query(
                connection,
                "SELECT realized_pnl, unrealized_pnl, fee FROM portfolio_fills",
            ):
                realized += float(row[0] or 0.0)
                unrealized += float(row[1] or 0.0)
                fee_total += float(row[2] or 0.0)

        risk: dict[str, float] | None = None
        if _table_exists(connection, "risk_state"):
            rows = _query(
                connection,
                """
                SELECT payload FROM risk_state
                ORDER BY session_date DESC, updated_at DESC LIMIT 1
                """,
            )
            if rows:
                row = rows[0]
                try:
                    payload = json.loads(str(row[0]))
                    day_start = float(payload.get("day_start_equity", 0.0))
                    risk = {
                        "day_start_equity": day_start,
                        "peak_equity": float(payload.get("peak_equity", 0.0)),
                        "realized_pnl": float(payload.get("realized_pnl", 0.0)),
                        "unrealized_pnl": float(payload.get("unrealized_pnl", 0.0)),
                        "fees": float(payload.get("fees", 0.0)),
                    }
                except (TypeError, ValueError, KeyError):
                    risk = None
        equity_estimate: float | None = None
        peak_equity: float | None = None
        drawdown_pct: float | None = None
        realized_total = realized
        unrealized_total = unrealized
        fee_total_all = fee_total
        if risk is not None:
            # The persisted risk_state is the authoritative running PnL; the
            # fills ledger may predate or postdate it, so report the sum.
            realized_total = risk["realized_pnl"] + realized
            unrealized_total = risk["unrealized_pnl"] + unrealized
            fee_total_all = risk["fees"] + fee_total
            equity_estimate = (
                risk["day_start_equity"] + realized_total + unrealized_total - risk["fees"]
                if risk["day_start_equity"] > 0
                else None
            )
            peak_equity = risk["peak_equity"] or None
            if equity_estimate is not None and peak_equity is not None and peak_equity > 0:
                drawdown_pct = max(0.0, (peak_equity - equity_estimate) / peak_equity)

        turnover_values = [value for values in turnover_by_market.values() for value in values]
        pool_values = [float(value) for values in candidate_pool_by_market.values() for value in values]
        return {
            "available": True,
            "strategy": {
                "decisions": {
                    "count": decision_count,
                    "by_market": {market: len(decisions) for market, decisions in decisions_by_market.items()},
                    "by_strategy": {
                        strategy: sum(
                            1
                            for decisions in decisions_by_market.values()
                            for _decision_id, _ts, candidate, _json in decisions
                            if candidate == strategy
                        )
                        for strategy in {
                            candidate
                            for decisions in decisions_by_market.values()
                            for _decision_id, _ts, candidate, _json in decisions
                        }
                    },
                    "latest": (
                        max(
                            timestamp
                            for decisions in decisions_by_market.values()
                            for _decision_id, timestamp, _strategy, _json in decisions
                        )
                        if decisions_by_market
                        else None
                    ),
                },
                "rebalances": {
                    # One persisted portfolio_decisions row == one rebalance
                    # decision, so the total is the decision count (the
                    # by_market map below stays per-market).
                    "count": decision_count,
                    "by_market": {market: len(decisions) for market, decisions in decisions_by_market.items()},
                    "orders_planned": rebalance_orders,
                },
                "target_turnover": {
                    **_stat_summary(turnover_values),
                    "by_market": {
                        market: _stat_summary(values) for market, values in sorted(turnover_by_market.items())
                    },
                    "unit": "one-way weight turnover per rebalance",
                },
                "observation_set_churn": {
                    "events": churn_by_market,
                    "total": sum(churn_by_market.values()),
                    "note": ("proxy: universe_version change between consecutive persisted decisions per market"),
                },
                "candidate_pool_size": {
                    **_stat_summary(pool_values),
                    "by_market": {
                        market: _stat_summary(values) for market, values in sorted(candidate_pool_by_market.items())
                    },
                },
            },
            "execution": {
                "submitted": submitted,
                "filled": filled,
                "rejected": rejected,
                "open": open_count,
                "reconciliation_required": int(execution.get("reconciliation_required", 0)),
                "fill_rate": (filled / submitted) if submitted > 0 else None,
            },
            "portfolio": {
                "pnl": {
                    "realized": round(realized_total, 6),
                    "unrealized": round(unrealized_total, 6),
                    "total": round(realized_total + unrealized_total, 6),
                    "fees": round(fee_total_all, 6),
                },
                "equity_estimate": (round(equity_estimate, 6) if equity_estimate is not None else None),
                "gross_exposure": None,
                "cash": None,
                "drawdown": {
                    "pct": round(drawdown_pct, 6) if drawdown_pct is not None else None,
                    "peak_equity": round(peak_equity, 6) if peak_equity is not None else None,
                },
                "note": (
                    "equity_estimate = day_start_equity + realized + unrealized - fees "
                    "from the latest persisted risk_state; gross exposure and cash are "
                    "broker-snapshot values and are not persisted in the ledger"
                ),
            },
            "asof": _iso_now(),
        }
    finally:
        if owns:
            connection.close()


# --------------------------------------------------------------------------
# cost_calibration
# --------------------------------------------------------------------------


def configured_cost_assumptions(config: object | None = None) -> dict[str, object]:
    """The backtest cost assumptions a calibration report compares against.

    ``config`` may be a ``parayabuck.config.Config`` (or any object exposing
    ``backtest.slippage_bps`` etc.); when ``None`` the shipped defaults are
    reported. This function NEVER modifies any assumption.
    """
    if config is None:
        # Lazy import keeps this module cheap for callers that only need
        # ledger queries; the shipped defaults stand in when unavailable.
        try:  # pragma: no cover - config is always importable in-tree
            from parayabuck.config import BacktestConfig
        except ImportError:  # pragma: no cover
            backtest = None
        else:
            backtest = BacktestConfig()
    else:
        backtest = getattr(config, "backtest", None)
    if backtest is None:
        return {
            "available": False,
            "note": "no configuration available; defaults unknown",
        }
    return {
        "available": True,
        "slippage_bps": float(backtest.slippage_bps),
        "spread_bps": float(backtest.spread_bps),
        "commission": float(backtest.commission),
        "crypto_taker_fee_bps": float(backtest.crypto_taker_fee_bps),
        "equity_use_effective_dated_fees": bool(backtest.equity_use_effective_dated_fees),
        "model": (
            "adverse slippage = slippage_bps + spread_bps/2 per fill; "
            "crypto taker fee on crypto fills; equity regulatory fees by trade date"
        ),
    }


def cost_calibration(
    db_path: str | Path,
    config: object | None = None,
    *,
    _connection: sqlite3.Connection | None = None,
) -> dict[str, object]:
    """Configured vs observed cost report. REPORT ONLY — never adjusts
    assumptions; observed values feed the report so a human can compare.

    ``_connection`` (private) lets a caller share one already-open read-only
    connection; the caller owns its lifecycle.
    """
    owns = _connection is None
    connection = _connection if _connection is not None else _open_readonly(db_path)
    configured = configured_cost_assumptions(config)
    if connection is None:
        empty = _empty_state()
        empty["configured"] = configured
        empty["observed"] = {
            "slippage": _stat_summary([]),
            "spread_estimate": _stat_summary([]),
            "fees": _stat_summary([]),
            "total_fees": 0.0,
        }
        empty["verdict"] = "no_observed_data"
        return empty
    try:
        slippage_values: list[float] = []
        spread_values: list[float] = []
        fee_values: list[float] = []
        fee_completeness = "legacy_or_no_raw_fee_provenance"
        if _table_exists(connection, "portfolio_execution_events"):
            fee_rows = _query(
                connection,
                """SELECT raw_cumulative_filled_quantity, fee_observed
                   FROM portfolio_execution_events""",
            )
            if any(_to_float(quantity) > 0 and not bool(observed) for quantity, observed in fee_rows):
                fee_completeness = "unknown"
            elif fee_rows:
                fee_completeness = "observed_event_local_not_aggregated"
        if _table_exists(connection, "portfolio_fills") and _table_exists(connection, "portfolio_orders"):
            for row in _query(
                connection,
                """
                SELECT f.price, f.fee, o.reference_price, o.side
                FROM portfolio_fills f
                JOIN portfolio_orders o ON o.client_order_id = f.client_order_id
                """,
            ):
                price, fee, reference, side = row
                if price and reference:
                    bps = slippage_bps(float(price), float(reference), str(side))
                    if bps is not None:
                        slippage_values.append(bps)
                if fee is not None and fee > 0:
                    fee_values.append(float(fee))
        orders_cols = _columns(connection, "orders")
        if "slippage_bps" in orders_cols:
            for (stored_bps,) in _query(connection, "SELECT slippage_bps FROM orders WHERE slippage_bps IS NOT NULL"):
                slippage_values.append(float(stored_bps))
        if "spread_estimate_bps" in orders_cols:
            for (stored_spread,) in _query(
                connection,
                "SELECT spread_estimate_bps FROM orders WHERE spread_estimate_bps IS NOT NULL",
            ):
                spread_values.append(float(stored_spread))
        if _table_exists(connection, "fee_activities"):
            for (fee,) in _query(connection, "SELECT fee FROM fee_activities WHERE fee > 0"):
                fee_values.append(float(fee))

        observed_slippage = _stat_summary(slippage_values)
        observed_spread = _stat_summary(spread_values)
        observed_fees = _stat_summary(fee_values)

        configured_slippage = _to_float(configured.get("slippage_bps", 0.0))
        configured_spread = _to_float(configured.get("spread_bps", 0.0))
        modeled_total_bps = configured_slippage + configured_spread / 2.0
        observed_median = observed_slippage["median"]
        verdict: str
        if observed_median is None:
            verdict = "no_observed_data"
        else:
            deviation = observed_median - modeled_total_bps
            if abs(deviation) <= 0.5 * max(1.0, modeled_total_bps):
                verdict = "within_model"
            elif deviation > 0:
                verdict = "above_model"
            else:
                verdict = "below_model"
        return {
            "available": True,
            "configured": configured,
            "observed": {
                "slippage_bps": observed_slippage,
                "spread_estimate_bps": observed_spread,
                "fees": observed_fees,
                "total_fees": round(sum(fee_values), 6),
                "fee_completeness": fee_completeness,
            },
            "comparison": {
                "modeled_adverse_bps": round(modeled_total_bps, 4),
                "observed_median_bps": observed_median,
                "observed_p75_bps": observed_slippage["p75"],
                "observed_p95_bps": observed_slippage["p95"],
                "verdict": verdict,
                # The current diagnostic is deliberately a decision-close
                # reference comparison.  It is useful within that diagnostic,
                # but cannot prove the backtest cost model is conservative.
                "reference_basis": "decision_close",
                "alignment_status": "not_backtest_aligned",
                "transferability_verdict": "UNKNOWN",
                "measurement_quality": "legacy_close_reference_only",
            },
            "reference_basis": "decision_close",
            "alignment_status": "not_backtest_aligned",
            "transferability_verdict": "UNKNOWN",
            "measurement_quality": "legacy_close_reference_only",
            "policy": (
                "REPORT ONLY: observed costs never adjust any configured "
                "assumption; a cost-model change requires a separate research mission."
            ),
            "asof": _iso_now(),
        }
    finally:
        if owns:
            connection.close()


# --------------------------------------------------------------------------
# Raw PAPER execution measurement
# --------------------------------------------------------------------------


def execution_measurement(
    db_path: str | Path,
    *,
    _connection: sqlite3.Connection | None = None,
    as_of: datetime | None = None,
) -> dict[str, object]:
    """Read raw gross execution beside authoritative net ownership.

    This is a measurement-only view of ``portfolio_execution_events``.  It
    does not repair invalid broker sequences or infer missing fees; those
    facts remain explicit in the returned quality fields.
    """
    owns = _connection is None
    connection = _connection if _connection is not None else _open_readonly(db_path)
    if connection is None:
        return _empty_state()
    try:
        if not _table_exists(connection, "portfolio_orders"):
            return {"available": True, "orders": [], "asof": _iso_now()}
        order_columns = _columns(connection, "portfolio_orders")
        submit_started_expr = "o.submit_call_started_at" if "submit_call_started_at" in order_columns else "NULL"
        submit_returned_expr = "o.submit_call_returned_at" if "submit_call_returned_at" in order_columns else "NULL"
        order_rows = _query(
            connection,
            f"""SELECT o.client_order_id, o.symbol, o.market, o.side, o.quantity,
                      o.cumulative_filled_quantity, o.status, o.created_at,
                      {submit_started_expr}, {submit_returned_expr},
                      d.decision_timestamp, d.target_json,
                      (SELECT r.phase FROM portfolio_rebalance_executions r
                       WHERE r.decision_id=o.decision_id)
               FROM portfolio_orders o
               JOIN portfolio_decisions d ON d.decision_id=o.decision_id
               ORDER BY o.created_at""",
        )
        event_rows = _query(
            connection,
            """SELECT id, source, dedup_identity, client_order_id, broker_order_id,
                      event_type, broker_event_at, received_at,
                      raw_cumulative_filled_quantity,
                      raw_cumulative_average_fill_price, raw_fee, fee_observed,
                      measurement_quality
               FROM portfolio_execution_events
               ORDER BY client_order_id, id""",
        )
        events_by_order: dict[str, list[tuple[Any, ...]]] = {}
        for event in event_rows:
            if as_of is not None:
                try:
                    event_at = datetime.fromisoformat(str(event[6]).replace("Z", "+00:00"))
                except ValueError:
                    continue
                event_at = event_at.replace(tzinfo=UTC) if event_at.tzinfo is None else event_at.astimezone(UTC)
                if event_at > as_of:
                    continue
            events_by_order.setdefault(str(event[3]), []).append(event)
        result_orders: list[dict[str, object]] = []
        valid_fill_qualities = {"first_fill", "valid_incremental"}
        benign_nonfill_qualities = {"status_only", "duplicate_or_zero_delta"}
        for row in order_rows:
            (
                client_order_id,
                symbol,
                market,
                side,
                expected_qty,
                net_owned_qty,
                status,
                persisted_at,
                submit_call_started_at,
                submit_call_returned_at,
                decision_timestamp,
                target_json,
                rebalance_phase,
            ) = row
            expected = _to_float(expected_qty)
            raw_events = events_by_order.get(str(client_order_id), [])
            previous_qty = 0.0
            previous_notional = 0.0
            has_previous = False
            gross_executed: float | None = None
            incremental_fills: list[dict[str, object]] = []
            qualities: list[str] = []
            fee_unknown = False
            first_fill_at: str | None = None
            last_fill_at: str | None = None
            terminal_at: str | None = None
            for event in raw_events:
                raw_qty = _to_float(event[8])
                raw_average = event[9]
                quality = str(event[12])
                qualities.append(quality)
                if raw_qty > 0 and not bool(event[11]):
                    fee_unknown = True
                if str(event[5]).lower() in {"fill", "filled", "canceled", "cancelled", "rejected", "expired"}:
                    terminal_at = str(event[6])
                if quality not in valid_fill_qualities or raw_average is None:
                    continue
                average = _to_float(raw_average)
                if average <= 0:
                    continue
                notional = raw_qty * average
                delta_qty = raw_qty if not has_previous else raw_qty - previous_qty
                delta_notional = notional if not has_previous else notional - previous_notional
                if delta_qty <= 0 or delta_notional <= 0:
                    continue
                incremental_fills.append(
                    {
                        "source": str(event[1]),
                        "dedup_identity": str(event[2]),
                        "broker_event_at": str(event[6]),
                        "local_received_at": str(event[7]),
                        "delta_quantity": delta_qty,
                        "delta_notional": delta_notional,
                        "incremental_price": delta_notional / delta_qty,
                    }
                )
                previous_qty = raw_qty
                previous_notional = notional
                has_previous = True
                gross_executed = raw_qty
                first_fill_at = first_fill_at or str(event[6])
                last_fill_at = str(event[6])
            try:
                metadata = json.loads(str(target_json)).get("metadata", {})
            except (TypeError, ValueError):
                metadata = {}
            decision_origin = str(metadata.get("decision_origin") or "scheduled")
            role = "immediate_buy" if str(side) == "buy" else "immediate_sell"
            if rebalance_phase is not None:
                role = "deferred_replanned_buy" if str(side) == "buy" else "two_phase_sell"
            if bool(metadata.get("intermediate_risk_reduction")):
                decision_origin = "intermediate_risk_reduction"
            net_owned = _to_float(net_owned_qty)
            latest_quality = qualities[-1] if qualities else "no_raw_execution_event"
            blocking_qualities = sorted(
                {quality for quality in qualities if quality not in valid_fill_qualities | benign_nonfill_qualities}
            )
            reconstruction_eligible = bool(incremental_fills) and not blocking_qualities
            exclusion_reasons = [f"raw_execution_quality_excluded:{quality}" for quality in blocking_qualities]
            if not incremental_fills:
                exclusion_reasons.append("raw_execution_quality_excluded:no_valid_cumulative_vwap")
            result_orders.append(
                {
                    "client_order_id": str(client_order_id),
                    "symbol": str(symbol),
                    "market": str(market),
                    "side": str(side),
                    "status": str(status),
                    "decision_origin": decision_origin,
                    "execution_role": role,
                    "decision_timestamp": str(decision_timestamp),
                    "order_persisted_at": str(persisted_at),
                    "broker_submit_call_started_at": (
                        str(submit_call_started_at) if submit_call_started_at is not None else None
                    ),
                    "broker_submit_call_returned_at": (
                        str(submit_call_returned_at) if submit_call_returned_at is not None else None
                    ),
                    "first_fill_at": first_fill_at,
                    "last_fill_at": last_fill_at,
                    "terminal_at": terminal_at,
                    "expected_order_quantity": expected,
                    "gross_cumulative_executed_quantity": gross_executed,
                    "gross_fill_ratio": gross_executed / expected
                    if gross_executed is not None and expected > 0
                    else None,
                    "net_owned_quantity": net_owned,
                    "net_ownership_ratio": net_owned / expected if expected > 0 else None,
                    "fee_status": (
                        "unknown" if fee_unknown or not raw_events else "observed_event_local_not_aggregated"
                    ),
                    "measurement_quality": latest_quality,
                    "measurement_quality_states": qualities,
                    "comparison_eligibility": {
                        # Only monotonic cumulative observations with a valid
                        # VWAP may establish final gross quantity or VWAP.
                        # Status-only/duplicate events are harmless but do
                        # not themselves create a comparable fill.
                        "final_gross_execution_quantity": reconstruction_eligible,
                        "final_vwap": reconstruction_eligible,
                        "price_only": reconstruction_eligible,
                        # Fees remain event-local, so no total-cost claim is
                        # possible until a future cross-source reconciler is
                        # implemented and independently proven.
                        "total_cost": False,
                    },
                    "comparison_exclusion_reasons": exclusion_reasons,
                    "raw_event_count": len(raw_events),
                    "incremental_fills": incremental_fills,
                }
            )
        return {
            "available": True,
            "orders": result_orders,
            "reference_basis": "decision_close",
            "alignment_status": "not_backtest_aligned",
            "transferability_verdict": "UNKNOWN",
            "asof": _iso_now(),
        }
    finally:
        if owns:
            connection.close()


# --------------------------------------------------------------------------
# expected_vs_actual
# --------------------------------------------------------------------------


def expected_vs_actual(
    order: dict[str, object],
    fills: tuple[dict[str, object], ...] | list[dict[str, object]],
) -> dict[str, object]:
    """Compare one expected order against its actual fills.

    ``order`` is a portfolio-order row (client_order_id, symbol, market,
    side, quantity, reference_price, created_at, status). ``fills`` are
    portfolio-fill rows (quantity, price, fee, occurred_at). Pure function:
    no I/O, deterministic, JSON-serializable.
    """
    client_order_id = str(order.get("client_order_id", ""))
    symbol = str(order.get("symbol", ""))
    market = str(order.get("market", ""))
    side = str(order.get("side", ""))
    expected_quantity = _to_float(order.get("quantity"))
    reference = order.get("reference_price")
    reference_price = _to_float(reference, 0.0) if reference is not None else None
    status = str(order.get("status", ""))
    submitted_at = order.get("created_at")
    actual_quantity = sum(_to_float(fill.get("quantity")) for fill in fills)
    total_value = sum(_to_float(fill.get("quantity")) * _to_float(fill.get("price")) for fill in fills)
    average_fill_price = total_value / actual_quantity if actual_quantity > 0 else None
    fees = sum(_to_float(fill.get("fee")) for fill in fills)
    delay: float | None = None
    if fills and submitted_at:
        delay = fill_delay_seconds(str(fills[0].get("occurred_at") or ""), str(submitted_at))
    price_delta_bps: float | None = None
    if average_fill_price is not None and reference_price:
        price_delta_bps = (average_fill_price - reference_price) / reference_price * _BPS
    adverse_bps: float | None = None
    if average_fill_price is not None and reference_price:
        adverse_bps = slippage_bps(average_fill_price, reference_price, side)
    complete = actual_quantity >= expected_quantity - 1e-12 and expected_quantity > 0
    return {
        "client_order_id": client_order_id,
        "symbol": symbol,
        "market": market,
        "side": side,
        "status": status,
        "expected_quantity": expected_quantity,
        "actual_quantity": round(actual_quantity, 6),
        "fill_ratio": (actual_quantity / expected_quantity) if expected_quantity > 0 else None,
        "expected_price": reference_price,
        "average_fill_price": (round(average_fill_price, 8) if average_fill_price is not None else None),
        "fill_price_delta_bps": (round(price_delta_bps, 4) if price_delta_bps is not None else None),
        "slippage_bps": round(adverse_bps, 4) if adverse_bps is not None else None,
        "fees": round(fees, 6),
        "fill_count": len(fills),
        "fill_delay_seconds": round(delay, 3) if delay is not None else None,
        "filled": len(fills) > 0,
        "complete": complete,
    }


def latest_decision_ids(db_path: str | Path) -> dict[str, str]:
    """Most recent decision_id per market (read-only)."""
    connection = _open_readonly(db_path)
    if connection is None:
        return {}
    try:
        if not _table_exists(connection, "portfolio_decisions"):
            return {}
        rows = _query(
            connection,
            """
            SELECT market, decision_id FROM portfolio_decisions
            WHERE (market, decision_timestamp) IN (
                SELECT market, MAX(decision_timestamp)
                FROM portfolio_decisions GROUP BY market
            )
            """,
        )
        return {str(market): str(decision_id) for market, decision_id in rows}
    finally:
        connection.close()


def decision_expected_vs_actual(
    db_path: str | Path,
    decision_id: str,
) -> dict[str, object]:
    """Expected-vs-actual comparison for every order of one decision."""
    connection = _open_readonly(db_path)
    if connection is None:
        return _empty_state()
    try:
        if not _table_exists(connection, "portfolio_decisions") or not _table_exists(connection, "portfolio_orders"):
            return {
                "available": True,
                "decision_id": decision_id,
                "orders": [],
                "asof": _iso_now(),
            }
        decision = _query(
            connection,
            """
            SELECT decision_timestamp, market, strategy_name, target_json
            FROM portfolio_decisions WHERE decision_id=?
            """,
            (decision_id,),
        )
        order_rows = _query(
            connection,
            """
            SELECT client_order_id, symbol, market, side, quantity, reference_price,
                   status, created_at
            FROM portfolio_orders WHERE decision_id=? ORDER BY created_at
            """,
            (decision_id,),
        )
        comparisons: list[dict[str, object]] = []
        for row in order_rows:
            order = {
                "client_order_id": str(row[0]),
                "symbol": str(row[1]),
                "market": str(row[2]),
                "side": str(row[3]),
                "quantity": float(row[4] or 0.0),
                "reference_price": float(row[5]) if row[5] is not None else None,
                "status": str(row[6]),
                "created_at": str(row[7]),
            }
            fill_rows = _query(
                connection,
                """
                SELECT quantity, price, fee, occurred_at
                FROM portfolio_fills WHERE client_order_id=? ORDER BY occurred_at
                """,
                (str(row[0]),),
            )
            fills = [
                {
                    "quantity": float(item[0] or 0.0),
                    "price": float(item[1] or 0.0),
                    "fee": float(item[2] or 0.0),
                    "occurred_at": str(item[3]),
                }
                for item in fill_rows
            ]
            comparisons.append(expected_vs_actual(order, fills))
        return {
            "available": True,
            "decision_id": decision_id,
            "decision": (
                {
                    "timestamp": str(decision[0][0]),
                    "market": str(decision[0][1]),
                    "strategy": str(decision[0][2]),
                }
                if decision
                else None
            ),
            "orders": comparisons,
            "asof": _iso_now(),
        }
    finally:
        connection.close()


__all__ = [
    "configured_cost_assumptions",
    "cost_calibration",
    "decision_expected_vs_actual",
    "execution_measurement",
    "execution_metrics",
    "expected_vs_actual",
    "fill_delay_seconds",
    "forward_metrics",
    "latest_decision_ids",
    "slippage_bps",
]
