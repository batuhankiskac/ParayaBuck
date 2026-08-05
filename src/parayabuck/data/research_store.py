"""Versioned local research data store decoupled from the broker warmup cache.

Research runs no longer depend on the broker API's short warmup cache: a
downloaded dataset is versioned, hashed and persisted under
``data/research/<dataset_id>/`` with per-symbol partitions, a JSON manifest
and optional point-in-time universe snapshots. Storage is Parquet when
``pyarrow`` is installed and falls back to gzipped CSV otherwise; both formats
round-trip identical ``Bar`` objects and expose the same manifest fields.
"""

from __future__ import annotations

import gzip
import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from parayabuck.core.models import Bar, Market
from parayabuck.markets.symbols import canonical_symbol

MANIFEST_FILENAME = "manifest.json"
SNAPSHOTS_FILENAME = "universe_snapshots.json"


def _try_import_pyarrow() -> bool:
    try:
        import pyarrow  # noqa: F401

        return True
    except ImportError:
        return False


PARQUET_AVAILABLE = _try_import_pyarrow()


@dataclass(frozen=True)
class DatasetManifest:
    dataset_id: str
    format: str
    timeframe: str
    source_feed: str
    adjustment: str
    universe_version: str
    downloaded_at: datetime
    start: datetime
    end: datetime
    symbols: tuple[str, ...]
    bars_per_symbol: dict[str, int]
    data_hash: str
    duplicate_bars: dict[str, int]
    notes: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "dataset_id": self.dataset_id,
            "format": self.format,
            "timeframe": self.timeframe,
            "source_feed": self.source_feed,
            "adjustment": self.adjustment,
            "universe_version": self.universe_version,
            "downloaded_at": self.downloaded_at.isoformat(),
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "symbols": list(self.symbols),
            "bars_per_symbol": dict(self.bars_per_symbol),
            "data_hash": self.data_hash,
            "duplicate_bars": dict(self.duplicate_bars),
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, object]) -> DatasetManifest:
        raw_counts = raw["bars_per_symbol"]
        bars_per_symbol = (
            {str(symbol): int(count) for symbol, count in raw_counts.items()}
            if isinstance(raw_counts, dict)
            else {}
        )
        raw_duplicates = raw["duplicate_bars"]
        duplicate_bars = (
            {str(symbol): int(count) for symbol, count in raw_duplicates.items()}
            if isinstance(raw_duplicates, dict)
            else {}
        )
        raw_symbols = raw["symbols"]
        symbols = (
            tuple(str(symbol) for symbol in raw_symbols)
            if isinstance(raw_symbols, (tuple, list))
            else ()
        )
        return cls(
            dataset_id=str(raw["dataset_id"]),
            format=str(raw["format"]),
            timeframe=str(raw["timeframe"]),
            source_feed=str(raw["source_feed"]),
            adjustment=str(raw["adjustment"]),
            universe_version=str(raw["universe_version"]),
            downloaded_at=datetime.fromisoformat(str(raw["downloaded_at"])),
            start=datetime.fromisoformat(str(raw["start"])),
            end=datetime.fromisoformat(str(raw["end"])),
            symbols=symbols,
            bars_per_symbol=bars_per_symbol,
            data_hash=str(raw["data_hash"]),
            duplicate_bars=duplicate_bars,
            notes=str(raw.get("notes", "")),
        )


@dataclass(frozen=True)
class UniverseSnapshotRecord:
    effective_at: datetime
    symbols: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "effective_at": self.effective_at.isoformat(),
            "symbols": list(self.symbols),
        }

    @classmethod
    def from_dict(cls, raw: dict[str, object]) -> UniverseSnapshotRecord:
        raw_symbols = raw["symbols"]
        symbols = (
            tuple(str(symbol) for symbol in raw_symbols)
            if isinstance(raw_symbols, (tuple, list))
            else ()
        )
        return cls(
            effective_at=datetime.fromisoformat(str(raw["effective_at"])),
            symbols=symbols,
        )


def hash_dataset(bars: list[Bar]) -> str:
    digest = hashlib.sha256()
    for bar in sorted(bars, key=lambda item: (item.timestamp, item.symbol, item.timeframe)):
        row = (
            bar.timestamp.isoformat(),
            bar.symbol,
            bar.timeframe,
            f"{bar.open:.12g}",
            f"{bar.high:.12g}",
            f"{bar.low:.12g}",
            f"{bar.close:.12g}",
            f"{bar.volume:.12g}",
            bar.market.value,
        )
        digest.update("|".join(row).encode())
        digest.update(b"\n")
    return digest.hexdigest()


