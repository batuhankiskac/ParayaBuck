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

from parayabuck.config import DEFAULT_DECISION_DB_PATH, load_config
from parayabuck.logger import get_logger

log = get_logger("parayabuck.web.services.dashboard")


_DASHBOARD_DEFAULT_DB = Path(DEFAULT_DECISION_DB_PATH)


@dataclass(frozen=True)
class DashboardContext:
    """Explicit display dependencies for one dashboard request."""

    engine_manager: Any
    backtest_manager: Any | None = None
    market_decisions: Callable[[str], dict[str, dict[str, Any]]] | None = None


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


def _dashboard_market_decisions_for_context(account_id: str) -> dict[str, dict[str, Any]]:
    context = _dashboard_context.get()
    if context is not None and context.market_decisions is not None:
        return context.market_decisions(account_id)
    return _dashboard_market_decisions(account_id)


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


def _dashboard_last_no_trade() -> dict[str, Any] | None:
    """Latest portfolio_no_trade reason for top-level dashboard banner.

    Reads the most recent portfolio_no_trade operational_event from the
    observability journal. Returns a single-sentence Turkish reason or None.
    """
    try:
        import sqlite3 as _sql
        from pathlib import Path as _P

        # Use the journal's actual path (isolated in tests via conftest)
        try:
            from parayabuck.monitoring.journal import get_journal

            journal = get_journal()
            journal_path = str(journal.path)
        except Exception:
            # Fallback to config or default
            cfg = _dashboard_config()
            journal_path = "data/runtime/observability.sqlite3"
            try:
                if cfg is not None and hasattr(cfg, "observability"):
                    jp = getattr(cfg.observability, "journal_path", None)
                    if jp:
                        journal_path = str(jp)
            except Exception:
                pass
        jp = _P(journal_path)
        if not jp.exists():
            return None
        con = _sql.connect(f"file:{jp.resolve()}?mode=ro", uri=True, timeout=2)
        try:
            row = con.execute(
                "SELECT kind, message, market, strategy, occurred_at, fields_json FROM operational_events WHERE kind='portfolio_no_trade' ORDER BY occurred_at DESC LIMIT 1"
            ).fetchone()
        finally:
            con.close()
        if not row:
            return None
        kind, message, market, strategy, occurred_at, fields_json = row
        import json as _json

        try:
            fields = _json.loads(fields_json) if fields_json else {}
        except Exception:
            fields = {}
        reason = fields.get("reason", "")
        return {
            "kind": kind,
            "message": message,
            "market": market,
            "strategy": strategy,
            "occurred_at": occurred_at,
            "reason": reason,
            "fields": fields,
        }
    except Exception as exc:
        log.warning("Dashboard last_no_trade probe failed: %s", exc)
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
        log.warning("Dashboard panel %s failed: %s", name, exc)
        return {"unavailable": True, "error": str(exc)}


def _dashboard_mode(cfg: Any) -> dict[str, Any]:
    """PAPER/LIVE badge state, driven by config.live.enabled + runtime mode.

    A live-configured deployment whose engine is not trading right now shows
    LIVE in a clearly-disabled style; a paper config always shows PAPER. The
    badge never derives from broker connectivity or secrets.
    """
    runtime_mode = "paper"
    live_enabled = False
    if cfg is not None:
        configured_mode = getattr(getattr(cfg, "runtime", None), "mode", None)
        runtime_mode = str(getattr(configured_mode, "value", None) or configured_mode or "paper")
        live_enabled = bool(getattr(getattr(cfg, "live", None), "enabled", False))
    paper = bool(getattr(_dashboard_manager(), "is_paper_mode", lambda: True)())
    engine_state = _manager_engine_state()
    live_configured = bool(runtime_mode == "live" and live_enabled)
    live_active = bool(live_configured and engine_state in {"running", "warming_up", "degraded"})
    live_disabled = bool(live_configured and not live_active)
    return {
        "paper": paper,
        "runtime_mode": runtime_mode,
        "live_enabled": live_enabled,
        "live_configured": live_configured,
        "live_active": live_active,
        "live_disabled": live_disabled,
        "engine_state": engine_state,
        "badge": (
            "paper"
            if (paper and not live_configured)
            else ("live" if live_active else ("live_disabled" if live_configured else "unknown"))
        ),
    }


