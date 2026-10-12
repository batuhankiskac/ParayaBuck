"""Runtime internal event queue — extracted from runtime.py.

Preserves thread synchronization, coalescing, overflow, and ordering. Do not
replace with asyncio.Queue.

The only market events are finalized strategy-cadence bars (the daily
decision bar). They are never evicted: a full queue coalesces a repeat of the
same bar and otherwise reports overflow, which the runtime turns into a halt
instead of silently dropping a decision input.
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
    bar: Bar
    received_at: datetime


class _EventQueue:
    """Bounded queue with same-bar coalescing and a no-eviction overflow policy."""

    def drain_for_test(self) -> tuple[_Event | None, ...]:
        """Return the queued items without removing them (tests only)."""
        with self._condition:
            return tuple(self._items)

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

    def qsize(self) -> int:
        with self._condition:
            return len(self._items)

    def empty(self) -> bool:
        return self.qsize() == 0

    def put_market_event(self, event: _Event) -> str:
        """Queue ``event``; returns ``added``, ``coalesced`` or ``overflow``.

        A full queue replaces a queued event for the SAME bar (market,
        symbol, timeframe, timestamp) with the newer one. It never evicts a
        different bar: every queued event is a decision input.
        """
        with self._condition:
            if len(self._items) < self.maxsize:
                self._items.append(event)
                self._condition.notify()
                return "added"
            key = (event.bar.market, event.bar.symbol, event.bar.timeframe, event.bar.timestamp)
            for index in range(len(self._items) - 1, -1, -1):
                candidate = self._items[index]
                if candidate is None:
                    continue
                if (
                    candidate.bar.market,
                    candidate.bar.symbol,
                    candidate.bar.timeframe,
                    candidate.bar.timestamp,
                ) == key:
                    self._items[index] = event
                    return "coalesced"
            return "overflow"

    def oldest_received_at(self) -> datetime | None:
        with self._condition:
            received = [item.received_at for item in self._items if item is not None]
            return min(received) if received else None


__all__ = ["_Event", "_EventQueue"]
