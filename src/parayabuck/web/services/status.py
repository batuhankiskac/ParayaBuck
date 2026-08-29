"""Read-only status aggregation — extracted from web/server.py /api/status.

Aggregates engine state + runtime health + config + universe snapshots
without mutating any runtime state. No secrets are returned.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from parayabuck.core.models import Market
from parayabuck.logger import get_logger

log = get_logger("parayabuck.web.services.status")


def _load_config(path: str):
    import parayabuck.config as _cfg

    return _cfg.load_config(path)


def _validate_active_universe(cfg: Any):
    import parayabuck.universe as _u

    return _u.validate_active_universe(cfg)


def _validate_active_crypto_universe(cfg: Any):
    import parayabuck.universe as _u

    return _u.validate_active_crypto_universe(cfg)


def _dashboard_load_json(path: str | Path) -> dict[str, Any]:
    """Read + parse one JSON file; {} on any error (read-only helper)."""
    try:
        text = Path(path).read_text(encoding="utf-8")
        data = json.loads(text)
        if isinstance(data, dict):
            return data
        return {}
    except Exception:
        return {}


def strategy_metadata(cfg: Any) -> dict[str, Any]:
    """Declared strategy contracts plus live universe sizes for the Web UI.

    Mirrors server.py:_strategy_metadata verbatim — no global timeframe,
    each strategy owns its signal timeframe.
    """
    from parayabuck.portfolio import portfolio_strategy_from

    result: dict[str, Any] = {"equity": None, "crypto": None}
    candidate_payload: dict[str, Any] = {}
    active_payload: dict[str, Any] = {}
    active_crypto_payload: dict[str, Any] = {}
    for path, store in (
        (cfg.universe.equity.candidate_universe_path, candidate_payload),
        (cfg.universe.equity.active_universe_path, active_payload),
        (cfg.universe.crypto.active_universe_path, active_crypto_payload),
    ):
        store.update(_dashboard_load_json(path))
    shortlist = candidate_payload.get("shortlist_symbols")
    crypto_candidates = active_crypto_payload.get("candidate_symbols")
    for market_key, selection, pool_size in (
        (
            "equity",
            cfg.strategy.equity,
            len(shortlist) if isinstance(shortlist, list) else None,
        ),
        (
            "crypto",
            cfg.strategy.crypto,
            len(crypto_candidates) if isinstance(crypto_candidates, list) else None,
        ),
    ):
        if not selection.enabled:
            continue
        try:
            strategy = portfolio_strategy_from(selection.name, selection.parameters, timeframe=selection.timeframe)
            spec = strategy.spec()
            observation_size = (
                len(active_payload.get("observation_set", active_payload.get("selected_symbols", ())))
                if market_key == "equity"
                else len(
                    active_crypto_payload.get("observation_set", active_crypto_payload.get("selected_symbols", ()))
                )
            )
            result[market_key] = {
                "name": spec.name,
                "display_name": spec.display_name,
                "version": spec.version,
                "signal_timeframe": spec.primary_timeframe,
                "supported_timeframes": list(spec.supported_timeframes),
                "rebalance_cadence": spec.rebalance_policy,
                "minimum_history_bars": spec.minimum_history_bars,
                "dynamic_universe_enabled": (
                    cfg.universe.equity.dynamic_selection_enabled
                    if market_key == "equity"
                    else cfg.universe.crypto.dynamic_selection_enabled
                ),
                "candidate_pool_size": pool_size,
                "observation_set_size": observation_size,
            }
        except Exception:
            result[market_key] = {"name": selection.name, "configuration_error": True}
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
        "active_equity_symbol_count": 0,
        "active_crypto_symbol_count": 0,
    }
    cfg: Any = None
    try:
        cfg = _load_config(str(manager.config_path))
        configured: list[str] = []
        if cfg.strategy.equity.enabled:
            configured.append(f"equity/{cfg.strategy.equity.name}")
        if cfg.strategy.crypto.enabled:
            configured.append(f"crypto/{cfg.strategy.crypto.name}")
        result["strategy_status"] = "configured:" + ",".join(configured)
        result["strategy_metadata"] = strategy_metadata(cfg)
    except Exception:
        result["strategy_status"] = "configuration_error"
        result["scanner"] = {"last_run_at": None, "status": "error"}
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
    runtime_refresh: dict[str, Any] = {}
    if runtime is not None and hasattr(runtime, "health"):
        try:
            health = runtime.health()
        except Exception as exc:
            log.warning("Status health probe failed: %s", exc)
            health = None
        if health is not None:
            result["runtime"] = asdict(health)
            runtime_refresh = health.universe_refresh
            result["strategy_status"] = health.strategy_status
            repository = getattr(runtime, "_portfolio_repository", None)
            if repository is not None:
                account_id = getattr(runtime.broker, "account_id", None) or "unknown"
                try:
                    result["operations"] = repository.operations_snapshot(account_id)
                except Exception:
                    pass
                result["ownership_drift_active"] = health.circuit_breaker_reason == "ownership_drift"
                try:
                    result["reconciliation_anomalies"] = list(repository.ownership_adjustments(account_id))
                except Exception:
                    result["reconciliation_anomalies"] = []
            result["active_equity_symbol_count"] = len(
                getattr(runtime, "_symbols_by_market", {}).get(Market.EQUITY, ())
            )
            result["active_crypto_symbol_count"] = len(
                getattr(runtime, "_symbols_by_market", {}).get(Market.CRYPTO, ())
            )

    if cfg is not None:
        try:
            candidate_path = Path(cfg.universe.equity.candidate_universe_path)
            active_path = Path(cfg.universe.equity.active_universe_path)
            active_crypto_path = Path(cfg.universe.crypto.active_universe_path)
            candidate_payload = (
                json.loads(candidate_path.read_text(encoding="utf-8")) if candidate_path.exists() else []
            )
            active_payload = json.loads(active_path.read_text(encoding="utf-8")) if active_path.exists() else {}
            active_crypto_payload = (
                json.loads(active_crypto_path.read_text(encoding="utf-8")) if active_crypto_path.exists() else {}
            )
            candidate_items = (
                candidate_payload.get(
                    "candidates",
                    candidate_payload.get("symbols", candidate_payload),
                )
                if isinstance(candidate_payload, dict)
                else candidate_payload
            )
            result["candidate_universe_size"] = len(candidate_items) if isinstance(candidate_items, (list, dict)) else 0
            equity_symbols, validated_scanner_status = _validate_active_universe(cfg.universe.equity)
            crypto_symbols, validated_crypto_scanner_status = _validate_active_crypto_universe(cfg.universe.crypto)
            if runtime is None:
                result["active_equity_symbol_count"] = len(equity_symbols)
                result["active_crypto_symbol_count"] = len(crypto_symbols)
            result["scanner"] = {
                "last_run_at": active_payload.get("selection_timestamp"),
                "status": runtime_refresh.get("status", {}).get("equity", validated_scanner_status),
                "equity_status": runtime_refresh.get("status", {}).get("equity", validated_scanner_status),
                "crypto_status": runtime_refresh.get("status", {}).get("crypto", validated_crypto_scanner_status),
                "equity_snapshot_status": validated_scanner_status,
                "crypto_snapshot_status": validated_crypto_scanner_status,
                "scanner_version": active_payload.get("scanner_version"),
                "crypto_last_run_at": active_crypto_payload.get("selection_timestamp"),
                "crypto_scanner_version": active_crypto_payload.get("scanner_version"),
                "next_refresh_at": runtime_refresh.get("next_refresh_at"),
                "refresh_errors": runtime_refresh.get("errors", {}),
                "equity_candidate_count": len(active_payload.get("candidate_symbols", ())),
                "equity_shortlist_count": len(
                    candidate_payload.get("shortlist_symbols", ()) if isinstance(candidate_payload, dict) else ()
                ),
                "equity_sector_metadata_coverage": active_payload.get("sector_metadata_coverage"),
                "crypto_candidate_count": len(active_crypto_payload.get("candidate_symbols", ())),
            }
            result["universe"] = {
                "equity": {
                    "symbols": list(active_payload.get("observation_set", active_payload.get("selected_symbols", ()))),
                    "pool_size": len(active_payload.get("selected_symbols", ())),
                    "scores": active_payload.get("score_breakdown", {}),
                    "eliminated": active_payload.get("eliminated", {}),
                },
                "crypto": {
                    "symbols": list(
                        active_crypto_payload.get("observation_set", active_crypto_payload.get("selected_symbols", ()))
                    ),
                    "pool_size": len(active_crypto_payload.get("selected_symbols", ())),
                    "scores": active_crypto_payload.get("score_breakdown", {}),
                    "eliminated": active_crypto_payload.get("eliminated", {}),
                },
            }
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            result["scanner"] = {"last_run_at": None, "status": "error"}
    return result
