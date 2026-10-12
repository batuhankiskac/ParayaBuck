"""Read-only status aggregation — extracted from web/server.py /api/status.

Aggregates engine state + runtime health + config + the static universe
without mutating any runtime state. No secrets are returned.
"""

from __future__ import annotations

import time
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from typing import Any

from parayabuck.logger import get_logger

log = get_logger("parayabuck.web.services.status")


def _load_config(path: str) -> Any:
    import parayabuck.config as _cfg

    return _cfg.load_config(path)


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


def build_status_snapshot(manager: Any) -> dict[str, Any]:
    """Aggregate read-only status from manager (no mutations, no secrets)."""
    key, _ = manager.get_credentials()
    result: dict[str, Any] = {
        "state": manager.current_engine_state(),
        "engine_error": getattr(manager, "last_engine_error", None),
        "paper": manager.is_paper_mode(),
        "has_credentials": bool(key),
        "security_warning": getattr(manager, "security_warning", None),
    }
    cfg: Any = None
    try:
        cfg = _load_config(str(manager.config_path))
        result["strategy_metadata"] = strategy_metadata(cfg)
    except Exception:
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

    runtime = getattr(manager, "runtime", None)
    if runtime is not None and hasattr(runtime, "health"):
        try:
            health = runtime.health()
        except Exception as exc:
            log.warning("Status health probe failed: %s", exc)
            health = None
        if health is not None:
            result["runtime"] = asdict(health)
            repository = getattr(runtime, "_portfolio_repository", None)
            if repository is not None:
                account_id = getattr(runtime.broker, "account_id", None) or "unknown"
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
