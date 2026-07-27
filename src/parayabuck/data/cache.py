from __future__ import annotations

import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path

from parayabuck.core.models import Bar, Market
from parayabuck.markets.symbols import canonical_symbol


class WarmupCache:
    def __init__(self, path: str = "data/cache/warmup.sqlite3") -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path, check_same_thread=False)
        self._lock = threading.Lock()
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS bars (
                feed TEXT NOT NULL,
                symbol TEXT NOT NULL,
                market TEXT NOT NULL,
                timeframe TEXT NOT NULL,
                adjustment TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                open REAL NOT NULL,
                high REAL NOT NULL,
                low REAL NOT NULL,
                close REAL NOT NULL,
                volume REAL NOT NULL,
                retrieved_at TEXT NOT NULL,
                PRIMARY KEY(feed, symbol, timeframe, adjustment, timestamp)
            )
            """
        )
        self.connection.commit()

    def upsert(self, bars: list[Bar], *, feed: str, adjustment: str = "all") -> None:
        retrieved_at = datetime.now(UTC).isoformat()
        rows = [
            (
                feed,
                canonical_symbol(bar.symbol, bar.market),
                bar.market.value,
                bar.timeframe,
                adjustment,
                bar.timestamp.isoformat(),
                bar.open,
                bar.high,
                bar.low,
                bar.close,
                bar.volume,
                retrieved_at,
            )
            for bar in bars
        ]
        if not rows:
            return
        with self._lock:
            self.connection.executemany(
                """
                INSERT INTO bars VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(feed, symbol, timeframe, adjustment, timestamp) DO UPDATE SET
                    open=excluded.open, high=excluded.high, low=excluded.low,
                    close=excluded.close, volume=excluded.volume,
                    retrieved_at=excluded.retrieved_at
                """,
                rows,
            )
            self.connection.commit()

    def get(
        self,
        symbol: str,
        timeframe: str,
        *,
        feed: str,
        start: datetime,
        end: datetime,
        adjustment: str = "all",
    ) -> list[Bar]:
        canonical = canonical_symbol(symbol)
        with self._lock:
            rows = self.connection.execute(
                """
                SELECT timestamp, open, high, low, close, volume, market
                FROM bars
                WHERE feed=? AND symbol=? AND timeframe=? AND adjustment=?
                  AND timestamp>=? AND timestamp<=?
                ORDER BY timestamp
                """,
                (
                    feed,
                    canonical,
                    timeframe,
                    adjustment,
                    start.isoformat(),
                    end.isoformat(),
                ),
            ).fetchall()
        return [
            Bar(
                timestamp=datetime.fromisoformat(row[0]),
                symbol=canonical,
                timeframe=timeframe,
                open=float(row[1]),
                high=float(row[2]),
                low=float(row[3]),
                close=float(row[4]),
                volume=float(row[5]),
                market=Market(row[6]),
            )
            for row in rows
        ]


__all__ = ["WarmupCache"]
