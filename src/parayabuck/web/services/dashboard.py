"""Dashboard aggregation — read-only operator views over the live WebState.

Payload assembly is display-only: nothing here writes trading state, ledger
decisions or the runtime. The module holds no engine manager, no FastAPI app
and no log handler at import time — the live manager is resolved per request
(``_context``) and the module-level state is only the short-lived payload
cache.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from contextvars import ContextVar
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from parayabuck.config import DEFAULT_DECISION_DB_PATH, RiskLimits, load_config
from parayabuck.logger import get_logger
from parayabuck.web.services.trend_status import trend_status_from_metadata

log = get_logger("parayabuck.web.services.dashboard")


_DASHBOARD_DEFAULT_DB = Path(DEFAULT_DECISION_DB_PATH)
# Display fallbacks when no config is available: the code defaults.
_RISK_DEFAULTS = RiskLimits()


@dataclass(frozen=True)
class DashboardContext:
    """Explicit display dependencies for one dashboard request."""

    engine_manager: Any


_dashboard_context: ContextVar[DashboardContext | None] = ContextVar("parayabuck_dashboard_context", default=None)


@contextmanager
def _dashboard_context_scope(context: DashboardContext) -> Iterator[None]:
    token = _dashboard_context.set(context)
    try:
        yield
    finally:
        _dashboard_context.reset(token)


def _live_manager() -> Any:
    """The process-wide engine manager, resolved on demand (never at import)."""
    from parayabuck.web.state import get_web_state

    state = get_web_state()
    return state.engine_manager if state is not None else None


def _dashboard_manager() -> Any:
    context = _dashboard_context.get()
    return context.engine_manager if context is not None else _live_manager()


def _dashboard_runtime() -> Any:
    """The live EngineRuntime instance, or None when stopped (or stubbed)."""
    return getattr(_dashboard_manager(), "runtime", None)


def _manager_engine_state() -> str:
    """Current engine state via the module manager (getattr tolerates stubs)."""
    return getattr(_dashboard_manager(), "current_engine_state", lambda: "stopped")()


# Display-only caches for dashboard payload assembly. None of these can ever
# affect trading state, decisions, or the runtime: they memoize reads of
# static/display inputs (the settings file) and short-lived payload slices.
_dashboard_config_cache: dict[tuple[str, int], Any] = {}
_dashboard_config_cache_lock = threading.Lock()


def _dashboard_config_key() -> tuple[str, int] | None:
    """Cache key for the settings file: (path, mtime_ns); None when unreadable."""
    try:
        path = Path(str(_dashboard_manager().config_path))
        return (str(path), path.stat().st_mtime_ns if path.exists() else 0)
    except OSError:
        return None


def _dashboard_runtime_health() -> dict[str, Any] | None:
    runtime = _dashboard_runtime()
    if runtime is None or not hasattr(runtime, "health"):
        return None
    try:
        return asdict(runtime.health())
    except Exception as exc:
        log.warning("Dashboard health probe failed: %s", exc)
        return None


def _dashboard_config() -> Any | None:
    """The parsed settings Config, cached per file version.

    The dashboard rebuilds its payload on every poll and resolves the ledger
    path from this config in a dozen places; the YAML parse is pure overhead
    and the file is static per process. The cache is keyed by (path, mtime)
    so a settings save (or a swapped config path in tests) invalidates it on
    the next call. The returned Config is treated as read-only by every
    consumer. This cache is DISPLAY-ONLY: trading state never reads it.
    """
    try:
        key = _dashboard_config_key()
        if key is None:
            return None
    except OSError:
        return None
    with _dashboard_config_cache_lock:
        cached = _dashboard_config_cache.get(key)
        if cached is not None or key in _dashboard_config_cache:
            return cached
    try:
        cfg = load_config(str(_dashboard_manager().config_path))
    except Exception:
        return None
    with _dashboard_config_cache_lock:
        _dashboard_config_cache.clear()
        _dashboard_config_cache[key] = cfg
    return cfg


def _dashboard_account_id() -> str:
    runtime = _dashboard_runtime()
    broker_account = getattr(getattr(runtime, "broker", None), "account_id", None)
    if broker_account:
        return str(broker_account)
    # Engine stopped (or stubbed): scope reads to the account this
    # deployment's own ledger last traded with, never another process's.
    connection = _dashboard_readonly_connection()
    if connection is not None:
        try:
            row = connection.execute(
                "SELECT account_id FROM portfolio_runtime_sessions "
                "WHERE account_id IS NOT NULL AND account_id != '' "
                "ORDER BY started_at DESC LIMIT 1"
            ).fetchone()
            if row is not None and row[0]:
                return str(row[0])
        except sqlite3.Error:
            pass
        finally:
            _dashboard_release(connection)
    return "unknown"


def _dashboard_db_path() -> Path:
    cfg = _dashboard_config()
    if cfg is not None:
        configured = getattr(getattr(cfg, "runtime", None), "decision_db_path", None)
        if configured:
            return Path(str(configured))
    return _DASHBOARD_DEFAULT_DB


# Thread-local slot for the ONE read-only connection a payload build shares
# across every panel (decisions, risk, execution, telemetry). Display-only.
_dashboard_request_connection: threading.local = threading.local()


def _dashboard_readonly_connection() -> sqlite3.Connection | None:
    """Read-only ledger connection for the current payload build.

    While :func:`_dashboard_shared_connection` is active every dashboard
    helper receives the SAME connection (opened lazily here); otherwise a
    short-lived connection is opened and the caller must release it with
    :func:`_dashboard_release`. Returns None when the DB is missing.
    """
    shared: sqlite3.Connection | None = getattr(_dashboard_request_connection, "connection", None)
    if shared is not None:
        return shared
    path = _dashboard_db_path()
    if not path.exists():
        return None
    try:
        connection: sqlite3.Connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5.0)
        connection.execute("PRAGMA busy_timeout=5000")
        return connection
    except sqlite3.Error as exc:
        log.warning("Dashboard could not open ledger read-only: %s", exc)
        return None


def _dashboard_release(connection: sqlite3.Connection | None) -> None:
    """Close ``connection`` unless it is the payload-build shared one."""
    if connection is None:
        return
    if getattr(_dashboard_request_connection, "connection", None) is connection:
        return  # shared: the build context owns its lifecycle
    with suppress(sqlite3.Error):
        connection.close()


@contextmanager
def _dashboard_shared_connection() -> Iterator[sqlite3.Connection | None]:
    """Run one payload build against a single shared read-only connection.

    The connection is opened lazily on first use and closed when the build
    finishes; helpers releasing it via :func:`_dashboard_release` no-op while
    the build is active. Display-only: never shared with trading state.
    """
    connection = _dashboard_readonly_connection()
    if connection is not None:
        _dashboard_request_connection.connection = connection
    try:
        yield connection
    finally:
        _dashboard_request_connection.connection = None
        if connection is not None:
            with suppress(sqlite3.Error):
                connection.close()


def _dashboard_safe_panel(name: str, builder: Callable[[], dict[str, Any] | None]) -> dict[str, Any]:
    """Run one panel builder; a failure becomes an explicit unavailable dict."""
    try:
        payload = builder()
        return payload if isinstance(payload, dict) else {"unavailable": True}
    except Exception as exc:
        # The browser gets a fixed reason; the exception text stays in the log.
        log.warning("Dashboard panel %s failed: %s", name, exc)
        return {"unavailable": True, "error": "panel unavailable"}


def _dashboard_market_decisions(account_id: str) -> dict[str, dict[str, Any]]:
    """Latest persisted decision per market (target + risk JSON payloads)."""
    connection = _dashboard_readonly_connection()
    if connection is None:
        return {}
    try:
        # Latest row per market only: the full history grows one row/day and
        # every poll discards all but the newest.
        rows = connection.execute(
            """
            SELECT market, decision_timestamp, strategy_name, target_json, risk_json
            FROM portfolio_decisions
            WHERE account_id = ?
              AND decision_timestamp = (
                  SELECT MAX(d2.decision_timestamp)
                  FROM portfolio_decisions d2
                  WHERE d2.account_id = portfolio_decisions.account_id
                    AND d2.market = portfolio_decisions.market
              )
            """,
            (account_id,),
        ).fetchall()
    except sqlite3.Error:
        return {}
    finally:
        _dashboard_release(connection)
    latest: dict[str, dict[str, Any]] = {}
    for market, timestamp, strategy_name, target, risk in rows:
        if market in latest:
            continue
        try:
            target_payload = json.loads(str(target)) if target else {}
            risk_payload = json.loads(str(risk)) if risk else {}
        except (TypeError, ValueError):
            continue
        latest[str(market)] = {
            "timestamp": str(timestamp),
            "strategy_name": str(strategy_name),
            "target": target_payload,
            "risk": risk_payload,
        }
    return latest


def _dashboard_next_rebalance_at(last_decision_at: str | None, policy: str | None) -> str | None:
    """Deterministic next rebalance boundary from the strategy cadence.

    Derived from the persisted decision timestamp and the strategy's declared
    rebalance policy (monthly / weekly / hours:N). It is an EXPECTED boundary
    (the decision runs on the first completed bar of the period), not a
    guarantee. A whole-day ``hours:N`` cadence (VT35: ``hours:24``) decides
    once per NYSE session, so it advances N/24 sessions on the NYSE calendar
    to that session's close (weekends, holidays and 13:00 ET early closes
    included) instead of adding wall-clock hours.
    """
    if not last_decision_at or not policy:
        return None
    try:
        last = datetime.fromisoformat(last_decision_at)
    except ValueError:
        return None
    if policy.startswith("hours:"):
        try:
            hours = int(policy.removeprefix("hours:"))
        except ValueError:
            return None
        if hours > 0 and hours % 24 == 0:
            return _next_session_close(last, hours // 24)
        return (last + timedelta(hours=hours)).isoformat()
    if policy == "weekly":
        days_ahead = 7 - last.weekday()
        boundary = (last + timedelta(days=days_ahead)).replace(hour=0, minute=0, second=0, microsecond=0)
        return boundary.isoformat()
    if policy == "monthly":
        year, month = last.year, last.month
        if month == 12:
            year, month = year + 1, 1
        else:
            month += 1
        return datetime(year, month, 1, tzinfo=UTC).isoformat()
    return None


def _next_session_close(last: datetime, sessions: int) -> str | None:
    """Close (UTC ISO) of the ``sessions``-th NYSE session after ``last``'s session."""
    from parayabuck.markets.nyse_calendar import NEW_YORK, session_close_utc

    if last.tzinfo is None:
        last = last.replace(tzinfo=UTC)
    day = last.astimezone(NEW_YORK).date()
    remaining = sessions
    # Two weeks per session is far beyond any NYSE closure run.
    for _ in range(14 * sessions):
        day += timedelta(days=1)
        close = session_close_utc(day)
        if close is None:
            continue
        remaining -= 1
        if remaining == 0:
            return close.isoformat()
    return None


