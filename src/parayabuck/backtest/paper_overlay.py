"""Overlay an operator-supplied PAPER/LIVE equity series on a backtest curve.

Pure helper: it takes an already computed backtest result and a list of
``(date, equity)`` points. It never reads the live database and never calls a
broker. Both series are aligned on the dates they share and normalised to 1.0
on the first shared date. For each shared date the ratio is
``paper_normalised / backtest_normalised``; ``gap = ratio - 1``.

Summary definitions:

- ``tracking_difference``: the final ratio minus 1.
- ``tracking_error_annualised``: sample standard deviation of the daily
  return differences (PAPER return minus backtest return, consecutive shared
  dates only) times ``sqrt(periods_per_year)``; ``None`` with fewer than two
  return differences.
- ``max_abs_gap``: the largest ``|gap|`` over the shared dates, with its date.
- ``first_threshold_breach_date``: the first shared date where ``|gap|``
  exceeds ``gap_threshold`` (default 2%), or ``None``.
"""

from __future__ import annotations

import csv
import math
import re
from collections.abc import Sequence
from datetime import date, datetime
from itertools import pairwise
from pathlib import Path
from statistics import stdev
from typing import Any

from parayabuck.backtest.portfolio_engine import session_date_for

GAP_THRESHOLD = 0.02
PERIODS_PER_YEAR = 252.0
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _check_equity(value: float, label: str) -> float:
    equity = float(value)
    if not math.isfinite(equity) or equity <= 0:
        raise ValueError(f"{label} must be positive and finite, got {value!r}")
    return equity


def _backtest_by_session(backtest_result: Any) -> dict[date, float]:
    timestamps = tuple(backtest_result.curve_timestamps)
    curve = tuple(backtest_result.equity_curve)
    if len(timestamps) != len(curve):
        raise ValueError("backtest equity_curve and curve_timestamps differ in length")
    by_session: dict[date, float] = {}
    for timestamp, equity in zip(timestamps, curve, strict=True):
        if not isinstance(timestamp, datetime):
            raise ValueError("backtest curve timestamps must be datetimes")
        # The curve is time ordered, so the last value of a session is its close.
        by_session[session_date_for(timestamp)] = _check_equity(equity, f"backtest equity at {timestamp.isoformat()}")
    return by_session


def _paper_by_date(points: Sequence[tuple[date, float]]) -> dict[date, float]:
    by_date: dict[date, float] = {}
    for day, equity in points:
        if not isinstance(day, date) or isinstance(day, datetime):
            raise ValueError(f"paper equity dates must be calendar dates, got {day!r}")
        if day in by_date:
            raise ValueError(f"paper equity has a duplicate date {day.isoformat()}")
        by_date[day] = _check_equity(equity, f"paper equity on {day.isoformat()}")
    return by_date


def paper_overlay(
    backtest_result: Any,
    paper_equity: Sequence[tuple[date, float]],
    *,
    gap_threshold: float = GAP_THRESHOLD,
    periods_per_year: float = PERIODS_PER_YEAR,
) -> dict[str, Any]:
    """Align a backtest equity curve with PAPER/LIVE equity and summarise the gap.

    Returns ``{"summary": {...}, "rows": [...]}``. Each row has the ISO ``date``,
    both normalised series, ``ratio`` and ``gap``. Raises ``ValueError`` on
    malformed input or fewer than two shared dates.
    """
    if not math.isfinite(gap_threshold) or gap_threshold < 0:
        raise ValueError("gap_threshold must be finite and non-negative")
    if not math.isfinite(periods_per_year) or periods_per_year <= 0:
        raise ValueError("periods_per_year must be positive and finite")
    backtest = _backtest_by_session(backtest_result)
    paper = _paper_by_date(paper_equity)
    common = sorted(backtest.keys() & paper.keys())
    if len(common) < 2:
        raise ValueError(f"the backtest and paper series share {len(common)} date(s); at least 2 are required")

    first = common[0]
    rows: list[dict[str, Any]] = []
    for day in common:
        backtest_normalised = backtest[day] / backtest[first]
        paper_normalised = paper[day] / paper[first]
        ratio = paper_normalised / backtest_normalised
        rows.append(
            {
                "date": day.isoformat(),
                "backtest_normalised": backtest_normalised,
                "paper_normalised": paper_normalised,
                "ratio": ratio,
                "gap": ratio - 1.0,
            }
        )

    worst = max(rows, key=lambda row: abs(row["gap"]))  # max returns the first of equal maxima
    breach = next((row["date"] for row in rows if abs(row["gap"]) > gap_threshold), None)

    differences = [
        (paper[cur] / paper[prev] - 1.0) - (backtest[cur] / backtest[prev] - 1.0) for prev, cur in pairwise(common)
    ]
    tracking_error: float | None = None
    if len(differences) >= 2:
        tracking_error = stdev(differences) * math.sqrt(periods_per_year)

    summary: dict[str, Any] = {
        "common_days": len(common),
        "first_common_date": common[0].isoformat(),
        "last_common_date": common[-1].isoformat(),
        "tracking_difference": rows[-1]["ratio"] - 1.0,
        "tracking_error_annualised": tracking_error,
        "max_abs_gap": abs(worst["gap"]),
        "max_abs_gap_date": worst["date"],
        "gap_threshold": gap_threshold,
        "first_threshold_breach_date": breach,
        "periods_per_year": periods_per_year,
    }
    return {"summary": summary, "rows": rows}


def load_paper_equity_csv(path: str | Path) -> list[tuple[date, float]]:
    """Read a two-column ``date,equity`` CSV (ISO dates, strictly increasing).

    A single header row ``date,equity`` is allowed; blank lines are skipped.
    Any other malformed row raises ``ValueError`` naming the line.
    """
    with Path(path).open(newline="", encoding="utf-8") as handle:
        reader = csv.reader(handle)
        points: list[tuple[date, float]] = []
        for row in reader:
            if not row or all(not cell.strip() for cell in row):
                continue
            line = reader.line_num
            if not points and [cell.strip().lower() for cell in row] == ["date", "equity"]:
                continue
            if len(row) != 2:
                raise ValueError(f"paper equity CSV line {line}: expected 2 columns, got {len(row)}")
            day_text, equity_text = (cell.strip() for cell in row)
            if not _ISO_DATE.match(day_text):
                raise ValueError(f"paper equity CSV line {line}: date must be YYYY-MM-DD, got {day_text!r}")
            try:
                day = date.fromisoformat(day_text)
                equity = float(equity_text)
            except ValueError as exc:
                raise ValueError(f"paper equity CSV line {line}: {exc}") from exc
            if not math.isfinite(equity) or equity <= 0:
                raise ValueError(f"paper equity CSV line {line}: equity must be positive and finite")
            if points and day <= points[-1][0]:
                raise ValueError(f"paper equity CSV line {line}: dates must be strictly increasing")
            points.append((day, equity))
    if not points:
        raise ValueError("paper equity CSV has no data rows")
    return points
