#!/usr/bin/env python3
"""Read-only PAPER soak audit for ``equity_residual_momentum_v3``.

The audit reads the configured settings and the portfolio runtime SQLite
database through SQLite's read-only URI.  It never imports a broker client,
creates a repository, submits an order, writes an observation, or changes a
strategy/risk/execution setting.

The report is JSON so it can be retained as a receipt without changing the
runtime database.  Missing databases, missing tables, and a database with no
v3 observations are reported explicitly as ``no_data`` rather than being
represented as successful zero-valued observations.

Usage:
    python scripts/audit_v3_paper_soak.py [--db PATH] [--config PATH] [--pretty]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import statistics
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

try:
    from parayabuck.config import DEFAULT_DECISION_DB_PATH, load_config  # noqa: E402
    from parayabuck.portfolio import portfolio_strategy_from  # noqa: E402
except ModuleNotFoundError:
    # The audit remains runnable with the standard library when the optional
    # project environment has not been installed yet.  The fallback parser
    # below still reports config identity and marks resolved strategy metadata
    # as unavailable instead of silently inventing it.
    DEFAULT_DECISION_DB_PATH = "data/runtime/parayabuck.sqlite3"
    load_config = None  # type: ignore[assignment]
    portfolio_strategy_from = None  # type: ignore[assignment]


EXPECTED_STRATEGY = "equity_residual_momentum_v3"
EXPECTED_VERSION = "3.0.0"
EXPECTED_MARKET = "equity"
EXPECTED_MODE = "paper"
TERMINAL_ORDER_STATUSES = frozenset({"filled", "fill", "canceled", "cancelled", "rejected", "expired"})
KNOWN_ORDER_STATUSES = TERMINAL_ORDER_STATUSES | frozenset(
    {"submitting", "submitted", "accepted", "partially_filled", "reconciliation_required"}
)
REQUIRED_PORTFOLIO_TABLES = (
    "portfolio_runtime_sessions",
    "portfolio_decisions",
    "portfolio_orders",
    "portfolio_fills",
    "portfolio_rebalance_executions",
    "portfolio_ownership_adjustments",
)


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _resolve_path(value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def _sha256(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError:
        return None
    return digest.hexdigest()


def _json_dict(value: object) -> dict[str, Any] | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _finite_float(value: object) -> float | None:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return number if number == number and abs(number) != float("inf") else None


def _round(value: object, digits: int = 8) -> float | None:
    number = _finite_float(value)
    return round(number, digits) if number is not None else None


def _summary(values: list[float]) -> dict[str, object]:
    if not values:
        return {
            "available": False,
            "count": 0,
            "minimum": None,
            "maximum": None,
            "mean": None,
            "median": None,
            "p95": None,
        }
    ordered = sorted(values)
    return {
        "available": True,
        "count": len(values),
        "minimum": round(ordered[0], 8),
        "maximum": round(ordered[-1], 8),
        "mean": round(statistics.fmean(values), 8),
        "median": round(statistics.median(values), 8),
        "p95": round(ordered[min(len(ordered) - 1, int(0.95 * (len(ordered) - 1)))], 8),
    }


def _table_exists(connection: sqlite3.Connection, table: str) -> bool:
    row = connection.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)).fetchone()
    return row is not None


def _columns(connection: sqlite3.Connection, table: str) -> set[str]:
    if not _table_exists(connection, table):
        return set()
    return {str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})")}


def _open_readonly(path: Path) -> tuple[sqlite3.Connection | None, str | None]:
    if not path.exists():
        return None, "database file does not exist"
    if not path.is_file():
        return None, "database path is not a regular file"
    try:
        connection = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only = ON")
        return connection, None
    except sqlite3.Error as exc:
        return None, f"could not open database read-only: {exc}"


def _fetchall(
    connection: sqlite3.Connection,
    sql: str,
    parameters: tuple[object, ...] = (),
) -> tuple[list[sqlite3.Row], str | None]:
    try:
        return list(connection.execute(sql, parameters).fetchall()), None
    except sqlite3.Error as exc:
        return [], str(exc)


def _parse_scalar(value: str) -> object:
    value = value.strip()
    if not value:
        return None
    if value.startswith("[") and value.endswith("]"):
        items = value[1:-1].strip()
        return [] if not items else [_parse_scalar(item) for item in items.split(",")]
    if value.lower() in {"true", "false"}:
        return value.lower() == "true"
    if value.lower() in {"null", "none", "~"}:
        return None
    if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
        return value[1:-1]
    try:
        return int(value)
    except ValueError:
        try:
            return float(value)
        except ValueError:
            return value


def _fallback_yaml_mapping(path: Path) -> dict[str, object]:
    """Parse the scalar/nested config fields needed by this read-only audit.

    PyYAML is used whenever the project environment provides it.  This small
    fallback is deliberately limited to the repository's indentation-based
    settings shape; it is only a dependency-free diagnostic fallback.
    """
    root: dict[str, object] = {}
    stack: list[tuple[int, dict[str, object]]] = [(-1, root)]
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return root
    for line in lines:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip(" "))
        content = line.strip()
        if " #" in content:
            content = content.split(" #", 1)[0].rstrip()
        if ":" not in content:
            continue
        key, raw_value = content.split(":", 1)
        key = key.strip()
        raw_value = raw_value.strip()
        while stack[-1][0] >= indent:
            stack.pop()
        parent = stack[-1][1]
        if raw_value:
            parent[key] = _parse_scalar(raw_value)
        else:
            child: dict[str, object] = {}
            parent[key] = child
            stack.append((indent, child))
    return root


def _fallback_config(raw: dict[str, object]) -> object:
    runtime_raw = raw.get("runtime", {})
    live_raw = raw.get("live", {})
    strategy_raw = raw.get("strategy", {})
    risk_raw = raw.get("risk", {})
    runtime = runtime_raw if isinstance(runtime_raw, dict) else {}
    live = live_raw if isinstance(live_raw, dict) else {}
    strategies = strategy_raw if isinstance(strategy_raw, dict) else {}
    risk = risk_raw if isinstance(risk_raw, dict) else {}

    def market_config(name: str) -> SimpleNamespace:
        value = strategies.get(name, {})
        value = value if isinstance(value, dict) else {}
        parameters = value.get("parameters", {})
        return SimpleNamespace(
            enabled=bool(value.get("enabled", False)),
            name=str(value.get("name", "")),
            maximum_allocation=float(value.get("maximum_allocation", 0.0) or 0.0),
            timeframe=value.get("timeframe"),
            parameters=parameters if isinstance(parameters, dict) else {},
        )

    return SimpleNamespace(
        runtime=SimpleNamespace(
            mode=SimpleNamespace(value=str(runtime.get("mode", "paper"))),
            execution_timeframe=str(runtime.get("execution_timeframe", "5m")),
            market_state_timeframe=str(runtime.get("market_state_timeframe", "5m")),
            decision_db_path=str(runtime.get("decision_db_path", DEFAULT_DECISION_DB_PATH)),
        ),
        live=SimpleNamespace(enabled=bool(live.get("enabled", False))),
        strategy=SimpleNamespace(equity=market_config("equity"), crypto=market_config("crypto")),
        risk=SimpleNamespace(
            max_position_pct=float(risk.get("max_position_pct", 0.10) or 0.10),
            max_gross_exposure_pct=float(risk.get("max_gross_exposure_pct", 0.60) or 0.60),
        ),
    )


def _fill_delay_seconds(occurred_at: str, submitted_at: str | None) -> float | None:
    if not submitted_at:
        return None
    try:
        occurred = datetime.fromisoformat(occurred_at)
        submitted = datetime.fromisoformat(submitted_at)
    except (TypeError, ValueError):
        return None
    delay = (occurred - submitted).total_seconds()
    return delay if delay >= 0 else None


def _adverse_slippage_bps(fill_price: float | None, reference_price: float | None, side: str) -> float | None:
    if fill_price is None or reference_price is None or fill_price <= 0 or reference_price <= 0:
        return None
    sign = {"buy": 1.0, "long": 1.0, "sell": -1.0, "short": -1.0}.get(side.lower())
    if sign is None:
        return None
    return (fill_price - reference_price) / reference_price * 10_000.0 * sign


def _expected_vs_actual(order: dict[str, object], fills: list[dict[str, object]]) -> dict[str, object]:
    expected_quantity = _finite_float(order.get("quantity")) or 0.0
    reference_price = _finite_float(order.get("reference_price"))
    actual_quantity = sum(_finite_float(fill.get("quantity")) or 0.0 for fill in fills)
    total_value = sum(
        (_finite_float(fill.get("quantity")) or 0.0) * (_finite_float(fill.get("price")) or 0.0) for fill in fills
    )
    average_price = total_value / actual_quantity if actual_quantity > 0 else None
    fees = sum(_finite_float(fill.get("fee")) or 0.0 for fill in fills)
    delay = _fill_delay_seconds(str(fills[0].get("occurred_at", "")), str(order.get("created_at"))) if fills else None
    adverse_bps = _adverse_slippage_bps(average_price, reference_price, str(order.get("side", "")))
    return {
        "actual_quantity": actual_quantity,
        "fill_ratio": actual_quantity / expected_quantity if expected_quantity > 0 else None,
        "slippage_bps": adverse_bps,
        "fill_delay_seconds": delay,
        "fees": fees,
    }


def _strategy_identity(config: object, market: str) -> dict[str, object]:
    market_config = getattr(getattr(config, "strategy", None), market, None)
    if market_config is None:
        return {"available": False, "reason": f"strategy.{market} is unavailable"}
    result: dict[str, object] = {
        "available": True,
        "enabled": bool(market_config.enabled),
        "configured_name": str(market_config.name),
        "maximum_allocation": _round(market_config.maximum_allocation),
        "configured_timeframe": market_config.timeframe,
        "parameters": dict(market_config.parameters),
    }
    try:
        if portfolio_strategy_from is None:
            raise RuntimeError("project dependencies are unavailable; resolved identity not loaded")
        strategy = portfolio_strategy_from(
            market_config.name,
            dict(market_config.parameters),
            timeframe=market_config.timeframe,
        )
        result["resolved"] = strategy.spec().to_dict()
    except Exception as exc:  # configuration errors belong in the report
        result["resolved"] = None
        result["resolve_error"] = f"{type(exc).__name__}: {exc}"
    return result


def _configuration_report(config_path: Path) -> tuple[dict[str, object], object | None]:
    report: dict[str, object] = {
        "path": str(config_path),
        "file_sha256": _sha256(config_path),
        "available": False,
        "runtime": None,
        "live": None,
        "strategies": {},
        "preflight": {
            "runtime_mode": "unknown",
            "live_enabled": "unknown",
            "live_environment_armed": os.getenv("PARAYABUCK_ENABLE_LIVE") == "YES",
            "equity_v3_selected": "unknown",
            "paper_only": "unknown",
        },
    }
    if load_config is None:
        config = _fallback_config(_fallback_yaml_mapping(config_path))
        report["dependency_fallback"] = True
    else:
        try:
            config = load_config(str(config_path))
        except Exception as exc:
            report["error"] = f"{type(exc).__name__}: {exc}"
            return report, None

    runtime_mode = str(config.runtime.mode.value)
    live_enabled = bool(config.live.enabled)
    live_environment_armed = os.getenv("PARAYABUCK_ENABLE_LIVE") == "YES"
    paper_only = runtime_mode == EXPECTED_MODE and not live_enabled and not live_environment_armed
    report.update(
        {
            "available": True,
            "runtime": {
                "mode": runtime_mode,
                "execution_timeframe": config.runtime.execution_timeframe,
                "market_state_timeframe": config.runtime.market_state_timeframe,
                "decision_db_path": config.runtime.decision_db_path,
            },
            "live": {
                "enabled": live_enabled,
                "environment_armed": live_environment_armed,
            },
            "strategies": {
                "equity": _strategy_identity(config, "equity"),
                "crypto": _strategy_identity(config, "crypto"),
            },
            "preflight": {
                "runtime_mode": "pass" if runtime_mode == EXPECTED_MODE else "fail",
                "live_enabled": "pass" if not live_enabled else "fail",
                "live_environment_armed": "fail" if live_environment_armed else "pass",
                "equity_v3_selected": ("pass" if config.strategy.equity.name == EXPECTED_STRATEGY else "fail"),
                "paper_only": "pass" if paper_only else "fail",
            },
        }
    )
    return report, config


def _expected_frozen_semantics() -> dict[str, object]:
    return {
        "strategy_name": EXPECTED_STRATEGY,
        "strategy_version": EXPECTED_VERSION,
        "market": EXPECTED_MARKET,
        "signal_timeframe": "1d",
        "rebalance_cadence": "weekly",
        "positive_12m_gate_before_consensus": True,
        "consensus": "equal mean of percentile ranks of raw 6-1, 9-1, and 12-1 residual returns",
        "consensus_horizons_residual_bars": [126, 189, 252],
        "skip_bars": 21,
        "ivol_reject_quantile": 0.70,
        "top_n": 8,
        "max_single_weight": 0.10,
        "risk_execution_universe": "inherited unchanged from equity_residual_momentum_v2",
        "crypto_touched": False,
        "live_allowed": False,
    }


def _target_weights(target: dict[str, Any]) -> tuple[dict[str, float], list[str]]:
    raw_weights = target.get("weights")
    if not isinstance(raw_weights, list):
        return {}, ["target weights are missing or not a list"]
    weights: dict[str, float] = {}
    errors: list[str] = []
    for item in raw_weights:
        if not isinstance(item, dict):
            errors.append("target contains a non-object weight")
            continue
        symbol = str(item.get("symbol", "")).strip()
        value = _finite_float(item.get("weight"))
        if not symbol:
            errors.append("target contains a weight without a symbol")
        elif value is None or value < 0:
            errors.append(f"target weight is invalid for {symbol or '<missing>'}")
        elif symbol in weights:
            errors.append(f"target contains duplicate symbol {symbol}")
        else:
            weights[symbol] = value
    return weights, errors


def _order_metrics(
    connection: sqlite3.Connection,
    decision_id: str,
    orders_available: bool,
    fills_available: bool,
) -> tuple[dict[str, object], list[str], list[dict[str, object]]]:
    if not orders_available:
        return (
            {
                "available": False,
                "count": None,
                "status_counts": None,
                "orders": None,
                "filled_quantity": None,
                "fees": None,
                "fill_count": None,
            },
            ["portfolio_orders table is unavailable"],
            [],
        )
    rows, error = _fetchall(
        connection,
        """
        SELECT client_order_id, symbol, market, side, quantity, reference_price,
               status, created_at
        FROM portfolio_orders WHERE decision_id = ? ORDER BY created_at, client_order_id
        """,
        (decision_id,),
    )
    if error is not None:
        return (
            {"available": False, "count": None, "error": error},
            [f"portfolio order query failed: {error}"],
            [],
        )

    status_counts = Counter(str(row["status"]) for row in rows)
    anomalies: list[str] = []
    compact_orders: list[dict[str, object]] = []
    total_filled_quantity = 0.0
    total_fees = 0.0
    total_fills = 0
    slippage_values: list[float] = []
    delay_values: list[float] = []
    for row in rows:
        order = {
            "client_order_id": str(row["client_order_id"]),
            "symbol": str(row["symbol"]),
            "market": str(row["market"]),
            "side": str(row["side"]),
            "quantity": _finite_float(row["quantity"]) or 0.0,
            "reference_price": _finite_float(row["reference_price"]),
            "status": str(row["status"]),
            "created_at": str(row["created_at"]),
        }
        fill_rows: list[sqlite3.Row] = []
        if fills_available:
            fill_rows, fill_error = _fetchall(
                connection,
                """
                SELECT quantity, price, fee, occurred_at
                FROM portfolio_fills WHERE client_order_id = ? ORDER BY occurred_at, activity_id
                """,
                (order["client_order_id"],),
            )
            if fill_error is not None:
                anomalies.append(f"fill query failed for {order['client_order_id']}: {fill_error}")
        fills = [
            {
                "quantity": _finite_float(fill["quantity"]) or 0.0,
                "price": _finite_float(fill["price"]) or 0.0,
                "fee": _finite_float(fill["fee"]) or 0.0,
                "occurred_at": str(fill["occurred_at"]),
            }
            for fill in fill_rows
        ]
        comparison = _expected_vs_actual(order, fills)
        actual_quantity = _finite_float(comparison.get("actual_quantity")) or 0.0
        total_filled_quantity += actual_quantity
        total_fees += _finite_float(comparison.get("fees")) or 0.0
        total_fills += len(fills)
        slippage = _finite_float(comparison.get("slippage_bps"))
        delay = _finite_float(comparison.get("fill_delay_seconds"))
        if slippage is not None:
            slippage_values.append(slippage)
        if delay is not None:
            delay_values.append(delay)
        status = str(order["status"])
        if status not in KNOWN_ORDER_STATUSES:
            anomalies.append(f"unknown order status {status!r} for {order['client_order_id']}")
        if fills_available and status in {"filled", "fill"} and not fills:
            anomalies.append(f"filled order has no persisted fill: {order['client_order_id']}")
        if actual_quantity > (float(order["quantity"]) + 1e-9):
            anomalies.append(f"order overfill: {order['client_order_id']}")
        if status == "reconciliation_required":
            anomalies.append(f"order requires reconciliation: {order['client_order_id']}")
        compact_orders.append(
            {
                "client_order_id": order["client_order_id"],
                "symbol": order["symbol"],
                "side": order["side"],
                "status": status,
                "expected_quantity": _round(order["quantity"]),
                "actual_quantity": _round(actual_quantity),
                "fill_ratio": _round(comparison.get("fill_ratio")),
                "fill_count": len(fills) if fills_available else None,
                "slippage_bps": _round(slippage),
                "fill_delay_seconds": _round(delay),
                "fees": _round(comparison.get("fees"), 6),
            }
        )

    return (
        {
            "available": True,
            "count": len(rows),
            "status_counts": dict(sorted(status_counts.items())),
            "orders": compact_orders,
            "filled_quantity": _round(total_filled_quantity),
            "fees": _round(total_fees, 6),
            "fill_count": total_fills if fills_available else None,
            "slippage_bps": _summary(slippage_values) if fills_available else None,
            "fill_delay_seconds": _summary(delay_values) if fills_available else None,
        },
        anomalies,
        compact_orders,
    )


def _scoped_rows(
    connection: sqlite3.Connection,
    table: str,
    account_ids: set[str],
    columns: str,
    order_column: str,
) -> tuple[dict[str, object], list[dict[str, object]]]:
    if not _table_exists(connection, table):
        return {"available": False, "reason": f"{table} table is unavailable"}, []
    if not account_ids:
        return {"available": False, "reason": "no v3 account/session identity is available"}, []
    placeholders = ", ".join("?" for _ in account_ids)
    rows, error = _fetchall(
        connection,
        (f"SELECT {columns} FROM {table} WHERE account_id IN ({placeholders}) ORDER BY {order_column}"),
        tuple(sorted(account_ids)),
    )
    if error is not None:
        return {"available": False, "error": error}, []
    return {"available": True, "count": len(rows)}, [dict(row) for row in rows]


def _audit_database(db_path: Path, config: object | None) -> dict[str, object]:
    report: dict[str, object] = {
        "path": str(db_path),
        "exists": db_path.exists(),
        "read_only": True,
        "query_only": None,
        "available": False,
        "tables": {},
        "observations": {
            "status": "no_data",
            "strategy": EXPECTED_STRATEGY,
            "market": EXPECTED_MARKET,
            "count": 0,
            "valid_count": 0,
            "reason": None,
        },
        "identity": {},
        "rebalances": [],
        "aggregates": {},
        "exposure": {},
        "anomalies": {"available": False, "count": None, "by_type": None, "items": None},
    }
    connection, error = _open_readonly(db_path)
    if connection is None:
        report["reason"] = error
        report["observations"] = {
            **report["observations"],
            "reason": "runtime database is unavailable; no PAPER observation was read",
        }
        return report

    try:
        report["available"] = True
        report["query_only"] = bool(connection.execute("PRAGMA query_only").fetchone()[0])
        table_names = {
            str(row[0])
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name")
        }
        report["tables"] = {
            table: {"present": table in table_names, "columns": sorted(_columns(connection, table))}
            for table in REQUIRED_PORTFOLIO_TABLES
        }
        decisions_present = "portfolio_decisions" in table_names
        if not decisions_present:
            report["observations"] = {
                **report["observations"],
                "reason": "portfolio_decisions table is unavailable; no v3 observation was read",
            }
            return report

        decision_columns = _columns(connection, "portfolio_decisions")
        session_columns = _columns(connection, "portfolio_runtime_sessions")
        required_decision_columns = {
            "decision_id",
            "session_id",
            "configuration_digest",
            "decision_timestamp",
            "market",
            "strategy_name",
            "target_json",
            "risk_json",
        }
        if not required_decision_columns.issubset(decision_columns):
            report["observations"] = {
                **report["observations"],
                "reason": "portfolio_decisions schema is incomplete; no v3 observation was read",
            }
            report["reason"] = "required portfolio_decisions columns are missing"
            return report

        decision_account = "d.account_id" if "account_id" in decision_columns else "''"
        session_account = "s.account_id" if "account_id" in session_columns else "''"
        session_digest = "s.configuration_digest" if "configuration_digest" in session_columns else "''"
        session_mode = "s.mode" if "mode" in session_columns else "''"
        session_status = "s.status" if "status" in session_columns else "''"
        session_join = (
            "LEFT JOIN portfolio_runtime_sessions s ON s.session_id = d.session_id"
            if "portfolio_runtime_sessions" in table_names
            else ""
        )
        rows, query_error = _fetchall(
            connection,
            f"""
            SELECT d.decision_id, d.session_id, {decision_account} AS decision_account_id,
                   d.configuration_digest, d.decision_timestamp, d.market,
                   d.strategy_name, d.target_json, d.risk_json,
                   {session_account} AS session_account_id,
                   {session_digest} AS session_configuration_digest,
                   {session_mode} AS session_mode,
                   {session_status} AS session_status
            FROM portfolio_decisions d
            {session_join}
            WHERE d.market = ? AND d.strategy_name = ?
            ORDER BY d.decision_timestamp, d.decision_id
            """,
            (EXPECTED_MARKET, EXPECTED_STRATEGY),
        )
        if query_error is not None:
            report["reason"] = f"v3 decision query failed: {query_error}"
            report["observations"] = {
                **report["observations"],
                "reason": "v3 decision query failed; no observation was interpreted",
            }
            return report

        orders_available = "portfolio_orders" in table_names
        fills_available = "portfolio_fills" in table_names
        lifecycle_available = "portfolio_rebalance_executions" in table_names
        previous_weights: dict[str, float] | None = None
        previous_universe: str | None = None
        rebalances: list[dict[str, object]] = []
        anomaly_items: list[dict[str, object]] = []
        turnover_values: list[float] = []
        invested_values: list[float] = []
        cash_values: list[float] = []
        position_count_values: list[float] = []
        max_weight_values: list[float] = []
        account_ids: set[str] = set()
        config_digests: set[str] = set()
        universe_versions: set[str] = set()
        universe_snapshots: set[str] = set()
        strategy_versions: set[str] = set()
        observed_modes: set[str] = set()
        identity_target_names: set[str] = set()
        duplicate_keys: Counter[tuple[str, str, str, str]] = Counter()
        total_orders = 0
        total_fills = 0
        total_fees = 0.0
        total_filled_quantity = 0.0
        risk_halted_count = 0
        risk_reasons: Counter[str] = Counter()
        blocked_reasons: Counter[str] = Counter()
        signal_verification_blocked_count = 0
        risk_control_blocked_count = 0
        risk_gross_after_values: list[float] = []
        unowned_fill_rows: list[dict[str, object]] = []
        unowned_fill_error: str | None = None

        if fills_available:
            raw_unowned_fills, unowned_fill_error = _fetchall(
                connection,
                """
                SELECT f.activity_id, f.client_order_id, f.occurred_at, f.quantity
                FROM portfolio_fills f
                LEFT JOIN portfolio_orders o ON o.client_order_id = f.client_order_id
                WHERE o.client_order_id IS NULL
                ORDER BY f.occurred_at, f.activity_id
                """,
            )
            unowned_fill_rows = [
                {
                    "activity_id": str(row["activity_id"]),
                    "client_order_id": str(row["client_order_id"]),
                    "occurred_at": str(row["occurred_at"]),
                    "quantity": _round(row["quantity"]),
                }
                for row in raw_unowned_fills
            ]
            if unowned_fill_error is not None:
                anomaly_items.append(
                    {
                        "decision_id": None,
                        "timestamp": None,
                        "types": [f"unowned fill query failed: {unowned_fill_error}"],
                    }
                )
            elif unowned_fill_rows:
                anomaly_items.append(
                    {
                        "decision_id": None,
                        "timestamp": unowned_fill_rows[0]["occurred_at"],
                        "types": ["unowned fill"],
                        "count": len(unowned_fill_rows),
                    }
                )

        for row in rows:
            decision_id = str(row["decision_id"])
            account_id = str(row["decision_account_id"] or row["session_account_id"] or "")
            digest = str(row["configuration_digest"] or "")
            timestamp = str(row["decision_timestamp"])
            session_mode_value = str(row["session_mode"] or "")
            account_ids.add(account_id) if account_id else None
            config_digests.add(digest) if digest else None
            observed_modes.add(session_mode_value) if session_mode_value else None
            duplicate_keys[(account_id, digest, timestamp, str(row["market"]))] += 1

            target = _json_dict(row["target_json"])
            risk = _json_dict(row["risk_json"])
            decision_anomalies: list[str] = []
            if target is None:
                decision_anomalies.append("invalid target_json")
                target = {}
            if risk is None:
                decision_anomalies.append("invalid risk_json")
                risk = {}

            target_name = str(target.get("strategy_name", ""))
            target_version = str(target.get("strategy_version", ""))
            target_market = str(target.get("market", EXPECTED_MARKET))
            identity_target_names.add(target_name) if target_name else None
            strategy_versions.add(target_version) if target_version else None
            if target_name != EXPECTED_STRATEGY:
                decision_anomalies.append("target strategy_name does not match v3")
            if target_version != EXPECTED_VERSION:
                decision_anomalies.append("target strategy_version does not match v3")
            if target_market != EXPECTED_MARKET:
                decision_anomalies.append("target market does not match equity")
            if not row["session_id"] or not row["session_mode"]:
                decision_anomalies.append("decision session identity is missing")
            if session_mode_value and session_mode_value != EXPECTED_MODE:
                decision_anomalies.append("session mode is not paper")
            if row["session_configuration_digest"] and str(row["session_configuration_digest"]) != digest:
                decision_anomalies.append("session and decision configuration digests differ")

            weights, weight_errors = _target_weights(target)
            decision_anomalies.extend(weight_errors)
            invested = sum(weights.values()) if weights or not weight_errors else None
            cash_weight = _finite_float(target.get("cash_weight"))
            max_weight = max(weights.values()) if weights else None
            if invested is not None:
                invested_values.append(invested)
            if cash_weight is not None:
                cash_values.append(cash_weight)
            position_count_values.append(float(len(weights)))
            if max_weight is not None:
                max_weight_values.append(max_weight)
            if invested is not None and cash_weight is not None and invested + cash_weight > 1.000001:
                decision_anomalies.append("target invested plus cash weights exceed 1")
            if config is not None and invested is not None:
                configured_equity = config.strategy.equity
                if invested > configured_equity.maximum_allocation + 1e-6:
                    decision_anomalies.append("target invested weight exceeds configured sleeve allocation")
                if max_weight is not None and max_weight > config.risk.max_position_pct + 1e-6:
                    decision_anomalies.append("target symbol weight exceeds configured position cap")

            universe_version = str(target.get("universe_version", ""))
            universe_snapshot = str(target.get("universe_snapshot_id", ""))
            if universe_version:
                universe_versions.add(universe_version)
            if universe_snapshot:
                universe_snapshots.add(universe_snapshot)
            turnover_for_row: float | None = None
            if previous_weights is not None and invested is not None:
                turnover_for_row = (
                    sum(
                        abs(previous_weights.get(symbol, 0.0) - weights.get(symbol, 0.0))
                        for symbol in set(previous_weights) | set(weights)
                    )
                    / 2.0
                )
                turnover_values.append(turnover_for_row)
            universe_changed = previous_universe is not None and universe_version != previous_universe

            order_report, order_anomalies, compact_orders = _order_metrics(
                connection, decision_id, orders_available, fills_available
            )
            decision_anomalies.extend(order_anomalies)
            total_orders += int(order_report.get("count") or 0)
            total_fills += int(order_report.get("fill_count") or 0)
            total_fees += _finite_float(order_report.get("fees")) or 0.0
            total_filled_quantity += _finite_float(order_report.get("filled_quantity")) or 0.0

            lifecycle: dict[str, object] = {"available": lifecycle_available, "recorded": False}
            if lifecycle_available:
                lifecycle_rows, lifecycle_error = _fetchall(
                    connection,
                    """
                    SELECT phase, status, updated_at FROM portfolio_rebalance_executions
                    WHERE decision_id = ?
                    """,
                    (decision_id,),
                )
                if lifecycle_error is not None:
                    lifecycle["error"] = lifecycle_error
                    decision_anomalies.append(f"rebalance lifecycle query failed: {lifecycle_error}")
                elif lifecycle_rows:
                    lifecycle_row = lifecycle_rows[-1]
                    lifecycle.update(
                        {
                            "recorded": True,
                            "phase": str(lifecycle_row["phase"]),
                            "status": str(lifecycle_row["status"]),
                            "updated_at": str(lifecycle_row["updated_at"]),
                        }
                    )
                    lifecycle_complete = str(lifecycle_row["phase"]) == "complete" and str(
                        lifecycle_row["status"]
                    ) not in {"blocked", "reconciliation_required"}
                    if not lifecycle_complete:
                        decision_anomalies.append("rebalance lifecycle is not complete")
                elif compact_orders:
                    decision_anomalies.append("orders exist but rebalance lifecycle is missing")

            risk_halted = risk.get("halted") if isinstance(risk.get("halted"), bool) else None
            reasons = risk.get("reasons") if isinstance(risk.get("reasons"), (list, tuple)) else []
            risk_reasons_for_row = [str(reason) for reason in reasons]
            if risk_halted:
                risk_halted_count += 1
                risk_reasons.update(risk_reasons_for_row)
                risk_control_blocked_count += 1
            risk_gross_before = _finite_float(risk.get("gross_before"))
            risk_gross_after = _finite_float(risk.get("gross_after"))
            if risk_gross_after is not None:
                risk_gross_after_values.append(risk_gross_after)
            if (
                config is not None
                and risk_gross_after is not None
                and risk_gross_after > config.risk.max_gross_exposure_pct + 1e-6
            ):
                decision_anomalies.append("risk gross_after exceeds configured gross cap")

            metadata = target.get("metadata")
            observability = metadata.get("paper_observability") if isinstance(metadata, dict) else None
            blocked_reason = (
                str(observability.get("blocked_or_skipped_reason", "")).strip()
                if isinstance(observability, dict)
                else ""
            )
            if not blocked_reason:
                blocked_reason = str(target.get("risk_off_detail", "")).strip()
            if blocked_reason:
                for reason in (part.strip() for part in blocked_reason.split("|")):
                    if reason:
                        blocked_reasons[reason] += 1
                lowered_reason = blocked_reason.lower()
                if "signal" in lowered_reason or "verif" in lowered_reason:
                    signal_verification_blocked_count += 1
                if any(token in lowered_reason for token in ("risk", "loss", "drawdown")):
                    risk_control_blocked_count += 1

            if decision_anomalies:
                anomaly_items.append(
                    {
                        "decision_id": decision_id,
                        "timestamp": timestamp,
                        "types": sorted(set(decision_anomalies)),
                    }
                )

            rebalances.append(
                {
                    "decision_id": decision_id,
                    "timestamp": timestamp,
                    "account_id": account_id or None,
                    "configuration_digest": digest or None,
                    "session_id": str(row["session_id"] or "") or None,
                    "session_mode": session_mode_value or None,
                    "session_status": str(row["session_status"] or "") or None,
                    "identity": {
                        "decision_strategy_name": str(row["strategy_name"]),
                        "target_strategy_name": target_name or None,
                        "target_strategy_version": target_version or None,
                        "market": str(row["market"]),
                        "universe_version": universe_version or None,
                        "universe_snapshot_id": universe_snapshot or None,
                        "data_timestamp": target.get("data_timestamp"),
                    },
                    "exposure": {
                        "target_invested_weight": _round(invested),
                        "target_cash_weight": _round(cash_weight),
                        "target_position_count": len(weights),
                        "target_max_symbol_weight": _round(max_weight),
                        "risk_gross_before": _round(risk_gross_before),
                        "risk_gross_after": _round(risk_gross_after),
                    },
                    "turnover": {
                        "one_way_weight": _round(turnover_for_row),
                        "universe_changed": universe_changed,
                    },
                    "risk": {
                        "halted": risk_halted,
                        "reasons": risk_reasons_for_row,
                    },
                    "orders": order_report,
                    "rebalance_lifecycle": lifecycle,
                }
            )
            previous_weights = weights if not weight_errors else None
            previous_universe = universe_version or None

        for key, count in duplicate_keys.items():
            if count > 1:
                anomaly_items.append(
                    {
                        "decision_id": None,
                        "timestamp": key[2],
                        "types": ["duplicate v3 decision identity"],
                        "count": count,
                    }
                )

        valid_count = len(rows) - sum(1 for item in anomaly_items if item.get("decision_id"))
        observation_status = "observed" if valid_count > 0 else "no_data"
        if rows and valid_count < len(rows):
            observation_status = "partial"
        report["observations"] = {
            "status": observation_status,
            "strategy": EXPECTED_STRATEGY,
            "market": EXPECTED_MARKET,
            "count": len(rows),
            "valid_count": max(0, valid_count),
            "first_timestamp": str(rows[0]["decision_timestamp"]) if rows else None,
            "last_timestamp": str(rows[-1]["decision_timestamp"]) if rows else None,
            "reason": (None if rows else "no equity_residual_momentum_v3 decisions in portfolio_decisions"),
        }
        report["identity"] = {
            "expected": {
                "strategy_name": EXPECTED_STRATEGY,
                "strategy_version": EXPECTED_VERSION,
                "market": EXPECTED_MARKET,
                "mode": EXPECTED_MODE,
            },
            "observed": {
                "decision_strategy_names": [EXPECTED_STRATEGY] if rows else [],
                "target_strategy_names": sorted(identity_target_names),
                "target_strategy_versions": sorted(strategy_versions),
                "configuration_digests": sorted(config_digests),
                "account_ids": sorted(account_ids),
                "session_modes": sorted(observed_modes),
                "universe_versions": sorted(universe_versions),
                "universe_snapshot_ids": sorted(universe_snapshots),
            },
            "configuration_digest_count": len(config_digests),
            "identity_stable": (bool(rows) and len(config_digests) <= 1 and strategy_versions == {EXPECTED_VERSION}),
        }
        report["rebalances"] = rebalances
        report["aggregates"] = {
            "successful_rebalances": max(0, len(rows) - len(anomaly_items)),
            "blocked_decisions": {
                "signal_verification": signal_verification_blocked_count,
                "risk_controls": risk_control_blocked_count,
                "reasons": dict(sorted(blocked_reasons.items())),
            },
            "target_turnover_one_way_weight": _summary(turnover_values),
            "target_invested_weight": _summary(invested_values),
            "target_cash_weight": _summary(cash_values),
            "target_position_count": _summary(position_count_values),
            "target_max_symbol_weight": _summary(max_weight_values),
            "orders": {
                "available": orders_available,
                "count": total_orders if orders_available else None,
                "fills": total_fills if fills_available else None,
                "filled_quantity": _round(total_filled_quantity) if orders_available else None,
                "fees": _round(total_fees, 6) if fills_available else None,
            },
            "risk": {
                "halted_decisions": risk_halted_count,
                "halt_reasons": dict(sorted(risk_reasons.items())),
                "gross_after": _summary(risk_gross_after_values),
            },
            "broker_api_failures": {
                "available": False,
                "count": None,
                "reason": ("transient broker API failures are not persisted in the portfolio runtime schema"),
            },
        }

        scoped_adjustments, adjustment_rows = _scoped_rows(
            connection,
            "portfolio_ownership_adjustments",
            account_ids,
            ("adjustment_id, account_id, symbol, market, quantity, kind, note, created_at, consumed_quantity"),
            "created_at",
        )
        scoped_incidents, incident_rows = _scoped_rows(
            connection,
            "safety_incidents",
            account_ids,
            "id, account_id, reason, scope, activated_at, updated_at",
            "updated_at",
        )
        scoped_risk_state, risk_state_rows = _scoped_rows(
            connection,
            "risk_state",
            account_ids,
            "account_id, session_date, payload, updated_at",
            "updated_at",
        )
        risk_state_items: list[dict[str, object]] = []
        for item in risk_state_rows:
            payload = _json_dict(str(item.get("payload", ""))) or {}
            risk_state_items.append(
                {
                    "account_id": item.get("account_id"),
                    "session_date": item.get("session_date"),
                    "updated_at": item.get("updated_at"),
                    "day_start_equity": _round(payload.get("day_start_equity"), 6),
                    "peak_equity": _round(payload.get("peak_equity"), 6),
                    "realized_pnl": _round(payload.get("realized_pnl"), 6),
                    "unrealized_pnl": _round(payload.get("unrealized_pnl"), 6),
                    "fees": _round(payload.get("fees"), 6),
                    "circuit_breaker_reason": payload.get("circuit_breaker_reason"),
                }
            )
            if payload.get("circuit_breaker_reason"):
                anomaly_items.append(
                    {
                        "decision_id": None,
                        "timestamp": str(item.get("updated_at", "")),
                        "types": ["risk state has an active circuit breaker reason"],
                        "account_id": item.get("account_id"),
                    }
                )
        report["anomalies"] = {
            "available": True,
            "count": len(anomaly_items) + len(adjustment_rows) + len(incident_rows),
            "by_type": dict(
                sorted(Counter(item_type for item in anomaly_items for item_type in item.get("types", [])).items())
            ),
            "items": anomaly_items,
            "ownership_adjustments": {
                **scoped_adjustments,
                "items": adjustment_rows,
            },
            "safety_incidents": {
                **scoped_incidents,
                "items": incident_rows,
            },
            "risk_state": {
                **scoped_risk_state,
                "items": risk_state_items,
            },
            "unowned_fills": {
                "available": fills_available and unowned_fill_error is None,
                "count": len(unowned_fill_rows) if fills_available and unowned_fill_error is None else None,
                "items": unowned_fill_rows if fills_available and unowned_fill_error is None else None,
                "reason": unowned_fill_error,
            },
        }

        net_fills: Counter[str] = Counter()
        if fills_available and rows:
            fill_scope_rows, fill_scope_error = _fetchall(
                connection,
                """
                SELECT o.symbol, o.side, f.quantity
                FROM portfolio_fills f
                JOIN portfolio_orders o ON o.client_order_id = f.client_order_id
                JOIN portfolio_decisions d ON d.decision_id = o.decision_id
                WHERE d.market = ? AND d.strategy_name = ?
                """,
                (EXPECTED_MARKET, EXPECTED_STRATEGY),
            )
            if fill_scope_error is None:
                for fill_row in fill_scope_rows:
                    quantity = _finite_float(fill_row["quantity"]) or 0.0
                    sign = 1.0 if str(fill_row["side"]).lower() == "buy" else -1.0
                    net_fills[str(fill_row["symbol"])] += sign * quantity
        report["exposure"] = {
            "target": {
                "available": bool(rows),
                "invested_weight": _summary(invested_values),
                "cash_weight": _summary(cash_values),
                "max_symbol_weight": _summary(max_weight_values),
                "position_count": _summary(position_count_values),
            },
            "ledger_net_filled_quantity": {
                "available": bool(net_fills),
                "basis": ("net portfolio_fills for v3 decisions; not a broker snapshot or market-value exposure"),
                "by_symbol": {
                    symbol: round(quantity, 8)
                    for symbol, quantity in sorted(net_fills.items())
                    if abs(quantity) > 1e-12
                },
            },
            "actual_market_value": {
                "available": False,
                "value": None,
                "reason": ("portfolio runtime schema does not persist a v3-linked broker mark-to-market snapshot"),
            },
            "risk_state": {
                **scoped_risk_state,
                "items": risk_state_items,
            },
        }
        if not rows:
            report["aggregates"]["orders"] = {
                "available": False,
                "count": None,
                "fills": None,
                "filled_quantity": None,
                "fees": None,
                "reason": "no v3 rebalance observations",
            }
            report["aggregates"]["risk"] = {
                "available": False,
                "halted_decisions": None,
                "halt_reasons": None,
                "reason": "no v3 rebalance observations",
            }
            report["anomalies"] = {
                "available": False,
                "count": None,
                "by_type": None,
                "items": None,
                "reason": "no v3 account/session identity is available",
            }
        return report
    finally:
        connection.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=None, help="runtime SQLite path (default: config)")
    parser.add_argument(
        "--config",
        default=str(ROOT / "config" / "settings.yaml"),
        help="settings.yaml path",
    )
    parser.add_argument("--pretty", action="store_true", help="pretty-print JSON")
    args = parser.parse_args()

    config_path = _resolve_path(args.config)
    configuration, config = _configuration_report(config_path)
    configured_db = str(config.runtime.decision_db_path) if config is not None else DEFAULT_DECISION_DB_PATH
    db_path = _resolve_path(args.db or configured_db)
    database = _audit_database(db_path, config)
    report = {
        "schema_version": "1.0.0",
        "audit": "v3_paper_soak",
        "generated_utc": _utc_now(),
        "read_only_contract": {
            "sqlite_open_mode": "ro",
            "sqlite_query_only": True,
            "broker_calls": False,
            "orders_submitted": False,
            "observations_fabricated": False,
            "runtime_writes": False,
        },
        "scope": {
            "strategy": EXPECTED_STRATEGY,
            "market": EXPECTED_MARKET,
            "mode": EXPECTED_MODE,
            "data_source": "portfolio_decisions and related portfolio_* runtime tables",
        },
        "frozen_semantics": _expected_frozen_semantics(),
        "configuration": configuration,
        "database": database,
        "promotion_interpretation": {
            "status": "report_only",
            "no_data_is_not_a_pass": True,
            "live_prohibited": True,
            "config_selection_is_not_changed_by_this_command": True,
        },
    }
    print(json.dumps(report, indent=2 if args.pretty else None, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