def _dashboard_market_status(
    engine_state: str,
    enabled: bool,
    market_health: dict[str, Any],
    health: dict[str, Any] | None,
) -> str:
    """active | warming | halted | disabled | engine states."""
    if not enabled:
        return "disabled"
    if engine_state in {"stopped", "error", "stop_failed", "stopping", "starting"}:
        return engine_state
    if (health or {}).get("circuit_breaker_reason"):
        return "halted"
    if market_health.get("entry_halt_reason"):
        return "halted"
    if market_health.get("warming_symbols"):
        return "warming"
    if health is None:
        return "unknown"
    return "active"


def _managed_share(position: dict[str, Any]) -> tuple[float, float]:
    """(managed quantity, managed market value) of one account position.

    Market value is pro-rated by the managed share of the total quantity. A
    position without the managed split (older payloads) counts in full.
    """
    total_qty = float(position.get("total_qty") or position.get("qty") or 0.0)
    market_value = float(position.get("market_value") or 0.0)
    if position.get("managed_qty") is None:
        return total_qty, market_value
    if total_qty <= 0.0:
        return 0.0, 0.0
    managed_qty = min(max(float(position.get("managed_qty") or 0.0), 0.0), total_qty)
    return managed_qty, market_value * managed_qty / total_qty


def _dashboard_market_card(
    market_key: str,
    *,
    cfg: Any,
    health: dict[str, Any] | None,
    account: dict[str, Any],
    meta: dict[str, Any],
    decision: dict[str, Any] | None,
    engine_state: str,
) -> dict[str, Any]:
    """One strategy card: contract, pools, holdings."""
    strategy_cfg = getattr(cfg, "strategy", None) if cfg is not None else None
    selection = getattr(strategy_cfg, market_key, None)
    if selection is None:
        return {
            "enabled": False,
            "name": market_key,
            "status": "unavailable",
            "strategy": {},
            "eligible_pool": None,
            "observation_set_size": None,
            "positions_count": 0,
            "current_allocation_pct": 0.0,
            "maximum_allocation_pct": 0.0,
            "last_decision_at": None,
            "next_rebalance_at": None,
            "holdings": [],
        }
    enabled = bool(selection.enabled)
    market_health = dict((health or {}).get("markets", {}).get(market_key, {}) or {})
    catch_up = dict(market_health.get("catch_up") or {})
    # The strategy card counts only the bot-managed slice of each position:
    # shares the bot did not open (external or manual) are not its allocation.
    positions = [
        (position, *_managed_share(position))
        for position in account.get("positions", [])
        if position.get("market") == market_key
    ]
    positions = [entry for entry in positions if entry[1] > 1e-12]
    equity = max(float(account.get("equity") or 0.0), 1e-9)
    market_value = sum(entry[2] for entry in positions)
    target = dict((decision or {}).get("target") or {})
    weights = {str(weight.get("symbol")): weight for weight in target.get("weights", [])}
    metadata = dict(target.get("metadata") or {})
    hypothesis = str(metadata.get("hypothesis") or "")
    holdings: list[dict[str, Any]] = []
    for position, managed_qty, managed_value in positions:
        symbol = str(position.get("symbol", ""))
        weight = weights.get(symbol)
        holdings.append(
            {
                "symbol": symbol,
                "direction": position.get("direction"),
                "qty": managed_qty,
                "market_value": managed_value,
                "current_weight_pct": (managed_value / equity) * 100,
                "target_weight_pct": (float((weight or {}).get("weight") or 0.0) * 100 if weight else None),
                "reason": (weight or {}).get("reason") if weight else None,
                "hypothesis": hypothesis,
                "unrealized_pnl": position.get("unrealized_pnl"),
            }
        )
    return {
        "enabled": enabled,
        "name": selection.name,
        "status": _dashboard_market_status(engine_state, enabled, market_health, health),
        "strategy": {
            "name": (meta or {}).get("name") or selection.name,
            "display_name": (meta or {}).get("display_name"),
            "version": (meta or {}).get("version"),
            "signal_timeframe": (meta or {}).get("signal_timeframe"),
            "rebalance_cadence": (meta or {}).get("rebalance_cadence"),
            "minimum_history_bars": (meta or {}).get("minimum_history_bars"),
            "configuration_error": bool((meta or {}).get("configuration_error")),
        },
        "eligible_pool": (meta or {}).get("candidate_pool_size"),
        "observation_set_size": (meta or {}).get("observation_set_size"),
        "positions_count": len(positions),
        "current_allocation_pct": (market_value / equity) * 100,
        "maximum_allocation_pct": float(selection.maximum_allocation or 0.0) * 100,
        "last_decision_at": (decision or {}).get("timestamp"),
        "next_rebalance_at": _dashboard_next_rebalance_at(
            (decision or {}).get("timestamp"),
            (meta or {}).get("rebalance_cadence"),
        ),
        "startup_catch_up": {
            "status": catch_up.get("status"),
            "at": catch_up.get("at"),
            "next_boundary_after": catch_up.get("next_boundary_after"),
        },
        "holdings": holdings,
    }