def _dashboard_market_decisions(account_id: str) -> dict[str, dict[str, Any]]:
    """Latest persisted decision per market (target + risk JSON payloads)."""
    connection = _dashboard_readonly_connection()
    if connection is None:
        return {}
    try:
        rows = connection.execute(
            """
            SELECT market, decision_timestamp, strategy_name, target_json, risk_json
            FROM portfolio_decisions
            WHERE account_id = ?
            ORDER BY decision_timestamp DESC
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
    guarantee.
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
    positions = [position for position in account.get("positions", []) if position.get("market") == market_key]
    equity = max(float(account.get("equity") or 0.0), 1e-9)
    market_value = sum(float(position.get("market_value") or 0.0) for position in positions)
    target = dict((decision or {}).get("target") or {})
    weights = {str(weight.get("symbol")): weight for weight in target.get("weights", [])}
    metadata = dict(target.get("metadata") or {})
    selected = [str(symbol) for symbol in metadata.get("selected_symbols", ())]
    qualified = int(metadata.get("qualified_symbols") or 0)
    hypothesis = str(metadata.get("hypothesis") or "")
    holdings: list[dict[str, Any]] = []
    for position in positions:
        symbol = str(position.get("symbol", ""))
        weight = weights.get(symbol)
        rank = selected.index(symbol) + 1 if symbol in selected else None
        holdings.append(
            {
                "symbol": symbol,
                "direction": position.get("direction"),
                "qty": position.get("total_qty") or position.get("qty") or 0,
                "market_value": position.get("market_value"),
                "current_weight_pct": (float(position.get("market_value") or 0.0) / equity) * 100,
                "target_weight_pct": (float((weight or {}).get("weight") or 0.0) * 100 if weight else None),
                "reason": (weight or {}).get("reason") if weight else None,
                "rank": rank,
                "of_qualified": qualified if rank is not None else None,
                "hypothesis": hypothesis,
                "unrealized_pnl": position.get("unrealized_pnl"),
                "protection_state": position.get("protection_state"),
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
            ORDER BY activated_at DESC, id DESC
            """,
            (account_id,),
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
        capacity = {"unavailable": True, "reason": str(exc)}
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
    drawdown = float(account.get("drawdown") or 0.0)
    limits = getattr(cfg, "risk", None) if cfg is not None else None
    incidents, risk_state = _dashboard_ledger_risk()
    capacity = _dashboard_buy_capacity()
    return {
        "gross_exposure_pct": (gross / equity) * 100,
        "gross_exposure_limit_pct": (float(getattr(limits, "max_gross_exposure_pct", 0.60) or 0.0) * 100),
        "cash_pct": (cash / equity) * 100,
        "cash_buffer_limit_pct": (float(getattr(limits, "cash_buffer_pct", 0.40) or 0.0) * 100),
        "equity_exposure_pct": (equity_value / equity) * 100,
        "max_position_pct": (float(getattr(limits, "max_position_pct", 0.10) or 0.0) * 100),
        "max_positions": int(getattr(limits, "max_positions", 0) or 0),
        "daily_pnl": daily_pnl,
        "daily_loss_limit_pct": (float(getattr(limits, "daily_loss_limit_pct", 0.005) or 0.0) * 100),
        "drawdown": drawdown,
        "max_drawdown_pct": (float(getattr(limits, "max_drawdown_pct", 0.05) or 0.0) * 100),
        "entry_risk_latch": dict((health or {}).get("entry_risk_latch") or {}),
        "circuit_breaker": (health or {}).get("circuit_breaker_reason"),
        "safety_incidents": incidents,
        "risk_epoch": risk_state,
        "buy_capacity": capacity,
    }


def _fill_expected_vs_actual(order: dict[str, Any], fill: dict[str, Any]) -> dict[str, Any] | None:
    """Per-order expected-vs-actual slippage telemetry; None on degrade."""
    try:
        from parayabuck.execution.telemetry import expected_vs_actual
    except Exception:
        return None
    try:
        return dict(expected_vs_actual(order, [fill]))
    except Exception as exc:
        log.debug("expected_vs_actual comparison unavailable: %s", exc)
        return None


def _dashboard_execution() -> dict[str, Any]:
    """Execution panel: open orders + recent fills with decision lineage."""
    account_id = _dashboard_account_id()
    connection = _dashboard_readonly_connection()
    if connection is None:
        return {
            "open_orders": [],
            "recent_fills": [],
            "last_decision": None,
            "reconciliation_anomalies": [],
        }
    order_rows: list[Any] = []
    fill_rows: list[Any] = []
    anomaly_rows: list[Any] = []
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
    try:
        anomaly_rows = connection.execute(
            """
            SELECT symbol, market, quantity, kind, note, created_at
            FROM portfolio_ownership_adjustments
            WHERE account_id = ?
              -- historical crypto dust clear/credit rows are bookkeeping, not anomalies
              AND kind NOT IN ('crypto_dust_clear', 'crypto_dust_credit')
            ORDER BY created_at DESC LIMIT 10
            """,
            (account_id,),
        ).fetchall()
    except sqlite3.Error as exc:
        log.debug("Dashboard reconciliation-anomaly query skipped: %s", exc)
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
        fill_entry["expected_vs_actual"] = _fill_expected_vs_actual(
            {
                "client_order_id": str(row[1]),
                "symbol": str(row[2]),
                "market": str(row[3]),
                "side": str(row[4]),
                "quantity": float(row[15] or 0.0),
                "reference_price": reference,
                "created_at": row[17],
                "status": str(row[16]),
            },
            {
                "quantity": float(row[5] or 0.0),
                "price": price,
                "fee": float(row[7] or 0.0),
                "occurred_at": str(row[12]),
            },
        )
        recent_fills.append(fill_entry)
    reconciliation_anomalies = [
        {
            "symbol": str(row[0]),
            "market": str(row[1]),
            "quantity": float(row[2] or 0.0),
            "kind": str(row[3]),
            "note": str(row[4] or ""),
            "created_at": str(row[5]),
        }
        for row in anomaly_rows
    ]
    return {
        "open_orders": open_orders,
        "recent_fills": recent_fills,
        "reconciliation_anomalies": reconciliation_anomalies,
    }


def _dashboard_universe(cfg: Any, health: dict[str, Any] | None) -> dict[str, Any]:
    """Universe panel: static equity symbols and pins."""
    if cfg is None:
        return {"unavailable": True, "reason": "config unavailable"}
    market_health = dict((health or {}).get("markets") or {})
    equity_universe = cfg.universe.equity
    equity_symbols = tuple(equity_universe.symbols) if equity_universe.enabled else ()
    return {
        "equity": {
            "observation_set": list(equity_symbols),
            "observation_set_size": len(equity_symbols),
            "pinned_symbols": list(dict(market_health.get("equity") or {}).get("managed_pins") or ()),
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
        "path": str(path),
        "exists": path.exists(),
        "size_bytes": size,
        "wal_file_present": Path(f"{path}-wal").exists(),
    }


def _dashboard_restart_count() -> int | None:
    """Fallback restart count when the runtime_metrics payload omits it.

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
    watchdog = (health or {}).get("intent_watchdog")
    reasons = watchdog.get("degraded_reasons") if isinstance(watchdog, dict) else None
    return [str(reason) for reason in reasons] if isinstance(reasons, list) else []


def _dashboard_health(
    cfg: Any,
    health: dict[str, Any] | None,
    account: dict[str, Any],
    *,
    runtime_metrics_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """System health panel: workers, queue, freshness, reconciliation, DB."""
    workers = dict((health or {}).get("workers") or {})
    heartbeats = [str(worker.get("last_heartbeat")) for worker in workers.values() if worker.get("last_heartbeat")]
    engine_cfg = getattr(cfg, "engine", None) if cfg is not None else None
    if runtime_metrics_payload is None:
        runtime_metrics_payload = _runtime_metrics_payload(_dashboard_account_id())
    restart_count = runtime_metrics_payload.get("restart_count")
    if restart_count is None:
        restart_count = _dashboard_restart_count()
    return {
        "engine_state": _manager_engine_state(),
        "healthy": (health or {}).get("healthy"),
        "workers": workers,
        "restart_count": restart_count,
        # Intent-watchdog alarm codes, the same list /readyz fails readiness
        # on: the badge must not read "Sağlıklı" while a session went undecided.
        "degraded_reasons": _dashboard_degraded_reasons(health),
        "queue": {
            "size": (health or {}).get("queue_size"),
            "age_seconds": (health or {}).get("queue_age_seconds"),
            "dropped_events": (health or {}).get("dropped_events"),
            "coalesced_events": (health or {}).get("coalesced_events"),
        },
        "markets": dict((health or {}).get("markets") or {}),
        "account_freshness": {
            "age_seconds": account.get("age_seconds"),
            "stale": account.get("stale"),
            "source": account.get("source"),
            "maximum_age_for_entry_seconds": getattr(engine_cfg, "maximum_account_age_for_entry_seconds", None),
        },
        "reconciliation": {
            "last_at": (health or {}).get("last_reconciliation_at"),
            "failed": (health or {}).get("circuit_breaker_reason") == "reconciliation_failed",
        },
        "database": _dashboard_database_state(),
        "last_heartbeat": max(heartbeats) if heartbeats else None,
        "entry_risk_latch": dict((health or {}).get("entry_risk_latch") or {}),
    }


def _dashboard_explanations(decisions: dict[str, dict[str, Any]], account: dict[str, Any]) -> dict[str, Any]:
    """Per-holding structured reasons from persisted decision metadata."""
    result: dict[str, Any] = {}
    for market_key in ("equity",):
        decision = decisions.get(market_key)
        target = dict((decision or {}).get("target") or {})
        metadata = dict(target.get("metadata") or {})
        weights = {str(weight.get("symbol")): weight for weight in target.get("weights", [])}
        selected = [str(symbol) for symbol in metadata.get("selected_symbols", ())]
        qualified = int(metadata.get("qualified_symbols") or 0)
        holdings: list[dict[str, Any]] = []
        for position in account.get("positions", []):
            if position.get("market") != market_key:
                continue
            symbol = str(position.get("symbol", ""))
            weight = weights.get(symbol)
            rank = selected.index(symbol) + 1 if symbol in selected else None
            if weight is not None:
                reason = str(weight.get("reason") or "target_weight")
            elif rank is not None:
                reason = "ranked candidate not in the latest target"
            else:
                reason = "held outside the latest decision (external or pre-decision)"
            holdings.append(
                {
                    "symbol": symbol,
                    "reason": reason,
                    "rank": rank,
                    "of_qualified": qualified if rank is not None else None,
                    "target_weight_pct": (float((weight or {}).get("weight") or 0.0) * 100 if weight else 0.0),
                    "decision_timestamp": (decision or {}).get("timestamp"),
                    "decision_strategy": (decision or {}).get("strategy_name"),
                    "hypothesis": metadata.get("hypothesis"),
                    "universe_version": target.get("universe_version"),
                    "rebalance_reason": target.get("rebalance_reason"),
                    "risk_multiplier": target.get("risk_multiplier"),
                }
            )
        result[market_key] = {
            "strategy_name": (decision or {}).get("strategy_name"),
            "last_decision_at": (decision or {}).get("timestamp"),
            "qualified_symbols": qualified,
            "selected_symbols": selected,
            "hypothesis": metadata.get("hypothesis"),
            "risk_off_detail": metadata.get("risk_off_detail"),
            "holdings": holdings,
        }
    return result


def _telemetry_payload() -> dict[str, Any]:
    """Execution/forward/cost telemetry for the dashboard; degrades explicitly."""
    try:
        from parayabuck.execution.telemetry import (
            cost_calibration,
            execution_metrics,
            forward_metrics,
        )
    except ImportError:
        return {
            "unavailable": True,
            "reason": "parayabuck.execution.telemetry is not available yet",
        }
    except Exception as exc:
        log.warning("Dashboard telemetry import failed: %s", exc)
        return {"unavailable": True, "reason": f"telemetry import error: {exc}"}
    db_path = _dashboard_db_path()
    connection = _dashboard_readonly_connection()
    try:
        if connection is not None:
            # Share the payload-build connection across all three reports.
            # Fall back to per-report connections when the module predates
            # the optional _connection parameter (or is a test stub).
            try:
                execution = execution_metrics(db_path, _connection=connection)
                forward = forward_metrics(db_path, _connection=connection)
                calibration = cost_calibration(db_path, _connection=connection)
            except TypeError:
                execution = execution_metrics(db_path)
                forward = forward_metrics(db_path)
                calibration = cost_calibration(db_path)
        else:
            execution = execution_metrics(db_path)
            forward = forward_metrics(db_path)
            calibration = cost_calibration(db_path)
        return {
            "unavailable": False,
            "execution_metrics": execution,
            "forward_metrics": forward,
            "cost_calibration": calibration,
        }
    except Exception as exc:
        log.warning("Dashboard telemetry failed: %s", exc)
        return {"unavailable": True, "reason": f"telemetry error: {exc}"}
    finally:
        _dashboard_release(connection)


def _runtime_metrics_payload(account_id: str) -> dict[str, Any]:
    """Authoritative runtime metrics (restarts, sessions) for the dashboard."""
    try:
        from parayabuck.monitoring.metrics import runtime_metrics

        return dict(runtime_metrics(_dashboard_db_path(), account_id=account_id))
    except ImportError:
        return {
            "unavailable": True,
            "reason": "parayabuck.monitoring.metrics is not available",
        }
    except Exception as exc:
        log.warning("Dashboard runtime metrics failed: %s", exc)
        return {"unavailable": True, "reason": f"runtime metrics error: {exc}"}


# Display-only dashboard snapshot cache: the browser polls all 9 dashboard
# endpoints every ~2s and every call rebuilds the ENTIRE payload (config,
# ledger reads, telemetry, broker-snapshot panel). Within a short TTL the
# payload is served from this cache, so a poll tick costs one rebuild instead
# of nine. The cache is keyed by engine state and settings-file version, and
# never feeds trading state, decisions, or the runtime (display-only).
_DASHBOARD_SNAPSHOT_TTL_SECONDS = 1.5
_dashboard_snapshot_lock = threading.Lock()
_dashboard_snapshot: tuple[float, str, tuple[str, int] | None, int, int, dict[str, Any]] | None = None


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
    current_context = _dashboard_context.get()
    decisions_key = id(current_context.market_decisions) if current_context is not None else 0
    now = time.monotonic()
    with _dashboard_snapshot_lock:
        cached = _dashboard_snapshot
        if (
            cached is not None
            and now < cached[0]
            and cached[1] == engine_state
            and cached[2] == config_key
            and cached[3] == manager_key
            and cached[4] == decisions_key
        ):
            return cached[5]
    payload = _build_dashboard_payload(engine_state)
    with _dashboard_snapshot_lock:
        _dashboard_snapshot = (
            time.monotonic() + _DASHBOARD_SNAPSHOT_TTL_SECONDS,
            engine_state,
            config_key,
            manager_key,
            decisions_key,
            payload,
        )
    return payload


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
    decisions = _dashboard_market_decisions_for_context(account_id)
    # runtime_metrics is consumed by BOTH the health panel and the metrics
    # panel; compute it once per build instead of twice.
    runtime_metrics_payload = _runtime_metrics_payload(account_id)
    meta: dict[str, Any] = {}
    if cfg is not None:
        try:
            from parayabuck.web.services.status import strategy_metadata

            meta = strategy_metadata(cfg)
        except Exception:
            meta = {}
    equity_meta = dict(meta.get("equity") or {})
    return {
        "as_of": datetime.now(UTC).isoformat(),
        "engine_state": engine_state,
        "engine_error": getattr(_dashboard_manager(), "last_engine_error", None),
        "last_no_trade": _dashboard_safe_panel("last_no_trade", _dashboard_last_no_trade),
        "mode": _dashboard_safe_panel("mode", lambda: _dashboard_mode(cfg)),
        "account": {
            "equity": account.get("equity"),
            "cash": account.get("cash"),
            "gross_exposure": account.get("gross_exposure"),
            "daily_pnl": account.get("daily_pnl"),
            "realized_pnl": account.get("realized_pnl"),
            "unrealized_pnl": account.get("unrealized_pnl"),
            "total_pnl": (float(account.get("realized_pnl") or 0.0) + float(account.get("unrealized_pnl") or 0.0))
            if account.get("realized_pnl") is not None or account.get("unrealized_pnl") is not None
            else None,
            "drawdown": account.get("drawdown"),
            "account_suffix": account.get("account_suffix"),
            "snapshot_at": account.get("snapshot_at"),
            "age_seconds": account.get("age_seconds"),
            "stale": account.get("stale"),
            "source": account.get("source"),
            "last_reconciliation_at": (health or {}).get("last_reconciliation_at"),
        },
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
        "risk": _dashboard_safe_panel("risk", lambda: _dashboard_risk(cfg, health, account)),
        "execution": _dashboard_safe_panel("execution", _dashboard_execution),
        "universe": _dashboard_safe_panel("universe", lambda: _dashboard_universe(cfg, health)),
        "health": _dashboard_safe_panel(
            "health",
            lambda: _dashboard_health(cfg, health, account, runtime_metrics_payload=runtime_metrics_payload),
        ),
        "explanations": _dashboard_safe_panel(
            "explanations",
            lambda: _dashboard_explanations(decisions, account),
        ),
        "metrics": _dashboard_metrics_payload(account_id, runtime_metrics_payload=runtime_metrics_payload),
    }


def _dashboard_metrics_payload(
    account_id: str,
    *,
    runtime_metrics_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "telemetry": _telemetry_payload(),
        "runtime_metrics": (
            runtime_metrics_payload if runtime_metrics_payload is not None else _runtime_metrics_payload(account_id)
        ),
    }


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
    backtest_manager = getattr(state, "backtest_manager", None)
    return DashboardContext(
        engine_manager=engine_manager,
        backtest_manager=backtest_manager,
        market_decisions=_dashboard_market_decisions,
    )


def get_overview_payload(state: Any | None = None) -> dict[str, Any]:
    try:
        return _dashboard_payload(context=_context(state))
    except Exception as exc:
        log.exception("Dashboard overview payload failed: %s", exc)
        return {"unavailable": True, "error": str(exc)}


def get_panel(key: str, state: Any | None = None) -> dict[str, Any]:
    payload = get_overview_payload(state)
    if isinstance(payload, dict) and payload.get("unavailable"):
        # The overview itself failed — hand its real cause back instead of
        # masking it as a missing panel.
        return payload
    if isinstance(payload, dict) and key in payload:
        return payload[key]  # type: ignore[no-any-return]
    return {"unavailable": True, "error": f"panel {key} not found"}
