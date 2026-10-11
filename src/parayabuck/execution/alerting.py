"""Shared execution alert emission (order flow is never affected)."""

from __future__ import annotations

from parayabuck.logger import get_logger

log = get_logger("parayabuck.execution.alpaca")


def emit_execution_alert(kind: str, *, message: str, **fields: object) -> None:
    """Emit one execution alert without ever affecting order flow."""
    try:
        from parayabuck.monitoring import alerts
    except ImportError:
        log.debug("alerts module unavailable; skipping %s alert", kind)
        return
    try:
        alerts.emit(kind, message=message, **fields)
    except Exception:
        log.warning("Alert emission failed for kind=%s (order flow unaffected)", kind, exc_info=True)


__all__ = ["emit_execution_alert"]
