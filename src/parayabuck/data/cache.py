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
    # Cache-scope validation metadata: incremental cache reuse is only
    # trusted when the recorded config digest (and strategy identity, where
    # relevant) still match the caller's. A mismatch fails closed to a full
    # refetch instead of serving bars computed under a different
    # configuration.
    _SCOPE_SCHEMA = """
        CREATE TABLE IF NOT EXISTS scope_meta (
            scope TEXT PRIMARY KEY,
            config_digest TEXT NOT NULL,
            identity TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL
        )
    """

    def __init__(self, path: str = "data/cache/warmup.sqlite3") -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path, check_same_thread=False)
        self._lock = threading.Lock()
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA busy_timeout=5000")
        self.connection.execute(self._SCHEMA)
        self.connection.execute(self._SCOPE_SCHEMA)
        self.connection.commit()
        self._migrate_legacy_schema()

    def _primary_key_columns(self) -> set[str]:
        pk_name = next(
            (row[1] for row in self.connection.execute("PRAGMA index_list(bars)").fetchall() if row[3] == "pk"),
            None,
        )
        if pk_name is None:
            return set()
        return {row[2] for row in self.connection.execute(f"PRAGMA index_info({pk_name})").fetchall()}

    def _migrate_legacy_schema(self) -> None:
        """Rebuild caches created before ``market`` joined the primary key.

        Older caches either lack the ``adjustment`` column entirely (pre-2025
        schema, RAW price basis) or key rows without ``market``. Rows whose
        key does not include the market can silently collide across markets,
        so the table is rebuilt whenever the primary key is missing it.
        """
        columns = {row[1] for row in self.connection.execute("PRAGMA table_info(bars)").fetchall()}
        if "adjustment" in columns and "market" in self._primary_key_columns():
            return
        with self._lock:
            self.connection.execute("ALTER TABLE bars RENAME TO bars_legacy")
            self.connection.execute(self._SCHEMA)
            if "adjustment" in columns:
                self.connection.execute(
                    """
                    INSERT INTO bars (feed, symbol, market, timeframe, adjustment, timestamp,
                                      open, high, low, close, volume, retrieved_at)
                    SELECT feed, symbol, market, timeframe, adjustment, timestamp,
                           open, high, low, close, volume, retrieved_at
                    FROM bars_legacy
                    """
                )
            else:
                # Legacy rows carry no market/adjustment; pre-adjustment data
                # was fetched on the RAW basis, so 'raw' is the honest label.
                self.connection.execute(
                    """
                    INSERT INTO bars (feed, symbol, market, timeframe, adjustment, timestamp,
                                      open, high, low, close, volume, retrieved_at)
                    SELECT feed, symbol, 'equity', timeframe, 'raw', timestamp,
                           open, high, low, close, volume, retrieved_at
                    FROM bars_legacy
                    """
                )
            self.connection.execute("DROP TABLE bars_legacy")
            self.connection.commit()

    def close(self) -> None:
        with self._lock:
            try:
                self.connection.close()
            except sqlite3.Error as exc:
                log.warning("Warmup cache close warning: %s", exc)

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
        canonical = canonical_symbol(symbol)
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

    def scope_is_valid(self, scope: str, config_digest: str) -> bool:
        """Whether a cache scope was last written under ``config_digest``.

        Fail-closed: a missing row or a digest mismatch means the cached
        bars may have been produced under a different configuration, so the
        caller must refetch instead of reusing the cache.
        """
        with self._lock:
            row = self.connection.execute("SELECT config_digest FROM scope_meta WHERE scope=?", (scope,)).fetchone()
        return bool(row and row[0] == config_digest)

    def scope_set(self, scope: str, config_digest: str, identity: str = "") -> None:
        """Record that a cache scope was refreshed under ``config_digest``.

        ``identity`` is a human-readable strategy/scope label (e.g. strategy
        name + timeframe) recorded for auditability; validation is driven by
        the digest.
        """
        with self._lock:
            self.connection.execute(
                """
                INSERT INTO scope_meta (scope, config_digest, identity, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(scope) DO UPDATE SET
                    config_digest=excluded.config_digest,
                    identity=excluded.identity,
                    updated_at=excluded.updated_at
                """,
                (scope, config_digest, identity, datetime.now(UTC).isoformat()),
            )
            self.connection.commit()


__all__ = ["WarmupCache"]