class VersionedResearchStore:
    """Parquet-preferred (gzip CSV fallback) partition store for research data."""

    def __init__(self, root: str | Path = "data/research") -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def dataset_dir(self, dataset_id: str) -> Path:
        return self.root / dataset_id

    def list_datasets(self) -> tuple[str, ...]:
        return tuple(
            sorted(
                entry.name
                for entry in self.root.iterdir()
                if entry.is_dir() and (entry / MANIFEST_FILENAME).exists()
            )
        )

    def manifest(self, dataset_id: str) -> DatasetManifest | None:
        path = self.dataset_dir(dataset_id) / MANIFEST_FILENAME
        if not path.exists():
            return None
        raw = json.loads(path.read_text(encoding="utf-8"))
        return DatasetManifest.from_dict(raw if isinstance(raw, dict) else {})

    def save_dataset(
        self,
        dataset_id: str,
        bars: list[Bar],
        *,
        timeframe: str,
        source_feed: str,
        adjustment: str,
        universe_version: str,
        notes: str = "",
    ) -> DatasetManifest:
        if not bars:
            raise ValueError("Cannot persist an empty dataset")
        if not dataset_id.strip():
            raise ValueError("Dataset id is required")
        directory = self.dataset_dir(dataset_id)
        directory.mkdir(parents=True, exist_ok=True)
        format_name = "parquet" if PARQUET_AVAILABLE else "csv_gz"
        by_symbol: dict[str, list[Bar]] = {}
        for bar in bars:
            canonical = canonical_symbol(bar.symbol, bar.market)
            if canonical not in by_symbol:
                by_symbol[canonical] = []
            by_symbol[canonical].append(bar)
        for symbol, symbol_bars in by_symbol.items():
            ordered = sorted(symbol_bars, key=lambda item: item.timestamp)
            self._write_partition(directory, symbol, timeframe, ordered, format_name)
        timestamps = [bar.timestamp for bar in bars]
        duplicates: dict[str, int] = {
            symbol: len(bars_list) - len({bar.timestamp for bar in bars_list})
            for symbol, bars_list in by_symbol.items()
        }
        manifest = DatasetManifest(
            dataset_id=dataset_id,
            format=format_name,
            timeframe=timeframe,
            source_feed=source_feed,
            adjustment=adjustment,
            universe_version=universe_version,
            downloaded_at=datetime.now(UTC),
            start=min(timestamps),
            end=max(timestamps),
            symbols=tuple(sorted(by_symbol)),
            bars_per_symbol={symbol: len(bars_list) for symbol, bars_list in by_symbol.items()},
            data_hash=hash_dataset(bars),
            duplicate_bars=duplicates,
            notes=notes,
        )
        (directory / MANIFEST_FILENAME).write_text(
            json.dumps(manifest.to_dict(), indent=2, sort_keys=True, ensure_ascii=False),
            encoding="utf-8",
        )
        return manifest

    def load_dataset(
        self,
        dataset_id: str,
        *,
        symbols: tuple[str, ...] | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> dict[str, list[Bar]]:
        manifest = self.manifest(dataset_id)
        if manifest is None:
            raise FileNotFoundError(f"Unknown research dataset: {dataset_id}")
        directory = self.dataset_dir(dataset_id)
        wanted = set(symbols) if symbols is not None else None
        loaded: dict[str, list[Bar]] = {}
        for symbol in manifest.symbols:
            if wanted is not None and symbol not in wanted:
                continue
            bars = self._read_partition(directory, symbol, manifest.timeframe, manifest.format)
            if start is not None or end is not None:
                bars = [
                    bar
                    for bar in bars
                    if (start is None or bar.timestamp >= start)
                    and (end is None or bar.timestamp <= end)
                ]
            loaded[symbol] = bars
        return loaded

    def save_universe_snapshots(
        self, dataset_id: str, snapshots: tuple[UniverseSnapshotRecord, ...]
    ) -> None:
        directory = self.dataset_dir(dataset_id)
        directory.mkdir(parents=True, exist_ok=True)
        (directory / SNAPSHOTS_FILENAME).write_text(
            json.dumps(
                [snapshot.to_dict() for snapshot in snapshots],
                indent=2,
                sort_keys=True,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    def load_universe_snapshots(self, dataset_id: str) -> tuple[UniverseSnapshotRecord, ...]:
        path = self.dataset_dir(dataset_id) / SNAPSHOTS_FILENAME
        if not path.exists():
            return ()
        raw = json.loads(path.read_text(encoding="utf-8"))
        return tuple(
            UniverseSnapshotRecord.from_dict(item)
            for item in raw
            if isinstance(item, dict)
        )

    def delete_dataset(self, dataset_id: str) -> None:
        directory = self.dataset_dir(dataset_id)
        if directory.exists():
            for path in directory.iterdir():
                path.unlink()
            directory.rmdir()

    # -- partition io -------------------------------------------------------

    @staticmethod
    def _partition_path(directory: Path, symbol: str, timeframe: str, format_name: str) -> Path:
        extension = ".parquet" if format_name == "parquet" else ".csv.gz"
        # Crypto pairs contain "/", which must not create nested directories.
        safe_symbol = symbol.replace("/", "_")
        return directory / f"{safe_symbol}_{timeframe}{extension}"

    def _write_partition(
        self,
        directory: Path,
        symbol: str,
        timeframe: str,
        bars: list[Bar],
        format_name: str,
    ) -> None:
        path = self._partition_path(directory, symbol, timeframe, format_name)
        if format_name == "parquet":
            import pandas as pd

            frame = pd.DataFrame(
                [
                    (
                        bar.timestamp.isoformat(),
                        bar.symbol,
                        bar.open,
                        bar.high,
                        bar.low,
                        bar.close,
                        bar.volume,
                        bar.market.value,
                    )
                    for bar in bars
                ],
                columns=["timestamp", "symbol", "open", "high", "low", "close", "volume", "market"],
            )
            frame.to_parquet(path, index=False)
            return
        header = "timestamp,symbol,open,high,low,close,volume,market"
        lines = [header] + [",".join(self._row_to_csv(bar)) for bar in bars]
        with gzip.open(path, "wt", encoding="utf-8", newline="") as handle:
            handle.write("\n".join(lines) + "\n")

    @staticmethod
    def _row_to_csv(bar: Bar) -> list[str]:
        return [
            bar.timestamp.isoformat(),
            bar.symbol,
            f"{bar.open:.10g}",
            f"{bar.high:.10g}",
            f"{bar.low:.10g}",
            f"{bar.close:.10g}",
            f"{bar.volume:.10g}",
            bar.market.value,
        ]

    def _read_partition(
        self, directory: Path, symbol: str, timeframe: str, format_name: str
    ) -> list[Bar]:
        path = self._partition_path(directory, symbol, timeframe, format_name)
        if not path.exists():
            return []
        if format_name == "parquet":
            import pandas as pd

            frame = pd.read_parquet(path)
            bars: list[Bar] = []
            for row in frame.itertuples(index=False):
                timestamp = row.timestamp
                if hasattr(timestamp, "to_pydatetime"):
                    timestamp = timestamp.to_pydatetime()
                if timestamp.tzinfo is None:
                    timestamp = timestamp.replace(tzinfo=UTC)
                bars.append(
                    Bar(
                        timestamp=timestamp.astimezone(UTC),
                        symbol=str(row.symbol),
                        timeframe=timeframe,
                        open=float(row.open),
                        high=float(row.high),
                        low=float(row.low),
                        close=float(row.close),
                        volume=float(row.volume),
                        market=Market(str(row.market)),
                    )
                )
            return bars
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            lines = [line.strip() for line in handle if line.strip()]
        if not lines:
            return []
        header = lines[0].split(",")
        index = {name: position for position, name in enumerate(header)}
        parsed: list[Bar] = []
        for line in lines[1:]:
            cells = line.split(",")
            timestamp = datetime.fromisoformat(cells[index["timestamp"]])
            if timestamp.tzinfo is None:
                timestamp = timestamp.replace(tzinfo=UTC)
            parsed.append(
                Bar(
                    timestamp=timestamp.astimezone(UTC),
                    symbol=str(cells[index["symbol"]]),
                    timeframe=timeframe,
                    open=float(cells[index["open"]]),
                    high=float(cells[index["high"]]),
                    low=float(cells[index["low"]]),
                    close=float(cells[index["close"]]),
                    volume=float(cells[index["volume"]]),
                    market=Market(cells[index["market"]]),
                )
            )
        return parsed


__all__ = [
    "DatasetManifest",
    "PARQUET_AVAILABLE",
    "UniverseSnapshotRecord",
    "VersionedResearchStore",
    "hash_dataset",
]
