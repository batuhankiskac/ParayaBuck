from __future__ import annotations

import csv
import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from parayabuck.backtest.portfolio_engine import TerminalLifecycle
from parayabuck.core.models import Market
from parayabuck.markets.symbols import canonical_symbol

CANONICAL_LIFETIME_FIELDS: tuple[str, ...] = (
    "entity_id",
    "provider_asset_id",
    "canonical_symbol",
    "historical_symbol",
    "valid_from",
    "valid_to",
    "first_observed_bar",
    "last_observed_bar",
    "current_status",
    "exchange",
    "rename_chain",
    "evidence_class",
    "pit_quality_tier",
    "terminal_event_class",
    "notes",
)


class LifetimeSchemaError(ValueError):
    """The canonical lifetime file is structurally invalid or ambiguous."""


@dataclass(frozen=True)
class RenameInterval:
    symbol: str
    valid_from: date | None
    valid_to: date | None

    def to_dict(self) -> dict[str, str | None]:
        return {
            "symbol": self.symbol,
            "valid_from": self.valid_from.isoformat() if self.valid_from is not None else None,
            "valid_to": self.valid_to.isoformat() if self.valid_to is not None else None,
        }


@dataclass(frozen=True)
class EntityLifetimeRecord:
    entity_id: str
    provider_asset_id: str
    canonical_symbol: str
    historical_symbol: str
    valid_from: datetime
    valid_to: datetime | None
    first_observed_bar: datetime
    last_observed_bar: datetime
    current_status: str
    exchange: str
    rename_chain: tuple[RenameInterval, ...]
    evidence_class: str
    pit_quality_tier: str
    terminal_event_class: str | None
    notes: str


@dataclass(frozen=True)
class UnresolvedLifetime:
    entity_id: str
    canonical_symbol: str
    reasons: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "entity_id": self.entity_id,
            "canonical_symbol": self.canonical_symbol,
            "reasons": list(self.reasons),
        }


@dataclass(frozen=True)
class LifetimeLoadReceipt:
    source_path: str
    source_sha256: str
    row_count: int
    explicit_terminal_count: int
    horizon_censored_count: int
    unresolved_count: int
    rename_chain_count: int
    terminal_mapping_sha256: str

    def to_dict(self) -> dict[str, object]:
        return {
            "source_path": self.source_path,
            "source_sha256": self.source_sha256,
            "row_count": self.row_count,
            "explicit_terminal_count": self.explicit_terminal_count,
            "horizon_censored_count": self.horizon_censored_count,
            "unresolved_count": self.unresolved_count,
            "rename_chain_count": self.rename_chain_count,
            "terminal_mapping_sha256": self.terminal_mapping_sha256,
        }


@dataclass(frozen=True)
class LifetimeLoadResult:
    records: tuple[EntityLifetimeRecord, ...]
    explicit_terminals: tuple[tuple[str, TerminalLifecycle], ...]
    horizon_censored: tuple[str, ...]
    unresolved: tuple[UnresolvedLifetime, ...]
    receipt: LifetimeLoadReceipt

    def engine_terminal_lifecycles(self) -> dict[str, TerminalLifecycle]:
        """Return only unambiguous terminal events suitable for the engine.

        Open active lifetimes and unresolved endpoint statuses deliberately do
        not appear. In particular, ``current_status`` is never used to remove
        an entity before its explicit terminal bar.
        """

        return dict(self.explicit_terminals)


