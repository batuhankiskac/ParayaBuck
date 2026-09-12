"""Small deterministic fixtures for research contract tests.

The helpers in this module only construct immutable domain values or write
artifacts below an explicitly supplied root.  They deliberately do not implicitly load
repository data, use wall-clock time, sort caller input, or create runtime
state in production paths.
"""

from __future__ import annotations

import copy
import hashlib
import json
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from parayabuck.backtest.terminal_aware_engine import TerminalInfo
from parayabuck.backtest.universe import PointInTimeUniverse, UniverseSnapshot
from parayabuck.core.models import Bar, Market
from parayabuck.data.market_dataset import CorporateAction, CorporateActionType


def bar(
    timestamp: datetime,
    symbol: str = "AAA",
    close: float = 100.0,
    *,
    market: Market = Market.EQUITY,
    timeframe: str = "1d",
    open: float | None = None,
    high: float | None = None,
    low: float | None = None,
    volume: float = 1_000.0,
) -> Bar:
    """Build one deterministic :class:`Bar` with explicit market semantics.

    Missing OHLC values form the smallest valid candle around ``open`` and
    ``close``.  Callers that test a gap or a range should pass ``high`` and
    ``low`` explicitly; the helper never invents strategy-specific movement.
    """

    open_value = close if open is None else open
    high_value = max(open_value, close) if high is None else high
    low_value = min(open_value, close) if low is None else low
    return Bar(
        timestamp=timestamp,
        symbol=symbol,
        timeframe=timeframe,
        open=open_value,
        high=high_value,
        low=low_value,
        close=close,
        volume=volume,
        market=market,
    )


def bar_series(
    symbol: str,
    closes: Sequence[float],
    *,
    start: datetime,
    step: timedelta = timedelta(days=1),
    market: Market = Market.EQUITY,
    timeframe: str = "1d",
    volume: float = 1_000.0,
    opens: Sequence[float] | None = None,
    highs: Sequence[float] | None = None,
    lows: Sequence[float] | None = None,
) -> tuple[Bar, ...]:
    """Return an immutable, deterministic series from caller-owned values."""

    values = tuple(closes)
    optional = {"opens": opens, "highs": highs, "lows": lows}
    for name, supplied in optional.items():
        if supplied is not None and len(supplied) != len(values):
            raise ValueError(f"{name} must have the same length as closes")
    return tuple(
        bar(
            start + step * index,
            symbol,
            close,
            market=market,
            timeframe=timeframe,
            open=None if opens is None else opens[index],
            high=None if highs is None else highs[index],
            low=None if lows is None else lows[index],
            volume=volume,
        )
        for index, close in enumerate(values)
    )


def snapshot(
    effective_at: datetime,
    symbols: Sequence[str],
    *,
    observation_set: Sequence[str] = (),
    maximum_stream_symbols: int = 0,
    pinned_symbols: Sequence[str] = (),
    scanner_version: str = "synthetic",
    config_digest: str = "synthetic",
    candidate_count: int = 0,
) -> UniverseSnapshot:
    """Build one PIT snapshot while retaining caller order where it matters."""

    if effective_at.tzinfo is None:
        raise ValueError("PIT snapshot timestamps must be timezone-aware")
    return UniverseSnapshot(
        effective_at=effective_at,
        symbols=frozenset(symbols),
        scanner_version=scanner_version,
        config_digest=config_digest,
        candidate_count=candidate_count,
        observation_set=tuple(observation_set),
        maximum_stream_symbols=maximum_stream_symbols,
        pinned_symbols=tuple(pinned_symbols),
    )


def pit_universe(
    market: Market,
    snapshots: Sequence[UniverseSnapshot],
    *,
    methodology: str = "synthetic",
) -> PointInTimeUniverse:
    """Build a PIT universe without silently reordering caller snapshots."""

    ordered = tuple(snapshots)
    if any(item.effective_at.tzinfo is None for item in ordered):
        raise ValueError("PIT snapshot timestamps must be timezone-aware")
    if any(right.effective_at < left.effective_at for left, right in zip(ordered, ordered[1:])):
        raise ValueError("PIT snapshots must be effective_at-ascending")
    return PointInTimeUniverse(market=market, snapshots=ordered, methodology=methodology)


def corporate_action(
    timestamp: datetime,
    symbol: str,
    action_type: CorporateActionType,
    value: float,
) -> CorporateAction:
    """Construct one validated immutable corporate action."""

    return CorporateAction(timestamp, symbol, action_type, value)


def terminal_info(
    *,
    entity_id: str,
    symbol: str,
    last_bar: datetime,
    display_symbol: str | None = None,
    terminal_event_class: str,
    evidence_class: str,
    should_haircut: bool,
) -> TerminalInfo:
    """Construct one explicit terminal record for terminal-aware tests."""

    return TerminalInfo(
        entity_id=entity_id,
        symbol=symbol,
        display_symbol=symbol if display_symbol is None else display_symbol,
        last_bar=last_bar,
        terminal_event_class=terminal_event_class,
        evidence_class=evidence_class,
        should_haircut=should_haircut,
    )


def sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def _safe_artifact_path(root: Path, relative: str | Path) -> Path:
    relative_path = Path(relative)
    if relative_path.is_absolute() or not relative_path.parts or ".." in relative_path.parts:
        raise ValueError("artifact path must be a non-empty relative path without '..'")
    root_path = root.resolve()
    target = (root_path / relative_path).resolve()
    if target != root_path and root_path not in target.parents:
        raise ValueError("artifact path escapes its root")
    return target


def write_artifact(
    root: Path,
    relative: str | Path,
    content: bytes | str,
    *,
    overwrite: bool = False,
) -> Path:
    """Write bytes below ``root`` and refuse accidental replacement by default."""

    target = _safe_artifact_path(root, relative)
    if target.exists() and not overwrite:
        raise FileExistsError(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content.encode("utf-8") if isinstance(content, str) else content)
    return target


def canonical_hash(
    payload: Mapping[str, Any],
    self_field: str,
    *,
    container: str | None,
) -> str:
    """Hash a JSON payload after removing its explicitly named self field.

    ``container='integrity'`` models the nested receipt convention.  Pass
    ``container=None`` for a top-level self-hash.  The input mapping is never
    mutated.
    """

    value = copy.deepcopy(dict(payload))
    if container is None:
        value.pop(self_field, None)
    else:
        integrity = value.get(container)
        if not isinstance(integrity, dict):
            raise ValueError(f"{container} object required")
        integrity.pop(self_field, None)
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return sha256_bytes(encoded)


__all__ = [
    "bar",
    "bar_series",
    "snapshot",
    "pit_universe",
    "corporate_action",
    "terminal_info",
    "sha256_bytes",
    "sha256_file",
    "write_artifact",
    "canonical_hash",
]
