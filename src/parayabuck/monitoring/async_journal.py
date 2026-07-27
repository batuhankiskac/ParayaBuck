from __future__ import annotations

import queue
import threading
from collections.abc import Callable
from typing import Any

from parayabuck.core.models import AccountSnapshot, Bar, Fill, OrderIntent, OrderState, Signal
from parayabuck.markets.regime import RegimeAssessment
from parayabuck.monitoring.journal import DecisionJournal


class AsyncDecisionJournal:
    """Bounded writer: scan noise may drop, order/fill/risk events may not."""

    def __init__(
        self,
        journal: DecisionJournal,
        capacity: int = 2_000,
        on_critical_failure: Callable[[str], None] | None = None,
    ) -> None:
        self.journal = journal
        self._queue: queue.Queue[tuple[str, tuple[Any, ...], bool] | None] = queue.Queue(
            maxsize=capacity
        )
        self._on_critical_failure = on_critical_failure
        self.dropped_scans = 0
        self.last_error: str | None = None
        self._thread = threading.Thread(target=self._run, name="pb-journal", daemon=True)
        self._thread.start()

    def record_scan(self, bar: Bar, assessment: RegimeAssessment, reason: str) -> None:
        self._enqueue("record_scan", (bar, assessment, reason), critical=False)

    def record_decision(self, signal: Signal, accepted: bool, reason: str) -> None:
        self._enqueue("record_decision", (signal, accepted, reason), critical=True)

    def record_order(self, order: OrderIntent, state: OrderState, reason: str = "") -> None:
        self._enqueue("record_order", (order, state, reason), critical=True)

    def record_fill(self, fill: Fill) -> None:
        self._enqueue("record_fill", (fill,), critical=True)

    def record_account(self, account: AccountSnapshot) -> None:
        self._enqueue("record_account", (account,), critical=True)

    def _enqueue(self, method: str, args: tuple[Any, ...], critical: bool) -> None:
        item = (method, args, critical)
        try:
            if critical:
                self._queue.put(item, timeout=1.0)
            else:
                self._queue.put_nowait(item)
        except queue.Full:
            if critical:
                self._fail(f"critical journal queue full while recording {method}")
            else:
                self.dropped_scans += 1

    def _run(self) -> None:
        while True:
            item = self._queue.get()
            if item is None:
                self._queue.task_done()
                return
            method, args, critical = item
            try:
                getattr(self.journal, method)(*args)
            except Exception as exc:
                self.last_error = str(exc)
                if critical:
                    self._fail(f"critical journal write failed: {method}")
            finally:
                self._queue.task_done()

    def _fail(self, reason: str) -> None:
        self.last_error = reason
        if self._on_critical_failure is not None:
            self._on_critical_failure(reason)

    def close(self) -> None:
        self._queue.join()
        self._queue.put(None)
        self._thread.join(timeout=5)


__all__ = ["AsyncDecisionJournal"]
