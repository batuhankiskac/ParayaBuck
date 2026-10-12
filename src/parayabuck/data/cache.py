from __future__ import annotations

import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path

from parayabuck.core.models import Bar, Market
from parayabuck.logger import get_logger
from parayabuck.markets.symbols import canonical_symbol

log = get_logger("parayabuck.data.cache")


class WarmupCache:
    _SCHEMA = """
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
            PRIMARY KEY(feed, symbol, market, timeframe, adjustment, timestamp)
        )
    """
    _COLUMNS = (
        "feed",
        "symbol",
        "market",
        "timeframe",
        "adjustment",
        "timestamp",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "retrieved_at",
    )
    _PRIMARY_KEY = ("feed", "symbol", "market", "timeframe", "adjustment", "timestamp")

    def __init__(self, path: str = "data/cache/warmup.sqlite3") -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path, check_same_thread=False)
        self._lock = threading.Lock()
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA busy_timeout=5000")
        self._drop_stale_schema()
        self.connection.execute(self._SCHEMA)
        self.connection.commit()

    def _drop_stale_schema(self) -> None:
        """Drop a ``bars`` table whose columns or primary key are not current.

        An older layout (for example the pre-market-key primary key) still
        answers ``get`` but makes every ``upsert`` fail its ``ON CONFLICT``
        clause, pinning warmup in "batch skipped". The cache is derived,
        re-fetchable data, so a stale table is dropped and rebuilt empty.
        """
        info = self.connection.execute("PRAGMA table_info(bars)").fetchall()
        if not info:
            return
        columns = {row[1] for row in info}
        primary_key = tuple(row[1] for row in sorted((row for row in info if row[5]), key=lambda row: row[5]))
        if columns == set(self._COLUMNS) and primary_key == self._PRIMARY_KEY:
            return
        log.warning(
            "Warmup cache %s has a stale bars schema (primary key %s); dropping it to rebuild from the provider",
            self.path,
            ",".join(primary_key) or "none",
        )
        self.connection.execute("DROP TABLE bars")

    def close(self) -> None:
        with self._lock:
            try:
                self.connection.close()
            except sqlite3.Error as exc:
                log.warning("Warmup cache close warning: %s", exc)

    def upsert(self, bars: list[Bar], *, feed: str, adjustment: str = "all") -> None:
        if not bars:
            return
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
        with self._lock:
            self.connection.executemany(
                """
                INSERT INTO bars (feed, symbol, market, timeframe, adjustment, timestamp, open, high, low, close, volume, retrieved_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(feed, symbol, market, timeframe, adjustment, timestamp)
                DO UPDATE SET
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
        market: Market,
        feed: str,
        start: datetime,
        end: datetime,
        adjustment: str = "all",
    ) -> list[Bar]:
        canonical = canonical_symbol(symbol, market)
        with self._lock:
            rows = self.connection.execute(
                """
                SELECT timestamp, open, high, low, close, volume, market
                FROM bars
                WHERE feed=? AND symbol=? AND market=? AND timeframe=? AND adjustment=?
                  AND timestamp>=? AND timestamp<=?
                ORDER BY timestamp
                """,
                (
                    feed,
                    canonical,
                    market.value,
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

    def replace_window(
        self,
        bars: list[Bar],
        *,
        feed: str,
        adjustment: str = "all",
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> bool:
        """Atomically replace the cache window for the symbols in ``bars``.

        An empty payload leaves the cache untouched. Only the window
        ``[start,end]`` for the affected feed/symbol/market/timeframe/adjustment is deleted and
        rewritten inside one transaction. On any error the transaction rolls
        back and the old cache is preserved.
        """
        if not bars:
            log.warning("WarmupCache.replace_window called with empty bars; cache preserved")
            return False
        # Determine window bounds from bars if not supplied
        if start is None:
            start = min(b.timestamp for b in bars)
        if end is None:
            end = max(b.timestamp for b in bars)
        grouped: dict[tuple[str, str, str, str], list[Bar]] = {}
        for bar in bars:
            key = (feed, canonical_symbol(bar.symbol, bar.market), bar.market.value, bar.timeframe)
            grouped.setdefault(key, []).append(bar)
        # Prepare rows for insertion before touching DB
        retrieved_at = datetime.now(UTC).isoformat()
        rows_by_key: dict[tuple[str, str, str, str], list[tuple[object, ...]]] = {}
        for key, blist in grouped.items():
            f, sym, mval, tf = key
            rows_by_key[key] = [
                (
                    f,
                    sym,
                    mval,
                    tf,
                    adjustment,
                    b.timestamp.isoformat(),
                    b.open,
                    b.high,
                    b.low,
                    b.close,
                    b.volume,
                    retrieved_at,
                )
                for b in blist
            ]
        with self._lock:
            try:
                self.connection.execute("BEGIN IMMEDIATE")
                for (f, sym, mval, tf), _ in grouped.items():
                    self.connection.execute(
                        """
                        DELETE FROM bars
                        WHERE feed=? AND symbol=? AND market=? AND timeframe=? AND adjustment=?
                          AND timestamp >= ? AND timestamp <= ?
                        """,
                        (f, sym, mval, tf, adjustment, start.isoformat(), end.isoformat()),
                    )
                for rows in rows_by_key.values():
                    self.connection.executemany(
                        """
                        INSERT INTO bars (feed, symbol, market, timeframe, adjustment, timestamp, open, high, low, close, volume, retrieved_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(feed, symbol, market, timeframe, adjustment, timestamp)
                        DO UPDATE SET open=excluded.open, high=excluded.high, low=excluded.low,
                                      close=excluded.close, volume=excluded.volume, retrieved_at=excluded.retrieved_at
                        """,
                        rows,
                    )
                self.connection.commit()
                log.info(
                    "WarmupCache.replace_window committed %d bars feed=%s adjustment=%s window=[%s,%s]",
                    len(bars),
                    feed,
                    adjustment,
                    start.isoformat(),
                    end.isoformat(),
                )
                return True
            except Exception as exc:
                try:
                    self.connection.rollback()
                except Exception:
                    pass
                log.warning("WarmupCache.replace_window rollback (%s); cache preserved", exc)
                return False


__all__ = ["WarmupCache"]
