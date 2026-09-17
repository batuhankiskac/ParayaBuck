"""Frozen, fail-closed data loading for ``equity_spy_defensive_overlay_v1``.

This module contains no strategy or economic logic.  The public
``load_frozen_data`` entry point verifies the exact protocol hashes and
manifests before parsing the v5 inputs.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from pathlib import Path
from typing import Any

from parayabuck.backtest.portfolio_engine import TerminalLifecycle
from parayabuck.core.models import Bar, Market
from parayabuck.data.market_dataset import CorporateAction, CorporateActionType
from parayabuck.markets.symbols import canonical_symbol

BAR_FILE = "data/historical/equity_pit_symmetric_2016_2026_v5.csv"
LIFETIME_FILE = "data/historical/equity_entity_lifetimes_full_v5.csv"
ACTION_FILE = "data/historical/equity_survivorship_full_corporate_actions_v5.csv"
BAR_MANIFEST_FILE = f"{BAR_FILE.removesuffix('.csv')}.manifest.json"
ACTION_MANIFEST_FILE = f"{ACTION_FILE.removesuffix('.csv')}.manifest.json"

FROZEN_HASHES = {
    BAR_FILE: "bb7aaae86bc1909c6b0a1094a59430f5ed9900bb3ec63891bbdcda8cda055139",
    LIFETIME_FILE: "ed675d9b6ba7f173b65ae2b5272ffb62e09d6a007ed36ea22d3aba7a36774596",
    ACTION_FILE: "a0a60755a3f6adfc06a5ef0e6ed82852e1584c20fdb62cefed92b765dc9adc23",
}


@dataclass(frozen=True)
class EntityLifetime:
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
    evidence_class: str
    pit_quality_tier: str
    terminal_event_class: str | None

    @property
    def terminal_lifecycle(self) -> TerminalLifecycle | None:
        if self.terminal_event_class is None:
            return None
        return TerminalLifecycle(self.last_observed_bar, self.terminal_event_class)


@dataclass(frozen=True)
class FrozenData:
    bars: list[Bar]
    lifetimes: dict[str, EntityLifetime]
    terminal_lifecycles: dict[str, TerminalLifecycle]
    corporate_actions: list[CorporateAction]
    hashes: dict[str, str]


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _aware_datetime(raw: str, *, field: str) -> datetime:
    try:
        value = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise ValueError(f"Invalid {field}: {raw!r}") from exc
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")
    return value


def _require_columns(reader: csv.DictReader[str], required: set[str], label: str) -> None:
    missing = required - set(reader.fieldnames or ())
    if missing:
        raise ValueError(f"{label} CSV is missing required columns: {', '.join(sorted(missing))}")


def load_bars_csv(path: str | Path, *, symbols: set[str] | None = None) -> list[Bar]:
    wanted = {canonical_symbol(symbol, Market.EQUITY) for symbol in symbols} if symbols is not None else None
    bars: list[Bar] = []
    previous_key: tuple[datetime, str] | None = None
    with Path(path).open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        _require_columns(
            reader,
            {"timestamp", "symbol", "timeframe", "open", "high", "low", "close", "volume", "market"},
            "Bar",
        )
        for row in reader:
            symbol = canonical_symbol(str(row["symbol"]), Market.EQUITY)
            if wanted is not None and symbol not in wanted:
                continue
            timestamp = _aware_datetime(str(row["timestamp"]), field="bar timestamp")
            if row["timeframe"] != "1d" or row["market"].lower() != Market.EQUITY.value:
                raise ValueError(f"Unexpected bar market/timeframe for {symbol}")
            prices = tuple(float(row[name]) for name in ("open", "high", "low", "close"))
            volume = float(row["volume"])
            values = (*prices, volume)
            # The frozen acquisition schema may encode unavailable sessions as
            # an all-zero placeholder.  It is evidence of absence, never a bar.
            if all(value == 0 for value in values):
                continue
            if not all(math.isfinite(value) for value in values):
                raise ValueError(f"Non-finite bar row for {symbol} at {timestamp.isoformat()}")
            if any(value <= 0 for value in prices) or volume < 0:
                raise ValueError(f"Invalid non-placeholder bar row for {symbol} at {timestamp.isoformat()}")
            key = (timestamp, symbol)
            if previous_key is not None and key <= previous_key:
                raise ValueError("Bar rows must be unique and ordered by timestamp, symbol")
            previous_key = key
            bars.append(
                Bar(
                    timestamp=timestamp,
                    symbol=symbol,
                    timeframe="1d",
                    open=prices[0],
                    high=prices[1],
                    low=prices[2],
                    close=prices[3],
                    volume=volume,
                    market=Market.EQUITY,
                )
            )
    if wanted is not None:
        missing = wanted - {bar.symbol for bar in bars}
        if missing:
            raise ValueError(f"Requested symbols have no real bars: {', '.join(sorted(missing))}")
    if not bars:
        raise ValueError("Bar CSV contains no selected real bars")
    return bars


def load_lifetimes_csv(path: str | Path, *, symbols: set[str] | None = None) -> dict[str, EntityLifetime]:
    wanted = {canonical_symbol(symbol, Market.EQUITY) for symbol in symbols} if symbols is not None else None
    lifetimes: dict[str, EntityLifetime] = {}
    with Path(path).open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        _require_columns(
            reader,
            {
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
                "evidence_class",
                "pit_quality_tier",
                "terminal_event_class",
            },
            "Lifetime",
        )
        for row in reader:
            symbol = canonical_symbol(str(row["canonical_symbol"]), Market.EQUITY)
            if wanted is not None and symbol not in wanted:
                continue
            valid_from = _aware_datetime(str(row["valid_from"]), field="valid_from")
            valid_to = _aware_datetime(str(row["valid_to"]), field="valid_to") if row["valid_to"] else None
            first_bar = _aware_datetime(str(row["first_observed_bar"]), field="first_observed_bar")
            last_bar = _aware_datetime(str(row["last_observed_bar"]), field="last_observed_bar")
            if first_bar > last_bar or valid_from > last_bar or (valid_to is not None and valid_to < valid_from):
                raise ValueError(f"Invalid lifetime chronology for {symbol}")
            if symbol in lifetimes:
                raise ValueError(f"Duplicate lifetime identity for {symbol}")
            event_class = str(row["terminal_event_class"]).strip() or None
            lifetimes[symbol] = EntityLifetime(
                entity_id=str(row["entity_id"]),
                provider_asset_id=str(row["provider_asset_id"]),
                canonical_symbol=symbol,
                historical_symbol=str(row["historical_symbol"]),
                valid_from=valid_from,
                valid_to=valid_to,
                first_observed_bar=first_bar,
                last_observed_bar=last_bar,
                current_status=str(row["current_status"]),
                exchange=str(row["exchange"]),
                evidence_class=str(row["evidence_class"]),
                pit_quality_tier=str(row["pit_quality_tier"]),
                terminal_event_class=event_class,
            )
    if wanted is not None:
        missing = wanted - set(lifetimes)
        if missing:
            raise ValueError(f"Requested symbols have no lifetime identity: {', '.join(sorted(missing))}")
    if not lifetimes:
        raise ValueError("Lifetime CSV contains no selected identities")
    return lifetimes


def load_dividend_actions_csv(
    path: str | Path,
    *,
    symbols: set[str] | None = None,
) -> list[CorporateAction]:
    wanted = {canonical_symbol(symbol, Market.EQUITY) for symbol in symbols} if symbols is not None else None
    actions: list[CorporateAction] = []
    raw_ids: set[str] = set()
    with Path(path).open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        _require_columns(reader, {"action_type", "symbol", "ex_date", "rate", "raw_id"}, "Corporate-action")
        for row in reader:
            raw_id = str(row["raw_id"])
            if not raw_id or raw_id in raw_ids:
                raise ValueError(f"Missing or duplicate corporate-action raw_id: {raw_id!r}")
            raw_ids.add(raw_id)
            if row["action_type"] != "cash_dividends":
                continue
            symbol = canonical_symbol(str(row["symbol"]), Market.EQUITY)
            if wanted is not None and symbol not in wanted:
                continue
            timestamp = datetime.combine(date.fromisoformat(str(row["ex_date"])), time.min, tzinfo=UTC)
            actions.append(CorporateAction(timestamp, symbol, CorporateActionType.DIVIDEND, float(row["rate"])))
    actions.sort(key=lambda item: (item.timestamp, item.symbol, item.value))
    return actions


def _read_manifest(path: Path) -> dict[str, Any]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"Manifest must be an object: {path}")
    return raw


def validate_manifest_semantics(bar_manifest: dict[str, Any], action_manifest: dict[str, Any]) -> None:
    if Path(str(bar_manifest.get("dataset", ""))).name != Path(BAR_FILE).name:
        raise ValueError("Bar manifest dataset identity mismatch")
    if bar_manifest.get("sha256") != FROZEN_HASHES[BAR_FILE]:
        raise ValueError("Bar manifest hash mismatch")
    if bar_manifest.get("feed") != "sip" or bar_manifest.get("adjustment") != "split":
        raise ValueError("Frozen bars must be SIP and split-adjusted")
    if Path(str(bar_manifest.get("lifetime_table", ""))).name != Path(LIFETIME_FILE).name:
        raise ValueError("Bar manifest lifetime identity mismatch")
    if bar_manifest.get("lifetime_table_hash") != FROZEN_HASHES[LIFETIME_FILE]:
        raise ValueError("Bar manifest lifetime hash mismatch")
    if Path(str(action_manifest.get("dataset", ""))).name != Path(ACTION_FILE).name:
        raise ValueError("Corporate-action manifest dataset identity mismatch")
    if action_manifest.get("sha256") != FROZEN_HASHES[ACTION_FILE]:
        raise ValueError("Corporate-action manifest hash mismatch")
    breakdown = action_manifest.get("action_breakdown")
    if not isinstance(breakdown, dict) or int(breakdown.get("cash_dividends", 0)) <= 0:
        raise ValueError("Corporate-action manifest has no cash dividends")


def verify_frozen_inputs(repo_root: str | Path | None = None) -> dict[str, str]:
    root = Path(repo_root) if repo_root is not None else Path(__file__).resolve().parents[2]
    bar_manifest = _read_manifest(root / BAR_MANIFEST_FILE)
    action_manifest = _read_manifest(root / ACTION_MANIFEST_FILE)
    validate_manifest_semantics(bar_manifest, action_manifest)
    observed = {relative: sha256_file(root / relative) for relative in FROZEN_HASHES}
    mismatches = [relative for relative, expected in FROZEN_HASHES.items() if observed[relative] != expected]
    if mismatches:
        raise ValueError(f"Frozen input hash mismatch: {', '.join(mismatches)}")
    return observed


def _assert_frozen_spy_sh_coverage(bars: list[Bar], lifetimes: dict[str, EntityLifetime]) -> None:
    by_symbol = {symbol: [bar for bar in bars if bar.symbol == symbol] for symbol in ("SPY", "SH")}
    expected_counts = {"SPY": 2658, "SH": 2606}
    for symbol, expected in expected_counts.items():
        series = by_symbol[symbol]
        if len(series) != expected:
            raise ValueError(f"Frozen {symbol} bar count mismatch: {len(series)} != {expected}")
        if series[0].timestamp.date() != date(2016, 1, 4) or series[-1].timestamp.date() != date(2026, 7, 30):
            raise ValueError(f"Frozen {symbol} date coverage mismatch")
        identity = lifetimes.get(symbol)
        if (
            identity is None
            or identity.current_status != "active"
            or identity.pit_quality_tier != "TIER_A_VERIFIED_PIT"
        ):
            raise ValueError(f"Frozen {symbol} lifetime identity is not active TIER_A_VERIFIED_PIT")
    spy_dates = {bar.timestamp.date() for bar in by_symbol["SPY"]}
    sh_dates = {bar.timestamp.date() for bar in by_symbol["SH"]}
    gaps = sorted(spy_dates - sh_dates)
    if len(gaps) != 52 or gaps[0] != date(2016, 4, 13) or gaps[-1] != date(2016, 6, 24):
        raise ValueError("Frozen SH gap identity mismatch")
    if sh_dates - spy_dates:
        raise ValueError("SH contains sessions absent from SPY")


def load_frozen_data(
    repo_root: str | Path | None = None,
    *,
    symbols: set[str] | None = None,
) -> FrozenData:
    """Verify and load the exact v5 package.

    ``symbols`` is a parsing filter only; verification always hashes all three
    frozen files.  When supplied it must include both SPY and SH so the frozen
    instrument identity and coverage gates cannot be bypassed.
    """

    root = Path(repo_root) if repo_root is not None else Path(__file__).resolve().parents[2]
    wanted = None if symbols is None else {canonical_symbol(symbol, Market.EQUITY) for symbol in symbols}
    if wanted is not None and not {"SPY", "SH"}.issubset(wanted):
        raise ValueError("Frozen research loads must include real SPY and SH")
    hashes = verify_frozen_inputs(root)
    bars = load_bars_csv(root / BAR_FILE, symbols=wanted)
    lifetimes = load_lifetimes_csv(root / LIFETIME_FILE, symbols=wanted)
    actions = load_dividend_actions_csv(root / ACTION_FILE, symbols=wanted)
    _assert_frozen_spy_sh_coverage(bars, lifetimes)
    terminal = {
        symbol: lifecycle
        for symbol, identity in lifetimes.items()
        if (lifecycle := identity.terminal_lifecycle) is not None
    }
    if any(action.action_type != CorporateActionType.DIVIDEND for action in actions):
        raise AssertionError("Split-adjusted package emitted a non-dividend corporate action")
    return FrozenData(bars, lifetimes, terminal, actions, hashes)


__all__ = [
    "ACTION_FILE",
    "BAR_FILE",
    "FROZEN_HASHES",
    "LIFETIME_FILE",
    "EntityLifetime",
    "FrozenData",
    "load_bars_csv",
    "load_dividend_actions_csv",
    "load_frozen_data",
    "load_lifetimes_csv",
    "sha256_file",
    "validate_manifest_semantics",
    "verify_frozen_inputs",
]
