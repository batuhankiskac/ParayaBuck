from __future__ import annotations

import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path

from parayabuck.core.models import Bar, Market
from parayabuck.logger import get_logger
from parayabuck.markets.symbols import canonical_symbol

log = get_logger("parayabuck.data.cache")


def is_crypto_daily_grid(timestamp: datetime) -> bool:
    """Whether a crypto 1d bar sits exactly on the UTC midnight grid.

    Alpaca crypto daily bars are calendar-day bars at 00:00 UTC. Test fakes
    that create 10:00/13:00/23:00 bars must be rejected at the cache boundary
    so they cannot pollute production.
    """
    ts = timestamp.astimezone(UTC) if timestamp.tzinfo is not None else timestamp.replace(tzinfo=UTC)
    return ts.hour == 0 and ts.minute == 0 and ts.second == 0 and ts.microsecond == 0


def _filter_off_grid_bars(bars: list[Bar]) -> tuple[list[Bar], int]:
    """Split into (valid, off_grid_count) for crypto 1d bars."""
    valid: list[Bar] = []
    off = 0
    for bar in bars:
        if bar.market == Market.CRYPTO and bar.timeframe == "1d" and not is_crypto_daily_grid(bar.timestamp):
            off += 1
            continue
        valid.append(bar)
    return valid, off


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
        # Use PRAGMA index_list to locate the primary-key autoindex; the
        # sqlite_master sql LIKE '%PRIMARY KEY%' trick fails for
        # sqlite_autoindex_* where sql is NULL.
        for row in self.connection.execute("PRAGMA index_list(bars)").fetchall():
            # row: (seq, name, unique, origin, partial)
            if len(row) >= 4 and row[3] == "pk":
                pk_name = row[1]
                quoted = '"' + str(pk_name).replace('"', '""') + '"'
                return {r[2] for r in self.connection.execute(f"PRAGMA index_info({quoted})").fetchall()}
        return set()

    def _migrate_legacy_schema(self) -> None:
        """Rebuild caches created before ``market`` joined the primary key.

        Older caches either lack the ``adjustment`` column entirely (pre-2025
        schema, RAW price basis) or key rows without ``market``. Rows whose
        key does not include the market can silently collide across markets,
        so the table is rebuilt whenever the primary key is missing it.

        Crash-safe recovery:
          A: modern bars, no legacy → no-op.
          B: old bars, no legacy → atomic rename → create → copy → drop.
          D: modern bars empty/full + legacy exists (crash after RENAME,
             CREATE, or COPY but before DROP) → idempotently merge legacy
             into bars then drop legacy. A crash right after RENAME also
             lands here: the constructor recreates an empty modern ``bars``
             (``CREATE TABLE IF NOT EXISTS``) before this method runs.
          E: both exist → determine schema state; never blindly DROP legacy
             at the start (previous bug). Legacy is only removed after its
             rows are proven present in the new table or after a logged merge.
        """
        with self._lock:
            tables = {
                str(row[0])
                for row in self.connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            }
            bars_exists = "bars" in tables
            legacy_exists = "bars_legacy" in tables

            def _is_modern_bars() -> bool:
                if not bars_exists:
                    return False
                cols = {str(row[1]) for row in self.connection.execute("PRAGMA table_info(bars)").fetchall()}
                return "adjustment" in cols and "market" in self._primary_key_columns()

            # Scenario A: already modern, no legacy → no-op
            if bars_exists and _is_modern_bars() and not legacy_exists:
                return

            # Both tables exist
            if bars_exists and legacy_exists:
                if _is_modern_bars():
                    bars_count = int(self.connection.execute("SELECT COUNT(*) FROM bars").fetchone()[0] or 0)
                    legacy_count = int(self.connection.execute("SELECT COUNT(*) FROM bars_legacy").fetchone()[0] or 0)
                    legacy_cols = {
                        str(row[1]) for row in self.connection.execute("PRAGMA table_info(bars_legacy)").fetchall()
                    }
                    if bars_count == 0 and legacy_count > 0:
                        # D: CREATE succeeded but COPY never happened — copy now
                        if "adjustment" in legacy_cols:
                            self.connection.execute(
                                """
                                INSERT OR IGNORE INTO bars (feed, symbol, market, timeframe, adjustment, timestamp,
                                                            open, high, low, close, volume, retrieved_at)
                                SELECT feed, symbol, market, timeframe, adjustment, timestamp,
                                       open, high, low, close, volume, retrieved_at
                                FROM bars_legacy
                                """
                            )
                        else:
                            self.connection.execute(
                                """
                                INSERT OR IGNORE INTO bars (feed, symbol, market, timeframe, adjustment, timestamp,
                                                            open, high, low, close, volume, retrieved_at)
                                SELECT feed, symbol, 'equity', timeframe, 'raw', timestamp,
                                       open, high, low, close, volume, retrieved_at
                                FROM bars_legacy
                                """
                            )
                        self.connection.execute("DROP TABLE bars_legacy")
                        self.connection.commit()
                        log.info(
                            "WarmupCache: recovered interrupted migration (bars empty) — copied %s rows",
                            legacy_count,
                        )
                        return
                    # D/E: both populated modern+legacy — idempotently merge then drop
                    # INSERT OR IGNORE makes the merge safe even if rows already copied
                    if "adjustment" in legacy_cols:
                        self.connection.execute(
                            """
                            INSERT OR IGNORE INTO bars (feed, symbol, market, timeframe, adjustment, timestamp,
                                                        open, high, low, close, volume, retrieved_at)
                            SELECT feed, symbol, market, timeframe, adjustment, timestamp,
                                   open, high, low, close, volume, retrieved_at
                            FROM bars_legacy
                            """
                        )
                    else:
                        self.connection.execute(
                            """
                            INSERT OR IGNORE INTO bars (feed, symbol, market, timeframe, adjustment, timestamp,
                                                        open, high, low, close, volume, retrieved_at)
                            SELECT feed, symbol, 'equity', timeframe, 'raw', timestamp,
                                   open, high, low, close, volume, retrieved_at
                            FROM bars_legacy
                            """
                        )
                    after = int(self.connection.execute("SELECT COUNT(*) FROM bars").fetchone()[0] or 0)
                    self.connection.execute("DROP TABLE bars_legacy")
                    self.connection.commit()
                    log.warning(
                        "WarmupCache: both bars and bars_legacy existed (bars %s → %s rows, legacy %s rows) — "
                        "merged legacy and removed bars_legacy",
                        bars_count,
                        after,
                        legacy_count,
                    )
                    return
                # bars is old schema but legacy also exists — ambiguous E, do not drop legacy blindly
                log.warning(
                    "WarmupCache: both bars (old schema) and bars_legacy exist — "
                    "preserving bars_legacy and migrating bars via backup"
                )
                # Move legacy aside so the normal B path can run without data loss
                self.connection.execute("ALTER TABLE bars_legacy RENAME TO bars_legacy_backup")
                self.connection.commit()
                # Update existence flags for the fall-through B path
                tables = {
                    str(row[0])
                    for row in self.connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
                }
                bars_exists = "bars" in tables
                legacy_exists = "bars_legacy" in tables  # now False, backup holds old legacy
                # Fall through to normal migration of old bars; backup left for inspection

            # Normal Scenario B: old bars, no legacy (or after E backup move)
            if bars_exists and not _is_modern_bars():
                cols = {str(row[1]) for row in self.connection.execute("PRAGMA table_info(bars)").fetchall()}
                # Atomic: RENAME → CREATE → COPY → DROP within one transaction (commit at end)
                self.connection.execute("ALTER TABLE bars RENAME TO bars_legacy")
                self.connection.execute(self._SCHEMA)
                if "adjustment" in cols:
                    self.connection.execute(
                        """
                        INSERT OR IGNORE INTO bars (feed, symbol, market, timeframe, adjustment, timestamp,
                                                    open, high, low, close, volume, retrieved_at)
                        SELECT feed, symbol, market, timeframe, adjustment, timestamp,
                               open, high, low, close, volume, retrieved_at
                        FROM bars_legacy
                        """
                    )
                else:
                    self.connection.execute(
                        """
                        INSERT OR IGNORE INTO bars (feed, symbol, market, timeframe, adjustment, timestamp,
                                                    open, high, low, close, volume, retrieved_at)
                        SELECT feed, symbol, 'equity', timeframe, 'raw', timestamp,
                               open, high, low, close, volume, retrieved_at
                        FROM bars_legacy
                        """
                    )
                self.connection.execute("DROP TABLE bars_legacy")
                self.connection.commit()
                return

    def close(self) -> None:
        with self._lock:
            try:
                self.connection.close()
            except sqlite3.Error as exc:
                log.warning("Warmup cache close warning: %s", exc)

    def upsert(self, bars: list[Bar], *, feed: str, adjustment: str = "all") -> None:
        valid, off = _filter_off_grid_bars(bars)
        if off:
            log.warning("WarmupCache.upsert rejected %d off-grid crypto 1d bars (feed=%s)", off, feed)
            try:
                from parayabuck.monitoring.journal import emit_journal
                from parayabuck.monitoring.operational_event import create_event

                evt = create_event(
                    kind="cache_integrity",
                    message=f"upsert rejected {off} off-grid crypto daily bars",
                    fields={"off_grid_bars": off, "feed": feed, "adjustment": adjustment},
                )
                emit_journal(evt)
            except Exception:
                pass
        if not valid:
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
            for bar in valid
        ]
        if not rows:
            return
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
        bars = [
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
        if market == Market.CRYPTO and timeframe == "1d":
            valid, off = _filter_off_grid_bars(bars)
            if off:
                log.warning(
                    "WarmupCache.get filtered %d off-grid crypto 1d bars symbol=%s feed=%s",
                    off,
                    canonical,
                    feed,
                )
                try:
                    from parayabuck.monitoring.journal import emit_journal
                    from parayabuck.monitoring.operational_event import create_event

                    evt = create_event(
                        kind="cache_integrity",
                        message=f"filtered {off} off-grid crypto daily bars on read",
                        fields={"off_grid_bars": off, "symbol": canonical, "feed": feed, "market": market.value},
                    )
                    emit_journal(evt)
                except Exception:
                    pass
                return valid
        return bars

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

        Validates ``bars`` in memory first; an empty or off-grid-only payload
        leaves the cache untouched. Only the window ``[start,end]`` for the
        affected feed/symbol/market/timeframe/adjustment is deleted and
        rewritten inside one transaction. On any error the transaction rolls
        back and the old cache is preserved.
        """
        if not bars:
            log.warning("WarmupCache.replace_window called with empty bars; cache preserved")
            return False
        # In-memory validation: filter off-grid crypto 1d bars, reject if all off-grid
        valid, off = _filter_off_grid_bars(bars)
        if off:
            log.warning("WarmupCache.replace_window filtered %d off-grid bars", off)
        if not valid:
            log.warning("WarmupCache.replace_window has no valid bars after filtering; cache preserved")
            return False
        # Determine window bounds from bars if not supplied
        if start is None:
            start = min(b.timestamp for b in valid)
        if end is None:
            end = max(b.timestamp for b in valid)
        grouped: dict[tuple[str, str, str, str], list[Bar]] = {}
        for bar in valid:
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
                    len(valid),
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