# Active incidents are rare; the cap only bounds the 5 s poll read.
_DASHBOARD_INCIDENT_LIMIT = 50


def _dashboard_ledger_risk() -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    """Active safety incidents + latest risk-state row for the account."""
    account_id = _dashboard_account_id()
    connection = _dashboard_readonly_connection()
    if connection is None:
        return [], None
    try:
        incident_rows = connection.execute(
            """
            SELECT reason, scope, activated_at, updated_at
            FROM safety_incidents
            WHERE account_id = ?
            ORDER BY activated_at DESC, id DESC LIMIT ?
            """,
            (account_id, _DASHBOARD_INCIDENT_LIMIT),
        ).fetchall()
        risk_rows = connection.execute(
            """
            SELECT session_date, payload, updated_at
            FROM risk_state
            WHERE account_id = ?
            ORDER BY session_date DESC, updated_at DESC LIMIT 1
            """,
            (account_id,),
        ).fetchall()
    except sqlite3.Error:
        return [], None
    finally:
        _dashboard_release(connection)
    incidents = [
        {
            "reason": str(row[0]),
            "scope": str(row[1]),
            "activated_at": str(row[2]),
            "updated_at": str(row[3]),
        }
        for row in incident_rows
    ]
    risk_state: dict[str, Any] | None = None
    if risk_rows:
        try:
            payload = json.loads(str(risk_rows[0][1]))
        except (TypeError, ValueError):
            payload = {}
        risk_state = {
            "session_date": str(risk_rows[0][0]),
            "updated_at": str(risk_rows[0][2]),
            **{str(key): value for key, value in dict(payload).items()},
        }
    return incidents, risk_state


