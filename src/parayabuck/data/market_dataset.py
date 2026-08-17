"""Point-in-time backtest bundle with separate signal and execution series."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from parayabuck.core.models import Bar, Market
from parayabuck.markets.symbols import canonical_symbol


class CorporateActionType(StrEnum):
    DIVIDEND = "dividend"
    SPLIT = "split"


@dataclass(frozen=True)
class CorporateAction:
    timestamp: datetime
    symbol: str
    action_type: CorporateActionType
    value: float

    def __post_init__(self) -> None:
        if self.timestamp.tzinfo is None:
            raise ValueError("Corporate-action timestamps must be timezone-aware")
        if not math.isfinite(self.value) or self.value <= 0:
            raise ValueError("Corporate-action value must be finite and positive")


@dataclass(frozen=True)
class CashRateObservation:
    timestamp: datetime
    annualized_rate: float

    def __post_init__(self) -> None:
        if self.timestamp.tzinfo is None:
            raise ValueError("Cash-rate timestamps must be timezone-aware")
        if not math.isfinite(self.annualized_rate) or self.annualized_rate <= -1:
            raise ValueError("Cash rate must be finite and greater than -100%")


@dataclass(frozen=True)
class DatasetLineage:
    dataset_id: str
    parent_dataset_ids: tuple[str, ...]
    provider: str
    source_feed: str
    adjustment: str
    downloaded_at: datetime
    data_hash: str

    def __post_init__(self) -> None:
        required = (
            self.dataset_id,
            self.provider,
            self.source_feed,
            self.adjustment,
            self.data_hash,
        )
        if any(not item.strip() for item in required):
            raise ValueError("Dataset lineage fields must not be empty")
        if self.downloaded_at.tzinfo is None:
            raise ValueError("Dataset download timestamp must be timezone-aware")


@dataclass(frozen=True)
class MarketDataset:
    """Immutable dual-price-series input for portfolio simulation.

    ``signal_bars`` may be total-return adjusted. ``executable_bars`` must be
    raw/split-aware prices suitable for quantity, cash, and fill accounting.
    """

    market: Market
    timeframe: str
    signal_bars: tuple[Bar, ...]
    executable_bars: tuple[Bar, ...]
    lineage: DatasetLineage
    corporate_actions: tuple[CorporateAction, ...] = ()
    cash_rates: tuple[CashRateObservation, ...] = ()

    def __post_init__(self) -> None:
        if not self.timeframe.strip():
            raise ValueError("Dataset timeframe is required")
        if not self.signal_bars or not self.executable_bars:
            raise ValueError("Signal and executable series are both required")
        for label, bars in (
            ("signal", self.signal_bars),
            ("executable", self.executable_bars),
        ):
            keys: set[tuple[str, datetime]] = set()
            for bar in bars:
                if bar.market != self.market or bar.timeframe != self.timeframe:
                    raise ValueError(f"{label} bar market/timeframe mismatch")
                key = (canonical_symbol(bar.symbol, bar.market), bar.timestamp)
                if key in keys:
                    raise ValueError(f"Duplicate {label} bar: {key}")
                keys.add(key)
        signal_symbols = {canonical_symbol(bar.symbol, bar.market) for bar in self.signal_bars}
        executable_symbols = {
            canonical_symbol(bar.symbol, bar.market) for bar in self.executable_bars
        }
        if not signal_symbols.issubset(executable_symbols):
            missing = sorted(signal_symbols - executable_symbols)
            raise ValueError(f"No executable series for signal symbols: {missing}")

    @property
    def symbols(self) -> tuple[str, ...]:
        return tuple(sorted({canonical_symbol(bar.symbol, bar.market) for bar in self.signal_bars}))

    def content_digest(self) -> str:
        payload: list[object] = [
            self.market.value,
            self.timeframe,
            self.lineage.dataset_id,
            self.lineage.data_hash,
        ]
        for label, bars in (
            ("signal", self.signal_bars),
            ("executable", self.executable_bars),
        ):
            for bar in sorted(bars, key=lambda item: (item.timestamp, item.symbol)):
                payload.append(
                    (
                        label,
                        bar.timestamp.isoformat(),
                        canonical_symbol(bar.symbol, bar.market),
                        bar.open,
                        bar.high,
                        bar.low,
                        bar.close,
                        bar.volume,
                    )
                )
        for rate in sorted(self.cash_rates, key=lambda item: item.timestamp):
            payload.append(("cash_rate", rate.timestamp.isoformat(), rate.annualized_rate))
        return hashlib.sha256(
            json.dumps(payload, separators=(",", ":"), sort_keys=False).encode()
        ).hexdigest()


__all__ = [
    "CorporateAction",
    "CorporateActionType",
    "DatasetLineage",
    "CashRateObservation",
    "MarketDataset",
]
