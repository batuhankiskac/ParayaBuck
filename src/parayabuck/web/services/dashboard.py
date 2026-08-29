"""Dashboard aggregation — thin wrapper over existing server helpers."""

from __future__ import annotations

from typing import Any


def get_overview_payload() -> dict[str, Any]:
    # Use legacy dashboard payload but with the current (possibly patched) manager
    # and also respect monkeypatched _dashboard_market_decisions on server
    try:
        import parayabuck.web.server as srv  # type: ignore
        import parayabuck.web.server_legacy as leg  # type: ignore

        # Patch legacy's global manager to the current (test-patched) one
        orig_manager = getattr(leg, "manager", None)
        orig_backtest = getattr(leg, "backtest_manager", None)
        orig_decisions = getattr(leg, "_dashboard_market_decisions", None)
        # Check if server has a custom _dashboard_market_decisions (monkeypatched)
        srv_decisions = getattr(srv, "_dashboard_market_decisions", None)
        # Determine if srv's version is different from leg's original (i.e., patched)
        is_patched_decisions = srv_decisions is not None and srv_decisions is not orig_decisions
        try:
            leg.manager = srv.manager  # type: ignore
            try:
                leg.backtest_manager = srv.backtest_manager  # type: ignore
            except Exception:
                pass
            if is_patched_decisions:
                leg._dashboard_market_decisions = srv_decisions  # type: ignore
            return leg._dashboard_payload()  # type: ignore
        finally:
            try:
                leg.manager = orig_manager  # type: ignore
                leg.backtest_manager = orig_backtest  # type: ignore
                if is_patched_decisions:
                    leg._dashboard_market_decisions = orig_decisions  # type: ignore
            except Exception:
                pass
    except Exception as exc:  # pragma: no cover
        return {"unavailable": True, "error": str(exc)}


def get_panel(key: str) -> dict[str, Any]:
    payload = get_overview_payload()
    if isinstance(payload, dict) and key in payload:
        return payload[key]  # type: ignore
    return {"unavailable": True, "error": f"panel {key} not found"}