_dashboard_capacity_cache_lock = threading.Lock()
_dashboard_capacity_cache: tuple[float, int, dict[str, Any]] | None = None


def _dashboard_buy_capacity() -> dict[str, Any]:
    """Sample read-only broker capacity at most once per ten seconds."""
    global _dashboard_capacity_cache
    runtime = _dashboard_runtime()
    if runtime is None or not hasattr(runtime, "capacity_health"):
        return {"unavailable": True, "reason": "runtime not started"}
    now = time.monotonic()
    with _dashboard_capacity_cache_lock:
        cached = _dashboard_capacity_cache
        if cached is not None and cached[0] > now and cached[1] == id(runtime):
            return cached[2]
    try:
        capacity = dict(runtime.capacity_health())
    except Exception as exc:
        log.warning("Dashboard capacity probe failed: %s", exc)
        capacity = {"unavailable": True, "reason": "capacity probe failed"}
    with _dashboard_capacity_cache_lock:
        _dashboard_capacity_cache = (time.monotonic() + 10.0, id(runtime), capacity)
    return capacity


def _dashboard_risk(cfg: Any, health: dict[str, Any] | None, account: dict[str, Any]) -> dict[str, Any]:
    """Risk panel: exposure, buffers, policies, latch, incidents, epoch."""
    equity = max(float(account.get("equity") or 0.0), 1e-9)
    cash = float(account.get("cash") or 0.0)
    gross = float(account.get("gross_exposure") or 0.0)
    positions = account.get("positions", [])
    equity_value = sum(float(p.get("market_value") or 0.0) for p in positions if p.get("market") == "equity")
    daily_pnl = float(account.get("daily_pnl") or 0.0)
    limits = getattr(cfg, "risk", None) if cfg is not None else None
    incidents, risk_state = _dashboard_ledger_risk()
    capacity = _dashboard_buy_capacity()
    return {
        "gross_exposure_pct": (gross / equity) * 100,
        "gross_exposure_limit_pct": (
            float(getattr(limits, "max_gross_exposure_pct", _RISK_DEFAULTS.max_gross_exposure_pct) or 0.0) * 100
        ),
        "cash_pct": (cash / equity) * 100,
        "cash_buffer_limit_pct": (
            float(getattr(limits, "cash_buffer_pct", _RISK_DEFAULTS.cash_buffer_pct) or 0.0) * 100
        ),
        "equity_exposure_pct": (equity_value / equity) * 100,
        "daily_pnl": daily_pnl,
        "daily_loss_limit_pct": (
            float(getattr(limits, "daily_loss_limit_pct", _RISK_DEFAULTS.daily_loss_limit_pct) or 0.0) * 100
        ),
        "max_drawdown_pct": (float(getattr(limits, "max_drawdown_pct", _RISK_DEFAULTS.max_drawdown_pct) or 0.0) * 100),
        "entry_risk_latch": dict((health or {}).get("entry_risk_latch") or {}),
        "circuit_breaker": (health or {}).get("circuit_breaker_reason"),
        "safety_incidents": incidents,
        "risk_epoch": risk_state,
        "buy_capacity": capacity,
    }


