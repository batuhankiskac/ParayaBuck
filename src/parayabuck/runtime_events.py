"""Runtime internal event queue — extracted from runtime.py.

Preserves thread synchronization, coalescing, replacement, overflow, and
ordering. Do not replace with asyncio.Queue in this mission.
"""

from __future__ import annotations

import queue
import threading
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime

from parayabuck.core.models import Bar


@dataclass(frozen=True)
class _Event:
    kind: str
    bar: Bar
    received_at: datetime


class _EventQueue:
    """Bounded queue with domain-aware coalescing and overflow policy."""

    def __init__(self, maxsize: int) -> None:
        self.maxsize = maxsize
        self._items: deque[_Event | None] = deque()
        self._condition = threading.Condition()

    def put_nowait(self, item: _Event | None) -> None:
        with self._condition:
            if len(self._items) >= self.maxsize:
                raise queue.Full
            self._items.append(item)
            self._condition.notify()

    def get(self, timeout: float | None = None) -> _Event | None:
        deadline = None if timeout is None else time.monotonic() + timeout
        with self._condition:
            while not self._items:
                remaining = None if deadline is None else deadline - time.monotonic()
                if remaining is not None and remaining <= 0:
                    raise queue.Empty
                self._condition.wait(remaining)
            return self._items.popleft()

    def get_nowait(self) -> _Event | None:
        with self._condition:
            if not self._items:
                raise queue.Empty
            return self._items.popleft()

    def qsize(self) -> int:
        with self._condition:
            return len(self._items)

    def empty(self) -> bool:
        return self.qsize() == 0

    def put_market_event(self, event: _Event) -> str:
        with self._condition:
            if len(self._items) < self.maxsize:
                self._items.append(event)
                self._condition.notify()
                return "added"
            key = (event.kind, event.bar.symbol, event.bar.timeframe, event.bar.timestamp)
            for index in range(len(self._items) - 1, -1, -1):
                candidate = self._items[index]
                if candidate is None:
                    continue
                candidate_key = (
                    candidate.kind,
                    candidate.bar.symbol,
                    candidate.bar.timeframe,
                    candidate.bar.timestamp,
                )
                if candidate_key == key:
                    self._items[index] = event
                    return "coalesced"
            if event.kind == "primary":
                return "overflow"
            victim = None
            for index in range(len(self._items) - 1, -1, -1):
                candidate = self._items[index]
                if candidate is not None and candidate.kind != "primary":
                    victim = index
                    break
            if victim is None:
                return "overflow"
            self._items[victim] = event
            return "replaced"

    def snapshot(self) -> tuple[_Event | None, ...]:
        with self._condition:
            return tuple(self._items)

    def oldest_received_at(self) -> datetime | None:
        with self._condition:
            received = [item.received_at for item in self._items if item is not None]
            return min(received) if received else None


# Public aliases — EngineRuntime and external callers should use these.
# Private names kept for backward compatibility with tests that patch them.
RuntimeEvent = _Event
EventQueue = _EventQueue

__all__ = ["RuntimeEvent", "EventQueue", "_Event", "_EventQueue"]