def load_entity_lifetimes(path: str | Path) -> LifetimeLoadResult:
    """Load and validate the canonical v5 entity-lifetime contract.

    Structural corruption raises ``LifetimeSchemaError``. Semantically
    incomplete rows remain in ``records`` and are reported in ``unresolved``;
    they are never guessed into engine terminal events.
    """

    source = Path(path)
    source_bytes = source.read_bytes()
    source_hash = hashlib.sha256(source_bytes).hexdigest()
    records: list[EntityLifetimeRecord] = []
    entity_ids: set[str] = set()
    provider_ids: set[str] = set()
    symbols: set[str] = set()

    with source.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        fields = tuple(reader.fieldnames or ())
        if len(fields) != len(set(fields)) or set(fields) != set(CANONICAL_LIFETIME_FIELDS):
            missing = sorted(set(CANONICAL_LIFETIME_FIELDS) - set(fields))
            extra = sorted(set(fields) - set(CANONICAL_LIFETIME_FIELDS))
            raise LifetimeSchemaError(f"Invalid lifetime CSV schema: missing={missing}, extra={extra}")
        for row_number, raw in enumerate(reader, start=2):
            record = _parse_record(raw, row_number)
            _require_unique(record.entity_id, entity_ids, "entity_id", row_number)
            _require_unique(record.provider_asset_id, provider_ids, "provider_asset_id", row_number)
            _require_unique(record.canonical_symbol, symbols, "canonical_symbol", row_number)
            records.append(record)

    terminals: list[tuple[str, TerminalLifecycle]] = []
    horizon_censored: list[str] = []
    unresolved: list[UnresolvedLifetime] = []
    for record in records:
        reasons = _unresolved_reasons(record)
        if reasons:
            unresolved.append(UnresolvedLifetime(record.entity_id, record.canonical_symbol, reasons))
            continue
        if record.current_status == "active":
            horizon_censored.append(record.canonical_symbol)
            continue
        assert record.valid_to is not None
        assert record.terminal_event_class is not None
        terminals.append(
            (
                record.canonical_symbol,
                TerminalLifecycle(record.last_observed_bar, record.terminal_event_class),
            )
        )

    terminals.sort(key=lambda item: item[0])
    horizon_censored.sort()
    unresolved.sort(key=lambda item: item.canonical_symbol)
    terminal_payload = [
        {
            "symbol": symbol,
            "last_bar": lifecycle.last_bar.isoformat(),
            "event_class": lifecycle.event_class,
        }
        for symbol, lifecycle in terminals
    ]
    mapping_hash = hashlib.sha256(
        json.dumps(terminal_payload, separators=(",", ":"), sort_keys=True).encode()
    ).hexdigest()
    receipt = LifetimeLoadReceipt(
        source_path=str(source),
        source_sha256=source_hash,
        row_count=len(records),
        explicit_terminal_count=len(terminals),
        horizon_censored_count=len(horizon_censored),
        unresolved_count=len(unresolved),
        rename_chain_count=sum(bool(record.rename_chain) for record in records),
        terminal_mapping_sha256=mapping_hash,
    )
    return LifetimeLoadResult(
        records=tuple(records),
        explicit_terminals=tuple(terminals),
        horizon_censored=tuple(horizon_censored),
        unresolved=tuple(unresolved),
        receipt=receipt,
    )


def _parse_record(raw: dict[str, str | None], row_number: int) -> EntityLifetimeRecord:
    values = {field: str(raw.get(field) or "").strip() for field in CANONICAL_LIFETIME_FIELDS}
    entity_id = _uuid(values["entity_id"], "entity_id", row_number)
    provider_asset_id = _uuid(values["provider_asset_id"], "provider_asset_id", row_number)
    canonical = _symbol(values["canonical_symbol"], "canonical_symbol", row_number)
    historical = _symbol(values["historical_symbol"], "historical_symbol", row_number)
    valid_from = _timestamp(values["valid_from"], "valid_from", row_number, required=True)
    valid_to = _timestamp(values["valid_to"], "valid_to", row_number, required=False)
    first_bar = _timestamp(values["first_observed_bar"], "first_observed_bar", row_number, required=True)
    last_bar = _timestamp(values["last_observed_bar"], "last_observed_bar", row_number, required=True)
    assert valid_from is not None and first_bar is not None and last_bar is not None
    if valid_from > first_bar or first_bar > last_bar:
        raise LifetimeSchemaError(f"row {row_number}: invalid valid_from/observed-bar interval")
    if valid_to is not None and (valid_from > valid_to or last_bar > valid_to):
        raise LifetimeSchemaError(f"row {row_number}: invalid valid_to interval")
    status = values["current_status"].lower()
    if status not in {"active", "inactive"}:
        raise LifetimeSchemaError(f"row {row_number}: unsupported current_status {status!r}")
    exchange = values["exchange"]
    evidence = values["evidence_class"]
    quality = values["pit_quality_tier"]
    if not exchange or not evidence or not quality:
        raise LifetimeSchemaError(f"row {row_number}: exchange/evidence_class/pit_quality_tier are required")
    rename_chain = _rename_chain(values["rename_chain"], canonical, row_number)
    terminal_event = values["terminal_event_class"] or None
    return EntityLifetimeRecord(
        entity_id=entity_id,
        provider_asset_id=provider_asset_id,
        canonical_symbol=canonical,
        historical_symbol=historical,
        valid_from=valid_from,
        valid_to=valid_to,
        first_observed_bar=first_bar,
        last_observed_bar=last_bar,
        current_status=status,
        exchange=exchange,
        rename_chain=rename_chain,
        evidence_class=evidence,
        pit_quality_tier=quality,
        terminal_event_class=terminal_event,
        notes=values["notes"],
    )