def _dashboard_execution() -> dict[str, Any]:
    """Execution panel: open orders + recent fills with decision lineage."""
    account_id = _dashboard_account_id()
    connection = _dashboard_readonly_connection()
    if connection is None:
        return {
            "open_orders": [],
            "recent_fills": [],
        }
    order_rows: list[Any] = []
    fill_rows: list[Any] = []
    try:
        from parayabuck.portfolio.runtime_service import TERMINAL_ORDER_STATUSES_SQL

        order_rows = connection.execute(
            f"""
            SELECT o.client_order_id, o.symbol, o.market, o.side, o.quantity,
                   o.status, o.reference_price, o.created_at,
                   d.decision_timestamp, d.strategy_name
            FROM portfolio_orders o
            JOIN portfolio_decisions d ON d.decision_id = o.decision_id
            JOIN portfolio_runtime_sessions s ON s.session_id = d.session_id
            WHERE s.account_id = ?
              AND o.status NOT IN ({TERMINAL_ORDER_STATUSES_SQL})
            ORDER BY o.created_at DESC LIMIT 25
            """,
            (account_id,),
        ).fetchall()
    except sqlite3.Error as exc:
        log.debug("Dashboard open-orders query skipped: %s", exc)
    try:
        fill_rows = connection.execute(
            """
            SELECT f.activity_id, f.client_order_id, o.symbol, o.market, o.side,
                   f.quantity, f.price, f.fee, f.slippage, o.reference_price,
                   f.realized_pnl, f.unrealized_pnl, f.occurred_at,
                   d.decision_timestamp, d.strategy_name,
                   o.quantity, o.status, o.created_at
            FROM portfolio_fills f
            JOIN portfolio_orders o ON o.client_order_id = f.client_order_id
            JOIN portfolio_decisions d ON d.decision_id = o.decision_id
            JOIN portfolio_runtime_sessions s ON s.session_id = d.session_id
            WHERE s.account_id = ?
            ORDER BY f.occurred_at DESC LIMIT 25
            """,
            (account_id,),
        ).fetchall()
    except sqlite3.Error as exc:
        log.debug("Dashboard recent-fills query skipped: %s", exc)
    finally:
        _dashboard_release(connection)

    def _lineage(decision_timestamp: object, strategy_name: object) -> dict[str, Any]:
        return {
            "decision_timestamp": (str(decision_timestamp) if decision_timestamp else None),
            "strategy": str(strategy_name) if strategy_name else None,
        }

    open_orders = [
        {
            "client_order_id": str(row[0]),
            "symbol": str(row[1]),
            "market": str(row[2]),
            "side": str(row[3]),
            "quantity": float(row[4] or 0.0),
            "status": str(row[5]),
            "reference_price": float(row[6]) if row[6] is not None else None,
            "created_at": str(row[7]),
            "lineage": _lineage(row[8], row[9]),
        }
        for row in order_rows
    ]
    recent_fills = []
    for row in fill_rows:
        price = float(row[6] or 0.0)
        reference = float(row[9]) if row[9] is not None else None
        direction = 1.0 if str(row[4]).lower() == "buy" else -1.0
        slippage_bps = ((price - reference) / reference) * 10000 * direction if reference and reference > 0 else None
        fill_entry = {
            "activity_id": str(row[0]),
            "client_order_id": str(row[1]),
            "symbol": str(row[2]),
            "market": str(row[3]),
            "side": str(row[4]),
            "quantity": float(row[5] or 0.0),
            "price": price,
            "fee": float(row[7] or 0.0),
            "slippage": float(row[8] or 0.0),
            "reference_price": reference,
            "slippage_bps": slippage_bps,
            "realized_pnl": float(row[10]) if row[10] is not None else None,
            "unrealized_pnl": float(row[11]) if row[11] is not None else None,
            "occurred_at": str(row[12]),
            "lineage": _lineage(row[13], row[14]),
        }
        recent_fills.append(fill_entry)
    return {
        "open_orders": open_orders,
        "recent_fills": recent_fills,
    }


