"""Dashboard aggregation — thin wrapper over existing server helpers."""

from __future__ import annotations

from typing import Any


def _context(state: Any | None) -> Any:
    import parayabuck.web.server as srv
    import parayabuck.web.server_legacy as leg

    if state is None:
        state = getattr(srv, "web_state", None)
    engine_manager = getattr(state, "engine_manager", None) if state is not None else getattr(srv, "manager", None)
    backtest_manager = (
        getattr(state, "backtest_manager", None) if state is not None else getattr(srv, "backtest_manager", None)
    )
    callback = getattr(srv, "_dashboard_market_decisions", None)
    if not callable(callback):
        callback = None
    return leg.DashboardContext(
        engine_manager=engine_manager,
        backtest_manager=backtest_manager,
        market_decisions=callback,
    )


def get_overview_payload(state: Any | None = None) -> dict[str, Any]:
    try:
        import parayabuck.web.server_legacy as leg

        return leg._dashboard_payload(context=_context(state))
    except Exception as exc:  # pragma: no cover
        return {"unavailable": True, "error": str(exc)}


def get_panel(key: str, state: Any | None = None) -> dict[str, Any]:
    payload = get_overview_payload(state)
    if isinstance(payload, dict) and key in payload:
        return payload[key]  # type: ignore[no-any-return]
    return {"unavailable": True, "error": f"panel {key} not found"}