def _unresolved_reasons(record: EntityLifetimeRecord) -> tuple[str, ...]:
    reasons: list[str] = []
    if record.current_status == "active":
        if record.valid_to is not None:
            reasons.append("active_entity_has_valid_to")
        if record.terminal_event_class is not None:
            reasons.append("active_entity_has_terminal_event")
        return tuple(reasons)
    if record.valid_to is None:
        reasons.append("inactive_entity_missing_valid_to")
    if record.terminal_event_class is None:
        reasons.append("inactive_entity_missing_terminal_event")
    if record.valid_to is not None and record.valid_to != record.last_observed_bar:
        reasons.append("terminal_boundary_does_not_match_last_observed_bar")
    return tuple(reasons)


def _uuid(value: str, field: str, row_number: int) -> str:
    if not value:
        raise LifetimeSchemaError(f"row {row_number}: {field} is required")
    try:
        parsed = UUID(value)
    except ValueError as exc:
        raise LifetimeSchemaError(f"row {row_number}: invalid {field}") from exc
    canonical = str(parsed)
    if value.lower() != canonical:
        raise LifetimeSchemaError(f"row {row_number}: non-canonical {field}")
    return canonical


def _symbol(value: str, field: str, row_number: int) -> str:
    if not value:
        raise LifetimeSchemaError(f"row {row_number}: {field} is required")
    try:
        normalized = str(canonical_symbol(value, Market.EQUITY))
    except ValueError as exc:
        raise LifetimeSchemaError(f"row {row_number}: invalid {field}") from exc
    if value != normalized:
        raise LifetimeSchemaError(f"row {row_number}: non-canonical {field} {value!r}")
    return normalized


def _timestamp(value: str, field: str, row_number: int, *, required: bool) -> datetime | None:
    if not value:
        if required:
            raise LifetimeSchemaError(f"row {row_number}: {field} is required")
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise LifetimeSchemaError(f"row {row_number}: invalid {field}") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise LifetimeSchemaError(f"row {row_number}: {field} must be timezone-aware")
    return parsed.astimezone(UTC)


def _rename_chain(value: str, canonical: str, row_number: int) -> tuple[RenameInterval, ...]:
    if not value:
        return ()
    try:
        raw_chain: Any = json.loads(value)
    except json.JSONDecodeError as exc:
        raise LifetimeSchemaError(f"row {row_number}: invalid rename_chain JSON") from exc
    if not isinstance(raw_chain, list) or not raw_chain:
        raise LifetimeSchemaError(f"row {row_number}: rename_chain must be a non-empty JSON list")
    intervals: list[RenameInterval] = []
    allowed_keys = {"symbol", "valid_from", "valid_to"}
    for index, raw_interval in enumerate(raw_chain):
        if not isinstance(raw_interval, dict) or set(raw_interval) - allowed_keys:
            raise LifetimeSchemaError(f"row {row_number}: invalid rename_chain entry {index}")
        symbol = _symbol(str(raw_interval.get("symbol") or "").strip(), "rename_chain.symbol", row_number)
        start = _date(raw_interval.get("valid_from"), "rename_chain.valid_from", row_number)
        end = _date(raw_interval.get("valid_to"), "rename_chain.valid_to", row_number)
        if start is not None and end is not None and start > end:
            raise LifetimeSchemaError(f"row {row_number}: reversed rename_chain interval {index}")
        if index > 0 and start is None:
            raise LifetimeSchemaError(f"row {row_number}: only the first rename interval may have an open start")
        if index < len(raw_chain) - 1 and end is None:
            raise LifetimeSchemaError(f"row {row_number}: only the last rename interval may have an open end")
        if intervals:
            previous = intervals[-1]
            if previous.valid_to is None or start is None or start <= previous.valid_to:
                raise LifetimeSchemaError(f"row {row_number}: overlapping or unordered rename_chain")
        intervals.append(RenameInterval(symbol, start, end))
    if intervals[-1].symbol != canonical:
        raise LifetimeSchemaError(f"row {row_number}: rename_chain must end at canonical_symbol")
    if len({interval.symbol for interval in intervals}) != len(intervals):
        raise LifetimeSchemaError(f"row {row_number}: rename_chain contains duplicate symbols")
    return tuple(intervals)


def _date(value: object, field: str, row_number: int) -> date | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise LifetimeSchemaError(f"row {row_number}: {field} must be an ISO date")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise LifetimeSchemaError(f"row {row_number}: invalid {field}") from exc


def _require_unique(value: str, seen: set[str], field: str, row_number: int) -> None:
    if value in seen:
        raise LifetimeSchemaError(f"row {row_number}: duplicate {field} {value!r}")
    seen.add(value)


__all__ = [
    "CANONICAL_LIFETIME_FIELDS",
    "EntityLifetimeRecord",
    "LifetimeLoadReceipt",
    "LifetimeLoadResult",
    "LifetimeSchemaError",
    "RenameInterval",
    "UnresolvedLifetime",
    "load_entity_lifetimes",
]