# Outstanding ownership adjustments are rare; the cap only bounds the poll read.
_DASHBOARD_OWNERSHIP_LIMIT = 50


def _dashboard_ownership(account_id: str) -> dict[str, Any]:
    """Ownership adjustments whose quantity no lot has consumed yet.

    A positive ``outstanding_quantity`` adds shares to ownership (a split
    rebase); a negative one removes them. Display only: the ownership
    resolver reads the same rows on the trading side.
    """
    connection = _dashboard_readonly_connection()
    if connection is None:
        return {"adjustments": []}
    try:
        rows = connection.execute(
            """
            SELECT symbol, market, kind, sign, quantity, consumed_quantity, note, created_at
            FROM portfolio_ownership_adjustments
            WHERE account_id = ? AND consumed_quantity < quantity
            ORDER BY created_at DESC LIMIT ?
            """,
            (account_id, _DASHBOARD_OWNERSHIP_LIMIT),
        ).fetchall()
    except sqlite3.Error:
        return {"unavailable": True, "adjustments": []}
    finally:
        _dashboard_release(connection)
    adjustments = []
    for symbol, market, kind, sign, quantity, consumed, note, created_at in rows:
        outstanding = float(sign or 0.0) * (float(quantity or 0.0) - float(consumed or 0.0))
        adjustments.append(
            {
                "symbol": str(symbol),
                "market": str(market),
                "kind": str(kind),
                "outstanding_quantity": outstanding,
                "note": str(note or ""),
                "created_at": str(created_at),
            }
        )
    return {"adjustments": adjustments}


def _dashboard_universe(cfg: Any) -> dict[str, Any]:
    """Universe panel: the static equity symbols."""
    if cfg is None:
        return {"unavailable": True, "reason": "config unavailable"}
    equity_universe = cfg.universe.equity
    equity_symbols = tuple(equity_universe.symbols) if equity_universe.enabled else ()
    return {
        "equity": {
            "observation_set": list(equity_symbols),
            "observation_set_size": len(equity_symbols),
            "scanner_status": "static" if equity_universe.enabled else "disabled",
        },
    }


def _dashboard_database_state() -> dict[str, Any]:
    path = _dashboard_db_path()
    try:
        size = path.stat().st_size if path.exists() else None
    except OSError:
        size = None
    return {
        "exists": path.exists(),
        "size_bytes": size,
    }


def _dashboard_restart_count() -> int | None:
    """Restart count on the build's shared read-only connection.

    Delegates the formula to ``monitoring.metrics.runtime_session_restart_count``
    so the dashboard can never drift from the authoritative accounting.
    """
    connection = _dashboard_readonly_connection()
    if connection is None:
        return 0
    try:
        # Lazy import mirrors the other monitoring.metrics call sites.
        from parayabuck.monitoring.metrics import runtime_session_restart_count

        return runtime_session_restart_count(connection)
    except (ImportError, sqlite3.Error):
        return 0
    finally:
        _dashboard_release(connection)


def _dashboard_degraded_reasons(health: dict[str, Any] | None) -> list[str]:
    """Active intent-watchdog alarm codes from the RuntimeHealth dict."""
    from parayabuck.monitoring.intent_watchdog import degraded_reasons

    return degraded_reasons(health)


# Breaker reasons that mean the account state could not be reconciled or protected.
_RECONCILIATION_BREAKER_REASONS = frozenset(
    {"reconciliation_failed", "reconciliation_bug_suspected", "protection_failure"}
)


def _dashboard_health(
    health: dict[str, Any] | None,
    account: dict[str, Any],
) -> dict[str, Any]:
    """System health panel: workers, freshness, reconciliation, DB."""
    workers = dict((health or {}).get("workers") or {})
    # Newest beat compared as datetimes, then emitted as ISO 8601 (the str()
    # form used a space separator and mis-sorted mixed microsecond precision).
    heartbeats = [worker.get("last_heartbeat") for worker in workers.values() if worker.get("last_heartbeat")]
    heartbeats = [beat for beat in heartbeats if isinstance(beat, datetime)]
    # Only the restart count is read from the ledger here: one COUNT on the
    # build's shared connection, not a full runtime_metrics() pass over a
    # second connection.
    restart_count = _dashboard_restart_count()
    return {
        "engine_state": _manager_engine_state(),
        "healthy": (health or {}).get("healthy"),
        "workers": workers,
        "restart_count": restart_count,
        # Intent-watchdog alarm codes, the same list /readyz fails readiness
        # on: the badge must not read "Sağlıklı" while a session went undecided.
        "degraded_reasons": _dashboard_degraded_reasons(health),
        "markets": dict((health or {}).get("markets") or {}),
        "account_freshness": {
            "age_seconds": account.get("age_seconds"),
            "source": account.get("source"),
        },
        "reconciliation": {
            "last_at": (health or {}).get("last_reconciliation_at"),
            # Every breaker reason a reconciliation or protection failure latches.
            "failed": (health or {}).get("circuit_breaker_reason") in _RECONCILIATION_BREAKER_REASONS,
        },
        "database": _dashboard_database_state(),
        "last_heartbeat": max(heartbeats).isoformat() if heartbeats else None,
    }


