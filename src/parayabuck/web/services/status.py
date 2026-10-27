"""Read-only status aggregation — extracted from web/server.py /api/status.

Aggregates engine state + runtime health + config + the static universe
without mutating any runtime state. No secrets are returned.
"""

from __future__ import annotations

import os
import threading
import time
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from typing import Any

from parayabuck.logger import get_logger

log = get_logger("parayabuck.web.services.status")

# /api/status is polled every 2 s per open tab. The settings file and the
# strategy it declares change only when saved, so the parsed config and the
# strategy metadata are reused until the settings file or the credential file
# changes on disk (mtime key, same idea as the manager's paper-mode cache).
# The cache lives on the manager that owns the settings path, not in a module
# global, so one manager's config never answers for another.
_CONFIG_CACHE_LOCK = threading.Lock()


def _load_config(path: str) -> Any:
    import parayabuck.config as _cfg

    return _cfg.load_config(path)


def _mtime_ns(path: str) -> int | None:
    try:
        return os.stat(path).st_mtime_ns
    except OSError:
        return None


def _equity_symbols(cfg: Any) -> tuple[str, ...]:
    """Static equity universe: configured symbols, empty while disabled."""
    universe = cfg.universe.equity
    return tuple(universe.symbols) if universe.enabled else ()


def _equity_status(cfg: Any) -> str:
    """Equity universe status — the static set has no scanner snapshot."""
    return "static" if cfg.universe.equity.enabled else "disabled"


def strategy_metadata(cfg: Any) -> dict[str, Any]:
    """Declared strategy contracts plus live universe sizes for the Web UI.

    Shared by /api/status and the dashboard. There is deliberately NO global
    strategy timeframe: each strategy owns its signal timeframe. A strategy
    that cannot be constructed surfaces as an error, never disappears.
    """
    from parayabuck.portfolio import portfolio_strategy_from

    result: dict[str, Any] = {"equity": None}
    equity_symbols = _equity_symbols(cfg)
    selection = cfg.strategy.equity
    if not selection.enabled:
        return result
    try:
        strategy = portfolio_strategy_from(selection.name, selection.parameters, timeframe=selection.timeframe)
        spec = strategy.spec()
        result["equity"] = {
            "name": spec.name,
            "display_name": spec.display_name,
            "version": spec.version,
            "signal_timeframe": spec.primary_timeframe,
            "rebalance_cadence": spec.rebalance_policy,
            "minimum_history_bars": spec.minimum_history_bars,
            "candidate_pool_size": len(equity_symbols),
            "observation_set_size": len(equity_symbols),
        }
    except Exception:
        result["equity"] = {"name": selection.name, "configuration_error": True}
    return result


def _cached_config(manager: Any) -> tuple[Any, dict[str, Any] | None]:
    """Parsed config and its strategy metadata, reloaded only when a file changed.

    The stamps are read before the load, so a save racing the load leaves a
    stale stamp and the next poll reloads. Load errors are never cached.
    """
    from parayabuck.config import dotenv_path

    config_path = str(manager.config_path)
    try:
        cache: dict[str, tuple[tuple[int | None, int | None], Any, dict[str, Any] | None]] = (
            manager.__dict__.setdefault("_status_config_cache", {})
        )
    except AttributeError:
        cache = {}
    stamp = (_mtime_ns(config_path), _mtime_ns(str(dotenv_path())))
    with _CONFIG_CACHE_LOCK:
        cached = cache.get(config_path)
    if cached is not None and cached[0] == stamp:
        return cached[1], cached[2]
    cfg = _load_config(config_path)
    try:
        metadata: dict[str, Any] | None = strategy_metadata(cfg)
    except Exception:
        # A broken strategy declaration drops only its metadata; the config still serves the universe.
        log.warning("Status strategy metadata failed", exc_info=True)
        metadata = None
    with _CONFIG_CACHE_LOCK:
        cache[config_path] = (stamp, cfg, metadata)
    return cfg, metadata


def _operator_halt_status(manager: Any) -> dict[str, Any] | None:
    """The OPERATOR_HALT marker in force (``None`` when absent). Never raises.

    An unreadable marker still reports as active: the halt is fail-closed.
    """
    marker_for = getattr(manager, "operator_halt_marker", None)
    if not callable(marker_for):
        return None
    try:
        from parayabuck.live_safety import operator_halt_active, read_operator_halt

        marker = marker_for()
        if not operator_halt_active(marker):
            return None
        record = read_operator_halt(marker) or {}
    except Exception:
        log.warning("Status operator halt check failed", exc_info=True)
        return {"active": True, "reason": "unreadable halt marker", "created_at": None}
    return {
        "active": True,
        "reason": str(record.get("reason") or "belirtilmemiş"),
        "created_at": record.get("created_at"),
    }


def build_status_snapshot(manager: Any) -> dict[str, Any]:
    """Aggregate read-only status from manager (no mutations, no secrets)."""
    key, secret = manager.get_credentials()
    runtime = getattr(manager, "runtime", None)
    # One health() probe per poll: its state is the engine state. The manager
    # is asked for a state only when there is no probe result (no runtime, or
    # a failed probe), so the probe is never run twice in one snapshot.
    health = None
    if runtime is not None and hasattr(runtime, "health"):
        try:
            health = runtime.health()
        except Exception as exc:
            log.warning("Status health probe failed: %s", exc)
    result: dict[str, Any] = {
        "state": health.state if health is not None else manager.current_engine_state(),
        "engine_error": getattr(manager, "last_engine_error", None),
        "paper": manager.is_paper_mode(),
        "has_credentials": bool(key and secret),
        "security_warning": getattr(manager, "security_warning", None),
        "operator_halt": _operator_halt_status(manager),
    }
    cfg: Any = None
    try:
        cfg, metadata = _cached_config(manager)
        if metadata is not None:
            result["strategy_metadata"] = metadata
    except Exception:
        cfg = None
        result["scanner"] = {"equity_status": "error"}
    retry_attempt = getattr(manager, "_retry_attempt", 0)
    retry_next_at = getattr(manager, "_retry_next_at", None)
    if retry_attempt > 0 and retry_next_at is not None:
        remaining = max(0.0, retry_next_at - time.monotonic())
        result["engine"] = {
            "retry": {
                "attempt": retry_attempt,
                "retry_in_seconds": round(remaining, 1),
                "next_retry_at": (datetime.now(UTC) + timedelta(seconds=remaining)).isoformat(),
            }
        }

    if health is not None:
        result["runtime"] = asdict(health)
        repository = getattr(runtime, "_portfolio_repository", None)
        if repository is not None:
            account_id = getattr(getattr(runtime, "broker", None), "account_id", None) or "unknown"
            try:
                result["operations"] = repository.operations_snapshot(account_id)
            except Exception:
                pass

    if cfg is not None:
        equity_symbols = _equity_symbols(cfg)
        result["scanner"] = {"equity_status": _equity_status(cfg)}
        result["universe"] = {
            "equity": {
                "symbols": list(equity_symbols),
            },
        }
    return result
