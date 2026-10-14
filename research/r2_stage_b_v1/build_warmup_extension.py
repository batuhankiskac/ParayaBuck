#!/usr/bin/env python3
"""Build the R2 warm-up extension for the Stage B substitute bars.

R2 (``equity_leveraged_trend_v1`` 1.2.0) declares ``minimum_history_bars = 397``
(the 126-session replay needs a 252-session vol median defined at index 271), so
the engine's LIVE-parity warm-up gate refuses to decide until the signal symbol
(QQQ) and the hold symbol (QLD) each have 397 completed closes. The Stage B
substitute file starts 2016-01-04 and has 252 QQQ sessions before the
2017-01-03 evaluation start, so R2 would start deciding ~145 sessions later
than E2. This script extends the warm-up backwards with the only pre-2016
QQQ/QLD/SPY series in the repository: ``data/historical/
stress_robustness_v1_daily.csv`` (the committed Yahoo daily file the
fast_reentry study used as its production price basis).

Basis check (in ``main``): on the 2,659 overlapping sessions the Yahoo ``close``
equals the Alpaca SIP split-adjusted ``close`` of the substitute file to a
median relative difference of 2.2e-8 (worst 9.3e-4). Both are split-adjusted
price closes; neither carries dividends, so the extension adds no corporate
actions (the replay ledger opens 2017-01-03 anyway).

Writes (deterministic, byte-stable):
  stage_b/data/historical/warmup_yahoo_2014_2015_bars_qqq_qld_spy.csv
  stage_b/data/historical/substitute_v5_plus_warmup_bars_qqq_spy_qld.csv
The combined file is the extension followed by the unmodified substitute rows.
"""

from __future__ import annotations

import csv
import hashlib
from datetime import date, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

import exchange_calendars as xcals
import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]  # worktree root
HIST = ROOT / "data/historical"
STAGE = HERE / "stage_b" / "data" / "historical"
YAHOO = HIST / "stress_robustness_v1_daily.csv"
SUBSTITUTE = STAGE / "substitute_v5_bars_qqq_spy_qld.csv"
EXTENSION = STAGE / "warmup_yahoo_2014_2015_bars_qqq_qld_spy.csv"
COMBINED = STAGE / "substitute_v5_plus_warmup_bars_qqq_spy_qld.csv"

SYMBOLS = ("QLD", "QQQ", "SPY")
EXTENSION_START = date(2014, 1, 2)
EXTENSION_END = date(2016, 1, 1)  # the substitute file starts 2016-01-04
NEW_YORK = ZoneInfo("America/New_York")
FIELDS = ("timestamp", "symbol", "timeframe", "open", "high", "low", "close", "volume", "market")
CALENDAR = xcals.get_calendar("XNYS", start="2013-12-01", end="2016-01-31")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _yahoo_rows() -> dict[str, dict[date, dict[str, str]]]:
    rows: dict[str, dict[date, dict[str, str]]] = {symbol: {} for symbol in SYMBOLS}
    with YAHOO.open() as handle:
        for row in csv.DictReader(handle):
            symbol = row["symbol"].upper()
            if symbol in rows:
                rows[symbol][date.fromisoformat(row["date"])] = row
    return rows


def _label(session: date) -> str:
    """Session-local midnight in New York, exactly the substitute file's convention."""
    stamp = datetime.combine(session, time(0, 0), tzinfo=NEW_YORK).astimezone(ZoneInfo("UTC"))
    return stamp.isoformat()


def main() -> int:
    sessions = [
        session.date()
        for session in CALENDAR.sessions_in_range(pd.Timestamp(EXTENSION_START), pd.Timestamp(EXTENSION_END))
    ]
    yahoo = _yahoo_rows()

    # Basis check on the overlap: Yahoo close vs SIP split-adjusted close.
    substitute = list(csv.DictReader(SUBSTITUTE.open()))
    sip_qqq = {row["timestamp"][:10]: float(row["close"]) for row in substitute if row["symbol"] == "QQQ"}
    differences = sorted(
        abs(float(yahoo["QQQ"][day]["close"]) / sip_qqq[day.isoformat()] - 1.0)
        for day in sorted(yahoo["QQQ"])
        if day.isoformat() in sip_qqq
    )
    print(
        f"QQQ overlap {len(differences)} sessions, median rel diff {differences[len(differences) // 2]:.3e}, "
        f"worst {differences[-1]:.3e}"
    )

    missing = {
        symbol: [day for day in sessions if day not in yahoo[symbol]]
        for symbol in SYMBOLS
    }
    for symbol, days in missing.items():
        if days:
            raise SystemExit(f"{symbol}: no Yahoo bar for {len(days)} sessions, first {days[0]}")

    extension: list[dict[str, str]] = []
    for session in sessions:
        for symbol in SYMBOLS:  # symbol-sorted rows, like the substitute file
            row = yahoo[symbol][session]
            extension.append(
                {
                    "timestamp": _label(session),
                    "symbol": symbol,
                    "timeframe": "1d",
                    "open": repr(round(float(row["open"]), 4)),
                    "high": repr(round(float(row["high"]), 4)),
                    "low": repr(round(float(row["low"]), 4)),
                    "close": repr(round(float(row["close"]), 4)),
                    "volume": repr(float(row["volume"])),
                    "market": "equity",
                }
            )

    def write(path: Path, rows: list[dict[str, str]]) -> None:
        with path.open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=FIELDS, lineterminator="\n")
            writer.writeheader()
            writer.writerows(rows)

    write(EXTENSION, extension)
    write(COMBINED, extension + substitute)
    print(
        f"{EXTENSION.name}: {len(extension)} rows ({sessions[0]} .. {sessions[-1]}), sha256 {_sha256(EXTENSION)[:16]}\n"
        f"{COMBINED.name}: {len(extension) + len(substitute)} rows, sha256 {_sha256(COMBINED)[:16]}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