# Display-only dashboard snapshot cache: the browser polls all 9 dashboard
# endpoints every ~2s and every call rebuilds the ENTIRE payload (config,
# ledger reads, telemetry, broker-snapshot panel). Within a short TTL the
# payload is served from this cache, so a poll tick costs one rebuild instead
# of nine. The cache is keyed by engine state and settings-file version, and
# never feeds trading state, decisions, or the runtime (display-only).
_DASHBOARD_SNAPSHOT_TTL_SECONDS = 1.5
_dashboard_snapshot_lock = threading.Lock()
_dashboard_snapshot: tuple[float, str, tuple[str, int] | None, int, dict[str, Any]] | None = None


def _dashboard_snapshot_reset() -> None:
    """Clear the display-only payload cache (benchmarks/tests)."""
    global _dashboard_snapshot, _dashboard_capacity_cache
    with _dashboard_snapshot_lock:
        _dashboard_snapshot = None
    with _dashboard_capacity_cache_lock:
        _dashboard_capacity_cache = None


def _dashboard_payload(context: DashboardContext | None = None) -> dict[str, Any]:
    """Full aggregated dashboard payload; each panel degrades on failure."""
    if context is not None:
        with _dashboard_context_scope(context):
            return _dashboard_payload()
    global _dashboard_snapshot
    engine_state = _manager_engine_state()
    config_key = _dashboard_config_key()
    manager_key = id(_dashboard_manager())
    now = time.monotonic()
    with _dashboard_snapshot_lock:
        cached = _dashboard_snapshot
        if (
            cached is not None
            and now < cached[0]
            and cached[1] == engine_state
            and cached[2] == config_key
            and cached[3] == manager_key
        ):
            return cached[4]
    payload = _build_dashboard_payload(engine_state)
    with _dashboard_snapshot_lock:
        _dashboard_snapshot = (
            time.monotonic() + _DASHBOARD_SNAPSHOT_TTL_SECONDS,
            engine_state,
            config_key,
            manager_key,
            payload,
        )
    return payload


def _trend_panel(
    trend_metadata: dict[str, Any],
    account: dict[str, Any],
    decision: dict[str, Any] | None,
) -> dict[str, Any]:
    """Trend card plus the time its signal was computed (the last persisted decision).

    With the engine stopped the card shows the last decision, which can be
    days old; ``as_of`` lets the card say so.
    """
    panel = dict(trend_status_from_metadata(trend_metadata, account))
    panel["as_of"] = (decision or {}).get("timestamp")
    return panel


def _build_dashboard_payload(engine_state: str) -> dict[str, Any]:
    """Assemble the full dashboard payload (called on snapshot-cache miss)."""
    with _dashboard_shared_connection():
        return _build_dashboard_payload_uncached(engine_state)


def _build_dashboard_payload_uncached(engine_state: str) -> dict[str, Any]:
    """Assemble all panels (single shared read-only connection active)."""
    cfg = _dashboard_config()
    health = _dashboard_runtime_health()
    account = _dashboard_account()
    account_id = _dashboard_account_id()
    decisions = _dashboard_market_decisions(account_id)
    meta: dict[str, Any] = {}
    if cfg is not None:
        try:
            from parayabuck.web.services.status import strategy_metadata

            meta = strategy_metadata(cfg)
        except Exception:
            meta = {}
    equity_meta = dict(meta.get("equity") or {})
    equity_decision = decisions.get("equity")
    equity_target_value = equity_decision.get("target") if isinstance(equity_decision, dict) else None
    equity_target = equity_target_value if isinstance(equity_target_value, dict) else {}
    trend_metadata_value = equity_target.get("metadata")
    trend_metadata = trend_metadata_value if isinstance(trend_metadata_value, dict) else {}
    account_view: dict[str, Any] = {
        "equity": account.get("equity"),
        "cash": account.get("cash"),
        "gross_exposure": account.get("gross_exposure"),
        "daily_pnl": account.get("daily_pnl"),
        "realized_pnl": account.get("realized_pnl"),
        "unrealized_pnl": account.get("unrealized_pnl"),
        # Basis: realized_pnl is TODAY's realized figure, unrealized_pnl is the
        # open P&L of every open position. The sum is labelled as such in the UI.
        "total_pnl": (float(account.get("realized_pnl") or 0.0) + float(account.get("unrealized_pnl") or 0.0))
        if account.get("realized_pnl") is not None or account.get("unrealized_pnl") is not None
        else None,
        "fees": account.get("fees"),
        "drawdown": account.get("drawdown"),
        "account_suffix": account.get("account_suffix"),
        "snapshot_at": account.get("snapshot_at"),
        "age_seconds": account.get("age_seconds"),
        "stale": account.get("stale"),
        "source": account.get("source"),
        # Positions ride with the balances so the positions table refreshes on every poll.
        "positions": list(account.get("positions") or []),
    }
    if account.get("source") == "unavailable":
        # The zero fallback is not a balance: send nulls so no client renders it as one.
        account_view = {
            key: (None if key not in {"stale", "source", "account_suffix"} else value)
            for key, value in account_view.items()
        }
    return {
        "as_of": datetime.now(UTC).isoformat(),
        "engine_state": engine_state,
        "account": account_view,
        "equity": _dashboard_safe_panel(
            "equity",
            lambda: _dashboard_market_card(
                "equity",
                cfg=cfg,
                health=health,
                account=account,
                meta=equity_meta,
                decision=decisions.get("equity"),
                engine_state=engine_state,
            ),
        ),
        "trend_status": _dashboard_safe_panel(
            "trend_status",
            lambda: _trend_panel(trend_metadata, account, equity_decision),
        ),
        "risk": _dashboard_safe_panel("risk", lambda: _dashboard_risk(cfg, health, account)),
        "execution": _dashboard_safe_panel("execution", _dashboard_execution),
        "ownership": _dashboard_safe_panel("ownership", lambda: _dashboard_ownership(account_id)),
        "universe": _dashboard_safe_panel("universe", lambda: _dashboard_universe(cfg)),
        "health": _dashboard_safe_panel(
            "health",
            lambda: _dashboard_health(health, account),
        ),
    }


