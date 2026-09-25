"""Account snapshot service — extracted fetch_account_snapshot caching logic.

Wraps EngineManager's broker snapshot with TTL 5s and a threading.Lock.
The manager remains the state holder (last_account, _last_account_monotonic,
_broker, _account_lock, _account_cache_ttl_seconds) so patching via
state injection and _state_lock atomicity are preserved. This module
owns the *logic* (caching policy, broker lazy-init, stale fallback) not the
data.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Any

from parayabuck.logger import get_logger

log = get_logger("parayabuck.web.services.account")

DEFAULT_TTL_SECONDS = 5.0


def fetch_account_snapshot(manager: Any) -> dict[str, Any]:
    """Return account snapshot with 5s cache, thread-safe via manager._account_lock.

    Mirrors EngineManager.fetch_account_snapshot + _refresh_account_snapshot
    verbatim from server.py, so routers can call this service without
    importing manager internals directly.
    """
    lock = getattr(manager, "_account_lock", None)
    # Fallback to global lock if manager is a test stub without lock
    if lock is None:
        return _refresh_account_snapshot(manager)

    with lock:
        now = time.monotonic()
        ttl = getattr(manager, "_account_cache_ttl_seconds", DEFAULT_TTL_SECONDS)
        last = getattr(manager, "last_account", None)
        monotonic = getattr(manager, "_last_account_monotonic", 0.0)
        if last is not None and now - monotonic < ttl:
            return {
                **last,
                "age_seconds": max(0.0, now - monotonic),
                "stale": False,
                "source": "cache",
            }
        return _refresh_account_snapshot(manager)


def _refresh_account_snapshot(manager: Any) -> dict[str, Any]:
    """Internal refresh; caller must hold manager._account_lock if present."""
    broker = getattr(manager, "broker", None)
    if broker is None:
        key, secret = manager.get_credentials()
        if key and secret:
            try:
                from parayabuck.execution.alpaca import AlpacaBroker

                paper = manager.is_paper_mode()
                manager.broker = AlpacaBroker(key, secret, paper=paper)
                broker = manager.broker
            except Exception as e:
                log.warning("Could not connect broker: %s", e)

    if broker is not None:
        try:
            acct = broker.account()
            managed: dict[str, Any] = {}
            runtime = getattr(manager, "runtime", None)
            runtime_ledger = getattr(runtime, "ledger", None) if runtime is not None else None
            broker_account_id = getattr(broker, "account_id", None)
            if runtime_ledger is not None and bool(broker_account_id):
                try:
                    managed = runtime_ledger.managed_for_account(broker_account_id)
                except Exception:
                    managed = {}

            positions_list: list[dict[str, Any]] = []
            for p in acct.positions:
                if not p.is_flat:
                    owned = managed.get(p.symbol)
                    positions_list.append(
                        {
                            "symbol": p.symbol,
                            "market": p.market.value,
                            "direction": "LONG" if p.qty > 0 else "SHORT",
                            "total_qty": abs(p.qty),
                            "qty": abs(p.qty),
                            "managed_qty": p.managed_qty,
                            "external_qty": p.external_qty,
                            "ownership_state": p.ownership,
                            "avg_price": p.avg_price,
                            "current_price": p.current_price,
                            "market_value": p.market_value,
                            "unrealized_pnl": p.unrealized_pnl,
                            "ownership": p.ownership,
                            "protection_state": (owned.protection_state.value if owned is not None else None),
                            "stop_price": (owned.current_stop or owned.original_stop if owned is not None else None),
                            "target_price": owned.original_target if owned is not None else None,
                            "stop_order_state": owned.stop_state if owned is not None else None,
                            "target_exit_state": (owned.target_state if owned is not None else None),
                            "manual_intervention_required": bool(
                                owned is not None and owned.status == "manual_intervention_required"
                            ),
                        }
                    )

            snapshot = {
                "equity": acct.equity,
                "cash": acct.cash,
                "daily_pnl": acct.daily_pnl,
                "realized_pnl": acct.realized_pnl,
                "unrealized_pnl": acct.unrealized_pnl,
                "fees": acct.fees,
                "drawdown": max(0.0, acct.peak_equity - acct.equity),
                "gross_exposure": sum(position.market_value for position in acct.positions if not position.is_flat),
                "positions": positions_list,
                "account_suffix": getattr(broker, "account_suffix", ""),
                "snapshot_at": datetime.now(UTC).isoformat(),
                "age_seconds": 0.0,
                "stale": False,
                "source": "broker",
            }
            manager.last_account = snapshot
            manager._last_account_monotonic = time.monotonic()
            return snapshot
        except Exception as e:
            log.warning("Error fetching account snapshot: %s", e)

    last_account = getattr(manager, "last_account", None)
    if last_account:
        monotonic = getattr(manager, "_last_account_monotonic", 0.0)
        age = max(0.0, time.monotonic() - monotonic)
        return {
            **last_account,
            "age_seconds": age,
            "stale": True,
            "source": "cache",
        }

    return {
        "equity": 0.0,
        "cash": 0.0,
        "daily_pnl": 0.0,
        "realized_pnl": 0.0,
        "unrealized_pnl": 0.0,
        "drawdown": 0.0,
        "positions": [],
        "account_suffix": "",
        "snapshot_at": None,
        "age_seconds": None,
        "stale": True,
        "source": "unavailable",
    }
