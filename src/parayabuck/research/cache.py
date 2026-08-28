"""Derived research cache: bars and universe with fingerprint invalidation."""

from __future__ import annotations

import hashlib
import json
import pickle
from datetime import datetime
from pathlib import Path
from typing import Any

from parayabuck.backtest.universe import PointInTimeUniverse
from parayabuck.core.models import Bar, Market
from parayabuck.logger import get_logger

log = get_logger("parayabuck.research.cache")
# Bump when Bar / cache schema changes.
BAR_CACHE_VERSION = 2
UNIVERSE_CACHE_VERSION = 2

CACHE_ROOT = Path("data/cache/research")


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _fingerprint_bars(csv_path: Path, market: Market) -> dict[str, Any]:
    sha = _sha256_file(csv_path)
    stat = csv_path.stat()
    return {
        "schema_version": BAR_CACHE_VERSION,
        "source_path": str(csv_path),
        "source_sha256": sha,
        "source_size": stat.st_size,
        "source_mtime": stat.st_mtime,
        "market": market.value,
    }


def _fingerprint_universe(
    csv_path: Path,
    market: Market,
    timeframe: str | None,
    end: datetime | None,
    config_digest: str,
    scanner_rebalance: str,
) -> dict[str, Any]:
    base = _fingerprint_bars(csv_path, market)
    return {
        **base,
        "schema_version": UNIVERSE_CACHE_VERSION,
        "market": market.value,
        "timeframe": timeframe,
        "end": end.isoformat() if end else None,
        "config_digest": config_digest,
        "scanner_rebalance": scanner_rebalance,
    }


def _cache_path(kind: str, key: dict[str, Any]) -> Path:
    # Deterministic file name from key hash.
    payload = json.dumps(key, sort_keys=True, default=str).encode()
    digest = hashlib.sha256(payload).hexdigest()[:16]
    market = key.get("market", "bars")
    return CACHE_ROOT / kind / f"{market}_{digest}.pkl"


def load_cached_bars(csv_path: Path, market: Market) -> list[Bar] | None:
    try:
        key = _fingerprint_bars(csv_path, market)
        path = _cache_path("bars", key)
        if not path.exists():
            return None
        with path.open("rb") as f:
            payload = pickle.load(f)
        if not isinstance(payload, dict):
            return None
        stored_key = payload.get("_key")
        if stored_key != key:
            return None
        bars = payload.get("bars")
        if not isinstance(bars, list) or not bars:
            return None
        if not isinstance(bars[0], Bar):
            return None
        return bars
    except (OSError, pickle.PickleError, ValueError, KeyError, AttributeError) as exc:
        log.warning("Research bar cache read failed for %s: %s", csv_path, exc)
        return None


def store_cached_bars(csv_path: Path, market: Market, bars: list[Bar]) -> None:
    try:
        key = _fingerprint_bars(csv_path, market)
        path = _cache_path("bars", key)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        payload = {"_key": key, "bars": bars}
        with tmp.open("wb") as f:
            pickle.dump(payload, f, protocol=4)
        tmp.replace(path)
    except (OSError, pickle.PickleError) as exc:
        log.warning("Research bar cache write failed for %s: %s", csv_path, exc)


def load_cached_universe(
    csv_path: Path,
    market: Market,
    timeframe: str | None,
    end: datetime | None,
    config_digest: str,
    scanner_rebalance: str,
) -> PointInTimeUniverse | None:
    try:
        key = _fingerprint_universe(csv_path, market, timeframe, end, config_digest, scanner_rebalance)
        path = _cache_path("universe", key)
        if not path.exists():
            return None
        with path.open("rb") as f:
            payload = pickle.load(f)
        if not isinstance(payload, dict):
            return None
        stored_key = payload.get("_key")
        if stored_key != key:
            return None
        universe = payload.get("universe")
        if not isinstance(universe, PointInTimeUniverse):
            return None
        return universe
    except (OSError, pickle.PickleError, ValueError, KeyError, AttributeError) as exc:
        log.warning("Research universe cache read failed for %s: %s", csv_path, exc)
        return None


def store_cached_universe(
    csv_path: Path,
    market: Market,
    timeframe: str | None,
    end: datetime | None,
    config_digest: str,
    scanner_rebalance: str,
    universe: PointInTimeUniverse,
) -> None:
    try:
        key = _fingerprint_universe(csv_path, market, timeframe, end, config_digest, scanner_rebalance)
        path = _cache_path("universe", key)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        payload = {"_key": key, "universe": universe}
        with tmp.open("wb") as f:
            pickle.dump(payload, f, protocol=4)
        tmp.replace(path)
    except (OSError, pickle.PickleError) as exc:
        log.warning("Research universe cache write failed for %s: %s", csv_path, exc)


def cache_stats() -> dict[str, Any]:
    if not CACHE_ROOT.exists():
        return {"exists": False}
    files = list(CACHE_ROOT.rglob("*.pkl"))
    total = sum(p.stat().st_size for p in files)
    return {
        "exists": True,
        "files": len(files),
        "total_bytes": total,
        "total_mb": round(total / (1024 * 1024), 2),
        "paths": [str(p) for p in files[:10]],
    }