_DASHBOARD_TAX_TTL_SECONDS = 60.0
_dashboard_tax_cache: dict[str, tuple[float, dict[str, Any]]] = {}


def get_tax_ytd_payload(state: Any | None = None) -> dict[str, Any]:
    """Year-to-date TL tax estimate for ``/api/dashboard/tax-ytd`` (separate from the overview)."""
    try:
        with _dashboard_context_scope(_context(state)):
            return _dashboard_tax_ytd(_dashboard_account_id())
    except Exception as exc:
        log.exception("Dashboard tax payload failed: %s", exc)
        return {"unavailable": True, "error": "tax payload unavailable"}


def _dashboard_tax_ytd(account_id: str) -> dict[str, Any]:
    """Year-to-date TL tax estimate (display only; cached for one minute).

    Reads the ledger through the shared read-only connection and the operator's
    USD/TRY and Yİ-ÜFE CSVs. Missing rates report ``status: rates_missing``.
    """
    import time
    from datetime import datetime

    from parayabuck.tax.export import ytd_status
    from parayabuck.tax.ledger import TURKEY_TIME

    year = datetime.now(TURKEY_TIME).year
    key = f"{account_id}:{year}"
    cached = _dashboard_tax_cache.get(key)
    now = time.monotonic()
    if cached is not None and now - cached[0] < _DASHBOARD_TAX_TTL_SECONDS:
        return cached[1]
    connection = _dashboard_readonly_connection()
    try:
        status = ytd_status(connection, account_id, year)
    except Exception as exc:
        # The card must render a failure, not a 500: report it as a status payload.
        log.warning("Dashboard tax status failed: %s", exc)
        return {"year": year, "status": "error", "error": "tax status unavailable"}
    finally:
        _dashboard_release(connection)
    # Only a clean result is cached; an error payload is recomputed on the next poll.
    if isinstance(status, dict) and status.get("status") == "ok":
        _dashboard_tax_cache[key] = (now, status)
    return status


def _dashboard_account() -> dict[str, Any]:
    fetch = getattr(_dashboard_manager(), "fetch_account_snapshot", None)
    if fetch is not None:
        try:
            return dict(fetch())
        except Exception as exc:
            log.warning("Dashboard account snapshot failed: %s", exc)
    return {
        "equity": 0.0,
        "cash": 0.0,
        "daily_pnl": 0.0,
        "realized_pnl": 0.0,
        "unrealized_pnl": 0.0,
        "drawdown": 0.0,
        "gross_exposure": 0.0,
        "positions": [],
        "account_suffix": "",
        "snapshot_at": None,
        "age_seconds": None,
        "stale": True,
        "source": "unavailable",
    }


def _context(state: Any | None) -> DashboardContext:
    """Explicit display dependencies for one dashboard request.

    ``state`` is the request's WebState; without one the process-wide state is
    used, so importing this module never builds an engine manager.
    """
    if state is None:
        from parayabuck.web.state import get_web_state

        state = get_web_state()
    engine_manager = getattr(state, "engine_manager", None)
    return DashboardContext(
        engine_manager=engine_manager,
    )


def get_overview_payload(state: Any | None = None) -> dict[str, Any]:
    try:
        return _dashboard_payload(context=_context(state))
    except Exception as exc:
        log.exception("Dashboard overview payload failed: %s", exc)
        return {"unavailable": True, "error": "overview unavailable"}


def account_id_for(state: Any | None = None) -> str:
    """Ledger account the dashboard scopes its reads to, for one request's WebState."""
    with _dashboard_context_scope(_context(state)):
        return _dashboard_account_id()
