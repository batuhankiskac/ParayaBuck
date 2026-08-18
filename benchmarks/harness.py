"""Deterministic timing/memory harness for the ParayaBuck optimization tournament.

Methodology (from the tournament rules):
- fast operations: warmup runs + >= 10 measured runs
- expensive operations (backtests): >= 3 measured runs
- report median, p95, min, max, variance on the SAME machine/process where possible
"""

from __future__ import annotations

import gc
import math
import os
import resource
import statistics
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TypeVar

T = TypeVar("T")


@dataclass(frozen=True)
class Timing:
    label: str
    samples: tuple[float, ...]
    unit: str = "s"

    @property
    def median(self) -> float:
        return statistics.median(self.samples)

    @property
    def p95(self) -> float:
        ordered = sorted(self.samples)
        if not ordered:
            return float("nan")
        index = min(len(ordered) - 1, math.ceil(0.95 * len(ordered)) - 1)
        return ordered[index]

    @property
    def minimum(self) -> float:
        return min(self.samples)

    @property
    def maximum(self) -> float:
        return max(self.samples)

    @property
    def variance(self) -> float:
        return statistics.variance(self.samples) if len(self.samples) > 1 else 0.0

    def row(self, scale: float = 1.0) -> str:
        return (
            f"{self.label:<44} med={self.median / scale:>10.4f} "
            f"p95={self.p95 / scale:>10.4f} min={self.minimum / scale:>10.4f} "
            f"max={self.maximum / scale:>10.4f} n={len(self.samples)}"
        )


def timeit(
    fn: Callable[[], T],
    *,
    label: str,
    warmup: int = 3,
    runs: int = 10,
    unit: str = "s",
    before_each: Callable[[], None] | None = None,
) -> tuple[Timing, T | None]:
    """Time ``fn`` with warmup runs; returns (Timing, last result)."""
    gc.disable()
    try:
        for _ in range(warmup):
            fn()
        samples: list[float] = []
        last: T | None = None
        for _ in range(runs):
            if before_each is not None:
                before_each()
            start = time.perf_counter()
            last = fn()
            elapsed = time.perf_counter() - start
            samples.append(elapsed)
        timing = Timing(label, tuple(samples), unit=unit)
        return timing, last
    finally:
        gc.enable()


@dataclass
class Report:
    timings: list[Timing] = field(default_factory=list)

    def add(self, timing: Timing) -> None:
        self.timings.append(timing)

    def print_table(self, scale: float = 1.0) -> None:
        for timing in self.timings:
            print(timing.row(scale=scale))


def _rusage_rss_bytes() -> int:
    raw = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    # macOS reports bytes, Linux reports KiB.
    if os.uname().sysname == "Darwin":
        return raw
    return raw * 1024


def rss_bytes() -> int:
    """Current process RSS in bytes (macOS/Linux)."""
    return _rusage_rss_bytes()


def peak_rss_bytes() -> int:
    return _rusage_rss_bytes()


def force_gc() -> None:
    gc.collect()


def mb(bytes_: int) -> float:
    return bytes_ / (1024 * 1024)
