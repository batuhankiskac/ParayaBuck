from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from parayabuck.config import EquityUniverseConfig
from parayabuck.markets.sectors import sector_of
from parayabuck.markets.symbols import canonical_symbol


@dataclass(frozen=True)
class EquityCandidate:
    symbol: str
    price: float
    median_dollar_volume_20d: float
    atr_pct: float
    data_completeness: float
    spread_bps: float | None = None
    economic_sector: str | None = None
    tradable: bool = True
    status: str = "active"
    exchange: str = "NASDAQ"
    leveraged_or_inverse: bool = False

    @classmethod
    def from_mapping(cls, value: dict[str, Any]) -> EquityCandidate:
        return cls(**value)


@dataclass(frozen=True)
class ScannerResult:
    selection_timestamp: str
    data_timestamp: str
    selected_symbols: tuple[str, ...]
    score_breakdown: dict[str, dict[str, float | str]]
    eliminated: dict[str, str]
    scanner_version: str
    config_digest: str
    status: str = "success"


class EquityUniverseScanner:
    """Deterministic pre-session selector; it never opens a market-data stream."""

    version = "equity-scanner-v1"

    def __init__(
        self,
        config: EquityUniverseConfig,
        *,
        minimum_price: float = 5.0,
        minimum_median_dollar_volume: float = 20_000_000,
        minimum_completeness: float = 0.95,
        minimum_atr_pct: float = 0.005,
        maximum_atr_pct: float = 0.12,
        maximum_per_sector: int = 5,
    ) -> None:
        self.config = config
        self.minimum_price = minimum_price
        self.minimum_median_dollar_volume = minimum_median_dollar_volume
        self.minimum_completeness = minimum_completeness
        self.minimum_atr_pct = minimum_atr_pct
        self.maximum_atr_pct = maximum_atr_pct
        self.maximum_per_sector = maximum_per_sector

    def scan(
        self,
        candidates: list[EquityCandidate],
        *,
        data_timestamp: datetime,
        selection_timestamp: datetime | None = None,
        pinned_symbols: tuple[str, ...] = (),
    ) -> ScannerResult:
        references = self.config.reference_symbols
        pinned = tuple(
            symbol
            for symbol in dict.fromkeys(canonical_symbol(item) for item in pinned_symbols)
            if symbol not in references
        )
        reserved = tuple(dict.fromkeys((*references, *pinned)))
        if len(reserved) > self.config.maximum_stream_symbols:
            raise ValueError("Reference and pinned symbols exceed the equity stream limit")
        eliminated: dict[str, str] = {}
        scored: list[tuple[float, str, str, dict[str, float | str]]] = []
        for candidate in candidates:
            symbol = canonical_symbol(candidate.symbol)
            reason = self._filter_reason(candidate)
            if reason:
                eliminated[symbol] = reason
                continue
            sector = candidate.economic_sector or sector_of(symbol)
            liquidity = min(1.0, math.log10(candidate.median_dollar_volume_20d) / 10)
            volatility_fit = max(
                0.0,
                1
                - abs(candidate.atr_pct - 0.03)
                / max(0.001, self.maximum_atr_pct - self.minimum_atr_pct),
            )
            spread_quality = (
                0.5 if candidate.spread_bps is None else max(0.0, 1 - candidate.spread_bps / 50)
            )
            score = (
                liquidity * 40
                + volatility_fit * 25
                + candidate.data_completeness * 25
                + spread_quality * 10
            )
            breakdown: dict[str, float | str] = {
                "total": round(score, 8),
                "liquidity": round(liquidity * 40, 8),
                "volatility_fit": round(volatility_fit * 25, 8),
                "data_completeness": round(candidate.data_completeness * 25, 8),
                "spread_quality": round(spread_quality * 10, 8),
                "economic_sector": sector,
            }
            scored.append((score, symbol, sector, breakdown))

        selected = list(reserved)
        breakdowns: dict[str, dict[str, float | str]] = {
            symbol: {
                "total": 0.0,
                "selection_role": (
                    "reference" if symbol in references else "pinned_managed_position"
                ),
                "economic_sector": sector_of(symbol),
            }
            for symbol in reserved
        }
        sector_counts: dict[str, int] = {}
        capacity = self.config.maximum_stream_symbols
        for _score, symbol, sector, breakdown in sorted(
            scored, key=lambda item: (-item[0], item[1])
        ):
            if symbol in selected:
                continue
            if len(selected) >= capacity:
                eliminated.setdefault(symbol, "stream_capacity")
                continue
            if sector_counts.get(sector, 0) >= self.maximum_per_sector:
                eliminated[symbol] = "sector_concentration"
                continue
            selected.append(symbol)
            sector_counts[sector] = sector_counts.get(sector, 0) + 1
            breakdowns[symbol] = breakdown
        timestamp = (selection_timestamp or datetime.now(UTC)).astimezone(UTC)
        return ScannerResult(
            selection_timestamp=timestamp.isoformat(),
            data_timestamp=data_timestamp.astimezone(UTC).isoformat(),
            selected_symbols=tuple(selected),
            score_breakdown=breakdowns,
            eliminated=dict(sorted(eliminated.items())),
            scanner_version=self.version,
            config_digest=self._config_digest(),
        )

    def persist(self, result: ScannerResult) -> None:
        path = Path(self.config.active_universe_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(asdict(result), indent=2, sort_keys=True),
            encoding="utf-8",
        )
        temporary.replace(path)

    def _filter_reason(self, item: EquityCandidate) -> str | None:
        if not item.tradable or item.status.lower() != "active":
            return "not_active_tradable"
        if item.exchange.upper() in {"OTC", "OTCQX", "OTCQB", "PINK"}:
            return "otc"
        if item.leveraged_or_inverse:
            return "leveraged_or_inverse"
        if not math.isfinite(item.price) or item.price < self.minimum_price:
            return "minimum_price"
        if (
            not math.isfinite(item.median_dollar_volume_20d)
            or item.median_dollar_volume_20d < self.minimum_median_dollar_volume
        ):
            return "minimum_liquidity"
        if not self.minimum_atr_pct <= item.atr_pct <= self.maximum_atr_pct:
            return "atr_range"
        if item.data_completeness < self.minimum_completeness:
            return "data_completeness"
        return None

    def _config_digest(self) -> str:
        payload = json.dumps(asdict(self.config), sort_keys=True).encode()
        return hashlib.sha256(payload).hexdigest()


def load_active_symbols(
    config: EquityUniverseConfig,
    *,
    pinned_symbols: tuple[str, ...] = (),
) -> tuple[str, ...]:
    """Load the last complete scanner output or return the configured fallback."""
    references = config.reference_symbols
    selected: tuple[str, ...] = ()
    if config.dynamic_selection_enabled:
        try:
            payload = json.loads(Path(config.active_universe_path).read_text(encoding="utf-8"))
            raw = payload["selected_symbols"]
            if payload.get("status", "success") != "success" or not isinstance(raw, list):
                raise ValueError("scanner output is not successful")
            selected = tuple(canonical_symbol(str(item)) for item in raw)
        except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
            selected = ()
    trade = selected or config.fallback_trade_symbols
    combined = tuple(
        dict.fromkeys((*references, *trade, *(canonical_symbol(item) for item in pinned_symbols)))
    )
    if len(combined) > config.maximum_stream_symbols:
        combined = combined[: config.maximum_stream_symbols]
    return combined
