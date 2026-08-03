from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from parayabuck.core.models import Direction


@dataclass(frozen=True)
class StopReplaceResult:
    succeeded: bool
    active_stop: float
    requested_stop: float
    reason: str


def safely_replace_stop(
    *,
    direction: Direction,
    current_stop: float,
    requested_stop: float,
    replace: Callable[[float], bool],
) -> StopReplaceResult:
    """Tighten a live protective stop without ever cancelling protection first.

    The broker callback must perform an atomic replace. A false result or an
    exception leaves the old stop authoritative and reports a failed dynamic
    update; callers must never emulate replace with cancel-then-submit.
    """
    tighter = (
        requested_stop > current_stop
        if direction == Direction.LONG
        else requested_stop < current_stop
    )
    if direction not in {Direction.LONG, Direction.SHORT} or not tighter:
        return StopReplaceResult(False, current_stop, requested_stop, "stop_would_loosen")
    try:
        succeeded = replace(requested_stop)
    except Exception:
        succeeded = False
    if not succeeded:
        return StopReplaceResult(False, current_stop, requested_stop, "atomic_replace_failed")
    return StopReplaceResult(True, requested_stop, requested_stop, "replaced")


__all__ = ["StopReplaceResult", "safely_replace_stop"]
